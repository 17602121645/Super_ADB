# 设备性能监控（DevicePerfMonitor）— 功能介绍

> 适用版本：Super_ADB 主窗口 → 系统操作 → 「设备性能监控」按钮
> 代码文件：`Super_ADB_Main/设备性能监控.py`（约 950 行，含多序列 ScrollChart）
> 入口：`main_window.btnDpm.clicked → open_perf_monitor()`
> 截图位置：本文档配套截图保存在 `feature_intro/device-perf-monitor-v2.png`（v2 四图版）

---

## 1. 功能概览

点击主窗口「设备性能监控」按钮，弹出一个**独立窗口**实时跟踪 Android 设备的整体健康度，**四张滚动走势图**一眼看清：

| 图表 | 数据源 | Y 轴范围 | 颜色 |
|---|---|---|---|
| **CPU 使用率** | `adb shell top -b -n 1`（失败回退 `top -n 1`）解析 `%Cpu(s):` 总占用 + `%Cpu0:`/`%Cpu1:`… 每核占用 | 0–100 % | 总 CPU 青绿 `#1de9b6`；各核自动分配区分色 |
| **内存占用** | `adb shell cat /proc/meminfo` | 0–总内存（MB，首次采样后自动适配） | 橙色 `#ffab40` |
| **网络上下行** | `adb shell cat /proc/net/dev`（跳过 lo，按字节差算速率） | 自适应 KB/s·MB/s | 接收 `#40c4ff` / 发送 `#b388ff` |
| **电池温度** | `adb shell dumpsys battery` 的 `temperature`（÷10 得 ℃） | 自适应 ℃ | 橙色 `#ffab40` |

每条曲线保留点数**可在顶部 SpinBox 配置（30–3600 点，即 1 分钟到 2 小时）**，默认 120 点；新点从右侧进入，旧点向左滚动消失。四张图共用同一套多序列 `ScrollChart`，便于发现"只在一个核跑满"的调度问题。

**每张图正下方实时显示「最高值 / 平均值 / 最低值」统计行**（与应用性能监控窗口完全一致）：CPU 取「总 CPU」主序列、内存取「内存」、网络取「↓接收」、电池取「温度」。无有效采样时显示 `--`。该统计行让"峰值温度/峰值占用/抖动幅度"一目了然，无需在曲线上目测。

辅助能力：

- ⏸ **暂停 / 继续** 按钮：暂停定时器但不关窗
- 📋 **复制调试** 按钮：把原始 `top` 输出丢进剪贴板，便于排查解析失败
- 💾 **导出 HTML** 按钮：把四张图的完整采样数据导出为离线 HTML 报告（Chart.js 绘制，保存到桌面 `Super_ADB/perf_device_<serial>_<时间戳>.html`，与应用性能监控报告**同目录、同风格**，且每张图也展示 最高/平均/最低 统计）
- 🪟 **关窗即停**：定时器停止，后台线程靠 daemon + `_closed` 标记自管生命周期
- 🟢 **复用窗口**：重复点击主窗口按钮不重复开窗，而是 `raise_() + activateWindow()`

---

## 2. 入口与触发

```
┌────────────────────────────────────────────────────┐
│  主窗口「系统操作」分区                              │
│   ┌────────────────┐                                │
│   │ 设备性能监控   │  ← btnDpm（红框标注）           │
│   └────────────────┘                                │
└────────────────────────────────────────────────────┘
```

点击后：

```python
def open_perf_monitor(self):
    serial = self._ensure_serial()                 # 1. 取当前选中设备
    if self._dpm_window and self._dpm_window.isVisible():
        self._dpm_window.raise_()                  # 2. 已开窗口：置顶
        self._dpm_window.activateWindow()
        return
    self._dpm_window = DevicePerfMonitor(serial, parent=self)  # 3. 新建
    self._dpm_window.show()
```

