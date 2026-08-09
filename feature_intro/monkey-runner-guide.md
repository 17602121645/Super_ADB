# Monkey 压力测试（MonkeyRunnerWindow）— 功能介绍

> 适用版本：Super_ADB 主窗口 → 应用操作 → 「Monkey」按钮
> 代码文件：`Super_ADB_Main/monkey_runner_window.py`（约 1080 行）
> 入口：`main_window.btnRunningApps_2.clicked → open_monkey_runner()`
> 截图位置：本文档配套截图保存在 `feature_intro/monkey-runner.png`

---

## 1. 功能概览

Android 自带的 `monkey` 工具是**最轻量的稳定性压测手段**——向指定应用随机注入按键/触摸/手势等事件，快速暴露崩溃、ANR 和未处理异常。直接在命令行拼参数枯燥且容易写错，本模块把 `monkey` **全部常用参数可视化**，配流式彩色日志与实时事件计数。

| 能力 | 体现 |
|---|---|
| **17 个 monkey 参数可视化** | 包名/事件数/间隔/种子/详细度/类别 + 7 项事件比例 + 6 项忽略/调试选项 |
| **实时流式日志** | `subprocess.Popen` + 后台读线程 + 100ms 批量缓冲 |
| **关键字彩色高亮** | `CRASH` 红 / `ANR` 橙 / `Events injected` 绿 / `:Monkey:` 紫 / `done` 绿 |
| **实时统计** | 事件数 / CRASH 数 / ANR 数 / 耗时（500ms 刷新） |
| **实时命令预览** | 修改任意参数 → 弹窗底部自动拼出 `adb shell monkey ...` |
| **归一化按钮** | 一键把 >=0 的事件比例按权重缩放到 100% |
| **运行/停止** | 「▶ 运行」/「■ 停止」按钮，关窗即停 |
| **运行模板（5 槽位）** | 常用配置一键保存/加载到 `~/.Super_ADB/monkey_templates.json` |
| **暂停 / 继续** | 给 monkey 进程发 `SIGSTOP`/`SIGCONT`，运行中随时冻结/恢复 |
| **实时事件分类饼图** | QPainter 自绘，跑测时按 触摸/手势/轨迹球/导航/按键/系统 占比实时刷新 |
| **崩溃报告自动拉取** | 检测到崩溃自动 `adb pull /data/tombstones/` → 桌面/Super_ADB |
| **事件回放** | 记录 `adb shell input` 序列，弹窗单步重放触发同样的崩溃 |
| **落盘日志** | 跑测同步写桌面 `Super_ADB/<pkg>_<timestamp>.log`，关窗可回看 |
| **monkey 版本探测** | 启动前展示 `adb shell monkey --version`，排查版本兼容 |
| **设备无 monkey 自动降级** | 探测无 `monkey` 命令时自动 `am start` 启动应用 + 提示 |
| **重复点击复用窗口** | `self._monkey_window` 句柄置顶 |

---

## 2. 入口与触发

```
┌────────────────────────────────────────────────────┐
│  主窗口「应用操作」分区                              │
│   ┌────────────┐                                    │
│   │ 运行内存   │  path/pid  │  应用监控  │ Monkey │  │
│   └────────────┘             │  运行内存 │  [← 红] │
└────────────────────────────────────────────────────┘
```

点击后：

```python
def open_monkey_runner(self):
    serial = self._ensure_serial()
    if not serial:
        return
    if self._monkey_window and self._monkey_window.isVisible():
        self._monkey_window.raise_()
        self._monkey_window.activateWindow()
        return
    # 默认带入主窗口已填的包名 (贴心小细节)
    default_pkg = self.pkgInput.text().strip()
    self._monkey_window = MonkeyRunnerWindow(
        serial, default_pkg=default_pkg, parent=self)
    self._monkey_window.show()
```

「**默认带入主窗口已填的包名**」是个细节：通常用户先在「获取正在运行列表」里选了一个应用，再点 Monkey，包名自动带过去，不需要重输。

---

## 3. 界面布局

截图复刻（窗口 820×700，最小 720×620）：

