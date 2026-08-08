# 「局域网 ADB 设备扫描」功能介绍

> 适用版本：Super_ADB Main 2026-08-08+
> 模块位置：`Super_ADB_Main/dialogs/lan_scanner_dialog.py`
> 关联文件：`Super_ADB_Main.py`（入口 `open_lan_scanner`）、`adb_utils.py`（`AdbHelper.connect`）、`popup_style.py`（`add_green_glow`）

---

## 一、功能概览

做 Android 调试的人大概都有过这种经历：

- 设备开了**无线调试**，但你不知道它分到的 IP 是哪一个；
- 路由器开了客户端隔离，不知道设备是不是真在同一网段；
- 一个一个试 `adb connect 192.168.1.x`，试到手酸。

「局域网 ADB 设备扫描」把这件事一键搞定：**给一个网段**，它就在后台并发地挨个 IP 去敲门（ADB 无线调试默认端口 **5555**），谁开着门就把谁的 IP、延迟亮给你看，然后**一键连接**。

简单说：**「给我一个网段」** → **「咔咔扫一遍」** → **「在线的给你列出来」** → **「点连接就上手了」**。

### 它解决什么

| 痛点                           | 这个功能怎么治                                             |
|--------------------------------|------------------------------------------------------------|
| 不知道设备 IP                  | 自动识别本机 IP，预填默认 `/24` 网段到下拉框                |
| `adb connect` 要挨个试         | 后台 100 线程并发 socket `connect` + ADB 握手验证          |
| 扫到一半发现扫错了             | 任意时刻点「停止扫描」，1 秒内退出                          |
| 找到 N 台设备要挨个连接        | 「一键连接全部」确认一次后批量串行连接                      |
| 结果想发给同事                 | 「复制所有 IP」直接 `192.168.1.42:5555` 进剪贴板            |
| 表格太长，眼睛找不到列         | 交替行底色 + IP 居中 + 延迟一位小数 + 状态色                |

---

## 二、入口与触发

- **位置**：主窗口「便捷工具」区，**「局域网扫描」** 按钮（与「JSON 工具」「MD5 计算」等同区）。
- **行为**：点击后弹出独立 `QDialog` 窗口；标题「局域网 ADB 设备扫描」。
- **重复点击**：窗口已开就 `raise_()` + `activateWindow()` 前置，不会重复开。
- **图标**：项目统一 `:/Super_ADB.png`（`png_rc` 注册的 Qt 资源）。

```python
# Super_ADB_Main.py:997
def open_lan_scanner(self):
    """打开局域网 ADB 设备扫描弹窗（复用窗口，重复点击 raise）。"""
    if self._lan_scanner_dialog is not None and self._lan_scanner_dialog.isVisible():
        self._lan_scanner_dialog.raise_()
        self._lan_scanner_dialog.activateWindow()
        return
    self._lan_scanner_dialog = LanScannerDialog(parent=self)
    self._lan_scanner_dialog.show()
```

---

## 三、界面布局

弹窗默认 **680 × 480** 起步，深色主题 + 青绿色高亮边框（与项目所有弹窗统一风格）。从上到下 4 段：

```
┌─ 局域网 ADB 设备扫描 ─────────────────────────────────┐
│ ┌─ 扫描设置 ──────────────────────────────────────┐    │
│ │  IP 范围: [192.168.1.0/24 (本机 192.168.1.50) ▼] │    │
│ │  超时:    [400 ms]    线程: [100]    端口: [5555] │    │
│ └─────────────────────────────────────────────────┘    │
│  [▶ 开始扫描]  [一键连接全部]  [复制所有 IP]    就绪    │
│  ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░  扫描中... 87/254 (34%)          │
│ ┌─ 扫描结果 ──────────────────────────────────────┐    │
│ │   IP 地址       │ 状态 │ 延迟 (ms) │ 操作        │    │
│ │ ────────────────┼──────┼───────────┼────────── │    │
│ │ 192.168.1.42    │ 🟢 在线 │  3.2     │ [ 连接 ] │    │
│ │ 192.168.1.108   │ 🟢 在线 │  8.7     │ [ 连接 ] │    │
│ │ 192.168.1.215   │ 🟢 在线 │ 41.0     │ [ 连接 ] │    │
│ └─────────────────────────────────────────────────┘    │
│ 💡 提示：双击在线设备可直接连接；扫描的是 TCP/IP ADB    │
│    调试端口（5555），请确保目标设备已开启「无线调试」   │
└─────────────────────────────────────────────────────┘
```

