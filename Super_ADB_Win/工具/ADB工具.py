# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— ADB 命令封装层
======================================
统一封装常用 adb 命令，所有耗时调用均通过 subprocess 在后台线程执行，
主线程只负责刷新 UI。
"""

import base64
import json
import os
import re
import shutil
import subprocess
import time
import sys

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ----------------------------------------------------------------------
# 通用配置读写（UI 状态持久化）
# ----------------------------------------------------------------------
# 旧文件名 → 新文件名（一次性迁移映射，主配置 adb_shell_config.json → Super_ADB配置.json）
_LEGACY_CONFIG_RENAMES = {
    'Super_ADB配置.json': 'adb_shell_config.json',
}


def _config_path(name):
    """配置文件统一放 <base>/配置/ 子目录，文件名 = basename(name)。

    自动迁移旧位置文件（首次访问新路径不存在时）覆盖以下情形：
      - base/<filename>      （旧 frozen 行为：直接散落在 exe 旁）
      - base/配置/<filename> （旧源码行为：已是 配置/）
      - base/<name>          （调用方原始参数，含前缀）
      - 主配置特例：旧名 adb_shell_config.json → 新名 Super_ADB配置.json

    macOS 冻结版走 ~/Library/Application Support/Super_ADB/。
    """
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        base = os.path.expanduser('~/Library/Application Support/Super_ADB')
    elif getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        # 源码模式：本文件位于 Super_ADB_Win/工具/ 下，配置在项目根（上一级）
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    fname = os.path.basename(name)
    if not fname:
        return None
    new_dir = os.path.join(base, '配置')
    new_path = os.path.join(new_dir, fname)

    if not os.path.exists(new_path):
        candidates = [
            os.path.join(base, fname),                # 旧 frozen 行为
            os.path.join(base, '配置', fname),         # 旧源码行为（已是 配置/）
            os.path.join(base, name),                 # 调用方原 name（含前缀）
        ]
        legacy_fname = _LEGACY_CONFIG_RENAMES.get(fname)
        if legacy_fname:
            candidates.extend([
                os.path.join(base, legacy_fname),
                os.path.join(base, '配置', legacy_fname),
            ])
        new_abs = os.path.normcase(os.path.abspath(new_path))
        seen = set()
        for old in candidates:
            old_abs = os.path.normcase(os.path.abspath(old))
            if old_abs in seen or old_abs == new_abs:
                continue
            seen.add(old_abs)
            if os.path.isfile(old):
                try:
                    os.makedirs(new_dir, exist_ok=True)
                    os.replace(old, new_path)
                except OSError:
                    pass
                break

    os.makedirs(new_dir, exist_ok=True)
    return new_path


def load_json_config(name):
    """读取配置，失败/缺失时返回空 dict，由调用方回退默认值。

    注意：配置既可能是 dict 也可能是 list（如设备指纹列表、历史记录），
    两者都原样返回；仅当文件不存在/损坏时才回退空 dict。
    """
    import logging
    path = _config_path(name)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, (dict, list)):
            return data
        logging.getLogger(__name__).warning(
            '配置 %s 顶层类型异常，已回退空: %r', name, type(data).__name__)
        return {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # 文件损坏/权限异常：明确告警，不再静默返回 {} 让用户配置被无声清空
        logging.getLogger(__name__).warning(
            '读取配置 %s 失败（文件可能损坏），已回退空: %s', name, e)
        return {}


def save_json_config(name, data):
    import logging
    try:
        with open(_config_path(name), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 统一走 logging，不再用裸 print
        logging.getLogger(__name__).warning('保存配置 %s 失败: %s', name, e)


def format_device_label(d: dict) -> str:
    """设备下拉框条目显示文本（展示层，与业务逻辑分离）。"""
    return f"{d.get('model') or d.get('serial')}  [{d.get('serial')}]"


class AdbError(Exception):
    pass


def readonly_guidance(serial):
    """推送/写入遇只读分区时的解锁引导（按设备类型区分）。

    - 模拟器（emulator-*）：/system 只读由启动参数控制，正解是 -writable-system
      重启，disable-verity 流程无效，不引导。
    - 真机（userdebug 固件）：引导 disable-verity 流程；root/remount 按钮已内置
      自动执行该流程。
    """
    if serial and serial.startswith('emulator-'):
        return ('目标分区只读。模拟器的 /system 受 verified boot 保护，需以可写模式重启：\n'
                '   1) 关闭模拟器后执行: emulator -avd <AVD名称> -writable-system -no-snapshot\n'
                '   2) 重启完成后执行: adb root && adb remount，再重新推送')
    return ('目标分区只读。请先在「系统操作」执行 root/remount 解锁'
            '（真机会自动尝试 disable-verity 强开），或手动执行：\n'
            '   adb disable-verity && adb reboot && adb root && adb remount')


def find_bundled_adb_path():
    """按当前操作系统探测本工具内置 adb 的绝对路径，找不到返回 None。

    跨平台子目录约定（与「外部扩展/adb/」下三个目录一致）：

    - **Windows**： ``platform-tools-latest-windows/platform-tools/adb.exe``
    - **macOS**：   ``platform-tools-latest-darwin/platform-tools/adb``
    - **Linux**：   ``platform-tools-latest-linux/platform-tools/adb``

    路径回退（与 ``find_scrcpy_dir`` 同款）：源码模式基目录 → 父目录 → 当前工作目录，
    兼容 ``Super_ADB_Win/外部扩展/...`` 与 ``_internal/外部扩展/...``（冻结模式）两种布局。
    """
    import platform
    sysname = platform.system().lower()
    if sysname == 'windows':
        suffix = os.path.join('外部扩展', 'adb', 'platform-tools-latest-windows',
                              'platform-tools', 'adb.exe')
    elif sysname == 'darwin':
        suffix = os.path.join('外部扩展', 'adb', 'platform-tools-latest-darwin',
                              'platform-tools', 'adb')
    else:
        suffix = os.path.join('外部扩展', 'adb', 'platform-tools-latest-linux',
                              'platform-tools', 'adb')

    here = os.path.dirname(os.path.abspath(__file__))
    candidates_root = [
        os.path.dirname(here),  # Super_ADB_Win/（源码模式）
        here,                   # _internal/工具/（冻结模式）
        os.getcwd(),
    ]
    for root in candidates_root:
        full = os.path.join(root, suffix)
        if os.path.isfile(full):
            return os.path.abspath(full)
    return None


class AdbHelper:
    """ADB 命令辅助类：提供设备扫描、命令执行、常用信息获取等能力。"""

    def __init__(self, adb_path=None, log_callback=None):
        # 探测链：显式传入值 > shutil.which('adb') > 内置 adb > 'adb' 兜底
        #
        # 解决 Windows PATH 进程缓存陷阱：
        # 环境配置弹窗通过 winreg 写注册表 PATH 后，WM_SETTINGCHANGE 只通知
        # explorer，不会反向写回当前 Python 进程的 os.environ；导致即便注册表
        # 已更新，当前进程的 shutil.which('adb') 仍用旧 PATH 找不到 adb。
        # 解法有两层：
        #   1) 环境配置弹窗的 _add_to_windows_path 写完注册表后同步 os.environ
        #      + SetEnvironmentVariableW（让当前进程立即生效）
        #   2) 本 __init__ 不再硬编码 'adb'，而是主动探测：先 shutil.which('adb')
        #      （PATH 已配置的情况），再回退到 find_bundled_adb_path()（本工具自
        #      带的 platform-tools），最后才兜底 'adb'（交给系统 FileNotFoundError
        #      让上层提示用户配置环境）。
        if adb_path and adb_path != 'adb':
            self.adb_path = adb_path
        else:
            probed = shutil.which('adb')
            if not probed:
                probed = find_bundled_adb_path()
            self.adb_path = probed or 'adb'
        # 命令日志回调：每次执行前输出完整命令，便于排查命令错误
        self.log_callback = log_callback

    def _cmd_str(self, cmd_list):
        """把命令列表拼成 shell 字符串（含空格的路径自动加引号）。"""
        parts = []
        for p in cmd_list:
            p = str(p)
            if ' ' in p or '\t' in p:
                p = f'"{p}"'
            parts.append(p)
        return ' '.join(parts)

    def _run(self, cmd_list, timeout=30, shell=False):
        """执行 adb 命令，返回 CompletedProcess；出错时抛出 AdbError。

        采用整条命令字符串 + shell=True 方式执行（与 migu 项目一致），
        保证 shell 命令中的管道、重定向等能被正确解析。
        """
        cmd_str = self._cmd_str(cmd_list)
        if self.log_callback:
            try:
                self.log_callback(f'$ {cmd_str}')
            except Exception:
                pass
        try:
            result = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                shell=True,
            )
            return result
        except subprocess.TimeoutExpired:
            raise AdbError(f"命令执行超时: {cmd_str}")
        except FileNotFoundError:
            raise AdbError(f"未找到 adb 命令: {self.adb_path}")
        except Exception as e:
            raise AdbError(f"命令执行异常: {e}")

    def check_adb(self):
        try:
            r = self._run([self.adb_path, 'version'], timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def get_devices(self):
        """返回设备列表 [{'serial': ..., 'model': ..., 'state': ...}, ...]"""
        r = self._run([self.adb_path, 'devices', '-l'], timeout=10)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout or '获取设备列表失败')
        devices = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('List of devices'):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            model = ''
            for token in parts[2:]:
                if token.startswith('model:'):
                    model = token.split(':', 1)[1]
                    break
            devices.append({'serial': serial, 'model': model, 'state': state})
        return devices

    def connect(self, ip, timeout=15):
        if ':' not in ip:
            ip = f'{ip}:5555'
        r = self._run([self.adb_path, 'connect', ip], timeout=timeout)
        return r.stdout.strip() or r.stderr.strip()

    def disconnect(self, serial=None):
        cmd = [self.adb_path, 'disconnect']
        if serial:
            cmd.append(serial)
        r = self._run(cmd, timeout=10)
        return r.stdout.strip() or r.stderr.strip()

    def pair(self, target, code, timeout=20):
        """执行 adb pair <target> <code>，返回 (ok, message)。

        target 形如 ip:port（手机「无线调试」配对弹窗里的地址）。
        成功判定同时兼容中英文回显（successfully paired / 配对成功）。
        """
        if ':' not in target:
            raise AdbError("pair 目标需包含端口（格式 ip:port）")
        r = self._run([self.adb_path, 'pair', target, code], timeout=timeout)
        out = (r.stdout or '').strip()
        err = (r.stderr or '').strip()
        combined = out or err or '无返回'
        ok = r.returncode == 0 and (
            'successfully paired' in combined.lower()
            or '配对成功' in combined
            or 'successfully' in combined.lower()
        )
        return ok, combined

    def run_shell(self, serial, command, timeout=30):
        """执行 adb [-s serial] shell <command>，返回 stdout。"""
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', command]
        r = self._run(cmd, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or '').strip()
            raise AdbError(self._translate_error(err))
        return r.stdout

    def run_direct(self, serial, args, timeout=30):
        """执行 adb [-s serial] <args...>，返回 stdout。"""
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += args
        r = self._run(cmd, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or '').strip()
            raise AdbError(self._translate_error(err))
        return r.stdout

    def run_batch_script(self, serial, script, timeout=15):
        """安全地执行多行 shell 脚本。

        通过 base64 编码避开 Windows cmd.exe 嵌套引号 + 管道符拆 args 的坑。
        用 shell=False + 列表形式调用 subprocess, 整个命令作为 1 个字符串
        传给 adb, 命令内部的 | & < > 等都按字面量处理 (原 shell=True
        会被 cmd.exe 拆管道)。
        Android 自带 base64 (toybox/busybox), Android 7+ 标准支持。
        """
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        cmd_str = f'echo {encoded} | base64 -d | sh'
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', cmd_str]
        result = self._run_no_shell(cmd, timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or '').strip()
            raise AdbError(self._translate_error(err))
        return result.stdout

    def _run_no_shell(self, cmd_list, timeout=30):
        """执行命令 (list 形式, 绕过 cmd.exe)。

        适用于参数中含 cmd.exe 特殊字符 (|, &, <, >) 的场景。
        """
        if self.log_callback:
            try:
                self.log_callback(f'$ {" ".join(str(p) for p in cmd_list)}')
            except Exception:
                pass
        try:
            return subprocess.run(
                cmd_list, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=timeout, creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise AdbError(f"命令执行超时: {cmd_list}")
        except FileNotFoundError:
            raise AdbError(f"未找到 adb 命令: {self.adb_path}")
        except Exception as e:
            raise AdbError(f"命令执行异常: {e}")

    def _translate_error(self, text):
        if not text:
            return ''
        low = text.lower()
        if 'permission denied' in low:
            return '权限不足（Permission denied），可能需要 root 权限'
        if 'no such file' in low:
            return '文件或目录不存在（No such file or directory）'
        if 'read-only file system' in low:
            return '只读文件系统，无法写入'
        if 'device not found' in low or 'no devices' in low:
            return '未找到设备，请检查连接'
        if 'more than one device' in low:
            return '连接了多个设备，请在下拉框中选择具体设备'
        return text.strip()

    def push_stream(self, serial, local_path, remote_path, progress_cb=None):
        """推送文件到设备，实时回调进度。

        与 AdbFileManager.push() 不同，本方法流式读取 adb push 输出并解析进度
        （兼容老版本 `[ 25%]` 与新版本 `(bytes in ...)` 两种回显），
        通过 progress_cb(pct:int, text:str) 实时上报；不传则静默推送。
        复用 AdbHelper 的 adb 路径与 CREATE_NO_WINDOW；失败抛 AdbError。
        """
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['push', local_path, remote_path]
        if self.log_callback:
            try:
                self.log_callback('$ ' + ' '.join(str(p) for p in cmd))
            except Exception:
                pass
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            raise AdbError(f'未找到 adb 命令: {self.adb_path}')
        out_lines = []
        try:
            size = 0
            try:
                size = os.path.getsize(local_path)
            except OSError:
                pass
            if proc.stdout:
                for line in proc.stdout:
                    line = line.rstrip('\n')
                    if not line.strip():
                        continue
                    out_lines.append(line)
                    if self.log_callback:
                        try:
                            self.log_callback(line)
                        except Exception:
                            pass
                    if progress_cb:
                        m = re.search(r'\[\s*(\d+)%\]', line)
                        if m:
                            pct = int(m.group(1))
                            progress_cb(pct, f'正在推送... {pct}%')
                        else:
                            m2 = re.search(r'\((\d+)\s*bytes', line)
                            if m2 and size > 0:
                                transferred = int(m2.group(1))
                                pct = min(100, int(transferred / size * 100))
                                progress_cb(pct, f'正在推送... {pct}%')
            proc.wait()
        except Exception as e:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
            raise AdbError(f'推送异常: {e}')
        if proc.returncode != 0:
            raise AdbError(self._push_fail_msg(serial, out_lines, proc.returncode))

    @staticmethod
    def _push_fail_msg(serial, out_lines, returncode):
        """组装 push 失败消息；检测到只读分区时自动附上解锁引导。"""
        msg = f'推送失败 (returncode={returncode})'
        tail = out_lines[-1].strip() if out_lines else ''
        low = '\n'.join(out_lines).lower()
        if 'read-only file system' in low or 'read-only filesystem' in low:
            msg += f'：{tail}' if tail else ''
            msg += f'\n{readonly_guidance(serial)}'
        elif tail:
            msg += f'：{tail}'
        return msg


class Adb设备操作(AdbHelper):
    """面向设备操作的封装，所有方法均接受 serial 参数。"""

    # OAID/AAID 标准 UUID 格式
    UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

    def get_oaid(self, serial):
        """尝试通过多种厂商内容提供者/Settings 获取 OAID/AAID。

        小米 / 华为 / OPPO / vivo / Google 等各家路径不同,这里做集中回退。
        返回第一个匹配标准 UUID 的字符串; 获取失败返回空字符串。
        """
        script = '''OAID_RAW=""
# 小米 / MiTV 专用路径优先 (com.miui.idprovider 的 uniform_id)
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.idprovider/uniform_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.id.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure advertising_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.google.android.gms.id/id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.bun.miitmdid.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.mdid.msa.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.huawei.hwid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.heytap.openid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.coloros.mcs.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.vivo.vms.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure oaid 2>/dev/null)"
echo "$OAID_RAW"'''
        try:
            raw = self.run_batch_script(serial, script, timeout=15)
            m = self.UUID_RE.search(raw or '')
            return m.group(0) if m else ''
        except Exception:
            return ''

    def get_device_info_dict(self, serial):
        """一次性批量获取设备硬件/系统信息, 返回 dict。

        11 次独立 shell 调用 → 1 次批量调用 + 1 次 get-serialno, 节省 ~5s 延迟。
        新增: cpu_model (SoC 型号), gpu (GPU 信息)。
        脚本通过 base64 编码执行, 彻底避开 Windows cmd.exe 嵌套引号陷阱
        —— 否则 adb shell 内层的 $() 命令替换会被 cmd.exe 拆成多个 args,
        导致 $(getprop x.y) 被切成两半, 整个命令失效。

        返回的 dict 每个 value 都是字符串, 获取失败时为 None 或 '未知'。
        """
        # 脚本必须没有空格的命令替换 (命令替换会被 cmd.exe 拆 args),
        # 或通过 base64 编码传递。后者更通用, 任何脚本都能跑。
        script = '''echo "___ANDROID_RELEASE___:$(getprop ro.build.version.release)"
echo "___ANDROID_SDK___:$(getprop ro.build.version.sdk)"
echo "___ANDROID_ID___:$(getprop ro.build.id)"
echo "___SECURITY_PATCH___:$(getprop ro.build.version.security_patch)"
echo "___MODEL___:$(getprop ro.product.model)"
echo "___BRAND___:$(getprop ro.product.brand)"
echo "___MANUFACTURER___:$(getprop ro.product.manufacturer)"
echo "___DEVICE___:$(getprop ro.product.device)"
echo "___CPU_ABI___:$(getprop ro.product.cpu.abi)"
echo "___CPU_ABILIST___:$(getprop ro.product.cpu.abilist)"
echo "___CPU_CHIPNAME___:$(getprop ro.hardware.chipname)"
echo "___CPU_HARDWARE___:$(getprop ro.hardware)"
echo "___CPU_BOARD___:$(getprop ro.board.platform)"
echo "___CPU_SOC___:$(getprop ro.boot.soc_id)"
HW=$(grep -m1 "^Hardware" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | awk '{print $NF}')
echo "___CPU_PROC___:$HW"
GPU=$(dumpsys SurfaceFlinger 2>/dev/null | grep -m1 -i GLES | head -c 300)
echo "___GPU___:$GPU"
echo "___EGL___:$(getprop ro.hardware.egl)"
echo "___WM_SIZE___:$(wm size 2>/dev/null)"
echo "___WM_DENSITY___:$(wm density 2>/dev/null)"
# MAC 多路径回退获取(过滤 Android 10+ 占位符 02:00:00:00:00:00)
_get_mac() {
    for iface in wlan0 eth0 wlan1; do
        path="/sys/class/net/$iface/address"
        [ -r "$path" ] && cat "$path" 2>/dev/null && return
    done
    ip link show wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ip link show eth0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ifconfig wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ifconfig eth0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    settings get secure wifi_mac_address 2>/dev/null
}
MAC=$(_get_mac | head -n1)
MAC=$(echo "$MAC" | tr '[:upper:]' '[:lower:]')
[ "$MAC" = "02:00:00:00:00:00" ] && MAC=""
[ -z "$MAC" ] && MAC="N/A"
echo "___MAC___:$MAC"
echo "___AID___:$(settings get secure android_id 2>/dev/null)"
# OAID/AAID 多厂商候选获取, 由 Python 端统一提取 UUID
OAID_RAW=""
# 小米 / MiTV 专用路径优先
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.idprovider/uniform_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.id.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure advertising_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.google.android.gms.id/id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.bun.miitmdid.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.mdid.msa.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.huawei.hwid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.heytap.openid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.coloros.mcs.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.vivo.vms.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure oaid 2>/dev/null)"
echo "___OAID_RAW___:$OAID_RAW"
MEM=$(grep -m1 "^MemTotal" /proc/meminfo 2>/dev/null)
echo "___MEMTOTAL___:$MEM"
MEMAVAIL=$(grep -m1 "^MemAvailable" /proc/meminfo 2>/dev/null)
echo "___MEMAVAIL___:$MEMAVAIL"
echo "___END___"'''

        info = {}
        try:
            raw = self.run_batch_script(serial, script, timeout=15)
            for line in (raw or '').splitlines():
                m = re.match(r'___([A-Z_]+)___:(.*)', line)
                if m:
                    info[m.group(1).lower()] = m.group(2).strip()
        except Exception as e:
            info['_error'] = f'批量命令失败: {e}'

        # 从 OAID 候选输出中提取标准 UUID 格式的 OAID/AAID
        oaid_raw = info.get('oaid_raw', '')
        if oaid_raw:
            m = self.UUID_RE.search(oaid_raw)
            if m:
                info['oaid'] = m.group(0)
        if 'oaid' not in info:
            info['oaid'] = ''

        # get-serialno 是 adb 级命令, 无法批量 (单独一次, 很快)
        try:
            serialno = self._run(
                [self.adb_path, '-s', serial, 'get-serialno'], timeout=5).stdout.strip()
        except Exception:
            serialno = ''
        info['serialno'] = serialno

        return info

    def get_device_info(self, serial):
        """获取设备信息并格式化为人类可读字符串。

        内部委托 get_device_info_dict 然后格式化 (避免重复 ADB 调用)。
        """
        info = self.get_device_info_dict(serial)

        def _v(key, default='未知'):
            val = info.get(key, default)
            return val if val else default

        ram_kb = ''
        m = re.search(r'MemTotal:\s*(\d+)', _v('memtotal', ''))
        if m:
            ram_kb = int(m.group(1))

        avail_kb = ''
        m2 = re.search(r'MemAvailable:\s*(\d+)', _v('memavail', ''))
        if m2:
            avail_kb = int(m2.group(1))

        if ram_kb:
            ram_total_gb = ram_kb / 1024 / 1024
            if avail_kb:
                used_kb = ram_kb - avail_kb
                used_gb = used_kb / 1024 / 1024
                pct = used_kb / ram_kb * 100
                ram_str = f'{ram_total_gb:.1f} GB / 已用 {used_gb:.1f} GB ({pct:.0f}%)'
            else:
                ram_str = f'{ram_total_gb:.1f} GB'
        else:
            ram_str = '未解析到 MemTotal'

        lines = [
            f'序列号: {_v("serialno")}',
            f'设备型号: {_v("model")}',
            f'厂商名称: {_v("brand")} ({_v("manufacturer")})',
            f'Android版本: {_v("android_release")} (SDK {_v("android_sdk")}, Build {_v("android_id")})',
            f'安全补丁: {_v("security_patch")}',
            f'CPU 架构: {_v("cpu_abi")} ({_v("cpu_abilist")})',
            f'CPU 型号: {_v("cpu_chipname") or _v("cpu_soc") or _v("cpu_proc") or _v("cpu_hardware") or _v("cpu_board")}',
            f'GPU: {_v("egl") or (_v("gpu")[:80] if _v("gpu") else "未知")}',
            f'屏幕分辨率: {_v("wm_size")}',
            f'屏幕密度: {_v("wm_density")}',
            f'运行内存(RAM): {ram_str}',
            f'MAC 地址: {_v("mac")}',
            f'OAID/AAID: {_v("oaid") if _v("oaid") else "未获取"}',
            f'Android ID: {_v("aid")}',
        ]
        return '\n'.join(lines)

    def set_proxy(self, serial, host_port):
        self.run_shell(serial, f'settings put global http_proxy {host_port}', timeout=5)
        return self.run_shell(serial, 'settings get global http_proxy', timeout=5).strip()

    def clear_proxy(self, serial):
        self.run_shell(serial, 'settings put global http_proxy :0', timeout=5)
        return self.run_shell(serial, 'settings get global http_proxy', timeout=5).strip()

    def reboot(self, serial):
        self.run_shell(serial, 'reboot', timeout=5)
        return '已发送重启命令'

    def root_and_remount(self, serial):
        """尝试把 system 分区设为 rw，返回每步详细报告字符串。

        新版 Android (10+, emulator 默认) 用 system-as-root，/system 是 / 的一部分,
        旧命令 ``mount -o rw,remount /system`` 会报 "/system not in /proc/mounts"。
        这里多策略: adb root -> adb remount -> 按 /proc/mounts 选择 remount 路径 ->
        写真实文件验证可写性。验证失败时自动分流:
        - 真机（userdebug 固件）: 自动执行 disable-verity -> reboot -> root -> remount
          再复验，实现一键强开;
        - 模拟器: disable-verity 流程无效，提示用 -writable-system 参数重启。
        每步独立捕获 AdbError, 永不抛到上层。"""
        lines = []

        # 1) adb root —— 没 root 后续都没戏, 直接结束
        try:
            r = self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
            if r.returncode == 0:
                lines.append('① adb root：成功')
            else:
                err = (r.stderr or r.stdout or '').strip() or f'返回码 {r.returncode}'
                lines.append(f'① adb root：失败（{err}）')
                # 可能不是 userdebug 镜像, 继续尝试也行, 但毫无意义, 直接告知用户
                return '\n'.join(lines)
        except AdbError as e:
            lines.append(f'① adb root：异常（{e}）')
            return '\n'.join(lines)

        # 2) adb remount —— Android 内建 remount, system-as-root 走的就是这条
        try:
            r = self._run([self.adb_path, '-s', serial, 'remount'], timeout=10)
            out = (r.stdout or r.stderr or '').strip()
            if r.returncode == 0:
                lines.append(f'② adb remount：成功{(" — " + out) if out else ""}')
            else:
                lines.append(f'② adb remount：返回码 {r.returncode}（{out or "失败"}）')
        except AdbError as e:
            lines.append(f'② adb remount：异常（{e}）')

        # 3) 探测 /system 是否独立挂载
        system_is_separate = False
        try:
            mounts = self.run_shell(serial, 'cat /proc/mounts', timeout=5)
            system_is_separate = bool(re.search(
                r'^[^ ]+ +/system ', mounts or '', re.MULTILINE))
        except AdbError:
            pass

        # 4) 按情况 remount
        if system_is_separate:
            lines.append('③ 检测：/system 是独立挂载点')
            try:
                self.run_shell(serial, 'mount -o rw,remount /system', timeout=10)
                lines.append('④ mount -o rw,remount /system：成功')
            except AdbError as e:
                lines.append(f'④ mount -o rw,remount /system：失败（{e}）')
        else:
            lines.append('③ 检测：/system 是根文件系统的一部分（system-as-root，跳过 /system）')
            try:
                self.run_shell(serial, 'mount -o rw,remount /', timeout=10)
                lines.append('④ mount -o rw,remount /：成功')
            except AdbError as e:
                lines.append(f'④ mount -o rw,remount /：失败（{e}；'
                             f'内核可能禁止 remount 根分区, 实际可写性看 ⑤）')

        # 5) 真实写入验证 —— 最可靠判据；失败则按设备类型自动强开
        probe = '/system/.super_adb_rw_probe'
        try:
            self.run_shell(
                serial, f'touch {probe} && rm {probe}', timeout=5)
            lines.append('⑤ 验证：可在 /system 写入 ✓')
            return '\n'.join(lines)
        except AdbError as e:
            lines.append(f'⑤ 验证：/system 仍只读（{e}）')

        # 模拟器：只读由启动参数控制，disable-verity 流程无意义，给出正解
        if serial and serial.startswith('emulator-'):
            lines.append('⑥ 提示：模拟器请以可写模式重启后再点本按钮：')
            lines.append('   emulator -avd <AVD名称> -writable-system -no-snapshot')
            lines.append('   重启完成后重新点击本按钮，即可完成 /system 解锁。')
            return '\n'.join(lines)

        # 真机（userdebug 固件）：自动执行 disable-verity -> reboot -> root -> remount
        lines.append('⑥ 真机检测到只读分区，自动尝试强开（disable-verity 流程）…')
        try:
            r = self._run([self.adb_path, '-s', serial, 'disable-verity'], timeout=15)
            out = (r.stdout or r.stderr or '').strip()
            if r.returncode == 0:
                lines.append(f'   6-1 adb disable-verity：成功{(" — " + out) if out else ""}')
            else:
                detail = f'（{out}）' if out else f'（返回码 {r.returncode}）'
                lines.append(f'   6-1 adb disable-verity：失败{detail}')
                lines.append('   固件不支持关闭 verity（需 userdebug 版本），无法自动强开。')
                return '\n'.join(lines)
        except AdbError as ex:
            lines.append(f'   6-1 adb disable-verity：异常（{ex}）')
            return '\n'.join(lines)

        try:
            self._run([self.adb_path, '-s', serial, 'reboot'], timeout=10)
            lines.append('   6-2 adb reboot：已重启，等待设备重连…')
            self._run([self.adb_path, '-s', serial, 'wait-for-device'], timeout=90)
            lines.append('   6-3 设备已重连')
        except AdbError as ex:
            lines.append(f'   6-2/6-3 等待设备重连失败（{ex}），请稍后手动执行: adb root && adb remount')
            return '\n'.join(lines)

        try:
            r = self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
            if r.returncode != 0:
                err = (r.stderr or r.stdout or '').strip()
                lines.append(f'   6-4 adb root：失败（{err or f"返回码 {r.returncode}"}）')
                return '\n'.join(lines)
            lines.append('   6-4 adb root：成功（adbd 重启中…）')
            self._run([self.adb_path, '-s', serial, 'wait-for-device'], timeout=30)
            lines.append('   6-5 adbd 重启完成，已重新连接')
        except AdbError as ex:
            lines.append(f'   6-4/6-5 adb root / 重连失败（{ex}）')
            return '\n'.join(lines)

        try:
            r = self._run([self.adb_path, '-s', serial, 'remount'], timeout=15)
            out = (r.stdout or r.stderr or '').strip()
            if r.returncode == 0:
                lines.append(f'   6-6 adb remount：成功{(" — " + out) if out else ""}')
            else:
                detail = f'（{out}）' if out else f'（返回码 {r.returncode}）'
                lines.append(f'   6-6 adb remount：失败{detail}')
        except AdbError as ex:
            lines.append(f'   6-6 adb remount：异常（{ex}）')

        try:
            self.run_shell(serial, f'touch {probe} && rm {probe}', timeout=5)
            lines.append('⑦ 复验：/system 现已可写 ✓ 解锁成功！')
        except AdbError as ex:
            lines.append(f'⑦ 复验：/system 仍只读（{ex}）')
            lines.append('   强开未生效。若设备是 userdebug 固件，请手动核对：')
            lines.append('   adb disable-verity && adb reboot && adb root && adb remount')

        return '\n'.join(lines)

    def screenshot(self, serial):
        timestamp = time.strftime('%Y%m%d%H%M%S')
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        remote = '/sdcard/adb_shell_screen.png'
        local = os.path.join(desktop, f'{timestamp}screen.png')
        self.run_shell(serial, f'screencap -p {remote}', timeout=15)
        r = self._run([self.adb_path, '-s', serial, 'pull', remote, local], timeout=30)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout)
        self.run_shell(serial, f'rm {remote}', timeout=5)
        return local

    def screen_record(self, serial, duration, stop_event):
        """录制屏幕；stop_event 为 threading.Event，调用 set() 可提前停止。"""
        timestamp = time.strftime('%Y%m%d%H%M%S')
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        remote = '/sdcard/adb_shell_record.mp4'
        local = os.path.join(desktop, f'{timestamp}record.mp4')
        cmd = [self.adb_path, '-s', serial, 'shell', 'screenrecord',
               '--time-limit', str(duration), '--size', '1280x720', remote]
        # 录屏输出无需捕获；用 DEVNULL 避免 stderr 管道缓冲 (64KB) 触发的死锁
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        start = time.time()
        while proc.poll() is None:
            if stop_event.is_set():
                proc.terminate()
                break
            if time.time() - start > duration + 30:
                proc.terminate()
                break
            time.sleep(0.3)
        # wait 超时后补 kill 兜底，防止 terminate 不生效导致进程变僵尸
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        r = self._run([self.adb_path, '-s', serial, 'pull', remote, local], timeout=60)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout)
        self.run_shell(serial, f'rm {remote}', timeout=5)
        return local

    @staticmethod
    def find_scrcpy_dir():
        """探测项目 外部扩展/ 下匹配当前平台的最新 scrcpy 目录。

        返回 scrcpy 目录绝对路径；未找到时返回 None。
        支持两种目录布局:
          - 外部扩展/scrcpy-win64-vX.Y/...
          - 外部扩展/scrcpy/scrcpy-win64-vX.Y/...
        按目录名中的版本号降序取最新版本。
        """
        # 本文件位于 工具/ 下，外部扩展/ 在项目根（上一级）；冻结后 __file__
        # 位于 _internal/ 顶层（base 即项目根），故 base 与其上一级都探测
        base = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(base)
        prefix_map = {'darwin': 'scrcpy-mac-', 'linux': 'scrcpy-linux-', 'win32': 'scrcpy-win64-'}
        prefix = prefix_map.get(sys.platform, 'scrcpy-win64-')
        candidates = []
        for root in (base, parent, os.getcwd()):
            data_dir = os.path.join(root, '外部扩展')
            if not os.path.isdir(data_dir):
                continue
            for name in os.listdir(data_dir):
                full = os.path.join(data_dir, name)
                if not os.path.isdir(full):
                    continue
                if name.startswith(prefix):
                    candidates.append(full)
                else:
                    try:
                        for sub in os.listdir(full):
                            sub_full = os.path.join(full, sub)
                            if os.path.isdir(sub_full) and sub.startswith(prefix):
                                candidates.append(sub_full)
                    except (PermissionError, OSError):
                        pass
        if not candidates:
            return None

        def _ver_key(path):
            ver_str = os.path.basename(path)[len(prefix):]
            return [int(t) if t.isdigit() else 0 for t in re.split(r'[.\-]', ver_str)]

        candidates.sort(key=_ver_key, reverse=True)
        return candidates[0]

    def scrcpy(self, serial, extra_args=None):
        """启动 scrcpy 投屏；优先使用 外部扩展/ 下匹配平台的最新版本 scrcpy 目录。

        extra_args: 可选的额外命令行参数列表（如码率/分辨率覆盖），默认用
        SCRCPY_DEFAULT_ARGS（针对无线 + 高分辨率电视优化：降分辨率提码率）。
        """
        # 默认参数（针对无线连接 + 电视高分辨率优化）：
        #   --max-size 1280   : 限制最长边 1280，显著降低无线传输量与解码压力（降延迟最有效）
        #   --video-bit-rate 16M : 码率提到 16Mbps（默认仅 8M），画质明显清晰
        #   --max-fps 60      : 限制 60fps，避免无谓高帧率占用带宽
        #   --render-driver   : Windows 用 direct3d 渲染最快（仅 win32 加）
        #   --no-audio        : 电视 Android 9 不支持音频转发，关闭避免无效尝试、启动更快
        # 调优：要更清晰把 --max-size 改 1920；若 PC 能硬解 HEVC 可加 --video-codec h265
        default_args = ['--max-size', '1280', '--video-bit-rate', '16M', '--max-fps', '60', '--no-audio']
        if sys.platform == 'win32':
            default_args += ['--render-driver', 'direct3d']
        args = list(extra_args) if extra_args else default_args

        is_win = sys.platform == 'win32'
        scrcpy_dir = self.find_scrcpy_dir()

        if scrcpy_dir:
            exe_name = 'scrcpy.exe' if is_win else 'scrcpy'
            exe_path = os.path.join(scrcpy_dir, exe_name)
            if not os.path.isfile(exe_path):
                raise FileNotFoundError(
                    f'在 {scrcpy_dir} 下未找到 {exe_name}，\n'
                    '请确认下载的是 scrcpy release 包（含 scrcpy.exe 和 scrcpy-server）。'
                )
            cmd = [exe_path, '-s', serial] + args
            cwd = scrcpy_dir
        else:
            exe_name = 'scrcpy.exe' if is_win else 'scrcpy'
            # 尝试在 PATH 中找 scrcpy
            found = False
            for d in os.environ.get('PATH', '').split(os.pathsep):
                if d and os.path.isfile(os.path.join(d, exe_name)):
                    found = True
                    break
            if not found:
                raise FileNotFoundError(
                    '未找到 scrcpy 可执行文件。\n'
                    '请下载对应平台 release 包并放到 Super_ADB_Win/外部扩展/scrcpy/scrcpy-win64-vX.Y/ 下。'
                )
            cmd = [exe_name, '-s', serial] + args
            cwd = None

        # 直接启动 scrcpy，不生成日志文件（问题已定位，关闭日志重定向）
        popen_kwargs = {
            'creationflags': CREATE_NO_WINDOW,
        }
        if cwd:
            popen_kwargs['cwd'] = cwd
        subprocess.Popen(cmd, **popen_kwargs)
        return '已启动投屏'

    def get_app_list(self, serial, flag=''):
        args = ['shell', 'pm', 'list', 'packages', '-f']
        if flag:
            args.append(flag)
        return self.run_direct(serial, args, timeout=30)

    def get_running_apps(self, serial):
        return self.run_shell(serial, 'pm list packages -e', timeout=30)

    def get_window_app(self, serial):
        try:
            out = self.run_shell(serial, 'dumpsys window | grep mCurrentFocus', timeout=10)
            m = re.search(r'\{(.*?)\}', out)
            if m:
                parts = m.group(1).split()
                if len(parts) >= 3:
                    return parts[2]
            return out.strip() or '未获取到当前界面'
        except Exception as e:
            return f'获取失败: {e}'

    def start_app(self, serial, package_name):
        if '/' in package_name:
            self.run_shell(serial, f'am start -n {package_name}', timeout=10)
            return f'已启动 {package_name}'

        # 先检查 monkey 是否可用 (部分模拟器/精简系统不含 monkey)
        # command -v monkey 在无 monkey 时返回非零 → run_shell 会抛 AdbError
        try:
            mk = self.run_shell(serial, 'command -v monkey', timeout=5)
            has_monkey = 'monkey' in (mk or '').lower()
        except AdbError:
            has_monkey = False

        if not has_monkey:
            # 没 monkey → 用 am start 回退: 先查入口 Activity
            resolve = self.run_shell(
                serial, f'cmd package resolve-activity --brief {package_name}',
                timeout=10)
            activity = ''
            for ln in (resolve or '').strip().splitlines():
                ln = ln.strip()
                if '/' in ln:
                    activity = ln
                    break
            if not activity:
                return f'{package_name} 未找到入口 Activity (设备无 monkey 且 resolve-activity 无结果)'
            self.run_shell(serial, f'am start -n {activity}', timeout=10)
            return f'已启动 {package_name} (via am start: {activity})'

        # 有 monkey → 正常用 monkey 启动
        out = self.run_shell(serial, f'monkey -p {package_name} -v -v -v 1', timeout=15)
        if 'No activities found' in out:
            return f'{package_name} 没找到入口，检查包名是否正确'
        return f'已启动 {package_name}'

    def stop_app(self, serial, package_name):
        return self.run_shell(serial, f'am force-stop {package_name}', timeout=10).strip()

    def clear_app(self, serial, package_name):
        return self.run_shell(serial, f'pm clear {package_name}', timeout=15).strip()

    def uninstall_app(self, serial, package_name):
        r = self._run([self.adb_path, '-s', serial, 'uninstall', package_name], timeout=30)
        return r.stdout.strip() or r.stderr.strip()

    def install_apk(self, serial, apk_path, extra_args=None, timeout=180):
        """安装 APK。

        extra_args: adb install 的附加参数列表, 例如 ['-r', '-t']。
        路径含空格/中文时由 _cmd_str 自动加引号 (shell=True)。
        返回 (returncode, stdout, stderr), 由上层决定如何展示。
        """
        cmd = [self.adb_path, '-s', serial, 'install']
        if extra_args:
            cmd.extend(str(a) for a in extra_args)
        cmd.append(apk_path)
        r = self._run(cmd, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def install(self, serial, apk_path, extra_args=None, timeout=300, progress_cb=None):
        """完整安装流程：push → pm install → cleanup，返回 (ok:bool, message:str)。

        progress_cb(pct:int, msg:str) 可选，用于 UI 进度反馈
        （推送阶段映射到 5%-75%，安装 80%，清理 95%，完成 100%）。
        apk 文件名中的特殊字符会被替换为 `_`，避免 adb shell 传参问题。
        """
        size = 0
        try:
            size = os.path.getsize(apk_path)
        except OSError:
            pass
        base = os.path.basename(apk_path)
        safe_base = re.sub(r'[^\w.\-]', '_', base)
        remote = f'/data/local/tmp/Super_ADB_install_{int(time.time())}_{safe_base}'

        if progress_cb:
            progress_cb(5, '准备传输 APK...')

        # 阶段 2：推送（流式进度映射到 5%-75%）
        push_cb = (lambda p, m: progress_cb(5 + int(p * 0.70), m)) if progress_cb else None
        try:
            self.push_stream(serial, apk_path, remote, progress_cb=push_cb)
        except AdbError as e:
            return False, f'推送失败: {e}'
        if progress_cb:
            progress_cb(78, '推送完成，准备安装...')

        # 阶段 3：pm install（远端路径，避免本地路径含空格/中文的坑）
        if progress_cb:
            progress_cb(80, '正在安装，请稍候...')
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', 'pm', 'install']
        if extra_args:
            cmd.extend(str(a) for a in extra_args)
        cmd.append(remote)
        try:
            r = self._run_no_shell(cmd, timeout=timeout)
        except AdbError as e:
            try:
                self._run_no_shell(
                    [self.adb_path] + (['-s', serial] if serial else [])
                    + ['shell', 'rm', '-f', remote], timeout=10)
            except AdbError:
                pass
            return False, f'安装失败: {e}'
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode == 0 and 'Success' in (r.stdout or ''):
            if progress_cb:
                progress_cb(95, '清理临时文件...')
            try:
                self._run_no_shell(
                    [self.adb_path] + (['-s', serial] if serial else [])
                    + ['shell', 'rm', '-f', remote], timeout=10)
            except AdbError:
                pass
            if progress_cb:
                progress_cb(100, '安装完成')
            return True, '安装成功。'
        try:
            self._run_no_shell(
                [self.adb_path] + (['-s', serial] if serial else [])
                + ['shell', 'rm', '-f', remote], timeout=10)
        except AdbError:
            pass
        return False, f'安装失败 (returncode={r.returncode}):\n{out.strip()}'

    def get_app_info(self, serial, package_name):
        """获取应用信息 (安装路径 + PID)。

        批量执行 pm path + pidof, 1 次 RTT 替代 2 次, 节省 ~1-2s。
        脚本通过 base64 编码执行, 避免 Windows cmd.exe 嵌套引号问题。
        """
        script = (
            'echo "===PATH==="\n'
            f'pm path {package_name} 2>&1\n'
            'echo "===PID==="\n'
            f'pidof {package_name} 2>&1\n'
            'echo "===END==="\n'
        )
        path = ''
        pid = ''
        try:
            raw = self.run_batch_script(serial, script, timeout=10)
            section = None
            path_lines = []
            for line in (raw or '').splitlines():
                line = line.strip()
                if line == '===PATH===':
                    section = 'path'
                    continue
                if line == '===PID===':
                    section = 'pid'
                    continue
                if line == '===END===':
                    break
                if section == 'path' and line:
                    path_lines.append(line)
                elif section == 'pid' and line:
                    pid = line
            path = '\n'.join(path_lines).replace('package:', '').strip()
        except Exception as e:
            path = f'获取失败: {e}'
            pid = '未运行'
        if not pid:
            pid = '未运行'
        return f'包名: {package_name}\n安装路径: {path or "未安装"}\n进程 PID: {pid}'

    def get_meminfo(self, serial, package_name):
        # 去掉尾随 `/` 与 `pkg/Activity` 中的 Activity 部分，避免非法包名导致解析全空
        pkg = package_name.rstrip('/').split('/', 1)[0].strip() if package_name else package_name
        return self.run_shell(serial, f'dumpsys meminfo {pkg}', timeout=15)

    def logcat_to_desktop(self, serial):
        """打开一个独立终端窗口实时输出 logcat 到桌面文件。"""
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        if sys.platform == 'darwin':
            # macOS：用 osascript 调 Terminal.app
            ts = time.strftime('%Y-%m-%d %H时%M分%S秒')
            log_file = os.path.join(desktop, f'日志{ts}.log')
            script = (
                f'tell application "Terminal"\n'
                f'  activate\n'
                f'  do script "adb -s {serial} logcat -v time > \\"{log_file}\\""\n'
                f'end tell'
            )
            subprocess.Popen(['osascript', '-e', script])
            return '已在终端中启动 logcat，关闭终端窗口或按 Ctrl+C 结束'
        elif sys.platform == 'linux':
            # Linux：尝试用 x-terminal-emulator 或 gnome-terminal
            ts = time.strftime('%Y-%m-%d %H时%M分%S秒')
            log_file = os.path.join(desktop, f'日志{ts}.log')
            cmd = f'adb -s {serial} logcat -v time > "{log_file}"'
            subprocess.Popen(
                ['x-terminal-emulator', '-e', f'bash -c \'{cmd}; exec bash\''],
                stderr=subprocess.DEVNULL
            )
            return '已在终端中启动 logcat，关闭终端窗口或按 Ctrl+C 结束'
        else:
            # Windows：start cmd /k + %date% %time%
            cmd = (f'start cmd /k "adb -s {serial} logcat -v time '
                   f'> \\"{desktop}\\日志%date:~0,4%-%date:~5,2%-%date:~8,2% %time:~0,2%时%time:~3,2%分%time:~6,2%.log\\""')
            subprocess.Popen(cmd, shell=True)
            return '已在独立窗口中启动 logcat，关闭窗口或按 Ctrl+C 结束'


# Android ls -la 常见时间格式：
#   drwxrwxrwx 3 root root 4096 Jul 27 14:05 Alarms
#   drwxrwxrwx 3 root root 4096 2026-07-27 14:05 Alarms
#   drwxrwxrwx 3 root root 4096 Jul 27 2026 Alarms
_MONTHS = {'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'}

# ----------------------------------------------------------------------
# 文件管理封装（供 file_manager_page 使用）
# ----------------------------------------------------------------------
def _decode_adb_output(b):
    """稳健解码 adb 输出字节流：优先 UTF-8，失败回退 GB18030/GBK，最后 latin-1。

    部分老 ROM 的 shell 输出并非 UTF-8（如 GBK 中文环境），若按系统 locale
    直接解码会出现中文文件名乱码；此函数可自动还原正确文本，专治 list_dir
    的中文文件名乱码问题。
    """
    if not b:
        return ''
    for enc in ('utf-8', 'gb18030', 'latin-1'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode('utf-8', errors='replace')


class AdbFileManager(AdbHelper):
    """adb 文件管理：列出目录、上传、下载、删除、重命名、授权(chmod)。"""

    # Android ls -la 常见时间格式：
    #   drwxrwxrwx 3 root root 4096 Jul 27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 2026-07-27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 Jul 27 2026 Alarms

    def list_dir(self, serial, path):
        ls_path = path if path == '/' else path.rstrip('/') + '/'
        cmd = self._base_cmd(serial) + ['shell', 'ls', '-la', f'"{ls_path}"']
        # 直接以字节流执行（shell=False，避开 Windows cmd.exe 对管道/引号的坑），
        # 再按 UTF-8→GBK 顺序稳健解码，根治老 ROM 中文文件名乱码。
        try:
            proc = subprocess.run(
                cmd, capture_output=True, shell=False,
                timeout=20, creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise AdbError('列出目录超时')
        except FileNotFoundError:
            raise AdbError(f'未找到 adb 命令: {self.adb_path}')
        out = _decode_adb_output(proc.stdout)
        err = _decode_adb_output(proc.stderr)
        if proc.returncode != 0 and not out.strip():
            raise AdbError(self._translate_error(err or out))

        entries = []
        for line in out.splitlines():
            line = line.rstrip('\r\n')
            if not line.strip() or line.strip().startswith('total'):
                continue
            parsed = self._parse_ls_line(line, path)
            if parsed:
                entries.append(parsed)
        return entries

    def read_text(self, serial, remote_path, max_bytes=2_000_000):
        """读取文本文件内容（供文件管理器预览用）。

        走 adb pull 落地到临时目录后按 UTF-8→GBK→latin-1 解码，可正确还原
        中文内容；超过 max_bytes 的部分会被截断并返回 truncated 标记。
        """
        import tempfile
        import shutil
        td = tempfile.mkdtemp(prefix='super_adb_read_')
        try:
            self.pull(serial, remote_path, td)
            files = [os.path.join(td, f) for f in os.listdir(td)]
            if not files:
                raise AdbError('拉取内容为空')
            fpath = files[0]
            with open(fpath, 'rb') as fh:
                raw = fh.read()
            truncated = len(raw) > max_bytes
            if truncated:
                raw = raw[:max_bytes]
            text = _decode_adb_output(raw)
            return {'text': text, 'truncated': truncated, 'size': len(raw)}
        finally:
            shutil.rmtree(td, ignore_errors=True)

    @staticmethod
    def _parse_ls_line(line, parent_path):
        """解析 ls -la 单行，支持多种时间/日期格式。"""
        parts = line.strip().split(None, 8)
        if len(parts) < 7:
            return None
        perm = parts[0]
        size_str = parts[4]

        # 判断时间格式
        if re.match(r'\d{4}-\d{2}-\d{2}', parts[5]):
            # YYYY-MM-DD HH:MM name
            if len(parts) < 8:
                return None
            mtime = f"{parts[5]} {parts[6]}"
            name = ' '.join(parts[7:])
        elif parts[5] in _MONTHS:
            # MMM DD HH:MM / MMM DD YYYY name
            if len(parts) < 9:
                return None
            mtime = f"{parts[5]} {parts[6]} {parts[7]}"
            name = ' '.join(parts[8:])
        else:
            # HH:MM name (或其他单字段时间)
            mtime = parts[5]
            name = ' '.join(parts[6:])

        is_dir = perm[0] == 'd'
        is_link = perm[0] == 'l'
        if is_link and ' -> ' in name:
            name = name.split(' -> ', 1)[0].strip()
        if name in ('.', '..'):
            return None
        try:
            size = int(size_str)
        except ValueError:
            size = 0
        base = parent_path.rstrip('/')
        child_path = base + '/' + name if base else '/' + name
        return {
            'name': name, 'path': child_path, 'is_dir': is_dir,
            'size': size, 'perm': perm, 'is_link': is_link, 'mtime': mtime,
        }

    def push(self, serial, local_path, remote_dir):
        # 复用 AdbHelper.push_stream（无进度回调即为静默推送）
        self.push_stream(serial, local_path, remote_dir)
        return '推送成功'

    def pull(self, serial, remote_path, local_dir):
        cmd = self._base_cmd(serial) + ['pull', remote_path, local_dir]
        r = self._run(cmd, timeout=300)
        if r.returncode != 0:
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        return '拉取成功'

    def delete_path(self, serial, path):
        cmd = self._base_cmd(serial) + ['shell', 'rm', '-rf', f'"{path}"']
        r = self._run(cmd, timeout=30)
        if r.returncode != 0 or r.stderr.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        return '删除成功'

    def rename_path(self, serial, old_path, new_path):
        cmd = self._base_cmd(serial) + ['shell', 'mv', f'"{old_path}"', f'"{new_path}"']
        r = self._run(cmd, timeout=30)
        if r.returncode != 0 or r.stderr.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        return '重命名成功'

    def chmod(self, serial, path, mode='777'):
        """修改设备文件/目录权限（adb shell chmod），默认 777。"""
        cmd = self._base_cmd(serial) + ['shell', 'chmod', mode, f'"{path}"']
        r = self._run(cmd, timeout=30)
        if r.returncode != 0 or r.stderr.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        return '授权成功'

    def _base_cmd(self, serial=None):
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        return cmd
