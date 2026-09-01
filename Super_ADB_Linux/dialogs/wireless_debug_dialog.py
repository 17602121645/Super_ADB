# -*- coding: utf-8 -*-
"""
统一无线调试面板
================
把原本分散的「局域网扫描」「WiFi 配对码连接」「二维码连接」三个入口
合并到同一个弹窗里，用 QTabWidget 分三个标签页，避免主界面按钮过多、入口分散。

实现要点：
  - 局域网扫描对话框 / WiFi配对对话框 都不调用 .show()，而是作为子控件嵌入标签页。
  - 二维码连接页 是 QWidget（非 QDialog），专门处理扫码和生成二维码。
  - 嵌入后它们不再是顶层窗口，closeEvent 不会触发，所以本面板的 closeEvent
    显式调用两者的 清理() 停掉后台扫描 / 连接 / 回填线程，避免悬挂进程。
  - WiFi配对对话框 的配对成功回调会被转发为 on_pair_success(ip, port)，
    方便主窗口把 IP:端口 填回连接输入框并刷新设备列表。
  - 二维码连接页 持有 pair_dialog 引用，扫码结果可一键填入配对页。
"""

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QIcon, QColor, QPainter, QPen, QBrush, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QPushButton,
    QScrollArea, QWidget, QSizePolicy,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import get_stylesheet, get_current_theme_id, THEMES, _parse_rgb as _parse_rgb_local
from ui.dialog_styles import 无边框缩放Mixin
from dialogs.lan_scan_dialog import 局域网扫描对话框
from dialogs.wifi_pair_dialog import WiFi配对对话框
from dialogs.qrcode_connect_page import 二维码连接页


def _tab_style(theme_id):
    """按当前主题生成 QTabWidget 标签页样式。"""
    t = THEMES.get(theme_id, THEMES['dark_cyan'])
    accent = t['accent']
    c = QColor(accent)
    r, g, b = c.red(), c.green(), c.blue()
    return f"""
        QTabWidget {{ background: transparent; border: none; }}
        QTabBar {{ background: transparent; }}
        QTabWidget::pane {{
            border: 1px solid {accent};
            border-top-left-radius: 0px;
            border-top-right-radius: 10px;
            border-bottom-left-radius: 10px;
            border-bottom-right-radius: 10px;
            background-color: transparent;
        }}
        QTabBar::tab {{
            background-color: transparent;
            color: {t['text_disabled']};
            border: 1px solid transparent;
            border-bottom: none;
            border-top-left-radius: 9px;
            border-top-right-radius: 9px;
            padding: 9px 20px;
            margin-right: 4px;
            font: 400 10pt "微软雅黑";
            min-height: 24px;
        }}
        QTabBar::tab:selected {{
            background-color: {t['bg_window']};
            color: {accent};
            border-top: 1px solid {accent};
            border-left: 1px solid {accent};
            border-right: 1px solid {accent};
            border-bottom: none;
        }}
        QTabBar::tab:hover:!selected {{
            background-color: {t['bg_combo']};
            color: {t['text_primary']};
        }}
    """


