# -*- coding: utf-8 -*-
"""
Super_ADB 关于弹窗
==================
展示公众号二维码、版本号与反馈引导，**支持运行时切换主题**。

设计要点：
- 弹窗内所有颜色（卡片背景、标题/副标题/链接、外发光等）都从当前主题
  ``界面样式.THEMES[tid]`` 派生，浅色/深色主题都能正常显示
- 提供 ``apply_theme(theme_id=None)``：
  - 默认 ``theme_id`` → 从父窗口 ``_current_theme`` 读，缺省回落到
    ``界面样式.DEFAULT_THEME``
  - 主窗口切换主题后通过 ``Super_ADB_Win._propagate_theme_to_dialogs``
    把新主题同步到已打开的弹窗
- 关闭按钮 hover 红色是跨主题通用视觉提示（不跟主题），其余一律吃主题色
"""

import png_rc  # noqa: F401   # 注册 :/Super_ADB.png 与 :/qrcode.jpg 资源
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QSizePolicy, QGraphicsDropShadowEffect,
)

from 界面样式 import FONT_FAMILY, THEMES, DEFAULT_THEME, _parse_rgb
from popup_style import add_green_glow

VERSION = 'v2026.08.07'
REPO_URL = 'https://gitee.com/jcs1995/super_-adb-2026.git'