```
╔ Monkey 压力测试 — emulator-5554 ━━━━━━━━━━━━━━━━━━━━━━━━ [—] [□] [×] ╗
║ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ ║
║ ┌─ 基本参数 ────────────────────────────────────────────────────┐ ║
║ │ 包名: [com.reathin.adbwifi/         ]  事件数:[500]  间隔:[0 ms]│ ║
║ │ 随机种子:[固定...]  详细度:[-v ▾]  类别:[LAUNCHER ▾]  [归一化100%]│ ║
║ └───────────────────────────────────────────────────────────────┘ ║
║                                                                   ║
║ ┌─ 事件比例 (%)  —  设为 -1 表示不指定，走 monkey 默认 ─────────┐ ║
║ │  触摸:[50]  滑动:[20]  轨迹球:[-1]  导航:[-1]                    │ ║
║ │  主导航:[-1]  应用切换:[-1]  任意事件:[-1]                       │ ║
║ └───────────────────────────────────────────────────────────────┘ ║
║                                                                   ║
║ ┌─ 忽略 / 调试选项 ──────────────────────────────────────────────┐ ║
║ │ ☐ 崩溃继续 ☐ 超时(ANR)继续 ☐ 安全异常继续 ☐ 出错杀进程       │ ║
║ │ ☐ 监控 native 崩溃 ☐ 出错生成 bugreport                       │ ║
║ └───────────────────────────────────────────────────────────────┘ ║
║                                                                   ║
║ [▶ 运行] [■ 停止]              运行中…  事件: 0 · CRASH: 0 · ANR: 0 · 耗时: 00:00 ║
║                                                                   ║
║ ┌─ 日志 ─────────────────────────────────────────────────────────┐ ║
║ │ $ adb -s emulator-5554 shell monkey -p com.reathin.adbwifi/ ... │ ║
║ │ ---- Monkey 开始 ----                                          │ ║
║ │ [提示] 该设备无 monkey 命令，回退到 am start 方式启动应用       │ ║
║ │ 包名: com.reathin.adbwifi/                                     │ ║
║ │ $ adb -s emulator-5554 shell cmd package resolve-activity --brief│ ║
║ │ 入口 Activity: com.reathin.adbwifi/.com.reathin.adbwifi.MainActivity│ ║
║ │ 应用已启动 ✓  Starting: Intent { ... }                          │ ║
║ └───────────────────────────────────────────────────────────────┘ ║
║                                                                   ║
║ adb -s emulator-5554 shell monkey -p com.reathin.adbwifi/ -v \     ║
║ --pct-touch 50 --pct-motion 20 ...  ← 实时命令预览 (灰色等宽字体) ║
╚═══════════════════════════════════════════════════════════════════╝
```

截图里是**降级路径已生效**的画面——该设备镜像（emulator-5554）没有 `monkey` 命令，所以弹窗自动用 `am start` 启动应用而非压测。

---

## 4. 命令参数可视化（17 项全覆盖）

`build_monkey_args(params: dict)` 把 monkey 全部常用参数映射成 UI 控件。每改一个参数，底部「实时命令预览」自动拼接完整 ADB 命令——所见即所得。

### 4.1 参数分类映射

| UI 控件 | Monkey 参数 | 取值范围 | 默认值 |
|---|---|---|---|
| `QLineEdit` 包名 | `-p <pkg>` | 任意包名 | 空 |
| `QSpinBox` 事件数 | `<count>` | 1–1,000,000 | 500 |
| `QSpinBox` 事件间隔 | `--throttle <ms>` | 0–60000 | 0 |
| `QLineEdit` 随机种子 | `-s <seed>` | 任意/留空随机 | 空 |
| `QComboBox` 详细度 | `-v` / `-vv` / `-vvv` | 1–3 | `-v` |
| `QComboBox` 类别 | `-c android.intent.category.XXX` | LAUNCHER/MONKEY/LEANBACK_LAUNCHER | LAUNCHER |
| `QSpinBox × 7` 事件比例 | `--pct-touch/motion/trackball/nav/majornav/appswitch/anyevent` | -1–100 | -1（默认）/-1/-1/-1/-1/-1，触摸 50/滑动 20 |
| `QCheckBox × 6` 忽略/调试 | `--ignore-crashes/timeouts/security-exceptions` / `--kill-process-after-error` / `--monitor-native-crashes` / `--bugreport` | bool | 全 false |

**百分比范围 -1 的含义**：截图里 7 个事件比例里有 5 个显示 -1（轨迹球/导航/主导航/应用切换/任意事件）——这是**「不指定」**的意思，走 monkey 默认。**只有 >=0 才会附加 `--pct-XXX` 参数**：

```python
for opt, key in pct_map:
    val = params.get(key)
    if val is not None and int(val) >= 0:
        parts += [opt, str(int(val))]
```

### 4.2 实时命令预览（自动绑定所有控件）

```python
# 在 _build_ui 末尾绑定所有输入控件:
for w in [self.pkg_input, self.count_spin, self.throttle_spin,
          self.seed_input, self.verbosity_combo, self.category_combo,
          self.ignore_crashes_chk, ...]:
    if isinstance(w, QComboBox):
        w.currentIndexChanged.connect(self._refresh_cmd_preview)
    elif isinstance(w, QCheckBox):
        w.toggled.connect(self._refresh_cmd_preview)
    else:
        # QSpinBox 用 valueChanged, QLineEdit 用 textChanged
        ...
```

`isinstance` 分流不同信号：QComboBox 用 `currentIndexChanged`、QCheckBox 用 `toggled`、QSpinBox 用 `valueChanged`、QLineEdit 用 `textChanged`。**这种"用 isinstance 触发不同 signal" 的写法很 Qt 但很实用**——避免引入额外的抽象层。

### 4.3 「归一化 100%」按钮

```python
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
                sp.setValue(max(0, 100 - new_total))   # 最后一个补齐, 避免累计误差
            else:
                v = round(sp.value() / total * 100)
                sp.setValue(v)
                new_total += v
```

**经典算法：缩放 + 余数补齐**

1. 先把每个值按比例缩放（`v / total * 100`，四舍五入）
2. 累加 `new_total`
3. 最后一项强制补齐到 `100 - new_total`，避免累计误差

如果当前总和为 0（**全 -1 或全 0**），退化为平均分，**最后一个补齐**避免类似 `33 + 33 + 34 = 100` 的不均衡。

