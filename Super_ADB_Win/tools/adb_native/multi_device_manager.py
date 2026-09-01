# -*- coding: utf-8 -*-
"""
多设备管理器
============
管理多个 ADB 连接，支持同时连接多台设备。

用法:
    from tools.adb_native import 多设备管理器
    mgr = 多设备管理器()
    mgr.扫描并连接所有设备()
    for serial, client in mgr.设备列表():
        print(serial, client.执行shell('getprop ro.product.model'))
    mgr.关闭所有()
"""

import threading
from typing import Dict, List, Optional
from .adb_protocol import AdbConnection, 扫描局域网设备


class 多设备管理器:
    """多设备 ADB 连接管理器。"""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._连接: Dict[str, AdbConnection] = {}
        self._锁 = threading.Lock()

    def 扫描设备(self, port: int = 5555, timeout: float = 0.5, 网段: str = None) -> List[dict]:
        """扫描局域网内的 ADB 设备。"""
        return 扫描局域网设备(port, timeout, 网段)

    def 连接设备(self, host: str, port: int = 5555) -> str:
        """连接设备，返回设备标识（host:port）。"""
        key = f'{host}:{port}'
        with self._锁:
            if key in self._连接:
                return key
            conn = AdbConnection(host, port, self.timeout)
            if conn.连接():
                self._连接[key] = conn
                return key
            else:
                conn.关闭()
                raise RuntimeError(f"连接设备失败: {key}（需要设备授权）")

    def 断开设备(self, key: str):
        """断开指定设备。"""
        with self._锁:
            conn = self._连接.pop(key, None)
            if conn:
                conn.关闭()

    def 获取连接(self, key: str) -> Optional[AdbConnection]:
        """获取指定设备的连接。"""
        return self._连接.get(key)

    def 设备列表(self) -> List[tuple]:
        """返回所有已连接设备 [(key, conn), ...]。"""
        return list(self._连接.items())

    def 设备数量(self) -> int:
        """已连接设备数量。"""
        return len(self._连接)

    def 扫描并连接所有设备(self, port: int = 5555, timeout: float = 0.5, 网段: str = None) -> List[str]:
        """扫描局域网并连接所有发现的设备，返回成功连接的设备标识列表。"""
        devices = self.扫描设备(port, timeout, 网段)
        connected = []
        for dev in devices:
            try:
                key = self.连接设备(dev['ip'], dev['port'])
                connected.append(key)
            except Exception:
                pass
        return connected

    def 批量执行shell(self, command: str, timeout: float = 30.0) -> Dict[str, str]:
        """在所有已连接设备上执行 shell 命令，返回 {key: output}。"""
        results = {}
        for key, conn in self._连接.items():
            try:
                results[key] = conn.执行shell(command, timeout)
            except Exception as e:
                results[key] = f'错误: {e}'
        return results

    def 批量推送文件(self, local_path: str, remote_path: str, timeout: float = 60.0) -> Dict[str, bool]:
        """向所有已连接设备推送文件，返回 {key: success}。"""
        results = {}
        for key, conn in self._连接.items():
            try:
                results[key] = conn.推送文件(local_path, remote_path, timeout)
            except Exception:
                results[key] = False
        return results

    def 批量安装应用(self, apk_path: str, timeout: float = 120.0) -> Dict[str, str]:
        """在所有已连接设备上安装 APK，返回 {key: result}。"""
        results = {}
        for key, conn in self._连接.items():
            try:
                results[key] = conn.安装应用(apk_path, timeout)
            except Exception as e:
                results[key] = f'错误: {e}'
        return results

    def 关闭所有(self):
        """关闭所有连接。"""
        with self._锁:
            for conn in self._连接.values():
                try:
                    conn.关闭()
                except Exception:
                    pass
            self._连接.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.关闭所有()

    def __len__(self):
        return len(self._连接)

    def __contains__(self, key):
        return key in self._连接

    def __getitem__(self, key):
        return self._连接[key]

    def __iter__(self):
        return iter(self._连接.items())
