# -*- coding: utf-8 -*-
"""
设备性能监控 —— 独立窗口
========================
点击 btnDpm 弹出，每 2 秒采样 CPU 使用率与内存占用，
以两条滚动走势图展示，支持暂停/继续，关窗即停止采样。

采样命令：
  CPU  — adb shell top -b -n 1  (失败时回退 top -n 1)
  内存 — adb shell cat /proc/meminfo

注意：不同模拟器/设备的 top 输出格式有差异，
      parse_cpu_percent 已覆盖常见格式，若仍失败可点击
      "复制调试" 按钮获取原始 top 输出以便微调解析逻辑。
"""

import re
import time
import threading
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QBrush, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QApplication,
)

from adb_utils import AdbHelper
from 界面样式 import STYLE_SHEET, FONT_FAMILY
from popup_style import HIGHLIGHT_CARD_STYLE, add_green_glow

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
import png_rc  # noqa: F401

SAMPLE_INTERVAL_MS = 2000   # 采样间隔 2 秒
MAX_POINTS = 120            # 保留最近 120 个点 (4 分钟)

# ANSI 转义序列 (某些 top 即使 -b 也可能带颜色码)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


def _grep_int(text, pattern):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------
# 解析：CPU 使用率
# ------------------------------------------------------------------
def parse_cpu_percent(raw: str):
    """从 top 输出解析总体 CPU 使用率 (%)，无法识别时返回 None。

    覆盖以下格式：
      ① toybox top (Android 8+)  %Cpu(s):  5.0 us,  2.0 sy, ..., 92.0 id
      ② 只含 idle 的 %Cpu(s) 行
      ③ busybox top 合并行      600%cpu  8%user  0%nice  4%sys 588%idle ...
      ④ busybox top 旧格式       CPU: 15% user, 5% kernel, 80% idle
      ⑤ CPU: X%  (直接百分比)
      ⑥ CPU usage: X%
      ⑦ User X%, System Y%  (无 idle 行)
      ⑧ 行首 CPU  XX%
    """
    if not raw:
        return None
    text = _strip_ansi(raw)

    # ① toybox top — 含 us / sy / id
    m = re.search(
        r'%?Cpu\(s\):\s*([\d.]+)\s+us.*?([\d.]+)\s+sy.*?([\d.]+)\s+id',
        text, re.I)
    if m:
        return round(100.0 - float(m.group(3)), 1)

    # ② 只匹配 idle
    m = re.search(r'%?Cpu\(s\):.*?([\d.]+)\s+id', text, re.I)
    if m:
        return round(100.0 - float(m.group(1)), 1)

    # ③ busybox top 合并行 — 600%cpu  8%user  0%nice  4%sys 588%idle ...
    #    多核 total 可能 > 100 (如 600 表示 6 核总容量), 实际使用 = (total - idle) / total * 100
    m = re.search(
        r'(\d+(?:\.\d+)?)%cpu\s+(\d+(?:\.\d+)?)%user\s+(\d+(?:\.\d+)?)%nice\s+(\d+(?:\.\d+)?)%sys\s+(\d+(?:\.\d+)?)%idle',
        text, re.I)
    if m:
        total, idle = float(m.group(1)), float(m.group(5))
        if total > 0:
            return round((total - idle) / total * 100.0, 1)

    # ④ busybox top — CPU: X% user, Y% kernel, Z% idle
    m = re.search(
        r'CPU:\s*(\d+)%\s+user.*?(\d+)%\s+(?:kernel|sys).*?(\d+)%\s+idle',
        text, re.I)
    if m:
        return 100 - int(m.group(3))

    # ④ CPU: X% (直接百分比)
    m = re.search(r'CPU:\s*(\d+(?:\.\d+)?)\s*%', text, re.I)
    if m:
        return float(m.group(1))

    # ⑤ CPU usage: X%
    m = re.search(r'CPU\s*usage:\s*(\d+(?:\.\d+)?)\s*%', text, re.I)
    if m:
        return float(m.group(1))

    # ⑥ User X%, System Y%  (无 idle 行，直接相加)
    m = re.search(
        r'User\s+(\d+(?:\.\d+)?)%.*?System\s+(\d+(?:\.\d+)?)%',
        text, re.I)
    if m:
        return round(float(m.group(1)) + float(m.group(2)), 1)

    # ⑦ 行首 CPU  XX%
    m = re.search(r'^[Cc][Pp][Uu]:?\s*(\d+(?:\.\d+)?)\s*%?\s*$',
                  text, re.MULTILINE)
    if m:
        return float(m.group(1))

    return None


