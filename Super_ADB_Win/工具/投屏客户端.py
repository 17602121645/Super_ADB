"""
投屏客户端
==========
基于 scrcpy 协议的纯 Python 投屏客户端，无需 scrcpy.exe，只需 scrcpy-server。

依赖: pip install av numpy
原理:
  1. 推送 scrcpy-server 到设备 /data/local/tmp/
  2. 启动 server (app_process 运行 Java 程序)
  3. adb forward 端口转发
  4. Python socket 接收 H.264 视频流, PyAV 解码
  5. 控制 socket 发送触摸/键盘/文本事件

用法:
    from 工具.投屏客户端 import 投屏客户端, 投屏视图
    client = 投屏客户端(adb, serial)
    client.启动()
    frame = client.获取帧()       # numpy 数组 (H, W, 3) BGR
    client.点击(500, 800)
    client.滑动(100, 500, 800, 500)
    client.输入文本("hello")
    client.截图保存("screen.png")
    client.停止()
"""

import os
import socket
import struct
import threading
import time
import subprocess
from typing import Optional, Tuple

import numpy as np

try:
    import av
except ImportError:
    av = None

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
        max_size: 视频最大尺寸 (宽或高)
        max_fps: 最大帧率
        bit_rate: 比特率
    """

    def __init__(self, adb, serial: str, server_path: str = None,
                 max_size: int = _DEFAULT_MAX_SIZE,
                 max_fps: int = _DEFAULT_MAX_FPS,
                 bit_rate: int = _DEFAULT_BIT_RATE,
                 video_codec: str = 'h264',
                 video_encoder: str = None,
                 server_version: str = None):
        if av is None:
            raise ImportError("需要安装 PyAV: pip install av")
        self.adb = adb
        self.serial = serial
        self.server_path = server_path or self._默认server路径()
        self.max_size = max_size
        self.max_fps = max_fps
        self.bit_rate = bit_rate
        self.video_codec = video_codec
        self.video_encoder = video_encoder
        self.server_version = server_version or self._从路径提取版本(self.server_path)
        self._raw_stream = False  # 正常模式
        self.帧信号 = _帧信号()
        self.帧就绪 = self.帧信号.帧就绪

        self._视频socket: Optional[socket.socket] = None
        self._控制socket: Optional[socket.socket] = None
        self._server进程: Optional[subprocess.Popen] = None
        self._端口 = _DEFAULT_PORT
        self._设备宽 = 0
        self._设备高 = 0
        self._设备名 = ""

        self._解码器 = None
        self._当前帧: Optional[np.ndarray] = None
        self._当前原始帧 = None  # av.VideoFrame，供OpenGL零拷贝渲染
        self._帧锁 = threading.Lock()
        self._运行中 = False
        self._接收线程: Optional[threading.Thread] = None

    # ─────────────────── 公共 API ───────────────────

    def 启动(self) -> bool:
        """启动投屏，返回是否成功。"""
        if self._运行中:
            return True
        try:
            print(f'[投屏] 推送 scrcpy-server (版本 {self.server_version})...')
            self._推送server()
            print('[投屏] 启动 server...')
            self._启动server()
            time.sleep(1.0)  # 等待 server 启动
            # 检查 server 进程是否已退出（启动失败会很快退出）
            if self._server进程.poll() is not None:
                stderr = self._server进程.stderr.read().decode('utf-8', errors='replace') if self._server进程.stderr else ''
                raise RuntimeError(f'scrcpy-server 启动失败，进程已退出。stderr:\n{stderr}')
            print('[投屏] 设置端口转发...')
            self._端口转发()
            print('[投屏] 连接视频 socket...')
            self._连接视频socket()
            # control socket 已在 _连接视频socket 中连接（raw_stream模式除外）
            if self._raw_stream:
                print('[投屏] raw_stream模式，跳过control连接')
            print('[投屏] 初始化解码器...')
            self._初始化解码器()
            self._运行中 = True
            self._接收线程 = threading.Thread(target=self._接收循环, daemon=True)
            self._接收线程.start()
            print(f'[投屏] 启动成功，设备尺寸: {self._设备宽}x{self._设备高}')
            return True
        except Exception as e:
            # 检查 server 进程状态
            if self._server进程:
                poll = self._server进程.poll()
                print(f'[投屏] server进程状态: poll={poll} (None=运行中)')
            # 读取 server 输出
            server_out = '\n'.join(self._server输出) if hasattr(self, '_server输出') else ''
            print(f'[投屏] 启动失败: {e}')
            if server_out:
                print(f'[投屏] server输出:\n{server_out}')
            self.停止()
            raise RuntimeError(f"投屏启动失败: {e}\nserver输出:\n{server_out}")

    def 停止(self):
        """停止投屏，释放资源。"""
        self._运行中 = False
        if self._接收线程:
            self._接收线程.join(timeout=2)
            self._接收线程 = None
        for sock in (self._视频socket, self._控制socket):
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
        self._视频socket = None
        self._控制socket = None
        if self._server进程:
            try:
                self._server进程.terminate()
            except Exception:
                pass
            self._server进程 = None
        try:
            self.adb.直接执行(self.serial, ['forward', '--remove', f'tcp:{self._端口}'], timeout=5)
        except Exception:
            pass
        self._解码器 = None
        self._当前帧 = None

    def 获取帧(self) -> Optional[np.ndarray]:
        """获取当前屏幕帧 (H, W, 3) BGR numpy 数组，无帧返回 None。
        延迟转换：只在调用时才从AVFrame转numpy，OpenGL渲染路径不触发。
        """
        with self._帧锁:
            if self._当前帧 is not None:
                return self._当前帧.copy()
            if self._当前原始帧 is not None:
                # 延迟转换并缓存
                self._当前帧 = self._当前原始帧.to_ndarray(format='bgr24')
                return self._当前帧.copy()
            return None

    def 获取原始帧(self):
        """获取当前原始 AVFrame（供 OpenGL 零拷贝渲染），无帧返回 None。"""
        with self._帧锁:
            return self._当前原始帧

    def 截图保存(self, 路径: str):
        """保存当前屏幕截图到文件（PNG/JPG，由扩展名决定）。"""
        frame = self.获取原始帧()
        if frame is None:
            raise RuntimeError("暂无画面")
        # 用 PyAV 原生方法保存，不依赖 cv2
        img = frame.to_image()  # PIL.Image
        img.save(路径)

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
        """发送按键事件。action: 0=DOWN, 1=UP"""
        msg = struct.pack('>BBiii', _TYPE_INJECT_KEYCODE, action, keycode, 0, 0)
        self._发送控制消息(msg)

    def 返回(self):
        """按返回键。"""
        self.按键(4, 0)  # KEYCODE_BACK DOWN
        time.sleep(0.05)
        self.按键(4, 1)  # UP

    def 主页(self):
        """按主页键。"""
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
        """从 scrcpy-server 所在目录名提取版本号，如 scrcpy-win64-v4.1 -> 4.1"""
        import re
        dirname = os.path.basename(os.path.dirname(path))
        m = re.search(r'v(\d+\.\d+(?:\.\d+)?)', dirname)
        if m:
            return m.group(1)
        return "4.1"  # 默认版本

    def _推送server(self):
        """推送 scrcpy-server 到设备（已存在则跳过）。"""
        # 检查设备上是否已有相同大小的文件
        try:
            local_size = os.path.getsize(self.server_path)
            remote_info = self.adb.执行shell(
                self.serial,
                f'ls -l {_SCRCPY_SERVER_REMOTE} 2>/dev/null',
                timeout=5
            )
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

    def _启动server(self):
        """在设备上启动 scrcpy-server。"""
        cmd = (
            f'CLASSPATH={_SCRCPY_SERVER_REMOTE} '
            f'app_process / com.genymobile.scrcpy.Server '
            f'{self.server_version} '
        )
        if self.max_size > 0:
            cmd += f'max_size={self.max_size} '
        cmd += (
            f'max_fps={self.max_fps} '
            f'video_bit_rate={self.bit_rate} '
            f'video_codec={self.video_codec} '
        )
        if self.video_encoder:
            cmd += f'video_encoder={self.video_encoder} '
        cmd += (
            f'log_level=info '
            f'audio=false '
            f'tunnel_forward=true'
        )
        print(f'[投屏] server命令: {cmd}')
        # 用 adb shell 启动，stdout/stderr 合并，后台线程读取
        self._server进程 = subprocess.Popen(
            [self.adb.adb_path, '-s', self.serial, 'shell', cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        # 启动线程读取 server 输出，避免管道阻塞
        self._server输出 = []
        def _读取输出():
            try:
                for line in self._server进程.stdout:
                    text = line.decode('utf-8', errors='replace').rstrip()
                    self._server输出.append(text)
                    print(f'[server] {text}')
            except Exception:
                pass
        threading.Thread(target=_读取输出, daemon=True).start()

    def _端口转发(self):
        """ADB 端口转发（先清除旧转发）。"""
        try:
            self.adb.直接执行(
                self.serial,
                ['forward', '--remove', f'tcp:{self._端口}'],
                timeout=5
            )
        except Exception:
            pass
        self.adb.直接执行(
            self.serial,
            ['forward', f'tcp:{self._端口}', 'localabstract:scrcpy'],
            timeout=10
        )
        print(f'[投屏] 端口转发已设置: tcp:{self._端口} -> localabstract:scrcpy')

    def _连接视频socket(self):
        """连接视频流 socket，读取dummy byte后立即连接control socket。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # 必须在connect之前设置缓冲区才生效
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)  # 4MB
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(10)
        sock.connect(('127.0.0.1', self._端口))
        print('[投屏] 视频socket已连接，等待数据...')

        if self._raw_stream:
            print('[投屏] raw_stream模式，直接接收H.264流')
            sock.settimeout(None)
            self._视频socket = sock
            return

        # 1. 读取 dummy byte
        dummy = self._recv_exact(sock, 1)
        print(f'[投屏] dummy byte: {dummy.hex()}')

        # 2. 立即连接 control socket（server等待所有连接建立后才发送元数据）
        print('[投屏] 连接控制 socket...')
        self._连接控制socket()

        # 3. 读取 64 字节设备名称
        name_buf = self._recv_exact(sock, 64)
        self._设备名 = name_buf.rstrip(b'\x00').decode('utf-8', errors='replace')
        print(f'[投屏] 设备名: {self._设备名}')

        # 4. 读取视频流头部: 4字节 codec_id + 12字节 session_meta(flags+width+height)
        codec_id_buf = self._recv_exact(sock, 4)
        codec_id = struct.unpack('>I', codec_id_buf)[0]
        print(f'[投屏] codec_id: {codec_id} (0x{codec_id:x})')
        session_meta = self._recv_exact(sock, 12)
        flags, self._设备宽, self._设备高 = struct.unpack('>III', session_meta)
        print(f'[投屏] session_meta: flags=0x{flags:x}, 尺寸: {self._设备宽}x{self._设备高}')

        sock.settimeout(None)
        self._视频socket = sock

    @staticmethod
    def _recv_exact(sock, n):
        """精确读取 n 字节。"""
        buf = b''
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("视频连接断开")
            buf += chunk
        return buf

    def _连接控制socket(self):
        """连接控制 socket。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect(('127.0.0.1', self._端口))
        sock.settimeout(None)
        self._控制socket = sock

    def _初始化解码器(self):
        """初始化 H.264 解码器（低延迟配置）。"""
        self._解码器 = av.codec.context.CodecContext.create('h264', 'r')
        # 低延迟: 帧级多线程 + 快速解码
        try:
            self._解码器.thread_type = {'FRAME'}
            self._解码器.thread_count = 2
        except Exception:
            pass

    def _接收循环(self):
        """后台线程：持续接收并解码视频帧。"""
        buffer = bytearray()
        帧计数 = 0
        接收字节数 = 0
        while self._运行中:
            try:
                chunk = self._视频socket.recv(262144)  # 256KB
                if not chunk:
                    print('[投屏] 视频流结束')
                    break
                buffer.extend(chunk)
                接收字节数 += len(chunk)

                if self._raw_stream:
                    try:
                        packets = self._解码器.parse(bytes(buffer))
                        buffer.clear()
                    except Exception:
                        if len(buffer) > 1_000_000:
                            buffer.clear()
                        continue
                else:
                    packets = []
                    while len(buffer) >= 12:
                        packet_size = struct.unpack_from('>I', buffer, 8)[0]
                        if packet_size > 10_000_000:
                            buffer.clear()
                            break
                        if len(buffer) < 12 + packet_size:
                            break
                        h264_data = bytes(buffer[12:12 + packet_size])
                        del buffer[:12 + packet_size]
                        try:
                            packets.extend(self._解码器.parse(h264_data))
                        except Exception:
                            continue

                for packet in packets:
                    try:
                        frames = self._解码器.decode(packet)
                    except Exception:
                        continue
                    for frame in frames:
                        with self._帧锁:
                            self._当前原始帧 = frame
                            self._当前帧 = None
                        帧计数 += 1
                        try:
                            self.帧就绪.emit()
                        except Exception:
                            pass
                        if 帧计数 % 60 == 0:
                            if self._设备宽 == 0:
                                self._设备宽 = frame.width
                                self._设备高 = frame.height

            except socket.timeout:
                continue
            except Exception as e:
                if self._运行中:
                    print(f'[投屏] 接收循环异常: {e}')
                    time.sleep(0.01)
                continue

    def _发送触摸(self, action: int, x: int, y: int, pointer_id: int = 0xffffffffffffffff):
        """发送触摸事件。"""
        if not self._控制socket:
            return
        # 坐标用定点数表示 (16.16)
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
            0,  # actionButton
            0,  # buttons
        )
        self._发送控制消息(msg)

    def _发送控制消息(self, data: bytes):
        """发送控制消息到设备。"""
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


# ─────────────────── PySide6 投屏视图控件 ───────────────────
try:
    from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QSizePolicy
    from PySide6.QtGui import QImage, QPixmap, QMouseEvent, QPainter
    from PySide6.QtCore import Qt, QTimer, Signal, QPoint

    class 投屏视图(QWidget):
        """可嵌入界面的投屏视图控件。

        Signals:
            帧更新: 每帧更新时发出
        """

        帧更新 = Signal()

        def __init__(self, parent=None):
            super().__init__(parent)
            self.client: Optional[投屏客户端] = None
            self._当前image: Optional[QImage] = None
            self.setMinimumSize(320, 240)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            self.setMouseTracking(True)
            self._按下位置 = None

        def 绑定客户端(self, client: 投屏客户端):
            """绑定投屏客户端。"""
            self.client = client
            if hasattr(client, '帧就绪'):
                client.帧就绪.connect(self._有新帧)

        def _有新帧(self):
            """收到新帧信号时刷新。"""
            self.update()

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            # 每次绘制时获取最新帧并转换为QImage
            if self.client:
                frame = self.client.获取帧()
                if frame is not None:
                    h, w, ch = frame.shape
                    bytes_per_line = ch * w
                    self._当前image = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_BGR888).copy()
            if self._当前image:
                pw, ph = self._当前image.width(), self._当前image.height()
                ww, wh = self.width(), self.height()
                scale = min(ww / pw, wh / ph)
                dw, dh = int(pw * scale), int(ph * scale)
                x, y = (ww - dw) // 2, (wh - dh) // 2
                painter.drawImage(x, y, self._当前image, 0, 0, pw, ph)
            else:
                painter.setPen(Qt.GlobalColor.gray)
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待投屏画面…")

        def _坐标转换(self, pos: QPoint) -> Tuple[int, int]:
            """把控件坐标转换为设备坐标。"""
            if not self.client or not self._当前image:
                return (0, 0)
            pw, ph = self._当前image.width(), self._当前image.height()
            ww, wh = self.width(), self.height()
            scale = min(ww / pw, wh / ph)
            dw, dh = int(pw * scale), int(ph * scale)
            ox, oy = (ww - dw) // 2, (wh - dh) // 2
            x = int((pos.x() - ox) / scale)
            y = int((pos.y() - oy) / scale)
            return (max(0, min(x, pw)), max(0, min(y, ph)))

        def mousePressEvent(self, event: QMouseEvent):
            if not self.client:
                return
            self._按下位置 = event.position().toPoint()
            x, y = self._坐标转换(self._按下位置)
            self.client.点击(x, y)

        def mouseMoveEvent(self, event: QMouseEvent):
            if not self.client or not self._按下位置:
                return
            # 拖动时发送 MOVE（简单实现，实际可优化为滑动）
            pass

        def mouseReleaseEvent(self, event: QMouseEvent):
            self._按下位置 = None

except ImportError:
    投屏视图 = None
