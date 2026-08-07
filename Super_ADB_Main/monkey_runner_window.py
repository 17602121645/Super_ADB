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

import re
import os
import subprocess
import threading
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QFont, QTextCursor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QCheckBox, QPushButton,
    QGroupBox, QTextEdit, QSizePolicy,
)

from adb_utils import AdbHelper, CREATE_NO_WINDOW
from 界面样式 import STYLE_SHEET, FONT_FAMILY
from popup_style import HIGHLIGHT_CARD_STYLE, add_green_glow

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
import png_rc  # noqa: F401


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
# Monkey 配置 + 运行窗口
# ------------------------------------------------------------------
class MonkeyRunnerWindow(QWidget):
    """Monkey 压测独立窗口。

    用法：
        win = MonkeyRunnerWindow(serial, default_pkg='', parent=main)
        win.show()
    """

    _line_arrived = Signal(str)
    _version_ready = Signal(str, str)  # text, stylesheet

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

        self.setWindowTitle(f'Monkey 压力测试 — {serial}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(720, 620)
        self.resize(820, 700)
        self.setStyleSheet(STYLE_SHEET)
        self.setWindowFlag(Qt.Window, True)

        # ── 绿色高亮外边框卡片 ────────────────────────────────
        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        self.card.setStyleSheet(HIGHLIGHT_CARD_STYLE)
        add_green_glow(self.card)

        self._build_ui()
        if self._default_pkg:
            self.pkg_input.setText(self._default_pkg)

        # 启动前后台探测 monkey 版本，便于排查版本兼容
        threading.Thread(target=self._probe_monkey_version, daemon=True).start()

        self._line_arrived.connect(self._append_log)
        self._version_ready.connect(self._apply_version_text)

        # 耗时计时器
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

        # 日志批量刷新定时器（100ms）：减少 QTextEdit 布局刷新 + stat 刷新次数
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._flush_logs)
        self._flush_timer.start()

        # 主布局：把卡片放到窗口上（留出 10px 让绿色光晕可见）
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

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
        bar.addWidget(self.btn_run)
        bar.addWidget(self.btn_stop)
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
            QTimer.singleShot(0, self._on_finished)

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

        # ① 查入口 Activity
        resolve_cmd = [self._adb.adb_path, '-s', self._serial,
                       'shell', 'cmd', 'package', 'resolve-activity', '--brief', pkg]
        self._append_log(f'$ adb -s {self._serial} shell cmd package resolve-activity --brief {pkg}', 'info')
        try:
            r = subprocess.run(
                resolve_cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=15)
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

        # ② am start 启动
        start_cmd = [self._adb.adb_path, '-s', self._serial,
                     'shell', 'am', 'start', '-n', activity]
        self._append_log(f'$ adb -s {self._serial} shell am start -n {activity}', 'info')
        try:
            r2 = subprocess.run(
                start_cmd, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW, timeout=15)
        except Exception as e:
            self._append_log(f'[错误] am start 失败: {e}', 'error')
            self._on_finished()
            return

        out2 = (r2.stdout or '').strip()
        if r2.returncode == 0 and ('Starting' in out2 or 'starting' in out2.lower()):
            self._append_log(f'应用已启动 ✓  {out2}', 'done')
            self._append_log('提示: 设备无 monkey 命令，无法执行压测；已为你打开应用，可手动操作或换带 Google APIs 的镜像重试。', 'info')
        else:
            self._append_log(f'[错误] am start 返回非零: {out2 or r2.stderr}', 'error')

        self._on_finished()

    def _read_loop(self):
        """后台线程：逐行读 Popen.stdout，通过 Signal 回主线程。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            if not self._closed:
                QTimer.singleShot(0, self._on_finished)
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
                QTimer.singleShot(0, self._on_finished)

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
        for line, kind in batch:
            text = line.rstrip()
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

        # 只刷新一次统计
        self._refresh_stat()

    @staticmethod
    def _escape_html(s: str) -> str:
        return (s.replace('&', '&amp;')
                 .replace('<', '&lt;')
                 .replace('>', '&gt;'))

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

    # ---- 状态刷新 ----
    def _refresh_stat(self):
        self.stat_label.setText(
            f'事件: {self._event_count}  ·  '
            f'CRASH: {self._crash_count}  ·  '
            f'ANR: {self._anr_count}  ·  '
            f'耗时: {self._elapsed_str()}')

    def _elapsed_str(self) -> str:
        if not self._start_ts:
            return '00:00'
        secs = int(time.time() - self._start_ts)
        return f'{secs // 60:02d}:{secs % 60:02d}'

    def _refresh_elapsed(self):
        self._refresh_stat()

    # ---- 关窗即停 ----
    def closeEvent(self, event):
        self._closed = True
        self._elapsed_timer.stop()
        self._flush_timer.stop()
        self._flush_logs()  # 最终刷新残留缓冲
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
