# -*- coding: utf-8 -*-
"""
Super_ADB 关于弹窗
==================
展示公众号二维码、版本号与反馈引导，适配深色主题。
"""

import os

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QSizePolicy, QGraphicsDropShadowEffect,
)

from 界面样式 import FONT_FAMILY

# 注册 png_rc 资源（含「透明公众号」二维码），import 即执行 qInitResources()
import png_rc  # noqa: F401

VERSION = 'v2026.08.07'
REPO_URL = 'https://gitee.com/jcs1995/super_-adb.git'


class AboutDialog(QDialog):
    """带自定义标题栏的圆角关于弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(440, 620)
        self.setWindowTitle('关于 Super_ADB')

        # ── 容器（圆角卡片）───────────────────────────────────────
        self.card = QWidget(self)
        self.card.setObjectName('aboutCard')
        self.card.setGeometry(10, 10, 420, 600)
        self.card.setStyleSheet(f"""
            #aboutCard {{
                background-color: #2d2d2d;
                border: 4px solid rgb(29,233,182);
                border-radius: 14px;
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: #e0e0e0;
            }}
            QPushButton#closeBtn {{
                background-color: transparent;
                color: #cccccc;
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
                color: rgb(29,233,182);
                background-color: #333333;
                border: 1px solid rgb(29,233,182);
                border-radius: 8px;
                padding: 8px 28px;
            }}
            QPushButton#okBtn:hover {{
                background-color: rgb(29,233,182);
                color: #1b1b1b;
            }}
            QPushButton#okBtn:pressed {{
                background-color: rgba(29,233,182,180);
                color: #1b1b1b;
            }}
        """)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 自定义标题栏 ──────────────────────────────────────────
        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(12, 8, 8, 8)
        title_bar.setSpacing(6)

        title_lbl = QLabel('关于 Super_ADB')
        title_lbl.setStyleSheet(f"color: rgb(29,233,182); font: 700 11pt '{FONT_FAMILY}';")
        title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title_bar.addWidget(title_lbl)

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

        # 标题
        app_title = QLabel('Super_ADB')
        app_title.setAlignment(Qt.AlignCenter)
        app_title.setStyleSheet(f"color: #ffffff; font: 700 18pt '{FONT_FAMILY}';")
        content.addWidget(app_title)

        # 副标题
        sub_title = QLabel('ADB 集成调试工具')
        sub_title.setAlignment(Qt.AlignCenter)
        sub_title.setStyleSheet(f"color: #bbbbbb; font: 10pt '{FONT_FAMILY}';")
        content.addWidget(sub_title)

        content.addSpacing(6)

        # 二维码（不透明版本）
        qr = self._load_qr_pixmap()
        self.qr_lbl = QLabel()
        self.qr_lbl.setAlignment(Qt.AlignCenter)
        self.qr_lbl.setFixedSize(220, 220)
        self.qr_lbl.setPixmap(qr)
        self.qr_lbl.setStyleSheet("""
            background-color: #ffffff;
            border: 2px solid rgb(29,233,182);
            border-radius: 10px;
            padding: 6px;
        """)
        content.addWidget(self.qr_lbl, alignment=Qt.AlignCenter)

        content.addSpacing(20)

        # 提示文字
        hint = QLabel('使用过程中遇到 Bug，或有好的改进提议\n欢迎扫码前往公众号留言反馈')
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: #e0e0e0; font: 9pt '{FONT_FAMILY}';")
        content.addWidget(hint)

        content.addStretch()

        # 版本号
        version_lbl = QLabel(f'版本号：{VERSION}')
        version_lbl.setAlignment(Qt.AlignCenter)
        version_lbl.setStyleSheet(f"color: #888888; font: 9pt '{FONT_FAMILY}';")
        content.addWidget(version_lbl)

        # 开源地址（可点击跳转）
        repo_lbl = QLabel(
            f'<a href="{REPO_URL}">开源地址：{REPO_URL}</a>')
        repo_lbl.setAlignment(Qt.AlignCenter)
        repo_lbl.setOpenExternalLinks(True)
        repo_lbl.setWordWrap(True)
        repo_lbl.setStyleSheet(
            f"color: rgb(29,233,182); font: 9pt '{FONT_FAMILY}';")
        content.addWidget(repo_lbl)

        # 底部按钮
        ok_btn = QPushButton('知道了')
        ok_btn.setObjectName('okBtn')
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.clicked.connect(self.accept)
        content.addWidget(ok_btn, alignment=Qt.AlignCenter)

        content_widget = QWidget()
        content_widget.setLayout(content)
        layout.addWidget(content_widget)

        # ── 绿色高亮外边框光晕 ────────────────────────────────────
        glow = QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(24)
        glow.setColor(QColor(29, 233, 182, 200))
        glow.setOffset(0, 0)
        self.card.setGraphicsEffect(glow)

        # 拖拽状态
        self._dragging = False
        self._drag_pos = QPoint()

    def _load_qr_pixmap(self):
        """加载公众号二维码（不透明版本），失败回退到占位图。"""
        # 资源路径（png_rc 已注册：前缀「公众号」→ 公众号.jpg）
        resource_path = ':/公众号/公众号.jpg'
        pm = QPixmap(resource_path)
        if not pm.isNull():
            return pm.scaled(200, 200, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        # 兜底：绘制占位图
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
