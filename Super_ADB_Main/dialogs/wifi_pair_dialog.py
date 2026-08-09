# -*- coding: utf-8 -*-
"""
WiFi 配对连接弹窗
================
用于 Android 11+ 无线调试的「配对码」流程：
- 在设备连接区点击「WiFi 配对」弹出本窗口
- 用户输入配对 IP、配对端口、6 位配对码（可直接粘贴「IP:端口」自动拆分）
- 后台执行 `adb pair IP:PORT CODE`
- 配对成功后提示用户，并可选直接 `adb connect IP:调试端口`

关键设计：
  - 所有 adb 命令都走后台线程，绝不阻塞 UI
  - 支持从剪贴板一键粘贴手机上显示的「IP:端口 配对码」
  - 配对成功后回填主窗口 ipInput，方便下一步 connect
"""

import re
import subprocess
import sys

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QIcon, QIntValidator, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QGroupBox, QMessageBox, QTextEdit,
    QSizePolicy,
)

import png_rc  # noqa: F401
from 界面样式 import ACCENT, FONT_FAMILY, STYLE_SHEET
from popup_style import add_green_glow


class _PairWorker(QObject):
    """后台执行 adb pair，避免 UI 卡住。"""

    done = Signal(bool, str)   # ok, message

    def __init__(self, ip, port, code, timeout=20):
        super().__init__()
        self._target = f"{ip}:{port}"
        self._code = code
        self._timeout = timeout
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            from adb_utils import AdbHelper
            adb = AdbHelper()
            cmd = [adb.adb_path, 'pair', self._target, self._code]
            cmd_str = adb._cmd_str(cmd)
            result = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                creationflags=0x08000000,
                shell=True,
            )
            out = (result.stdout or '').strip()
            err = (result.stderr or '').strip()
            combined = out or err or '无返回'
            ok = result.returncode == 0 and ('successfully paired' in combined.lower()
                                              or '配对成功' in combined
                                              or 'successfully' in combined.lower())
            self.done.emit(ok, combined)
        except subprocess.TimeoutExpired:
            self.done.emit(False, f"❌ 配对超时（{self._timeout}s）：手机可能未开启「使用配对码配对设备」或网络不通")
        except Exception as e:
            self.done.emit(False, f"❌ 配对异常：{e}")


class _ConnectWorker(QObject):
    """配对成功后后台 connect 调试端口。"""

    done = Signal(bool, str)

    def __init__(self, ip, port, timeout=12):
        super().__init__()
        self._target = f"{ip}:{port}"
        self._timeout = timeout

    def run(self):
        try:
            from adb_utils import AdbHelper
            result = AdbHelper().connect(self._target, timeout=self._timeout)
            ok = 'connected' in (result or '').lower() or 'already' in (result or '').lower()
            self.done.emit(ok, result or '无返回')
        except Exception as e:
            self.done.emit(False, f"❌ 连接失败：{e}")


