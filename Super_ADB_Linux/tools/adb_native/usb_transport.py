# -*- coding: utf-8 -*-
"""
USB Transport for 自研 ADB
==========================
跨平台 USB 传输层，统一接口。

平台策略:
  - Windows: 优先使用原生 WinUSB (usb_window_native.py)，
             不依赖 pyusb/libusb，和官方 adb.exe 行为一致；
             原生不可用时回退到 pyusb。
  - Linux/macOS: 使用 pyusb (libusb)。

ADB USB 设备特征:
  - Interface Class: 255 (Vendor Specific)
  - SubClass: 66
  - Protocol: 1
  - Bulk OUT endpoint: 发送 ADB 消息
  - Bulk IN endpoint: 接收 ADB 消息

依赖:
  - Windows 原生模式: 无额外依赖（系统自带 setupapi/winusb）
  - pyusb 模式: pip install pyusb
    - Windows 需安装 WinUSB 驱动（可用 Zadig 工具替换）
    - Linux 需配置 udev 规则或使用 root
"""

import sys
import threading
from typing import Optional, List, Tuple

# ═══════════════════════════════════════════════════════════════
# 平台检测与后端选择
# ═══════════════════════════════════════════════════════════════

_IS_WINDOWS = sys.platform == 'win32'

# 枚举诊断信息（供上层排查"找不到USB设备"问题）
_枚举诊断 = []

# 尝试加载 Windows 原生 WinUSB 后端
_native_win = None
_native_error = None
if _IS_WINDOWS:
    try:
        from . import usb_window_native as _native_win
    except (ImportError, SystemError):
        # 直接运行脚本时相对导入失败，回退到绝对导入
        try:
            import usb_window_native as _native_win
        except Exception as e:
            _native_error = e
    except Exception as e:
        _native_error = e

# 尝试加载 pyusb 后端（作为回退 / Linux/macOS 主力）
_pyusb = None
_pyusb_error = None
try:
    import usb.core
    import usb.util
    _pyusb = True
except Exception as e:
    _pyusb_error = e


# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

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
    0x2A47,  # Xiaomi
    0x2717,  # Xiaomi (old)
    0x12D1,  # Huawei
    0x339B,  # Honor / 荣耀
    0x1D4D,  # Allwinner (当贝盒子等)
    0x2207,  # Rockchip
    0x17EF,  # Lenovo
    0x2A49,  # OnePlus
    0x04E8,  # Samsung
]

# ADB 接口特征
ADB_INTERFACE_CLASS = 255
ADB_INTERFACE_SUBCLASS = 66
ADB_INTERFACE_PROTOCOL = 1


# ═══════════════════════════════════════════════════════════════
# 统一设备信息类
# ═══════════════════════════════════════════════════════════════

class UsbDeviceInfo:
    """USB 设备信息（统一接口，兼容原生 WinUSB 和 pyusb）。

    属性:
        vid/pid: 厂商 ID / 产品 ID
        manufacturer/product/serial: 字符串描述符（可能为空）
        标识: 唯一标识（优先 serial，其次 vid:pid）
        _backend: 'native' 或 'pyusb'
        _native_info: 原生 WinUsbDeviceInfo（native 后端）
        _pyusb_dev/_pyusb_intf/_pyusb_ep_in/_pyusb_ep_out: pyusb 对象（pyusb 后端）
    """

    def __init__(self, vid: int, pid: int,
                 manufacturer: str = '', product: str = '', serial: str = ''):
        self.vid = vid
        self.pid = pid
        self.manufacturer = manufacturer
        self.product = product
        self.serial = serial
        self._backend = ''
        # native 后端
        self._native_info = None
        # pyusb 后端
        self._pyusb_dev = None
        self._pyusb_intf = None
        self._pyusb_ep_in = None
        self._pyusb_ep_out = None

    @property
    def 标识(self) -> str:
        return self.serial or f'{self.vid:04x}:{self.pid:04x}'

    def __repr__(self):
        return f'<UsbDevice {self.标识} {self.manufacturer} {self.product} [{self._backend}]>'


# ═══════════════════════════════════════════════════════════════
# 设备枚举
# ═══════════════════════════════════════════════════════════════

