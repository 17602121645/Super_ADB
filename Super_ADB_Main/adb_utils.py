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
import subprocess
import time
import sys

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ----------------------------------------------------------------------
# 通用配置读写（UI 状态持久化）
# ----------------------------------------------------------------------
def _config_path(name):
    """配置文件路径：macOS 打包后用 ~/Library/Application Support/Super_ADB/。"""
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        base = os.path.expanduser('~/Library/Application Support/Super_ADB')
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, name)
    base = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    return os.path.join(base, name)


def load_json_config(name):
    """读取配置，失败/缺失时返回空 dict，由调用方回退默认值。"""
    try:
        with open(_config_path(name), 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_json_config(name, data):
    try:
        with open(_config_path(name), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[配置] 保存 {_config_path(name)} 失败: {e}')


def format_device_label(d: dict) -> str:
    """设备下拉框条目显示文本（展示层，与业务逻辑分离）。"""
    return f"{d.get('model') or d.get('serial')}  [{d.get('serial')}]"


class AdbError(Exception):
    pass


class AdbHelper:
    """ADB 命令辅助类：提供设备扫描、命令执行、常用信息获取等能力。"""

    def __init__(self, adb_path='adb', log_callback=None):
        self.adb_path = adb_path
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
                self.log_callback(f'执行命令: {cmd_str}')
            except Exception:
                pass
        try:
            result = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
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

    def connect(self, ip):
        if ':' not in ip:
            ip = f'{ip}:5555'
        r = self._run([self.adb_path, 'connect', ip], timeout=15)
        return r.stdout.strip() or r.stderr.strip()

    def disconnect(self, serial=None):
        cmd = [self.adb_path, 'disconnect']
        if serial:
            cmd.append(serial)
        r = self._run(cmd, timeout=10)
        return r.stdout.strip() or r.stderr.strip()

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
                self.log_callback(f'执行命令: {" ".join(str(p) for p in cmd_list)}')
            except Exception:
                pass
        try:
            return subprocess.run(
                cmd_list, capture_output=True, text=True,
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


class AdbDeviceOps(AdbHelper):
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
        self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
        self._run([self.adb_path, '-s', serial, 'remount'], timeout=10)
        self.run_shell(serial, 'mount -o rw,remount /system', timeout=10)
        return '已尝试获取 system 读写权限'

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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
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
        proc.wait(timeout=10)
        r = self._run([self.adb_path, '-s', serial, 'pull', remote, local], timeout=60)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout)
        self.run_shell(serial, f'rm {remote}', timeout=5)
        return local

    def scrcpy(self, serial):
        """启动 scrcpy 投屏；优先使用当前目录 data/ 下的 scrcpy。"""
        base = os.path.dirname(os.path.abspath(__file__))
        # 按平台选择 scrcpy 目录名
        if sys.platform == 'darwin':
            scrcpy_subdir = 'scrcpy-mac-v2.6'
        elif sys.platform == 'linux':
            scrcpy_subdir = 'scrcpy-linux-v2.6'
        else:
            scrcpy_subdir = 'scrcpy-win64-v2.6'
        scrcpy_dir = os.path.join(base, 'data', scrcpy_subdir)
        if not os.path.isdir(scrcpy_dir):
            scrcpy_dir = os.path.join(os.getcwd(), 'data', scrcpy_subdir)
        if os.path.isdir(scrcpy_dir):
            # Windows cmd 用 cd /d，macOS/Linux 用 cd
            cd_flag = '/d ' if sys.platform == 'win32' else ''
            cmd = f'cd {cd_flag}"{scrcpy_dir}" && scrcpy -s {serial}'
        else:
            cmd = f'scrcpy -s {serial}'
        subprocess.Popen(cmd, shell=True, creationflags=CREATE_NO_WINDOW)
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
class AdbFileManager(AdbHelper):
    """adb 文件管理：列出目录、上传、下载、删除、重命名。"""

    # Android ls -la 常见时间格式：
    #   drwxrwxrwx 3 root root 4096 Jul 27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 2026-07-27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 Jul 27 2026 Alarms

    def list_dir(self, serial, path):
        ls_path = path if path == '/' else path.rstrip('/') + '/'
        cmd = self._base_cmd(serial) + ['shell', 'ls', '-la', f'"{ls_path}"']
        r = self._run(cmd, timeout=20)
        if r.returncode != 0 and not r.stdout.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))

        entries = []
        for line in r.stdout.splitlines():
            line = line.rstrip('\r\n')
            if not line.strip() or line.strip().startswith('total'):
                continue
            parsed = self._parse_ls_line(line, path)
            if parsed:
                entries.append(parsed)
        return entries

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
        cmd = self._base_cmd(serial) + ['push', local_path, remote_dir]
        r = self._run(cmd, timeout=300)
        if r.returncode != 0:
            raise AdbError(self._translate_error(r.stderr or r.stdout))
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

    def _base_cmd(self, serial=None):
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        return cmd
