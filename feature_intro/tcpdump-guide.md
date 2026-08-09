# tcpdump 抓包 — 功能介绍

> 适用版本：Super_ADB 主窗口 → 系统操作 → 「PC本机IP」输入框右侧的 **tcpdump 抓包**按钮
> 代码文件：`Super_ADB_Main/tcpdump_dialog.py`（约 **293 行**）
> 关联代码：`Super_ADB_Main/Super_ADB_Main.py:56`（import）+ `908-919`（open_tcpdump_dialog）
> 截图位置：本文档配套截图保存在 `feature_intro/tcpdump.png`
> 关联文档：`system-ops-guide.md` §3.6（tcpdump 入口的概览）

---

## 1. 功能概览

点击系统操作区顶部「PC本机IP」右边的 **tcpdump 抓包** 按钮，弹出一个独立的 tcpdump 抓包窗口——**在设备上实时抓包，pcap 二进制流实时写本地文件**。结束（手动停止 / 关窗）后文件保存在 `桌面/Super_ADB/tcpdump_<serial>_<时间戳>.pcap`，**直接拖进 Wireshark 就能分析**。

### 一句话总结

> 「**ADB 网络抓包零配置工具**」——选好设备、选好网卡、点开始，pcap 自动落盘，不用 SSH 设备、不用 push 二进制、不用解析乱码。

### 截图复刻

```
┌─ tcpdump 抓包 — emulator-5554 ──────────────────────────────┐
│  在设备上执行 tcpdump 抓包，pcap 实时写入本地文件。           │  ← 说明
│  结束后文件保存在 桌面/Super_ADB/。                          │
│                                                              │
│  网卡: [wlan0                                              ] │  ← 默认 wlan0
│  过滤: [                                                 ]  │  ← 可留空
│       过滤表达式(可选)，如 port 443 / tcp / host 1.2.3.4
│  协议: [不限制                                       ▼   ]  │  ← 4 项下拉
│                                                              │
│  [▶ 开始抓包]  [■ 停止]                       就绪            │  ← 操作栏
│                                                              │
│  已抓 0 KB · 0 包 · 00:00                                  │  ← 实时统计 (500ms 刷新)
│  ┌──────────────────────────────────────────────────────┐  │
│  │                                                      │  │  ← 日志区
│  │                                                      │  │     (黑底 Consolas)
│  │                                                      │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

## 2. 入口与触发

主窗口左边栏 → **系统操作** → **PC本机IP 输入框右侧**的「**tcpdump 抓包**」按钮（截图里那个：

```
┌─ 系统操作 ─────────────────────────────────────────────┐
│  PC本机IP [192.168.1.50:8888   ✕]      [tcpdump 抓包] │  ← 顶部一行
│  [设置代理] [取消代理] [设备重启] [system 读写]          │  ← 第 1 行
│  ...
└──────────────────────────────────────────────────────┘
```

绑定关系（`Super_ADB_Main.py:1093`）：

```python
self.btnTcpdump.clicked.connect(self.open_tcpdump_dialog)
```

主方法（`Super_ADB_Main.py:908-919`）：

```python
def open_tcpdump_dialog(self):
    """打开 tcpdump 抓包弹窗（复用窗口，重复点击 raise）。"""
    if self._tcpdump_dialog is not None and self._tcpdump_dialog.isVisible():
        self._tcpdump_dialog.raise_()
        self._tcpdump_dialog.activateWindow()
        return
    serial = self._ensure_serial()
    if not serial:
        self.set_status('请先选择设备', ok=False)   # ← 注意这里是状态栏，不是日志
        return
    self._tcpdump_dialog = TcpdumpDialog(serial, parent=self)
    self._tcpdump_dialog.show()
