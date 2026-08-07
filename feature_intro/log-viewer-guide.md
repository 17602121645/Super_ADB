# 「日志查看器（Logcat）」功能介绍

> 适用版本：Super_ADB Main 2026-08-07+
> 模块位置：`Super_ADB_Main/log_viewer_page.py`
> 关联文件：`fav_combo.py`、`adb_utils.py` (`AdbHelper`)

---

## 一、功能概览

日志查看器是主窗口右侧分屏的另一个内嵌子页面，专门负责 **`adb logcat` 实时抓取 + 本地日志回放**。8 件套能力：

1. **实时抓取** —— `QProcess` 流式启动 `adb -s <serial> logcat -v threadtime`，实时显示
2. **暂停 / 继续** —— 抓取过程可暂停渲染，按钮文字在「暂停 / 继续」间切换
3. **即时停止** —— 一键终止 logcat 进程（防卡顿优化）
4. **自动落盘** —— 抓取期间所有原始行写入 `~/Desktop/Super_ADB/adb_logcat_YYYYMMDD_HHMMSS.log`
5. **多维过滤** —— Tag / 包名（自动转 PID） / 消息（支持正则）
6. **过滤收藏** —— 常用过滤条件可加星收藏，下次自动恢复
7. **本地日志回放** —— 打开任意 `.log` / `.txt`，复用同一套过滤工具
8. **按 PID/Pkg 自动解析** —— 输入包名 `com.xxx`，后台查 `pidof` + 累积 `ps` 历史 PID 映射

设计目标：**让万行/秒的 logcat 也能在 UI 上流畅滚动、不卡顿、不丢日志**。

---

## 二、入口与触发

- **位置**：主窗口右侧分屏的「日志」标签页
- **首次进入**：构造时自动 `check_adb()` + `scan_devices()` 拉一次设备列表
- **设备同步**：主窗口在「连接/刷新设备」时通过 `sync_devices(devices)` 统一更新
- **顶层窗口 Move 事件监听**：用 `installEventFilter` 监听主窗口 `QEvent.Move`，识别用户拖动窗口 → 降频渲染

---

## 三、界面布局

顶部 2 段工具栏 + 中间日志列表 + 底部统计栏，与截图一一对应：

```
┌────────────────────────────────────────────────────────────────────┐
│ 设备:[25102RKBEC [emu] ▼] [刷新] [开始抓取] [暂停] [清除] [打开本地文件] │
├────────────────────────────────────────────────────────────────────┤
│ 标签:[_____▼ ★] 包名:[_____▼ ★] 消息:[_____▼ ★] □正则 [重置]       │
├────────────────────────────────────────────────────────────────────┤
│ 08-08 01:38:46.421 178 2010 W res         : validateDnsTlsServer...│
│ 08-08 01:38:46.421 178 2010 W res         : Validation failed        │
│ 08-08 01:39:00.019 204  228 D EmuHWC2     : sent 8 syncs in 60s      │
│ 08-08 01:39:42.628 364  496 I WifiConnMgr : SetInitialScanState...   │
│ 08-08 01:39:42.630 364  500 D IsntatWifi  : Drop, size: 1            │
│ 08-08 01:39:42.630 326  326 W wificond    : Scan already started     │
│ 08-08 01:39:42.630 326  326 I wificond    : Scan                     │
│ 08-08 01:39:43.631 364  644 D WifiNl80211Mgr: Scan result ready event│
├────────────────────────────────────────────────────────────────────┤
│ ☑跟随滚动            🔴 实时日志(已停止)  累计 12531 行 | 匹配 10000 │
└────────────────────────────────────────────────────────────────────┘
```

### 关键 UI 细节

- **5 段操作**：`刷新` 设备 / `开始抓取` / `暂停` / `清除` / `打开本地文件`
- **3 个收藏下拉框**：标签 / 包名 / 消息，每个右侧带 ★ 收藏按钮（点击把当前输入加入收藏）
- **正则 checkbox**：勾上后消息框按 `re.search` 匹配，placeholder 文案动态切换提示
- **`QListWidget` 主体**：等宽字体 + uniform item sizes，**只画可见行**（常数级 paint 开销）
- **底部状态**：跟随滚动 checkbox / 模式标签（实时 or 本地）/ 计数 label（累计 N 行 | 匹配 N 行 | 文件名 | 已暂停）

