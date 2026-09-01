# -*- coding: utf-8 -*-
"""
ADB Logcat 日志查看器 —— 内嵌子页面
=====================================
提供实时日志抓取、停止、过滤、着色显示功能。
使用 QProcess 流式读取 adb logcat 输出，不依赖外部项目。
"""

import os
import re
import time
import threading
import datetime
from collections import deque

# =====================================================================
# 调试埋点（性能/卡顿分析用）
# - 默认关闭（_DBG=False 时既不写文件也不打印，零开销）。
# - 排查卡顿时把 _DBG 临时置 True：日志同时写入程序目录下的
#   logcat_debug.log 并打印到 stdout，方便命令行启动或把文件发回分析。
# - 注意：只有 _DBG=True 时才会创建/打开 logcat_debug.log，
#   关闭状态下不会在磁盘上产生该文件。
# =====================================================================
_DBG = False
_DBG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logcat_debug.log')
_dbg_fh = None
if _DBG:
    try:
        _dbg_fh = open(_DBG_PATH, 'w', encoding='utf-8', buffering=1)
        _dbg_fh.write(f'# logcat debug log started at {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n')
    except Exception:
        _dbg_fh = None

_T0 = time.perf_counter()

def _dbg(tag, msg):
    """带时间戳 + 相对启动时间的调试日志（_DBG=False 时直接 return，无副作用）。"""
    if not _DBG:
        return
    try:
        now = datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]
        rel = time.perf_counter() - _T0
        line = f'[{now}] +{rel:8.3f}s [{tag}] {msg}\n'
        if _dbg_fh is not None:
            _dbg_fh.write(line)
            _dbg_fh.flush()
        try:
            print(line, end='', flush=True)
        except Exception:
            pass
    except Exception:
        pass

from PySide6.QtCore import (
    Qt, QProcess, QTimer, QThreadPool, Signal, QObject, QRunnable, QUrl, QEvent,
)
from PySide6.QtGui import (QColor, QFont,
                           QDesktopServices)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QLabel, QLineEdit, QCheckBox, QListWidget, QListWidgetItem,
    QAbstractSpinBox, QAbstractItemView,
    QScrollBar, QHeaderView, QListView, QMenu, QFileDialog, QSizePolicy,
    QApplication,
)

from tools.adb_tools import AdbHelper, 格式化设备标签, 加载json配置, 保存json配置
from tools.favorite_combobox import 收藏下拉框

# 缓冲区上限
BUFFER_MAX = 100_000
# 屏幕最大行数：QListWidget 只绘制可视行（uniform item sizes → 常量级 paint），
# 拖动窗口时不再同步阻塞主线程；超出上限用 takeItem(0) 从头部 trim，O(1) 出队。
VIEW_MAX_BLOCKS = 10_000
RENDER_MAX = 8_000
# 拖动期间每帧渲染的小批量上限：日志继续流动但不给主线程造成压力
DRAG_BATCH = 100

# 收藏持久化配置
CONFIG_NAME = 'config/super_adb_config.json'
FAV_KEY = 'log_favs'

# 级别颜色
LEVEL_COLORS = {
    'V': '#9aa0a6', 'D': '#6db3f2', 'I': '#cfd8dc',
    'W': '#f5c542', 'E': '#ff6b6b', 'F': '#ff3b30',
}
LEVEL_DEFAULT = '#cfd8dc'


def _parse_line(raw: str):
    m = re.match(r'^(\d{2}-\d{2})\s+(\d{1,2}:\d{2}:\d{2}\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEF])\s+(\S+?):\s?(.*)$', raw)
    if m:
        return {
            'raw': raw, 'date': m.group(1), 'time': m.group(2),
            'pid': m.group(3), 'tid': m.group(4), 'level': m.group(5),
            'tag': m.group(6), 'msg': m.group(7),
        }
    return {'raw': raw, 'level': '', 'tag': '', 'pid': '', 'msg': ''}


