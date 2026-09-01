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
  - 主窗口切换主题后通过 ``Super_ADB_Win._传播主题到弹窗``
    把新主题同步到已打开的弹窗
- 关闭按钮 hover 红色是跨主题通用视觉提示（不跟主题），其余一律吃主题色
"""

import os
import sys

from ui import png_rc  # noqa: F401   # 注册 :/Super_ADB.png 与 :/qrcode.jpg 资源
from PySide6.QtCore import Qt, QPoint, QRectF
from PySide6.QtGui import QFont, QPixmap, QPainter, QColor, QIcon, QPen, QBrush, QPainterPath
from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QWidget, QGraphicsDropShadowEffect, QSizePolicy, QApplication)

from ui.ui_styles import FONT_FAMILY, THEMES, DEFAULT_THEME, _parse_rgb
from ui.dialog_styles import 无边框缩放Mixin
from tools.adb_tools import 加载json配置

VERSION = 'v2026.08.07'
GITHUB_REPO_URL = 'https://github.com/17602121645/Super_ADB.git'

# 向后兼容：目录/文件改英文名之前的旧打包产物仍是中文路径，按序回退查找。
_BUILD_INFO_CANDIDATES = (
    ('config', 'build_info.json'),
    ('config', '打包信息.json'),
    ('配置', 'build_info.json'),
    ('配置', '打包信息.json'),
)


def _获取版本号():
    """从 exe 旁边的 build_info.json 读取打包时间作为版本号，缺失时回退到硬编码 VERSION。

    跨平台路径：
      - Windows/Linux frozen: <exe_dir>/config/build_info.json
      - macOS frozen:          <.app>/Contents/MacOS/config/build_info.json
      - 源码模式:               项目根/config/build_info.json
    旧中文路径（配置/打包信息.json）仍作为回退候选。
    注意：不使用 加载json配置()，因为 macOS 上该函数指向 ~/Library/Application Support/，
    而打包信息在 .app 包内。
    """
    import json as _json
    try:
        if getattr(sys, 'frozen', False):
            # 打包模式：配置文件在可执行文件旁边
            _base = os.path.dirname(sys.executable)
        else:
            # 源码模式：本文件位于 对话框/ 下，配置在项目根
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _info_path = ''
        for _dir, _fn in _BUILD_INFO_CANDIDATES:
            _cand = os.path.join(_base, _dir, _fn)
            if os.path.exists(_cand):
                _info_path = _cand
                break
        if os.path.exists(_info_path):
            with open(_info_path, 'r', encoding='utf-8') as _f:
                info = _json.load(_f)
            if isinstance(info, dict) and info.get('打包时间'):
                return info['打包时间']
    except Exception:
        pass
    return VERSION


def _获取下载地址():
    """从 exe 旁边的 build_info.json 读取新版下载地址，缺失时返回空字符串。"""
    import json as _json
    try:
        if getattr(sys, 'frozen', False):
            _base = os.path.dirname(sys.executable)
        else:
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _info_path = ''
        for _dir, _fn in _BUILD_INFO_CANDIDATES:
            _cand = os.path.join(_base, _dir, _fn)
            if os.path.exists(_cand):
                _info_path = _cand
                break
        if os.path.exists(_info_path):
            with open(_info_path, 'r', encoding='utf-8') as _f:
                info = _json.load(_f)
            if isinstance(info, dict) and info.get('下载地址'):
                return info['下载地址']
    except Exception:
        pass
    return ''


# ----------------------------------------------------------------------
# 关于弹窗 QSS 模板（主题切换时用 str.format() 填充）
# ----------------------------------------------------------------------
关于弹窗样式模板 = """
#aboutDialog {{ background: transparent; border: none; }}
#aboutCard {{ background: transparent; border: none; }}
QLabel {{ background: transparent; border: none; color: {text_primary}; font-family: '{font}'; }}
QPushButton#closeBtn {{ background-color: transparent; color: {text_disabled}; border: none;
    border-radius: 6px; font: 14px 'Segoe UI','{font}'; min-width: 28px; min-height: 22px; }}
QPushButton#closeBtn:hover {{ background-color: #e81123; color: #ffffff; }}
QPushButton#closeBtn:pressed {{ background-color: #b0091a; color: #ffffff; }}
QPushButton#okBtn {{ font: 700 10pt '{font}'; color: {accent}; background-color: {bg_button};
    border: 1px solid {accent}; border-radius: 8px; padding: 8px 28px; }}
