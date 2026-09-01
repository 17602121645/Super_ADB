# -*- coding: utf-8 -*-
"""
WiFi 密码查看器弹窗
==================
点击主界面「便捷工具 → WiFi 密码」弹出的独立窗口：
- 后台线程读取本机已保存的全部 WiFi 配置（不阻塞 UI）
- 统计卡片 + 表格化展示：SSID / 密码 / 认证方式 / 加密 / 状态
- 密码默认掩码，一键切换明文；支持搜索过滤、单条复制、批量复制、导出
- 内置「环境诊断」，直接定位"为什么这台电脑读不到 WiFi 密码"
"""

import csv
from tools.json_io import save_json
import os
import sys

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QIcon, QColor, QFont
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QWidget,
    QLineEdit, QPushButton, QGroupBox, QTableWidget, QTableWidgetItem,
    QProgressBar, QHeaderView, QMessageBox, QAbstractItemView, QFileDialog,
    QSizePolicy,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import ACCENT, get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import add_green_glow, highlight_card_style, _create_popup_card
from tools import wifi_tools

# ── 语义色 ──
C_OK = QColor("#00CC66")        # 取到密码
C_OPEN = QColor("#8899AA")      # 开放网络
C_WARN = QColor("#FFA940")      # 无密码但有原因
C_FAIL = QColor("#FF6B6B")      # 读取失败


class _LoadWorker(QObject):
    """后台读取 WiFi 配置，逐条汇报进度。"""

    one = Signal(int, int, object)   # done, total, detail
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, workers=8):
        super().__init__()
        self._workers = workers
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            data = wifi_tools.collect_all(
                workers=self._workers,
                progress_cb=lambda d, t, item: self.one.emit(d, t, item),
                should_stop=lambda: self._cancelled,
            )
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))
            return
        if not self._cancelled:
            self.finished.emit(data)