def _match_entry(entry, filter_tag, filter_pids, filter_msg, filter_regex=False):
    """模块级过滤判定（无 self 依赖，可在后台线程安全调用）。

    标签/消息过滤统一用大小写不敏感的子串匹配；勾选"正则"时，
    消息过滤改用 re.search(filter_msg, entry['msg'])，支持复杂 pattern。
    _rerender() 可将其丢入后台线程池执行，避免 10 万条遍历冻结 UI。
    """
    if filter_tag:
        # 标签过滤：精确匹配 tag（忽略大小写），但允许用户省略 [...] 后缀。
        # 例如输入 "screenBoot" 可匹配 "screenBoot[main]"，但不会误命中 "ScreenBootUi"。
        ft = filter_tag.lower()
        et = entry['tag'].lower()
        if ft != et and ft != et.split('[')[0]:
            return False
    if filter_pids and entry['pid'] not in filter_pids:
        return False
    if filter_msg:
        msg = entry['msg']
        if filter_regex:
            try:
                ok = re.search(filter_msg, msg) is not None
            except re.error:
                # 非法正则（如未闭合括号）退化为子串匹配，避免误隐藏全部日志
                ok = filter_msg.lower() in msg.lower()
        else:
            ok = filter_msg.lower() in msg.lower()
        if not ok:
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