---

## 5. 进程模型（核心）

Monkey 窗口的进程管理是这套工具里最复杂的一块——需要同时处理 **子进程 + 两个守护线程 + 100ms 渲染缓冲 + 关窗收尾**。

### 5.1 三层线程架构

```
                  主线程                              后台线程 #1            后台线程 #2
              ┌─────────────┐                   ┌─────────────────┐   ┌─────────────────┐
              │ ▶ 运行按钮  │                   │ _read_loop      │   │ _watch_proc     │
              │   _run()    │ ──Popen monkey──► │ while readline()│   │ proc.wait()     │
              │             │                   │ → emit line     │   │ → _on_finished  │
              │             │                   └─────────────────┘   └─────────────────┘
              │ _flush_timer│ (100ms 批量渲染)
              │ _elapsed_t  │ (500ms 计时刷新)
              └──────┬──────┘
                     │ 关窗
                     ▼
                  closeEvent
                     ├─ _closed=True
                     ├─ terminate→0.5s→kill
                     └─ _flush_logs() (收尾)
```

### 5.2 `_run()` 的 11 步流程

```python
def _run(self):
    if self._running: return
    try:
        args = build_monkey_args(self._collect_params())
    except ValueError as e:
        self.status_label.setText(f'参数错误: {e}')
        return
    
    # ① 切换 UI 状态
    self._running = True
    self._start_ts = time.time()
    self._event_count = self._crash_count = self._anr_count = 0
    self.btn_run.setEnabled(False)
    self.btn_stop.setEnabled(True)
    
    # ② 清空日志 + 显示起始行
    self.log_edit.clear()
    self._append_log(f'$ adb -s {serial} shell {" ".join(args)}', 'info')
    self._append_log('---- Monkey 开始 ----', 'info')
    
    # ③ 探测 monkey 是否可用 (部分模拟器/设备会缺 monkey)
    monkey_available = True
    check_cmd = [adb_path, '-s', serial, 'shell', 'command', '-v', 'monkey']
    check = subprocess.run(check_cmd, ..., timeout=10)
    if check.returncode != 0 or 'monkey' not in (check.stdout or '').lower():
        monkey_available = False
    
    # ④ 设备没有 monkey → 回退到 am start 启动应用
    if not monkey_available:
        self._fallback_am_start(args)
        return
    
    # ⑤ 启动 Popen
    cmd = [adb_path, '-s', serial, 'shell'] + args
    self._proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
        bufsize=1, creationflags=CREATE_NO_WINDOW)
    
    # ⑥ 后台读线程
    self._reader = threading.Thread(target=self._read_loop, daemon=True)
    self._reader.start()
    
    # ⑦ 后台监视线程
    self._watcher = threading.Thread(target=self._watch_proc, daemon=True)
    self._watcher.start()
    
    # ⑧ 启动计时器
    self._elapsed_timer.start()
```

**几个细节值得讲**：

- **`stderr=subprocess.STDOUT`**：把 stderr 重定向到 stdout，**用一个 readline 循环处理所有输出**，避免分别管两个 pipe
- **`bufsize=1`（行缓冲）**：尽早拿到输出，否则可能缓存一大段才 flush
- **`encoding='utf-8', errors='replace'`**：monkey 输出偶有 Android 私有编码字符，`replace` 比 `strict` 鲁棒
- **`creationflags=CREATE_NO_WINDOW`**：Windows 专属，**不弹黑色 cmd.exe 窗口**（从 `adb_utils` 引入）
- **`daemon=True`**：守护线程，主进程退出会被一起拽走，避免僵尸线程

### 5.3 双线程守护：为什么需要 `_watch_proc`？

```python
def _read_loop(self):
    while True:
        if self._closed: break
        line = proc.stdout.readline()    # ← 这一步可能永远阻塞!
        if not line: break
        ...

def _watch_proc(self):
    proc.wait()    # ← 监视进程退出,主动通知主线程收尾
    QTimer.singleShot(0, self._on_finished)
```

**经典问题**：`adb shell monkey` 结束时，**monkey 进程已退出但 `adb` 父进程可能还卡在 pipe 上不关闭**，导致 `readline()` 永远阻塞在 stdout。**单靠读线程无法发现这个状态**——它正在等"空行"，但空行永远不会来。

`_watch_proc` 用 `proc.wait()` 监视**进程退出**（不依赖 stdout），进程退了就立刻 `QTimer.singleShot(0, self._on_finished)` 通知主线程收尾——**两条路任一条触发都完成清理**。

### 5.4 三保险收尾机制

```
① _watch_proc:  proc.wait() 返回 → _on_finished
② _read_loop:   readline() 拿到空 → _on_finished
③ 日志检测 "events injected: N" ≥ 设定值 → QTimer.singleShot(100, _finish_if_still_running)
④ 日志检测 "// Monkey finished" → QTimer.singleShot(100, _finish_if_still_running)
```

**第 ③④ 保险**：有时 monkey 进程结束但 stdout pipe 真就不关（已知 Android bug），上述两个保险都用不上时，**日志关键字匹配到「达到事件数」或「monkey 自己说 finished」**，主动触发收尾。