class _StatCard(QFrame):
    """顶部统计卡片：一个大数字 + 一行说明。"""

    def __init__(self, title, color, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QFrame {{ background: rgba(255,255,255,0.04);"
            f" border: 1px solid {color}; border-radius: 8px; }}")
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        v = QVBoxLayout(self)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(2)

        self.value_lbl = QLabel("—")
        f = QFont()
        f.setPointSize(17)
        f.setBold(True)
        self.value_lbl.setFont(f)
        self.value_lbl.setStyleSheet(f"color: {color}; border: none; background: transparent;")
        self.value_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self.value_lbl)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #9AA5B1; font-size: 11px; border: none; background: transparent;")
        title_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(title_lbl)

    def set_value(self, v):
        self.value_lbl.setText(str(v))


class WiFi对话框(QDialog):
    COL_SSID, COL_PWD, COL_AUTH, COL_CIPHER, COL_NOTE, COL_OP = range(6)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WiFi 密码查看器")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumSize(940, 600)
        self._theme_id = get_current_theme_id(self)
        self._accent = THEMES[self._theme_id]['accent']
        self.setStyleSheet(get_stylesheet(self._theme_id))
        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, self._theme_id)

        self._thread = None
        self._worker = None
        self._closing = False
        self._data = []
        self._show_plain = False

        self._build_ui()
        self._start_load()

    # ══════════════════════════════════════════════════════════
    # 主题切换
    # ══════════════════════════════════════════════════════════
    def apply_theme(self, theme_id):
        """运行时切换主题。"""
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self._theme_id = theme_id
        self._accent = THEMES[theme_id]['accent']
        self.setStyleSheet(get_stylesheet(theme_id))
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(self._accent))
        self.update()

    # ══════════════════════════════════════════════════════════
    # UI
    # ══════════════════════════════════════════════════════════
    def _build_ui(self):
        root = QVBoxLayout(self.card)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 统计卡片 ──
        h_stat = QHBoxLayout()
        h_stat.setSpacing(10)
        self.card_total = _StatCard("已保存 WiFi", "#4A9EFF")
        self.card_ok = _StatCard("成功获取密码", "#00CC66")
        self.card_open = _StatCard("开放网络", "#8899AA")
        self.card_fail = _StatCard("无法获取", "#FFA940")
        for c in (self.card_total, self.card_ok, self.card_open, self.card_fail):
            h_stat.addWidget(c)
        root.addLayout(h_stat)

        # ── 工具栏 ──
        h_bar = QHBoxLayout()
        h_bar.setSpacing(8)

        self.btn_refresh = QPushButton("🔄 刷新")
        self.btn_refresh.setMinimumHeight(32)
        self.btn_refresh.clicked.connect(self._start_load)
        h_bar.addWidget(self.btn_refresh)

        self.btn_eye = QPushButton("👁 显示密码")
        self.btn_eye.setMinimumHeight(32)
        self.btn_eye.setCheckable(True)
        self.btn_eye.setToolTip("在掩码与明文之间切换")
        self.btn_eye.clicked.connect(self._toggle_plain)
        h_bar.addWidget(self.btn_eye)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 搜索 WiFi 名称或密码…")
        self.search.setMinimumHeight(32)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        h_bar.addWidget(self.search, 1)

        self.btn_copy = QPushButton("📋 复制全部")
        self.btn_copy.setMinimumHeight(32)
        self.btn_copy.clicked.connect(self._copy_all)
        h_bar.addWidget(self.btn_copy)

        self.btn_export = QPushButton("💾 导出")
        self.btn_export.setMinimumHeight(32)
        self.btn_export.clicked.connect(self._export)
        h_bar.addWidget(self.btn_export)

        self.btn_doctor = QPushButton("🩺 环境诊断")
        self.btn_doctor.setMinimumHeight(32)
        self.btn_doctor.setToolTip("排查为什么这台电脑读不到 WiFi 密码")
        self.btn_doctor.clicked.connect(self._show_doctor)
        h_bar.addWidget(self.btn_doctor)

        root.addLayout(h_bar)

        # ── 进度条 ──
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setMaximumHeight(6)
        self.progress.setTextVisible(False)
        root.addWidget(self.progress)

        # ── 表格 ──
        g = QGroupBox("已保存的 WiFi 网络")
        v = QVBoxLayout(g)
        v.setContentsMargins(8, 8, 8, 8)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["WiFi 名称 (SSID)", "密码", "认证方式", "加密", "状态", "操作"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(self.COL_SSID, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_PWD, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_AUTH, QHeaderView.Interactive)
        hh.setSectionResizeMode(self.COL_CIPHER, QHeaderView.Fixed)
        hh.setSectionResizeMode(self.COL_NOTE, QHeaderView.Stretch)
        hh.setSectionResizeMode(self.COL_OP, QHeaderView.Fixed)
        self.table.setColumnWidth(self.COL_SSID, 240)
        self.table.setColumnWidth(self.COL_PWD, 190)
        self.table.setColumnWidth(self.COL_AUTH, 130)
        self.table.setColumnWidth(self.COL_CIPHER, 80)
        self.table.setColumnWidth(self.COL_OP, 84)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.doubleClicked.connect(self._on_double_click)
        v.addWidget(self.table)

        root.addWidget(g, 1)

        # ── 底部状态 ──
        h_foot = QHBoxLayout()
        self.lbl_status = QLabel("正在读取…")
        self.lbl_status.setStyleSheet(f"color: {self._accent};")
        h_foot.addWidget(self.lbl_status)
        h_foot.addStretch()
        tip = QLabel("🔒 密码来自本机已保存的凭据，请勿外传截图")
        tip.setStyleSheet("color: #888; font-size: 11px;")
        h_foot.addWidget(tip)
        root.addLayout(h_foot)

    # ══════════════════════════════════════════════════════════
    # 加载（后台线程，不卡 UI）
    # ══════════════════════════════════════════════════════════
    def _start_load(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self._data = []
        self.table.setRowCount(0)
        for c in (self.card_total, self.card_ok, self.card_open, self.card_fail):
            c.set_value("—")
        self.btn_refresh.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)          # 未知总数时先走跑马灯
        self.lbl_status.setText("正在读取本机 WiFi 配置…")

        self._thread = QThread()
        self._worker = _LoadWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.one.connect(self._on_one)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.start()

    def _on_one(self, done, total, detail):
        if self._closing:
            return
        if self.progress.maximum() == 0:
            self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.lbl_status.setText(f"正在读取… {done}/{total}")

    def _on_finished(self, data):
        if self._closing:
            return
        self._cleanup_thread()
        self._data = data
        self._rebuild_table()
        self.progress.setVisible(False)
        self.btn_refresh.setEnabled(True)

        total = len(data)
        n_ok = sum(1 for d in data if d.get("password"))
        n_open = sum(1 for d in data if d.get("open"))
        n_fail = total - n_ok - n_open
        self.card_total.set_value(total)
        self.card_ok.set_value(n_ok)
        self.card_open.set_value(n_open)
        self.card_fail.set_value(n_fail)

        if total == 0:
            self.lbl_status.setText("⚠ 未找到任何已保存的 WiFi，点「环境诊断」查看原因")
        else:
            self.lbl_status.setText(
                f"✅ 完成：共 {total} 个配置，成功获取 {n_ok} 个密码")

    def _on_failed(self, msg):
        if self._closing:
            return
        self._cleanup_thread()
        self.progress.setVisible(False)
        self.btn_refresh.setEnabled(True)
        self.lbl_status.setText(f"❌ 读取失败：{msg}")
        QMessageBox.warning(
            self, "读取失败",
            f"{msg}\n\n可点击「🩺 环境诊断」查看具体原因。")

    def _cleanup_thread(self):
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None

    # ══════════════════════════════════════════════════════════
    # 表格渲染
    # ══════════════════════════════════════════════════════════
    @staticmethod
    def _mask(pwd):
        if not pwd:
            return ""
        if len(pwd) <= 2:
            return "*" * len(pwd)
        return pwd[0] + "*" * (len(pwd) - 2) + pwd[-1]

    def _rebuild_table(self):
        self.table.setRowCount(0)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)

        for d in self._data:
            row = self.table.rowCount()
            self.table.insertRow(row)

            # SSID
            it = QTableWidgetItem(d["ssid"])
            it.setToolTip(d["ssid"])
            self.table.setItem(row, self.COL_SSID, it)

            # 密码（原文存 UserRole，显示按掩码开关切换）
            pwd = d.get("password")
            pit = QTableWidgetItem()
            pit.setData(Qt.UserRole, pwd)
            pit.setFont(mono)
            if pwd:
                pit.setForeground(C_OK)
            elif d.get("open"):
                pit.setForeground(C_OPEN)
            else:
                pit.setForeground(C_FAIL if d.get("error") else C_WARN)
            self.table.setItem(row, self.COL_PWD, pit)

            # 认证 / 加密
            self.table.setItem(row, self.COL_AUTH,
                               QTableWidgetItem(d.get("auth") or "—"))
            ci = QTableWidgetItem(d.get("cipher") or "—")
            ci.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, self.COL_CIPHER, ci)

            # 状态
            if pwd:
                note, color = "🟢 已获取", C_OK
            elif d.get("open"):
                note, color = "⚪ 开放网络，无密码", C_OPEN
            elif d.get("error"):
                note, color = f"🔴 {d.get('reason') or '读取失败'}", C_FAIL
            else:
                note, color = f"🟡 {d.get('reason') or '无密码'}", C_WARN
            nit = QTableWidgetItem(note)
            nit.setForeground(color)
            nit.setToolTip(d.get("reason") or d.get("error") or note)
            self.table.setItem(row, self.COL_NOTE, nit)

            # 操作
            if pwd:
                btn = QPushButton("复制")
                btn.setProperty("class", "accentBtn")
                btn.setCursor(Qt.PointingHandCursor)
                btn.setFixedHeight(24)
                btn.clicked.connect(lambda _c=False, p=pwd, s=d["ssid"]:
                                    self._copy_one(s, p))
                self.table.setCellWidget(row, self.COL_OP, btn)

        self._refresh_password_cells()
        self._apply_filter(self.search.text())

    def _refresh_password_cells(self):
        """按当前掩码开关刷新密码列文本。"""
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_PWD)
            if item is None:
                continue
            pwd = item.data(Qt.UserRole)
            if pwd:
                item.setText(pwd if self._show_plain else self._mask(pwd))
            else:
                item.setText("—")

    def _toggle_plain(self):
        self._show_plain = self.btn_eye.isChecked()
        self.btn_eye.setText("🙈 隐藏密码" if self._show_plain else "👁 显示密码")
        self._refresh_password_cells()

    def _apply_filter(self, text):
        kw = (text or "").strip().lower()
        visible = 0
        for row in range(self.table.rowCount()):
            if not kw:
                self.table.setRowHidden(row, False)
                visible += 1
                continue
            ssid = (self.table.item(row, self.COL_SSID).text() or "").lower()
            pwd_item = self.table.item(row, self.COL_PWD)
            pwd = (pwd_item.data(Qt.UserRole) or "").lower() if pwd_item else ""
            hit = kw in ssid or kw in pwd
            self.table.setRowHidden(row, not hit)
            if hit:
                visible += 1
        if kw:
            self.lbl_status.setText(f"🔍 匹配 {visible} / {self.table.rowCount()} 条")

    # ══════════════════════════════════════════════════════════
    # 操作
    # ══════════════════════════════════════════════════════════
    def _copy_one(self, ssid, pwd):
        QApplication.clipboard().setText(pwd)
        self.lbl_status.setText(f"📋 已复制「{ssid}」的密码到剪贴板")

    def _on_double_click(self, index):
        row = index.row()
        item = self.table.item(row, self.COL_PWD)
        if item and item.data(Qt.UserRole):
            ssid_item = self.table.item(row, self.COL_SSID)
            self._copy_one(ssid_item.text() if ssid_item else "", item.data(Qt.UserRole))

    def _copy_all(self):
        if not self._data:
            return
        lines = []
        for d in self._data:
            if d.get("password"):
                lines.append(f"{d['ssid']}\t{d['password']}")
            elif d.get("open"):
                lines.append(f"{d['ssid']}\t<开放网络，无密码>")
            else:
                lines.append(f"{d['ssid']}\t<{d.get('reason') or '未获取'}>")
        QApplication.clipboard().setText("\n".join(lines))
        self.lbl_status.setText(f"📋 已复制 {len(lines)} 条记录到剪贴板")

    @staticmethod
    def _desktop_dir():
        """真实桌面路径（兼容 OneDrive 重定向），失败回退 ~/Desktop。"""
        try:
            import ctypes
            from ctypes import wintypes
            folderid_desktop = '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}'
            fn = ctypes.windll.shell32.SHGetKnownFolderPath
            fn.argtypes = [ctypes.c_wchar_p, wintypes.DWORD, wintypes.HANDLE,
                           ctypes.POINTER(ctypes.c_wchar_p)]
            fn.restype = wintypes.HRESULT
            p = ctypes.c_wchar_p()
            if fn(folderid_desktop, 0, None, ctypes.byref(p)) == 0 and p.value:
                return p.value
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), "Desktop")

    def _export(self):
        if not self._data:
            QMessageBox.information(self, "无数据", "还没有可导出的 WiFi 记录。")
            return
        default_dir = os.path.join(self._desktop_dir(), "Super_ADB")
        try:
            os.makedirs(default_dir, exist_ok=True)
        except Exception:
            default_dir = self._desktop_dir()

        path, selected = QFileDialog.getSaveFileName(
            self, "导出 WiFi 列表",
            os.path.join(default_dir, "wifi_passwords"),
            "CSV 文件 (*.csv);;JSON 文件 (*.json)")
        if not path:
            return

        if ".json" in (selected or "").lower():
            if not path.lower().endswith(".json"):
                path += ".json"
            fmt = "json"
        else:
            if not path.lower().endswith(".csv"):
                path += ".csv"
            fmt = "csv"

        try:
            if fmt == "json":
                ok = save_json(path, self._data)
            else:
                # utf-8-sig：让 Excel 正确识别中文
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["WiFi 名称", "密码", "认证方式", "加密", "状态说明"])
                    for d in self._data:
                        w.writerow([
                            d["ssid"],
                            d.get("password") or "",
                            d.get("auth") or "",
                            d.get("cipher") or "",
                            d.get("reason") or ("已获取" if d.get("password") else ""),
                        ])
                ok = True
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        if not ok:
            QMessageBox.critical(self, "导出失败", "写入 JSON 失败，详见日志")
            return
        self.lbl_status.setText(f"💾 已导出到 {path}")
        QMessageBox.information(self, "导出成功", f"已导出 {len(self._data)} 条记录：\n{path}")

    # ══════════════════════════════════════════════════════════
    # 环境诊断
    # ══════════════════════════════════════════════════════════
    def _show_doctor(self):
        self.lbl_status.setText("🩺 正在诊断…")
        QApplication.processEvents()
        try:
            items = wifi_tools.diagnose()
        except Exception as e:
            QMessageBox.critical(self, "诊断失败", str(e))
            return
        self.lbl_status.setText("🩺 诊断完成")

        icon = {"ok": "✅", "warn": "⚠️", "error": "❌"}
        color = {"ok": "#00CC66", "warn": "#FFA940", "error": "#FF6B6B"}
        rows = "".join(
            f"<tr>"
            f"<td style='padding:5px 8px;vertical-align:top;'>{icon[lv]}</td>"
            f"<td style='padding:5px 8px;vertical-align:top;'>"
            f"<b style='color:{color[lv]};'>{title}</b><br/>"
            f"<span style='color:#AAB;'>{detail}</span></td>"
            f"</tr>"
            for lv, title, detail in items)

        html = (
            "<h3>WiFi 读取环境诊断</h3>"
            f"<table style='border-spacing:0;'>{rows}</table>"
            "<hr/>"
            "<p><b>常见「读不到」的原因：</b></p>"
            "<ol>"
            "<li><b>没有无线网卡</b> —— 多数台式机走网线，系统里根本没有 WLAN 配置。</li>"
            "<li><b>WLAN AutoConfig 服务被关</b> —— netsh wlan 全部命令都会失败。</li>"
            "<li><b>企业级 802.1X 网络</b> —— 用账号/证书认证，不存在共享密码可读。</li>"
            "<li><b>组策略下发的配置</b> —— 公司电脑常见，密钥受保护，需管理员权限。</li>"
            "<li><b>系统语言差异</b> —— netsh 中/英文字段名不同，只匹配单语言的工具会全军覆没"
            "（本工具已兼容）。</li>"
            "<li><b>重装系统 / 网络重置</b> —— 配置文件已被清空。</li>"
            "</ol>")

        box = QMessageBox(self)
        box.setWindowTitle("环境诊断")
        box.setTextFormat(Qt.RichText)
        box.setText(html)
        box.setIcon(QMessageBox.NoIcon)
        box.exec()

    # ══════════════════════════════════════════════════════════
    def closeEvent(self, event):
        self._closing = True
        if self._thread and self._thread.isRunning():
            if self._worker:
                self._worker.cancel()
            self._thread.quit()
            self._thread.wait(2000)
            self._thread = None
            self._worker = None
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dlg = WiFi对话框()
    dlg.show()
    sys.exit(app.exec())