`_dpm_window` 是主窗口的成员属性（不在堆上飘着），保证唯一性。复用模式跟 `InstallZipDialog`、`MonkeyRunner`、`AppPerfMonitor` 一致。

---

## 3. 界面布局

截图复刻（窗口约 820×720，最小 760×600）：

![设备性能监控 v2 四图](device-perf-monitor-v2.png)

> 上图为真实运行截图：CPU（总+每核）/ 内存 / 网络上下行 / 电池温度 四张滚动图 + 顶部「保留点数」SpinBox + 「导出 HTML」按钮。下方为界面结构示意（仅画 CPU/内存两图作代表）：

```
╔ 设备性能监控 — emulator-5554 ━━━━━━━━━━━━━━━━━━━━━━━━ [—] [□] [×] ╗
║ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ ║
║ 采样时间: 01:59:29   CPU: 0.7%   内存: 15.1% (899/5944 MB)         ║
║                                                    [暂停] [复制调试] ║
║                                                                  ║
║  ┌─ CPU 使用率 ────────────────────────────────────────────────┐ ║
║  │ 75% ──────────────────────────────────────────────────────  │ ║
║  │ 50%                                                      ─  │ ║
║  │ 25% ─────────────────────                                 ─  │ ║
║  │  0% ─────────────────────                                 •  │ ║
║  │              最近 4/120 点 · 每 2s 采样                      │ ║
║  └────────────────────────────────────────────────────────────┘ ║
║                                                                  ║
║  ┌─ 内存占用 ─────────────────────────────────────────────────┐ ║
║  │ 5944MB ─────────────────────────────────────────────────── │ ║
║  │ 4458MB                                                  ─ │ ║
║  │ 2972MB                                                  ─ │ ║
║  │ 1486MB ─────────────────────                            • │ ║
║  │   0MB ─────────────────────                              │ ║
║  │              最近 4/120 点 · 每 2s 采样                      │ ║
║  └────────────────────────────────────────────────────────────┘ ║
║                                                                  ║
║   (CPU 解析失败时显示一行红色调试文本，自动 3s 后回到隐式状态)   ║
╚══════════════════════════════════════════════════════════════════╝
```

视觉风格与 `弹窗样式.HIGHLIGHT_CARD_STYLE` + `add_green_glow(card)` 一致——**绿色高亮边框 + 外发光**，与「应用性能监控」、「Monkey 压测」三大独立窗口一致。

---

## 4. 核心组件：ScrollChart（自绘折线图）

`ScrollChart` 是这个模块最值得讲的自定义组件——**不依赖 PyQtChart，全靠 `QPainter` 重画**。优势：体积小、可定制、零依赖。

> **v2 变化**：`ScrollChart` 从单序列升级为**多序列**（`__init__(title, series_specs, unit, y_max, max_points, auto_grow=False)`，其中 `series_specs=[(name, color_hex), ...]`）。`add_series(name, color)` / `add_point(name, value, failed)` 按序列名追加；每张图的「总 CPU + 每核」「接收 + 发送」等都靠它画出多条折线。下方绘制逻辑（按 None 分段、末点圆点、半透明填充）对每条序列同样适用。

### 4.1 关键字段

```python
class ScrollChart(QWidget):
    def __init__(self, title, color_hex, unit, y_max=100.0, max_points=None, parent=None):
        self._values = deque(maxlen=self._max_points)   # 环形缓冲
        self._failed = False                            # 最新点是否失败
        # 缓存 QFont/QColor/QPen（避免 paintEvent 高频调用时反复分配）
```

`deque(maxlen=120)` 配 `append` 实现**自动淘汰最旧点**——零维护成本。

### 4.2 三种数据状态

| 入参 | 含义 | 视觉表现 |
|---|---|---|
| `add_point(value, failed=False)` | 正常采样 | 折线延伸到该点；末点画实心圆 |
| `add_point(value, failed=True)` | 解析成功但视为失败 | 折线断开（按 None 分段），叠加 "获取失败" 文字 |
| `add_point(0, failed=True)` | 完整失败（CPU 走不通时） | 同上，并触发调试信息 |

