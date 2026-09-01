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
import subprocess
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

from ui import png_rc  # noqa: F401
from ui.ui_styles import ACCENT, FONT_FAMILY, STYLE_SHEET, get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import add_green_glow

ADB_PORT = 5555
DEFAULT_TIMEOUT = 0.8       # 每个IP的socket超时（秒），WiFi环境建议0.8-1.0
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
                # TCP 连上后发送 CNXN 验证是否真的是 ADB 设备
                # （过滤掉 TCP 能连但不是 ADB 服务的假设备）
                try:
                    import struct
                    # ADB CNXN 消息：24字节头 + banner
                    banner = b'host::features=shell_v2,cmd'
                    # 正确的 checksum = sum(payload) & 0xffffffff
                    checksum = sum(banner) & 0xffffffff
                    CNXN = 0x4e584e43
                    AUTH = 0x48545541
                    header = struct.pack('<IIIIII',
                        CNXN,         # command
                        0x01000000,   # version
                        1048576,      # max_payload
                        len(banner),  # data_length
                        checksum,     # data_checksum
                        CNXN ^ 0xffffffff  # magic
                    )
                    s.sendall(header + banner)
                    s.settimeout(3.0)
                    # 读取完整的24字节响应头
                    resp = b''
                    while len(resp) < 24:
                        chunk = s.recv(24 - len(resp))
                        if not chunk:
                            break
                        resp += chunk
                    if len(resp) < 24:
                        return None
                    # 解析响应头
                    cmd, arg0, arg1, data_len, data_crc, magic = struct.unpack('<IIIIII', resp)
                    # ★ 严格验证：magic必须是command ^ 0xffffffff
                    expected_magic = cmd ^ 0xffffffff
                    if magic != expected_magic:
                        return None
                    # ★ 验证data_length是否合理（ADB payload最大1MB）
                    if data_len > 1024 * 1024:
                        return None
                    # AUTH或CNXN才是ADB设备
                    if cmd not in (AUTH, CNXN):
                        return None
                    # ★ 额外验证：如果是AUTH，arg0应该是1（TOKEN）
                    if cmd == AUTH and arg0 != 1:
                        return None
                except Exception:
                    return None  # 无响应或不是 ADB，过滤掉
                latency_ms = (time.monotonic() - t0) * 1000.0
                return round(latency_ms, 1)
        except Exception:
            return None


