# -*- coding: utf-8 -*-
"""
APK 签名证书解析器（基于 JDK keytool，无额外依赖）
==================================================
用于「安装/解包」弹窗解析 META-INF/*.RSA / *.DSA / *.EC 中的 X.509 证书。

环境：依赖系统已安装的 JDK `keytool.exe`。若未找到，会返回空结果并附带原因。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import List, Dict, Optional


def _find_keytool() -> Optional[str]:
    """在 PATH 与常见 JDK 路径里找 keytool（跨平台：Windows / Linux / macOS）。"""
    exe = shutil.which('keytool')
    if exe:
        return exe
    if sys.platform == 'win32':
        # Windows 常见 JDK 安装位置兜底
        candidates = [
            r'C:\Program Files\Java\jdk-26\bin\keytool.exe',
            r'C:\Program Files\Java\jdk-25\bin\keytool.exe',
            r'C:\Program Files\Java\jdk-24\bin\keytool.exe',
            r'C:\Program Files\Java\jdk-21\bin\keytool.exe',
            r'C:\Program Files\Java\jdk-17\bin\keytool.exe',
            r'C:\Program Files\Java\jdk-11\bin\keytool.exe',
            r'C:\Program Files\Java\jre-11\bin\keytool.exe',
            r'D:\Java\jdk-26.0.2\bin\keytool.exe',
            r'D:\Java\jdk-25\bin\keytool.exe',
            r'D:\Java\jdk-21\bin\keytool.exe',
            r'D:\Java\jdk-17\bin\keytool.exe',
        ]
    elif sys.platform == 'darwin':
        # macOS 常见 JDK 路径（/usr/libexec/java_home 动态探测优先）
        candidates = [
            '/usr/bin/keytool',
            '/usr/local/bin/keytool',
            '/Library/Java/JavaVirtualMachines/jdk-26.jdk/Contents/Home/bin/keytool',
            '/Library/Java/JavaVirtualMachines/jdk-25.jdk/Contents/Home/bin/keytool',
            '/Library/Java/JavaVirtualMachines/jdk-21.jdk/Contents/Home/bin/keytool',
            '/Library/Java/JavaVirtualMachines/jdk-17.jdk/Contents/Home/bin/keytool',
            '/Library/Java/JavaVirtualMachines/jdk-11.jdk/Contents/Home/bin/keytool',
        ]
    else:
        # Linux 常见 JDK 路径（Debian/Ubuntu/Fedora/Arch 等）
        candidates = [
            '/usr/bin/keytool',
            '/usr/local/bin/keytool',
            '/usr/lib/jvm/java-26-openjdk-amd64/bin/keytool',
            '/usr/lib/jvm/java-25-openjdk-amd64/bin/keytool',
            '/usr/lib/jvm/java-21-openjdk-amd64/bin/keytool',
            '/usr/lib/jvm/java-17-openjdk-amd64/bin/keytool',
            '/usr/lib/jvm/java-11-openjdk-amd64/bin/keytool',
            '/usr/lib/jvm/default-java/bin/keytool',
            '/usr/lib/jvm/latest/bin/keytool',
            '/opt/java/jdk-26/bin/keytool',
            '/opt/java/jdk-21/bin/keytool',
            '/opt/java/jdk-17/bin/keytool',
            os.path.expanduser('~/jdk-26/bin/keytool'),
            os.path.expanduser('~/jdk-21/bin/keytool'),
            os.path.expanduser('~/jdk-17/bin/keytool'),
        ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _run_keytool(args: List[str], timeout: int = 30) -> tuple[int, str, str]:
    keytool = _find_keytool()
    if not keytool:
        return 1, '', '未找到 keytool，请安装 JDK 并加入 PATH'
    # 强制英文输出，避免不同 locale（如中文 Windows）导致正则解析失效
    cmd = [keytool, '-J-Duser.language=en', '-J-Duser.country=US'] + args
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 1, '', f'未找到 keytool: {keytool}'
    except subprocess.TimeoutExpired:
        return 1, '', 'keytool 执行超时'
    except Exception as e:
        return 1, '', f'keytool 执行异常: {e}'


def _parse_keytool_printcert(out: str) -> List[Dict[str, str]]:
    """把 keytool -printcert 的多证书文本解析成结构化列表。"""
    certs: List[Dict[str, str]] = []
    # 多个 Signer 之间用空行分隔，先按段拆分
    # 每段里 Owner/Issuer/Serial number/Valid from/Certificate fingerprints
    blocks = re.split(r'\n(?=Signer\s+#|\s*Owner:\s)', out)
    for block in blocks:
        if not block.strip():
            continue
        cert: Dict[str, str] = {
            'owner': '', 'issuer': '', 'serial': '',
            'valid_from': '', 'valid_until': '',
            'sha1': '', 'sha256': '', 'sig_alg': '', 'version': '',
        }
        m = re.search(r'Owner:\s*(.+)', block)
        if m:
            cert['owner'] = m.group(1).strip()
        m = re.search(r'Issuer:\s*(.+)', block)
        if m:
            cert['issuer'] = m.group(1).strip()
        m = re.search(r'Serial number:\s*(.+)', block)
        if m:
            cert['serial'] = m.group(1).strip()
        m = re.search(r'Valid from:\s*(.+?)\s+until:\s*(.+)', block, re.IGNORECASE)
        if m:
            cert['valid_from'] = _fmt_date(m.group(1).strip())
            cert['valid_until'] = _fmt_date(m.group(2).strip())
        m = re.search(r'SHA1:\s*([0-9A-Fa-f:]+)', block)
        if m:
            cert['sha1'] = m.group(1).strip().upper()
        m = re.search(r'SHA256:\s*([0-9A-Fa-f:]+)', block)
        if m:
            cert['sha256'] = m.group(1).strip().upper()
        m = re.search(r'Signature algorithm name:\s*(.+)', block)
        if m:
            cert['sig_alg'] = m.group(1).strip()
        m = re.search(r'Version:\s*(\d+)', block)
        if m:
            cert['version'] = m.group(1).strip()
        if cert['owner'] or cert['issuer'] or cert['sha1']:
            certs.append(cert)
    return certs


def _fmt_date(s: str) -> str:
    """把 keytool 默认英文日期尽量转成 ISO-ish 短格式；解析失败则原样返回。"""
    try:
        # keytool 默认格式示例: "Sat Aug 08 05:50:00 CST 2026"
        # 去掉时区缩写后按 %a %b %d %H:%M:%S %Y 解析
        s_clean = re.sub(r'\s+[A-Z]{3,4}\s+', ' ', s)  # 去掉 CST/PDT 等
        dt = datetime.strptime(s_clean.strip(), '%a %b %d %H:%M:%S %Y')
        return dt.strftime('%Y-%m-%d %H:%M')
    except Exception:
        return s


def parse_apk_certs(apk_path: str, timeout: int = 30) -> Dict[str, any]:
    """解析 APK 里的签名证书。

    返回 {
        'ok': bool,
        'certs': [dict, ...],
        'error': str,
    }
    """
    if not os.path.isfile(apk_path):
        return {'ok': False, 'certs': [], 'error': '文件不存在'}

    # 优先用 -jarfile（APK 即 JAR 包，keytool 会自动找 META-INF 里的签名）
    rc, out, err = _run_keytool(['-printcert', '-jarfile', apk_path], timeout=timeout)
    if rc == 0 and ('Owner:' in out or 'Issuer:' in out):
        return {'ok': True, 'certs': _parse_keytool_printcert(out), 'error': ''}

    # 兜底：把 .RSA/.DSA/.EC 签名块文件解到临时目录，再用 -file 解析
    import tempfile
    import zipfile
    extracted = []
    try:
        with zipfile.ZipFile(apk_path, 'r') as zf:
            for info in zf.infolist():
                name_u = info.filename.upper()
                if name_u.startswith('META-INF/') and any(name_u.endswith(ext) for ext in ('.RSA', '.DSA', '.EC')):
                    extracted.append((info.filename, zf.read(info.filename)))
    except Exception as e:
        return {'ok': False, 'certs': [], 'error': f'打开 APK 失败: {e}'}

    if not extracted:
        return {'ok': False, 'certs': [], 'error': '未找到 META-INF 签名文件（可能使用 APK Signature Scheme v2/v3 且不含 v1 签名）'}

    certs: List[Dict[str, str]] = []
    tmp_dir = tempfile.mkdtemp(prefix='super_adb_cert_')
    try:
        for name, data in extracted:
            tmp_path = os.path.join(tmp_dir, os.path.basename(name))
            with open(tmp_path, 'wb') as fh:
                fh.write(data)
            rc, out, err = _run_keytool(['-printcert', '-file', tmp_path], timeout=timeout)
            if rc == 0:
                certs.extend(_parse_keytool_printcert(out))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if certs:
        return {'ok': True, 'certs': certs, 'error': ''}
    return {'ok': False, 'certs': [], 'error': err or '无法解析证书信息'}