### 4.3 按 None 分段绘制的妙处

```python
for i, v in enumerate(self._values):
    if v is None:
        if cur:
            segments.append(cur)    # 遇到 None 收尾一段
            cur = []
        continue
    x = cx + cw - (n - 1 - i) * spacing   # 右对齐：最新点在右边缘
    yv = min(max(v, 0.0), self._y_max)    # 数值钳制
    y = cy + ch * (1 - yv / self._y_max)  # 数值 → 像素 Y
    cur.append((x, y))
```

**"遇到失败就在折线上自然画出一个缺口"**——不需要额外的"失败点位"状态机，绘制逻辑自适应。一行代码胜过百行 if-else。

### 4.4 性能优化（与日志页同源思路）

```python
# 缓存绘制对象：paintEvent 每 2s 被调用两次（CPU+内存图），
# 避免 new QFont/QColor/QPen 的反复分配开销
self._bg_color = QColor(43, 43, 43)
self._title_font = QFont(FONT_FAMILY, 9, QFont.Bold)
self._line_pen = QPen(self._color, 2)
# ...
```

跟「应用性能监控」里的 `ScrollChart` 是同一套思路（见 `设备性能监控.py:185-199` 注释），高频重绘场景下能显著降低 CPU 占用。

### 4.5 末点圆点 + 半透明填充

```python
# 填充区域 (QPainterPath)
fp = QPainterPath()
fp.moveTo(QPointF(seg[0][0], cy + ch))     # 起点：折线左端往下落到 X 轴
for x, y in seg:
    fp.lineTo(QPointF(x, y))                # 走折线
fp.lineTo(QPointF(seg[-1][0], cy + ch))    # 终点：折线右端往下落到 X 轴
fp.closeSubpath()
p.setBrush(QBrush(self._fill_color))       # 半透明 (alpha=35) 填充
p.drawPath(fp)
```

`add_point()` 把 `self._color.setAlpha(35)` 作为填充色，既保留折线锐利感，又能看清背板网格。

---

## 5. 采样工作流（4 步拆解）

```
┌──────────┐  QTimer.timeout (每 2s)   ┌─────────────────────────┐
│  _tick   │ ────────────────────────► │ threading.Thread        │
│ (主线程) │                           │ target=_sample_task     │
└────┬─────┘                           │   ├─ adb top (timeout=10)│
     │ _sampling=True (防重叠)         │   ├─ adb cat /proc/mem..│
     ▼                                 │   └─ parse + emit dict  │
 立即返回,不阻塞 UI                      └────────────┬────────────┘
                                                       │ _sample_done.emit(dict)
                                                       ▼
                                          ┌─────────────────────────┐
                                          │ _on_sample (主线程)      │
                                          │  ├─ 更新 _info_label    │
                                          │  ├─ add_point → 折线     │
                                          │  └─ 失败时显示调试文本   │
                                          └─────────────────────────┘
```

### 5.1 `_tick`（主线程定时器）

```python
def _tick(self):
    if self._closed or self._paused or self._sampling:
        return                          # 三个哨兵快速跳过
    self._sampling = True
    threading.Thread(target=self._sample_task, daemon=True).start()
```

**三个哨兵**：
- `self._closed` —— 关窗标志
- `self._paused` —— 暂停标志
- `self._sampling` —— 防重叠（上一轮还没跑完就跳过本轮）

`daemon=True` 让后台线程不阻塞进程退出——关窗时主线弹回，整进程哪怕还有一帧没结束也会被 Python GC 拽走。

### 5.2 `_sample_task`（后台线程）

执行两条 ADB 命令：