QPushButton#okBtn:hover {{ background-color: {accent}; color: {text_pressed}; }}
QPushButton#okBtn:pressed {{ background-color: rgba({r},{g},{b},180); color: {text_pressed}; }}
QLabel#aboutTitle {{ color: {accent}; font: 700 11pt '{font}'; }}
QLabel#aboutQr {{ background-color: #ffffff; border: 2px solid {accent}; border-radius: 10px; padding: 6px; }}
QLabel#aboutRepo {{ color: {accent}; font: 9pt '{font}'; background: transparent; }}
QLabel#aboutRepo a {{ color: {accent}; text-decoration: none; }}
QLabel#aboutRepo a:hover {{ text-decoration: underline; }}
QLabel#aboutDownload {{ color: {text_disabled}; font: 9pt '{font}'; background: transparent; }}
QLabel#aboutDownload a {{ color: {text_disabled}; text-decoration: none; }}
QLabel#aboutDownload a:hover {{ text-decoration: underline; }}
"""


class 关于对话框(QDialog, 无边框缩放Mixin):
    """带自定义标题栏的圆角关于弹窗，跟随主窗口主题，支持边缘缩放。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName('aboutDialog')  # QSS 选择器稳定锚点（与 无线调试对话框 同款）
        self.setMinimumSize(360, 480)
        self.resize(440, 620)
        self.setWindowTitle('关于 Super_ADB')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))

        # paintEvent 绘制背景所需的颜色成员（apply_theme 会覆盖；此处给兜底默认值，
        # 防止极早期 paintEvent 触发时 _bg_* 尚未赋值）
        self._bg_window = QColor('#080808')
        self._bg_border = QColor(0, 255, 128)
        self._bg_radius = 14

        # 容器（圆角卡片）—— 现在仅作为布局容器；bg/border/border-radius 全部挂在 QDialog 本体
        # （见 apply_theme 的 self.setStyleSheet），与无线调试对话框同款，主题切换时靠 QSS 级联
        # 自动刷新所有子控件，避开 WA_TranslucentBackground + DropShadowEffect 下 QWidget
        # 样式 cache 不失效的陷阱。
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

        # 版本号（次要文字，从配置文件读取打包时间）
        self.version_lbl = QLabel(f'版本号：{_获取版本号()}')
        self.version_lbl.setAlignment(Qt.AlignCenter)
        content.addWidget(self.version_lbl)

        # 新版下载地址（从配置文件读取，样式同版本号）
        _dl_url = _获取下载地址()
        if _dl_url:
            self.download_lbl = QLabel(f'<a href="{_dl_url}">新版下载地址：{_dl_url}</a>')
            self.download_lbl.setObjectName('aboutDownload')
            self.download_lbl.setAlignment(Qt.AlignCenter)
            self.download_lbl.setOpenExternalLinks(True)
            self.download_lbl.setWordWrap(True)
            content.addWidget(self.download_lbl)
        else:
            self.download_lbl = None

        content.addStretch()

        # GitHub 仓库地址（可点击跳转，样式同开源链接）
        self.github_lbl = QLabel(f'<a href="{GITHUB_REPO_URL}">GitHub 仓库：{GITHUB_REPO_URL}</a>')
        self.github_lbl.setObjectName('aboutRepo')
        self.github_lbl.setAlignment(Qt.AlignCenter)
        self.github_lbl.setOpenExternalLinks(True)
        self.github_lbl.setWordWrap(True)
        content.addWidget(self.github_lbl)

        # 底部按钮
        self.ok_btn = QPushButton('知道了')
        self.ok_btn.setObjectName('okBtn')
        self.ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_btn.clicked.connect(self.accept)
        content.addWidget(self.ok_btn, alignment=Qt.AlignCenter)

        content_widget = QWidget()
        content_widget.setLayout(content)
        layout.addWidget(content_widget)

        # 外发光由 paintEvent 直接绘制（不使用 QGraphicsDropShadowEffect）。
        # 原因：WA_TranslucentBackground + effect 挂在弹窗自身时，Qt 离屏缓冲区
        # 会缓存旧帧，导致主题切换后 paintEvent 用新色绘制但屏幕仍显示旧帧。
        # paintEvent 直接绘制发光，整个渲染在同一 paintEvent 完成，无缓存问题。

        # ── 应用当前主题（默认从父窗口读，缺省走 DEFAULT_THEME） ──
        self._current_theme_id = self._resolve_theme(None)
        self.apply_theme(self._current_theme_id)

        # 拖拽状态
        self._dragging = False
        self._drag_pos = QPoint()

        # 边缘缩放（所有子控件创建完毕后初始化）
        self._初始化缩放(边距=8)

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
        """按主题重算颜色并刷新样式。

        外发光由 paintEvent 直接绘制（不使用 QGraphicsDropShadowEffect），
        因此无离屏缓冲区缓存问题，setStyleSheet + repaint 即可正确刷新。
        """
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

        # paintEvent 绘制背景+边框所需的颜色成员
        self._bg_window = QColor(bg_window)
        self._bg_border = QColor(r, g, b)
        self._bg_radius = 14

        # 子控件 QSS（用模板填充）
        self.setStyleSheet(关于弹窗样式模板.format(
            accent=accent, r=r, g=g, b=b,
            bg_button=bg_button,
            text_primary=text_primary, text_pressed=text_pressed, text_disabled=text_disabled,
            font=FONT_FAMILY,
        ))

        # 单独覆盖文字类 QLabel（保留各自 QSS 的优先级）
        self.app_title.setStyleSheet(f"color: {text_primary}; font: 700 18pt '{FONT_FAMILY}';")
        self.sub_title.setStyleSheet(f"color: {text_disabled}; font: 10pt '{FONT_FAMILY}';")
        self.hint.setStyleSheet(f"color: {text_primary}; font: 9pt '{FONT_FAMILY}';")
        self.version_lbl.setStyleSheet(f"color: {text_disabled}; font: 9pt '{FONT_FAMILY}';")

        # 同步重绘（paintEvent 直接绘制发光+背景+边框，无离屏缓冲区缓存问题）
        self.update()
        self.repaint()

        print(f'[关于弹窗] apply_theme 完成: theme={tid}, accent=rgb({r},{g},{b})')

    def paintEvent(self, event):
        """用 QPainter 画外发光 + 圆角背景 + 主题色边框。

        外发光直接在 paintEvent 中绘制（多层同心圆角矩形模拟模糊），
        不使用 QGraphicsDropShadowEffect——避免 WA_TranslucentBackground 下
        离屏缓冲区缓存旧帧导致主题切换后屏幕不更新。
        颜色来自 apply_theme 写入的 self._bg_window / self._bg_border / self._bg_radius。
        """
        from PySide6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        r = self._bg_radius
        border_c = self._bg_border

        # ── 外发光：多层同心圆角矩形模拟高斯模糊（从外向内，透明度递增） ──
        # 共 6 层，每层间距 1px，透明度从 8 递增到 48，总扩展约 6px
        发光层数 = 6
        for i in range(发光层数, 0, -1):
            inset = 4 - i  # 从 4px 内容边距向外扩展 i 层
            glow_rect = QRectF(inset, inset, rect.width() - inset * 2, rect.height() - inset * 2)
            alpha = int(8 + (40 * (1 - i / 发光层数)))  # 8~48
            glow_path = QPainterPath()
            glow_path.addRoundedRect(glow_rect, r + (发光层数 - i), r + (发光层数 - i))
            painter.fillPath(glow_path, QBrush(QColor(border_c.red(), border_c.green(), border_c.blue(), alpha)))

        # ── 背景（圆角矩形） ──
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(4, 4, rect.width() - 8, rect.height() - 8), r, r)
        painter.fillPath(bg, QBrush(self._bg_window))

        # ── 边框（主题色，4px 宽） ──
        pen = QPen(self._bg_border, 4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(bg)
        painter.end()
        super().paintEvent(event)

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
    # 鼠标拖拽 + 边缘缩放
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):
        # 边缘缩放优先：在边缘热区则启动缩放，不进入拖拽
        if self._缩放按下处理(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        # 缩放中或边缘热区：由缩放逻辑处理（含光标更新）
        if self._缩放移动处理(event):
            return
        if self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._缩放释放处理(event):
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            event.accept()

    def resizeEvent(self, event):
        """缩放时同步 card 几何（card 用 setGeometry 固定位置，不会自动跟随布局）。"""
        super().resizeEvent(event)
        if hasattr(self, 'card'):
            self.card.setGeometry(10, 10, self.width() - 20, self.height() - 20)

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
    dlg = 关于对话框()
    dlg.show()
    app.exec()
