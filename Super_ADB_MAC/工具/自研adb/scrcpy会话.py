# -*- coding: utf-8 -*-
"""
ScrcpySession - 基于自研 ADB 的投屏会话
======================================
完全进程内闭环，不依赖外部 adb.exe / adb server。

核心优化:
  1. 自研 ADB 直连设备，去掉 5037 回环
  2. 忽略 server 的 pts，用本地 monotonic 时间戳，解决 5 秒延迟
  3. 视频流零拷贝，收到 WRTE 立即解码渲染
  4. 启动前设备预检（API >= 21、app_process 存在），不支持的设备直接给出明确原因
  5. reverse 隧道失败自动回退 forward（与官方 scrcpy 一致，
     部分设备/网络模式下 adb reverse 不可用）

用法:
    from 工具.自研adb import 自研adb客户端
    from 工具.自研adb.scrcpy会话 import ScrcpySession

    adb = 自研adb客户端('192.168.75.18', 5555)
    adb.连接()
    session = ScrcpySession(adb)
    session.启动()
    frame = session.获取原始帧()  # AVFrame，供 OpenGL 零拷贝渲染
    session.点击(500, 800)
    session.停止()
"""

import os
import socket
import struct
import threading
import time
import uuid
from typing import Optional, Tuple

try:
    from 工具.h264解码器 import H264解码器
except ImportError:
    try:
        from 工具.自研adb.工具.h264解码器 import H264解码器  # 兼容非常规导入路径
    except ImportError:
        H264解码器 = None

# Media Foundation 硬件解码器（仅 Windows，可选）
try:
    from 工具.mf_h264解码器 import MF_H264解码器, 可用 as _mf可用
except ImportError:
    try:
        from 工具.自研adb.工具.mf_h264解码器 import MF_H264解码器, 可用 as _mf可用
    except ImportError:
        MF_H264解码器 = None
        def _mf可用():
            return False

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    QObject = object
    Signal = None

from 工具.自研adb.adb协议 import 借用连接 as _池借用, 剥离连接 as _池剥离, AdbConnection


# ─────────────────── 常量 ───────────────────
_SCRCPY_SERVER_REMOTE = "/data/local/tmp/scrcpy-server"
_DEFAULT_PORT = 27183
_DEFAULT_MAX_SIZE = 1024
_DEFAULT_MAX_FPS = 60
_DEFAULT_BIT_RATE = 8_000_000

# 控制消息类型
_TYPE_INJECT_KEYCODE = 0
_TYPE_INJECT_TEXT = 1
_TYPE_INJECT_TOUCH = 2
_TYPE_INJECT_SCROLL = 3
_TYPE_BACK_OR_SCREEN_ON = 4
_TYPE_EXPAND_NOTIFICATION = 5
_TYPE_EXPAND_SETTINGS = 6
_TYPE_COLLAPSE_PANELS = 7
_TYPE_GET_CLIPBOARD = 8
_TYPE_SET_CLIPBOARD = 9
_TYPE_SET_DISPLAY_POWER = 10
_TYPE_ROTATE_DEVICE = 11

# 触摸动作
_ACTION_DOWN = 0
_ACTION_UP = 1
_ACTION_MOVE = 2


class _帧信号(QObject):
    """帧就绪信号。"""
    帧就绪 = Signal()
    解码器回退 = Signal(str, str)  # (原后端, 新后端)，用于弹窗提示用户