```python
elif 'events injected' in low:
    ...
    if self._event_count >= self.count_spin.value():
        QTimer.singleShot(100, self._finish_if_still_running)
elif '// monkey finished' in low:
    ...
    QTimer.singleShot(100, self._finish_if_still_running)
```

### 5.5 优雅停止链

```python
def _stop(self):
    proc.terminate()
    try:
        proc.wait(timeout=0.5)        # 给 0.5s 优雅退出
    except subprocess.TimeoutExpired:
        proc.kill()                    # 否则强制 kill
```

```python
# closeEvent 同样模式（注意：先 flush 再置 _closed，保证残留缓冲被渲染）
def closeEvent(self, event):
    self._elapsed_timer.stop()
    self._flush_timer.stop()
    self._flush_logs()   # 先刷新残留缓冲（此时 _closed 仍为 False）
    self._closed = True
    proc.terminate()
    try: proc.wait(timeout=0.5)
    except: proc.kill()
```

**链式逻辑**：`SIGTERM → 0.5s 等待 → SIGKILL` —— 给 monkey 机会写完日志、`flush` 缓存，又保证不无限等待。

---

## 6. 流式日志 + 100ms 批量缓冲

跟「日志查看器」同样的**渲染解耦思路**：

```
后台线程 (高频)        缓冲          主线程 (低频)
_read_loop ──┐                ┌──► _flush_timer (100ms)
             ├→ _pending_lines ──►   _flush_logs()
             │     (list)         │
        每行 append        一次性 insertHtml + _refresh_stat
```

### 6.1 `_append_log`：缓冲入口

```python
def _append_log(self, line: str, kind: str = None):
    self._pending_lines.append((line, kind))   # 仅入队,不渲染
```

**只入队不渲染**——这条设计让后台线程 0 渲染开销，主线程每 100ms 才进一次 `_flush_logs()`。

### 6.2 `_flush_logs`：批量渲染 + 关键字识别

```python
def _flush_logs(self):
    if not self._pending_lines: return
    batch = self._pending_lines
    self._pending_lines = []                # 一次性取走

    color_map = {
        'info':   '#56b6c2',    # 青色 — 命令/分隔/提示
        'crash':  '#ff6b6b',    # 红色 — CRASH
        'anr':    '#ffab40',    # 橙色 — ANR
        'done':   '#98c379',    # 绿色 — 完成/启动
        'error':  '#ff6b6b',    # 红色 — 错误
        'monkey': '#c678dd',    # 紫色 — :Monkey: 行
    }

    html_parts = []
    for line, kind in batch:
        text = line.rstrip()
        if kind is None:
            # 自动识别关键字 (6 类)
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
            elif '// monkey finished' in low:
                kind = 'done'
            elif text.startswith(':Monkey:') or text.startswith('// :Monkey:'):
                kind = 'monkey'
                self._event_count += 1                # 兜底计数
            elif text.startswith('$ ') or text.startswith('----') ...:
                kind = 'info'

        color = color_map.get(kind, '#d4d4d4')
        bold = 'font-weight:bold;' if kind in ('crash', 'done') else ''
        html_parts.append(
            f'<span style="color:{color};{bold}">{self._escape_html(text)}</span>')

    # 一次性插入 (一次文档布局刷新)
    if html_parts:
        cursor = QTextCursor(self.log_edit.document())
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml('<br>'.join(html_parts))
        sb = self.log_edit.verticalScrollBar()
        sb.setValue(sb.maximum())

    self._refresh_stat()
```

### 6.3 关键字识别的 6 类规则

| 触发条件 | kind | 颜色 | 计数影响 |
|---|---|---|---|
| `// crash` 或 `crash:` | crash | 红粗体 | `_crash_count += 1` |
| `// not responding` 或 `anr` | anr | 橙 | `_anr_count += 1` |
| `events injected: N` | done | 绿粗体 | `_event_count = N` |
| `// monkey finished` | done | 绿粗体 | — |
| `:Monkey:` 开头 | monkey | 紫 | `_event_count += 1`（兜底） |
| `$ ...` / `----` / `[错误]` / `[警告]` | info | 青 | — |

**事件计数有三个来源**（优先级从高到低）：

1. `events injected: N` —— monkey 自己报告的**累计事件数**（最权威）
2. `// Monkey finished` —— 完成标志
3. `:Monkey:` 行 —— 每次 monkey 启动一组事件就打印一行，**兜底计数**

### 6.4 一次 insertHtml 避免布局抖动

```python
cursor.insertHtml('<br>'.join(html_parts))
sb.setValue(sb.maximum())    # 自动滚动到底
```

**关键**：把 100ms 内所有日志**一次性**塞进 `QTextEdit`（中间用 `<br>` 拼接），而不是 N 次 `append`，**只触发一次文档布局刷新**。

对 100ms 内累积 50–200 行的 monkey 跑测场景，这种"批量插入"是肉眼可感的差别——一行一行插入会让 UI 卡顿，批量插入几乎无感。

### 6.5 `_escape_html` 防 XSS

```python
@staticmethod
def _escape_html(s: str) -> str:
    return (s.replace('&', '&amp;')
             .replace('<', '&lt;')
             .replace('>', '&gt;'))
```

