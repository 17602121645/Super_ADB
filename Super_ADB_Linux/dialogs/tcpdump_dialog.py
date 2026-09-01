# -*- coding: utf-8 -*-
"""
tcpdump 抓包弹窗
================
点击系统操作栏「tcpdump 抓包」弹出。配置网卡 / 过滤表达式后，
在设备上执行 `tcpdump -i <iface> -s 0 -w <设备路径>`，
抓包数据写入设备存储，停止后 adb pull 回本地：

    桌面/Super_ADB/tcpdump_<serial>_<时间戳>.pcap

结束后（点停止或关窗）文件留在桌面，可用 Wireshark 等打开回看。
"""

import os
import struct
import time
import socket
import subprocess
import threading

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QLineEdit, QComboBox, QPushButton, QSizePolicy, QApplication,
    QCheckBox,
)

from tools.adb_tools import AdbHelper, AdbFileManager, CREATE_NO_WINDOW
from tools.adb_native.adb_protocol import CMD_OKAY, CMD_WRTE, CMD_CLSE, AdbMessage
from tools.adb_native.adb_protocol import 借用连接 as _adb_borrow, 剥离连接 as _adb_detach
from ui.ui_styles import (
    STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_current_theme_id,
    THEMES, DEFAULT_THEME, _parse_rgb,
)
from ui.dialog_styles import add_green_glow

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from ui import png_rc  # noqa: F401


