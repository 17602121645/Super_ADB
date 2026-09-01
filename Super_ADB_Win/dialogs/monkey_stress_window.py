# -*- coding: utf-8 -*-
"""
Monkey 压力测试 —— 独立配置 + 运行窗口
========================================
点击 btnRunningApps_2 (Monkey) 弹出。

功能：
  · 可视化配置 monkey 全部常用参数 (包名/事件数/间隔/种子/详细度/
    事件比例/忽略选项/类别)
  · 流式输出 monkey 日志，关键事件高亮
    (CRASH 红 / ANR 橙 / Events injected 绿 / :Monkey: 默认)
  · 实时事件计数、耗时计时
  · 运行/停止、关窗即停

执行方式：
  subprocess.Popen('adb -s <serial> shell monkey <args>')
  + 后台线程逐行读 stdout → Qt Signal 回主线程
"""

from tools.json_io import load_json, save_json
import re
import os
import subprocess
import threading
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QFont, QTextCursor, QIcon, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QCheckBox, QPushButton,
    QGroupBox, QTextEdit, QSizePolicy, QDialog, QProgressBar,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

from tools.adb_tools import AdbHelper, CREATE_NO_WINDOW
from ui.ui_styles import STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import highlight_card_style, add_green_glow, _create_popup_card

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from ui import png_rc  # noqa: F401


# ------------------------------------------------------------------
# Monkey 命令拼接
# ------------------------------------------------------------------
def build_monkey_args(params: dict) -> list:
    """把参数字典拼成 monkey 命令参数列表 (不含 'adb -s serial shell' 前缀)。

    必填: pkg, count
    可选: throttle, seed, verbosity, category,
          pct_touch/pct_motion/pct_trackball/pct_nav/pct_majornav/
          pct_appswitch/pct_anyevent,
          ignore_crashes/ignore_timeouts/ignore_security/
          kill_process/monitor_native/bugreport
    """
    pkg = (params.get('pkg') or '').strip()
    if not pkg:
        raise ValueError('请输入包名')
    count = int(params.get('count') or 0)
    if count <= 0:
        raise ValueError('事件数必须 > 0')

    parts = ['monkey', '-p', pkg]

    throttle = int(params.get('throttle') or 0)
    if throttle > 0:
        parts += ['--throttle', str(throttle)]

    seed = (params.get('seed') or '').strip()
    if seed:
        parts += ['-s', seed]

    # 详细度: 1→-v, 2→-vv, 3→-vvv
    verbosity = int(params.get('verbosity') or 1)
    parts.append('-' + 'v' * max(1, min(3, verbosity)))

    # 事件比例 (只在 >=0 时附加; -1 表示不设置, 走 monkey 默认)
    pct_map = [
        ('--pct-touch',      'pct_touch'),
        ('--pct-motion',     'pct_motion'),
        ('--pct-trackball',  'pct_trackball'),
        ('--pct-nav',        'pct_nav'),
        ('--pct-majornav',   'pct_majornav'),
        ('--pct-appswitch',  'pct_appswitch'),
        ('--pct-anyevent',   'pct_anyevent'),
    ]
    for opt, key in pct_map:
        val = params.get(key)
        if val is not None and int(val) >= 0:
            parts += [opt, str(int(val))]

    # 忽略 / 调试选项
    if params.get('ignore_crashes'):
        parts.append('--ignore-crashes')
    if params.get('ignore_timeouts'):
        parts.append('--ignore-timeouts')
    if params.get('ignore_security'):
        parts.append('--ignore-security-exceptions')
    if params.get('kill_process'):
        parts.append('--kill-process-after-error')
    if params.get('monitor_native'):
        parts.append('--monitor-native-crashes')
    if params.get('bugreport'):
        parts.append('--bugreport')

    # 类别
    cat = (params.get('category') or 'LAUNCHER').strip()
    parts += ['-c', f'android.intent.category.{cat}']

    parts.append(str(count))
    return parts


# ------------------------------------------------------------------
# 事件回放：把 monkey 输出翻译成可重放的 adb shell input 命令
# ------------------------------------------------------------------
# Android 标准 KEYCODE 数字值（只收录 monkey 常见输出的按键，其余走原名称兜底）
KEYCODE_MAP = {
    'KEYCODE_HOME': 3, 'KEYCODE_BACK': 4, 'KEYCODE_MENU': 82,
    'KEYCODE_DPAD_UP': 19, 'KEYCODE_DPAD_DOWN': 20,
    'KEYCODE_DPAD_LEFT': 21, 'KEYCODE_DPAD_RIGHT': 22,
    'KEYCODE_DPAD_CENTER': 23, 'KEYCODE_ENTER': 66,
    'KEYCODE_DEL': 67, 'KEYCODE_VOLUME_UP': 24,
    'KEYCODE_VOLUME_DOWN': 25, 'KEYCODE_POWER': 26,
    'KEYCODE_CAMERA': 27, 'KEYCODE_SEARCH': 84,
    'KEYCODE_MEDIA_PLAY_PAUSE': 85, 'KEYCODE_APP_SWITCH': 187,
    'KEYCODE_NOTIFICATION': 83, 'KEYCODE_CALL': 5,
    'KEYCODE_ENDCALL': 6, 'KEYCODE_0': 7, 'KEYCODE_1': 8,
    'KEYCODE_2': 9, 'KEYCODE_3': 10, 'KEYCODE_4': 11,
    'KEYCODE_5': 12, 'KEYCODE_6': 13, 'KEYCODE_7': 14,
    'KEYCODE_8': 15, 'KEYCODE_9': 16,
}

# 饼图分类标签
EVT_TOUCH = '触摸'
EVT_TRACKBALL = '轨迹球'
EVT_MOTION = '手势'
EVT_NAV = '导航'
EVT_KEY = '按键'
EVT_SYS = '系统'


def _to_input_cmd(line: str):
    """把一行 monkey :Sending 输出翻译成 adb shell input 命令。

    仅支持可映射为 input 命令的事件；轨迹球/翻转/旋转等无对应 input 命令，返回 None。
    """
    # 触摸：用 ACTION_UP 的坐标代表一次 tap（与 ACTION_DOWN 距离很近视为点击，否则视为 swipe）
    if ':Sending Touch (ACTION_UP):' in line:
        m = re.search(r'\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)', line)
        if m:
            return f'input tap {int(float(m.group(1)))} {int(float(m.group(2)))}'
    elif ':Sending Key' in line:
        m = re.search(r'(KEYCODE_\w+)', line)
        if m:
            code = m.group(1)
            num = KEYCODE_MAP.get(code, code)
            return f'input keyevent {num}'
    return None


