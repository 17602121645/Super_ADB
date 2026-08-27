"""
投屏窗口对话框
==============
基于纯 Python 投屏客户端的独立投屏窗口，替代 scrcpy.exe。

依赖: pip install av numpy PyOpenGL PyOpenGL_accelerate
"""

import os
import sys
import time
import threading

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QWidget,
    QMessageBox, QSizePolicy
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QColor, QIcon
from 项目UI.界面样式 import get_current_theme_id, THEMES
from 项目UI.弹窗样式 import add_green_glow, highlight_card_style

from 工具.投屏客户端 import 投屏客户端
from 工具.OpenGL投屏视图 import OpenGL投屏视图


class _启动工作器(QObject):
    """后台启动投屏的工作器。"""
    成功 = Signal(object)  # 投屏客户端
    失败 = Signal(str)
    进度 = Signal(str)

    def __init__(self, adb, serial, settings):
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.settings = settings

    @staticmethod
    def _解析码率(s):
        """把 '8M'/'4M'/'2000000' 等字符串转成整数 bps。"""
        s = str(s).strip().upper()
        if s.endswith('M'):
            return int(float(s[:-1]) * 1_000_000)
        if s.endswith('K'):
            return int(float(s[:-1]) * 1_000)
        try:
            return int(s)
        except ValueError:
            return 8_000_000

    def run(self):
        try:
            # load_scrcpy_settings 返回的 key: resolution/bitrate/fps/codec/render
            max_size = int(self.settings.get('resolution', 1024))
            if max_size <= 0:
                max_size = 1024  # 保护：0=不限制会导致2K分辨率卡顿
            max_fps = int(self.settings.get('fps', 60))
            if max_fps <= 0:
                max_fps = 60
            bitrate_str = str(self.settings.get('bitrate', '8M'))
            bit_rate = self._解析码率(bitrate_str)
            video_codec = self.settings.get('codec', 'h264')
            video_encoder = ''  # Python投屏暂不支持指定encoder

            # 检查是否使用自研 ADB
            用自研adb = False
            try:
                from 工具.ADB工具 import 加载json配置
                cfg = 加载json配置('配置/Super_ADB配置.json')
                adb_cfg = cfg.get('adb', {}) if isinstance(cfg, dict) else {}
                用自研adb = adb_cfg.get('self_built', False)
            except Exception:
                pass

            # ★ 启动前检查：设备端 scrcpy-server 状态 + 本地文件
            self.进度.emit('[投屏] 检查设备端 scrcpy 状态...')
            try:
                # 检查设备上是否已有 scrcpy-server
                device_check = self.adb.执行shell(
                    self.serial, 'ls -l /data/local/tmp/scrcpy-server 2>&1', timeout=5)
                if device_check and 'No such file' not in device_check:
                    self.进度.emit(f'[投屏] 设备已有 scrcpy-server: {device_check.strip()}')
                else:
                    self.进度.emit('[投屏] 设备无 scrcpy-server，将自动推送')
                # 检查设备上是否有 scrcpy 进程在运行
                proc_check = self.adb.执行shell(
                    self.serial, 'ps -A 2>/dev/null | grep -i scrcpy', timeout=5)
                if proc_check and 'scrcpy' in proc_check.lower():
                    self.进度.emit(f'[投屏] 检测到设备上已有 scrcpy 进程:\n{proc_check.strip()}')
                else:
                    self.进度.emit('[投屏] 设备上无 scrcpy 进程（正常）')
            except Exception as e:
                self.进度.emit(f'[投屏] 检查设备 scrcpy 状态失败: {e}（继续启动）')

            # 检查本地 scrcpy-server 文件
            try:
                import os
                local_server = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    '外部扩展', 'scrcpy', 'scrcpy-win64-v4.1', 'scrcpy-server')
                if os.path.isfile(local_server):
                    size = os.path.getsize(local_server)
                    self.进度.emit(f'[投屏] 本地 scrcpy-server 存在: {size/1024:.0f} KB')
                else:
                    self.进度.emit(f'[投屏] 警告: 本地 scrcpy-server 不存在: {local_server}')
            except Exception as e:
                self.进度.emit(f'[投屏] 检查本地 scrcpy-server 失败: {e}')

            if 用自研adb and ':' in self.serial:
                # 自研 ADB 模式：使用 ScrcpySession，直连设备，不调用 adb.exe
                self.进度.emit('[投屏] 使用自研 ADB 模式...')
                from 工具.自研adb import 自研adb客户端, ScrcpySession
                host = self.serial.split(':')[0]
                port = int(self.serial.split(':')[1]) if ':' in self.serial else 5555
                self_adb = 自研adb客户端(host, port)
                self_adb.连接()
                client = ScrcpySession(
                    self_adb,
                    max_size=max_size,
                    max_fps=max_fps,
                    bit_rate=bit_rate,
                    video_codec=video_codec,
                    ignore_pts=True,
                    use_reverse=True,
                )
            else:
                # 普通模式：使用投屏客户端（subprocess 调用 adb.exe）
                client = 投屏客户端(
                    self.adb, self.serial,
                    max_size=max_size,
                    max_fps=max_fps,
                    bit_rate=bit_rate,
                    video_codec=video_codec,
                    video_encoder=video_encoder,
                )

            # 拦截 print 输出作为进度
            import builtins
            原print = builtins.print
            def 拦截print(*args, **kwargs):
                msg = ' '.join(str(a) for a in args)
                if msg.startswith('[投屏]') or msg.startswith('[ScrcpySession]'):
                    self.进度.emit(msg)
                原print(*args, **kwargs)
            builtins.print = 拦截print
            try:
                client.启动()
            finally:
                builtins.print = 原print

            self.成功.emit(client)
        except ImportError as e:
            self.失败.emit(f'依赖未安装: {e}\n\n请运行: pip install av numpy opencv-python')
        except Exception as e:
            self.失败.emit(str(e))