### 关键 UI 细节

- **绿色发光边框** + **青绿 accent** 来自 `popup_style.add_green_glow()`，与其它弹窗视觉一致。
- **自动检测本机网段**：启动时 `socket.getaddrinfo` 拿到所有 IPv4，**排除 `127.` 环回**，为每个 `/24` 网段生成一条 `192.168.1.0/24 （本机 192.168.1.50）` 自动入下拉框。多个网卡（如同时插着 Wi-Fi 和有线）会全部列出供选。
- **下拉框可编辑**：除了下拉选，也可以直接键入自定义 IP 范围。
- **进度条仅在扫描中可见**：默认隐藏，`_start_scan` 显示、`_cleanup_thread` 后隐藏。
- **状态栏标签**实时反馈：「就绪」→「正在扫描 254 个地址...」→「扫描中... 87/254 (34%)」→「已发现 3 台设备...」→「✅ 扫描完成：共 254 个地址，发现 3 台 ADB 设备」。
- **结果表**：4 列；IP 列 Stretch（自适应宽度），状态/延迟/操作列固定宽；交替行底色；**扫描完成后按延迟升序自动重排**（最短延迟排最前）；空结果时合并 4 列显示一行灰色提示「未在当前网段发现 ADB 设备（端口 5555）」。
- **端口可配**：扫描设置里新增 `端口` 输入框（默认 5555，范围 1–65535），改端口会同步更新底部提示文案；扫描、连接、复制 IP 三处都走该端口。

---

## 四、IP 范围输入格式（三种皆可）

`_parse_ip_range(text)` 是个静态方法，按以下顺序匹配：

| 格式       | 示例                       | 适用场景                              |
|------------|----------------------------|---------------------------------------|
| **CIDR**   | `192.168.1.0/24`           | 整网段（最常用，自动排除 .0 和 .255）  |
| **起止区间** | `192.168.1.1-192.168.1.254` | 子范围（避开 .0 广播或过滤冗余段）    |
| **单个 IP** | `192.168.1.42`             | 单点探测（已知某台机器，验证可达性）   |

```python
# Super_ADB_Main/dialogs/lan_scanner_dialog.py:241
@staticmethod
def _parse_ip_range(text):
    """解析用户输入的 IP 范围，返回 IPv4Address 列表。
    支持格式：
      - CIDR:   192.168.1.0/24
      - 范围:   192.168.1.1-192.168.1.254
      - 单个:   192.168.1.100
    """
```

输入解析失败不会崩溃：在主窗口触发扫描时会弹 `QMessageBox.warning`，把三种支持格式贴给用户复制。

---

## 五、扫描线程模型（这是核心）

```python
# Super_ADB_Main/dialogs/lan_scanner_dialog.py:37
class _ScanWorker(QObject):
    """后台扫描线程：遍历 IP 列表，逐个探测 ADB 端口。"""

    found    = Signal(str, float, object)   # ip, latency_ms, extra_info
    progress = Signal(int, int)             # current, total
    finished = Signal(list)                 # [(ip, latency_ms), ...] 全量
    stopped  = Signal()
```

### 整条链

```
[▶ 开始扫描]
   │
   ▼
LanScannerDialog._start_scan
   │ ├─ _parse_ip_range       UI 线程
   │ ├─ table.setRowCount(0)  清旧结果
   │ ├─ btn_scan → "■ 停止扫描"，三个 spin 灰
   │ ├─ QThread + worker.moveToThread()
   │ └─ _scan_thread.start()
   │
   ▼
[QThread 跑 _ScanWorker.run]
   │
   │ ┌─ ThreadPoolExecutor(max_workers=user_set)
   │ │   │
   │ │   ├─ _probe(ip): socket.connect((ip, 端口))
   │ │   │     ├─ 成功:  latency_ms = (monotonic - t0)*1000
   │ │   │     │          recv(4) == b'CNxn'   ← ADB 握手包
   │ │   │     │          ↓
   │ │   │     │     emit found(ip, latency_ms, None)
   │ │   │     │
   │ │   │     └─ 失败/超时: return None
   │ │   │
   │ │   └─ 每 20 个完成 → emit progress(current, total)
   │ │
   │ └─ as_completed(...)   cancel 时 cancel() 所有未完成的 future
   │
   ▼
emit finished(results) → _on_scan_finished (UI 线程)
   │ ├─ _cleanup_thread  (wait 3000ms)
   │ ├─ btn_scan → "▶ 开始扫描"
   │ ├─ btn_connect_all / btn_copy_all 启用
   │ └─ lbl_status → "✅ 扫描完成：共 254 个地址，发现 3 台 ADB 设备"
```

