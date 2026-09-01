#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""打包产物冒烟测试（三平台通用，跑在 CI 的原生 runner 上）。

用途：在 build.yml 里打包 + 归档之后、上传产物之前调用，验证「用户解压出来的
东西真的能用」。**必须针对解压后的归档运行，不是 dist/ 目录** —— 曾经踩过的坑
（make_zip.py 丢失指向目录的符号链接，导致 .app 启动即 ModuleNotFoundError:
'_struct'）只在 归档→解压 往返之后才暴露，直接测 dist/ 是绿的。

用法：
    python3 .github/scripts/smoke_test.py --extracted-dir <解压后的目录> \
        [--timeout 20] [--report-dir <失败时写现场的目录>]

平台由 sys.platform 自动判断；解压目录必须显式传入（三平台归档格式与解压工具
各不相同，让脚本去猜等于把 workflow 的逻辑复制一份进来）。

退出码：0 = 全部通过；1 = 有断言失败（CI 应据此让作业失败）。
"""
import argparse
import os
import platform
import struct
import subprocess
import sys
import time

# Windows 控制台默认 cp1252，脚本里的中文 print 会直接 UnicodeEncodeError 把步骤搞挂。
# 在脚本内根治，而不是要求每个调用方去设 PYTHONIOENCODING。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

IS_MAC = sys.platform == 'darwin'
IS_WIN = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')

# ── 各平台产物布局与期望 ────────────────────────────────────────────────────
# 刻意写死，不从 spec 推导：spec 本身就可能写错（历史上 datas 的目标路径带过
# 前导斜杠），从它推导出来的期望值会跟着一起错，测试永远绿。断言必须是独立的
# 一份「我认为产物里应该有什么」。
if IS_MAC:
    LAYOUT = {
        'name': 'macOS',
        'app_glob': '*.app',
        'exe_rel': os.path.join('Contents', 'MacOS', 'Super_ADB_MAC'),
        # 相对 app 根的必需条目；用后缀匹配，容忍 PyInstaller 版本间的布局差异
        'required': [
            'Contents/Resources/config/build_info.json',
            'resources/Super_ADB.png',
            'resources/chart.umd.min.js',
            'vendor/scrcpy',
        ],
        # 绝不允许存在：Contents/MacOS 下除主程序外的内容会被 codesign 当作
        # 未签名的嵌套代码对象，一个 json 就足以让签名校验失败
        'forbidden': ['Contents/MacOS/config'],
        'exe_arch': {'arm64'},
        'vendor_arch': {'arm64'},
        'check_symlinks': True,
        'check_codesign': True,
    }
elif IS_WIN:
    LAYOUT = {
        'name': 'Windows',
        'app_glob': 'Super_ADB',
        'exe_rel': 'Super_ADB.exe',
        'required': [
            'config/build_info.json',
            'resources/Super_ADB.png',
            'vendor/scrcpy',
        ],
        'forbidden': [],
        'exe_arch': {'x86-64'},
        # 随包的 adb.exe 是 32 位 PE，x64 Windows 下经 WoW64 正常运行
        'vendor_arch': {'x86-64', 'i386'},
        # Compress-Archive 不保留符号链接，Windows 产物本就不含符号链接
        'check_symlinks': False,
        'check_codesign': False,
    }
else:
    LAYOUT = {
        'name': 'Linux',
        'app_glob': 'Super_ADB',
        'exe_rel': 'Super_ADB',
        'required': [
            'config/build_info.json',
            'resources/Super_ADB.png',
            'vendor/scrcpy',
            'vendor/adb',
        ],
        'forbidden': [],
        'exe_arch': {'x86-64'},
        'vendor_arch': {'x86-64'},
        'check_symlinks': True,
        'check_codesign': False,
    }

CRASH_MARKERS = (
    'Traceback (most recent call last)',
    'ModuleNotFoundError',
    'Fatal Python error',
    'Segmentation fault',
    'Abort trap',
    'failed to start because no Qt platform plugin',
    'could not find or load the Qt platform plugin',
)

_failures = []
_notes = []


def check(ok, label, detail=''):
    print('  %-5s %s%s' % ('PASS' if ok else 'FAIL', label, ('  — ' + detail) if detail else ''))
    if not ok:
        _failures.append('%s%s' % (label, ('  — ' + detail) if detail else ''))
    return ok


# ── 架构探测（不依赖 file 命令，Windows runner 上没有）────────────────────
def detect_arch(path):
    """返回 'arm64' / 'x86-64' / 'i386' / None（无法识别）。"""
    try:
        with open(path, 'rb') as f:
            head = f.read(64)
    except OSError:
        return None
    if len(head) < 20:
        return None

    if head[:4] == b'\x7fELF':
        if head[4] != 2:
            return 'i386'
        machine = struct.unpack_from('<H', head, 18)[0]
        return {0x3E: 'x86-64', 0xB7: 'arm64'}.get(machine)

    # Mach-O（含 universal fat）
    if head[:4] in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe'):
        cputype = struct.unpack_from('<I', head, 4)[0]
        return {0x0100000C: 'arm64', 0x01000007: 'x86-64', 0x00000007: 'i386'}.get(cputype)
    if head[:4] in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'):
        return 'arm64'  # fat 包：至少含 arm64 切片即可

    if head[:2] == b'MZ':
        try:
            with open(path, 'rb') as f:
                f.seek(0x3C)
                pe_off = struct.unpack('<I', f.read(4))[0]
                f.seek(pe_off)
                if f.read(4) != b'PE\0\0':
                    return None
                machine = struct.unpack('<H', f.read(2))[0]
        except (OSError, struct.error):
            return None
        return {0x8664: 'x86-64', 0x014C: 'i386', 0xAA64: 'arm64'}.get(machine)
    return None


def find_app_root(extracted):
    import glob
    hits = sorted(glob.glob(os.path.join(extracted, LAYOUT['app_glob'])))
    hits = [h for h in hits if os.path.isdir(h)]
    if not hits:
        return None
    return hits[0]


def walk_all(root):
    """产出 (绝对路径, 相对 root 的 posix 风格路径)；不跟随符号链接。"""
    for dirpath, dirnames, filenames in os.walk(root):
        for n in list(dirnames) + list(filenames):
            p = os.path.join(dirpath, n)
            yield p, os.path.relpath(p, root).replace(os.sep, '/')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--extracted-dir', required=True, help='归档解压后的目录')
    ap.add_argument('--timeout', type=int, default=20, help='启动后观察多少秒（默认 20）')
    ap.add_argument('--report-dir', default='', help='失败时把现场写到这里，供 CI 上传')
    args = ap.parse_args()

    # 立刻转绝对路径：launch() 会给子进程传 cwd=，相对路径在切目录后就失效了
    args.extracted_dir = os.path.abspath(args.extracted_dir)
    if args.report_dir:
        args.report_dir = os.path.abspath(args.report_dir)

    print('=== Super_ADB 打包产物冒烟测试 ===')
    print('平台: %s (%s / %s)' % (LAYOUT['name'], sys.platform, platform.machine()))
    print('解压目录: %s' % args.extracted_dir)

    if not os.path.isdir(args.extracted_dir):
        print('FATAL: 解压目录不存在')
        return 1

    app_root = find_app_root(args.extracted_dir)
    if not check(app_root is not None, '定位产物根目录',
                 '' if app_root else '在 %s 下找不到 %s' % (args.extracted_dir, LAYOUT['app_glob'])):
        return finish(args, None, '')
    print('产物根目录: %s' % app_root)

    entries = list(walk_all(app_root))
    rels = [r for _, r in entries]

    # ── 1. 主可执行文件 ──
    exe = os.path.join(app_root, LAYOUT['exe_rel'])
    check(os.path.isfile(exe), '主可执行文件存在', LAYOUT['exe_rel'])
    if os.path.isfile(exe) and not IS_WIN:
        check(os.access(exe, os.X_OK), '主可执行文件有执行位')

    # ── 2. 必需条目 ──
    for want in LAYOUT['required']:
        hit = [r for r in rels if r == want or r.endswith('/' + want)]
        detail = ''
        if hit:
            full = os.path.join(app_root, hit[0].replace('/', os.sep))
            empty = os.path.isdir(full) and not os.listdir(full)
            if empty:
                hit = []
                detail = '目录为空'
        check(bool(hit), '必需条目 %s' % want, detail)

    # ── 3. 禁止存在的条目 ──
    for bad in LAYOUT['forbidden']:
        hit = [r for r in rels if r == bad or r.startswith(bad + '/')]
        check(not hit, '不应存在 %s' % bad, ('实际存在: ' + hit[0]) if hit else '')

    # ── 4. 符号链接完整性 ──
    # 这是 make_zip.py 丢目录型软链那个缺陷的直接防线
    if LAYOUT['check_symlinks']:
        links = [(p, r) for p, r in entries if os.path.islink(p)]
        broken = [r for p, r in links if not os.path.exists(p)]
        check(not broken, '符号链接无断链',
              '共 %d 条，断 %d 条: %s' % (len(links), len(broken), ', '.join(broken[:5])) if broken
              else '共 %d 条' % len(links))
        _notes.append('符号链接 %d 条' % len(links))

    # ── 5. 架构 ──
    exe_arch = detect_arch(exe) if os.path.isfile(exe) else None
    check(exe_arch in LAYOUT['exe_arch'], '主程序架构',
          '期望 %s，实际 %s' % ('/'.join(sorted(LAYOUT['exe_arch'])), exe_arch))

    # vendor 下的 adb / scrcpy 主二进制：主程序能跑不代表它们架构对，
    # 它们是启动后才按需调用的，而且是手工放进仓库的
    for binname in ('adb', 'scrcpy'):
        cands = [p for p, r in entries
                 if '/vendor/' in ('/' + r)
                 and os.path.basename(r).lower() in (binname, binname + '.exe')
                 and os.path.isfile(p) and not os.path.islink(p)]
        if not cands:
            check(False, 'vendor 下存在 %s 二进制' % binname)
            continue
        arch = detect_arch(cands[0])
        check(arch in LAYOUT['vendor_arch'], 'vendor/%s 架构' % binname,
              '期望 %s，实际 %s' % ('/'.join(sorted(LAYOUT['vendor_arch'])), arch))

    # ── 6. codesign（仅 macOS）──
    if LAYOUT['check_codesign']:
        r = subprocess.run(['codesign', '--verify', '--deep', '--strict', app_root],
                           capture_output=True, text=True)
        check(r.returncode == 0, 'codesign --verify --deep --strict',
              (r.stderr or r.stdout).strip().splitlines()[0] if r.returncode else '')

    # ── 7. 实际启动 ──
    out = ''
    if os.path.isfile(exe):
        out = launch(exe, args.timeout)

    return finish(args, app_root, out)


def launch(exe, timeout):
    """headless 启动 timeout 秒，期间不得退出，输出里不得出现崩溃标志。"""
    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env.setdefault('QT_LOGGING_RULES', 'qt.qpa.fonts=false')
    print('  启动: %s (QT_QPA_PLATFORM=offscreen, 观察 %ds)' % (os.path.basename(exe), timeout))

    p = subprocess.Popen([exe], cwd=os.path.dirname(exe), env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         errors='replace')
    deadline = time.time() + timeout
    while time.time() < deadline:
        if p.poll() is not None:
            break
        time.sleep(0.5)

    alive = p.poll() is None
    if alive:
        p.terminate()
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    out = ''
    try:
        out = (p.stdout.read() or '') if p.stdout and not p.stdout.closed else ''
    except (ValueError, OSError):
        pass

    check(alive, '启动后 %ds 内未退出' % timeout,
          '' if alive else '退出码 %s' % p.returncode)
    hits = [m for m in CRASH_MARKERS if m in out]
    check(not hits, '输出中无崩溃标志', ('命中: ' + ', '.join(hits)) if hits else '')
    if out.strip():
        print('  ── 进程输出（末 20 行）──')
        for line in out.strip().splitlines()[-20:]:
            print('    ' + line)
    return out


def finish(args, app_root, out):
    print()
    if _failures:
        print('冒烟测试未通过，%d 项失败：' % len(_failures))
        for f in _failures:
            print('  - %s' % f)
        if args.report_dir:
            write_report(args.report_dir, app_root, out)
        return 1
    print('冒烟测试通过%s' % (('（%s）' % '，'.join(_notes)) if _notes else ''))
    return 0


def write_report(report_dir, app_root, out):
    """失败时留现场：进程输出 + 文件清单。几十 KB，够定位，不上传整个产物。"""
    try:
        os.makedirs(report_dir, exist_ok=True)
        with open(os.path.join(report_dir, 'failures.txt'), 'w', encoding='utf-8') as f:
            f.write('平台: %s\n\n' % LAYOUT['name'])
            f.write('\n'.join('- ' + x for x in _failures) + '\n')
        with open(os.path.join(report_dir, 'process_output.txt'), 'w', encoding='utf-8') as f:
            f.write(out or '(无输出)')
        if app_root:
            with open(os.path.join(report_dir, 'file_listing.txt'), 'w', encoding='utf-8') as f:
                for p, r in walk_all(app_root):
                    kind = 'L' if os.path.islink(p) else ('D' if os.path.isdir(p) else 'F')
                    tgt = ' -> ' + os.readlink(p) if os.path.islink(p) else ''
                    broken = '  [断链]' if os.path.islink(p) and not os.path.exists(p) else ''
                    f.write('%s  %s%s%s\n' % (kind, r, tgt, broken))
        print('现场已写入: %s' % report_dir)
    except OSError as e:
        print('写现场失败（不影响判定）: %s' % e)


if __name__ == '__main__':
    sys.exit(main())