class AboutDialog(QDialog):
    """带自定义标题栏的圆角关于弹窗，跟随主窗口主题。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(440, 620)
        self.setWindowTitle('关于 Super_ADB')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))

        # ── 容器（圆角卡片）───────────────────────────────────────
        self.card = QWidget(self)
        self.card.setObjectName('aboutCard')
        self.card.setGeometry(10, 10, 420, 600)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 自定义标题栏 ──────────────────────────────────────────
        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(12, 8, 8, 8)
        title_bar.setSpacing(6)

        self.title_lbl = QLabel('关于 Super_ADB')
        self.title_lbl.setObjectName('aboutTitle')
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
        content.setContentsMargins(24, 10, 24, 22)
        content.setSpacing(14)

        # 大标题（中央）
        self.app_title = QLabel('Super_ADB')
        self.app_title.setAlignment(Qt.AlignCenter)
        content.addWidget(self.app_title)

        # 副标题
        self.sub_title = QLabel('ADB 集成调试工具')
        self.sub_title.setAlignment(Qt.AlignCenter)
        content.addWidget(self.sub_title)

        content.addSpacing(6)

        # 二维码（保持白底，跟主题无关——保证扫码识别率）
        qr = self._load_qr_pixmap()
        self.qr_lbl = QLabel()
        self.qr_lbl.setObjectName('aboutQr')
        self.qr_lbl.setAlignment(Qt.AlignCenter)
        self.qr_lbl.setFixedSize(220, 220)
        self.qr_lbl.setPixmap(qr)
        content.addWidget(self.qr_lbl, alignment=Qt.AlignCenter)

        content.addSpacing(12)

        # 提示文字
        self.hint = QLabel(
            '使用过程中遇到 Bug，或有好的改进提议\n'
            '欢迎扫码前往公众号留言反馈\n\n'
            '详细使用说明请前往公众号查看\n'
            '公众号搜索：Super_ADB'
        )
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        content.addWidget(self.hint)

        content.addStretch()

        # 版本号（次要文字）
        self.version_lbl = QLabel(f'版本号：{VERSION}')
        self.version_lbl.setAlignment(Qt.AlignCenter)
        content.addWidget(self.version_lbl)

        # 开源地址（可点击跳转）
        self.repo_lbl = QLabel(f'<a href="{REPO_URL}">开源地址：{REPO_URL}</a>')
        self.repo_lbl.setObjectName('aboutRepo')
        self.repo_lbl.setAlignment(Qt.AlignCenter)
        self.repo_lbl.setOpenExternalLinks(True)
        self.repo_lbl.setWordWrap(True)
        content.addWidget(self.repo_lbl)

        # 底部按钮
        self.ok_btn = QPushButton('知道了')
        self.ok_btn.setObjectName('okBtn')
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self.accept)
        content.addWidget(self.ok_btn, alignment=Qt.AlignCenter)

        content_widget = QWidget()
        content_widget.setLayout(content)
        layout.addWidget(content_widget)

        # ── 外发光（强调色高亮），在 apply_theme 中按主题创建 ─────
        # 使用 popup_style.add_green_glow 以兼容主窗口主题切换后的
        # _rebuild_all_glow 统一重建（避免 QGraphicsDropShadowEffect
        # setColor 在 DWM 分层窗口下被 cache 吞掉的问题）。

        # ── 应用当前主题（默认从父窗口读，缺省走 DEFAULT_THEME） ──
        self._current_theme_id = self._resolve_theme(None)
        self.apply_theme(self._current_theme_id)

        # 拖拽状态
        self._dragging = False
        self._drag_pos = QPoint()

    # ------------------------------------------------------------------
    # 主题支持
    # ------------------------------------------------------------------
    def _resolve_theme(self, theme_id):
        """解析要使用的主题 id：优先参数，其次父窗口，最后 DEFAULT_THEME。"""
        if isinstance(theme_id, str) and theme_id in THEMES:
            return theme_id
        # 从父窗口读当前主题
        p = self.parent()
        cur = getattr(p, '_current_theme', None)
        if isinstance(cur, str) and cur in THEMES:
            return cur
        return DEFAULT_THEME

    def apply_theme(self, theme_id=None):
        """按主题重算卡片 / 文字 / 按钮 / 链接 / 外发光的颜色。

        主窗口 ``_switch_theme`` → ``_propagate_theme_to_dialogs`` 时会调本方法，
        弹窗就能跟随主题实时切换。"""
        tid = self._resolve_theme(theme_id)
        self._current_theme_id = tid
        t = THEMES.get(tid, THEMES[DEFAULT_THEME])

        accent = t['accent']                       # 'rgb(0,137,123)' 等
        r, g, b = _parse_rgb(accent)
        bg_window = t['bg_window']
        bg_button = t['bg_button']
        text_primary = t['text_primary']
        text_pressed = t['text_pressed']
        text_disabled = t['text_disabled']

        # 标题栏/链接色 = 强调色（深色主题下读起来也清晰）
        title_color = accent if self._is_dark(bg_window) else accent

        # ── 卡片 + 卡片里所有 QLabel 的默认样式 ──────────────────
        # 关闭按钮 ✕ 跨主题通用红色 hover（不跟主题），保证视觉提示一致
        self.card.setStyleSheet(f"""
            #aboutCard {{
                background-color: {bg_window};
                border: 4px solid {accent};
                border-radius: 14px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: {text_primary};
                font-family: '{FONT_FAMILY}';
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
                font: 700 10pt '{FONT_FAMILY}';
                color: {accent};
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
            QLabel#aboutTitle {{
                color: {title_color};
                font: 700 11pt '{FONT_FAMILY}';
            }}
            QLabel#aboutQr {{
                background-color: #ffffff;
                border: 2px solid {accent};
                border-radius: 10px;
                padding: 6px;
            }}
            QLabel#aboutRepo {{
                color: {accent};
                font: 9pt '{FONT_FAMILY}';
                background: transparent;
            }}
            QLabel#aboutRepo a {{
                color: {accent};
                text-decoration: none;
            }}
            QLabel#aboutRepo a:hover {{
                text-decoration: underline;
            }}
        """)

        # ── 单独覆盖文字类 QLabel（保留各自 QSS 的优先级） ─────
        # 大标题：主文字色，确保深/浅主题都能读
        self.app_title.setStyleSheet(
            f"color: {text_primary}; font: 700 18pt '{FONT_FAMILY}';"
        )
        # 副标题：用次要文字色，整体更柔和
        self.sub_title.setStyleSheet(
            f"color: {text_disabled}; font: 10pt '{FONT_FAMILY}';"
        )
        # 提示文字：主文字色
        self.hint.setStyleSheet(
            f"color: {text_primary}; font: 9pt '{FONT_FAMILY}';"
        )
        # 版本号：次要文字色
        self.version_lbl.setStyleSheet(
            f"color: {text_disabled}; font: 9pt '{FONT_FAMILY}';"
        )

        # ── 外发光用强调色派生（透明度按主题差异化：浅色更柔和） ──
        if self._is_dark(bg_window):
            glow_alpha = 200
        else:
            # 浅色主题高 alpha 会很突兀，降到 120 让卡片有"漂浮感"而不刺眼
            glow_alpha = 120
        add_green_glow(self.card, blur_radius=24, alpha=glow_alpha, accent=QColor(r, g, b))

    @staticmethod
    def _is_dark(bg_hex):
        """按背景亮度粗判深浅：浅色背景→True，反之→False。"""
        s = bg_hex.lstrip('#')
        if len(s) != 6:
            return True
        try:
            rr, gg, bb = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            return True
        # 简单亮度公式（W3C 调整后亮度）
        lum = (0.299 * rr + 0.587 * gg + 0.114 * bb) / 255.0
        return lum < 0.55

    # ------------------------------------------------------------------
    # 二维码加载
    # ------------------------------------------------------------------
    def _load_qr_pixmap(self):
        """加载公众号二维码（不透明版本），从 qrc 资源读取（打包后也能用），失败回退到占位图。

        资源 alias = qrcode.jpg，源文件 ui/公众号.jpg，由 ui/png.qrc 编译进 png_rc.py。
        用 qrc 而非磁盘读取，是为了打包进 PyInstaller 后仍能正常显示（--add-data 经常漏配）。
        """
        pm = QPixmap(':/qrcode.jpg')
        if not pm.isNull():
            return pm.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        # 兜底：绘制占位图（理论上不会到这里，qrc 里有就一定能加载）
        pm = QPixmap(200, 200)
        pm.fill(QColor('#ffffff'))
        p = QPainter(pm)
        p.setPen(QColor('#333333'))
        p.setFont(QFont(FONT_FAMILY, 12))
        p.drawText(pm.rect(), Qt.AlignCenter, '二维码加载失败')
        p.end()
        return pm

    # ------------------------------------------------------------------
    # 鼠标拖拽（限定在标题栏区域拖动整个弹窗）
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
        # 相对父窗口居中
        if self.parent():
            parent_geo = self.parent().geometry()
            self.move(
                parent_geo.center().x() - self.width() // 2,
                parent_geo.center().y() - self.height() // 2,
            )


if __name__ == '__main__':
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    dlg = AboutDialog()
    dlg.show()
    app.exec()
