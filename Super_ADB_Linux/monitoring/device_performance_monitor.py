# -*- coding: utf-8 -*-
"""
设备性能监控 —— 独立窗口
========================
点击 btnDpm 弹出，每 2 秒采样多项系统健康度指标，以多序列滚动走势图展示：

  · CPU 使用率(%)      —— top 的「总 CPU」+ 每核 %Cpu0/%Cpu1... 折线（发现单核跑满）
  · 内存占用(MB)        —— /proc/meminfo 已用内存
  · 网络速率(KB/s)      —— /proc/net/dev 接收/发送速率（系统级健康度）
  · 电池温度(°C)        —— dumpsys battery 的 temperature（橙色曲线）

支持：暂停/继续、点数可配置（长按监控）、一键导出 HTML 报告。

采样命令：
  CPU     — adb shell top -b -n 1  (失败时回退 top -n 1)
  内存    — adb shell cat /proc/meminfo
  网络    — adb shell cat /proc/net/dev
  电池    — adb shell dumpsys battery
"""

import re
import os
import json
import time
import threading
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QBrush, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QSizePolicy, QFileDialog, QApplication,
)

from tools.adb_tools import AdbHelper
from ui.ui_styles import (
    STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_current_theme_id,
    THEMES, DEFAULT_THEME, _parse_rgb,
)
from ui.dialog_styles import add_green_glow
from tools.chart_js import load_chart_js

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from ui import png_rc  # noqa: F401

SAMPLE_INTERVAL_MS = 2000   # 采样间隔 2 秒
DEFAULT_MAX_POINTS = 120    # 默认保留最近 120 个点 (4 分钟)
MIN_MAX_POINTS = 30
MAX_MAX_POINTS = 3600

# ANSI 转义序列 (某些 top 即使 -b 也可能带颜色码)
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)


def _grep_int(text, pattern):
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------
# 解析：CPU 使用率（总）
# ------------------------------------------------------------------
def parse_cpu_percent(raw: str):
    """从 top 输出解析总体 CPU 使用率 (%)，无法识别时返回 None。"""
    if not raw:
        return None
    text = _strip_ansi(raw)

    m = re.search(
        r'%?Cpu\(s\):\s*([\d.]+)\s+us.*?([\d.]+)\s+sy.*?([\d.]+)\s+id',
        text, re.I)
    if m:
        return round(100.0 - float(m.group(3)), 1)

    m = re.search(r'%?Cpu\(s\):\s*.*?([\d.]+)%?\s*id', text, re.I)
    if m:
        return round(100.0 - float(m.group(1)), 1)

    m = re.search(
        r'(\d+(?:\.\d+)?)%cpu\s+(\d+(?:\.\d+)?)%user\s+(\d+(?:\.\d+)?)%nice\s+(\d+(?:\.\d+)?)%sys\s+(\d+(?:\.\d+)?)%idle',
        text, re.I)
    if m:
        total, idle = float(m.group(1)), float(m.group(5))
        if total > 0:
            return round((total - idle) / total * 100.0, 1)

    m = re.search(
        r'CPU:\s*(\d+)%\s+user.*?(\d+)%\s+(?:kernel|sys).*?(\d+)%\s+idle',
        text, re.I)
    if m:
        return 100 - int(m.group(3))

    m = re.search(r'CPU:\s*(\d+(?:\.\d+)?)\s*%', text, re.I)
    if m:
        return float(m.group(1))

    m = re.search(r'CPU\s*usage:\s*(\d+(?:\.\d+)?)\s*%', text, re.I)
    if m:
        return float(m.group(1))

    m = re.search(
        r'User\s+(\d+(?:\.\d+)?)%.*?System\s+(\d+(?:\.\d+)?)%',
        text, re.I)
    if m:
        return round(float(m.group(1)) + float(m.group(2)), 1)

    m = re.search(r'^[Cc][Pp][Uu]:?\s*(\d+(?:\.\d+)?)\s*%?\s*$',
                  text, re.MULTILINE)
    if m:
        return float(m.group(1))

    return None


# ------------------------------------------------------------------
# 解析：每核 CPU 使用率（%Cpu0 / %Cpu1 ...）
# ------------------------------------------------------------------
def parse_per_core_cpu(raw: str):
    """从 top 输出解析每核 CPU 使用率，返回 {核序号: 使用率%}。

    覆盖 toybox top 格式:  %Cpu0  :  3.0% user, ... 96.0% idle
    使用率 = 100 - idle。无每核行时返回空 dict（老 busybox 设备等）。
    """
    if not raw:
        return {}
    text = _strip_ansi(raw)
    cores = {}
    for m in re.finditer(
            r'^%Cpu(\d+)\s*:\s*[\d.]+%\s*user.*?([\d.]+)%\s*idle',
            text, re.MULTILINE | re.IGNORECASE):
        try:
            idx = int(m.group(1))
            idle = float(m.group(2))
            cores[idx] = round(100.0 - idle, 1)
        except Exception:
            pass
    return cores