```python
# CPU: 两次回退保证兼容性
try:
    cpu_raw = self._adb.run_shell(serial, 'top -b -n 1', timeout=10)
except Exception:
    try:
        cpu_raw = self._adb.run_shell(serial, 'top -n 1', timeout=10)
    except Exception as e:
        cpu_raw = f'执行异常: {e}'

# 内存: cat /proc/meminfo (相对稳定,所有 Android 都支持)
try:
    mem_raw = self._adb.run_shell(serial, 'cat /proc/meminfo', timeout=5)
    mi = parse_meminfo(mem_raw)
except Exception:
    mi = None

if not self._closed:
    self._sample_done.emit({...})    # Qt Signal 线程安全,自动 queued connection
```

**为什么用 `threading.Thread` 而不是 `QThread`?**——后台纯 IO+CPU（CPU 不重），跨线程交互只靠一个 `Signal` 通知主线程。`QThread` 自带的 `run/moveToThread/start` 仪式对这场景过重。

### 5.3 `_on_sample`（主线程，定时器回调由 Signal 触发）

```python
# 首次获取到总内存 → 动态调整内存图 Y 轴
if mem_total_mb and not self._mem_total_mb:
    self._mem_total_mb = mem_total_mb
    self._mem_chart.set_y_max(mem_total_mb)

# 更新信息栏
self._info_label.setText(
    f'采样时间: {ts}    CPU: {cpu_str}    内存: {mem_str}')

# 失败时的调试信息 (折叠显示 top 前 5 行)
if cpu_pct is None:
    preview = ' | '.join(lines[:5])[:280]
    self._debug_label.setText(f'CPU 解析失败 (第 N 次) — top 前 5 行: ...')
```

---

## 6. 解析层（dual 解析器）

### 6.1 `parse_cpu_percent` —— 8 种格式兜底

Android 生态里 `top` 命令的输出格式碎片化非常严重，不同 ROM/模拟器/版本差异大。代码里手写了 8 条正则分支：

| # | 命中模式 | 示例 | 计算方式 |
|---|---|---|---|
| ① | `%Cpu(s): X us ... Y sy ... Z id` | toybox top（Android 8+） | `100 - idle` |
| ② | `%Cpu(s): ... Z id`（只含 idle） | 同上，只剩 idle | `100 - idle` |
| ③ | `N%cpu X%user ... Z%idle` | busybox 合并行 | `(total - idle) / total * 100` |
| ④ | `CPU: X% user, Y% kernel, Z% idle` | busybox 旧格式 | `100 - idle` |
| ⑤ | `CPU: X%`（直接百分比） | 部分精简 ROM | 直读 |
| ⑥ | `CPU usage: X%` | 某些定制 | 直读 |
| ⑦ | `User X%, System Y%`（无 idle） | 老 ROM | `user + sys` |
| ⑧ | 行首 `CPU: X%` | 边界格式 | 直读 |

**逐条匹配，哪个命中算哪个**——前一条命中就 `return`，不再继续。这是经典的**"分层兜底 + 短路"** 写法。

**多核 case**：③ 中的 `N%cpu` 可以 > 100（如 600 表示 6 核总容量），所以公式是 `(total - idle) / total * 100`，而不是 `100 - idle`。

### 6.2 `parse_meminfo` —— 标准 `/proc/meminfo`

```python
total_kb = _grep_int(raw, r'MemTotal:\s*(\d+)')
avail_kb = _grep_int(raw, r'MemAvailable:\s*(\d+)')   # Android 7+
free_kb   = _grep_int(raw, r'MemFree:\s*(\d+)')
cached_kb = _grep_int(raw, r'Cached:\s*(\d+)') or 0

# 优先 MemAvailable；否则 Total - Free - Cached
if avail_kb is not None:
    used_kb = total_kb - avail_kb
elif free_kb is not None:
    used_kb = total_kb - free_kb - cached_kb
else:
    used_kb = 0
```

**为什么 `used = total - available` 比 `total - free` 更准？**——`MemAvailable` 是内核已经减掉"可回收缓存"的真正可用量，是从应用视角看到的"实际可用内存"。这样计算出的 `used` 跟系统设置里看到的内存占用吻合。

