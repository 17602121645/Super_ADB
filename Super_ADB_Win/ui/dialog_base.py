# -*- coding: utf-8 -*-
"""
对话框基类
==========
所有弹窗的统一基类，集中处理：
- 窗口图标（:/Super_ADB.png）
- 主题样式（get_stylesheet + 自动跟随当前主题）
- 发光边框（add_green_glow，可选）
- 主题切换（apply_theme，子类可覆盖）

子类只需关注业务布局，不再重复写图标/样式/发光样板。

用法：
    from ui.dialog_base import 对话框基类

    class 证书安装对话框(对话框基类):
        def __init__(self, adb, 获取序列号, parent=None):
            super().__init__(parent, 标题='证书安装', 最小尺寸=(620, 480), 发光=True)
            # 只写业务布局...
"""
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QDialog

try:
    from ui import png_rc  # noqa: F401
except ModuleNotFoundError:
    pass

from ui.ui_styles import get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import add_green_glow


class 对话框基类(QDialog):
    """弹窗基类：统一图标、样式、发光、主题切换。"""

    def __init__(self, parent=None, 标题='', 最小尺寸=(520, 400), 发光=True):
        super().__init__(parent)
        self._主题id = get_current_theme_id(self)
        if 标题:
            self.setWindowTitle(标题)
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(*最小尺寸)
        self.setStyleSheet(get_stylesheet(self._主题id))
        if 发光:
            add_green_glow(self)

    def apply_theme(self, theme_id):
        """主题切换时刷新样式。子类如有自定义控件样式，可覆盖此方法并先调用 super()。"""
        if theme_id not in THEMES:
            return
        self._主题id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