monkey 输出里偶有 `&` / `<` / `>` 字符（路径、XML 错误信息），直接 `insertHtml` 会被解析成 HTML 标签。**必须 escape**——这是所有 HTML 拼接的通用防御。

---

## 7. 自动降级：设备无 monkey 时回退 am start

```python
# 探测 monkey
check_cmd = [adb_path, '-s', serial, 'shell', 'command', '-v', 'monkey']
check = subprocess.run(check_cmd, ..., timeout=10)
if check.returncode != 0 or 'monkey' not in (check.stdout or '').lower():
    monkey_available = False

if not monkey_available:
    self._fallback_am_start(args)
    return
```

**触发场景**（截图就是）：emulator-5554 这种不带 Google APIs 的精简镜像**没有 monkey 二进制**。

```python
def _fallback_am_start(self, monkey_args: list):
    # ① 从 monkey 参数里提取包名
    pkg = monkey_args[monkey_args.index('-p') + 1]
    
    # ② 查入口 Activity
    resolve_cmd = [adb_path, '-s', serial, 'shell', 'cmd',
                   'package', 'resolve-activity', '--brief', pkg]
    r = subprocess.run(resolve_cmd, ..., timeout=15)
    
    # 解析最后一行 pkg/.Activity
    activity = ''
    for ln in (r.stdout or '').strip().splitlines():
        if ln and '/' in ln:
            activity = ln
    
    # ③ am start 启动
    start_cmd = [adb_path, '-s', serial, 'shell', 'am', 'start', '-n', activity]
    r2 = subprocess.run(start_cmd, ..., timeout=15)
    
    if r2.returncode == 0 and 'starting' in r2.stdout.lower():
        self._append_log(f'应用已启动 ✓  {out2}', 'done')
        self._append_log(
            '提示: 设备无 monkey 命令，无法执行压测；已为你打开应用，'
            '可手动操作或换带 Google APIs 的镜像重试。', 'info')
```

**降级路径的 3 个细节**：

1. **从 monkey 参数里提取包名**（`args.index('-p') + 1`）—— 复用 monkey 路径下的用户输入，避免重输
2. **`cmd package resolve-activity --brief` 是 Android 12+ 的官方命令** —— 返回最后一行就是 `pkg/.Activity`
3. **明确告诉用户为什么降级**——不是静默切换，而是提示「设备无 monkey 命令，无法执行压测；已为你打开应用」

---

## 8. 状态栏 + 计时器

```python
# 顶部状态
self.status_label = QLabel('就绪')
# 绿色 #1de9b6 / 红色 #ff6b6b
# 文案: '运行中…' / '运行结束 (returncode=rc)' / '参数错误: ...'

# 实时统计
self.stat_label = QLabel('事件: 0  ·  CRASH: 0  ·  ANR: 0  ·  耗时: 00:00')

# 500ms 刷新计时器
self._elapsed_timer = QTimer(self)
self._elapsed_timer.setInterval(500)
self._elapsed_timer.timeout.connect(self._refresh_elapsed)

def _refresh_elapsed(self):
    self._refresh_stat()

def _refresh_stat(self):
    self.stat_label.setText(
        f'事件: {self._event_count}  ·  '
        f'CRASH: {self._crash_count}  ·  '
        f'ANR: {self._anr_count}  ·  '
        f'耗时: {self._elapsed_str()}')
```

**耗时格式 `mm:ss`**（`f'{secs // 60:02d}:{secs % 60:02d}'`）——简单但够用。**事件数取的是 monkey 自己报的累计数**（`events injected: N`），不靠 :Monkey: 行估算。

---

## 9. 线程模型全景图

```
                              主线程
   ┌────────────────────────────────────────────────────┐
   │                                                    │
   │   UI 事件循环                                       │
   │   ├─ ▶ 运行点击 → _run() (主线程同步)               │
   │   │   ├─ build_monkey_args (CPU)                   │
   │   │   ├─ check monkey 可用性 (subprocess.run)       │
   │   │   ├─ subprocess.Popen (同步创建)                │
   │   │   └─ 启动两个 daemon thread                    │
   │   │                                                │
   │   ├─ QTimer 100ms _flush_timer → _flush_logs()      │
   │   │   └─ 一次性 insertHtml + _refresh_stat         │
   │   │                                                │
   │   ├─ QTimer 500ms _elapsed_timer → _refresh_elapsed │
   │   │                                                │
   │   └─ ■ 停止点击 / 关窗 → terminate → 0.5s → kill   │
   │                                                    │
   └────────────────────────────────────────────────────┘

   后台线程 #1 (daemon): _read_loop
   ┌────────────────────────────────────────────────────┐
   │ while not _closed:                                  │
   │   line = proc.stdout.readline()                     │
   │   _line_arrived.emit(line)                          │
   │                                                    │
   │ if 拿到空行 / _closed:                              │
   │   QTimer.singleShot(0, _on_finished)               │
   └────────────────────────────────────────────────────┘

   后台线程 #2 (daemon): _watch_proc
   ┌────────────────────────────────────────────────────┐
   │ rc = proc.wait()                                    │
   │ QTimer.singleShot(0, _on_finished)                  │
   └────────────────────────────────────────────────────┘
```