`pct` 字段直接百分比化，CPU 与内存共用一套 `add_point` 接口。

---

## 7. 性能优化（4 个亮点）

### 7.1 三哨兵 + `daemon` 防线程泄漏

`_tick` 的三个 `if return` 哨兵保证：
- 关窗后不会再启动新线程
- 暂停期间定时器都不工作
- 上一轮没跑完就跳过本轮（避免短时间堆积）

`_closed = True` 在 `closeEvent` 里设置，已经在跑的 `_sample_task` 走完最后一行会检查这个标志后**不 emit Signal**——即便主线都关了也不会触发野生回调。

### 7.2 `add_point(value, failed=True)` 而非 None

CPU 解析失败时仍然 `add_point(0, failed=True)`——这是有意的：
- 维持 deque 长度一致（每 2s 推进一格）
- 让 `paintEvent` 按 `failed=True` 决定叠加"获取失败"文字
- 调试信息标签显示**这是连续第 N 次失败**，方便排查

### 7.3 解析与绘制解耦

`_sample_task` 全部在后台线程跑正则在 CPU 上做解析；`_on_sample` 在主线程只做：
- 一次 `add_point` 两次（CPU + 内存）
- 一次 `setText`（顶部信息栏）
- 必要时一次 `setText`（调试标签）

每次 `_on_sample` 主线程工作量 < 1ms，对 60 FPS UI 无感。

### 7.4 Y 轴动态适配

```python
if mem_total_mb and not self._mem_total_mb:
    self._mem_total_mb = mem_total_mb
    self._mem_chart.set_y_max(mem_total_mb)
```

首次采样后内存图 Y 轴从默认值 2048 MB 改成设备实际总内存（如截图里 `5944 MB`）。这样无论 1GB 入门机还是 16GB 旗舰机，曲线都能铺满整个图区，不需要用户手动配置。

---

## 8. 失败处理与调试信息

CPU 解析失败是最常见的异常路径（多出现在定制 ROM 上），代码里有**三道防线**：

1. **`top -b -n 1` 失败 → 自动回退 `top -n 1`**：一些设备 toybox top 不支持 `-b`
2. **`parse_cpu_percent` 返回 None → `add_point(0, failed=True)`**：UI 立刻看到"获取失败"
3. **调试标签显示前 5 行 top 原始输出 + 失败次数**：方便定位解析失败原因

```python
if cpu_pct is None:
    lines = [l.strip() for l in cpu_raw.strip().splitlines() if l.strip()]
    preview = ' | '.join(lines[:5])
    if len(preview) > 280:
        preview = preview[:280] + '...'
    self._debug_label.setText(
        f'CPU 解析失败 (第 {self._cpu_fail_count} 次) — top 前 5 行: {preview}')
```

**"复制调试"** 按钮则把**完整** `top` 原始输出复制到剪贴板——3 秒后调试标签还原成上次状态：

```python
QApplication.clipboard().setText(text)
QTimer.singleShot(3000, lambda: ...)   # 3s 还原,避免一直霸屏
```

这是非常贴心的设计：调试文本不能压到正常信息，但又不能让用户错过错误。

---

## 9. 线程模型全景图

```
                  主线程                              后台线程
              ┌─────────────┐                   ┌─────────────────┐
              │ QTimer(2s)  │                   │ _sample_task    │
              │   _tick     │ ─────start───────►│  ├ adb top      │
              │             │                   │  ├ adb meminfo  │
              │             │◄──emit dict──────│  └ parse+Signal │
              │ _on_sample  │                   └─────────────────┘
              │  ├ add_point│                         ↑ daemon=True
              │  ├ setText  │                         │
              │  └ setVisible│                         │
              └─────────────┘                         │
                                                       │
关窗 ──► _closed=True ──► _timer.stop() ────►      (后台任务跑完会自检)
        ──► 已跑的线程: 跑完这一帧不再 emit Signal
```

