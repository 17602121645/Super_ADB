# -*- coding: utf-8 -*-
"""
ADB Logcat 日志查看器 —— 内嵌子页面
=====================================
提供实时日志抓取、停止、过滤、着色显示功能。
使用 QProcess 流式读取 adb logcat 输出，不依赖外部项目。
"""

import os
import re
import datetime
from collections import deque

from PySide6.QtCore import (
    Qt, QProcess, QTimer, QThreadPool, Signal, QObject, QRunnable, QUrl,
)
from PySide6.QtGui import (QColor, QFont, QTextCursor, QTextCharFormat,
                           QDesktopServices)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QLabel, QLineEdit, QCheckBox, QPlainTextEdit, QAbstractSpinBox,
    QScrollBar, QHeaderView, QListView, QMenu, QFileDialog, QSizePolicy,
)

from adb_utils import AdbHelper, format_device_label, load_json_config, save_json_config
from fav_combo import FavComboBox

# 缓冲区上限
BUFFER_MAX = 100_000
VIEW_MAX_BLOCKS = 30_000
RENDER_MAX = 20_000

# 收藏持久化配置
CONFIG_NAME = 'adb_shell_config.json'
FAV_KEY = 'log_favs'

# 级别颜色
LEVEL_COLORS = {
    'V': '#9aa0a6', 'D': '#6db3f2', 'I': '#cfd8dc',
    'W': '#f5c542', 'E': '#ff6b6b', 'F': '#ff3b30',
}
LEVEL_DEFAULT = '#cfd8dc'

# 各级别字符格式缓存：避免高频插入时每行新建 QTextCharFormat（卡顿主因之一）
_FMT_CACHE = {}


def _fmt_for_level(level: str) -> QTextCharFormat:
    fmt = _FMT_CACHE.get(level)
    if fmt is None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(LEVEL_COLORS.get(level, LEVEL_DEFAULT)))
        if level in ('E', 'F'):
            fmt.setFontWeight(QFont.Bold)
        _FMT_CACHE[level] = fmt
    return fmt


def _parse_line(raw: str):
    m = re.match(r'^(\d{2}-\d{2})\s+(\d{1,2}:\d{2}:\d{2}\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(\S+?):\s?(.*)$', raw)
    if m:
        return {
            'raw': raw, 'date': m.group(1), 'time': m.group(2),
            'pid': m.group(3), 'tid': m.group(4), 'level': m.group(5),
            'tag': m.group(6), 'msg': m.group(7),
        }
    return {'raw': raw, 'level': '', 'tag': '', 'pid': '', 'msg': ''}


def _match_entry(entry, filter_tag, filter_pids, filter_msg, filter_regex):
    """模块级过滤判定（无 self 依赖，可在后台线程安全调用）。

    与 LogViewerPage._match 逻辑完全一致，提取为独立函数后
    _rerender() 可将其丢入后台线程池执行，避免 10 万条正则匹配冻结 UI。
    """
    if filter_tag:
        if filter_regex:
            try:
                if not re.search(filter_tag, entry['tag']):
                    return False
            except re.error:
                pass
        elif filter_tag.lower() not in entry['tag'].lower():
            return False
    if filter_pids and entry['pid'] not in filter_pids:
        return False
    if filter_msg:
        if filter_regex:
            try:
                if not re.search(filter_msg, entry['msg']):
                    return False
            except re.error:
                pass
        elif filter_msg.lower() not in entry['msg'].lower():
            return False
    return True


