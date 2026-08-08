# -*- coding: utf-8 -*-
"""
局域网 ADB 设备扫描弹窗
======================
点击主界面「便捷工具 → 局域网扫描」按钮弹出的独立窗口：
- 自动检测本机 IP，默认扫描同网段（端口 5555）
- 支持自定义 IP 范围（CIDR / 起始-结束 / 单个 IP）
- 后台线程并发扫描，实时显示结果（IP / 状态 / 延迟 / 操作）
- 发现的设备可直接一键连接或复制 IP
"""

import ipaddress
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QIcon, QStandardItemModel, QStandardItem
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QGroupBox, QTableWidget, QTableWidgetItem,
    QProgressBar, QComboBox, QSpinBox, QHeaderView, QMessageBox,
    QAbstractItemView,
)

import png_rc  # noqa: F401
from 界面样式 import ACCENT, FONT_FAMILY, STYLE_SHEET
from popup_style import add_green_glow

ADB_PORT = 5555
DEFAULT_TIMEOUT = 0.4       # 每个IP的socket超时（秒）
MAX_WORKERS = 100          # 并发扫描线程数
SCAN_BATCH_SIZE = 20       # 每批信号汇报的条目数（避免频繁UI刷新）


class _ScanWorker(QObject):
    """后台扫描线程：遍历 IP 列表，逐个探测 ADB 端口。"""

    found = Signal(str, float, object)   # ip, latency_ms, extra_info
    progress = Signal(int, int)           # current, total
    finished = Signal(list)               # [(ip, latency_ms), ...] 全量结果
    stopped = Signal()

    def __init__(self, ips, timeout=DEFAULT_TIMEOUT, max_workers=MAX_WORKERS):
        super().__init__()
        self._ips = list(ips)
        self._timeout = timeout
        self._max_workers = min(max_workers, len(ips))
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        results = []
        total = len(self._ips)
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(self._probe, ip): ip for ip in self._ips}
            done_count = 0
            for future in as_completed(futures):
                if self._cancelled:
                    # 取消未完成的
                    for f in futures:
                        f.cancel()
                    self.stopped.emit()
                    return
                ip = futures[future]
                try:
                    latency = future.result()
                except Exception:
                    latency = None
                done_count += 1
                if latency is not None:
                    results.append((ip, latency))
                    self.found.emit(ip, latency, None)
                if done_count % SCAN_BATCH_SIZE == 0 or done_count == total:
                    self.progress.emit(done_count, total)
        self.progress.emit(total, total)
        # 按延迟排序
        results.sort(key=lambda x: x[1])
        self.finished.emit(results)

    def _probe(self, ip):
        """探测单个 IP 的 ADB 端口。返回延迟(ms) 或 None（不可达）。"""
        try:
            t0 = time.monotonic()
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self._timeout)
            s.connect((ip, ADB_PORT))
            latency_ms = (time.monotonic() - t0) * 1000.0
            # 尝试读取 ADB 协议握手(CNxn)确认是ADB而非其他服务
            try:
                s.settimeout(1.0)
                data = s.recv(4)
                if data != b'CNxn':
                    s.close()
                    return None  # 端口开放但不是ADB
            except Exception:
                pass  # 读不到数据也视为可能（有些设备握手慢）
            s.close()
            return round(latency_ms, 1)
        except Exception:
            return None


class LanScannerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("局域网 ADB 设备扫描")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(480)
        add_green_glow(self)
        self._worker = None
        self._scan_thread = None
        self._build_ui()
        self._auto_detect_network()

    # ── UI 构建 ──

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 扫描设置 ──
        g_set = QGroupBox("扫描设置")
        h_set = QHBoxLayout(g_set)
        h_set.setSpacing(8)

        h_set.addWidget(QLabel("IP 范围："))
        self.range_combo = QComboBox()
        self.range_combo.setEditable(True)
        self.range_combo.setMinimumWidth(280)
        self.range_combo.setPlaceholderText("例如 192.168.1.0/24 或 192.168.1.1-192.168.1.254")
        h_set.addWidget(self.range_combo)

        h_set.addWidget(QLabel("超时："))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 2000)
        self.timeout_spin.setValue(int(DEFAULT_TIMEOUT * 1000))
        self.timeout_spin.setSuffix(" ms")
        self.timeout_spin.setToolTip("每个 IP 的连接等待时间，值越小越快但漏检率越高")
        h_set.addWidget(self.timeout_spin)

        h_set.addWidget(QLabel("线程："))
        self.worker_spin = QSpinBox()
        self.worker_spin.setRange(10, 256)
        self.worker_spin.setValue(MAX_WORKERS)
        self.worker_spin.setToolTip("并发扫描线程数，越多越快但占用资源越多")
        h_set.addWidget(self.worker_spin)

        root.addWidget(g_set)

        # ── 操作按钮行 ──
        h_btn = QHBoxLayout()

        self.btn_scan = QPushButton("▶ 开始扫描")
        self.btn_scan.setMinimumHeight(34)
        self.btn_scan.clicked.connect(self._toggle_scan)
        h_btn.addWidget(self.btn_scan)

        self.btn_connect_all = QPushButton("一键连接全部")
        self.btn_connect_all.setEnabled(False)
        self.btn_connect_all.clicked.connect(self._connect_all_found)
        h_btn.addWidget(self.btn_connect_all)

        self.btn_copy_all = QPushButton("复制所有 IP")
        self.btn_copy_all.setEnabled(False)
        self.btn_copy_all.clicked.connect(self._copy_all_ips)
        h_btn.addWidget(self.btn_copy_all)

        h_btn.addStretch()

        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet(f"color: {ACCENT};")
        h_btn.addWidget(self.lbl_status)

        root.addLayout(h_btn)

        # ── 进度条 ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)

        # ── 结果表格 ──
        g_result = QGroupBox("扫描结果")
        v_result = QVBoxLayout(g_result)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["IP 地址", "状态", "延迟 (ms)", "操作"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 90)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setAlternatingRowColors(True)
        v_result.addWidget(self.table)

        root.addWidget(g_result)

        # 底部提示
        hint = QLabel("💡 提示：双击在线设备可直接连接；扫描的是 TCP/IP ADB 调试端口（5555），请确保目标设备已开启「无线调试」")
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

    # ── 自动检测本机网络 ──

    def _auto_detect_network(self):
        """自动检测本机 IP 并填充默认网段到下拉框。"""
        hostname = socket.gethostname()
        try:
            ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        except Exception:
            return
        added = set()
        for info in ips:
            ip_str = info[4][0]
            if ip_str.startswith('127.') or ip_str in added:
                continue
            added.add(ip_str)
            try:
                network = ipaddress.IPv4Network(f"{ip_str}/24", strict=False)
                cidr = str(network)
                self.range_combo.addItem(f"{cidr} （本机 {ip_str}）", cidr)
            except Exception:
                continue
        if self.range_combo.count() > 0:
            self.range_combo.setCurrentIndex(0)

    # ── IP 范围解析 ──

    @staticmethod
    def _parse_ip_range(text):
        """
        解析用户输入的 IP 范围，返回 IPv4Address 列表。
        支持格式：
          - CIDR:   192.168.1.0/24
          - 范围:   192.168.1.1-192.168.1.254
          - 单个:   192.168.1.100
        """
        text = text.strip()
        if not text:
            return []
        # CIDR
        if '/' in text:
            try:
                net = ipaddress.IPv4Network(text, strict=False)
                # 排除网络地址和广播地址
                return [str(h) for h in net.hosts()]
            except Exception:
                pass
        # 范围 start-end
        if '-' in text:
            parts = text.rsplit('-', 1)
            if len(parts) == 2:
                try:
                    start = ipaddress.IPv4Address(parts[0].strip())
                    end = ipaddress.IPv4Address(parts[1].strip())
                    return [str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1)]
                except Exception:
                    pass
        # 单个 IP
        try:
            ipaddress.IPv4Address(text)
            return [text]
        except Exception:
            return []

    # ── 扫描控制 ──

    def _toggle_scan(self):
        if self._scan_thread is not None and self._scan_thread.isRunning():
            self._stop_scan()
        else:
            self._start_scan()

    def _start_scan(self):
        range_text = self.range_combo.currentText().split('（')[0].strip()
        ips = self._parse_ip_range(range_text)
        if not ips:
            QMessageBox.warning(self, "输入无效",
                                 "无法解析 IP 范围。支持格式：\n"
                                 "• CIDR: 192.168.1.0/24\n"
                                 "• 范围: 192.168.1.1-192.168.1.254\n"
                                 "• 单个: 192.168.1.100")
            return

        # 清空旧结果
        self.table.setRowCount(0)
        self._found_ips = []  # 保留发现列表供"一键连接"

        # UI 状态切换
        self.btn_scan.setText("■ 停止扫描")
        self.btn_connect_all.setEnabled(False)
        self.btn_copy_all.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(ips))
        self.progress.setValue(0)
        self.lbl_status.setText(f"正在扫描 {len(ips)} 个地址...")
        self.range_combo.setEnabled(False)
        self.timeout_spin.setEnabled(False)
        self.worker_spin.setEnabled(False)

        # 启动后台线程
        self._scan_thread = QThread()
        self._worker = _ScanWorker(
            ips,
            timeout=self.timeout_spin.value() / 1000.0,
            max_workers=self.worker_spin.value(),
        )
        self._worker.moveToThread(self._scan_thread)
        self._scan_thread.started.connect(self._worker.run)
        self._worker.found.connect(self._on_device_found)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.stopped.connect(self._on_scan_stopped)
        self._scan_thread.start()

    def _stop_scan(self):
        if self._worker:
            self._worker.cancel()
        self.btn_scan.setEnabled(False)
        self.lbl_status.setText("正在停止...")

    # ── 回调信号 ──

    def _on_device_found(self, ip, latency_ms, _extra):
        row = self.table.rowCount()
        self.table.insertRow(row)

        item_ip = QTableWidgetItem(ip)
        item_ip.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 0, item_ip)

        status_item = QTableWidgetItem("🟢 在线")
        status_item.setForeground(ACCENT_COLOR_GREEN)
        status_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 1, status_item)

        lat_item = QTableWidgetItem(f"{latency_ms:.1f}")
        lat_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, 2, lat_item)

        btn_conn = QPushButton("连接")
        btn_conn.setProperty("class", "accentBtn")
        btn_conn.setCursor(Qt.PointingHandCursor)
        btn_conn.clicked.connect(lambda checked, _ip=ip: self._connect_one(_ip))
        self.table.setCellWidget(row, 3, btn_conn)

        self._found_ips.append(ip)
        self.lbl_status.setText(f"已发现 {len(self._found_ips)} 台设备...")

    def _on_progress(self, current, total):
        self.progress.setValue(current)
        pct = current * 100 // total if total else 0
        self.lbl_status.setText(f"扫描中... {current}/{total} ({pct}%)")

    def _on_scan_finished(self, results):
        self._cleanup_thread()
        total_scanned = self.progress.maximum()
        found = len(results)
        self.progress.setValue(total_scanned)
        self.lbl_status.setText(f"✅ 扫描完成：共 {total_scanned} 个地址，发现 {found} 台 ADB 设备")

        self.btn_scan.setText("▶ 开始扫描")
        self.btn_scan.setEnabled(True)
        self.range_combo.setEnabled(True)
        self.timeout_spin.setEnabled(True)
        self.worker_spin.setEnabled(True)

        if found > 0:
            self.btn_connect_all.setEnabled(True)
            self.btn_copy_all.setEnabled(True)
        elif total_scanned > 0:
            # 全部离线时也加一行提示
            self.table.insertRow(0)
            tip = QTableWidgetItem("  未在当前网段发现 ADB 设备（端口 5555）")
            tip.setForeground(TIP_GRAY)
            tip.setFlags(tip.flags() & ~Qt.ItemIsSelectable)
            self.table.setItem(0, 0, tip)
            self.table.setSpan(0, 0, 1, 4)

    def _on_scan_stopped(self):
        self._cleanup_thread()
        self.progress.setValue(self.progress.maximum())
        self.lbl_status.setText("⛔ 扫描已停止")
        self.btn_scan.setText("▶ 开始扫描")
        self.btn_scan.setEnabled(True)
        self.range_combo.setEnabled(True)
        self.timeout_spin.setEnabled(True)
        self.worker_spin.setEnabled(True)

    def _cleanup_thread(self):
        if self._scan_thread:
            self._scan_thread.quit()
            self._scan_thread.wait(3000)
            self._scan_thread = None
        self._worker = None

    # ── 操作方法 ──

    def _connect_one(self, ip):
        """连接单台设备（通过主窗口的 ADB connect）。"""
        parent = self.parent()
        if parent and hasattr(parent, 'adb') and hasattr(parent, '_do_connect'):
            parent._do_connect(f"{ip}:{ADB_PORT}")
        else:
            # fallback: 直接调 adb
            from adb_utils import AdbHelper
            adb = AdbHelper()
            result = adb.connect(ip)
            QMessageBox.information(self, "连接结果", f"{ip}:{ADB_PORT}\n{result}")

    def _connect_all_found(self):
        """一键连接所有发现的设备。"""
        if not hasattr(self, '_found_ips') or not self._found_ips:
            return
        reply = QMessageBox.question(
            self, "确认连接",
            f"确定要连接全部 {len(self._found_ips)} 台设备吗？\n"
            + "\n".join(f"  • {ip}" for ip in self._found_ips),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        from adb_utils import AdbHelper
        adb = AdbHelper()
        results = []
        for ip in self._found_ips:
            r = adb.connect(ip)
            results.append(f"{ip}: {r}")
        dlg = QMessageBox(self)
        dlg.setWindowTitle("批量连接结果")
        dlg.setText(f"已完成 {len(self._found_ips)} 台设备的连接请求：")
        dlg.setDetailedText("\n".join(results))
        dlg.exec()

    def _copy_all_ips(self):
        """复制所有发现的 IP 到剪贴板。"""
        if not hasattr(self, '_found_ips') or not self._found_ips:
            return
        text = "\n".join(f"{ip}:{ADB_PORT}" for ip in self._found_ips)
        QApplication.clipboard().setText(text)
        self.lbl_status.setText(f"已复制 {len(self._found_ips)} 个地址到剪贴板")

    def _on_double_click(self, index):
        """双击表格行 → 连接该设备。"""
        row = index.row()
        ip_item = self.table.item(row, 0)
        if ip_item:
            ip = ip_item.text()
            if ip and not ip.startswith("  未"):  # 不是提示行
                self._connect_one(ip)

    def closeEvent(self, event):
        """关闭窗口时停止正在运行的扫描。"""
        if self._scan_thread and self._scan_thread.isRunning():
            self._stop_scan()
            # 给线程一点时间退出
            if self._scan_thread:
                self._scan_thread.wait(2000)
        event.accept()


# ── 颜色常量（避免循环导入） ──
try:
    from PySide6.QtGui import QColor
    ACCENT_COLOR_GREEN = QColor("#00CC66")
    TIP_GRAY = QColor("#999999")
except Exception:
    ACCENT_COLOR_GREEN = None
    TIP_GRAY = None


if __name__ == "__main__":
    import sys as _sys
    app = QApplication(_sys.argv)
    dlg = LanScannerDialog()
    dlg.show()
    _sys.exit(app.exec())
