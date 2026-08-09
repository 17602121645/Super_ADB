# 系统操作栏 — 功能介绍

> 适用版本：Super_ADB 2026-08-08+
> 代码文件：`Super_ADB_Main/Super_ADB_Main.py`
> 关联代码：`Super_ADB_Main/adb_utils.py`
> 配套截图：`feature_intro/system-ops.png`

---

## 1. 功能概览

「系统操作」是 Super_ADB 主窗口左上角的**高频快捷功能区**，把日常调试里最常用的 ADB 系统级命令收敛成一排按钮。不需要记命令、不需要切终端，选完设备直接点。

截图里展示的能力矩阵：

| 行 | 按钮 | 作用 | 等价 ADB / 命令 |
|---|---|---|---|
| 顶部输入条 | **PC本机IP** + `tcpdump 抓包` 按钮 | 自动填充 / 手动编辑 PC 代理地址；右侧按钮打开 tcpdump 抓包窗口 | `socket.gethostbyname()` + `:8888`；`tcpdump -i <iface> -s 0 -w -` |
| 第 1 行 | **设置代理** | 把设备全局 HTTP 代理指向 PC | `settings put global http_proxy <ip>:<port>` |
| 第 1 行 | **取消代理** | 清空设备 HTTP 代理 | `settings put global http_proxy :0` |
| 第 1 行 | **设备重启** | 重启当前设备 | `reboot` |
| 第 1 行 | **system 读写** | adb root + remount + 重新挂 system 为可写 | `adb root` → `adb remount` → `mount -o rw,remount /system` |
| 第 2 行 | **获取设备信息** | 批量采集硬件、系统、网络、OAID 等 | 见 §4.1 |
| 第 2 行 | **运行中列表** | 列出设备上正在运行的包 | `pm list packages -e` |
| 第 2 行 | **第三方包** | 列出用户安装的三方应用 | `pm list packages -3 -f` |
| 第 2 行 | **系统包** | 列出系统预装应用 | `pm list packages -s -f` |
| 第 3 行 | **设备性能监控** | 打开独立窗口，实时 CPU / 内存 / GPU 曲线 | `device_perf_monitor.py` |
| 第 3 行 | **输入文本** | 弹窗向设备焦点输入框发送文本（支持中文） | `input text` / 剪贴板 / ADBKeyBoard |
| 第 3 行 | **界面包获取** | 获取当前前台窗口包名/Activity | `dumpsys window \| grep mCurrentFocus` |
| 第 3 行 | **所有包** | 列出设备全部包名 | `pm list packages -f` |

---

## 2. 入口与布局

```
┌─ 系统操作 ───────────────────────────────────────────┐
│  PC本机IP [192.168.1.50:8888              ×]        │  ← 新增代理地址输入条
│  [设置代理] [取消代理] [设备重启] [system 读写]      │  ← 第 1 行（红框为设置/取消代理）
│  [获取设备信息] [运行中列表] [第三方包] [系统包]     │  ← 第 2 行
│  [设备性能监控] [输入文本] [界面包获取] [所有包]     │  ← 第 3 行
└─────────────────────────────────────────────────────┘
```

- **位置**：主窗口左侧上部，设备下拉框下方。
- **设备前置条件**：所有按钮都会先调用 `_ensure_serial()`，未选择设备时会在输出区提示 `请先选择或连接一个设备`。
- **结果输出**：命令返回统一写到主窗口底部「输出」区；独立窗口类（设备性能监控、输入文本）则打开自己的子窗口。

![系统操作栏截图](system-ops.png)

---

## 3. 代理功能详解（截图红框重点）

### 3.1 为什么需要 PC 本机 IP 输入框

之前的「设置代理」直接写死 `本机IP:8888`， Charles / Fiddler 这类工具虽然默认监听 8888，但存在两个问题：

1. **多网卡环境 IP 不对**：`_get_local_ip()` 返回的是系统解析的主机名 IP，不一定和手机处于同一局域网。
2. **端口可能不是 8888**：Burp 默认 8080、mitmproxy 可能是 8080/9090、用户自定义代理端口等。

新增输入框后，**启动自动填一个默认值**，用户可以直接改，也可以不改。

### 3.2 启动时自动填充

```python
def _init_pc_ip_input(self):
    ...
    self.pcIpInput.setText(f'{self._get_local_ip()}:8888')
```

- 调用 `socket.gethostbyname(socket.gethostname())` 取本机 IPv4
- 失败兜底 `127.0.0.1`
- 拼上默认端口 `:8888`
- 输入框带清空按钮（×），可一键重置

### 3.3 设置代理流程

```python
def set_proxy(self):
    serial = self._ensure_serial()
    if not serial:
        return
    host_port = self.pcIpInput.text().strip()
    if not host_port:
        self.log('请先在「PC本机IP」输入框填写 本机IP:端口')
        return
    self._run_async(self.adb.set_proxy, serial, host_port)
```