**唯一跨线程通信路径**：`_sample_done = Signal(object)` —— Qt 自动 queued connection，线程安全。

---

## 10. 代码结构

```
设备性能监控.py
├── 常量                              # SAMPLE_INTERVAL_MS, MAX_POINTS
├── _strip_ansi / _grep_int           # 工具函数
├── parse_cpu_percent(raw)            # CPU 8 种格式兜底解析
├── parse_meminfo(raw)                # /proc/meminfo 解析
├── class ScrollChart(QWidget)        # 自绘折线图组件
│   ├── __init__                      # 缓存 QFont/Color/Pen
│   ├── add_point(value, failed)      # 环形缓冲
│   ├── set_y_max / set_max_points    # 动态调整
│   └── paintEvent                    # 网格/折线/填充/末点/失败文字
└── class DevicePerfMonitor(QWidget)  # 监控窗口
    ├── __init__                      # 建 UI + 立即 _tick()
    ├── _build_ui                     # 信息栏+两图+调试标签
    ├── _tick                         # 定时器回调,启线程
    ├── _sample_task                  # 后台 IO+解析,emit
    ├── _on_sample                    # 主线程:刷 UI
    ├── _toggle_pause / _copy_debug   # 用户操作
    └── closeEvent                    # 设 _closed 停止定时器
```

整个模块**完全自洽**：解析、绘图、采样都在一个文件里，不依赖 `应用性能监控.py` 同名 `ScrollChart`。如果你复制这个文件到另一项目也能直接跑（仅需 `AdbHelper`、`弹窗样式`）。

---

## 11. 边界限制与已知约束

| 限制 | 说明 |
|---|---|
| **整体设备指标** | CPU 是**全设备** CPU 使用率（无 PID 维度），不是某一应用 |
| **`top` 输出依赖** | 解析层依赖标准 `top` 格式；极小众 ROM 仍可能返回 None（暴露"复制调试"按钮处理） |
| **非 root 限制** | 未 root 设备 `top` 输出 PID 列表但总体 CPU 仍可读，不受影响 |
| **单实例窗口** | 一次只能监控一台设备；要切换设备需先关窗再点按钮 |
| **后台线程并发** | `_tick` 用 `_sampling` 旗标防重叠，但 2s 间隔 + 单 IO 调用一般够用，**无法用作秒级精细压测** |
| **数据无持久化（可导出）** | 默认关闭后历史曲线消失；新增「导出 HTML」可把四图完整采样数据落盘到桌面 `Super_ADB/`，便于归档与回看 |

---

## 12. 典型用例

### 用例 1：日常开发巡检

```
启动应用 → 点「设备性能监控」→ 看 30 秒内 CPU/内存波动
   ├─ CPU 持续 > 70% → 怀疑主线程有死循环或后台定时任务过频
   ├─ 内存缓慢上涨不回落 → 内存泄漏嫌疑
   └─ 都正常 → 放心提交代码
```

### 用例 2：复现卡顿时的现场取证

```
遇到卡顿 → 立即打开设备性能监控 → 等复现
   → 看 CPU/内存曲线在哪个时间点出现尖峰
   → 配合 logcat 时间戳定位具体事件
```

### 用例 3：对比竞品设备表现

```
设备 A (旗舰):  内存总量 12GB,空闲 8GB → 内存占比 33%
设备 B (入门):  内存总量 3GB,空闲 1GB → 内存占比 67%

同一应用在两个设备上的内存占比绝对值不同 → 不能直接比
应该让两台设备的曲线**斜率**对齐看：泄漏趋势才是关键
```

### 用例 4：定制 ROM 适配

```
设备性能监控打开后发现 "CPU 解析失败 (第 1 次)" 的红色提示
   ├─ 提示里的 "top 前 5 行" 看一眼能不能套现有 8 种格式
   ├─ 不行就点 "复制调试" 把完整 top 输出丢给开发者
   └─ 开发者再加一条正则分支到 parse_cpu_percent
```

