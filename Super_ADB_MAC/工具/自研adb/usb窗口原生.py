# -*- coding: utf-8 -*-
"""
Windows 原生 WinUSB 传输层
===========================
通过 ctypes 直接调用 Windows 原生 USB API（setupapi.dll + winusb.dll + kernel32.dll），
不依赖 pyusb / libusb，也不需要 Zadig 替换驱动，和官方 adb.exe 行为一致。

ADB 设备接口特征:
  - Interface Class: 255 (Vendor Specific)
  - SubClass: 66
  - Protocol: 1
  - Bulk OUT / Bulk IN 端点

设备枚举策略:
  1. 遍历已知的 ADB 接口 GUID 列表（Google 标准 + 常见厂商）
  2. 用 SetupDiGetClassDevs + SetupDiEnumDeviceInterfaces 枚举
  3. 对每个设备路径尝试 CreateFile + WinUsb_Initialize
  4. 验证接口特征 (class=255, subclass=66, protocol=1)
  5. 符合条件的加入设备列表

在安装了标准 android_winusb.inf 驱动的设备上可直接使用。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
from typing import Optional, List, Tuple

# ═══════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════

# SetupDi 标志
DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

# CreateFile 标志
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# WinUSB 管道类型
UsbdPipeTypeControl = 0
UsbdPipeTypeIsochronous = 1
UsbdPipeTypeBulk = 2
UsbdPipeTypeInterrupt = 3

# WinUSB 管道策略
PIPE_TRANSFER_TIMEOUT = 3

# ADB 接口特征
ADB_INTERFACE_CLASS = 255
ADB_INTERFACE_SUBCLASS = 66
ADB_INTERFACE_PROTOCOL = 1


# ═══════════════════════════════════════════════════════════════
# GUID
# ═══════════════════════════════════════════════════════════════

class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.wintypes.DWORD),
        ("Data2", ctypes.wintypes.WORD),
        ("Data3", ctypes.wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def 从字符串(cls, guid_str: str) -> "GUID":
        """从 GUID 字符串创建，如 {f72fe0d4-cbcb-407d-8814-9ed6897c0990}"""
        g = guid_str.strip("{}")
        parts = g.split("-")
        guid = cls()
        guid.Data1 = int(parts[0], 16)
        guid.Data2 = int(parts[1], 16)
        guid.Data3 = int(parts[2], 16)
        guid.Data4 = (ctypes.c_ubyte * 8)(
            int(parts[3][0:2], 16), int(parts[3][2:4], 16),
            int(parts[4][0:2], 16), int(parts[4][2:4], 16),
            int(parts[4][4:6], 16), int(parts[4][6:8], 16),
            int(parts[4][8:10], 16), int(parts[4][10:12], 16),
        )
        return guid


# 已知的 ADB 接口 GUID 列表（按优先级排列）
# 标准 android_winusb.inf 使用第一个；不同厂商可能使用变体
ADB_INTERFACE_GUIDS = [
    GUID.从字符串("{f72fe0d4-cbcb-407d-8814-9ed6897c0990}"),  # Google 标准
    GUID.从字符串("{f72fe0d4-cbcb-407d-8814-9ed673d0dd6b}"),  # 华为/荣耀变体
    GUID.从字符串("{88bae032-5a81-49f0-bc3d-a4ff138216d6}"),  # WinUSB 设备类
]


# ═══════════════════════════════════════════════════════════════
# 结构体
# ═══════════════════════════════════════════════════════════════

class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", ctypes.wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", ctypes.wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("DevicePath", ctypes.wintypes.WCHAR * 1),
    ]


class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bInterfaceNumber", ctypes.c_ubyte),
        ("bAlternateSetting", ctypes.c_ubyte),
        ("bNumEndpoints", ctypes.c_ubyte),
        ("bInterfaceClass", ctypes.c_ubyte),
        ("bInterfaceSubClass", ctypes.c_ubyte),
        ("bInterfaceProtocol", ctypes.c_ubyte),
        ("iInterface", ctypes.c_ubyte),
    ]


class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType", ctypes.c_ulong),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", ctypes.wintypes.USHORT),
        ("Interval", ctypes.c_ubyte),
    ]


# ═══════════════════════════════════════════════════════════════
# DLL 加载与函数签名
# ═══════════════════════════════════════════════════════════════

_setupapi = ctypes.WinDLL("setupapi")
_winusb = ctypes.WinDLL("winusb")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ── SetupAPI ───────────────────────────────────────────────────
_setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
_setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), ctypes.wintypes.LPCWSTR,
    ctypes.wintypes.HWND, ctypes.wintypes.DWORD,
]

_setupapi.SetupDiEnumDeviceInterfaces.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.POINTER(GUID), ctypes.wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]

_setupapi.SetupDiGetDeviceInterfaceDetailW.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W),
    ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD),
    ctypes.POINTER(SP_DEVINFO_DATA),
]

_setupapi.SetupDiDestroyDeviceInfoList.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

# ── Kernel32 ───────────────────────────────────────────────────
_kernel32.CreateFileW.restype = ctypes.c_void_p
_kernel32.CreateFileW.argtypes = [
    ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD,
    ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.c_void_p,
]
_kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

# ── WinUSB ─────────────────────────────────────────────────────
_winusb.WinUsb_Initialize.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_Initialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]

_winusb.WinUsb_Free.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_Free.argtypes = [ctypes.c_void_p]

_winusb.WinUsb_QueryInterfaceSettings.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_QueryInterfaceSettings.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(USB_INTERFACE_DESCRIPTOR)]

_winusb.WinUsb_QueryPipe.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_QueryPipe.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(WINUSB_PIPE_INFORMATION)]

_winusb.WinUsb_WritePipe.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_WritePipe.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_char_p,
    ctypes.wintypes.ULONG, ctypes.POINTER(ctypes.wintypes.ULONG), ctypes.c_void_p]

_winusb.WinUsb_ReadPipe.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_ReadPipe.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_char_p,
    ctypes.wintypes.ULONG, ctypes.POINTER(ctypes.wintypes.ULONG), ctypes.c_void_p]

_winusb.WinUsb_SetPipePolicy.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_SetPipePolicy.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]

_winusb.WinUsb_FlushPipe.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_FlushPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]


# ═══════════════════════════════════════════════════════════════
# 设备信息
# ═══════════════════════════════════════════════════════════════

class WinUsbDeviceInfo:
    """Windows 原生 USB 设备信息。"""

    def __init__(self, device_path: str, vid: int, pid: int,
                 manufacturer: str = '', product: str = '', serial: str = ''):
        self.device_path = device_path
        self.vid = vid
        self.pid = pid
        self.manufacturer = manufacturer
        self.product = product
        self.serial = serial

    @property
    def 标识(self) -> str:
        return self.serial or f'{self.vid:04x}:{self.pid:04x}'

    def __repr__(self):
        return f'<WinUsbDevice {self.标识} {self.manufacturer} {self.product}>'


def _从路径解析vidpid(device_path: str) -> Tuple[int, int]:
    """从设备路径中解析 VID/PID。"""
    vid = pid = 0
    m = re.search(r'vid_([0-9a-fA-F]{4})', device_path)
    if m:
        vid = int(m.group(1), 16)
    m = re.search(r'pid_([0-9a-fA-F]{4})', device_path)
    if m:
        pid = int(m.group(1), 16)
    return vid, pid


# ═══════════════════════════════════════════════════════════════
# 设备枚举
# ═══════════════════════════════════════════════════════════════

def 枚举adb设备() -> List[WinUsbDeviceInfo]:
    """枚举所有 ADB USB 设备（通过 Windows 原生 SetupAPI + WinUSB 验证）。

    遍历已知的 ADB 接口 GUID 列表，对每个枚举到的设备路径尝试
    CreateFile + WinUsb_Initialize，验证接口特征 (class=255, subclass=66, protocol=1)。
    """
    devices = []
    seen_paths = set()

    for guid in ADB_INTERFACE_GUIDS:
        try:
            _枚举_by_guid(guid, devices, seen_paths)
        except Exception:
            continue

    return devices


def _枚举_by_guid(guid: GUID, devices: list, seen_paths: set):
    """用指定 GUID 枚举 ADB 设备。"""
    hDevInfo = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hDevInfo == INVALID_HANDLE_VALUE or hDevInfo is None:
        return

    try:
        index = 0
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = _setupapi.SetupDiEnumDeviceInterfaces(
                hDevInfo, None, ctypes.byref(guid), index, ctypes.byref(ifdata))
            if not ok:
                break

            # 获取所需缓冲区大小
            required = ctypes.wintypes.DWORD(0)
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(ifdata), None, 0,
                ctypes.byref(required), None)
            if required.value == 0:
                index += 1
                continue

            # 获取设备路径
            buf = (ctypes.c_ubyte * required.value)()
            detail = ctypes.cast(buf, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            detail.contents.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)

            ok = _setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(ifdata), detail, required.value,
                None, ctypes.byref(devinfo))
            if not ok:
                index += 1
                continue

            device_path = ctypes.wstring_at(
                ctypes.addressof(detail.contents) + ctypes.sizeof(ctypes.wintypes.DWORD))
            if not device_path or device_path in seen_paths:
                index += 1
                continue
            seen_paths.add(device_path)

            # 尝试用 WinUSB 打开，验证是否为 ADB 接口
            vid, pid = _从路径解析vidpid(device_path)
            try:
                temp_info = WinUsbDeviceInfo(device_path, vid, pid)
                temp_transport = WinUsbTransport(temp_info, timeout=2000)
                temp_transport.打开()
                devices.append(temp_info)
                temp_transport.关闭()
            except Exception:
                pass  # 不是 ADB 设备或无法打开，跳过

            index += 1
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)


# ═══════════════════════════════════════════════════════════════
# 传输层
# ═══════════════════════════════════════════════════════════════

class WinUsbTransport:
    """Windows 原生 WinUSB 传输层。

    用法:
        transport = WinUsbTransport(device_info, timeout=5000)
        transport.打开()
        transport.发送(data)
        data = transport.接收(length)
        transport.关闭()
    """

    def __init__(self, device_info: WinUsbDeviceInfo, timeout: int = 5000):
        self.device_info = device_info
        self.timeout = timeout
        self._file_handle = None
        self._winusb_handle = None
        self._ep_in = 0
        self._ep_out = 0
        self._interface_number = 0

    def 打开(self):
        """打开设备并初始化 WinUSB，找到 ADB 接口和 Bulk 端点。"""
        # 1. CreateFile 打开设备路径
        self._file_handle = _kernel32.CreateFileW(
            self.device_info.device_path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None)
        if (self._file_handle == INVALID_HANDLE_VALUE
                or self._file_handle is None or self._file_handle == 0):
            err = ctypes.get_last_error()
            self._file_handle = None
            raise RuntimeError(
                f"CreateFile 失败 (error={err}), 路径={self.device_info.device_path[:80]}")

        # 2. WinUsb_Initialize
        self._winusb_handle = ctypes.c_void_p()
        ok = _winusb.WinUsb_Initialize(self._file_handle, ctypes.byref(self._winusb_handle))
        if not ok:
            err = ctypes.get_last_error()
            self._关闭文件句柄()
            raise RuntimeError(f"WinUsb_Initialize 失败 (error={err})")

        # 3. 查找 ADB 接口
        # 识别策略（与官方 adb 一致）:
        #   1. 优先标准 ADB 特征: class=255, subclass=66, protocol=1
        #   2. 回退厂商自定义接口: class=255（任意 subclass/protocol）
        interface_desc = USB_INTERFACE_DESCRIPTOR()
        standard_iface = -1
        vendor_iface = -1

        for alt in range(8):
            ok = _winusb.WinUsb_QueryInterfaceSettings(
                self._winusb_handle, alt, ctypes.byref(interface_desc))
            if not ok:
                break
            if interface_desc.bInterfaceClass == ADB_INTERFACE_CLASS:
                if (interface_desc.bInterfaceSubClass == ADB_INTERFACE_SUBCLASS
                        and interface_desc.bInterfaceProtocol == ADB_INTERFACE_PROTOCOL):
                    if standard_iface < 0:
                        standard_iface = alt
                elif vendor_iface < 0:
                    vendor_iface = alt

        # 优先标准 ADB 接口，回退厂商自定义接口
        self._interface_number = standard_iface if standard_iface >= 0 else vendor_iface
        if self._interface_number < 0:
            # 都没找到，用第一个接口
            ok = _winusb.WinUsb_QueryInterfaceSettings(
                self._winusb_handle, 0, ctypes.byref(interface_desc))
            if not ok:
                self.关闭()
                raise RuntimeError("无法查询接口设置")
            self._interface_number = 0
        else:
            # 重新查询选中接口的描述符
            _winusb.WinUsb_QueryInterfaceSettings(
                self._winusb_handle, self._interface_number, ctypes.byref(interface_desc))

        # 4. 查找 Bulk IN / Bulk OUT 端点
        num_pipes = interface_desc.bNumEndpoints
        for pipe_index in range(num_pipes):
            pipe_info = WINUSB_PIPE_INFORMATION()
            ok = _winusb.WinUsb_QueryPipe(
                self._winusb_handle, self._interface_number, pipe_index,
                ctypes.byref(pipe_info))
            if not ok:
                continue
            if pipe_info.PipeType == UsbdPipeTypeBulk:
                if pipe_info.PipeId & 0x80:
                    self._ep_in = pipe_info.PipeId
                else:
                    self._ep_out = pipe_info.PipeId

        if self._ep_in == 0 or self._ep_out == 0:
            self.关闭()
            raise RuntimeError(
                f"未找到 Bulk IN/OUT 端点 (in=0x{self._ep_in:02x}, out=0x{self._ep_out:02x})")

        # 5. 设置管道超时
        self._设置超时(self._ep_in, self.timeout)
        self._设置超时(self._ep_out, self.timeout)

    def _设置超时(self, pipe_id: int, timeout_ms: int):
        """设置管道读写超时（毫秒）。"""
        try:
            timeout_val = ctypes.c_ulong(timeout_ms)
            _winusb.WinUsb_SetPipePolicy(
                self._winusb_handle, pipe_id, PIPE_TRANSFER_TIMEOUT,
                ctypes.sizeof(timeout_val), ctypes.byref(timeout_val))
        except Exception:
            pass

    def 发送(self, data: bytes) -> int:
        """通过 Bulk OUT 端点发送数据。"""
        if self._winusb_handle is None:
            raise RuntimeError("WinUSB 未初始化")
        written = ctypes.wintypes.ULONG(0)
        buf = ctypes.create_string_buffer(data)
        ok = _winusb.WinUsb_WritePipe(
            self._winusb_handle, self._ep_out,
            buf, len(data), ctypes.byref(written), None)
        if not ok:
            err = ctypes.get_last_error()
            if err == 121:  # ERROR_SEM_TIMEOUT
                raise TimeoutError(f"WinUsb_WritePipe 超时 (error={err})")
            raise RuntimeError(f"WinUsb_WritePipe 失败 (error={err})")
        return written.value

    def 接收(self, length: int) -> bytes:
        """从 Bulk IN 端点接收数据。"""
        if self._winusb_handle is None:
            raise RuntimeError("WinUSB 未初始化")
        buf = ctypes.create_string_buffer(length)
        read = ctypes.wintypes.ULONG(0)
        ok = _winusb.WinUsb_ReadPipe(
            self._winusb_handle, self._ep_in,
            buf, length, ctypes.byref(read), None)
        if not ok:
            err = ctypes.get_last_error()
            if err in (121, 997):  # ERROR_SEM_TIMEOUT / ERROR_IO_INCOMPLETE
                raise TimeoutError(f"WinUsb_ReadPipe 超时 (error={err})")
            raise RuntimeError(f"WinUsb_ReadPipe 失败 (error={err})")
        return buf.raw[:read.value]

    def 刷新(self):
        """刷新管道（清除未完成的传输）。"""
        if self._winusb_handle:
            try:
                _winusb.WinUsb_FlushPipe(self._winusb_handle, self._ep_in)
            except Exception:
                pass

    def _关闭文件句柄(self):
        if (self._file_handle is not None
                and self._file_handle != INVALID_HANDLE_VALUE
                and self._file_handle != 0):
            try:
                _kernel32.CloseHandle(self._file_handle)
            except Exception:
                pass
            self._file_handle = None

    def 关闭(self):
        """关闭设备。"""
        if self._winusb_handle is not None:
            try:
                _winusb.WinUsb_Free(self._winusb_handle)
            except Exception:
                pass
            self._winusb_handle = None
        self._关闭文件句柄()
        self._ep_in = 0
        self._ep_out = 0

    def __enter__(self):
        self.打开()
        return self

    def __exit__(self, *args):
        self.关闭()


# 兼容旧代码的别名
UsbDeviceInfo = WinUsbDeviceInfo
UsbTransport = WinUsbTransport


if __name__ == '__main__':
    print('枚举 ADB USB 设备 (Windows 原生 WinUSB)...')
    devices = 枚举adb设备()
    print(f'找到 {len(devices)} 个设备')
    for d in devices:
        print(f'  {d}')
        print(f'    路径: {d.device_path[:120]}')
