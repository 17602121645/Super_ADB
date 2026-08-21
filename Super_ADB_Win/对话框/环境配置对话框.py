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
- 内置 ADB 路径探测：与 ``ADB工具.find_scrcpy_dir`` 同样的「base / parent / cwd」三级回退，
  覆盖源码模式 ``Super_ADB_Win/外部扩展/...`` 与冻结模式 ``_internal/外部扩展/...`` 两种布局
"""

import os
import sys
import shutil
import subprocess
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QSizePolicy, QGraphicsDropShadowEffect, QFrame,
    QPlainTextEdit,
)

from 项目UI.界面样式 import FONT_FAMILY, THEMES, DEFAULT_THEME, _parse_rgb
from 项目UI.弹窗样式 import add_green_glow
from ADB工具 import find_bundled_adb_path

# Windows 专属 PATH 持久化（其他平台该按钮隐藏/禁用）
IS_WINDOWS = sys.platform == 'win32'


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


def add_to_user_path(new_dir):
    """把 new_dir 追加到当前用户的 PATH 末尾（去重），返回 (ok, msg)。

    跨平台实现：
    - **Windows**：通过 ``winreg`` 操作 ``HKCU\\Environment``（无需管理员），
      写入后通过 ``ctypes`` 广播 ``WM_SETTINGCHANGE`` 让新启动进程立即生效
    - **macOS**：写入 ``~/.zshrc``（优先）或 ``~/.bash_profile``（intel mac）/ ``~/.bashrc``
    - **Linux**：写入 ``~/.bashrc``（优先）/ ``~/.profile`` / ``~/.zshrc``

    Linux/macOS 上仅追加 ``export PATH="...":$PATH  # Added by Super_ADB``，
    并以 marker 注释去重，下次再调用不会重复追加。
    """
    import platform
    sysname = platform.system().lower()
    if sysname == 'windows':
        return _add_to_windows_path(new_dir)
    return _add_to_unix_rc(new_dir, sysname=sysname)


def _add_to_windows_path(new_dir):
    """Windows: winreg 操作 HKCU\\Environment。"""
    try:
        import winreg
    except ImportError:
        return False, 'winreg 模块不可用'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment',
                            0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current, _ = winreg.QueryValueEx(key, 'Path')
            except FileNotFoundError:
                current = ''
            # 拆分 PATH：Windows PATH 用 ; 分隔（用户级忽略大小写）
            parts = [p for p in current.split(';') if p]
            norm = os.path.normcase(os.path.normpath(new_dir))
            exists_norm = {os.path.normcase(os.path.normpath(p)) for p in parts}
            if norm in exists_norm:
                return True, '已存在于 PATH（无需重复添加）'
            parts.append(new_dir)
            new_value = ';'.join(parts)
            winreg.SetValueEx(key, 'Path', 0, winreg.REG_EXPAND_SZ, new_value)
        # 广播环境变量变更（让已运行的 explorer / 其它进程能感知）
        try:
            import ctypes
            from ctypes import wintypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'Environment',
                SMTO_ABORTIFHUNG, 1000, ctypes.byref(wintypes.LRESULT(0))
            )
        except Exception:
            pass

        # 关键修复：同步当前 Python 进程的环境变量（解决 PATH 进程缓存陷阱）
        # 根因：Python 启动时一次性拷贝 PATH 副本到 os.environ，注册表改了
        # 之后 WM_SETTINGCHANGE 只通知 explorer，不会反向写回 os.environ；
        # 导致当前进程的 shutil.which('adb') 仍用旧 PATH，识别不到新加入的 adb。
        # 解法：手动同步 os.environ + SetEnvironmentVariableW 让当前进程立即生效。
        try:
            os.environ['PATH'] = new_value
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.kernel32.SetEnvironmentVariableW('Path', new_value)
        except Exception:
            pass

        return True, '已写入用户 PATH，当前进程及新启动的终端均已生效'
    except PermissionError:
        return False, '权限不足：写入用户 PATH 被拒绝'
    except Exception as e:
        return False, f'写入失败: {e}'


def _add_to_unix_rc(new_dir, sysname):
    """macOS / Linux：写入用户 shell 启动文件（``~/.zshrc`` / ``~/.bashrc`` 等）。

    去重：以 marker 注释判定是否已追加过，重复调用幂等。
    """
    try:
        norm = os.path.abspath(os.path.expanduser(new_dir))
    except Exception as e:
        return False, f'路径规范化失败: {e}'
    home = os.path.expanduser('~')

    # macOS 与 Linux 默认 shell 不同，选择优先级不同
    if sysname == 'darwin':
        candidates = ['.zshrc', '.bash_profile', '.bashrc']
    else:
        candidates = ['.bashrc', '.profile', '.zshrc']

    target = None
    for fname in candidates:
        candidate = os.path.join(home, fname)
        if os.path.isfile(candidate):
            target = candidate
            break
    if target is None:
        # 兜底用首选文件，会创建新文件
        target = os.path.join(home, candidates[0])

    marker = '# Added by Super_ADB - platform-tools path'
    line = f'export PATH="{norm}:$PATH"  {marker}\n'

    try:
        existing = ''
        if os.path.isfile(target):
            with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                existing = f.read()
        if marker in existing:
            return True, f'已存在于 {os.path.basename(target)}（无需重复添加）'
        # 文件不存在则创建（确保父目录存在）
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'a', encoding='utf-8') as f:
            if existing and not existing.endswith('\n'):
                f.write('\n')
            f.write(line)
        return True, f'已写入 {os.path.basename(target)}，新启动终端生效'
    except PermissionError:
        return False, f'权限不足：写入 {target} 被拒绝'
    except Exception as e:
        return False, f'写入失败: {e}'


class 环境配置对话框(QDialog):
    """带自定义标题栏的圆角环境配置弹窗，跟随主窗口主题。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(680, 510)
        self.setWindowTitle('环境配置')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))

        # ── 容器（圆角卡片）───────────────────────────────────────
        self.card = QWidget(self)
        self.card.setObjectName('envCard')
        self.card.setGeometry(10, 10, 660, 450)

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

        # 版本 + 路径（改 QPlainTextEdit，长内容可滚动完整展示）
        self.version_lbl = self._make_mono_edit('版本：—')
        content.addWidget(self.version_lbl)

        self.path_lbl = self._make_mono_edit('路径：—')
        content.addWidget(self.path_lbl)

        # Section 2: 工具内置 ADB（跨平台 Windows / macOS / Linux 都显示）
        content.addSpacing(6)
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setObjectName('sep')
        content.addWidget(sep1)
        content.addSpacing(4)

        # 跨平台 Section 2 标题会显示当前系统的内置 adb 二进制名（adb.exe / adb）
        import platform as _plat
        _adb_name = 'adb.exe' if _plat.system().lower() == 'windows' else 'adb'
        sec2_lbl = QLabel(f'工具内置 ADB（{_adb_name} · 一键配置）')
        sec2_lbl.setObjectName('secTitle')
        self.addpath_btn = QPushButton('一键配置环境')
        self.addpath_btn.setObjectName('addpathBtn')
        self.addpath_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.addpath_btn.setFixedHeight(36)
        self.addpath_btn.setMinimumWidth(140)
        self.addpath_btn.clicked.connect(self._on_add_to_path)
        sec2_row = QHBoxLayout()
        sec2_row.setSpacing(8)
        sec2_row.setContentsMargins(0, 0, 0, 0)
        sec2_row.addWidget(sec2_lbl)
        sec2_row.addStretch()
        sec2_row.addWidget(self.addpath_btn)
        content.addLayout(sec2_row)

        # 内置 ADB 路径（mono edit，长路径走横向滚动条展示完整）
        self.bundled_lbl = self._make_mono_edit('—')
        content.addWidget(self.bundled_lbl)

        self.path_result_lbl = QLabel('')
        self.path_result_lbl.setObjectName('resultLbl')
        self.path_result_lbl.setWordWrap(True)
        content.addWidget(self.path_result_lbl)

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
        self._resolve_bundled_path()
        self._current_theme_id = self._resolve_theme(None)
        self.apply_theme(self._current_theme_id)
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
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
                border: 1px solid rgba({r},{g},{b},{90 if is_dark else 70});
                border-radius: 6px;
            }}
            QPlainTextEdit#monoEdit {{
                color: {text_strong};
                font: 700 10pt 'Consolas','Cascadia Mono','Courier New','{FONT_FAMILY}';
                padding: 6px 10px;
                background-color: rgba({r},{g},{b},{20 if is_dark else 28});
                /* 边框加粗到 2px + 提高 alpha，确保深色主题下也清晰可见 */
                border: 2px solid rgba({r},{g},{b},{160 if is_dark else 130});
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
            QLabel#resultLbl {{
                color: {text_strong};
                font: 9pt '{FONT_FAMILY}';
                padding: 2px 0;
                border: none;
                background: transparent;
            }}
            QLabel#tipLbl {{
                color: {text_disabled if is_dark else '#555555'};
                font: 9pt '{FONT_FAMILY}';
                padding: 0;
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
                border: 2px solid {accent};
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
            QPushButton#addpathBtn {{
                font: 700 10pt '{FONT_FAMILY}';
                color: {btn_default_color};
                background-color: {bg_button};
                border: 2px solid {accent};
                border-radius: 6px;
                padding: 8px 18px;
            }}
            QPushButton#addpathBtn:hover {{
                background-color: {accent};
                color: {text_pressed};
            }}
            QPushButton#addpathBtn:pressed {{
                background-color: rgba({r},{g},{b},180);
                color: {text_pressed};
            }}
            QPushButton#addpathBtn:disabled {{
                color: {text_disabled};
                border-color: {text_disabled};
            }}
            QPushButton#refreshBtn {{
                font: 9pt '{FONT_FAMILY}';
                color: {btn_default_color};
                background-color: transparent;
                border: 1px solid {text_disabled};
                border-radius: 6px;
                padding: 4px 10px;
            }}
            QPushButton#refreshBtn:hover {{
                border-color: {accent};
                color: {accent};
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
    def _resolve_bundled_path(self):
        """探测内置 ADB 路径，缓存到 self._bundled_path。"""
        self._bundled_path = find_bundled_adb_path()
        if self._bundled_path:
            self.bundled_lbl.setPlainText(self._bundled_path)
            self.addpath_btn.setEnabled(True)
        else:
            import platform as _plat3
            _sys_label = {'windows': 'Windows', 'darwin': 'macOS', 'linux': 'Linux'}.get(
                _plat3.system().lower(), _plat3.system()
            )
            self.bundled_lbl.setPlainText(f'（未在本工具目录找到 {_sys_label} 版内置 adb）')
            self.addpath_btn.setEnabled(False)

    def _refresh_adb_info(self):
        """重新探测当前 ADB 状态并刷新 UI。"""
        ver, path = detect_current_adb()
        if ver and path:
            self.status_lbl.setText('已就绪（当前 PATH 包含 adb）')
            self.status_lbl.setStyleSheet(
                f"color: {self._color_ok}; font: 700 10pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
            self.status_icon.setStyleSheet(
                f"color: {self._color_ok}; font: 14pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
            self.version_lbl.setPlainText(f'版本：{ver}')
            self.path_lbl.setPlainText(f'路径：{path}')
        else:
            self.status_lbl.setText('未检测到 ADB，请点击下方「一键配置环境」')
            self.status_lbl.setStyleSheet(
                f"color: {self._color_err}; font: 700 10pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
            self.status_icon.setStyleSheet(
                f"color: {self._color_err}; font: 14pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 0;"
            )
            self.version_lbl.setPlainText('版本：—')
            self.path_lbl.setPlainText('路径：—')
        if hasattr(self, 'path_result_lbl'):
            self.path_result_lbl.setText('')  # 清掉上次的写入结果

    def _on_add_to_path(self):
        """点击「一键配置环境」：把内置 adb 的父目录写入当前系统的用户 PATH。"""
        if not self._bundled_path:
            self.path_result_lbl.setStyleSheet(
                f"color: {self._color_err}; font: 9pt '{FONT_FAMILY}';"
                f"background: transparent; border: none; padding: 2px 0;"
            )
            self.path_result_lbl.setText('未找到内置 ADB，无法配置。请确认「外部扩展/adb/platform-tools-latest-<系统>/platform-tools/」目录存在')
            return
        # 写入的是 adb 的父目录（platform-tools），这样 PATH 里就能直接 adb
        target_dir = os.path.dirname(self._bundled_path)
        ok, msg = add_to_user_path(target_dir)
        color = self._color_ok if ok else self._color_err
        self.path_result_lbl.setStyleSheet(
            f"color: {color}; font: 9pt '{FONT_FAMILY}';"
            f"background: transparent; border: none; padding: 2px 0;"
        )
        self.path_result_lbl.setText(msg)
        # 无论成功失败都重新探测一次（成功后系统 PATH 应已可被探测到）
        if ok:
            self._refresh_adb_info()

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
