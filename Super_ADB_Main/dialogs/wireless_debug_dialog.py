# -*- coding: utf-8 -*-
"""
统一无线调试面板
================
把原本分散的「局域网扫描」「WiFi 配对码连接」「二维码连接」三个入口
合并到同一个弹窗里，用 QTabWidget 分三个标签页，避免主界面按钮过多、入口分散。

实现要点：
  - LanScannerDialog / WifiPairDialog 都不调用 .show()，而是作为子控件嵌入标签页。
  - QrConnectPage 是 QWidget（非 QDialog），专门处理扫码和生成二维码。
  - 嵌入后它们不再是顶层窗口，closeEvent 不会触发，所以本面板的 closeEvent
    显式调用两者的 cleanup() 停掉后台扫描 / 连接 / 回填线程，避免悬挂进程。
  - WifiPairDialog 的配对成功回调会被转发为 on_pair_success(ip, port)，
    方便主窗口把 IP:端口 填回连接输入框并刷新设备列表。
  - QrConnectPage 持有 pair_dialog 引用，扫码结果可一键填入配对页。
"""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QPushButton,
    QScrollArea,
)

import png_rc  # noqa: F401
from popup_style import add_green_glow, ACCENT_CSS
from lan_scanner_dialog import LanScannerDialog
from wifi_pair_dialog import WifiPairDialog
from qr_connect_page import QrConnectPage

# ── 标签页样式：深色 + 青绿强调色，圆角卡片 + 选中下划线 ──
TAB_STYLE = f"""
    QTabWidget::pane {{
        border: 1px solid rgba(29,233,182,120);
        border-radius: 10px;
        top: -1px;
        background-color: #202020;
    }}
    QTabBar::tab {{
        background-color: #2b2b2b;
        color: #a8a8a8;
        border: 1px solid #3a3a3a;
        border-top-left-radius: 9px;
        border-top-right-radius: 9px;
        padding: 9px 20px;
        margin-right: 4px;
        font: 400 10pt "微软雅黑";
        min-height: 24px;
    }}
    QTabBar::tab:selected {{
        background-color: rgba(29,233,182,26);
        color: {ACCENT_CSS};
        border: 1px solid {ACCENT_CSS};
        border-bottom: 2px solid {ACCENT_CSS};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: #343434;
        color: #e0e0e0;
    }}
    QTabBar::tab:first {{
        margin-left: 4px;
    }}
    QTabWidget::tab-bar {{
        left: 4px;
    }}
    /* 顶部小高亮条，强化「当前页」的视觉锚点 */
    QTabBar::tab:selected {{
        background-color: rgba(29,233,182,32);
    }}
"""


class WirelessDebugDialog(QDialog):
    """统一无线调试入口：局域网扫描 + 配对码连接 + 二维码连接。

    参数:
      - on_pair_success(ip, port): 配对码页 / 二维码页「配对成功后」触发，仅刷新设备列表并把 IP:端口
        填回主窗口输入框。
      - on_device_connected(serial): 局域网扫描里「adb connect 成功后」触发（serial 形如 "IP:PORT"），
        让主窗口把刚连上的设备选中并刷新三处设备下拉框。
    """

    def __init__(self, parent=None, on_pair_success=None, on_device_connected=None):
        super().__init__(parent)
        self.setWindowTitle("无线调试")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        # 只保留最小宽度限制，高度由内容自然决定；嵌入子页会再重置其自身的最小尺寸
        self.setMinimumWidth(560)
        # 默认打开给足高度，避免一弹出就滚动；用户仍可继续缩到很小
        self.resize(820, 680)
        add_green_glow(self)

        self._lan_dialog = None
        self._pair_dialog = None
        self._qr_page = None
        self._on_device_connected = on_device_connected

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.tab = QTabWidget()
        self.tab.setStyleSheet(TAB_STYLE)
        root.addWidget(self.tab, 1)

        # ── 标签页 1：局域网扫描 ──
        self._lan_dialog = LanScannerDialog(
            parent=self, on_device_connected=self._on_device_connected)
        # 嵌入后不要让其独立窗口的最小尺寸限制整个 QTabWidget
        self._lan_dialog.setMinimumSize(0, 0)
        self.tab.addTab(self._lan_dialog, "📡 局域网扫描")

        # ── 标签页 2：配对码连接 ──
        def _pair_cb():
            if callable(on_pair_success):
                ip = self._pair_dialog.ip_edit.text().strip()
                port = self._pair_dialog.debug_port_edit.text().strip() or '5555'
                on_pair_success(ip, port)

        self._pair_dialog = WifiPairDialog(parent=self, on_pair_success=_pair_cb)
        self._pair_dialog._embedded = True
        self._pair_dialog.setMinimumSize(0, 0)
        # 配对页同样套滚动容器，避免把整窗最小高度撑死
        pair_scroll = QScrollArea()
        pair_scroll.setWidgetResizable(True)
        pair_scroll.setWidget(self._pair_dialog)
        pair_scroll.setFrameShape(QScrollArea.NoFrame)
        self.tab.addTab(pair_scroll, "🔑 配对码连接")

        # ── 标签页 3：二维码连接 ──
        self._qr_page = QrConnectPage(
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
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        h.addWidget(close_btn)
        root.addLayout(h)

    def cleanup(self):
        """停掉两个子对话框的后台线程（嵌入时 closeEvent 不会触发它们）。"""
        if self._lan_dialog is not None:
            try:
                self._lan_dialog.cleanup()
            except Exception:
                pass
        if self._pair_dialog is not None:
            try:
                self._pair_dialog.cleanup()
            except Exception:
                pass
        if self._qr_page is not None:
            try:
                self._qr_page.cleanup()
            except Exception:
                pass

    def closeEvent(self, event):
        self.cleanup()
        event.accept()
