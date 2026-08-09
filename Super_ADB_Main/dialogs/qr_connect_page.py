# -*- coding: utf-8 -*-
"""
二维码连接页面
================
从「配对码连接」页拆分出来的独立标签页，集中管理：
  - 📷 扫码：从剪贴板图片或文件扫描手机无线调试二维码，自动填回配对页
  - 🔳 生成二维码：生成 Android 无线调试标准格式的二维码，供手机扫描并自动配对

设计要点：
  - 作为 QWidget（非 QDialog）嵌入 QTabWidget，不独占窗口。
  - 扫码结果通过回调 on_scan_result(ip, port, code) 转发给配对页填字段。
  - 生成二维码支持「弹窗模式」（大图方便手机扫）和「内嵌预览」（本页直接看）。

关于「生成二维码让手机扫描」的配对原理（关键，曾踩坑）：
  - 二维码格式必须为 WIFI:T:ADB;S:<服务名>;P:<配对码>;;
    S 是「服务实例名」（随机字符串），**绝不能填 PC 的 IP:端口**。
  - 手机用「无线调试 → 使用二维码配对设备」扫描后，会按这个名字在局域网
    广播一条 mDNS 配对服务（_adb-tls-pairing._tcp）。
  - 因此 PC 端必须启动 mDNS 浏览器监听该服务，发现后用服务里的手机
    IP:端口 执行 `adb pair <手机IP:端口> <配对码>`，配对才算完成。
  - 旧实现把 S 填成 IP:端口、且没有 mDNS 监听，所以手机扫描毫无反应。
"""

import io
import random
import re
import socket

from PySide6.QtCore import (
    Qt, QSize, QByteArray, QBuffer, QIODevice, Signal, QObject, QThread)
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QTextEdit, QFileDialog, QMessageBox,
    QSizePolicy, QCheckBox, QFormLayout,
)

import png_rc  # noqa: F401
from popup_style import add_green_glow
from 界面样式 import ACCENT, STYLE_SHEET


# ───────────────────────────────────────────────────────────────
# mDNS 配对服务监听（zeroconf）
# ───────────────────────────────────────────────────────────────
class _MdnsBridge(QObject):
    """把 zeroconf 后台线程的发现事件安全转发到 Qt 主线程。"""
    discovered = Signal(str, str, int)   # name, ip, port


class _PairingMdnsListener:
    """监听 _adb-tls-pairing._tcp，匹配指定服务名后回调 on_discovered。"""

    def __init__(self, expected_name, on_discovered):
        self.expected_name = expected_name          # 例如 superadb-AB12CD
        self.on_discovered = on_discovered          # (name, ip, port)
        self._seen = set()

    @staticmethod
    def _extract_ipv4(info, lan_ip):
        """从 ServiceInfo 提取手机 IPv4（优先与 PC 同网段）。"""
        candidates = []
        raw = getattr(info, 'addresses', None) or []
        for a in raw:
            if isinstance(a, (bytes, bytearray)) and len(a) == 4:
                candidates.append(socket.inet_ntoa(bytes(a)))
        ips = getattr(info, 'ip_addresses', None) or []
        for a in ips:
            s = str(a)
            if ':' not in s:                 # 只要 IPv4
                candidates.append(s)
        if not candidates:
            return None
        if lan_ip:
            subnet = '.'.join(lan_ip.split('.')[:3])
            for c in candidates:
                if c.startswith(subnet + '.'):
                    return c
        return candidates[0]

    def add_service(self, zc, type_, name):
        if name in self._seen:
            return
        self._seen.add(name)
        # 只关注我们生成的服务名（完整名形如 superadb-AB12CD._adb-tls-pairing._tcp.local.）
        if not name.startswith(self.expected_name + '.'):
            return
        try:
            info = zc.get_service_info(type_, name)
        except Exception:
            info = None
        if not info:
            return
        ip = self._extract_ipv4(info, _lan_ip_hint())
        port = info.port
        if ip and port:
            try:
                self.on_discovered(name, ip, port)
            except Exception:
                pass

    def update_service(self, zc, type_, name):
        # 首次发现后 ServiceBrowser 也会回调 update，复用 add 逻辑即可
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass


def _lan_ip_hint():
    """获取本机局域网 IPv4，用于挑选同网段地址。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        try:
            s.close()
        except Exception:
            pass


# ───────────────────────────────────────────────────────────────
# 后台执行 adb pair
# ───────────────────────────────────────────────────────────────
class _QrPairWorker(QObject):
    done = Signal(bool, str)   # ok, message

    def __init__(self, target, code, timeout=20):
        super().__init__()
        self._target = target
        self._code = code
        self._timeout = timeout

    def run(self):
        try:
            from adb_utils import AdbHelper
            ok, msg = AdbHelper().pair(self._target, self._code, timeout=self._timeout)
            self.done.emit(ok, msg)
        except Exception as e:
            self.done.emit(False, f"❌ 配对异常：{e}")


class QrConnectPage(QWidget):
    """二维码连接标签页：扫码 + 生成二维码（手机扫描后自动配对）。"""

    def __init__(self, parent=None, pair_dialog=None, on_pair_success=None):
        """
        Args:
            parent: 父 widget（QTabWidget）
            pair_dialog: WifiPairDialog 实例引用，扫码成功后回调填入其输入框
            on_pair_success: 二维码自动配对成功后回调，用于刷新主窗口设备列表
        """
        super().__init__(parent)
        self._pair_dialog = pair_dialog
        self._on_pair_success = on_pair_success

        # 生成二维码相关的内部状态
        self._service_name = ''     # 本次二维码对应的 mDNS 服务名
        self._code = ''             # 本次二维码对应的 6 位配对码
        self._waiting = False       # 是否正在 mDNS 等待手机扫描

        # mDNS / 配对后台句柄
        self._zc = None
        self._browser = None
        self._listener = None
        self._mdns_bridge = None
        self._qr_pair_worker = None
        self._qr_pair_thread = None

        # 扫码结果回填状态
        self._last_scan_ip = ''
        self._last_scan_port = ''
        self._last_scan_code = ''
        self._last_qr_pix = None      # QPixmap | None
        self._last_qr_payload = ''    # str

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET)

    # ══════════════════════════════════════════════════════════
    # UI 构建
    # ══════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 说明 ──
        tip = QLabel(
            "📷 扫码：扫描手机「无线调试 → 使用二维码配对设备」弹出的二维码，自动提取 IP/端口/配对码\n"
            "🔳 生成：生成本机的配对二维码，用手机「使用二维码配对设备」扫描后，Super_ADB 自动完成配对")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{ACCENT}; font-size:12px;")
        root.addWidget(tip)

        # ═════════════════════════════════════════════════════
        # 区块 B：生成二维码（手机扫描 → 自动配对）
        # ═════════════════════════════════════════════════════
        gen_g = QGroupBox("🔳 生成二维码（PC → 手机，扫描后自动配对）")
        gv = QVBoxLayout(gen_g)

        hint = QLabel(
            "生成后请用手机「无线调试 → 使用二维码配对设备」扫描本二维码，"
            "Super_ADB 会自动发现手机并完成 adb pair，无需手动输入。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        gv.addWidget(hint)

        # 输入行：配对码（可选自动）
        form = QFormLayout()
        form.setSpacing(8)

        self.gen_code = QLineEdit()
        self.gen_code.setPlaceholderText("6 位配对码，留空则自动随机生成")
        self.gen_code.setMaxLength(6)
        self.gen_code.setMaximumWidth(120)
        form.addRow("配对码：", self.gen_code)

        gv.addLayout(form)

        # 操作按钮行
        gh = QHBoxLayout()

        self.btn_gen_qr = QPushButton("✨ 生成二维码并开始等待")
        self.btn_gen_qr.setProperty("class", "accentBtn")
        self.btn_gen_qr.setToolTip("生成二维码，并启动 mDNS 监听等待手机扫描")
        self.btn_gen_qr.clicked.connect(self._generate_qr)
        gh.addWidget(self.btn_gen_qr)

        self.btn_stop_wait = QPushButton("⏹ 停止等待")
        self.btn_stop_wait.setToolTip("停止 mDNS 监听，不再自动配对")
        self.btn_stop_wait.setEnabled(False)
        self.btn_stop_wait.clicked.connect(self._stop_waiting)
        gh.addWidget(self.btn_stop_wait)

        gh.addStretch()
        gv.addLayout(gh)

        # 二维码预览区
        self.qr_preview_label = QLabel()
        self.qr_preview_label.setAlignment(Qt.AlignCenter)
        self.qr_preview_label.setMinimumSize(160, 160)
        self.qr_preview_label.setMaximumHeight(260)
        self.qr_preview_label.setStyleSheet(
            "background:#ffffff; border-radius:10px; border:1px solid #333;")
        self.qr_preview_label.setText("二维码预览区\n（点击上方按钮生成）")
        gv.addWidget(self.qr_preview_label, 1)

        # payload 原文 + 复制
        self.qr_payload_text = QTextEdit()
        self.qr_payload_text.setReadOnly(True)
        self.qr_payload_text.setMaximumHeight(50)
        self.qr_payload_text.setPlaceholderText("生成的二维码原始文本…")
        gv.addWidget(self.qr_payload_text)

        gcopy = QHBoxLayout()
        gcopy.addStretch()
        self.btn_copy_payload = QPushButton("📋 复制二维码内容")
        self.btn_copy_payload.setEnabled(False)
        self.btn_copy_payload.clicked.connect(self._copy_payload)
        gcopy.addWidget(self.btn_copy_payload)

        self.btn_popup_qr = QPushButton("🔍 弹窗大图（方便手机扫）")
        self.btn_popup_qr.setEnabled(False)
        self.btn_popup_qr.clicked.connect(self._popup_qr)
        gcopy.addWidget(self.btn_popup_qr)
        gv.addLayout(gcopy)

        # 等待状态行
        self.wait_status = QLabel("状态：待生成二维码")
        self.wait_status.setWordWrap(True)
        self.wait_status.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        gv.addWidget(self.wait_status)

        # 说明
        note = QLabel(
            "格式说明：WIFI:T:ADB;S:<服务名>;P:<配对码>;; （Android 无线调试标准格式）\n"
            "• 手机扫描后会在局域网广播名为「<服务名>」的 mDNS 配对服务（_adb-tls-pairing._tcp）\n"
            "• Super_ADB 监听到该服务后自动执行 adb pair，无需手动输入")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        gv.addWidget(note)

        root.addWidget(gen_g, 1)

        # ═════════════════════════════════════════════════════
        # 区块 A：扫码
        # ═════════════════════════════════════════════════════
        scan_g = QGroupBox("📷 扫描二维码（手机 → PC）")
        sv = QVBoxLayout(scan_g)

        sh = QHBoxLayout()
        self.btn_scan_clip = QPushButton("📋 从剪贴板图片扫码")
        self.btn_scan_clip.setToolTip(
            "直接读取剪贴板里的图片（截图后无需保存文件），识别其中的二维码内容")
        self.btn_scan_clip.clicked.connect(lambda: self._scan_qr(from_clipboard=True))
        sh.addWidget(self.btn_scan_clip)

        self.btn_scan_file = QPushButton("📂 选择图片文件扫码")
        self.btn_scan_file.setToolTip("选择一张包含二维码的 PNG/JPG/BMP 图片文件进行识别")
        self.btn_scan_file.clicked.connect(lambda: self._scan_qr(from_clipboard=False))
        sh.addWidget(self.btn_scan_file)

        sh.addStretch()
        sv.addLayout(sh)

        # 扫码结果展示
        self.scan_result = QTextEdit()
        self.scan_result.setReadOnly(True)
        self.scan_result.setMaximumHeight(80)
        self.scan_result.setPlaceholderText("扫码结果会显示在这里…")
        sv.addWidget(self.scan_result)

        # 一键填入配对页按钮
        sh2 = QHBoxLayout()
        sh2.addStretch()
        self.btn_fill_pair = QPushButton("📥 填入配对页")
        self.btn_fill_pair.setToolTip("将扫码结果中的 IP / 端口 / 配对码自动填入「配对码连接」标签页")
        self.btn_fill_pair.setEnabled(False)
        self.btn_fill_pair.clicked.connect(self._fill_to_pair)
        sh2.addWidget(self.btn_fill_pair)
        sv.addLayout(sh2)

        root.addWidget(scan_g)

    # ══════════════════════════════════════════════════════════
    # 扫码
    # ══════════════════════════════════════════════════════════
    def _scan_qr(self, from_clipboard=True):
        """扫描二维码：优先剪贴板图片，否则选文件。"""
        from PySide6.QtWidgets import QApplication

        img = None
        if from_clipboard:
            img = QApplication.clipboard().image()
            if img.isNull():
                self._log_scan("⚠️ 剪贴板中没有图片，请先截图复制")
                return
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择二维码图片", "",
                "图片 (*.png *.jpg *.jpeg *.bmp)")
            if not path:
                return
            import cv2
            import numpy as np
            arr = cv2.imread(path)
            if arr is None:
                self._log_scan("⚠️ 无法读取图片文件")
                return
            text = self._decode_qr_from_array(arr)
            self._handle_scan_result(text)
            return

        # 剪贴板有图 → 转 numpy 再解码
        text = self._decode_qr_from_qimage(img)
        self._handle_scan_result(text)

    def _decode_qr_from_qimage(self, qimg):
        """QImage → OpenCV array → QR decode。"""
        import numpy as np
        import cv2
        buf = QBuffer()
        buf.open(QIODevice.ReadWrite)
        qimg.save(buf, "PNG")
        data = np.frombuffer(buf.data().data(), dtype=np.uint8)
        arr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if arr is None:
            return ''
        return self._decode_qr_from_array(arr)

    def _decode_qr_from_array(self, arr):
        """OpenCV array → QR detectAndDecode。"""
        import cv2
        try:
            detector = cv2.QRCodeDetector()
            data, _pts, _ = detector.detectAndDecode(arr)
            return (data or '').strip()
        except Exception as e:
            self._log_scan(f"⚠️ 二维码解码失败：{e}")
            return ''

    def _handle_scan_result(self, text):
        """处理扫码结果：显示 + 解析 + 启用「填入配对页」按钮。"""
        if not text:
            self._log_scan("⚠️ 未能从图片中识别二维码（请确认截图清晰、二维码完整）")
            return

        self._log_scan(f"📷 二维码内容：{text[:200]}")

        # 解析 IP:端口
        m_ip = re.search(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{1,5})", text)
        if m_ip:
            self._last_scan_ip = m_ip.group(1)
            self._last_scan_port = m_ip.group(2)
        else:
            self._last_scan_ip = ''
            self._last_scan_port = ''

        # 解析 6 位配对码
        rest = text[m_ip.end():] if m_ip else text
        m_code = re.search(r"\b(\d{6})\b", rest)
        self._last_scan_code = m_code.group(1) if m_code else ''

        # 显示解析摘要
        parts = []
        if self._last_scan_ip:
            parts.append(f"IP: {self._last_scan_ip}")
        if self._last_scan_port:
            parts.append(f"端口: {self._last_scan_port}")
        if self._last_scan_code:
            parts.append(f"配对码: {self._last_scan_code}")
        if parts:
            self._log_scan(f"✅ 识别到：{' | '.join(parts)}")
            self.btn_fill_pair.setEnabled(True)
        else:
            self._log_scan("⚠️ 二维码中未找到 IP:端口 或 6 位配对码")

    def _fill_to_pair(self):
        """将最近一次扫码结果填入配对页的输入框。"""
        if not self._pair_dialog:
            self._log_scan("⚠️ 未关联配对页，无法自动填入")
            return
        filled = []
        if self._last_scan_ip:
            self._pair_dialog.ip_edit.setText(self._last_scan_ip)
            filled.append("IP")
        if self._last_scan_port:
            self._pair_dialog.port_edit.setText(self._last_scan_port)
            filled.append("端口")
        if self._last_scan_code:
            self._pair_dialog.code_edit.setText(self._last_scan_code)
            filled.append("配对码")
        if filled:
            self._log_scan(f"✅ 已填入配对页：{', '.join(filled)}")
            # 切换到配对页让用户看到
            parent_tab = self.parent()
            while parent_tab and not hasattr(parent_tab, 'setCurrentIndex'):
                parent_tab = parent_tab.parent()
            if parent_tab is not None:
                try:
                    idx = parent_tab.indexOf(self._pair_dialog)
                    if idx >= 0:
                        parent_tab.setCurrentIndex(idx)
                except Exception:
                    pass
        else:
            self._log_scan("⚠️ 没有可填入的数据")

    # ══════════════════════════════════════════════════════════
    # 生成二维码 + mDNS 自动配对
    # ══════════════════════════════════════════════════════════
    def _build_qr_payload(self):
        """构造 WIFI:T:ADB;S:<服务名>;P:<配对码>;; 标准载荷。

        关键：S 必须是随机服务名（非 IP:端口），手机扫描后会按此名广播
        mDNS 配对服务，PC 端才能用 mDNS 发现并执行 adb pair。
        """
        code = self.gen_code.text().strip()
        if not re.match(r"^\d{6}$", code):
            code = f"{random.randint(0, 999999):06d}"
            self.gen_code.setText(code)
        # 服务名：固定前缀 + 6 位无歧义字符（去除 0/O/1/I 等易混字符）
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        name = 'superadb-' + ''.join(random.choices(alphabet, k=6))
        payload = f"WIFI:T:ADB;S:{name};P:{code};;"
        return payload, name, code

    def _generate_qr(self):
        """生成二维码并在本页预览，随后启动 mDNS 等待手机扫描。"""
        payload, name, code = self._build_qr_payload()
        self._service_name = name
        self._code = code
        try:
            import segno
        except Exception as e:
            QMessageBox.warning(
                self, "缺少依赖",
                f"二维码生成库 segno 未安装：{e}\n请执行：pip install segno")
            return
        try:
            buf = io.BytesIO()
            qr = segno.make(payload, error='m')
            qr.save(buf, kind='png', scale=10, border=2,
                    dark='#0b0e14', light='#ffffff')
            png = buf.getvalue()
            img = QImage.fromData(QByteArray(png))
            pix = QPixmap.fromImage(img)
        except Exception as e:
            self._log_scan(f"⚠️ 生成二维码失败：{e}")
            return
        if pix.isNull():
            self._log_scan("⚠️ 二维码图像渲染失败")
            return

        # 缩放到预览区合适大小（保持比例）
        preview_size = self.qr_preview_label.size()
        max_h = self.qr_preview_label.maximumHeight()
        if max_h > 0:
            preview_size.setHeight(min(preview_size.height(), max_h))
        # 未布局时给个默认尺寸
        if preview_size.width() <= 28 or preview_size.height() <= 28:
            preview_size = QSize(220, 220)
        scaled = pix.scaled(
            preview_size.width() - 28, preview_size.height() - 28,
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.qr_preview_label.setText("")
        self.qr_preview_label.setPixmap(scaled)

        # 显示 payload
        self.qr_payload_text.setPlainText(payload)

        # 保存状态
        self._last_qr_pix = pix       # 保留原图用于弹窗
        self._last_qr_payload = payload
        self.btn_copy_payload.setEnabled(True)
        self.btn_popup_qr.setEnabled(True)

        self._log_scan(f"✅ 二维码已生成 — 服务名 {name} / 配对码 {code}")
        self._log_scan("👉 请打开手机「无线调试 → 使用二维码配对设备」扫描上方二维码")

        # 启动 mDNS 等待手机扫描
        self._start_waiting()

    def _start_waiting(self):
        """启动 mDNS 浏览器监听 _adb-tls-pairing._tcp，等待手机扫描后广播。"""
        self._stop_waiting()  # 先停掉上一次（若重复点击生成）
        if not self._service_name or not self._code:
            return
        try:
            from zeroconf import Zeroconf, ServiceBrowser, ServiceListener  # noqa: F401
        except Exception as e:
            self._log_scan(
                f"⚠️ 缺少 mDNS 依赖 zeroconf：{e}（无法自动发现手机，可改用「配对码连接」页手动配对）")
            self.wait_status.setText("状态：未安装 zeroconf，无法自动监听；请用「配对码连接」页手动配对")
            return

        self._mdns_bridge = _MdnsBridge()
        self._mdns_bridge.discovered.connect(self._on_discovered)
        self._listener = _PairingMdnsListener(
            self._service_name, self._mdns_bridge.discovered.emit)
        try:
            self._zc = Zeroconf()
            self._browser = ServiceBrowser(
                self._zc, "_adb-tls-pairing._tcp.local.", self._listener)
        except Exception as e:
            self._log_scan(f"⚠️ 启动 mDNS 监听失败：{e}")
            self.wait_status.setText(f"状态：mDNS 启动失败：{e}")
            self._zc = None
            return

        self._waiting = True
        self.btn_stop_wait.setEnabled(True)
        self.wait_status.setText(
            f"状态：等待手机扫描二维码…\n"
            f"正在监听服务名 {self._service_name} 的 mDNS 广播（_adb-tls-pairing._tcp）")
        self._log_scan("👂 已启动 mDNS 监听，等待手机扫描二维码后广播配对服务…")

    def _stop_waiting(self):
        """停止 mDNS 监听，释放 zeroconf 资源。"""
        self._waiting = False
        self.btn_stop_wait.setEnabled(False)
        if self._browser is not None:
            try:
                self._browser.cancel()
            except Exception:
                pass
            self._browser = None
        if self._zc is not None:
            try:
                self._zc.close()
            except Exception:
                pass
            self._zc = None
        self._listener = None
        self._mdns_bridge = None

    def _on_discovered(self, name, ip, port):
        """mDNS 发现手机配对服务 → 停止监听 → 后台执行 adb pair。"""
        if not self._waiting:
            return
        self._stop_waiting()
        self._log_scan(f"📱 已发现手机配对服务：{name} @ {ip}:{port}")
        self.wait_status.setText(
            f"状态：已发现手机 {ip}:{port}，正在执行 adb pair …")

        self._qr_pair_worker = _QrPairWorker(f"{ip}:{port}", self._code, timeout=20)
        self._qr_pair_thread = QThread(self)
        self._qr_pair_worker.moveToThread(self._qr_pair_thread)
        self._qr_pair_thread.started.connect(self._qr_pair_worker.run)
        self._qr_pair_worker.done.connect(
            lambda ok, msg: self._on_qr_pair_done(ok, msg, ip, port))
        self._qr_pair_thread.start()

    def _on_qr_pair_done(self, ok, msg, ip, port):
        """adb pair 结果处理：刷新状态 + 通知主窗口刷新设备列表。"""
        self._log_scan(msg)
        if ok:
            self.wait_status.setText(
                f"✅ 配对成功：{ip}:{port}（adb 将自动连接，设备列表即将刷新）")
            self._log_scan(
                f"✅ 二维码配对成功，手机 {ip}:{port} 已配对，可前往主界面查看设备")
            if callable(self._on_pair_success):
                try:
                    self._on_pair_success()
                except Exception:
                    pass
        else:
            self.wait_status.setText(f"❌ 配对失败：{msg[:120]}")
            self._log_scan(
                "💡 提示：请确认手机已扫描本页二维码、且双方处于同一 Wi-Fi；"
                "也可改用「配对码连接」页手动输入配对")

    def _copy_payload(self):
        """复制二维码原始文本到剪贴板。"""
        from PySide6.QtWidgets import QApplication
        if self._last_qr_payload:
            QApplication.clipboard().setText(self._last_qr_payload)
            self.btn_copy_payload.setText("已复制 ✅")

    def _popup_qr(self):
        """弹窗展示大尺寸二维码（方便手机相机扫描）。"""
        if not self._last_qr_pix or not self._last_qr_payload:
            return
        payload = self._last_qr_payload
        name = self._service_name
        code = self._code

        dlg = QDialog(self)
        dlg.setWindowTitle("扫码配对二维码")
        dlg.setWindowIcon(QIcon(":/Super_ADB.png"))
        add_green_glow(dlg)

        root = QVBoxLayout(dlg)
        root.setSpacing(12)
        root.setContentsMargins(20, 20, 20, 20)

        title = QLabel("📱 用手机扫描此二维码")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size:15px; font-weight:bold;")
        root.addWidget(title)

        # 大图白色卡片
        qr_card = QLabel()
        qr_card.setAlignment(Qt.AlignCenter)
        qr_card.setStyleSheet(
            "background:#ffffff; border-radius:12px; padding:20px;")

        # 弹窗里用更大尺寸
        big = self._last_qr_pix.scaled(320, 320, Qt.KeepAspectRatio,
                                        Qt.SmoothTransformation)
        qr_card.setPixmap(big)
        root.addWidget(qr_card)

        info = QLabel(f"服务名：<b>{name}</b>&nbsp;&nbsp;配对码：<b>{code}</b>")
        info.setAlignment(Qt.AlignCenter)
        info.setStyleSheet("font-size:13px;")
        root.addWidget(info)

        raw = QTextEdit()
        raw.setReadOnly(True)
        raw.setPlainText(payload)
        raw.setMaximumHeight(56)
        root.addWidget(raw)

        cpy = QHBoxLayout()
        cpy.addStretch()
        cbtn = QPushButton("📋 复制二维码内容")
        cbtn.clicked.connect(lambda: __import__('PySide6.QtWidgets')
                             .QApplication.clipboard().setText(payload))
        cpy.addWidget(cbtn)
        cpy.addStretch()
        root.addLayout(cpy)

        note = QLabel(
            "说明：本二维码采用 Android 无线调试标准格式（WIFI:T:ADB;...）。\n"
            "请用手机「无线调试 → 使用二维码配对设备」扫描，Super_ADB 会自动完成配对。")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        root.addWidget(note)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        root.addWidget(close_btn)

        dlg.exec()

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════
    def _log_scan(self, text):
        self.scan_result.append(text)

    def cleanup(self):
        """停止 mDNS 监听与后台配对线程（嵌入统一面板时由父窗口调用）。"""
        self._stop_waiting()
        if self._qr_pair_thread is not None and self._qr_pair_thread.isRunning():
            try:
                self._qr_pair_thread.quit()
                self._qr_pair_thread.wait(2000)
            except Exception:
                pass
        self._qr_pair_thread = None
        self._qr_pair_worker = None


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    import sys
    app = QApplication([])
    w = QrConnectPage()
    w.resize(640, 720)
    w.show()
    sys.exit(app.exec())