# ------------------------------------------------------------------
# 解析：内存信息
# ------------------------------------------------------------------
def parse_meminfo(raw: str):
    """从 /proc/meminfo 解析内存信息，返回 dict 或 None。"""
    if not raw:
        return None

    total_kb = _grep_int(raw, r'MemTotal:\s*(\d+)')
    if total_kb is None:
        return None
    avail_kb = _grep_int(raw, r'MemAvailable:\s*(\d+)')
    free_kb = _grep_int(raw, r'MemFree:\s*(\d+)')
    cached_kb = _grep_int(raw, r'Cached:\s*(\d+)') or 0

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
# 解析：网络收发字节数 (/proc/net/dev)
# ------------------------------------------------------------------
def parse_net_dev(raw: str):
    """从 /proc/net/dev 解析累计收发字节数，返回 (rx_bytes, tx_bytes) 或 None。

    跳过回环 lo，汇总其余所有网卡的 Receive/Transmit 字节。
    """
    if not raw:
        return None
    rx = tx = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith('Inter') or line.startswith('face'):
            continue
        parts = line.split(':')
        if len(parts) < 2:
            continue
        iface = parts[0].strip()
        if iface == 'lo':
            continue
        nums = parts[1].split()
        if len(nums) < 9:
            continue
        try:
            rx += int(nums[0])   # Receive bytes
            tx += int(nums[8])   # Transmit bytes
        except Exception:
            pass
    return (rx, tx) if (rx or tx) else None


# ------------------------------------------------------------------
# 解析：电池温度 (dumpsys battery)
# ------------------------------------------------------------------
def parse_battery_temp(raw: str):
    """从 dumpsys battery 解析温度(°C)，原始值为 0.1°C，返回 None 表示无数据。"""
    if not raw:
        return None
    m = re.search(r'temperature:\s*(\d+)', raw)
    if m:
        return round(int(m.group(1)) / 10.0, 1)
    return None