class 投屏窗口对话框(QDialog):
    """独立投屏窗口，支持鼠标点击/滑动/键盘输入。"""

    def __init__(self, adb, serial, parent=None, settings=None):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self.settings = settings or {}
        self.client = None
        self._线程 = None
        self._工作器 = None
        self.setWindowTitle(f'投屏 - {serial}')
        self.resize(800, 600)
        self.setMinimumSize(400, 300)

        # 允许最小化/最大化
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self._theme_id = get_current_theme_id(self)
        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        self.card.setStyleSheet(highlight_card_style(self._theme_id))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(self.card)
        add_green_glow(self.card, accent=QColor(THEMES[self._theme_id]['accent']))

        self._构建界面()
        self._启动投屏()

    def _构建界面(self):
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 顶部工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self.状态标签 = QLabel('正在启动投屏…')
        self.状态标签.setStyleSheet('color: #8b949e;')
        toolbar.addWidget(self.状态标签)

        toolbar.addStretch()

        # 按钮通用样式：hover/pressed 有明显反馈
        _btn_style = (
            "QPushButton{"
            "background-color: rgba(255,255,255,8);"
            "color: #c9d1d9;"
            "border: 1px solid #30363d;"
            "border-radius: 6px;"
            "padding: 4px 8px;"
            "font: 9pt 'Microsoft YaHei UI','Segoe UI';"
            "}"
            "QPushButton:hover{"
            "background-color: #00897b;"
            "color: #ffffff;"
            "border-color: #00897b;"
            "}"
            "QPushButton:pressed{"
            "background-color: #00695c;"
            "color: #ffffff;"
            "border-color: #00695c;"
            "}"
        )

        btn_home = QPushButton('主页')
        btn_home.setFixedWidth(60)
        btn_home.setStyleSheet(_btn_style)
        btn_home.clicked.connect(self._按主页)
        toolbar.addWidget(btn_home)

        btn_back = QPushButton('返回')
        btn_back.setFixedWidth(60)
        btn_back.setStyleSheet(_btn_style)
        btn_back.clicked.connect(self._按返回)
        toolbar.addWidget(btn_back)

        btn_screenshot = QPushButton('截图')
        btn_screenshot.setFixedWidth(60)
        btn_screenshot.setStyleSheet(_btn_style)
        btn_screenshot.clicked.connect(self._截图)
        toolbar.addWidget(btn_screenshot)

        layout.addLayout(toolbar)

        # 投屏视图（OpenGL GPU渲染，零拷贝）
        self.view = OpenGL投屏视图(self)
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self.view, 1)

    def _启动投屏(self):
        """后台线程启动投屏，不阻塞UI。"""
        self._工作器 = _启动工作器(self.adb, self.serial, self.settings)
        self._线程 = threading.Thread(target=self._工作器.run, daemon=True)
        self._工作器.成功.connect(self._启动成功)
        self._工作器.失败.connect(self._启动失败)
        self._工作器.进度.connect(self._更新进度)
        self._线程.start()

    def _更新进度(self, msg):
        self.状态标签.setText(msg.replace('[投屏] ', ''))

    def _启动成功(self, client):
        self.client = client
        self.view.绑定客户端(client)
        self.状态标签.setText(f'投屏中 · {client.设备尺寸[0]}x{client.设备尺寸[1]}')

    def _启动失败(self, err_msg):
        self.状态标签.setText('启动失败')
        QMessageBox.warning(self, '投屏失败', f'启动投屏失败:\n{err_msg}')
        QTimer.singleShot(100, self.close)

    def _按主页(self):
        if self.client:
            self.client.主页()

    def _按返回(self):
        if self.client:
            self.client.返回()

    def _截图(self):
        if not self.client:
            return
        try:
            # 统一保存到 桌面\Super_ADB\ 文件夹
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            save_dir = os.path.join(desktop, 'Super_ADB')
            os.makedirs(save_dir, exist_ok=True)
            ts = time.strftime('%Y%m%d_%H%M%S')
            filename = f'screenshot_{ts}.png'
            filepath = os.path.join(save_dir, filename)
            self.client.截图保存(filepath)
            self.状态标签.setText(f'截图已保存: {filename}')
        except Exception as e:
            QMessageBox.warning(self, '截图失败', str(e))

    def keyPressEvent(self, event):
        """键盘输入转发到设备。"""
        if self.client and event.text():
            self.client.输入文本(event.text())
        super().keyPressEvent(event)

    def closeEvent(self, event):
        if self.client:
            self.client.停止()
        super().closeEvent(event)
