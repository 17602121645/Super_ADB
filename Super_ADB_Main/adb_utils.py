# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— ADB 命令封装层
======================================
统一封装常用 adb 命令，所有耗时调用均通过 subprocess 在后台线程执行，
主线程只负责刷新 UI。
"""

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

    def get_device_info(self, serial):
        props = [
            ('ro.build.version.release', 'Android版本'),
            ('ro.build.version.sdk', 'Api版本'),
            ('ro.product.model', '设备型号'),
            ('ro.product.brand', '厂商名称'),
            ('ro.product.cpu.abi', 'CPU架构'),
        ]
        lines = []
        for prop, name in props:
            try:
                val = self.run_shell(serial, f'getprop {prop}', timeout=5).strip()
            except Exception as e:
                val = f'获取失败: {e}'
            lines.append(f'{name}: {val}')
        try:
            lines.append(f'序列号: {self._run([self.adb_path, "-s", serial, "get-serialno"], timeout=5).stdout.strip()}')
        except Exception as e:
            lines.append(f'序列号: 获取失败: {e}')
        try:
            wm = self.run_shell(serial, 'wm size', timeout=5)
            lines.append(f'屏幕分辨率: {wm.strip()}')
        except Exception as e:
            lines.append(f'屏幕分辨率: 获取失败: {e}')
        try:
            density = self.run_shell(serial, 'wm density', timeout=5)
            lines.append(f'屏幕密度: {density.strip()}')
        except Exception as e:
            lines.append(f'屏幕密度: 获取失败: {e}')
        try:
            mac = self.run_shell(serial, 'cat /sys/class/net/wlan0/address', timeout=5).strip()
            lines.append(f'MAC地址: {mac}')
        except Exception as e:
            lines.append(f'MAC地址: 获取失败: {e}')
        try:
            android_id = self.run_shell(serial, 'settings get secure android_id', timeout=5).strip()
            lines.append(f'Android ID: {android_id}')
        except Exception as e:
            lines.append(f'Android ID: 获取失败: {e}')
        try:
            mem = self.run_shell(serial, 'cat /proc/meminfo | grep MemTotal', timeout=5)
            m = re.search(r'MemTotal:\s*(\d+)', mem)
            if m:
                kb = int(m.group(1))
                lines.append(f'运行内存(RAM): {kb / 1024 / 1024:.1f} GB ({kb} KB)')
            else:
                lines.append('运行内存(RAM): 未解析到 MemTotal')
        except Exception as e:
            lines.append(f'运行内存(RAM): 获取失败: {e}')
        try:
            df = self.run_shell(serial, 'df -h /data', timeout=5)
            parts = df.strip().splitlines()[-1].split()
            if len(parts) >= 5:
                lines.append(f'存储空间(/data): 总计 {parts[1]} / 已用 {parts[2]} / 可用 {parts[3]} (使用率 {parts[4]})')
            else:
                lines.append('存储空间(/data): 未解析到 df 输出')
        except Exception as e:
            lines.append(f'存储空间(/data): 获取失败: {e}')
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
        """启动 scrcpy 投屏；优先使用当前目录 data/scrcpy-win64-v2.6。"""
        base = os.path.dirname(os.path.abspath(__file__))
        scrcpy_dir = os.path.join(base, 'data', 'scrcpy-win64-v2.6')
        if not os.path.isdir(scrcpy_dir):
            scrcpy_dir = os.path.join(os.getcwd(), 'data', 'scrcpy-win64-v2.6')
        if os.path.isdir(scrcpy_dir):
            cmd = f'cd /d "{scrcpy_dir}" && scrcpy -s {serial}'
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

    def get_app_info(self, serial, package_name):
        try:
            path = self.run_shell(serial, f'pm path {package_name}', timeout=10).strip()
            path = path.replace('package:', '').strip()
        except Exception as e:
            path = f'获取失败: {e}'
        try:
            pid = self.run_shell(serial, f'pidof {package_name}', timeout=5).strip()
        except Exception:
            pid = '未运行'
        return f'包名: {package_name}\n安装路径: {path}\n进程 PID: {pid}'

    def get_meminfo(self, serial, package_name):
        return self.run_shell(serial, f'dumpsys meminfo {package_name}', timeout=15)

    def logcat_to_desktop(self, serial):
        """打开一个独立的 cmd 窗口实时输出 logcat 到桌面文件。"""
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
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