class _ConnectWorker(QObject):
    """后台执行 adb connect，避免主线程被 subprocess 阻塞导致窗口「未响应」。

    设计要点：
      - 通过 moveToThread 由 QThread 调度，避免直接 threading.Thread 跨线程投递到 UI
      - 给 connect / getprop 都设置较短 timeout (10s)，超时立刻抛错而非挂死
      - 用 done 信号一次汇报全部结果（含 ok / msg），UI 侧据此刷新按钮/弹窗
    """
    done = Signal(bool, str)   # ok, message ("connected to 1.2.3.4:5555" 或 "timeout...")

    def __init__(self, ip, port=ADB_PORT, timeout=8):
        super().__init__()
        self._ip = ip
        self._port = port
        self._timeout = timeout
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        # 调用前先检查：被取消就不再起 adb 进程（节省资源）
        if self._cancelled:
            return
        target = f"{self._ip}:{self._port}"
        try:
            from tools.adb_tools import AdbHelper
            helper = AdbHelper()

            # 自研 ADB 模式：直接用自研客户端连接，不调用 adb.exe
            if helper._用自研adb:
                try:
                    # ★ 必须走 AdbHelper._获取自研adb（类级共享缓存），不要自己
                    #   new 客户端：
                    #   1) 自己 new 的 client 不进缓存，连上后主窗口刷新设备列表
                    #      时又会重新建连一次 —— 日志里「同一设备连接两次」就是
                    #      这么来的；
                    #   2) 旧代码连成功后立刻 关闭()，把该设备的池连接全部销毁，
                    #      紧接着主窗口的第二次建连要重新走 AUTH。部分 ROM 在
                    #      socket 刚断开后短时间内不再回 CNXN（也不再弹授权框），
                    #      于是第二次就是「连接失败，状态=None」。
                    #   现在连接由缓存持有并保持存活，主窗口直接复用，
                    #   全过程只有一次认证、一次弹窗。
                    client = helper._获取自研adb(target)
                    if client is not None:
                        self.done.emit(True, f"connected to {target}")
                    else:
                        # _获取自研adb 失败返回 None，具体原因已打印在控制台
                        self.done.emit(
                            False,
                            "连接失败：设备未授权或未响应，请在设备上确认调试授权弹窗")
                    return
                except Exception as e:
                    self.done.emit(False, f"❌ 连接失败：{e}")
                    return

            # 系统 ADB / Socket 直连模式：用 subprocess 调用 adb connect
            adb_path = helper.adb_path
            cmd = [adb_path, 'connect', target]
            creationflags = 0
            try:
                # Windows: 避免弹黑框
                from tools.adb_tools import CREATE_NO_WINDOW  # type: ignore
                creationflags = CREATE_NO_WINDOW
            except Exception:
                pass
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=self._timeout,
                creationflags=creationflags,
                shell=False,
            )
            result = (proc.stdout or proc.stderr or '').strip()
            # adb connect 成功返回类似 "connected to 1.2.3.4:5555"
            ok = ('connected' in result.lower()
                  or 'already' in result.lower())
            self.done.emit(ok, result or '无返回')
        except subprocess.TimeoutExpired:
            # 10s 仍未返回 → 视为失败(用户看到的就是失败提示,而非「未响应」)
            self.done.emit(False, f"连接超时 ({self._timeout}s)，请检查目标设备是否开启无线调试")
        except Exception as e:
            # 超时 / 进程卡死 / 任何异常都汇报错误而非让 UI 永久挂着
            self.done.emit(False, f"❌ 连接失败：{e}")


class _EnrichWorker(QObject):
    """连接成功后回填机型名到状态列。后台跑 getprop，避免主线程卡死。

    改进点（替代旧的 threading.Thread + QTimer 方案）：
      - 用 QObject + moveToThread，信号回填，线程模型统一为 Qt
      - getprop 单独设 timeout 5s；失败容错（无授权/未连上/超时一律静默跳过）
    """
    done = Signal(str, str)   # ip, display_name ("在线 · Xiaomi Mi 10" / "在线")

    # 品牌 + 型号一次 shell 取回：两条 getprop 用 ';' 串在同一个 shell 会话里执行。
    # 拆成两次 执行shell 会多开一个 shell 会话、多一个网络往返，日志里也会打出
    # 两行 "$ adb -s ... shell getprop ..."，看着像重复请求。
    _PROP_CMD = "getprop ro.product.brand;getprop ro.product.model"

    @staticmethod
    def _解析品牌型号(out):
        """把 _PROP_CMD 的输出按行拆成 (brand, model)。

        属性为空时 getprop 仍会输出一个空行，所以按行号取值而不是过滤空行，
        避免品牌缺失时把型号错当成品牌。
        """
        lines = (out or '').replace('\r', '').split('\n')
        brand = lines[0].strip() if len(lines) > 0 else ''
        model = lines[1].strip() if len(lines) > 1 else ''
        return brand, model

    def __init__(self, ip, port=ADB_PORT, timeout=5, adb=None):
        super().__init__()
        self._ip = ip
        self._serial = f"{ip}:{port}"
        self._timeout = timeout
        self._cancelled = False
        self._adb = adb  # 主窗口的 AdbHelper 实例，复用自研adb连接缓存

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        try:
            # 优先用主窗口传入的 AdbHelper 实例（复用已缓存的自研adb连接，
            # 避免新建连接时设备并发连接限制导致失败）。
            if self._adb is not None:
                out = self._adb.执行shell(self._serial, self._PROP_CMD,
                                         timeout=self._timeout)
                brand, model = self._解析品牌型号(out)
                name = (brand + " " + model).strip() or model or ''
                if self._cancelled:
                    return
                self.done.emit(self._ip, name)
                return
            # 兜底：无 AdbHelper 时直接用自研 ADB 客户端直连
            # 注意：本类只存了 _serial，没有 _port；且 自研adb客户端 的构造签名是
            # (host, port, key_path, log_callback)，没有 timeout 参数 —— 旧写法
            # `自研adb客户端(self._ip, self._port, timeout=...)` 必然抛
            # AttributeError/TypeError 并被下面的 except 吞掉，型号永远取不到。
            from tools.adb_native import 自研adb客户端
            _port = int(self._serial.rsplit(':', 1)[1]) if ':' in self._serial else ADB_PORT
            client = 自研adb客户端(self._ip, _port)
            if client.连接(timeout=self._timeout):
                out = client.执行shell(self._PROP_CMD, timeout=self._timeout)
                client.关闭()
                brand, model = self._解析品牌型号(out)
                name = (brand + " " + model).strip() or model or ''
                if self._cancelled:
                    return
                self.done.emit(self._ip, name)
                return
        except Exception:
            pass
        # 未授权 / 离线 / 超时 → 静默回填空串，UI 据此保留原状态
        self.done.emit(self._ip, '')


