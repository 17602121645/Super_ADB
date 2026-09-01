# -*- coding: utf-8 -*-
"""
Super_ADB 环境配置弹窗
======================
展示当前 ADB 版本 / 路径信息；在 Windows 下额外展示本工具内置的 ADB 路径，
并提供「添加到 PATH」按钮一键写入用户级环境变量。

设计要点：
- 沿用主项目深色主题：所有颜色由 ``界面样式.THEMES[tid]`` 派生，支持运行时切换
- ADB 探测：优先 ``shutil.which('adb')``（PATH 已配置），否则 ``adb version`` 试跑
- PATH 写入：直接走 ``winreg`` 操作 ``HKCU\\Environment``（无需管理员权限），
  写入后通过 ``ctypes`` 广播 ``WM_SETTINGCHANGE`` 让新启动的进程立即生效
- 内置 ADB 路径探测：与 ``ADB工具.查找scrcpy目录`` 同样的「base / parent / cwd」三级回退，
  覆盖源码模式 ``Super_ADB_Win/vendor/...`` 与冻结模式 ``_internal/vendor/...`` 两种布局
"""

import os
import sys
import shutil
import subprocess
from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QSizePolicy, QGraphicsDropShadowEffect, QFrame,
    QPlainTextEdit, QCheckBox,
)

from ui.ui_styles import FONT_FAMILY, THEMES, DEFAULT_THEME, _parse_rgb
from ui.dialog_styles import add_green_glow
from tools.adb_tools import 加载json配置, 保存json配置

# 配置文件名
CONFIG_NAME = 'config/super_adb_config.json'
ADB_CONFIG_KEY = 'adb'  # 配置文件中 ADB 相关配置的 key