# ------------------------------------------------------------------
# 多序列滚动折线图组件
# ------------------------------------------------------------------
class ScrollChart(QWidget):
    """新数据从右进入、旧数据向左滚出的多序列折线图。

    - series: 有序 {name: {'color': QColor, 'values': deque, 'failed': bool}}
    - 按 None 分段绘制，自然产生缺口效果
    - auto_grow: 当数值超过当前 Y 轴上限且开启时自动抬高上限
    """

    def __init__(self, title, series_specs, unit, y_max=100.0,
                 max_points=None, auto_grow=False, parent=None):
        super().__init__(parent)
        self._title = title
        self._unit = unit
        self._y_max = float(y_max) if y_max and y_max > 0 else 100.0
        self._max_points = max_points or DEFAULT_MAX_POINTS
        self._auto_grow = auto_grow
        self._series = {}
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        # 缓存绘制对象
        self._bg_color = QColor(43, 43, 43)
        self._chart_bg_color = QColor(31, 31, 31)
        self._grid_pen = QPen(QColor(50, 50, 50), 1)
        self._axis_label_color = QColor(130, 130, 130)
        self._border_pen = QPen(QColor(60, 60, 60), 1)
        self._fail_color = QColor(255, 107, 107)
        self._legend_font = QFont(FONT_FAMILY, 8)
        self._x_axis_color = QColor(110, 110, 110)
        self._title_font = QFont(FONT_FAMILY, 9, QFont.Bold)
        self._label_font = QFont(FONT_FAMILY, 8)
        self._fail_font = QFont(FONT_FAMILY, 13, QFont.Bold)

        if series_specs:
            self.set_series(series_specs)

    def set_series(self, specs):
        """specs: list of (name, color_hex)。重建序列（清空数据）。"""
        self._series = {}
        for name, color_hex in specs:
            self.add_series(name, color_hex)

    def add_series(self, name, color_hex):
        if name in self._series:
            return
        self._series[name] = {
            'color': QColor(color_hex),
            'values': deque(maxlen=self._max_points),
            'failed': False,
            'line_pen': QPen(QColor(color_hex), 2),
            'fill_color': None,
        }
        self._series[name]['line_pen'].setWidth(2)
        self.update()

    def set_y_max(self, y_max):
        if y_max and y_max > 0:
            self._y_max = float(y_max)
            self.update()

    def set_max_points(self, n):
        n = max(MIN_MAX_POINTS, int(n))
        if n == self._max_points:
            return
        self._max_points = n
        for s in self._series.values():
            old = list(s['values'])
            s['values'] = deque(old, maxlen=n)

    def add_point(self, name, value, failed=False):
        s = self._series.get(name)
        if s is None:
            return
        s['values'].append(None if failed else float(value))
        s['failed'] = failed
        if not failed and self._auto_grow and value > self._y_max:
            self._y_max = float(value) * 1.25
        self.update()

    def clear(self):
        for s in self._series.values():
            s['values'].clear()
            s['failed'] = False
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        p.fillRect(self.rect(), self._bg_color)

        m_top, m_bottom, m_left, m_right = 22, 18, 46, 14
        cx, cy = m_left, m_top
        cw, ch = w - m_left - m_right, h - m_top - m_bottom
        if cw < 20 or ch < 20:
            p.end()
            return

        # 标题（左）
        p.setFont(self._title_font)
        p.setPen(self._series and list(self._series.values())[0]['color']
                 or QColor('#1de9b6'))
        p.drawText(QRectF(2, 2, w - 4, m_top - 4),
                   Qt.AlignLeft | Qt.AlignVCenter, self._title)

        # 图例（右，序列 <=4 时绘制名称）
        legend_names = list(self._series.keys())
        if 1 <= len(legend_names) <= 4:
            p.setFont(self._legend_font)
            right_x = w - 6
            for name in reversed(legend_names):
                c = self._series[name]['color']
                tw = p.fontMetrics().horizontalAdvance(name)
                bx = right_x - tw - 14
                p.setPen(c)
                p.setBrush(c)
                p.drawRect(QRectF(bx, 6, 9, 9))
                p.setPen(self._axis_label_color)
                p.drawText(QRectF(bx + 12, 0, tw, m_top),
                           Qt.AlignLeft | Qt.AlignVCenter, name)
                right_x = bx - 8

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

        max_points = self._max_points
        for s in self._series.values():
            values = s['values']
            n = len(values)
            spacing = cw / max(max_points - 1, 1)
            segments, cur = [], []
            for i, v in enumerate(values):
                if v is None:
                    if cur:
                        segments.append(cur)
                        cur = []
                    continue
                x = cx + cw - (n - 1 - i) * spacing
                yv = min(max(v, 0.0), self._y_max)
                y = cy + ch * (1 - yv / self._y_max)
                cur.append((x, y))
            if cur:
                segments.append(cur)

            color = s['color']
            for seg in segments:
                if len(seg) >= 2:
                    fp = QPainterPath()
                    fp.moveTo(QPointF(seg[0][0], cy + ch))
                    for x, y in seg:
                        fp.lineTo(QPointF(x, y))
                    fp.lineTo(QPointF(seg[-1][0], cy + ch))
                    fp.closeSubpath()
                    fc = QColor(color)
                    fc.setAlpha(22)
                    p.setBrush(QBrush(fc))
                    p.setPen(Qt.NoPen)
                    p.drawPath(fp)
                    p.setPen(s['line_pen'])
                    p.setBrush(Qt.NoBrush)
                    for j in range(len(seg) - 1):
                        p.drawLine(QPointF(seg[j][0], seg[j][1]),
                                   QPointF(seg[j + 1][0], seg[j + 1][1]))
                if seg:
                    p.setBrush(color)
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QPointF(seg[-1][0], seg[-1][1]), 3, 3)

        # 边框
        p.setPen(self._border_pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(cx, cy, cw, ch))

        # 任意序列失败提示
        if any(s['failed'] for s in self._series.values()):
            p.setPen(self._fail_color)
            p.setFont(self._fail_font)
            p.drawText(QRectF(cx, cy, cw, ch), Qt.AlignCenter, '获取失败')

        # X 轴说明
        p.setPen(self._x_axis_color)
        p.setFont(self._label_font)
        p.drawText(QRectF(cx, cy + ch + 2, cw, m_bottom - 4),
                   Qt.AlignCenter,
                   f'最近 {n}/{max_points} 点 · 每 {SAMPLE_INTERVAL_MS // 1000}s 采样')

        p.end()


