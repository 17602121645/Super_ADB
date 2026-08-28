# -*- coding: utf-8 -*-
"""
自研 ADB 包
==========
纯 Python 实现 ADB 协议，直接连接设备 5555 端口，不依赖外部 adb server。

用法:
    from 工具.自研adb import 自研adb客户端
    client = 自研adb客户端('192.168.1.100')
    client.连接()
    print(client.执行shell('getprop ro.build.version.release'))
    client.关闭()
"""

from .adb协议 import (  # noqa
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
from .自研adb客户端 import 自研adb客户端
from .多设备管理器 import 多设备管理器
from .usb传输层 import UsbTransport, UsbDeviceInfo, 枚举adb设备, UsbHotplug
from .usb连接 import UsbAdbConnection
# scrcpy 会话依赖 PyQt5，导入失败时不影响 ADB 核心功能
try:
    from .scrcpy会话 import ScrcpySession
except Exception:
    ScrcpySession = None


__all__ = [
    '自研adb客户端',
    '多设备管理器',
    'ScrcpySession',
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
