# 应用性能监控（AppPerfMonitor）— 功能介绍

> 适用版本：Super_ADB 主窗口 → 应用操作 → 「应用监控」按钮
> 代码文件：`Super_ADB_Main/应用性能监控.py`（约 **3407 行** —— 项目里**最大、最复杂的子模块**）
> 入口：`main_window.btnpm.clicked → open_app_monitor()`
> 截图位置：本文档配套截图保存在 `feature_intro/app-perf-monitor.png`

---

## 1. 功能概览

点击主窗口「应用监控」按钮（**红框标注那个**），弹出一个**独立窗口**实时跟踪指定 Android 应用的全方位健康度 —— 项目**最重的指标矩阵**：**12 个滚动走势图 + 5 个自动检测 + 1 个启动耗时测量 + 完整的 HTML 报告导出**。

### 12 项图表指标

| # | 图表 | 数据源 | 默认 Y 轴 | 颜色 | 采样频率 |
|---|---|---|---|---|---|
| 1 | **CPU 使用率** | `top -b -n 1 -p <pid>`（备选 dumpsys cpuinfo） | 0-100% | 青绿 `#1de9b6` | 每 2s |
| 2 | **内存 PSS (TOTAL)** | `dumpsys meminfo <pkg>` TOTAL PSS | 自适应 | 橙 `#ffab40` | 每 2s |
| 3 | **Java Heap** | dumpsys meminfo Java Heap / Dalvik Heap | 自适应 | 蓝 `#61afef` | 每 2s |
| 4 | **Native Heap** | dumpsys meminfo Native Heap | 自适应 | 红 `#e06c75` | 每 2s |
| 5 | **Graphics 显存** | dumpsys meminfo Graphics | 自适应 | 紫 `#c678dd` | 每 2s |
| 6 | **线程数** | `cat /proc/<pid>/status \| grep Threads` | 自适应 | 橙棕 `#d19a66` | 每 2s |
| 7 | **Jank 丢帧率** | `dumpsys gfxinfo <pkg>` Janky frames | 0-100% | 青 `#56b6c2` | 每 2s |
| 8 | **应用耗电（mAh）** | `dumpsys batterystats` Estimated power use | 自适应 | 粉 `#ff6b9d` | 每 30s |
| 9 | **FPS 帧率** | dumpsys gfxinfo Total frames delta ÷ 2s | 自适应 | 黄 `#e5c07b` | 每 2s |
| 10 | **网络流量 (TX+RX KB/s)** | `/proc/uid_stat/<uid>/tcp_snd + tcp_rcv` delta | 自适应 | 蓝 `#61afef` | 每 2s |
| 11 | **文件描述符 (FD)** | `ls /proc/<pid>/fd \| wc -l` | 自适应 | 红 `#e06c75` | 每 2s |
| 12 | **磁盘 I/O (Read+Write KB/s)** | `/proc/<pid>/io` delta | 自适应 | 紫 `#c678dd` | 每 2s |

### 6 项自动检测

- 🔍 **内存泄漏检测**（基于 PSS / Java / Native 的线性回归斜率）
- 📦 **内存快照 (hprof) 自动捕获**（泄漏阈值触发时自动 `am dumpheap` + `adb pull` 到桌面，顶部「📦 抓 hprof」按钮可手动抓取）
- 🚨 **内存溢出（OOM）三层检测**（逼近预警 / 进程突然消失的崩溃捕获 / 压力标签）
- ⏱️ **ANR 检测**（进程消失时拉 logcat 搜 6 种 ANR 关键字）
- 🆘 **崩溃日志展示**（命中时显示完整 OOM/ANR 日志，含上下文 3 行折叠展开）
- 🚀 **启动耗时测量**（点击独立按钮，冷启动三策略兜底）

### 5 项信息栏

- 顶部：**包名 / PID / CPU / PSS / 线程 / 已运行时长**（图标 `>1` 文件）
- 运行信息：`⏱ 已运行 XmYs`（青紫）
- 电池：`🔋 85% | 5.00V | +5mA | 35.0°C | ⚡充电中`（电量低自动红色）
- 应用耗电：`🔌 ~12.3mAh (5.7%) / ~216.0mAh` 总耗电
- 设备信息 + 应用信息（启动时**后台一次性获取**，不阻塞采样）

### 关键辅助功能

- 📊 **保留点数可调**：60-3600，默认 300（≈10 分钟）；改后自动重排内存
- ⏸ **暂停/继续**：停止定时器但不关窗
- 🚀 **启动耗时**：独立按钮，三策略 `monkey → am start -n → am start intent`，冷启动测量
- 📄 **导出报告**：导出一份**自包含 HTML 报告**（Chart.js 4.4 + 折叠日志 + 可打印）
- 📋 **复制调试**：把全部 14 项原始 adb 输出剪贴板丢出，便于排查
- 🟢 **复用窗口**：重复点击 `raise_()` 置顶，不重复开窗
- 📈 **Y 轴自适应**：图表 Y 轴动态按数据范围扩展（`set_y_max`）×15% 触发

---

## 2. 入口与触发

```
┌────────────────────────────────────────────────────┐
│  主窗口「应用操作」分区                              │
│   ┌────────────────────────────────────────────────┐ │
│   │ 包名: [com.example.x]  [关闭] [启动] [清理数据] │ │
│   │ [安装/解包][卸载][path/pid][应用监控][运行内存][Monkey]│
│   │                                  ▲             │ │
│   │                                  │红框         │ │
│   └────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────┘
        │
        └──► btnpm.clicked → open_app_monitor()
              ├─ _ensure_serial()     (取已选设备)
              ├─ _package_name()     (取已填包名)
              ├─ if 已开窗且 visible: raise_() + activateWindow()
              └─ else: AppPerfMonitor(serial, pkg).show()
```

`Super_ADB_Main/Super_ADB_Main.py:796-810`：

```python
def open_app_monitor(self):
    serial = self._ensure_serial()
    if not serial: return
    pkg = self._package_name()
    if not pkg:
        self.log('请先在包名输入框填写要监控的包名')
        return
    if self._app_monitor_window is not None and self._app_monitor_window.isVisible():
        self._app_monitor_window.raise_()
        self._app_monitor_window.activateWindow()
        return
    self._app_monitor_window = AppPerfMonitor(serial, pkg, parent=self)
    self._app_monitor_window.show()
```

**关键贴心设计**：复用了**主窗口包名输入框**——你不用重新填一遍，已经填好 `com.reathin.adbwifi` 就直接拿来监控。

---

## 3. 界面布局