class Tcpdump对话框(QWidget):
    """tcpdump 抓包独立窗口。"""

    _bytes_updated = Signal(int, float)
    _stream_ended = Signal()
    _stop_completed = Signal()
    _repair_completed = Signal(bool, str, int)  # pcap_ok, path, bytes
    _log_signal = Signal(str)
    _stop_progress = Signal(str)  # 停止阶段进度
    _pull_progress = Signal(str, int)  # status_text, pct
    _pull_finished = Signal(bool, int)  # ok, local_size
    _usb_detected = Signal(str)  # 设备端 U 盘路径（空格分隔，空串=未检测到）
    _usb_diag_done = Signal()  # U 盘模式设备端统计收集完成
    _run_on_ui = Signal(object)  # 后台线程投递 UI 操作到主线程执行

    def __init__(self, serial, parent=None, adb=None):
        super().__init__(parent)
        # 优先复用调用方（主窗口）传入的 AdbHelper/Adb设备操作 实例：
        # 避免 new 独立实例 → 自研adb 缓存为空 → 重复 AUTH 建连。
        # 为兼容旧代码（直接 Tcpdump对话框(serial)），未传参时 fallback AdbHelper()。
        # （AdbHelper 的 自研adb 缓存已升级为**类级共享**，fakllback 也不会重复建连。）
        if adb is not None:
            self._adb = adb
        else:
            self._adb = AdbHelper()
        self._serial = serial
        self._proc = None
        self._reader = None
        self._closed = False
        self._running = False
        self._stopping = False
        self._self_mode = False
        self._stop_event = None
        self._fh = None
        self._path = ''           # 本地路径
        self._remote_path = ''     # 设备端路径
        self._bytes = 0
        self._start_ts = 0
        self._usb_paths = []       # 设备端检测到的 U 盘路径
        self._on_usb_mode = False  # 本次抓包是否存 U 盘（停止后不拉回）
        self._usb_verify_result = None  # U 盘文件完整性校验结果 (ok, lines)
        self._tcpdump_bin = 'tcpdump'  # 设备上 tcpdump 可执行路径（默认依赖 PATH）

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
        self._stream_ended.connect(self._finalize)
        self._stop_completed.connect(self._on_stop_completed)
        self._repair_completed.connect(self._on_repair_completed)
        self._stop_progress.connect(self._on_stop_progress)
        self._pull_progress.connect(self._on_pull_progress)
        self._pull_finished.connect(self._on_pull_finished)
        self._log_signal.connect(self._log)
        self._usb_detected.connect(self._on_usb_detected)
        self._usb_diag_done.connect(self._report_final_diagnostics)
        self._run_on_ui.connect(self._exec_ui)
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_stat)
        # 打开弹窗时后台识别设备上的 U 盘
        threading.Thread(target=self._detect_usb, daemon=True).start()

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
            f'QLabel#usbLabel{{color: {text_disabled}; font: 9pt "{FONT_FAMILY}";}}'
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

    def apply_theme(self, theme_id):
        """运行时切换主题：更新全局 QSS + card 样式 + 外发光。"""
        if theme_id not in THEMES:
            theme_id = DEFAULT_THEME
        self._theme_id = theme_id
        self.setStyleSheet(self._style(theme_id))
        if hasattr(self, 'card') and self.card is not None:
            self.card.setStyleSheet(self._card_style(theme_id))
            accent = THEMES[theme_id]['accent']
            r, g, b = _parse_rgb(accent)
            add_green_glow(self.card, accent=QColor(r, g, b))
        self.update()

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

        self.iface_edit = QLineEdit('')
        self.iface_edit.setPlaceholderText('留空=所有接口，或指定网卡如 wlan0 / eth0 / rmnet0')
        self.iface_edit.setToolTip('留空抓所有接口；部分设备可指定 wlan0 / eth0 / rmnet0 等')
        g.addWidget(QLabel('网卡:'), 0, 0)
        g.addWidget(self.iface_edit, 0, 1)

        self.filter_edit = QLineEdit('')
        self.filter_edit.setPlaceholderText('附加过滤(可选)，如 host 1.2.3.4 / dst port 8080')
        g.addWidget(QLabel('过滤:'), 1, 0)
        g.addWidget(self.filter_edit, 1, 1)

        self.proto_combo = QComboBox()
        self.proto_combo.addItems(['HTTP/HTTPS', '不限制', 'TCP', 'UDP', 'ICMP'])
        self.proto_combo.setToolTip('快速协议过滤；选 HTTP/HTTPS 默认抓 port 80+443')
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

        # 实时统计 + 设备端 U 盘路径
        stat_bar = QHBoxLayout()
        stat_bar.setSpacing(10)
        self.stat_label = QLabel('已抓 0 KB · 0 包 · 00:00')
        self.stat_label.setObjectName('statLabel')
        stat_bar.addWidget(self.stat_label)
        stat_bar.addStretch(1)
        self.usb_check_btn = QPushButton('检查U盘')
        self.usb_check_btn.setToolTip('重新检测设备上的 U 盘及其剩余/总容量')
        self.usb_check_btn.setCursor(Qt.PointingHandCursor)
        self.usb_check_btn.clicked.connect(self._on_usb_check_clicked)
        stat_bar.addWidget(self.usb_check_btn)
        self.usb_label = QLabel('')
        self.usb_label.setObjectName('usbLabel')
        stat_bar.addWidget(self.usb_label)
        lay.addLayout(stat_bar)

        # 日志
        from PySide6.QtWidgets import QTextEdit
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setObjectName('logEdit')
        self.log_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay.addWidget(self.log_edit, 1)

    # ---- 开始 ----
    def _detect_usb(self):
        """后台线程：识别设备上挂载的 U 盘（OTG 外置存储）路径。

        U 盘挂载后同时出现在 /mnt/media_rw/<id> 和 /storage/<id>，
        保险起见再用 UUID 模式（XXXX-XXXX）扫一遍 /storage 兜底。
        """
        try:
            out = (self._adb.执行shell(
                self._serial,
                'for d in $(ls /mnt/media_rw/ 2>/dev/null); do '
                '[ -d /storage/$d ] && echo /storage/$d; done; '
                "ls /storage/ 2>/dev/null | grep -E '^[0-9A-Fa-f]{4}-"
                "[0-9A-Fa-f]{4}$' | sed 's|^|/storage/|'",
                timeout=8) or '').strip()
        except Exception:
            out = ''
        paths = []
        for line in out.splitlines():
            p = line.strip()
            if p.startswith('/storage/') and p not in paths:
                paths.append(p)
        if not paths:
            self._usb_detected.emit('')
            return
        # 查询容量: df -k 列为 文件系统 1K块 已用 可用 使用% 挂载点
        items = []
        try:
            df_cmd = '; '.join(f'df -k {p} 2>/dev/null | tail -n1' for p in paths)
            df_out = str(self._adb.执行shell(self._serial, df_cmd, timeout=6) or '')
            size_map = {}
            for ln in df_out.splitlines():
                cols = ln.split()
                if len(cols) >= 4 and cols[-1] in paths:
                    try:
                        size_map[cols[-1]] = (int(cols[1]) * 1024, int(cols[3]) * 1024)
                    except ValueError:
                        pass
            for p in paths:
                if p in size_map:
                    total, avail = size_map[p]
                    items.append(f'{p}|{total}|{avail}')
                else:
                    items.append(p)
        except Exception:
            items = list(paths)
        self._usb_detected.emit(' '.join(items))

    def _on_usb_check_clicked(self):
        """手动重新检测 U 盘：后台线程执行，避免卡 UI。"""
        self.usb_check_btn.setEnabled(False)
        self.usb_label.setText('正在检查U盘...')
        threading.Thread(target=self._detect_usb, daemon=True).start()

    def _on_usb_detected(self, paths_text):
        """主线程：把检测到的 U 盘路径与容量显示在统计行右侧。"""
        # 恢复手动检查按钮（弹窗刚打开首次检测时按钮可能尚未创建）
        btn = getattr(self, 'usb_check_btn', None)
        if btn is not None:
            btn.setEnabled(True)
        self._usb_paths = []
        labels = []
        for item in paths_text.split(' '):
            if not item:
                continue
            seg = item.split('|')
            p = seg[0]
            if not p.startswith('/storage/'):
                continue
            self._usb_paths.append(p)
            if len(seg) >= 3:
                try:
                    total, avail = int(seg[1]), int(seg[2])
                    labels.append(
                        f'{p}（可用 {self._fmt_size(avail)}'
                        f'/{self._fmt_size(total)}）')
                    continue
                except ValueError:
                    pass
            labels.append(p)
        if not self._usb_paths:
            self.usb_label.setText('未检测到U盘')
            self.usb_label.setToolTip('')
            return
        disp = '，'.join(labels)
        self.usb_label.setText(f'U盘: {disp}')
        self.usb_label.setToolTip(disp)
        try:
            self.usb_label.setStyleSheet(
                f'color: {THEMES[self._theme_id]["text_primary"]};')
        except Exception:
            pass

    def _exec_ui(self, fn):
        """在主线程执行 fn（后台线程通过 _run_on_ui 信号投递）。"""
        try:
            fn()
        except Exception:
            pass

    def _start_fail(self, status):
        """后台启动流程失败：恢复开始按钮并标红状态（回主线程执行 UI）。"""
        def _u():
            self._preparing = False
            self.btn_start.setEnabled(True)
            self.status_label.setText(status)
            self.status_label.setStyleSheet('color: #ff6b6b;')
        self._run_on_ui.emit(_u)
        return None

    def _capture_running_ui(self):
        """抓包真正启动后的 UI 状态（主线程执行）。"""
        self._preparing = False
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_stop.setText('■ 停止')
        self.status_label.setText('抓包中…')
        self.status_label.setStyleSheet('color: #1de9b6;')
        self._timer.start()

    def _start(self):
        if self._running or getattr(self, '_preparing', False):
            return
        # 每次开始抓包先清空日志栏，避免多次会话堆积
        self.log_edit.clear()
        iface = self.iface_edit.text().strip() or 'any'
        flt = self.filter_edit.text().strip()
        proto = self.proto_combo.currentText()
        if proto == 'HTTP/HTTPS':
            base = 'tcp and (port 80 or port 443)'
            flt = f'{base} and ({flt})' if flt else base
        elif proto != '不限制':
            flt = (proto.lower() + ' ' + flt).strip()

        # ★ 启动流程含大量 adb 调用（可达 30 秒+），
        # 全部放后台线程执行，主线程只做输入读取，否则窗口会「未响应」
        self._preparing = True
        self.btn_start.setEnabled(False)
        self.status_label.setText('准备中…')
        self.status_label.setStyleSheet('color: #ffb74d;')
        threading.Thread(target=self._start_rest, args=(iface, flt),
                         daemon=True).start()

    def _start_rest(self, iface, flt):
        """后台线程：tcpdump 检查/目录准备/权限探针/启动抓包。

        UI 操作一律通过 _run_on_ui 信号回主线程；失败走 _start_fail。
        """
        self._log('[检查] 设备上是否安装 tcpdump...')
        try:
            # 方式1: which tcpdump
            which_out = (self._adb.执行shell(
                self._serial, 'which tcpdump 2>/dev/null', timeout=5) or '').strip()
            # 方式2: tcpdump --version（有些设备which不工作）
            ver_out = (self._adb.执行shell(
                self._serial, 'tcpdump --version 2>&1 | head -n1', timeout=5) or '').strip()
            self._log(f'[检查] which: {which_out or "未找到"}')
            self._log(f'[检查] version: {ver_out or "无输出"}')
            # 判断设备是否已安装可用的 tcpdump：
            # - which 找到路径 → 已安装
            # - version 输出版本号（不含错误关键词）→ 已安装
            # - which 未找到 + version 返回 not found/inaccessible → 未安装，走自动推送
            _err_keywords = ['not found', 'No such file', 'inaccessible', 'cannot execute', 'permission denied']
            _has_err = any(k in ver_out for k in _err_keywords)
            _installed = bool(which_out) or (bool(ver_out) and not _has_err)
            if not _installed:
                self._log('[检查] 设备未安装 tcpdump，尝试自动推送...')
                if self._自动推送tcpdump():
                    self._log('[检查] tcpdump 自动推送成功')
                else:
                    self._log('[错误] 设备上未安装 tcpdump 且自动推送失败，无法抓包')
                    self._log('[提示] 请手动将 tcpdump 二进制推送到设备，或放到 vendor/tcpdump/ 目录')
                    return self._start_fail('设备无 tcpdump')
            else:
                self._log(f'[检查] tcpdump 可用: {ver_out or which_out}')
        except Exception as e:
            self._log(f'[警告] 检查 tcpdump 失败: {e}，继续尝试抓包')

        # 打开本地 pcap 文件（用于接收 pull 回来的数据）
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        save_dir = os.path.join(desktop, 'Super_ADB')
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self._log(f'[错误] 无法创建目录: {e}')
            return self._start_fail('目录创建失败')
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_serial = (self._serial or 'dev').replace(':', '_').replace('/', '_')
        self._path = os.path.join(save_dir, f'tcpdump_{safe_serial}_{ts}.pcap')
        # 设备端缓存目录：
        #  - U 盘模式按会话时间归档: <U盘>/Super_ADB/<开始时间>/{数据包,日志}
        #  - 内置存储临时目录: /sdcard/Super_ADB/（拉回后整体删除）
        def _calc_paths(root, session=False):
            self._remote_dir = f'{root}/数据包' if session else root
            self._remote_log_dir = f'{root}/日志' if session else root
            self._remote_path = (
                f'{self._remote_dir}/Super_ADB_capture_{safe_serial}_{ts}.pcap')
            self._stderr_path = (
                f'{self._remote_log_dir}/Super_ADB_stderr_{safe_serial}_{ts}.log')

        self._on_usb_mode = bool(self._usb_paths)
        if self._on_usb_mode:
            self._usb_session_root = f'{self._usb_paths[0]}/Super_ADB/{ts}'
            _calc_paths(self._usb_session_root, session=True)
            self._log(f'[U盘] 数据将保存到 U 盘: {self._usb_session_root}'
                      f'（停止后不拉回）')
        else:
            _calc_paths('/sdcard/Super_ADB')
        self._stderr_offset = 0
        self._device_tcpdump_r = ''

        # 确保设备端缓存目录存在，并清理可能残留的旧文件
        try:
            if self._on_usb_mode:
                # U 盘挂载在 FUSE 下，shell 用户可能无权创建目录，失败时用 su 兜底；
                # 顺带删除内置存储的临时文件夹，防止旧会话堆积。
                # 会话按时间归档到新目录，无需清理旧文件
                dirs = (f'{self._remote_dir} {self._remote_log_dir}')
                out = str(self._adb.执行shell(
                    self._serial,
                    f'rm -rf /sdcard/Super_ADB 2>/dev/null; '
                    f'mkdir -p {dirs} 2>/dev/null; '
                    f'[ -d {self._remote_dir} ] || su -c "mkdir -p {dirs}"; '
                    f'[ -d {self._remote_dir} ] && echo USB_DIR_OK',
                    timeout=6) or '')
                if 'USB_DIR_OK' not in out:
                    self._log(f'[警告] U 盘目录不可写（{self._remote_dir}），回退到内置存储')
                    self._on_usb_mode = False
                    _calc_paths('/sdcard/Super_ADB')
                    self._adb.执行shell(
                        self._serial,
                        f'mkdir -p {self._remote_dir}; '
                        f'rm -f {self._remote_path} {self._stderr_path} '
                        f'2>/dev/null',
                        timeout=3)
            else:
                # 内置存储模式：整删临时文件夹后重建，防止会话堆积。
                # /sdcard 在大多数设备上 shell 用户可写（FUSE），部分 ROM 需 su 兜底。
                self._adb.执行shell(
                    self._serial,
                    f'rm -rf /sdcard/Super_ADB 2>/dev/null; '
                    f'mkdir -p {self._remote_dir} 2>/dev/null; '
                    f'[ -d {self._remote_dir} ] || su -c "mkdir -p {self._remote_dir}"; '
                    f'[ -d {self._remote_dir} ] && echo SDCARD_DIR_OK',
                    timeout=4)
        except Exception:
            pass

        # ★ 权限探针：tcpdump 原始套接字抓包需 root。若 adb shell 为 shell 用户
        # 且设备无 su，设备端会权限报错立即退出，
        # 表现为「点开始马上就停止」。提前探测明确提示；su 可用则自动提权。
        use_su = False
        try:
            id_out = (self._adb.执行shell(self._serial, 'id 2>/dev/null', timeout=5) or '')
            if 'uid=0' in id_out:
                self._log('[检查] adb shell 已是 root，可直接抓包')
            else:
                su_out = (self._adb.执行shell(
                    self._serial, 'su -c id 2>&1 | head -n1', timeout=5) or '')
                if 'uid=0' in su_out:
                    use_su = True
                    self._log('[检查] 非 root 但 su 可用 → 以 su 提权抓包')
                else:
                    probe_cmd = f'{self._tcpdump_bin} -i {iface} -c 1 2>&1 | head -n1' if iface != 'any' else f'{self._tcpdump_bin} -c 1 2>&1 | head -n1'
                    err = (self._adb.执行shell(
                        self._serial, probe_cmd,
                        timeout=5) or '').strip()
                    if 'permission' in err.lower() or 'permitted' in err.lower():
                        self._log(f'[错误] 权限不足: {err}')
                        # 无 su 时按「system」按钮的思路尝试 adb root 提权
                        # （只走 root+重连，不做 remount/disable-verity，避免重启设备）
                        self._log('[检查] 无 su → 尝试 adb root 提权（同「system」按钮）')
                        rooted = False
                        try:
                            if getattr(self._adb, '_用自研adb', False):
                                client = self._adb._获取自研adb(self._serial)
                                if client and client.获取root():
                                    rooted = True
                                    time.sleep(2)  # root 后设备重启 adbd 会断开
                                    try:
                                        client.自动重连()
                                    except Exception:
                                        pass
                            else:
                                r = self._adb._run(
                                    [self._adb.adb_path, '-s', self._serial, 'root'],
                                    timeout=10)
                                root_out = ((r.stdout or '') + (r.stderr or '')).strip()
                                if r.returncode == 0 and 'cannot run as root' not in root_out.lower():
                                    rooted = True
                                    time.sleep(2)
                        except Exception as e:
                            self._log(f'[警告] adb root 尝试异常: {e}')
                        if rooted:
                            id2 = (self._adb.执行shell(
                                self._serial, 'id 2>/dev/null', timeout=5) or '')
                            if 'uid=0' in id2:
                                self._log('[检查] adb root 提权成功，已是 root，继续抓包')
                            else:
                                rooted = False
                        if not rooted:
                            self._log('[错误] adb root 提权失败（设备不支持 root 或非 userdebug 镜像），请换 rooted 设备')
                            return self._start_fail('权限不足(需root)')
        except Exception as e:
            self._log(f'[警告] 权限探针失败: {e}，继续尝试抓包')

        # 设备端 stderr 重定向到临时文件（保留完整错误/丢包信息用于诊断）
        if iface == 'any':
            inner = f'{self._tcpdump_bin} -s 0 -w {self._remote_path} 2>{self._stderr_path}'
        else:
            inner = f'{self._tcpdump_bin} -i {iface} -s 0 -w {self._remote_path} 2>{self._stderr_path}'
        if flt:
            # 用双引号包裹 BPF 过滤器表达式，避免设备 shell 解析括号导致 tcpdump 立即退出
            # （HTTP/HTTPS 默认生成 tcp and (port 80 or port 443) 含括号，未加引号时
            # adb shell → 设备 shell 会把 ( ) 当作子shell元字符，触发语法错误）
            inner += ' "' + flt + '"'
        shell_cmd = f"su -c '{inner}'" if use_su else inner
        self._log(f'$ adb -s {self._serial} shell {shell_cmd}')

        # 显示设备端保存路径
        self._log(f'  设备端 pcap:  {self._remote_path}')
        if self._on_usb_mode:
            self._log('  ⓘ U 盘模式：停止后数据保留在 U 盘，不拉回本地')
        else:
            self._log('  本地路径将在停止后 pull 回来')

        if getattr(self._adb, '_用自研adb', False):
            client = self._adb._获取自研adb(self._serial)
            if not client:
                self._log('[错误] 自研adb连接设备失败，无法抓包')
                return self._start_fail('自研adb失败')
            self._self_mode = True
            self._stop_event = threading.Event()
            self._reader = threading.Thread(
                target=self._tcpdump_device_runner,
                args=(client, shell_cmd), daemon=True)
        else:
            self._self_mode = False
            cmd = [self._adb.adb_path, '-s', self._serial, 'shell', shell_cmd]
            try:
                self._proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW)
            except Exception as e:
                self._log(f'[错误] 启动失败: {e}')
                return self._start_fail('启动失败')
            self._reader = threading.Thread(target=self._tcpdump_wait_loop, daemon=True)

        self._running = True
        self._bytes = 0
        self._start_ts = time.time()
        self._reader.start()
        self._run_on_ui.emit(self._capture_running_ui)

    def _check_stderr(self):
        """增量读取设备端 stderr 文件，过滤关键诊断行并输出。"""
        try:
            raw = self._adb.执行shell(
                self._serial,
                f'tail -c +{self._stderr_offset + 1} {self._stderr_path} 2>/dev/null',
                timeout=1.5)
            if not raw:
                try:
                    size = self._adb.执行shell(
                        self._serial,
                        f'stat -c %s {self._stderr_path} 2>/dev/null || echo 0',
                        timeout=1.5)
                    if size:
                        self._stderr_offset = int((size or '0').strip() or '0')
                except Exception:
                    pass
                return
            text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
            total = 0
            try:
                sz = self._adb.执行shell(
                    self._serial,
                    f'stat -c %s {self._stderr_path} 2>/dev/null || echo 0',
                    timeout=1.5)
                total = int((sz or '0').strip() or '0')
            except Exception:
                pass
            self._stderr_offset = total
            for line in text.splitlines():
                ln = line.strip()
                if not ln:
                    continue
                low = ln.lower()
                if 'listening on' in low or 'pcap file' in low:
                    self._log(f'[tcpdump] {ln}')
                elif 'dropped' in low or 'drop' in low:
                    self._log(f'⚠ [tcpdump] {ln}')
                elif any(k in low for k in ('error', 'denied', 'permission',
                                              'no such', 'disk', 'quota',
                                              'space left', 'not found',
                                              'can\'t', 'cannot', 'failed',
                                              'invalid', 'abort')):
                    self._log(f'🔴 [tcpdump] {ln}')
                elif 'packets captured' in low or 'packets received' in low or 'packets dropped' in low:
                    self._log(f'[tcpdump] {ln}')
        except Exception:
            pass

    def _tcpdump_wait_loop(self):
        """官方 adb 模式：等待 tcpdump 进程结束（写设备文件），同时轮询设备端文件大小。"""
        proc = self._proc
        if proc is None:
            return
        try:
            while not self._closed:
                # 轮询设备端文件大小以更新进度
                try:
                    size_out = self._adb.执行shell(
                        self._serial, f'stat -c %s {self._remote_path} 2>/dev/null || echo 0',
                        timeout=2)
                    if size_out:
                        self._bytes = int((size_out or '0').strip() or '0')
                        self._bytes_updated.emit(self._bytes, time.time() - self._start_ts)
                except Exception:
                    pass

                self._check_stderr()
                
                # 检查子进程是否还活着
                if proc.poll() is not None:
                    break
                time.sleep(1.0)
        except Exception as e:
            self._log(f'[错误] 等待异常: {e}')
        finally:
            self._check_stderr()
            # 读取 PC 端 adb 子进程的 stderr/stdout，捕获 adb/设备 shell 层面的报错
            # （如过滤器含括号未加引号 → 设备 shell 语法错误，错误写到 PC 端 stderr，
            # 而非设备端 stderr 文件，导致 _check_stderr 读不到任何内容）
            try:
                p = self._proc
                if p is not None and p.poll() is not None:
                    out_buf = b''
                    err_buf = b''
                    if p.stdout is not None:
                        try:
                            out_buf = p.stdout.read() or b''
                        except Exception:
                            pass
                    if p.stderr is not None:
                        try:
                            err_buf = p.stderr.read() or b''
                        except Exception:
                            pass
                    for buf, tag in ((out_buf, 'adb stdout'), (err_buf, 'adb stderr')):
                        if not buf:
                            continue
                        txt = buf.decode('utf-8', errors='replace') if isinstance(buf, bytes) else buf
                        for ln in txt.splitlines():
                            ln = ln.strip()
                            if ln:
                                self._log(f'🔴 [{tag}] {ln}')
            except Exception:
                pass
            if not self._closed:
                self._stream_ended.emit()

    def _tcpdump_device_runner(self, client, shell_cmd):
        """自研 ADB 模式：在设备端执行 tcpdump（写文件），同时轮询进度。"""
        stop_evt = self._stop_event
        
        def _poll_size():
            if stop_evt.is_set():
                return
            try:
                size_out = self._adb.执行shell(
                    self._serial, f'stat -c %s {self._remote_path} 2>/dev/null || echo 0',
                    timeout=1.5)
                if size_out:
                    self._bytes = int((size_out or '0').strip() or '0')
                    self._bytes_updated.emit(self._bytes, time.time() - self._start_ts)
            except Exception:
                pass
            self._check_stderr()
        
        # USB 模式：client 是 UsbAdbConnection，没有 sock，也无法像 TCP 那样
        # 再借一条独占连接（同一设备只有一条 transport / 一对端点）。
        # 改走共享连接的 shell流，它内部已实现单管道复用与报文转发。
        if getattr(client, 'sock', None) is None and hasattr(client, 'shell流'):
            self._tcpdump_usb_runner(client, shell_cmd, stop_evt, _poll_size)
            return
        
        conn = None
        try:
            conn = _adb_borrow(client.host, client.port, 10.0, client.key_path)
            _adb_detach(conn)
            local_id = None
            try:
                local_id = conn.打开服务(f'shell:{shell_cmd}')
                conn.sock.settimeout(0.5)
                while not stop_evt.is_set():
                    try:
                        msg = conn._接收消息()
                        if msg.command == CMD_WRTE:
                            try:
                                conn._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
                            except Exception:
                                break
                        elif msg.command == CMD_CLSE:
                            break
                    except socket.timeout:
                        _poll_size()
                        continue
                    except (RuntimeError, OSError):
                        break
                _poll_size()
            finally:
                try:
                    if local_id is not None:
                        conn._发送(AdbMessage(CMD_CLSE, local_id, conn._remote_id))
                except Exception:
                    pass
        except Exception as e:
            self._log(f'[错误] 自研adb执行异常: {e}')
        finally:
            self._check_stderr()
            # 关闭连接，释放 ADB transport
            if conn is not None:
                try:
                    conn.关闭()
                except Exception:
                    pass
            if not self._closed:
                self._stream_ended.emit()

    def _tcpdump_usb_runner(self, client, shell_cmd, stop_evt, poll_size):
        """USB 模式：复用共享连接的 shell流 执行设备端 tcpdump。

        与 TCP 分支的差异：
          - 不借新连接、不关闭底层连接（USB 连接是类级共享的）；
          - shell流 内部自带短超时轮询，但不会回调「空闲」事件，
            因此进度轮询放到独立线程里跑。
        """
        def _on_data(chunk: bytes):
            # tcpdump 输出重定向到设备文件，这里通常只有告警/错误
            try:
                txt = chunk.decode('utf-8', errors='replace')
            except Exception:
                return
            for ln in txt.splitlines():
                ln = ln.strip()
                if ln:
                    self._log(f'[tcpdump] {ln}')

        poll_stop = threading.Event()

        def _poll_loop():
            while not poll_stop.wait(1.0):
                if stop_evt.is_set():
                    break
                poll_size()

        poll_thread = threading.Thread(target=_poll_loop, daemon=True)
        poll_thread.start()
        try:
            client.shell流(shell_cmd, _on_data, stop_evt, open_timeout=10.0)
        except Exception as e:
            self._log(f'[错误] 自研adb(USB)执行异常: {e}')
        finally:
            poll_stop.set()
            try:
                poll_thread.join(timeout=2.0)
            except Exception:
                pass
            try:
                poll_size()
            except Exception:
                pass
            self._check_stderr()
            if not self._closed:
                self._stream_ended.emit()

    # ---- 停止 ----
    def _stop(self):
        if not self._running or self._stopping:
            return
        self._stopping = True
        self._log('---- 用户停止 ----')
        
        # 更新按钮状态
        self.btn_stop.setText('⏳ 正在停止...')
        self.btn_stop.setEnabled(False)
        self.status_label.setText('正在停止抓包...')
        self.status_label.setStyleSheet('color: #ffc56b;')
        
        # 在后台线程中执行阻塞的停止操作
        threading.Thread(target=self._do_stop, daemon=True).start()

    def _do_stop(self):
        """后台线程执行：优雅停止抓包，带分步日志反馈。"""
        self._closed = True

        # ① 终止设备端 tcpdump（带超时保护）
        # （顺序：先 SIGINT 让 tcpdump flush → 再关 adb stdout →
        #   等读取线程退出 → 最后收尾文件）
        self._log('[停止] 正在终止设备端 tcpdump...')
        self._stop_progress.emit('正在终止设备端 tcpdump...')
        
        kill_done = threading.Event()
        def _do_kill():
            try:
                self._graceful_kill_device_tcpdump()
            finally:
                kill_done.set()
        
        kill_thread = threading.Thread(target=_do_kill, daemon=True)
        kill_thread.start()
        if not kill_done.wait(timeout=8.0):
            self._log('[停止] SIGINT 发送超时，强制结束...')
            # 超时后继续，不等了
        
        # ② 设置停止事件/关闭 stdout
        self._log('[停止] 正在通知抓包线程退出...')
        self._stop_progress.emit('正在通知抓包线程退出...')
        
        if self._self_mode:
            if self._stop_event is not None:
                self._stop_event.set()
        else:
            proc = self._proc
            if proc is not None:
                if proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass

        # ③ 等待读取线程结束
        self._log('[停止] 等待抓包线程退出...')
        self._stop_progress.emit('等待抓包线程退出...')
        reader = self._reader
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
            if reader.is_alive():
                self._log('[停止] 抓包线程未及时退出，继续收尾...')

        # ④ 通知主线程完成收尾
        self._log('[停止] 准备拉取文件...')
        self._stop_progress.emit('准备拉取文件...')
        self._stop_completed.emit()

    def _on_stop_completed(self):
        """停止完成后在主线程调用 _finalize。"""
        self._finalize()

    def _on_stop_progress(self, text):
        """主线程接收停止阶段进度更新。"""
        self.status_label.setText(text)
        self.status_label.setStyleSheet('color: #ffc56b;')

    def _on_pull_progress(self, text, pct):
        """主线程接收 pull 进度更新。"""
        self.status_label.setText(text)
        if pct >= 0:
            self.status_label.setStyleSheet('color: #ffc56b;')

    def _parse_tcpdump_stats_text(self, text):
        """从 tcpdump 输出文本中提取统计行（captured / received / dropped）。"""
        if not text:
            return {}
        import re
        stats = {}
        for ln in text.splitlines():
            low = ln.lower().strip()
            if not low:
                continue
            m = re.search(r'(\d+)\s+packets?\s+captured', low)
            if m:
                stats['captured'] = int(m.group(1))
            m = re.search(r'(\d+)\s+packets?\s+received\s+by\s+filter', low)
            if m:
                stats['received'] = int(m.group(1))
            m = re.search(r'(\d+)\s+packets?\s+dropped\s+by\s+kernel', low)
            if m:
                stats['kernel_dropped'] = int(m.group(1))
            m = re.search(r'(\d+)\s+packets?\s+dropped(?!\s+by\s+kernel)', low)
            if m and 'kernel_dropped' not in stats:
                stats['dropped'] = int(m.group(1))
        return stats

    def _count_local_packets(self):
        """用轻量解析器统计本地 pcap 文件中的包数，作为交叉校验。"""
        try:
            from tools.lightweight_pcap_parser import PcapReader
            count = 0
            for _ in PcapReader(self._path):
                count += 1
            return count
        except Exception:
            return -1

    def _report_final_diagnostics(self):
        """汇总诊断：优先用设备端 tcpdump -r 的官方统计，其次 stderr，最后本地解析器交叉校验。"""
        device_stats = self._parse_tcpdump_stats_text(self._device_tcpdump_r)
        stderr_stats = {}
        stderr_errors = []
        try:
            raw = self._adb.执行shell(
                self._serial, f'cat {self._stderr_path} 2>/dev/null', timeout=3)
            if raw:
                text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
                stderr_stats = self._parse_tcpdump_stats_text(text)
                for ln in text.splitlines():
                    low = ln.lower().strip()
                    if low and any(k in low for k in ('error', 'denied', 'permission',
                                                       'no such', 'disk', 'quota',
                                                       'space left', 'not found',
                                                       'can\'t', 'cannot', 'failed',
                                                       'invalid', 'abort', 'unreachable')):
                        stderr_errors.append(ln.strip())
        except Exception:
            pass

        # 设备端统计：优先 tcpdump -r（最权威），其次 stderr
        captured = device_stats.get('captured', stderr_stats.get('captured'))
        received = device_stats.get('received', stderr_stats.get('received'))
        kernel_dropped = device_stats.get('kernel_dropped', stderr_stats.get('kernel_dropped'))
        dropped = device_stats.get('dropped', stderr_stats.get('dropped'))
        any_stats = captured is not None or received is not None or kernel_dropped is not None

        local_count = self._count_local_packets()
        usb_res = getattr(self, '_usb_verify_result', None)

        if (not any_stats and local_count < 0 and not stderr_errors
                and usb_res is None):
            return

        self._log('── tcpdump 诊断汇总 ──')
        if captured is not None:
            self._log(f'  设备端捕获包数 (tcpdump captured): {captured}')
        if received is not None:
            self._log(f'  过滤器接收包数 (received by filter): {received}')
        if kernel_dropped is not None:
            if kernel_dropped == 0:
                self._log('  ✅ 内核丢包: 0')
            else:
                self._log(f'⚠  内核丢包: {kernel_dropped}（可能导致响应体损坏）')
        elif dropped is not None:
            if dropped == 0:
                self._log('  ✅ 丢包: 0')
            else:
                self._log(f'⚠  丢包: {dropped}')
        if local_count >= 0:
            self._log(f'  本地文件包数: {local_count}（解析器交叉校验）')
            if captured is not None and local_count != captured:
                diff = captured - local_count
                self._log(f'⚠  设备端 {captured} 个 vs 本地 {local_count} 个，差 {diff} —— 可能是 adb pull 丢数据')
        if stderr_errors:
            self._log('🔴 tcpdump 运行错误:')
            for e in stderr_errors[:10]:
                self._log(f'    {e}')
            if len(stderr_errors) > 10:
                self._log(f'    ... 共 {len(stderr_errors)} 条')

        if kernel_dropped and kernel_dropped > 0:
            self._log('📌 内核丢包通常因为抓包量超出内核环形缓冲区 —— 建议用更精确的过滤（如 tcp and (port 80 or port 443)）')

        # U 盘模式：输出完整性校验结论，通过才提示可拔出
        if usb_res is not None:
            ok, verify_lines = usb_res
            self._log('── U 盘文件完整性校验 ──')
            for ln in verify_lines:
                self._log(f'  {ln}')
            if ok:
                self.status_label.setText('已校验 · 可安全拔出U盘')
                self.status_label.setStyleSheet('color: #1de9b6;')
                self._log('[完成] 校验通过，可以安全拔出 U 盘')
            else:
                self.status_label.setText('校验未通过 · 请勿拔出U盘')
                self.status_label.setStyleSheet('color: #ff6161;')

    def _on_pull_finished(self, ok, local_size):
        """主线程接收 pull 完成通知。"""
        if ok:
            self._bytes = local_size
            self.status_label.setText(f'已停止 · {self._fmt_size(local_size)}')
            self.status_label.setStyleSheet('color: #1de9b6;')
        else:
            self.status_label.setText('已停止 · 拉取失败')
            self.status_label.setStyleSheet('color: #ff6b6b;')
        self._refresh_stat()
        self._proc = None
        self._self_mode = False
        self._stop_event = None

        self._report_final_diagnostics()

        # 非用户停止且秒退无数据：多为设备端权限报错或网卡名错误
        if not self._closed and self._bytes == 0 and (time.time() - self._start_ts) < 3:
            self._log('[提示] tcpdump 立即退出且无数据：常见原因为抓包权限不足(需root)或网卡名错误')

        # 在后台线程中执行校验
        self._repair_thread = threading.Thread(
            target=self._bg_verify_and_repair, daemon=True)
        self._repair_thread.start()

    def _bg_pull_and_verify(self):
        """后台线程：拉取文件 + 完整性校验。"""
        self._log('[Pull] 正在检查设备端文件...')
        self._pull_progress.emit('正在检查设备端文件...', -1)

        # 获取设备端文件大小
        remote_size = 0
        try:
            size_out = self._adb.执行shell(
                self._serial, f'stat -c %s {self._remote_path} 2>/dev/null || echo 0',
                timeout=5)
            remote_size = int((size_out or '0').strip() or '0')
        except Exception:
            pass

        if remote_size <= 0:
            self._log('[Pull] 设备端文件为空')
            self._pull_finished.emit(False, 0)
            return

        self._log(f'[Pull] 设备端文件: {self._fmt_size(remote_size)}')
        self._log('[Pull] 开始拉取到本地...')
        self._pull_progress.emit(f'正在拉取 {self._fmt_size(remote_size)} ...', 0)

        pull_ok = self._pull_from_device_with_progress(remote_size)

        if pull_ok:
            local_size = os.path.getsize(self._path) if os.path.isfile(self._path) else 0
            self._log(f'[Pull] pcap 完成: {self._fmt_size(local_size)}')
            self._pull_finished.emit(True, local_size)
        else:
            self._log('[错误] 文件拉取失败')
            self._pull_finished.emit(False, 0)

    def _graceful_kill_device_tcpdump(self):
        """向设备端 tcpdump 发送 Ctrl+C，使其 flush 缓冲区并优雅退出。
        发送 SIGINT 后轮询进程是否退出，最多等 5 秒，确保缓冲区落盘后再继续。
        """
        def _执行(cmd, timeout=3):
            try:
                if getattr(self._adb, '_用自研adb', False):
                    client = self._adb._获取自研adb(self._serial)
                    if client:
                        return client.执行shell(cmd, timeout=timeout)
                else:
                    return self._adb.执行shell(self._serial, cmd, timeout=timeout)
            except Exception:
                return None

        def _tcpdump还在运行():
            """检查设备端是否还有 tcpdump 进程在运行。"""
            out = _执行('pidof tcpdump 2>/dev/null || ps -A 2>/dev/null | grep -c "[t]cpdump"', timeout=2)
            if out is None:
                return False
            text = out.decode('utf-8', errors='replace') if isinstance(out, bytes) else str(out)
            return bool(text.strip())

        try:
            # ① 发送 SIGINT 让 tcpdump 优雅退出（flush 缓冲区）
            _执行('kill -INT $(pidof tcpdump 2>/dev/null) 2>/dev/null', timeout=3)
            self._log('[停止] 已向设备端 tcpdump 发送 SIGINT，等待缓冲区落盘...')

            # ② 轮询等待 tcpdump 进程退出（最多 5 秒）
            #    自研 ADB 模式下必须等进程退出后再关 shell 连接，
            #    否则 CMD_CLSE 会触发 SIGHUP 强制杀死 tcpdump，缓冲区丢失
            等待开始 = time.time()
            while _tcpdump还在运行() and (time.time() - 等待开始) < 5.0:
                time.sleep(0.2)

            if _tcpdump还在运行():
                # ③ 超时仍未退出，发送 SIGTERM 强制终止
                self._log('[停止] tcpdump 未在 5 秒内退出，发送 SIGTERM 强制终止...')
                _执行('kill -TERM $(pidof tcpdump 2>/dev/null) 2>/dev/null', timeout=2)
                time.sleep(0.5)
                if _tcpdump还在运行():
                    _执行('kill -KILL $(pidof tcpdump 2>/dev/null) 2>/dev/null', timeout=2)
                    time.sleep(0.3)
            else:
                self._log('[停止] tcpdump 已优雅退出，缓冲区已落盘')
        except Exception:
            pass

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

    @staticmethod
    def _fmt_size(nbytes):
        """格式化字节数为人类可读字符串。"""
        if nbytes < 1024:
            return f'{nbytes} B'
        elif nbytes < 1024 * 1024:
            return f'{nbytes / 1024:.1f} KB'
        elif nbytes < 1024 * 1024 * 1024:
            return f'{nbytes / 1024 / 1024:.1f} MB'
        else:
            return f'{nbytes / 1024 / 1024 / 1024:.2f} GB'

    def _finalize(self):
        if not self._running:
            return
        self._running = False
        self._stopping = False
        self._timer.stop()
        self._close_proc()

        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setText('■ 停止')

        # U 盘模式：数据保留在设备 U 盘，不拉回本地
        if self._on_usb_mode:
            self.status_label.setText('已停止 · 正在校验U盘文件...')
            self.status_label.setStyleSheet('color: #ffc56b;')
            self._log(f'[完成] pcap 已保存在设备 U 盘: {self._remote_path}')
            self._log('[完成] 正在校验完整性，请等待「可安全拔出」提示')
            self._proc = None
            self._self_mode = False
            self._stop_event = None
            # 后台校验 U 盘文件 + 收集统计，完成后主线程输出结论
            threading.Thread(target=self._bg_usb_diagnostics, daemon=True).start()
            return

        # 立即可见的状态更新
        self.status_label.setText('准备拉取文件...')
        self.status_label.setStyleSheet('color: #ffc56b;')

        # 在后台线程中执行 pull + 校验，避免 UI 冻结
        self._pull_thread = threading.Thread(
            target=self._bg_pull_and_verify, daemon=True)
        self._pull_thread.start()

    def _bg_usb_diagnostics(self):
        """U 盘模式后台线程：校验 U 盘 pcap 完整性 + 收集统计，通知主线程输出。"""
        self._usb_verify_result = self._verify_usb_pcap()
        try:
            self._collect_device_tcpdump_stats()
        except Exception:
            self._device_tcpdump_r = ''
        self._usb_diag_done.emit()

    def _verify_usb_pcap(self):
        """设备端校验 U 盘 pcap：魔数有效 + 写入稳定 + 与抓包统计大小一致。

        返回 (是否通过, 日志行列表)。
        """
        path = self._remote_path
        captured = int(self._bytes or 0)
        # 间隔 1 秒读两次文件大小，稳定即落盘完成；顺带读 4 字节魔数
        probe = (
            f's1=$(wc -c < {path} 2>/dev/null); sleep 1; '
            f's2=$(wc -c < {path} 2>/dev/null); '
            f'm=$(dd if={path} bs=4 count=1 2>/dev/null | od -An -tx1); '
            f'echo "S1:$s1"; echo "S2:$s2"; echo "M:$m"'
        )
        try:
            out = str(self._adb.执行shell(self._serial, probe, timeout=20) or '')
        except Exception as e:
            return False, [f'[校验] 设备端校验执行失败: {e}']
        s1 = s2 = -1
        magic = ''
        for ln in out.splitlines():
            ln = ln.strip()
            if ln.startswith('S1:'):
                try:
                    s1 = int(ln[3:].strip() or -1)
                except ValueError:
                    s1 = -1
            elif ln.startswith('S2:'):
                try:
                    s2 = int(ln[3:].strip() or -1)
                except ValueError:
                    s2 = -1
            elif ln.startswith('M:'):
                magic = ln[2:].strip().replace(' ', '')
        if s1 < 0:
            return False, [
                '[校验] 无法读取 U 盘文件大小，请勿拔出',
                f'[校验] 可手动确认: su -c "wc -c < {path}"']
        lines = [f'[校验] 抓包统计大小: {captured // 1024} KB · '
                 f'U 盘实际大小: {s1 // 1024} KB']
        ok = True
        if magic not in ('d4c3b2a1', 'a1b2c3d4', '0a0d0d0a'):
            ok = False
            lines.append(f'[校验] 🔴 文件头无效: {magic or "空"}（非 pcap 格式）')
        else:
            lines.append('[校验] ✅ 文件头有效（pcap 魔数正确）')
        if s1 != s2:
            ok = False
            lines.append('[校验] 🔴 写入未稳定（1 秒内大小仍在变化），请稍后再拔')
        else:
            lines.append('[校验] ✅ 写入已稳定落盘')
        if captured > 0 and s1 + 2048 < captured:
            ok = False
            lines.append(
                f'[校验] 🔴 U 盘文件比抓包统计少 {(captured - s1) // 1024} KB，'
                '可能未写完，请勿拔出')
        return ok, lines

    def _pull_from_device_with_progress(self, remote_size):
        """带进度的 pull 实现。返回是否成功。"""
        try:
            if getattr(self._adb, '_用自研adb', False):
                return self._pull_self_with_progress(remote_size)
            else:
                return self._pull_official_with_progress(remote_size)
        except Exception as e:
            self._log(f'[Pull] 异常: {e}')
            return False

    def _pull_official_with_progress(self, remote_size):
        """官方 adb 模式：用 Popen 读取 pull 进度。"""
        cmd = [self._adb.adb_path, '-s', self._serial, 'pull',
               self._remote_path, self._path]
        self._log(f'[Pull] $ adb -s {self._serial} pull {self._remote_path}')

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            creationflags=CREATE_NO_WINDOW)

        start_time = time.time()
        last_pct = -1
        try:
            # adb pull 的进度信息输出到 stderr（如 '[ 45%] 1234/5678'）
            # 但 adb 也可能不输出进度，所以我们用文件轮询作为备选
            while proc.poll() is None:
                # 每 0.5 秒检查本地文件大小
                if os.path.isfile(self._path):
                    current = os.path.getsize(self._path)
                    self._bytes = current
                    pct = int(current / remote_size * 100) if remote_size > 0 else 0
                    if pct != last_pct:
                        last_pct = pct
                        elapsed = time.time() - start_time
                        speed = current / elapsed if elapsed > 0 else 0
                        eta = (remote_size - current) / speed if speed > 0 else 0
                        self._log(
                            f'[Pull] {pct:3d}% · {self._fmt_size(current)}/{self._fmt_size(remote_size)}'
                            f' · {self._fmt_size(int(speed))}/s · 预计 {int(eta)}s')
                        self._pull_progress.emit(
                            f'拉取中 {pct}% · {self._fmt_size(current)}/{self._fmt_size(remote_size)}', pct)
                        self._bytes_updated.emit(current, elapsed)
                time.sleep(0.5)
            
            proc.wait(timeout=600)
            
            if proc.returncode == 0 and os.path.isfile(self._path):
                local_size = os.path.getsize(self._path)
                self._bytes = local_size
                # 设备端文件暂不清理，等本地校验完整后再删整个临时文件夹
                self._collect_device_tcpdump_stats()
                return True
            else:
                self._log(f'[Pull] adb pull 返回码: {proc.returncode}')
                try:
                    err_txt = (proc.stderr.read() or b'').decode(
                        'utf-8', errors='replace').strip()
                    for ln in err_txt.splitlines()[:5]:
                        if ln.strip():
                            self._log(f'🔴 [adb pull] {ln.strip()}')
                except Exception:
                    pass
                return False
        except Exception as e:
            self._log(f'[Pull] 异常: {e}')
            return False

    def _pull_self_with_progress(self, remote_size):
        """自研 adb sync pull（快速版）：直接操作 socket 高效读取。

        优化点：
        - recv_into + bytearray 替代 _recv_exact 的 bytes 拼接（减少 60%+ 内存拷贝）
        - TCP_NODELAY 关闭 Nagle 算法（小包零延迟）
        - 文件写攒 256KB 再 write（减少系统调用）
        - 单次 recv 超时 30s：数据流断掉时快速中止，回退官方 adb pull
        - sync 包按「字节流」解析：adbd 会把多个 sync 包合并进一个 WRTE 载荷，
          或把一个包拆到多条 WRTE（DATA 包=24B头+64K数据=65560B，与 max_payload
          不对齐）。此前按「一条 WRTE=一个完整包」解析，错位后 DONE 被吞掉，
          主机永远等不到数据 → 30 秒超时「sync 数据流中断」
        """
        client = self._adb._获取自研adb(self._serial)
        if not client:
            self._log('[Pull] 自研adb连接失败')
            return False

        # USB 设备（序列号不含冒号）没有 socket，sync 快速通道依赖 sock.recv_into，
        # 无法复用 → 用自研 adb 客户端通用拉取接口（client.拉取文件），文件轮询显示进度。
        if ':' not in self._serial:
            return self._pull_self_usb_with_progress(client, remote_size)

        self._log('[Pull] 使用自研adb sync协议拉取（快速模式）...')
        start_time = time.time()
        conn = None
        try:
            conn = _adb_borrow(client.host, client.port, 10.0, client.key_path)
            _adb_detach(conn)
            local_id = None
            old_timeout = conn.sock.gettimeout()
            try:
                # 30s 无数据即视为数据流中断（健康传输间隔在毫秒级），
                # 中断会触发 socket.timeout → 回退官方 adb pull
                conn.sock.settimeout(30.0)
                conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                local_id = conn.打开服务('sync:')
                path_bytes = self._remote_path.encode('utf-8')
                recv_cmd = b'RECV' + struct.pack('<I', len(path_bytes)) + path_bytes
                conn._发送(AdbMessage(CMD_WRTE, local_id, conn._remote_id, recv_cmd))
                msg = conn._接收消息()
                if msg.command != CMD_OKAY:
                    self._log('[Pull] RECV 失败')
                    return False

                sock = conn.sock
                downloaded = 0
                last_log_pct = -1
                got_done = False
                pending_write = bytearray()
                WRITE_THRESHOLD = 256 * 1024  # 攒 256KB 再写
                sbuf = bytearray()  # sync 字节流缓冲（sync 包与 WRTE 载荷不对齐）

                def _fast_recv_exact(n):
                    """高效读 n 字节到 bytearray，避免 bytes 拼接。"""
                    buf = bytearray(n)
                    pos = 0
                    while pos < n:
                        try:
                            r = sock.recv_into(memoryview(buf)[pos:])
                        except socket.timeout:
                            raise
                        if r == 0:
                            raise RuntimeError("连接断开")
                        pos += r
                    return bytes(buf)

                def _read_adb_into_sbuf():
                    """读一条 ADB 报文：WRTE 载荷并入 sync 流缓冲并回 OKAY。

                    返回 False 表示本流已结束（CLSE/发送失败），True 可继续读。
                    """
                    header = _fast_recv_exact(24)
                    command, arg0, arg1, length, crc, magic = \
                        struct.unpack('<IIIIII', header)
                    if magic != (command ^ 0xffffffff):
                        raise RuntimeError(f"magic 不匹配: exp={command ^ 0xffffffff:#x}")
                    payload = _fast_recv_exact(length) if length > 0 else b''
                    if command == CMD_WRTE:
                        if arg1 == local_id:
                            sbuf.extend(payload)
                        try:
                            conn._发送(AdbMessage(CMD_OKAY, local_id, conn._remote_id))
                        except Exception:
                            return False
                    elif command == CMD_CLSE:
                        if arg1 == local_id:
                            return False
                    # CMD_OKAY 等其他报文：忽略，继续读
                    return True

                with open(self._path, 'wb') as f:
                    while not got_done:
                        # ── 先从缓冲解析所有完整 sync 包 ──
                        while len(sbuf) >= 4:
                            cmd4 = bytes(sbuf[:4])
                            if cmd4 == b'DATA':
                                if len(sbuf) < 8:
                                    break
                                data_len = struct.unpack('<I', sbuf[4:8])[0]
                                if len(sbuf) < 8 + data_len:
                                    break  # 包未收全，读下一条 ADB 报文
                                pending_write.extend(sbuf[8:8 + data_len])
                                del sbuf[:8 + data_len]
                                downloaded += data_len
                                self._bytes = downloaded

                                if len(pending_write) >= WRITE_THRESHOLD:
                                    f.write(bytes(pending_write))
                                    pending_write.clear()

                                pct = int(downloaded / remote_size * 100) if remote_size > 0 else 0
                                if pct != last_log_pct and (pct % 5 == 0):
                                    last_log_pct = pct
                                    elapsed = time.time() - start_time
                                    speed = downloaded / elapsed if elapsed > 0 else 0
                                    eta = (remote_size - downloaded) / speed if speed > 0 else 0
                                    self._log(
                                        f'[Pull] {pct:3d}% · {self._fmt_size(downloaded)}/{self._fmt_size(remote_size)}'
                                        f' · {self._fmt_size(int(speed))}/s · 预计 {int(eta)}s')
                                    self._pull_progress.emit(
                                        f'拉取中 {pct}% · {self._fmt_size(downloaded)}/{self._fmt_size(remote_size)}', pct)
                                    self._bytes_updated.emit(downloaded, elapsed)
                            elif cmd4 == b'DONE':
                                if len(sbuf) < 8:
                                    break
                                del sbuf[:8]
                                if pending_write:
                                    f.write(bytes(pending_write))
                                    pending_write.clear()
                                got_done = True
                                break
                            elif cmd4 == b'FAIL':
                                if len(sbuf) < 8:
                                    break
                                err_len = struct.unpack('<I', sbuf[4:8])[0]
                                if len(sbuf) < 8 + err_len:
                                    break
                                err = bytes(sbuf[8:8 + err_len]).decode('utf-8', errors='replace')
                                del sbuf[:8 + err_len]
                                raise RuntimeError(f'设备拒绝: {err}')
                            else:
                                raise RuntimeError(f'sync 流协议错误: 未知包 {cmd4!r}')
                        if got_done:
                            break
                        if not _read_adb_into_sbuf():
                            break  # 设备关闭流

                if got_done:
                    elapsed = time.time() - start_time
                    rate = downloaded / elapsed / 1024 if elapsed > 0 else 0
                    self._log(f'[Pull] sync 完成: {self._fmt_size(downloaded)} · {rate:.0f} KB/s · {elapsed:.1f}s')
                    self._bytes = downloaded
                    # 设备端文件暂不清理，等本地校验完整后再删整个临时文件夹
                    self._collect_device_tcpdump_stats()
                    return True
                else:
                    self._log('[Pull] sync 未完成，回退官方 adb pull...')
            except socket.timeout:
                self._log('[Pull] sync 数据流中断（30 秒无数据），回退官方 adb pull...')
            except (TimeoutError, RuntimeError, OSError) as e:
                self._log(f'[Pull] sync 异常: {e}，回退官方 adb pull...')
            finally:
                conn.sock.settimeout(old_timeout)
                try:
                    conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
                except Exception:
                    pass
                try:
                    if local_id is not None:
                        conn._发送(AdbMessage(CMD_CLSE, local_id, conn._remote_id))
                except Exception:
                    pass
        except Exception as e:
            self._log(f'[Pull] sync 连接异常: {e}，回退 shell 方式...')
        finally:
            if conn is not None:
                try:
                    conn.关闭()
                except Exception:
                    pass

        # sync 失败，优先回退官方 adb pull（独立连接，最可靠）
        if getattr(self._adb, 'adb_path', ''):
            self._log('[Pull] 回退官方 adb pull...')
            try:
                if self._pull_official_with_progress(remote_size):
                    return True
            except Exception as e:
                self._log(f'[Pull] 官方 adb pull 异常: {e}')

        # 最后回退 shell 方式（base64）
        self._log('[Pull] 使用 shell(base64) 方式拉取...')
        try:
            import base64
            b64_data = client.执行shell(f'base64 "{self._remote_path}"', timeout=120)
            b64_clean = ''.join(b64_data.split())
            if not b64_clean:
                self._log('[Pull] shell 方式：文件为空')
                return False
            file_data = base64.b64decode(b64_clean)
            with open(self._path, 'wb') as f:
                f.write(file_data)
            self._bytes = len(file_data)
            self._log(f'[Pull] shell 方式完成: {self._fmt_size(len(file_data))}')
            self._pull_progress.emit(
                f'拉取中 100% · {self._fmt_size(len(file_data))}/{self._fmt_size(remote_size)}', 100)
            # 设备端文件暂不清理，等本地校验完整后再删整个临时文件夹
            self._collect_device_tcpdump_stats()
            return True
        except Exception as e:
            self._log(f'[Pull] shell 方式也失败: {e}')
            return False

    def _pull_self_usb_with_progress(self, client, remote_size):
        """USB 模式自研 adb 拉取（client.拉取文件 + 文件轮询显示进度）。"""
        self._log('[Pull] 使用自研adb USB 通道拉取...')
        start_time = time.time()

        result = {'ok': False, 'error': None}

        def _do_pull():
            try:
                result['ok'] = client.拉取文件(
                    self._remote_path, self._path, timeout=300)
            except Exception as e:
                result['error'] = str(e)

        pull_thread = threading.Thread(target=_do_pull, daemon=True)
        pull_thread.start()

        last_pct = -1
        while pull_thread.is_alive():
            if os.path.isfile(self._path):
                current = os.path.getsize(self._path)
                self._bytes = current
                pct = int(current / remote_size * 100) if remote_size > 0 else 0
                if pct != last_pct:
                    last_pct = pct
                    elapsed = time.time() - start_time
                    speed = current / elapsed if elapsed > 0 else 0
                    eta = (remote_size - current) / speed if speed > 0 else 0
                    self._log(
                        f'[Pull] {pct:3d}% · {self._fmt_size(current)}/{self._fmt_size(remote_size)}'
                        f' · {self._fmt_size(int(speed))}/s · 预计 {int(eta)}s')
                    self._pull_progress.emit(
                        f'拉取中 {pct}% · {self._fmt_size(current)}/{self._fmt_size(remote_size)}', pct)
                    self._bytes_updated.emit(current, elapsed)
            time.sleep(0.5)

        pull_thread.join(timeout=300)

        if result['ok'] and os.path.isfile(self._path):
            local_size = os.path.getsize(self._path)
            self._bytes = local_size
            self._collect_device_tcpdump_stats()
            return True
        else:
            self._log(f'[Pull] USB 自研拉取失败: {result.get("error", "拉取返回 False")}')
            return False

    def _自动推送tcpdump(self):
        """设备无 tcpdump 时，从本地 vendor/tcpdump/ 推送对应架构二进制到设备。

        流程：检测设备架构 → 查找本地 vendor/tcpdump/tcpdump_<arch> →
        推送到 /sdcard/Super_ADB/ → 复制到 /data/local/tmp/tcpdump 并 chmod +x
        （/sdcard 通常 noexec 无法直接执行）→ 验证可执行 → 设置 self._tcpdump_bin
        """
        try:
            # 1. 检测设备架构（多种方式备选，都失败时默认 arm64）
            abi = ''
            for prop_cmd in ['getprop ro.product.cpu.abi', 'getprop ro.product.cpu.abilist']:
                abi = (self._adb.执行shell(
                    self._serial, prop_cmd, timeout=5) or '').strip()
                if abi:
                    # abilist 是逗号分隔列表，取第一个
                    if ',' in abi:
                        abi = abi.split(',')[0].strip()
                    break
            if not abi:
                # 备选：uname -m（aarch64 / armv7l / x86_64 / i686）
                uname_out = (self._adb.执行shell(
                    self._serial, 'uname -m', timeout=5) or '').strip()
                if uname_out:
                    abi = uname_out
                    self._log(f'[推送] getprop 失败，用 uname -m: {abi}')
            if not abi:
                self._log('[推送] 架构检测失败，默认使用 arm64（大部分现代手机）')
                abi = 'arm64-v8a'
            self._log(f'[推送] 设备架构: {abi}')

            # 2. 映射架构到本地文件名
            if 'arm64' in abi or 'aarch64' in abi:
                arch = 'arm64'
            elif 'armeabi' in abi or 'arm' in abi or 'armv7' in abi:
                arch = 'arm'
            elif 'x86_64' in abi or 'amd64' in abi:
                arch = 'x86_64'
            elif 'x86' in abi or 'i686' in abi or 'i386' in abi:
                arch = 'x86'
            else:
                self._log(f'[推送] 不支持的架构: {abi}，默认使用 arm64')
                arch = 'arm64'

            # 3. 检查本地 外部扩展/tcpdump/ 文件夹有没有对应架构的二进制
            import glob
            # 兼容源码模式（Super_ADB_Win/外部扩展/）与冻结模式（_internal/外部扩展/）
            here = os.path.dirname(os.path.abspath(__file__))
            ext_dir = None
            for root in [os.path.dirname(here), here, os.getcwd()]:
                candidate = os.path.join(root, 'vendor', 'tcpdump')
                if os.path.isdir(candidate):
                    ext_dir = candidate
                    break
            if ext_dir is None:
                ext_dir = os.path.join(os.path.dirname(here), 'vendor', 'tcpdump')
            local_bin = os.path.join(ext_dir, f'tcpdump_{arch}')
            if not os.path.isfile(local_bin):
                candidates = glob.glob(os.path.join(ext_dir, f'tcpdump_{arch}*'))
                if candidates:
                    local_bin = candidates[0]
                else:
                    self._log(f'[推送] 本地 外部扩展/tcpdump/ 未找到 tcpdump_{arch} 二进制')
                    self._log(f'[推送] 请将对应架构的 tcpdump 放到: {ext_dir}')
                    return False

            self._log(f'[推送] 本地二进制: {os.path.basename(local_bin)} ({os.path.getsize(local_bin)} 字节)')

            # 4. 确保 /sdcard/Super_ADB/ 目录存在
            mkdir_out = (self._adb.执行shell(
                self._serial, 'mkdir -p /sdcard/Super_ADB 2>&1', timeout=5) or '').strip()
            if mkdir_out and 'denied' in mkdir_out.lower():
                self._log(f'[推送] /sdcard 不可写: {mkdir_out}')
                return False

            # 5. 推送到 /sdcard/Super_ADB/（保持原文件名）
            # 注意：Adb设备操作 没有 推送文件 方法（该方法在 AdbFileManager 中），
            # 所以这里创建 AdbFileManager 实例来推送，复用 self._adb 的配置。
            self._log('[推送] 推送到 /sdcard/Super_ADB/ ...')
            _fm = AdbFileManager()
            _fm._用自研adb = getattr(self._adb, '_用自研adb', False)
            _fm.log_callback = getattr(self._adb, 'log_callback', None)
            _fm.adb_path = getattr(self._adb, 'adb_path', '')
            push_ok = _fm.推送文件(self._serial, local_bin, '/sdcard/Super_ADB/')
            if not push_ok:
                self._log('[推送] 推送到 /sdcard 失败（可能 /sdcard 不可写或空间不足）')
                return False

            # 5.1 校验推送后文件大小
            remote_sd = f'/sdcard/Super_ADB/tcpdump_{arch}'
            sd_size = (self._adb.执行shell(
                self._serial, f'wc -c < {remote_sd} 2>/dev/null', timeout=5) or '').strip()
            try:
                sd_size_int = int(sd_size.split()[0]) if sd_size else 0
            except (ValueError, IndexError):
                sd_size_int = 0
            if sd_size_int < 100000:
                self._log(f'[推送] /sdcard 上文件异常（大小={sd_size_int}B），推送可能不完整')
                return False
            self._log(f'[推送] /sdcard 校验通过（{sd_size_int} 字节）')

            # 6. 复制到 /data/local/tmp/tcpdump 并 chmod +x（/sdcard 通常 noexec）
            remote_exec = '/data/local/tmp/tcpdump'
            self._log(f'[推送] 复制到 {remote_exec} 并设置执行权限...')
            cp_out = (self._adb.执行shell(
                self._serial,
                f'cat {remote_sd} > {remote_exec} 2>&1 && chmod 755 {remote_exec} 2>&1 && echo OK',
                timeout=10) or '').strip()
            if 'OK' not in cp_out:
                self._log(f'[推送] 复制到 /data/local/tmp 失败: {cp_out or "无输出"}')
                self._log('[推送] 可能原因：/data/local/tmp 不可写、空间不足、或 SELinux 拒绝')
                return False

            # 6.1 校验执行文件大小和权限
            exec_info = (self._adb.执行shell(
                self._serial, f'wc -c < {remote_exec} 2>/dev/null; ls -l {remote_exec} 2>/dev/null', timeout=5) or '').strip()
            self._log(f'[推送] 执行文件校验: {exec_info or "无输出"}')

            # 7. 验证可执行
            ver = (self._adb.执行shell(
                self._serial, f'{remote_exec} --version 2>&1 | head -n1', timeout=5) or '').strip()
            _ver_err = any(k in ver for k in ['not found', 'No such file', 'inaccessible', 'cannot execute', 'permission denied', 'Exec format error'])
            if ver and not _ver_err:
                self._log(f'[推送] 验证成功: {ver}')
                self._tcpdump_bin = remote_exec
                return True
            else:
                self._log(f'[推送] 验证失败: {ver or "无输出"}')
                if 'Exec format error' in ver:
                    self._log('[推送] 可能原因：架构不匹配（推送了错误架构的二进制）')
                elif 'permission denied' in ver or 'cannot execute' in ver:
                    self._log('[推送] 可能原因：SELinux 拒绝执行 /data/local/tmp/ 下的文件')
                else:
                    self._log('[推送] 可能原因：文件损坏、架构不匹配、或 SELinux 限制')
                return False

        except Exception as e:
            self._log(f'[推送] 异常: {e}')
            return False

    def _collect_device_tcpdump_stats(self):
        """拉取完成但未清理设备端文件前，用 tcpdump -r 重读 pcap 获取官方统计行。
        
        tcpdump -r 会重新解析 pcap 并在退出时打印：
          X packets captured
          X packets received by filter
          X packets dropped by kernel
        这比只读 stderr 更可靠（SIGINT 杀进程时 stderr 行可能没刷出来）。
        结果存入 self._device_tcpdump_r，供 _report_final_diagnostics 使用。
        """
        try:
            inner = f'{self._tcpdump_bin} -r {self._remote_path} 2>&1 | tail -n 15'
            out = self._adb.执行shell(self._serial, inner, timeout=5)
            if isinstance(out, bytes):
                out = out.decode('utf-8', errors='replace')
            self._device_tcpdump_r = (out or '').strip()
        except Exception:
            self._device_tcpdump_r = ''

    def _clean_remote_file(self):
        """删除设备端整个临时文件夹（pcap/stderr 一并清理）。"""
        try:
            self._adb.执行shell(
                self._serial,
                f'rm -rf {self._remote_dir} 2>/dev/null',
                timeout=5)
            self._log('[Pull] 校验完整，已删除设备端临时文件夹')
        except Exception:
            pass

    def _bg_verify_and_repair(self):
        """后台线程：校验 pcap 文件完整性，完成后发信号通知主线程。"""
        log = self._log_signal.emit
        try:
            pcap_ok, stats = self._verify_pcap(self._path, log_func=log)
            if not pcap_ok and self._path:
                size = os.path.getsize(self._path) if os.path.isfile(self._path) else 0
                log(f'[警告] pcap 文件校验失败，文件可能不完整或已损坏')
                log(f'  文件大小: {size} 字节')
                log(f'  已识别数据包: {stats.get("valid", 0)}')
                log(f'  建议: 重新抓包获取完整数据')
                log(f'  (为防止数据丢失，不再自动修复，请手动备份后重试)')
            self._repair_completed.emit(pcap_ok, self._path, self._bytes)
        except Exception as e:
            log(f'[校验] 异常: {e}')
            self._repair_completed.emit(False, self._path, self._bytes)

    def _on_repair_completed(self, pcap_ok, path, total_bytes):
        """后台修复完成后在主线程更新 UI。"""
        size_kb = total_bytes // 1024
        if pcap_ok:
            self.status_label.setText(f'已停止 · 保存 {size_kb} KB')
            self.status_label.setStyleSheet('color: #98c379;')
            # 本地校验完整，后台删除设备端临时文件夹，防止堆积
            threading.Thread(target=self._clean_remote_file, daemon=True).start()
        else:
            self.status_label.setText(f'已停止 · 文件异常 {size_kb} KB')
            self.status_label.setStyleSheet('color: #ff6b6b;')
            self._log(f'[警告] pcap 文件可能不完整或已损坏 ({size_kb} KB)')
            self._log(f'  可能原因: 设备端 tcpdump 被强制杀死，缓冲区数据丢失')
            self._log(f'  建议: 重新抓包，停止时先等 tcpdump 处理完成再关闭')
            self._log(path)

    def _verify_pcap(self, path, log_func=None):
        """校验 pcap 文件完整性：使用轻量PCAP解析器验证可读性。
        
        Returns:
            (是否有效, 统计信息dict)
        """
        _log = log_func or self._log
        stats = {'valid': 0, 'errors': 0, 'total': 0}
        
        if not path or not os.path.isfile(path):
            _log('[校验] 文件不存在')
            return False, stats
        
        try:
            size = os.path.getsize(path)
            if size < 24:
                _log(f'[校验] 文件过小 ({size} 字节)，不足 pcap 全局头大小')
                return False, stats
            
            # 先检查魔数
            with open(path, 'rb') as f:
                header = f.read(24)
            
            if len(header) < 24:
                _log('[校验] 文件过小，无法读取 pcap 全局头')
                return False, stats
            
            magic = header[:4]
            import struct
            
            if magic == b'\xd4\xc3\xb2\xa1':
                endian = '<'
                is_pcapng = False
            elif magic == b'\xa1\xb2\xc3\xd4':
                endian = '>'
                is_pcapng = False
            elif magic == b'\x0a\x0d\x0d\x0a':
                is_pcapng = True
                _log('[校验] pcapng 格式，使用轻量解析器验证')
            else:
                _log(f'[校验] 无效的 pcap 魔数: {magic.hex()}')
                return False, stats
            
            if not is_pcapng:
                ver_major, ver_minor, thiszone, sigfigs, snaplen, network = struct.unpack(
                    f'{endian}HHiIII', header[4:24])
                _log(f'[校验] pcap 版本: {ver_major}.{ver_minor}, 链路类型: {network}, snaplen: {snaplen}')
            
            # 使用轻量PCAP解析器验证
            try:
                from tools.lightweight_pcap_parser import PcapReader
                reader = PcapReader(path)
                count = 0
                has_error = False
                
                for pkt in reader:
                    count += 1
                    if count % 50000 == 0:
                        _log(f'[校验] 已扫描 {count} 个数据包...')
                
                stats['valid'] = count
                stats['total'] = count
                
                if count == 0:
                    _log('[校验] 未找到有效数据包')
                    return False, stats
                
                _log(f'[校验] 共 {count} 个数据包')
                if is_pcapng:
                    _log(f'[校验] pcapng 文件校验通过')
                return True, stats
                
            except ImportError:
                _log('[校验] 轻量PCAP解析模块不可用，使用基础校验')
                # 回退：基础顺序扫描
                return self._verify_pcap_basic(path, header, endian, is_pcapng, log_func=_log)
            except Exception as e:
                _log(f'[校验] 解析器错误: {e}')
                # 回退：基础校验
                ok, basic_stats = self._verify_pcap_basic(path, header, endian, is_pcapng, log_func=_log)
                basic_stats['errors'] += 1
                return ok, basic_stats
                
        except Exception as e:
            _log(f'[校验] 异常: {e}')
            return False, stats
    
    def _verify_pcap_basic(self, path, header, endian, is_pcapng, log_func=None):
        """基础 pcap 校验（回退方案）。"""
        _log = log_func or self._log
        stats = {'valid': 0, 'errors': 0, 'total': 0}
        
        if is_pcapng:
            _log('[校验] pcapng 基础校验: 文件结构有效')
            stats['valid'] = 0
            return True, stats
        
        import struct
        snaplen = struct.unpack(f'{endian}I', header[20:24])[0]
        max_packet_size = snaplen if snaplen > 0 else 65535
        
        with open(path, 'rb') as f:
            data = f.read()
        
        offset = 24
        packet_count = 0
        error_count = 0
        last_error_offset = 0
        
        while offset < len(data):
            remaining = len(data) - offset
            if remaining < 16:
                if remaining > 4:
                    # 可能是尾部填充，不报错
                    pass
                break
            
            ts_sec, ts_usec, incl_len, orig_len = struct.unpack(
                f'{endian}IIII', data[offset:offset+16])
            
            # 合理的时间戳范围 (2020-2035)
            if ts_sec < 1577836800 or ts_sec > 20512224000:
                # 时间戳异常，可能文件损坏
                if error_count == 0:
                    _log(f'[警告] 位置 {offset}: 时间戳异常 ({ts_sec})')
                    last_error_offset = offset
                error_count += 1
                offset += 1
                continue
            
            if incl_len == 0 and orig_len == 0:
                offset += 16
                continue
            
            # 长度检查：incl_len 不应超过合理范围
            if incl_len > 262144:
                if error_count == 0:
                    _log(f'[警告] 位置 {offset}: 数据包长度异常 ({incl_len})')
                    last_error_offset = offset
                error_count += 1
                offset += 1
                continue
            
            if incl_len > remaining - 16:
                error_count += 1
                break
            
            offset += 16 + incl_len
            packet_count += 1
        
        stats['valid'] = packet_count
        stats['total'] = packet_count
        stats['errors'] = error_count
        
        _log(f'[校验] 基础扫描: 共 {packet_count} 个数据包')
        
        if error_count > 0 and packet_count == 0:
            _log(f'[警告] 未找到有效数据包，文件可能已严重损坏')
            return False, stats
        
        if error_count > 0:
            _log(f'[警告] 发现 {error_count} 个异常位置，文件可能不完整')
            if last_error_offset > 0:
                _log(f'  首个异常位置: {last_error_offset}')
            # 只要有有效数据包就认为可用
            return packet_count > 0, stats
        
        return True, stats

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
        if self._running and not self._stopping:
            # 优雅关闭：与 _stop() 相同的流程
            self._stopping = True
            self._closed = True
            self._graceful_kill_device_tcpdump()

            if self._self_mode:
                if self._stop_event is not None:
                    self._stop_event.set()
            else:
                proc = self._proc
                if proc is not None and proc.stdout is not None:
                    try:
                        proc.stdout.close()
                    except Exception:
                        pass

            # 等待读取线程结束
            reader = self._reader
            if reader is not None and reader.is_alive():
                reader.join(timeout=3.0)

            # 关闭文件（确保 flush）
            if self._fh is not None:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None

            self._close_proc()

        super().closeEvent(event)
