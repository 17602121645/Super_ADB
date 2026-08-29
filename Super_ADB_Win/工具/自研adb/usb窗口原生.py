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
DIGCF_ALLCLASSES = 0x00000004

# SetupDi 设备属性
SPDRP_HARDWAREID = 0x00000001
SPDRP_FRIENDLYNAME = 0x0000000C
SPDRP_DEVICEDESC = 0x00000000

# CreateFile 标志
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_OVERLAPPED = 0x40000000  # WinUSB 必须用重叠 I/O 打开，否则 WinUsb_Initialize 失败
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

# USB 设备安装类 GUID（用于暴力枚举所有 USB 设备）
_USB_DEVICE_CLASS_GUID = GUID.从字符串("{36FC9E60-C465-11CF-8056-444553540000}")

# 诊断日志：记录枚举过程中跳过的设备（用于排查 PTP/MTP 模式下设备识别问题）
_诊断日志: List[str] = []


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

# 获取设备实例 ID（含序列号）
_setupapi.SetupDiGetDeviceInstanceIdW.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.c_wchar_p, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD)]

# 枚举设备信息（SetupDiEnumDeviceInfo）
_setupapi.SetupDiEnumDeviceInfo.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]

# 读取设备注册表属性（硬件ID、描述等）
_setupapi.SetupDiGetDeviceRegistryPropertyW.restype = ctypes.wintypes.BOOL
_setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), ctypes.wintypes.DWORD,
    ctypes.POINTER(ctypes.wintypes.DWORD),
    ctypes.c_void_p, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD)]

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

_winusb.WinUsb_GetDescriptor.restype = ctypes.wintypes.BOOL
_winusb.WinUsb_GetDescriptor.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte,
    ctypes.c_ushort, ctypes.c_char_p, ctypes.c_ulong,
    ctypes.POINTER(ctypes.c_ulong)]


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


def _从实例id解析序列号(hDevInfo, devinfo) -> str:
    """从设备实例 ID 解析 USB 序列号。
    对于复合设备接口（MI_xx），需要获取父 USB 设备的实例 ID。
    父设备实例 ID 格式: USB\\VID_xxxx&PID_xxxx\\SERIAL_NUMBER
    """
    try:
        import ctypes as _ct
        cfgmgr = _ct.windll.cfgmgr32
        # 获取父设备实例
        parent_inst = _ct.wintypes.DWORD(0)
        ret = cfgmgr.CM_Get_Parent(_ct.byref(parent_inst), devinfo.DevInst, 0)
        if ret != 0:
            # 没有父设备，用当前设备
            target_inst = devinfo.DevInst
        else:
            target_inst = parent_inst.value
        # 获取父设备实例 ID
        buf_size = 256
        buf = _ct.create_unicode_buffer(buf_size)
        ret = cfgmgr.CM_Get_Device_IDW(target_inst, buf, buf_size, 0)
        if ret == 0:
            instance_id = buf.value
            parts = instance_id.split('\\')
            if len(parts) >= 3:
                serial = parts[-1]
                if '&' in serial:
                    serial = serial.split('&')[0]
                return serial
    except Exception:
        pass
    return ''


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


def _读取usb_string(transport: 'WinUsbTransport', index: int, lang_id: int = 0x0409) -> str:
    """通过 WinUSB 读取 USB 字符串描述符。"""
    if index == 0:
        return ''
    buf = ctypes.create_string_buffer(256)
    transferred = ctypes.c_ulong(0)
    ok = _winusb.WinUsb_GetDescriptor(
        transport._winusb_handle, 0x03, index, lang_id,
        buf, 256, ctypes.byref(transferred))
    if not ok or transferred.value < 2:
        return ''
    # 字符串描述符: bLength(1), bDescriptorType(1), bString(UTF-16LE)
    raw = buf.raw[2:transferred.value]
    try:
        return raw.decode('utf-16-le').rstrip('\x00')
    except Exception:
        return ''


def _读取设备字符串(transport: 'WinUsbTransport', info: WinUsbDeviceInfo):
    """读取设备的 manufacturer/product/serial 字符串描述符。"""
    # 先读设备描述符获取字符串索引
    dev_desc = ctypes.create_string_buffer(18)
    transferred = ctypes.c_ulong(0)
    ok = _winusb.WinUsb_GetDescriptor(
        transport._winusb_handle, 0x01, 0, 0,
        dev_desc, 18, ctypes.byref(transferred))
    if not ok or transferred.value < 18:
        return
    # 设备描述符偏移: 14=iManufacturer, 15=iProduct, 16=iSerialNumber
    i_man = dev_desc.raw[14]
    i_prod = dev_desc.raw[15]
    i_serial = dev_desc.raw[16]
    if i_man:
        info.manufacturer = _读取usb_string(transport, i_man)
    if i_prod:
        info.product = _读取usb_string(transport, i_prod)
    if i_serial:
        info.serial = _读取usb_string(transport, i_serial)