class WifiPairDialog(QDialog):
    """WiFi 配对码连接对话框。"""

    def __init__(self, parent=None, on_pair_success=None):
        super().__init__(parent)
        self.setWindowTitle("WiFi 配对码连接")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        add_green_glow(self)

        # 配对成功后回调（主窗口用来刷新设备列表）
        self._on_pair_success = on_pair_success
        self._pair_thread = None
        self._pair_worker = None
        self._connect_thread = None
        self._connect_worker = None
        self._closing = False

        self._build_ui()
        self._apply_style()

    # ══════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 输入区 ──
        g = QGroupBox("配对信息（来自手机「无线调试 → 使用配对码配对设备」弹窗）")
        v = QVBoxLayout(g)

        # IP:端口 一行，支持自动拆分
        h1 = QHBoxLayout()
        h1.addWidget(QLabel("IP 地址："))
        self.ip_edit = QLineEdit()
        self.ip_edit.setPlaceholderText("例如 192.168.1.16")
        self.ip_edit.textChanged.connect(self._on_ip_text_changed)
        h1.addWidget(self.ip_edit)

        h1.addWidget(QLabel("配对端口："))
        self.port_edit = QLineEdit()
        self.port_edit.setPlaceholderText("例如 38973")
        self.port_edit.setMaximumWidth(100)
        self.port_edit.setValidator(QIntValidator(1, 65535, self))
        h1.addWidget(self.port_edit)
        v.addLayout(h1)

        # 配对码
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("配对码："))
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("6 位数字，例如 016813")
        re_validator = QRegularExpressionValidator(r"\d{0,6}", self)
        self.code_edit.setValidator(re_validator)
        self.code_edit.setMaxLength(6)
        h2.addWidget(self.code_edit)

        # 一键粘贴
        self.btn_paste = QPushButton("📋 粘贴")
        self.btn_paste.setToolTip("从剪贴板自动解析「IP:端口 配对码」")
        self.btn_paste.clicked.connect(self._paste_from_clipboard)
        h2.addWidget(self.btn_paste)
        v.addLayout(h2)

        # 调试端口（配对成功后用来 connect）
        h3 = QHBoxLayout()
        h3.addWidget(QLabel("调试端口："))
        self.debug_port_edit = QLineEdit()
        self.debug_port_edit.setPlaceholderText("手机「无线调试」页面显示的端口（默认 5555）")
        self.debug_port_edit.setValidator(QIntValidator(1, 65535, self))
        self.debug_port_edit.setText("5555")
        h3.addWidget(self.debug_port_edit)
        v.addLayout(h3)

        root.addWidget(g)

        # ── 操作按钮 ──
        h_btn = QHBoxLayout()
        self.btn_pair = QPushButton("🔑 开始配对")
        self.btn_pair.setProperty("class", "accentBtn")
        self.btn_pair.clicked.connect(self._start_pair)
        h_btn.addWidget(self.btn_pair)

        self.btn_connect = QPushButton("🔗 连接调试端口")
        self.btn_connect.setEnabled(False)
        self.btn_connect.clicked.connect(self._start_connect)
        h_btn.addWidget(self.btn_connect)

        self.btn_help = QPushButton("❓ 使用说明")
        self.btn_help.clicked.connect(self._show_help)
        h_btn.addWidget(self.btn_help)

        h_btn.addStretch()
        root.addLayout(h_btn)

        # ── 结果输出 ──
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("配对结果会显示在这里…")
        self.output.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.output, 1)

        # ── 底部提示 ──
        self.status_lbl = QLabel("提示：手机需先开启「无线调试」并点击「使用配对码配对设备」")
        self.status_lbl.setStyleSheet(f"color: {ACCENT};")
        root.addWidget(self.status_lbl)

    def _apply_style(self):
        self.setStyleSheet(STYLE_SHEET)

    # ══════════════════════════════════════════════════════════
    # 自动解析
    # ══════════════════════════════════════════════════════════
    def _on_ip_text_changed(self, text):
        """用户在 IP 框里粘贴了「IP:端口」时自动拆分端口。"""
        text = text.strip()
        if ':' in text:
            ip, _, port = text.rpartition(':')
            ip = ip.strip()
            port = port.strip()
            if re.match(r"^\d{1,5}$", port):
                self.ip_edit.setText(ip)
                self.port_edit.setText(port)
                self.code_edit.setFocus()

    def _paste_from_clipboard(self):
        text = QApplication.clipboard().text().strip()
        if not text:
            return
        # 尝试解析形如：192.168.1.16:38973 016813
        # 或：IP 地址和端口 192.168.1.16:38973\nWLAN 配对码 016813
        lines = text.replace('\r\n', '\n').split('\n')
        combined = ' '.join(lines)

        # 找 IP:端口
        m_ip = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", combined)
        # 找 6 位配对码（允许空格分隔）
        m_code = re.search(r"(?:配对码|code)[:：\s]*(\d{6})|(?:^|\s)(\d{6})(?:\s|$)",
                           combined, re.IGNORECASE)

        if m_ip:
            self.ip_edit.setText(m_ip.group(1))
            self.port_edit.setText(m_ip.group(2))
        if m_code:
            code = m_code.group(1) or m_code.group(2)
            self.code_edit.setText(code)
        if not m_ip and not m_code:
            self._log("剪贴板内容无法自动解析，请手动填写")

    # ══════════════════════════════════════════════════════════
    # 配对
    # ══════════════════════════════════════════════════════════
    def _start_pair(self):
        ip = self.ip_edit.text().strip()
        port = self.port_edit.text().strip()
        code = self.code_edit.text().strip()

        if not ip:
            QMessageBox.warning(self, "缺少 IP", "请输入手机的 IP 地址")
            return
        if not port or not port.isdigit():
            QMessageBox.warning(self, "缺少端口", "请输入配对端口（手机弹窗中显示的端口）")
            return
        if not re.match(r"^\d{6}$", code):
            QMessageBox.warning(self, "配对码格式错误", "配对码应为 6 位数字")
            return

        self._set_buttons_busy(True)
        self.output.clear()
        self._log(f"$ adb pair {ip}:{port} {code}")
        self.status_lbl.setText(f"正在配对 {ip}:{port} …")

        self._pair_worker = _PairWorker(ip, int(port), code, timeout=20)
        self._pair_thread = QThread(self)
        self._pair_worker.moveToThread(self._pair_thread)
        self._pair_thread.started.connect(self._pair_worker.run)
        self._pair_worker.done.connect(self._on_pair_done)
        self._pair_thread.start()

    def _on_pair_done(self, ok, msg):
        self._set_buttons_busy(False)
        self._log(msg)
        if ok:
            self.status_lbl.setText("✅ 配对成功，现在可以点击「连接调试端口」建立调试连接")
            self.btn_connect.setEnabled(True)
            if self._on_pair_success:
                try:
                    self._on_pair_success()
                except Exception:
                    pass
        else:
            self.status_lbl.setText(f"❌ {msg[:80]}")

    # ══════════════════════════════════════════════════════════
    # 连接调试端口
    # ══════════════════════════════════════════════════════════
    def _start_connect(self):
        ip = self.ip_edit.text().strip()
        port = self.debug_port_edit.text().strip() or "5555"
        if not ip:
            QMessageBox.warning(self, "缺少 IP", "请先填写 IP 地址")
            return

        self._set_buttons_busy(True)
        self._log(f"$ adb connect {ip}:{port}")
        self.status_lbl.setText(f"正在连接 {ip}:{port} …")

        self._connect_worker = _ConnectWorker(ip, int(port), timeout=12)
        self._connect_thread = QThread(self)
        self._connect_worker.moveToThread(self._connect_thread)
        self._connect_thread.started.connect(self._connect_worker.run)
        self._connect_worker.done.connect(self._on_connect_done)
        self._connect_thread.start()

    def _on_connect_done(self, ok, msg):
        self._set_buttons_busy(False)
        self._log(msg)
        if ok:
            self.status_lbl.setText(f"✅ 已连接 {msg}")
            QMessageBox.information(self, "连接成功",
                                    f"{msg}\n\n主窗口设备列表将自动刷新。")
            self.accept()
        else:
            self.status_lbl.setText(f"❌ {msg[:80]}")
            QMessageBox.warning(self, "连接失败",
                                f"{msg}\n\n请确认手机「无线调试」页面显示的调试端口已正确填写。")

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════
    def _log(self, text):
        self.output.append(text)

    def _set_buttons_busy(self, busy):
        self.btn_pair.setEnabled(not busy)
        self.btn_connect.setEnabled(not busy and self.btn_connect.isEnabled())
        self.btn_paste.setEnabled(not busy)
        QApplication.processEvents()

    def _show_help(self):
        QMessageBox.information(
            self, "WiFi 配对码连接说明",
            "1. 在手机上打开「设置 → 开发者选项 → 无线调试」\n"
            "2. 点击「使用配对码配对设备」，会弹出一个 6 位配对码和 IP:端口\n"
            "3. 在本窗口输入：\n"
            "   • IP 地址：例如 192.168.1.16\n"
            "   • 配对端口：弹窗里显示的端口（例如 38973）\n"
            "   • 配对码：6 位数字（例如 016813）\n"
            "4. 点击「开始配对」\n"
            "5. 配对成功后，再点击「连接调试端口」\n\n"
            "提示：也可以直接复制手机弹窗里的内容，点击「📋 粘贴」自动解析。")

    def closeEvent(self, event):
        self._closing = True
        for w in (self._pair_worker, self._connect_worker):
            if w is not None:
                try:
                    w.cancel()
                except Exception:
                    pass
        for t in (self._pair_thread, self._connect_thread):
            if t is not None and t.isRunning():
                t.quit()
                t.wait(2000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = WifiPairDialog()
    dlg.show()
    sys.exit(app.exec())
