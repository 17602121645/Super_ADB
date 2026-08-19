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
import time

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import (QIcon, QIntValidator, QRegularExpressionValidator,
                          QPixmap)
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QGroupBox, QMessageBox, QTextEdit,
    QSizePolicy, QCheckBox, QFileDialog, QWidget,
)

import png_rc  # noqa: F401
from 界面样式 import ACCENT, FONT_FAMILY, STYLE_SHEET, get_stylesheet, get_current_theme_id, THEMES
from popup_style import add_green_glow
from adb_utils import load_json_config, save_json_config

_PAIRED_CFG = 'wifi_paired_devices.json'      # 已配对设备指纹持久化
_HISTORY_CFG = 'wifi_debug_history.json'      # 配对/连接操作历史


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
                encoding='utf-8',
                errors='replace',
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
    """配对成功后后台 connect 调试端口，支持多候选端口逐个尝试。"""

    done = Signal(bool, str, list)  # ok, message, tried_ports

    def __init__(self, ip, ports, timeout=8):
        super().__init__()
        self._ip = ip
        self._ports = ports          # list[int]，按优先级排序
        self._timeout = timeout
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        from adb_utils import AdbHelper
        adb = AdbHelper()
        tried = []
        for port in self._ports:
            if self._cancelled:
                return
            target = f"{self._ip}:{port}"
            tried.append(port)
            try:
                result = adb.connect(target, timeout=self._timeout)
                ok = ('connected' in (result or '').lower()
                      or 'already' in (result or '').lower())
                if ok:
                    self.done.emit(True, result, tried)
                    return
            except Exception as e:
                result = f"连接 {target} 失败：{e}"
        # 所有端口都失败
        ports_str = ', '.join(str(p) for p in tried)
        self.done.emit(False, f"❌ 尝试端口 {ports_str} 均连接失败", tried)