def detect_current_adb():
    """探测当前 PATH 中的 adb，返回 (version_str, abs_path) 或 (None, None)。"""
    # 1) shutil.which 拿到绝对路径
    adb_path = shutil.which('adb')
    if not adb_path:
        return None, None
    # 2) adb version 拿首行版本字符串
    try:
        r = subprocess.run(
            [adb_path, 'version'],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if r.returncode == 0:
            first_line = (r.stdout or '').strip().splitlines()
            ver = first_line[0] if first_line else '未知版本'
        else:
            ver = '执行失败'
    except Exception as e:
        ver = f'执行异常: {e}'
    return ver, os.path.abspath(adb_path)


def detect_socket_adb():
    """检测 Socket 直连 ADB server，返回 (version_str, '127.0.0.1:5037') 或 (None, None)。

    若 server 未运行，自动用内置 adb 启动一次（adb start-server），
    启动成功后继续检测；启动失败才返回 None。
    """
    try:
        from tools.adb_protocol_client import Adb协议客户端, 检查server运行, 启动adb服务器
        if not 检查server运行():
            启动adb服务器()
            if not 检查server运行():
                return None, None
        client = Adb协议客户端(自动启动server=False)
        ver = client.获取版本()
        return f'Socket直连 · ADB 版本 0x{ver:x} (协议)', '127.0.0.1:5037 (Socket直连)'
    except Exception:
        return None, None


def 读取socket直连设置() -> bool:
    """读取是否启用 Socket 直连。"""
    cfg = 加载json配置(CONFIG_NAME)
    adb_cfg = cfg.get(ADB_CONFIG_KEY, {})
    return adb_cfg.get('socket_direct', False)


def 读取自研adb设置() -> bool:
    """读取是否启用自研 adb。"""
    cfg = 加载json配置(CONFIG_NAME)
    adb_cfg = cfg.get(ADB_CONFIG_KEY, {})
    return adb_cfg.get('self_built', False)


def 读取系统adb设置() -> bool:
    """读取是否使用系统环境变量的 adb。"""
    cfg = 加载json配置(CONFIG_NAME)
    adb_cfg = cfg.get(ADB_CONFIG_KEY, {})
    return adb_cfg.get('system_adb', False)


def 保存adb设置(socket_direct: bool, self_built: bool, system_adb: bool):
    """保存 ADB 配置到 JSON 文件。三个选项互斥。

    优先级: system_adb > socket_direct > self_built
    同时勾选多个时，只保留优先级最高的那个。
    """
    cfg = 加载json配置(CONFIG_NAME)
    if not isinstance(cfg, dict):
        cfg = {}
    # 互斥：三个选项只能选一个
    if system_adb:
        socket_direct = False
        self_built = False
    elif socket_direct:
        self_built = False
        system_adb = False
    cfg[ADB_CONFIG_KEY] = {
        'socket_direct': socket_direct,
        'self_built': self_built,
        'system_adb': system_adb,
    }
    保存json配置(CONFIG_NAME, cfg)


class 环境配置对话框(QDialog):
    """带自定义标题栏的圆角环境配置弹窗，跟随主窗口主题。"""

    # ADB 设置（socket_direct / self_built）变更时发射，主窗口收到后热更新 adb 实例
    设置变更 = Signal()
    # 后台探测完成 → 主线程更新 UI（后台线程无事件循环，
    # 不能靠 QTimer.singleShot 回主线程，必须用信号）
    _probe_done = Signal(object)
    # 后台切换（kill/start server）完成 → 主线程
    _switch_done = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(760, 560)
        self.setWindowTitle('环境配置')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))

        # ── 容器（圆角卡片）───────────────────────────────────────
        self.card = QWidget(self)
        self.card.setObjectName('envCard')
        self.card.setGeometry(10, 10, 740, 540)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 自定义标题栏 ──────────────────────────────────────────
        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(12, 8, 8, 8)
        title_bar.setSpacing(6)
        self.title_lbl = QLabel('环境配置')
        self.title_lbl.setObjectName('envTitle')
        self.title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title_bar.addWidget(self.title_lbl)
        close_btn = QPushButton('✕')
        close_btn.setObjectName('closeBtn')
        close_btn.setFixedSize(28, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.accept)
        title_bar.addWidget(close_btn)
        title_widget = QWidget()
        title_widget.setLayout(title_bar)
        layout.addWidget(title_widget)

        # ── 内容区 ────────────────────────────────────────────────
        content = QVBoxLayout()
        content.setContentsMargins(22, 12, 22, 18)
        content.setSpacing(12)

        # Section 1: 当前 ADB 环境（标题 + 重新检测 同一行）
        sec1_lbl = QLabel('当前 ADB 环境')
        sec1_lbl.setObjectName('secTitle')
        self.refresh_btn = QPushButton('重新检测')
        self.refresh_btn.setObjectName('refreshBtn')
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._refresh_adb_info)
        sec1_row = QHBoxLayout()
        sec1_row.setSpacing(8)
        sec1_row.setContentsMargins(0, 0, 0, 0)
        sec1_row.addWidget(sec1_lbl)
        sec1_row.addStretch()
        sec1_row.addWidget(self.refresh_btn)
        content.addLayout(sec1_row)

        # 状态行（直接 addLayout，避免被外层 QVBoxLayout 当成可压缩成员压成 0 高度）
        self.status_row = QHBoxLayout()
        self.status_row.setSpacing(8)
        self.status_row.setContentsMargins(0, 0, 0, 0)
        self.status_icon = QLabel('●')
        self.status_icon.setObjectName('statusIcon')
        self.status_icon.setFixedWidth(18)
        self.status_lbl = QLabel('检测中…')
        self.status_lbl.setObjectName('statusLbl')
        # 关键：水平方向 Expanding，让状态文字占满整行不被裁剪
        self.status_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.status_lbl.setWordWrap(True)
        self.status_row.addWidget(self.status_icon)
        self.status_row.addWidget(self.status_lbl, 1)
        content.addLayout(self.status_row)

        # 使用系统环境变量的 adb（与其他两个互斥）
        self.system_chk = QCheckBox('使用系统环境变量的 ADB（PATH 中的 adb.exe）')
        self.system_chk.setObjectName('socketChk')
        self.system_chk.setChecked(读取系统adb设置())
        self.system_chk.stateChanged.connect(self._on_system_toggle)
        content.addWidget(self.system_chk)

        # Socket 直连开关
        self.socket_chk = QCheckBox('使用 Socket 直连 ADB（127.0.0.1:5037，不启动 adb 进程）')
        self.socket_chk.setObjectName('socketChk')
        self.socket_chk.setChecked(读取socket直连设置())
        self.socket_chk.stateChanged.connect(self._on_socket_toggle)
        content.addWidget(self.socket_chk)

        # 自研 adb 开关（与其他两个互斥）
        self.selfbuilt_chk = QCheckBox('使用自研 ADB（直连设备 5555，无需官方 adb）')
        self.selfbuilt_chk.setObjectName('socketChk')
        self.selfbuilt_chk.setChecked(读取自研adb设置())
        self.selfbuilt_chk.stateChanged.connect(self._on_selfbuilt_toggle)
        content.addWidget(self.selfbuilt_chk)

        # 版本 + 路径（改 QPlainTextEdit，长内容可滚动完整展示）
        self.version_lbl = self._make_mono_edit('版本：—')
        content.addWidget(self.version_lbl)
        content.addSpacing(8)

        self.path_lbl = self._make_mono_edit('路径：—')
        content.addWidget(self.path_lbl)

        # 跨平台 PATH 配置小提示
        import platform as _plat2
        _os_label = {'windows': 'Windows', 'darwin': 'macOS', 'linux': 'Linux'}.get(
            _plat2.system().lower(), '当前系统'
        )
        self.cn_tip_lbl = QLabel(
            f'💡 当前系统：{_os_label}。PATH 含中文通常不影响 ADB 运行；少数老旧 32 位工具通过 ANSI 读环境变量时可能异常。'
        )
        self.cn_tip_lbl.setObjectName('tipLbl')
        self.cn_tip_lbl.setWordWrap(True)
        # 关键：水平方向 Expanding 占满整行 + 高度策略 Preferred，让 word wrap 后能
        # 自然撑高（不设 minimumHeight 否则弹窗总高会被永久拉大，徒增空白）
        self.cn_tip_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content.addWidget(self.cn_tip_lbl)

        # 底部关闭按钮
        content.addStretch()
        self.close_btn = QPushButton('关闭')
        self.close_btn.setObjectName('okBtn')
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setFixedHeight(36)
        self.close_btn.setMinimumWidth(100)
        self.close_btn.clicked.connect(self.accept)
        content.addWidget(self.close_btn, alignment=Qt.AlignCenter)

        content_widget = QWidget()
        content_widget.setLayout(content)
        layout.addWidget(content_widget)

        # ── 探测初始数据 + 应用主题 ──
        self._current_theme_id = self._resolve_theme(None)
        self.apply_theme(self._current_theme_id)
        self._adb_restarting = False  # 防止多次快速切换导致重启进程并发
        self._probe_gen = 0  # 探测代数，丢弃慢线程的过期结果
        self._probe_done.connect(self._apply_probe_result)
        self._switch_done.connect(self._on_switch_done)
        self._refresh_adb_info()

        # 拖拽状态
        self._dragging = False
        self._drag_pos = QPoint()

    # ------------------------------------------------------------------
    # 主题支持
    # ------------------------------------------------------------------
    def _resolve_theme(self, theme_id):
        if isinstance(theme_id, str) and theme_id in THEMES:
            return theme_id
        p = self.parent()
        cur = getattr(p, '_current_theme', None)
        if isinstance(cur, str) and cur in THEMES:
            return cur
        return DEFAULT_THEME

    @staticmethod
    def _make_mono_edit(text='', max_h=48):
        """只读等宽文本框（取代 QLabel）— 长路径自动出**横向**滚动条。

        设置：
        - ``setLineWrapMode(NoWrap)`` —— 不自动换行，让横向滚动条接管长内容
        - ``setVerticalScrollBarPolicy(AlwaysOff)`` —— 永远不显示纵向滚动条
        - ``setHorizontalScrollBarPolicy(AsNeeded)`` —— 长内容自动出横向滚动条
        - ``setFixedHeight(max_h)`` + ``setMinimumHeight(max_h)`` —— 锁定单行高度，
          避免被外层 QVBoxLayout 当成可压缩成员压成 0 高度
        - ``setFrameShape(NoFrame)`` —— 边框由 QSS 的 ``QPlainTextEdit#monoEdit`` 接管
        """
        edit = QPlainTextEdit(text)
        edit.setObjectName('monoEdit')
        edit.setReadOnly(True)
        edit.setFixedHeight(max_h)
        edit.setMinimumHeight(max_h)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        edit.setFrameShape(QFrame.Shape.NoFrame)
        edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        return edit

    def apply_theme(self, theme_id=None):
        tid = self._resolve_theme(theme_id)
        self._current_theme_id = tid
        t = THEMES.get(tid, THEMES[DEFAULT_THEME])
        accent = t['accent']
        r, g, b = _parse_rgb(accent)
        bg_window = t['bg_window']
        bg_button = t['bg_button']
        text_primary = t['text_primary']
        text_pressed = t['text_pressed']
        text_disabled = t['text_disabled']
        text_success = '#2ecc71'
        text_error = '#e74c3c'

        is_dark = self._is_dark(bg_window)
        # 浅色主题下强制用 #000 充当"关键文本"颜色——accent 墨绿 rgb(0,137,123) 在
        # 白底上对比度只有 5.6:1，刚好压线 AA，叠加抗锯齿后视觉上看似"看不清"；
        # 用 #000 把对比度拉到 21:1，深色主题保持 text_primary（浅色字）。
        text_strong = '#000000' if not is_dark else text_primary
        # 按钮 default 颜色同理：浅色主题用 #000 更稳
        btn_default_color = '#000000' if not is_dark else accent

        self.card.setStyleSheet(f"""
            #envCard {{
                background-color: {bg_window};
                border: 4px solid {accent};
                border-radius: 14px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {text_strong};
                font-family: '{FONT_FAMILY}';
            }}
            QLabel#envTitle {{
                color: {accent};
                font: 700 11pt '{FONT_FAMILY}';
            }}
            QLabel#secTitle {{
                /* 浅色主题用 #000，深色主题用 text_primary（accent 仅做边框/装饰） */
                color: {text_strong};
                font: 700 12pt '{FONT_FAMILY}';
                padding-bottom: 4px;
            }}
            QLabel#monoLbl {{
                color: {text_strong};
                font: 700 10pt 'Consolas','Cascadia Mono','Courier New','{FONT_FAMILY}';
                padding: 6px 10px;
                background-color: rgba({r},{g},{b},{15 if is_dark else 22});
                border: 1px solid {accent};
                border-radius: 6px;
            }}
            QPlainTextEdit#monoEdit {{
                color: {text_strong};
                font: 700 10pt 'Consolas','Cascadia Mono','Courier New','{FONT_FAMILY}';
                padding: 6px 10px;
                background-color: rgba({r},{g},{b},{20 if is_dark else 28});
                border: 1px solid {accent};
                border-radius: 6px;
                /* 选中文字颜色 vs 背景 */
                selection-background-color: rgba({r},{g},{b},100);
                selection-color: {text_strong};
            }}
            QPlainTextEdit#monoEdit QScrollBar:horizontal {{
                background: transparent;
                height: 8px;
                margin: 2px 4px;
            }}
            QPlainTextEdit#monoEdit QScrollBar::handle:horizontal {{
                background: rgba({r},{g},{b},120);
                border-radius: 4px;
                min-width: 32px;
            }}
            QPlainTextEdit#monoEdit QScrollBar::handle:horizontal:hover {{
                background: {accent};
            }}
            QPlainTextEdit#monoEdit QScrollBar::add-line:horizontal,
            QPlainTextEdit#monoEdit QScrollBar::sub-line:horizontal {{
                background: transparent; width: 0;
            }}
            QLabel#statusIcon {{
                font: 14pt '{FONT_FAMILY}';
                background: transparent;
                border: none;
                padding: 0;
            }}
            QLabel#statusLbl {{
                color: {text_strong};
                font: 700 10pt '{FONT_FAMILY}';
                padding: 0;
                border: none;
                background: transparent;
            }}
            QLabel#tipLbl {{
                color: {text_primary if is_dark else '#555555'};
                font: 9pt '{FONT_FAMILY}';
                padding: 4px 0;
                border: none;
                background: transparent;
            }}
            QFrame#sep {{
                background-color: rgba({r},{g},{b},50);
                border: none;
                max-height: 1px;
            }}
            QPushButton#closeBtn {{
                background-color: transparent;
                color: {t['text_disabled']};
                border: none;
                border-radius: 6px;
                font: 14px 'Segoe UI','{FONT_FAMILY}';
                min-width: 28px;
                min-height: 22px;
            }}
            QPushButton#closeBtn:hover {{
                background-color: #e81123;
                color: #ffffff;
            }}
            QPushButton#closeBtn:pressed {{
                background-color: #b0091a;
                color: #ffffff;
            }}
            QPushButton#okBtn {{
                font: 700 11pt '{FONT_FAMILY}';
                color: {btn_default_color};
                background-color: {bg_button};
                border: 1px solid {accent};
                border-radius: 8px;
                padding: 8px 28px;
            }}
            QPushButton#okBtn:hover {{
                background-color: {accent};
                color: {text_pressed};
            }}
            QPushButton#okBtn:pressed {{
                background-color: rgba({r},{g},{b},180);
                color: {text_pressed};
            }}
            QPushButton#refreshBtn {{
                font: 9pt '{FONT_FAMILY}';
                color: {btn_default_color};
                background-color: transparent;
                border: 1px solid {accent};
                border-radius: 6px;
                padding: 8px 16px;
                min-height: 32px;
            }}
            QPushButton#refreshBtn:hover {{
                border-color: {accent};
                color: {accent};
            }}
            QCheckBox#socketChk {{
                color: {text_strong};
                font: 700 9pt '{FONT_FAMILY}';
                background: transparent;
                border: none;
                padding: 4px 0;
                spacing: 8px;
            }}
            QCheckBox#socketChk::indicator {{
                width: 16px;
                height: 16px;
                border: 2px solid {accent};
                border-radius: 4px;
                background: transparent;
            }}
            QCheckBox#socketChk::indicator:checked {{
                background-color: {accent};
                border-color: {accent};
            }}
            QCheckBox#socketChk::indicator:checked::after {{
                content: '✓';
                color: {text_pressed};
                font-size: 12px;
            }}
        """)

        # 状态色：成功绿 / 失败红（跨主题通用）
        self._color_ok = text_success
        self._color_err = text_error
        # 标题单独 setStyleSheet 保留 QSS 优先级（用 accent 保持标题视觉品牌感）
        self.title_lbl.setStyleSheet(
            f"color: {accent}; font: 700 11pt '{FONT_FAMILY}';"
            f"background: transparent; border: none; padding: 0;"
        )

        # 关键修复：强制刷新 card 背景色。
        # 根因：Windows DWM 合成 + WA_TranslucentBackground + DropShadowEffect 三件套下，
        # QWidget.setStyleSheet 写入新 ``background-color`` 后，Qt 样式 cache 不会自动失效，
        # 导致主窗口切换主题时**只有边框/按钮/文字色变了，card 背景仍保持旧色**
        # ——用户必须关闭重开弹窗才生效。
        # 解法：unpolish 把 widget 从 QStyle 摘掉 → setStyleSheet → polish 重新挂上 → update()
        # 强制下一帧 paintEvent 按新 background-color 重画。
        try:
            from PySide6.QtWidgets import QStyle
            style = self.card.style()
            if style is not None:
                style.unpolish(self.card)
                style.polish(self.card)
            self.card.update()
        except Exception:
            pass

        # 外发光
        if self._is_dark(bg_window):
            glow_alpha = 200
        else:
            glow_alpha = 120
        add_green_glow(self.card, blur_radius=24, alpha=glow_alpha, accent=QColor(r, g, b))
        # 强制对话框级别的重绘，确保背景色立即生效
        self.update()
        self.repaint()

    @staticmethod
    def _is_dark(bg_hex):
        s = bg_hex.lstrip('#')
        if len(s) != 6:
            return True
        try:
            rr, gg, bb = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            return True
        lum = (0.299 * rr + 0.587 * gg + 0.114 * bb) / 255.0
        return lum < 0.55

    # ------------------------------------------------------------------
    # 数据探测
    # ------------------------------------------------------------------
    def _refresh_adb_info(self):
        """异步探测当前 ADB 状态并刷新 UI。

        探测涉及 subprocess / socket 探测（socket 模式还可能启动 adb server），
        在主线程同步执行会卡住整个应用，必须放后台线程，
        结果经 _probe_done 信号回主线程更新。
        """
        self.status_lbl.setText('检测中…')
        # 主线程快照勾选状态（后台线程读 Qt 控件不安全）
        if self.system_chk.isChecked():
            mode = 'system'
        elif self.selfbuilt_chk.isChecked():
            mode = 'selfbuilt'
        elif self.socket_chk.isChecked():
            mode = 'socket'
        else:
            mode = 'none'
        self._probe_gen += 1
        gen = self._probe_gen
        import threading
        threading.Thread(target=self._probe_thread, args=(mode, gen),
                         daemon=True).start()

    def _probe_thread(self, mode: str, gen: int):
        result = self._probe_sync(mode)
        result['gen'] = gen
        self._probe_done.emit(result)

    def _apply_probe_result(self, result: dict):
        """主线程：应用探测结果到 UI（丢弃过期代数的结果）。"""
        if result.get('gen') != self._probe_gen:
            return
        color = self._color_ok if result['ok'] else self._color_err
        self.status_lbl.setText(result['status'])
        self.status_lbl.setStyleSheet(
            f"color: {color}; font: 700 10pt '{FONT_FAMILY}';"
            f"background: transparent; border: none; padding: 0;"
        )
        self.status_icon.setStyleSheet(
            f"color: {color}; font: 14pt '{FONT_FAMILY}';"
            f"background: transparent; border: none; padding: 0;"
        )
        self.version_lbl.setPlainText(result['version'])
        self.path_lbl.setPlainText(result['path'])

    def _probe_sync(self, mode: str) -> dict:
        """后台线程：探测当前 ADB 状态，返回结果 dict。"""
        if mode == 'system':
            # 系统环境变量 ADB 模式：强制使用 PATH 中的 adb（排除项目自带的）
            from tools.adb_tools import 查找系统adb路径
            system_adb = 查找系统adb路径()
            if system_adb:
                try:
                    result = subprocess.run(
                        [system_adb, 'version'],
                        capture_output=True, text=True, timeout=5,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                    ver_line = result.stdout.splitlines()[0] if result.stdout else ''
                    ver = ver_line.replace('Android Debug Bridge version ', '').strip()
                except Exception:
                    ver = '未知'
                return {'ok': True, 'status': '已就绪（使用系统环境变量中的 ADB）',
                        'version': f'版本：{ver}', 'path': f'路径：{system_adb}'}
            return {'ok': False, 'status': '系统 PATH 中未找到 adb，请先配置环境变量',
                    'version': '版本：—', 'path': '路径：未在 PATH 中找到 adb'}
        if mode == 'selfbuilt':
            # 自研 adb 模式（A方案：动态库 + ctypes）
            return {'ok': True, 'status': '已切换为自研 ADB 模式（动态库 + ctypes）',
                    'version': '版本：自研 ADB（动态库模式）',
                    'path': '路径：内置动态库（无需官方 adb）'}
        if mode == 'socket':
            # Socket 直连模式
            ver, path = detect_socket_adb()
            if ver and path:
                return {'ok': True, 'status': '已就绪（Socket 直连 5037 端口）',
                        'version': f'版本：{ver}', 'path': f'路径：{path}'}
            return {'ok': False, 'status': 'Socket 直连失败：ADB server 未运行，请先启动 adb server',
                    'version': '版本：—', 'path': '路径：127.0.0.1:5037（连接失败）'}
        # 传统 subprocess 模式
        ver, path = detect_current_adb()
        if ver and path:
            return {'ok': True, 'status': '已就绪（当前 PATH 包含 adb）',
                    'version': f'版本：{ver}', 'path': f'路径：{path}'}
        return {'ok': False, 'status': '未检测到 ADB，请点击下方「一键配置环境」',
                'version': '版本：—', 'path': '路径：—'}

    def _重启adb进程(self, 模式: str):
        """切换 ADB 环境时重启相关进程（异步执行，不阻塞 UI）。

        Args:
            模式: 'system' / 'socket' / 'selfbuilt' / 'none'
        """
        if self._adb_restarting:
            return  # 防止快速多次切换导致并发 kill/start 冲突
        self._adb_restarting = True
        # 先立即刷新 UI，显示"切换中"状态
        self.status_lbl.setText('正在切换 ADB 环境…')
        self.status_lbl.setStyleSheet(
            f"color: {self._color_ok}; font: 700 10pt '{FONT_FAMILY}';"
            f"background: transparent; border: none; padding: 0;"
        )
        import threading
        t = threading.Thread(target=self._重启adb进程同步, args=(模式,), daemon=True)
        t.start()

    def _重启adb进程同步(self, 模式: str):
        """在后台线程中执行：杀旧 adb 进程，按新模式启动 server。
        完成后通过 QTimer.singleShot 回到主线程刷新 UI。"""
        import subprocess
        import time
        import platform
        from PySide6.QtCore import QTimer

        is_windows = platform.system().lower() == 'windows'
        # Windows 下隐藏 subprocess 弹出的 CMD 窗口（打包后尤其明显）
        CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        _kw = {'creationflags': CREATE_NO_WINDOW} if is_windows else {}

        # 1. 杀掉所有 adb.exe 进程
        # 自研模式保留 adb 进程：投屏已改为「adb connect + 官方 scrcpy」，依赖 adb server。
        if 模式 != 'selfbuilt':
            try:
                if is_windows:
                    subprocess.run(
                        ['taskkill', '/F', '/IM', 'adb.exe', '/T'],
                        capture_output=True, timeout=5, **_kw
                    )
                else:
                    # 精确匹配「可执行名 adb」：-f 'adb' 会子串匹配整个命令行，
                    # 本应用路径含 Super_ADB（子串 adb）会误杀自己
                    subprocess.run(['pkill', '-x', 'adb'], capture_output=True, timeout=5)
                time.sleep(0.5)
            except Exception:
                pass

        # 2. 按模式决定是否启动 adb server
        if 模式 in ('system', 'socket'):
            from tools.adb_tools import 查找系统adb路径, 查找内置adb路径
            adb_path = None
            if 模式 == 'system':
                adb_path = 查找系统adb路径()
            if not adb_path:
                adb_path = 查找内置adb路径() or 'adb'
            try:
                subprocess.Popen(
                    [adb_path, 'start-server'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_kw
                )
                time.sleep(1.0)
            except Exception:
                pass
        # 自研模式保留 adb 进程；adb server 由 投屏() 里的 adb connect 按需拉起

        # 回到主线程刷新 UI + 释放锁。
        # 注意：后台线程无事件循环，QTimer.singleShot 回调永不触发（历史缺陷：
        # 状态卡在「正在切换 ADB 环境…」），必须用信号回主线程。
        self._adb_restarting = False
        self._switch_done.emit(模式)

    def _on_switch_done(self, 模式: str):
        """主线程：后台切换完成后设置状态并异步重新探测。"""
        # 先设置切换完成状态，再调用 _refresh_adb_info 更新版本/路径
        if 模式 == 'selfbuilt':
            self.status_lbl.setText('已就绪（自研 ADB 模式，直连设备 5555）')
            self.status_lbl.setStyleSheet(
                f"color: {self._color_ok}; font: 700 10pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
            self.status_icon.setStyleSheet(
                f"color: {self._color_ok}; font: 14pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
        elif 模式 in ('system', 'socket'):
            # system/socket 模式由 _refresh_adb_info 检测后设置最终状态
            pass
        else:
            self.status_lbl.setText('已关闭 ADB server')
            self.status_lbl.setStyleSheet(
                f"color: {self._color_ok}; font: 700 10pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
        self._refresh_adb_info()

    def _on_system_toggle(self, state):
        """系统 adb 开关切换（与其他两个互斥）。"""
        enabled = state == Qt.CheckState.Checked.value
        if enabled:
            # 勾选系统 adb 时，取消其他两个
            self.socket_chk.blockSignals(True)
            self.socket_chk.setChecked(False)
            self.socket_chk.blockSignals(False)
            self.selfbuilt_chk.blockSignals(True)
            self.selfbuilt_chk.setChecked(False)
            self.selfbuilt_chk.blockSignals(False)
        保存adb设置(
            socket_direct=self.socket_chk.isChecked(),
            self_built=self.selfbuilt_chk.isChecked(),
            system_adb=enabled,
        )
        # 切换环境：杀旧进程，按新模式重启（异步，不阻塞 UI）
        self._重启adb进程('system' if enabled else 'none')
        self.设置变更.emit()

    def _on_socket_toggle(self, state):
        """Socket 直连开关切换（与其他两个互斥）。"""
        enabled = state == Qt.CheckState.Checked.value
        if enabled:
            # 勾选 Socket 直连时，取消其他两个
            self.system_chk.blockSignals(True)
            self.system_chk.setChecked(False)
            self.system_chk.blockSignals(False)
            self.selfbuilt_chk.blockSignals(True)
            self.selfbuilt_chk.setChecked(False)
            self.selfbuilt_chk.blockSignals(False)
        保存adb设置(
            socket_direct=enabled,
            self_built=self.selfbuilt_chk.isChecked(),
            system_adb=self.system_chk.isChecked(),
        )
        # 切换环境：杀旧进程，按新模式重启（异步，不阻塞 UI）
        self._重启adb进程('socket' if enabled else 'none')
        self.设置变更.emit()

    def _on_selfbuilt_toggle(self, state):
        """自研 adb 开关切换（与其他两个互斥）。"""
        enabled = state == Qt.CheckState.Checked.value
        if enabled:
            # 勾选自研 adb 时，取消其他两个
            self.system_chk.blockSignals(True)
            self.system_chk.setChecked(False)
            self.system_chk.blockSignals(False)
            self.socket_chk.blockSignals(True)
            self.socket_chk.setChecked(False)
            self.socket_chk.blockSignals(False)
        保存adb设置(
            socket_direct=self.socket_chk.isChecked(),
            self_built=enabled,
            system_adb=self.system_chk.isChecked(),
        )
        # 切换环境：杀旧进程，自研模式不需要 adb server（异步，不阻塞 UI）
        self._重启adb进程('selfbuilt' if enabled else 'none')
        self.设置变更.emit()

    # ------------------------------------------------------------------
    # 鼠标拖拽
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()

    def showEvent(self, event):
        super().showEvent(event)
        if self.parent():
            parent_geo = self.parent().geometry()
            self.move(
                parent_geo.center().x() - self.width() // 2,
                parent_geo.center().y() - self.height() // 2,
            )


if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    dlg = 环境配置对话框()
    dlg.show()
    app.exec()