---

## 四、级别颜色与字体

```python
LEVEL_COLORS = {
    'V': '#9aa0a6',   # Verbose 浅灰
    'D': '#6db3f2',   # Debug   浅蓝
    'I': '#cfd8dc',   # Info    默认浅色
    'W': '#f5c542',   # Warn    黄色
    'E': '#ff6b6b',   # Error   红色 + 粗体
    'F': '#ff3b30',   # Fatal   深红 + 粗体
}
```

```python
# log_viewer_page.py:206 / 717
self._bold_font = QFont('Consolas', 9)
self._bold_font.setBold(True)
self._bold_font.setStyleHint(QFont.Monospace)
...
if e['level'] in ('E', 'F'):
    item.setFont(self._bold_font)
```

**E / F 级别**（Error / Fatal）自动加粗 + 红字，第一眼就能看到崩溃异常。

---

## 五、抓取工作流

### 1. 启动

```python
# log_viewer_page.py:443
def _start_capture(self):
    if not self._current_serial:
        self.status_label.setText('请先选择设备')
        return
    self._open_log_file()             # 1. 创建日志文件
    self._entries.clear()              # 2. 清空 entries
    self._pending_view.clear()
    self._line_buf = ''
    self._total = 0
    self.text_edit.clear()
    self._proc.start(
        self._mgr.adb_path,
        ['-s', self._current_serial, 'logcat', '-v', 'threadtime'],   # ← threadtime 格式
    )
    self._proc.waitForStarted(3000)    # 3. 启动 QProcess
    ...
    self._capturing = True
    self._flush_timer.start()          # 4. 启动 150ms 渲染定时器
    self._ps_timer.start()             # 5. 启动 3s ps 轮询定时器
```

**关键细节**：
- 用 `-v threadtime` 而不是 `-v time`，得到带 PID + TID 的完整格式（与 `_parse_line` 正则匹配）
- 启动后立即开始两个定时器：**150ms 渲染**（`_flush_timer`）+ **3s ps 轮询**（`_ps_timer`）

### 2. 数据流入

```python
# log_viewer_page.py:582
def _on_data(self):
    if not self._capturing:
        self._proc.readAllStandardOutput()   # ← 关键：stop 后即使事件队列里残留的旧调用也直接 drain
        return
    data = bytes(self._proc.readAllStandardOutput()).decode('utf-8', 'replace')
    cnt = 0
    self._line_buf += data
    with self._raw_lock:
        while '\n' in self._line_buf:
            line, self._line_buf = self._line_buf.split('\n', 1)
            line = line.rstrip('\r')
            if line:
                self._raw_lines.append(line)
                if self._log_file:
                    self._write_buf.append(line)
                cnt += 1
    self._maybe_start_parse()
```

**关键设计**：
- `_on_data` 只做"读 buffer + 拆行 + 入原始行缓冲"，**零正则解析**
- 解析/过滤交给后台 `_CmdWorker`（`_start_parse_worker`），避免万行/批的正则冻结 UI
- 磁盘落盘也只 append 到 `_write_buf`，由 `_flush_view` 批量 flush

### 3. 解析（后台线程）

```python
# log_viewer_page.py:611
def _start_parse_worker(self):
    with self._raw_lock:
        if not self._raw_lines:
            return
        batch = self._raw_lines
        self._raw_lines = []
    self._parsing = True
    f_tag = self._filter_tag
    f_pids = set(self._filter_pids)
    f_msg = self._filter_msg

    def _task():
        entries = []
        matched = []
        for raw in batch:
            e = _parse_line(raw)
            entries.append(e)
            if _match_entry(e, f_tag, f_pids, f_msg, self._filter_regex):
                matched.append(e)
        return entries, matched

    w = _CmdWorker(_task)
    w.signals.result.connect(self._on_parsed)
    self._pool.start(w)
```

**防过载**：`_parsing` 旗标保证同时只有 1 个解析 worker 在跑，新数据持续入 `_raw_lines` 等待下一轮解析。worker 完成后回调 `_on_parsed` 检查 `self._raw_lines` 还有数据就续跑 —— **天然限速**，避免解析慢于产入导致内存膨胀。