# ------------------------------------------------------------------
# 监控窗口
# ------------------------------------------------------------------
class 设备性能监控(QWidget):
    """设备性能监控独立窗口。"""

    _sample_done = Signal(object)
    _export_ready = Signal(str)

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
        self._max_points = DEFAULT_MAX_POINTS
        self._last_net = None   # (ts, rx_bytes, tx_bytes)

        # 历史数据（用于导出 HTML）
        self._hist = {
            'ts': deque(maxlen=self._max_points),
            'cpu_total': deque(maxlen=self._max_points),
            'cpu_cores': {},            # idx -> deque
            'mem': deque(maxlen=self._max_points),
            'net_rx': deque(maxlen=self._max_points),
            'net_tx': deque(maxlen=self._max_points),
            'batt': deque(maxlen=self._max_points),
        }

        self.setWindowTitle(f'设备性能监控 — {serial}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(760, 640)
        self.resize(820, 720)
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

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._sample_done.connect(self._on_sample)
        self._export_ready.connect(self._on_export_ready)

        self._timer.start()
        self._tick()

    # ---- 主题支持 ----
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
        text_primary = t['text_primary']
        text_disabled = t['text_disabled']
        text_pressed = t['text_pressed']
        border_disabled = t.get('border_disabled', text_disabled)
        return (
            f'QWidget{{background: {bg_window}; color: {text_primary}; '
            f'font: 10pt "{FONT_FAMILY}";}}'
            f'QLabel{{background: transparent; color: {text_primary};}}'
            f'QLabel#infoLabel{{color: {accent}; font: 11pt "{FONT_FAMILY}";}}'
            f'QLabel#debugLabel{{color: #ff6b6b; font: 9pt "{FONT_FAMILY}";}}'
            f'QPushButton{{background: {bg_button}; color: {accent}; '
            f'border: 1px solid {accent}; border-radius: 6px; padding: 6px 14px; '
            f'font: 9pt "{FONT_FAMILY}";}}'
            f'QPushButton:hover{{background: {accent}; color: {text_pressed};}}'
            f'QPushButton:pressed{{background: rgba({ar},{ag},{ab},180); color: {text_pressed};}}'
            f'QPushButton:disabled{{color: {text_disabled}; border: 1px solid {border_disabled}; '
            f'background: {bg_window};}}'
            f'QSpinBox{{background: {bg_input}; color: {text_primary}; '
            f'border: 1px solid {bg_button}; border-radius: 4px; padding: 4px;}}'
            f'QSpinBox::up-button, QSpinBox::down-button{{background: {bg_button};}}'
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

    # ---- UI 搭建 ----
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        # 顶部信息栏
        top = QHBoxLayout()
        top.setSpacing(10)
        self._info_label = QLabel('采样中…')
        self._info_label.setObjectName('infoLabel')
        top.addWidget(self._info_label)
        top.addStretch(1)

        # 点数配置
        top.addWidget(QLabel('保留点数:'))
        self._points_spin = QSpinBox()
        self._points_spin.setRange(MIN_MAX_POINTS, MAX_MAX_POINTS)
        self._points_spin.setSingleStep(30)
        self._points_spin.setValue(self._max_points)
        self._points_spin.setToolTip('保留的采样点数量，越大监控时间越长')
        self._points_spin.valueChanged.connect(self._on_points_changed)
        top.addWidget(self._points_spin)

        self._btn_pause = QPushButton('暂停')
        self._btn_pause.setFixedWidth(80)
        self._btn_pause.clicked.connect(self._toggle_pause)
        top.addWidget(self._btn_pause)

        self._btn_export = QPushButton('导出 HTML')
        self._btn_export.setFixedWidth(100)
        self._btn_export.clicked.connect(self._export_html)
        top.addWidget(self._btn_export)

        self._btn_copy = QPushButton('复制调试')
        self._btn_copy.setFixedWidth(90)
        self._btn_copy.clicked.connect(self._copy_debug)
        top.addWidget(self._btn_copy)
        lay.addLayout(top)

        # 四张走势图
        self._cpu_chart = ScrollChart(
            'CPU 使用率 (%)', [('总CPU', '#1de9b6')], '%', 100.0)
        lay.addWidget(self._cpu_chart, 1)
        self._cpu_stats = self._make_stats_label()
        lay.addWidget(self._cpu_stats)

        self._mem_chart = ScrollChart(
            '内存占用 (MB)', [('内存', '#ffab40')], 'MB', 2048.0)
        lay.addWidget(self._mem_chart, 1)
        self._mem_stats = self._make_stats_label()
        lay.addWidget(self._mem_stats)

        self._net_chart = ScrollChart(
            '网络速率 (KB/s)', [('↓接收', '#4fc3f7'), ('↑发送', '#80deea')],
            'KB', 1024.0, auto_grow=True)
        lay.addWidget(self._net_chart, 1)
        self._net_stats = self._make_stats_label()
        lay.addWidget(self._net_stats)

        self._batt_chart = ScrollChart(
            '电池温度 (°C)', [('温度', '#ff8a65')], '°C', 60.0,
            auto_grow=True)
        lay.addWidget(self._batt_chart, 1)
        self._batt_stats = self._make_stats_label()
        lay.addWidget(self._batt_stats)

        # 调试信息
        self._debug_label = QLabel('')
        self._debug_label.setObjectName('debugLabel')
        self._debug_label.setWordWrap(True)
        self._debug_label.setVisible(False)
        lay.addWidget(self._debug_label)

    # ---- 采样调度 ----
    def _tick(self):
        if self._closed or self._paused or self._sampling:
            return
        self._sampling = True
        threading.Thread(target=self._sample_task, daemon=True).start()

    def _sample_task(self):
        cpu_pct = None
        cpu_raw = ''
        per_core = {}
        mem_pct = None
        mem_used_mb = None
        mem_total_mb = None
        net_rx_kbps = None
        net_tx_kbps = None
        batt_temp = None

        now = time.time()

        # ---- CPU: top -b -n 1 ----
        try:
            cpu_raw = self._adb.执行shell(
                self._serial, 'top -b -n 1', timeout=10)
        except Exception:
            try:
                cpu_raw = self._adb.执行shell(
                    self._serial, 'top -n 1', timeout=10)
            except Exception as e:
                cpu_raw = f'执行异常: {e}'
        if cpu_raw and not cpu_raw.startswith('执行异常'):
            cpu_pct = parse_cpu_percent(cpu_raw)
            per_core = parse_per_core_cpu(cpu_raw)

        # ---- 内存: cat /proc/meminfo ----
        try:
            mem_raw = self._adb.执行shell(
                self._serial, 'cat /proc/meminfo', timeout=5)
            mi = parse_meminfo(mem_raw)
            if mi:
                mem_pct = mi['pct']
                mem_used_mb = mi['used_mb']
                mem_total_mb = mi['total_mb']
        except Exception:
            pass

        # ---- 网络: cat /proc/net/dev ----
        try:
            net_raw = self._adb.执行shell(
                self._serial, 'cat /proc/net/dev', timeout=5)
            nt = parse_net_dev(net_raw)
            if nt and self._last_net is not None:
                prev_ts, prev_rx, prev_tx = self._last_net
                dt = now - prev_ts
                if dt > 0.1:
                    net_rx_kbps = round((nt[0] - prev_rx) / 1024.0 / dt, 1)
                    net_tx_kbps = round((nt[1] - prev_tx) / 1024.0 / dt, 1)
                    if net_rx_kbps < 0:
                        net_rx_kbps = 0.0
                    if net_tx_kbps < 0:
                        net_tx_kbps = 0.0
            if nt:
                self._last_net = (now, nt[0], nt[1])
        except Exception:
            pass

        # ---- 电池: dumpsys battery ----
        try:
            batt_raw = self._adb.执行shell(
                self._serial, 'dumpsys battery', timeout=5)
            batt_temp = parse_battery_temp(batt_raw)
        except Exception:
            pass

        if not self._closed:
            self._sample_done.emit({
                'ts': time.strftime('%H:%M:%S'),
                'cpu_pct': cpu_pct,
                'cpu_raw': cpu_raw,
                'per_core': per_core,
                'mem_pct': mem_pct,
                'mem_used_mb': mem_used_mb,
                'mem_total_mb': mem_total_mb,
                'net_rx_kbps': net_rx_kbps,
                'net_tx_kbps': net_tx_kbps,
                'batt_temp': batt_temp,
            })

    # ---- 结果处理 (主线程) ----
    def _on_sample(self, data):
        if self._closed:
            return
        self._sampling = False

        ts = data['ts']
        cpu_pct = data['cpu_pct']
        cpu_raw = data['cpu_raw']
        per_core = data['per_core'] or {}
        mem_pct = data['mem_pct']
        mem_used_mb = data['mem_used_mb']
        mem_total_mb = data['mem_total_mb']
        net_rx_kbps = data['net_rx_kbps']
        net_tx_kbps = data['net_tx_kbps']
        batt_temp = data['batt_temp']

        self._last_cpu_raw = cpu_raw

        # 首次拿到总内存时设定内存图 Y 轴
        if mem_total_mb and not self._mem_total_mb:
            self._mem_total_mb = mem_total_mb
            self._mem_chart.set_y_max(mem_total_mb)

        # ---- CPU 图（总 + 每核）----
        if cpu_pct is not None:
            self._cpu_chart.add_point('总CPU', cpu_pct, failed=False)
            self._cpu_fail_count = 0
        else:
            self._cpu_chart.add_point('总CPU', 0, failed=True)
            self._cpu_fail_count += 1

        if per_core:
            n = max(per_core.keys()) + 1
            for idx in range(n):
                name = f'Cpu{idx}'
                if name not in self._cpu_chart._series:
                    # 用 HSV 生成区分度高的颜色（避开总 CPU 的绿色）
                    self._cpu_chart.add_series(name, _core_hex(idx, n))
                val = per_core.get(idx)
                if val is not None:
                    self._cpu_chart.add_point(name, val, failed=False)

        # ---- 内存图 ----
        if mem_used_mb is not None:
            self._mem_chart.add_point('内存', mem_used_mb, failed=(mem_pct is None))
        else:
            self._mem_chart.add_point('内存', 0, failed=True)

        # ---- 网络图 ----
        if net_rx_kbps is not None:
            self._net_chart.add_point('↓接收', net_rx_kbps, failed=False)
            self._net_chart.add_point('↑发送', net_tx_kbps or 0, failed=False)
        else:
            # 首帧或采样失败：不画点（保持缺口，待第二帧出速率）
            pass

        # ---- 电池图 ----
        if batt_temp is not None:
            self._batt_chart.add_point('温度', batt_temp, failed=False)
        else:
            self._batt_chart.add_point('温度', 0, failed=True)

        # ---- 历史（导出用）----
        self._hist['ts'].append(ts)
        self._hist['cpu_total'].append(cpu_pct)
        for idx, val in per_core.items():
            dq = self._hist['cpu_cores'].setdefault(
                idx, deque(maxlen=self._max_points))
            dq.append(val)
        self._hist['mem'].append(mem_used_mb)
        self._hist['net_rx'].append(net_rx_kbps)
        self._hist['net_tx'].append(net_tx_kbps)
        self._hist['batt'].append(batt_temp)

        # ---- 各图 最高/平均/最低 统计 ----
        self._update_stats()

        # ---- 顶部信息栏 ----
        cpu_str = f'{cpu_pct:.1f}%' if cpu_pct is not None else '获取失败'
        if mem_total_mb and mem_pct is not None:
            mem_str = f'{mem_pct:.1f}% ({mem_used_mb:.0f}/{mem_total_mb:.0f} MB)'
        elif mem_pct is not None:
            mem_str = f'{mem_pct:.1f}% ({mem_used_mb:.0f} MB)'
        else:
            mem_str = '获取失败'
        net_str = (f'↓{net_rx_kbps:.0f} ↑{net_tx_kbps:.0f} KB/s'
                   if net_rx_kbps is not None else '网络: 采集中')
        batt_str = f'{batt_temp:.1f}°C' if batt_temp is not None else '电池: 获取失败'
        core_str = f' · 核数 {len(per_core)}' if per_core else ''
        self._info_label.setText(
            f'{ts}  CPU {cpu_str}{core_str}  内存 {mem_str}  '
            f'{net_str}  电池 {batt_str}')

        # 调试信息: CPU 解析失败
        if cpu_pct is None:
            lines = [l.strip() for l in cpu_raw.strip().splitlines() if l.strip()]
            preview = ' | '.join(lines[:5])
            if len(preview) > 280:
                preview = preview[:280] + '...'
            self._debug_label.setText(
                f'CPU 解析失败 (第 {self._cpu_fail_count} 次) '
                f'— top 前 5 行: {preview}')
            self._debug_label.setVisible(True)
        else:
            self._debug_label.setVisible(False)

    # ---- 点数配置 ----
    def _on_points_changed(self, n):
        self._max_points = n
        for c in (self._cpu_chart, self._mem_chart, self._net_chart,
                  self._batt_chart):
            c.set_max_points(n)
        # 历史队列同步 resize
        for k in ('ts', 'cpu_total', 'mem', 'net_rx', 'net_tx', 'batt'):
            old = list(self._hist[k])
            self._hist[k] = deque(old, maxlen=n)
        for idx in list(self._hist['cpu_cores'].keys()):
            dq = self._hist['cpu_cores'][idx]
            self._hist['cpu_cores'][idx] = deque(list(dq), maxlen=n)

    # ---- 各图「最高/平均/最低」统计标签（与应用监控一致）----
    def _make_stats_label(self):
        lbl = QLabel('  最高值: --     平均值: --     最低值: --')
        lbl.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #c8c8c8; '
            f'background: transparent; padding: 0 2px 2px;')
        return lbl

    @staticmethod
    def _stats_text(values, unit):
        vals = [v for v in values if v is not None]
        if not vals:
            return '  最高值: --     平均值: --     最低值: --'
        hi, lo = max(vals), min(vals)
        avg = sum(vals) / len(vals)
        return (f'  最高值: {hi:.1f}{unit}     '
                f'平均值: {avg:.1f}{unit}     '
                f'最低值: {lo:.1f}{unit}')

    def _update_stats(self):
        s = self._cpu_chart._series.get('总CPU')
        if s:
            self._cpu_stats.setText(self._stats_text(s['values'], '%'))
        s = self._mem_chart._series.get('内存')
        if s:
            self._mem_stats.setText(self._stats_text(s['values'], 'MB'))
        s = self._net_chart._series.get('↓接收')
        if s:
            self._net_stats.setText(self._stats_text(s['values'], 'KB/s'))
        s = self._batt_chart._series.get('温度')
        if s:
            self._batt_stats.setText(self._stats_text(s['values'], '°C'))

    @staticmethod
    def _ds_stats(dq, unit):
        vals = [v for v in dq if v is not None]
        if not vals:
            return {'max': None, 'avg': None, 'min': None}
        return {
            'max': round(max(vals), 1),
            'avg': round(sum(vals) / len(vals), 1),
            'min': round(min(vals), 1),
        }

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

    # ---- 导出 HTML ----
    def _export_html(self):
        if not self._hist['ts']:
            self._info_label.setText('暂无数据，先采样一会儿再导出')
            return
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        save_dir = os.path.join(desktop, 'Super_ADB')
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as e:
            self._info_label.setText(f'无法创建目录: {e}')
            return
        ts = time.strftime('%Y%m%d_%H%M%S')
        safe_serial = (self._serial or 'device').replace(':', '_').replace('/', '_')
        filename = f'perf_device_{safe_serial}_{ts}.html'
        path = os.path.join(save_dir, filename)

        html = self._build_html_report()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(html)
        except Exception as e:
            self._info_label.setText(f'导出失败: {e}')
            return
        self._export_ready.emit(path)

    def _on_export_ready(self, path):
        self._info_label.setText(f'已导出: {os.path.basename(path)}')

    def _build_html_report(self):
        """生成自包含 HTML 报告（Chart.js 折线图）。"""
        h = self._hist
        cores = sorted(h['cpu_cores'].keys())
        cpu_datasets = [
            {'label': '总CPU', 'data': _to_list(h['cpu_total']),
             'borderColor': '#1de9b6', 'backgroundColor': 'rgba(29,233,182,.15)',
             'fill': True, 'tension': .25, 'pointRadius': 0, 'unit': '%',
             'stats': self._ds_stats(h['cpu_total'], '%')},
        ]
        for idx in cores:
            cpu_datasets.append({
                'label': f'Cpu{idx}',
                'data': _to_list(h['cpu_cores'][idx]),
                'borderColor': _hsv_hex(idx, len(cores)),
                'backgroundColor': 'transparent', 'fill': False,
                'tension': .25, 'pointRadius': 0, 'unit': '%',
                'stats': self._ds_stats(h['cpu_cores'][idx], '%'),
            })
        net_datasets = [
            {'label': '↓接收', 'data': _to_list(h['net_rx']),
             'borderColor': '#4fc3f7', 'backgroundColor': 'rgba(79,195,247,.15)',
             'fill': True, 'tension': .25, 'pointRadius': 0, 'unit': 'KB/s',
             'stats': self._ds_stats(h['net_rx'], 'KB/s')},
            {'label': '↑发送', 'data': _to_list(h['net_tx']),
             'borderColor': '#80deea', 'backgroundColor': 'transparent',
             'fill': False, 'tension': .25, 'pointRadius': 0, 'unit': 'KB/s',
             'stats': self._ds_stats(h['net_tx'], 'KB/s')},
        ]
        mem_datasets = [
            {'label': '内存(MB)', 'data': _to_list(h['mem']),
             'borderColor': '#ffab40', 'backgroundColor': 'rgba(255,171,64,.15)',
             'fill': True, 'tension': .25, 'pointRadius': 0, 'unit': 'MB',
             'stats': self._ds_stats(h['mem'], 'MB')},
        ]
        batt_datasets = [
            {'label': '温度(°C)', 'data': _to_list(h['batt']),
             'borderColor': '#ff8a65', 'backgroundColor': 'rgba(255,138,101,.15)',
             'fill': True, 'tension': .25, 'pointRadius': 0, 'unit': '°C',
             'stats': self._ds_stats(h['batt'], '°C')},
        ]
        payload = {
            'ts': _to_list_str(h['ts']),
            'cpu': cpu_datasets,
            'net': net_datasets,
            'mem': mem_datasets,
            'batt': batt_datasets,
            'serial': self._serial,
            'export_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'points': self._max_points,
        }
        chart_json = json.dumps(payload, ensure_ascii=False)
        return _HTML_TEMPLATE.replace('__CHART_DATA__', chart_json).replace(
            '__CHART_JS__', load_chart_js())

    # ---- 关窗即停止 ----
    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        # 关闭日志：在途采样线程见到 _closed 会丢弃结果，定时器已停，无后台残留
        主 = self.parent()
        if 主 is not None and hasattr(主, '日志'):
            主.日志('[设备性能监控] 窗口已关闭，后台采样已停止')
        super().closeEvent(event)


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------
def _to_list(dq):
    """deque -> list，None 保留为 None（Chart.js spanGaps=false 断开）。"""
    return list(dq)


def _to_list_str(dq):
    return list(dq)


def _hsv_hex(idx, n):
    import colorsys
    hue = (200 - idx * (200 / max(n, 1))) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.7, 0.85)
    return f'#{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}'


