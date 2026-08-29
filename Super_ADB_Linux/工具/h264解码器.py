# -*- coding: utf-8 -*-
"""
H.264 解码器
===========
基于 openh264（Cisco BSD 许可）的轻量 ctypes 封装，替代 PyAV（省 ~63MB 打包体积）。
只需解码 H.264/AVC，输出 YUV420p 三平面，接口兼容 av.VideoFrame 的关键用法：
  frame.width / frame.height / frame.planes[i].buffer_ptr / frame.planes[i].line_size
  frame.to_image()  → PIL.Image (RGB)

DLL 来源优先级:
  1. 环境变量 OPENH264_DLL（测试/自定义部署）
  2. PyInstaller 打包后: <程序目录>/_internal/外部扩展/openh264/openh264.dll
  3. 源码运行: <项目根>/外部扩展/openh264/openh264.dll
  4. 系统库名: openh264.dll / libopenh264.so.7 / libopenh264.dylib

用法:
    from 工具.h264解码器 import H264解码器
    dec = H264解码器()
    frame = dec.解码(access_unit_bytes)   # 一次给一帧完整 Annex B 数据
    if frame: ...                          # SPS/PPS/P 帧可能暂无输出返回 None
    dec.关闭()
"""

import ctypes
import os
import sys
from typing import List, Optional


# ─────────────────── DLL 定位 ───────────────────

def _候选dll路径() -> List[str]:
    paths = []
    env = os.environ.get('OPENH264_DLL')
    if env:
        paths.append(env)
    相对 = os.path.join('外部扩展', 'openh264')
    # 随包内置库文件名（按平台）
    if sys.platform.startswith('win'):
        库名 = 'openh264.dll'
    elif sys.platform == 'darwin':
        库名 = 'libopenh264.dylib'
    else:
        库名 = 'libopenh264.so'
    # PyInstaller 打包后 sys.executable 在 dist 根，数据在 _internal
    if getattr(sys, 'frozen', False):
        paths.append(os.path.join(os.path.dirname(sys.executable), '_internal', 相对, 库名))
    # 源码运行: 本文件在 工具/ 下，项目根是其上级
    paths.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 相对, 库名))
    # 系统库（Linux/Mac 用包管理器安装 openh264 的场景）
    if sys.platform.startswith('win'):
        paths.append('openh264.dll')
    elif sys.platform == 'darwin':
        paths.append('libopenh264.dylib')
    else:
        paths += ['libopenh264.so.7', 'libopenh264.so']
    return paths


def _加载dll() -> ctypes.CDLL:
    errs = []
    for p in _候选dll路径():
        try:
            if sys.platform.startswith('win'):
                # DLL 同目录的 mingw 运行时（libstdc++-6.dll 等）需先入搜索路径
                d = os.path.dirname(os.path.abspath(p))
                if os.path.isdir(d) and hasattr(os, 'add_dll_directory'):
                    os.add_dll_directory(d)
            return ctypes.CDLL(p)
        except OSError as e:
            errs.append(f'{p}: {e}')
    hint = ''
    if sys.platform == 'darwin':
        hint = '\nmacOS 可安装系统库: brew install openh264（或放入 外部扩展/openh264/libopenh264.dylib）'
    elif not sys.platform.startswith('win'):
        hint = '\nLinux 可安装系统库: dnf install openh264（Fedora cisco 源）或自行编译放入 外部扩展/openh264/'
    raise ImportError(
        '找不到 openh264 动态库，投屏解码不可用。' + hint + '\n尝试过:\n' + '\n'.join(errs))


_lib: Optional[ctypes.CDLL] = None


def _取库() -> ctypes.CDLL:
    global _lib
    if _lib is None:
        _lib = _加载dll()
        _lib.WelsCreateDecoder.restype = ctypes.c_long
        _lib.WelsCreateDecoder.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        _lib.WelsDestroyDecoder.argtypes = [ctypes.c_void_p]
    return _lib


# ─────────────────── C 结构（对照 codec_api.h / codec_app_def.h / codec_def.h） ───────────────────