# ------------------------------------------------------------------
# 实时事件分类饼图（QPainter 自绘，无额外依赖）
# ------------------------------------------------------------------
class EventPieChart(QWidget):
    """把 monkey 事件分类计数画成饼图 + 图例。"""

    COLORS = ['#1de9b6', '#ffab40', '#ff6b6b', '#7ee787',
              '#4aa8ff', '#d2a8ff', '#ff7b72', '#39d0d8']

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict[str, int] = {}
        self.setMinimumHeight(150)
        self.setMaximumHeight(180)

    def set_data(self, data: dict):
        self._data = dict(data)
        self.update()

    def paintEvent(self, event):
        if not self._data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(self._data.values())
        if total <= 0:
            return

        rect = self.rect().adjusted(10, 10, -200, -10)
        start = 0
        for i, (k, v) in enumerate(self._data.items()):
            angle = v / total * 360 * 16
            p.setBrush(QColor(self.COLORS[i % len(self.COLORS)]))
            p.setPen(Qt.NoPen)
            p.drawPie(rect, int(start), int(angle))
            start += angle

        p.setPen(QColor('#e0e0e0'))
        p.setFont(QFont(FONT_FAMILY, 9))
        x = rect.right() + 20
        y = rect.top() + 18
        for i, (k, v) in enumerate(self._data.items()):
            p.setBrush(QColor(self.COLORS[i % len(self.COLORS)]))
            p.drawRoundedRect(x, y, 12, 12, 3, 3)
            pct = v / total * 100
            p.drawText(x + 18, y + 11, f'{k}: {v} ({pct:.1f}%)')
            y += 20
        p.end()


# ------------------------------------------------------------------
# 事件回放对话框（单步重放 adb shell input 序列）
# ------------------------------------------------------------------
class ReplayDialog(QDialog):
    """把记录到的 input 命令序列单步重放到设备上。"""

    _progress = Signal(int, int, str)  # done, total, current_cmd

    def __init__(self, serial, events, parent=None):
        super().__init__(parent)
        self._serial = serial
        self._events = list(events)
        self._adb = AdbHelper()
        self._running = False
        self._delay = 0.3
        self.setWindowTitle('Monkey 事件回放')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(460, 360)
        self.setStyleSheet(get_stylesheet(get_current_theme_id(self)))
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._info_label = QLabel(
            f'共 {len(self._events)} 条可回放事件（触摸点击 / 按键）。\n'
            f'轨迹球、翻转、旋转等无对应 input 命令，已自动跳过（仍计入饼图）。')
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet(f"color: {THEMES[get_current_theme_id(self)]['text_disabled']};")
        root.addWidget(self._info_label)

        # 事件列表
        self.list_w = QListWidget()
        self.list_w.setAlternatingRowColors(True)
        for i, cmd in enumerate(self._events):
            QListWidgetItem(f'{i+1:>4}.  adb shell {cmd}', self.list_w)
        self.list_w.setSelectionMode(QAbstractItemView.NoSelection)
        root.addWidget(self.list_w, 1)

        # 速度控制
        speed_lay = QHBoxLayout()
        speed_lay.addWidget(QLabel('每条间隔:'))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 3000)
        self.delay_spin.setValue(300)
        self.delay_spin.setSuffix(' ms')
        self.delay_spin.valueChanged.connect(lambda v: setattr(self, '_delay', v / 1000.0))
        speed_lay.addWidget(self.delay_spin)
        speed_lay.addStretch(1)
        root.addLayout(speed_lay)

        # 进度条
        self.progress = QProgressBar()
        self.progress.setRange(0, max(1, len(self._events)))
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.status = QLabel('就绪')
        self.status.setStyleSheet('color:#888;')
        root.addWidget(self.status)

        # 按钮
        btn_lay = QHBoxLayout()
        btn_lay.addStretch(1)
        self.btn_start = QPushButton('▶ 开始回放')
        self.btn_start.clicked.connect(self._start)
        self.btn_stop = QPushButton('■ 停止')
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_replay)
        btn_lay.addWidget(self.btn_start)
        btn_lay.addWidget(self.btn_stop)
        root.addLayout(btn_lay)

        self._progress.connect(self._on_progress)

    def _start(self):
        if self._running or not self._events:
            return
        self._running = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText('回放中…')
        self.status.setStyleSheet('color:#1de9b6;')
        threading.Thread(target=self._run, daemon=True).start()

    def _stop_replay(self):
        self._running = False

    def _run(self):
        total = len(self._events)
        for i, cmd in enumerate(self._events):
            if not self._running:
                break
            try:
                subprocess.run(
                    [self._adb.adb_path, '-s', self._serial, 'shell'] + cmd.split(),
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    creationflags=CREATE_NO_WINDOW, timeout=10)
            except Exception:
                pass
            # 高亮当前行
            self._cur = i
            self._progress.emit(i + 1, total, cmd)
            if self._delay > 0:
                time.sleep(self._delay)
        self._running = False
        self._progress.emit(total, total, '完成' if self._cur == total - 1 else '已停止')

    def _on_progress(self, done, total, cmd):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)
        self.status.setText(f'{done}/{total}  {cmd}')
        # 滚动到当前行
        if getattr(self, '_cur', -1) >= 0:
            self.list_w.scrollToItem(self.list_w.item(self._cur))
            self.list_w.setCurrentRow(self._cur)
        if done >= total:
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.status.setStyleSheet('color:#98c379;' if '完成' in cmd else 'color:#ffab40;')