### 4. 渲染（150ms 一次）

```python
# log_viewer_page.py:731
def _flush_view(self):
    t0 = time.perf_counter()
    # 先批量写盘
    if self._write_buf and self._log_file:
        try:
            self._log_file.write('\n'.join(self._write_buf) + '\n')
        except Exception:
            pass
        self._write_buf.clear()

    if not self._pending_view:
        return

    # 拖动窗口期间：降频渲染（小批量 + 跟随滚动）
    if self._dragging:
        if len(self._pending_view) > VIEW_MAX_BLOCKS:
            self._pending_view = self._pending_view[-VIEW_MAX_BLOCKS:]
        batch = self._pending_view[:DRAG_BATCH] if len(self._pending_view) > DRAG_BATCH else self._pending_view
        self._pending_view = self._pending_view[len(batch):]
        if batch:
            self._insert_batch(batch)
            if self.follow_chk.isChecked():
                self.text_edit.scrollToBottom()
        self._update_count()
        return

    # 正常情况：单批 200 行，超出分多帧
    MAX_BATCH = 200
    if len(self._pending_view) > MAX_BATCH:
        batch = self._pending_view[:MAX_BATCH]
        self._pending_view = self._pending_view[MAX_BATCH:]
        QTimer.singleShot(0, self._flush_view)
    else:
        batch = self._pending_view
        self._pending_view = []
    self._insert_batch(batch)
    ...
```

**两个机制**：
- **200 行 / 帧** 批量插入：单次 addItem 更轻，事件循环能及时响应窗口拖动/点击
- **拖动期间 `DRAG_BATCH=100` 小批量**：监听主窗口 Move 事件 → `_dragging=True`，每帧只插 100 行，松手 300ms 后 `_dragging=False` 恢复全速

### 5. 停止（即时响应，2026-08-07 修复卡顿）

```python
# log_viewer_page.py:485
def _stop_capture(self):
    if not self._capturing:
        return
    # 立刻更新 UI 状态，**不等待进程结束**
    self._capturing = False
    self._line_buf = ''
    with self._raw_lock:
        self._raw_lines = []
    self._pending_view.clear()
    self._flush_timer.stop()
    self._ps_timer.stop()
    self.btn_start.setText('开始抓取')
    self.btn_start.setEnabled(False)
    ...
    self.status_label.setText('正在停止…')

    # 先 flush 磁盘缓冲，保证已读到的日志不丢
    if self._write_buf and self._log_file:
        try:
            self._log_file.write('\n'.join(self._write_buf) + '\n')
            self._write_buf.clear()
        except Exception:
            pass

    # 主动 drain 一次 pipe 缓冲
    try:
        self._proc.readAllStandardOutput()
    except Exception:
        pass

    # 异步终止进程
    if self._proc.state() != QProcess.NotRunning:
        self._proc.terminate()
        QTimer.singleShot(500, self._ensure_process_killed)
    else:
        self._finalize_stop()
```

**关键点**（2026-08-07 修复）：
- 早期版本 stop 后主线程被事件队列里残留的 `_on_data` 旧调用逐个跑完（带正则 parse + filter）卡死数秒
- 修复：**所有持续接收数据的 Qt 信号回调都要带"快速跳过"哨兵**——`_on_data` 第一行就检查 `self._capturing`，False 直接 drain buffer 返回
- `_stop_capture` 不等进程结束，先更新 UI → 用户立即看到「已停止」
- `_pending_view` 清空 → 屏幕立即停止刷新（已渲染内容保留）
- 磁盘先 flush → 不丢已读到的日志

**进程异步终止链**：

```
_stop_capture ──→ _proc.terminate()
                  └─ 500ms 后 _ensure_process_killed()
                       └─ 还在跑 → _proc.kill()
                          └─ 300ms 后 _finalize_stop() 收尾
```

---

## 六、过滤系统

### 6.1 三个维度

| 维度 | 输入 | 匹配方式 |
|---|---|---|
| **标签** | 子串 | `filter_tag.lower() in entry['tag'].lower()`（大小写不敏感） |
| **包名/PID** | `com.xxx.app 1234 5678` 混合 | 自动分类：纯数字→PID；否则当包名→查 `pidof` + 历史 PID |
| **消息** | 子串 / 正则 | 默认大小写不敏感子串；勾选「正则」后用 `re.search` |

