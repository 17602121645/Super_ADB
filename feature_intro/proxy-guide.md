# 代理（Proxy）— 功能介绍

> 适用版本：Super_ADB 主窗口 → 系统操作 → 「设置代理 / 取消代理」按钮（**截图里红框那两个**）
> 代码文件：极简实现分散在 2 个文件，共约 **30 行**
> 入口：`main_window.btnSetProxy.clicked → set_proxy()` / `btnClearProxy.clicked → clear_proxy()`
> 异步模型：`QThreadPool(max=6) + CmdWorker`，结果回流到主窗口「输出」日志区

---

## 1. 功能概览

一键给当前设备**设置 HTTP 代理**或**取消代理**——典型场景：**手机走 PC 代理抓包**（Charles / Fiddler / mitmproxy / Burp）。

设计上完全「**零弹窗**」——点了直接生效，不问 IP 不问端口，结果回写主窗口日志区。

| 按钮 | 动作 | 等价 ADB |
|---|---|---|
| **设置代理** | 写入 `本机 IP:8888` + 立即回读验证 | `adb shell settings put global http_proxy <host>:8888`<br>`adb shell settings get global http_proxy` |
| **取消代理** | 写入 `:0`（Android 直连约定值）+ 立即回读验证 | `adb shell settings put global http_proxy :0`<br>`adb shell settings get global http_proxy` |

> 这两份功能**默认端口 8888 写死**——见「边界限制」第 2 条。

### 截图里的「输出」区日志（验证执行过程）

```
[2026-08-08 03:56:21]
执行命令: adb -s emulator-5554 shell "settings put global http_proxy 0"

[2026-08-08 03:56:21]
执行命令: adb -s emulator-5554 shell "settings get global http_proxy"
```

实际日志比这多，但核心 4 行（`put` 命令 + `get` 验证 + `get` 结果）能看到。

---

## 2. 入口与触发

主窗口左边栏 → **系统操作** → 两个紧贴着的按钮（截图里红框框出来的）：

```
┌─ 系统操作 ────────────────────────────────────┐
│  [设置代理] [取消代理] [设备重启] [system 读写]│  ← 第 1 行
│  [获取设备信息] [运行中列表] [第三方包] [系统包]│  ← 第 2 行
│  [设备性能监控] [输入文本] [界面包获取] [所有包]│  ← 第 3 行
└──────────────────────────────────────────────┘
```

注意顺序：「设置代理 / 取消代理」故意挨着——一开一关的语义对称，方便记忆。

绑定关系（`Super_ADB_Main.py:201-202`）：

```python
self.btnSetProxy.clicked.connect(self.set_proxy)
self.btnClearProxy.clicked.connect(self.clear_proxy)
```

---

## 3. 工作流（5 步拆解）

```
[1] 点击按钮 ──> set_proxy() / clear_proxy()
       │
       ▼
[2] _ensure_serial() ──> 取当前设备下拉框的 serial
       │                 （没选设备？打印「请先选择或连接一个设备」并 return）
       ▼
[3] set_proxy: host = _get_local_ip()        (调 socket.gethostbyname(hostname))
    拼: f'{host}:8888'                        (端口写死 8888)
       │
       ▼
[4] _run_async(self.adb.set_proxy, serial, '192.168.x.x:8888')
    │
    │   「线程池里」：
    │   ├─ adb_utils.set_proxy() 跑：
    │   │   adb -s <serial> shell "settings put global http_proxy 192.168.x.x:8888"
    │   │   adb -s <serial> shell "settings get global http_proxy"
    │   └─ 返回字符串（设备上的实际值）
    │
    ▼
[5] log() 跨线程把结果显示到主窗口「输出」区
    （先 output.clear() 清掉旧日志，再 append 时间戳 + 执行命令 + 结果）
```

`clear_proxy` 路径完全一样，唯一区别是命令参数固定 `:0`。

---

## 4. 底层实现细节

### 4.1 ADB 命令封装层（`adb_utils.py:424-430`）

