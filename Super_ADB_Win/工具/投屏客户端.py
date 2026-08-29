# -*- coding: utf-8 -*-
"""
投屏客户端（新版）
==================
基于 scrcpy 协议的纯 Python 投屏客户端，无需 scrcpy.exe，只需 scrcpy-server。

与旧版相比的关键改进（对齐自研 ADB 模式 ScrcpySession，解决 x86_64/模拟器白屏）：
  1. 启动前设备预检：自动选出可用的 app_process64 / app_process32 / app_process
     （旧版硬编码 app_process，x86_64 模拟器 32 位 loader 加载 64 位 ART 立即 SIGABRT）
  2. scid 自动生成 + 转发隧道名配套：scrcpy 4.1 要求 scid_<scid> 格式
  3. reverse 连接模式（官方默认，server 主动连 PC）与 forward 模式可选，
     优先 reverse，失败自动回退 forward（与官方 scrcpy 行为一致）
  4. 编码器启动失败（Aborted / MediaCodec IllegalArgumentException / CaptureErr）
     自动切换软编码器 c2.android.avc.encoder 完整重试一次，
     再失败则抛出「设备不支持」友好提示（旧版完全没有回退，直接白屏）
  5. server 进程退出提前检测：连接视频 socket 前先轮询进程状态，避免白等超时
     退出时附带完整 server 输出，避免只有 "timed out"

解码依赖: 内置 openh264（外部扩展/openh264/，~1MB，替代 PyAV 省 ~63MB 包体）

用法:
    from 工具.投屏客户端 import 投屏客户端
    client = 投屏客户端(adb, serial, use_reverse=None, fallback_sw_encoder=True)
    client.启动()
    frame = client.获取原始帧()
    client.停止()
"""

import os
import socket
import struct
import threading
import time
import subprocess
import uuid
from typing import Optional, Tuple

try:
    from 工具.h264解码器 import H264解码器
except ImportError:
    try:
        from h264解码器 import H264解码器  # 兼容脚本直跑
    except ImportError:
        H264解码器 = None

try:
    from 工具.mf_h264解码器 import MF_H264解码器
except ImportError:
    MF_H264解码器 = None

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    QObject = object
    Signal = None


# ─────────────────── 常量 ───────────────────
_SCRCPY_SERVER_REMOTE = "/data/local/tmp/scrcpy-server"
_DEFAULT_PORT = 27183
_DEFAULT_MAX_SIZE = 1920
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