class 无线调试对话框(QDialog, 无边框缩放Mixin):
    """统一无线调试入口：局域网扫描 + 配对码连接 + 二维码连接，支持边缘缩放。

    参数:
      - on_pair_success(ip, port): 配对码页 / 二维码页「配对成功后」触发，仅刷新设备列表并把 IP:端口
        填回主窗口输入框。
      - on_device_connected(serial): 局域网扫描里「adb connect 成功后」触发（serial 形如 "IP:PORT"），
        让主窗口把刚连上的设备选中并刷新三处设备下拉框。
    """

    def __init__(self, parent=None, on_pair_success=None, on_device_connected=None, adb=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName('wirelessDialog')
        self.setWindowTitle("无线调试")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(560)
        self.resize(820, 680)

        self._adb = adb  # 主窗口的 AdbHelper 实例，复用自研adb连接缓存

        self._theme_id = get_current_theme_id(self)

        # paintEvent 绘制背景+边框+发光所需的颜色成员（按当前主题初始化，避免启动时
        # 边框/背景停留在硬编码的 dark_neon 绿色，与当前主题不一致）
        _t0 = THEMES.get(self._theme_id, THEMES['dark_cyan'])
        _r0, _g0, _b0 = _parse_rgb_local(_t0['accent'])
        self._bg_window = QColor(_t0['bg_window'])
        self._bg_border = QColor(_r0, _g0, _b0)
        self._bg_radius = 14

        self._lan_dialog = None
        self._pair_dialog = None
        self._qr_page = None
        self._设备连接时 = on_device_connected

        # 拖拽状态
        self._dragging = False
        self._drag_pos = QPoint()

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # ── 自定义标题栏 ──
        title_bar = QHBoxLayout()
        title_bar.setContentsMargins(8, 4, 8, 4)
        title_bar.setSpacing(6)
        self.title_lbl = QLabel('无线调试')
        self.title_lbl.setObjectName('wirelessTitle')
        self.title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        title_bar.addWidget(self.title_lbl)
        close_btn = QPushButton('✕')
        close_btn.setObjectName('closeBtn')
        close_btn.setFixedSize(28, 22)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.close)
        title_bar.addWidget(close_btn)
        title_widget = QWidget()
        title_widget.setLayout(title_bar)
        root.addWidget(title_widget)

        # 全局样式（弹窗自身背景透明，由 paintEvent 绘制）
        self.setStyleSheet(self._build_qss(self._theme_id))

        self.tab = QTabWidget()
        self.tab.setStyleSheet(_tab_style(self._theme_id))
        root.addWidget(self.tab, 1)

        # ── 标签页 1：局域网扫描 ──
        self._lan_dialog = 局域网扫描对话框(
            parent=self, on_device_connected=self._设备连接时, adb=self._adb)
        # 嵌入后不要让其独立窗口的最小尺寸限制整个 QTabWidget
        self._lan_dialog.setMinimumSize(0, 0)
        self.tab.addTab(self._lan_dialog, "📡 局域网扫描")

        # ── 标签页 2：配对码连接 ──
        def _pair_cb():
            if callable(on_pair_success):
                ip = self._pair_dialog.ip_edit.text().strip()
                port = self._pair_dialog.debug_port_edit.text().strip() or '5555'
                on_pair_success(ip, port)

        self._pair_dialog = WiFi配对对话框(parent=self, on_pair_success=_pair_cb)
        self._pair_dialog.set_embedded(True)
        self._pair_dialog.setMinimumSize(0, 0)
        # 配对页直接嵌入标签页（与局域网扫描一致），QDialog 套 QScrollArea 会导致空白
        self.tab.addTab(self._pair_dialog, "🔑 配对码连接")

        # ── 标签页 3：二维码连接 ──
        self._qr_page = 二维码连接页(
            parent=self, pair_dialog=self._pair_dialog,
            on_pair_success=on_pair_success)
        # 二维码页内容多，用滚动容器包裹，避免把整窗最小高度撑死
        qr_scroll = QScrollArea()
        qr_scroll.setWidgetResizable(True)
        qr_scroll.setWidget(self._qr_page)
        qr_scroll.setFrameShape(QScrollArea.NoFrame)
        self.tab.addTab(qr_scroll, "🔳 二维码连接")

        # ── 底部按钮 ──
        h = QHBoxLayout()
        h.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("closeBtn2")
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)
        root.addLayout(h)

        # 边缘缩放（所有子控件创建完毕后初始化）
        self._初始化缩放(边距=8)

    def _build_qss(self, theme_id):
        """构建弹窗 QSS：弹窗自身背景透明（paintEvent 绘制），子控件吃全局样式。"""
        t = THEMES.get(theme_id, THEMES['dark_cyan'])
        accent = t['accent']
        text_primary = t['text_primary']
        text_disabled = t['text_disabled']
        base = get_stylesheet(theme_id)
        return f"""
            #wirelessDialog {{ background: transparent; border: none; }}
            #wirelessTitle {{ color: {accent}; font: 700 11pt '微软雅黑'; background: transparent; border: none; }}
            QPushButton#closeBtn {{ background-color: transparent; color: {text_disabled}; border: none;
                border-radius: 6px; font: 14px 'Segoe UI','微软雅黑'; min-width: 28px; min-height: 22px; }}
            QPushButton#closeBtn:hover {{ background-color: #e81123; color: #ffffff; }}
            QPushButton#closeBtn:pressed {{ background-color: #b0091a; color: #ffffff; }}
            QPushButton#closeBtn2 {{ background-color: {t['bg_button']}; color: {accent};
                border: 1px solid {accent}; border-radius: 8px; padding: 8px 28px; font: 700 10pt '微软雅黑'; }}
            QPushButton#closeBtn2:hover {{ background-color: {accent}; color: {t['text_pressed']}; }}
            {base}
        """

    def apply_theme(self, theme_id):
        """运行时切换主题：更新颜色成员 → 重刷 QSS → 标签页 → 向子页面传播 → repaint。"""
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self._theme_id = theme_id
        t = THEMES[theme_id]
        r, g, b = _parse_rgb_local(t['accent'])
        # paintEvent 颜色成员
        self._bg_window = QColor(t['bg_window'])
        self._bg_border = QColor(r, g, b)
        self._bg_radius = 14
        # QSS + 标签页样式
        self.setStyleSheet(self._build_qss(theme_id))
        self.tab.setStyleSheet(_tab_style(theme_id))
        # 向三个嵌入子页面传播主题
        for 子页 in (self._lan_dialog, self._pair_dialog, self._qr_page):
            if 子页 is not None:
                apply = getattr(子页, 'apply_theme', None)
                if callable(apply):
                    try:
                        apply(theme_id)
                    except Exception as e:
                        print(f'[主题] 无线调试子页 {type(子页).__name__} 同步失败: {e!r}')
        self.update()
        self.repaint()

    def paintEvent(self, event):
        """用 QPainter 画外发光 + 圆角背景 + 主题色边框（与关于/环境配置弹窗同款）。"""
        from PySide6.QtCore import QRectF
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        r = self._bg_radius
        border_c = self._bg_border
        # 外发光：6 层同心圆角矩形模拟高斯模糊
        发光层数 = 6
        for i in range(发光层数, 0, -1):
            inset = 4 - i
            glow_rect = QRectF(inset, inset, rect.width() - inset * 2, rect.height() - inset * 2)
            alpha = int(8 + (40 * (1 - i / 发光层数)))
            glow_path = QPainterPath()
            glow_path.addRoundedRect(glow_rect, r + (发光层数 - i), r + (发光层数 - i))
            painter.fillPath(glow_path, QBrush(QColor(border_c.red(), border_c.green(), border_c.blue(), alpha)))
        # 背景
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(4, 4, rect.width() - 8, rect.height() - 8), r, r)
        painter.fillPath(bg, QBrush(self._bg_window))
        # 边框
        pen = QPen(self._bg_border, 4)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(bg)
        painter.end()
        super().paintEvent(event)

    # ── 无边框窗口拖拽 + 边缘缩放 ──
    def mousePressEvent(self, event):
        # 边缘缩放优先：在边缘热区则启动缩放，不进入拖拽
        if self._缩放按下处理(event):
            return
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 50:
            self._dragging = True
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        # 缩放中或边缘热区：由缩放逻辑处理（含光标更新）
        if self._缩放移动处理(event):
            return
        if self._dragging and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._缩放释放处理(event):
            return
        self._dragging = False
        super().mouseReleaseEvent(event)

    def 清理(self):
        """停掉两个子对话框的后台线程（嵌入时 closeEvent 不会触发它们）。"""
        if self._lan_dialog is not None:
            try:
                self._lan_dialog.清理()
            except Exception:
                pass
        if self._pair_dialog is not None:
            try:
                self._pair_dialog.清理()
            except Exception:
                pass
        if self._qr_page is not None:
            try:
                self._qr_page.清理()
            except Exception:
                pass

    def closeEvent(self, event):
        self.清理()
        event.accept()