# ═══════════════════════════════════════════════════════════════
# 设备枚举
# ═══════════════════════════════════════════════════════════════

def 枚举adb设备() -> List[WinUsbDeviceInfo]:
    """枚举所有 ADB USB 设备（通过 Windows 原生 SetupAPI + WinUSB 验证）。

    策略：
    1. 遍历已知的 ADB 接口 GUID 列表（快速路径）
    2. 若未找到，暴力枚举所有 USB 设备接口（兜底，适配未注册已知GUID的驱动）
    """
    global _诊断日志
    _诊断日志 = []  # 清空上次诊断日志
    devices = []
    seen_paths = set()

    for guid in ADB_INTERFACE_GUIDS:
        try:
            _枚举_by_guid(guid, devices, seen_paths)
        except Exception as e:
            _诊断日志.append(f"[GUID枚举异常] {e}")
            continue

    # 暴力枚举回退：已知 GUID 都没找到时，枚举所有 USB 设备接口
    if not devices:
        _诊断日志.append("[暴力枚举] 已知GUID未找到设备，开始全量USB接口扫描...")
        try:
            _枚举_bruteforce(devices, seen_paths)
        except Exception as e:
            _诊断日志.append(f"[暴力枚举异常] {e}")

    # 终极诊断：如果还是没找到设备，枚举所有 USB 设备节点（不论有无驱动）
    # 用于检测 PTP 模式下没装驱动的设备
    if not devices:
        try:
            usb_nodes = _枚举_usb设备节点()
            _诊断日志.append(f"[设备节点诊断] 共发现 {len(usb_nodes)} 个USB设备节点:")
            for node in usb_nodes:
                _line = f"  VID={node['vid']:04x} PID={node['pid']:04x} 描述={node['desc']}"
                _诊断日志.append(_line)
                for hid in node['hardware_ids'][:3]:
                    _诊断日志.append(f"    HWID: {hid}")
        except Exception as e:
            _诊断日志.append(f"[设备节点诊断异常] {e}")

    _诊断日志.append(f"[枚举完成] 找到 {len(devices)} 个可用ADB设备")
    return devices


def 获取枚举诊断日志() -> List[str]:
    """获取最近一次设备枚举的诊断日志（用于排查设备识别问题）。"""
    return list(_诊断日志)


def _枚举_bruteforce(devices: list, seen_paths: set):
    """暴力枚举所有 USB 设备接口，用 WinUSB 打开验证是否为 ADB 接口。

    适配未注册已知 ADB 接口 GUID 的驱动（如部分荣耀/华为机型）。
    使用 DIGCF_ALLCLASSES 枚举所有设备接口，按路径含 vid_ 过滤 USB 设备。
    """
    hDevInfo = _setupapi.SetupDiGetClassDevsW(
        None, None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE | DIGCF_ALLCLASSES)
    if hDevInfo == INVALID_HANDLE_VALUE or hDevInfo is None:
        return

    try:
        index = 0
        while True:
            ifdata = SP_DEVICE_INTERFACE_DATA()
            ifdata.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = _setupapi.SetupDiEnumDeviceInterfaces(
                hDevInfo, None, None, index, ctypes.byref(ifdata))
            if not ok:
                break

            # 获取设备路径
            required = ctypes.wintypes.DWORD(0)
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                hDevInfo, ctypes.byref(ifdata), None, 0,
                ctypes.byref(required), None)
            if required.value == 0:
                index += 1
                continue

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

            # 只处理 USB 设备（路径含 vid_）
            if 'vid_' not in device_path.lower():
                index += 1
                continue

            seen_paths.add(device_path)
            vid, pid = _从路径解析vidpid(device_path)
            try:
                temp_info = WinUsbDeviceInfo(device_path, vid, pid)
                temp_transport = WinUsbTransport(temp_info, timeout=1000)
                temp_transport.打开()
                try:
                    _读取设备字符串(temp_transport, temp_info)
                except Exception:
                    pass
                devices.append(temp_info)
                temp_transport.关闭()
            except Exception as e:
                # 记录无法用 WinUSB 打开的 USB 设备（可能是驱动未绑定）
                # 提取 MI 编号用于诊断
                mi_match = re.search(r'mi_(\d+)', device_path.lower())
                mi = mi_match.group(1) if mi_match else '?'
                _diag = f"[跳过] VID={vid:04x} PID={pid:04x} MI={mi} WinUSB打开失败: {e}"
                _诊断日志.append(f"  {_diag}")

            index += 1
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)


