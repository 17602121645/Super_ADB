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

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
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

    def __init__(self, ips, timeout=DEFAULT_TIMEOUT, max_workers=MAX_WORKERS,
                 port=ADB_PORT):
        super().__init__()
        self._ips = list(ips)
        self._timeout = timeout
        self._max_workers = min(max_workers, len(ips))
        self._port = port
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
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(self._timeout)
                s.connect((ip, self._port))
                latency_ms = (time.monotonic() - t0) * 1000.0
                # 尝试读取 ADB 协议握手(CNxn)确认是ADB而非其他服务
                try:
                    s.settimeout(1.0)
                    data = s.recv(4)
                    if data != b'CNxn':
                        return None  # 端口开放但不是ADB
                except Exception:
                    pass  # 读不到数据也视为可能（有些设备握手慢）
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
        self._port = ADB_PORT
        self._closing = False
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

        h_set.addWidget(QLabel("端口："))
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(ADB_PORT)
        self.port_spin.setToolTip("ADB 无线调试端口，默认 5555；部分设备/场景使用非标端口")
        self.port_spin.valueChanged.connect(self._on_port_changed)
        h_set.addWidget(self.port_spin)

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
        # IP 列改为 Interactive + 默认 200px，不再 Stretch 把整行占满
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 200)   # IP 地址
        self.table.setColumnWidth(1, 140)   # 状态（容纳「🟢 在线 · 机型名」）
        self.table.setColumnWidth(2, 90)    # 延迟
        self.table.setColumnWidth(3, 130)   # 操作（容纳连接按钮，不截字）
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setAlternatingRowColors(True)
        v_result.addWidget(self.table)

        root.addWidget(g_result)

        # 底部提示
        self.hint_label = QLabel(
            f"💡 提示：双击在线设备可直接连接；扫描的是 TCP/IP ADB 调试端口"
            f"（{ADB_PORT}），请确保目标设备已开启「无线调试」")
        self.hint_label.setStyleSheet("color: #888; font-size: 11px;")
        self.hint_label.setWordWrap(True)
        root.addWidget(self.hint_label)

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
            port=self._port,
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

        btn_conn = self._make_connect_btn(ip)
        self.table.setCellWidget(row, 3, btn_conn)

        self._found_ips.append(ip)
        self.lbl_status.setText(f"已发现 {len(self._found_ips)} 台设备...")

    def _on_progress(self, current, total):
        self.progress.setValue(current)
        pct = current * 100 // total if total else 0
        self.lbl_status.setText(f"扫描中... {current}/{total} ({pct}%)")

    def _on_scan_finished(self, results):
        if getattr(self, '_closing', False):
            return
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
            self._resort_by_latency()
        elif total_scanned > 0:
            # 全部离线时也加一行提示
            self.table.insertRow(0)
            tip = QTableWidgetItem("  未在当前网段发现 ADB 设备（端口 5555）")
            tip.setForeground(TIP_GRAY)
            tip.setFlags(tip.flags() & ~Qt.ItemIsSelectable)
            self.table.setItem(0, 0, tip)
            self.table.setSpan(0, 0, 1, 4)

    def _on_scan_stopped(self):
        if getattr(self, '_closing', False):
            return
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
        target = f"{ip}:{self._port}"
        parent = self.parent()
        if parent and hasattr(parent, 'adb') and hasattr(parent, '_do_connect'):
            parent._do_connect(target)
        else:
            # fallback: 直接调 adb
            from adb_utils import AdbHelper
            adb = AdbHelper()
            result = adb.connect(target)
            QMessageBox.information(self, "连接结果", f"{target}\n{result}")
        # 连接成功后回填机型名（不可达/未授权则静默跳过）
        self._enrich_after_connect(ip)

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
            r = adb.connect(f"{ip}:{self._port}")
            results.append(f"{ip}:{self._port}: {r}")
            self._enrich_after_connect(ip)
        dlg = QMessageBox(self)
        dlg.setWindowTitle("批量连接结果")
        dlg.setText(f"已完成 {len(self._found_ips)} 台设备的连接请求：")
        dlg.setDetailedText("\n".join(results))
        dlg.exec()

    def _copy_all_ips(self):
        """复制所有发现的 IP 到剪贴板。"""
        if not hasattr(self, '_found_ips') or not self._found_ips:
            return
        text = "\n".join(f"{ip}:{self._port}" for ip in self._found_ips)
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

    # ── 结果整理 / 端口 / 机型回填 ──

    def _make_connect_btn(self, ip):
        """统一构造表格行的「连接」按钮：最小宽度+固定高度，避免列窄时字被截。"""
        btn = QPushButton("连接")
        btn.setProperty("class", "accentBtn")
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(80)
        btn.setFixedHeight(28)
        btn.clicked.connect(lambda checked, _ip=ip: self._connect_one(_ip))
        return btn

    def _resort_by_latency(self):
        """扫描完成后按延迟升序重建结果表，并同步 _found_ips 顺序。"""
        n = self.table.rowCount()
        if n <= 1:
            return
        order = []
        for r in range(n):
            lat_item = self.table.item(r, 2)
            try:
                order.append((float(lat_item.text()), r))
            except (TypeError, ValueError):
                order.append((float('inf'), r))
        order.sort(key=lambda x: x[0])
        # 提取可见数据后重建（避免复用可能被 Qt 释放的 item 指针）
        snapshot = []
        for _, r in order:
            ip = self.table.item(r, 0).text()
            st_item = self.table.item(r, 1)
            st_text = st_item.text()
            st_fg = st_item.foreground().color()
            lat_text = self.table.item(r, 2).text()
            snapshot.append((ip, st_text, st_fg, lat_text))
        self.table.setRowCount(0)
        for ip, st_text, st_fg, lat_text in snapshot:
            row = self.table.rowCount()
            self.table.insertRow(row)
            item_ip = QTableWidgetItem(ip)
            item_ip.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, item_ip)
            status_item = QTableWidgetItem(st_text)
            status_item.setForeground(st_fg)
            status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 1, status_item)
            lat_item = QTableWidgetItem(lat_text)
            lat_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, lat_item)
            btn_conn = self._make_connect_btn(ip)
            self.table.setCellWidget(row, 3, btn_conn)
        self._found_ips = [ip for ip, *_ in snapshot]

    def _on_port_changed(self, val):
        """端口变更：同步内部值并更新底部提示文案。"""
        self._port = val
        self.hint_label.setText(
            f"💡 提示：双击在线设备可直接连接；扫描的是 TCP/IP ADB 调试端口"
            f"（{val}），请确保目标设备已开启「无线调试」")

    def _enrich_after_connect(self, ip):
        """连接成功后回填机型名到对应表格行（后台线程，避免阻塞 UI）。"""
        serial = f"{ip}:{self._port}"

        def _work():
            try:
                from adb_utils import AdbHelper
                adb = AdbHelper()
                brand = adb.run_shell(serial, "getprop ro.product.brand", timeout=5).strip()
                model = adb.run_shell(serial, "getprop ro.product.model", timeout=5).strip()
                name = (brand + " " + model).strip() or model or "未知机型"
            except Exception:
                return
            QTimer.singleShot(0, lambda: self._apply_enrich(ip, name))

        threading.Thread(target=_work, daemon=True).start()

    def _apply_enrich(self, ip, name):
        if getattr(self, '_closing', False):
            return
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == ip:
                st = self.table.item(r, 1)
                if st:
                    st.setText(f"🟢 在线 · {name}")
                break

    def closeEvent(self, event):
        """关闭窗口时停止正在运行的扫描并干净退出后台线程。

        直接 cancel + quit + wait，避免依赖异步 stopped 信号在 UI 线程
        被 wait 阻塞期间无法投递、导致窗口销毁后才回调到已释放的 widget。
        """
        self._closing = True
        if self._scan_thread and self._scan_thread.isRunning():
            if self._worker:
                self._worker.cancel()
            self._scan_thread.quit()
            self._scan_thread.wait(2000)
            self._scan_thread = None
            self._worker = None
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