**信号流向**：

- `_read_loop` 通过 `Signal(str)` 通知主线程（自动 queued connection）
- `_watch_proc` 用 `QTimer.singleShot(0, ...)` 跨线程触发主线程回调（**比 Signal 更轻量**——单事件无数据载荷）

---

## 10. 代码结构

```
monkey_runner_window.py
├── build_monkey_args(params: dict)              # 参数字典 → monkey 参数列表
│
└── class MonkeyRunnerWindow(QWidget)
    ├── __init__
    │   ├─ _build_ui (分组: 基本参数 / 事件比例 / 忽略选项 / 操作栏 / 日志 / 命令预览)
    │   └─ QTimer: _elapsed_timer (500ms) + _flush_timer (100ms)
    │
    ├── _normalize_pct (一键缩放到 100%)
    │
    ├── _refresh_cmd_preview (实时拼接命令)
    ├── _collect_params (UI → 字典)
    │
    ├── _run
    │   ├─ monkey 可用性探测
    │   ├─ 不可用 → _fallback_am_start
    │   └─ 可用 → Popen + 启两个 daemon 线程
    │
    ├── _fallback_am_start (am start 启动应用)
    │   ├─ 提取包名
    │   ├─ cmd package resolve-activity --brief
    │   └─ am start -n activity
    │
    ├── _read_loop (后台读 stdout)
    ├── _watch_proc (后台监视进程退出)
    │
    ├── _append_log / _flush_logs
    │   ├─ 6 类关键字自动识别 (crash/anr/done/monkey/info/error)
    │   ├─ 批量 insertHtml
    │   └─ _escape_html 防 XSS
    │
    ├── _stop (用户停止) / closeEvent (关窗)
    │   ├─ terminate → 0.5s → kill
    │   └─ _closed = True 让所有线程自然收尾
    │
    ├── _on_finished (统一收尾入口)
    │   ├─ 关闭 stdout (让 readline 立即返回)
    │   ├─ 恢复 UI (按钮/状态/计时器)
    │   └─ 清空进程句柄
    │
    └── _refresh_stat / _refresh_elapsed / _elapsed_str
```

**`build_monkey_args()` 是纯函数**——无副作用，可独立单测。整个模块的逻辑可以拆成"参数拼接 + UI 渲染 + 进程管理 + 日志解析"四块，互不耦合。

---

## 11. 边界限制与已知约束

| 限制 | 说明 |
|---|---|
| **无 monkey 自动降级** | 部分镜像无 monkey 二进制，自动 `am start` 启动但**不真压测** |
| **`adb shell pipe` 不关闭** | 已知 Android bug，靠双线程 + 日志关键字三保险收尾 |
| **中文 / 特殊字符** | monkey 输出偶有编码异常，`encoding='utf-8', errors='replace'` 兜底 |
| **事件计数精度** | 优先 `events injected: N`，否则靠 `:Monkey:` 行兜底——**极端场景下可能误差 ±N** |
| **暂停 / 继续** | ✅ 已支持（给 monkey 进程发 `SIGSTOP`/`SIGCONT` 冻结/恢复） |
| **多设备并行** | 一个窗口绑一个 serial，要压多设备开多窗口 |
| **超时可控** | 单次 monkey 跑测**无总超时**，靠用户点停止或事件数达到 |
| **bugreport 选项** | monkey `--bugreport` 仅触发 N 次事件后写 `/data/tombstones/`，**不是完整的 bugreport** |
| **关闭文本编辑器中无保存** | 日志只在本窗口展示，关窗即丢；后续可扩展落盘 |
| **事件回放范围** | 仅记录可映射为 `adb shell input` 的点击/按键；轨迹球/翻转/旋转等无法回放（仍计入饼图） |
| **崩溃报告拉取权限** | 部分设备 `/data/tombstones` 无读权限，会友好提示而非崩溃 |

---

## 12. 典型用例

### 用例 1：基础稳定性冒烟测试

```
配置:
  包名:  com.example.app
  事件数: 500
  类别:   LAUNCHER
  勾选: ☐ 崩溃继续 ☐ 监控 native 崩溃

操作:
  点 ▶ 运行
  等 30s-3min 看 log 区
  看到 "Events injected: 500" 即完成
  看底部 "事件: 500 · CRASH: 0 · ANR: 0 · 耗时: 01:23"
```

### 用例 2：极端压力 10000 事件

```
配置:
  事件数: 10000
  间隔:   0 ms (最大化频率)
  详细度: -vvv (看每条事件)

用途: 长期挂机测试, 跑 1-2 小时
      看是不是稳定 OK 或最终崩溃
```

### 用例 3：自定义事件分布（点击偏多）

```
事件比例:
  触摸 70 / 滑动 20 / 轨迹球 -1 / 导航 -1 ...
归一化 → 自动分配成 70/30

用途: 测试点击密集场景 (电商/表单)
```

### 用例 4：忽略崩溃长跑

```
勾选:
  ☐ 崩溃继续 (CRASH 后继续跑)
  ☐ 超时(ANR)继续
  ☐ 监控 native 崩溃

用途: 想知道"在多少事件后必崩"，让 monkey 跑完拿到完整分布
```

### 用例 5：发现崩溃后立即生成 bugreport