class 日志查看器页(QWidget):
    # 抓取中 logcat 进程意外退出（多半是设备掉线/离线）时发出，供主窗口刷新设备列表
    device_disconnected = Signal()
    # 自研 ADB 模式：后台 shell 流线程收到数据时发出，bytes → 主线程处理
    _shell_data = Signal(bytes)

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
        # 原始行缓冲：_on_data 只负责"读+拆行+入缓冲"（零正则，极快），
        # 解析+过滤交给后台线程，避免万行/批的正则匹配冻结主线程。
        self._raw_lines = []
        self._raw_lock = threading.Lock()
        self._parsing = False     # 后台解析 worker 是否在跑（仅主线程读写，单线程无竞态）
        self._log_file = None
        self._log_path = ''
        self._mode = ''  # '' 未加载 / 'live' 实时抓取 / 'local' 本地文件
        self._filter_tag = ''
        self._filter_pids = set()
        self._filter_seq = 0
        self._pending_pkgs = []
        self._pkg_pid_map = {}   # 包名 -> 历史 PID 集合（ps 轮询累积，覆盖进程重启）
        self._filter_msg = ''
        self._filter_regex = False  # 是否启用正则匹配（消息过滤框）
        self._hl_keywords = []      # 高亮关键字（小写），命中行整行红底
        self._desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        self._save_dir = os.path.join(self._desktop, 'Super_ADB')

        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_data)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        # 自研 ADB 模式：后台线程 + shell流，替代 QProcess 调用官方 adb
        self._shell_stop_event = None
        self._shell_thread = None
        self._shell_data.connect(self._on_shell_data)

        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(3)

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(150)
        self._flush_timer.timeout.connect(self._flush_view)

        # 拖动/移动窗口时降频渲染：拖动期间 _flush_view 改为小批量 + 跟随滚动
        # （日志继续流动但不抢主线程）；窗口移动交给系统原生 move（startSystemMove），
        # 主线程不再逐帧 self.move()。磁盘写入不受任何影响（见 _flush_view）。
        self._dragging = False
        self._drag_resume_timer = QTimer(self)
        self._drag_resume_timer.setSingleShot(True)
        self._drag_resume_timer.setInterval(300)
        self._drag_resume_timer.timeout.connect(self._on_drag_resume)
        self._evf_installed = False  # 顶层窗口事件过滤器只装一次（_build_ui / inject_widgets 双路径）

        # E/F 级别加粗字体（日志列表项用），与 _beautify_view 的等宽字体同源
        self._bold_font = QFont('Consolas', 9)
        self._bold_font.setBold(True)
        self._bold_font.setStyleHint(QFont.Monospace)

        # 高亮关键字：鲜红背景 + 白色加粗文字，确保在深色主题下清晰可见
        self._hl_bg = QColor(233, 76, 61)
        self._hl_fg = QColor(255, 255, 255)

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
        # 不在构造期扫描设备：由主窗口 刷新设备() 统一触发，经 sync_devices() 下发。
        # 否则启动时会并发扫描三次（主窗口 + 本页 + 文件管理页），且本页 _mgr 未挂
        # log_callback，这次扫描完全静默、结果也会被 inject_widgets 替换掉。

    def inject_widgets(self, *, device_combo: QComboBox,
                       btn_refresh: QPushButton, btn_start: QPushButton,
                       btn_pause: QPushButton, btn_clear: QPushButton,
                       status_label: QLabel, tag_combo, proc_combo, msg_combo,
                       tag_star: QPushButton, proc_star: QPushButton,
                       msg_star: QPushButton,
                       btn_reset: QPushButton,
                       text_edit: QListWidget, follow_chk: QCheckBox,
                       regex_chk: QCheckBox,
                       count_label: QLabel, btn_load_file: QPushButton = None,
                       mode_label: QLabel = None, hl_edit: QLineEdit = None):
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
        self.regex_chk = regex_chk
        self.regex_chk.stateChanged.connect(self._on_regex_toggled)
        self._load_favs()
        self.btn_reset = btn_reset
        btn_reset.clicked.connect(self._reset_filter)
        self.text_edit = text_edit
        # 只画可见行（uniform 行高），paint 开销与文档总行数无关、常数级；
        # 行数上限由 _trim_list 在插入后从头部裁剪维持。
        self.text_edit.setUniformItemSizes(True)
        self.text_edit.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.text_edit.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._更新占位提示()
        self.follow_chk = follow_chk
        self.count_label = count_label
        self.btn_load_file = btn_load_file
        if self.btn_load_file is not None:
            self.btn_load_file.clicked.connect(self._load_local_file)
        self._init_mode_label(mode_label)
        self._beautify_view()

        # 高亮输入框（.ui 中定义）
        self.hl_edit = hl_edit
        if self.hl_edit is not None:
            self.hl_edit.textChanged.connect(self._on_hl_changed)

        # 清理旧控件。注入模式下本页仅作逻辑控制器，可见控件来自 .ui，
        # 自身不再建立布局——直接卸下 _build_ui 遗留布局，避免重复 setLayout
        # 触发 “QLayout: Attempting to add QLayout … which already has a layout” 告警。
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
                elif item.layout() is not None:
                    item.layout().deleteLater()
            # 注意：PySide6 6.11.1 的 QWidget 未暴露 takeLayout()，故用
            # QLayout.deleteLater() 安全卸下旧布局，避免二次 setLayout 告警且不崩。
            old_layout.deleteLater()

        # 不在此自动扫描设备：由主窗口 刷新设备() 统一触发，
        # 通过 sync_devices() 同步下拉框，避免与主窗口扫描竞态互相覆盖。

        # 安装拖动感知：捕获顶层窗口 Move 事件，拖动时暂停 UI 渲染，避免卡死
        # 注意：LogViewerPage 本体没有 parent，self.window() 会返回自身。
        # 用一个已注入的主窗口子控件（如 text_edit）来反查真正的顶层窗口。
        self._top_win = self.text_edit.window() if self.text_edit is not None else None
        if (self._top_win is not None and self._top_win is not self
                and not getattr(self, '_evf_installed', False)):
            self._top_win.installEventFilter(self)
            self._evf_installed = True
            _dbg('INIT', f'eventFilter installed on top-window {self._top_win!r}')

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
        # 可编辑+只读：允许选中文本复制（Ctrl+C），但不允许修改
        self.device_combo.setEditable(True)
        self.device_combo.lineEdit().setReadOnly(True)
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
        self.regex_chk.setToolTip('勾选后"消息"过滤框按正则表达式匹配（re.search）')
        self.regex_chk.stateChanged.connect(self._on_regex_toggled)
        fbar.addWidget(self.regex_chk)
        btn_reset = QPushButton('重置')
        btn_reset.clicked.connect(self._reset_filter)
        fbar.addWidget(btn_reset)
        # 高亮关键字输入框：命中任意关键字的行整行背景变红
        fbar.addWidget(QLabel('高亮:'))
        self.hl_edit = QLineEdit()
        self.hl_edit.setPlaceholderText('高亮关键字，逗号分隔，如 Exception,ANR')
        self.hl_edit.setToolTip('命中任意关键字的日志行整行背景变红')
        self.hl_edit.textChanged.connect(self._on_hl_changed)
        fbar.addWidget(self.hl_edit, 1)
        layout.addLayout(fbar)

        # 日志视图：QListWidget（uniform 行高，仅画可见行，paint 常数级）
        self.text_edit = QListWidget()
        self.text_edit.setUniformItemSizes(True)
        self.text_edit.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.text_edit.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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

        # 独立构建路径：standalone 模式下 LogViewerPage 可能没有 parent，
        # 退而求其次监听自己；inject_widgets 路径会再装一次真正的顶层窗口。
        self._top_win = self.window()
        if (self._top_win is not None and self._top_win is not self
                and not getattr(self, '_evf_installed', False)):
            self._top_win.installEventFilter(self)
            self._evf_installed = True
            _dbg('INIT', f'eventFilter(build_ui) installed on {self._top_win!r}')

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    def _scan_devices(self):
        self.status_label.setText('扫描中…')
        w = _CmdWorker(self._mgr.获取设备列表)
        w.setAutoDelete(True)
        w.signals.result.connect(self._on_scan_result)
        w.signals.error.connect(lambda e: self.status_label.setText(f'扫描失败: {e}'))
        self._pool.start(w)

    def _on_scan_result(self, devices, select_serial=None):
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.blockSignals(False)
        if not devices:
            self.status_label.setText('无设备')
            if self._capturing:
                self._stop_capture()
            self.btn_start.setText('开始抓取')
            self.btn_start.setEnabled(False)
            self._current_serial = None
            return
        if select_serial is None:
            select_serial = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        online = [d for d in devices if d.get('state') == 'device']
        for d in online:
            self.device_combo.addItem(格式化设备标签(d), d.get('serial'))
        idx = self.device_combo.findData(select_serial) if select_serial else -1
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)
        if self.device_combo.count() == 0:
            self.status_label.setText('无设备')
            if self._capturing:
                self._stop_capture()
            self.btn_start.setText('开始抓取')
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
        _dbg('TOGGLE', f'click _capturing={self._capturing}')
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
        with self._raw_lock:
            self._raw_lines = []
        self._total = 0
        self.text_edit.clear()

        # 自研 ADB 模式：用后台线程 + shell流，不启动官方 adb 进程
        if self._mgr._用自研adb:
            client = self._mgr._获取自研adb(self._current_serial)
            if not client:
                self.status_label.setText('自研adb连接设备失败，无法抓取日志')
                self._close_log_file()
                return
            self._shell_stop_event = threading.Event()
            stop_evt = self._shell_stop_event

            def _on_raw(data: bytes):
                if not stop_evt.is_set():
                    self._shell_data.emit(data)

            self._shell_thread = threading.Thread(
                target=client.shell流,
                args=('logcat -v threadtime', _on_raw, stop_evt),
                daemon=True,
            )
            self._shell_thread.start()
            _dbg('START', 'self-built adb shell stream started')
        else:
            self._proc.start(
                self._mgr.adb_path,
                ['-s', self._current_serial, 'logcat', '-v', 'threadtime'],
            )
            _dbg('START', 'proc.start() called, entering waitForStarted(3000)...')
            t0 = time.perf_counter()
            started = self._proc.waitForStarted(3000)
            _dbg('START', f'waitForStarted -> {started}, '
                          f'blocked_main_thread={time.perf_counter() - t0:.3f}s  '
                          f'<-- 若接近3.0则adb启动慢，期间UI点击被排队')
            if not started:
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
        _dbg('START', 'capturing=True, timers started')

    def _stop_capture(self):
        """停止抓取：立刻更新 UI，然后异步终止进程，避免主线程被 waitForFinished 阻塞。"""
        if not self._capturing:
            _dbg('STOP', 'IGNORED (already not capturing)')
            return
        _dbg('STOP', f'enter, total={self._total} pending={len(self._pending_view)} '
                     f'entries={len(self._entries)} state={self._proc.state()}')
        # 立刻停止定时器和后续数据流入；UI 状态立即反馈给用户
        self._capturing = False
        # 清掉行缓冲里残留的不完整行，避免被下一次抓取误当作首行
        self._line_buf = ''
        # 清空未解析的原始行：用户已选择停止，丢弃尚未来得及解析的数据（磁盘已保存）
        with self._raw_lock:
            self._raw_lines = []
        # 清空待渲染队列：屏幕立即停止刷新（已渲染的内容保留；磁盘日志完整）
        self._pending_view.clear()
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
        _dbg('STOP', 'UI updated (btn/text changed) <-- 此时用户应看到按钮变回"开始抓取"')

        # 先 flush 磁盘缓冲，保证已读到的日志不丢
        if self._write_buf and self._log_file:
            try:
                self._log_file.write('\n'.join(self._write_buf) + '\n')
                self._write_buf.clear()
            except Exception:
                pass

        # 自研 ADB 模式：设置停止事件，后台线程会自动退出
        if self._mgr._用自研adb:
            if self._shell_stop_event:
                self._shell_stop_event.set()
            _dbg('STOP', 'self-built adb: stop_event set, waiting for thread')
            # 轮询等待线程结束（不阻塞主线程）
            QTimer.singleShot(100, self._wait_shell_thread)
        else:
            # 主动 drain 一次 Qt 内部 pipe 缓冲：哪怕事件队列里已经派发了 _on_data，
            # 这一帧开始它们也会因为 self._capturing=False 早退（见下方 _on_data），
            # 但先把已经到手的 buffer 取走更稳。
            try:
                self._proc.readAllStandardOutput()
            except Exception:
                pass

            # 异步终止 adb logcat 进程
            if self._proc.state() != QProcess.NotRunning:
                self._proc.terminate()
                _dbg('STOP', 'terminate() called, wait 500ms for _ensure_process_killed')
                # 500ms 后若仍在运行则强制 kill；全程不阻塞主线程
                QTimer.singleShot(500, self._ensure_process_killed)
            else:
                _dbg('STOP', 'proc already NotRunning, calling _finalize_stop now')
                self._finalize_stop()

    def _ensure_process_killed(self):
        _dbg('KILL', f'enter state={self._proc.state()}')
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            _dbg('KILL', 'kill() called, wait 300ms for _finalize_stop')
            QTimer.singleShot(300, self._finalize_stop)
        else:
            _dbg('KILL', 'already NotRunning')
            self._finalize_stop()

    def _wait_shell_thread(self):
        """轮询等待自研 ADB shell 流后台线程结束。"""
        if self._shell_thread and self._shell_thread.is_alive():
            _dbg('KILL', 'shell thread still alive, retry in 100ms')
            QTimer.singleShot(100, self._wait_shell_thread)
            return
        _dbg('KILL', 'shell thread finished')
        self._shell_thread = None
        self._shell_stop_event = None
        self._finalize_stop()

    def _finalize_stop(self, ec=None):
        """进程已结束或超时后的统一收尾（主线程）。"""
        _dbg('FINAL', f'enter state={self._proc.state()} pending={len(self._pending_view)}')
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
        self._flush_view()
        self._close_log_file()
        self.btn_start.setEnabled(True)
        if ec is not None:
            self.status_label.setText(f'logcat 已退出 (code={ec})')
        else:
            self.status_label.setText('已停止')
        _dbg('FINAL', 'done, btn enabled')

    def _toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText('继续' if self._paused else '暂停')
        self._update_mode_label()
        if not self._paused:
            self._rerender()

    def _clear_view(self):
        self._entries.clear()
        self._pending_view.clear()
        with self._raw_lock:
            self._raw_lines = []
        self._total = 0
        self.text_edit.clear()
        self._update_count()
        self._更新占位提示()

    def _更新占位提示(self):
        """列表为空时不显示占位提示（已移除）。"""
        pass

    # ------------------------------------------------------------------
    # 日志流
    # ------------------------------------------------------------------
    def _on_data(self):
        # 设计：本方法只在主线程做"读 buffer + 拆行 + 入原始行缓冲 + 写磁盘缓冲"，
        # 完全不做正则解析/匹配（避免万行/批的正则冻结 UI，见下方埋点日志）。
        # 解析+过滤交给后台线程（_start_parse_worker），结果回主线程渲染。
        if not self._capturing:
            self._proc.readAllStandardOutput()
            _dbg('DATA', 'SKIP (capturing=False) drain buffer')
            return
        data = bytes(self._proc.readAllStandardOutput()).decode('utf-8', 'replace')
        cnt = 0
        self._line_buf += data
        with self._raw_lock:
            while '\n' in self._line_buf:
                line, self._line_buf = self._line_buf.split('\n', 1)
                line = line.rstrip('\r')
                if line:
                    self._raw_lines.append(line)
                    if self._log_file:
                        self._write_buf.append(line)   # 磁盘缓冲（原始行）
                    cnt += 1
        _dbg('DATA', f'recv={cnt} raw={len(self._raw_lines)}')
        # 触发/延续后台解析（若已有 worker 在跑则本次仅入缓冲，由 worker 收尾时续跑）
        self._maybe_start_parse()

    def _on_shell_data(self, data: bytes):
        """自研 ADB 模式：后台 shell 流线程通过信号发回的数据，主线程处理。"""
        if not self._capturing:
            _dbg('DATA', 'SKIP shell (capturing=False)')
            return
        text = data.decode('utf-8', 'replace')
        cnt = 0
        self._line_buf += text
        with self._raw_lock:
            while '\n' in self._line_buf:
                line, self._line_buf = self._line_buf.split('\n', 1)
                line = line.rstrip('\r')
                if line:
                    self._raw_lines.append(line)
                    if self._log_file:
                        self._write_buf.append(line)
                    cnt += 1
        _dbg('DATA', f'shell recv={cnt} raw={len(self._raw_lines)}')
        self._maybe_start_parse()

    def _maybe_start_parse(self):
        if self._parsing:
            return
        self._start_parse_worker()

    def _start_parse_worker(self):
        """从 _raw_lines 原子取出一批，后台线程批量解析+过滤，结果回主线程渲染。"""
        with self._raw_lock:
            if not self._raw_lines:
                return
            batch = self._raw_lines
            self._raw_lines = []
        self._parsing = True
        # 快照过滤参数（后台线程读到不可变副本）
        f_tag = self._filter_tag
        f_pids = set(self._filter_pids)
        f_msg = self._filter_msg

        def _task():
            entries = []
            matched = []
            for raw in batch:
                e = _parse_line(raw)
                entries.append(e)
                if _match_entry(e, f_tag, f_pids, f_msg, self._filter_regex):
                    matched.append(e)
            return entries, matched

        w = _CmdWorker(_task)
        w.signals.result.connect(self._on_parsed)
        w.signals.error.connect(lambda e: _dbg('PARSE', f'error: {e}'))
        self._pool.start(w)

    def _on_parsed(self, result):
        """后台解析完成回调（主线程）：并入总缓存 + 待渲染列表。

        注意：这里【不】直接调 _flush_view()。渲染统一交给 _flush_timer
        （100ms 一次），避免 worker 每 ~16ms 完成一次就触发一次渲染链，
        否则主线程会被"渲染 500 行 + singleShot(0) 续渲染"几乎 100% 占满 → UI 卡死。
        """
        entries, matched = result
        self._entries.extend(entries)
        self._total += len(entries)
        if self._capturing and not self._paused:
            self._pending_view.extend(matched)
        self._parsing = False
        # 若还有未解析的原始行（抓取期间持续到达），继续消费，天然限速
        with self._raw_lock:
            more = bool(self._raw_lines)
        if more:
            self._start_parse_worker()

    def _ingest(self, raw):
        # 仅用于本地文件加载（同步，数据量可控）；实时抓取走 _on_data + 后台解析。
        if not raw:
            return
        entry = _parse_line(raw)
        self._entries.append(entry)
        self._total += 1
        if not self._paused and self._match(entry):
            self._pending_view.append(entry)

    def _on_finished(self, ec, es):
        if not self._capturing:
            return
        # 抓取中进程意外退出（设备掉线/离线多为此情形）：
        # 先复位抓取状态（避免 UI 卡在"抓取中"），再通知主窗口刷新设备列表
        self._stop_capture()
        self._finalize_stop(ec)
        self.device_disconnected.emit()

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
        copy_act = menu.addAction('复制选中行')
        copy_act.triggered.connect(self._copy_selected)
        menu.addSeparator()
        save_act = menu.addAction('打开保存目录')
        save_act.triggered.connect(self._open_folder)
        clear_act = menu.addAction('清空')
        clear_act.triggered.connect(self._clear_view)
        menu.exec(self.text_edit.viewport().mapToGlobal(pos))

    def _copy_selected(self):
        """复制选中的整行日志（QListWidget 为整行选择，不支持自由框选）。"""
        items = self.text_edit.selectedItems()
        if items:
            QApplication.clipboard().setText('\n'.join(it.text() for it in items))

    def _open_folder(self):
        path = self._log_path or os.path.join(self._save_dir, 'x.log')
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _insert_batch(self, entries):
        """批量追加日志行到 QListWidget（仅画可见行，paint 常数级）。"""
        te = self.text_edit
        te.setUpdatesEnabled(False)
        try:
            hl = self._hl_keywords
            for e in entries:
                item = QListWidgetItem(e['raw'])
                item.setForeground(QColor(LEVEL_COLORS.get(e['level'], LEVEL_DEFAULT)))
                if e['level'] in ('E', 'F'):
                    item.setFont(self._bold_font)
                if hl and any(k in e['raw'].lower() for k in hl):
                    item.setBackground(self._hl_bg)
                    item.setForeground(self._hl_fg)
                    item.setFont(self._bold_font)
                te.addItem(item)
        finally:
            te.setUpdatesEnabled(True)
        self._trim_list()

    def _trim_list(self):
        """QListWidget 无内建行数上限：超出时从头部整批删除（维持滚动窗口）。"""
        n = self.text_edit.count()
        if n > VIEW_MAX_BLOCKS:
            for _ in range(n - VIEW_MAX_BLOCKS):
                self.text_edit.takeItem(0)

    def _flush_view(self):
        t0 = time.perf_counter()
        # 批量写盘（即使暂停/拖动中/无待渲染行也需 flush 磁盘缓冲）
        if self._write_buf and self._log_file:
            try:
                self._log_file.write('\n'.join(self._write_buf) + '\n')
            except Exception:
                pass
            self._write_buf.clear()

        if not self._pending_view:
            return

        # 拖动窗口期间：降频渲染（小批量 + 跟随滚动），日志继续流动但不抢主线程。
        # 窗口移动本身已交给系统原生 move（startSystemMove），不再逐帧 self.move()。
        if self._dragging:
            if len(self._pending_view) > VIEW_MAX_BLOCKS:
                self._pending_view = self._pending_view[-VIEW_MAX_BLOCKS:]
            batch = self._pending_view[:DRAG_BATCH] if len(self._pending_view) > DRAG_BATCH else self._pending_view
            self._pending_view = self._pending_view[len(batch):]
            if batch:
                self._insert_batch(batch)
                if self.follow_chk.isChecked():
                    self.text_edit.scrollToBottom()
            self._update_count()
            _dbg('FLUSH', f'DRAG batch={len(batch)} pending_left={len(self._pending_view)} '
                          f'cost={time.perf_counter() - t0:.3f}s')
            return

        # 限制单次插入行数：每批 200 行让单次 addItem 更轻，事件循环能及时响应
        # 窗口拖动/点击；QListWidget 仅画可见行，paint 开销与总行数无关。
        MAX_BATCH = 200
        if len(self._pending_view) > MAX_BATCH:
            batch = self._pending_view[:MAX_BATCH]
            self._pending_view = self._pending_view[MAX_BATCH:]
            # 剩余行分到下一帧继续渲染，让事件循环有机会处理用户输入
            QTimer.singleShot(0, self._flush_view)
        else:
            batch = self._pending_view
            self._pending_view = []

        self._insert_batch(batch)
        if self.follow_chk.isChecked():
            self.text_edit.scrollToBottom()
        self._update_count()
        _dbg('FLUSH', f'batch={len(batch)} pending_left={len(self._pending_view)} '
                      f'cost={time.perf_counter() - t0:.3f}s')

    # ------------------------------------------------------------------
    # 拖动感知：顶层窗口 Move 期间降频渲染（DRAG_BATCH 小批量 + 跟随滚动），
    # 日志继续流动但每帧只插入少量行，主线程始终有空处理拖动/点击。
    # 窗口自身的位移交给系统原生 move（startSystemMove），主线程不参与逐帧 self.move()。
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        # 防御：PySide6 绑定层会把非 QObject（如布局项 QWidgetItem）误传为
        # watched 参数，直接放行避免 super() 抛 TypeError（PYSIDE-3143 变体）
        if not isinstance(obj, QObject):
            return False
        # 仅响应缓存的顶层窗口的 Move 事件，避免对子控件做无用判断
        if obj is getattr(self, '_top_win', None) and event.type() == QEvent.Move:
            # 拖动中：标记降频渲染，并续命 300ms 恢复计时器
            self._dragging = True
            self._drag_resume_timer.start()
        return super().eventFilter(obj, event)

    def _on_drag_resume(self):
        # 拖动停止 300ms 后恢复 UI 渲染（下一帧 _flush_view 会补上积压内容）
        self._dragging = False
        _dbg('DRAG', 'resume UI render')

    def _rerender(self):
        """异步重渲染：主线程快速快照 entries+过滤参数，后台线程做全量匹配，
        信号回主线程只做 addItem（QListWidget 仅画可见行），避免 10 万条遍历冻结 UI。"""
        seq = self._filter_seq
        # 主线程快照（仅复制指针，~1ms/10万条，远快于匹配遍历）
        entries_snapshot = list(self._entries)
        # 快照过滤参数（后台线程读到的是不可变副本）
        f_tag = self._filter_tag
        f_pids = set(self._filter_pids)
        f_msg = self._filter_msg

        def _task():
            matched = [e for e in entries_snapshot
                       if _match_entry(e, f_tag, f_pids, f_msg, self._filter_regex)]
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
        self._insert_batch(result['shown'])
        if self.follow_chk.isChecked():
            self.text_edit.scrollToBottom()
        self._update_count(result['matched_count'], len(result['shown']))
        self._更新占位提示()

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
        combo = 收藏下拉框(key=key, placeholder=placeholder)
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
        favs = 加载json配置(CONFIG_NAME).get(FAV_KEY) or {}
        self.tag_combo.set_favorites(favs.get('tag'))
        self.proc_combo.set_favorites(favs.get('proc'))
        self.msg_combo.set_favorites(favs.get('msg'))

    def _on_favs_changed(self, key, items):
        cfg = 加载json配置(CONFIG_NAME)
        favs = cfg.get(FAV_KEY) or {}
        favs[key] = items
        cfg[FAV_KEY] = favs
        保存json配置(CONFIG_NAME, cfg)

    # ------------------------------------------------------------------
    # 过滤
    # ------------------------------------------------------------------
    def _on_regex_toggled(self, *_):
        """「正则」勾选框切换：更新消息框占位提示，使其明确作为正则输入，并立即重过滤。"""
        on = self.regex_chk.isChecked()
        try:
            self.msg_combo.setPlaceholderText(
                '正则 pattern，如 Error|Exception' if on else '搜索关键字')
        except Exception:
            pass
        self._on_filter_changed()

    def _on_filter_changed(self, *_):
        # 防抖：停止输入 250ms 后才应用过滤并重渲染
        self._filter_timer.start()

    def _on_hl_changed(self, text):
        """高亮关键字变化：更新关键字列表，并立即重渲染已有日志以套用/取消红底。"""
        self._hl_keywords = [k.strip().lower() for k in text.split(',') if k.strip()]
        self._rerender()

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
                # 1) pidof 最快，但部分 ROM/模拟器无此命令
                try:
                    out = self._mgr.执行shell(serial, f'pidof {pkg}', timeout=5)
                    pids.update(out.split())
                except Exception:
                    pass
                # 2) pidof 失败/无输出时，用 ps -A -o PID,NAME 兜底
                if not pids:
                    try:
                        out = self._mgr.执行shell(serial, 'ps -A -o PID,NAME', timeout=8)
                        for line in out.splitlines():
                            parts = line.split(None, 1)
                            if len(parts) == 2 and parts[0].isdigit():
                                name = parts[1].strip()
                                # 精确匹配进程名，或包名的子进程/服务（包名:xxx）
                                if name == pkg or name.startswith(pkg + ':'):
                                    pids.add(parts[0])
                    except Exception:
                        pass
                # 3) 再兜底：ps -A 全量行匹配
                if not pids:
                    try:
                        out = self._mgr.执行shell(serial, 'ps -A', timeout=8)
                        for line in out.splitlines():
                            if pkg not in line:
                                continue
                            parts = line.split()
                            for p in parts:
                                if p.isdigit():
                                    pids.add(p)
                                    break
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
        # 如果用户只输入了包名且全部未找到对应进程，应显示“无匹配”
        # 而不是因 _filter_pids 为空而不过滤（导致显示全部日志）。
        if not self._filter_pids and miss:
            self._filter_pids = {'__NOMATCH__'}
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
            pairs = {}
            # 优先使用 -o PID,NAME 格式（字段明确）
            try:
                out = self._mgr.执行shell(serial, 'ps -A -o PID,NAME', timeout=8)
                for line in out.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        pairs.setdefault(parts[1], set()).add(parts[0])
                if pairs:
                    return pairs
            except Exception:
                pass
            # 兜底：老设备/模拟器不支持 -o，直接 ps -A 全量解析
            try:
                out = self._mgr.执行shell(serial, 'ps -A', timeout=8)
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 2 and parts[0].isdigit():
                        # NAME 通常是最后一列，取末尾字段作为进程名
                        name = parts[-1]
                        pairs.setdefault(name, set()).add(parts[0])
            except Exception:
                pass
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
        mc = matched if matched is not None else self.text_edit.count()
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