> 三个维度**取交集**（AND 关系）。

### 6.2 包名 → PID 解析

输入 `com.reathin.adbwifi`，自动转成它当前的所有 PID：

```python
# log_viewer_page.py:957
def _resolve_pkg_pids(self, pkgs, seq):
    ...
    def _task():
        found = {}
        for pkg in pkgs:
            pids = set(hist.get(pkg, set()))   # 1. 先查 ps 累积的历史 PID
            try:
                out = self._mgr.run_shell(serial, f'pidof {pkg}', timeout=5)  # 2. 再查实时 PID
                pids.update(out.split())
            except Exception:
                pass
            if pids:
                found[pkg] = sorted(pids)
        return found

    w = _CmdWorker(_task)
    w.signals.result.connect(lambda found: self._on_pkg_pids(found, pkgs, seq))
    ...
```

**关键设计**：
- **PID 累积映射 `_pkg_pid_map`**：抓取期间每 3 秒跑一次 `ps -A -o PID,NAME` 累积「包名 → 历史 PID 集」
- 进程**重启后旧日志仍能命中** —— 之前抓到的 PID 不再是当前 PID，但被记录在历史里，过滤时一并考虑

### 6.3 防抖 + 重渲染

```python
# log_viewer_page.py:211
self._filter_timer = QTimer(self)
self._filter_timer.setInterval(250)
self._filter_timer.setSingleShot(True)
self._filter_timer.timeout.connect(self._apply_filter)
```

输入时 250ms 内不再变化才应用过滤 —— 避免每次按键都触发全量重渲染（10 万条遍历可能 1-2 秒）。

```python
# log_viewer_page.py:797
def _rerender(self):
    """异步重渲染：主线程快速快照 entries+过滤参数，后台线程做全量匹配"""
    seq = self._filter_seq
    entries_snapshot = list(self._entries)        # 仅复制指针（~1ms/10万条）
    f_tag = self._filter_tag
    f_pids = set(self._filter_pids)
    f_msg = self._filter_msg

    def _task():
        matched = [e for e in entries_snapshot if _match_entry(e, f_tag, f_pids, f_msg, self._filter_regex)]
        shown = matched[-RENDER_MAX:]              # 只渲染最近 8000 条
        return {'matched_count': len(matched), 'shown': shown, 'seq': seq}

    w = _CmdWorker(_task)
    w.signals.result.connect(self._on_rerender_done)
    ...
```

**`_filter_seq` 丢弃过期结果**：用户连续改过滤条件时，前几次的 worker 结果会被 `seq != self._filter_seq` 直接丢弃。

### 6.4 正则

```python
# log_viewer_page.py:114
if filter_msg:
    msg = entry['msg']
    if filter_regex:
        try:
            ok = re.search(filter_msg, msg) is not None
        except re.error:
            ok = filter_msg.lower() in msg.lower()     # ← 非法正则退化为子串，避免误隐藏
    else:
        ok = filter_msg.lower() in msg.lower()
```

**防误操作**：用户输入未闭合的括号等非法正则时，**不会让所有日志消失**，自动退化为子串匹配。

---

## 七、过滤收藏

标签/包名/消息三个下拉框都是 `FavComboBox`（`fav_combo.py` 自研控件），支持：

- 输入文字后点 ★ → 加入收藏
- 下拉框自动列出历史收藏项
- 收藏列表持久化到 `adb_shell_config.json` 的 `log_favs` 键

```python
# log_viewer_page.py:914
def _load_favs(self):
    favs = load_json_config(CONFIG_NAME).get(FAV_KEY) or {}
    self.tag_combo.set_favorites(favs.get('tag'))
    self.proc_combo.set_favorites(favs.get('proc'))
    self.msg_combo.set_favorites(favs.get('msg'))

def _on_favs_changed(self, key, items):
    cfg = load_json_config(CONFIG_NAME)
    favs = cfg.get(FAV_KEY) or {}
    favs[key] = items
    cfg[FAV_KEY] = favs
    save_json_config(CONFIG_NAME, cfg)
```