对照截图复刻：

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 应用监控 — com.reathin.adbwifi                              ─  □  ✕  │  ← 标题栏
├─────────────────────────────────────────────────────────────────────────┤
│ ┌─ 包名: com.reathin.adbwifi  PID:1757  CPU:0.0%  PSS:26MB  ──────────┐ │  ← ① 顶部信息栏
│ │ 21线程  已运行 17m28s     [🚀 启动耗时] 启动 234ms | 就绪 871ms      │ │     青绿色
│ │                            保留点数: [300 点]    [⏸ 暂停]            │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ② 内存泄漏检测
│ │ 内存泄漏检测: ✅ PSS:+0.2 MB/min | Java:+0.5 MB/min | Native:+0.1   │ │     绿/橙/红
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ③ 内存溢出检测
│ │ 内存溢出检测: ✅ 安全 — Java 0MB / 512MB (0%) | PSS: 26MB           │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ④ ANR 检测
│ │ ANR 检测: ✅ 正常                                                    │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ⑤ 运行信息
│ │ ⏱ 已运行 17m28s                                                      │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ⑥ 电池信息
│ │ 🔋 电池: 85% | 5000.00V | 35.0°C | ⚡充电中                          │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ⑦ 设备信息（启动时后台获取）
│ │ 📱 设备信息: 获取中...                                                │ │     左侧 3px 青绿色边
│ │ [已获取] v1.0.3 (3) | SDK 34 / Min SDK 19 | 安装: 2026-...            │ │
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────────────────────────────────┐ │  ← ⑧ 应用信息
│ │ 📦 应用信息: v1.0.3 (3) | target SDK 34 / Min SDK 19 | ...           │ │     左侧 3px 蓝色边
│ └─────────────────────────────────────────────────────────────────────┘ │
│ ┌─[滚动区域]─────────────────────────────────────────────────────────┐ │
│ │ CPU 使用率 — com.reathin.adbwifi                                     │ │  ← ⑨-⑳ 12 个图表
│ │ ╭─100%────────────────────────────╮ 最高值:0.0% 平均值:0.0% ...    │ │
│ │ │           📈 青绿色折线            ╰──────────────────────────────╯ │ │
│ │ ╰─0%────────────────────────────────╯ 最近 4/300 点 · 每 2s 采样     │ │
│ │ 内存 PSS (TOTAL) — com.reathin.adbwifi                              │ │
│ │ ... (同样格式, 512MB 橙图)                                           │ │
│ │ Java Heap — com.reathin.adbwifi                                     │ │
│ │ Native Heap — com.reathin.adbwifi                                   │ │
│ │ Graphics 显存 — com.reathin.adbwifi                                  │ │
│ │ 线程数 — com.reathin.adbwifi                                         │ │
│ │ Jank 丢帧率 — com.reathin.adbwifi                                    │ │
│ │ 应用耗电 (mAh 累计) ... 🔌 ~12.3mAh (5.7%)                          │ │
│ │ FPS 帧率 — com.reathin.adbwifi                                       │ │
│ │ 网络流量 (TX+RX KB/s) — com.reathin.adbwifi                          │ │
│ │ 文件描述符 (FD) — com.reathin.adbwifi                                │ │
│ │ 磁盘 I/O (Read+Write KB/s) — com.reathin.adbwifi                     │ │
│ │ 📊 扩展指标: 🔄 GC: 32次 | 🌡 CPU温度: 42°C | 🔒 WakeLock: 无 |    │ │  ← ㉑ 扩展信息栏
│ │ 📦 存储: 45M | 📉 掉电: 1.2%/h                                      │ │     左侧 3px 棕边
│ └─────────────────────────────────────────────────────────────────────┘ │
│ 开始时间: 2026-08-08 03:16:55  采样: 03:17:02  每 2s 采样  保留 300 │ ← ㉒ 底部状态
│                                          [📄 导出报告] [📋 复制调试] │
└─────────────────────────────────────────────────────────────────────────┘
```

每个图表的**统一格式**：

```
图表标题 (含包名)              ← 12pt 字、图表色
╭─y-max────────────────╮        ← 半透明填充曲线
╰─0%──────────────────╯
最高值:xx.xx%  平均值:xx.xx%  最低值:xx.xx%   ← 9pt 字、图表色
最近 X/Y 点 · 每 2s 采样       ← 9pt 灰色
```

**对比设备性能监控**：设备监控只画 CPU + 内存两条线（极简），应用监控把它**扩展为 12 条 + 自动检测 + 启动测量 + 报告导出**——本模块是一份**完整的移动端 APM**。

---

## 4. 顶部信息栏（PID + CPU + PSS + 线程 + 运行时长）

```
包名: com.reathin.adbwifi    PID: 1757    CPU: 0.0%    PSS: 26MB    线程: 21线程    已运行 17m28s
         ▲                       ▲             ▲            ▲              ▲             ▲
         │                       │             │            │              │             │
         always 显示             pidof         top 列      dumpsys         /proc/.../    /proc/<pid>/stat
                                                       TOTAL PSS    Threads     starttime + uptime
```

**所有指标都来自该模块独立的 ADB 调用**：

| 字段 | ADB 命令 | 解析 |
|---|---|---|
| PID | `pidof <pkg>` | 首字段 |
| CPU | `top -b -n 1 -p <pid>` | 列头 `PID %CPU` 定位 |
| PSS | `dumpsys meminfo <pkg>` | `TOTAL PSS: N` (KB→MB) |
| 线程 | `cat /proc/<pid>/status` | `Threads: N` |
| 已运行 | `/proc/<pid>/stat`+`/proc/uptime` | `uptime - starttime/100` |

---

## 5. 内存泄漏检测（线性回归）

> 本节是项目里**最智能的统计模块** —— 用 6 年统计学基础课教的**最小二乘法**实时算内存趋势。

### 5.1 核心算法

```python
def _detect_leak(values, window=LEAK_WINDOW):  # LEAK_WINDOW=30
    """基于线性回归斜率检测内存泄漏趋势。"""
    pts = [v for v in values if v is not None]
    if len(pts) < LEAK_MIN_SAMPLES:        # LEAK_MIN_SAMPLES=10
        return ('insufficient', 0)

    recent = pts[-window:]                  # 最近 30 点 = 1 分钟
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

    # ---- 阈值 ----
    if slope_per_min > 1.0:    return ('leak', slope_per_min)
    elif slope_per_min > 0.3:  return ('warning', slope_per_min)
    elif slope_per_min < -0.3: return ('declining', slope_per_min)
    else:                       return ('stable', slope_per_min)
```

### 5.2 阈值设计

| 状态 | 斜率（MB/min） | 颜色 | 图标 | 释义 |
|---|---|---|---|---|
| **leak（疑似泄漏）** | `> 1.0` | 红 `#ff6b6b` | ⚠️ | 持续增长 + 看趋势不收敛 |
| **warning（缓慢增长）** | `> 0.3` | 黄 `#e5c07b` | ↑ | 需关注 |
| **declining（下降中）** | `< -0.3` | 青 `#56b6c2` | ↓ | GC 回收正常 |
| **stable（稳定）** | `-0.3 ~ 0.3` | 绿 `#98c379` | ✅ | 健康 |
| **insufficient（数据不足）** | — | 灰 `#999` | ○ | < 10 个有效点（采样初期） |

### 5.3 综合判定 —— 取最严重

```python
worst = max(
    [pss_st, java_st, native_st],
    key=lambda s: _LEAK_PRIORITY.get(s, 0)
)
# insufficient=0 / declining=1 / stable=2 / warning=3 / leak=4
```

### 5.4 显示示例

```
内存泄漏检测: ⚠️ PSS:+1.2 MB/min | Java:+0.5 MB/min | Native:-0.1 MB/min
                │              │
                │              └─ 30 个采样点的回归斜率
                └─ 三项最高状态 (此行最严重的是 leak)
```

### 5.5 为什么用 30 采样点 + 2s 间隔 = 1 分钟窗口？

- **太长（如 5 分钟）**：长跑中能漏掉短时 spike
- **太短（如 10 秒）**：抖动太大，斜率噪声高，误判多
- **1 分钟**：正好涵盖**几次 GC 周期**，能区分"瞬时泄漏"vs"持续增长"

### 5.6 阈值自动 heap dump（hprof 捕获）

当 `worst == 'leak'`（任一项回归斜率 `> 1.0 MB/min`）时，自动触发一次 heap dump：

