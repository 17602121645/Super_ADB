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

from PySide6.QtCore import Qt, Signal, QPoint, QCoreApplication
from PySide6.QtGui import QColor, QPainter, QPolygon, QImage, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QLabel,
    QFileDialog,
)

import sys

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

    主题切换支持
    ------------
    弹窗经常在 ``__init__`` 里调本函数只一次；后续主程序切换主题时，弹窗
    内部样式 (``setStyleSheet``) 由 ``Super_ADB_Main._propagate_theme_to_dialogs``
    同步刷新，但发光 ``QGraphicsDropShadowEffect`` 不会自动变色。本函数
    在 widget 上挂两个属性：

    - ``_green_glow_params = (blur_radius, alpha)``：标记 + 原始参数。
    - ``_green_glow_accent_rgb = (r, g, b)``：当前 accent（初始值）。

    配合 ``rebuild_glow(widget, accent_rgb)`` 可逐 widget 重建 DropShadow，
    避开 PySide6 6.11.1 + DWM 分层窗口合成下的 halo bitmap cache 坑
    （attach 后 halo 不立即重画 + setColor 不失效）。
    """
    color = accent if isinstance(accent, QColor) else ACCENT
    glow = QGraphicsDropShadowEffect(widget)
    glow.setBlurRadius(blur_radius)
    glow.setColor(QColor(color.red(), color.green(), color.blue(), alpha))
    glow.setOffset(0, 0)
    widget.setGraphicsEffect(glow)
    widget._green_glow_params = (blur_radius, alpha)
    widget._green_glow_accent_rgb = (color.red(), color.green(), color.blue())


def rebuild_glow(widget, accent_rgb=None):
    """主题切换后重建 widget 上的 DropShadow。

    Returns
    -------
    bool
        是否真的做了重建（widget 上没有 ``_green_glow_params`` 标记时返 False）。

    关键技巧
    --------
    - **blurRadius 微差**（22~25 之间，按主题色 hash 浮动）强制 DWM 视作
      不同 effect bitmap，halo 旧 cache 必失效。
    - detach → ``processEvents`` → attach → ``processEvents`` flush 时序，
      避免 detach/reattach 在事件循环中被合并（实测 2026-08-20：合并执行
      时 DWM 仍认为 effect 没换）。
    - 末尾 ``repaint() + windowHandle().requestUpdate() + Win32 InvalidateRect``
      兜底 native 合成。
    """
    params = getattr(widget, '_green_glow_params', None)
    if params is None:
        return False
    blur_radius, alpha = params
    if accent_rgb is None:
        accent_rgb = widget._green_glow_accent_rgb
    r, g, b = accent_rgb[0], accent_rgb[1], accent_rgb[2]
    # blurRadius 微差（22..25 for base blur_radius=24）
    color_hash = (r * 31 + g * 17 + b * 7) & 0x03
    new_blur = blur_radius - 2 + color_hash

    new_glow = QGraphicsDropShadowEffect(widget)
    new_glow.setBlurRadius(new_blur)
    new_glow.setOffset(0, 0)
    new_glow.setColor(QColor(r, g, b, alpha))

    # detach 旧 effect
    widget.setGraphicsEffect(None)
    if QCoreApplication.instance() is not None:
        QCoreApplication.processEvents()
    # attach 新 effect
    widget.setGraphicsEffect(new_glow)
    if QCoreApplication.instance() is not None:
        QCoreApplication.processEvents()
    widget._green_glow_accent_rgb = (r, g, b)
    # native 层强制重画
    widget.repaint()
    wh = widget.windowHandle()
    if wh is not None:
        wh.requestUpdate()
    if sys.platform == 'win32':
        try:
            import ctypes
            hwnd = int(widget.winId())
            ctypes.windll.user32.InvalidateRect(hwnd, None, True)
            ctypes.windll.user32.UpdateWindow(hwnd)
        except Exception:
            pass
    # ── 强刷 DropShadow halo bitmap ──
    # PySide6 6.11.1 + DWM 分层合成下，setGraphicsEffect(new) + repaint 偶尔
    # 让 DropShadow 的 halo bitmap 处于「已挂载未渲染」状态，必须靠真实
    # resizeEvent 触发 paintEvent 才能让 halo 真正重画（实测 2026-08-20）。
    # 临时 nudge 1px 几何再回原位（顶层 widget 才有效；最大化 / 全屏跳过）。
    _post_glow_kick(widget)
    return True


def _post_glow_kick(widget):
    """强刷 DropShadow halo bitmap：临时 nudge 1px 几何再回原位。"""
    try:
        from PySide6.QtCore import Qt
        # 仅 top-level window 起作用（普通子 widget 几何 nudge 不会触发 native paint）
        if not widget.isWindow():
            return
        if widget.isMaximized() or widget.isFullScreen():
            return
        cur = widget.geometry()
        widget.setGeometry(cur.adjusted(0, 0, 0, 1))
        widget.setGeometry(cur)
        widget.repaint()
    except Exception:
        pass


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
