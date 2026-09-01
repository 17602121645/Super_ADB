# -*- coding: utf-8 -*-
"""
应用性能监控 —— 独立窗口
========================
点击 btnpm 弹出，根据包名每 2 秒采样指定应用的性能指标，
以滚动走势图展示，并基于线性回归斜率自动检测内存泄漏，
同时实时监测内存溢出 (OOM) 风险。

监控指标:
  CPU%         — top -b -n 1 -p <pid> (备选 dumpsys cpuinfo)
  内存 PSS     — dumpsys meminfo <package> TOTAL PSS
  Java Heap    — dumpsys meminfo <package> Java Heap
  Native Heap  — dumpsys meminfo <package> Native Heap
  Graphics     — dumpsys meminfo <package> Graphics
  线程数       — /proc/<pid>/status Threads
  Jank 丢帧率  — dumpsys gfxinfo <package> Janky frames

运行信息 (信息栏显示, 非图表):
  运行时长     — /proc/<pid>/stat starttime + /proc/uptime 计算
  电池状态     — dumpsys battery (电量/电压/电流/温度/充放电)
  应用耗电     — dumpsys batterystats Estimated power use (每 30s, 按 UID 查询)
  总耗电       — dumpsys batterystats Computed drain

内存泄漏检测:
  基于 PSS / Java Heap / Native Heap 的线性回归斜率判断
  持续增长 >1 MB/min → ⚠️ 疑似内存泄漏
  持续增长 >0.3 MB/min → ↑ 缓慢增长
  下降 → ↓ 下降中 (GC 回收正常)
  否则 → ✅ 稳定
  需至少 10 个有效采样点才开始分析

内存溢出 (OOM) 检测 —— 三层:
  1. 逼近预警: Java Heap / 设备堆上限 (getprop dalvik.vm.heapsize)
     > 90% → ☠️ 随时可能 OOM
     > 80% → ⚠️ 逼近上限
     > 60% → 偏高
     否则 → ✅ 安全
  2. 崩溃检测: 进程突然消失时抓 logcat 搜索 OutOfMemoryError /
     lowmemorykiller 等关键词, 命中则报告 OOM 崩溃
  3. 压力标签: 颜色等级 + 百分比, 与泄漏检测形成互补闭环

设计参照 DevicePerfMonitor，复用 ScrollChart 绘制组件。
"""

import os
import re
import time
import json
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QApplication, QSpinBox, QTextBrowser,
    QToolButton, QTextEdit,
)

from tools.adb_tools import Adb设备操作
from monitoring.device_performance_monitor import ScrollChart
from tools.chart_js import load_chart_js
from collections import deque  # AppScrollChart._values 兜底
from ui.ui_styles import STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_current_theme_id, THEMES
from dialogs.device_info_dialog import 获取设备信息_方法B


# ------------------------------------------------------------------
# 向后兼容适配：ScrollChart 已重构为多序列 (series_specs + add_point(name, value))，
# 但 应用性能监控 仍按旧单序列接口使用 (chart._values / chart.add_point(value, failed))。
# AppScrollChart 桥接两者，单序列名固定为 '值'。
# ------------------------------------------------------------------
class AppScrollChart(ScrollChart):
    """单序列滚动图，保持 应用性能监控 旧的访问方式。"""

    _NAME = '值'

    def __init__(self, title, color, unit, y_max=100.0,
                 max_points=None, auto_grow=False, parent=None):
        super().__init__(title, [(self._NAME, color)], unit,
                         y_max=y_max, max_points=max_points,
                         auto_grow=auto_grow, parent=parent)

    @property
    def _values(self):
        s = self._series.get(self._NAME)
        return s['values'] if s else deque()

    def add_point(self, value, failed=False):
        super().add_point(self._NAME, value, failed=failed)

from ui.dialog_styles import highlight_card_style, add_green_glow, _create_popup_card

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from ui import png_rc  # noqa: F401

SAMPLE_INTERVAL_MS = 2000   # 采样间隔 2 秒
MAX_POINTS = 120            # 保留最近 120 个点 (4 分钟)
LEAK_WINDOW = 30            # 泄漏检测窗口 (30 个采样点 = 1 分钟)
LEAK_MIN_SAMPLES = 10       # 泄漏检测最少需要 10 个有效点

# ---- OOM 检测阈值 ----
OOM_WARN_RATIO = 0.80      # Java Heap / MaxHeap > 80% → 逼近预警
OOM_CRITICAL_RATIO = 0.90  # Java Heap / MaxHeap > 90% → 危险
OOM_MODERATE_RATIO = 0.60  # Java Heap / MaxHeap > 60% → 偏高


# ------------------------------------------------------------------
# 解析函数 (模块级, 无 self 依赖, 可在后台线程安全调用)
# ------------------------------------------------------------------

def _parse_cpu_from_top(raw, pid):
    """从 top -b -n 1 -p <PID> 输出中解析指定进程的 CPU 使用率 (%)。

    自动适配新版 (Android 8+) 和旧版 (Android 6/7) 的 top 输出列顺序差异:
      新版: PID USER PR NI VIRT RES SHR S %CPU %MEM TIME+ COMMAND
      旧版: PID PR VIRT RES SHR S CPU% MEM% TIME+ NAME
    """
    if not raw or not pid:
        return None
    pid = str(pid).strip()
    cpu_col_idx = None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 列头行: 解析 %CPU 列位置
        if stripped.upper().startswith('PID '):
            cols = stripped.split()
            for i, c in enumerate(cols):
                cu = c.upper().replace('+', '')
                if cu in ('%CPU', 'CPU%'):
                    cpu_col_idx = i
                    break
            continue
        # 数据行: 行首必须是 PID
        cols = stripped.split()
        if not cols or cols[0] != pid:
            continue
        # 优先用列头定位
        if cpu_col_idx is not None and len(cols) > cpu_col_idx:
            try:
                return float(cols[cpu_col_idx])
            except ValueError:
                pass
        # 兜底: 状态列 S/R/D 作锚点, 之后第一个数字就是 %CPU
        STATUS_CHARS = {'S', 'R', 'D', 'Z', 'T', 'I', 'X', 'K', 'P', 'M'}
        for i, c in enumerate(cols):
            if c in STATUS_CHARS and i + 1 < len(cols):
                for j in range(i + 1, len(cols)):
                    try:
                        val = float(cols[j])
                        if 0 <= val <= 500:
                            return val
                    except ValueError:
                        continue
                break
    return None


def _parse_cpu_from_cpuinfo(raw, pid, package):
    """从 dumpsys cpuinfo 输出中按 PID 或包名解析 CPU% (备选方案)。"""
    if not raw:
        return None
    pid = str(pid).strip() if pid else ''
    pkg = package or ''
    for line in raw.splitlines():
        line = line.strip()
        if not line or '%' not in line:
            continue
        if pid and pkg:
            m = re.match(
                r'(\d+(?:\.\d+)?)%\s+' + re.escape(pid) + r'/' + re.escape(pkg) + r'\b',
                line)
            if m:
                return float(m.group(1))
        if (pid and pid in line) or (pkg and pkg in line):
            m = re.search(r'(\d+(?:\.\d+)?)%', line)
            if m:
                return float(m.group(1))
    return None


def _parse_meminfo(raw):
    """从 dumpsys meminfo 输出中解析各项内存指标 (KB → MB)。

    兼容新旧两版 dumpsys meminfo 输出 (已实测通过 Android 9 系统级 + 应用级):

    格式 A — App Summary (有冒号):
        Java Heap:    23456                            34567
        Native Heap:    80345                          123456
        Graphics:     6789                           12345
        TOTAL PSS:   125678            TOTAL RSS:   234567

    格式 B — 表格区 (无冒号), 真实堆占用在右侧 RSS Total 列:
        Native Heap        0        0        0        0        0    31228    26794     4433
        Dalvik Heap        0        0        0        0        0     2775     2263      512

    策略: 优先用 App Summary 中的 PSS 值, 若 PSS 为 0 或缺失, 则 fallback
    到表格区的 RSS Total 列 (真正反映堆占用)。
    """
    if not raw:
        return {}
    result = {}

    # ---- App Summary 区 (格式 A): 优先用 ----
    # Java/Native 在 PSS 为 0 时, 尝试表格 fallback
    summary = {
        'java_mb':     (r'Java Heap:\s*(\d+)',     'java'),
        'native_mb':   (r'Native Heap:\s*(\d+)',   'native'),
    }
    for key, (pat, fallback_key) in summary.items():
        m = re.search(pat, raw)
        if m:
            v = int(m.group(1))
            if fallback_key and v == 0:
                fb = _heap_from_table(raw, fallback_key)
                if fb:
                    result[key] = fb
            elif v > 0:
                result[key] = v / 1024.0
        elif fallback_key:
            # App Summary 没有这个字段, 但 Java/Native 可以走表格
            fb = _heap_from_table(raw, fallback_key)
            if fb:
                result[key] = fb

    # ---- Graphics 显存: 特殊处理 ----
    # 重要: dumpsys meminfo 在应用未分配显存时根本不输出 Graphics 行
    # 这种情况下返回 None 会让前端误以为"采样失败"
    # 正确做法: 缺省视为 0 MB (合理状态), 只有 dumpsys 整体命令失败才返回 None
    _parse_graphics(raw, result)

    # TOTAL PSS 始终用 App Summary (那里就是 PSS 总和)
    m = re.search(r'TOTAL PSS:\s*(\d+)', raw)
    if m:
        result['pss_mb'] = int(m.group(1)) / 1024.0
    else:
        # 表格 fallback: TOTAL 行第 1 列就是 PSS Total
        m = re.search(r'^\s*TOTAL\s+(\d+)', raw, re.MULTILINE)
        if m:
            result['pss_mb'] = int(m.group(1)) / 1024.0

    return result


def _heap_from_table(raw, which):
    """从表格区抓 Java(Dalvik) / Native Heap 行的真实堆占用。

    兼容 Android 9 (Dalvik Heap + Native Heap) 和 Android 12+ (Java Heap + Native Heap) 两种格式:

    Android 9 (老格式):
        Native Heap        0        0        0        0        0    31228    26794     4433
        Dalvik Heap        0        0        0        0        0     2775     2263      512

    Android 12+ (新格式, 一行 9 列):
                   Pss  Private  Private  SwapPss      Rss     Heap Size   Heap Alloc    Heap Free
                 Total      Dirty     Clean    Dirty    Total
        Java Heap:        0        0        0        0    45678      256000      123456      132544

    列结构 (Native/Java 是 2 个 token):
      [0] [1]   [2]       [3]          [4]         [5]       [6]     [7]        [8]        [9]
      Native Heap PSS_Tot  PDirty      PClean      SwapDirty RSS_Tot  Heap_Size  Heap_Alloc Heap_Free
      Java Heap (Android 12+ 名字; 同 Native Heap 列结构)

    策略: 优先取 RSS_Total; 若 RSS=0 (系统服务常见), 用 Heap_Size (实际分配的字节数)
    """
    # 兼容两种命名: 老版 'Dalvik' 和新版 'Java'
    if which == 'java':
        targets = ['Dalvik', 'Java Heap', 'Java']
    else:
        targets = ['Native']
    lines = raw.splitlines()

    # 找列头确定 RSS Total / Heap Size 列位置
    rss_col = _find_col_index(lines, 'rss', target='total')
    heap_size_col = _find_col_index(lines, 'heap', target='size')

    for line in lines:
        s = line.strip()
        # 行必须以 target 开头 (target 后跟 空格 / Tab / 冒号 / 行尾)
        matched = False
        for t in targets:
            if s == t or s.startswith(t + ' ') or s.startswith(t + '\t') \
                    or s.startswith(t + ':'):
                matched = True
                break
        if not matched:
            continue
        cols = s.replace(':', '').split()
        # 尝试 1: RSS Total (按列头定位)
        if rss_col is not None and len(cols) > rss_col:
            try:
                v = int(cols[rss_col])
                if v > 0:
                    return v / 1024.0
            except (ValueError, IndexError):
                pass
        # 尝试 2: Heap Size (按列头定位)
        if heap_size_col is not None and len(cols) > heap_size_col:
            try:
                v = int(cols[heap_size_col])
                if v > 0:
                    return v / 1024.0
            except (ValueError, IndexError):
                pass
        # 兜底: 取行内最大非零数 (Heap_Size 一定是 Dalvik/Java/Native 行
        # 中最大的值, 其他 PSS/Private/RSS 列都比它小)
        max_v = 0
        for c in cols[1:]:
            try:
                v = int(c)
                if v > max_v:
                    max_v = v
            except ValueError:
                continue
        if max_v > 100:  # > 100 KB
            return max_v / 1024.0
    return None


def _parse_graphics(raw, result):
    """解析 Graphics 显存 (KB → MB)。

    关键问题: dumpsys meminfo 在应用没有分配显存时, App Summary 和表格区
    **根本不会输出 Graphics 行**. 这种情况下旧解析器返回 None, 导致前端
    显示"获取失败"——但其实 0 MB 是真实状态.

    本函数处理以下三种情况:
      1. App Summary 有 Graphics 行, PSS 非 0   → 用 PSS 值
      2. App Summary 有 Graphics 行, PSS = 0     → fallback 到表格区 Graphics 行
      3. 完全无 Graphics 行                      → 视为 0 MB (合理状态)

    只有 dumpsys 命令本身返回空才会让 result 不包含 graphics_mb (前端才显示失败)
    """
    if not raw or 'MEMINFO in pid' not in raw:
        # dumpsys 整体失败, 保持 graphics_mb 缺失
        return

    # ---- 尝试 1: App Summary 的 `Graphics:` 行 (PSS 列) ----
    m = re.search(r'(?<!\w)Graphics:\s*(\d+)', raw)
    if m:
        v = int(m.group(1))
        if v > 0:
            result['graphics_mb'] = v / 1024.0
            return
        # PSS=0, 继续尝试表格 fallback

    # ---- 尝试 2: 表格区的 `Graphics` 行 ----
    fb = _graphics_from_table(raw)
    if fb is not None:
        result['graphics_mb'] = fb
        return

    # ---- 尝试 3: 行缺失 → 视为 0 MB ----
    result['graphics_mb'] = 0.0


def _graphics_from_table(raw):
    """从表格区解析 Graphics 行 (RSS Total 列, fallback 到 Heap Size)。

    表格格式示例:
        Graphics     12345    12345        0        0    12345
    """
    for line in raw.splitlines():
        s = line.strip()
        if not s.startswith('Graphics '):
            continue
        cols = s.split()
        if len(cols) < 2:
            continue
        # 尝试多个可能的 RSS Total 列位置 (旧版/新版 top)
        # Android 8+: PSS_Tot | Private_Dirty | Private_Clean | SwapDirty | RSS_Tot | Heap_Size | ...
        # 一般 col[0] 是 PSS_Tot, 但 Graphics 行的 PSS 可能为 0, 真实值在后面
        # 简化策略: 取所有非 0 数字中最大的 (RSS Total / Heap Size)
        nums = []
        for c in cols[1:]:
            try:
                v = int(c)
                nums.append(v)
            except ValueError:
                continue
        if not nums:
            continue
        # 优先取最大的非零值 (Heap Size 往往是最大值)
        candidates = [n for n in nums if n > 0]
        if candidates:
            return max(candidates) / 1024.0
    return None


def _find_col_index(lines, label_prefix, target='total'):
    """在列头行找指定列的索引。"""
    for line in lines[:30]:
        s = line.strip()
        # 形如: "Total    Dirty    Clean    Dirty    Total     Size    Alloc    Free"
        # 我们要找到第 2 个 'Total' (RSS Total), 它前面有 'Rss'
        if not s or 'Total' not in s:
            continue
        cols = s.split()
        # 找紧跟在 label_prefix 后面/前面的 target 词
        for i, c in enumerate(cols):
            if c.lower().startswith(label_prefix):
                # 可能是 'Rss Total' 或 'Rss'
                if i + 1 < len(cols) and cols[i + 1].lower() == target:
                    return i + 1
                # 可能是 'Heap' (没 Total 后缀), 或 'Heap Size'
                if target in c.lower():
                    return i
                # 'Heap Size' 这种, label 包含完整 'Size'
                if label_prefix == 'heap' and i + 1 < len(cols) and \
                   cols[i + 1].lower() == 'size':
                    return i + 1
    return None


def _parse_threads(raw):
    """从 /proc/<pid>/status 输出中解析线程数。"""
    if not raw:
        return None
    m = re.search(r'Threads:\s*(\d+)', raw)
    return int(m.group(1)) if m else None


def _parse_jank(raw):
    """从 dumpsys gfxinfo 输出中解析 jank 帧率和总数。

    返回: (jank_count, jank_percent) 或 (None, None)
    """
    if not raw:
        return None, None
    m = re.search(r'Janky frames:\s*(\d+)\s*\(([\d.]+)%\)', raw)
    if m:
        return int(m.group(1)), float(m.group(2))
    return None, None


def _parse_max_heap(raw):
    """解析 getprop dalvik.vm.heapsize 输出 (如 '512m' → 512.0 MB)。

    典型输出:
      [dalvik.vm.heapsize]: [512m]
      或纯数字 512 (单位 MB)
    """
    if not raw:
        return None
    m = re.search(r'(\d+)\s*m\b', raw, re.IGNORECASE)
    if m:
        return float(m.group(1))
    m = re.search(r'\b(\d+)\b', raw.strip())
    if m:
        v = int(m.group(1))
        if v > 10000:
            return v / 1024.0
        return float(v)
    return None


_OOM_LOG_PATTERNS = [
    r'OutOfMemoryError',
    r'low[-_ ]?memory[-_ ]?kill',
    r'oom[-_ ]?kill',
    r'Out of memory',
    r'Fatal.*SIGKILL',
    r'killing.*oom',
    r'oom[-_ ]?score',
    r'lmkd.*kill',
]