**典型用法**：
- 收藏 `EmuHWC2`、`wificond`、`ActivityManager` 等常用 tag
- 收藏 `com.reathin.adbwifi` 这类自己的包名
- 收藏 `Exception` / `ANR` / `Force` 这类错误关键字

---

## 八、本地日志回放

点击「打开本地文件」 → 选 `.log` / `.txt` → 同步 `_ingest()` 每行进入 entry → `_rerender()` 复用过滤工具显示。

```python
# log_viewer_page.py:1093
def _load_local_file(self):
    if self._capturing:
        self.status_label.setText('正在抓取中，请先停止')
        return
    path, _ = QFileDialog.getOpenFileName(self, '选择日志文件', self._desktop,
                                          '日志文件 (*.log *.txt);;所有文件 (*)')
    if not path: return
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.read().splitlines()
    except Exception as e:
        self.status_label.setText(f'打开失败: {e}')
        return
    self._entries.clear()
    self._pending_view.clear()
    self._total = 0
    for line in lines:
        if line:
            self._ingest(line)
    self._mode = 'local'
    self._log_path = path
    self.text_edit.clear()
    self._rerender()
    self._update_mode_label()
    self.status_label.setText(f'已加载 {len(lines)} 行: {os.path.basename(path)}')
```

> 与实时抓取共用同一套过滤 / 渲染 / 收藏工具，无需切换 UI。

---

## 九、自动落盘

每次「开始抓取」自动创建文件：

```python
# log_viewer_page.py:1052
def _open_log_file(self):
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    try:
        os.makedirs(self._save_dir, exist_ok=True)
    except Exception as e:
        ...
        return
    path = os.path.join(self._save_dir, f'adb_logcat_{ts}.log')
    self._log_file = open(path, 'a', encoding='utf-8', buffering=-1)
    ...
```

**输出位置**：`~/Desktop/Super_ADB/adb_logcat_YYYYMMDD_HHMMSS.log`

**写入策略**：原始行先入 `_write_buf`，由 `_flush_view()` 每 150ms 批量 `write('\n'.join(_write_buf) + '\n')`，**降低磁盘 IO 次数**。

> 右键菜单「打开保存目录」一键 `QDesktopServices.openUrl` 跳到该目录。

---

## 十、性能优化亮点（这是日志模块的灵魂）

日志模块踩过/解决过好几次严重的卡顿坑，全部沉淀在代码里：

### 1. 万行/秒卡死主线程（**P0**）

**问题**：早期版本 `_on_data` 在主线程直接 `_parse_line` + `_match_entry`，万行 batch 时正则匹配冻结主线程 1-2 秒。

**解决**：拆出后台线程。
```
主线程: _on_data ─── 读 buffer + 拆行 + 入 _raw_lines
                                ↓
后台线程:  _start_parse_worker ─── _parse_line + _match_entry
                                ↓
主线程: _on_parsed ─── extend entries + extend pending_view（不直接渲染）
                                ↓
主线程: _flush_view (150ms 定时器) ─── _insert_batch (200 行/帧)
```

### 2. 渲染阻塞（QListWidget vs QPlainTextEdit）

**问题**：`QPlainTextEdit` 内部是文本块结构，万行后滚动 + 拖动窗口卡死。

**解决**：改用 `QListWidget` + `setUniformItemSizes(True)`。
- uniform item sizes → paint 时只画**可见行**，与文档总行数无关
- `_trim_list` 头部裁剪维持 `VIEW_MAX_BLOCKS=10000`
- 配合 `_entries = deque(maxlen=100_000)` 总缓存上限

### 3. 拖动窗口卡死

**问题**：早期版本用户拖动主窗口时主线程被逐帧 `self.move()` 拖垮，日志渲染排队。

**解决**：
1. 窗口移动改用 `startSystemMove()` 系统原生拖动（主窗口层处理）
2. `LogViewerPage.eventFilter` 监听顶层窗口 `QEvent.Move` → `_dragging=True`
3. 拖动期间 `_flush_view` 改用 `DRAG_BATCH=100` 小批量
4. 300ms 静止后 `_on_drag_resume` 恢复 `_dragging=False`

### 4. 过滤重渲染 10 万条遍历冻结