# ------------------------------------------------------------------
# 解析：内存信息
# ------------------------------------------------------------------
def parse_meminfo(raw: str):
    """从 /proc/meminfo 解析内存信息，返回 dict 或 None。

    返回字段: total_kb, used_kb, total_mb, used_mb, pct
    """
    if not raw:
        return None

    total_kb = _grep_int(raw, r'MemTotal:\s*(\d+)')
    if total_kb is None:
        return None
    avail_kb = _grep_int(raw, r'MemAvailable:\s*(\d+)')
    free_kb = _grep_int(raw, r'MemFree:\s*(\d+)')
    cached_kb = _grep_int(raw, r'Cached:\s*(\d+)') or 0

    # 优先 MemAvailable (Android 7+)；否则 Total - Free - Cached
    if avail_kb is not None:
        used_kb = total_kb - avail_kb
    elif free_kb is not None:
        used_kb = total_kb - free_kb - cached_kb
    else:
        used_kb = 0
    used_kb = max(0, used_kb)

    return {
        'total_kb': total_kb,
        'used_kb': used_kb,
        'total_mb': total_kb / 1024,
        'used_mb': used_kb / 1024,
        'pct': round(used_kb / total_kb * 100, 1) if total_kb > 0 else 0.0,
    }


# ------------------------------------------------------------------
# 滚动折线图组件
# ------------------------------------------------------------------
class ScrollChart(QWidget):
    """新数据从右进入、旧数据向左滚出的折线图。

    - values 为 deque(maxlen=MAX_POINTS)，None 代表采样失败的缺口
    - 按 None 分段绘制，自然产生缺口效果
    - 最新一次采样失败时叠加 "获取失败" 文字
    """

    def __init__(self, title, color_hex, unit, y_max=100.0, max_points=None, parent=None):
        super().__init__(parent)
        self._title = title
        self._color = QColor(color_hex)
        self._unit = unit
        self._y_max = float(y_max) if y_max and y_max > 0 else 100.0
        self._max_points = max_points or MAX_POINTS
        self._values = deque(maxlen=self._max_points)
        self._failed = False
        self.setMinimumHeight(130)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        # 缓存绘制对象：paintEvent 每 2s 被调用两次（CPU+内存图），
        # 避免 new QFont/QColor/QPen 的反复分配开销（叠加日志页负载时尤为明显）
        self._bg_color = QColor(43, 43, 43)
        self._chart_bg_color = QColor(31, 31, 31)
        self._grid_pen = QPen(QColor(50, 50, 50), 1)
        self._axis_label_color = QColor(130, 130, 130)
        self._border_pen = QPen(QColor(60, 60, 60), 1)
        self._fail_color = QColor(255, 107, 107)
        self._x_axis_color = QColor(110, 110, 110)
        self._title_font = QFont(FONT_FAMILY, 9, QFont.Bold)
        self._label_font = QFont(FONT_FAMILY, 8)
        self._fail_font = QFont(FONT_FAMILY, 13, QFont.Bold)
        self._fill_color = QColor(self._color)
        self._fill_color.setAlpha(35)
        self._line_pen = QPen(self._color, 2)

    def set_y_max(self, y_max):
        if y_max and y_max > 0:
            self._y_max = float(y_max)
            self.update()

    def set_max_points(self, n):
        """动态修改最大保留点数，保留已有数据。"""
        n = max(10, int(n))
        if n == self._max_points:
            return
        old_vals = list(self._values)
        self._max_points = n
        self._values = deque(old_vals, maxlen=n)
        self.update()

    def add_point(self, value, failed=False):
        self._values.append(None if failed else float(value))
        self._failed = failed
        self.update()

    def clear(self):
        self._values.clear()
        self._failed = False
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 整体背景
        p.fillRect(self.rect(), self._bg_color)

        # 边距: 上 / 下 / 左 / 右
        m_top, m_bottom, m_left, m_right = 24, 20, 50, 14
        cx, cy = m_left, m_top
        cw, ch = w - m_left - m_right, h - m_top - m_bottom
        if cw < 20 or ch < 20:
            p.end()
            return

        # 标题
        p.setFont(self._title_font)
        p.setPen(self._color)
        p.drawText(QRectF(2, 2, w - 4, m_top - 4),
                   Qt.AlignLeft | Qt.AlignVCenter, self._title)

        # 图表区背景
        p.fillRect(QRectF(cx, cy, cw, ch), self._chart_bg_color)

        # 网格 + Y 轴标签
        p.setFont(self._label_font)
        for i in range(5):
            y = cy + ch * i / 4
            p.setPen(self._grid_pen)
            p.drawLine(QPointF(cx, y), QPointF(cx + cw, y))
            val = self._y_max * (1 - i / 4)
            p.setPen(self._axis_label_color)
            p.drawText(QRectF(2, y - 9, m_left - 6, 18),
                       Qt.AlignRight | Qt.AlignVCenter,
                       f'{val:.0f}{self._unit}')

        # 折线 (按 None 分段，制造缺口)
        n = len(self._values)
        spacing = cw / max(self._max_points - 1, 1)
        segments, cur = [], []
        for i, v in enumerate(self._values):
            if v is None:
                if cur:
                    segments.append(cur)
                    cur = []
                continue
            # 右对齐：最新点在右边缘
            x = cx + cw - (n - 1 - i) * spacing
            yv = min(max(v, 0.0), self._y_max)
            y = cy + ch * (1 - yv / self._y_max)
            cur.append((x, y))
        if cur:
            segments.append(cur)

        for seg in segments:
            if len(seg) >= 2:
                # 填充区域
                fp = QPainterPath()
                fp.moveTo(QPointF(seg[0][0], cy + ch))
                for x, y in seg:
                    fp.lineTo(QPointF(x, y))
                fp.lineTo(QPointF(seg[-1][0], cy + ch))
                fp.closeSubpath()
                p.setBrush(QBrush(self._fill_color))
                p.setPen(Qt.NoPen)
                p.drawPath(fp)
                # 折线
                p.setPen(self._line_pen)
                p.setBrush(Qt.NoBrush)
                for j in range(len(seg) - 1):
                    p.drawLine(QPointF(seg[j][0], seg[j][1]),
                               QPointF(seg[j + 1][0], seg[j + 1][1]))
            if seg:
                # 末点圆点
                p.setBrush(self._color)
                p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(seg[-1][0], seg[-1][1]), 3.5, 3.5)

        # 边框
        p.setPen(self._border_pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx, cy, cw, ch))

        # 失败提示
        if self._failed:
            p.setPen(self._fail_color)
            p.setFont(self._fail_font)
            p.drawText(QRectF(cx, cy, cw, ch), Qt.AlignCenter, '获取失败')

        # X 轴说明
        p.setPen(self._x_axis_color)
        p.setFont(self._label_font)
        p.drawText(QRectF(cx, cy + ch + 2, cw, m_bottom - 4),
                   Qt.AlignCenter,
                   f'最近 {n}/{self._max_points} 点 · 每 {SAMPLE_INTERVAL_MS // 1000}s 采样')

        p.end()