```python
def set_proxy(self, serial, host_port):
    self.run_shell(serial, f'settings put global http_proxy {host_port}', timeout=5)
    return self.run_shell(serial, 'settings get global http_proxy', timeout=5).strip()

def clear_proxy(self, serial):
    self.run_shell(serial, 'settings put global http_proxy :0', timeout=5)
    return self.run_shell(serial, 'settings get global http_proxy', timeout=5).strip()
```

**三连设计**：
1. **写命令**：`settings put global http_proxy <host_port>`（5s 超时）
2. **读命令**：`settings get global http_proxy`（5s 超时，立刻验真伪）
3. **返回字符串**：让上层能在日志区看到设备实际生效的值

注意 **`:0`** 这个**特殊值**——Android 系统的「不走任何代理」约定值（不是 `none`，也不是空串）。其它非空值才会真正走代理。

### 4.2 本机 IP 获取（`Super_ADB_Main.py:987-992`）

```python
@staticmethod
def _get_local_ip():
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except Exception:
        return '127.0.0.1'
```

- `socket.gethostname()`：拿本地主机名（DNS 设置里的 `计算机名`）
- `socket.gethostbyname()`：解析为 IPv4
- **失败兜底**：`127.0.0.1`（设备指向本机 PC，对于「PC 端跑代理软件」的场景对一半错一半——见边界限制第 3 条）

> **不选网卡**：不管你连了 Wi-Fi 还是 USB tethering 还是 VPN，都返回系统认为的「主机 IP」。Windows 的 `socket.gethostbyname()` 一般会返回**最后激活的 IPv4**，所以多数场景能用，但**多网卡 / VPN 环境可能不准**。

### 4.3 主方法（`Super_ADB_Main.py:402-413`）

```python
def set_proxy(self):
    serial = self._ensure_serial()
    if not serial:
        return
    host = self._get_local_ip()
    self._run_async(self.adb.set_proxy, serial, f'{host}:8888')

def clear_proxy(self):
    serial = self._ensure_serial()
    if not serial:
        return
    self._run_async(self.adb.clear_proxy, serial)
```

模板一致性：所有系统操作按钮都遵循这个 3 行模板——确保 serial → 异步执行。

### 4.4 异步执行架构

复用主窗口已有的 `_run_async` 抽象（`Super_ADB_Main.py:310-318`）：

```python
def _run_async(self, func, *args, **kwargs):
    """将函数放入线程池后台执行，结果通过 log / set_status 展示。"""
    self.output.clear()
    worker = CmdWorker(func, *args, **kwargs)
    worker.signals.result.connect(lambda r: self.log(str(r)))
    worker.signals.error.connect(lambda e: self.log(f'错误: {e}'))
    worker.signals.finished.connect(lambda: self._drop_worker(worker))
    self._live_workers.append(worker)
    self.pool.start(worker)
```

关键点：
1. **`output.clear()` 先清空「输出」日志区**——避免历史日志干扰
2. **`CmdWorker(QRunnable)`** 走主窗口的 `QThreadPool(max=6)`
3. **跨线程回 UI**：`worker.signals.result` 触发 `log()`，log 内部用 `QMetaObject.invokeMethod(..., Qt.QueuedConnection, ...)` 把 `append` 派发到主线程

完整生命周期：**点击 → 线程池启动 → 后台跑 2 条 ADB → 信号回调 → UI 更新 → `_drop_worker` 收尾**。

---

## 5. 工作流时序图

```
用户              MainWindow           QThreadPool        AdbHelper           Android 设备
 │  点击 [设置代理]  │                      │                   │                   │
 ├─────────────────>│                      │                   │                   │
 │                  │ _ensure_serial()     │                   │                   │
 │                  │ _get_local_ip() → "192.168.x.x"            │                   │
 │                  │ _run_async(set_proxy, "192.168.x.x:8888")  │                   │
 │                  │ output.clear()       │                   │                   │
 │                  │ pool.start(worker)   │                   │                   │
 │                  ├─────────────────────>│                   │                   │
 │                  │                      │ run_shell(put)    │                   │
 │                  │                      ├──────────────────>│ adb shell "settings put global http_proxy 192.168.x.x:8888"
 │                  │                      │                   ├──────────────────>
 │                  │                      │                   │<──────────────────┤ OK
 │                  │                      │ run_shell(get)    │                   │
 │                  │                      ├──────────────────>│ adb shell "settings get global http_proxy"
 │                  │                      │                   ├──────────────────>
 │                  │                      │                   │<──────────────────┤ "192.168.x.x:8888"
 │                  │                      │   ←───────────── │                   │
 │                  │ log(result + cmd×N) via QueuedConnection │                   │
 │                  │<─────────────────────┤                   │                   │
 │ <显示「输出」区日志>│                      │                   │                   │
```