**问题**：改过滤条件后全量遍历 `_entries` 在主线程跑 1-2 秒。

**解决**：`_rerender` 走后台线程，主线程只做 `list(self._entries)` 快照（~1ms / 10 万条，远快于匹配遍历）。

### 5. 停止卡死（2026-08-07 修复）

**问题**：stop 后事件队列里残留的 `_on_data` 旧调用逐个跑完（带正则 parse + filter）吃满主线程。

**解决**：`_on_data` 第一行加哨兵 `if not self._capturing: self._proc.readAllStandardOutput(); return` —— stop 后旧调用瞬间全退。

### 6. 解析 vs 渲染节奏不匹配

**问题**：解析 worker 每 ~16ms 完成一次就触发渲染链，主线程 100% 占满。

**解决**：解析完成后 `_on_parsed` **不直接调 `_flush_view`**，只 extend pending 列表；渲染统一交给 150ms 定时器。

### 7. 进程终止阻塞

**问题**：`waitForFinished()` 阻塞主线程数秒。

**解决**：异步终止链 `terminate() → 500ms → kill() → 300ms → _finalize_stop()`。

---

## 十一、线程模型一览

| 线程 | 类型 | 职责 |
|---|---|---|
| 主线程 | Qt GUI | UI 交互、`_on_data`（轻）、`_flush_view`（150ms 定时）、`_insert_batch`、右键菜单 |
| `_proc` | QProcess | `adb logcat -v threadtime` 流式 stdout |
| `QThreadPool(max=3)` | 3 worker | 解析 + 过滤重渲染 + 包名 PID 解析 + ps 轮询 + 设备扫描 |
| `_flush_timer` | QTimer 150ms | 触发 `_flush_view`（批量写盘 + 批量渲染） |
| `_ps_timer` | QTimer 3s | 触发 `_poll_processes`（累积 PID 历史） |
| `_filter_timer` | QTimer 250ms 单次 | 触发 `_apply_filter`（输入防抖） |
| `_drag_resume_timer` | QTimer 300ms 单次 | 触发 `_on_drag_resume`（恢复全速渲染） |

**`_CmdWorker` 三信号**：`result`、`error`、`finished`；主线程通过 `_live_workers` / `_rerender_worker` / `_pkg_worker` / `_ps_worker` 显式持有引用，防止被 GC。

---

## 十二、调试埋点系统

模块顶部有个内置调试开关：

```python
# log_viewer_page.py:24
_DBG = False  # ← 默认关闭（零开销）
_DBG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logcat_debug.log')
```

排查卡顿时把 `_DBG = True`：
- 日志同时**写入程序目录的 `logcat_debug.log`**（命令行启动或把文件发回分析）
- 同时 print 到 stdout
- 关键节点全部埋点：`START / STOP / KILL / DATA / PARSE / FLUSH / DRAG / FINAL`

**典型埋点**：

```python
_dbg('TOGGLE', f'click _capturing={self._capturing}')
_dbg('START', f'waitForStarted -> {started}, blocked_main_thread={time.perf_counter() - t0:.3f}s')
_dbg('DATA', f'recv={cnt} raw={len(self._raw_lines)}')
_dbg('FLUSH', f'batch={len(batch)} pending_left={len(self._pending_view)} cost={time.perf_counter() - t0:.3f}s')
_dbg('DRAG', 'resume UI render')
```

> 默认 `_DBG=False` 时 `_dbg()` 第一行 return，**零开销**；关闭状态下程序目录不会有 `logcat_debug.log` 文件。

---

## 十三、代码结构速查

| 文件 | 关键内容 |
|---|---|
| `log_viewer_page.py` | `LogViewerPage` 主页面 + `_parse_line` + `_match_entry` + `_CmdWorker(QRunnable)` + 调试埋点系统 |
| `fav_combo.py` | `FavComboBox` —— 可收藏下拉框控件 |
| `adb_utils.py:35-48` | `load_json_config` / `save_json_config` —— 收藏 + 配置持久化 |
| `adb_utils.py:64`    | `AdbHelper.get_devices` —— 设备扫描 |
| `adb_utils.py:154`   | `AdbHelper.run_shell` —— `pidof` / `ps -A -o PID,NAME` |

---

## 十四、边界与限制