### 探测逻辑的三个细节

1. **超时**：`socket.settimeout(self._timeout)`，单位秒，默认 `0.4`。UI 上是 100–2000 ms 滑块。低超时会让 2.4G 远端的设备漏检，可调高到 800+。
2. **ADB 协议校验**：`s.recv(4)` 必须等于 `b'CNxn'`（`CNXN` 魔数 + 几个字节），**仅仅是 TCP 端口开放不算数**——开了 SSH、HTTPS、文件共享服务都会被剔除。「读不到字节」则放行（部分设备握手慢），保守一点不漏报。
3. **延迟测量**：从 `socket.connect()` 起到连接成功，`monotonic()` 精度，亚毫秒记一位小数。

### 取消 / 停止

- 「停止扫描」直接调 `worker.cancel()` 把 `_cancelled = True`。
- 主循环每收一个 `as_completed` 就 check 一次：置位后 `pool.cancel` 全部未完成 future，`emit stopped` → `_on_scan_stopped`（UI 线程重置按钮/进度条，状态显示「⛔ 扫描已停止」）。
- 关闭窗口（X）走 `closeEvent` 自动停 + `wait(2000)`，杜绝后台线程悬挂。

### 进度与刷新节流

每 20 个完成就 `emit progress`，**避免海量信号把 UI 线程淹没**（254 个 IP 全发信号会卡死主线程）。同时 `found` 信号只在命中时发——空 IP 不会每 20 个汇报一次。表格内 `setRowCount + insertRow` 单线程在 UI 线程统一处理，无竞态。

---

## 六、连接与导出

### 5.1 单台连接

方式 A —— 行尾的 `连接` 按钮 / 双击整行：
```python
# lan_scanner_dialog.py:429
def _connect_one(self, ip):
    target = f"{ip}:{self._port}"
    parent = self.parent()
    if parent and hasattr(parent, 'adb') and hasattr(parent, '_do_connect'):
        parent._do_connect(target)
    else:
        from adb_utils import AdbHelper
        adb = AdbHelper()
        result = adb.connect(target)
        QMessageBox.information(self, "连接结果", f"{target}\n{result}")
    # 连接成功后回填机型名
    self._enrich_after_connect(ip)
```

> **机型回填**：连接成功后会在**后台线程** `getprop ro.product.brand/model` 拿到机型名（如 `Pixel 7`），再回到 UI 线程把状态列从「🟢 在线」改成「🟢 在线 · Pixel 7」。后台线程执行，不阻塞界面；设备未授权/不可达时静默跳过，不影响连接结果。单台连接与「一键连接全部」都会触发回填。

**优先调主窗口的 `_do_connect(ip:port)`**：与顶栏手填 IP + 点击连接走的是**同一条路径**——连接成功会刷新设备下拉框、状态栏变绿、设备列表里出现新行；
**失败回退**：`AdbHelper.connect(ip)` 调起 `adb` 子进程，结果以 `QMessageBox` 弹窗显示。

### 5.2 一键连接全部

```python
# lan_scanner_dialog.py:423
def _connect_all_found(self):
    reply = QMessageBox.question(
        self, "确认连接",
        f"确定要连接全部 {len(self._found_ips)} 台设备吗？\n"
        + "\n".join(f"  • {ip}" for ip in self._found_ips),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    ...
```

弹出确认窗（列出每个 IP），同意后**串行** `AdbHelper.connect(ip)` 每一台，结果放入 `QMessageBox.setDetailedText(...)`（可折叠展开，方便拷贝日志）。

### 5.3 复制所有 IP