`clear_proxy` 时序完全一样，唯一区别是命令是 `settings put global http_proxy :0`。

---

## 6. 典型使用场景

### 场景 1：Charles 抓 HTTPS 包（最常见）

1. PC 装 Charles，监听 `*:8888`
2. PC 防火墙允许 8888 入站
3. 主窗口 → 选择手机 → 点「**设置代理**」
4. 输出区看到「执行命令: ... get global http_proxy → `192.168.x.x:8888`」
5. 手机打开任何 App → Charles 收到请求
6. 抓完点「**取消代理**」恢复直连

### 场景 2：mitmproxy 抓包

```bash
# PC 终端
mitmproxy --listen-port 8888
```

然后 Super_ADB → 点「设置代理」，手机所有 HTTP/HTTPS 走 mitmproxy（要装 CA 证书到手机，见各工具的官方文档）。

### 场景 3：Fiddler 抓包

Fiddler 默认监听 8888（可以去 Tools > Options 改），但 Super_ADB **写死 8888**——所以要么 Fiddler 用 8888，要么改 Fiddler 端口为 8888（最方便）。

### 场景 4：自动化测试，让测试 App 直连 PC

设置代理后，**只有 HTTP 流量**走 PC；HTTPS 不走（除非装 CA 证书）。对于自动化测试脚本 `http://192.168.x.x:8080/api/...` 这种，可以通过 PC 直接拦截 + mock。

---

## 7. 边界与限制

> 这是这个模块**最值得读的一节**——因为它**真的小**，所以这部分的「要做没做」更值得讨论。

1. **端口硬编码 8888**：主窗口没暴露输入框，要改端口只能改源码 `Super_ADB_Main.py:407` 的 `f'{host}:8888'`。常见替代：Charles 默认 8888 / Burp 默认 8080 / Fiddler 默认 8888。
2. **不写入 HTTPS 代理**：`http_proxy` 系统设置只对 HTTP 生效。HTTPS 仍然直连——这是 Android 的设计，不是 Bug。要抓 HTTPS 得装代理工具的 CA 证书到手机的 `/system/etc/security/cacerts/`（需要 root）。
3. **`_get_local_ip()` 在多网卡环境可能不准**：
    - 如果 PC 同时连着 VPN / 多 Wi-Fi / USB tethering，`gethostbyname()` 可能返回非预期网卡
    - 兜底 `127.0.0.1` 是「设备指向本机 PC」——对模拟器 `127.0.0.1` 通（因为 ADB 转发），但真机就会失败
    - **变通**：用 `ipconfig` 查实际 IP 后手填（**目前只支持手动编辑源码**）
4. **不做持久化**：Android 的 `http_proxy` 设置在 **`重启后失效**`（除非用 `persist.db.global.http_proxy` 这种特殊 key，或者用 Magisk 模块）。所以这个功能**只适合临时调试**。
5. **不显示 Wi-Fi 名 / 当前代理状态**：点击前你**看不见**当前手机代理是什么——得自己敲 `adb shell settings get global http_proxy`。
6. **不区分以太网 / Wi-Fi / 移动网络**：所有网络都共用 `http_proxy` 一条全局值。
7. **取消代理 = `:0`，不是空字符串**：跟 Android 系统约定走，不用 `none` / `null`。
8. **错误处理薄弱**：ADB 失败（如设备掉线）只在「输出」区打 `错误: ...`，不弹窗、不重试、不提示重连。

---

## 8. 与其它子系统对照

