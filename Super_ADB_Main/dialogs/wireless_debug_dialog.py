# -*- coding: utf-8 -*-
"""
统一无线调试面板
================
把原本分散的「局域网扫描」与「WiFi 配对码连接」两个入口合并到同一个弹窗里，
用 QTabWidget 分两个标签页，避免主界面按钮过多、入口分散。

实现要点：
  - LanScannerDialog / WifiPairDialog 都不调用 .show()，而是作为子控件嵌入标签页。
  - 嵌入后它们不再是顶层窗口，closeEvent 不会触发，所以本面板的 closeEvent
    显式调用两者的 cleanup() 停掉后台扫描 / 连接 / 回填线程，避免悬挂进程。
  - WifiPairDialog 的配对成功回调会被转发为 on_pair_success(ip, port)，
    方便主窗口把 IP:端口 填回连接输入框并刷新设备列表。
"""

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QLabel, QPushButton,
)

import png_rc  # noqa: F401
from popup_style import add_green_glow
from lan_scanner_dialog import LanScannerDialog
from wifi_pair_dialog import WifiPairDialog


class WirelessDebugDialog(QDialog):
    """统一无线调试入口：局域网扫描 + WiFi 配对码连接。"""

    def __init__(self, parent=None, on_pair_success=None):
        super().__init__(parent)
        self.setWindowTitle("无线调试")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(720)
        self.setMinimumHeight(520)
        add_green_glow(self)

        self._lan_dialog = None
        self._pair_dialog = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        tip = QLabel(
            "「局域网扫描」用于发现同网段已开启 ADB 的设备；"
            "「配对码连接」用于 Android 11+ 无线调试配对码绑定。两者共用底部「关闭」。")
        tip.setWordWrap(True)
        root.addWidget(tip)

        self.tab = QTabWidget()
        root.addWidget(self.tab, 1)

        # ── 标签页 1：局域网扫描 ──
        self._lan_dialog = LanScannerDialog(parent=self)
        self.tab.addTab(self._lan_dialog, "局域网扫描")

        # ── 标签页 2：配对码连接 ──
        def _pair_cb():
            if callable(on_pair_success):
                ip = self._pair_dialog.ip_edit.text().strip()
                port = self._pair_dialog.debug_port_edit.text().strip() or '5555'
                on_pair_success(ip, port)

        self._pair_dialog = WifiPairDialog(parent=self, on_pair_success=_pair_cb)
        self._pair_dialog._embedded = True
        self.tab.addTab(self._pair_dialog, "配对码连接")

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

    def closeEvent(self, event):
        self.cleanup()
        event.accept()
