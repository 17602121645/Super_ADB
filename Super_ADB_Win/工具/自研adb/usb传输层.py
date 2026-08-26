# -*- coding: utf-8 -*-
"""
USB Transport for 自研 ADB
==========================
通过 pyusb 直接访问 Android ADB USB 设备，不依赖 adb server。

ADB USB 设备特征:
  - Interface Class: 255 (Vendor Specific)
  - SubClass: 66
  - Protocol: 1
  - Bulk OUT endpoint: 发送 ADB 消息
  - Bulk IN endpoint: 接收 ADB 消息

依赖: pip install pyusb
Windows 需安装 WinUSB 驱动（可用 Zadig 工具替换）
"""

import usb.core
import usb.util
from typing import Optional, List, Tuple

# 已知的 ADB USB 厂商 ID（部分）
ADB_VID_LIST = [
    0x18D1,  # Google
    0x05C6,  # Qualcomm
    0x0BB4,  # HTC
    0x04E8,  # Samsung
    0x0FCE,  # Sony Ericsson
    0x04DD,  # Sharp
    0x091E,  # LG
    0x04B4,  # Cypress
    0x0B05,  # Asus
    0x0489,  # Foxconn
    0x0471,  # Philips
    0x04DA,  # Panasonic
    0x054C,  # Sony
    0x0F1C,  # Rockchip
    0x1782,  # Spreadtrum
    0x0BB4,  # ZTE
    0x2A47,  # Xiaomi
    0x2717,  # Xiaomi (old)
    0x12D1,  # Huawei
    0x1D4D,  # Allwinner (当贝盒子等)
    0x2207,  # Rockchip
    0x17EF,  # Lenovo
    0x2A49,  # OnePlus
]

# ADB 接口特征
ADB_INTERFACE_CLASS = 255
ADB_INTERFACE_SUBCLASS = 66
ADB_INTERFACE_PROTOCOL = 1


class UsbDeviceInfo:
    """USB 设备信息。"""

    def __init__(self, dev, interface, ep_in, ep_out):
        self.dev = dev
        self.interface = interface
        self.ep_in = ep_in
        self.ep_out = ep_out
        self.vid = dev.idVendor
        self.pid = dev.idProduct
        self.manufacturer = usb.util.get_string(dev, dev.iManufacturer) or ''
        self.product = usb.util.get_string(dev, dev.iProduct) or ''
        self.serial = usb.util.get_string(dev, dev.iSerialNumber) or ''

    @property
    def 标识(self) -> str:
        return self.serial or f'{self.vid:04x}:{self.pid:04x}'

    def __repr__(self):
        return f'<UsbDevice {self.标识} {self.manufacturer} {self.product}>'


def 枚举adb设备() -> List[UsbDeviceInfo]:
    """枚举所有 ADB USB 设备。"""
    devices = []
    for vid in ADB_VID_LIST:
        try:
            for dev in usb.core.find(find_all=True, idVendor=vid):
                try:
                    info = _查找adb接口(dev)
                    if info:
                        devices.append(info)
                except Exception:
                    continue
        except Exception:
            continue
    return devices


def _查找adb接口(dev) -> Optional[UsbDeviceInfo]:
    """在设备中查找 ADB 接口。"""
    try:
        for cfg in dev:
            for intf in cfg:
                if (intf.bInterfaceClass == ADB_INTERFACE_CLASS and
                        intf.bInterfaceSubClass == ADB_INTERFACE_SUBCLASS and
                        intf.bInterfaceProtocol == ADB_INTERFACE_PROTOCOL):
                    ep_in = None
                    ep_out = None
                    for ep in intf:
                        if ep.bEndpointAddress & 0x80:
                            ep_in = ep
                        else:
                            ep_out = ep
                    if ep_in and ep_out:
                        return UsbDeviceInfo(dev, intf, ep_in, ep_out)
    except Exception:
        return None
    return None


class UsbTransport:
    """USB 传输层，与 AdbConnection 集成。

    用法:
        transport = UsbTransport(device_info)
        transport.打开()
        transport.发送(data)
        data = transport.接收(length)
        transport.关闭()
    """

    def __init__(self, device_info: UsbDeviceInfo, timeout: int = 5000):
        self.device_info = device_info
        self.timeout = timeout
        self._claimed = False

    def 打开(self):
        """打开 USB 设备并声明接口。"""
        dev = self.device_info.dev
        #  detach kernel driver (Linux)
        if dev.is_kernel_driver_active(self.device_info.interface.bInterfaceNumber):
            try:
                dev.detach_kernel_driver(self.device_info.interface.bInterfaceNumber)
            except Exception:
                pass
        # 声明接口
        usb.util.claim_interface(dev, self.device_info.interface.bInterfaceNumber)
        self._claimed = True

    def 发送(self, data: bytes) -> int:
        """通过 Bulk OUT 端点发送数据。"""
        return self.device_info.ep_out.write(data, timeout=self.timeout)

    def 接收(self, length: int) -> bytes:
        """从 Bulk IN 端点接收数据。"""
        data = self.device_info.ep_in.read(length, timeout=self.timeout)
        return bytes(data)

    def 关闭(self):
        """关闭 USB 设备。"""
        if self._claimed:
            try:
                usb.util.release_interface(
                    self.device_info.dev,
                    self.device_info.interface.bInterfaceNumber
                )
            except Exception:
                pass
            self._claimed = False
        try:
            usb.util.dispose_resources(self.device_info.dev)
        except Exception:
            pass

    def __enter__(self):
        self.打开()
        return self

    def __exit__(self, *args):
        self.关闭()


def 测试usb设备():
    """测试枚举 USB 设备。"""
    print('枚举 ADB USB 设备...')
    devices = 枚举adb设备()
    print(f'找到 {len(devices)} 个设备:')
    for d in devices:
        print(f'  {d}')
    return devices


class UsbHotplug:
    """USB 热插拔监视器。

    用法:
        hotplug = UsbHotplug()
        hotplug.启动(on_connect=回调, on_disconnect=回调)
        hotplug.停止()
    """

    def __init__(self):
        self._running = False
        self._线程 = None
        self._on_connect = None
        self._on_disconnect = None
        self._已知设备 = set()

    def 启动(self, on_connect=None, on_disconnect=None, interval: float = 2.0):
        """启动热插拔监视。

        Args:
            on_connect: 设备插入回调 (device_info)
            on_disconnect: 设备拔出回调 (serial)
            interval: 轮询间隔（秒）
        """
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._running = True
        self._已知设备 = set()

        def _监视():
            import time
            while self._running:
                try:
                    current = 枚举adb设备()
                    current_ids = {d.标识 for d in current}

                    # 检测新设备
                    for d in current:
                        if d.标识 not in self._已知设备:
                            if self._on_connect:
                                try:
                                    self._on_connect(d)
                                except Exception:
                                    pass

                    # 检测拔出设备
                    for sid in self._已知设备:
                        if sid not in current_ids:
                            if self._on_disconnect:
                                try:
                                    self._on_disconnect(sid)
                                except Exception:
                                    pass

                    self._已知设备 = current_ids
                except Exception:
                    pass
                time.sleep(interval)

        self._线程 = threading.Thread(target=_监视, daemon=True)
        self._线程.start()

    def 停止(self):
        """停止热插拔监视。"""
        self._running = False
        if self._线程:
            self._线程.join(timeout=3)
            self._线程 = None

    @property
    def 运行中(self) -> bool:
        return self._running


import threading


if __name__ == '__main__':
    测试usb设备()