class _WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class _CmdWorker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            r = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class LogViewerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr = AdbHelper()
        self._current_serial = None
        self._capturing = False
        self._paused = False
        self._total = 0
        self._entries = deque(maxlen=BUFFER_MAX)
        self._pending_view = []
        self._line_buf = ''
        self._write_buf = []      # 磁盘写缓冲：累积行，由 _flush_view 批量写入
        self._log_file = None
        self._log_path = ''
        self._mode = ''  # '' 未加载 / 'live' 实时抓取 / 'local' 本地文件
        self._filter_tag = ''
        self._filter_pids = set()
        self._filter_seq = 0
        self._pending_pkgs = []
        self._pkg_pid_map = {}   # 包名 -> 历史 PID 集合（ps 轮询累积，覆盖进程重启）
        self._filter_msg = ''
        self._filter_regex = False
        self._desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        self._save_dir = os.path.join(self._desktop, 'Super_ADB')

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_data)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(3)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._flush_view)

        # 过滤输入防抖：250ms 内不再变化才重渲染（参考 adb_log_tool）
        self._filter_timer = QTimer(self)
        self._filter_timer.setInterval(250)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_filter)

        # 抓取期间每 3 秒轮询一次 ps，累积"包名 -> 历史 PID"映射
        self._ps_timer = QTimer(self)
        self._ps_timer.setInterval(3000)
        self._ps_timer.timeout.connect(self._poll_processes)

        self._built = False
        self._build_ui()
        if self._mgr.check_adb():
            self._scan_devices()

    def inject_widgets(self, *, device_combo: QComboBox,
                       btn_refresh: QPushButton, btn_start: QPushButton,
                       btn_pause: QPushButton, btn_clear: QPushButton,
                       status_label: QLabel, tag_combo, proc_combo, msg_combo,
                       tag_star: QPushButton, proc_star: QPushButton,
                       msg_star: QPushButton,
                       regex_chk: QCheckBox, btn_reset: QPushButton,
                       text_edit: QPlainTextEdit, follow_chk: QCheckBox,
                       count_label: QLabel, btn_load_file: QPushButton = None,
                       mode_label: QLabel = None):
        """将 .ui 中预定义的控件注入，替代 _build_ui() 创建的控件。"""
        if self._built:
            return
        self._built = True

        # 替换所有控件引用
        self.device_combo = device_combo
        self.device_combo.currentIndexChanged.connect(self._on_device)
        self.btn_refresh = btn_refresh
        self.btn_refresh.clicked.connect(self._scan_devices)
        self.btn_start = btn_start
        self.btn_start.clicked.connect(self._toggle_capture)
        self.btn_pause = btn_pause
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_clear = btn_clear
        self.btn_clear.clicked.connect(self._clear_view)
        self.status_label = status_label
        # .ui 中的可收藏下拉框：绑定 key 与信号，收藏按钮原地接线
        self.tag_combo = self._setup_fav_combo(tag_combo, 'tag')
        self.proc_combo = self._setup_fav_combo(proc_combo, 'proc')
        self.msg_combo = self._setup_fav_combo(msg_combo, 'msg')
        self._wire_star(tag_star, self.tag_combo)
        self._wire_star(proc_star, self.proc_combo)
        self._wire_star(msg_star, self.msg_combo)
        self._load_favs()
        self.regex_chk = regex_chk
        self.regex_chk.stateChanged.connect(self._on_filter_changed)
        self.btn_reset = btn_reset
        btn_reset.clicked.connect(self._reset_filter)
        self.text_edit = text_edit
        self.follow_chk = follow_chk
        self.count_label = count_label
        self.btn_load_file = btn_load_file
        if self.btn_load_file is not None:
            self.btn_load_file.clicked.connect(self._load_local_file)
        self._init_mode_label(mode_label)
        self._beautify_view()

        # 清理旧控件
        layout = self.layout()
        if layout:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
        self.setLayout(QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        if self._mgr.check_adb():
            self._scan_devices()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 工具栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel('设备:'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        self.device_combo.currentIndexChanged.connect(self._on_device)
        bar.addWidget(self.device_combo)
        self.btn_refresh = QPushButton('刷新')
        self.btn_refresh.clicked.connect(self._scan_devices)
        bar.addWidget(self.btn_refresh)
        self.btn_start = QPushButton('开始抓取')
        self.btn_start.clicked.connect(self._toggle_capture)
        bar.addWidget(self.btn_start)
        self.btn_pause = QPushButton('暂停')
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        bar.addWidget(self.btn_pause)
        self.btn_clear = QPushButton('清除')
        self.btn_clear.clicked.connect(self._clear_view)
        bar.addWidget(self.btn_clear)
        self.btn_load_file = QPushButton('打开本地文件')
        self.btn_load_file.clicked.connect(self._load_local_file)
        bar.addWidget(self.btn_load_file)
        bar.addStretch(1)
        self.status_label = QLabel('就绪')
        bar.addWidget(self.status_label)
        layout.addLayout(bar)

        # 过滤栏
        fbar = QHBoxLayout()
        fbar.addWidget(QLabel('标签:'))
        self.tag_combo, tag_star = self._make_fav_combo('tag', '日志 TAG')
        fbar.addWidget(self.tag_combo)
        fbar.addWidget(tag_star)
        fbar.addWidget(QLabel('包名:'))
        self.proc_combo, proc_star = self._make_fav_combo('proc', '包名，如 com.xxx.app，空格分隔多个')
        fbar.addWidget(self.proc_combo)
        fbar.addWidget(proc_star)
        fbar.addWidget(QLabel('消息:'))
        self.msg_combo, msg_star = self._make_fav_combo('msg', '搜索关键字')
        fbar.addWidget(self.msg_combo)
        fbar.addWidget(msg_star)
        self.regex_chk = QCheckBox('正则')
        self.regex_chk.stateChanged.connect(self._on_filter_changed)
        fbar.addWidget(self.regex_chk)
        btn_reset = QPushButton('重置')
        btn_reset.clicked.connect(self._reset_filter)
        fbar.addWidget(btn_reset)
        layout.addLayout(fbar)

        # 日志视图
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._beautify_view()
        layout.addWidget(self.text_edit, 1)

        # 底部统计
        bot = QHBoxLayout()
        self.follow_chk = QCheckBox('跟随滚动')
        self.follow_chk.setChecked(True)
        bot.addWidget(self.follow_chk)
        bot.addStretch(1)
        self.count_label = QLabel('累计 0 行 | 匹配 0')
        bot.addWidget(self.count_label)
        layout.addLayout(bot)
        self._init_mode_label()

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    def _scan_devices(self):
        self.status_label.setText('扫描中…')
        w = _CmdWorker(self._mgr.get_devices)
        w.setAutoDelete(True)
        w.signals.result.connect(self._on_scan_result)
        w.signals.error.connect(lambda e: self.status_label.setText(f'扫描失败: {e}'))
        self._pool.start(w)

    def _on_scan_result(self, devices, select_serial=None):
        if not devices:
            self.status_label.setText('无设备')
            self.btn_start.setEnabled(False)
            return
        if select_serial is None:
            select_serial = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        online = [d for d in devices if d.get('state') == 'device']
        for d in online:
            self.device_combo.addItem(format_device_label(d), d.get('serial'))
        idx = self.device_combo.findData(select_serial) if select_serial else -1
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)
        if self.device_combo.count() == 0:
            self.status_label.setText('无设备')
            self.btn_start.setEnabled(False)
        else:
            self._current_serial = self.device_combo.currentData()
            self.btn_start.setEnabled(True)
            self.status_label.setText(f'已连接 {self.device_combo.count()} 台设备')

    # 供主窗口统一同步：连接/刷新后三处下拉框一起更新
    def sync_devices(self, devices, select_serial=None):
        self._on_scan_result(devices, select_serial)

    def _on_device(self):
        serial = self.device_combo.currentData()
        if serial and self._capturing:
            self._stop_capture()
        self._current_serial = serial
        self._pkg_pid_map.clear()

    # ------------------------------------------------------------------
    # 抓取控制
    # ------------------------------------------------------------------
    def _toggle_capture(self):
        if self._capturing:
            self._stop_capture()
        else:
            self._start_capture()

    def _start_capture(self):
        if not self._current_serial:
            self.status_label.setText('请先选择设备')
            return
        self._open_log_file()
        if not self._log_file:
            return
        self._entries.clear()
        self._pending_view.clear()
        self._line_buf = ''
        self._total = 0
        self.text_edit.clear()
        self._proc.start(
            self._mgr.adb_path,
            ['-s', self._current_serial, 'logcat', '-v', 'threadtime'],
        )
        if not self._proc.waitForStarted(3000):
            self.status_label.setText('logcat 启动失败')
            self._close_log_file()
            return
        self._capturing = True
        self._paused = False
        self._mode = 'live'
        self.btn_start.setText('停止抓取')
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText('暂停')
        if self.btn_load_file is not None:
            self.btn_load_file.setEnabled(False)
        self._flush_timer.start()
        self._ps_timer.start()
        self._update_mode_label()
        self.status_label.setText('抓取中…')

    def _stop_capture(self):
        """停止抓取：立刻更新 UI，然后异步终止进程，避免主线程被 waitForFinished 阻塞。"""
        if not self._capturing:
            return
        # 立刻停止定时器和后续数据流入；UI 状态立即反馈给用户
        self._capturing = False
        self._flush_timer.stop()
        self._ps_timer.stop()
        self.btn_start.setText('开始抓取')
        self.btn_start.setEnabled(False)   # 防止重复点击
        self.btn_pause.setEnabled(False)
        self.btn_pause.setText('暂停')
        if self.btn_load_file is not None:
            self.btn_load_file.setEnabled(True)
        self._update_mode_label()
        self.status_label.setText('正在停止…')

        # 先 flush 磁盘缓冲，保证已读到的日志不丢
        if self._write_buf and self._log_file:
            try:
                self._log_file.write('\n'.join(self._write_buf) + '\n')
                self._write_buf.clear()
            except Exception:
                pass

        # 异步终止 adb logcat 进程
        if self._proc.state() != QProcess.NotRunning:
            self._proc.terminate()
            # 500ms 后若仍在运行则强制 kill；全程不阻塞主线程
            QTimer.singleShot(500, self._ensure_process_killed)
        else:
            self._finalize_stop()

    def _ensure_process_killed(self):
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            QTimer.singleShot(300, self._finalize_stop)
        else:
            self._finalize_stop()

    def _finalize_stop(self, ec=None):
        """进程已结束或超时后的统一收尾（主线程）。"""
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
        self._flush_view()
        self._close_log_file()
        self.btn_start.setEnabled(True)
        if ec is not None:
            self.status_label.setText(f'logcat 已退出 (code={ec})')
        else:
            self.status_label.setText('已停止')

    def _toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText('继续' if self._paused else '暂停')
        self._update_mode_label()
        if not self._paused:
            self._rerender()

    def _clear_view(self):
        self._entries.clear()
        self._pending_view.clear()
        self._total = 0
        self.text_edit.clear()
        self._update_count()

    # ------------------------------------------------------------------
    # 日志流
    # ------------------------------------------------------------------
    def _on_data(self):
        data = bytes(self._proc.readAllStandardOutput()).decode('utf-8', 'replace')
        self._line_buf += data
        while '\n' in self._line_buf:
            line, self._line_buf = self._line_buf.split('\n', 1)
            line = line.rstrip('\r')
            self._ingest(line)

    def _ingest(self, raw):
        if not raw:
            return
        entry = _parse_line(raw)
        self._entries.append(entry)
        self._total += 1
        if self._log_file:
            self._write_buf.append(raw)    # 缓冲，由 _flush_view 批量写盘
        if not self._paused and self._match(entry):
            self._pending_view.append(entry)

    def _on_finished(self, ec, es):
        if not self._capturing:
            return
        self._finalize_stop(ec)

    def _on_error(self, err):
        if self._capturing:
            self.status_label.setText(f'logcat 出错: {err}')

    # ------------------------------------------------------------------
    # 视图渲染
    # ------------------------------------------------------------------
    def _beautify_view(self):
        """日志视图美化：等宽字体 + 右键菜单（复制/打开保存目录/清空）。"""
        font = QFont('Consolas', 9)
        font.setStyleHint(QFont.Monospace)
        self.text_edit.setFont(font)
        self.text_edit.setContextMenuPolicy(Qt.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        copy_act = menu.addAction('复制选中')
        copy_act.triggered.connect(self.text_edit.copy)
        menu.addSeparator()
        save_act = menu.addAction('打开保存目录')
        save_act.triggered.connect(self._open_folder)
        clear_act = menu.addAction('清空')
        clear_act.triggered.connect(self._clear_view)
        menu.exec(self.text_edit.viewport().mapToGlobal(pos))

    def _open_folder(self):
        path = self._log_path or os.path.join(self._save_dir, 'x.log')
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _fmt_for(self, level):
        return _fmt_for_level(level)

    def _insert_batch(self, cursor, entries):
        """把连续同级别的行合并为一次 insertText，减少文档布局刷新次数。"""
        buf_level = None
        buf = []
        for e in entries:
            if e['level'] != buf_level:
                if buf:
                    cursor.insertText('\n'.join(buf) + '\n', _fmt_for_level(buf_level))
                buf_level = e['level']
                buf = [e['raw']]
            else:
                buf.append(e['raw'])
        if buf:
            cursor.insertText('\n'.join(buf) + '\n', _fmt_for_level(buf_level))

    def _flush_view(self):
        # 批量写盘（即使暂停/无待渲染行也需 flush 磁盘缓冲）
        if self._write_buf and self._log_file:
            try:
                self._log_file.write('\n'.join(self._write_buf) + '\n')
            except Exception:
                pass
            self._write_buf.clear()
        if not self._pending_view:
            return

        # 限制单次插入行数，避免停止/恢复时一次性渲染数千行导致 UI 卡顿
        MAX_BATCH = 500
        if len(self._pending_view) > MAX_BATCH:
            batch = self._pending_view[:MAX_BATCH]
            self._pending_view = self._pending_view[MAX_BATCH:]
            # 剩余行分到下一帧继续渲染，让事件循环有机会处理用户输入
            QTimer.singleShot(0, self._flush_view)
        else:
            batch = self._pending_view
            self._pending_view = []

        cursor = QTextCursor(self.text_edit.document())
        cursor.movePosition(QTextCursor.End)
        self._insert_batch(cursor, batch)
        self._trim_view()
        if self.follow_chk.isChecked():
            self.text_edit.verticalScrollBar().setValue(self.text_edit.verticalScrollBar().maximum())
        self._update_count()

    def _trim_view(self):
        doc = self.text_edit.document()
        over = doc.blockCount() - VIEW_MAX_BLOCKS
        if over > 0:
            cur = QTextCursor(doc)
            cur.movePosition(QTextCursor.Start)
            cur.movePosition(QTextCursor.Down, QTextCursor.KeepAnchor, over)
            cur.removeSelectedText()

    def _rerender(self):
        """异步重渲染：主线程快速快照 entries+过滤参数，后台线程做全量正则匹配，
        信号回主线程只做 insertText，避免 10 万条遍历冻结 UI。"""
        seq = self._filter_seq
        # 主线程快照（仅复制指针，~1ms/10万条，远快于正则匹配）
        entries_snapshot = list(self._entries)
        # 快照过滤参数（后台线程读到的是不可变副本）
        f_tag = self._filter_tag
        f_pids = set(self._filter_pids)
        f_msg = self._filter_msg
        f_regex = self._filter_regex

        def _task():
            matched = [e for e in entries_snapshot
                       if _match_entry(e, f_tag, f_pids, f_msg, f_regex)]
            shown = matched[-RENDER_MAX:]
            return {'matched_count': len(matched), 'shown': shown, 'seq': seq}

        w = _CmdWorker(_task)
        w.signals.result.connect(self._on_rerender_done)
        w.signals.error.connect(
            lambda e: self.status_label.setText(f'过滤失败: {e}'))
        self._rerender_worker = w  # 持有引用防止 GC
        self._pool.start(w)

    def _on_rerender_done(self, result):
        """后台过滤完成回调（主线程）：执行文本插入 + 滚动 + 计数。"""
        if result['seq'] != self._filter_seq:
            return  # 过滤条件已变化，丢弃过期结果
        self.text_edit.clear()
        cur = QTextCursor(self.text_edit.document())
        cur.movePosition(QTextCursor.End)
        self._insert_batch(cur, result['shown'])
        if self.follow_chk.isChecked():
            sb = self.text_edit.verticalScrollBar()
            sb.setValue(sb.maximum())
        self._update_count(result['matched_count'], len(result['shown']))

    # ------------------------------------------------------------------
    # 过滤栏控件构造 / 收藏
    # ------------------------------------------------------------------
    def _setup_fav_combo(self, combo, key):
        """为 .ui 中的 FavComboBox 绑定 key、信号和尺寸策略。"""
        combo.set_key(key)
        combo.currentTextChanged.connect(self._on_filter_changed)
        combo.favoritesChanged.connect(self._on_favs_changed)
        # 改成可扩展，同时保留水平拉伸系数（setSizePolicy 默认值会清空 stretch）
        policy = combo.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Expanding)
        policy.setVerticalPolicy(QSizePolicy.Fixed)
        policy.setHorizontalStretch(1)
        combo.setSizePolicy(policy)
        combo.setMinimumWidth(120)
        return combo

    def _wire_star(self, star, combo):
        """为 .ui 中的收藏按钮设置黄色实心星样式并绑定收藏动作。"""
        star.setFixedSize(28, 28)
        star.setStyleSheet(
            'QPushButton { color: #f5c542; font-size: 14px; border: none; '
            'background: transparent; padding: 0px; }'
            'QPushButton:hover { color: #ffd75e; background: rgba(245,197,66,30); }'
            'QPushButton:pressed { color: #d9a520; background: rgba(245,197,66,60); }'
        )
        star.clicked.connect(lambda _=False, c=combo: c.add_favorite(c.currentText()))

    def _make_fav_combo(self, key, placeholder):
        """独立模式（_build_ui）用：创建可收藏下拉框 + ☆ 收藏按钮。"""
        combo = FavComboBox(key=key, placeholder=placeholder)
        self._setup_fav_combo(combo, key)
        star = QPushButton('★')
        star.setToolTip('把当前输入加入收藏')
        self._wire_star(star, combo)
        return combo, star

    def _layout_of(self, widget):
        """查找包含 widget 的直接布局（从父控件布局递归向下找）。"""
        parent = widget.parentWidget()
        top = parent.layout() if parent else None
        if top is None:
            return None

        def find(w, layout):
            if layout.indexOf(w) >= 0:
                return layout
            for i in range(layout.count()):
                sub = layout.itemAt(i).layout()
                if sub:
                    hit = find(w, sub)
                    if hit:
                        return hit
            return None

        return find(widget, top)

    def _init_mode_label(self, mode_label=None):
        """模式提示标签：优先使用 .ui 中的控件，独立模式才动态创建。"""
        if getattr(self, '_mode_label', None) is not None:
            return
        if mode_label is not None:
            self._mode_label = mode_label
            return
        self._mode_label = QLabel('未加载日志')
        lay = self._layout_of(self.count_label)
        if lay is not None:
            lay.insertWidget(1, self._mode_label)

    def _update_mode_label(self):
        if getattr(self, '_mode_label', None) is None:
            return
        if self._mode == 'live':
            if self._capturing:
                text = '🔴 实时日志（抓取中' + ('，已暂停' if self._paused else '') + '）'
            else:
                text = '🔴 实时日志（已停止）'
        elif self._mode == 'local':
            text = f'📄 本地文件: {os.path.basename(self._log_path)}'
        else:
            text = '未加载日志'
        self._mode_label.setText(text)

    def _load_favs(self):
        favs = load_json_config(CONFIG_NAME).get(FAV_KEY) or {}
        self.tag_combo.set_favorites(favs.get('tag'))
        self.proc_combo.set_favorites(favs.get('proc'))
        self.msg_combo.set_favorites(favs.get('msg'))

    def _on_favs_changed(self, key, items):
        cfg = load_json_config(CONFIG_NAME)
        favs = cfg.get(FAV_KEY) or {}
        favs[key] = items
        cfg[FAV_KEY] = favs
        save_json_config(CONFIG_NAME, cfg)

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------
    def _on_filter_changed(self, *_):
        # 防抖：停止输入 250ms 后才应用过滤并重渲染
        self._filter_timer.start()

    def _apply_filter(self):
        self._filter_seq += 1
        self._filter_tag = self.tag_combo.currentText().strip()
        self._filter_msg = self.msg_combo.currentText().strip()
        self._filter_regex = self.regex_chk.isChecked()
        tokens = [t for t in re.split(r'[,\s]+', self.proc_combo.currentText().strip()) if t]
        self._filter_pids = set(t for t in tokens if t.isdigit())
        self._pending_pkgs = [t for t in tokens if not t.isdigit()]
        if self._pending_pkgs:
            self._resolve_pkg_pids(self._pending_pkgs, self._filter_seq)
        else:
            self._rerender()

    def _resolve_pkg_pids(self, pkgs, seq):
        """把包名解析成 PID 集合（pidof 实时值 + ps 轮询累积的历史值）再过滤。"""
        serial = self._current_serial
        if not serial:
            self.status_label.setText('按包名过滤需先选择设备')
            self._rerender()
            return
        self.status_label.setText(f'解析包名 PID: {", ".join(pkgs)} …')
        hist = {p: set(v) for p, v in self._pkg_pid_map.items()}  # 快照，供后台线程读取

        def _task():
            found = {}
            for pkg in pkgs:
                pids = set(hist.get(pkg, set()))
                try:
                    out = self._mgr.run_shell(serial, f'pidof {pkg}', timeout=5)
                    pids.update(out.split())
                except Exception:
                    pass
                if pids:
                    found[pkg] = sorted(pids)
            return found

        w = _CmdWorker(_task)
        w.signals.result.connect(lambda found: self._on_pkg_pids(found, pkgs, seq))
        w.signals.error.connect(lambda e: self.status_label.setText(f'包名解析失败: {e}'))
        self._pkg_worker = w  # 持有引用防止被 GC
        self._pool.start(w)

    def _on_pkg_pids(self, found, pkgs, seq):
        if seq != self._filter_seq:
            return  # 过滤条件已变化，丢弃过期结果
        for pkg, pids in found.items():
            self._pkg_pid_map.setdefault(pkg, set()).update(pids)
            self._filter_pids.update(pids)
        miss = [p for p in pkgs if p not in found]
        if found and not miss:
            self.status_label.setText(f'包名 → PID: {", ".join(sorted(self._filter_pids))}')
        elif found:
            self.status_label.setText(f'部分包名未找到进程: {", ".join(miss)}')
        else:
            self.status_label.setText(f'未找到包名对应进程: {", ".join(pkgs)}')
        self._rerender()

    def _poll_processes(self):
        """抓取期间定期执行 ps，累积"包名 -> 历史 PID"，进程重启后旧日志也能命中。"""
        serial = self._current_serial
        if not serial:
            return

        def _task():
            out = self._mgr.run_shell(serial, 'ps -A -o PID,NAME', timeout=8)
            pairs = {}
            for line in out.splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    pairs.setdefault(parts[1], set()).add(parts[0])
            return pairs

        w = _CmdWorker(_task)
        w.signals.result.connect(self._on_ps_result)
        self._ps_worker = w
        self._pool.start(w)

    def _on_ps_result(self, pairs):
        grown = False
        for name, pids in pairs.items():
            old = self._pkg_pid_map.get(name)
            if old is None:
                self._pkg_pid_map[name] = set(pids)
                old = self._pkg_pid_map[name]
            elif not pids <= old:
                old.update(pids)
            else:
                continue
            if name in self._pending_pkgs:
                grown = True
        # 正在过滤的包名出现了新 PID（如进程重启）→ 立即刷新过滤
        if grown and self._pending_pkgs:
            self._apply_filter()

    def _reset_filter(self):
        self.tag_combo.clearEditText()
        self.proc_combo.clearEditText()
        self.msg_combo.clearEditText()
        self.regex_chk.setChecked(False)

    def _match(self, entry):
        """主线程逐条过滤（_ingest 用），委托给模块级 _match_entry。"""
        return _match_entry(entry, self._filter_tag, self._filter_pids,
                            self._filter_msg, self._filter_regex)

    # ------------------------------------------------------------------
    # 日志文件
    # ------------------------------------------------------------------
    def _open_log_file(self):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        try:
            os.makedirs(self._save_dir, exist_ok=True)
        except Exception as e:
            self._log_file = None
            self._log_path = ''
            self.status_label.setText(f'无法创建保存目录: {e}')
            return
        path = os.path.join(self._save_dir, f'adb_logcat_{ts}.log')
        try:
            self._log_file = open(path, 'a', encoding='utf-8', buffering=-1)
            self._log_path = path
            self.status_label.setText(f'保存: {path}')
        except Exception as e:
            self._log_file = None
            self._log_path = ''
            self.status_label.setText(f'无法创建日志文件: {e}')

    def _close_log_file(self):
        if self._write_buf and self._log_file:
            try:
                self._log_file.write('\n'.join(self._write_buf) + '\n')
            except Exception:
                pass
            self._write_buf.clear()
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _update_count(self, matched=None, shown=None):
        mc = matched if matched is not None else self.text_edit.document().blockCount()
        save = os.path.basename(self._log_path) if self._log_path else '（未保存）'
        suffix = f'（仅渲染最近 {shown} 条）' if shown is not None and shown >= RENDER_MAX else ''
        self.count_label.setText(
            f'累计 {self._total} 行 | 匹配 {mc} 行{suffix} | 文件: {save}'
            + (' | 已暂停' if self._paused else ''))

    def _load_local_file(self):
        """选择本地日志文件，清空输出框后加载，复用现有过滤工具显示。"""
        if self._capturing:
            self.status_label.setText('正在抓取中，请先停止')
            return
        path, _ = QFileDialog.getOpenFileName(
            self, '选择日志文件', self._desktop,
            '日志文件 (*.log *.txt);;所有文件 (*)')
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.read().splitlines()
        except Exception as e:
            self.status_label.setText(f'打开失败: {e}')
            return
        self._entries.clear()
        self._pending_view.clear()
        self._total = 0
        for line in lines:
            if line:
                self._ingest(line)
        self._mode = 'local'
        self._log_path = path
        self.text_edit.clear()
        self._rerender()
        self._update_mode_label()
        self.status_label.setText(f'已加载 {len(lines)} 行: {os.path.basename(path)}')