| 维度 | 代理（本模块） | 输入文本 | 设备性能监控 |
|---|---|---|---|
| 弹窗 | ❌ 无 | ✅ QDialog | ✅ QWidget 独立窗口 |
| 异步 | ✅ `_run_async` | ✅ 但更复杂 | ✅ `threading.Thread` 后台 |
| 端口/路径用户可改 | ❌ 写死 | ✅ 文本框 | ❌ |
| 实时图表 | ❌ | ❌ | ✅ 双折线图 |
| 错误弹窗 | ❌ | ✅ | ✅ |
| 自动获取本地信息 | ✅ 本机 IP | ✅ ADBKeyBoard 检测 | ✅ CPU 格式自动适配 |
| 适配 Android 版本差异 | 弱（`MemAvailable` 这种都不涉及） | 中（ADBKeyBoard） | 强（8 种 CPU 格式兜底） |

**对照结论**：代理是项目里**最轻量的功能**——也是项目里少有的**「真的什么都没做」**的功能。复杂度的天花板被它的需求（无 UI）卡死了。

---

## 9. 代码结构（一文件两方法）

```
Super_ADB_Main/
├── adb_utils.py               (ADB 命令封装层)
│   ├── AdbHelper
│   │   ├── set_proxy(serial, host_port)       ←── 写 + 读
│   │   └── clear_proxy(serial)                ←── 写 + 读
│   └── ...
└── Super_ADB_Main.py          (主窗口)
    ├── set_proxy()                            ←── 主方法
    ├── clear_proxy()                          ←── 主方法
    ├── _get_local_ip()                        ←── 拿主机 IP
    ├── _run_async(func, *args)                ←── 异步执行抽象
    └── ...
```

总有效代码约 **30 行**，是项目里**体量第二小的功能**（最小的是「断开连接」、「清理数据」这种 3 行主方法）。

---

## 10. 线程模型

```
┌─────────────────────────────────────────────┐
│ 主线程                                        │
│   ├─ UI 事件循环                               │
│   │   └─ btnSetProxy.clicked → set_proxy()    │
│   ├─ self.output (QTextEdit 日志区)            │
│   └─ self.pool (QThreadPool max=6)             │
└──────────────┬───────────────────────────────┘
               │ CmdWorker
               ▼
┌─────────────────────────────────────────────┐
│ Worker 线程（池中）                           │
│   ├─ AdbHelper.set_proxy(serial, host_port)   │
│   │   ├─ adb shell ... (5s 超时)              │
│   │   ├─ adb shell ... (5s 超时)              │
│   │   └─ return "192.168.x.x:8888"           │
│   └─ signals.result → main.log()             │
└─────────────────────────────────────────────┘
```

完全复用主窗口的**通用异步架构**——代理这个功能**没有任何自己专属的线程逻辑**。

---

## 11. 5 个测试用例

### 11.1 在模拟器上设代理（截图复刻）

**前置**：设备 `emulator-5554` 在线
**操作**：点「设置代理」
**预期**：
1. 输出区先 clear，再依次显示：
    - `执行命令: adb -s emulator-5554 shell "settings put global http_proxy <host>:8888"`
    - `执行命令: adb -s emulator-5554 shell "settings get global http_proxy"`
    - `<host>:8888`
2. 模拟器内 `adb shell curl -v http://example.com` 会走 PC 代理（如果有代理软件）

### 11.2 取消代理

**操作**：点「取消代理」
**预期**：输出区看到 `settings get global http_proxy` 返回 `:0`（不是空，是 `:0`）

### 11.3 无设备时点击

**前置**：主窗口没选设备
**操作**：点任意代理按钮
**预期**：输出区追加 `请先选择或连接一个设备`，按钮无反应（不弹窗不报错）

### 11.4 多网卡环境（边界）

**前置**：PC 同时连 Wi-Fi + VPN
**操作**：点「设置代理」
**可能问题**：`_get_local_ip()` 返回的 IP 不是手机能路由到的网卡
**变通**：手填源码中的 `f'{host}:8888'`（或加 UI 输入框）

### 11.5 网络断开后取消