class _RangeCombo(QComboBox):
    """重载 showPopup：用户每次展开下拉框时自动重新探测本机网段。

    设计要点：
      - 仅在「展开列表」时刷新（点箭头），不影响编辑区正常输入。
      - 刷新后尽量恢复刷新前已选中的项，避免每次弹开都跳回第一项。
      - 扫描进行中下拉框被 setEnabled(False)，不会触发 showPopup，故不会打乱扫描。
      - 复用已有的 refresh_network_range()，不改动其逻辑与初始化行为。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dialog = None

    def set_dialog(self, dlg):
        self._dialog = dlg

    def showPopup(self):
        if self._dialog is not None:
            prev = self.currentText()
            self._dialog.refresh_network_range()
            idx = self.findText(prev)
            if idx >= 0:
                self.setCurrentIndex(idx)
        super().showPopup()


class 局域网扫描对话框(QDialog):
    """局域网 ADB 设备扫描弹窗。

    参数:
      - on_device_connected(serial): 用户在扫描结果里点「连接」(或「一键连接全部」)
        并被 adb connect 成功后回调一次；serial 形如 "192.168.75.20:5555"。
        供主窗口把刚连上的设备自动选中并刷新设备列表下拉框。
    """

    def __init__(self, parent=None, on_device_connected=None, adb=None):
        super().__init__(parent)
        self.setWindowTitle("局域网 ADB 设备扫描")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(480)
        self._theme_id = get_current_theme_id(self)
        self._accent = THEMES[self._theme_id]['accent']
        self.setStyleSheet(get_stylesheet(self._theme_id))
        # 主窗口回填回调：adb connect 成功后调用，参数为 f"{ip}:{port}"
        self._on_device_connected = on_device_connected
        self._adb = adb  # 主窗口的 AdbHelper 实例，复用自研adb连接缓存
        self._worker = None
        self._scan_thread = None
        self._port = ADB_PORT
        self._closing = False
        # 追踪正在运行的连接/回填后台线程，避免关闭窗口时悬挂
        self._connect_threads = []   # list[(QThread, _ConnectWorker)]
        self._enrich_threads = []    # list[(QThread, _EnrichWorker)]
        # 处于「正在连接」的按钮映射 ip→btn（点击后改文字/禁用，回调后再恢复）
        self._busy_buttons = {}
        # 发现的 IP 列表（_on_device_found 中填充,_connect_all_found/_copy_all_ips 读）
        self._found_ips = []
        self._build_ui()
        self._auto_detect_network()

    # ── 主题切换 ──

    def apply_theme(self, theme_id):
        """运行时切换主题：更新 accent 颜色 + 重设全局 QSS + 状态标签颜色。"""
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self._theme_id = theme_id
        self._accent = THEMES[theme_id]['accent']
        self.setStyleSheet(get_stylesheet(theme_id))
        if hasattr(self, 'lbl_status') and self.lbl_status is not None:
            self.lbl_status.setStyleSheet(f"color: {self._accent};")
        self.update()

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
        self.range_combo = _RangeCombo()
        self.range_combo.set_dialog(self)
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
        self.lbl_status.setStyleSheet(f"color: {self._accent};")
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
        # IP 列 Interactive 默认 200px；状态列 Stretch 吃掉剩余空间（容纳机型名+消除右侧空白）
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 200)   # IP 地址
        self.table.setColumnWidth(1, 140)   # 状态最小宽度（容纳「🟢 在线 · 机型名」）
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

    def refresh_network_range(self):
        """重新检测本机 IP 并刷新下拉框（切换网络后调用）。"""
        # 保留用户手动输入的自定义项：先清除自动检测项，再重新添加
        custom_items = []
        for i in range(self.range_combo.count()):
            text = self.range_combo.itemText(i)
            data = self.range_combo.itemData(i)
            if data is None:
                custom_items.append(text)
        self.range_combo.clear()
        for text in custom_items:
            self.range_combo.addItem(text)
        self._auto_detect_network()

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
            # 扫描完成后对所有发现设备异步回填机型（不管是否已连接，
            # 未连接设备自研adb连接失败则静默跳过，不影响UI）。
            for ip in self._found_ips:
                self._enrich_after_connect(ip)
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

    def _find_btn_for_ip(self, ip):
        """根据 ip 反查表格里的按钮 widget（用于改文字/禁用）。"""
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == ip:
                return self.table.cellWidget(r, 3)
        return None

    def _set_status_for_ip(self, ip, text, color=None):
        """统一改状态列文案。"""
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.text() == ip:
                st = self.table.item(r, 1)
                if st is None:
                    continue
                st.setText(text)
                if color is not None:
                    st.setForeground(color)
                return True
        return False

    def _connect_one(self, ip):
        """连接单台设备（异步执行 adb connect,绝不阻塞 UI 线程）。

        行为：
          1. 找到该行按钮 → 改为「连接中...」+ 禁用，防重复点击
          2. 状态列改为「⏳ 连接中...」
          3. 启动 _ConnectWorker 后台线程执行 adb connect (timeout 10s)
          4. 收到 done 信号后：恢复按钮、改状态列、弹窗、异步回填机型名
        """
        if getattr(self, '_closing', False):
            return
        # 标记忙碌（防止同一 IP 重复点击）
        if ip in self._busy_buttons:
            return
        btn = self._find_btn_for_ip(ip)
        if btn is not None:
            btn.setText("连接中...")
            btn.setEnabled(False)
            self._busy_buttons[ip] = btn
        self._set_status_for_ip(ip, "⏳ 连接中...", TIP_GRAY)
        self.lbl_status.setText(f"正在连接 {ip}:{self._port} ...")

        worker = _ConnectWorker(ip, port=self._port, timeout=8)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_connect_done)
        # 信号绑完再 start，确保回调能找到 widget
        thread.start()
        self._connect_threads.append((thread, worker))

    def _on_connect_done(self, ok, msg, ip=None):
        """_ConnectWorker 的 done 信号回调（UI 线程）。ip 通过 lambda 闭包传入。"""
        # 因为 done 信号只有 (ok, msg)，ip 来源在 click 时已经记到 _busy_buttons;
        # 我们从点击映射恢复 ip
        # 但 Python 是逐个 worker 调一次,这里用 sender() 不靠谱（QRunnable-like），
        # 故改用 lambda 闭包 —— 改为包装一层
        sender = self.sender()
        ip = None
        if sender is not None:
            for i, (t, w) in enumerate(self._connect_threads):
                if w is sender:
                    ip = w._ip
                    break
        if ip is None or getattr(self, '_closing', False):
            return
        # 恢复按钮
        btn = self._busy_buttons.pop(ip, None)
        if btn is not None:
            btn.setText("连接")
            btn.setEnabled(True)
        # 更新状态列 & 弹窗
        if ok:
            self._set_status_for_ip(ip, f"🟢 在线", ACCENT_COLOR_GREEN)
            self.lbl_status.setText(f"✅ 已连接 {ip}:{self._port}")
        else:
            self._set_status_for_ip(ip, "❌ 离线/失败", TIP_GRAY)
            self.lbl_status.setText(f"❌ {ip}:{self._port} {msg}")
        # 不论成功失败都清理对应 worker (成功的话再异步回填机型)
        self._cleanup_worker_list(self._connect_threads, ip)
        if ok:
            self._enrich_after_connect(ip)
            # 通知主窗口：刚连上一台新设备，刷新设备下拉框并自动选中它
            if callable(self._on_device_connected):
                try:
                    self._on_device_connected(f"{ip}:{self._port}")
                except Exception:
                    pass

    def _cleanup_worker_list(self, lst, ip):
        """从 (QThread, worker) 列表移除并清理对应 ip 的条目。"""
        kept = []
        for t, w in lst:
            if getattr(w, '_ip', None) == ip:
                try:
                    if t.isRunning():
                        t.quit()
                        t.wait(2000)
                except Exception:
                    pass
                w.deleteLater()
                t.deleteLater()
            else:
                kept.append((t, w))
        lst.clear()
        lst.extend(kept)

    def _connect_all_found(self):
        """一键连接所有发现的设备（异步串行：避免一次性起 N 个 adb 进程卡死）。"""
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
        if getattr(self, '_closing', False):
            return
        # 串行触发：每台完成后由 _on_connect_done 自行递归下一台
        self._connect_all_queue = list(self._found_ips)
        self._connect_all_results = []
        self._set_status_for_ip  # keep linter quiet
        self.btn_connect_all.setEnabled(False)
        self.lbl_status.setText(f"批量连接中：0/{len(self._connect_all_queue)}")
        self._connect_queue_next()

    def _connect_queue_next(self):
        """从批量队列中取出一台发起连接（递归驱动）。"""
        if getattr(self, '_closing', False):
            return
        if not self._connect_all_queue:
            self.btn_connect_all.setEnabled(True)
            done = len(self._connect_all_results)
            total = done
            QMessageBox.information(self, "批量连接完成",
                                    f"已完成 {done} 台设备的连接请求。\n"
                                    f"详细结果已写入状态栏。")
            return
        ip = self._connect_all_queue.pop(0)
        before = len(self._connect_all_results)
        # 用自包装的一次性回调，结束后继续推下一台并刷新计数
        original_done_cb = self._on_connect_done

        def _wrapped(ok, msg, _ip=ip, _before=before):
            self._connect_all_results.append((_ip, ok, msg))
            self.lbl_status.setText(
                f"批量连接中：{len(self._connect_all_results)}/"
                f"{len(self._connect_all_results) + len(self._connect_all_queue)}"
            )
            self._connect_queue_next()

        # 直接绑到 worker 自身的 done 信号
        # 简化：用一个内部 worker + 临时 dict 路由
        self._enqueue_batch_connect(ip, _wrapped)

    def _enqueue_batch_connect(self, ip, callback):
        """批量连接版：start worker + 单独绑一次性回调。"""
        if getattr(self, '_closing', False):
            callback(False, "窗口已关闭")
            return
        btn = self._find_btn_for_ip(ip)
        if btn is not None:
            btn.setText("连接中...")
            btn.setEnabled(False)
            self._busy_buttons[ip] = btn
        self._set_status_for_ip(ip, "⏳ 连接中...", TIP_GRAY)
        worker = _ConnectWorker(ip, port=self._port, timeout=8)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def _done(ok, msg):
            if getattr(self, '_closing', False):
                return
            btn = self._busy_buttons.pop(ip, None)
            if btn is not None:
                btn.setText("连接")
                btn.setEnabled(True)
            color = ACCENT_COLOR_GREEN if ok else TIP_GRAY
            text = "🟢 在线" if ok else "❌ 离线/失败"
            self._set_status_for_ip(ip, text, color)
            self._cleanup_worker_list(self._connect_threads, ip)
            if ok:
                self._enrich_after_connect(ip)
                # 通知主窗口：刚连上一台新设备，刷新设备下拉框并自动选中它
                if callable(self._on_device_connected):
                    try:
                        self._on_device_connected(f"{ip}:{self._port}")
                    except Exception:
                        pass
            try:
                callback(ok, msg)
            except Exception:
                pass

        worker.done.connect(_done)
        # 跟踪并清理
        self._connect_threads.append((thread, worker))
        thread.start()

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
        """连接成功后异步回填机型名。

        使用 QObject + moveToThread 模式（旧版 threading.Thread + QTimer 跨线程投递
        在某些 PySide6 下不安全）。失败一律静默（不动 UI）。
        """
        if getattr(self, '_closing', False):
            return
        # 同一个 ip 短时间内多次触发（比如批量连接后再 enrich）→ 合并到一个 worker
        for t, w in self._enrich_threads:
            if getattr(w, '_ip', None) == ip:
                return
        worker = _EnrichWorker(ip, port=self._port, timeout=5, adb=self._adb)
        thread = QThread(self)

        def _on_done(_ip, name, _t=thread, _w=worker):
            # 先回填 UI
            if name and not getattr(self, '_closing', False):
                # 先尝试匹配「🟢 在线」/「🟢 在线 · XXX」行,加名称后缀
                target_row = None
                for r in range(self.table.rowCount()):
                    item = self.table.item(r, 0)
                    if item and item.text() == _ip:
                        target_row = r
                        break
                if target_row is not None:
                    st = self.table.item(target_row, 1)
                    if st is not None:
                        cur = st.text()
                        if name not in cur:
                            st.setText(f"{cur} · {name}" if not cur.endswith(' ·') else
                                       f"{cur.rstrip(' ·')} · {name}")
            # 清理本线程
            self._cleanup_worker_list(self._enrich_threads, _ip)

        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(_on_done)
        self._enrich_threads.append((thread, worker))
        thread.start()

    def cleanup(self):
        """停止所有后台线程（扫描 + 连接 + 回填），绝不留悬挂进程。

        从 closeEvent 抽取出来，供嵌入到统一无线调试面板时由父窗口调用
        （嵌入后本对话框不再是顶层窗口，closeEvent 不会触发）。
        """
        self._closing = True
        # 扫描线程
        if self._scan_thread and self._scan_thread.isRunning():
            if self._worker:
                self._worker.cancel()
            self._scan_thread.quit()
            self._scan_thread.wait(2000)
            self._scan_thread = None
            self._worker = None
        # 连接 worker 线程：cancel 后再 quit+wait(短超时),不会持续阻塞 UI
        for t, w in list(self._connect_threads):
            try:
                w.cancel()
                if t.isRunning():
                    t.quit()
                    t.wait(2000)
                w.deleteLater()
                t.deleteLater()
            except Exception:
                pass
        self._connect_threads.clear()
        # 机型回填 worker 线程
        for t, w in list(self._enrich_threads):
            try:
                w.cancel()
                if t.isRunning():
                    t.quit()
                    t.wait(1500)
                w.deleteLater()
                t.deleteLater()
            except Exception:
                pass
        self._enrich_threads.clear()

    def closeEvent(self, event):
        """关闭窗口时停止所有后台线程（扫描 + 连接 + 回填），绝不留悬挂进程。"""
        self.cleanup()
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
    dlg = 局域网扫描对话框()
    dlg.show()
    _sys.exit(app.exec())