class _SVideoProperty(ctypes.Structure):
    _fields_ = [
        ('size', ctypes.c_uint32),
        ('eVideoBsType', ctypes.c_uint32),  # VIDEO_BITSTREAM_AVC = 0
    ]


class _SDecodingParam(ctypes.Structure):
    _fields_ = [
        ('pFileNameRestructed', ctypes.c_char_p),
        ('uiCpuLoad', ctypes.c_uint32),
        ('uiTargetDqLayer', ctypes.c_uint8),
        ('eEcActiveIdc', ctypes.c_uint32),   # ERROR_CON_IDC，默认 0 = ERROR_CON_ENABLE
        ('bParseOnly', ctypes.c_bool),
        ('sVideoProperty', _SVideoProperty),
    ]


class _SSysMEMBuffer(ctypes.Structure):
    _fields_ = [
        ('iWidth', ctypes.c_int),
        ('iHeight', ctypes.c_int),
        ('iFormat', ctypes.c_int),
        ('iStride', ctypes.c_int * 2),  # [0]=Y 行距 [1]=U/V 行距
    ]


class _UsrData(ctypes.Union):
    _fields_ = [('sSystemBuffer', _SSysMEMBuffer)]


class _SBufferInfo(ctypes.Structure):
    _fields_ = [
        ('iBufferStatus', ctypes.c_int),
        ('uiInBsTimeStamp', ctypes.c_uint64),
        ('uiOutYuvTimeStamp', ctypes.c_uint64),
        ('UsrData', _UsrData),
        ('pDst', ctypes.POINTER(ctypes.c_ubyte) * 3),  # 2.x 头文件新增字段
    ]


# ISVCDecoder 虚表索引（实证指纹确认：此 mingw 构建虚表无析构槽，
# 按声明序排列；Linux/Mac 发行版的 openh264 如布局不同需重新探针）
_VT_INITIALIZE = 0
_VT_UNINITIALIZE = 1
_VT_DECODE_FRAME_NO_DELAY = 3
_VT_SET_OPTION = 8

_DECODER_OPTION_ERROR_CON_IDC = 8
_DECODER_OPTION_TRACE_LEVEL = 9
_DECODER_OPTION_NUM_OF_THREADS = 18

_SC1 = b'\x00\x00\x01'       # 3 字节起始码
_SC2 = b'\x00\x00\x00\x01'   # 4 字节起始码


def _查找起始码(数据: bytes, start: int) -> int:
    """返回 start 之后（含）下一个起始码的起始下标，无则 -1。
    返回位置保证数据[pos+3] == 1（3字节码前面不是 00）。"""
    pos = 数据.find(_SC1, start)
    if pos < 0:
        return -1
    if pos > 0 and 数据[pos - 1] == 0:
        return pos - 1  # 实际是 4 字节起始码
    return pos


def _vtable(dec_ptr) -> ctypes.POINTER(ctypes.c_void_p):
    return ctypes.cast(ctypes.cast(dec_ptr, ctypes.POINTER(ctypes.c_void_p))[0],
                       ctypes.POINTER(ctypes.c_void_p))


def _调虚函数(dec_ptr, idx, ftypes, *args):
    """按虚表索引调用 C++ 虚函数（第一参是 this）。"""
    f = ctypes.WINFUNCTYPE(*ftypes) if sys.platform.startswith('win') else ctypes.CFUNCTYPE(*ftypes)
    return f(_vtable(dec_ptr)[idx])(dec_ptr, *args)


# ─────────────────── 帧对象 ───────────────────

class _平面:
    """兼容 av.VideoFrame.planes[i] 的 buffer_ptr/line_size 接口。"""
    __slots__ = ('buffer_ptr', 'line_size')

    def __init__(self, ptr: int, line_size: int):
        self.buffer_ptr = ptr
        self.line_size = line_size