class WifiPairDialog(QDialog):
    """WiFi 配对码连接对话框。"""

    def __init__(self, parent=None, on_pair_success=None):
        super().__init__(parent)
        self.setWindowTitle("WiFi 配对码连接")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(520)
        self.setMinimumHeight(380)
        self._theme_id = get_current_theme_id(self)
        self._accent = THEMES[self._theme_id]['accent']
        self.setStyleSheet(get_stylesheet(self._theme_id))
        add_green_glow(self)

        # 配对成功后回调（主窗口用来刷新设备列表）
        self._on_pair_success = on_pair_success
        self._pair_thread = None
        self._pair_worker = None
        self._connect_thread = None
        self._connect_worker = None
        self._reconnect_thread = None
        self._reconnect_worker = None
        self._paired = []
        self._closing = False
        self._connect_enabled = False   # 配对成功后才允许 connect
        self._embedded = False          # 被统一无线调试面板嵌入时设为 True

        self._build_ui()
        self._apply_style()
        self._refresh_paired_list()
        # 自动重连最近一台已配对设备（WiFi 重连后某些 ROM 调试端口仍有效）
        if self._paired:
            QTimer.singleShot(600, lambda: self._reconnect_saved(self._paired[0]))

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

        # 一键粘贴 + 扫码
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

        # 自动连接复选框
        self.chk_auto_connect = QCheckBox("配对成功后自动连接调试端口")
        self.chk_auto_connect.setChecked(True)
        self.chk_auto_connect.setToolTip("配对成功后自动执行 adb connect，一步到位")
        v.addWidget(self.chk_auto_connect)

        root.addWidget(g)

        # ── 已配对设备 ──
        self.paired_group = QGroupBox("已配对设备（自动保存，可一键重连）")
        self.paired_group_layout = QVBoxLayout(self.paired_group)
        self.paired_group_layout.setSpacing(4)
        self.paired_empty_lbl = QLabel("暂无已配对设备记录")
        self.paired_empty_lbl.setStyleSheet(f"color: {ACCENT};")
        self.paired_group_layout.addWidget(self.paired_empty_lbl)
        root.addWidget(self.paired_group)

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

        self.btn_history = QPushButton("📜 历史")
        self.btn_history.clicked.connect(self._show_history)
        h_btn.addWidget(self.btn_history)

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
        self.status_lbl.setStyleSheet(f"color: {self._accent};")
        root.addWidget(self.status_lbl)

    def _apply_style(self):
        self.setStyleSheet(get_stylesheet(self._theme_id))

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
            self._log("⚠️ 剪贴板为空")
            return
        lines = text.replace('\r\n', '\n').split('\n')
        combined = ' '.join(lines)

        # 找 IP:端口
        m_ip = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", combined)
        # 找 6 位配对码
        m_code = re.search(r"(?:配对码|code)[:：\s]*(\d{6})|(?:^|\s)(\d{6})(?:\s|$)",
                           combined, re.IGNORECASE)
        errors = []

        if m_ip:
            self.ip_edit.setText(m_ip.group(1))
            self.port_edit.setText(m_ip.group(2))
        else:
            m_ip_only = re.search(r"\d{1,3}(?:\.\d{1,3}){3}", combined)
            if m_ip_only:
                errors.append("找到了 IP 地址，但缺少端口号（格式应为 IP:端口，如 192.168.1.16:38973）")
            else:
                errors.append("未找到 IP:端口 格式（应为 192.168.x.x:xxxxx）")

        if m_code:
            code = m_code.group(1) or m_code.group(2)
            self.code_edit.setText(code)
        else:
            m_short = re.search(r"(?:配对码|code)[:：\s]*(\d{1,5})(?:\s|$)", combined, re.IGNORECASE)
            if m_short:
                n = len(m_short.group(1))
                errors.append(f"配对码只有 {n} 位？应为 6 位数字，请检查手机上显示的配对码")
            else:
                errors.append("未找到 6 位配对码")

        if errors:
            self._log("⚠️ " + "；".join(errors))
        else:
            self._log("✅ 已从剪贴板自动解析并填入")

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
        self._add_history('配对', f"{self.ip_edit.text().strip()}:{self.port_edit.text().strip()}", ok, msg)
        if ok:
            self.status_lbl.setText("✅ 配对成功")
            self._connect_enabled = True
            self.btn_connect.setEnabled(True)
            if self._on_pair_success:
                try:
                    self._on_pair_success()
                except Exception:
                    pass
            # 配对成功后自动连接
            if self.chk_auto_connect.isChecked():
                self._log("⟳ 配对成功，自动开始连接调试端口…")
                self._start_connect()
        else:
            self.status_lbl.setText(f"❌ {msg[:80]}")

    # ══════════════════════════════════════════════════════════
    # 连接调试端口
    # ══════════════════════════════════════════════════════════
    def _start_connect(self):
        ip = self.ip_edit.text().strip()
        if not ip:
            QMessageBox.warning(self, "缺少 IP", "请先填写 IP 地址")
            return

        # 构建候选端口列表（去重保序）
        user_port = self.debug_port_edit.text().strip() or "5555"
        pair_port = self.port_edit.text().strip()
        seen = set()
        ports = []
        for p in [user_port, "5555", pair_port, "37800"]:
            if p and p.isdigit() and p not in seen:
                seen.add(p)
                ports.append(int(p))

        self._set_buttons_busy(True)
        ports_str = ', '.join(str(p) for p in ports)
        self._log(f"$ adb connect {ip}:{ports[0]} （候选: {ports_str}）")
        self.status_lbl.setText(f"正在连接 {ip} …")

        self._connect_worker = _ConnectWorker(ip, ports, timeout=8)
        self._connect_thread = QThread(self)
        self._connect_worker.moveToThread(self._connect_thread)
        self._connect_thread.started.connect(self._connect_worker.run)
        self._connect_worker.done.connect(self._on_connect_done)
        self._connect_thread.start()

    def _on_connect_done(self, ok, msg, tried_ports):
        self._set_buttons_busy(False)
        self._log(msg)
        target = (f"{self.ip_edit.text().strip()}:{tried_ports[-1]}"
                  if tried_ports else self.ip_edit.text().strip())
        self._add_history('连接', target, ok, msg)
        if ok:
            # 成功时回填实际连接的端口
            if tried_ports:
                self.debug_port_edit.setText(str(tried_ports[-1]))
            self.status_lbl.setText(f"✅ 已连接 {msg[:60]}")
            self._log("✅ 连接成功，主窗口设备列表将自动刷新")
            # 保存配对记录（持久化，便于自动重连）
            ip = self.ip_edit.text().strip()
            self._save_current_pair(ip, tried_ports[-1], self.port_edit.text().strip())
            if self._on_pair_success:
                try:
                    self._on_pair_success()
                except Exception:
                    pass
            self.accept()
        else:
            tried_str = ', '.join(str(p) for p in tried_ports)
            self.status_lbl.setText(
                f"❌ 连接失败（尝试端口: {tried_str}）— 请确认手机「无线调试」页面显示的调试端口")
            self._log("💡 提示：调试端口是手机「无线调试」主页面显示的端口，"
                      "不是配对弹窗里的端口。两者通常不同。")

    # ══════════════════════════════════════════════════════════
    # 已配对设备持久化 + 自动重连
    # ══════════════════════════════════════════════════════════
    def _load_paired(self):
        data = load_json_config(_PAIRED_CFG)
        if isinstance(data, list):
            return data
        return []

    def _save_paired(self, paired):
        save_json_config(_PAIRED_CFG, paired)

    def _refresh_paired_list(self):
        """重建「已配对设备」列表 UI。"""
        while self.paired_group_layout.count():
            item = self.paired_group_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._paired = self._load_paired()
        if not self._paired:
            lbl = QLabel("暂无已配对设备记录")
            lbl.setStyleSheet(f"color: {ACCENT};")
            self.paired_group_layout.addWidget(lbl)
            return
        for entry in self._paired:
            ip = entry.get('ip', '')
            port = entry.get('debug_port', 5555)
            model = entry.get('model', '')
            row = QHBoxLayout()
            label = QLabel(f"{ip}:{port}" + (f"  [{model}]" if model else ""))
            row.addWidget(label)
            row.addStretch()
            btn_re = QPushButton("重连")
            btn_re.setMaximumWidth(60)
            btn_re.clicked.connect(lambda _checked=False, e=entry: self._reconnect_saved(e))
            row.addWidget(btn_re)
            btn_del = QPushButton("删除")
            btn_del.setMaximumWidth(60)
            btn_del.clicked.connect(lambda _checked=False, e=entry: self._delete_paired(e))
            row.addWidget(btn_del)
            container = QWidget()
            container.setLayout(row)
            self.paired_group_layout.addWidget(container)

    def _reconnect_saved(self, entry):
        ip = entry.get('ip')
        debug_port = entry.get('debug_port', 5555)
        if not ip:
            return
        self._reconnect_target = f"{ip}:{debug_port}"
        self._set_buttons_busy(True)
        self._log(f"⟳ 正在重连已配对设备 {ip}:{debug_port} …")
        self.status_lbl.setText(f"正在重连 {ip}:{debug_port} …")
        self._reconnect_worker = _ConnectWorker(ip, [int(debug_port)], timeout=8)
        self._reconnect_thread = QThread(self)
        self._reconnect_worker.moveToThread(self._reconnect_thread)
        self._reconnect_thread.started.connect(self._reconnect_worker.run)
        self._reconnect_worker.done.connect(self._on_reconnect_done)
        self._reconnect_thread.start()

    def _on_reconnect_done(self, ok, msg, tried_ports):
        self._set_buttons_busy(False)
        self._log(msg)
        target = getattr(self, '_reconnect_target', '')
        self._add_history('重连', target, ok, msg)
        if ok:
            self._log(f"✅ 已重连 {msg[:60]}")
            self.status_lbl.setText(f"✅ 已重连 {msg[:60]}")
            if self._on_pair_success:
                try:
                    self._on_pair_success()
                except Exception:
                    pass
        else:
            self.status_lbl.setText(
                f"❌ 重连失败（端口 {tried_ports}）— 该设备可能已更换调试端口，请重新配对")

    def _delete_paired(self, entry):
        ip = entry.get('ip')
        paired = [p for p in self._load_paired() if p.get('ip') != ip]
        self._save_paired(paired)
        self._paired = paired
        self._refresh_paired_list()
        self._log(f"🗑 已删除已配对记录：{ip}")

    def _save_current_pair(self, ip, debug_port, pair_port):
        """连接成功后保存设备指纹，便于下次一键重连。"""
        model = ''
        try:
            from adb_utils import AdbHelper
            serial = f"{ip}:{debug_port}"
            model = AdbHelper().run_shell(serial, "getprop ro.product.model",
                                          timeout=5).strip()
        except Exception:
            model = ''
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        entry = {
            'ip': ip,
            'debug_port': int(debug_port),
            'pair_port': int(pair_port) if str(pair_port).isdigit() else None,
            'model': model,
            'paired_at': now,
            'last_connected': now,
        }
        paired = self._load_paired()
        paired = [p for p in paired if p.get('ip') != ip]
        paired.insert(0, entry)       # 最近配对放最前
        paired = paired[:20]          # 最多保留 20 条
        self._save_paired(paired)
        self._paired = paired
        self._refresh_paired_list()

    def _add_history(self, action, target, ok, detail=''):
        """记录一条配对/连接操作历史。"""
        entry = {
            '时间': time.strftime('%Y-%m-%d %H:%M:%S'),
            '动作': action,
            '目标': str(target),
            '结果': '成功' if ok else '失败',
            '详情': (detail or '')[:200],
        }
        hist = load_json_config(_HISTORY_CFG)
        if not isinstance(hist, list):
            hist = []
        hist.insert(0, entry)
        hist = hist[:200]              # 最多保留 200 条
        save_json_config(_HISTORY_CFG, hist)

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════
    def _log(self, text):
        self.output.append(text)

    def _set_buttons_busy(self, busy):
        self.btn_pair.setEnabled(not busy)
        self.btn_connect.setEnabled(not busy and self._connect_enabled)
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
            "   • 调试端口：手机「无线调试」主页面显示的端口（默认 5555）\n"
            "4. 点击「开始配对」\n\n"
            "快捷操作：\n"
            "• 点击「📋 粘贴」可直接从剪贴板自动解析 IP:端口 和配对码\n"
            "• 扫码和生成二维码功能已移至「二维码连接」标签页\n"
            "• 勾选「配对成功后自动连接」后，配对成功会自动执行 connect\n"
            "• 连接失败时会自动尝试 5555 / 配对端口 / 37800 等候选端口\n"
            "• 已配对设备会自动保存，下次打开弹窗可一键重连")

    def _show_history(self):
        from wifi_history_dialog import WifiHistoryDialog
        dlg = WifiHistoryDialog(self)
        dlg.exec()

    def cleanup(self):
        """停止所有后台线程，供嵌入统一面板时由父窗口调用。"""
        self._closing = True
        for w in (self._pair_worker, self._connect_worker, self._reconnect_worker):
            if w is not None:
                try:
                    w.cancel()
                except Exception:
                    pass
        for t in (self._pair_thread, self._connect_thread, self._reconnect_thread):
            if t is not None and t.isRunning():
                t.quit()
                t.wait(2000)

    def closeEvent(self, event):
        self.cleanup()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = WifiPairDialog()
    dlg.show()
    sys.exit(app.exec())
