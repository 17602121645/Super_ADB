# -*- coding: utf-8 -*-
"""
弹窗高亮边框样式
================
统一给项目中的自定义弹窗/独立窗口加上青绿色高亮边框 + 外发光，
并提供可在多个弹窗复用的拖拽区控件 ``DropArea``。

运行期主题切换
--------------
本模块的所有颜色都可以由调用方传入 ``theme_id``（来自 ``界面样式.THEMES`` 的 key），
并通过 ``apply_theme()`` 同步刷新。默认 ``dark_teal`` 与 ``界面样式.DEFAULT_THEME`` 一致。
"""

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QColor, QPainter, QPolygon, QImage, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLabel,
    QFileDialog,
)

from 界面样式 import (
    FONT_FAMILY,
    THEMES,
    DEFAULT_THEME,
)


# ----------------------------------------------------------------------
# 默认主题高亮样式（兼容旧调用）
# ----------------------------------------------------------------------
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


def add_green_glow(widget, blur_radius=24, alpha=200, accent=None):
    """给 widget 添加强调色外发光效果（无偏移，模拟高亮边框光晕）。

    Parameters
    ----------
    accent : QColor | None
        自定义发光颜色；None 则使用默认青绿色，保持旧调用兼容。
    """
    color = accent if isinstance(accent, QColor) else ACCENT
    glow = QGraphicsDropShadowEffect(widget)
    glow.setBlurRadius(blur_radius)
    glow.setColor(QColor(color.red(), color.green(), color.blue(), alpha))
    glow.setOffset(0, 0)
    widget.setGraphicsEffect(glow)


def _parse_rgb(rgb_str):
    """解析 'rgb(29,233,182)' / 'rgb(29, 233, 182)' → (29, 233, 182)。"""
    s = rgb_str
    if s.startswith('rgb(') and s.endswith(')'):
        s = s[4:-1]
    parts = [p.strip() for p in s.split(',') if p.strip()]
    return tuple(int(p) for p in parts[:3])


def _accent_rgb(theme_id):
    """返回主题强调色的 (r, g, b) 三元组。"""
    t = THEMES.get(theme_id, THEMES[DEFAULT_THEME])
    return _parse_rgb(t['accent'])


def _hex_to_rgb(s):
    """解析 '#a7ffeb' / '#fff' → (r, g, b)；解析失败返回 None。"""
    if not isinstance(s, str) or not s.startswith('#'):
        return None
    s = s[1:]
    if len(s) == 3:
        s = ''.join(ch * 2 for ch in s)
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _rgba_string(rgb_or_str, alpha):
    """根据 ''#a7ffeb'' / 'rgb(0,0,0)' / (r,g,b) 拼出 rgba(r,g,b,a)。"""
    if isinstance(rgb_or_str, (tuple, list)) and len(rgb_or_str) >= 3:
        r, g, b = rgb_or_str[:3]
        return f'rgba({r},{g},{b},{alpha})'
    parsed = _parse_rgb(str(rgb_or_str)) if str(rgb_or_str).startswith('rgb') else None
    if parsed is None:
        parsed = _hex_to_rgb(str(rgb_or_str)) or (0, 0, 0)
    r, g, b = parsed
    return f'rgba({r},{g},{b},{alpha})'


# ----------------------------------------------------------------------
# DropArea：可复用拖拽区
# ----------------------------------------------------------------------
class DropArea(QLabel):
    """可拖入文件 / 点击选择文件的虚线框区域。

    把 ``install_zip_dialog`` 里的同名类提到本模块共用，并扩展为多文件拖入。

    Parameters
    ----------
    text : str
        居中显示的提示文案，建议格式 ``"拖拽 X 到此处\\n（或点击下方按钮选择）"``。
    file_filter : str
        点击弹出 ``QFileDialog`` 时使用的过滤串，例如 ``"所有文件 (*)"``；
        传 ``""`` 表示不限制。
    file_mode : str
        ``"single"`` 只取拖入的第一个文件；``"multi"`` 把多个文件/文件夹都传给调用方。
    theme_id : str
        初始主题 id，可在创建后通过 ``apply_theme()`` 切换。
    """

    # 全部已转为本地文件路径（多文件 / 多文件夹模式：调用方展开）
    paths_dropped = Signal(list)

    def __init__(self, parent=None, text='', file_filter='', file_mode='single',
                 theme_id=DEFAULT_THEME):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(72)
        self._text = text or '拖入文件\n（或点击选择文件）'
        self._filter = file_filter
        self._mode = file_mode
        self._theme_id = theme_id if theme_id in THEMES else DEFAULT_THEME
        self._active = False  # 拖拽悬停中标记
        self.setText(self._text)
        self._apply_style()

    # -- 主题切换 ------------------------------------------------------
    def apply_theme(self, theme_id):
        if theme_id not in THEMES:
            return
        self._theme_id = theme_id
        self._apply_style()

    def _apply_style(self):
        t = THEMES[self._theme_id]
        accent = t['accent']
        text_primary = t['text_primary']
        bg_window = t['bg_window']
        # 用 accent 的低透明色作拖入时的底色提示
        accent_low = _rgba_string(accent, 30)        # ~12%
        accent_mid = _rgba_string(accent, 90)        # ~35%
        if self._active:
            # 拖入时高亮态
            self.setStyleSheet(
                f'QLabel{{background: {accent_low}; border: 2px dashed {accent};'
                f' border-radius: 8px; color: {accent};'
                f' font: 10pt "{FONT_FAMILY}"; padding: 12px;}}')
        else:
            # 默认态：边框用主题色淡描，鼠标悬停时变深
            self.setStyleSheet(
                f'QLabel{{background: {accent_low}; border: 2px dashed {accent};'
                f' border-radius: 8px; color: {text_primary};'
                f' font: 10pt "{FONT_FAMILY}"; padding: 12px;}}'
                f'QLabel:hover{{border: 2px solid {accent}; color: {accent};}}')

    # -- 用户交互 ------------------------------------------------------
    def mousePressEvent(self, ev):
        if not self._filter:
            return
        dlg = QFileDialog(self, '选择文件', '', self._filter)
        if self._mode == 'multi':
            dlg.setFileMode(QFileDialog.ExistingFiles)
        else:
            dlg.setFileMode(QFileDialog.ExistingFile)
        if dlg.exec():
            paths = [p for p in dlg.selectedFiles() if p]
            if paths:
                self.paths_dropped.emit(paths)

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self._active = True
            self._apply_style()

    def dragLeaveEvent(self, _ev):
        self._active = False
        self._apply_style()

    def dropEvent(self, ev: QDropEvent):
        self._active = False
        self._apply_style()
        urls = ev.mimeData().urls()
        paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
        paths = [p for p in paths if p]
        if paths:
            self.paths_dropped.emit(paths)
            ev.acceptProposedAction()


# ----------------------------------------------------------------------
# Down-Arrow 图标：主题感知版（替代 界面样式._arrow_icon_path 中默认值的复用入口）
# ----------------------------------------------------------------------
def make_down_arrow_pixmap(theme_id, size=16):
    """生成主题色向下箭头 QPixmap，用于自定义下拉箭头位置仍需要纯 QPixmap 的场景。"""
    r, g, b = _accent_rgb(theme_id)
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(0x00000000)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(r, g, b))
    p.setBrush(QColor(r, g, b))
    p.drawPolygon(QPolygon([QPoint(3, 5), QPoint(13, 5), QPoint(8, 12)]))
    p.end()
    return img
