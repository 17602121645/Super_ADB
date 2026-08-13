# -*- coding: utf-8 -*-
"""
弹窗高亮边框样式
================
统一给项目中的自定义弹窗/独立窗口加上青绿色高亮边框 + 外发光。
"""

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect

ACCENT = QColor(29, 233, 182)
ACCENT_CSS = 'rgb(29,233,182)'

# 卡片容器样式：深背景 + 青绿高亮边框 + 圆角
HIGHLIGHT_CARD_STYLE = """
    #popupCard {
        background-color: #2d2d2d;
        border: 4px solid rgb(29,233,182);
        border-radius: 12px;
    }
    QLabel {
        background: transparent;
        border: none;
        color: #e0e0e0;
    }
"""


def add_green_glow(widget, blur_radius=24, alpha=200):
    """给 widget 添加青绿色外发光效果（无偏移，模拟高亮边框光晕）。"""
    glow = QGraphicsDropShadowEffect(widget)
    glow.setBlurRadius(blur_radius)
    glow.setColor(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), alpha))
    glow.setOffset(0, 0)
    widget.setGraphicsEffect(glow)