def _core_hex(idx, n):
    """每核折线颜色（十六进制，QColor 可解析）。"""
    return _hsv_hex(idx, n)


# ------------------------------------------------------------------
# HTML 报告模板
# ------------------------------------------------------------------
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>设备性能监控报告</title>
<script>__CHART_JS__</script>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; background: #1e1e1e; color: #d4d4d4;
         font-family: "Microsoft YaHei", "PingFang SC", sans-serif; padding: 20px; }
  h1 { color: #1de9b6; font-size: 22px; margin: 0 0 4px; }
  .meta { color: #888; font-size: 13px; margin-bottom: 16px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .card { background: #252526; border: 1px solid #333; border-radius: 8px; padding: 12px; }
  .card h3 { margin: 0 0 4px; color: #c8c8c8; font-size: 15px; }
  .stats { color: #9fd8c8; font-size: 12px; margin: 0 0 6px; line-height: 1.5;
           white-space: pre-wrap; }
  .chart-box { position: relative; height: 280px; }
  .btn-bar { margin: 12px 0; }
  button { background: #2d2d2d; color: #d4d4d4; border: 1px solid #444;
           border-radius: 6px; padding: 6px 14px; cursor: pointer; font-size: 13px; }
  button:hover { background: #3a3a3a; }
  .cdn-fail { display: none; color: #ff6b6b; text-align: center; padding: 40px; font-size: 16px; }
  @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
  @media print { .btn-bar { display: none; } body { padding: 0; max-width: none; } }
</style>
</head>
<body>
  <h1>设备性能监控报告</h1>
  <div class="meta" id="meta"></div>
  <div class="btn-bar"><button onclick="window.print()">打印 / 导出 PDF</button></div>
  <div id="cdn-fail" class="cdn-fail">⚠️ Chart.js 加载失败，请检查网络连接后刷新页面</div>
  <div class="grid">
    <div class="card"><h3>CPU 使用率 (%) — 总 CPU 与每核</h3><div class="stats" id="sCpu"></div><div class="chart-box"><canvas id="cCpu"></canvas></div></div>
    <div class="card"><h3>内存占用 (MB)</h3><div class="stats" id="sMem"></div><div class="chart-box"><canvas id="cMem"></canvas></div></div>
    <div class="card"><h3>网络速率 (KB/s) — 接收 / 发送</h3><div class="stats" id="sNet"></div><div class="chart-box"><canvas id="cNet"></canvas></div></div>
    <div class="card"><h3>电池温度 (°C)</h3><div class="stats" id="sBatt"></div><div class="chart-box"><canvas id="cBatt"></canvas></div></div>
  </div>
<script>
  var DATA = __CHART_DATA__;
  document.getElementById('meta').textContent =
    '设备: ' + DATA.serial + '  ·  导出时间: ' + DATA.export_time +
    '  ·  采样点: ' + DATA.points;
  function makeChart(id, datasets) {
    return new Chart(document.getElementById(id), {
      type: 'line',
      data: { labels: DATA.ts, datasets: datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: false, spanGaps: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { labels: { color: '#bbb', boxWidth: 12, font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: '#888', maxTicksLimit: 10 }, grid: { color: '#333' } },
          y: { ticks: { color: '#888' }, grid: { color: '#333' }, beginAtZero: true }
        }
      }
    });
  }
  if (typeof Chart === 'undefined') {
    document.getElementById('cdn-fail').style.display = 'block';
  } else {
    makeChart('cCpu', DATA.cpu);
    makeChart('cMem', DATA.mem);
    makeChart('cNet', DATA.net);
    makeChart('cBatt', DATA.batt);
    fillStats('sCpu', DATA.cpu);
    fillStats('sMem', DATA.mem);
    fillStats('sNet', DATA.net);
    fillStats('sBatt', DATA.batt);
  }
  function fillStats(id, datasets) {
    var el = document.getElementById(id);
    if (!el) return;
    var parts = (datasets || []).map(function (d) {
      var st = d.stats || {};
      var u = d.unit || '';
      if (st.max == null) return d.label + ': --';
      return d.label + ':  最高 ' + st.max + u + '  平均 ' + st.avg + u + '  最低 ' + st.min + u;
    });
    el.textContent = parts.join('    |    ');
  }
</script>
</body>
</html>
"""