class 投屏客户端:
    """scrcpy 投屏客户端。

    Args:
        adb: Adb设备操作 实例
        serial: 设备序列号
        server_path: 本地 scrcpy-server 文件路径
        max_size: 视频最大尺寸（最长边），0 = 不限制（不推荐，2K 以上会卡顿/触发编码器崩溃）
        max_fps: 最大帧率
        bit_rate: 比特率
        video_codec: 'h264' / 'h265' / 'av1'
        video_encoder: 指定编码器名（如 'c2.android.avc.encoder' 软编码器）；
            None 或 '' = 由设备自动选择硬编码器
        server_version: scrcpy-server 版本号（如 '4.1'），默认从路径提取
        use_reverse: 隧道模式：
            True  → 优先 reverse（官方默认，server 主动连 PC），失败回退 forward
            False → 仅 forward（PC 主动连 server localabstract 隧道）
            None  → 自动：WiFi 设备(':' in serial) 优先 reverse，USB 设备 forward
        fallback_sw_encoder: 默认编码器 Aborted / IAE 时，是否自动切
            c2.android.avc.encoder 软编码器重试（默认 True，强烈推荐开启）
    """

    def __init__(self, adb, serial: str, server_path: str = None,
                 max_size: int = _DEFAULT_MAX_SIZE,
                 max_fps: int = _DEFAULT_MAX_FPS,
                 bit_rate: int = _DEFAULT_BIT_RATE,
                 video_codec: str = 'h264',
                 video_encoder: str = None,
                 server_version: str = None,
                 use_reverse: Optional[bool] = None,
                 fallback_sw_encoder: bool = True,
                 解码器后端: str = 'auto'):
        if H264解码器 is None and MF_H264解码器 is None:
            raise ImportError("需要 openh264 或 mf_h264 动态库（外部扩展/）")
        self.adb = adb
        self.serial = serial
        self.server_path = server_path or self._默认server路径()
        # max_size <= 0 保护：不限制会导致 2K 分辨率卡顿 + 部分设备编码器直接崩溃
        self.max_size = max_size if (isinstance(max_size, int) and max_size > 0) else _DEFAULT_MAX_SIZE
        self.max_fps = max_fps if (isinstance(max_fps, int) and max_fps > 0) else _DEFAULT_MAX_FPS
        self.bit_rate = bit_rate
        self.video_codec = video_codec
        # 用户显式指定的 encoder（'' 和 None 都视为"未指定，走自动"）
        self.video_encoder = video_encoder if video_encoder else None
        self.server_version = server_version or self._从路径提取版本(self.server_path)
        # 隧道模式决策
        if use_reverse is None:
            use_reverse = ':' in serial  # WiFi 设备优先 reverse（官方默认行为）
        self.use_reverse = bool(use_reverse)
        # 默认编码器失败后是否自动切软编码器重试
        self.fallback_sw_encoder = bool(fallback_sw_encoder)
        # 解码器后端: 'auto'=优先MF硬件解码,失败回退openh264; 'mf'=仅MF; 'openh264'=仅软解
        self.解码器后端 = 解码器后端
        self._实际解码器后端 = None  # 实际使用的后端（auto模式下可能回退）

        # 每次完整启动尝试刷新，避免旧 server 残留占住同名隧道
        self.scid = ''
        self._隧道名 = 'scrcpy'

        self.帧信号 = _帧信号()
        self.帧就绪 = self.帧信号.帧就绪

        self._视频socket: Optional[socket.socket] = None
        self._控制socket: Optional[socket.socket] = None
        self._监听socket: Optional[socket.socket] = None  # reverse 模式 PC 端监听
        self._server进程: Optional[subprocess.Popen] = None
        self._端口 = _DEFAULT_PORT
        self._设备宽 = 0
        self._设备高 = 0
        self._设备名 = ""

        self._解码器 = None
        self._当前原始帧 = None
        self._帧锁 = threading.Lock()
        # MF 硬解回退相关
        self._spspps缓存 = b''
        self._idr缓存 = b''
        self._mf无帧计数 = 0
        self._mf回退阈值 = 30
        self._已回退 = False
        # 背压标志：True 表示已 emit 但 GUI 还没取帧，期间不再重复 emit，
        # 避免帧就绪信号在 Qt 事件队列里无上限堆积（延迟累积的主因）
        self._帧待取 = False
        # 因消费跟不上而丢弃的帧数（仅统计，用于排查）
        self._丢帧计数 = 0
        self._运行中 = False
        self._接收线程: Optional[threading.Thread] = None
        # server 输出（启动失败时附在异常里，避免只有超时）
        self._server输出: list = []
        # 预检选出的 app_process 二进制（64 位优先）
        self._app_bin = 'app_process'

    # ─────────────────── 公共 API ───────────────────

    def 启动(self) -> bool:
        """启动投屏，失败自动按以下顺序兜底，全部失败抛出带原因的 RuntimeError。

        兜底顺序:
          1) 设备预检（API>=21、app_process 选择）
          2) 推送 scrcpy-server
          3) 首选 use_reverse 模式：
             - reverse=True → 先 reverse，失败回退 forward
             - reverse=False → 直接 forward
             默认编码器（若用户未显式指定 video_encoder）启动
          4) 若启动阶段检测到编码器 Aborted / IAE / CaptureErr，
             且 fallback_sw_encoder=True → 切 c2.android.avc.encoder 完整重试一次
             （保留同一隧道模式回退链）
          5) 仍失败 → 若为编码器类问题，抛出【设备不支持】友好提示；其余原样附 server 输出
        """
        if self._运行中:
            return True

        # 记录用户最初的 encoder 选择：若已显式指定，回退时不强制覆盖
        _首尝试编码器 = self.video_encoder  # None 表示"用户未指定，设备自动"
        _已经过软编码器重试 = False

        def _等server输出落地():
            if self._server输出:
                return
            deadline = time.time() + 2.0
            while time.time() < deadline and not self._server输出:
                time.sleep(0.05)

        def _刷新scid():
            """每次启动尝试换新 scid，避免前一次 abort 的 server 残留占住同名隧道。"""
            self.scid = f'{uuid.uuid4().int & 0x7FFFFFFF:08x}'
            self._隧道名 = f'scrcpy_{self.scid}'

        def _跑一次启动(reverse_mode: bool):
            """一次完整的 server 启动 + 隧道建立 + 握手 + 解码器初始化。
            失败抛异常，让外层走回退链。"""
            _刷新scid()
            self._设备宽 = 0
            self._设备高 = 0
            if reverse_mode:
                try:
                    self._尝试启动(reverse=True)
                except (socket.timeout, ConnectionRefusedError, OSError, RuntimeError) as e:
                    print(f'[投屏] reverse 失败，回退 forward: {e}')
                    self._清理尝试()
                    self._尝试启动(reverse=False)
            else:
                self._尝试启动(reverse=False)

        # ── 启动序列 ──
        try:
            print(f'[投屏] 预检设备 & app_process ...')
            self._设备预检()
            print(f'[投屏] 推送 scrcpy-server (版本 {self.server_version}) ...')
            self._推送server()

            # 第 1 轮：按默认编码器 + 用户指定隧道模式
            try:
                _跑一次启动(reverse_mode=self.use_reverse)
            except Exception:
                _等server输出落地()
                # 编码器故障？→ 走软编码器兜底重试
                if (_首尝试编码器 is None  # 用户未手动指定 encoder
                        and not _已经过软编码器重试
                        and self.fallback_sw_encoder
                        and self._是编码器故障()):
                    print('[投屏] 默认硬编码器 Aborted/IAE，自动切软编码器 '
                          'c2.android.avc.encoder 重试一次 ...')
                    self._清理尝试()
                    self.video_encoder = 'c2.android.avc.encoder'
                    _已经过软编码器重试 = True
                    try:
                        _跑一次启动(reverse_mode=self.use_reverse)
                    except Exception:
                        _等server输出落地()
                        is_support_err = self._是编码器故障() \
                            or ('Aborted' in '\n'.join(self._server输出))
                        if is_support_err:
                            tail = '\n'.join(self._server输出[-16:])
                            msg = ('【设备不支持】scrcpy 投屏：设备端没有可用的视频编码器'
                                   '（OMX硬编码器 + Google软编码器 均 Aborted/'
                                   'IllegalArgumentException 或架构不匹配）。'
                                   '\n建议：1) 投屏设置中切「自动编码器」/ 软编码器再试；'
                                   '\n2) 确认设备 Android 版本 5.0+；'
                                   '\n3) 改用 外部扩展/scrcpy/ 下官方 scrcpy 客户端投屏。')
                            if tail:
                                msg = f'{msg}\n—— 调试信息（可忽略） ——\nserver输出尾部:\n{tail}'
                            self.停止()
                            raise RuntimeError(msg)
                        raise  # 非编码器类错误，原样上抛
                else:
                    # 两类不走软编码器兜底的场景：
                    #   a) 用户已显式手动指定 video_encoder（非 None）
                    #   b) 错误非编码器类（纯网络/超时/隧道）
                    # 对 a) 且是编码器故障 → 也贴【设备不支持】标签
                    _等server输出落地()
                    _tail_all = '\n'.join(self._server输出)
                    if _首尝试编码器 and (self._是编码器故障()
                                           or 'Aborted' in _tail_all):
                        msg = ('【设备不支持】scrcpy 投屏：指定的视频编码器 '
                               f'`{_首尝试编码器}` 在 server 端 Aborted/'
                               'IllegalArgumentException，设备不支持该编码器；'
                               '建议在投屏设置里切「自动选择」或换软编码器。')
                        if _tail_all:
                            msg = (f'{msg}\n—— 调试信息 ——\n'
                                   f'server输出:\n{_tail_all[-800:]}')
                        self.停止()
                        raise RuntimeError(msg)
                    raise  # 正常上抛

            # 启动成功，开接收线程
            self._运行中 = True
            self._接收线程 = threading.Thread(target=self._接收循环, daemon=True)
            self._接收线程.start()
            print(f'[投屏] 启动成功，设备尺寸: {self._设备宽}x{self._设备高}')
            return True

        except RuntimeError as e:
            # 【设备不支持】类异常已经有完整提示，原样上抛
            msg = str(e)
            print(f'[投屏] 启动失败: {msg[:160]}')
            if '【设备不支持】' in msg:
                raise
            self.停止()
            tail = '\n'.join(self._server输出[-8:])
            if tail:
                raise RuntimeError(f'{e}\nserver输出:\n{tail}') from e
            raise
        except Exception as e:
            print(f'[投屏] 启动失败: {type(e).__name__}: {e}')
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
        # 两种转发都清一遍（回退场景会残留）
        try:
            self.adb.直接执行(self.serial,
                              ['reverse', '--remove', f'localabstract:{self._隧道名}'],
                              timeout=5)
        except Exception:
            pass
        try:
            self.adb.直接执行(self.serial,
                              ['forward', '--remove', f'tcp:{self._端口}'],
                              timeout=5)
        except Exception:
            pass
        if self._server进程 is not None:
            try:
                self._server进程.terminate()
            except Exception:
                pass
            try:
                self._server进程.wait(timeout=3)
            except Exception:
                try:
                    self._server进程.kill()
                except Exception:
                    pass
            self._server进程 = None
        if self._解码器 is not None:
            try:
                self._解码器.关闭()
            except Exception:
                pass
        self._解码器 = None
        self._当前原始帧 = None

    def 获取帧(self):
        with self._帧锁:
            self._帧待取 = False
            return self._当前原始帧

    def 获取原始帧(self):
        with self._帧锁:
            self._帧待取 = False
            return self._当前原始帧

    @property
    def 丢帧计数(self) -> int:
        """因 GUI 渲染跟不上而被覆盖的帧数（排查用）。"""
        return self._丢帧计数

    def 截图保存(self, 路径: str):
        frame = self.获取原始帧()
        if frame is None:
            raise RuntimeError("暂无画面")
        img = frame.to_image()
        img.save(路径)

    def 点击(self, x: int, y: int):
        self._发送触摸(_ACTION_DOWN, x, y)
        time.sleep(0.05)
        self._发送触摸(_ACTION_UP, x, y)

    def 滑动(self, x1: int, y1: int, x2: int, y2: int, 时长: float = 0.3):
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
        if not text:
            return
        data = text.encode('utf-8')
        msg = struct.pack('>BI', _TYPE_INJECT_TEXT, len(data)) + data
        self._发送控制消息(msg)

    def 按键(self, keycode: int, action: int = 1):
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

    @property
    def 设备尺寸(self) -> Tuple[int, int]:
        return (self._设备宽, self._设备高)

    @property
    def 运行中(self) -> bool:
        return self._运行中

    # ─────────────────── 内部实现 ───────────────────

    def _默认server路径(self) -> str:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(base, '外部扩展', 'scrcpy', 'scrcpy-win64-v4.1', 'scrcpy-server'),
            os.path.join(base, '外部扩展', 'scrcpy', 'scrcpy-server'),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p
        raise FileNotFoundError("未找到 scrcpy-server，请指定 server_path")

    @staticmethod
    def _从路径提取版本(path: str) -> str:
        import re
        dirname = os.path.basename(os.path.dirname(path))
        m = re.search(r'v(\d+\.\d+(?:\.\d+)?)', dirname)
        if m:
            return m.group(1)
        return "4.1"

    def _设备预检(self):
        """启动前检查：Android API >= 21 + 选出可用的 app_process 二进制。

        优先级：app_process64 → app_process（符号链接到 64 时OK）→ app_process32
        旧版硬编码 app_process，在 x86_64 模拟器上若 app_process 不存在或链接到 32 位 loader，
        ART 加载 64 位 jar 会直接 SIGABRT → 表现为「shell 输出只有 Aborted」。
        """
        # 1. Android SDK
        try:
            sdk_out = (self.adb.执行shell(self.serial,
                        'getprop ro.build.version.sdk', timeout=5) or '').strip()
            sdk = int(sdk_out.split()[0]) if sdk_out else 0
        except Exception:
            sdk = 0
        if 0 < sdk < 21:
            raise RuntimeError(f"设备 Android 版本过低 (API {sdk} < 21)，不支持 scrcpy 投屏")
        # 2. app_process 存在性 & 挑 64 位优先
        out = (self.adb.执行shell(self.serial,
                 'ls -l /system/bin/app_process /system/bin/app_process64 '
                 '/system/bin/app_process32 2>&1', timeout=5) or '')
        found = []
        for ln in out.splitlines():
            ln = ln.strip()
            if not ln or 'No such file' in ln or ln.startswith('ls:'):
                continue
            # 取每行最后一个字段作为真实路径
            parts = ln.split()
            path = parts[-1] if parts else ''
            for cand in ('app_process64', 'app_process32', 'app_process'):
                if path.endswith(cand):
                    found.append(cand)
                    break
        chosen = None
        for prefer in ('app_process64', 'app_process', 'app_process32'):
            if prefer in found:
                chosen = prefer
                break
        if not chosen:
            raise RuntimeError("设备缺少 app_process，无法启动 scrcpy 投屏（非标准 Android 系统）")
        self._app_bin = chosen
        print(f'[投屏] 预检通过: API={sdk or "未知"}, app_bin={self._app_bin}')

    def _推送server(self):
        try:
            local_size = os.path.getsize(self.server_path)
            remote_info = self.adb.执行shell(
                self.serial, f'ls -l {_SCRCPY_SERVER_REMOTE} 2>/dev/null', timeout=5)
            if remote_info and str(local_size) in remote_info:
                print(f'[投屏] server已存在，跳过推送 ({local_size} bytes)')
                return
        except Exception:
            pass
        print(f'[投屏] 推送 server ({os.path.getsize(self.server_path)} bytes)...')
        self.adb.直接执行(
            self.serial,
            ['push', self.server_path, _SCRCPY_SERVER_REMOTE],
            timeout=120
        )

    def _组装server命令(self, tunnel_forward: bool) -> str:
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
        if tunnel_forward:
            cmd += 'tunnel_forward=true'
        return cmd

    def _启动server(self, tunnel_forward: bool):
        """用 adb shell 启动 scrcpy-server，后台线程持续读取 stdout/stderr 合并流。"""
        cmd = self._组装server命令(tunnel_forward=tunnel_forward)
        print(f'[投屏] server命令: {cmd}')
        print(f'[投屏] 隧道名: localabstract:{self._隧道名}')

        self._server输出 = []
        self._server进程 = subprocess.Popen(
            [self.adb.adb_path, '-s', self.serial, 'shell', cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        def _净化(text: str) -> str:
            text = text.replace('\r\n', '\n').replace('\r', '\n')
            return ''.join(
                ch for ch in text
                if ch in '\n\t' or (ch.isprintable() and ch != '\ufffd')
            )

        def _读取输出():
            try:
                for raw in self._server进程.stdout:
                    text = _净化(raw.decode('utf-8', errors='replace').rstrip())
                    if text:
                        self._server输出.append(text)
                        print(f'[server] {text}')
            except Exception:
                pass

        threading.Thread(target=_读取输出, daemon=True).start()

    # ── 回退：编码器故障检测（与 ScrcpySession 同规则） ──
    def _是编码器故障(self) -> bool:
        tail = '\n'.join(self._server输出)
        if ('Capture/encoding error' in tail
                or 'Applying video encoder constraints' in tail
                or ('MediaCodec' in tail and 'IllegalArgumentException' in tail)):
            return True
        # native abort：shell 输出只有 'Aborted'，且设备宽=0（握手没完成 → 启动早期）
        if ('Aborted' in tail or 'SIGABRT' in tail) and self._设备宽 == 0:
            return True
        return False

    def _尝试启动(self, reverse: bool):
        print(f'[投屏] 启动 server (tunnel={"reverse" if not reverse else "forward"})...')
        self._启动server(tunnel_forward=not reverse)
        # 等 server 起来，同时检查是否秒退（Aborted 会很快）
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if self._server进程.poll() is not None:
                # 进程已退出，收集输出并抛错
                time.sleep(0.2)  # 让输出线程读完最后一行
                msg = ' '.join(self._server输出[-8:])[-400:]
                raise RuntimeError(f'server 启动即退出: {msg or "(无输出)"}')
            time.sleep(0.1)
        if reverse:
            print('[投屏] 设置 reverse 隧道（server 主动连 PC）...')
            self._设置reverse()
        else:
            print('[投屏] forward 模式：PC 经端口转发连 localabstract 隧道...')
        print('[投屏] 连接视频 socket...')
        self._连接视频socket(reverse=reverse)
        print('[投屏] 初始化解码器...')
        self._初始化解码器()

    def _清理尝试(self):
        for sock in (self._视频socket, self._控制socket, self._监听socket):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._视频socket = None
        self._控制socket = None
        self._监听socket = None
        try:
            self.adb.直接执行(self.serial,
                              ['reverse', '--remove', f'localabstract:{self._隧道名}'],
                              timeout=5)
        except Exception:
            pass
        try:
            self.adb.直接执行(self.serial,
                              ['forward', '--remove', f'tcp:{self._端口}'],
                              timeout=5)
        except Exception:
            pass
        if self._server进程 is not None:
            try:
                self._server进程.terminate()
            except Exception:
                pass
            try:
                self._server进程.wait(timeout=3)
            except Exception:
                try:
                    self._server进程.kill()
                except Exception:
                    pass
            self._server进程 = None
        self._解码器 = None
        time.sleep(0.5)  # 等 server 进程完全释放，端口/隧道不冲突

    def _设置reverse(self):
        """官方默认模式：PC 监听本地端口 → adb reverse 设备 localabstract 指向它。"""
        self._监听socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._监听socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._监听socket.bind(('127.0.0.1', self._端口))
        self._监听socket.listen(2)  # 视频 + 控制
        self._监听socket.settimeout(10)
        print(f'[投屏] PC 监听端口: {self._端口}')
        # 清理旧 reverse 再设新的
        try:
            self.adb.直接执行(self.serial,
                              ['reverse', '--remove', f'localabstract:{self._隧道名}'],
                              timeout=5)
        except Exception:
            pass
        r = self.adb.直接执行(self.serial,
                                ['reverse', f'localabstract:{self._隧道名}',
                                 f'tcp:{self._端口}'],
                                timeout=10, capture=True)
        # reverse 命令成功一般无输出；失败会打印错误
        r_str = (r or '').strip()
        if r_str and any(k in r_str.lower() for k in ['error', 'not found', 'cannot']):
            raise RuntimeError(f'reverse 隧道设置失败: {r_str}')
        print(f'[投屏] reverse OK: 设备 localabstract:{self._隧道名} -> PC tcp:{self._端口}')

    def _连接视频socket(self, reverse: bool):
        if reverse and self._监听socket:
            print('[投屏] 等待设备接入 (reverse)...')
            # server 秒退检测：等连接时同步检查进程是否还在
            deadline = time.time() + 10
            sock = None
            while time.time() < deadline:
                if self._server进程 and self._server进程.poll() is not None:
                    msg = ' '.join(self._server输出[-8:])[-400:]
                    raise RuntimeError(f'server 已退出，视频连接失败: {msg or "(无输出)"}')
                self._监听socket.settimeout(1.0)
                try:
                    sock, _addr = self._监听socket.accept()
                    print(f'[投屏] 设备接入: {_addr}')
                    break
                except socket.timeout:
                    continue
            if sock is None:
                raise socket.timeout('等待设备接入 reverse 超时 10s')
        else:
            # forward：先设端口转发
            try:
                self.adb.直接执行(self.serial,
                                  ['forward', '--remove', f'tcp:{self._端口}'],
                                  timeout=5)
            except Exception:
                pass
            self.adb.直接执行(self.serial,
                              ['forward', f'tcp:{self._端口}',
                               f'localabstract:{self._隧道名}'],
                              timeout=10)
            print(f'[投屏] forward OK: tcp:{self._端口} -> localabstract:{self._隧道名}')
            sock = None
            deadline = time.time() + 20
            while sock is None:
                if self._server进程 and self._server进程.poll() is not None:
                    msg = ' '.join(self._server输出[-8:])[-400:]
                    raise RuntimeError(f'server 已退出 (未完成握手): {msg or "(无输出)"}')
                try:
                    s = socket.socket()
                    s.settimeout(1.0)
                    s.connect(('127.0.0.1', self._端口))
                    sock = s
                except Exception:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.5)
            print('[投屏] 视频socket已连接，等待数据...')

        # 1) 读 dummy byte
        dummy = self._精确接收(sock, 1)
        print(f'[投屏] dummy byte: {dummy.hex()}')

        # 2) 立即连接 control socket（server 等两条连接建好才发送元数据）
        print('[投屏] 连接控制 socket...')
        self._连接控制socket(reverse=reverse)

        # 3) 64 字节设备名
        name_buf = self._精确接收(sock, 64)
        self._设备名 = name_buf.rstrip(b'\x00').decode('utf-8', errors='replace')
        print(f'[投屏] 设备名: {self._设备名}')

        # 4) 4 字节 codec_id + 12 字节 session_meta(flags+width+height)
        codec_id_buf = self._精确接收(sock, 4)
        codec_id = struct.unpack('>I', codec_id_buf)[0]
        session_meta = self._精确接收(sock, 12)
        flags, self._设备宽, self._设备高 = struct.unpack('>III', session_meta)
        print(f'[投屏] codec_id=0x{codec_id:x}, flags=0x{flags:x}, '
              f'尺寸: {self._设备宽}x{self._设备高}')

        sock.settimeout(None)
        self._视频socket = sock

    @staticmethod
    def _精确接收(sock, n):
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("视频连接断开（EOF）")
            buf += chunk
        return buf

    def _连接控制socket(self, reverse: bool):
        if reverse and self._监听socket:
            deadline = time.time() + 10
            sock = None
            while time.time() < deadline:
                if self._server进程 and self._server进程.poll() is not None:
                    break
                self._监听socket.settimeout(1.0)
                try:
                    sock, _ = self._监听socket.accept()
                    break
                except socket.timeout:
                    continue
            if sock is None:
                raise socket.timeout('等待设备 control 连接超时')
            print(f'[投屏] 控制socket已接入')
        else:
            sock = None
            deadline = time.time() + 10
            while sock is None:
                try:
                    s = socket.socket()
                    s.settimeout(3)
                    s.connect(('127.0.0.1', self._端口))
                    sock = s
                except Exception:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.3)
        self._控制socket = sock

    def _初始化解码器(self):
        """根据解码器后端设置初始化 H264 解码器。"""
        backend = self.解码器后端
        # auto 模式：优先 MF，失败回退 openh264
        if backend == 'auto':
            if MF_H264解码器 is not None:
                try:
                    self._解码器 = MF_H264解码器()
                    self._实际解码器后端 = 'mf'
                    print('[投屏] 解码器: MF 硬件解码 (auto)')
                    return
                except Exception as e:
                    print(f'[投屏] MF 解码器初始化失败，回退 openh264: {e}')
            self._解码器 = H264解码器()
            self._实际解码器后端 = 'openh264'
            print('[投屏] 解码器: openh264 软解 (auto)')
        elif backend == 'mf':
            if MF_H264解码器 is None:
                raise ImportError("MF H264 解码器不可用（外部扩展/mf_h264/）")
            self._解码器 = MF_H264解码器()
            self._实际解码器后端 = 'mf'
            print('[投屏] 解码器: MF 硬件解码 (手动)')
        else:  # openh264
            self._解码器 = H264解码器()
            self._实际解码器后端 = 'openh264'
            print('[投屏] 解码器: openh264 软解 (手动)')

    def _接收循环(self):
        buffer = bytearray()
        帧计数 = 0
        while self._运行中:
            try:
                chunk = self._视频socket.recv(262144)  # 256KB
                if not chunk:
                    print('[投屏] 视频流结束（对端关闭）')
                    break
                buffer.extend(chunk)

                frames = []
                while len(buffer) >= 12:
                    # 12 字节帧头: 8 字节 pts_flags + 4 字节 packet_size
                    pts_flags = struct.unpack_from('>Q', buffer, 0)[0]
                    if pts_flags >> 63:
                        # 会话元数据：分辨率变更包
                        self._设备宽 = pts_flags & 0xFFFFFFFF
                        self._设备高 = struct.unpack_from('>I', buffer, 8)[0]
                        print(f'[投屏] 会话元数据: 尺寸变化为 '
                              f'{self._设备宽}x{self._设备高}')
                        del buffer[:12]
                        continue
                    packet_size = struct.unpack_from('>I', buffer, 8)[0]
                    if packet_size > 10_000_000:
                        print(f'[投屏] 包大小异常 {packet_size}，丢弃缓冲等待 IDR 恢复')
                        buffer.clear()
                        break
                    if len(buffer) < 12 + packet_size:
                        break
                    h264_data = bytes(buffer[12:12 + packet_size])
                    del buffer[:12 + packet_size]
                    # 缓存 SPS/PPS 和 IDR，供 MF 回退后重放
                    if h264_data[:4] == b'\x00\x00\x00\x01':
                        nal_type = h264_data[4] & 0x1F
                    elif len(h264_data) > 3 and h264_data[:3] == b'\x00\x00\x01':
                        nal_type = h264_data[3] & 0x1F
                    else:
                        nal_type = 0
                    if nal_type in (7, 8):  # SPS / PPS
                        self._spspps缓存 += h264_data
                    elif nal_type == 5:  # IDR
                        self._idr缓存 = h264_data
                    try:
                        frame = self._解码器.解码(h264_data)
                    except Exception:
                        continue
                    if frame is None:
                        # MF 硬解回退检测：连续无帧输出达到阈值时自动切 openh264
                        if (not self._已回退 and self._实际解码器后端 == 'mf'
                                and H264解码器 is not None):
                            self._mf无帧计数 += 1
                            if self._mf无帧计数 >= self._mf回退阈值:
                                print(f'[投屏] MF 硬解 {self._mf无帧计数} 包无输出，'
                                      f'自动回退 openh264 软解')
                                try:
                                    self._解码器.关闭()
                                except Exception:
                                    pass
                                self._解码器 = H264解码器()
                                self._实际解码器后端 = 'openh264'
                                self._已回退 = True
                                self._mf无帧计数 = 0
                                # 重放缓存的 SPS/PPS 和 IDR
                                if self._spspps缓存:
                                    try:
                                        self._解码器.解码(self._spspps缓存, 仅参考=True)
                                    except Exception:
                                        pass
                                if self._idr缓存:
                                    try:
                                        idr_frame = self._解码器.解码(self._idr缓存)
                                        if idr_frame:
                                            frames.append(idr_frame)
                                            print(f'[投屏] 回退后 IDR 重放出帧: '
                                                  f'{idr_frame.width}x{idr_frame.height}')
                                    except Exception as e:
                                        print(f'[投屏] 回退 IDR 重放异常: {e}')
                        continue  # SPS/PPS 配置包或暂未出帧
                    # 有帧输出，重置 MF 回退计数
                    self._mf无帧计数 = 0
                    if frame is not None:
                        frames.append(frame)

                for frame in frames:
                    with self._帧锁:
                        if self._当前原始帧 is not None and self._帧待取:
                            # 上一帧 GUI 还没取走就被覆盖，说明渲染跟不上
                            self._丢帧计数 += 1
                        self._当前原始帧 = frame
                    帧计数 += 1
                    if 帧计数 % 60 == 0 and self._设备宽 == 0:
                        self._设备宽 = frame.width
                        self._设备高 = frame.height

                # 一批数据只发一次信号，且 GUI 未取帧期间不重复发，
                # 防止 emit 速度超过 GUI 消费速度导致事件队列无上限堆积
                if frames:
                    需要通知 = False
                    with self._帧锁:
                        if not self._帧待取:
                            self._帧待取 = True
                            需要通知 = True
                    if 需要通知:
                        try:
                            self.帧就绪.emit()
                        except Exception:
                            with self._帧锁:
                                self._帧待取 = False

            except socket.timeout:
                continue
            except Exception as e:
                if self._运行中:
                    print(f'[投屏] 接收循环异常: {e}')
                    time.sleep(0.01)
                continue

    def _发送触摸(self, action: int, x: int, y: int,
                  pointer_id: int = 0xffffffffffffffff):
        if not self._控制socket:
            return
        x_fixed = int(x * 65536)
        y_fixed = int(y * 65536)
        pressure = 0xFFFF if action != _ACTION_UP else 0
        msg = struct.pack(
            '>BBQiiHHHii',
            _TYPE_INJECT_TOUCH, action, pointer_id,
            x_fixed, y_fixed, self._设备宽, self._设备高, pressure,
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