`adb_utils.set_proxy` 底层：

```python
def set_proxy(self, serial, host_port):
    self.run_shell(serial, f'settings put global http_proxy {host_port}', timeout=5)
    return self.run_shell(serial, 'settings get global http_proxy', timeout=5).strip()
```

**写 + 读两步验证**：先 put，再 get 回读，确保设备上真的生效。

### 3.4 取消代理

```python
def clear_proxy(self):
    serial = self._ensure_serial()
    if not serial:
        return
    self._run_async(self.adb.clear_proxy, serial)
```

底层写入 `:0`——这是 Android 系统约定的「无代理」值，不是空字符串、不是 `none`。

### 3.5 典型抓包工作流

```
1. PC 打开 Charles / Fiddler，确认监听端口
2. 把端口号填到「PC本机IP」框（如 192.168.1.50:8888）
3. 确认手机和 PC 在同一 Wi-Fi
4. 点「设置代理」
5. 输出区看到 get global http_proxy 返回刚才填的地址 → 成功
6. 手机打开目标 App，PC 代理软件收到流量
7. 抓完点「取消代理」恢复直连
```

### 3.6  tcpdump 抓包（PC本机IP 输入框右侧按钮）

在「PC本机IP」输入框**右边**新增了一个 **`tcpdump 抓包`** 按钮，点开后弹出一个独立抓包窗口，把设备的网络包实时存成 `.pcap`，方便用 Wireshark 分析。

**窗口能力：**

| 控件 | 作用 |
|---|---|
| 网卡 (iface) | 默认 `wlan0`，可改 `rmnet0` / `eth0` 等 |
| 协议下拉 | `tcp` / `udp` / `icmp`（可选，留空抓全部） |
| 过滤表达式 | 自定义 BPF，如 `port 443`、`host 1.2.3.4` |
| 开始 / 停止 | 启停 `adb shell tcpdump -i <iface> -s 0 -w - [flt]` |
| 实时字节数 | 显示已抓取字节，抓包中实时刷新 |

**落盘位置：** 桌面 `Super_ADB/tcpdump_<serial>_<时间戳>.pcap`（二进制流直接写盘，不经过文本中转，Wireshark 可直接打开）。

**实现要点：**
- 走 `subprocess.Popen` + 后台读线程，读到的原始字节块（`wb` 模式）直接 `write` 到 `.pcap`，保证 pcap 格式完整可解析。
- 复用主窗口串口（`_ensure_serial()`），弹窗为**复用窗口**模式（重复点击不重复创建）。
- 未 root 设备一般没有 `tcpdump` 二进制，窗口会立刻报「tcpdump not found」，属预期（需设备内置或 push 一个 tcpdump）。

---

## 4. 其它按钮能力简介

### 4.1 获取设备信息

一次性批量采集 20+ 项信息：

- Android 版本、SDK、安全补丁
- 厂商 / 品牌 / 型号 / 设备代号 / CPU ABI
- CPU 型号、SoC、GPU、EGL
- 屏幕分辨率、密度
- MAC 地址（多路径回退，过滤 02:00:00:00:00:00 占位符）
- OAID/AAID（小米、华为、OPPO、vivo、Google 等多厂商候选提取）
- 内存总量 / 可用内存
- ADB serialno

实现亮点：**11 次独立 shell 调用合并为 1 次批量脚本**，通过 base64 编码执行，彻底绕过 Windows cmd.exe 的嵌套引号陷阱，原本 5s+ 的延迟降到 1s 内。

### 4.2 运行中列表 / 第三方包 / 系统包 / 所有包

都走 `pm list packages` 系列命令：

| 按钮 | 参数 | 输出 |
|---|---|---|
| 运行中列表 | `-e` | 启用的包 |
| 第三方包 | `-3 -f` | 用户安装的三方包，带 APK 路径 |
| 系统包 | `-s -f` | 系统预装包，带 APK 路径 |
| 所有包 | `-f` | 全部包，带 APK 路径 |

输出直接回显到主窗口「输出」区，可配合搜索框或「复制输出」使用。

### 4.3 界面包获取

读取 `dumpsys window | grep mCurrentFocus`，提取当前焦点窗口信息，快速知道当前前台是哪家 App、哪个 Activity。适用于：

- 定位当前页面包名
- 确认跳转是否成功
- 写自动化测试时找 Activity 名

### 4.4 设备重启

直接 `adb shell reboot`。注意：

- 命令发出去后设备立即开始重启，不会有「已完成」回显
- 输出区只显示 `已发送重启命令`
- 重启期间设备会短暂离线

### 4.5 system 读写

执行三步：

1. `adb root`
2. `adb remount`
3. `mount -o rw,remount /system`

