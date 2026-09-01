# -*- coding: utf-8 -*-
"""
自研 ADB 包
==========
纯 Python 实现 ADB 协议，直接连接设备 5555 端口，不依赖外部 adb server。

用法:
    from tools.adb_native import 自研adb客户端
    client = 自研adb客户端('192.168.1.100')
    client.连接()
    print(client.执行shell('getprop ro.build.version.release'))
    client.关闭()
"""

from .adb_protocol import (  # noqa
    AdbConnection,
    AdbMessage,
    打包消息,
    解包消息,
    扫描局域网设备,
    获取已连接设备,
    CMD_CNXN,
    CMD_AUTH,
    CMD_OPEN,
    CMD_OKAY,
    CMD_WRTE,
    CMD_CLSE,
    STATE_OFFLINE,
    STATE_AUTH,
    STATE_DEVICE,
)
from .adb_client import 自研adb客户端
from .multi_device_manager import 多设备管理器
from .usb_transport import UsbTransport, UsbDeviceInfo, 枚举adb设备, UsbHotplug
from .usb_connection import UsbAdbConnection


__all__ = [
    '自研adb客户端',
    '多设备管理器',
    'AdbConnection',
    'UsbAdbConnection',
    'UsbTransport',
    'UsbDeviceInfo',
    'UsbHotplug',
    '枚举adb设备',
    'AdbMessage',
    '打包消息',
    '解包消息',
    '扫描局域网设备',
    '获取已连接设备',
    'STATE_OFFLINE',
    'STATE_AUTH',
    'STATE_DEVICE',
]
