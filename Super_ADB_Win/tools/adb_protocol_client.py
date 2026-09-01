# -*- coding: utf-8 -*-
"""
纯 Python ADB 协议客户端（B 方案）
==============================
通过 socket 连接本地 adb server (127.0.0.1:5037)，用 ADB 协议直接通信，
替代 subprocess 调用 adb.exe。首次调用时若 server 未启动，自动用 adb start-server 启动。

ADB 协议:
  发送: 4字节十六进制长度 + 命令内容
  响应: 4字节状态 (OKAY/FAIL) + 数据

依赖: 无第三方依赖，纯标准库
"""

import os
import socket
import struct
import subprocess
import time
import sys
from typing import Optional, List, Tuple

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

_ADB_SERVER_PORT = 5037
_ADB_HOST = '127.0.0.1'


class AdbProtocolError(Exception):
    """ADB 协议错误。"""
    pass


class AdbServerNotRunning(AdbProtocolError):
    """ADB server 未运行。"""
    pass


def _查找adb路径() -> Optional[str]:
    """查找 adb 可执行文件路径（用于启动 server）。"""
    # 1. 环境变量
    adb_path = os.environ.get('ADB_PATH')
    if adb_path and os.path.isfile(adb_path):
        return adb_path

    # 2. PATH 中查找
    for d in os.environ.get('PATH', '').split(os.pathsep):
        for name in ('adb.exe', 'adb'):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p

    # 3. 项目内置路径
    import platform
    sysname = platform.system().lower()
    if sysname == 'windows':
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-windows',
                              'platform-tools', 'adb.exe')
    elif sysname == 'darwin':
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-darwin',
                              'platform-tools', 'adb')
    else:
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-linux',
                              'platform-tools', 'adb')

    here = os.path.dirname(os.path.abspath(__file__))
    for root in (os.path.dirname(here), here, os.getcwd()):
        full = os.path.join(root, suffix)
        if os.path.isfile(full):
            return os.path.abspath(full)

    # 回退：scrcpy 发行包自带的官方 adb（删除 vendor/adb 后，启动 server 仍可用）
    try:
        from tools.adb_tools import Adb设备操作 as _Adb操作类
        scrcpy_dirs = _Adb操作类.查找scrcpy目录()
        # 兼容两种返回形态：字符串（单个路径）/ 列表（多版本）
        if isinstance(scrcpy_dirs, str):
            scrcpy_dirs = [scrcpy_dirs]
        for scrcpy_dir in scrcpy_dirs or []:
            cand = os.path.join(scrcpy_dir,
                                'adb.exe' if sysname == 'windows' else 'adb')
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    except Exception:
        pass
    return None