# ------------------------------------------------------------------
# Monkey 配置 + 运行窗口
# ------------------------------------------------------------------
class Monkey压测窗口(QWidget):
    """Monkey 压测独立窗口。

    用法：
        win = Monkey压测窗口(serial, default_pkg='', parent=main)
        win.show()
    """

    _line_arrived = Signal(str)
    _version_ready = Signal(str, str)  # text, stylesheet
    _pause_state_ready = Signal(bool, str)  # is_resume, message
    _tombstone_done = Signal(bool, str)     # ok, message
    # 后台监视/读输出线程结束时回主线程收尾。
    # 后台线程不能调 QTimer.singleShot（无事件循环，回调永不触发），
    # 必须用信号（跨线程自动 QueuedConnection）。
    _proc_ended = Signal()

    def __init__(self, serial, default_pkg='', parent=None):
        super().__init__(parent)
        self._adb = AdbHelper()
        self._serial = serial
        self._default_pkg = default_pkg or ''
        self._proc = None
        self._reader = None
        self._watcher = None
        self._closed = False
        self._running = False
        self._start_ts = 0
        self._event_count = 0
        self._crash_count = 0
        self._anr_count = 0
        self._pending_lines = []      # 日志批量缓冲，由 _flush_timer 渲染
        self._proc_returncode = None  # 由 _watch_proc 设置
        self._monkey_log_fh = None    # 落盘日志文件句柄
        self._monkey_log_path = ''
        self._paused = False
        self._monkey_pid = None
        self._event_stats = {}
        self._recorded_events = []
        self._pending_touch = None
        self._pending_swipe = None
        self._templates_file = os.path.join(
            os.path.expanduser('~'), '.Super_ADB', 'monkey_templates.json')
        self._replay_dlg = None

        self.setWindowTitle(f'Monkey 压力测试 — {serial}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(720, 620)
        self.resize(820, 700)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id))
        self.setWindowFlag(Qt.Window, True)

        # ── 主题色高亮外边框卡片（含主布局挂载）─────────────────
        self.card, _ = _create_popup_card(self, self._theme_id)

        self._build_ui()
        if self._default_pkg:
            self.pkg_input.setText(self._default_pkg)

        # 启动前后台探测 monkey 版本，便于排查版本兼容
        threading.Thread(target=self._probe_monkey_version, daemon=True).start()

        self._line_arrived.connect(self._append_log)
        self._version_ready.connect(self._apply_version_text)
        self._pause_state_ready.connect(self._on_pause_state_ready)
        self._tombstone_done.connect(self._on_tombstone_done)
        self._proc_ended.connect(self._on_finished)

        # 耗时计时器
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

        # 日志批量刷新定时器（100ms）：减少 QTextEdit 布局刷新 + stat 刷新次数
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._flush_logs)
        self._flush_timer.start()

    # ---- 主题切换 ----
    def apply_theme(self, theme_id):
        """运行时切换主题：重设全局 QSS + card 样式 + 外发光 + 信息标签颜色。"""
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        if hasattr(self, 'card') and self.card is not None:
            self.card.setStyleSheet(highlight_card_style(theme_id))
            add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        if hasattr(self, '_info_label') and self._info_label is not None:
            self._info_label.setStyleSheet(f"color: {THEMES[theme_id]['text_disabled']};")
        self.update()

    # ---- UI 搭建 ----
    def _build_ui(self):
        root = QVBoxLayout(self.card)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # === 基本参数 ===
        g1 = QGroupBox('基本参数')
        f1 = QGridLayout(g1)
        f1.setContentsMargins(10, 14, 10, 8)
        f1.setHorizontalSpacing(12)
        f1.setVerticalSpacing(6)

        self.pkg_input = QLineEdit()
        self.pkg_input.setPlaceholderText('com.example.app')
        f1.addWidget(QLabel('包名:'), 0, 0)
        f1.addWidget(self.pkg_input, 0, 1, 1, 3)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 1000000)
        self.count_spin.setValue(500)
        f1.addWidget(QLabel('事件数:'), 0, 4)
        f1.addWidget(self.count_spin, 0, 5)

        self.throttle_spin = QSpinBox()
        self.throttle_spin.setRange(0, 60000)
        self.throttle_spin.setValue(0)
        self.throttle_spin.setSuffix(' ms')
        f1.addWidget(QLabel('事件间隔:'), 0, 6)
        f1.addWidget(self.throttle_spin, 0, 7)

        self.seed_input = QLineEdit()
        self.seed_input.setPlaceholderText('留空=随机')
        f1.addWidget(QLabel('随机种子:'), 1, 0)
        f1.addWidget(self.seed_input, 1, 1)

        self.verbosity_combo = QComboBox()
        self.verbosity_combo.addItems(['-v', '-vv', '-vvv'])
        f1.addWidget(QLabel('详细度:'), 1, 2)
        f1.addWidget(self.verbosity_combo, 1, 3)

        self.category_combo = QComboBox()
        self.category_combo.addItems(['LAUNCHER', 'MONKEY', 'LEANBACK_LAUNCHER'])
        f1.addWidget(QLabel('类别:'), 1, 4)
        f1.addWidget(self.category_combo, 1, 5)

        btn_normalize = QPushButton('归一化 100%')
        btn_normalize.clicked.connect(self._normalize_pct)
        f1.addWidget(btn_normalize, 1, 7)

        # 模板槽位
        tmpl_lay = QHBoxLayout()
        tmpl_lay.setSpacing(6)
        self.template_combo = QComboBox()
        self.template_combo.addItems([f'模板 {i}' for i in range(1, 6)])
        self.template_combo.setFixedWidth(90)
        btn_save_tmpl = QPushButton('保存')
        btn_load_tmpl = QPushButton('加载')
        btn_save_tmpl.setFixedWidth(64)
        btn_load_tmpl.setFixedWidth(64)
        btn_save_tmpl.clicked.connect(self._save_template)
        btn_load_tmpl.clicked.connect(self._load_template)
        tmpl_lay.addWidget(QLabel('配置模板:'))
        tmpl_lay.addWidget(self.template_combo)
        tmpl_lay.addWidget(btn_save_tmpl)
        tmpl_lay.addWidget(btn_load_tmpl)
        tmpl_lay.addStretch(1)
        f1.addLayout(tmpl_lay, 2, 0, 1, 8)

        root.addWidget(g1)

        # === 事件比例 ===
        g2 = QGroupBox('事件比例 (%)  —  设为 -1 表示不指定，走 monkey 默认')
        f2 = QGridLayout(g2)
        f2.setContentsMargins(10, 14, 10, 8)
        f2.setHorizontalSpacing(10)
        f2.setVerticalSpacing(6)
        self._pct_spins = {}
        pct_items = [
            ('pct_touch',     '触摸',    50),
            ('pct_motion',    '滑动',    20),
            ('pct_trackball', '轨迹球',  -1),
            ('pct_nav',       '导航',    -1),
            ('pct_majornav',  '主导航',  -1),
            ('pct_appswitch', '应用切换', -1),
            ('pct_anyevent',  '任意事件', -1),
        ]
        for i, (key, label, default) in enumerate(pct_items):
            row, col = i // 4, (i % 4) * 2
            sp = QSpinBox()
            sp.setRange(-1, 100)
            sp.setValue(default)
            sp.setFixedWidth(64)
            f2.addWidget(QLabel(f'{label}:'), row, col)
            f2.addWidget(sp, row, col + 1)
            self._pct_spins[key] = sp
        root.addWidget(g2)

        # === 忽略 / 调试选项 ===
        g3 = QGroupBox('忽略 / 调试选项')
        f3 = QHBoxLayout(g3)
        f3.setContentsMargins(10, 14, 10, 8)
        f3.setSpacing(16)
        self.ignore_crashes_chk = QCheckBox('崩溃继续')
        self.ignore_timeouts_chk = QCheckBox('超时(ANR)继续')
        self.ignore_security_chk = QCheckBox('安全异常继续')
        self.kill_process_chk = QCheckBox('出错杀进程')
        self.monitor_native_chk = QCheckBox('监控 native 崩溃')
        self.bugreport_chk = QCheckBox('出错生成 bugreport')
        for w in (self.ignore_crashes_chk, self.ignore_timeouts_chk,
                  self.ignore_security_chk, self.kill_process_chk,
                  self.monitor_native_chk, self.bugreport_chk):
            f3.addWidget(w)
        f3.addStretch(1)
        root.addWidget(g3)

        # === 操作栏 ===
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.btn_run = QPushButton('▶ 运行')
        self.btn_run.setFixedWidth(100)
        self.btn_run.clicked.connect(self._run)
        self.btn_stop = QPushButton('■ 停止')
        self.btn_stop.setFixedWidth(100)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        self.btn_pause = QPushButton('⏸ 暂停')
        self.btn_pause.setFixedWidth(100)
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_replay = QPushButton('↻ 回放')
        self.btn_replay.setFixedWidth(80)
        self.btn_replay.setEnabled(False)
        self.btn_replay.clicked.connect(self._open_replay)
        bar.addWidget(self.btn_run)
        bar.addWidget(self.btn_stop)
        bar.addWidget(self.btn_pause)
        bar.addWidget(self.btn_replay)
        bar.addStretch(1)
        self.status_label = QLabel('就绪')
        self.status_label.setStyleSheet('color: #1de9b6;')
        bar.addWidget(self.status_label)
        self.version_label = QLabel('monkey: 检测中…')
        self.version_label.setStyleSheet('color: #888;')
        bar.addWidget(self.version_label)
        bar.addSpacing(16)
        self.stat_label = QLabel('事件: 0  ·  CRASH: 0  ·  ANR: 0  ·  耗时: 00:00')
        self.stat_label.setStyleSheet('color: #b0b0b0;')
        bar.addWidget(self.stat_label)
        root.addLayout(bar)

        # === 日志输出 ===
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet(
            f'QTextEdit {{ background: #1a1a1a; color: #d4d4d4; '
            f'font: 10pt "Consolas", "{FONT_FAMILY}"; }}')
        self.log_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        root.addWidget(self.log_edit, 1)

        # === 实时事件分类饼图 ===
        self.pie_chart = EventPieChart()
        self.pie_chart.setVisible(False)
        root.addWidget(self.pie_chart)

        # === 预览命令 ===
        self.cmd_label = QLabel('')
        self.cmd_label.setStyleSheet(
            'color: #888; font: 9pt "Consolas"; background: transparent;')
        self.cmd_label.setWordWrap(True)
        root.addWidget(self.cmd_label)

        # 参数变化时刷新预览
        for w in [self.pkg_input, self.count_spin, self.throttle_spin,
                  self.seed_input, self.verbosity_combo, self.category_combo,
                  self.ignore_crashes_chk, self.ignore_timeouts_chk,
                  self.ignore_security_chk, self.kill_process_chk,
                  self.monitor_native_chk, self.bugreport_chk]:
            if isinstance(w, QComboBox):
                w.currentIndexChanged.connect(self._refresh_cmd_preview)
            elif isinstance(w, QCheckBox):
                w.toggled.connect(self._refresh_cmd_preview)
            else:
                w.valueChanged.connect(self._refresh_cmd_preview) if hasattr(w, 'valueChanged') else w.textChanged.connect(self._refresh_cmd_preview)
        for sp in self._pct_spins.values():
            sp.valueChanged.connect(self._refresh_cmd_preview)
        self._refresh_cmd_preview()

    # ---- 事件比例归一化 ----
    def _normalize_pct(self):
        """把所有 >=0 的事件比例按权重缩放到合计 100。"""
        used = [(k, sp) for k, sp in self._pct_spins.items() if sp.value() >= 0]
        if not used:
            return
        total = sum(sp.value() for _, sp in used)
        if total == 0:
            # 平均分
            share = 100 // len(used)
            for i, (_, sp) in enumerate(used):
                sp.setValue(share if i < len(used) - 1 else 100 - share * (len(used) - 1))
        else:
            new_total = 0
            for i, (_, sp) in enumerate(used):
                if i == len(used) - 1:
                    sp.setValue(max(0, 100 - new_total))
                else:
                    v = round(sp.value() / total * 100)
                    sp.setValue(v)
                    new_total += v

    # ---- 命令预览 ----
    def _refresh_cmd_preview(self, *_):
        try:
            args = build_monkey_args(self._collect_params())
            self.cmd_label.setText(
                f'adb -s {self._serial} shell ' + ' '.join(args))
        except ValueError as e:
            self.cmd_label.setText(f'(参数不完整: {e})')

    def _collect_params(self) -> dict:
        p = {
            'pkg': self.pkg_input.text(),
            'count': self.count_spin.value(),
            'throttle': self.throttle_spin.value(),
            'seed': self.seed_input.text(),
            'verbosity': self.verbosity_combo.currentText().count('v'),
            'category': self.category_combo.currentText(),
            'ignore_crashes': self.ignore_crashes_chk.isChecked(),
            'ignore_timeouts': self.ignore_timeouts_chk.isChecked(),
            'ignore_security': self.ignore_security_chk.isChecked(),
            'kill_process': self.kill_process_chk.isChecked(),
            'monitor_native': self.monitor_native_chk.isChecked(),
            'bugreport': self.bugreport_chk.isChecked(),
        }
        for k, sp in self._pct_spins.items():
            p[k] = sp.value()
        return p

    # ---- 运行模板（5 槽位） ----
    def _load_templates(self) -> dict:
        # 缺失或解析失败均返回 {}；解析失败会记录 warning（不再无声吞掉）
        return load_json(self._templates_file, default={})

    def _save_template(self):
        idx = self.template_combo.currentIndex()
        name = self.template_combo.currentText()
        try:
            templates = self._load_templates()
            templates[str(idx)] = self._collect_params()
            if save_json(self._templates_file, templates):
                self.status_label.setText(f'已保存 {name}')
                self.status_label.setStyleSheet('color: #1de9b6;')
            else:
                self.status_label.setText('保存模板失败')
                self.status_label.setStyleSheet('color: #ff6b6b;')
        except Exception as e:
            self.status_label.setText(f'保存模板失败: {e}')
            self.status_label.setStyleSheet('color: #ff6b6b;')

    def _load_template(self):
        idx = self.template_combo.currentIndex()
        name = self.template_combo.currentText()
        templates = self._load_templates()
        params = templates.get(str(idx))
        if not params:
            self.status_label.setText(f'{name} 为空')
            self.status_label.setStyleSheet('color: #ffab40;')
            return
        self._apply_params(params)
        self._refresh_cmd_preview()
        self.status_label.setText(f'已加载 {name}')
        self.status_label.setStyleSheet('color: #1de9b6;')

    def _apply_params(self, p: dict):
        self.pkg_input.setText(p.get('pkg', ''))
        self.count_spin.setValue(p.get('count', 500))
        self.throttle_spin.setValue(p.get('throttle', 0))
        self.seed_input.setText(p.get('seed', ''))
        v = p.get('verbosity', 1)
        self.verbosity_combo.setCurrentIndex(max(0, min(2, v - 1)))
        self.category_combo.setCurrentText(p.get('category', 'LAUNCHER'))
        self.ignore_crashes_chk.setChecked(bool(p.get('ignore_crashes')))
        self.ignore_timeouts_chk.setChecked(bool(p.get('ignore_timeouts')))
        self.ignore_security_chk.setChecked(bool(p.get('ignore_security')))
        self.kill_process_chk.setChecked(bool(p.get('kill_process')))
        self.monitor_native_chk.setChecked(bool(p.get('monitor_native')))
        self.bugreport_chk.setChecked(bool(p.get('bugreport')))
        for k, sp in self._pct_spins.items():
            sp.setValue(p.get(k, -1))

    # ---- monkey 版本探测 ----
    def _probe_monkey_version(self):
        """后台探测设备 monkey 版本，便于排查版本兼容。

        注意：通过 Signal 回主线程更新 QLabel，避免跨线程操作 UI。
        """
        try:
            out = subprocess.run(
                [self._adb.adb_path, '-s', self._serial, 'shell',
                 'monkey', '--version'],
                capture_output=True, text=True, encoding='utf-8',
                errors='replace', creationflags=CREATE_NO_WINDOW, timeout=10)
            ver = (out.stdout or '').strip() or (out.stderr or '').strip()
            if ver:
                self._version_ready.emit(f'monkey: {ver}', 'color: #1de9b6;')
            else:
                self._version_ready.emit('monkey: 未返回版本', 'color: #ffab40;')
        except Exception as e:
            self._version_ready.emit('monkey: 检测失败', 'color: #ff6b6b;')
            _ = e

    def _apply_version_text(self, text, stylesheet):
        """主线程槽：设置 monkey 版本文本（关闭窗口后不再访问控件）。"""
        if self._closed:
            return
        try:
            self.version_label.setText(text)
            self.version_label.setStyleSheet(stylesheet)
        except Exception:
            pass

    # ---- 暂停 / 继续（给 monkey 进程发 SIGSTOP/SIGCONT） ----
    def _toggle_pause(self):
        if not self._running:
            return
        if self._paused:
            self._resume_monkey()
        else:
            self._pause_monkey()

    def _pause_monkey(self):
        pid = self._find_monkey_pid()
        if not pid:
            self.status_label.setText('未找到 monkey 进程')
            self.status_label.setStyleSheet('color: #ffab40;')
            return
        self._send_signal(pid, '-STOP')

    def _resume_monkey(self):
        pid = self._find_monkey_pid()
        if not pid:
            self.status_label.setText('未找到 monkey 进程')
            self.status_label.setStyleSheet('color: #ffab40;')
            return
        self._send_signal(pid, '-CONT')

    def _find_monkey_pid(self) -> str:
        """通过 pidof / ps 找设备上 monkey 进程 PID。"""
        try:
            r = subprocess.run(
                [self._adb.adb_path, '-s', self._serial, 'shell',
                 'pidof', '-s', 'com.android.commands.monkey'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=5)
            pid = r.stdout.strip().split()[0] if r.stdout.strip() else ''
            if pid.isdigit():
                return pid
        except Exception:
            pass
        # fallback：ps -A | grep monkey
        try:
            r = subprocess.run(
                [self._adb.adb_path, '-s', self._serial, 'shell', 'ps -A | grep monkey'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=5)
            for ln in (r.stdout or '').splitlines():
                parts = ln.split()
                if 'monkey' in ln and len(parts) > 1 and parts[1].isdigit():
                    return parts[1]
        except Exception:
            pass
        return ''

    def _send_signal(self, pid: str, sig: str):
        def _task():
            try:
                r = subprocess.run(
                    [self._adb.adb_path, '-s', self._serial, 'shell', 'kill', sig, pid],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    creationflags=CREATE_NO_WINDOW, timeout=5)
                is_cont = sig == '-CONT'
                if r.returncode == 0:
                    self._pause_state_ready.emit(is_cont, '已继续' if is_cont else '已暂停')
                else:
                    self._pause_state_ready.emit(is_cont, f'发送 {sig} 失败: {r.stderr or r.stdout}')
            except Exception as e:
                self._pause_state_ready.emit(False, f'信号发送异常: {e}')
        threading.Thread(target=_task, daemon=True).start()

    def _on_pause_state_ready(self, is_cont: bool, msg: str):
        if not self._running:
            return
        if is_cont:
            self._paused = False
            self.btn_pause.setText('⏸ 暂停')
            self.status_label.setText('运行中…')
            self.status_label.setStyleSheet('color: #1de9b6;')
        else:
            self._paused = True
            self.btn_pause.setText('▶ 继续')
            self.status_label.setText(f'已暂停 · {msg}')
            self.status_label.setStyleSheet('color: #ffab40;')

    # ---- 落盘日志 ----
    def _open_monkey_log(self, pkg):
        """打开落盘日志文件 <pkg>_<timestamp>.log（桌面/Super_ADB）。"""
        if self._monkey_log_fh is not None:
            return
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        save_dir = os.path.join(desktop, 'Super_ADB')
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception:
            return
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_pkg = re.sub(r'[^A-Za-z0-9_.-]', '_', pkg or 'monkey')
        path = os.path.join(save_dir, f'{safe_pkg}_{ts}.log')
        try:
            self._monkey_log_fh = open(path, 'w', encoding='utf-8')
            self._monkey_log_path = path
            self._monkey_log_fh.write(
                f'# Monkey 压测日志  pkg={pkg}  device={self._serial}  '
                f'time={time.strftime("%Y-%m-%d %H:%M:%S")}\n')
        except Exception:
            self._monkey_log_fh = None
            self._monkey_log_path = ''

    def _close_monkey_log(self):
        if self._monkey_log_fh is not None:
            try:
                self._monkey_log_fh.flush()
                self._monkey_log_fh.close()
            except Exception:
                pass
            self._monkey_log_fh = None

    # ---- 运行 ----
    def _run(self):
        if self._running:
            return
        try:
            args = build_monkey_args(self._collect_params())
        except ValueError as e:
            self.status_label.setText(f'参数错误: {e}')
            self.status_label.setStyleSheet('color: #ff6b6b;')
            return

        # 切换 UI 状态
        self._running = True
        self._start_ts = time.time()
        self._event_count = 0
        self._crash_count = 0
        self._anr_count = 0
        self._event_stats = {}
        self._recorded_events = []
        self._pending_touch = None
        self.pie_chart.setVisible(False)
        self.btn_replay.setEnabled(False)
        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status_label.setText('运行中…')
        self.status_label.setStyleSheet('color: #1de9b6;')
        self._refresh_stat()

        # 清空日志 + 打开落盘日志
        self.log_edit.clear()
        self._open_monkey_log(args[2] if len(args) > 2 else self.pkg_input.text())
        self._append_log(
            f'$ adb -s {self._serial} shell {" ".join(args)}', 'info')
        self._append_log('---- Monkey 开始 ----', 'info')

        # 先检查 monkey 是否可用 (部分模拟器/设备会缺 monkey)
        monkey_available = True
        check_cmd = [self._adb.adb_path, '-s', self._serial, 'shell', 'command', '-v', 'monkey']
        try:
            check = subprocess.run(
                check_cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=10)
            if check.returncode != 0 or 'monkey' not in (check.stdout or '').lower():
                monkey_available = False
        except Exception as e:
            self._append_log(f'[警告] monkey 可用性检查失败: {e}', 'error')
            monkey_available = False

        # 设备没有 monkey → 回退到 am start 打开应用
        if not monkey_available:
            self._append_log('[提示] 该设备无 monkey 命令，回退到 am start 方式启动应用', 'info')
            self._fallback_am_start(args)
            return

        # 启动 Popen
        cmd = [self._adb.adb_path, '-s', self._serial, 'shell'] + args
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                bufsize=1,  # 行缓冲, 尽快拿到输出
                creationflags=CREATE_NO_WINDOW,  # CREATE_NO_WINDOW
            )
        except Exception as e:
            self._append_log(f'启动失败: {e}', 'error')
            self._on_finished()
            return

        # 后台线程读输出
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

        # 进程退出监视线程：防止 adb shell pipe 在 monkey 结束后不关闭导致读线程卡住
        self._watcher = threading.Thread(target=self._watch_proc, daemon=True)
        self._watcher.start()

        self._elapsed_timer.start()

    def _watch_proc(self):
        """后台线程：等待 Popen 进程退出，然后通知主线程收尾。"""
        proc = self._proc
        if proc is None:
            return
        try:
            rc = proc.wait()
        except Exception:
            rc = None
        if not self._closed and self._running:
            # 在状态里记录最终返回码，方便 _on_finished 使用
            self._proc_returncode = rc
            self._proc_ended.emit()

    def _fallback_am_start(self, monkey_args: list):
        """设备无 monkey 时回退方案: 用 am start 启动应用。

        monkey_args 形如 ['monkey', '-p', 'com.x', ...]
        包名在 args[2] 位置。
        """
        # 从 monkey 参数里提取包名
        pkg = ''
        try:
            idx = monkey_args.index('-p')
            pkg = monkey_args[idx + 1]
        except (ValueError, IndexError):
            pass
        if not pkg:
            pkg = self.pkg_input.text().strip()
        if not pkg:
            self._append_log('[错误] 未找到包名，无法启动', 'error')
            self._on_finished()
            return

        self._append_log(f'包名: {pkg}', 'info')

        # ① 查入口 Activity（自研adb模式用执行shell，否则用官方adb）
        resolve_cmd_str = f'cmd package resolve-activity --brief {pkg}'
        self._append_log(f'$ adb -s {self._serial} shell {resolve_cmd_str}', 'info')
        try:
            if getattr(self._adb, '_用自研adb', False):
                resolve_out = self._adb.执行shell(self._serial, resolve_cmd_str, timeout=15)
                # 模拟 _run_no_shell 的返回结构
                class _R: pass
                r = _R()
                r.stdout = resolve_out or ''
                r.returncode = 0 if resolve_out else 1
                r.stderr = ''
            else:
                resolve_cmd = [self._adb.adb_path, '-s', self._serial,
                               'shell', 'cmd', 'package', 'resolve-activity', '--brief', pkg]
                r = self._adb._run_no_shell(resolve_cmd, timeout=15)
        except Exception as e:
            self._append_log(f'[错误] 查询入口 Activity 失败: {e}', 'error')
            self._on_finished()
            return

        # resolve-activity --brief 输出最后一行是 pkg/.Activity
        activity = ''
        for ln in (r.stdout or '').strip().splitlines():
            ln = ln.strip()
            if ln and '/' in ln:
                activity = ln
        if not activity:
            self._append_log(f'[错误] 未找到入口 Activity，原始输出: {r.stdout}', 'error')
            self._append_log('提示: 可尝试用 am start -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n <pkg>/.<Activity> 手动启动', 'info')
            self._on_finished()
            return

        self._append_log(f'入口 Activity: {activity}', 'done')

        # ② am start 启动（自研adb模式用执行shell，否则用官方adb）
        start_cmd_str = f'am start -n {activity}'
        self._append_log(f'$ adb -s {self._serial} shell {start_cmd_str}', 'info')
        try:
            if getattr(self._adb, '_用自研adb', False):
                out2 = self._adb.执行shell(self._serial, start_cmd_str, timeout=15) or ''
                returncode = 0 if out2 else 1
            else:
                start_cmd = [self._adb.adb_path, '-s', self._serial,
                             'shell', 'am', 'start', '-n', activity]
                r2 = self._adb._run_no_shell(start_cmd, timeout=15)
                out2 = (r2.stdout or '').strip()
                returncode = r2.returncode
        except Exception as e:
            self._append_log(f'[错误] am start 失败: {e}', 'error')
            self._on_finished()
            return

        if returncode == 0 and ('Starting' in out2 or 'starting' in out2.lower()):
            self._append_log(f'应用已启动 ✓  {out2}', 'done')
            self._append_log('提示: 设备无 monkey 命令，无法执行压测；已为你打开应用，可手动操作或换带 Google APIs 的镜像重试。', 'info')
        else:
            self._append_log(f'[错误] am start 返回非零: {out2}', 'error')

        self._on_finished()

    def _read_loop(self):
        """后台线程：逐行读 Popen.stdout，通过 Signal 回主线程。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            if not self._closed:
                self._proc_ended.emit()
            return
        try:
            while True:
                if self._closed:
                    break
                line = proc.stdout.readline()
                if not line:
                    break
                self._line_arrived.emit(line.rstrip('\r\n'))
        except Exception:
            pass
        finally:
            # 读完后通知主线程
            if not self._closed:
                self._proc_ended.emit()

    # ---- 日志追加 + 关键字高亮 ----
    def _append_log(self, line: str, kind: str = None):
        """缓冲日志行，由 _flush_timer(100ms) 批量渲染。

        kind 控制颜色: None=自动检测, info=青色, crash=红色,
        anr=橙色, done=绿色, error=红色
        """
        self._pending_lines.append((line, kind))
        # 同步落盘（原始行，无 HTML 着色）
        if self._monkey_log_fh is not None:
            try:
                self._monkey_log_fh.write(line + '\n')
            except Exception:
                pass

    def _flush_logs(self):
        """100ms 批量渲染：减少 QTextEdit 布局刷新 + stat 刷新次数。"""
        if self._closed:
            self._pending_lines = []
            return
        if not self._pending_lines:
            return
        batch = self._pending_lines
        self._pending_lines = []

        color_map = {
            'info':   '#56b6c2',
            'crash':  '#ff6b6b',
            'anr':    '#ffab40',
            'done':   '#98c379',
            'error':  '#ff6b6b',
            'monkey': '#c678dd',
        }

        html_parts = []
        stats_changed = False
        for line, kind in batch:
            text = line.rstrip()
            # 事件分类统计 + 回放序列记录（与着色解耦，无论 kind 是否已知都尝试解析）
            if self._classify_and_record(text):
                stats_changed = True
            if kind is None:
                low = text.lower()
                if '// crash' in low or 'crash:' in low:
                    kind = 'crash'
                    self._crash_count += 1
                elif '// not responding' in low or 'anr' in low:
                    kind = 'anr'
                    self._anr_count += 1
                elif 'events injected' in low:
                    kind = 'done'
                    m = re.search(r'events injected:\s*(\d+)', low)
                    if m:
                        self._event_count = int(m.group(1))
                    # 达到设定事件数后，主动结束运行（防止 adb shell pipe 不关闭导致状态卡住）
                    if self._event_count >= self.count_spin.value():
                        QTimer.singleShot(100, self._finish_if_still_running)
                elif '// monkey finished' in low:
                    kind = 'done'
                    # Monkey 自己报告结束，但 stdout 可能仍不关闭，主动收尾
                    QTimer.singleShot(100, self._finish_if_still_running)
                elif text.startswith(':Monkey:') or text.startswith('// :Monkey:'):
                    kind = 'monkey'
                elif text.startswith('$ ') or text.startswith('----') or text.startswith('[错误]') or text.startswith('[警告]'):
                    kind = 'info'

            # 粗略事件计数: :Monkey: 行出现一次算一组事件
            if kind == 'monkey':
                self._event_count += 1

            color = color_map.get(kind, '#d4d4d4')
            bold = 'font-weight:bold;' if kind in ('crash', 'done') else ''
            html_parts.append(
                f'<span style="color:{color};{bold}">{self._escape_html(text)}</span>')

        # 一次性插入（一次文档布局刷新）
        if html_parts:
            cursor = QTextCursor(self.log_edit.document())
            cursor.movePosition(QTextCursor.End)
            cursor.insertHtml('<br>'.join(html_parts))
            sb = self.log_edit.verticalScrollBar()
            sb.setValue(sb.maximum())

        # 饼图实时刷新（仅在分类计数变化时有数据才显示）
        if stats_changed and self._event_stats:
            self.pie_chart.setVisible(True)
            self.pie_chart.set_data(self._event_stats)

        # 只刷新一次统计
        self._refresh_stat()

    @staticmethod
    def _escape_html(s: str) -> str:
        return (s.replace('&', '&amp;')
                 .replace('<', '&lt;')
                 .replace('>', '&gt;'))

    # ---- 事件分类统计 + 回放序列记录 ----
    def _classify_and_record(self, text: str) -> bool:
        """解析一行 monkey 输出，更新事件分类计数并（按需）记录可回放命令。

        返回 True 表示本次分类计数发生了变化（需要刷新饼图）。
        """
        changed = False
        t = text

        # —— 分类统计（覆盖 monkey -v/-vv 常见 :Sending 行）——
        if ':Sending Touch' in t:
            key = EVT_TOUCH
        elif ':Sending Motion' in t:
            key = EVT_MOTION
        elif ':Sending Trackball' in t:
            key = EVT_TRACKBALL
        elif ':Sending Key' in t:
            key = EVT_NAV if ('KEYCODE_DPAD' in t or 'KEYCODE_NAV' in t) else EVT_KEY
        elif ':Sending Flip' in t or ':Sending Rotation' in t:
            key = EVT_SYS
        else:
            key = None

        if key is not None:
            self._event_stats[key] = self._event_stats.get(key, 0) + 1
            changed = True

        # —— 回放序列：仅记录可映射为 adb shell input 的事件 ——
        cmd = _to_input_cmd(t)
        if cmd:
            self._recorded_events.append(cmd)

        return changed

    # ---- 停止 ----
    def _stop(self):
        if not self._running:
            return
        self._append_log('---- 用户停止 ----', 'info')
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                # 给 0.5s 优雅退出, 否则 kill
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        self._on_finished()

    def _finish_if_still_running(self):
        """由日志关键字触发的安全收尾：仅当仍在运行时才调用 _on_finished。"""
        if self._running:
            self._on_finished()

    # ---- 运行结束 ----
    def _on_finished(self):
        if not self._running:
            return
        self._running = False
        self._elapsed_timer.stop()
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)

        proc = self._proc
        # 关闭 stdout，让还在阻塞的 readline() 立即返回并结束读线程
        if proc and proc.stdout:
            try:
                proc.stdout.close()
            except Exception:
                pass

        rc = self._proc_returncode
        if rc is None and proc:
            rc = proc.returncode
        msg = f'运行结束 (returncode={rc})'
        if rc in (0, None):
            self.status_label.setText(msg)
            self.status_label.setStyleSheet('color: #98c379;')
        else:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet('color: #ff6b6b;')
        self._append_log(msg, 'info')
        # 关闭落盘日志并提示路径（关窗后仍可回看）
        if self._monkey_log_path:
            self._append_log(f'日志已保存到: {self._monkey_log_path}', 'done')
        self._close_monkey_log()
        self._proc = None
        self._reader = None
        self._watcher = None
        self._proc_returncode = None

        # 回放按钮：有记录的事件才允许回放
        if self._recorded_events:
            self.btn_replay.setEnabled(True)

        # 崩溃报告：检测到崩溃则自动拉取 tombstone 到桌面/Super_ADB
        if self._crash_count > 0:
            threading.Thread(target=self._pull_tombstones, daemon=True).start()

    # ---- 状态刷新 ----
    def _refresh_stat(self):
        self.stat_label.setText(
            f'事件: {self._event_count}  ·  '
            f'CRASH: {self._crash_count}  ·  '
            f'ANR: {self._anr_count}  ·  '
            f'耗时: {self._elapsed_str()}')

    # ---- 崩溃报告：自动拉取 tombstone ----
    def _pull_tombstones(self):
        """后台拉取 /data/tombstones/ 到 桌面/Super_ADB/tombstones_<serial>_<ts>/。"""
        adb = self._adb.adb_path
        serial = self._serial
        ok = False
        msg = ''
        try:
            ls = subprocess.run(
                [adb, '-s', serial, 'shell', 'ls', '/data/tombstones/'],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=10)
            files = [f.strip() for f in ls.stdout.split() if f.strip()]
            files = [f for f in files if f.startswith('tombstone') and 'No such' not in f]
            if not files:
                self._tombstone_done.emit(False, '未发现 tombstone 文件')
                return
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            dest = os.path.join(desktop, 'Super_ADB',
                                f'tombstones_{serial}_{time.strftime("%Y%m%d_%H%M%S")}')
            os.makedirs(dest, exist_ok=True)
            pulled = 0
            for f in files:
                r = subprocess.run(
                    [adb, '-s', serial, 'pull', f'/data/tombstones/{f}', dest],
                    capture_output=True, text=True, encoding='utf-8', errors='replace',
                    creationflags=CREATE_NO_WINDOW, timeout=30)
                if r.returncode == 0:
                    pulled += 1
            ok = pulled > 0
            msg = (f'已拉取 {pulled}/{len(files)} 个 tombstone → {dest}'
                   if ok else f'tombstone 拉取失败（可能无权限）: {dest}')
        except Exception as e:
            msg = f'tombstone 拉取异常: {e}'
        self._tombstone_done.emit(ok, msg)

    def _on_tombstone_done(self, ok: bool, msg: str):
        try:
            if self._closed:
                return
            self._append_log(f'[崩溃报告] {msg}', 'done' if ok else 'info')
            if ok:
                self.status_label.setText('已拉取崩溃报告')
                self.status_label.setStyleSheet('color: #1de9b6;')
        except Exception:
            pass

    # ---- 事件回放 ----
    def _open_replay(self):
        if not self._recorded_events:
            self.status_label.setText('本次运行没有可回放的事件')
            self.status_label.setStyleSheet('color: #ffab40;')
            return
        dlg = ReplayDialog(self._serial, self._recorded_events, self)
        dlg.setAttribute(Qt.WindowStaysOnTopHint, False)
        dlg.show()
        self._replay_dlg = dlg  # 保持引用，防止被 GC

    def _elapsed_str(self) -> str:
        if not self._start_ts:
            return '00:00'
        secs = int(time.time() - self._start_ts)
        return f'{secs // 60:02d}:{secs % 60:02d}'

    def _refresh_elapsed(self):
        self._refresh_stat()

    # ---- 关窗即停 ----
    def closeEvent(self, event):
        self._elapsed_timer.stop()
        self._flush_timer.stop()
        self._flush_logs()  # 最终刷新残留缓冲（此时 _closed 仍为 False）
        self._closed = True
        self._close_monkey_log()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
        super().closeEvent(event)