用于需要修改 `/system` 分区内容的场景（如 push 证书、替换系统文件）。需要设备已 root 或支持 adb root。

### 4.6 设备性能监控

打开独立窗口 `DevicePerfMonitor`，2 秒定时采样 + 后台线程读 ADB，实时绘制 CPU、内存、GPU 滚动曲线。详细文档见 `device-perf-monitor-guide.md`。

### 4.7 输入文本

弹窗向设备焦点输入框发送文本，支持三层策略：纯 ASCII 走 `input text`、非 ASCII 走 Win32 剪贴板（模拟器）、再失败走 ADBKeyBoard。详细文档见 `input-text-guide.md`。

---

## 5. 代码结构

```
Super_ADB_Main/
├── Super_ADB_Main.py
│   ├── 系统操作按钮绑定区（__init__ 中 clicked.connect）
│   │   ├── btnSetProxy → set_proxy()
│   │   ├── btnClearProxy → clear_proxy()
│   │   ├── btnReboot → reboot_device()
│   │   ├── btnDeviceInfo → show_device_info()
│   │   ├── btnSystemRoot → system_root()
│   │   ├── btnInputText → open_input_text_dialog()
│   │   ├── btnDpm → open_perf_monitor()
│   │   ├── btnRunningApps → show_running_apps()
│   │   ├── btnWindowApp → show_window_app()
│   │   ├── btnApps3 → list_apps_3()
│   │   ├── btnAppsS → list_apps_s()
│   │   └── btnAppsAll → list_apps_all()
│   ├── _init_pc_ip_input()        ← PC本机IP 输入框 / tcpdump 按钮（定义在 ui/Super_ADB.ui 的 sysGroup 顶部，setupUi 创建）
│   ├── _get_local_ip()            ← 获取本机 IPv4
│   ├── set_proxy() / clear_proxy()
│   └── _run_async()               ← 统一异步执行抽象
│
└── adb_utils.py
    ├── set_proxy(serial, host_port)
    ├── clear_proxy(serial)
    ├── reboot(serial)
    ├── root_and_remount(serial)
    ├── get_device_info(serial)
    ├── get_device_info_dict(serial)
    ├── get_app_list(serial, flag)
    ├── get_running_apps(serial)
    └── get_window_app(serial)
```

---

## 6. 线程模型

所有系统操作按钮都复用主窗口的 `_run_async` 通用异步架构：

```
主线程 UI
  ├─ 用户点击按钮
  ├─ _ensure_serial() 检查设备
  ├─ output.clear()（部分按钮）
  ├─ pool.start(CmdWorker)
  │      ▼
  │   QThreadPool Worker 线程
  │      ├─ 执行 adb_utils 方法
  │      ├─ 运行 adb shell / adb root / adb remount 等
  │      └─ signals.result → 主线程 log()
  │      ▲
  └─ 主线程收到信号，append 到输出区
```

- 线程池上限 `maxThreadCount = 6`
- 独立窗口（设备性能监控、输入文本）在按钮点击时直接构造并 `show()`，不占用线程池
- 所有 ADB 结果都通过 Qt Signal 跨线程回到主线程更新 UI

---

## 7. 边界与限制

### 7.1 代理相关

1. **仅设置 HTTP 代理**：`http_proxy` 对 HTTPS 不生效，抓 HTTPS 需要额外在设备安装代理工具 CA 证书。
2. **代理不持久化**：Android 多数 ROM 重启后 `http_proxy` 会清空，每次调试需重新设置。
3. **`_get_local_ip()` 多网卡可能不准**：VPN / 多 Wi-Fi / USB 网络共享时，返回的 IP 可能不是手机可达的网段。
4. **端口由用户负责**：改成非 8888 端口后，PC 代理软件必须监听对应端口。
5. **取消代理用 `:0`**：这是 Android 约定值，不是空串。

### 7.2 系统操作通用

1. **所有按钮依赖已连接设备**：未选设备时统一提示，不会弹窗。
2. **system 读写需要 root**：未 root 设备执行会失败，错误显示在输出区。
3. **设备重启没有二次确认**：点了立刻重启。
4. **大应用列表可能耗时**：`pm list packages -f` 在低端设备或包很多时需要几秒。
5. **界面包获取依赖系统窗口服务**：部分悬浮窗、锁屏、分屏场景下 `mCurrentFocus` 可能不唯一。

---

## 8. 典型用例

### 用例 1：Charles 抓包

1. PC 打开 Charles，确认 Proxy Settings → Proxies → HTTP Proxy 监听 `8888`
2. Super_ADB 选择设备
3. 修改「PC本机IP」为 PC 在 Wi-Fi 下的实际 IP（如 `192.168.1.50:8888`）
4. 点「设置代理」
5. 输出区看到 `192.168.1.50:8888`
6. 手机操作目标 App，Charles 收到请求
7. 结束点「取消代理」