def 枚举adb设备() -> List[UsbDeviceInfo]:
    """枚举所有 ADB USB 设备。

    平台策略:
      - Windows: 优先原生 WinUSB，回退 pyusb
      - Linux/macOS: pyusb

    返回:
        List[UsbDeviceInfo]
    """
    global _枚举诊断
    _枚举诊断 = []  # 每次枚举前清空诊断日志，避免跳过 pyusb 时残留上次数据
    devices = []
    seen = set()  # 去重键: (vid, pid, serial)，空 serial 用 (vid, pid, '')
    # 记录空 serial 设备的索引，便于后续被有真实 serial 的同设备替换
    _empty_serial_idx = {}  # (vid, pid) -> devices 索引

    def _添加(info: UsbDeviceInfo):
        # 有真实 serial 的设备：若之前有同 vid:pid 但空 serial 的设备，替换之
        if info.serial:
            vp_key = (info.vid, info.pid)
            if vp_key in _empty_serial_idx:
                idx = _empty_serial_idx[vp_key]
                devices[idx] = info
                del _empty_serial_idx[vp_key]
                seen.add((info.vid, info.pid, info.serial))
                return
        key = (info.vid, info.pid, info.serial or '')
        if key not in seen:
            seen.add(key)
            if not info.serial:
                _empty_serial_idx[(info.vid, info.pid)] = len(devices)
            devices.append(info)

    # 1) Windows 原生 WinUSB（优先）
    if _IS_WINDOWS and _native_win is not None:
        try:
            native_devs = _native_win.枚举adb设备()
            # 合并原生枚举诊断日志（排查 PTP/MTP 模式设备识别问题）
            try:
                for _d in _native_win.获取枚举诊断日志():
                    _枚举诊断.append(f"[原生] {_d}")
            except Exception:
                pass
            for nd in native_devs:
                info = UsbDeviceInfo(
                    vid=nd.vid, pid=nd.pid,
                    manufacturer=nd.manufacturer, product=nd.product, serial=nd.serial)
                info._backend = 'native'
                info._native_info = nd
                _添加(info)
        except Exception:
            pass

    # 2) pyusb（回退 / Linux/macOS 主力）
    # Windows 上原生后端已找到设备时，跳过 pyusb（避免无 libusb 后端时产生大量 No backend 错误日志）
    _skip_pyusb = _IS_WINDOWS and len(devices) > 0
    if _pyusb and not _skip_pyusb:
        try:
            for info in _枚举adb设备_pyusb():
                _添加(info)
        except Exception:
            pass

    return devices


def _枚举adb设备_pyusb() -> List[UsbDeviceInfo]:
    """用 pyusb 枚举 ADB 设备。

    优先按已知 VID 列表快速扫描，再全量扫描兜底（发现未知 VID）。
    按 (bus, address) 去重，避免同一设备被多次添加。
    """
    global _枚举诊断
    _枚举诊断 = []
    devices = []
    seen_dev = set()  # 按 (bus, address) 去重
    _all_dev_count = 0

    def _尝试添加(dev):
        nonlocal _all_dev_count
        key = (dev.bus, dev.address)
        if key in seen_dev:
            return
        _all_dev_count += 1
        try:
            info = _查找adb接口_pyusb(dev)
            if info:
                seen_dev.add(key)
                devices.append(info)
                _枚举诊断.append(f'  匹配ADB: vid={dev.idVendor:04x} pid={dev.idProduct:04x} serial={info.serial!r}')
            else:
                pass  # 非ADB设备不记录，避免日志过多
        except Exception:
            pass  # 单设备处理异常不记录，避免日志过多

    # 快速路径：按已知 VID 列表扫描
    for vid in ADB_VID_LIST:
        try:
            count = 0
            for dev in usb.core.find(find_all=True, idVendor=vid):
                _尝试添加(dev)
                count += 1
            # 只记录找到设备或异常的 VID，避免日志过长
            if count > 0:
                _枚举诊断.append(f'按VID {vid:04x} 扫描: {count} 个设备')
        except Exception:
            continue  # 单个VID扫描异常不记录，避免日志过多

    # 兜底路径：全量扫描所有 USB 设备（发现未知 VID）
    try:
        count = 0
        for dev in usb.core.find(find_all=True):
            _尝试添加(dev)
            count += 1
        _枚举诊断.append(f'全量扫描: {count} 个设备, 其中ADB={len(devices)}')
    except Exception:
        pass  # 全量扫描异常不记录，避免日志过多

    return devices


