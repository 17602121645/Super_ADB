# -*- coding: utf-8 -*-
"""
Media Foundation H.264 硬件解码器
==================================
基于 Windows Media Foundation 的硬件加速 H.264 解码器，通过自研 C++ DLL
（外部扩展/mf_h264/mf_h264_decoder.dll）调用，输出 NV12 → 转 YUV420p。

接口与 工具.h264解码器.H264解码器 完全兼容，可直接替换：
    dec = MF_H264解码器()
    frame = dec.解码(access_unit_bytes)
    dec.关闭()

与 openh264 软解的区别：
  - 硬件解码（GPU），CPU 占用低，高分辨率/高帧率更流畅
  - 仅 Windows 平台（依赖 Media Foundation）
  - 打包体积 +406KB（DLL 静态链接，无额外依赖）
  - 首帧延迟略高（MFT 初始化），后续帧延迟极低

DLL 来源优先级:
  1. 环境变量 MF_H264_DLL
  2. PyInstaller 打包后: <程序目录>/_internal/外部扩展/mf_h264/mf_h264_decoder.dll
  3. 源码运行: <项目根>/外部扩展/mf_h264/mf_h264_decoder.dll
"""

import ctypes
import os
import sys
from typing import Optional


# ─────────────────── DLL 定位 ───────────────────

def _候选dll路径():
    paths = []
    env = os.environ.get('MF_H264_DLL')
    if env:
        paths.append(env)
    相对 = os.path.join('外部扩展', 'mf_h264')
    库名 = 'mf_h264_decoder.dll'
    if getattr(sys, 'frozen', False):
        paths.append(os.path.join(os.path.dirname(sys.executable), '_internal', 相对, 库名))
    paths.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 相对, 库名))
    return paths


def _加载dll():
    errs = []
    for p in _候选dll路径():
        try:
            d = os.path.dirname(os.path.abspath(p))
            if os.path.isdir(d) and hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(d)
            return ctypes.WinDLL(p)
        except OSError as e:
            errs.append(f'{p}: {e}')
    raise ImportError(
        '找不到 mf_h264_decoder.dll，硬件解码不可用。\n尝试过:\n' + '\n'.join(errs))


_lib = None


def _取库():
    global _lib
    if _lib is None:
        _lib = _加载dll()
        _lib.mf_h264_create.restype = ctypes.c_void_p
        _lib.mf_h264_create.argtypes = [ctypes.c_int, ctypes.c_int]
        # v2.0 新接口：带 SPS/PPS 的创建
        try:
            _lib.mf_h264_create_ex.restype = ctypes.c_void_p
            _lib.mf_h264_create_ex.argtypes = [ctypes.c_int, ctypes.c_int,
                                                  ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
            _lib.mf_h264_decoder_name.restype = ctypes.c_char_p
            _lib.mf_h264_decoder_name.argtypes = [ctypes.c_void_p]
            _lib._有ex接口 = True
        except AttributeError:
            _lib._有ex接口 = False
        _lib.mf_h264_decode.restype = ctypes.c_int
        _lib.mf_h264_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int]
        _lib.mf_h264_get_frame.restype = ctypes.c_int
        _lib.mf_h264_get_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        _lib.mf_h264_flush.restype = None
        _lib.mf_h264_flush.argtypes = [ctypes.c_void_p]
        _lib.mf_h264_destroy.restype = None
        _lib.mf_h264_destroy.argtypes = [ctypes.c_void_p]
        _lib.mf_h264_version.restype = ctypes.c_char_p
        _lib.mf_h264_version.argtypes = []
    return _lib


def 可用() -> bool:
    """检查 MF 硬件解码是否可用（不抛异常）。"""
    try:
        _取库()
        return True
    except Exception:
        return False


# ─────────────────── 帧对象 ───────────────────

class _平面:
    __slots__ = ('buffer_ptr', 'line_size')

    def __init__(self, ptr: int, line_size: int):
        self.buffer_ptr = ptr
        self.line_size = line_size