### 用例 2：快速查看设备信息

1. 选择设备
2. 点「获取设备信息」
3. 输出区展示 Android 版本、型号、CPU、GPU、RAM、MAC、OAID 等

### 用例 3：定位当前前台 Activity

1. 在手机上打开想分析的页面
2. 点「界面包获取」
3. 输出区显示 `com.example.app/com.example.app.MainActivity`

### 用例 4：列出系统应用排查预装

1. 点「系统包」
2. 输出区列出所有系统包及 APK 路径
3. 配合「复制输出」粘贴到文本编辑器中搜索

---

## 9. 未来扩展点

1. **🔥 代理 IP 多网卡下拉**：枚举本机所有 IPv4，下拉选择，解决 `_get_local_ip()` 不准问题。
2. **🔥 显示当前代理状态**：点击「设置代理」前先 get 一次，输出区提示当前是否已设置代理。
3. **🔥 代理失败重试 / 错误弹窗**：端口不可达或设置失败时给出明确提示。
4. **HTTPS 代理 / PAC 脚本支持**：补充 `global_proxy_pac` 或 `global_https_proxy` 设置。
5. **设备信息导出 JSON**：把 `get_device_info_dict` 结果保存为文件，方便测试报告使用。
6. **运行中列表增加 PID / 内存**：`dumpsys activity processes` 扩展信息。
7. **第三方包 / 系统包增加版本号**：解析 `dumpsys package <pkg>`。
8. **界面包获取增加 activity 堆栈**：`dumpsys activity top` 输出当前任务栈。
9. **一键打开 Wi-Fi 设置**：配合代理使用，快速切到手机 Wi-Fi 页面。
10. **常用代理配置收藏**：像日志过滤收藏一样，保存几组常用 `IP:端口`。

---

## 10. 一句话总结

**「系统操作」栏是 Super_ADB 的「工具腰带」——把代理、重启、root、设备信息、包列表、性能监控等最常用 ADB 命令，从「打开终端 → 查命令 → 敲命令 → 看回显」压缩到「选设备 → 点按钮 → 看输出」。新增的 PC本机IP 输入框让代理设置从「写死 8888」变成「可编辑、可校验、可复用」。**

---

## 附录 A：Android 代理 settings 键对照

| 命令 | 作用 |
|---|---|
| `settings put global http_proxy <host>:<port>` | 设置 HTTP 代理（唯一推荐方式） |
| `settings put global http_proxy :0` | 取消代理（Android 约定值） |
| `settings get global http_proxy` | 读取当前代理 |
| `settings delete global http_proxy` | 删除键（不推荐，可能恢复系统默认值） |

---

## 附录 B：常见问题

**Q：设置了代理但 App 不走 PC？**
- 检查 PC 防火墙是否放行对应端口
- 检查代理软件是否监听 `0.0.0.0`（有些默认只监听 `127.0.0.1`）
- 确认手机和 PC 在同一局域网
- 检查「PC本机IP」框里的 IP 是否真的是 PC 在该网络的 IP

**Q：取消代理后，HTTPS 还是被代理？**
- HTTPS 不走 `http_proxy`，如果之前装了 CA 证书，需要手动移除或关闭代理软件

**Q：获取设备信息很慢？**
- 首次执行需要建立 ADB shell 连接，后续会变快
- 如果设备响应慢，可能是 OAID content provider 查询超时

**Q：system 读写失败？**
- 设备必须支持 `adb root` 或已 root
- 部分模拟器默认就是可写的，不需要 remount

---

_文档版本：v2 · 与 `Super_ADB_Main.py` 当前代码一致_
_最近更新：2026-08-08_

---

## 本版新增（2026-08-08）：界面控件 .ui 同步

- **PC本机IP 输入框 + tcpdump 抓包按钮**：从「代码动态 new + 整体下移 grid 行」改为**定义在 `ui/Super_ADB.ui` 的 `sysGroup` 顶部**（`pcIpLabel` / `pcIpInput` / `btnTcpdump`，外层 `pcIpCell` 容器 + `pcIpLayout` 水平布局），由 `setupUi` 创建。`Super_ADB_Main.py` 的 `_init_pc_ip_input()` 不再 new 控件，仅补设 placeholder / tooltip / 默认值 / 信号连接；并重新生成 `Super_ADB_Main/Super_ADB.py`（pyside6-uic）。
- **范围说明**：项目仅 `ui/Super_ADB.ui`（主窗口）一个 .ui 文件；Monkey 窗口、设备性能窗口、日志窗口均为纯代码构建、无独立 .ui，其动态控件（Monkey 版本标签、设备性能 SpinBox/导出、日志高亮框）维持代码创建，未强制回写 .ui（采用「仅主窗口 .ui」方案，已与用户确认）。