**前置**：手机已设置 `192.168.1.5:8888`，但 PC 关了
**操作**：点「取消代理`
**预期**：ADB 命令成功 → 设备下次重连 Wi-Fi 即直连；不需要重启

---

## 12. 10 个未来扩展点

按「价值 / 改动量」排序：

1. **🔥 端口可输入**：在按钮旁边加一个 QSpinBox（默认 8888），改一行 main 方法即可（**改动量最小，价值最高**）
2. **🔥 主机 IP 可下拉选择**：枚举 PC 所有网卡 IPv4，下拉可选（解决边界限制 #3）
3. **🔥 持久化**：改用 `settings put global persist.db.http_proxy`（部分 ROM 支持）
4. 显示当前代理状态：点击按钮前先 read 一次，弹个状态条
5. 配合 `chrome://inspect` 一键开启：手机 Chrome 调试 + PC 代理组合
6. 写 HTTPS 代理（需要新加一组命令）
7. 包级别 PAC 脚本 URL 设置
8. Wi-Fi AP 检测（PC 是热点时自动用 `192.168.x.1`）
9. 添加代理白名单：`*.example.com DIRECT`
10. 失败时弹错误窗：现在只在日志区打印错误

**最小改动（实现 1 + 2）只需 20 行代码 + 一个 .ui 字段修改**——下次想加就顺手加了。

---

## 13. 一句话总结

**「代理」是项目里最不起眼的功能——因为它没弹窗、没图表、没日志级别、没报告导出。但正是这种「零 UI」设计，让 Charles / Fiddler 抓包的工作流从「7 步命令行」压缩到「1 次点击」。**

---

## 附录 A：与 Android `settings` 命令的对照

`http_proxy` 是 Android 全局键，**最早从 2.x 时代就有**。相关命令：

| 子命令 | 用途 |
|---|---|
| `settings put global http_proxy <host>:<port>` | 设置（**唯一推荐做法**） |
| `settings put global http_proxy :0` | 取消代理（**Android 约定值**：`:0` 不是空） |
| `settings get global http_proxy` | 读取当前值 |
| `settings delete global http_proxy` | 删除键（**不推荐**——可能恢复成系统默认） |
| `settings list global \| grep http_proxy` | 查看相关键 |

**为什么用 `global` 而不是 `system` / `secure`**：因为 `http_proxy` 是**运行时网络配置**（用户改 Wi-Fi 时可能改），归 `global` namespace 管；`secure` 是 ROM 锁定的，`system` 是系统应用改的。

## 附录 B：常见排错 FAQ

**Q：设置了代理，但 App 没走 PC？**
- 检查 PC 防火墙是否允许 8888 入站（Windows 防火墙首次会弹窗）
- 检查 PC 代理软件是否真的启用了（Charles / Fiddler 默认监听 `127.0.0.1`，不是 `0.0.0.0`）
- 确认手机 Wi-Fi 是「PC 所在的同一局域网」（不是 4G，不是 guest Wi-Fi）

**Q：端口不对，代理失败？**
- Super_ADB 写死 8888，要改得改源码（或加输入框，见扩展点 #1）
- 部分代理工具监听 8080（Burp）/ 9090（mitmproxy 别名），得手动同步

**Q：取消代理后，某些 App 还是走代理？**
- HTTP 走系统设置 → 立即生效
- HTTPS 不走系统设置 → 必须装 CA 证书到手机，「取消」对它无效（要手动从 `/system/etc/security/cacerts/` 移除）

**Q：重启手机后代理还在吗？**
- **多数 ROM 不持久化**——`http_proxy` 重启后清空
- 部分定制 ROM（如 MIUI）会「记住」最后一次设置
- 推荐**每次抓包重新点一次按钮**

**Q：模拟器能用吗？**
- 模拟器走宿主机的 NAT，`127.0.0.1` 会指向 PC
- 如果 PC 装在 `192.168.x.x`，模拟器内 `curl 127.0.0.1:8888` 直接通
- 如果「模拟器 + Charles」抓包，**不必设置代理**——直接监听 `127.0.0.1:8888` 即可（但 App 得配 `10.0.2.2` 之类的特殊地址）