```
勾选:
  ☐ 出错生成 bugreport (--bugreport)
  ☐ 崩溃继续

跑完 5000 事件后 monkey 自动在 /data/tombstones/ 写现场
adb pull /data/tombstones/ 拉下来分析
```

---

## 13. 未来扩展点

1. **多窗口并行压测** —— 主窗口同时打开 N 个 Monkey 窗口，每个绑不同设备
2. ✅ **落盘日志**（已实现）—— 跑测时同步写到桌面 `Super_ADB/<pkg>_<timestamp>.log`，关窗后能回看
3. ✅ **运行模板**（已实现）—— 常用配置保存成模板 (5 个槽位)，一键切换
4. ✅ **崩溃报告自动拉取**（已实现）—— 跑完自动 `adb pull /data/tombstones/`，放到桌面/Super_ADB
5. ✅ **实时事件分类饼图**（已实现）—— 跑测时绘制饼图：触摸/手势/轨迹球/导航/按键/系统占比
6. ✅ **暂停 / 继续**（已实现）—— 给 monkey 进程发 `SIGSTOP`/`SIGCONT` 冻结/恢复
7. ✅ **设备多 monkey 探测**（已实现）—— 启动前在窗口顶部展示 monkey 版本 (`adb shell monkey --version`)，方便排查版本兼容
8. ✅ **事件回放**（已实现）—— 跑测时记 `adb shell input ...` 序列，可单步回放触发同样的崩溃
9. **压测完成后调用应用性能监控** —— `events injected` 到位后自动开 `AppPerfMonitor` 窗口记录稳态
10. **CPU 占用监控集成** —— 跑测同时开 `DevicePerfMonitor`，关联性能数据与崩溃时刻

---

## 14. 本版新增（2026-08-08）：运行模板 / 暂停继续 / 事件饼图 / 崩溃拉取 / 事件回放

> 配套代码：`Super_ADB_Main/monkey_runner_window.py`（`EventPieChart` / `ReplayDialog` 类 + `_pull_tombstones` / `_classify_and_record` / `_open_replay` 等方法）

### 14.1 运行模板（5 槽位）

基本参数组第二行加了「配置模板: [模板 1 ▾] [保存] [加载]」。

- **保存**：把当前所有 UI 参数序列化进 `~/.Super_ADB/monkey_templates.json`（键为槽位索引 `0..4`），中文 `ensure_ascii=False` 友好存储
- **加载**：反序列化后回填所有控件（`_apply_params`），并刷新命令预览
- 文件结构：`{"0": {pkg, count, throttle, ...}, "1": {...}, ...}`，缺省为空

```python
def _save_template(self):
    templates = self._load_templates()
    templates[str(self.template_combo.currentIndex())] = self._collect_params()
    os.makedirs(os.path.dirname(self._templates_file), exist_ok=True)
    with open(self._templates_file, 'w', encoding='utf-8') as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)
```

### 14.2 暂停 / 继续（SIGSTOP + SIGCONT）

操作栏新增「⏸ 暂停 / ▶ 继续」按钮，运行中可随时冻结/恢复 monkey：

```python
def _toggle_pause(self):
    (self._resume_monkey() if self._paused else self._pause_monkey())

def _pause_monkey(self):
    pid = self._find_monkey_pid()          # pidof -s com.android.commands.monkey (ps 兜底)
    if pid: self._send_signal(pid, '-STOP')

def _send_signal(self, pid, sig):
    def _task():
        r = subprocess.run([adb, '-s', serial, 'shell', 'kill', sig, pid], ...)
        self._pause_state_ready.emit(sig == '-CONT', '已继续' if sig=='-CONT' else '已暂停')
    threading.Thread(target=_task, daemon=True).start()
```

- **原理**：monkey 自身不支持 pause，但 Linux `kill -STOP` 能冻结整个进程（含其下的事件注入循环），`-CONT` 恢复。比"改 throttle 间隔"更彻底
- **PID 查找**：`pidof -s com.android.commands.monkey` 优先，`ps -A | grep monkey` 兜底
- 信号发送走**后台线程**，结果通过 `_pause_state_ready` 信号回主线程切按钮文字（⏸↔▶）

### 14.3 实时事件分类饼图（QPainter 自绘）

日志区下方新增 `EventPieChart` 控件（默认隐藏，首次有事件数据时自动显示）：

- **分类**：在 `_flush_logs` 中逐行解析 `:Sending` 行，归入 6 类
  | 输出特征 | 分类 |
  |---|---|
  | `:Sending Touch` | 触摸 |
  | `:Sending Motion` | 手势 |
  | `:Sending Trackball` | 轨迹球 |
  | `:Sending Key`（含 `KEYCODE_DPAD`/`KEYCODE_NAV`） | 导航 |
  | `:Sending Key`（其他） | 按键 |
  | `:Sending Flip` / `:Sending Rotation` | 系统 |
- **渲染**：`_flush_logs` 每 100ms 批量刷新时若计数有变化，`pie_chart.set_data(_event_stats)` → `paintEvent` 用 `QPainter.drawPie` 画饼 + 图例（`COLORS` 8 色调色板）
- **零依赖**：不引第三方图表库，纯 Qt 自绘