def _check_oom_crash(logcat_raw, context=3):
    """检查 logcat 输出是否包含 OOM 相关日志。

    返回 (first_line, full_text):
      first_line: 第一条匹配行 (用于 OOM 标签摘要), 无匹配则 None
      full_text:  所有匹配行 + 上下文 (用于展示框), 无匹配则 None

    context: 每条匹配行前后各取多少行上下文 (默认 3 行)
    """
    if not logcat_raw:
        return (None, None)
    lines = logcat_raw.splitlines()
    matched_indices = set()
    for i, line in enumerate(lines):
        for pat in _OOM_LOG_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                matched_indices.add(i)
                break
    if not matched_indices:
        return (None, None)

    first_line = lines[min(matched_indices)].strip()

    # 收集匹配行 + 上下文, 去重并保持顺序
    display_indices = set()
    for idx in matched_indices:
        for j in range(max(0, idx - context), min(len(lines), idx + context + 1)):
            display_indices.add(j)

    parts = []
    prev = -2
    for idx in sorted(display_indices):
        if idx > prev + 1 and parts:
            parts.append('  ...')  # 上下文断开标记
        prefix = '>>' if idx in matched_indices else '  '
        parts.append(f'{prefix} {lines[idx]}')
        prev = idx
    full_text = '\n'.join(parts)
    return (first_line, full_text)


# ------------------------------------------------------------------
# 运行时长 & 耗电 解析函数
# ------------------------------------------------------------------

CLK_TCK = 100  # Android/Linux sysconf(_SC_CLK_TCK) 恒为 100


def _parse_process_starttime(stat_raw):
    """从 /proc/<pid>/stat 解析进程启动时间 (自系统启动以来的 clock ticks)。

    /proc/<pid>/stat 格式:
      pid (comm) state ppid pgrp ... starttime ...
    comm 字段在括号内, 可能含空格, 所以用最后一个 ')' 定位分割点。
    starttime 是第 22 个字段 (从 1 开始计数), 在 ')' 之后是第 20 个字段。
    """
    if not stat_raw:
        return None
    idx = stat_raw.rfind(')')
    if idx < 0:
        return None
    rest = stat_raw[idx + 1:].split()
    # rest[0] = state (field 3), ..., rest[19] = starttime (field 22)
    if len(rest) >= 20:
        try:
            return int(rest[19])
        except ValueError:
            return None
    return None


def _parse_uptime(uptime_raw):
    """从 /proc/uptime 解析系统运行时间 (秒)。"""
    if not uptime_raw:
        return None
    parts = uptime_raw.split()
    if parts:
        try:
            return float(parts[0])
        except ValueError:
            return None
    return None


def _calc_running_seconds(stat_raw, uptime_raw):
    """计算进程已运行时长 (秒)。

    running = uptime - starttime / CLK_TCK
    """
    starttime = _parse_process_starttime(stat_raw)
    uptime = _parse_uptime(uptime_raw)
    if starttime is None or uptime is None:
        return None
    return max(0.0, uptime - starttime / float(CLK_TCK))


def _format_duration(seconds):
    """将秒数格式化为人类可读的时长字符串。"""
    if seconds is None or seconds < 0:
        return '--'
    if seconds < 60:
        return f'{int(seconds)}s'
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f'{m}m{s}s'
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f'{h}h{m}m'


def _parse_uid(package_raw):
    """从 dumpsys package 输出解析 userId。

    典型行: userId=10001
    """
    if not package_raw:
        return None
    m = re.search(r'userId=(\d+)', package_raw)
    return int(m.group(1)) if m else None


def _uid_to_batterystats_label(uid):
    """将数字 UID 转换为 batterystats 中的标签格式。

    UID 10001 → 'u0a1', UID 10123 → 'u0a123', UID 10000 → 'u0a0'
    """
    if uid is None:
        return None
    if uid >= 10000:
        return f'u0a{uid - 10000}'
    return f'u{uid}'


def _parse_battery_info(battery_raw):
    """从 dumpsys battery 解析电池信息。

    返回 dict:
      level:        电量百分比 (0-100)
      voltage_mv:   电压 (mV)
      current_ua:   电流 (μA, 正=充电 负=放电)
      temp_c:       温度 (°C)
      charging:     是否在充电
      status:       状态码字符串
    """
    if not battery_raw:
        return {}
    result = {}
    m = re.search(r'level:\s*(\d+)', battery_raw)
    if m:
        result['level'] = int(m.group(1))
    m = re.search(r'voltage:\s*(\d+)', battery_raw)
    if m:
        result['voltage_mv'] = int(m.group(1))
    m = re.search(r'current now:\s*(-?\d+)', battery_raw)
    if m:
        result['current_ua'] = int(m.group(1))
    m = re.search(r'temperature:\s*(\d+)', battery_raw)
    if m:
        result['temp_c'] = int(m.group(1)) / 10.0
    m = re.search(r'status:\s*(\d+)', battery_raw)
    if m:
        code = int(m.group(1))
        result['status'] = code
        # 2=charging, 3=discharging, 4=not charging, 5=full
        result['charging'] = code in (2, 5)
    return result


def _parse_app_power(batterystats_raw, uid):
    """从 dumpsys batterystats 解析指定 UID 的估算耗电 (mAh)。

    在 "Estimated power use" 区块中, 格式:
      Uid u0a123: 12.3 (cpu=8.1, wifi=3.2, ...)

    也兼容无括号详情的格式:
      Uid u0a123: 12.3

    注意: 模拟器/未充分使用的设备上 dumpsys batterystats 可能完全没有
    UID 级别的耗电数据 (只有 Global 行), 这种情况下返回 None,
    调用方应结合 _has_uid_power_data() 判断是"数据缺失"还是"解析失败"。
    """
    if not batterystats_raw or uid is None:
        return None
    uid_label = _uid_to_batterystats_label(uid)
    if not uid_label:
        return None

    in_power_section = False
    for line in batterystats_raw.splitlines():
        s = line.strip()
        # 检测 "Estimated power use" 区块开始
        if 'Estimated power use' in s:
            in_power_section = True
            continue
        # 空行结束区块
        if in_power_section and not s:
            break
        if not in_power_section:
            continue
        # 匹配 "Uid u0a123: 12.3" 或 "Uid u0a123: 12.3 (cpu=...)"
        if s.startswith('Uid ') and ':' in s:
            m = re.match(r'Uid\s+(\S+):\s+([\d.]+)', s)
            if m and m.group(1) == uid_label:
                try:
                    return float(m.group(2))
                except ValueError:
                    pass
    return None


def _has_uid_power_data(batterystats_raw):
    """检测 dumpsys batterystats 输出是否包含 UID 级别的耗电数据。

    模拟器/无真实电池消耗的设备上, "Estimated power use" 区块往往只有:
        Capacity: 4000, Computed drain: 0, actual drain: 0
        Global
    没有任何 Uid 行。这种情况下 _parse_app_power 一定返回 None,
    但应归类为"数据缺失"而非"解析失败"。
    """
    if not batterystats_raw:
        return False
    in_power_section = False
    for line in batterystats_raw.splitlines():
        s = line.strip()
        if 'Estimated power use' in s:
            in_power_section = True
            continue
        if in_power_section and not s:
            break  # 区块结束
        if in_power_section and s.startswith('Uid '):
            return True  # 至少有一个 Uid 行
    return False


def _parse_total_power(batterystats_raw):
    """从 dumpsys batterystats 解析总估算耗电 (mAh)。

    格式: Capacity: 3500, Computed drain: 293.1, actual drain: 427.3
    """
    if not batterystats_raw:
        return None
    m = re.search(r'Computed drain:\s*([\d.]+)', batterystats_raw)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


# ------------------------------------------------------------------
# 扩展指标解析函数 (FPS / 网络流量 / FD / 磁盘IO / 温度 / GC / WakeLock / ANR / 启动时间 / 存储)
# ------------------------------------------------------------------

def _parse_total_frames(raw):
    """从 dumpsys gfxinfo 输出中解析总渲染帧数。

    格式: Total frames rendered: 12345
    用于计算 FPS = delta_frames / sample_interval
    """
    if not raw:
        return None
    m = re.search(r'Total frames rendered:\s*(\d+)', raw)
    return int(m.group(1)) if m else None


def _parse_fd_count(raw):
    """从 `ls /proc/<pid>/fd | wc -l` 输出解析 FD 数量。"""
    if not raw:
        return None
    m = re.search(r'(\d+)', raw.strip())
    return int(m.group(1)) if m else None


def _parse_disk_io(raw):
    """从 /proc/<pid>/io 解析磁盘读写字节。

    返回 (read_bytes, write_bytes) 或 (None, None)。
    """
    if not raw:
        return (None, None)
    r = None
    w = None
    m = re.search(r'read_bytes:\s*(\d+)', raw)
    if m:
        r = int(m.group(1))
    m = re.search(r'write_bytes:\s*(\d+)', raw)
    if m:
        w = int(m.group(1))
    return (r, w)


def _parse_network_traffic(raw):
    """从合并的 tcp_snd + tcp_rcv 输出解析网络流量字节数。

    输入格式 (由采样脚本生成):
      ===SND===
      123456
      ===RCV===
      789012

    返回 (snd_bytes, rcv_bytes) 或 (None, None)。
    """
    if not raw:
        return (None, None)
    snd = None
    rcv = None
    m = re.search(r'===SND===\s*(\d+)', raw)
    if m:
        snd = int(m.group(1))
    m = re.search(r'===RCV===\s*(\d+)', raw)
    if m:
        rcv = int(m.group(1))
    return (snd, rcv)


def _parse_cpu_temp(raw):
    """从 /sys/class/thermal/thermal_zone*/temp 解析 CPU 温度。

    输出可能有多行 (多个 thermal_zone), 取最大值作为 CPU 温度。
    格式: 45000 (毫摄氏度, 需 /1000)
    """
    if not raw:
        return None
    temps = []
    for line in raw.splitlines():
        s = line.strip()
        if s:
            try:
                v = int(s)
                if v > 1000:
                    temps.append(v / 1000.0)
                else:
                    temps.append(float(v))
            except ValueError:
                pass
    return max(temps) if temps else None


def _parse_gc_count(raw):
    """从 logcat 输出中统计 GC 事件次数。

    匹配常见的 Android GC 日志格式:
      I/art     : Explicit concurrent mark...
      I/art     : Background concurrent...GCC...
      W/art     : Suspending all...
      ...clamping GC...
    """
    if not raw:
        return 0
    count = 0
    for line in raw.splitlines():
        if re.search(r'\bGC\b|\bgc\b', line) and (
            'art' in line.lower() or 'concurrent' in line.lower()
            or 'mark' in line.lower() or 'suspend' in line.lower()
            or 'clamping' in line.lower() or 'pause' in line.lower()
        ):
            count += 1
    return count


def _parse_wakelock(raw, package):
    """从 dumpsys power 输出检测指定包是否持有 WakeLock。

    返回: 持有的 wakelock 列表 (字符串), 空列表表示未持有。
    """
    if not raw or not package:
        return []
    locks = []
    in_wl = False
    for line in raw.splitlines():
        s = line.strip()
        if 'Wake Locks:' in s or 'wake lock' in s.lower():
            in_wl = True
            continue
        if in_wl and package in s:
            locks.append(s)
        elif in_wl and not s:
            in_wl = False
    return locks


# ANR 日志匹配模式
_ANR_LOG_PATTERNS = [
    r'ANR\s+in\s',
    r'Application\s+Not\s+Responding',
    r'\bANR\b.*com\.',
    r'am_anr\s*:',
    r'Signal\s+Catch\s+Output\s+Scheduler',
    r'waiting\s+on\s+lock.*ANR',
]


def _check_anr_crash(logcat_raw, context=3):
    """检查 logcat 输出是否包含 ANR 相关日志。

    返回 (first_line, full_text), 与 _check_oom_crash 同模式。
    """
    if not logcat_raw:
        return (None, None)
    lines = logcat_raw.splitlines()
    matched_indices = set()
    for i, line in enumerate(lines):
        for pat in _ANR_LOG_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                matched_indices.add(i)
                break
    if not matched_indices:
        return (None, None)

    first_line = lines[min(matched_indices)].strip()

    display_indices = set()
    for idx in matched_indices:
        for j in range(max(0, idx - context), min(len(lines), idx + context + 1)):
            display_indices.add(j)

    parts = []
    prev = -2
    for idx in sorted(display_indices):
        if idx > prev + 1 and parts:
            parts.append('  ...')
        prefix = '>>' if idx in matched_indices else '  '
        parts.append(f'{prefix} {lines[idx]}')
        prev = idx
    full_text = '\n'.join(parts)
    return (first_line, full_text)


def _parse_startup_time(raw):
    """从 `am start -W` 输出解析启动耗时。

    典型输出:
      Status: ok
      LaunchState: COLD
      Activity: com.example/.MainActivity
      TotalTime: 1234
      WaitTime: 1567
      Complete

    返回: dict {total_ms, wait_ms, launch_state, status} 或 None
    """
    if not raw:
        return None
    result = {}
    m = re.search(r'TotalTime:\s*(\d+)', raw)
    if m:
        result['total_ms'] = int(m.group(1))
    m = re.search(r'WaitTime:\s*(\d+)', raw)
    if m:
        result['wait_ms'] = int(m.group(1))
    m = re.search(r'LaunchState:\s*(\w+)', raw)
    if m:
        result['launch_state'] = m.group(1)
    m = re.search(r'Status:\s*(\w+)', raw)
    if m:
        result['status'] = m.group(1)
    return result if result else None


def _parse_main_activity(raw):
    """从 dumpsys package 输出解析主 Activity 名称。

    格式行: android.intent.action.MAIN:
              ...com.example/.MainActivity
    """
    if not raw:
        return None
    in_main = False
    for line in raw.splitlines():
        s = line.strip()
        if 'android.intent.action.MAIN' in s:
            in_main = True
            continue
        if in_main:
            m = re.search(r'(\S+/\.\w+|\S+/\S+Activity)', s)
            if m:
                return m.group(1)
            if s and not s.startswith(' ') and 'android.intent' in s:
                in_main = False
    return None


def _parse_app_storage(raw):
    """从 `du -sh /data/data/<pkg>` 输出解析应用存储大小。

    格式: 45M\t/data/data/com.example
    """
    if not raw:
        return None
    m = re.match(r'([\d.]+)([KMGT]?)', raw.strip())
    if m:
        size = float(m.group(1))
        unit = m.group(2)
        return f'{size}{unit or "B"}'
    return None


def _parse_app_info(raw, package):
    """从 `dumpsys package <pkg>` 输出解析应用包信息。

    返回 dict: versionName, versionCode, firstInstallTime, lastUpdateTime,
              targetSdk, minSdk, debuggable, enabled, uid, dataDir, minSdkVersion
    """
    info = {}
    if not raw:
        return info

    # versionName / versionCode
    m = re.search(r'versionName=([^\s]+)', raw)
    if m:
        info['versionName'] = m.group(1)
    m = re.search(r'versionCode=(\d+)', raw)
    if m:
        info['versionCode'] = m.group(1)

    # firstInstallTime / lastUpdateTime
    m = re.search(r'firstInstallTime=(.+)', raw)
    if m:
        info['firstInstallTime'] = m.group(1).strip()
    m = re.search(r'lastUpdateTime=(.+)', raw)
    if m:
        info['lastUpdateTime'] = m.group(1).strip()

    # targetSdk
    m = re.search(r'targetSdk=(\d+)', raw)
    if m:
        info['targetSdk'] = m.group(1)

    # minSdk (新版 Android)
    m = re.search(r'minSdk=(\d+)', raw)
    if m:
        info['minSdk'] = m.group(1)

    # debuggable — 在 flags=[...] 里查找
    m = re.search(r'flags=\[([^\]]+)\]', raw)
    if m:
        flags_str = m.group(1)
        info['debuggable'] = 'DEBUGGABLE' in flags_str
    else:
        # 某些设备用 fl=0x 格式
        m = re.search(r'fl=0x([0-9a-fA-F]+)', raw)
        if m:
            flags_val = int(m.group(1), 16)
            info['debuggable'] = bool(flags_val & 0x2)

    # enabled
    m = re.search(r'enabled=(\d+)', raw)
    if m:
        info['enabled'] = m.group(1) != '0'
    else:
        info['enabled'] = True  # 默认 enabled

    # userId (UID)
    m = re.search(r'userId=(\d+)', raw)
    if m:
        info['uid'] = m.group(1)

    # dataDir
    m = re.search(r'dataDir=([^\s]+)', raw)
    if m:
        info['dataDir'] = m.group(1)

    # codePath (APK 路径)
    m = re.search(r'codePath=([^\s]+)', raw)
    if m:
        info['codePath'] = m.group(1)

    return info


# ------------------------------------------------------------------
# 内存泄漏检测: 基于线性回归斜率
# ------------------------------------------------------------------

def _detect_leak(values, window=LEAK_WINDOW):
    """基于线性回归斜率检测内存泄漏趋势。

    Args:
        values: deque/list of float or None (来自 ScrollChart._values)
        window: 最近 N 个采样点用于分析 (默认 30 = 1 分钟)

    Returns: (status, slope_mb_per_min)
        status: 'insufficient' | 'stable' | 'warning' | 'leak' | 'declining'
    """
    pts = [v for v in values if v is not None]
    if len(pts) < LEAK_MIN_SAMPLES:
        return ('insufficient', 0)

    recent = pts[-window:]
    n = len(recent)
    if n < LEAK_MIN_SAMPLES:
        return ('insufficient', 0)

    # 线性回归: y = a + b*x, b = slope
    xs = list(range(n))
    ys = recent
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    sum_x2 = sum(x * x for x in xs)
    denom = n * sum_x2 - sum_x * sum_x
    if denom == 0:
        return ('stable', 0)
    slope_per_sample = (n * sum_xy - sum_x * sum_y) / denom

    # 转换为 MB/min (采样间隔 2s → 30 samples/min)
    samples_per_min = 60.0 / (SAMPLE_INTERVAL_MS / 1000.0)
    slope_per_min = slope_per_sample * samples_per_min

    if slope_per_min > 1.0:
        return ('leak', slope_per_min)
    elif slope_per_min > 0.3:
        return ('warning', slope_per_min)
    elif slope_per_min < -0.3:
        return ('declining', slope_per_min)
    else:
        return ('stable', slope_per_min)


# 泄漏状态优先级排序
_LEAK_PRIORITY = {
    'insufficient': 0,
    'declining':    1,
    'stable':       2,
    'warning':      3,
    'leak':         4,
}
_LEAK_COLOR = {
    'leak':         '#ff6b6b',
    'warning':      '#e5c07b',
    'stable':       '#98c379',
    'declining':    '#56b6c2',
    'insufficient': '#999999',
}
_LEAK_ICON = {
    'leak':         '⚠️',
    'warning':      '↑',
    'stable':       '✅',
    'declining':    '↓',
    'insufficient': '○',
}


# ------------------------------------------------------------------
# 应用性能监控窗口
# ------------------------------------------------------------------