| 场景 | 行为 |
|---|---|
| 无设备 | 「开始抓取」按钮 disabled，状态栏「无设备」 |
| 启动 logcat 失败（`waitForStarted` 3 秒超时） | 状态栏「logcat 启动失败」+ 关闭日志文件 |
| 设备中途断开（`_on_error`） | 状态栏「logcat 出错: <err>」 |
| 抓取中切设备 | 自动 stop + 清 `_pkg_pid_map` |
| 抓取中点「打开本地文件」 | 「打开本地文件」按钮 disabled，状态栏「正在抓取中，请先停止」 |
| 输入非法正则 | 退化为子串匹配，不会误隐藏全部日志 |
| 进程 `terminate` 500ms 后还在跑 | `_ensure_process_killed` 强制 `kill()` |
| 累计行数 > 100 000 | `_entries` deque 自动从头部裁剪 |
| 渲染行数 > 10 000 | `_trim_list` 从头部裁剪（O(1) takeItem） |
| 跟随滚动关闭 | 用户向上滚动查看历史时，新日志不抢屏 |
| 本地文件回放 vs 实时模式 | 状态栏模式标签 + 不同模式文案 |

---

## 十五、快速用例

### 用例 1：抓崩溃现场

1. 选设备 → 点「开始抓取」
2. 触发 App 崩溃
3. 在 Tag 框输 `AndroidRuntime`（或点 ★ 收藏它），Message 输 `Exception`（勾正则）
4. 抓取窗口自动滚动到崩溃堆栈
5. 右键选中堆栈 → 「复制选中行」贴到 bug 系统
6. 点「停止抓取」 → 右键「打开保存目录」拿完整 `.log`

### 用例 2：只看自己 App 的日志

1. 选设备 → 点「开始抓取」
2. 在「包名」框输 `com.reathin.adbwifi`（或点 ★ 收藏）
3. 后台自动解析成 PID → 状态栏显示「包名 → PID: 12345」
4. 进程重启 → 3 秒内自动累积新 PID 继续命中

### 用例 3：本地日志回溯昨天的问题

1. 找到昨天保存的 `adb_logcat_20260807_xxx.log`
2. 点「打开本地文件」选它
3. 模式标签变 `📄 本地文件: adb_logcat_20260807_xxx.log`
4. 复用 Tag/包名/消息 过滤工具精确定位问题时段

### 用例 4：高日志量设备调试（5k 行/秒）

1. 选设备 → 点「开始抓取」
2. 日志持续高速涌入
3. 拖动窗口看其它区域 → 自动降频渲染（100 行/帧），不卡顿
4. 松手 300ms 后自动恢复全速
5. 计数 label 实时显示「累计 X 行 | 匹配 Y 行」

### 用例 5：调试工具自身卡顿

1. 编辑源码把 `_DBG = False` 改成 `_DBG = True`
2. 重启程序 → 命令行启动捕获 stdout
3. 复现卡顿 → 抓 `logcat_debug.log` + stdout 分析
4. 排查完改回 `_DBG = False`（零开销）

---

## 十六、未来可扩展点（idea，未实现）

- [x] **日志关键字高亮**（已实现，2026-08-08）：用户可配置关键字（如 `Exception`、`ANR`），命中行背景变红
- [ ] **侧边栏按 PID 树状分组**：按 App 进程分组显示
- [ ] **保存到云端**：.log 一键上传到 OSS / S3
- [ ] **导出过滤结果**：只把当前过滤命中的行另存为新 .log
- [ ] **历史抓取会话列表**：显示最近 N 次抓取的元信息（设备/起止时间/总行数）
- [ ] **结构化字段过滤**：按 PID / TID 精确过滤（而非仅包名）
- [ ] **实时统计**：每秒日志速率、Warn/Error 占比饼图
- [ ] **跨进程日志合并**：同时拉多台设备的 logcat 按时间戳合并
- [ ] **崩溃堆栈解析**：自动识别 Java/Kotlin 堆栈并折叠展开
- [ ] **快捷键**：Ctrl+F 搜索 / Ctrl+L 清空 / Ctrl+S 保存 / F5 暂停切换

---

> 📌 文档版本 v1 · 2026-08-08 · 悠悠整理 🐱