def 启动adb服务器(adb_path: str = None) -> bool:
    """启动 adb server（如果未运行）。返回是否成功。"""
    if 检查server运行():
        return True
    if adb_path is None:
        adb_path = _查找adb路径()
    if adb_path is None:
        raise AdbProtocolError("未找到 adb 可执行文件，无法启动 server")
    try:
        subprocess.run(
            [adb_path, 'start-server'],
            capture_output=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        time.sleep(0.5)
        return 检查server运行()
    except Exception:
        return False


def 检查server运行() -> bool:
    """检查 adb server 是否在运行。"""
    try:
        sock = socket.create_connection((_ADB_HOST, _ADB_SERVER_PORT), timeout=1)
        sock.close()
        return True
    except Exception:
        return False


class Adb连接:
    """单个 ADB socket 连接，用于执行一条命令。"""

    def __init__(self, timeout: float = 10.0):
        self.sock = socket.create_connection((_ADB_HOST, _ADB_SERVER_PORT), timeout=timeout)
        self.sock.settimeout(timeout)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def 发送命令(self, cmd: str):
        """发送 ADB 命令（4字节长度 + 内容）。"""
        data = cmd.encode('utf-8')
        header = f'{len(data):04x}'.encode('ascii')
        self.sock.sendall(header + data)

    def 读取状态(self) -> str:
        """读取 4 字节状态码（OKAY/FAIL）。"""
        status = self._精确接收(4)
        return status.decode('ascii', errors='replace')

    def 读取数据(self) -> bytes:
        """读取数据（先读4字节长度，再读内容）。"""
        length_hex = self._精确接收(4).decode('ascii')
        length = int(length_hex, 16)
        if length == 0:
            return b''
        return self._精确接收(length)

    def 读取全部(self) -> bytes:
        """读取直到连接关闭（用于 shell 输出）。"""
        chunks = []
        while True:
            try:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            except socket.timeout:
                break
        return b''.join(chunks)

    def _精确接收(self, n: int) -> bytes:
        """精确读取 n 字节。"""
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise AdbProtocolError("连接断开")
            buf += chunk
        return buf


class Adb协议客户端:
    """纯 Python ADB 客户端，通过 socket 与 adb server 通信。

    用法:
        client = Adb协议客户端()
        devices = client.获取设备列表()
        output = client.执行shell('emulator-5554', 'getprop ro.build.version.release')
        client.推送文件('emulator-5554', 'local.txt', '/sdcard/local.txt')
    """

    def __init__(self, adb_path: str = None, timeout: float = 10.0,
                 自动启动server: bool = True):
        self.adb_path = adb_path or _查找adb路径()
        self.timeout = timeout
        self.自动启动server = 自动启动server
        self._确保server运行()

    def _确保server运行(self):
        if not 检查server运行():
            if self.自动启动server:
                启动adb服务器(self.adb_path)
            else:
                raise AdbServerNotRunning("ADB server 未运行")

    def _新建连接(self) -> Adb连接:
        """新建一个 ADB 连接。"""
        return Adb连接(timeout=self.timeout)

    def _诊断设备不可用(self, serial: str, err) -> str:
        """命令失败时诊断：查官方 adb devices，若目标设备离线/未连接（单客户端
        盒子唯一槽位被占用时多见此状），改写为可操作提示。返回改写后的消息。
        """
        if not serial:
            return str(err)
        low = str(err).lower()
        if not any(k in low for k in (
                'offline', 'timed out', 'timeout', '超时', 'cannot connect',
                'connection', 'not found', '断开', 'closed')):
            return str(err)
        target = serial
        try:
            import subprocess as _sp
            adb = self.adb_path or _查找adb路径() or 'adb'
            run_kwargs = dict(capture_output=True, text=True, timeout=3)
            if os.name == 'nt':
                run_kwargs['creationflags'] = 0x08000000
            r = _sp.run([adb, 'devices'], **run_kwargs)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == target:
                    st = parts[1]
                    if st == 'offline':
                        return (f'设备 {target} 在官方 adb server 侧为 offline：'
                                f'单客户端盒子唯一槽位可能被占用，请先执行 '
                                f'adb disconnect {target}，并确认无自研直连残留后重试')
                    if st in ('unauthorized', 'authorizing'):
                        return (f'设备 {target} 在官方 adb server 侧为 {st}（未授权）：'
                                f'请在设备弹窗上确认授权后重试')
                    return f'设备 {target} 官方 adb 状态为 {st}，操作失败: {err}'
            return (f'设备 {target} 未出现在官方 adb devices 中（server 未能连接）：'
                    f'可能被其他连接占用唯一槽位，请先 adb connect {target} '
                    f'或断开其他占用后重试')
        except Exception:
            return str(err)

    def _host命令(self, cmd: str) -> str:
        """执行 host 命令，返回字符串结果。"""
        with self._新建连接() as conn:
            conn.发送命令(cmd)
            status = conn.读取状态()
            if status != 'OKAY':
                err = conn.读取数据().decode('utf-8', errors='replace')
                raise AdbProtocolError(f"命令失败: {cmd}, 错误: {err}")
            data = conn.读取数据()
            return data.decode('utf-8', errors='replace')

    # ─────────────────── 基础查询 ───────────────────

    def 获取版本(self) -> int:
        """获取 adb server 版本号。"""
        result = self._host命令('host:version')
        return int(result, 16)

    def 获取设备列表(self) -> List[dict]:
        """获取已连接设备列表。

        返回: [{'serial': 'emulator-5554', 'state': 'device'}, ...]
        """
        result = self._host命令('host:devices')
        devices = []
        for line in result.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices.append({'serial': parts[0], 'state': parts[1]})
        return devices

    def 获取设备列表详细(self) -> List[dict]:
        """获取设备列表（含产品信息）。"""
        result = self._host命令('host:devices-l')
        devices = []
        for line in result.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                dev = {'serial': parts[0], 'state': parts[1]}
                # 解析附加信息
                for p in parts[2:]:
                    if ':' in p:
                        k, v = p.split(':', 1)
                        dev[k] = v
                devices.append(dev)
        return devices

    # ─────────────────── Shell 命令 ───────────────────

    def 执行shell(self, serial: str, command: str, timeout: float = 30.0) -> str:
        """执行 adb shell 命令，返回 stdout。

        Args:
            serial: 设备序列号
            command: shell 命令
            timeout: 超时时间（秒）
        """
        try:
            with self._新建连接() as conn:
                conn.sock.settimeout(timeout)
                # 先切换到目标设备
                conn.发送命令(f'host:transport:{serial}')
                status = conn.读取状态()
                if status != 'OKAY':
                    err = conn.读取数据().decode('utf-8', errors='replace')
                    raise AdbProtocolError(f"切换设备失败: {err}")
                # 发送 shell 命令
                conn.发送命令(f'shell:{command}')
                status = conn.读取状态()
                if status != 'OKAY':
                    err = conn.读取数据().decode('utf-8', errors='replace')
                    raise AdbProtocolError(f"shell 命令失败: {err}")
                # 读取全部输出
                output = conn.读取全部()
                return output.decode('utf-8', errors='replace')
        except AdbProtocolError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))
        except OSError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))

    def 执行shell原始(self, serial: str, command: str, timeout: float = 30.0) -> bytes:
        """执行 shell 命令，返回原始字节。"""
        try:
            with self._新建连接() as conn:
                conn.sock.settimeout(timeout)
                conn.发送命令(f'host:transport:{serial}')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("切换设备失败")
                conn.发送命令(f'shell:{command}')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("shell 命令失败")
                return conn.读取全部()
        except AdbProtocolError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))
        except OSError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))

    # ─────────────────── 文件传输 ───────────────────

    def 推送文件(self, serial: str, local_path: str, remote_path: str,
                 timeout: float = 60.0) -> bool:
        """推送文件到设备。

        使用 sync: 协议的 SEND 命令。
        """
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"本地文件不存在: {local_path}")

        file_size = os.path.getsize(local_path)
        try:
            with self._新建连接() as conn:
                conn.sock.settimeout(timeout)
                # 切换设备
                conn.发送命令(f'host:transport:{serial}')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("切换设备失败")
                # 进入 sync 模式
                conn.发送命令('sync:')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("进入 sync 模式失败")

                # SEND 命令: "SEND" + 8字节(路径长度+模式) + 路径
                # 路径格式: <remote_path>,<权限>
                send_cmd = b'SEND'
                path_with_mode = f'{remote_path},0777'.encode('utf-8')
                conn.sock.sendall(send_cmd + struct.pack('<I', len(path_with_mode)) + path_with_mode)

                # 发送文件数据: "DATA" + 4字节数据长度 + 数据
                with open(local_path, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        conn.sock.sendall(b'DATA' + struct.pack('<I', len(chunk)) + chunk)

                # DONE 命令: "DONE" + 4字节文件修改时间
                mtime = int(os.path.getmtime(local_path))
                conn.sock.sendall(b'DONE' + struct.pack('<I', mtime))

                # 读取响应
                response = conn._精确接收(4)
                if response != b'OKAY':
                    raise AdbProtocolError(f"推送失败，响应: {response}")
                return True
        except AdbProtocolError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))
        except OSError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))

    def 拉取文件(self, serial: str, remote_path: str, local_path: str,
                 timeout: float = 60.0) -> bool:
        """从设备拉取文件。

        使用 sync: 协议的 RECV 命令。
        """
        try:
            with self._新建连接() as conn:
                conn.sock.settimeout(timeout)
                conn.发送命令(f'host:transport:{serial}')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("切换设备失败")
                conn.发送命令('sync:')
                if conn.读取状态() != 'OKAY':
                    raise AdbProtocolError("进入 sync 模式失败")

                # RECV 命令: "RECV" + 4字节路径长度 + 路径
                path_bytes = remote_path.encode('utf-8')
                conn.sock.sendall(b'RECV' + struct.pack('<I', len(path_bytes)) + path_bytes)

                # 接收数据
                with open(local_path, 'wb') as f:
                    while True:
                        header = conn._精确接收(8)
                        cmd = header[:4]
                        length = struct.unpack('<I', header[4:8])[0]
                        if cmd == b'DATA':
                            data = conn._精确接收(length)
                            f.write(data)
                        elif cmd == b'DONE':
                            break
                        elif cmd == b'FAIL':
                            err = conn._精确接收(length).decode('utf-8', errors='replace')
                            raise AdbProtocolError(f"拉取失败: {err}")
                        else:
                            raise AdbProtocolError(f"未知响应: {cmd}")
                return True
        except AdbProtocolError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))
        except OSError as e:
            raise AdbProtocolError(self._诊断设备不可用(serial, e))

    # ─────────────────── 应用管理 ───────────────────

    def 安装应用(self, serial: str, apk_path: str, timeout: float = 120.0) -> str:
        """安装 APK。先用 push 到 /data/local/tmp，再 pm install。"""
        if not os.path.isfile(apk_path):
            raise FileNotFoundError(f"APK 不存在: {apk_path}")
        remote = f'/data/local/tmp/{os.path.basename(apk_path)}'
        self.推送文件(serial, apk_path, remote, timeout=timeout)
        result = self.执行shell(serial, f'pm install -r "{remote}"', timeout=timeout)
        # 清理临时文件
        try:
            self.执行shell(serial, f'rm "{remote}"', timeout=10)
        except Exception:
            pass
        return result

    def 卸载应用(self, serial: str, package: str, timeout: float = 30.0) -> str:
        """卸载应用。"""
        return self.执行shell(serial, f'pm uninstall {package}', timeout=timeout)

    # ─────────────────── 设备控制 ───────────────────

    def 重启设备(self, serial: str, mode: str = 'system'):
        """重启设备。mode: system/recovery/bootloader/sideload。"""
        with self._新建连接() as conn:
            conn.发送命令(f'host:transport:{serial}')
            if conn.读取状态() != 'OKAY':
                raise AdbProtocolError("切换设备失败")
            conn.发送命令(f'reboot:{mode}')
            # reboot 命令不需要读响应

    def 关闭server(self):
        """关闭 adb server。"""
        try:
            with self._新建连接() as conn:
                conn.发送命令('host:kill')
                conn.读取状态()
        except Exception:
            pass

    # ─────────────────── 端口转发 ───────────────────

    def 端口转发(self, serial: str, local_port: int, remote: str) -> bool:
        """设置端口转发: adb forward tcp:local_port remote。

        remote 格式如: localabstract:scrcpy 或 tcp:8080
        """
        cmd = f'host-serial:{serial}:forward:tcp:{local_port};{remote}'
        result = self._host命令(cmd)
        return True

    def 取消端口转发(self, serial: str, local_port: int) -> bool:
        """取消端口转发。端口不存在时静默忽略（不抛异常）。"""
        cmd = f'host-serial:{serial}:killforward:tcp:{local_port}'
        try:
            self._host命令(cmd)
        except AdbError:
            pass  # 端口转发不存在，忽略
        return True

    def 列出转发(self) -> str:
        """列出所有端口转发。"""
        return self._host命令('host:list-forward')


# ─────────────────── 便捷函数 ───────────────────

def 快速执行shell(serial: str, command: str, timeout: float = 30.0) -> str:
    """快速执行 shell 命令（自动创建客户端）。"""
    client = Adb协议客户端(timeout=timeout)
    return client.执行shell(serial, command, timeout=timeout)


def 快速获取设备列表() -> List[dict]:
    """快速获取设备列表（自动创建客户端）。"""
    client = Adb协议客户端()
    return client.获取设备列表()


if __name__ == '__main__':
    # 简单测试
    print('ADB server 运行中:', 检查server运行())
    client = Adb协议客户端()
    print('ADB 版本:', hex(client.获取版本()))
    devices = client.获取设备列表详细()
    print('设备列表:')
    for d in devices:
        print(f'  {d}')
    if devices:
        serial = devices[0]['serial']
        print(f'Android 版本:', client.执行shell(serial, 'getprop ro.build.version.release').strip())