```

**复用模式**与 install_zip_dialog / input_text_dialog 一致：第一次 new，重复点击 raise+activateWindow。

> **注意**：未选设备时**走状态栏** `set_status('请先选择设备', ok=False)`（红 ✕），不是日志——这是跟 input_text_dialog 那个流程的细微区别。

---

## 3. 弹窗 UI 详解

### 3.1 整体框架

```
TcpdumpDialog (QWidget, 620×400 默认)
└── QVBoxLayout (10px 边距)
    └── card (QWidget, 名字 popupCard)
        ├── HIGHLIGHT_CARD_STYLE + add_green_glow()    ← 高级卡片样式
        └── QVBoxLayout (14, 12, 14, 12 px 边距, 10 spacing)
            ├── QLabel (说明文字)
            ├── QGridLayout (3 行表单)
            │   ├── 0 行: 网卡
            │   ├── 1 行: 过滤表达式
            │   └── 2 行: 协议下拉
            ├── QHBoxLayout (操作栏)
            │   ├── btn_start (▶ 开始抓包, 120px 宽)
            │   ├── btn_stop  (■ 停止,   100px 宽)
            │   ├── stretch
            │   └── status_label (实时状态, 青绿 #1de9b6)
            ├── QLabel (实时统计, 500ms 刷新)
            └── QTextEdit (日志区, 黑底 Consolas 9pt)
```

### 3.2 表单字段

| # | 字段 | 控件 | 默认 | Tooltip |
|---|---|---|---|---|
| 0 | 网卡 | `QLineEdit` | `wlan0` | 抓包网卡，如 wlan0 / eth0 / rmnet0；部分设备不支持 any |
| 1 | 过滤表达式 | `QLineEdit`（带 placeholder） | 空 | 过滤表达式(可选)，如 port 443 / tcp / host 1.2.3.4 |
| 2 | 协议 | `QComboBox` 4 项 | `不限制` | 快速协议过滤，会拼到过滤表达式前面 |

**协议下拉 4 项**：`不限制` / `tcp` / `udp` / `icmp`。
**设计取舍**：
- 协议选择会**自动拼到过滤表达式前**（不替换）——用户自定义过滤继续生效
- 例：协议选 `tcp` + 过滤填 `port 443` → 最终 `tcp port 443`
- 比纯手写更安全（少打一个字少一个错）

### 3.3 操作栏

| 控件 | 行为 |
|---|---|
| `▶ 开始抓包` | 启动 Popen + 读循环 + 500ms 计时器 |
| `■ 停止` | 调 `_stop()` → `_close_proc()` (terminate→wait→kill) |
| `status_label` | `就绪`（灰）→ `抓包中…`（青绿 `#1de9b6`）→ `已停止 · 保存 N KB`（绿 `#98c379`） |

> **停止按钮初始灰**（不可点），开始后才可点；开始按钮反过来——开始后**立即禁用**，避免重复启动。

### 3.4 实时统计 label

500ms QTimer 触发 `_refresh_stat(secs)`：

```python
pkts = self._bytes // 1500  # 粗略估算包数（仅展示用）
self.stat_label.setText(
    f'已抓 {self._bytes // 1024} KB · ~{pkts} 包 · '
    f'{int(secs) // 60:02d}:{int(secs) % 60:02d}')
```

**`pkts = bytes // 1500`** ——这是一个**有意的精度损失**：
- 以太网平均帧长 1500 字节
- tcpdump 文件 = pcap 头 + 数据包，每包近似 1500 字节
- 这不是真实包数，**仅供 UI 反馈**——真正的精确包数要等 Wireshark 打开统计

### 3.5 日志区

`QTextEdit` 黑底 `#1a1a1a` + 浅灰 `#d4d4d4` + Consolas 9pt。

**会写的内容**：
- `开始时`：完整命令回显 `$ adb -s <serial> shell tcpdump -i <iface> -s 0 -w - <flt>`
- `手动停止时`：`---- 用户停止 ----`
- `关闭时`：`抓包结束，共 N KB，保存到:` + 完整路径（两行）

**stderr 没有单独读**——这是设计取舍：抓包工具的 stderr 主要是不重要警告，写到日志反而嘈杂。需要的话 Popen 已经把它接到了 `PIPE`，加一行 `threading.Thread(... _read_stderr)` 即可。

---

## 4. ⭐ 核心架构：实时 pcap 二进制流

这个模块**最特别的地方**：**二进制的 pcap 流不是按文本行读的，而是 64KB 一块直接写盘**。这是与 Monkey（按行 readline）/ Logcat（按行流式）最大的区别。

### 4.1 文件命名规则

每次「开始抓包」生成一个文件（`tcpdump_dialog.py:149-159`）：

```python
desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
save_dir = os.path.join(desktop, 'Super_ADB')
os.makedirs(save_dir, exist_ok=True)
ts = time.strftime('%Y%m%d_%H%M%S')
safe_serial = (self._serial or 'dev').replace(':', '_').replace('/', '_')
self._path = os.path.join(save_dir, f'tcpdump_{safe_serial}_{ts}.pcap')
self._fh = open(self._path, 'wb')
```

**三道防线**：
1. **`makedirs(save_dir, exist_ok=True)`** 自动建桌面 `Super_ADB/` 子目录
2. **`safe_serial.replace(':', '_').replace('/', '_')`** —— 防 Windows 路径非法（多设备时 serial 可能含 `:` 或 `/`）
3. **时间戳精确到秒** —— 同设备同秒再点会**覆盖**（不是 bug，是设计：默认你不会 1 秒内连开 2 次同一个抓包）

### 4.2 ⭐ 命令拼装（`tcpdump_dialog.py:166-169`）

```python
cmd = [self._adb.adb_path, '-s', self._serial, 'shell',
       'tcpdump', '-i', iface, '-s', '0', '-w', '-']
if flt:
    cmd.append(flt)
```

**关键参数拆解**：

| 参数 | 含义 | 为什么 |
|---|---|---|
| `-i <iface>` | 指定网卡 | 默认 `wlan0`，可改 eth0 / rmnet0 / any |
| **`-s 0`** | **不限快照长度（默认 68 截断）** | 抓全部数据，避免 HTTPS 等大包被截断 |
| **`-w -`** | **写到 stdout**（而不是文件） | 让 adb 父进程拿到原始 pcap 流（设备上不用先存再 pull） |
| `<filter>` | 末尾追加 | 真正的 BPF 过滤（不分类别） |

> ⚠️ `-s 0` **改变默认行为**——原版 tcpdump 默认 `-s 68` 字节（只抓头），如果不加 `-s 0`，很多 HTTPS/SIP 包会被截断看不到 payload。

### 4.3 ⭐ Popen + 64KB 块读取 + 信号回主线程

这是本模块的**架构骨干**（`tcpdump_dialog.py:172-212`）：

```python
try:
    self._proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW)
except Exception as e:
    self._log(f'[错误] 启动失败: {e}')
    self._cleanup_proc()
    return

self._running = True
self._bytes = 0
self._start_ts = time.time()
self._reader = threading.Thread(target=self._read_loop, daemon=True)
self._reader.start()
...

def _read_loop(self):
    proc = self._proc
    if proc is None or proc.stdout is None:
        return
    try:
        while True:
            if self._closed:
                break
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            if self._fh is not None:
                self._fh.write(chunk)
                self._bytes += len(chunk)
                self._bytes_updated.emit(self._bytes, time.time() - self._start_ts)
    except Exception:
        pass
    finally:
        if not self._closed:
            QTimer.singleShot(0, self._finalize)
```

**七个关键设计点**：

1. **`subprocess.Popen` + `creationflags=CREATE_NO_WINDOW`**（Windows 专属）—— 不弹 cmd 黑窗
2. **`daemon=True` + 三哨兵 `_closed / _running / _timer`** ——主进程退出时拽走读循环
3. **`proc.stdout.read(65536)`** —— **64KB 一块**，比按行 readline 快得多（二进制流没行）
4. **`if not chunk: break`** —— **EOF 自然退出**——进程关闭后 PIPE 自动 EOF，循环收敛
5. **`_bytes_updated.emit(nbytes, secs)`** —— **Signal 跨线程回主线程**（信号机制，Qt 自动 queued）
6. **`except Exception: pass`** —— 静默吞掉（不破坏 UI，所有错都会被捕获到 `_cleanup_proc`）
7. **`QTimer.singleShot(0, self._finalize)`** —— 在后台线程用 **0 延迟定时器调度回主线程**（避免直接调 UI 跨线程崩）

### 4.4 为什么不用 `_run_async / CmdWorker`？

主窗口的 `_run_async` 走 `QThreadPool` + `CmdWorker`——**问题**：
- CmdWorker 默认 setAutoDelete(True)，跑完就 GC——但**抓包是长跑任务**，不知道何时结束
- `_run_async` 后台完成通过 `signals.result` 回调一次性回写——而抓包需要持续**块级**信号

所以这里**手撸一个 daemon 线程**更合适——不预设结束时间，让 `proc.stdout.read` 自然 EOF 退。

### 4.5 ⭐ 优雅停止链（`tcpdump_dialog.py:221-231`）

```python
def _close_proc(self):
    proc = self._proc
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
```

**两级兜底**：
1. `proc.terminate()`（Windows 上是 `TerminateProcess`）→ `proc.wait(1.0s)` —— 1 秒内自己死
2. 1 秒还没死 → `proc.kill()`（Windows `TerminateProcess` 强杀）

**为什么 timeout=1.0（比 Monkey 的 0.5 多）？**
- Monkey 进程是**多进程链路**（adb → shell → monkey），终止时间更长，1s 也是给它 0.5s 让用户感受不到
- tcpdump 是**单进程**（adb → shell → tcpdump），tcpdump 死 adb 立刻 EOF + pipe 关闭，很快就能收尾
- 但 tcpdump 可能正在 flush pcap buffer，**多给 0.5s 让它刷盘**（防止 pcap 文件尾损坏）

---

## 5. 时序图

```
用户           TcpdumpDialog           daemon 线程            adb.exe              Android tcpdump
 │  点击 [开始抓包] │                       │                    │                       │
 ├─────────────────>│                       │                    │                       │
 │                  │ open(path, 'wb')     │                    │                       │
 │                  │ _proc = Popen(cmd,   │                    │                       │
 │                  │   stdout=PIPE)       │                    │                       │
 │                  │ _reader.start()      │                    │                       │
 │                  ├─────────────────────>│                    │                       │
 │                  │                       │ proc.stdout.read(65536) → block  │
 │                  │                       ├───────────────────>│                       │
 │                  │                       │                    │ adb shell tcpdump ... │
 │                  │                       │                    ├──────────────────────>│
 │                  │                       │                    │<──── pcap 流 ──────│
 │                  │                       │ proc.stdout.read() 拿到 64KB              │
 │                  │                       │ _fh.write(chunk)   │                       │
 │                  │                       │ emit(bytes, secs)  │                       │
 │                  │<─────────────────────┤                    │                       │
 │                  │ _on_bytes_updated() → 刷新统计 label        │                       │
 │                  │ _timer (500ms) → _refresh_stat()           │                       │
 │  点击 [停止]      │                       │                    │                       │
 ├─────────────────>│                       │                    │                       │
 │                  │ _log('用户停止')       │                    │                       │
 │                  │ _close_proc()         │                    │                       │
 │                  │  proc.terminate()     │                    │                       │
 │                  ├──────────────────────>│  → 1s 内死          │                       │
 │                  │                       │ proc.stdout.read() 返回 b'' ──┐ break loop
 │                  │                       │ QTimer.singleShot(0, _finalize)
 │                  │<─────────────────────┤                    │                       │
 │                  │ _finalize()          │                    │                       │
 │                  │ _fh.flush() + close()│                    │                       │
 │                  │ _log("抓包结束 ...") │                    │                       │
 │                  │ status_label "已停止" │                    │                       │
                  ...
```

---

## 6. 完整工作流（点击 [开始抓包] 后的 9 步）

1. **入参检查**：`_running` 已经在跑 → 直接 return
2. **拼过滤**：`proto` 不为「不限制」时 → `(proto + ' ' + flt).strip()`
3. **建目录**：`makedirs(桌面/Super_ADB, exist_ok=True)`，失败 → 日志 `[错误] 无法创建目录` 并 return
4. **建文件**：`open(self._path, 'wb')`，失败 → 日志 `[错误] 无法创建 pcap 文件` 并 return
5. **拼命令**：`[adb, -s, <serial>, shell, tcpdump, -i, <iface>, -s, 0, -w, -]`（+可选过滤）
6. **回显命令**：`_log('$ adb -s ... shell tcpdump ...')` —— 方便用户复制复现
7. **Popen**：`stdout=PIPE, stderr=PIPE, creationflags=CREATE_NO_WINDOW`，失败 → `_cleanup_proc()` 并 return
8. **启动读循环**：`threading.Thread(target=self._read_loop, daemon=True).start()`
9. **更新 UI**：btn_start 禁用，btn_stop 可用，status_label "抓包中…"，`_timer` 启动 500ms 心跳

---

## 7. 关闭路径（5 个入口都正确收尾）

| 触发 | 处理 |
|---|---|
| 点击 **[■ 停止]** | `_stop()` → `_close_proc()` → 读循环 EOF → `_finalize()` 显式调 |
| 点击 **窗口 X** | `closeEvent` → 设 `_closed = True` → `_close_proc()` → 读循环下次 read 立刻 break → `_finalize()` 不显式调，由读循环 finally 调度 |
| 进程自然结束（设备掉线等） | 读循环 `if not chunk: break` → `finally` 调度 `_finalize()` |
| 后台线程 exception | `except Exception: pass` 静默吞 → `finally` 调度 `_finalize()` |
| 直接结束主进程 | `daemon=True` 让 `_reader` 不阻主进程退出 |

**5 个入口收敛到同一个 `_finalize`**，避免漏路径。

`_finalize` 干 5 件事（`tcpdump_dialog.py:233-254`）：

```python
def _finalize(self):
    if not self._running:        # ← 幂等守护
        return
    self._running = False
    self._timer.stop()
    self._close_proc()
    if self._fh is not None:
        self._fh.flush()
        self._fh.close()
        self._fh = None
    self.btn_start.setEnabled(True)
    self.btn_stop.setEnabled(False)
    dur = int(time.time() - self._start_ts)
    self.status_label.setText(f'已停止 · 保存 {self._bytes // 1024} KB')
    self.status_label.setStyleSheet('color: #98c379;')
    self._log(f'抓包结束，共 {self._bytes // 1024} KB，保存到:')
    self._log(self._path)
    self._refresh_stat()
    self._proc = None
```

注意：
- `if not self._running: return` —— **幂等守护**，避免重复触发
- `_fh.flush()` —— **强刷**，保证 pcap 文件完整（不依赖 Python GC）
- `_log(self._path)` —— 用户最关心的东西给两行

---

## 8. 高保真文件示例

抓完一次 HTTPS 调试会话，桌面会有：

```
C:\Users\57676\Desktop\Super_ADB\
└── tcpdump_25102RKBEC_emulator_5554_20260808_125617.pcap    ← ~3 MB (中等会话)
```

**串号是 `emulator-5554`（含 -）→ safe_serial 不替换 -**，因为 `-` 是 Windows 合法字符。

把这个 .pcap 文件直接拖进 Wireshark：
- 协议分布统计（统计 → 协议分级）
- 过滤 `tcp.port == 443` 看所有 HTTPS 握手
- 右键 → Follow → TCP Stream 看完整 TCP 流

---

## 9. 性能优化（6 项）

1. **64KB 块读取** —— 是 1 行 256B 的 ~256 倍快，对二进制流尤其重要
2. **`add_green_glow` 卡片样式** —— 跟 install_zip_dialog / input_text_dialog 同一套（绿色 = 媒体类），视觉风格统一
3. **`_timer = QTimer(500ms)`** —— 统计刷新频率 = 2Hz 足够（用户视觉感受不到抖动）
4. **`Signal 跨线程 queued** —— 字节计数信号自动 `Qt.AutoConnection` → `QueuedConnection`，**主线程不阻塞**
5. **`flush() + close()`** —— 不依赖 GC，pcap 文件立即可读
6. **路径中转最少** —— adb → 本地 .pcap，**没有任何文本解析**，Wireshark 准确性 100%

---

## 10. 线程模型

```
┌─────────────────────────────────────────────┐
│ 主线程                                        │
│   ├─ UI 事件循环                               │
│   │   ├─ btn_start.clicked → _start()        │
│   │   ├─ btn_stop.clicked → _stop()          │
│   │   ├─ _timer.timeout → _refresh_stat()     │
│   │   └─ closeEvent → _closed = True         │
│   ├─ QTextEdit (log_edit)                    │
│   ├─ QLabel status_label / stat_label        │
│   └─ QFile (写 pcap).open(self._path, 'wb')  │
└──────────────┬───────────────────────────────┘
               │ 跨线程信号 (Signal, Qt.QueuedConnection)
               ▼
┌─────────────────────────────────────────────┐
│ daemon 线程 (_reader)                        │
│   └─ _read_loop():                           │
│       ├─ proc.stdout.read(65536)             │
│       ├─ fh.write(chunk)                     │
│       └─ _bytes_updated.emit(n, secs)        │
└──────────────┬───────────────────────────────┘
               │ adb.exe 子进程
               ▼
┌─────────────────────────────────────────────┐
│ adb.exe                                      │
│   └─ adb shell tcpdump -i wlan0 -s 0 -w -    │
│       └─ Android tcpdump 进程                │
└─────────────────────────────────────────────┘
```

**三哨兵**（`_closed / _running / _timer`）保证线程生命周期清晰——主进程退出时 daemon 线程自动被拽走，不会卡住。

---

## 11. ⭐ 边界限制（重要！）

> 这个模块的**核心坑**集中在「设备侧」和「pcap 文件本身」——比应用操作按钮的边界多得多。

### 11.1 设备侧最常见的两个坑

**坑 1：设备没有 tcpdump 二进制**

```
$ adb shell tcpdump -i wlan0 -s 0 -w - tcp port 443
/system/bin/sh: tcpdump: not found
```

**Android 默认不带 tcpdump**——你需要在设备上：
- **方案 A**：装个 tcpdump apk（如 [tcpdump for Android](https://www.androidtcpdump.com/)），让其把二进制 push 到 `/system/bin/tcpdump`（**需 root**）
- **方案 B**：用 root 权限手动 push：从 PC 编译好的 `tcpdump` 推到 `/system/xbin/tcpdump`
- **方案 C**：用 [tcpdump-static](https://github.com/Trungnguyen1991/tcpdump-static) 这种静态编译的版本

**坑 2：没有 root 权限**

```
$ adb shell tcpdump -i wlan0
tcpdump: wlan0: You don't have permission to capture on that device
```

`wlan0` 等网卡必须 `CAP_NET_RAW` 权限——普通 shell 用户没有。**变通**：
- `adb root` 后再试（多数 debug 设备支持）
- 抓本机 loopback（`-i lo`）通常可以（不需要 raw socket）
- 找一张不需要权限的网卡（部分设备 `usb0` / `rmnet0` 可以）

### 11.2 文件侧三个坑

**坑 3：pcap 文件**巨大**

- 视频 / 直播 / 长 HTTPS 抓包 → 几秒就是几十 MB
- 桌面空间可能被快速占满（`Super_ADB/` 没有自动清理）
- **变通**：用过滤器大幅减少（如 `host 1.2.3.4 and port 443`）——文件大小降一个数量级

**坑 4：同秒点击会覆盖**

`ts = time.strftime('%Y%m%d_%H%M%S')`——**精确到秒**。如果手抖 1 秒内连续两次 [开始抓包]，第二次会**完全覆盖**第一次（因为 pcap 文件名相同）。

**坑 5：pcap 文件末尾可能损坏**

终止时如果 tcpdump 还在写，**Popen.terminate / kill 不会触发 tcpdump 优雅退出**——pcap 文件的全局头可能被写但末尾 packet 残缺。

**多数 Wireshark 能容错**（自动识别 valid packets）——但偶发会提示「pcap 文件已损坏」。

### 11.3 UI 侧三个坑

**坑 6：不显示 stderr**

Popen 捕获了 stderr 但**没单独线程读**。如果 tcpdump 报 warning（如 `tcpdump: WARNING: reading from file ...`）会**默默丢失**——只能从 stdout 的开头几个字节判断（pcap 头不会写失败信息）。

**坑 7：日志区不全**

`QTextEdit` 累积日志无上限 —— 但**每次抓包的日志不超过 5 行**（开/停/路径），所以这个不是问题。

**坑 8：包数估算不准确**

`bytes // 1500` 对 TCP 大包（SYN/Ack 小包 / 1500+ MTU 大包）估算 **误差 3 倍以上**—— **仅供 UI 反馈**，不要当真。

### 11.4 命令侧两个坑

**坑 9：过滤语法直接是 tcpdump 的 BPF 语法**

这里不是 Super_ADB 帮你解析——你写错 tcpdump 会**直接报 syntax error** 然后 Popen 立刻结束，读循环 EOF 后 `_finalize`。

错误示例：
- ❌ `tcp and port 443` （**注意**：Super_ADB 用 `and`，但协议下拉用 `tcp` BPF 关键字），会被拼成 `tcp tcp and port 443` 报错
- ✅ `tcp and port 443` （过滤表达式栏留空，让协议选择决定）

**坑 10：网卡不存在时不报错**

`tcpdump -i nonexistent0` 会**先输出一行错误然后死**——但 stdout 立刻 EOF，**pcap 文件只剩全局头**（24 字节）。

---

## 12. ⭐ 性能优化设计取舍详解

### 12.1 为什么用 64KB 而不是按行？

tcpdump 的 stdout 是**纯二进制 pcap 流**——没有行的概念。如果按 `readline()`：
- Windows 上 pcap 数据可能含 `\n` 字节，被 readline 当行尾截断——**pcap 文件损坏**
- 按字节读 `read(1)` 慢死

**64KB 一块**是最常见的高性能流式文件 IO 大小——Python 标准库内部 buffer 也是这个数。

### 12.2 为什么不用 `qt.QLocalSocket` 或 `QSocketNotifier`？

理论上 PySide6 提供 `QSocketNotifier` 把 fd 接到 Qt 事件循环。但：
- Popen 拿到的 pipe fd 在 Windows 上不是「socket」，QSocketNotifier 不可用
- daemon 线程 + `proc.stdout.read(65536)` 阻塞等待，**根本没有真正的并发需求**（一个线程足以应对 100Mbps 网卡）

> **简单优于过分工程**——这里一个 daemon 线程处理一切。

### 12.3 为什么 `_bytes` 在两个地方更新？

```python
def _read_loop(self):     # ← 后台线程
    ...
    self._bytes += len(chunk)
    self._bytes_updated.emit(self._bytes, ...)

def _on_bytes_updated(self, nbytes, secs):   # ← 主线程
    self._bytes = nbytes                    # ← 又写一次！
    self._refresh_stat(secs)
```

设计意图：
- `_bytes` 在主线程也可能被 `_refresh_stat` 直接读（500ms 心跳可能和 read emit 错开）
- 主线程**信任**信号带过来的值，把它设为权威值
- 后台写 `+=` 是**乐观自增**，避免 UI 信号延迟带来的累积误差

实际上**两个线程写同一 int 在 CPython 下是安全的**（GIL 保护单个字节码），所以没并发问题。

---

## 13. 与其它子系统对照

| 维度 | tcpdump | Monkey | 日志 |
|---|---|---|---|
| 弹窗 | ✅ QWidget | ✅ QWidget | 内嵌分屏 |
| 流式读取 | ✅ 64KB 块 | ✅ 1 行 readline | ✅ QProcess readyRead |
| 后台线程 | ✅ daemon | ✅ daemon | ✅ QProcess |
| 信号跨线程 | ✅ Signal | ⚠️ log 累积 queue | ✅ 直接接 readyRead |
| 二进制流 | ✅ pcap | ❌ 文本 | ❌ 文本 |
| 写本地文件 | ✅ 实时 | ✅ 批量 | ✅ 批量 |
| 优雅停止 | ✅ 1s | ✅ 0.5s+kill | ✅ terminate+kill |
| 平台限制 | 需 tcpdump+root | 需 monkey | 普适 |
| 复杂度 | 中 | 高 | 高 |

---

## 14. 代码结构

```
Super_ADB_Main/
├── tcpdump_dialog.py            (弹窗实现, 293 行)
│   ├── TcpdumpDialog (QWidget)
│   │   ├── __init__()           # 拼 UI + 计时器 + 信号
│   │   ├── _build_ui()          # 表单 + 操作栏 + 实时统计 + 日志
│   │   ├── _start()             # 建文件 + Popen + 读循环
│   │   ├── _read_loop()         # ← 后台线程, 64KB 块读
│   │   ├── _stop()              # 用户手动停止
│   │   ├── _close_proc()        # terminate → wait → kill
│   │   ├── _finalize()          # 收尾（幂等）
│   │   ├── _cleanup_proc()      # 启动失败时的清理
│   │   ├── _on_bytes_updated()  # 跨线程信号回调
│   │   ├── _refresh_stat()      # 500ms 心跳
│   │   ├── _log()               # 日志追加
│   │   └── closeEvent()         # 关窗时也收敛
│   └── ...
└── Super_ADB_Main.py
    ├── from tcpdump_dialog import TcpdumpDialog    (line 56)
    ├── self._tcpdump_dialog = None                  (line 153)
    ├── open_tcpdump_dialog()                       (line 908-919)
    └── btnTcpdump.clicked.connect                  (line 1093)
```

**入口链路**：点击按钮 → `open_tcpdump_dialog()` → `TcpdumpDialog(serial, parent=self).show()`

---

## 15. 5 个测试用例

### 15.1 模拟器 + 全量抓包（截图复刻）

**前置**：emulator-5554 在线 + 装了 tcpdump
**操作**：填 `wlan0` → 点 [开始抓包] → 在模拟器上随便开几个 App → 点 [停止]
**预期**：
- 输出区显示 `$ adb -s emulator-5554 shell tcpdump -i wlan0 -s 0 -w -`
- 统计区 500ms 跳动 KB/包数
- 桌面 `Super_ADB/` 出现 `tcpdump_25102RKBEC_emu-5554_*.pcap`
- 拖进 Wireshark 能看到协议分布

### 15.2 真机 + 协议过滤

**前置**：手机 + root + tcpdump
**操作**：协议选 `tcp` + 过滤填 `port 443`
**预期**：命令拼成 `tcpdump -i wlan0 -s 0 -w - tcp port 443`，只抓 TCP 443 端口

### 15.3 设备无 tcpdump

**前置**：模拟器（默认没 tcpdump）
**操作**：点 [开始抓包]
**预期**：
- 日志显示完整命令
- **立刻停止**（adbpopen 的 stdout 立刻 EOF，因为 adb shell 报 `tcpdump: not found`）
- 桌面有 pcap 文件（**但只有 24 字节全球头**，因为 stdout 没写出全局头就死了）

### 15.4 关窗停止抓包

**操作**：抓到一半直接点窗口右上 ✕
**预期**：
- `closeEvent` 设 `_closed = True`
- `_close_proc()` 终止 adb → tcpdump 死 → stdout EOF → 读循环 break → `_finalize` via Timer
- pcap 文件保存完整（最坏情况丢末尾几个包）

### 15.5 抓包中途拔设备（设备掉线）

**操作**：抓到一半拔 USB
**预期**：
- adb 子进程被系统杀掉 → stdout EOF → 读循环 break → `_finalize` 调度
- 日志状态：`---- 设备掉线 ----`（如果有 stderr 读的话，本模块**没有**所以是静默）
- 桌面仍能拿到部分 pcap 内容

---

## 16. 11 个未来扩展点

按「价值 / 改动量」排序：

1. **🔥 实时包数精确显示**：接入一个 libpcap 解析器（pycap 或 scapy），实时解 pcap 给出真包数（~30 行代码）
2. **🔥 设备 tcpdump 一键安装**：检测不到 `tcpdump: not found` 时引导用户下载/推送（防坑 #1）
3. **🔥 文件大小限制 + 自动轮转**：超过 100MB 触发下一文件（防坑 #3）
4. **读取 stderr 单独线程**：把 tcpdump warning 也显示出来（修复坑 #6）
5. **时间戳精度到毫秒**：避免同秒覆盖（修复坑 #4）
6. **协议快捷预设按钮**：「HTTPS」「DNS」「TCP 80/443」一键填入
7. **wireshark 直接打开按钮**：跳转到 `wireshark.exe tcpdump_xxx.pcap`
8. **过滤语法校验**：解析 BPF 语法，错就告警（防坑 #9）
9. **网卡下拉自动枚举**：`adb shell ip -o link show` 拿网卡列表（防坑 #10）
10. **pcap 文件预览**：点击 [停止] 后弹个简单表（top 10 协议 / top 10 远程 IP）
11. **远程传输**：抓完自动推送到 PC 服务器（curl / scp）

---

## 17. 一句话总结

> **「tcpdump 抓包」是项目里唯一一个端到端的二进制流式抓包工具——adb 让设备端看不到文件，本地让 Wireshark 拿到原始字节，64KB 块读 + daemon 线程 + Signal 跨线程 = 一段 200 行代码搞定的事。**

它的价值不在复杂度，而在**「让 ADB 工具体验首次支持生产级网络分析」**。之前要 tcpdump 抓包得先 adb shell 进设备、再开 adbd 端口、再 scp 拉文件——现在**一键 + 自动落盘**。

---

## 附录 A：tcpdump 入门速查

**最常用的 10 个过滤表达式**：

| 表达式 | 含义 |
|---|---|
| `port 443` | 抓 443 端口（HTTP/HTTPS） |
| `host 1.2.3.4` | 抓某 IP 的所有包 |
| `tcp and port 80` | TCP 80（HTTP） |
| `udp and port 53` | UDP 53（DNS） |
| `tcp and src host 192.168.1.1` | 仅某 IP 发出 |
| `tcp and dst host 192.168.1.1` | 仅某 IP 接收 |
| `not host 192.168.1.1` | 排除某 IP |
| `tcp[tcpflags] & (tcp-syn) != 0` | 只看 TCP SYN |
| `greater 1000` | 抓大于 1000 字节的包 |
| `icmp` | 只看 ping |

**Wireshark 配套过滤**（拿到 pcap 之后）：

| 表达式 | 含义 |
|---|---|
| `tcp.port == 443` | 同 `port 443` |
| `ip.addr == 192.168.1.1` | 同 `host 192.168.1.1` |
| `http.request.uri == "/api/login"` | 某 HTTP 路径 |
| `tls.handshake.type == 1` | TLS Client Hello |
| `dns.qry.name == "example.com"` | DNS 查询某域名 |

## 附录 B：常见排错 FAQ

**Q：开始抓包后没反应，日志不更新，统计不跳？**
- 设备没装 tcpdump → 看设置提示
- 没 root → `adb root` 后再试
- 网卡不存在 → 改 `-i rmnet0` / `usb0` / `any`

**Q：pcap 文件打开 Wireshark 提示「文件损坏」？**
- 多半是 terminating 太快（pcap 头已写但末尾 packet 残缺）
- Wireshark 通常能容错——勾选「忽略 pcap 错误」试试
- 或者改用 1.5s timeout 让 tcpdump 多点时间刷盘

**Q：抓到很多包，但 Wireshark 显示「Unknown Protocol」？**
- 一些 App 用 QUIC / KCP / 自研协议 → 不在 Wireshark 默认协议列表
- 解码：右键包 → Decode As → 选协议

**Q：抓 loopback (`lo`) 为什么不需要 root？**
- `lo` 是回环网卡，所有 Unix socket / pipeline 都走它
- 部分设备允许非 root 抓 lo（设置 capability）
- 但有时抓不到其他 App 通信（同一台设备的 wlan0）

**Q：能直接在模拟器用吗？**
- 模拟器内 `tcpdump` 通常是**系统自带**的（Android Studio AVD）
- 但 `-i wlan0` 可能失败（模拟器无线是虚拟的）——试 `-i any` 或 `-i eth0`
- 模拟器抓包更简单：在 PC 端用 Wireshark 直接抓 WinPcap → TAP-Windows Adapter（如果装了 Genymotion / Android Studio 模拟器）

**Q：怎么最快生成一个可分析的 pcap？**
1. 连上模拟器
2. Super_ADB → tcpdump 抓包 → 默认参数 → 开始
3. 模拟器上开 Chrome 访问 `example.com`
4. Super_ADB → 停止
5. 桌面 `Super_ADB/tcpdump_*.pcap` 拖进 Wireshark
6. 输入 `dns` 或 `tcp.port == 80` 看