- 后台线程执行 `am dumpheap <pid> /data/local/tmp/<pkg>_<ts>.hprof`
- 再 `adb pull` 到 `桌面/Super_ADB/hprof_<pkg>_<ts>/<pkg>_<ts>.hprof`
- 拉取完成后自动 `rm` 设备端临时文件，状态栏提示可用 MAT / Android Studio 打开
- **节流**：一次泄漏 episode 只自动 dump 一次（泄漏解除后 `_hprof_dumped` 重置，可再次触发）
- **手动抓取**：顶部「📦 抓 hprof」按钮随时触发（不受节流限制，仍有 `_hprof_running` 防并发）

### 5.6 关键设计：None 值过滤

```python
pts = [v for v in values if v is not None]
```

**故意丢掉** None 而不是把 0 填进去 —— 因为 failed 采样点**用 0 占位**（详见 [§9 性能优化](#9-性能优化)），但 0 不参与回归，否则会把趋势"拉低到 0"。

---

## 6. 内存溢出（OOM）三层检测

> 是项目里**最聪明的告警系统** —— 不是单点报警，而是**逼近预警 / 崩溃捕获 / 压力标签 三层互补**。

### 6.1 三层架构

```
┌──────────────────────────────────────────────────────────┐
│ 第 1 层 — 逼近预警 (实时, 每 2s)                          │
│   Java Heap / MaxHeap                                    │
│     > 90% → ☠️ 随时可能 OOM   (深红)                       │
│     > 80% → ⚠ 逼近上限         (橙)                       │
│     > 60% → 偏高              (黄)                       │
│     else  → ✅ 安全              (绿)                       │
└──────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│ 第 2 层 — 崩溃捕获 (触发式, 进程消失时)                     │
│   pidof 返回空 → 拉 logcat -d -t 200                      │
│   → 8 种 OOM 关键字匹配 (OutOfMemoryError / lowmemorykiller │
│   / oom-kill / Fatal SIGKILL / etc)                       │
│   → 命中: 显示 "OOM 应用已崩溃 — 第一行摘要" + 折叠日志     │
│   → +6 种 ANR 关键字一并捕获 (am_anr / waiting on lock)  │
└──────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│ 第 3 层 — 压力标签 (与崩溃检测互斥, 进程在运行才显示)        │
│   颜色等级 + 百分比: "Java 380MB / 512MB (74%)"            │
│   + 内置备份检测 (上面两层未命中时的兜底)                    │
└──────────────────────────────────────────────────────────┘
```

### 6.2 逼近预警算法

```python
ratio = java_mb / max_heap_mb
if   ratio >= 0.90: _set('☠️ 随时可能 OOM',    '#ff6b6b', 红底)
elif ratio >= 0.80: _set('⚠️ 逼近上限',        '#ff9866', 橙底)
elif ratio >= 0.60: _set('偏高',              '#e5c07b')
else:                _set('✅ 安全',           '#98c379')
```

**OOMC 优先 > 逼近预警 > 进程未运行**（一旦捕获崩溃，标签切红，没空间再说百分比）：

```python
if oom_crash:
    _set(f'内存溢出检测: OOM 应用已崩溃 — {snippet[:100]}', ...)
    return            # ← 短路, 不再进入逼近预警判定
```

### 6.3 崩溃捕获关键字（8 种 OOM + 6 种 ANR）

```python
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

_ANR_LOG_PATTERNS = [
    r'ANR\s+in\s',
    r'Application\s+Not\s+Responding',
    r'\bANR\b.*com\.',
    r'am_anr\s*:',
    r'Signal\s+Catch\s+Output\s+Scheduler',
    r'waiting\s+on\s+lock.*ANR',
]
```

### 6.4 崩溃日志折叠 + 上下文

```python
context = 3   # 匹配行前后各 3 行
display = set()
for idx in matched:                          # 匹配行索引集
    for j in range(idx-3, idx+4):          # 取前后 3 行
        display.add(j)

# 排序输出, 不连续处插入 '...'
parts = []
prev = -2
for idx in sorted(display):
    if idx > prev + 1 and parts:
        parts.append('  ...')               # 上下文断开标记
    prefix = '>>' if idx in matched else '  '
    parts.append(f'{prefix} {lines[idx]}')
    prev = idx
```

**输出样例**：

```
>> 10-02 18:23:45.123  1234  1234 I art: OutOfMemoryError: Failed to allocate a 16MB allocation
   10-02 18:23:45.124  1234  1234 I art:  	at java.lang.ClassLoader.loadClass(Native Method)
   ...
>> 10-02 18:23:45.789  5678  5678 I lmkd: killing 'com.example' (1234) due to memory pressure
```

`>>` 标记**命中行**，`  ` 是上下文，3 行超出范围时插入 `...`。

### 6.5 双信号同时命中 = 报表同时显示

```python
if oom_crash_log or anr_crash_log:
    parts.append('=== OOM 崩溃日志 (logcat -d -t 200 筛选) ===\n...')
    if anr_crash_log:
        parts.append('\n\n=== ANR 日志 (同一 logcat 筛选) ===\n...')
    self._crash_log_browser.setPlainText('\n'.join(parts))
    self._crash_log_browser.setVisible(True)
```

一次 `logcat -d -t 200` 同时抓 OOM + ANR，**两份崩溃日志可能并存**（罕见但确实存在）。

---

## 7. ANR 检测

### 7.1 模式与 OOM 完全对齐

```python
def _check_anr_crash(logcat_raw, context=3):
    # 同 _check_oom_crash 的实现, 仅关键字列表不一样
    ...
```

### 7.2 状态优先级

```
检测到 ANR 关键字 + 进程消失    → 🔴 "ANR 检测: ⚠ 应用无响应 — 第一行摘要"
进程未运行                      → 灰 "进程未运行"
正常                            → 绿 "✅ ANR 检测: 正常"
```

### 7.3 关联 ANR 日志

ANR 命中时，**日志并入 OOM 崩溃展示框**（不上线单独一个 ANR 框避免视觉噪声）：

```python
if anr_crash_log:
    parts.append('\n\n=== ANR 日志 (同一 logcat 筛选) ===\n' + anr_crash_log)
```

---

## 8. 启动耗时测量（点击独立按钮）

> 本节是 APM 里相对独立的功能 —— 它有**自己的三策略启动 + 两个等待轮询 + 30s 看门狗**，是项目里**最复杂的一次性测量流程**。

### 8.1 流程概览

```
点击 [启动耗时]
  │
  ├─ 1. force-stop 关闭进程 ─────► am force-stop <pkg>  等待 2s
  │
  ├─ 2. 启动默认 Activity (三策略兜底):
  │    A. monkey -p <pkg> -c LAUNCHER 1          ← 首选, 最稳定
  │    B. am start -n <pkg>/<.MainActivity>      ← 有 main activity 时
  │    C. am start -a MAIN -c LAUNCHER <pkg>     ← 兜底
  │
  ├─ 3. 轮询 pidof 检测进程启动 (40 × 250ms = 最长 10s)
  │    └─ 命中 → process_started_ms
  │
  ├─ 4. 轮询 dumpsys window | grep mCurrentFocus (16 × 500ms = 最长 8s)
  │    └─ 命中 → fully_started_ms
  │
  └─ 5. 展示 "启动 Xms | 就绪 Yms"
       └─ watch  8s 后强制显示 "超时 (>30s)" 并恢复按钮
```

### 8.2 三策略启动详解

| 策略 | 命令 | 适用 | 判断成功 |
|---|---|---|---|
| **A. monkey** | `monkey -p <pkg> -c android.intent.category.LAUNCHER 1` | 多数设备 | `'Events injected' in raw` |
| **B. am start -n** | `am start -n <pkg>/<.MainActivity>` | 已知 main activity 时 | `'Starting:' in raw and 'Error' not in raw` |
| **C. am intent** | `am start -a MAIN -c LAUNCHER <pkg>` | 兜底 | 直接 `started_ok = True` 走轮询 |

**为什么有三策略？** 不同设备/ROM 可能缺 `monkey`（如某些定制 ROM），`am -n` 又依赖预解析 main activity。三策略保证**总有一种能拉起 Activity**。

### 8.3 关键轮询黑科技

**进程启动检测用 `pidof`**（最快，但仅返回 PID），**Activity 就绪检测用 `mCurrentFocus`**（比 dumpsys activity activities 快得多）：

```python
# 进程启动: 40 次 × 250ms = 最长 10s, 给 OS 启动进程的时间
for _ in range(40):
    time.sleep(0.25)
    pid_raw = self._adb.run_shell(self._serial, f'pidof {self._package}', timeout=2)
    if pid_raw.strip():
        break

# Activity 就绪: 16 次 × 500ms = 最长 8s, 给首帧绘制时间
for _ in range(16):
    time.sleep(0.5)
    win_raw = self._adb.run_shell(
        self._serial, 'dumpsys window | grep -i "mCurrentFocus"', timeout=3)
    if self._package in (win_raw or ''):
        break
```

**两层意义不一样**：
- `process_started_ms`（**首帧**）：进程刚能跑，**不一定有 UI**
- `fully_started_ms`（**完全启动**）：Activity 已就绪, mCurrentFocus 含包名

### 8.4 看门狗 30s 强制兜底

```python
self._startup_watchdog = QTimer(self)
self._startup_watchdog.setSingleShot(True)
self._startup_watchdog.timeout.connect(_watchdog)
self._startup_watchdog.start(30000)

def _watchdog():
    if not self._btn_startup.isEnabled():      # 按钮还被禁用 (还在测量)
        self._startup_done.emit('超时 (>30s)', '#ff6b6b')
        self._btn_startup.setEnabled(True)
```

**为什么需要**：避免按钮永远处于禁用状态（如果 force-stop 后某次启动永远 block 住）。

### 8.5 颜色三档提示

```python
if fully_started_ms > 5000:
    color = '#ff6b6b'                        # 红: 大于 5s 卡顿严重
elif fully_started_ms < 2000:
    color = '#98c379'                        # 绿: 小于 2s 流畅
else:
    color = '#e5c07b'                        # 黄: 中等
```

---

## 9. 性能优化

### 9.1 关键设计：None vs 0 的选择

继承 DevicePerfMonitor 的 `add_point(val, failed=...)` API：

```python
# 失败时:
if pss_mb is not None:
    self._pss_chart.add_point(pss_mb, failed=False)   # 实际值
else:
    self._pss_chart.add_point(0, failed=True)         # ← 故意给 0 而非 None!
```

ScrollChart 的设计哲学：

- `add_point(val, failed=True)` → `self._values.append(0)` + `failed_count += 1`
- 连续失败超过 3 个 → 折线**断开**显示（避免一条假的 0 直线误导）

**这是给设备监控的妥协**：因为它只有两条线，0 折断视觉效果好。但给应用监控也复用了——**这反而成了双刃剑**：

- ✅ **好处**：失败时立刻看到缺口，知道数据缺失
- ❌ **坏处**：回归算法会把失败点的 0 误算进均值

**应对**：**线性回归调用 `pts = [v for v in values if v is not None]`** 显式过滤掉 0/failed 点。这是有意为之的**协作契约**。

### 9.2 慢指标分层（不同频率不同生命期）

**核心思想**：不是所有指标每 2s 都拉 —— 有些要昂贵的，要降低频率：

| 指标 | 真正频率 | 计数器 | 主要耗时 |
|---|---|---|---|
| 设备堆上限 (`getprop dalvik.vm.heapsize`) | **仅 1 次**（首次 PID 时） | `_max_heap_fetched` | 几乎免费 |
| 应用 UID (`dumpsys package`) | **仅 1 次** | `_uid_fetched` | ~2-3s（最大） |
| batterystats 耗电 | **每 15 次 ≈ 30s** | `_batterystats_tick` | ~5-10s |
| CPU 温度 thermal_zone* | **每 5 次 ≈ 10s** | `_slow_tick` | ~0.5s |
| GC 计数 (`logcat -d -t 100`) | **每 10 次 ≈ 20s** | `_slow_tick` | ~2-3s |
| WakeLock (`dumpsys power`) | **每 15 次 ≈ 30s** | `_slow_tick` | ~2s |
| du -sh 存储 | **每 30 次 ≈ 60s** | `_slow_tick` | ~1-2s |

```python
# 例: GC 计数
if self._slow_tick % 10 == 0:        # =0 时拉一次
    gc_raw = self._adb.run_shell(...)
    gc_count = _parse_gc_count(gc_raw)
    self._gc_count = gc_count
else:
    gc_count = self._gc_count        # 否则用上次缓存
```

**为什么不一次性拉所有？** 单次 sampling 会爆 15+ ADB 调用，一个 s+ 全跑完，至少阻塞七八秒。**用户可见的延迟**会让窗口感觉卡死。分层让**慢指标节流，快的每 2s 都新鲜**。

### 9.3 三哨兵（`_closed`/`_paused`/`_sampling`）

继承自 DevicePerfMonitor 的精髓：

```python
def _tick(self):
    if self._closed or self._paused or self._sampling:
        return                            # ← 三哨兵, 任一为真直接跳过
    self._sampling = True
    threading.Thread(target=self._sample_task, daemon=True).start()
```

- `_closed`: 窗口已关, 永不执行新采样
- `_paused`: 用户点了暂停, 别再发新调用
- `_sampling`: 上一次还没跑完 (后台采样有时超过 2s), 防重叠成十倍广告

### 9.4 dumpsys 命令合并

```python
# ---- 内存: dumpsys meminfo <package> (一次获取 PSS/Java/Native/Graphics) ----
mem_raw = self._adb.run_shell(self._serial, f'dumpsys meminfo {self._package}', timeout=15)
mem_info = _parse_meminfo(mem_raw)               # 解析出 4 项

# ---- FD + 磁盘 I/O 用 echo 拼接 (一次 shell) ----
proc_raw = self._adb.run_shell(
    self._serial,
    f'echo "===FD==="; ls /proc/{pid}/fd | wc -l; '
    f'echo "===IO==="; cat /proc/{pid}/io',
    timeout=5)

# ---- 网络流量用 echo 拼接 ----
net_raw = self._adb.run_shell(
    self._serial,
    f'echo "===SND==="; cat /proc/uid_stat/{uid}/tcp_snd 2>/dev/null; '
    f'echo "===RCV==="; cat /proc/uid_stat/{uid}/tcp_rcv 2>/dev/null',
    timeout=3)
```

**为什么用 `echo` 分隔符？** Windows cmd.exe 不支持嵌套引号 + `$(...)`, **两次 `adb shell` 比一次 `; ` 拼接慢 2-3 倍**（ADB 客户端握手开销 ~1s）。

### 9.5 FPS 计算细节（dumpsys gfxinfo 复用）

```python
# 每 2s 都重新拉 dumpsys gfxinfo (本应单独一次调用)
# 我们重用了 gfx_raw 既解析 jank 又解析总帧数 delta
gfx_raw = self._adb.run_shell(self._serial, f'dumpsys gfxinfo {self._package}', ...)
jank_count, jank_pct = _parse_jank(gfx_raw)
# ↓ 与 jank 共用同一份 raw:
total_frames = _parse_total_frames(gfx_raw)
if total_frames is not None and self._prev_frames is not None:
    delta = total_frames - self._prev_frames
    if delta >= 0:
        fps = delta / (SAMPLE_INTERVAL_MS / 1000.0)   # = delta / 2
self._prev_frames = total_frames
```

**复用**：jank 和 FPS 共享同一次 `dumpsys gfxinfo` 调用，零额外 ADB 开销。

### 9.6 Y 轴自适应（set_y_max 触发条件）

```python
if pss_mb > cur_max * 0.85:                                # 超过 85% 触发
    self._pss_chart.set_y_max(max(pss_mb * 1.2, 100.0))    # 新 max = 当前×1.2, 至少 100MB
```

**为什么是 85%？** 给"贴顶"留 15% 空隙, 不然曲线压顶看不清。

### 9.7 信号跨线程

```python
_sample_done = Signal(object)              # 主线程更新图表 / 标签
_startup_done = Signal(str, str)           # 启动结果 (text, color)

# 后台线程:
self._sample_done.emit({...})              # dict 一次性发完

# 主线程 (Qt 自动调度):
self._sample_done.connect(self._on_sample)
self._on_sample(data)                       # 用 dict 而非多个参数, 一次更新所有 UI
```

---

## 10. 解析层（30 项解析函数全景图）

整个文件 3407 行，约 **1500 行（44%）** 都是解析函数 —— **这是本模块的真正智力核心**。

### 10.1 解析函数全景表

| # | 函数 | 输入 | 输出 | 复杂度 |
|---|---|---|---|---|
| 1 | `_parse_cpu_from_top` | `top -b -n 1 -p <pid>` | CPU % | 8 种 top 列头格式 |
| 2 | `_parse_cpu_from_cpuinfo` | `dumpsys cpuinfo` | CPU % | PID + 包名双匹配 |
| 3 | `_parse_meminfo` | `dumpsys meminfo <pkg>` | dict (pss/java/native/graphics) | 4 项 + 双重 fallback |
| 4 | `_heap_from_table` | 同上 | Java/Native Heap MB | 3 列定位算法 |
| 5 | `_parse_graphics` | 同上 | Graphics MB | 3 种无数据处理 |
| 6 | `_graphics_from_table` | 同上 | Graphics MB (RSS / Heap Size fallback) | 取最大非零值 |
| 7 | `_find_col_index` | 列头行 | 列索引 | 找 Rss Total / Heap Size |
| 8 | `_parse_threads` | `/proc/<pid>/status` | 线程数 | 正则一行 |
| 9 | `_parse_jank` | `dumpsys gfxinfo <pkg>` | (count, pct) | 正则一行 |
| 10 | `_parse_max_heap` | `getprop dalvik.vm.heapsize` | MB | 多种单位格式 |
| 11 | `_check_oom_crash` | `logcat -d -t 200` | (first_line, full_text) | 8 个关键字 + 上下文 |
| 12 | `_check_anr_crash` | 同上 | 同上 | 6 个关键字 |
| 13 | `_parse_process_starttime` | `/proc/<pid>/stat` | clock ticks | 用 `rfind(')')` |
| 14 | `_parse_uptime` | `/proc/uptime` | seconds | 第一字段 |
| 15 | `_calc_running_seconds` | starttime + uptime | 秒数 | `uptime - starttime/100` |
| 16 | `_format_duration` | 秒数 | 字符串 | 三档格式 |
| 17 | `_parse_uid` | `dumpsys package` | UID | `userId=N` |
| 18 | `_uid_to_batterystats_label` | UID | `'u0aN'` | 10000 偏移 |
| 19 | `_parse_battery_info` | `dumpsys battery` | dict (level/voltage/current/temp/charging) | 6 项字段 |
| 20 | `_parse_app_power` | `dumpsys batterystats` | UID 级 mAh | 区块扫描 + 行匹配 |
| 21 | `_has_uid_power_data` | 同上 | bool | 是否含 Uid 行 |
| 22 | `_parse_total_power` | 同上 | Computed drain mAh | 简单正则 |
| 23 | `_parse_total_frames` | `dumpsys gfxinfo` | 总帧数 | FPS delta 用 |
| 24 | `_parse_fd_count` | `ls /proc/<pid>/fd \| wc -l` | FD 数 | 数字正则 |
| 25 | `_parse_disk_io` | `/proc/<pid>/io` | (read, write) | 两字段 |
| 26 | `_parse_network_traffic` | `tcp_snd+tcp_rcv` 拼接输出 | (snd, rcv) | echo 分隔符 |
| 27 | `_parse_cpu_temp` | `thermal_zone*/temp` 多行 | °C | 取最大值 |
| 28 | `_parse_gc_count` | `logcat -d -t 100` | GC 事件计数 | 关键字 + 限定词 |
| 29 | `_parse_wakelock` | `dumpsys power` | 持有的 wakelock 列表 | 区块扫描 |
| 30 | `_parse_startup_time` | `am start -W` | dict (total_ms 等) | 4 项字段 |
| 31 | `_parse_main_activity` | `dumpsys package` | `<pkg>/<.MainActivity>` | 区块扫描 |
| 32 | `_parse_app_storage` | `du -sh` | `'45M'` | K/M/G 单位 |
| 33 | `_parse_app_info` | `dumpsys package` | dict (10 项) | versionCode, targetSdk 等 |
| 34 | `_detect_leak` | deque[float] | (status, slope_mb/min) | **线性回归** |

### 10.2 dumpsys meminfo 三策略解析（复杂度最高）

`_parse_meminfo` 内嵌一个 **try → fallback → fallback → fallback 的级联**：

```
1. App Summary 区        → "PSS/Native/Java Heap: PSS_GIVEN  RSS"
   ├─ PSS > 0:  用 PSS
   └─ PSS = 0:  fallback to 表格区
       ├─ 表格 RSS_Total > 0:  用 RSS
       └─ 表格 RSS_Total = 0:  fallback to Heap_Size
           └─ Heap_Size > 0:   用 Heap_Size
              └─ 全 0:          取 max(>100KB)
2. Graphics 特殊处理      → 在备表/PSE 两种间选择 (详见 _parse_graphics)
3. TOTAL PSS 始终 App Summary (那里才是 PSS 真和, 表格区是 RSS+PSS 混合)
```

**为什么这么多兜底？** Android dumpsys 输出**因版本/ROM 千差万别** —— 同一份 Android 9 的 dumpsys meminfo 输出在小米/华为/三星/原生 AOSP 上细节都不一样。一份可靠的解析器要**敢优雅降级**。

### 10.3 dumpsys cpuinfo 优先级

```python
# 1) 首选: top -b -n 1 -p <pid>
top_raw = self._adb.run_shell(self._serial, f'top -b -n 1 -p {pid}', timeout=5)
cpu_pct = _parse_cpu_from_top(top_raw, pid)

# 2) 备选: dumpsys cpuinfo (top 在某些定制 ROM 上崩溃)
if cpu_pct is None:
    cpu_raw = self._adb.run_shell(self._serial, 'dumpsys cpuinfo', timeout=10)
    cpu_pct = _parse_cpu_from_cpuinfo(cpu_raw, pid, self._package)
```

**两种解析器分别独立实现**，因为它们的输出结构完全不同 —— top 是固定列宽表，dumpsys cpuinfo 是 `12.3% 1234/com.example:` 格式。

### 10.4 dumpsys batterystats 三状态区分

```python
# ---- 区分"接口失败"和"设备无 UID 级数据" (模拟器常见) ----
self._app_power_error = False
has_uid_data = _has_uid_power_data(bs_raw)
self._app_power_no_data = not has_uid_data
if self._uid is not None:
    app_power_mah = _parse_app_power(bs_raw, self._uid)
```

| 场景 | `_app_power_no_data` | `_app_power_error` | 图表行为 |
|---|---|---|---|
| 模拟器 (只有 Global 行) | `True` | `False` | 不画点 (避免画 0 噪声) |
| 接口超时 | `False` | `True` | 画"获取失败"红字 |
| 正常设备 | `False` | `False` | 画实际值 |

这是设备监控没有的**精妙状态机**。

### 10.5 /proc/<pid>/stat 解析（`rfind(')')` 黑科技）

```python
def _parse_process_starttime(stat_raw):
    """starttime 是第 22 个字段, 在 ')' 之后是第 20 个字段"""
    if not stat_raw:
        return None
    idx = stat_raw.rfind(')')           # ← 用 rfind 不是 split!
    if idx < 0:
        return None
    rest = stat_raw[idx + 1:].split()    # 分割 '(' 之后所有内容
    if len(rest) >= 20:
        try:
            return int(rest[19])         # starttime = rest[19] (0-indexed)
        except ValueError:
            return None
    return None
```

**为什么用 `rfind(')')`**：`/proc/<pid>/stat` 第 2 个字段是**进程名称**，可能含空格（如 `(adbd)`），也可能含括号（如 `(bash)`、`(WebView)`）。从右往左找 `)` 才能可靠跳过。

---

## 11. 线程模型全景图

### 11.1 4 类线程

```
                     ┌──────────────┐
                     │  Qt Main     │  ← UI 唯一线程, 所有控件读写都在这里
                     └──────┬───────┘
                            │
            ┌───────────────┼──────────────────────┐
            │                                       │
    ┌───────▼───────┐                       ┌───────▼───────┐
    │ _sample_done  │                       │_startup_done  │
    │ Signal 队列   │                       │  Signal 队列  │
    └───────▲───────┘                       └───────▲───────┘
            │                                       │
            │ from QTimer 2s tick + threading.Thread │
            │                                       │ threading.Thread
    ┌───────┴──────────────┐                ┌────────┴─────────┐
    │ 后台采样线程 (daemon) │                │ 启动测量线程(daemon)│
    │ (_sample_task)       │                │ (_do_measure)     │
    │ ─ pid / cpu / mem /  │                │ ─ force-stop      │
    │   threads / jank /   │                │ ─ 三策略启动      │
    │   stat / battery /   │                │ ─ 40+16 轮询      │
    │   batterystats / fd  │                │ ─ watchdog 30s    │
    │   / io / fps / net / │                └──────────────────┘
    │   (≥14 次 ADB 调用)   │
    └──────────────────────┘

    ┌──────────────────────┐
    │ 设备信息后台获取     │  ← threading.Thread (daemon), 仅一次
    │ (_fetch_device_info) │
    │  一次性 get_device_  │
    │  info_dict (8+ 次   │
    │  ADB 调用)          │
    └──────────────────────┘
```

### 11.2 哨兵状态机（一行就让线程安全收敛）

```python
def _tick(self):
    if self._closed or self._paused or self._sampling:
        return                            # ← 三哨兵, 任一为真直接跳过
    self._sampling = True                 # ← 立刻置真防并发
    threading.Thread(target=self._sample_task, daemon=True).start()

def _sample_task(self):
    # ... 跑 14 次 ADB 调用
    if not self._closed:                   # ← 跨线程检查 _closed
        self._sample_done.emit({...})      # 通知主线程

def _on_sample(self, data):                # ← 在主线程
    if self._closed: return                # 双保险
    self._sampling = False                 # ← 释放哨兵, _tick 才能再开新线程
```

**为什么 daemon=True**：万一主进程崩溃, 这些线程不会 hold 住退出。

### 11.3 启动耗时的 watchdog（30s 单次 QTimer）

```python
self._startup_watchdog = QTimer(self)
self._startup_watchdog.setSingleShot(True)
self._startup_watchdog.timeout.connect(_watchdog)
self._startup_watchdog.start(30000)
```

**`QTimer` 默认要在主线程跑** —— 因为启动测量是后台 `threading.Thread`, 但 **widget 必须在主线程操作**。所以用 Qt 原生 QTimer 跨线程安全。

---

## 12. 调试埋点（_DBG 风格）

### 12.1 全部 14 项原始输出缓存

```python
self._last_raw_top = ''           # 1. top
self._last_raw_mem = ''           # 2. dumpsys meminfo
self._last_raw_threads = ''       # 3. /proc/<pid>/status
self._last_raw_gfx = ''           # 4. dumpsys gfxinfo
self._last_raw_stat = ''          # 5. /proc/<pid>/stat
self._last_raw_uptime = ''        # 6. /proc/uptime
self._last_raw_battery = ''       # 7. dumpsys battery
self._last_raw_batterystats = ''  # 8. dumpsys batterystats
self._last_raw_fd = ''            # 9. (echo 拼接) ls /proc/<pid>/fd | wc -l
self._last_raw_io = ''            # 10. /proc/<pid>/io
self._last_raw_net = ''           # 11. (echo 拼接) tcp_snd + tcp_rcv
self._last_raw_temp = ''          # 12. thermal_zone*/temp
self._last_raw_gc = ''            # 13. logcat -d -t 100 (GC 计数用)
self._last_raw_wakelock = ''      # 14. dumpsys power
self._last_raw_storage = ''       # 15. du -sh
self._last_raw_startup = ''       # 16. am start -W
self._last_raw_pkg = ''           # 17. dumpsys package
```

### 12.2 复制调试按钮

```python
def _copy_debug(self):
    def _tail(s, n=2000):
        if not s: return '(空)'
        return '\n'.join(s.splitlines()[-n:])              # 末尾 2000 行防爆

    text = (f'包名: {self._package}\n'
            f'PID: {self._pid or "未知"}\n'
            ...
            f'===== top 输出 =====\n{_tail(self._last_raw_top)}\n\n'
            f'===== dumpsys meminfo 输出 =====\n{_tail(self._last_raw_mem)}\n\n'
            ...)
    QApplication.clipboard().setText(text)
    self._status_label.setText('已复制调试信息到剪贴板 (含全部原始输出)')
    QTimer.singleShot(2500, lambda: self._status_label.setText(old))
```

**为什么每个原始输出都缓存** —— 一旦发现某个指标异常，可以**马上 copy 调试**丢出最近一次的 raw，反向追查出 dumpsys 输出格式变化（设备升级/ROM 重打包都会）。

---

## 13. HTML 报告导出（自包含 + Chart.js 4.4）

> 本节是项目里**最重的报告生成器** —— 单函数 `_export_html` 约 320 行（2920-3306），生成**可打印、可分享、自带图表**的完整报告。

### 13.1 导出流程

```
点击 [📄 导出报告]
  │
  ├─ 1. 收集所有 chart._values (12 个图表的全部 deque)
  ├─ 2. 收集检测栏当前文本 (泄漏/OOM/ANR/电池/扩展...)
  ├─ 3. 收集应用信息 + 启动详情 + 设备信息
  ├─ 4. 收集崩溃/ANR 日志 (if 展示框可见)
  ├─ 5. 渲染 HTML 模板 (122 项占位符替换)
  ├─ 6. 写入 ~/Desktop/Super_ADB/app_perf_<pkg>_<timestamp>.html
  └─ 7. QDesktopServices.openUrl 自动打开
```

### 13.2 报告结构

```
┌─ HTML 报告 ─────────────────────────────────┐
│ <h1>应用性能监控报告</h1>                     │
│ 📦 包名: ... | 🔑 PID: ... | UID: ...        │
│ 📱 设备: ... | 🧰 Java 堆上限: 512MB        │
│ 🔋 电池: ... | 🔌 应用耗电: ~12.3mAh        │
│ 🕐 开始时间: ... | 📊 采样间隔: 2s | 300点   │
│ 📊 有效数据: 287 / 300                      │
├──────────────────────────────────────────────┤
│ 🔍 内存泄漏检测: ⚠️ PSS:+1.2 MB/min...      │
│ 🚨 内存溢出检测: ⚠ 逼近上限 — Java 400/512  │
│ ⏱️ ANR 检测: ✅ 正常                        │
│ [崩溃 / ANR 日志 (折叠展开) ]                 │
│ 运行信息 / 应用耗电 / 电池 / 扩展指标          │
│ 🚀 启动耗时: 启动 234ms | 就绪 871ms          │
│ [启动耗时详情]                                │
│ [设备信息] [应用信息]                          │
├──────────────────────────────────────────────┤
│ <Chart Grid: 12 张 Chart.js 折线图>          │
├──────────────────────────────────────────────┤
│ [内存泄漏检测详情 - 表格: PSS/Java/Native斜率]│
├──────────────────────────────────────────────┤
│ 报告生成时间: ...    [🖨️ 打印 / 保存 PDF]     │
│ <Chart.js 4.4 CDN>  (失败时显示降级提示)      │
└──────────────────────────────────────────────┘
```

### 13.3 Chart.js 自适应 Y 轴

```javascript
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
```

- **百分比图**强制 min=0
- **数据图**按 15% padding 自适应
- **空数据**走默认 fallbackMax (跟 ScrollChart set_y_max 思路一样)

### 13.4 Graphics 占位（数据全零时）

```javascript
var allZero = isAllZero(c.data) && c.id === 'gfx';
if (allZero) {
    var ph = document.createElement('div');
    ph.className = 'chart-placeholder';
    ph.textContent = '未分配显存 (Graphics PSS = 0)';
    card.appendChild(ph);
}
```

**为什么不画全 0 的折线？** Graphics 显存大多数应用是 0 MB —— 画全 0 折线**完全没意义且误导**。直接显示文字占位（"未分配显存"）更直观。

### 13.5 崩溃日志折叠 + 打印样式

```javascript
// 默认折叠: 高度 240px + 底部渐隐遮罩
.log-content.collapsed {
    max-height: 240px;
    mask-image: linear-gradient(to bottom, #000 70%, transparent 100%);
    -webkit-mask-image: linear-gradient(to bottom, #000 70%, transparent 100%);
}

// 打印时: 取消折叠, 隐藏按钮, 适合 PDF 保存
@media print {
    .log-content { max-height: none !important; -webkit-mask-image: none !important; ... }
    .log-toggle { display: none; }
}
```

### 13.6 占位符替换（避免 f-string 的 `{{ }}` 噩梦）

```python
template = '''<!DOCTYPE html>...
  <h1>应用性能监控报告</h1>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/..."></script>
  ...__LEAK_TEXT__  __OOM_TEXT__  __ANR_TEXT__  __CRASH_LOG_SECTION__...
  __DEVICE_TEXT__  __APP_INFO_TEXT__  ...
  var reportData = __DATA_JSON__;
'''

replacements = {
    '__DATA_JSON__': data_json,
    '__PACKAGE__': r['package'],
    ...
}
result = template
for k, v in replacements.items():
    result = result.replace(k, v)
return result
```

**为什么不直接 f-string？** f-string 的 `{{` `}}` 转义 + 内嵌 JS `{...}` 字面量**根本写不下来**。**占位符替换**是最干净的方式。

### 13.7 输出文件位置

```python
out = os.path.expanduser(f'~/Desktop/Super_ADB/app_perf_<pkg>_<timestamp>.html')
QDesktopServices.openUrl(QUrl.fromLocalFile(out))
```

导出后**自动用系统默认浏览器打开**。

---

## 14. 代码结构（模块顶部 3407 行概览）

```
应用性能监控.py  (3407 行)
├─ 1-65      模块级 docstring (24 行注释 + 常量定义 + 导入)
├─ 80-439    解析函数组 1 (CPU + meminfo + threads + jank + max_heap + OOM patterns)
├─ 440-479   _check_oom_crash
├─ 482-560   运行时长解析 (process_starttime + uptime + duration)
├─ 564-690   电池解析 (battery + app_power + total_power)
├─ 693-940   扩展指标解析 (FPS + FD + IO + net + temp + GC + wakelock + startup + main_activity + storage + app_info)
├─ 945-1011  _parse_app_info
├─ 1015-1085 _detect_leak + 状态/颜色/图标常量
├─ 1088-1204 AppPerfMonitor 类初始化 (字段 + QTimer + 信号 + 启动 device_info 后台获取)
├─ 1215-1434 _build_ui (UI 搭建: 8 个信息栏 + 12 个图表 + 扩展信息栏 + 底部状态)
├─ 1436-1775 _tick + _sample_task (主采样循环)
├─ 1780-1802 _compute_stats + _compute_stats_or_na
├─ 1805-2035 _on_sample (主线程结果处理 + 12 个图表更新 + 5 个信息栏更新)
├─ 2037-2068 _update_leak_detection
├─ 2070-2156 _update_oom_detection (三层检测)
├─ 2158-2172 _update_power_label
├─ 2174-2203 _update_app_power_label
├─ 2205-2254 _update_battery_label
├─ 2257-2276 _update_anr_detection
├─ 2278-2330 _update_extra_info (扩展指标横栏)
├─ 2334-2575 _measure_startup + 30s 看门狗 + 三策略启动
├─ 2580-2920 _export_html 数据收集
├─ 2920-3352 _export_html HTML 模板渲染 (Chart.js + Chart 数据 + 折叠日志)
├─ 3354-3362 _toggle_pause
├─ 3364-3401 _copy_debug
├─ 3403-3407 closeEvent
```

**占比统计**：

| 类别 | 大概行数 | 占比 |
|---|---|---|
| 解析函数 | 1500 | 44% |
| HTML 报告导出 | 600 | 18% |
| 主线程结果处理 + 信息栏 | 350 | 10% |
| 采样循环 (tick + task + on_sample) | 400 | 12% |
| 启动耗时测量 | 280 | 8% |
| UI 搭建 | 220 | 6% |
| 其余 (配置 / 调试 / close) | 60 | 2% |

---

## 15. 边界限制 / 已知陷阱

### 15.1 设备相关

| 现象 | 原因 | 应对 |
|---|---|---|
| CPU 始终 0% | 极冷启动期进程尚未被 CPU 调度器纳管 | 正常，5-10 次后会跳 |
| PSS 一直是 `获取失败` | 应用非 debug 包 / dumpsys meminfo 不准 | 不常见, 通常等下次 |
| 线程数始终 0 | `/proc/<pid>/status` 权限 | 模拟器上正常 |
| 网络流量无数据 | 模拟器无 `/proc/uid_stat` 目录 | 跳过画图 |
| 应用耗电显示 "🔌 不可用" | 模拟器没有 UID 级耗电数据 | 跳过画图 + 显示"不可用" |
| 启动耗时显示 "超时" | 三策略全失败 (罕见, ROM 太特殊) | 点重试 |

### 15.2 数据准确性

| 指标 | 真实精度 | 备注 |
|---|---|---|
| CPU | 2s 平均 ± 1% | Android top 的固有限制 |
| PSS | 精确（系统级） | |
| Java / Native Heap | 精确 | |
| 启动耗时 | ±50ms (轮询间隔) | 实际比 reports 数稍小 |
| 内存泄漏斜率 | ±0.3 MB/min 噪声 | 30 个点 × 2s |

### 15.3 性能预算

- 单次完整采样：**14+ 次 ADB 调用**，约 **5-7s**（batterystats 是瓶颈）
- 2s 采样间隔意味着**每次新 tick 进来时**, 上次还在跑 → 三哨兵跳过
- 大量设备（>3 个）并发监控时, 注意 ADB server 调度压力

### 15.4 已知 Bug

- **第一次 `_tick()` 强立即执行**: 启动时立即发一次 (不等待 QTimer 2s), 这可能导致窗口刚显示时立即"卡顿"显示底栏进度条
- **导出报告 > 1MB**: 12 个图表 × 300 点 = 3600 行 data, JSON 可能让 HTML 比较大, 网络差时 Chart.js CDN 失败

---

## 16. 典型用例

### 16.1 监控应用冷启动耗时

```
1. 主窗口连上设备 → 填包名
2. 点击「应用监控」
3. 等窗口起来 → 点 🚀 「启动耗时」
4. 看 "启动 234ms | 就绪 871ms"
   - 绿色 = 流旖 (<2s)
   - 黄色 = 中等 (2-5s)
   - 红色 = 卡顿严重 (>5s)
```

### 16.2 查内存泄漏

```
1. 应用监控起 5+ 分钟
2. 看「内存泄漏检测」状态：
   ✅ 稳定 (斜率 ±0.3 MB/min)  →  不出了，考虑结束
   ↑ 缓慢增长 (0.3~1.0 MB/min) →  多跑一会儿观察
   ⚠️ 疑似泄漏 (>1.0 MB/min)    →  复制趋势快照 + 复现步骤上报
```

### 16.3 压力测试时监测内存/电讪

```
1. Monkey 压测运行中 → 点「应用监控」(同时监控)
2. 看 PSS / Java Heap 是否持续上涨 → 决定何时停 Monkey
3. 看电池掉电速率 → 判断压力强度
```

### 16.4 升级后对比

```
1. 升级前 → 打开应用监控 → 导出报告 (HTML 包含完整图表) → 保存
2. 升级后 → 打开应用监控 → 导出报告 → 对比两个 HTML 的图表 / 斜率
3. 重点看 Jank / Java Heap / FPS 变化
```

### 16.5 给同事复现问题

```
1. 遇到性能问题 → 应用监控导出一份 HTML + 复制调试
2. 发给同事 → 同事看 Code Begin / 图表 / 斜率 / 崩溃日志 → 复现
3. HTML 本身自带全部信息 + 可打印为 PDF (不看 Code Begin 都行)
```

---

## 17. 代码内复用关系图

```
                    ┌──────────────────────┐
                    │   应用性能监控    │
                    └──────────┬───────────┘
                               │
       ┌───────────────────┬───┴────┬─────────────────────┐
       │                   │        │                     │
       ▼                   ▼        ▼                     ▼
  ScrollChart       AdbDeviceOps  popup_style           STYLE_SHEET
  (device_perf_     (.run_shell   (HIGHLIGHT_CARD_     (界面样式.py)
   monitor)         .get_device_   STYLE +
                    info_dict)     add_green_glow)
       │                   │        │
       │                   │        │
       │        复用         │        │  复用 (高亮边框 + 绿色发光)
       │     (抽样/列定位)  │              │
       ▼                   │              ▼
   QPainter        QTimer +          QLabel
   自绘图表        threading.Thread   INFO/QPushButton
                   后台采样
```

**ScrollChart** 是核心可复用组件 —— 应用监控一次性画了 **12 个** 图实例，与设备性能监控的 **2 个**实例复用同一份代码。

**`_parse_meminfo` 被导出了**（`main.py:54` `from 应用性能监控 import AppPerfMonitor, _parse_meminfo`）——其他模块如果有 dumpsys meminfo 解析需求可以**直接导入用**。

---

## 17. 本版新增（2026-08-08）

1. **内存快照 hprof 自动捕获**：泄漏阈值（回归斜率 > 1.0 MB/min）触发 `am dumpheap` + `adb pull`，落盘桌面 `Super_ADB/hprof_<pkg>_<ts>/`，状态栏提示可用 MAT 分析；新增「📦 抓 hprof」手动按钮。
2. **修复 ScrollChart 接口失配（关键预存 bug）**：`ScrollChart` 已重构为多序列（`series_specs` + `add_point(name, value)`，数据存于 `_series`），但本模块仍按旧单序列接口使用（`chart._values` / `add_point(value, failed)`，共 8 处 `_values` 访问 + 20+ 处 `add_point` + 构造传 `color`）。新增 `AppScrollChart(ScrollChart)` 适配器子类（单序列名固定 `'值'`，`@property` 暴露 `_values`，覆盖 `add_point`），**使整个「应用级性能监控窗口」可正常构造/运行**（此前因构造时解包字符串崩溃而无法打开）。

---

## 18. 未来可扩展点

- **上报** ⭐⭐⭐：报告导出加上传云存储 (S3/OSS)
- **多进程并发监控** ⭐⭐⭐：支持同时监控多个包, 以 tab 切换
- **自定义脚本侦测** ⭐⭐：被监控进程点中的钩子 (如 dump heap)
- **古仔剖 (Profiling) 能** ⭐⭐：集成 Android Profileer (Profiler.startMethodTracing)
- **历史报告对比** ⭐⭐：同一个包的多份报告差异对比
- **阈值告警** ⭐⭐：设置内存泄漏阈值, 触发系统通知
- **Chart 切换为 X 轴为时间戳**：现在 X 轴仅是采样点序号, 真的时间轴更专业
- **自定义 PNG 导出** ⭐：画个手动快照 PNG, 方便插入 Issue
- **多设备仪表盘** ⭐：多个设备同一个包进行对比
- **预定义分析 model** ⭐：针对某些特定指标高的包, 自动推荐优化
- **内存快照/hprof 捕获** ✅：检测到泄漏阈值时, 自动 dump hprof + adb pull（2026-08-08 已实现）

---

## 附录 A：与设备性能监控（DevicePerfMonitor）的对比

| 维度 | 设备性能监控 | 应用性能监控 |
|---|---|---|
| 图表数量 | 2 (CPU + 内存) | 12 |
| 目标 | 整机健康度 | 单个应用全方位 APM |
| 检测能力 | 无 | 内存泄漏/OOM/ANR/崩溃 |
| 启动测量 | 无 | 三策略 + watch dog |
| 报告导出 | 无 | 完整 HTML (Chart.js) |
| 信息栏 | 0 | 8 (顶部/泄漏/OOM/ANR/运行/电池/设备/应用) |
| 调试 | 复制 top 原始输出 | **14 项**全部原始输出 |
| 代码行数 | ~560 | ~3407 (6x) |
| 是否复用 ScrollChart | 定义者 | 画 12 个实例 |
| UI 高亮 | 无 | `popup_style.HIGHLIGHT_CARD_STYLE` + 绿色发光 |

**它们是本项目 APM 子系统的"上位机 + 下位机"**：监控设备 vs 监控应用。

## 附录 B：与 Monkey / 压测模块的互补

| 任务 | 使用哪模块 |
|---|---|
| 看 CPU/PSS/Jank 趋势 | ✅ **应用监控** |
| 复现冷启动性能 | ✅ **应用监控** (启动耗时按钮) |
| 发随机事件找崩溃 | ➡️ **Monkey** |
| 发特定操作序列 | ➡️ **Monkey 自定义脚本** |
| 同时跑上述两类 | **同时开两窗口** (都开) |
| 跳以下情况使用应用监控 |
| 看应用代码逻辑 / 反编译 | ➡️ **文件管理器** + **APK 解包器** |
| 看网络包 | ➡️ **tcpdump + 应用监控网络流量图** (协同) |