class H264帧:
    """一帧 YUV420p，数据在 Python 字节缓冲中，对象存活即数据有效。"""
    __slots__ = ('width', 'height', 'planes', '_缓冲')

    def __init__(self, width: int, height: int, y: bytes, u: bytes, v: bytes,
                 stride_y: int, stride_uv: int):
        self.width = width
        self.height = height
        # from_buffer 要求可变缓冲；对象存活即数据有效，供 OpenGL 零拷贝读取
        bufs = [bytearray(y), bytearray(u), bytearray(v)]
        self._缓冲 = bufs
        self.planes = [
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(bufs[0])), stride_y),
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(bufs[1])), stride_uv),
            _平面(ctypes.addressof(ctypes.c_char.from_buffer(bufs[2])), stride_uv),
        ]

    def to_image(self):
        """转 PIL.Image (RGB)，截图保存用。BT.601 近似转换（LUT 加速）。"""
        from PIL import Image
        w, h = self.width, self.height
        bufs = self._缓冲
        sy = self.planes[0].line_size
        su = self.planes[1].line_size
        # 行距可能有对齐填充，先裁成紧凑行
        y_rows = b''.join(bytes(bufs[0][r * sy:r * sy + w]) for r in range(h))
        hw, hh = w >> 1, h >> 1
        u_rows = b''.join(bytes(bufs[1][r * su:r * su + hw]) for r in range(hh))
        v_rows = b''.join(bytes(bufs[2][r * su:r * su + hw]) for r in range(hh))
        y_img = Image.frombytes('L', (w, h), y_rows)
        u_img = Image.frombytes('L', (hw, hh), u_rows).resize((w, h), Image.NEAREST)
        v_img = Image.frombytes('L', (hw, hh), v_rows).resize((w, h), Image.NEAREST)
        yuv = Image.merge('YCbCr', (y_img, u_img, v_img))
        return yuv.convert('RGB')


# ─────────────────── 解码器 ───────────────────