### 用例 5：与「应用性能监控」配合排查

两者数据维度互补：

| 维度 | 设备性能监控（本文） | 应用性能监控（独立窗口） |
|---|---|---|
| CPU | 全设备占比 | 单进程占比（含 Java Heap / Native Heap） |
| 内存 | 整设备 used / available | PSS + Java Heap + Native Heap + Graphics |
| 线程数 | ✗ | ✓ |
| 采样频率 | 2s | 2s |

**组合用法**：先看「应用性能监控」确认目标应用 PSS 正常，再看「设备性能监控」对比整设备基线。

---

## 13. 未来扩展点

1. ✅ **多核 CPU 分核展示**（已实现）—— `top` 的 `%Cpu0:` `%Cpu1:` 行解析为多折线，便于发现"只在一个核跑满"的调度问题；每核自动分配区分色
2. **导出 CSV/PNG** —— 当前已支持**导出 HTML**（含四图完整数据，Chart.js 离线报告），后续可再加 CSV/PNG 直出
3. **阈值告警** —— CPU 持续 > 80% 或内存可用 < 500MB 时弹气泡或发通知（已有顶部信息栏可拓展）
4. ✅ **网络上下行速率**（已实现）—— `/proc/net/dev` 解析，接收/发送双折线作为系统级健康度
5. ✅ **电池温度曲线**（已实现）—— `dumpsys battery` 的 `temperature` 加一路橙色曲线
6. **跨设备对比** —— 同时开两个窗口并排，A/B 设备的同应用启动时间、稳态内存一图比清
7. **悬浮窗迷你模式** —— 缩到屏幕角落只保留两个数字，方便边测边看
8. **解析器热加载** —— `parse_cpu_percent` 现在是硬编码正则，可以做成读 `~/.workbuddy/cpu_patterns.json` 让用户自加正则
9. **历史曲线回放** —— 把 deque 数据 JSON 化落到文件，下次启动可拖时间轴回放
10. **远端监控** —— 通过 TCP 把 `_sample_done` 的 dict 推到远程 dashboard，远程带外监控无显示器设备

---

## 附录 A：为什么用 `add_point(0, failed=True)` 而不是 `add_point(None, failed=True)`

虽然 `paintEvent` 已经按 `None` 自然分段绘制，但代码里故意**不写 None**——而是写一个 dummy 值 `0`。原因：

- 调试信息标签里 "失败第 N 次" 计数使用 deque 长度推进，必须每 2s 推一个点
- `0` 作为失败点会出现在折线下方，不会污染正常曲线（间距够大时视觉无感）
- 重置只需 `clear()`（清空 deque），不需要重置额外失败计数器

---

## 附录 B：跟其它子系统的交互

| 复用 / 协作 | 体现 |
|---|---|
| **复用 `弹窗样式`** | `HIGHLIGHT_CARD_STYLE` + `add_green_glow(self.card)` —— 跟「应用性能监控」、「Monkey 压测」三大窗口同款绿色高亮卡 |
| **复用 `AdbHelper.run_shell`** | 后台线程直接调用，走 `subprocess.run` + shell=False 路径，避免 cmd.exe 引号陷阱（参「Windows cmd.exe 陷阱」） |
| **复用 `FONT_FAMILY`** | 全局字体一致 |
| **复用 `png_rc`** | 任务栏图标统一 `:/Super_ADB.png` |
| **复用主窗口串口** | 通过 `open_perf_monitor()` 的 `_ensure_serial()` 取数 |
| **跟日志页调试体系** | 失败时显示红色文本的习惯跟 `LogViewerPage._DBG` 体系同源 |

---

_文档版本：v3 · 与 `设备性能监控.py` 当前代码一致_
_最近更新：2026-08-08_