```python
# lan_scanner_dialog.py:447
def _copy_all_ips(self):
    text = "\n".join(f"{ip}:{ADB_PORT}" for ip in self._found_ips)
    QApplication.clipboard().setText(text)
    self.lbl_status.setText(f"已复制 {len(self._found_ips)} 个地址到剪贴板")
```

格式 `ip:5555` 每行一个，直接贴到 `adb -s ...` 批处理脚本里就能用。

---

## 七、可调参数

| 参数      | 范围     | 默认  | 说明                                                                 |
|-----------|----------|-------|----------------------------------------------------------------------|
| **IP 范围** | 文本     | `/24` 自适应 | 见 §四 三种格式                                         |
| **超时**    | 100–2000 ms | 400 ms | `socket.settimeout` 值；越小越快但漏检率越高              |
| **线程**    | 10–256   | 100   | `ThreadPoolExecutor(max_workers=...)`；本机性能差的设备调低避免 socket 撑爆 |
| **端口**    | 1–65535 | 5555  | ADB 无线调试端口；`socket.connect`、连接、复制 IP 三处统一使用 |

> **调优建议**：扫一段 `/24` 254 个地址，默认参数在办公网络大约 **6–12 秒**。觉得慢：把线程加到 200 + 超时拉到 200ms；觉得漏：超时 1000ms + 线程 50 减半压力。

---

## 八、依赖与线程安全

- **不依赖主窗口状态**：除「连接」按钮需复用主窗口 `self.parent().adb` 之外，扫描/解析/进度全部自洽，**主窗口无选中设备时也能用**。
- **依赖项**：`界面样式.py`（ACCENT/FONT_FAMILY/STYLE_SHEET）、`popup_style.add_green_glow`、`adb_utils.AdbHelper`、`png_rc`（图标资源）。
- **线程模型**：
  - QThread 跑 `_ScanWorker.run`（一个后台 worker）；
  - 内部 `ThreadPoolExecutor` 跑 `_probe`（最多 256 个真正并发的 socket 连接）；
  - UI 端所有交互（表格、行、按钮）一律在主线程，靠 Qt 信号跨线程。
- **资源回收**：扫描线程在 `_cleanup_thread` 内 `quit + wait(3000)`；**关闭弹窗（`closeEvent`）时直接 `cancel + quit + wait(2000)` 并置 `_closing` 标记**，让所有信号回调在窗口销毁后早退、绝不触碰已释放的 widget，从根上杜绝关窗崩溃。

---

## 九、常见问题

| 现象                                                | 排查                                                                 |
|-----------------------------------------------------|----------------------------------------------------------------------|
| 弹出后下拉框空，没有「本机 xxx 网段」选项           | 这台机器没拿到非环回 IPv4（极少见）；手动输入 CIDR 即可               |
| 扫得很快但一台都没找到                              | 大概率设备没开「**无线调试**」；或与本机不在同一网段（VLAN 隔离）    |
| 超时调到 100 还能找到                               | 网络好的办公环境更激进的话可以缩到 200 ms                            |
| 找到但「连接」失败                                   | 设备无线调试授权过期（第一次 USB 配过对的设备重连需要重新授权），到设备上点「允许」|
| 「一键连接全部」结果列表里有 `failed to connect`    | 部分设备被路由器限制了入站连接或开了 MAC 过滤；单台逐个排查            |
| 扫描中直接关弹窗会崩 / 卡死                        | 已修复：`closeEvent` 直接 `cancel+quit+wait` 并置 `_closing`，回调在销毁后早退 |
| 连接成功后状态列没显示机型名                        | 机型名来自后台 `getprop`；设备未授权或网络抖动会静默跳过，属正常      |
| 关闭弹窗后状态栏还显示扫描中                         | 不可能：`_stop_scan`+`wait(2000)` 强制退出；如有此情况检查 popup 是否被挡住 |

---

## 十、与其他子系统的关系

- **不读写设备**——只是发现可连的 IP，扫出来的设备还没连上之前，主窗口设备下拉框不会出现它们。
- **连接成功后**才会触发主窗口 `adb devices` 刷新（`_do_connect` 内部会调）。
- **不写文件、不写日志**——纯网络探测 + UI 状态。
- **不影响 Monkey、logcat、性能监控等其他模块**——完全独立的功能入口。
