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

import subprocess
import threading
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QCheckBox, QPushButton,
    QGroupBox, QTextEdit, QSizePolicy,
)

from adb_utils import AdbHelper
from 界面样式 import STYLE_SHEET


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

    def __init__(self, serial, default_pkg='', parent=None):
        super().__init__(parent)
        self._adb = AdbHelper()
        self._serial = serial
        self._default_pkg = default_pkg or ''
        self._proc = None
        self._reader = None
        self._closed = False
        self._running = False
        self._start_ts = 0
        self._event_count = 0
        self._crash_count = 0
        self._anr_count = 0

        self.setWindowTitle(f'Monkey 压力测试 — {serial}')
        self.setMinimumSize(720, 620)
        self.resize(820, 700)
        self.setStyleSheet(STYLE_SHEET)
        self.setWindowFlag(Qt.Window, True)

        self._build_ui()
        if self._default_pkg:
            self.pkg_input.setText(self._default_pkg)

        self._line_arrived.connect(self._append_log)

        # 耗时计时器
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(500)
        self._elapsed_timer.timeout.connect(self._refresh_elapsed)

    # ---- UI 搭建 ----
    def _build_ui(self):
        root = QVBoxLayout(self)
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
        bar.addSpacing(16)
        self.stat_label = QLabel('事件: 0  ·  CRASH: 0  ·  ANR: 0  ·  耗时: 00:00')
        self.stat_label.setStyleSheet('color: #b0b0b0;')
        bar.addWidget(self.stat_label)
        root.addLayout(bar)

        # === 日志输出 ===
        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet(
            'QTextEdit { background: #1a1a1a; color: #d4d4d4; '
            'font: 10pt "Consolas", "微软雅黑"; }')
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

        # 清空日志
        self.log_edit.clear()
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
                creationflags=0x08000000, timeout=10)
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
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception as e:
            self._append_log(f'启动失败: {e}', 'error')
            self._on_finished()
            return

        # 后台线程读输出
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._elapsed_timer.start()

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
                creationflags=0x08000000, timeout=15)
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
                creationflags=0x08000000, timeout=15)
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
        """追加一行日志, kind 控制颜色:
        None=自动检测, info=青色, crash=红色, anr=橙色, done=绿色, error=红色
        """
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
                # 解析事件数
                import re as _re
                m = _re.search(r'events injected:\s*(\d+)', low)
                if m:
                    self._event_count = int(m.group(1))
            elif text.startswith(':Monkey:') or text.startswith('// :Monkey:'):
                kind = 'monkey'
            elif text.startswith('$ ') or text.startswith('----') or text.startswith('[错误]') or text.startswith('[警告]'):
                kind = 'info'

        color_map = {
            'info':   '#56b6c2',
            'crash':  '#ff6b6b',
            'anr':    '#ffab40',
            'done':   '#98c379',
            'error':  '#ff6b6b',
            'monkey': '#c678dd',
        }
        color = color_map.get(kind, '#d4d4d4')

        # 粗略事件计数: :Monkey: 行出现一次算一组事件
        if kind == 'monkey':
            self._event_count += 1

        # 使用 HTML 追加, 避免 QTextCursor 格式在某些主题下不生效
        bold = 'font-weight:bold;' if kind in ('crash', 'done') else ''
        html = f'<span style="color:{color};{bold}">{self._escape_html(text)}</span>'
        self.log_edit.append(html)

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

    # ---- 运行结束 ----
    def _on_finished(self):
        if not self._running:
            return
        self._running = False
        self._elapsed_timer.stop()
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        rc = self._proc.returncode if self._proc else None
        msg = f'运行结束 (returncode={rc})'
        if rc in (0, None):
            self.status_label.setText(msg)
            self.status_label.setStyleSheet('color: #98c379;')
        else:
            self.status_label.setText(msg)
            self.status_label.setStyleSheet('color: #ff6b6b;')
        self._append_log(msg, 'info')
        self._proc = None
        self._reader = None

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