# ------------------------------------------------------------------
# 监控窗口
# ------------------------------------------------------------------
class DevicePerfMonitor(QWidget):
    """设备性能监控独立窗口。

    用法：
        win = DevicePerfMonitor(serial, parent=main_window)
        win.show()
    """

    _sample_done = Signal(object)

    def __init__(self, serial, parent=None):
        super().__init__(parent)
        self._adb = AdbHelper()
        self._serial = serial
        self._paused = False
        self._sampling = False
        self._closed = False
        self._mem_total_mb = None
        self._last_cpu_raw = ''
        self._cpu_fail_count = 0

        self.setWindowTitle(f'设备性能监控 — {serial}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(700, 500)
        self.resize(760, 560)
        self.setStyleSheet(STYLE_SHEET)
        self.setWindowFlag(Qt.Window, True)

        # 卡片容器：绿色高亮边框 + 发光
        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        self.card.setStyleSheet(HIGHLIGHT_CARD_STYLE)
        add_green_glow(self.card)

        self._build_ui()

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._sample_done.connect(self._on_sample)

        # 立即开始采样
        self._timer.start()
        self._tick()

    # ---- UI 搭建 ----
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        # 顶部信息栏
        top = QHBoxLayout()
        top.setSpacing(12)
        self._info_label = QLabel('采样中…')
        self._info_label.setStyleSheet(
            f'font: 11pt "{FONT_FAMILY}"; color: #1de9b6; background: transparent;')
        top.addWidget(self._info_label)
        top.addStretch(1)
        self._btn_pause = QPushButton('暂停')
        self._btn_pause.setFixedWidth(80)
        self._btn_pause.clicked.connect(self._toggle_pause)
        top.addWidget(self._btn_pause)
        self._btn_copy = QPushButton('复制调试')
        self._btn_copy.setFixedWidth(90)
        self._btn_copy.clicked.connect(self._copy_debug)
        top.addWidget(self._btn_copy)
        lay.addLayout(top)

        # 两张走势图
        self._cpu_chart = ScrollChart('CPU 使用率', '#1de9b6', '%', 100.0)
        lay.addWidget(self._cpu_chart, 1)

        self._mem_chart = ScrollChart('内存占用', '#ffab40', 'MB', 2048.0)
        lay.addWidget(self._mem_chart, 1)

        # 调试信息 (仅 CPU 失败时显示)
        self._debug_label = QLabel('')
        self._debug_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #ff6b6b; background: transparent;')
        self._debug_label.setWordWrap(True)
        self._debug_label.setVisible(False)
        lay.addWidget(self._debug_label)

    # ---- 采样调度 ----
    def _tick(self):
        """定时器回调：启动后台采样线程 (防止重叠)。"""
        if self._closed or self._paused or self._sampling:
            return
        self._sampling = True
        threading.Thread(target=self._sample_task, daemon=True).start()

    def _sample_task(self):
        """后台线程：执行 adb 命令获取 CPU 与内存数据。"""
        cpu_pct = None
        cpu_raw = ''
        mem_pct = None
        mem_used_mb = None
        mem_total_mb = None

        # ---- CPU: top -b -n 1, 失败回退 top -n 1 ----
        try:
            cpu_raw = self._adb.run_shell(
                self._serial, 'top -b -n 1', timeout=10)
        except Exception:
            try:
                cpu_raw = self._adb.run_shell(
                    self._serial, 'top -n 1', timeout=10)
            except Exception as e:
                cpu_raw = f'执行异常: {e}'

        if cpu_raw and not cpu_raw.startswith('执行异常'):
            cpu_pct = parse_cpu_percent(cpu_raw)

        # ---- 内存: cat /proc/meminfo ----
        try:
            mem_raw = self._adb.run_shell(
                self._serial, 'cat /proc/meminfo', timeout=5)
            mi = parse_meminfo(mem_raw)
            if mi:
                mem_pct = mi['pct']
                mem_used_mb = mi['used_mb']
                mem_total_mb = mi['total_mb']
        except Exception:
            pass

        if not self._closed:
            self._sample_done.emit({
                'ts': time.strftime('%H:%M:%S'),
                'cpu_pct': cpu_pct,
                'cpu_raw': cpu_raw,
                'mem_pct': mem_pct,
                'mem_used_mb': mem_used_mb,
                'mem_total_mb': mem_total_mb,
            })

    # ---- 结果处理 (主线程) ----
    def _on_sample(self, data):
        if self._closed:
            return
        self._sampling = False

        ts = data['ts']
        cpu_pct = data['cpu_pct']
        cpu_raw = data['cpu_raw']
        mem_pct = data['mem_pct']
        mem_used_mb = data['mem_used_mb']
        mem_total_mb = data['mem_total_mb']

        self._last_cpu_raw = cpu_raw

        # 首次获取到总内存时，设定内存图 Y 轴范围
        if mem_total_mb and not self._mem_total_mb:
            self._mem_total_mb = mem_total_mb
            self._mem_chart.set_y_max(mem_total_mb)

        # CPU 图
        if cpu_pct is not None:
            self._cpu_chart.add_point(cpu_pct, failed=False)
            self._cpu_fail_count = 0
        else:
            self._cpu_chart.add_point(0, failed=True)
            self._cpu_fail_count += 1

        # 内存图
        if mem_used_mb is not None:
            self._mem_chart.add_point(mem_used_mb, failed=(mem_pct is None))
        else:
            self._mem_chart.add_point(0, failed=True)

        # 顶部信息栏
        cpu_str = f'{cpu_pct:.1f}%' if cpu_pct is not None else '获取失败'
        if mem_total_mb and mem_pct is not None:
            mem_str = f'{mem_pct:.1f}% ({mem_used_mb:.0f}/{mem_total_mb:.0f} MB)'
        elif mem_pct is not None:
            mem_str = f'{mem_pct:.1f}% ({mem_used_mb:.0f} MB)'
        else:
            mem_str = '获取失败'
        self._info_label.setText(
            f'采样时间: {ts}    CPU: {cpu_str}    内存: {mem_str}')

        # 调试信息: CPU 解析失败时显示原始 top 前 5 行
        if cpu_pct is None:
            lines = [l.strip() for l in cpu_raw.strip().splitlines()
                     if l.strip()]
            preview = ' | '.join(lines[:5])
            if len(preview) > 280:
                preview = preview[:280] + '...'
            self._debug_label.setText(
                f'CPU 解析失败 (第 {self._cpu_fail_count} 次) '
                f'— top 前 5 行: {preview}')
            self._debug_label.setVisible(True)
        else:
            self._debug_label.setVisible(False)

    # ---- 暂停/继续 ----
    def _toggle_pause(self):
        self._paused = not self._paused
        self._btn_pause.setText('继续' if self._paused else '暂停')
        if self._paused:
            self._timer.stop()
        else:
            self._timer.start()
            self._tick()

    # ---- 复制调试信息 ----
    def _copy_debug(self):
        text = self._last_cpu_raw or '(无数据)'
        QApplication.clipboard().setText(text)
        old = self._debug_label.text()
        self._debug_label.setText('已复制 top 原始输出到剪贴板，可粘贴发送')
        self._debug_label.setVisible(True)
        QTimer.singleShot(3000, lambda: (
            self._debug_label.setText(old),
            self._debug_label.setVisible(bool(old)),
        ))

    # ---- 关窗即停止 ----
    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        super().closeEvent(event)