def _安全读取字符串(dev, index: int) -> str:
    """安全读取 USB 字符串描述符（Windows 上可能失败，返回空字符串）。"""
    if index == 0:
        return ''
    try:
        s = usb.util.get_string(dev, index)
        return s or ''
    except Exception:
        return ''


def _查找adb接口_pyusb(dev) -> Optional[UsbDeviceInfo]:
    """在 pyusb 设备中查找 ADB 接口。

    识别策略（与官方 adb 行为一致）:
      1. 优先匹配标准 ADB 接口: class=255, subclass=66, protocol=1
      2. 回退匹配厂商自定义接口: class=255（任意 subclass/protocol），
         且有 Bulk IN + Bulk OUT 端点
      3. 部分设备（如荣耀）的 ADB 接口可能是 class=255/subclass=255/protocol=0
    """
    def _检查接口(intf) -> Optional[Tuple]:
        """检查接口是否有 Bulk IN/OUT 端点，返回 (ep_in, ep_out) 或 None。"""
        ep_in = None
        ep_out = None
        for ep in intf:
            if ep.bmAttributes == 2:  # Bulk
                if ep.bEndpointAddress & 0x80:
                    ep_in = ep
                else:
                    ep_out = ep
        if ep_in and ep_out:
            return (ep_in, ep_out)
        return None

    try:
        # 收集所有候选接口
        standard_candidates = []  # 标准 ADB 特征
        vendor_candidates = []    # 厂商自定义 (class=255)

        for cfg in dev:
            for intf in cfg:
                eps = _检查接口(intf)
                if eps is None:
                    continue
                if (intf.bInterfaceClass == ADB_INTERFACE_CLASS
                        and intf.bInterfaceSubClass == ADB_INTERFACE_SUBCLASS
                        and intf.bInterfaceProtocol == ADB_INTERFACE_PROTOCOL):
                    standard_candidates.append((intf, eps))
                elif intf.bInterfaceClass == ADB_INTERFACE_CLASS:
                    vendor_candidates.append((intf, eps))

        # 优先标准 ADB 接口，回退厂商自定义接口
        # 宽松匹配（class=255 但非标准 subclass/protocol）时，
        # 要求 VID 在已知 ADB 厂商列表中，避免误识别指纹器/读卡器等设备
        candidates = standard_candidates
        if not candidates and dev.idVendor in ADB_VID_LIST:
            candidates = vendor_candidates
        if not candidates:
            return None

        intf, (ep_in, ep_out) = candidates[0]
        info = UsbDeviceInfo(
            vid=dev.idVendor, pid=dev.idProduct,
            manufacturer=_安全读取字符串(dev, dev.iManufacturer),
            product=_安全读取字符串(dev, dev.iProduct),
            serial=_安全读取字符串(dev, dev.iSerialNumber))
        info._backend = 'pyusb'
        info._pyusb_dev = dev
        info._pyusb_intf = intf
        info._pyusb_ep_in = ep_in
        info._pyusb_ep_out = ep_out
        # 释放 pyusb 资源，避免设备句柄/状态残留导致下次枚举时
        # 字符串描述符读取失败（表现为 serial/product 为空）。
        # 后续连接时 pyusb 会自动重新打开设备。
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        return info
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 统一传输层
# ═══════════════════════════════════════════════════════════════