def _枚举_usb设备节点() -> List[dict]:
    """枚举所有 USB 设备节点（不论是否有驱动），读取硬件 ID 获取 VID/PID。

    用于诊断：PTP 模式下设备可能没装驱动，设备接口枚举不到，
    但设备节点仍然存在，可以通过此函数发现并提示用户安装驱动。

    返回: [{'vid': int, 'pid': int, 'desc': str, 'hardware_ids': [str]}]
    """
    results = []
    hDevInfo = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(_USB_DEVICE_CLASS_GUID), None, None, DIGCF_PRESENT)
    if hDevInfo == INVALID_HANDLE_VALUE or hDevInfo is None:
        return results

    try:
        index = 0
        while True:
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            ok = _setupapi.SetupDiEnumDeviceInfo(hDevInfo, index, ctypes.byref(devinfo))
            if not ok:
                break

            # 读取硬件 ID（多字符串，第一个通常是 USB\VID_xxxx&PID_xxxx）
            hw_ids = _读取设备属性多字符串(hDevInfo, devinfo, SPDRP_HARDWAREID)
            desc = _读取设备属性字符串(hDevInfo, devinfo, SPDRP_DEVICEDESC)

            vid = pid = 0
            for hid in hw_ids:
                m = re.search(r'vid_([0-9a-f]{4})', hid, re.IGNORECASE)
                if m:
                    vid = int(m.group(1), 16)
                m = re.search(r'pid_([0-9a-f]{4})', hid, re.IGNORECASE)
                if m:
                    pid = int(m.group(1), 16)
                if vid and pid:
                    break

            if vid and pid:
                results.append({
                    'vid': vid, 'pid': pid,
                    'desc': desc,
                    'hardware_ids': hw_ids,
                })
            index += 1
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(hDevInfo)

    return results


def _读取设备属性字符串(hDevInfo, devinfo, prop: int) -> str:
    """读取设备属性字符串。"""
    required = ctypes.wintypes.DWORD(0)
    _setupapi.SetupDiGetDeviceRegistryPropertyW(
        hDevInfo, ctypes.byref(devinfo), prop, None, None, 0, ctypes.byref(required))
    if required.value == 0:
        return ''
    buf = ctypes.create_unicode_buffer(required.value)
    ok = _setupapi.SetupDiGetDeviceRegistryPropertyW(
        hDevInfo, ctypes.byref(devinfo), prop, None,
        ctypes.cast(buf, ctypes.c_void_p), required.value, None)
    if not ok:
        return ''
    return buf.value


def _读取设备属性多字符串(hDevInfo, devinfo, prop: int) -> List[str]:
    """读取设备属性多字符串（REG_MULTI_SZ），以双 null 结尾。"""
    required = ctypes.wintypes.DWORD(0)
    _setupapi.SetupDiGetDeviceRegistryPropertyW(
        hDevInfo, ctypes.byref(devinfo), prop, None, None, 0, ctypes.byref(required))
    if required.value == 0:
        return []
    buf = ctypes.create_unicode_buffer(required.value)
    ok = _setupapi.SetupDiGetDeviceRegistryPropertyW(
        hDevInfo, ctypes.byref(devinfo), prop, None,
        ctypes.cast(buf, ctypes.c_void_p), required.value, None)
    if not ok:
        return []
    # 多字符串：以双 null 分隔，最后以双 null 结尾
    # create_unicode_buffer 没有 .raw，用 string_at 读取原始字节
    raw = ctypes.string_at(buf, required.value)
    try:
        text = raw.decode('utf-16-le').rstrip('\x00')
        return [s for s in text.split('\x00') if s]
    except Exception:
        return []


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

            # 通过已知 ADB 接口 GUID 找到的设备，几乎肯定是 ADB 设备
            # 即使 WinUSB 打开失败，也保留设备（序列号从实例 ID 获取）
            vid, pid = _从路径解析vidpid(device_path)
            temp_info = WinUsbDeviceInfo(device_path, vid, pid)
            # 先从设备实例 ID 获取序列号（不依赖 WinUSB）
            _serial_from_instance = _从实例id解析序列号(hDevInfo, devinfo)
            if _serial_from_instance:
                temp_info.serial = _serial_from_instance
            # 尝试用 WinUSB 打开，读取更详细的字符串描述符
            try:
                temp_transport = WinUsbTransport(temp_info, timeout=2000)
                temp_transport.打开()
                try:
                    _读取设备字符串(temp_transport, temp_info)
                except Exception:
                    pass
                temp_transport.关闭()
            except Exception:
                pass  # WinUSB 打开失败不影响枚举，设备已保留
            devices.append(temp_info)

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
            None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
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

    def 更新超时(self, timeout_ms: int):
        """运行期动态调整管道超时（毫秒）。

        WinUSB 的超时是通过 PIPE_TRANSFER_TIMEOUT 管道策略生效的，
        只改 self.timeout 字段不会影响已打开的管道，必须重新下发策略。
        流式服务（logcat）需要短超时轮询以便及时响应停止信号。
        """
        self.timeout = int(timeout_ms)
        if self._winusb_handle is not None:
            if self._ep_in:
                self._设置超时(self._ep_in, self.timeout)
            if self._ep_out:
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