class MF_H264帧:
    """一帧 YUV420p，由 NV12 转换而来。数据在 Python bytearray 中。"""
    __slots__ = ('width', 'height', 'planes', '_缓冲')

    def __init__(self, width: int, height: int, y: bytearray, u: bytearray, v: bytearray):
        self.width = width
        self.height = height
        self._缓冲 = [y, u, v]
        self.planes = [
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(y)), width),
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(u)), width // 2),
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(v)), width // 2),
        ]

    def to_image(self):
        """转 PIL.Image (RGB)。BT.601 近似转换。"""
        from PIL import Image
        w, h = self.width, self.height
        bufs = self._缓冲
        y_img = Image.frombytes('L', (w, h), bytes(bufs[0]))
        hw, hh = w >> 1, h >> 1
        u_img = Image.frombytes('L', (hw, hh), bytes(bufs[1])).resize((w, h), Image.NEAREST)
        v_img = Image.frombytes('L', (hw, hh), bytes(bufs[2])).resize((w, h), Image.NEAREST)
        yuv = Image.merge('YCbCr', (y_img, u_img, v_img))
        return yuv.convert('RGB')


# ─────────────────── NV12 → YUV420p 转换 ───────────────────

def _nv12转yuv420p(nv12: bytes, width: int, height: int, stride: int):
    """NV12 → YUV420p 三平面。返回 (y, u, v) bytearray。"""
    y_size = stride * height
    uv_size = stride * (height // 2)
    y_plane = bytearray(nv12[:y_size])
    uv_plane = nv12[y_size:y_size + uv_size]
    hw = width // 2
    hh = height // 2
    u_plane = bytearray(hw * hh)
    v_plane = bytearray(hw * hh)
    # 去交织：UV 平面是 U0,V0,U1,V1,... 按行排列
    for row in range(hh):
        src_off = row * stride
        dst_off = row * hw
        for col in range(hw):
            u_plane[dst_off + col] = uv_plane[src_off + col * 2]
            v_plane[dst_off + col] = uv_plane[src_off + col * 2 + 1]
    # 裁剪 Y 平面的 stride 填充（如果 stride > width）
    if stride != width:
        y_trimmed = bytearray(width * height)
        for row in range(height):
            y_trimmed[row * width:(row + 1) * width] = y_plane[row * stride:row * stride + width]
        y_plane = y_trimmed
    return y_plane, u_plane, v_plane


# ─────────────────── 解码器 ───────────────────

class MF_H264解码器:
    """Media Foundation 硬件 H.264 解码器。

    接口兼容 H264解码器：解码(data, 仅参考=False) → MF_H264帧 | None
    """

    def __init__(self):
        self._lib = _取库()
        self._handle = None
        self._宽 = 0
        self._高 = 0
        self._已关闭 = False
        self._spspps = b''  # 缓存 SPS/PPS，创建解码器时注入
        self._版本 = self._lib.mf_h264_version().decode('ascii', errors='replace')

    def _确保解码器(self, width: int, height: int):
        """分辨率变化时重建解码器（MF MFT 不支持动态分辨率）。"""
        if self._handle and self._宽 == width and self._高 == height:
            return
        if self._handle:
            self._lib.mf_h264_destroy(self._handle)
            self._handle = None
        # 使用 v2.0 新接口，传入 SPS/PPS
        if getattr(self._lib, '_有ex接口', False) and self._spspps:
            buf = (ctypes.c_ubyte * len(self._spspps))(*self._spspps)
            self._handle = self._lib.mf_h264_create_ex(width, height, buf, len(self._spspps))
        else:
            self._handle = self._lib.mf_h264_create(width, height)
        if not self._handle:
            raise RuntimeError(f'MF H264 创建解码器失败 ({width}x{height})')
        self._宽 = width
        self._高 = height
        # 打印当前使用的解码器名称
        if getattr(self._lib, '_有ex接口', False):
            try:
                name = self._lib.mf_h264_decoder_name(self._handle).decode('ascii', errors='replace')
                print(f'[MF解码器] 使用: {name} ({width}x{height})')
            except Exception:
                pass

    def 解码(self, 数据: bytes, 仅参考: bool = False) -> Optional[MF_H264帧]:
        """解码一个 access unit（完整一帧 Annex B 数据）。

        仅参考=True 时只推进解码器状态，返回 None（省掉 NV12→YUV 转换）。
        """
        if self._已关闭 or not 数据:
            return None
        # 缓存 SPS/PPS（含 NAL type 7 的包），供创建解码器时注入
        if not self._spspps:
            if b'\x00\x00\x00\x01g' in 数据 or b'\x00\x00\x01g' in 数据:
                # 提取从第一个起始码到第三个起始码之间的数据（SPS+PPS）
                起始码位置 = []
                for i in range(len(数据) - 3):
                    if (数据[i:i+4] == b'\x00\x00\x00\x01' or
                        数据[i:i+3] == b'\x00\x00\x01'):
                        起始码位置.append(i)
                        if len(起始码位置) >= 3:
                            break
                if len(起始码位置) >= 2:
                    结束 = 起始码位置[2] if len(起始码位置) >= 3 else len(数据)
                    self._spspps = 数据[起始码位置[0]:结束]
                    print(f'[MF解码器] 缓存 SPS/PPS ({len(self._spspps)} bytes)')
        # 从 SPS 中解析分辨率（如果还不知道）
        if self._宽 == 0 or self._高 == 0:
            w, h = _解析sps分辨率(数据)
            if w > 0 and h > 0:
                self._确保解码器(w, h)
            else:
                return None  # 还没收到 SPS，等下一帧
        else:
            self._确保解码器(self._宽, self._高)
        if not self._handle:
            return None
        # 输入 H264 数据
        buf = (ctypes.c_ubyte * len(数据))(*数据)
        ret = self._lib.mf_h264_decode(self._handle, buf, len(数据))
        if ret != 0:
            return None
        if 仅参考:
            return None
        # 获取解码帧
        out_data = ctypes.c_void_p()
        out_w = ctypes.c_int()
        out_h = ctypes.c_int()
        frame_size = self._lib.mf_h264_get_frame(self._handle, ctypes.byref(out_data),
                                                    ctypes.byref(out_w), ctypes.byref(out_h))
        if frame_size <= 0 or not out_data.value:
            return None
        w, h = out_w.value, out_h.value
        if w <= 0 or h <= 0:
            return None
        # 拷贝出 DLL 内部缓冲（DLL 内部 malloc，get_frame 后下次调用会复用）
        nv12 = ctypes.string_at(out_data.value, frame_size)
        # NV12 stride = frame_size / (h + h//2)（近似，实际可能有对齐）
        stride = w  # MF 输出通常 stride == width
        if frame_size >= stride * h * 3 // 2:
            pass  # stride == width
        else:
            # 计算实际 stride
            stride = frame_size // (h + h // 2)
        y, u, v = _nv12转yuv420p(nv12, w, h, stride)
        return MF_H264帧(w, h, y, u, v)

    def 解码流(self, 块: bytes) -> Optional[MF_H264帧]:
        """累积模式（兼容接口，MF 按帧解码，此处直接透传）。"""
        if not 块:
            return None
        return self.解码(块)

    def 关闭(self):
        if self._已关闭:
            return
        self._已关闭 = True
        try:
            if self._handle:
                self._lib.mf_h264_flush(self._handle)
                self._lib.mf_h264_destroy(self._handle)
        except Exception:
            pass
        self._handle = None


# ─────────────────── SPS 分辨率解析 ───────────────────

def _解析sps分辨率(data: bytes):
    """从 Annex B 数据中解析 SPS 的宽高。失败返回 (0, 0)。"""
    # 查找 SPS NAL（nal_type = 7）
    pos = 0
    n = len(data)
    while pos < n - 4:
        # 找起始码
        if data[pos:pos + 3] == b'\x00\x00\x01':
            nal_start = pos + 3
        elif data[pos:pos + 4] == b'\x00\x00\x00\x01':
            nal_start = pos + 4
        else:
            pos += 1
            continue
        if nal_start >= n:
            break
        nal_type = data[nal_start] & 0x1F
        if nal_type == 7:  # SPS
            return _解析sps(data[nal_start + 1:])
        pos = nal_start + 1
    return 0, 0


def _解析sps(sps_payload: bytes):
    """解析 SPS RBSP（已去掉 NAL header），返回 (width, height)。"""
    try:
        # 简易 Exp-Golomb 解析
        bits = _BitReader(sps_payload)
        profile_idc = bits.read(8)
        bits.read(8)  # constraint_set + reserved
        bits.read(8)  # level_idc
        bits.read_ue()  # seq_parameter_set_id
        if profile_idc in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            chroma_format_idc = bits.read_ue()
            if chroma_format_idc == 3:
                bits.read(1)  # separate_colour_plane_flag
            bits.read_ue()  # bit_depth_luma
            bits.read_ue()  # bit_depth_chroma
            bits.read(1)  # qpprime_y_zero_transform_bypass_flag
            seq_scaling_matrix_present = bits.read(1)
            if seq_scaling_matrix_present:
                for i in range(8 if chroma_format_idc != 3 else 12):
                    if bits.read(1):
                        if i < 6:
                            _解析缩放列表(bits, 16)
                        else:
                            _解析缩放列表(bits, 64)
        bits.read_ue()  # log2_max_frame_num
        pic_order_cnt_type = bits.read_ue()
        if pic_order_cnt_type == 0:
            bits.read_ue()
        elif pic_order_cnt_type == 1:
            bits.read(1)
            bits.read_se()
            bits.read_se()
            num_ref_frames_in_pic_order_cnt_cycle = bits.read_ue()
            for _ in range(num_ref_frames_in_pic_order_cnt_cycle):
                bits.read_se()
        bits.read_ue()  # max_num_ref_frames
        bits.read(1)  # gaps_in_frame_num_value_allowed_flag
        pic_width_in_mbs_minus1 = bits.read_ue()
        pic_height_in_map_units_minus1 = bits.read_ue()
        width = (pic_width_in_mbs_minus1 + 1) * 16
        height = (pic_height_in_map_units_minus1 + 1) * 16
        frame_mbs_only_flag = bits.read(1)
        if not frame_mbs_only_flag:
            bits.read(1)  # mb_adaptive_frame_field_flag
        return width, height
    except Exception:
        return 0, 0


def _解析缩放列表(bits, size):
    last_scale = 8
    next_scale = 8
    for _ in range(size):
        if next_scale != 0:
            delta_scale = bits.read_se()
            next_scale = (last_scale + delta_scale + 256) % 256
        if next_scale != 0:
            last_scale = next_scale


class _BitReader:
    """简易位读取器，支持 Exp-Golomb。"""
    __slots__ = ('data', 'byte_pos', 'bit_pos')

    def __init__(self, data: bytes):
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0

    def read(self, n: int) -> int:
        val = 0
        for _ in range(n):
            if self.byte_pos >= len(self.data):
                return val
            val = (val << 1) | ((self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1)
            self.bit_pos += 1
            if self.bit_pos >= 8:
                self.bit_pos = 0
                self.byte_pos += 1
        return val

    def read_ue(self) -> int:
        zeros = 0
        while self.read(1) == 0:
            zeros += 1
            if zeros > 32:
                return 0
        if zeros == 0:
            return 0
        return (1 << zeros) - 1 + self.read(zeros)

    def read_se(self) -> int:
        val = self.read_ue()
        if val % 2 == 0:
            return -(val // 2)
        return (val + 1) // 2


# ─────────────────── 自测 ───────────────────

if __name__ == '__main__':
    if 可用():
        print(f'MF H264 解码器可用，版本: {MF_H264解码器()._版本}')
    else:
        print('MF H264 解码器不可用')
