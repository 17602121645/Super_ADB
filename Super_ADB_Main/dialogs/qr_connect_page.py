# -*- coding: utf-8 -*-
"""
二维码连接页面
================
从「配对码连接」页拆分出来的独立标签页，集中管理：
  - 📷 扫码：从剪贴板图片或文件扫描手机无线调试二维码，自动填回配对页
  - 🔳 生成二维码：构造 WIFI:T:ADB;... 标准格式并弹窗/内嵌展示，供手机扫描

设计要点：
  - 作为 QWidget（非 QDialog）嵌入 QTabWidget，不独占窗口。
  - 扫码结果通过回调 on_scan_result(ip, port, code) 转发给配对页填字段。
  - 生成二维码支持「弹窗模式」（大图方便手机扫）和「内嵌预览」（本页直接看）。
"""

import io
import random
import re
import socket

from PySide6.QtCore import Qt, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QGroupBox, QTextEdit, QFileDialog, QMessageBox,
    QSizePolicy, QCheckBox, QFormLayout,
)

import png_rc  # noqa: F401
from popup_style import add_green_glow
from 界面样式 import ACCENT, STYLE_SHEET


class QrConnectPage(QWidget):
    """二维码连接标签页：扫码 + 生成二维码。"""

    def __init__(self, parent=None, pair_dialog=None):
        """
        Args:
            parent: 父 widget（QTabWidget）
            pair_dialog: WifiPairDialog 实例引用，扫码成功后回调填入其输入框
        """
        super().__init__(parent)
        self._pair_dialog = pair_dialog
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
            "📷 扫码：扫描手机「无线调试 → 使用二维码配对设备」的二维码，自动填入配对信息\n"
            "🔳 生成：将当前配对信息生成为标准二维码，用手机相机扫描即可获取")
        tip.setWordWrap(True)
        tip.setStyleSheet(f"color:{ACCENT}; font-size:12px;")
        root.addWidget(tip)

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

        # ═════════════════════════════════════════════════════
        # 区块 B：生成二维码
        # ═════════════════════════════════════════════════════
        gen_g = QGroupBox("🔳 生成二维码（PC → 手机）")
        gv = QVBoxLayout(gen_g)

        # 输入行：IP / 端口 / 配对码
        form = QFormLayout()
        form.setSpacing(8)

        self.gen_ip = QLineEdit()
        self.gen_ip.setPlaceholderText("自动检测本机局域网 IP，或手动输入")
        self.gen_ip.setText(self._get_lan_ip())
        form.addRow("IP 地址：", self.gen_ip)

        self.gen_port = QLineEdit()
        self.gen_port.setPlaceholderText("配对端口，例如 38973")
        self.gen_port.setMaximumWidth(120)
        form.addRow("配对端口：", self.gen_port)

        self.gen_code = QLineEdit()
        self.gen_code.setPlaceholderText("6 位配对码，留空则自动随机生成")
        self.gen_code.setMaxLength(6)
        self.gen_code.setMaximumWidth(120)
        form.addRow("配对码：", self.gen_code)

        gv.addLayout(form)

        # 操作按钮行
        gh = QHBoxLayout()

        self.btn_gen_qr = QPushButton("✨ 生成二维码")
        self.btn_gen_qr.setProperty("class", "accentBtn")
        self.btn_gen_qr.setToolTip("根据上方信息生成 Android 无线调试标准格式二维码")
        self.btn_gen_qr.clicked.connect(self._generate_qr)
        gh.addWidget(self.btn_gen_qr)

        self.chk_auto_fill = QCheckBox("自动同步到配对页")
        self.chk_auto_fill.setChecked(True)
        self.chk_auto_fill.setToolTip("生成时自动把 IP/端口/配对码同步到「配对码连接」标签页")
        gh.addWidget(self.chk_auto_fill)

        gh.addStretch()
        gv.addLayout(gh)

        # 二维码预览区
        self.qr_preview_label = QLabel()
        self.qr_preview_label.setAlignment(Qt.AlignCenter)
        self.qr_preview_label.setMinimumSize(220, 220)
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

        # 说明
        note = QLabel(
            "格式说明：WIFI:T:ADB;S:<IP:端口>;<配对码>;; （Android 无线调试标准 Wi-Fi 配对格式）\n"
            "• 手机相机或任意扫码 App 可直接识别此二维码\n"
            "• 在 Android Studio 中对应「Pair Devices Using Wi-Fi」功能")
        note.setWordWrap(True)
        note.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
        gv.addWidget(note)

        root.addWidget(gen_g, 1)

        # 内部状态（供扫码结果回填）
        self._last_scan_ip = ''
        self._last_scan_port = ''
        self._last_scan_code = ''
        self._last_qr_pix = None      # QPixmap | None
        self._last_qr_payload = ''    # str

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
    # 生成二维码
    # ══════════════════════════════════════════════════════════
    def _get_lan_ip(self):
        """获取本机局域网 IPv4。"""
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

    def _build_qr_payload(self):
        """构造 WIFI:T:ADB;S:<ip:port>;P:<code>;; 标准载荷。"""
        ip = self.gen_ip.text().strip() or self._get_lan_ip()
        port = self.gen_port.text().strip() or '5555'
        code = self.gen_code.text().strip()
        if not re.match(r"^\d{6}$", code):
            code = f"{random.randint(0, 999999):06d}"
            self.gen_code.setText(code)
        name = f"{ip}:{port}"
        payload = f"WIFI:T:ADB;S:{name};P:{code};;"
        return payload, ip, port, code

    def _generate_qr(self):
        """生成二维码并在本页预览。"""
        payload, ip, port, code = self._build_qr_payload()
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
        scaled = pix.scaled(
            preview_size.width() - 28, preview_size.height() - 28,
            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.qr_preview_label.setPixmap(scaled)

        # 显示 payload
        self.qr_payload_text.setPlainText(payload)

        # 保存状态
        self._last_qr_pix = pix       # 保留原图用于弹窗
        self._last_qr_payload = payload
        self.btn_copy_payload.setEnabled(True)
        self.btn_popup_qr.setEnabled(True)

        self._log_scan(f"✅ 二维码已生成 — 目标 {ip}:{port} / 配对码 {code}")

        # 自动同步到配对页
        if self.chk_auto_fill.isChecked() and self._pair_dialog:
            self._pair_dialog.ip_edit.setText(ip)
            self._pair_dialog.port_edit.setText(port)
            self._pair_dialog.code_edit.setText(code)

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
        _, ip, port, code = self._build_qr_payload()

        dlg = QDialog(self)
        dlg.setWindowTitle("扫码配对二维码")
        dlg.setWindowIcon(QIcon(":/Super_ADB.png"))
        dlg.setMinimumWidth(400)
        dlg.setMinimumHeight(560)
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

        info = QLabel(f"连接目标：<b>{ip}:{port}</b>&nbsp;&nbsp;配对码：<b>{code}</b>")
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
            "用手机相机或任意扫码 App 扫描即可读取上面的连接目标与配对码。")
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
        """占位 cleanup（本页无后台线程），保持接口一致。"""
        pass


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    w = QrConnectPage()
    w.resize(640, 700)
    w.show()
    sys.exit(app.exec())