class 应用性能监控(QWidget):
    """应用性能监控独立窗口。

    用法：
        win = 应用性能监控(serial, package_name, parent=main_window)
        win.show()
    """

    _sample_done = Signal(object)
    _startup_done = Signal(str, str)  # text, color (跨线程 UI 更新)
    _hprof_done = Signal(bool, str, str)  # ok, message, local_path

    def __init__(self, serial, package_name, parent=None):
        super().__init__(parent)
        self._adb = Adb设备操作()
        self._serial = serial
        self._package = package_name
        self._paused = False
        self._sampling = False
        self._closed = False
        self._pid = None
        self._hprof_dumped = False     # 一次泄漏 episode 只自动 dump 一次
        self._hprof_running = False    # 防止并发 dump
        self._last_raw_top = ''
        self._last_raw_mem = ''
        self._last_raw_threads = ''
        self._last_raw_gfx = ''
        self._max_heap_mb = None        # 设备单 App Java 堆上限 (MB)
        self._max_heap_fetched = False  # 是否已尝试获取堆上限
        self._was_running = False       # 上次采样时应用是否在运行 (用于 OOM 崩溃检测)
        self._max_points = 300         # 保留点数 (可配置, 默认 300 = 10 分钟)
        self._start_time = time.strftime('%Y-%m-%d %H:%M:%S')

        # ---- 运行时长 & 耗电 ----
        self._uid = None                # 应用 UID (dumpsys package 获取, 仅一次)
        self._uid_fetched = False       # 是否已尝试获取 UID
        self._batterystats_tick = 0     # batterystats 采样计数器
        self._app_power_mah = None      # 应用估算耗电 (mAh)
        self._total_power_mah = None    # 设备总估算耗电 (mAh)
        self._app_power_no_data = False  # dumpsys batterystats 不含 UID 级数据
                                         # (模拟器/未使用设备的固有限制, 非解析失败)
        self._app_power_error = False    # dumpsys batterystats 接口失败
        self._battery_info = {}         # dumpsys battery 解析结果
        self._last_raw_battery = ''     # dumpsys battery 原始输出 (调试用)
        self._last_raw_batterystats = ''  # dumpsys batterystats 原始输出 (调试用)
        self._last_raw_stat = ''        # /proc/pid/stat 原始输出 (调试用)
        self._last_raw_uptime = ''      # /proc/uptime 原始输出 (调试用)

        # ---- 扩展指标状态 ----
        self._prev_frames = None         # 上次总帧数 (FPS 计算)
        self._prev_net_snd = None        # 上次网络发送字节数
        self._prev_net_rcv = None        # 上次网络接收字节数
        self._prev_io_read = None        # 上次磁盘读字节数
        self._prev_io_write = None       # 上次磁盘写字节数
        self._prev_battery_level = None  # 上次电池电量 (掉电速率)
        self._prev_battery_ts = None     # 上次电池采样时间戳
        self._slow_tick = 0              # 慢指标采样计数器 (温度/GC/wakelock/存储)
        self._gc_count = 0               # GC 事件累计次数
        self._wakelock_list = []         # 当前持有的 WakeLock 列表
        self._cpu_temp = None            # CPU 温度 (°C)
        self._app_storage = None         # 应用存储大小 (字符串)
        self._main_activity = None       # 主 Activity (am start 用)
        self._main_activity_fetched = False
        self._last_raw_io = ''           # /proc/pid/io 原始输出
        self._last_raw_fd = ''           # ls /proc/pid/fd 原始输出
        self._last_raw_net = ''          # 网络流量原始输出
        self._last_raw_temp = ''         # thermal_zone 原始输出
        self._last_raw_gc = ''           # GC logcat 原始输出
        self._last_raw_wakelock = ''     # dumpsys power 原始输出
        self._last_raw_storage = ''      # du -sh 原始输出
        self._last_raw_startup = ''      # am start -W 原始输出
        self._startup_first_frame_ms = None   # 首帧时间 (am start -W TotalTime)
        self._startup_fully_ms = None         # 完全启动时间 (ResumedActivity 轮询)
        self._startup_state = ''              # 启动状态 (COLD/HOT/WARM)
        self._app_info = None            # 应用包信息 dict (版本号/安装时间等)
        self._app_info_fetched = False   # 是否已获取包信息
        self._last_raw_pkg = ''          # dumpsys package 原始输出 (调试用)

        # ---- 设备信息 (启动时后台获取, 仅一次) ----
        # 复用「设备信息对话框」采集逻辑: getprop 全量 + 并发 10 个标识符
        self._device_info = None       # {'getprop_text': str, 'identifiers': [(name, value)], ...}
        self._device_info_fetched = False
        self._device_info_thread = None

        # ---- 图表自动隐藏: 连续 N 次获取不到数据则隐藏图表 ----
        self._chart_fail_count = {}     # chart -> 连续失败次数
        self._chart_hidden = set()      # 已隐藏的 chart
        self._chart_fail_count = {}     # 连续失败计数
        self._chart_zero_count = {}     # 连续全零计数
        self._CHART_HIDE_THRESHOLD = 5  # 连续失败/全零阈值

        self.setWindowTitle(f'应用监控 — {package_name}')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(740, 580)
        self.resize(800, 700)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id))
        self.setWindowFlag(Qt.Window, True)

        # 卡片容器：主题色高亮边框 + 发光（含主布局挂载）
        self.card, _ = _create_popup_card(self, self._theme_id)

        self._build_ui()

        self._timer = QTimer(self)
        self._timer.setInterval(SAMPLE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self._sample_done.connect(self._on_sample)
        self._startup_done.connect(self._on_startup_done)
        self._hprof_done.connect(self._on_hprof_done)

        # 立即开始采样
        self._timer.start()
        self._tick()

        # 启动设备信息后台获取 (一次性, 不阻塞采样启动)
        self._device_info_thread = threading.Thread(
            target=self._fetch_device_info_task, daemon=True)
        self._device_info_thread.start()

    def _fetch_device_info_task(self):
        """后台线程: 逐行获取设备信息，获取一条展示一条。

        getprop 先获取并立即可展示，标识符按顺序逐个获取并追加。
        """
        # 初始化结果
        self._device_info = {
            'getprop_text': '设备属性 (getprop) 获取中…',
            'identifiers': [],
            'raw_getprop': '',
            'ok': False,
            'error': '',
        }
        # 1) 先获取 getprop
        try:
            from dialogs.device_info_dialog import 设备信息_属性字典, _B_IDS
            raw = self._adb.执行shell(self._serial, 'getprop', timeout=10)
            self._device_info['raw_getprop'] = raw or ''
            self._device_info['getprop_text'] = 设备信息_属性字典(raw or '', self._serial)
        except Exception as e:
            self._device_info['error'] = f'getprop 失败: {e}'
            self._device_info['getprop_text'] = f'getprop 获取失败: {e}'
        # getprop 获取完就可以展示了
        self._device_info_fetched = True

        # 2) 按顺序逐个获取标识符，获取一条追加一条
        for 名称, fn in _B_IDS:
            try:
                值 = fn(self._adb, self._serial)
            except Exception as e:
                值 = f'获取失败: {e}'
            self._device_info['identifiers'].append((名称, str(值)))

        self._device_info['ok'] = True

    # ---- 主题切换 ----
    def apply_theme(self, theme_id):
        """运行时切换主题：更新全局 QSS + card 样式 + 外发光。"""
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        if hasattr(self, 'card') and self.card is not None:
            self.card.setStyleSheet(highlight_card_style(theme_id))
            add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        self.update()

    # ---- UI 搭建 ----
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        # ---- 顶部信息栏 ----
        top = QHBoxLayout()
        top.setSpacing(12)
        self._info_label = QLabel(f'包名: {self._package}    采样中…')
        self._info_label.setStyleSheet(
            f'font: 11pt "{FONT_FAMILY}"; color: #1de9b6; background: transparent;')
        self._info_label.setWordWrap(True)
        top.addWidget(self._info_label, 1)

        # 启动耗时按钮 + 结果标签
        self._btn_startup = QPushButton('启动耗时')
        self._btn_startup.setFixedWidth(80)
        self._btn_startup.setToolTip('测量应用冷启动耗时 (首帧 + 完全启动)')
        self._btn_startup.clicked.connect(self._measure_startup)
        top.addWidget(self._btn_startup)
        self._startup_result_label = QLabel('')
        self._startup_result_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #1de9b6; background: transparent;')
        top.addWidget(self._startup_result_label)

        # 保留点数配置
        pts_label = QLabel('保留点数:')
        pts_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #999999; background: transparent;')
        top.addWidget(pts_label)
        self._spin_points = QSpinBox()
        self._spin_points.setRange(60, 3600)
        self._spin_points.setSingleStep(30)
        self._spin_points.setValue(self._max_points)
        self._spin_points.setFixedWidth(100)
        self._spin_points.setSuffix(' 点')
        self._spin_points.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #dcdcdc; '
            f'background: rgba(255,255,255,0.08); border: 1px solid #444; '
            f'border-radius: 3px; padding: 1px 4px;')
        self._spin_points.setToolTip(
            f'采样间隔 {SAMPLE_INTERVAL_MS // 1000}s\n'
            f'当前 {self._max_points} 点 ≈ '
            f'{self._max_points * SAMPLE_INTERVAL_MS / 1000 / 60:.1f} 分钟')
        self._spin_points.valueChanged.connect(self._on_max_points_changed)
        top.addWidget(self._spin_points)

        self._btn_pause = QPushButton('暂停')
        self._btn_pause.setFixedWidth(80)
        self._btn_pause.clicked.connect(self._toggle_pause)
        top.addWidget(self._btn_pause)

        self._btn_dump_hprof = QPushButton('dump hprof')
        self._btn_dump_hprof.setMinimumWidth(110)
        self._btn_dump_hprof.setToolTip('手动抓取进程堆快照 (am dumpheap + adb pull)')
        self._btn_dump_hprof.clicked.connect(lambda: self._trigger_hprof_dump('手动'))
        top.addWidget(self._btn_dump_hprof)
        lay.addLayout(top)

        # ---- 内存泄漏检测栏 ----
        self._leak_label = QLabel('内存泄漏检测: ○ 数据不足 (需 10+ 个采样点)')
        self._leak_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')
        lay.addWidget(self._leak_label)

        # ---- hprof 快照状态栏 (默认隐藏, 触发 dump 时显示) ----
        self._hprof_label = QLabel('')
        self._hprof_label.setWordWrap(True)
        self._hprof_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #56b6c2; '
            f'background: rgba(86,182,194,0.08); padding: 4px 8px; '
            f'border-radius: 4px;')
        self._hprof_label.setVisible(False)
        lay.addWidget(self._hprof_label)

        # ---- 内存溢出检测栏 ----
        self._oom_label = QLabel('内存溢出检测: 等待数据…')
        self._oom_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')
        lay.addWidget(self._oom_label)

        # ---- ANR 检测栏 ----
        self._anr_label = QLabel('ANR 检测: 等待数据…')
        self._anr_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')
        lay.addWidget(self._anr_label)

        # ---- 崩溃/ANR 日志折叠展示框 (默认隐藏, 检测到崩溃时显示) ----
        self._crash_log_container = QFrame()
        self._crash_log_container.setVisible(False)
        crash_log_layout = QVBoxLayout(self._crash_log_container)
        crash_log_layout.setContentsMargins(0, 0, 0, 0)
        crash_log_layout.setSpacing(2)

        # 折叠标题栏
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self._crash_log_toggle_btn = QToolButton()
        self._crash_log_toggle_btn.setCheckable(True)
        self._crash_log_toggle_btn.setChecked(False)
        self._crash_log_toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._crash_log_toggle_btn.setArrowType(Qt.RightArrow)
        self._crash_log_toggle_btn.setText('展开崩溃日志')
        self._crash_log_toggle_btn.setStyleSheet(
            f'QToolButton {{ font: 9pt "{FONT_FAMILY}"; color: #ff6b6b; '
            f'border: none; padding: 2px 4px; }}')
        self._crash_log_toggle_btn.toggled.connect(self._on_crash_log_toggle)
        header_layout.addWidget(self._crash_log_toggle_btn)
        header_layout.addStretch()
        crash_log_layout.addLayout(header_layout)

        self._crash_log_browser = QTextBrowser()
        self._crash_log_browser.setMaximumHeight(180)
        self._crash_log_browser.setVisible(False)
        self._crash_log_browser.setStyleSheet(
            f'QTextBrowser {{ font: 9pt "Consolas", "{FONT_FAMILY}", monospace; '
            f'color: #ff6b6b; background: rgba(255,107,107,0.08); '
            f'border: 1px solid rgba(255,107,107,0.3); border-radius: 4px; '
            f'padding: 4px; }}')
        crash_log_layout.addWidget(self._crash_log_browser)
        lay.addWidget(self._crash_log_container)

        # ---- 运行时长信息栏 ----
        self._power_label = QLabel('运行信息: 采样中…')
        self._power_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')
        lay.addWidget(self._power_label)

        # ---- 电池信息栏 (放在设备信息上方) ----
        self._battery_label = QLabel('🔋 电池: 采样中…')
        self._battery_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')
        lay.addWidget(self._battery_label)

        # ---- 设备信息: 两个可折叠框 (getprop 属性 + 设备标识符) ----
        # 与设备信息弹窗保持一致: 上面 getprop, 下面标识符
        self._device_getprop_container, self._device_getprop_toggle_btn, self._device_getprop_edit = \
            self._build_collapsible_text_box(
                '📋 设备属性 (getprop 全量, 按中文分组)',
                initial_checked=False,
                border_color='#1de9b6',
                accent='#1de9b6',
                max_height=280,
            )
        lay.addWidget(self._device_getprop_container)

        self._device_ids_container, self._device_ids_toggle_btn, self._device_ids_edit = \
            self._build_collapsible_text_box(
                '🔖 设备标识符 (IMEI/MAC/OAID/GAID/Android ID 等, 并发获取)',
                initial_checked=False,
                border_color='#c678dd',
                accent='#c678dd',
                max_height=200,
            )
        lay.addWidget(self._device_ids_container)

        # ---- 应用包信息栏 (版本号/安装时间/SDK等) ----
        self._app_info_label = QLabel('📦 应用信息: 获取中…')
        self._app_info_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #dcdcdc; '
            f'background: rgba(255,255,255,0.05); padding: 8px 10px; '
            f'border-radius: 6px; border-left: 3px solid #61afef;')
        self._app_info_label.setWordWrap(True)
        lay.addWidget(self._app_info_label)

        # ---- 滚动区域容纳所有图表 ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet('QScrollArea { background: transparent; border: none; }')

        charts_widget = QWidget()
        charts_widget.setStyleSheet('background: transparent;')
        charts_lay = QVBoxLayout(charts_widget)
        charts_lay.setContentsMargins(0, 0, 0, 0)
        charts_lay.setSpacing(4)

        # ---- 应用耗电信息标签 (与应用耗电图统计栏同一行, 右对齐) ----
        self._app_power_label = QLabel('🔌 采样中…')
        self._app_power_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #ff6b9d; '
            f'background: transparent; padding: 1px 8px;')

        # 辅助函数: 创建 chart + stats label 配对
        def _add_chart(title, color, unit, y_max, extra_widget=None):
            chart = AppScrollChart(title, color, unit, y_max, max_points=self._max_points)
            chart.setFixedHeight(130)
            charts_lay.addWidget(chart)
            stats = QLabel('  最高值: --     平均值: --     最低值: --')
            stats.setStyleSheet(
                f'font: 9pt "{FONT_FAMILY}"; color: {color}; '
                f'background: transparent; padding: 1px 8px;')
            if extra_widget is not None:
                # stats + extra_widget 同一行 (水平布局)
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                row.addWidget(stats, 1)
                row.addWidget(extra_widget, 0)
                charts_lay.addLayout(row)
            else:
                charts_lay.addWidget(stats)
            return chart, stats

        self._cpu_chart, self._cpu_stats = _add_chart(
            f'CPU 使用率 — {self._package}', '#1de9b6', '%', 100.0)
        self._pss_chart, self._pss_stats = _add_chart(
            f'内存 PSS (TOTAL) — {self._package}', '#ffab40', 'MB', 512.0)
        self._java_chart, self._java_stats = _add_chart(
            f'Java Heap — {self._package}', '#61afef', 'MB', 256.0)
        self._native_chart, self._native_stats = _add_chart(
            f'Native Heap — {self._package}', '#e06c75', 'MB', 256.0)
        self._gfx_chart, self._gfx_stats = _add_chart(
            f'Graphics 显存 — {self._package}', '#c678dd', 'MB', 256.0)
        self._thread_chart, self._thread_stats = _add_chart(
            f'线程数 — {self._package}', '#d19a66', '', 200.0)
        self._jank_chart, self._jank_stats = _add_chart(
            f'Jank 丢帧率 — {self._package}', '#56b6c2', '%', 100.0)
        self._power_chart, self._power_stats = _add_chart(
            f'应用耗电 (mAh 累计) — {self._package}', '#ff6b9d', 'mAh', 100.0,
            extra_widget=self._app_power_label)

        # ---- 扩展图表: FPS / 网络流量 / FD / 磁盘IO ----
        self._fps_chart, self._fps_stats = _add_chart(
            f'FPS 帧率 — {self._package}', '#e5c07b', 'fps', 120.0)
        self._net_chart, self._net_stats = _add_chart(
            f'网络流量 (TX+RX KB/s) — {self._package}', '#61afef', 'KB/s', 500.0)
        self._fd_chart, self._fd_stats = _add_chart(
            f'文件描述符 (FD) — {self._package}', '#e06c75', '', 500.0)
        self._io_chart, self._io_stats = _add_chart(
            f'磁盘 I/O (Read+Write KB/s) — {self._package}', '#c678dd', 'KB/s', 500.0)

        # ---- 扩展信息栏 (GC/WakeLock/CPU温度/存储/掉电速率) ----
        self._extra_info_label = QLabel('📊 扩展指标: 采样中…')
        self._extra_info_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #dcdcdc; '
            f'background: rgba(255,255,255,0.05); padding: 6px 8px; '
            f'border-radius: 4px; border-left: 3px solid #d19a66;')
        self._extra_info_label.setWordWrap(True)
        charts_lay.addWidget(self._extra_info_label)

        scroll.setWidget(charts_widget)
        self._scroll_area = scroll
        self._scroll_content = charts_widget
        lay.addWidget(scroll, 1)

        # ---- 底部状态行 ----
        bottom = QHBoxLayout()
        self._status_label = QLabel('')
        self._status_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #999999; background: transparent;')
        bottom.addWidget(self._status_label, 1)

        self._btn_export = QPushButton('导出报告')
        self._btn_export.setFixedWidth(90)
        self._btn_export.clicked.connect(self._export_html)
        bottom.addWidget(self._btn_export)
        self._btn_copy = QPushButton('复制调试')
        self._btn_copy.setFixedWidth(90)
        self._btn_copy.clicked.connect(self._copy_debug)
        bottom.addWidget(self._btn_copy)
        lay.addLayout(bottom)

    # ---- 辅助: 创建可折叠 + 可滚动 QTextEdit 子控件 ----
    def _build_collapsible_text_box(self, title, initial_checked, border_color,
                                    accent='#1de9b6', max_height=240):
        """构造一个 QFrame 容器: 标题栏 (带折叠箭头) + QTextEdit (V+H 滚动条)。

        Returns:
            (container, toggle_btn, edit): 三件套, 容器外暴露 toggle_btn 控制展开
        """
        container = QFrame()
        container.setStyleSheet(
            f'QFrame {{ background: rgba(255,255,255,0.04); '
            f'border-radius: 6px; border-left: 3px solid {border_color}; }}')
        v = QVBoxLayout(container)
        v.setContentsMargins(6, 4, 6, 6)
        v.setSpacing(2)

        # 折叠标题栏 (QToolButton 自带箭头)
        toggle_btn = QToolButton()
        toggle_btn.setCheckable(True)
        toggle_btn.setChecked(initial_checked)
        toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle_btn.setArrowType(Qt.DownArrow if initial_checked else Qt.RightArrow)
        toggle_btn.setText(title + ('  ▼' if initial_checked else '  ▶'))
        toggle_btn.setStyleSheet(
            f'QToolButton {{ font: 10pt "{FONT_FAMILY}"; color: {accent}; '
            f'border: none; padding: 2px 4px; background: transparent; }} '
            f'QToolButton:hover {{ color: #fff; }}')
        # 用 lambda 捕获变量, 切可见 + 切箭头
        def _on_toggle(checked, _b=toggle_btn, _t=title):
            _b.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
            _b.setText(_t + ('  ▼' if checked else '  ▶'))
        toggle_btn.toggled.connect(_on_toggle)
        v.addWidget(toggle_btn)

        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        edit.setMaximumHeight(max_height)
        edit.setVisible(initial_checked)
        edit.setStyleSheet(
            f'QTextEdit {{ font: 9pt "{FONT_FAMILY}", "Consolas", monospace; '
            f'color: #dcdcdc; background: rgba(0,0,0,0.25); '
            f'border: 1px solid #444; border-radius: 4px; padding: 6px 8px; }} '
            f'QScrollBar:vertical {{ background: #2b2b2b; width: 10px; }} '
            f'QScrollBar::handle:vertical {{ background: #555; border-radius: 4px; }} '
            f'QScrollBar:horizontal {{ background: #2b2b2b; height: 10px; }} '
            f'QScrollBar::handle:horizontal {{ background: #555; border-radius: 4px; }}')
        v.addWidget(edit)
        # 通过 toggled 控制 edit 可见
        def _on_toggle_visible(checked, _e=edit):
            _e.setVisible(checked)
        toggle_btn.toggled.connect(_on_toggle_visible)
        return container, toggle_btn, edit

    # ---- 采样调度 ----
    def _tick(self):
        """定时器回调：启动后台采样线程 (防止重叠)。"""
        if self._closed or self._paused or self._sampling:
            return
        self._sampling = True
        threading.Thread(target=self._sample_task, daemon=True).start()

    def _sample_task(self):
        """后台线程：执行 adb 命令获取性能数据 (含运行时长 & 耗电)。"""
        cpu_pct = None
        mem_info = {}
        threads_count = None
        jank_count = None
        jank_pct = None
        pid = None
        error_msg = ''
        running_seconds = None
        battery_info = {}
        app_power_mah = self._app_power_mah       # 保留上次缓存值
        total_power_mah = self._total_power_mah
        # ---- 扩展指标 ----
        fd_count = None
        io_read = None
        io_write = None
        total_frames = None
        fps = None
        net_snd = None
        net_rcv = None
        cpu_temp = None
        gc_count = None
        wakelock_list = []
        app_storage = None

        # ---- 获取 PID ----
        try:
            pid_out = self._adb.执行shell(
                self._serial, f'pidof {self._package}', timeout=5)
            pid = pid_out.strip().split()[0] if pid_out.strip() else None
        except Exception:
            pid = None

        if not pid:
            # ---- OOM/ANR 崩溃检测: 进程突然消失时抓 logcat 搜索关键词 ----
            oom_crash_line = None
            oom_crash_log = None
            anr_crash_line = None
            anr_crash_log = None
            if self._was_running:
                try:
                    logcat_raw = self._adb.执行shell(
                        self._serial, 'logcat -d -t 200', timeout=5)
                    oom_crash_line, oom_crash_log = _check_oom_crash(logcat_raw)
                    anr_crash_line, anr_crash_log = _check_anr_crash(logcat_raw)
                except Exception:
                    pass
            if not self._closed:
                self._sample_done.emit({
                    'ts': time.strftime('%H:%M:%S'),
                    'cpu_pct': None, 'mem_info': {},
                    'threads': None, 'jank_pct': None,
                    'pid': None,
                    'error': f'{self._package} 未运行',
                    'oom_crash': oom_crash_line,
                    'oom_crash_log': oom_crash_log,
                    'anr_crash': anr_crash_line,
                    'anr_crash_log': anr_crash_log,
                    'max_heap_mb': self._max_heap_mb,
                    'running_seconds': None,
                    'battery_info': {},
                    'app_power_mah': None,
                    'total_power_mah': None,
                    'app_power_no_data': self._app_power_no_data,
                    'app_power_error': self._app_power_error,
                    'uid': self._uid,
                    'fd_count': None, 'io_read': None, 'io_write': None,
                    'fps': None, 'net_snd': None, 'net_rcv': None,
                    'cpu_temp': None, 'gc_count': None, 'wakelock_list': [],
                    'app_storage': None,
                })
            return

        self._pid = pid

        # ---- 获取设备堆上限 (仅第一次) ----
        if not self._max_heap_fetched:
            self._max_heap_fetched = True
            try:
                hp = self._adb.执行shell(
                    self._serial, 'getprop dalvik.vm.heapsize', timeout=3)
                self._max_heap_mb = _parse_max_heap(hp)
            except Exception:
                self._max_heap_mb = None
            # 尝试 large heap (largeHeap=true 的应用上限更高)
            if self._max_heap_mb:
                try:
                    hpl = self._adb.执行shell(
                        self._serial, 'getprop dalvik.vm.heapsize.large', timeout=3)
                    large = _parse_max_heap(hpl)
                    if large and large > self._max_heap_mb:
                        self._max_heap_mb = large
                except Exception:
                    pass

        # ---- 获取应用 UID (仅第一次, 用于 batterystats 耗电查询) ----
        if not self._uid_fetched:
            self._uid_fetched = True
            try:
                pkg_raw = self._adb.执行shell(
                    self._serial, f'dumpsys package {self._package}', timeout=8)
                self._last_raw_pkg = pkg_raw or ''
                self._uid = _parse_uid(pkg_raw)
                # 从同一份 dumpsys package 输出解析应用信息 (版本号/安装时间等)
                self._app_info = _parse_app_info(pkg_raw, self._package)
                self._app_info_fetched = True
            except Exception:
                self._uid = None
                self._app_info = {}
                self._app_info_fetched = True

        # ---- CPU: 优先 top -b -n 1 -p <pid>，备选 dumpsys cpuinfo ----
        try:
            top_raw = self._adb.执行shell(
                self._serial, f'top -b -n 1 -p {pid}', timeout=5)
            self._last_raw_top = top_raw or ''
            cpu_pct = _parse_cpu_from_top(top_raw, pid)
        except Exception:
            cpu_pct = None

        if cpu_pct is None:
            try:
                cpu_raw = self._adb.执行shell(
                    self._serial, 'dumpsys cpuinfo', timeout=10)
                self._last_raw_top = cpu_raw or ''
                cpu_pct = _parse_cpu_from_cpuinfo(cpu_raw, pid, self._package)
            except Exception as e:
                if not error_msg:
                    error_msg = f'CPU 采样异常: {e}'

        # ---- 内存: dumpsys meminfo <package> (一次获取 PSS/Java/Native/Graphics) ----
        try:
            mem_raw = self._adb.执行shell(
                self._serial, f'dumpsys meminfo {self._package}', timeout=15)
            self._last_raw_mem = mem_raw or ''
            mem_info = _parse_meminfo(mem_raw)
        except Exception as e:
            if not error_msg:
                error_msg = f'内存采样异常: {e}'

        # ---- 线程数: cat /proc/<pid>/status ----
        try:
            thr_raw = self._adb.执行shell(
                self._serial, f'cat /proc/{pid}/status', timeout=3)
            self._last_raw_threads = thr_raw or ''
            threads_count = _parse_threads(thr_raw)
        except Exception:
            threads_count = None

        # ---- Jank 丢帧率: dumpsys gfxinfo <package> (best effort) ----
        try:
            gfx_raw = self._adb.执行shell(
                self._serial, f'dumpsys gfxinfo {self._package}', timeout=8)
            self._last_raw_gfx = gfx_raw or ''
            jank_count, jank_pct = _parse_jank(gfx_raw)
        except Exception:
            jank_count, jank_pct = None, None

        # ---- 运行时长: /proc/<pid>/stat (starttime) + /proc/uptime ----
        try:
            stat_raw = self._adb.执行shell(
                self._serial, f'cat /proc/{pid}/stat', timeout=3)
            self._last_raw_stat = stat_raw or ''
            uptime_raw = self._adb.执行shell(
                self._serial, 'cat /proc/uptime', timeout=3)
            self._last_raw_uptime = uptime_raw or ''
            running_seconds = _calc_running_seconds(stat_raw, uptime_raw)
        except Exception:
            running_seconds = None

        # ---- 电池信息: dumpsys battery (每次采样, 快速 ~0.5s) ----
        try:
            bat_raw = self._adb.执行shell(
                self._serial, 'dumpsys battery', timeout=5)
            self._last_raw_battery = bat_raw or ''
            battery_info = _parse_battery_info(bat_raw)
            self._battery_info = battery_info
        except Exception:
            battery_info = {}

        # ---- 应用耗电: dumpsys batterystats ----
        # 第一次采样立即获取 (避免用户等 30s), 之后每 15 次采样 (≈30s) 刷一次
        self._batterystats_tick += 1
        should_query_batterystats = (
            self._batterystats_tick == 1 or self._batterystats_tick % 15 == 0)
        if should_query_batterystats:
            try:
                bs_raw = self._adb.执行shell(
                    self._serial, 'dumpsys batterystats', timeout=20)
                self._last_raw_batterystats = bs_raw or ''
                # 区分"接口失败"和"设备无 UID 级数据" (模拟器常见)
                self._app_power_error = False
                has_uid_data = _has_uid_power_data(bs_raw)
                self._app_power_no_data = not has_uid_data
                # 设备不支持 UID 级耗电数据 → 直接隐藏应用耗电图表和标签
                if self._app_power_no_data and self._power_chart not in self._chart_hidden:
                    self._power_chart.hide()
                    if self._power_stats:
                        self._power_stats.hide()
                    if self._app_power_label:
                        self._app_power_label.hide()
                    self._chart_hidden.add(self._power_chart)
                if self._uid is not None:
                    app_power_mah = _parse_app_power(bs_raw, self._uid)
                    self._app_power_mah = app_power_mah
                total_power_mah = _parse_total_power(bs_raw)
                self._total_power_mah = total_power_mah
            except Exception:
                # 接口失败 (超时/权限等), 保留上次缓存值, 标记 error
                self._app_power_error = True
                self._app_power_no_data = False

        # ---- FD 数 + 磁盘 I/O: /proc/<pid>/fd + /proc/<pid>/io (每次采样, 快速) ----
        try:
            proc_raw = self._adb.执行shell(
                self._serial,
                f'echo "===FD==="; ls /proc/{pid}/fd | wc -l; '
                f'echo "===IO==="; cat /proc/{pid}/io',
                timeout=5)
            self._last_raw_fd = proc_raw or ''
            fd_count = _parse_fd_count(proc_raw)
            io_read, io_write = _parse_disk_io(proc_raw)
            self._last_raw_io = proc_raw or ''
        except Exception:
            fd_count = None

        # ---- FPS: 从 gfxinfo 已有输出解析总帧数, 计算 delta / interval ----
        if self._last_raw_gfx:
            total_frames = _parse_total_frames(self._last_raw_gfx)
            if total_frames is not None and self._prev_frames is not None:
                delta = total_frames - self._prev_frames
                if delta >= 0:
                    fps = delta / (SAMPLE_INTERVAL_MS / 1000.0)
            self._prev_frames = total_frames

        # ---- 网络流量: /proc/uid_stat/<uid>/tcp_snd + tcp_rcv (每次采样, 快速) ----
        if self._uid is not None:
            try:
                net_raw = self._adb.执行shell(
                    self._serial,
                    f'echo "===SND==="; cat /proc/uid_stat/{self._uid}/tcp_snd 2>/dev/null; '
                    f'echo "===RCV==="; cat /proc/uid_stat/{self._uid}/tcp_rcv 2>/dev/null',
                    timeout=3)
                self._last_raw_net = net_raw or ''
                net_snd, net_rcv = _parse_network_traffic(net_raw)
            except Exception:
                net_snd, net_rcv = None, None

        # ---- 慢指标: 每 N 次采样获取一次 (温度/GC/WakeLock/存储) ----
        self._slow_tick += 1
        # CPU 温度 (每 5 次 ≈ 10s)
        if self._slow_tick % 5 == 0:
            try:
                temp_raw = self._adb.执行shell(
                    self._serial,
                    'cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null',
                    timeout=3)
                self._last_raw_temp = temp_raw or ''
                cpu_temp = _parse_cpu_temp(temp_raw)
                self._cpu_temp = cpu_temp
            except Exception:
                pass
        else:
            cpu_temp = self._cpu_temp

        # GC 统计 (每 10 次 ≈ 20s, 从 logcat 统计 GC 事件)
        if self._slow_tick % 10 == 0:
            try:
                gc_raw = self._adb.执行shell(
                    self._serial, 'logcat -d -t 100', timeout=5)
                self._last_raw_gc = gc_raw or ''
                gc_count = _parse_gc_count(gc_raw)
                self._gc_count = gc_count
            except Exception:
                pass
        else:
            gc_count = self._gc_count

        # Wake Lock (每 15 次 ≈ 30s, 同 batterystats 周期)
        if self._slow_tick % 15 == 0:
            try:
                wl_raw = self._adb.执行shell(
                    self._serial, 'dumpsys power', timeout=8)
                self._last_raw_wakelock = wl_raw or ''
                wakelock_list = _parse_wakelock(wl_raw, self._package)
                self._wakelock_list = wakelock_list
            except Exception:
                pass
        else:
            wakelock_list = self._wakelock_list

        # 应用存储 (每 30 次 ≈ 60s, du 较慢)
        if self._slow_tick % 30 == 0:
            try:
                du_raw = self._adb.执行shell(
                    self._serial,
                    f'du -sh /data/data/{self._package} 2>/dev/null',
                    timeout=5)
                self._last_raw_storage = du_raw or ''
                app_storage = _parse_app_storage(du_raw)
                self._app_storage = app_storage
            except Exception:
                pass
        else:
            app_storage = self._app_storage

        if not self._closed:
            self._sample_done.emit({
                'ts': time.strftime('%H:%M:%S'),
                'cpu_pct': cpu_pct,
                'mem_info': mem_info,
                'threads': threads_count,
                'jank_count': jank_count,
                'jank_pct': jank_pct,
                'pid': pid,
                'error': error_msg,
                'oom_crash': None,
                'oom_crash_log': None,
                'anr_crash': None,
                'anr_crash_log': None,
                'max_heap_mb': self._max_heap_mb,
                'running_seconds': running_seconds,
                'battery_info': battery_info,
                'app_power_mah': app_power_mah,
                'total_power_mah': total_power_mah,
                'app_power_no_data': self._app_power_no_data,
                'app_power_error': self._app_power_error,
                'uid': self._uid,
                'fd_count': fd_count,
                'io_read': io_read,
                'io_write': io_write,
                'fps': fps,
                'net_snd': net_snd,
                'net_rcv': net_rcv,
                'cpu_temp': cpu_temp,
                'gc_count': gc_count,
                'wakelock_list': wakelock_list,
                'app_storage': app_storage,
            })

    # ---- 统计计算 ----
    @staticmethod
    def _compute_stats(chart, unit):
        """从 chart._values 过滤 None 后计算 最高/平均/最低。"""
        vals = [v for v in chart._values if v is not None]
        if not vals:
            return '  最高值: --     平均值: --     最低值: --'
        hi = max(vals)
        lo = min(vals)
        avg = sum(vals) / len(vals)
        return (f'  最高值: {hi:.1f}{unit}     '
                f'平均值: {avg:.1f}{unit}     '
                f'最低值: {lo:.1f}{unit}')

    @staticmethod
    def _compute_stats_or_na(chart, unit):
        """同 _compute_stats, 但无数据时返回 '数据不可用' 提示 (用于扩展指标)。"""
        vals = [v for v in chart._values if v is not None]
        if not vals:
            return '  ⚠ 数据不可用 (设备不支持或权限不足)'
        hi = max(vals)
        lo = min(vals)
        avg = sum(vals) / len(vals)
        return (f'  最高值: {hi:.1f}{unit}     '
                f'平均值: {avg:.1f}{unit}     '
                f'最低值: {lo:.1f}{unit}')

    # ---- 结果处理 (主线程) ----
    def _on_sample(self, data):
        if self._closed:
            return
        self._sampling = False

        ts = data['ts']
        cpu_pct = data['cpu_pct']
        mem_info = data.get('mem_info', {})
        threads_count = data.get('threads')
        jank_pct = data.get('jank_pct')
        pid = data['pid']
        error = data.get('error', '')
        oom_crash = data.get('oom_crash')
        max_heap = data.get('max_heap_mb')
        if max_heap and not self._max_heap_mb:
            self._max_heap_mb = max_heap

        # ---- 运行时长 & 耗电 ----
        running_seconds = data.get('running_seconds')
        battery_info = data.get('battery_info', {})
        app_power_mah = data.get('app_power_mah')
        total_power_mah = data.get('total_power_mah')
        uid = data.get('uid')
        if uid and not self._uid:
            self._uid = uid
        # 同步采样线程的状态标志 (避免线程间读旧值)
        if 'app_power_no_data' in data:
            self._app_power_no_data = data['app_power_no_data']
        if 'app_power_error' in data:
            self._app_power_error = data['app_power_error']

        # ---- 扩展指标 ----
        fd_count = data.get('fd_count')
        io_read = data.get('io_read')
        io_write = data.get('io_write')
        fps = data.get('fps')
        net_snd = data.get('net_snd')
        net_rcv = data.get('net_rcv')
        cpu_temp = data.get('cpu_temp')
        gc_count = data.get('gc_count')
        wakelock_list = data.get('wakelock_list', [])
        app_storage = data.get('app_storage')
        anr_crash = data.get('anr_crash')
        anr_crash_log = data.get('anr_crash_log')

        pss_mb = mem_info.get('pss_mb')
        java_mb = mem_info.get('java_mb')
        native_mb = mem_info.get('native_mb')
        gfx_mb = mem_info.get('graphics_mb')

        # ---- 更新 CPU 图 ----
        if cpu_pct is not None:
            self._cpu_chart.add_point(cpu_pct, failed=False)
        else:
            self._cpu_chart.add_point(0, failed=True)

        # ---- 更新 PSS 图 ----
        if pss_mb is not None:
            self._pss_chart.add_point(pss_mb, failed=False)
            cur_max = self._pss_chart._y_max
            if pss_mb > cur_max * 0.85:
                self._pss_chart.set_y_max(max(pss_mb * 1.2, 100.0))
        else:
            self._pss_chart.add_point(0, failed=True)

        # ---- 更新 Java Heap 图 ----
        if java_mb is not None:
            self._java_chart.add_point(java_mb, failed=False)
            cur_max = self._java_chart._y_max
            if java_mb > cur_max * 0.85:
                self._java_chart.set_y_max(max(java_mb * 1.2, 50.0))
        else:
            self._java_chart.add_point(0, failed=True)

        # ---- 更新 Native Heap 图 ----
        if native_mb is not None:
            self._native_chart.add_point(native_mb, failed=False)
            cur_max = self._native_chart._y_max
            if native_mb > cur_max * 0.85:
                self._native_chart.set_y_max(max(native_mb * 1.2, 50.0))
        else:
            self._native_chart.add_point(0, failed=True)

        # ---- 更新 Graphics 图 ----
        if gfx_mb is not None:
            self._gfx_chart.add_point(gfx_mb, failed=False)
            cur_max = self._gfx_chart._y_max
            if gfx_mb > cur_max * 0.85:
                self._gfx_chart.set_y_max(max(gfx_mb * 1.2, 50.0))
            self._记录图表结果(self._gfx_chart, self._gfx_stats, True)
        else:
            self._gfx_chart.add_point(0, failed=True)
            self._记录图表结果(self._gfx_chart, self._gfx_stats, False)

        # ---- 更新 线程数 图 ----
        if threads_count is not None:
            self._thread_chart.add_point(float(threads_count), failed=False)
            cur_max = self._thread_chart._y_max
            if threads_count > cur_max * 0.85:
                self._thread_chart.set_y_max(max(threads_count * 1.3, 50.0))
        else:
            self._thread_chart.add_point(0, failed=True)

        # ---- 更新 Jank 图 ----
        if jank_pct is not None:
            self._jank_chart.add_point(jank_pct, failed=False)
            self._记录图表结果(self._jank_chart, self._jank_stats, True)
        else:
            self._jank_chart.add_point(0, failed=True)
            self._记录图表结果(self._jank_chart, self._jank_stats, False)

        # ---- 更新应用耗电图 (只在 batterystats 刷新周期打点, 其余填 None 留缺口) ----
        if self._batterystats_tick == 1 or self._batterystats_tick % 15 == 0:
            if app_power_mah is not None:
                self._power_chart.add_point(app_power_mah, failed=False)
                # 自适应 Y 轴
                cur_max = self._power_chart._y_max
                if app_power_mah > cur_max * 0.85:
                    self._power_chart.set_y_max(max(app_power_mah * 1.2, 100.0))
                self._记录图表结果(self._power_chart, self._power_stats, True)
            elif self._app_power_no_data:
                # 设备未提供 UID 级耗电数据 (模拟器/未充分使用), 跳过打点
                # 避免在图表上画 "获取失败" 红字误导用户
                self._记录图表结果(self._power_chart, self._power_stats, False)
            else:
                # 真正的接口失败 (timeout/权限等)
                self._power_chart.add_point(0, failed=True)
                self._记录图表结果(self._power_chart, self._power_stats, False)
        # 非刷新周期不采样 (chart 自然显示缺口)

        # ---- 更新 FPS 图 ----
        if fps is not None:
            self._fps_chart.add_point(fps, failed=False)
            cur_max = self._fps_chart._y_max
            if fps > cur_max * 0.85:
                self._fps_chart.set_y_max(max(fps * 1.2, 120.0))
            self._记录图表结果(self._fps_chart, self._fps_stats, True)
        else:
            # fps 为 None 时跳过打点 (首帧无基线 / 设备不支持 gfxinfo 帧统计)
            # 避免画 "获取失败" 红字误导 (与 power chart _app_power_no_data 同策略)
            self._记录图表结果(self._fps_chart, self._fps_stats, False)

        # ---- 更新 网络流量 图 (TX+RX 合计 KB/s) ----
        if net_snd is not None and net_rcv is not None and \
                self._prev_net_snd is not None and self._prev_net_rcv is not None:
            delta_snd = max(0, net_snd - self._prev_net_snd)
            delta_rcv = max(0, net_rcv - self._prev_net_rcv)
            net_kbps = (delta_snd + delta_rcv) / 1024.0 / (SAMPLE_INTERVAL_MS / 1000.0)
            self._net_chart.add_point(net_kbps, failed=False)
            cur_max = self._net_chart._y_max
            if net_kbps > cur_max * 0.85:
                self._net_chart.set_y_max(max(net_kbps * 1.2, 100.0))
            self._记录图表结果(self._net_chart, self._net_stats, True)
        else:
            # 网络数据不可用时跳过 (模拟器无 /proc/uid_stat, 首帧无基线)
            self._记录图表结果(self._net_chart, self._net_stats, False)
        self._prev_net_snd = net_snd
        self._prev_net_rcv = net_rcv

        # ---- 更新 FD 图 ----
        if fd_count is not None:
            self._fd_chart.add_point(float(fd_count), failed=False)
            cur_max = self._fd_chart._y_max
            if fd_count > cur_max * 0.85:
                self._fd_chart.set_y_max(max(fd_count * 1.3, 100.0))
            self._记录图表结果(self._fd_chart, self._fd_stats, True)
        else:
            # fd_count 为 None 时跳过 (权限不足或 procfs 不可读)
            self._记录图表结果(self._fd_chart, self._fd_stats, False)

        # ---- 更新 磁盘 I/O 图 (Read+Write 合计 KB/s) ----
        if io_read is not None and io_write is not None and \
                self._prev_io_read is not None and self._prev_io_write is not None:
            delta_r = max(0, io_read - self._prev_io_read)
            delta_w = max(0, io_write - self._prev_io_write)
            io_kbps = (delta_r + delta_w) / 1024.0 / (SAMPLE_INTERVAL_MS / 1000.0)
            self._io_chart.add_point(io_kbps, failed=False)
            cur_max = self._io_chart._y_max
            if io_kbps > cur_max * 0.85:
                self._io_chart.set_y_max(max(io_kbps * 1.2, 100.0))
            self._记录图表结果(self._io_chart, self._io_stats, True)
        else:
            # I/O 数据不可用时跳过 (权限不足或 procfs 不可读, 首帧无基线)
            self._记录图表结果(self._io_chart, self._io_stats, False)
        self._prev_io_read = io_read
        self._prev_io_write = io_write

        # ---- 更新统计栏 ----
        self._cpu_stats.setText(self._compute_stats(self._cpu_chart, '%'))
        self._pss_stats.setText(self._compute_stats(self._pss_chart, ' MB'))
        self._java_stats.setText(self._compute_stats(self._java_chart, ' MB'))
        self._native_stats.setText(self._compute_stats(self._native_chart, ' MB'))
        self._gfx_stats.setText(self._compute_stats(self._gfx_chart, ' MB'))
        self._thread_stats.setText(self._compute_stats(self._thread_chart, ''))
        self._jank_stats.setText(self._compute_stats(self._jank_chart, '%'))
        self._power_stats.setText(self._compute_stats(self._power_chart, ' mAh'))
        self._fps_stats.setText(self._compute_stats_or_na(self._fps_chart, ' fps'))
        self._net_stats.setText(self._compute_stats_or_na(self._net_chart, ' KB/s'))
        self._fd_stats.setText(self._compute_stats_or_na(self._fd_chart, ''))
        self._io_stats.setText(self._compute_stats_or_na(self._io_chart, ' KB/s'))

        # ---- 内存泄漏检测 ----
        self._update_leak_detection()

        # ---- 内存溢出检测 ----
        self._update_oom_detection(data)

        # ---- 追踪运行状态 (供下次 _sample_task 的 OOM 崩溃检测使用) ----
        self._was_running = pid is not None

        # ---- 顶部信息栏 (含运行时长) ----
        if pid:
            cpu_str = f'{cpu_pct:.1f}%' if cpu_pct is not None else 'N/A'
            pss_str = f'{pss_mb:.0f}MB' if pss_mb is not None else 'N/A'
            thr_str = f'{threads_count}线程' if threads_count is not None else 'N/A'
            run_str = f'已运行 {_format_duration(running_seconds)}' if running_seconds is not None else ''
            self._info_label.setText(
                f'包名: {self._package}    PID: {pid}    '
                f'CPU: {cpu_str}    PSS: {pss_str}    线程: {thr_str}'
                + (f'    {run_str}' if run_str else ''))
        else:
            self._info_label.setText(
                f'包名: {self._package}    {error or "未运行"}')

        # ---- 运行信息栏 (运行时长) ----
        self._update_power_label(running_seconds, pid)

        # ---- 应用耗电信息标签 (独立标签, 放在应用耗电图下方) ----
        self._update_app_power_label(app_power_mah, total_power_mah)

        # ---- 电池信息标签 (独立标签, 放在应用耗电标签下方) ----
        self._update_battery_label(battery_info)

        # ---- ANR 检测 ----
        self._update_anr_detection(data)

        # ---- 扩展信息标签 (GC/WakeLock/CPU温度/存储/掉电速率) ----
        self._update_extra_info(
            gc_count, wakelock_list, cpu_temp, app_storage, battery_info, pid)

        # ---- 设备信息栏 (一次性获取) ----
        self._update_device_getprop_box()
        self._update_device_ids_box()

        # ---- 应用包信息栏 (一次性获取, 与 UID 同一次 dumpsys package) ----
        self._update_app_info_label()
        # ---- 底部状态 ----
        self._status_label.setText(
            f'开始时间: {self._start_time}    采样时间: {ts}    '
            f'每 {SAMPLE_INTERVAL_MS // 1000}s 采样    保留最近 {self._max_points} 个点')

    # ---- 内存泄漏检测 ----
    def _update_leak_detection(self):
        """分析 PSS / Java Heap / Native Heap 趋势, 更新泄漏检测栏。"""
        pss_st, pss_sl = _detect_leak(self._pss_chart._values)
        java_st, java_sl = _detect_leak(self._java_chart._values)
        native_st, native_sl = _detect_leak(self._native_chart._values)

        # 综合判定: 取最严重的状态
        worst = max(
            [pss_st, java_st, native_st],
            key=lambda s: _LEAK_PRIORITY.get(s, 0))

        # 构建详情文本
        def _fmt(name, st, sl):
            if st == 'insufficient':
                return f'{name}: 数据不足'
            sign = '+' if sl > 0 else ''
            return f'{name}: {sign}{sl:.1f} MB/min'

        details = ' | '.join([
            _fmt('PSS', pss_st, pss_sl),
            _fmt('Java', java_st, java_sl),
            _fmt('Native', native_st, native_sl),
        ])

        icon = _LEAK_ICON.get(worst, '○')
        self._leak_label.setText(f'内存泄漏检测: {icon} {details}')
        self._leak_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: {_LEAK_COLOR.get(worst, "#999")}; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')

        # ---- 泄漏阈值触发自动 heap dump ----
        if worst == 'leak':
            # 线性回归斜率 >1 MB/min 视为疑似泄漏 → 自动 dump hprof
            self._trigger_hprof_dump('泄漏阈值')
        else:
            # 泄漏解除后重置标志, 下次再泄漏可再次 dump
            self._hprof_dumped = False

    # ---- hprof 堆快照抓取 ----
    def _trigger_hprof_dump(self, reason='手动'):
        """触发一次 heap dump（后台线程），结果通过 _hprof_done 回主线程。

        reason='泄漏阈值' 时受 _hprof_dumped 节流（一次泄漏 episode 只自动 dump 一次）；
        reason='手动' 不受此限（但仍用 _hprof_running 防并发）。
        """
        if self._hprof_running:
            return
        if reason != '手动' and self._hprof_dumped:
            return
        if not self._pid:
            if reason == '手动':
                self._on_hprof_done(False, '未获取到进程 PID，无法 dump', '')
            return
        self._hprof_dumped = True
        self._hprof_running = True
        self._hprof_label.setVisible(True)
        self._hprof_label.setText(f'⏳ 正在抓取 heap 快照 ({reason}) …')
        self._hprof_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #ffab40; '
            f'background: rgba(255,171,64,0.08); padding: 4px 8px; '
            f'border-radius: 4px;')
        threading.Thread(target=self._run_hprof_dump, args=(reason,), daemon=True).start()

    def _run_hprof_dump(self, reason):
        """后台执行 am dumpheap + adb pull，把 hprof 拉到桌面/Super_ADB。"""
        ok = False
        msg = ''
        local_path = ''
        try:
            ts = time.strftime('%Y%m%d_%H%M%S')
            safe_pkg = re.sub(r'[^A-Za-z0-9_.-]', '_', self._package or 'app')
            dev_file = f'/data/local/tmp/{safe_pkg}_{ts}.hprof'

            # ① am dumpheap 到设备临时目录
            self._adb.执行shell(
                self._serial, f'am dumpheap {self._pid} {dev_file}', timeout=90)

            # ② adb pull 到本地 桌面/Super_ADB/hprof_<pkg>_<ts>/
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            dest_dir = os.path.join(desktop, 'Super_ADB', f'hprof_{safe_pkg}_{ts}')
            os.makedirs(dest_dir, exist_ok=True)
            local_file = os.path.join(dest_dir, f'{safe_pkg}_{ts}.hprof')
            self._adb.直接执行(
                self._serial, ['pull', dev_file, local_file], timeout=180)

            # ③ 清理设备端临时文件
            try:
                self._adb.执行shell(self._serial, f'rm -f {dev_file}', timeout=10)
            except Exception:
                pass

            ok = True
            local_path = local_file
            msg = f'{reason}触发 heap dump 完成 → {local_file}'
        except Exception as e:
            msg = f'{reason}触发 heap dump 失败: {e}'
        self._hprof_done.emit(ok, msg, local_path)

    def _on_hprof_done(self, ok, msg, local_path):
        try:
            self._hprof_running = False
            self._hprof_label.setVisible(True)
            if ok:
                self._hprof_label.setText(f'📦 {msg}（可用 Android Studio / MAT 打开分析）')
                self._hprof_label.setStyleSheet(
                    f'font: 9pt "{FONT_FAMILY}"; color: #56b6c2; '
                    f'background: rgba(86,182,194,0.08); padding: 4px 8px; '
                    f'border-radius: 4px;')
            else:
                self._hprof_label.setText(f'⚠️ {msg}')
                self._hprof_label.setStyleSheet(
                    f'font: 9pt "{FONT_FAMILY}"; color: #ff6b6b; '
                    f'background: rgba(255,107,107,0.08); padding: 4px 8px; '
                    f'border-radius: 4px;')
        except Exception:
            pass

    def _on_crash_log_toggle(self, checked):
        """切换崩溃日志折叠/展开,并保留行数提示。"""
        self._crash_log_browser.setVisible(checked)
        text = self._crash_log_toggle_btn.text()
        suffix = ''
        if '(' in text and ')' in text:
            suffix = ' ' + text[text.index('('):text.index(')') + 1]
        if checked:
            self._crash_log_toggle_btn.setText(f'收起崩溃日志{suffix}')
            self._crash_log_toggle_btn.setArrowType(Qt.DownArrow)
        else:
            self._crash_log_toggle_btn.setText(f'展开崩溃日志{suffix}')
            self._crash_log_toggle_btn.setArrowType(Qt.RightArrow)

    # ---- 内存溢出检测 ----
    def _update_oom_detection(self, data):
        """更新内存溢出检测栏 (OOM 逼近预警 + 崩溃检测 + 崩溃日志展示)。

        三层检测:
          1. OOM 崩溃 (优先级最高): 进程消失 + logcat 命中 OOM 关键词
             → 标签显示摘要, 下方展示框显示完整匹配日志 (含上下文)
          2. 逼近预警: Java Heap / 设备堆上限 的百分比
          3. 压力等级标签: 颜色 + 百分比
        """
        oom_crash = data.get('oom_crash')
        oom_crash_log = data.get('oom_crash_log')
        max_heap = data.get('max_heap_mb') or self._max_heap_mb
        pid = data.get('pid')
        mem_info = data.get('mem_info', {})
        java_mb = mem_info.get('java_mb')
        pss_mb = mem_info.get('pss_mb')

        def _set(text, color, bg='rgba(255,255,255,0.05)'):
            self._oom_label.setText(text)
            self._oom_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: {color}; '
                f'background: {bg}; padding: 4px 8px; '
                f'border-radius: 4px;')

        # ---- 1. OOM 崩溃 (最高优先级) ----
        anr_crash = data.get('anr_crash')
        anr_crash_log = data.get('anr_crash_log')

        if oom_crash:
            snippet = oom_crash[:100] if len(oom_crash) > 100 else oom_crash
            _set(f'内存溢出检测: OOM 应用已崩溃 — {snippet}',
                 '#ff6b6b', 'rgba(255,107,107,0.12)')
            # 展示完整崩溃日志 (含上下文), 如有 ANR 日志也一并展示
            if oom_crash_log or anr_crash_log:
                parts = []
                if oom_crash_log:
                    parts.append('=== OOM 崩溃日志 (logcat -d -t 200 筛选) ===\n'
                                 '>> 标记匹配行, 上下各 3 行为上下文\n\n'
                                 + oom_crash_log)
                if anr_crash_log:
                    parts.append('\n\n=== ANR 日志 (同一 logcat 筛选) ===\n'
                                 + anr_crash_log)
                full_text = '\n'.join(parts)
                line_count = len(full_text.splitlines())
                self._crash_log_browser.setPlainText(full_text)
                self._crash_log_toggle_btn.setText(f'展开崩溃日志 ({line_count} 行)')
                # 默认折叠: 显示标题栏, 隐藏详细日志
                self._crash_log_container.setVisible(True)
                self._crash_log_toggle_btn.setChecked(False)
            else:
                self._crash_log_container.setVisible(False)
            return

        # ANR 崩溃 (OOM 未命中时检查)
        if anr_crash:
            self._crash_log_container.setVisible(False)  # OOM 日志折叠框先隐藏
        else:
            # 非崩溃状态: 隐藏展示框
            self._crash_log_container.setVisible(False)

        # ---- 2. 进程未运行 (未检测到 OOM 日志) ----
        if not pid:
            _set('内存溢出检测: 进程未运行 (未检测到 OOM 日志)', '#999999')
            return

        # ---- 3. 逼近预警: Java Heap / Max Heap ----
        if max_heap and java_mb is not None:
            ratio = java_mb / max_heap
            pct = ratio * 100
            pss_str = f'PSS: {pss_mb:.0f}MB' if pss_mb else 'PSS: N/A'

            if ratio >= OOM_CRITICAL_RATIO:
                _set(f'内存溢出检测: OOM 随时可能溢出 — '
                     f'Java {java_mb:.0f}MB / {max_heap:.0f}MB ({pct:.0f}%) | {pss_str}',
                     '#ff6b6b', 'rgba(255,107,107,0.12)')
            elif ratio >= OOM_WARN_RATIO:
                _set(f'内存溢出检测: 逼近上限 — '
                     f'Java {java_mb:.0f}MB / {max_heap:.0f}MB ({pct:.0f}%) | {pss_str}',
                     '#ff9866', 'rgba(255,152,102,0.10)')
            elif ratio >= OOM_MODERATE_RATIO:
                _set(f'内存溢出检测: 偏高 — '
                     f'Java {java_mb:.0f}MB / {max_heap:.0f}MB ({pct:.0f}%) | {pss_str}',
                     '#e5c07b')
            else:
                _set(f'内存溢出检测: 安全 — '
                     f'Java {java_mb:.0f}MB / {max_heap:.0f}MB ({pct:.0f}%) | {pss_str}',
                     '#98c379')
        elif max_heap and java_mb is None:
            _set(f'内存溢出检测: 堆上限 {max_heap:.0f}MB, Java Heap 获取中…', '#999999')
        else:
            _set('内存溢出检测: 未获取到堆上限 (getprop dalvik.vm.heapsize)', '#999999')

    # ---- 运行信息栏 (运行时长) ----
    def _update_power_label(self, running_seconds, pid):
        """更新运行信息栏: 只显示运行时长 (电池/耗电已拆出到独立标签)。"""
        if running_seconds is not None and pid:
            self._power_label.setText(
                f'⏱ 已运行 {_format_duration(running_seconds)}')
            self._power_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: #56b6c2; '
                f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
                f'border-radius: 4px;')
        else:
            self._power_label.setText('⏱ 已运行: 等待数据…')
            self._power_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
                f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
                f'border-radius: 4px;')

    # ---- 应用耗电信息标签 (独立标签, 放在应用耗电图下方) ----
    def _update_app_power_label(self, app_power_mah, total_power_mah):
        """更新应用耗电信息标签 (与应用耗电图统计栏同一行, 右侧紧凑显示)。"""
        # 紧凑样式 (与 stats label 同行, 透明背景)
        def _style(color):
            return (f'font: 9pt "{FONT_FAMILY}"; color: {color}; '
                    f'background: transparent; padding: 1px 8px;')

        if app_power_mah is not None:
            if total_power_mah is not None and total_power_mah > 0:
                pct = app_power_mah / total_power_mah * 100
                text = f'🔌 ~{app_power_mah:.1f}mAh ({pct:.1f}%)'
                color = '#e5c07b' if pct > 30 else '#ff6b9d'
            else:
                text = f'🔌 ~{app_power_mah:.1f}mAh'
                color = '#ff6b9d'
            self._app_power_label.setText(text)
            self._app_power_label.setStyleSheet(_style(color))
        elif self._app_power_mah is not None:
            self._app_power_label.setText(f'🔌 ~{self._app_power_mah:.1f}mAh (缓存)')
            self._app_power_label.setStyleSheet(_style('#999999'))
        elif self._app_power_no_data:
            self._app_power_label.setText('🔌 不可用')
            self._app_power_label.setStyleSheet(_style('#ff9866'))
        elif self._app_power_error:
            self._app_power_label.setText('🔌 查询失败')
            self._app_power_label.setStyleSheet(_style('#ff6b6b'))
        else:
            self._app_power_label.setText('🔌 采样中…')
            self._app_power_label.setStyleSheet(_style('#999999'))

    # ---- 电池信息标签 (独立标签, 放在设备信息上方) ----
    def _update_battery_label(self, battery_info):
        """更新电池信息标签: 电量/电压/电流/温度/充放电状态。"""
        if not battery_info:
            self._battery_label.setText('🔋 电池: 采样中…')
            self._battery_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: #999999; '
                f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
                f'border-radius: 4px;')
            return

        bat_parts = []
        lvl = battery_info.get('level')
        if lvl is not None:
            bat_parts.append(f'{lvl}%')
        vol = battery_info.get('voltage_mv')
        if vol is not None:
            bat_parts.append(f'{vol / 1000.0:.2f}V')
        cur = battery_info.get('current_ua')
        if cur is not None:
            # 正=充电 负=放电
            if cur >= 0:
                bat_parts.append(f'+{cur / 1000.0:.0f}mA')
            else:
                bat_parts.append(f'{cur / 1000.0:.0f}mA')
        temp = battery_info.get('temp_c')
        if temp is not None:
            bat_parts.append(f'{temp:.1f}°C')
        charging = battery_info.get('charging')
        if charging is not None:
            bat_parts.append('⚡充电中' if charging else '🔋放电中')

        if not bat_parts:
            self._battery_label.setText('🔋 电池: 解析失败')
            self._battery_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: #ff9866; '
                f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
                f'border-radius: 4px;')
            return

        self._battery_label.setText('🔋 电池: ' + ' | '.join(bat_parts))
        # 电量低 + 放电中 → 红色提醒
        if lvl is not None and lvl <= 20 and not charging:
            color = '#ff6b6b'
        else:
            color = '#98c379'  # 默认绿色 (电量充足)
        self._battery_label.setStyleSheet(
            f'font: 10pt "{FONT_FAMILY}"; color: {color}; '
            f'background: rgba(255,255,255,0.05); padding: 4px 8px; '
            f'border-radius: 4px;')

    # ---- ANR 检测栏 ----
    def _update_anr_detection(self, data):
        """更新 ANR 检测标签。"""
        anr_crash = data.get('anr_crash')
        pid = data.get('pid')

        def _set(text, color, bg='rgba(255,255,255,0.05)'):
            self._anr_label.setText(text)
            self._anr_label.setStyleSheet(
                f'font: 10pt "{FONT_FAMILY}"; color: {color}; '
                f'background: {bg}; padding: 4px 8px; '
                f'border-radius: 4px;')

        if anr_crash:
            snippet = anr_crash[:100] if len(anr_crash) > 100 else anr_crash
            _set(f'ANR 检测: ⚠ 应用无响应 — {snippet}',
                 '#ff6b6b', 'rgba(255,107,107,0.12)')
        elif not pid:
            _set('ANR 检测: 进程未运行', '#999999')
        else:
            _set('ANR 检测: 正常', '#98c379')

    # ---- 扩展信息栏 (GC/WakeLock/CPU温度/存储/掉电速率) ----
    def _update_extra_info(self, gc_count, wakelock_list, cpu_temp,
                           app_storage, battery_info, pid):
        """更新扩展信息标签: GC 次数 / WakeLock / CPU 温度 / 存储 / 掉电速率。"""
        if not pid:
            self._extra_info_label.setText('📊 扩展指标: 进程未运行')
            return

        parts = []

        # GC 次数
        if gc_count is not None:
            parts.append(f'🔄 GC: {gc_count}次')

        # CPU 温度
        if cpu_temp is not None:
            temp_color = ''
            if cpu_temp >= 60:
                temp_str = f'{cpu_temp:.0f}°C ⚠'
            else:
                temp_str = f'{cpu_temp:.0f}°C'
            parts.append(f'🌡 CPU温度: {temp_str}')

        # WakeLock
        if wakelock_list:
            parts.append(f'🔒 WakeLock: {len(wakelock_list)}个持有')
        else:
            parts.append('🔒 WakeLock: 无')

        # 应用存储
        if app_storage:
            parts.append(f'📦 存储: {app_storage}')

        # 电池掉电速率
        cur_level = battery_info.get('level') if battery_info else None
        cur_ts = time.time()
        if cur_level is not None and self._prev_battery_level is not None \
                and self._prev_battery_ts is not None:
            dt = cur_ts - self._prev_battery_ts
            if dt > 0 and not battery_info.get('charging', False):
                drain = self._prev_battery_level - cur_level
                if drain > 0:
                    rate_per_h = drain / dt * 3600
                    parts.append(f'📉 掉电: {rate_per_h:.1f}%/h')
                elif drain < 0:
                    parts.append('📉 掉电: 充电中')
                else:
                    parts.append('📉 掉电: 0%/h')
        self._prev_battery_level = cur_level
        self._prev_battery_ts = cur_ts

        text = '📊 扩展指标: ' + ' | '.join(parts) if parts else '📊 扩展指标: 采样中…'
        self._extra_info_label.setText(text)

    # ---- 启动耗时测量 ----
    def _on_startup_done(self, text, color):
        """Signal 槽: 在主线程安全更新启动结果标签。"""
        self._startup_result_label.setText(text)
        self._startup_result_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: {color}; background: transparent;')

    def _measure_startup(self):
        """点击按钮: 先关闭进程再启动, 测量冷启动耗时。

        流程:
        1. force-stop + 等 2s
        2. am start (非阻塞) + 记录起始时间
        3. 轮询 pidof → 检测进程启动 (process_started_ms)
        4. 轮询 dumpsys window | grep mCurrentFocus → 检测 Activity 就绪 (fully_started_ms)
        5. 展示结果

        全部 UI 更新通过 _startup_done Signal (跨线程安全)
        Watchdog: 30s 后强制显示"超时"并恢复按钮
        """
        self._startup_result_label.setText('准备中…')
        self._startup_result_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #e5c07b; background: transparent;')
        self._btn_startup.setEnabled(False)
        QApplication.processEvents()

        # Watchdog: 30s 后强制重置
        def _watchdog():
            if not self._btn_startup.isEnabled():
                self._startup_done.emit('超时 (>30s)', '#ff6b6b')
                self._btn_startup.setEnabled(True)

        self._startup_watchdog = QTimer(self)
        self._startup_watchdog.setSingleShot(True)
        self._startup_watchdog.timeout.connect(_watchdog)
        self._startup_watchdog.start(30000)

        def _do_measure():
            try:
                # 1. 先关闭
                self._startup_done.emit('关闭进程…', '#e5c07b')
                try:
                    self._adb.执行shell(
                        self._serial, f'am force-stop {self._package}',
                        timeout=5)
                except Exception:
                    pass
                time.sleep(2.0)

                # 2. 启动默认 Activity (多策略兼容)
                #    不同设备/ROM 可能缺少 monkey, 多种方式兜底
                self._startup_done.emit('启动中…', '#e5c07b')
                t0 = time.time()
                startup_log = []
                started_ok = False

                # 2a. 先确保有 main activity 信息 (复用缓存)
                if not self._main_activity_fetched:
                    self._main_activity_fetched = True
                    if not self._last_raw_pkg:
                        try:
                            pkg_raw = self._adb.执行shell(
                                self._serial,
                                f'dumpsys package {self._package}',
                                timeout=8)
                            self._last_raw_pkg = pkg_raw or ''
                        except Exception:
                            pass
                    self._main_activity = _parse_main_activity(
                        self._last_raw_pkg)

                # 策略 A: monkey -p <pkg> -c LAUNCHER 1 (首选, 最稳定)
                try:
                    raw_a = self._adb.执行shell(
                        self._serial,
                        f'monkey -p {self._package} '
                        f'-c android.intent.category.LAUNCHER 1',
                        timeout=8) or ''
                    startup_log.append(f'[monkey] {raw_a.strip()}')
                    if 'Events injected' in raw_a:
                        started_ok = True
                except Exception as e:
                    startup_log.append(f'[monkey] 异常: {e}')

                # 策略 B: am start -n <pkg>/<activity> (有 main activity 时)
                if not started_ok and self._main_activity:
                    try:
                        raw_b = self._adb.执行shell(
                            self._serial,
                            f'am start -n {self._main_activity}',
                            timeout=8) or ''
                        startup_log.append(
                            f'[am -n {self._main_activity}] {raw_b.strip()}')
                        if 'Starting:' in raw_b \
                                and 'Error' not in raw_b:
                            started_ok = True
                    except Exception as e:
                        startup_log.append(f'[am -n] 异常: {e}')

                # 策略 C: am start -a MAIN -c LAUNCHER <pkg> (兜底)
                if not started_ok:
                    try:
                        raw_c = self._adb.执行shell(
                            self._serial,
                            f'am start '
                            f'-a android.intent.action.MAIN '
                            f'-c android.intent.category.LAUNCHER '
                            f'{self._package}',
                            timeout=8) or ''
                        startup_log.append(
                            f'[am intent] {raw_c.strip()}')
                        # 不管返回, 交给 pidof 轮询验证
                        started_ok = True
                    except Exception as e:
                        startup_log.append(f'[am intent] 异常: {e}')

                self._last_raw_startup = '\n'.join(startup_log)

                # 3. 轮询 pidof 检测进程启动
                self._startup_done.emit('等待启动…', '#e5c07b')
                pid = None
                for _ in range(40):        # 最多 40 × 250ms = 10s
                    time.sleep(0.25)
                    try:
                        pid_raw = self._adb.执行shell(
                            self._serial, f'pidof {self._package}', timeout=2)
                        pid = pid_raw.strip()
                        if pid:
                            break
                    except Exception:
                        pass

                process_started_ms = int((time.time() - t0) * 1000) if pid else None

                # 4. 轮询 dumpsys window | grep mCurrentFocus 检测 Activity 就绪
                #    mCurrentFocus 比 dumpsys activity activities 快得多
                self._startup_done.emit('等待就绪…', '#e5c07b')
                fully_started_ms = None
                for _ in range(16):        # 最多 16 × 500ms = 8s
                    time.sleep(0.5)
                    try:
                        win_raw = self._adb.执行shell(
                            self._serial,
                            'dumpsys window | grep -i "mCurrentFocus"',
                            timeout=3)
                        if self._package in (win_raw or ''):
                            fully_started_ms = int((time.time() - t0) * 1000)
                            break
                    except Exception:
                        pass

                # 5. 取消 watchdog
                self._startup_watchdog.stop()

                # 6. 存储测量结果
                self._startup_first_frame_ms = process_started_ms
                self._startup_fully_ms = fully_started_ms
                self._startup_state = 'OK' if (process_started_ms and fully_started_ms) else (
                    'PARTIAL' if process_started_ms else 'FAILED')

                # 7. 展示结果
                if process_started_ms is not None and fully_started_ms is not None:
                    text = f'启动 {process_started_ms}ms | 就绪 {fully_started_ms}ms'
                    if fully_started_ms > 5000:
                        color = '#ff6b6b'
                    elif fully_started_ms < 2000:
                        color = '#98c379'
                    else:
                        color = '#e5c07b'
                    self._startup_done.emit(text, color)
                elif process_started_ms is not None:
                    text = f'启动 {process_started_ms}ms (未检测到就绪)'
                    self._startup_done.emit(text, '#e5c07b')
                else:
                    self._startup_done.emit('启动失败 (未检测到进程)', '#ff6b6b')

            except Exception as e:
                self._startup_watchdog.stop()
                err = str(e)[:60]
                self._startup_done.emit(f'错误: {err}', '#ff6b6b')
            finally:
                self._btn_startup.setEnabled(True)
                self._startup_watchdog.stop()

        threading.Thread(target=_do_measure, daemon=True).start()

    # ---- 设备信息栏 (一次性获取, 启动后 ~1s 内就绪) ----
    def _update_app_info_label(self):
        """更新应用包信息标签 (版本号/安装时间/SDK等, 仅一次)。"""
        if not self._app_info_fetched:
            return
        # 已经更新过就不再重复
        if self._app_info_label.text() != '📦 应用信息: 获取中…':
            return
        info = self._app_info
        if not info:
            self._app_info_label.setText('📦 应用信息: 未获取到 (设备可能不支持)')
            return

        parts = []
        if 'versionName' in info:
            ver = f"v{info['versionName']}"
            if 'versionCode' in info:
                ver += f" ({info['versionCode']})"
            parts.append(ver)
        if 'targetSdk' in info:
            sdk_str = f"Target SDK {info['targetSdk']}"
            if 'minSdk' in info:
                sdk_str += f" / Min SDK {info['minSdk']}"
            parts.append(sdk_str)
        if 'firstInstallTime' in info:
            parts.append(f"安装: {info['firstInstallTime']}")
        if 'lastUpdateTime' in info:
            parts.append(f"更新: {info['lastUpdateTime']}")
        if info.get('debuggable'):
            parts.append("🐛 Debuggable")
        if 'uid' in info:
            parts.append(f"UID: {info['uid']}")
        if 'dataDir' in info:
            parts.append(f"数据目录: {info['dataDir']}")

        self._app_info_label.setText(
            '📦 应用信息: ' + '  |  '.join(parts) if parts
            else '📦 应用信息: 无可用信息')

    def _记录图表结果(self, chart, stats_label, success):
        """记录图表数据获取结果，连续失败或连续全零超过阈值则自动隐藏图表。

        连续全零检测: 无论 success 与否, 只要有效值全为 0
        就计为一次"无数据", 连续超过阈值同样隐藏。
        """
        if chart in self._chart_hidden:
            return
        # 全零检测 —— 无论 success 与否都检测
        vals = list(chart._values)
        valid = [v for v in vals if v is not None]
        if valid and all(v == 0 for v in valid):
            self._chart_zero_count[chart] = self._chart_zero_count.get(chart, 0) + 1
            if self._chart_zero_count[chart] >= self._CHART_HIDE_THRESHOLD:
                chart.hide()
                if stats_label:
                    stats_label.hide()
                self._chart_hidden.add(chart)
                return
        else:
            self._chart_zero_count[chart] = 0

        if success:
            self._chart_fail_count[chart] = 0
        else:
            self._chart_fail_count[chart] = self._chart_fail_count.get(chart, 0) + 1
            if self._chart_fail_count[chart] >= self._CHART_HIDE_THRESHOLD:
                chart.hide()
                if stats_label:
                    stats_label.hide()
                self._chart_hidden.add(chart)

    def _update_device_getprop_box(self):
        """更新 getprop 属性文本框: 全量 getprop 按中文分组展示。

        与设备信息对话框共用同一份采集 (获取设备信息_方法B)。
        """
        if not self._device_info_fetched:
            self._device_getprop_edit.setPlainText('设备属性 (getprop) 获取中…')
            return
        result = self._device_info or {}
        if not result:
            self._device_getprop_edit.setPlainText('设备属性 (getprop): 无数据')
            return
        prop_text = result.get('getprop_text') or ''
        if not prop_text:
            self._device_getprop_edit.setPlainText('设备属性 (getprop): 无数据')
            return
        self._device_getprop_edit.setPlainText(prop_text)

    def _update_device_ids_box(self):
        """更新设备标识符文本框: IMEI/MAC/OAID/GAID/Android ID 等并发获取结果。

        与设备信息对话框共用同一份采集 (获取设备信息_方法B)。
        """
        if not self._device_info_fetched:
            self._device_ids_edit.setPlainText('设备标识符 获取中…')
            return
        result = self._device_info or {}
        if not result:
            self._device_ids_edit.setPlainText('设备标识符: 无数据')
            return
        ids = result.get('identifiers') or []
        if not ids:
            self._device_ids_edit.setPlainText('设备标识符: 无数据')
            return
        lines = []
        名称宽度 = max((len(n) for n, _ in ids), default=8)
        for 名称, 值 in ids:
            lines.append(f'  {名称:<{名称宽度}}  {值}')
        if result.get('error') and not result.get('ok'):
            lines.append(f'\n(部分失败: {result["error"]})')
        self._device_ids_edit.setPlainText('\n'.join(lines))

    # ---- 保留点数变更 ----
    def _on_max_points_changed(self, val):
        """SpinBox 值改变时更新所有图表的 deque 容量。"""
        self._max_points = val
        for chart in (self._cpu_chart, self._pss_chart, self._java_chart,
                      self._native_chart, self._gfx_chart,
                      self._thread_chart, self._jank_chart,
                      self._power_chart):
            chart.set_max_points(val)
        minutes = val * SAMPLE_INTERVAL_MS / 1000 / 60
        self._spin_points.setToolTip(
            f'采样间隔 {SAMPLE_INTERVAL_MS // 1000}s\n'
            f'当前 {val} 点 ≈ {minutes:.1f} 分钟')

    # ---- 导出 HTML 报告 ----
    def _export_html(self):
        """将当前全部采样数据导出为自包含 HTML 报告 (含交互式 Chart.js 图表)。

        从 7 个 ScrollChart 的 _values deque 直接读数据, 不依赖任何 widget
        geometry / grab(), 彻底避开 QScrollArea 高度压缩问题。
        保存到桌面 \\Super_ADB\\app_perf_<包名>_<时间戳>.html
        """
        # ---- 1. 收集 8 张图的数据 ----
        charts_data = [
            ('cpu',  'CPU 使用率',         '#1de9b6', '%',  self._cpu_chart),
            ('pss',  '内存 PSS (TOTAL)',  '#ffab40', 'MB', self._pss_chart),
            ('java', 'Java Heap',         '#61afef', 'MB', self._java_chart),
            ('native','Native Heap',      '#e06c75', 'MB', self._native_chart),
            ('gfx',  'Graphics 显存',     '#c678dd', 'MB', self._gfx_chart),
            ('thread','线程数',           '#d19a66', '',   self._thread_chart),
            ('jank', 'Jank 丢帧率',       '#56b6c2', '%',  self._jank_chart),
            ('power', '应用耗电',         '#ff6b9d', 'mAh', self._power_chart),
            ('fps',   'FPS 帧率',         '#e5c07b', 'fps', self._fps_chart),
            ('net',   '网络流量 (TX+RX)', '#61afef', 'KB/s', self._net_chart),
            ('fd',    '文件描述符 (FD)',   '#e06c75', '',   self._fd_chart),
            ('io',    '磁盘 I/O (R+W)',   '#c678dd', 'KB/s', self._io_chart),
        ]

        # 构建 JS 数据: [null, 12.3, null, 45.6, ...] 格式
        # 过滤无数据图表: 全 null 或全 0 的不展示 (Graphics 全零保留, 显示占位提示)
        js_charts = []
        for key, title, color, unit, chart in charts_data:
            # 应用耗电: 设备不支持 UID 级耗电数据时跳过
            if key == 'power' and self._app_power_no_data:
                continue
            vals = list(chart._values)
            # JSON null for None (Chart.js spanGaps=false 自动断开)
            js_vals = [round(v, 2) if v is not None else None for v in vals]
            # 统计
            valid = [v for v in vals if v is not None]
            # 无有效数据 → 跳过
            if not valid:
                continue
            # 全零 → 跳过
            if all(v == 0 for v in valid):
                continue
            if valid:
                stats = {
                    'max': round(max(valid), 2),
                    'avg': round(sum(valid) / len(valid), 2),
                    'min': round(min(valid), 2),
                }
            else:
                stats = {'max': None, 'avg': None, 'min': None}
            js_charts.append({
                'id': key,
                'title': title,
                'color': color,
                'unit': unit,
                'y_max': round(chart._y_max, 1),
                'data': js_vals,
                'stats': stats,
            })

        # ---- 2. 收集状态文本 ----
        leak_text = self._leak_label.text()
        oom_text = self._oom_label.text()
        info_text = self._info_label.text()
        power_text = self._power_label.text()
        app_power_text = self._app_power_label.text()
        battery_text = self._battery_label.text()
        # 崩溃日志 (如果折叠容器可见, 说明检测到了 OOM/ANR 崩溃)
        crash_log_text = ''
        if self._crash_log_container.isVisible():
            crash_log_text = self._crash_log_browser.toPlainText()

        # 泄漏检测详情
        pss_st, pss_sl = _detect_leak(self._pss_chart._values)
        java_st, java_sl = _detect_leak(self._java_chart._values)
        native_st, native_sl = _detect_leak(self._native_chart._values)

        leak_details = [
            {'name': 'PSS',    'status': pss_st,    'slope': round(pss_sl, 2) if pss_sl else 0},
            {'name': 'Java',   'status': java_st,   'slope': round(java_sl, 2) if java_sl else 0},
            {'name': 'Native', 'status': native_st, 'slope': round(native_sl, 2) if native_sl else 0},
        ]

        heap_str = f'{self._max_heap_mb:.0f}MB' if self._max_heap_mb else '未知'
        uid_str = str(self._uid) if self._uid else '未知'
        app_power_str = f'{self._app_power_mah:.1f}mAh' if self._app_power_mah else '未获取'
        total_power_str = f'{self._total_power_mah:.0f}mAh' if self._total_power_mah else '未获取'

        # 电池信息
        bat = self._battery_info
        bat_str = ''
        if bat:
            bat_parts = []
            if 'level' in bat:
                bat_parts.append(f"{bat['level']}%")
            if 'voltage_mv' in bat:
                bat_parts.append(f"{bat['voltage_mv'] / 1000.0:.2f}V")
            if 'temp_c' in bat:
                bat_parts.append(f"{bat['temp_c']:.1f}°C")
            if 'charging' in bat:
                bat_parts.append('充电中' if bat['charging'] else '放电中')
            bat_str = ' | '.join(bat_parts) if bat_parts else '未获取'

        # 设备信息 (与设备信息弹窗一致: getprop 属性 + 设备标识符)
        dev_info = self._device_info or {}
        device_getprop_text = dev_info.get('getprop_text') or '设备属性未获取'
        ids = dev_info.get('identifiers') or []
        if ids:
            名称宽度 = max((len(n) for n, _ in ids), default=8)
            device_ids_text = '\n'.join(
                f'  {名称:<{名称宽度}}  {值}' for 名称, 值 in ids)
        else:
            device_ids_text = '设备标识符未获取'
        if dev_info.get('error') and not dev_info.get('ok'):
            device_ids_text += f'\n\n(部分失败: {dev_info["error"]})'

        # ---- 3. 生成 HTML ----
        # ANR 标签文本
        anr_text = self._anr_label.text()
        # 扩展信息标签文本
        extra_info_text = self._extra_info_label.text()
        # 启动耗时结果
        startup_text = self._startup_result_label.text()
        # 启动耗时结构化数据
        if self._startup_first_frame_ms is not None or self._startup_fully_ms is not None:
            startup_detail = (
                f'启动状态: {self._startup_state or "未知"}\n'
                f'首帧时间: {self._startup_first_frame_ms}ms{"\n" if self._startup_fully_ms is not None else ""}'
            )
            if self._startup_fully_ms is not None:
                startup_detail += f'完全启动: {self._startup_fully_ms}ms\n'
                if self._startup_first_frame_ms is not None:
                    startup_detail += (
                        f'差值(完全-首帧): {self._startup_fully_ms - self._startup_first_frame_ms}ms '
                        f'(闪屏/初始化耗时)\n'
                    )
        else:
            startup_detail = '未测量或测量失败'
        # 应用包信息
        app_info_text = self._app_info_label.text()

        report = {
            'package': self._package,
            'serial': self._serial,
            'pid': self._pid or '未知',
            'uid': uid_str,
            'start_time': self._start_time,
            'export_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'sample_interval_s': SAMPLE_INTERVAL_MS // 1000,
            'max_points': self._max_points,
            'max_heap_mb': heap_str,
            'app_power_mah': app_power_str,
            'total_power_mah': total_power_str,
            'battery_info': bat_str,
            'info_text': info_text,
            'leak_text': leak_text,
            'oom_text': oom_text,
            'anr_text': anr_text,
            'extra_info_text': extra_info_text,
            'startup_text': startup_text,
            'startup_detail': startup_detail,
            'app_info_text': app_info_text,
            'crash_log_text': crash_log_text,
            'power_text': power_text,
            'app_power_text': app_power_text,
            'battery_text': battery_text,
            'device_getprop_text': device_getprop_text,
            'device_ids_text': device_ids_text,
            'leak_details': leak_details,
            'charts': js_charts,
        }

        html = self._build_html_template(report)

        # ---- 4. 保存到桌面 \Super_ADB ----
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        save_dir = os.path.join(desktop, 'Super_ADB')
        os.makedirs(save_dir, exist_ok=True)
        safe_pkg = re.sub(r'[^\w.]', '_', self._package)
        ts = time.strftime('%Y%m%d_%H%M%S')
        filename = f'app_perf_{safe_pkg}_{ts}.html'
        filepath = os.path.join(save_dir, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

        old = self._status_label.text()
        self._status_label.setText(f'已导出报告 → {filepath}')
        QTimer.singleShot(5000, lambda: self._status_label.setText(old))

    @staticmethod
    def _build_html_template(r):
        """生成自包含 HTML 报告。r 是包含全部数据的 dict。

        改进点:
          - Y 轴自适应缩放 (非百分比图表不强制 beginAtZero)
          - X 轴时间标签 (显示采样序号 + 相对时间)
          - 数据只写一次 (JS 从 reportData 动态生成图表, 不重复)
          - 统计数字格式化 (整数不带 .0)
          - Graphics 全零时显示占位提示
          - 泄漏状态中文化
          - CDN 离线降级提示
          - 打印 / 保存 PDF 按钮
          -         鼠标悬浮十字准线 + tooltip 显示采样序号
        """
        def _strip_prefix(text, prefixes):
            # 去掉状态文本里的冗余中文前缀（如 "内存泄漏检测: "）
            for p in prefixes:
                if text.startswith(p):
                    return text[len(p):]
            return text

        data_json = json.dumps(r, ensure_ascii=False)

        # 泄漏状态中文映射
        leak_zh = {
            'leak': '疑似泄漏', 'warning': '缓慢增长',
            'stable': '稳定', 'declining': '下降中',
            'insufficient': '数据不足',
        }
        leak_colors = {
            'leak': '#ff6b6b', 'warning': '#e5c07b',
            'stable': '#98c379', 'declining': '#56b6c2',
            'insufficient': '#999999',
        }
        leak_icons = {
            'leak': '\u26a0\ufe0f', 'warning': '\u2191', 'stable': '\u2705',
            'declining': '\u2193', 'insufficient': '\u25cb',
        }

        # 构建 leak details rows
        leak_rows = ''
        for d in r['leak_details']:
            c = leak_colors.get(d['status'], '#999')
            icon = leak_icons.get(d['status'], '\u25cb')
            status_zh = leak_zh.get(d['status'], d['status'])
            sign = '+' if d['slope'] > 0 else ''
            leak_rows += (
                f'\n            <tr>'
                f'<td style="color:{c}">{icon} {d["name"]}</td>'
                f'<td style="color:{c}">{sign}{d["slope"]:.1f} MB/min</td>'
                f'<td style="color:{c}">{status_zh}</td>'
                f'</tr>')

        # 清理状态栏文本 (去掉冗余前缀)
        leak_text = _strip_prefix(r['leak_text'], ('内存泄漏检测: ', '内存泄漏检测:'))
        oom_text = _strip_prefix(r['oom_text'], ('内存溢出检测: ', '内存溢出检测:'))
        power_text = _strip_prefix(r.get('power_text', ''), ('运行信息: ', '运行信息:'))

        # 有效数据点数
        first_chart = r['charts'][0] if r['charts'] else {'data': []}
        valid_count = len([v for v in first_chart['data'] if v is not None])
        total_count = len(first_chart['data'])

        # HTML 模板 (用占位符避免 f-string 的 {{ }} 转义噩梦)
        template = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>应用性能报告 — __PACKAGE__</title>
<script>__CHART_JS__</script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1e1e1e; color: #dcdcdc;
    font-family: "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
    padding: 20px; max-width: 1200px; margin: 0 auto;
  }
  h1 { color: #1de9b6; font-size: 22px; margin-bottom: 4px; }
  .meta { color: #999; font-size: 13px; margin-bottom: 16px; line-height: 1.8; }
  .meta span { margin-right: 20px; }
  .status-bar {
    background: rgba(255,255,255,0.05); border-radius: 6px;
    padding: 10px 16px; margin-bottom: 8px; font-size: 14px;
  }
  .leak-bar { border-left: 3px solid #e5c07b; }
  .oom-bar { border-left: 3px solid #ff6b6b; }
  .anr-bar { border-left: 3px solid #56b6c2; }
  .grid {
    display: grid; grid-template-columns: 1fr 1fr;
    gap: 16px; margin-top: 16px;
  }
  .card {
    background: #2b2b2b; border-radius: 8px; padding: 16px;
    border: 1px solid #333;
  }
  .card h3 { color: #bbb; font-size: 14px; margin-bottom: 8px; }
  .chart-box { height: 200px; position: relative; }
  .chart-placeholder {
    height: 200px; display: flex; align-items: center; justify-content: center;
    color: #666; font-size: 14px; border: 1px dashed #444; border-radius: 4px;
  }
  .stats {
    display: flex; gap: 20px; margin-top: 8px;
    font-size: 12px; color: #999;
  }
  .stats span { color: #ddd; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; }
  td { padding: 6px 12px; border-bottom: 1px solid #333; }
  .footer {
    color: #666; font-size: 12px; margin-top: 20px;
    display: flex; justify-content: space-between; align-items: center;
  }
  .btn-print {
    background: #333; color: #dcdcdc; border: 1px solid #555;
    border-radius: 4px; padding: 4px 12px; cursor: pointer;
    font-size: 12px; font-family: inherit;
  }
  .btn-print:hover { background: #444; }
  .cdn-fail { display: none; color: #ff6b6b; text-align: center; padding: 40px; font-size: 16px; }
  /* 崩溃/ANR 日志折叠 */
  .log-card {
    border: 1px solid rgba(255, 107, 107, 0.3);
    background: rgba(255, 107, 107, 0.06);
  }
  .log-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 8px;
    flex-wrap: wrap;
  }
  .log-header h3 { margin: 0; }
  .log-summary {
    color: #999;
    font-size: 12px;
    margin-left: auto;
  }
  .log-toggle {
    background: rgba(255, 107, 107, 0.15);
    color: #ff6b6b;
    border: 1px solid rgba(255, 107, 107, 0.4);
    border-radius: 4px;
    padding: 4px 12px;
    cursor: pointer;
    font-size: 12px;
    font-family: inherit;
  }
  .log-toggle:hover { background: rgba(255, 107, 107, 0.25); }
  .log-content {
    margin: 0;
    font: 10pt ui-monospace, "Cascadia Code", Consolas, "Courier New", monospace;
    color: #e07070;
    white-space: pre-wrap;
    line-height: 1.5;
    transition: max-height 0.3s ease;
    overflow: hidden;
  }
  .log-content.collapsed {
    max-height: 240px;
    mask-image: linear-gradient(to bottom, #000 70%, transparent 100%);
    -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent 100%);
  }
  /* 设备信息可折叠区域 */
  .device-section {
    margin-bottom: 8px;
  }
  .device-toggle {
    width: 100%;
    text-align: left;
    background: rgba(255,255,255,0.05);
    color: #1de9b6;
    border: 1px solid #333;
    border-radius: 6px;
    padding: 8px 12px;
    cursor: pointer;
    font-size: 14px;
    font-family: inherit;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .device-toggle:hover { background: rgba(255,255,255,0.1); }
  .device-toggle.ids { color: #c678dd; border-left: 3px solid #c678dd; }
  .device-toggle.props { border-left: 3px solid #1de9b6; }
  .device-content {
    max-height: 400px;
    overflow-y: auto;
    overflow-x: auto;
    background: rgba(0,0,0,0.25);
    border: 1px solid #444;
    border-top: none;
    border-radius: 0 0 6px 6px;
    padding: 10px 12px;
    font: 10pt ui-monospace, "Cascadia Code", Consolas, "Courier New", monospace;
    color: #dcdcdc;
    white-space: pre;
    line-height: 1.5;
    transition: max-height 0.3s ease, padding 0.3s ease;
  }
  .device-content.collapsed {
    max-height: 0;
    padding-top: 0;
    padding-bottom: 0;
    overflow: hidden;
  }
  .device-arrow { transition: transform 0.3s ease; }
  .device-arrow.collapsed { transform: rotate(-90deg); }
  @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
  @media print {
    .btn-print { display: none; }
    body { padding: 0; max-width: none; }
    .log-content { max-height: none !important; -webkit-mask-image: none !important; mask-image: none !important; }
    .log-toggle { display: none; }
    .log-summary { display: none; }
    .device-content { max-height: none !important; overflow: visible !important; }
    .device-toggle { display: none; }
  }
</style>
</head>
<body>
  <h1>应用性能监控报告</h1>
  <div class="meta">
    <span>📦 包名: <strong style="color:#dcdcdc">__PACKAGE__</strong></span>
    <span>🔑 PID: <strong style="color:#dcdcdc">__PID__</strong></span>
    <span>UID: <strong style="color:#dcdcdc">__UID__</strong></span>
    <span>📱 设备: <strong style="color:#dcdcdc">__SERIAL__</strong></span>
    <span>🧰 Java 堆上限: <strong style="color:#dcdcdc">__MAX_HEAP__</strong></span><br>
    <span>🔋 电池: <strong style="color:#dcdcdc">__BATTERY_INFO__</strong></span>
    <span>🔌 应用耗电: <strong style="color:#dcdcdc">__APP_POWER__</strong></span>
    <span>总耗电: <strong style="color:#dcdcdc">__TOTAL_POWER__</strong></span><br>
    <span>🕐 开始时间: __START_TIME__</span>
    <span>📊 采样间隔: __SAMPLE_INTERVAL__s</span>
    <span>📈 保留点数: __MAX_POINTS__</span>
    <span>📊 有效数据: <strong style="color:#1de9b6">__VALID_COUNT__</strong> / __TOTAL_COUNT__</span>
  </div>

  <div class="status-bar leak-bar">🔍 __LEAK_TEXT__</div>
  <div class="status-bar oom-bar">🚨 __OOM_TEXT__</div>
  <div class="status-bar anr-bar">⏱️ __ANR_TEXT__</div>
  __CRASH_LOG_SECTION__
  <div class="status-bar" style="border-left: 3px solid #56b6c2;">__POWER_TEXT__</div>
  <div class="status-bar" style="border-left: 3px solid #ff6b9d;">__APP_POWER_TEXT__</div>
  <div class="status-bar" style="border-left: 3px solid #98c379;">__BATTERY_TEXT__</div>
  <div class="status-bar" style="border-left: 3px solid #d19a66;">__EXTRA_INFO_TEXT__</div>
  <div class="status-bar" style="border-left: 3px solid #1de9b6;">🚀 启动耗时: __STARTUP_TEXT__</div>
  <div class="card" style="margin-top:8px;border-left:3px solid #1de9b6;">
    <h3 style="color:#1de9b6;">启动耗时详情</h3>
    <pre style="font: 11pt ui-monospace, 'Cascadia Code', Consolas, 'Courier New', monospace; color: #dcdcdc; white-space: pre-wrap; line-height: 1.6;">__STARTUP_DETAIL__</pre>
  </div>

  <div class="device-section" style="margin-top:12px;">
    <button class="device-toggle props" onclick="toggleDevice(this)">
      <span>📋 设备属性 (getprop 全量, 按中文分组)</span>
      <span class="device-arrow collapsed">▼</span>
    </button>
    <div class="device-content collapsed">__DEVICE_GETPROP_TEXT__</div>
  </div>

  <div class="device-section">
    <button class="device-toggle ids" onclick="toggleDevice(this)">
      <span>🔖 设备标识符 (IMEI/MAC/OAID/GAID/Android ID 等, 并发获取)</span>
      <span class="device-arrow collapsed">▼</span>
    </button>
    <div class="device-content collapsed">__DEVICE_IDS_TEXT__</div>
  </div>

  <div class="card" style="margin-top:8px;border-left:3px solid #61afef;">
    <h3 style="color:#61afef;">应用包信息</h3>
    <pre style="font: 11pt ui-monospace, 'Cascadia Code', Consolas, 'Courier New', monospace; color: #dcdcdc; white-space: pre-wrap; line-height: 1.6;">__APP_INFO_TEXT__</pre>
  </div>

  <div id="chart-grid" class="grid"></div>
  <div id="cdn-fail" class="cdn-fail">⚠️ Chart.js 加载失败，请检查网络连接后刷新页面</div>

  <div class="card" style="margin-top:16px;">
    <h3>内存泄漏检测详情 (线性回归斜率)</h3>
    <table>
      <tr style="color:#666"><td>指标</td><td>斜率</td><td>状态</td></tr>__LEAK_ROWS__
    </table>
  </div>

  <div class="footer">
    <span>报告生成时间: __EXPORT_TIME__</span>
    <button class="btn-print" onclick="window.print()">🖨️ 打印 / 保存 PDF</button>
  </div>

<script>
  var reportData = __DATA_JSON__;

  // 数字格式化: 整数不带 .0, 小数保留合理位数
  function fmtNum(v, unit) {
    if (v === null || v === undefined) return '--';
    if (unit === '%') return v.toFixed(2) + '%';
    if (unit === '') return Number.isInteger(v) ? v.toString() : v.toFixed(1);
    // MB
    if (v < 1) return v.toFixed(2) + unit;
    if (v < 10) return v.toFixed(2) + unit;
    return v.toFixed(1) + unit;
  }

  // Y 轴自适应: 百分比从 0 开始, 其余根据数据范围缩放
  function computeYRange(data, isPercent, fallbackMax) {
    var valid = data.filter(function(v) { return v !== null && v !== undefined; });
    if (valid.length === 0) return { min: 0, max: fallbackMax || 100 };
    var dmin = Math.min.apply(null, valid);
    var dmax = Math.max.apply(null, valid);
    if (isPercent) return { min: 0, max: Math.max(dmax * 1.2, 10) };
    var range = dmax - dmin;
    var pad = range > 0 ? range * 0.15 : Math.max(dmax * 0.1, 1);
    return {
      min: Math.max(0, dmin - pad),
      max: dmax + pad
    };
  }

  // 判断是否全零 (用于 Graphics 占位)
  function isAllZero(data) {
    return data.every(function(v) { return v === null || v === undefined || v === 0; });
  }

  document.addEventListener('DOMContentLoaded', function() {
    // 日志折叠初始化不依赖 Chart.js，优先执行
    initLogCollapse();

    if (typeof Chart === 'undefined') {
      document.getElementById('cdn-fail').style.display = 'block';
      return;
    }

    var grid = document.getElementById('chart-grid');
    var interval = reportData.sample_interval_s;

    reportData.charts.forEach(function(c) {
      // 创建卡片
      var card = document.createElement('div');
      card.className = 'card';

      var title = document.createElement('h3');
      title.textContent = c.title;
      card.appendChild(title);

      // Graphics 全零时显示占位
      var allZero = isAllZero(c.data) && c.id === 'gfx';
      if (allZero) {
        var ph = document.createElement('div');
        ph.className = 'chart-placeholder';
        ph.textContent = '未分配显存 (Graphics PSS = 0)';
        card.appendChild(ph);
      } else {
        var box = document.createElement('div');
        box.className = 'chart-box';
        var canvas = document.createElement('canvas');
        canvas.id = 'chart_' + c.id;
        box.appendChild(canvas);
        card.appendChild(box);
      }

      // 统计行
      var statsDiv = document.createElement('div');
      statsDiv.className = 'stats';
      var valid = c.data.filter(function(v) { return v !== null && v !== undefined; });
      if (valid.length > 0) {
        var hi = Math.max.apply(null, valid);
        var lo = Math.min.apply(null, valid);
        var avg = valid.reduce(function(a, b) { return a + b; }, 0) / valid.length;
        statsDiv.innerHTML =
          '<span>最高: <strong>' + fmtNum(hi, c.unit) + '</strong></span>' +
          '<span>平均: <strong>' + fmtNum(avg, c.unit) + '</strong></span>' +
          '<span>最低: <strong>' + fmtNum(lo, c.unit) + '</strong></span>';
      } else {
        statsDiv.innerHTML = '<span>无有效数据</span>';
      }
      card.appendChild(statsDiv);
      grid.appendChild(card);

      if (allZero) return;

      // 创建图表
      var isPercent = (c.unit === '%');
      var yRange = computeYRange(c.data, isPercent, c.y_max);

      var ctx = document.getElementById('chart_' + c.id).getContext('2d');
      new Chart(ctx, {
        type: 'line',
        data: {
          labels: c.data.map(function(_, i) {
            var sec = i * interval;
            return sec < 60 ? sec + 's' : Math.floor(sec/60) + 'm' + (sec%60 ? (sec%60)+'s' : '');
          }),
          datasets: [{
            label: c.title,
            data: c.data,
            borderColor: c.color,
            backgroundColor: c.color + '22',
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            pointHoverBackgroundColor: c.color,
            pointHoverBorderColor: '#fff',
            pointHoverBorderWidth: 2,
            tension: 0.3,
            fill: true,
            spanGaps: false
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          interaction: { mode: 'index', intersect: false },
          plugins: {
            legend: { display: true, labels: { color: '#bbb', font: { size: 13 } } },
            tooltip: {
              backgroundColor: 'rgba(30,30,30,0.95)',
              titleColor: '#fff',
              bodyColor: '#ddd',
              borderColor: c.color,
              borderWidth: 1,
              callbacks: {
                title: function(items) {
                  var idx = items[0].dataIndex;
                  var sec = idx * interval;
                  var t = sec < 60 ? sec + 's' : Math.floor(sec/60) + 'm' + (sec%60 ? (sec%60)+'s' : '');
                  return '采样 #' + (idx+1) + ' (' + t + ')';
                },
                label: function(ctx) {
                  var v = ctx.parsed.y;
                  return ctx.dataset.label + ': ' + (v === null ? 'N/A' : fmtNum(v, c.unit));
                }
              }
            }
          },
          scales: {
            x: {
              display: true,
              grid: { display: false },
              ticks: {
                color: '#888', font: { size: 10 },
                maxRotation: 0,
                autoSkip: true, maxTicksLimit: 8
              }
            },
            y: {
              beginAtZero: isPercent,
              suggestedMin: yRange.min,
              suggestedMax: yRange.max,
              grid: { color: 'rgba(255,255,255,0.06)' },
              ticks: {
                color: '#888', font: { size: 11 },
                callback: function(v) { return v + c.unit; }
              }
            }
          }
        }
      });
    });
  });

  // 崩溃/ANR 日志折叠控制
  function initLogCollapse() {
    var card = document.querySelector('.log-card');
    if (!card) return;
    var pre = card.querySelector('.log-content');
    var summary = card.querySelector('.log-summary');
    var btn = card.querySelector('.log-toggle');
    var text = pre.textContent || '';
    var lines = text.split(String.fromCharCode(10)).length;
    var threshold = 10;
    if (lines > threshold) {
      pre.classList.add('collapsed');
      var folded = Math.max(0, lines - threshold);
      summary.textContent = '共 ' + lines + ' 行，已折叠 ' + folded + ' 行';
      btn.textContent = '展开 ▼';
    } else {
      pre.classList.remove('collapsed');
      summary.textContent = '共 ' + lines + ' 行';
      btn.style.display = 'none';
    }
  }

  function toggleLog(btn) {
    var card = btn.closest('.log-card');
    if (!card) return;
    var pre = card.querySelector('.log-content');
    var summary = card.querySelector('.log-summary');
    var lines = (pre.textContent || '').split(String.fromCharCode(10)).length;
    var threshold = 10;
    pre.classList.toggle('collapsed');
    var collapsed = pre.classList.contains('collapsed');
    if (collapsed) {
      var folded = Math.max(0, lines - threshold);
      summary.textContent = '共 ' + lines + ' 行，已折叠 ' + folded + ' 行';
      btn.textContent = '展开 ▼';
    } else {
      summary.textContent = '共 ' + lines + ' 行，全部展开';
      btn.textContent = '折叠 ▲';
    }
  }

  // 设备信息可折叠区域
  function toggleDevice(btn) {
    var section = btn.closest('.device-section');
    if (!section) return;
    var content = section.querySelector('.device-content');
    var arrow = btn.querySelector('.device-arrow');
    content.classList.toggle('collapsed');
    arrow.classList.toggle('collapsed');
  }
</script>
</body>
</html>'''

        # 占位符替换
        replacements = {
            '__DATA_JSON__': data_json,
            '__PACKAGE__': r['package'],
            '__PID__': str(r['pid']),
            '__SERIAL__': r['serial'],
            '__MAX_HEAP__': r['max_heap_mb'],
            '__START_TIME__': r['start_time'],
            '__EXPORT_TIME__': r['export_time'],
            '__SAMPLE_INTERVAL__': str(r['sample_interval_s']),
            '__MAX_POINTS__': str(r['max_points']),
            '__VALID_COUNT__': str(valid_count),
            '__TOTAL_COUNT__': str(total_count),
            '__LEAK_TEXT__': leak_text,
            '__OOM_TEXT__': oom_text,
            '__ANR_TEXT__': r.get('anr_text', ''),
            '__EXTRA_INFO_TEXT__': r.get('extra_info_text', ''),
            '__STARTUP_TEXT__': r.get('startup_text', '') or '未测量',
            '__STARTUP_DETAIL__': r.get('startup_detail', '未测量'),
            '__CRASH_LOG_SECTION__': (
                f'<div class="card log-card" style="margin:8px 0;">'
                f'<div class="log-header">'
                f'<h3 style="color:#ff6b6b;">崩溃 / ANR 日志</h3>'
                f'<span class="log-summary">计算中…</span>'
                f'<button class="log-toggle" onclick="toggleLog(this)">展开 ▼</button>'
                f'</div>'
                f'<pre class="log-content">'
                f'{r.get("crash_log_text", "").replace("<", "&lt;").replace(">", "&gt;")}</pre>'
                f'</div>'
            ) if r.get('crash_log_text') else '',
            '__POWER_TEXT__': power_text,
            '__APP_POWER_TEXT__': r.get('app_power_text', ''),
            '__BATTERY_TEXT__': r.get('battery_text', ''),
            '__DEVICE_GETPROP_TEXT__': r.get('device_getprop_text', '').replace('<', '&lt;').replace('>', '&gt;'),
            '__DEVICE_IDS_TEXT__': r.get('device_ids_text', '').replace('<', '&lt;').replace('>', '&gt;'),
            '__APP_INFO_TEXT__': r.get('app_info_text', ''),
            '__UID__': r.get('uid', '未知'),
            '__BATTERY_INFO__': r.get('battery_info', '未获取'),
            '__APP_POWER__': r.get('app_power_mah', '未获取'),
            '__TOTAL_POWER__': r.get('total_power_mah', '未获取'),
            '__LEAK_ROWS__': leak_rows,
            '__CHART_JS__': load_chart_js(),
        }
        result = template
        for k, v in replacements.items():
            result = result.replace(k, v)
        return result

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
        def _tail(s, n=2000):
            if not s:
                return '(空)'
            return '\n'.join(s.splitlines()[-n:])

        heap_str = f'{self._max_heap_mb:.0f}MB' if self._max_heap_mb else '未知'
        uid_str = str(self._uid) if self._uid else '未知'
        power_str = f'{self._app_power_mah:.1f}mAh' if self._app_power_mah else '未获取'
        text = (f'包名: {self._package}\n'
                f'PID: {self._pid or "未知"}\n'
                f'设备: {self._serial}\n'
                f'UID: {uid_str}\n'
                f'Java 堆上限: {heap_str}\n'
                f'应用耗电: {power_str}\n\n'
                f'===== top 输出 =====\n{_tail(self._last_raw_top)}\n\n'
                f'===== dumpsys meminfo 输出 =====\n{_tail(self._last_raw_mem)}\n\n'
                f'===== /proc/pid/status 输出 =====\n{_tail(self._last_raw_threads)}\n\n'
                f'===== /proc/pid/stat 输出 =====\n{_tail(self._last_raw_stat)}\n\n'
                f'===== /proc/uptime 输出 =====\n{_tail(self._last_raw_uptime)}\n\n'
                f'===== /proc/pid/fd + /proc/pid/io 输出 =====\n{_tail(self._last_raw_fd)}\n\n'
                f'===== 网络流量 (/proc/uid_stat) 输出 =====\n{_tail(self._last_raw_net)}\n\n'
                f'===== CPU 温度 (thermal_zone) 输出 =====\n{_tail(self._last_raw_temp)}\n\n'
                f'===== GC logcat 输出 =====\n{_tail(self._last_raw_gc)}\n\n'
                f'===== dumpsys power (WakeLock) 输出 =====\n{_tail(self._last_raw_wakelock)}\n\n'
                f'===== du -sh (应用存储) 输出 =====\n{_tail(self._last_raw_storage)}\n\n'
                f'===== am start -W (启动耗时) 输出 =====\n{_tail(self._last_raw_startup)}\n\n'
                f'===== dumpsys battery 输出 =====\n{_tail(self._last_raw_battery)}\n\n'
                f'===== dumpsys batterystats 输出 =====\n{_tail(self._last_raw_batterystats)}\n\n'
                f'===== dumpsys gfxinfo 输出 =====\n{_tail(self._last_raw_gfx)}\n\n'
                f'===== dumpsys package 输出 =====\n{_tail(self._last_raw_pkg)}\n\n'
                f'===== 崩溃/ANR 日志 (如有) =====\n'
                f'{self._crash_log_browser.toPlainText() if self._crash_log_container.isVisible() else "(无)"}')
        QApplication.clipboard().setText(text)
        old = self._status_label.text()
        self._status_label.setText('已复制调试信息到剪贴板 (含全部原始输出)')
        QTimer.singleShot(2500, lambda: self._status_label.setText(old))

    # ---- 关窗即停止 ----
    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        # 关闭日志：在途采样线程见到 _closed 会丢弃结果，定时器已停，无后台残留
        主 = self.parent()
        if 主 is not None and hasattr(主, '日志'):
            主.日志('[应用性能监控] 窗口已关闭，后台采样已停止')
        super().closeEvent(event)
