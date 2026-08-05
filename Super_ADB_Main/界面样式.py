# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— 深色主题样式
====================================
基于 adb_Exp / adb_log_tool 的青绿色强调色深色主题，
扩展了对 QTextEdit、QPlainTextEdit、QGroupBox、QSpinBox、QTextBrowser 等控件的样式支持。
"""

import os
import tempfile

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPainter, QPolygon, QImage

ACCENT = "rgb(29,233,182)"


def _arrow_icon_path():
    """程序化生成一张强调色"向下箭头"PNG，供 QComboBox::down-arrow 使用。"""
    path = os.path.join(tempfile.gettempdir(), 'adb_shell_down_arrow.png')
    if not os.path.exists(path):
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(0x00000000)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(29, 233, 182))
        p.setBrush(QColor(29, 233, 182))
        p.drawPolygon(QPolygon([QPoint(3, 5), QPoint(13, 5), QPoint(8, 12)]))
        p.end()
        img.save(path)
    return path.replace('\\', '/')


_ARROW_ICON = _arrow_icon_path()

STYLE_SHEET = f"""
    /* ────────────── 全局窗口：深色背景 + 浅色文字 ────────────── */
    QWidget {{
        background-color: #2b2b2b;
        color: #e0e0e0;
        font: 10pt "微软雅黑";
    }}

    /* ────────────── 分组框 QGroupBox ────────────── */
    QGroupBox {{
        border: 1px solid {ACCENT};
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 10px;
        padding-bottom: 8px;
        padding-left: 8px;
        padding-right: 8px;
        font: 700 10pt "微软雅黑";
        color: {ACCENT};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        color: {ACCENT};
    }}

    /* ────────────── 下拉框 QComboBox ────────────── */
    QComboBox {{
        background-color: #3a3a3a;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 4px 8px;
        min-height: 20px;
    }}
    QComboBox:hover {{
        border: 1px solid {ACCENT};
        background-color: #444444;
    }}
    QComboBox:focus {{
        border: 2px solid {ACCENT};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border-left: 1px solid {ACCENT};
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
    }}
    QComboBox::down-arrow {{
        image: url({_ARROW_ICON});
        width: 12px;
        height: 12px;
        margin-right: 4px;
    }}
    QComboBox QAbstractItemView {{
        background-color: #2d2d2d;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 4px;
        outline: none;
        selection-background-color: rgba(29,233,182,80);
        selection-color: #ffffff;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 4px 8px;
        min-height: 22px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: rgba(29,233,182,50);
    }}
    QComboBox QAbstractItemView::item:selected {{
        background-color: rgba(29,233,182,90);
        color: #ffffff;
    }}

    /* ────────────── 按钮 QPushButton ────────────── */
    QPushButton {{
        font: 700 10pt "微软雅黑";
        color: {ACCENT};
        background-color: #333333;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{
        background-color: {ACCENT};
        color: #1b1b1b;
    }}
    QPushButton:pressed {{
        background-color: rgba(29,233,182,180);
        color: #1b1b1b;
        padding-left: 15px;
        padding-top: 7px;
    }}
    QPushButton:disabled {{
        color: #777777;
        border: 1px solid #555555;
        background-color: #2b2b2b;
    }}

    /* ────────────── 输入框 QLineEdit ────────────── */
    QLineEdit {{
        background-color: #1f1f1f;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: rgba(29,233,182,120);
        selection-color: #ffffff;
    }}
    QLineEdit:focus {{
        border: 2px solid {ACCENT};
    }}
    QLineEdit:disabled {{
        color: #777777;
        border: 1px solid #555555;
    }}

    /* ────────────── 数字框 QSpinBox ────────────── */
    QSpinBox {{
        background-color: #1f1f1f;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QSpinBox:focus {{
        border: 2px solid {ACCENT};
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background-color: #333333;
        border: 1px solid {ACCENT};
        width: 18px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: {ACCENT};
    }}
    QSpinBox::up-button {{
        border-top-right-radius: 4px;
    }}
    QSpinBox::down-button {{
        border-bottom-right-radius: 4px;
    }}

    /* ────────────── 文本编辑区 QTextEdit / QPlainTextEdit / QTextBrowser ────────────── */
    QTextEdit, QPlainTextEdit, QTextBrowser {{
        background-color: #1f1f1f;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 6px;
        selection-background-color: rgba(29,233,182,120);
        selection-color: #ffffff;
    }}
    QTextEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus {{
        border: 2px solid {ACCENT};
    }}

    /* ────────────── 标签 QLabel ────────────── */
    QLabel {{
        background: transparent;
        border: none;
        color: #e0e0e0;
    }}

    /* ────────────── 状态栏 QStatusBar ────────────── */
    QStatusBar {{
        background-color: #222222;
        color: {ACCENT};
        border-top: 1px solid #3a3a3a;
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ────────────── 菜单 QMenu ────────────── */
    QMenu {{
        background-color: #2d2d2d;
        color: #e0e0e0;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 16px;
        border-radius: 4px;
        background-color: transparent;
    }}
    QMenu::item:selected {{
        background-color: rgba(29,233,182,110);
        color: #ffffff;
    }}
    QMenu::item:disabled {{
        color: #777777;
    }}
    QMenu::separator {{
        height: 1px;
        background-color: #444444;
        margin: 4px 8px;
    }}

    /* ────────────── 滚动条 QScrollBar ────────────── */
    QScrollBar:vertical {{
        background: transparent;
        border: none;
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(29,233,182,130);
        min-height: 24px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {ACCENT};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        border: none;
        background: none;
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        border: none;
        height: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(29,233,182,130);
        min-width: 24px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {ACCENT};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        border: none;
        background: none;
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}
"""