class H264解码器:
    """openh264 解码器封装。

    输入约定: 解码() 一次接收一个 access unit（一帧完整 Annex B 数据，
    可含多个 NAL 及起始码），与 scrcpy 的 packet 边界一致。
    """

    def __init__(self):
        self._lib = _取库()
        self._dec = ctypes.c_void_p()
        self._已关闭 = False
        rv = self._lib.WelsCreateDecoder(ctypes.byref(self._dec))
        if rv != 0 or not self._dec.value:
            raise RuntimeError(f'WelsCreateDecoder 失败: {rv}')
        # 关闭日志刷屏
        level = ctypes.c_int(0)
        self._设置选项(_DECODER_OPTION_TRACE_LEVEL, ctypes.byref(level))

        param = _SDecodingParam()
        param.eEcActiveIdc = 0  # ERROR_CON_ENABLE，丢包时尽量容错显示
        param.sVideoProperty.size = ctypes.sizeof(_SVideoProperty)
        param.sVideoProperty.eVideoBsType = 0  # VIDEO_BITSTREAM_AVC
        rv = _调虚函数(self._dec, _VT_INITIALIZE,
                        [ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_SDecodingParam)],
                        ctypes.byref(param))
        if rv != 0:
            self.关闭()
            raise RuntimeError(f'openH264 Initialize 失败: {rv} (0x{rv & 0xFFFFFFFF:x})')
        self._info = _SBufferInfo()
        self._dst = (ctypes.POINTER(ctypes.c_ubyte) * 3)()
        self._已关闭 = False
        self._nal缓冲 = bytearray()  # 累积模式（解码流）的 NAL 累积缓冲
        # 多线程解码（上限自动受 CPU 核数约束）
        threads = ctypes.c_int(4)
        self._设置选项(_DECODER_OPTION_NUM_OF_THREADS, ctypes.byref(threads))

    def 解码流(self, 块: bytes) -> Optional[H264帧]:
        """累积模式：任意字节块输入，内部按起始码切 NAL、按 slice NAL 切帧。
        适配 scrcpy raw_stream 无帧头场景。出帧返回 H264帧，否则 None。
        传空块可继续排空已累积的完整帧（每次最多出一帧）。"""
        if self._已关闭:
            return None
        if 块:
            self._nal缓冲.extend(块)
        data = bytes(self._nal缓冲)
        if not data:
            return None
        n = len(data)
        # 所有起始码位置（指向 00..01 中最后一个 01 的 0，即数据[pos+3]==1）
        边界 = []
        pos = _查找起始码(data, 0)
        while pos >= 0:
            边界.append(pos)
            pos = _查找起始码(data, pos + 3)
        if len(边界) < 2:
            return None  # 连一个完整 NAL 都没有
        # 从第二个起始码倒序找第一个 slice NAL → 其前即帧边界
        切点 = -1
        for i in range(len(边界) - 1, 0, -1):
            t = data[边界[i] + 3] & 0x1F
            if 1 <= t <= 5:
                切点 = 边界[i]
                break
        if 切点 < 0:
            # 只有参数集/SEI，继续累积（防缓冲无限增长）
            if n > 1_000_000:
                self._nal缓冲 = bytearray(data[边界[-1]:])
            return None
        帧数据 = bytes(data[:切点])
        self._nal缓冲 = bytearray(data[切点:])
        return self.解码(帧数据)

    def _设置选项(self, opt, ptr):
        try:
            _调虚函数(self._dec, _VT_SET_OPTION,
                       [ctypes.c_long, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p],
                       opt, ptr)
        except Exception:
            pass

    def 解码(self, 数据: bytes, 仅参考: bool = False) -> Optional[H264帧]:
        """解码一个 access unit（完整一帧 Annex B 数据），出帧返回 H264帧。

        仅参考=True 时只推进解码器状态（保持后续 P 帧的参考帧链完整），
        跳过 YUV 三平面拷贝与 H264帧 构造，恒定返回 None。
        用于追帧：缓冲区里已有更新的帧时，旧帧无需上屏，省掉整帧拷贝开销。
        """
        if self._已关闭 or not 数据:
            return None
        buf = ctypes.create_string_buffer(数据, len(数据))
        rv = _调虚函数(self._dec, _VT_DECODE_FRAME_NO_DELAY,
                       [ctypes.c_int, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_int,
                        ctypes.POINTER(ctypes.c_ubyte) * 3,
                        ctypes.POINTER(_SBufferInfo)],
                       ctypes.cast(buf, ctypes.c_void_p), len(数据),
                       self._dst, ctypes.byref(self._info))
        if rv != 0:
            return None  # 解码错误（丢包/损坏），等 IDR 恢复
        if self._info.iBufferStatus != 1:
            return None
        if 仅参考:
            # 解码器状态已推进，参考帧链完整；跳过拷贝，不上屏
            return None
        mb = self._info.UsrData.sSystemBuffer
        w, h = mb.iWidth, mb.iHeight
        if w <= 0 or h <= 0:
            return None
        sy = mb.iStride[0]
        su = mb.iStride[1]
        hw, hh = w >> 1, h >> 1
        # 立即拷贝出解码器内部缓冲（该缓冲会被后续解码复用）
        py = ctypes.string_at(self._dst[0], sy * h)
        pu = ctypes.string_at(self._dst[1], su * hh)
        pv = ctypes.string_at(self._dst[2], su * hh)
        return H264帧(w, h, py, pu, pv, sy, su)

    def 关闭(self):
        if self._已关闭:
            return
        self._已关闭 = True
        self._nal缓冲.clear()
        try:
            if self._dec.value:
                _调虚函数(self._dec, _VT_UNINITIALIZE,
                           [ctypes.c_long, ctypes.c_void_p])
        except Exception:
            pass
        try:
            if self._dec.value:
                self._lib.WelsDestroyDecoder(self._dec)
        except Exception:
            pass
        self._dec = ctypes.c_void_p()


# ─────────────────── 自测 ───────────────────

if __name__ == '__main__':
    d = H264解码器()
    print('openh264 解码器创建成功')
    d.关闭()
    print('关闭成功')