### 14.4 崩溃报告自动拉取（tombstones → 桌面）

`_on_finished` 检测到 `CRASH > 0` 时，后台拉取 `/data/tombstones/`：

```python
def _pull_tombstones(self):
    ls = subprocess.run([adb,'-s',serial,'shell','ls','/data/tombstones/'], ...)
    files = [f for f in ls.stdout.split() if f.startswith('tombstone')]
    if not files:
        self._tombstone_done.emit(False, '未发现 tombstone 文件'); return
    dest = 桌面/Super_ADB/tombstones_<serial>_<ts>/
    os.makedirs(dest, exist_ok=True)
    for f in files:
        subprocess.run([adb,'-s',serial,'pull', f'/data/tombstones/{f}', dest], ...)
    self._tombstone_done.emit(True, f'已拉取 {pulled}/{len(files)} 个 tombstone → {dest}')

def _on_tombstone_done(self, ok, msg):
    self._append_log(f'[崩溃报告] {msg}', 'done' if ok else 'info')
```

- 拉取在**后台线程**完成，结果通过 `_tombstone_done` 信号回主线程，避免阻塞 UI
- 目标目录：`桌面/Super_ADB/tombstones_<serial>_<timestamp>/`，与落盘日志同一父目录
- 部分设备 `/data/tombstones` 无权限读取时会友好提示「可能无权限」，不会崩

### 14.5 事件回放（记录 input 序列）

跑测时 `_classify_and_record` 把**可映射为 `adb shell input` 的事件**记录到 `_recorded_events`：

- **记录规则**：`ACTION_UP` 触摸 → `input tap x y`；`KEYCODE_*` 按键 → `input keyevent <数字>`（走 `KEYCODE_MAP`）
- **跳过**：轨迹球/翻转/旋转无对应 input 命令，不记录（仍计入饼图）
- **回放**：运行结束后「↻ 回放」按钮启用，弹出 `ReplayDialog`：
  - 列出全部可回放命令，可调「每条间隔」(0–3000ms)
  - 点「▶ 开始回放」后台逐条 `adb shell input ...`，进度条 + 当前命令实时显示，可随时「■ 停止」
  - 自动滚动高亮当前回放行

```python
def _open_replay(self):
    dlg = ReplayDialog(self._serial, self._recorded_events, self)
    dlg.show()
    self._replay_dlg = dlg     # 保持引用防 GC
```

**典型用途**：复现崩溃——跑测触发某崩溃后，回放同一串 input 事件，快速稳定复现现场。

---

## 附录 A：与「日志查看器」的流式日志对照

两者都用「100ms 批量缓冲」思路，但场景细节不同：

| 维度 | MonkeyRunnerWindow（本模块） | LogViewerPage |
|---|---|---|
| **数据源** | `subprocess.Popen` (monkey) | `QProcess` (logcat) |
| **后台线程** | `_read_loop` (daemon) | QProcess 内置 |
| **缓冲** | `_pending_lines: list` | 解析后批量 append |
| **渲染** | `QTextEdit.insertHtml(<br>...)` | `QListWidget.addItems` |
| **关键字高亮** | 6 类 (crash/anr/done/monkey/info/error) | 6 级别 (V/D/I/W/E/F) + Tag/PID 着色 |
| **100ms 定时器** | `_flush_timer` (UI 端) | 同样的设计 |
| **自动收尾** | 三保险 (watch_proc/read_loop/关键字) | 无（长跑不结束） |
| **关窗处理** | terminate → 0.5s → kill | terminate → 500ms → kill |

**共同点**：**所有「持续接收数据」的 Qt UI 都要带「快速跳过」哨兵 + 「批量渲染」解耦**。这是 PySide 编程的核心经验。

---

## 附录 B：常见问题排查

### Q1：跑了 30 分钟还没结束？

```
检查:
① 事件数是不是设得太大 (1000000) —— 改小
② throttle 是不是设成 0 —— 0=全速, 默认就够快
③ 日志是不是卡在 "Events injected" 没动 —— 进程已退出但 pipe 不关
   → 等几秒会触发日志关键字收尾
   → 或手动点 ■ 停止
```

### Q2：emulator 上点了运行但只看到「应用已启动 ✓」？

```
设备无 monkey 命令, 走了 am start 降级
→ 改用带 Google APIs 的镜像
→ 或安装 monkey.apk 到精简镜像
```

### Q3：CRASH 计数一直为 0 但其实应用闪退了？

```
原因:
① 没勾选 "崩溃继续" —— monkey 在第一个崩溃就停了
② monkey 的崩溃检测依赖 ":Monkey:" 后台汇报
   → 改用 -vvv 看详情, 看实际崩在哪
③ 用了 -s 固定种子, 但 monkey 报告格式变了
   → 升级 monkey 工具
```

### Q4：归一化按钮点了没反应？

```
全部 7 个比例都是 -1 → 没有"需要归一化"的项
→ 先把至少一个改成 >=0 (默认 触摸 50 / 滑动 20), 再归一化
```

---

_文档版本：v2 · 与 `monkey_runner_window.py` 当前代码一致（含运行模板/暂停继续/事件饼图/崩溃拉取/事件回放）_
_最近更新：2026-08-08_