class ScrcpySession:
    """基于自研 ADB 的 scrcpy 投屏会话。

    Args:
        adb: 自研adb客户端 实例（已连接）
        server_path: 本地 scrcpy-server 文件路径
        max_size: 视频最大尺寸
        max_fps: 最大帧率
        bit_rate: 比特率
        ignore_pts: 是否忽略 server 的 pts（默认 True，解决延迟）
        use_reverse: 是否优先使用 reverse 隧道（默认 False：自研 ADB 直连
            adbd 无 host:reverse 服务且无法中转回连，reverse 不可用；
            forward 模式由 server 监听 localabstract，PC 直连隧道流）
    """

    def __init__(self, adb, server_path: str = None,
                 max_size: int = _DEFAULT_MAX_SIZE,
                 max_fps: int = _DEFAULT_MAX_FPS,
                 bit_rate: int = _DEFAULT_BIT_RATE,
                 video_codec: str = 'h264',
                 server_version: str = '4.1',
                 ignore_pts: bool = True,
                 use_reverse: bool = False,
                 video_encoder: str = None,
                 video_codec_options: str = None,
                 fallback_sw_encoder: bool = True,
                 解码器后端: str = 'auto'):
        if H264解码器 is None and MF_H264解码器 is None:
            raise ImportError("需要 openh264 或 mf_h264 动态库（外部扩展/）")
        self.adb = adb
        self.server_path = server_path or self._默认server路径()
        self.max_size = max_size
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.video_codec = video_codec
        self.server_version = server_version
        self.ignore_pts = ignore_pts
        self.use_reverse = use_reverse
        # 解码器后端: 'auto'=优先MF硬件解码,失败回退openh264; 'mf'=仅MF; 'openh264'=仅软解
        self.解码器后端 = 解码器后端
        self._实际解码器后端 = None  # 实际使用的后端（auto模式下可能回退）
        # 指定编码器名（如 c2.android.avc.encoder）与附加编码参数
        # （scrcpy CodecOption 格式: key:type=value，逗号分隔多个）
        self.video_encoder = video_encoder
        self.video_codec_options = video_codec_options
        # 默认编码器失败后是否自动切软编码器重试（默认 True，强烈推荐开启）
        self.fallback_sw_encoder = bool(fallback_sw_encoder)
        # 随机隧道名后缀，scrcpy 4.1 要求 localabstract:scrcpy_<scid>
        # shell 命令和 reverse 命令必须使用相同的 scid
        # scid 必须是 31-bit 非负值：官方 Options.parse 用有符号
        # Integer.parseInt(value,16) 解析，≥0x80000000 会 NumberFormatException
        self.scid = f'{uuid.uuid4().int & 0x7FFFFFFF:08x}'
        # reverse/forward 都用 scid 命名隧道，server 命令恒带 scid=
        self._隧道名 = f'scrcpy_{self.scid}'

        self.帧信号 = _帧信号()
        self.帧就绪 = self.帧信号.帧就绪
        self.解码器回退 = self.帧信号.解码器回退

        self._视频socket: Optional[socket.socket] = None
        self._控制socket: Optional[socket.socket] = None
        self._监听socket: Optional[socket.socket] = None  # reverse模式下的监听socket
        self._端口 = _DEFAULT_PORT
        self._设备宽 = 0
        self._设备高 = 0
        self._设备名 = ""

        self._解码器 = None
        self._当前原始帧 = None
        self._帧锁 = threading.Lock()
        self._运行中 = False
        self._接收线程: Optional[threading.Thread] = None
        self._server线程: Optional[threading.Thread] = None
        # server 长时 exec 独占的独立连接（从池借出后剥离，不归还，
        # 避免与主连接上的 reverse/forward 命令并发读同一 socket 串报文）
        self._server_conn = None
        # 捕获 server 输出，启动失败时附在错误信息里，避免只有 "timed out"
        self._server输出: list = []
        # 设备预检选出的 app_process 二进制（部分设备只有 app_process32/64）
        self._app_bin = 'app_process'

        # 性能统计
        self._帧计数 = 0
        self._跳帧计数 = 0        # 追帧模式下只作参考、未上屏的帧数
        self._首帧时间 = 0
        self._最近帧时间 = 0
        # 解码异常只打印一次，避免刷屏（黑屏排查用）
        self._解码异常已报 = False

    # ─────────────────── 公共 API ───────────────────

    def 启动(self) -> bool:
        """启动投屏，返回是否成功。

        启动顺序（失败时自动按序兜底，最多两次完整的 server 启动尝试）：
          1. 按 use_reverse 选择首选隧道（reverse / forward），使用设备默认
             编码器（self.video_encoder 未指定时 = OMX/硬编码器路径）；
          2. 若首尝试 server 进程退出时命中 Aborted / 编码器异常信号：
              a) 若首尝试已显式指定 video_encoder（含手动软编码器）→ 判
                 定为设备端没有可用编码器，抛出【设备不支持】；
              b) 若仍在使用"默认编码器" → 自动切换到 Google 平台软编码器
                 `c2.android.avc.encoder` 再完整重试一次（首选隧道 + 若为
                 reverse 失败继续 forward 兜底不变）；
          3. 软编码器重试仍失败：再次核对 server 输出，若依旧是编码器 /
             Aborted 类失败，抛出带【设备不支持】标签的明确异常，UI 层应
             提示用户“设备不支持投屏（无可用视频编码器 / 架构不匹配）”。

        这样覆盖了 4 条失败路径：
          - use_reverse=True，reverse 成功但默认编码器 Aborted
            （历史版本此处漏了软编码器回退 → 用户看到的 "server 退出: Aborted"
            正是此路径）
          - use_reverse=True，reverse 失败回退 forward，forward 下默认编码器 Aborted
          - use_reverse=False，forward 下默认编码器 Aborted
          - 显式指定了软编码器，仍然 Aborted
        """
        if self._运行中:
            return True

        # 记录用户最初传入的 encoder：若调用方已经手动指定过非空 encoder，
        # 说明外部已经做出了选择，兜底重试不强制覆盖用户意图。
        _首尝试编码器 = self.video_encoder
        _已经过重试 = False  # True = 已经走完一次“软编码器兜底”

        def _等server输出落地():
            if self._server输出:
                return
            deadline = time.time() + 2.0
            while time.time() < deadline and not self._server输出:
                time.sleep(0.05)

        def _跑一次完整启动():
            """首选 use_reverse → 若失败且为隧道类错误则自动回退 forward。"""
            # 每次重试都换一个新 scid，避免前一次 abort 的 server 残留
            # 仍占着旧 localabstract:scrcpy_<scid> 导致握手再次失败。
            self.scid = f'{uuid.uuid4().int & 0x7FFFFFFF:08x}'
            self._隧道名 = f'scrcpy_{self.scid}'
            self._设备宽 = 0
            self._设备高 = 0

            if self.use_reverse:
                try:
                    print('[ScrcpySession] 尝试 reverse 隧道...')
                    self._尝试启动(reverse=True)
                except (socket.timeout, ConnectionRefusedError, OSError, RuntimeError) as e:
                    # 隧道 / 网络 / server 已退出 —— 全量回退 forward
                    print(f'[ScrcpySession] reverse 隧道失败，回退 forward: {e}')
                    self._清理尝试()
                    self._尝试启动(reverse=False)
            else:
                self._尝试启动(reverse=False)

        try:
            self._设备预检()
            print(f'[ScrcpySession] 推送 scrcpy-server (版本 {self.server_version})...')
            self._推送server()
            try:
                _跑一次完整启动()
            except Exception:
                _等server输出落地()
                # 软编码器回退条件：
                #   1. fallback_sw_encoder = True（用户开启了回退）
                #   2. 尚未重试过
                #   3. 确认为编码器故障（而非网络/隧道问题）
                if (self.fallback_sw_encoder
                        and not _已经过重试
                        and self._是编码器故障()):
                    print('[ScrcpySession] 首尝试编码器不兼容'
                          '（Aborted / not found / Capture/encoding error / MediaCodec IAE），'
                          '自动切换软编码器 c2.android.avc.encoder 重试一次...')
                    self._清理尝试()
                    self.video_encoder = 'c2.android.avc.encoder'
                    _已经过重试 = True
                    # 重新执行预检和推送，确保设备端状态干净
                    # （某些设备上第一次 server abort 后立即重启会再次 Aborted，
                    #  经过一次主连接交互后恢复正常）
                    self._设备预检()
                    self._推送server()
                    try:
                        _跑一次完整启动()
                    except Exception:
                        _等server输出落地()
                        # 软编码器仍失败 → 合并两次 server 输出给 UI，
                        # 明确抛出【设备不支持】异常
                        is_support_err = self._是编码器故障() \
                            or ('Aborted' in '\n'.join(self._server输出))
                        if is_support_err:
                            tail = '\n'.join(self._server输出[-16:])
                            msg = ('【设备不支持】scrcpy 投屏: 设备端没有可用的视频编码器'
                                   '（OMX硬编码器 + Google软编码器 均 Aborted/'
                                   'IllegalArgumentException 或架构不匹配）。'
                                   '\n建议：1) 确认设备 Android 版本 5.0+；'
                                   '\n2) 投屏设置里手动指定 video-codec 为 h265 或'
                                   ' av1（如设备支持）再试；'
                                   '\n3) 或改用 USB 连接 + 外部扩展/scrcpy/ 目录下'
                                   '的官方 scrcpy 客户端投屏。')
                            if tail:
                                msg = f'{msg}\nserver输出(尾部):\n{tail}'
                            self.停止()
                            raise RuntimeError(msg)
                        raise
                else:
                    # 走到这里的几类场景：
                    #   a) fallback_sw_encoder = False（用户关闭了回退）
                    #   b) 已经走过一次软编码器回退，仍然失败
                    #   c) 非编码器故障（纯网络/超时等），正常上抛
                    # 对 a) 且为首尝试显式指定 encoder 失败的情况，
                    #    贴上【设备不支持】标签，UI 能给出明确提示
                    _等server输出落地()
                    _tail_all = '\n'.join(self._server输出)
                    if _首尝试编码器 and (self._是编码器故障()
                                           or 'Aborted' in _tail_all):
                        msg = ('【设备不支持】scrcpy 投屏: 指定的视频编码器 '
                               f'`{_首尝试编码器}` 仍在 server 启动阶段 '
                               'Aborted/IllegalArgumentException，设备端不支持该编码器，'
                               '建议在投屏设置里切回「自动选择」或换 h265/av1。')
                        if _tail_all:
                            msg = f'{msg}\nserver输出(尾部):\n' + _tail_all[-800:]
                        self.停止()
                        raise RuntimeError(msg)
                    raise

            self._运行中 = True
            self._接收线程 = threading.Thread(target=self._接收循环, daemon=True)
            self._接收线程.start()
            print(f'[ScrcpySession] 启动成功，设备尺寸: {self._设备宽}x{self._设备高}')
            return True
        except RuntimeError as e:
            # 【设备不支持】类异常已经在上方拼装完整，原样上抛，UI 层识别。
            print(f'[ScrcpySession] 启动失败: {e}')
            if '【设备不支持】' in str(e):
                raise
            self.停止()
            tail = '\n'.join(self._server输出[-8:])
            if tail:
                raise RuntimeError(f'{e}\nserver输出:\n{tail}') from e
            raise
        except Exception as e:
            print(f'[ScrcpySession] 启动失败: {e}')
            self.停止()
            tail = '\n'.join(self._server输出[-8:])
            if tail:
                raise RuntimeError(f'{e}\nserver输出:\n{tail}') from e
            raise

    def 停止(self):
        """停止投屏，释放资源。"""
        self._运行中 = False
        if self._接收线程:
            self._接收线程.join(timeout=2)
            self._接收线程 = None
        for sock in (self._视频socket, self._控制socket, self._监听socket):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._视频socket = None
        self._控制socket = None
        self._监听socket = None
        # 无论最终用的哪种隧道，两种转发都清一遍（回退场景会残留）
        try:
            self.adb.取消反向转发(f'localabstract:{self._隧道名}')
        except Exception:
            pass
        try:
            self.adb.取消端口转发(self._端口)
        except Exception:
            pass
        # 关闭 server 独占连接 → 设备端 app_process 随之退出
        if self._server_conn is not None:
            conn = self._server_conn
            self._server_conn = None  # 先置空，让 server 线程忽略随后的关闭错误
            try:
                conn.关闭()
            except Exception:
                pass
        self._server线程 = None
        if self._解码器 is not None:
            try:
                self._解码器.关闭()
            except Exception:
                pass
        self._解码器 = None
        self._当前原始帧 = None

    def 获取原始帧(self):
        """获取当前原始 H.264 帧（供 OpenGL 零拷贝渲染）。"""
        with self._帧锁:
            return self._当前原始帧

    def 获取帧(self):
        """获取当前帧（H264帧对象，含 width/height/planes）。"""
        with self._帧锁:
            return self._当前原始帧

    @property
    def 设备尺寸(self) -> Tuple[int, int]:
        return (self._设备宽, self._设备高)

    @property
    def 运行中(self) -> bool:
        return self._运行中

    @property
    def 帧率(self) -> float:
        """当前帧率（最近 1 秒）。"""
        if self._首帧时间 == 0:
            return 0
        elapsed = time.monotonic() - self._首帧时间
        if elapsed <= 0:
            return 0
        return self._帧计数 / elapsed

    # ─────────────────── 控制 API ───────────────────

    def 点击(self, x: int, y: int):
        """点击屏幕坐标。"""
        self._发送触摸(_ACTION_DOWN, x, y)
        time.sleep(0.05)
        self._发送触摸(_ACTION_UP, x, y)

    def 滑动(self, x1: int, y1: int, x2: int, y2: int, 时长: float = 0.3):
        """从 (x1,y1) 滑动到 (x2,y2)。"""
        self._发送触摸(_ACTION_DOWN, x1, y1)
        steps = max(5, int(时长 * 60))
        for i in range(1, steps + 1):
            t = i / steps
            x = int(x1 + (x2 - x1) * t)
            y = int(y1 + (y2 - y1) * t)
            self._发送触摸(_ACTION_MOVE, x, y)
            time.sleep(时长 / steps)
        self._发送触摸(_ACTION_UP, x2, y2)

    def 输入文本(self, text: str):
        """输入文本（支持中文）。"""
        if not text:
            return
        data = text.encode('utf-8')
        msg = struct.pack('>BI', _TYPE_INJECT_TEXT, len(data)) + data
        self._发送控制消息(msg)

    def 按键(self, keycode: int, action: int = 1):
        """发送按键事件。"""
        msg = struct.pack('>BBiii', _TYPE_INJECT_KEYCODE, action, keycode, 0, 0)
        self._发送控制消息(msg)

    def 返回(self):
        self.按键(4, 0)
        time.sleep(0.05)
        self.按键(4, 1)

    def 主页(self):
        self.按键(3, 0)
        time.sleep(0.05)
        self.按键(3, 1)

    # ─────────────────── 内部实现 ───────────────────

    def _默认server路径(self) -> str:
        # __file__ = 工具/自研adb/scrcpy会话.py
        # dirname 1次 = 工具/自研adb
        # dirname 2次 = 工具
        # dirname 3次 = Super_ADB_Win
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates = [
            os.path.join(base, '外部扩展', 'scrcpy', 'scrcpy-win64-v4.1', 'scrcpy-server'),
            os.path.join(base, '外部扩展', 'scrcpy', 'scrcpy-server'),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        raise FileNotFoundError("未找到 scrcpy-server，请指定 server_path")

    def _设备预检(self):
        """启动前检查设备是否支持 scrcpy 投屏，给出明确错误而非超时。"""
        # 1. Android API 检查：scrcpy 要求 API >= 21 (Android 5.0)
        try:
            sdk_out = (self.adb.执行shell('getprop ro.build.version.sdk', timeout=5) or '').strip()
            sdk = int(sdk_out.split()[0]) if sdk_out else 0
        except Exception:
            sdk = 0
        if 0 < sdk < 21:
            raise RuntimeError(f"设备 Android 版本过低 (API {sdk} < 21)，不支持 scrcpy 投屏")
        # 2. app_process 存在性检查，并选出可用的二进制
        #    （部分设备只有 app_process32 或 app_process64）
        out = (self.adb.执行shell(
            'ls /system/bin/app_process /system/bin/app_process64 '
            '/system/bin/app_process32 2>&1', timeout=5) or '')
        found = [ln.strip() for ln in out.splitlines()
                 if ln.strip() and 'No such file' not in ln]
        if found:
            for ln in found:
                # ls 输出可能是 "ls: ..." 错误行，只取真实路径
                if ln.endswith('app_process'):
                    self._app_bin = 'app_process'
                    break
                if ln.endswith('app_process64'):
                    self._app_bin = 'app_process64'
                    break
                if ln.endswith('app_process32'):
                    self._app_bin = 'app_process32'
            print(f'[ScrcpySession] 预检: API={sdk or "未知"}, app_bin={self._app_bin}')
        else:
            raise RuntimeError("设备缺少 app_process，无法启动 scrcpy 投屏（非标准 Android 系统）")

    def _推送server(self):
        """推送 scrcpy-server 到设备（自研 ADB sync 协议）。"""
        try:
            local_size = os.path.getsize(self.server_path)
            remote_info = self.adb.执行shell(f'ls -l {_SCRCPY_SERVER_REMOTE} 2>/dev/null', timeout=5)
            if remote_info and str(local_size) in remote_info:
                print(f'[ScrcpySession] server已存在，跳过推送 ({local_size} bytes)')
                return
        except Exception:
            pass
        print(f'[ScrcpySession] 推送 server ({os.path.getsize(self.server_path)} bytes)...')
        self.adb.推送文件(self.server_path, _SCRCPY_SERVER_REMOTE, timeout=120)

    def _启动server(self, tunnel_forward: bool):
        """在设备上启动 scrcpy-server（自研 ADB exec: 服务，后台线程）。

        server 是长时 exec，必须用从池借出并剥离的独立连接，
        不能占用客户端主连接——否则主连接上再发 reverse/forward 命令
        时会与 server 输出并发读同一 socket，串报文导致双方卡死。
        """
        cmd = (
            f'CLASSPATH={_SCRCPY_SERVER_REMOTE} '
            f'{self._app_bin} / com.genymobile.scrcpy.Server '
            f'{self.server_version} '
        )
        if self.max_size > 0:
            cmd += f'max_size={self.max_size} '
        cmd += (
            f'max_fps={self.max_fps} '
            f'video_bit_rate={self.bit_rate} '
            f'video_codec={self.video_codec} '
            f'log_level=info '
            f'audio=false '
            f'scid={self.scid} '
        )
        if self.video_encoder:
            cmd += f'video_encoder={self.video_encoder} '
        if self.video_codec_options:
            cmd += f'video_codec_options={self.video_codec_options} '
        if tunnel_forward:
            # forward模式 server 监听 localabstract，PC 主动连接
            cmd += 'tunnel_forward=true'
        print(f'[ScrcpySession] server命令: {cmd}')
        print(f'[ScrcpySession] 隧道名: localabstract:{self._隧道名}')

        # 借一个独立连接并从池剥离（不归还），server 退出/停止时关闭
        self._server输出 = []
        conn = _池借用(self.adb.host, self.adb.port, timeout=10.0,
                        key_path=self.adb.key_path)
        _池剥离(conn)  # 从池剥离（不再归还），生命周期由本会话负责
        self._server_conn = conn

        def _净化输出(text: str) -> str:
            """清理 shell 输出中的传输层残留字节（ADB 帧头/U+FFFD 等不可打印内容），
            仅保留可打印字符与换行、制表，保证日志与错误提示可读。"""
            text = text.replace('\r\n', '\n').replace('\r', '\n')
            return ''.join(
                ch for ch in text
                if ch in '\n\t' or (ch.isprintable() and ch != '\ufffd')
            )

        def _运行server():
            try:
                # exec: 服务会持续运行，直到连接被关闭
                result = _净化输出(str(self._server_conn.执行shell(cmd, timeout=3600)))
                self._server输出.append(result[:500])
                print(f'[ScrcpySession] server 退出: {result[:200]}')
            except Exception as e:
                # 清理时 _server_conn 被主动关闭引发的错误（如 10038）不算 server 异常
                if self._server_conn is not None:
                    msg = _净化输出(str(e))
                    self._server输出.append(msg)
                    print(f'[ScrcpySession] server 线程异常: {msg}')

        self._server线程 = threading.Thread(target=_运行server, daemon=True)
        self._server线程.start()

    def _设置reverse(self):
        """设置 reverse 隧道（设备主动连接 PC）。

        1. PC 端监听端口
        2. 设置 reverse: 设备 localabstract:scrcpy_<scid> -> PC 端口
        3. scrcpy-server 连接设备上的 localabstract:scrcpy_<scid>，数据到 PC
        """
        # 1. PC 端监听端口
        self._监听socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._监听socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._监听socket.bind(('127.0.0.1', self._端口))
        self._监听socket.listen(2)  # 视频 + 控制两个连接
        self._监听socket.settimeout(10)
        print(f'[ScrcpySession] PC 监听端口: {self._端口}')

        # 2. 设置 reverse：设备端 remote 必须是 localabstract:scrcpy_<scid>
        #    直连 adbd 不支持 host:reverse 服务（需 ADB server 中转），
        #    设置失败立即抛错走 forward 回退，避免白等 6 秒超时
        try:
            self.adb.取消反向转发(f'localabstract:{self._隧道名}')
        except Exception:
            pass
        if not self.adb.反向转发(f'localabstract:{self._隧道名}', self._端口):
            raise RuntimeError('reverse 隧道设置失败（自研ADB直连模式不支持）')
        print(f'[ScrcpySession] reverse 已设置: 设备 localabstract:{self._隧道名} -> PC tcp:{self._端口}')

    def _是编码器故障(self) -> bool:
        """根据 server 输出判断是否为编码器配置失败（而非网络/隧道问题）。

        覆盖以下失败路径：
          1) Java 层被 scrcpy SurfaceEncoder 捕获的异常：输出含
             'Capture/encoding error' / 'Applying video encoder constraints'
             / MediaCodec+IllegalArgumentException 等可读日志；
          2) MediaCodec/OMX 服务直接 native crash（SIGABRT）：shell 层只打印
             一行 'Aborted'，没有任何 Java 堆栈（典型：Allwinner OMX 硬编码器
             拒绝 KEY_LATENCY，键存在即触发 abort，app_process 整进程死亡）；
          3) 指定的编码器不存在：server 输出 'Video encoder ... not found'
             （设备上没有对应名称的编码器，如指定了 OMX 硬编码器但设备只有软编）。
        启动阶段（连接视频 socket 前）即崩溃，属于编码器故障的概率极高，
        命中 Aborted 时一律尝试软编码器回退。
        """
        tail = '\n'.join(self._server输出)
        # ① Java 层已知异常字符串
        if ('Capture/encoding error' in tail
                or 'Applying video encoder constraints' in tail
                or ('MediaCodec' in tail and 'IllegalArgumentException' in tail)):
            return True
        # ② 指定的编码器不存在（设备端没有对应 encoder name）
        if 'Video encoder' in tail and 'not found' in tail:
            return True
        if 'video encoder' in tail and 'not found' in tail:
            return True
        # ③ native abort：shell 输出只有 Aborted
        if 'Aborted' in tail or 'SIGABRT' in tail:
            # 只有在启动早期（server 尚未正常进入 accept 循环）才认定为编码器问题，
            # 避免运行中因其他原因 abort 时被误判。这里通过 self._设备宽是否已
            # 赋值（= 元数据已收到 = 已进入编码循环）来区分。
            if self._设备宽 == 0:
                return True
        return False

    def _尝试启动(self, reverse: bool):
        """按指定隧道模式完成一次完整的启动尝试。"""
        print('[ScrcpySession] 启动 server...')
        self._启动server(tunnel_forward=not reverse)
        time.sleep(1.0)
        if reverse:
            print('[ScrcpySession] 设置 reverse 隧道...')
            self._设置reverse()
        else:
            print('[ScrcpySession] forward: 直连 adbd localabstract 流（无需端口转发）...')
        print('[ScrcpySession] 连接视频 socket...')
        self._连接视频socket(reverse=reverse)
        print('[ScrcpySession] 初始化解码器...')
        self._初始化解码器()

    def _清理尝试(self):
        """清理一次失败的启动尝试，为回退重试做准备。"""
        self._运行中 = False
        if self._接收线程:
            self._接收线程.join(timeout=2)
            self._接收线程 = None
        for sock in (self._视频socket, self._控制socket, self._监听socket):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._视频socket = None
        self._控制socket = None
        self._监听socket = None
        # 无论最终用的哪种隧道，两种转发都清一遍（回退场景会残留）
        try:
            self.adb.取消反向转发(f'localabstract:{self._隧道名}')
        except Exception:
            pass
        try:
            self.adb.取消端口转发(self._端口)
        except Exception:
            pass
        # 关闭 server 独占连接 → 设备端 app_process 随之退出
        if self._server_conn is not None:
            conn = self._server_conn
            self._server_conn = None  # 先置空，让 server 线程忽略随后的关闭错误
            try:
                conn.关闭()
            except Exception:
                pass
        self._server线程 = None
        if self._解码器 is not None:
            try:
                self._解码器.关闭()
            except Exception:
                pass
        self._解码器 = None
        self._当前原始帧 = None
        # 设备端释放编码器/显示资源需要一点时间，否则下一次启动可能 Aborted
        time.sleep(1.5)

    def _连接视频socket(self, reverse: bool):
        """连接视频流 socket。"""
        if reverse and self._监听socket:
            # reverse模式：accept 设备的连接
            print('[ScrcpySession] 等待设备连接 (reverse)...')
            sock, addr = self._监听socket.accept()
            print(f'[ScrcpySession] 设备已连接: {addr}')
        else:
            # forward模式：直连 adbd 的 localabstract 隧道流。
            # server 端 JVM 初始化需数秒，listener 就绪前打开服务会被 CLSE 拒绝，
            # 因此轮询重试；期间 server 退出则立即失败并带出其输出
            sock = None
            deadline = time.time() + 20
            while sock is None:
                if self._server线程 and not self._server线程.is_alive():
                    raise RuntimeError('server 已退出: ' + ' '.join(self._server输出)[-400:])
                try:
                    sock = self.adb.打开隧道socket(f'localabstract:{self._隧道名}')
                except Exception:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.5)
            print('[ScrcpySession] 视频socket已连接，等待数据...')

        # 1. 读取 dummy byte
        dummy = self._精确接收(sock, 1)
        print(f'[ScrcpySession] dummy byte: {dummy.hex()}')

        # 2. 立即连接 control socket
        print('[ScrcpySession] 连接控制 socket...')
        self._连接控制socket(reverse=reverse)

        # 3. 读取 64 字节设备名称
        name_buf = self._精确接收(sock, 64)
        self._设备名 = name_buf.rstrip(b'\x00').decode('utf-8', errors='replace')
        print(f'[ScrcpySession] 设备名: {self._设备名}')

        # 4. 读取视频流头部
        codec_id_buf = self._精确接收(sock, 4)
        codec_id = struct.unpack('>I', codec_id_buf)[0]
        print(f'[ScrcpySession] codec_id: {codec_id} (0x{codec_id:x})')
        session_meta = self._精确接收(sock, 12)
        flags, self._设备宽, self._设备高 = struct.unpack('>III', session_meta)
        print(f'[ScrcpySession] session_meta: flags=0x{flags:x}, 尺寸: {self._设备宽}x{self._设备高}')

        sock.settimeout(None)
        self._视频socket = sock

    @staticmethod
    def _精确接收(sock, n):
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("视频连接断开")
            buf += chunk
        return buf

    def _连接控制socket(self, reverse: bool):
        """连接控制 socket。"""
        if reverse and self._监听socket:
            # reverse模式：accept 第二个连接
            sock, addr = self._监听socket.accept()
            print(f'[ScrcpySession] 控制socket已连接: {addr}')
        else:
            # forward模式：直连第二条 adbd 隧道流（控制）
            sock = None
            deadline = time.time() + 10
            while sock is None:
                try:
                    sock = self.adb.打开隧道socket(f'localabstract:{self._隧道名}')
                except Exception:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.5)
        self._控制socket = sock

    def _初始化解码器(self):
        """按 解码器后端 设置创建解码器，auto 模式优先 MF 硬件解码，失败回退 openh264。"""
        后端 = self.解码器后端
        if 后端 == 'auto':
            # auto: 优先 MF 硬件解码（CPU占用低），不可用或创建失败则回退 openh264
            if _mf可用() and MF_H264解码器 is not None:
                try:
                    self._解码器 = MF_H264解码器()
                    self._实际解码器后端 = 'mf'
                    print('[ScrcpySession] 使用 Media Foundation 硬件解码')
                    return
                except Exception as e:
                    print(f'[ScrcpySession] MF 硬件解码初始化失败，回退 openh264: {e}')
            if H264解码器 is not None:
                self._解码器 = H264解码器()
                self._实际解码器后端 = 'openh264'
                print('[ScrcpySession] 使用 openh264 软件解码')
                return
            raise ImportError("MF 和 openh264 解码器均不可用")
        elif 后端 == 'mf':
            if not _mf可用() or MF_H264解码器 is None:
                raise ImportError("Media Foundation 硬件解码不可用（缺少 mf_h264_decoder.dll 或系统不支持）")
            self._解码器 = MF_H264解码器()
            self._实际解码器后端 = 'mf'
            print('[ScrcpySession] 使用 Media Foundation 硬件解码')
        elif 后端 == 'openh264':
            if H264解码器 is None:
                raise ImportError("openh264 解码器不可用")
            self._解码器 = H264解码器()
            self._实际解码器后端 = 'openh264'
            print('[ScrcpySession] 使用 openh264 软件解码')
        else:
            raise ValueError(f"未知解码器后端: {后端}（应为 auto/mf/openh264）")

    @staticmethod
    def _缓冲首个完整包大小(buffer) -> int:
        """返回缓冲区开头是否已是一个完整包：-1 表示不完整，否则返回总字节数。"""
        if len(buffer) < 12:
            return -1
        pts_flags = struct.unpack_from('>Q', buffer, 0)[0]
        if pts_flags >> 63:
            return 12  # 会话元数据包，定长 12 字节无 payload
        packet_size = struct.unpack_from('>I', buffer, 8)[0]
        if packet_size > 10_000_000:
            return 12  # 异常包，交给主循环处理
        if len(buffer) < 12 + packet_size:
            return -1
        return 12 + packet_size

    def _接收循环(self):
        """后台线程：接收并解码视频帧。

        核心优化 1（低延迟）：忽略 server 的 pts，收到帧立即解码渲染。
        pts_flags 高 32 位是 pts，低 32 位是 flags；
        设备 pts 可能比墙钟大几十秒，导致 MediaCodec 等待正确显示时间。
        这里直接丢弃 pts，用本地 monotonic 时间戳。

        核心优化 2（追帧，防延迟累积）：每轮先把隧道里已到达的数据全部收干，
        若缓冲区内积压了多个完整帧，则中间的旧帧用「仅参考」模式解码——
        推进参考帧链但跳过 YUV 拷贝与上屏，只完整输出最新一帧。
        解码吞吐（约 22fps）低于设备出帧（30fps）时，若不追帧，
        每秒积压约 8 帧，几十秒后延迟就会累积到几十秒。
        """
        buffer = bytearray()
        self._帧计数 = 0
        self._跳帧计数 = 0
        self._首帧时间 = 0
        # MF 硬解运行时回退：若前 N 个包无帧输出，自动切到 openh264 软解
        # （部分设备的 Baseline profile 流不被系统首选硬解码器支持）
        _mf无帧计数 = 0
        _mf回退阈值 = 30
        _已回退 = False
        _spspps缓存 = b''  # 缓存 SPS/PPS，回退时喂给新解码器
        _idr缓存 = b''     # 缓存第一个 IDR 帧，回退时重放
        print('[ScrcpySession] 接收循环启动 (ignore_pts=%s)' % self.ignore_pts)

        while self._运行中:
            try:
                chunk = self._视频socket.recv(262144)  # 256KB
                if not chunk:
                    print('[ScrcpySession] 视频流结束')
                    break
                buffer.extend(chunk)

                # 追帧第一步：把隧道队列里已到达的数据一次性收干，
                # 避免数据滞留在队列里形成不可见的积压
                队列 = getattr(self._视频socket, '_队列', None)
                while 队列 is not None and not 队列.empty():
                    more = self._视频socket.recv(262144)
                    if not more:
                        break
                    buffer.extend(more)

                # 解析 12 字节帧头: 8字节 pts_flags + 4字节 packet_size
                while len(buffer) >= 12:
                    pts_flags = struct.unpack_from('>Q', buffer, 0)[0]
                    if pts_flags >> 63:
                        # 会话元数据包（分辨率变化，scrcpy v4 Streamer.writeSessionMeta）：
                        # 布局 4B flags + 4B width + 4B height，恰好落在 12 字节
                        # 帧头位置（低32位=宽，"packet_size"位=高），无 payload
                        self._设备宽 = pts_flags & 0xFFFFFFFF
                        self._设备高 = struct.unpack_from('>I', buffer, 8)[0]
                        print(f'[ScrcpySession] 会话元数据: 尺寸变化为 '
                              f'{self._设备宽}x{self._设备高}')
                        del buffer[:12]
                        continue
                    # pts_flags 在这里被忽略（不使用）
                    packet_size = struct.unpack_from('>I', buffer, 8)[0]
                    if packet_size > 10_000_000:
                        print(f'[ScrcpySession] 包大小异常: {packet_size}, 清空buffer')
                        buffer.clear()
                        break
                    if len(buffer) < 12 + packet_size:
                        break
                    h264_data = bytes(buffer[12:12 + packet_size])
                    del buffer[:12 + packet_size]
                    # 缓存 SPS/PPS（含 NAL type 7 的包），供解码器回退时使用
                    if not _spspps缓存 and (b'\x00\x00\x00\x01g' in h264_data or b'\x00\x00\x01g' in h264_data):
                        _spspps缓存 = h264_data
                    # 缓存第一个 IDR 帧（NAL type 5），回退时重放给新解码器
                    if not _idr缓存 and (b'\x00\x00\x00\x01e' in h264_data or b'\x00\x00\x01e' in h264_data):
                        _idr缓存 = h264_data
                    # 追帧第二步：后面还有完整包时，本帧只作参考帧解码，不上屏
                    仅参考 = self._缓冲首个完整包大小(buffer) > 0
                    # scrcpy 无 B 帧，解码序即显示序，一包直接解一帧
                    # 注意：H264解码器 的方法名是「解码」，不是 decode；
                    # 曾误写 decode 导致 AttributeError 被静默吞掉 → 全程黑屏
                    try:
                        frame = self._解码器.解码(h264_data, 仅参考=仅参考)
                    except Exception as e:
                        if not self._解码异常已报:
                            self._解码异常已报 = True
                            print(f'[ScrcpySession] 解码异常: {type(e).__name__}: {e}')
                        continue
                    if 仅参考:
                        self._跳帧计数 += 1
                        continue
                    if frame is None:
                        # MF 硬解回退检测：连续无帧输出达到阈值时自动切 openh264
                        if (not _已回退 and self._实际解码器后端 == 'mf'
                                and H264解码器 is not None):
                            _mf无帧计数 += 1
                            if _mf无帧计数 >= _mf回退阈值:
                                print(f'[ScrcpySession] MF 硬解 {_mf无帧计数} 包无输出，'
                                      f'自动回退 openh264 软解')
                                try:
                                    self._解码器.关闭()
                                except Exception:
                                    pass
                                self._解码器 = H264解码器()
                                self._实际解码器后端 = 'openh264'
                                _已回退 = True
                                _mf无帧计数 = 0
                                # 用户手动选了硬件解码时，发出回退信号供 UI 弹窗提示
                                if self.解码器后端 == 'mf':
                                    try:
                                        self.解码器回退.emit('mf', 'openh264')
                                    except Exception:
                                        pass
                                # 直接把缓存的 SPS/PPS 和 IDR 喂给新解码器，
                                # 确保新解码器有参考帧才能解后续 P 帧
                                if _spspps缓存:
                                    try:
                                        self._解码器.解码(_spspps缓存, 仅参考=True)
                                    except Exception:
                                        pass
                                if _idr缓存:
                                    try:
                                        idr_frame = self._解码器.解码(_idr缓存)
                                        if idr_frame:
                                            with self._帧锁:
                                                self._当前原始帧 = idr_frame
                                            self._帧计数 += 1
                                            if self._首帧时间 == 0:
                                                self._首帧时间 = time.monotonic()
                                            print(f'[ScrcpySession] 回退后 IDR 重放出帧: '
                                                  f'{idr_frame.width}x{idr_frame.height}')
                                    except Exception as e:
                                        print(f'[ScrcpySession] 回退 IDR 重放异常: {e}')
                        continue  # SPS/PPS 配置包或暂未出帧
                    # 有帧输出，重置 MF 回退计数
                    _mf无帧计数 = 0
                    with self._帧锁:
                        self._当前原始帧 = frame
                    self._帧计数 += 1
                    if self._首帧时间 == 0:
                        self._首帧时间 = time.monotonic()
                    self._最近帧时间 = time.monotonic()
                    try:
                        self.帧就绪.emit()
                    except Exception:
                        pass
                    if self._帧计数 % 60 == 0:
                        elapsed = time.monotonic() - self._首帧时间
                        fps = self._帧计数 / elapsed if elapsed > 0 else 0
                        print(f'[ScrcpySession] 已解码 {self._帧计数} 帧, '
                              f'平均帧率: {fps:.1f} fps, 追帧跳过: {self._跳帧计数} 帧')

            except socket.timeout:
                continue
            except Exception as e:
                if self._运行中:
                    print(f'[ScrcpySession] 接收循环异常: {e}')
                    time.sleep(0.01)
                continue

    def _发送触摸(self, action: int, x: int, y: int, pointer_id: int = 0xffffffffffffffff):
        if not self._控制socket:
            return
        x_fixed = int(x * 65536)
        y_fixed = int(y * 65536)
        pressure = 0xFFFF if action != _ACTION_UP else 0
        msg = struct.pack(
            '>BBQiiHHHii',
            _TYPE_INJECT_TOUCH,
            action,
            pointer_id,
            x_fixed,
            y_fixed,
            self._设备宽,
            self._设备高,
            pressure,
            0, 0,
        )
        self._发送控制消息(msg)

    def _发送控制消息(self, data: bytes):
        if not self._控制socket:
            return
        try:
            self._控制socket.sendall(data)
        except Exception:
            pass

    def __enter__(self):
        self.启动()
        return self

    def __exit__(self, *args):
        self.停止()