class UsbTransport:
    """USB 传输层（统一接口，兼容原生 WinUSB 和 pyusb）。

    用法:
        transport = UsbTransport(device_info, timeout=5000)
        transport.打开()
        transport.发送(data)
        data = transport.接收(length)
        transport.关闭()
    """

    def __init__(self, device_info: UsbDeviceInfo, timeout: int = 5000):
        self.device_info = device_info
        self.timeout = timeout
        self._native_transport = None
        self._claimed = False

    def 打开(self):
        """打开 USB 设备。"""
        backend = self.device_info._backend

        if backend == 'native' and self.device_info._native_info is not None:
            # 原生 WinUSB 传输层
            self._native_transport = _native_win.WinUsbTransport(
                self.device_info._native_info, timeout=self.timeout)
            self._native_transport.打开()
            return

        if backend == 'pyusb' and self.device_info._pyusb_dev is not None:
            self._打开_pyusb()
            return

        raise RuntimeError(f"不支持的 USB 后端: {backend}")

    def _打开_pyusb(self):
        """用 pyusb 打开设备并声明接口。"""
        dev = self.device_info._pyusb_dev
        intf = self.device_info._pyusb_intf

        # 设置配置（部分设备需要显式设置才能正常通信）
        try:
            dev.set_configuration()
        except Exception:
            pass

        # detach kernel driver (仅 Linux/macOS，Windows 不支持此操作)
        if not _IS_WINDOWS:
            try:
                if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                    dev.detach_kernel_driver(intf.bInterfaceNumber)
            except Exception:
                pass

        # 声明接口
        usb.util.claim_interface(dev, intf.bInterfaceNumber)
        self._claimed = True

    def 更新超时(self, timeout_ms: int):
        """运行期动态调整读写超时（毫秒）。

        注意: native(WinUSB) 后端的超时由管道策略 PIPE_TRANSFER_TIMEOUT 决定，
        仅修改 self.timeout 字段不会生效，必须下发到 WinUsbTransport。
        流式服务（如 logcat）需要短超时轮询以便及时响应停止信号。
        """
        self.timeout = int(timeout_ms)
        if self._native_transport is not None:
            try:
                self._native_transport.更新超时(self.timeout)
            except AttributeError:
                # 兼容旧版原生传输层
                self._native_transport.timeout = self.timeout

    def 发送(self, data: bytes) -> int:
        """通过 Bulk OUT 端点发送数据。"""
        if self._native_transport:
            return self._native_transport.发送(data)

        if self.device_info._backend == 'pyusb':
            return self.device_info._pyusb_ep_out.write(data, timeout=self.timeout)

        raise RuntimeError("USB 未连接")

    def 接收(self, length: int) -> bytes:
        """从 Bulk IN 端点接收数据。"""
        if self._native_transport:
            return self._native_transport.接收(length)

        if self.device_info._backend == 'pyusb':
            data = self.device_info._pyusb_ep_in.read(length, timeout=self.timeout)
            return bytes(data)

        raise RuntimeError("USB 未连接")

    def 关闭(self):
        """关闭 USB 设备。"""
        if self._native_transport:
            try:
                self._native_transport.关闭()
            except Exception:
                pass
            self._native_transport = None
            return

        if self._claimed and self.device_info._pyusb_dev is not None:
            try:
                usb.util.release_interface(
                    self.device_info._pyusb_dev,
                    self.device_info._pyusb_intf.bInterfaceNumber)
            except Exception:
                pass
            self._claimed = False
            try:
                usb.util.dispose_resources(self.device_info._pyusb_dev)
            except Exception:
                pass

    def __enter__(self):
        self.打开()
        return self

    def __exit__(self, *args):
        self.关闭()


# ═══════════════════════════════════════════════════════════════
# 热插拔监视
# ═══════════════════════════════════════════════════════════════

class UsbHotplug:
    """USB 热插拔监视器（轮询方式，跨平台）。

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


# ═══════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════

def 测试usb设备():
    """测试枚举 USB 设备。"""
    backend_info = []
    if _IS_WINDOWS:
        if _native_win:
            backend_info.append('native-winusb')
        else:
            backend_info.append(f'native-error:{_native_error}')
    if _pyusb:
        backend_info.append('pyusb')
    else:
        backend_info.append(f'pyusb-error:{_pyusb_error}')

    print(f'USB 后端: {", ".join(backend_info)}')
    print('枚举 ADB USB 设备...')
    devices = 枚举adb设备()
    print(f'找到 {len(devices)} 个设备:')
    for d in devices:
        print(f'  {d}')
    return devices


if __name__ == '__main__':
    测试usb设备()
