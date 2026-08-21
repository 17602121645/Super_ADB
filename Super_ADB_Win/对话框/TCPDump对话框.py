# -*- coding: utf-8 -*-
"""
tcpdump 抓包弹窗
================
点击系统操作栏「tcpdump 抓包」弹出。配置网卡 / 过滤表达式后，
在设备上执行 `tcpdump -i <iface> -s 0 -w - <filter>`，把 stdout 的
pcap 二进制流实时写入本地文件：

    桌面/Super_ADB/tcpdump_<serial>_<时间戳>.pcap

结束后（点停止或关窗）文件留在桌面，可用 Wireshark 等打开回看。
"""

import os
import time
import subprocess
import threading

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QSizePolicy,
)

from ADB工具 import AdbHelper, CREATE_NO_WINDOW
from 项目UI.界面样式 import (
    STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_current_theme_id,
    THEMES, DEFAULT_THEME, _parse_rgb,
)
from 项目UI.弹窗样式 import add_green_glow

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from 项目UI import png_rc  # noqa: F401


class Tcpdump对话框(QWidget):
    """tcpdump 抓包独立窗口。"""

    _bytes_updated = Signal(int, float)

    def __init__(self, serial, parent=None):
        super().__init__(parent)
        self._adb = AdbHelper()
        self._serial = serial
        self._proc = None
        self._reader = None
        self._closed = False
        self._running = False
        self._stopping = False
        self._fh = None
        self._path = ''
        self._bytes = 0
        self._start_ts = 0

        self.setWindowTitle(f'tcpdump 抓包 — {serial}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(560, 360)
        self.resize(620, 400)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(self._style(self._theme_id))
        self.setWindowFlag(Qt.Window, True)

        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        self.card.setStyleSheet(self._card_style(self._theme_id))
        accent = THEMES.get(self._theme_id, THEMES[DEFAULT_THEME])['accent']
        r, g, b = _parse_rgb(accent)
        add_green_glow(self.card, accent=QColor(r, g, b))

        self._build_ui()
        self._bytes_updated.connect(self._on_bytes_updated)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_stat)

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

    def _style(self, theme_id):
        """生成弹窗 QSS，颜色跟随主题。"""
        if theme_id not in THEMES:
            theme_id = getattr(self, '_theme_id', DEFAULT_THEME)
        t = THEMES[theme_id]
        accent = t['accent']
        ar, ag, ab = _parse_rgb(accent)
        bg_window = t['bg_window']
        bg_button = t['bg_button']
        bg_input = t['bg_input']
        bg_menu = t['bg_menu']
        text_primary = t['text_primary']
        text_disabled = t['text_disabled']
        text_pressed = t['text_pressed']
        border_disabled = t.get('border_disabled', text_disabled)
        return (
            f'QWidget{{background: {bg_window}; color: {text_primary}; '
            f'font: 10pt "{FONT_FAMILY}";}}'
            f'QLabel{{background: transparent; color: {text_primary};}}'
            f'QLabel#tipLabel{{color: {text_disabled}; font: 9pt "{FONT_FAMILY}";}}'
            f'QLabel#statusLabel{{color: {accent}; font: 9pt "{FONT_FAMILY}";}}'
            f'QLabel#statLabel{{color: {text_disabled}; font: 9pt "{FONT_FAMILY}";}}'
            f'QTextEdit#logEdit{{background: {bg_input}; color: {text_primary}; '
            f'border: 1px solid {bg_button}; border-radius: 6px; '
            f'font: 9pt "Consolas", "{FONT_FAMILY}";}}'
            f'QPushButton{{background: {bg_button}; color: {accent}; '
            f'border: 1px solid {accent}; border-radius: 6px; padding: 6px 14px; '
            f'font: 9pt "{FONT_FAMILY}";}}'
            f'QPushButton:hover{{background: {accent}; color: {text_pressed};}}'
            f'QPushButton:pressed{{background: rgba({ar},{ag},{ab},180); color: {text_pressed};}}'
            f'QPushButton:disabled{{color: {text_disabled}; border: 1px solid {border_disabled}; '
            f'background: {bg_window};}}'
            f'QLineEdit{{background: {bg_input}; color: {text_primary}; '
            f'border: 1px solid {bg_button}; border-radius: 6px; padding: 6px;}}'
            f'QLineEdit:focus{{border: 1px solid {accent};}}'
            f'QComboBox{{background: {bg_input}; color: {text_primary}; '
            f'border: 1px solid {bg_button}; border-radius: 6px; padding: 6px;}}'
            f'QComboBox:focus{{border: 1px solid {accent};}}'
            f'QComboBox::drop-down{{border: none; width: 20px;}}'
            f'QComboBox QAbstractItemView{{background: {bg_menu}; color: {text_primary}; '
            f'border: 1px solid {bg_button}; selection-background-color: {accent};}}'
        )

    def _card_style(self, theme_id):
        """card 容器样式：背景 + 主题色 4px 边框。"""
        if theme_id not in THEMES:
            theme_id = getattr(self, '_theme_id', DEFAULT_THEME)
        t = THEMES[theme_id]
        return (
            f'#popupCard{{background: {t["bg_window"]}; '
            f'border: 4px solid {t["accent"]}; border-radius: 12px;}}'
            f'#popupCard QLabel{{background: transparent; border: none; color: {t["text_primary"]};}}'
        )

    def apply_theme(self, theme_id):
        """主窗口切换主题时调用，同步刷新弹窗颜色与发光。"""
        if theme_id not in THEMES or theme_id == getattr(self, '_theme_id', None):
            return
        self._theme_id = theme_id
        self.setStyleSheet(self._style(theme_id))
        self.card.setStyleSheet(self._card_style(theme_id))
        accent = THEMES[theme_id]['accent']
        r, g, b = _parse_rgb(accent)
        add_green_glow(self.card, accent=QColor(r, g, b))

    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        tip = QLabel('在设备上执行 tcpdump 抓包，pcap 实时写入本地文件。'
                     '结束后文件保存在 桌面/Super_ADB/。')
        tip.setObjectName('tipLabel')
        tip.setWordWrap(True)
        lay.addWidget(tip)

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)

        self.iface_edit = QLineEdit('wlan0')
        self.iface_edit.setToolTip('抓包网卡，如 wlan0 / eth0 / rmnet0；部分设备不支持 any')
        g.addWidget(QLabel('网卡:'), 0, 0)
        g.addWidget(self.iface_edit, 0, 1)

        self.filter_edit = QLineEdit('')
        self.filter_edit.setPlaceholderText('过滤表达式(可选)，如 port 443 / tcp / host 1.2.3.4')
        g.addWidget(QLabel('过滤:'), 1, 0)
        g.addWidget(self.filter_edit, 1, 1)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(['不限制', 'tcp', 'udp', 'icmp'])
        self.proto_combo.setToolTip('快速协议过滤，会拼到过滤表达式前面')
        g.addWidget(QLabel('协议:'), 2, 0)
        g.addWidget(self.proto_combo, 2, 1)
        lay.addLayout(g)

        # 操作栏
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_start = QPushButton('▶ 开始抓包')
        self.btn_start.setFixedWidth(120)
        self.btn_start.clicked.connect(self._start)
        bar.addWidget(self.btn_start)
        self.btn_stop = QPushButton('■ 停止')
        self.btn_stop.setFixedWidth(100)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        bar.addWidget(self.btn_stop)
        bar.addStretch(1)
        self.status_label = QLabel('就绪')
        self.status_label.setObjectName('statusLabel')
        bar.addWidget(self.status_label)
        lay.addLayout(bar)

        # 实时统计
        self.stat_label = QLabel('已抓 0 KB · 0 包 · 00:00')
        self.stat_label.setObjectName('statLabel')
        lay.addWidget(self.stat_label)

        # 日志
        from PySide6.QtWidgets import QTextEdit
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName('logEdit')
        self.log_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.log_edit, 1)

    # ---- 开始 ----
    def _start(self):
        if self._running:
            return
        iface = self.iface_edit.text().strip() or 'wlan0'
        flt = self.filter_edit.text().strip()
        proto = self.proto_combo.currentText()
        if proto != '不限制':
            flt = (proto + ' ' + flt).strip()

        # 打开本地 pcap 文件
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        save_dir = os.path.join(desktop, 'Super_ADB')
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self._log(f'[错误] 无法创建目录: {e}')
            return
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_serial = (self._serial or 'dev').replace(':', '_').replace('/', '_')
        self._path = os.path.join(save_dir, f'tcpdump_{safe_serial}_{ts}.pcap')
        try:
            self._fh = open(self._path, 'wb')
        except Exception as e:
            self._log(f'[错误] 无法创建 pcap 文件: {e}')
            return

        cmd = [self._adb.adb_path, '-s', self._serial, 'shell',
               'tcpdump', '-i', iface, '-s', '0', '-w', '-']
        if flt:
            cmd.append(flt)

        self._log(f'$ adb -s {self._serial} shell tcpdump -i {iface} -s 0 -w - {flt}'.strip())
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW)
        except Exception as e:
            self._log(f'[错误] 启动失败: {e}')
            self._cleanup_proc()
            return

        self._running = True
        self._bytes = 0
        self._start_ts = time.time()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText('抓包中…')
        self.status_label.setStyleSheet('color: #1de9b6;')
        self._timer.start()

    def _read_loop(self):
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while not self._closed:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                if self._fh is not None:
                    self._fh.write(chunk)
                    self._bytes += len(chunk)
                    self._bytes_updated.emit(self._bytes, time.time() - self._start_ts)
        except Exception:
            pass
        finally:
            # 进程结束或关闭：收尾（由 _stop 主动触发 _finalize 时避免重复）
            if not self._closed:
                QTimer.singleShot(0, self._finalize)

    # ---- 停止 ----
    def _stop(self):
        if not self._running or self._stopping:
            return
        self._stopping = True
        self._log('---- 用户停止 ----')
        self._closed = True
        # 先强制杀掉本地 adb 进程，再关闭 stdout，让 _read_loop 的 read 立即退出
        self._close_proc(force=True)
        proc = self._proc
        if proc is not None and proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                pass
        self._finalize()

    def _close_proc(self, force=False):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except Exception:
                pass

    def _finalize(self):
        if not self._running:
            return
        self._running = False
        self._stopping = False
        self._timer.stop()
        self._close_proc()
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass
            self._fh = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        dur = int(time.time() - self._start_ts)
        self.status_label.setText(f'已停止 · 保存 {self._bytes // 1024} KB')
        self.status_label.setStyleSheet('color: #98c379;')
        self._log(f'抓包结束，共 {self._bytes // 1024} KB，保存到:')
        self._log(self._path)
        self._refresh_stat()
        self._proc = None

    def _cleanup_proc(self):
        self._close_proc()
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    # ---- 状态 ----
    def _on_bytes_updated(self, nbytes, secs):
        self._bytes = nbytes
        self._refresh_stat(secs)

    def _refresh_stat(self, secs=None):
        if secs is None:
            secs = time.time() - self._start_ts if self._start_ts else 0
        pkts = self._bytes // 1500  # 粗略估算包数（仅展示用）
        self.stat_label.setText(
            f'已抓 {self._bytes // 1024} KB · ~{pkts} 包 · '
            f'{int(secs) // 60:02d}:{int(secs) % 60:02d}')

    def _log(self, line):
        self.log_edit.append(line)

    # ---- 关窗 ----
    def closeEvent(self, event):
        self._closed = True
        if self._running:
            self._close_proc()
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None
        super().closeEvent(event)
