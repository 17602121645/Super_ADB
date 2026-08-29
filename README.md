# Super_ADB —— ADB 集成调试工具

> 基于 **PySide6 + 自研 ADB 协议栈** 的 Android 调试一体化工具箱，支持 Windows / macOS / Linux 三平台，深色主题（可切换 6 套主题）。

## 简介

Super_ADB 把日常 Android 调试中最常用的操作——设备连接、系统操作、应用管理、文件传输、日志抓取、性能监控、Monkey 压测、scrcpy 投屏、网络抓包——集成到一个界面里。免命令行也能完成大部分调试工作，同时保留了直接敲命令的入口。

**核心亮点**：内置自研 ADB 协议栈（TLS 认证 + sync 快速传输 + delayed_ack Burst Mode），文件传输速度远超官方 adb；无线调试支持局域网扫描 / 配对码 / 二维码三合一连接；还有一只住在主窗口里、会躲避鼠标的小猫陪你加班。

## 功能特性

| 模块 | 能力 |
|---|---|
| **自研 ADB** | TLS 认证（Android 9+）、sync 协议快速传输（64KB DATA 块 × 15 帧合并）、delayed_ack Burst Mode、USB 传输层、mDNS 自动发现、多设备管理 |
| **设备连接** | USB / 无线调试、刷新设备列表、连接 / 断开、一键重启（recovery / bootloader）、局域网扫描自动发现 |
| **无线调试** | 三合一弹窗：局域网扫描（5555+CNNX 自动发现） / 配对码连接（adb pair） / 二维码连接（mDNS 自动监听 + 扫码回填） |
| **系统操作** | 设置 / 清除代理、设备信息、剪贴板写设备、PC 本机 IP 显示、**scrcpy 投屏**（分辨率/码率/帧率/编码/渲染驱动可调） |
| **应用操作** | 启动 / 停止 / 卸载 / 清除应用、运行应用列表、应用信息、APK 安装 / 解包（元信息解析） |
| **文件管理** | 设备文件树浏览、上传 / 下载、权限操作（右键「授权 777」）、只读分区自动解锁引导、**递归搜索** |
| **日志抓取** | 多标签 logcat、关键字过滤、标签 / 进程 / 消息星标、实时流式输出 |
| **性能监控** | 设备级（CPU / 内存 / 温度 / FPS）+ 应用级（12 项图表指标、内存泄漏检测、ANR / OOM 检测）、HTML 报告导出 |
| **Monkey 压测** | 命令模板、暂停 / 继续、实时事件饼图、崩溃报告拉取、事件回放 |
| **网络抓包** | tcpdump 自动推送（arm64 / arm 双架构）、PCAP 解析、过滤器支持 |
| **便捷工具** | 命令行、JSON 工具、MD5 / 哈希校验、时间戳转换、WiFi 密码审计 |
| **桌面宠物** | 主窗口里的小猫，状态机驱动（idle/walk/run/play/sleep），自动躲避鼠标、气泡互动 |

### 自研 ADB 传输性能

| 方向 | 实现 | 速率 |
|------|------|------|
| 上传 push | 自研 ADB（sync + Burst Mode） | ~56 MB/s |
| 上传 push | 官方 adb 1.0.41 | ~21 MB/s |
| 下载 pull | 自研 ADB | ~33 MB/s |
| 下载 pull | 官方 adb | ~32 MB/s |

*测试条件：荣耀 ELZ-AN20 · Wi-Fi 无线调试 · 128MB 随机数据 · 各方向 3 轮取平均*

### 便捷工具详解

- **命令行**：打开系统 PowerShell（Windows）/ 终端（macOS, Linux）。
- **JSON 工具**：格式化 / 压缩、差异对比、YAML 互转、Schema 校验、树形视图、字典互转（JSON ↔ Python dict 字面量）。
- **哈希校验**：MD5 / SHA1 / SHA256 / SHA512 / SHA3-256 / CRC32 / PEM subject-hash 多算法校验，拖入文件即算，进度条 + 复制全部 + CSV/JSON 导出；支持注册 Windows 右键菜单「计算哈希」。
- **时间戳转换**：Unix 时间戳 ↔ 北京时间实时双向互转，自动识别秒 / 毫秒 / 微秒 / 纳秒。
- **WiFi 密码审计**：独立 CLI（`工具/WiFi密码破解.py`，multiprocessing + threading 双级并行），WPA PMKID 模式密码强度自测 + 本机已存 WiFi 密码恢复。

### 主题切换

标题栏下拉按钮可切换 6 套主题（`dark_teal` 默认 / `dark_cyan` / `dark_purple` / `dark_amber` / `dark_crimson` / `light_soft`），写入 `adb_shell_config.json`，下次启动自动加载。已打开的弹窗也会跟随主题刷新。

## 目录结构

```
Super_ADB/
├── Super_ADB_Win/          # Windows 主项目
│   ├── 对话框/              # 弹窗（TCPDump、Monkey、无线调试、文件管理等）
│   ├── 页面/                # 主窗口子页面（日志、文件管理、终端、小猫）
│   ├── 监控/                # 性能监控（设备级 + 应用级）
│   ├── 工具/                # 工具模块
│   │   ├── 自研adb/         # 自研 ADB 协议栈（TLS认证、sync传输、USB层、mDNS）
│   │   ├── ADB工具.py       # ADB 封装（AdbHelper / Adb设备操作 / AdbFileManager）
│   │   └── ...
│   ├── 外部扩展/             # scrcpy（25MB）+ tcpdump（4.2MB）
│   ├── 打包/                # PyInstaller 打包脚本
│   ├── 脚本/                # 项目全景文档生成等
│   ├── 项目启动入口/         # 主入口
│   └── 项目UI/              # .ui 布局 + 样式 + 资源
├── Super_ADB_Linux/        # Linux 适配子项目
├── Super_ADB_MAC/          # macOS 适配子项目
├── ui/                      # 共享 .ui 布局文件
└── 项目说明/                 # 项目全景文档（HTML）
```

详见 [`项目结构图.md`](项目结构图.md)；模块依赖关系见 [`依赖关系图.md`](依赖关系图.md)。

## 安装

```bash
# 需要 Python 3.13+（推荐 3.14），并确认 adb 已配置且在 PATH 中
pip install -r requirements.txt
```

项目运行仅依赖 **PySide6** 与 **Pillow** 两项核心第三方包，其余均为 Python 标准库。**Optional 依赖**：

| 包 | 用途 | 缺失影响 |
|----|------|---------|
| `segno` | 无线调试配对二维码生成 | 二维码连接页不可用 |
| `zeroconf` | mDNS 监听（手机扫码配对） | 二维码自动配对不可用 |
| `ifaddr` | 本机 IP 探测 | PC 本机 IP 显示不准确 |
| `pyzbar` | 二维码扫码解码 | 扫码识别手机配对二维码不可用（打包产物已自带 `libzbar-64.dll`） |
| `cryptography` | 自研 ADB TLS 认证 | 自研 ADB 无线连接不可用 |
| `pyusb` | 自研 ADB USB 传输层 | 自研 ADB USB 连接不可用 |

## 运行

```bash
python Super_ADB_Win/项目启动入口/Super_ADB_主入口.py
```

可选首次启动附加参数：

- `--hash <文件路径>`：独立进程运行右键哈希计算（被 Windows 资源管理器右键菜单调用），不打开主窗口。
- `--hidden`：启动时直接隐藏到托盘（用于开机自启场景）。

## 跨平台兼容性

- 核心代码已跨平台，已埋 `darwin/linux/win32` 平台分支。
- 三平台子项目：`Super_ADB_Win/`、`Super_ADB_Linux/`、`Super_ADB_MAC/`，核心代码保持同步。
- 非跨平台功能：Windows 右键「计算哈希」集成、WiFi 密码查看（平台特定命令）、剪贴板写设备（部分平台）。
- 投屏使用官方 scrcpy 二进制（`外部扩展/scrcpy/`），按平台自动选择对应版本。

## 开发说明

### UI 与逻辑分离

界面布局由 `ui/Super_ADB.ui`（Qt Designer）定义，通过 `pyside6-uic` 生成 `项目UI/Super_ADB.py`。

```bash
pyside6-uic ui/Super_ADB.ui -o Super_ADB_Win/项目UI/Super_ADB.py
```

⚠️ **不要手改 `Super_ADB.py`**——它是自动生成的，下次重新生成会被整体覆盖。

### 模块划分

按功能划分子目录：`对话框/`（弹窗）、`页面/`（主窗口子页面）、`监控/`（性能监控）、`工具/`（工具模块，含自研 ADB 协议栈）、`脚本/`（构建/测试脚本）、`打包/`（PyInstaller 打包专用）。

### 自研 ADB 协议栈

位于 `工具/自研adb/`，核心模块：

| 模块 | 功能 |
|------|------|
| `adb协议.py` | ADB 消息协议封装（CNXN/AUTH/OPEN/OKAY/WRTE/CLSE） |
| `自研adb客户端.py` | 自研 ADB 客户端（连接、认证、sync 传输） |
| `密钥交换算法.py` | TLS 认证密钥交换 |
| `配对客户端.py` / `配对认证.py` | 无线调试配对（adb pair） |
| `usb传输层.py` / `usb连接.py` | USB 传输层（libusb） |
| `mdns发现.py` / `mdns主动查询.py` | mDNS 服务发现（无线调试端口自动解析） |
| `多设备管理器.py` | 多设备连接管理 |

### 资源管理

图标等资源由 `项目UI/png.qrc` 经 `pyside6-rcc` 编译为 `项目UI/png_rc.py`，勿手改。

## 环境要求

- Python ≥ 3.13
- 已安装并配置 ADB（在 PATH 中）—— 官方 adb 用于回退和部分功能
- Windows / macOS / Linux
- 可选：scrcpy（投屏，已随包分发）、tcpdump（抓包，已随包分发）

## 许可证

详见仓库 `LICENSE`。

## 关注与反馈

微信公众号：**Super_ADB**（微信搜索 `Super_ADB`）

![微信公众号二维码](Super_ADB_Win/资源/公众号.jpg)

使用过程中遇到 Bug、有改进建议，或想看详细使用教程，欢迎关注公众号留言反馈。

## 下载地址

> 夸克网盘分享链接会定期更新，请以最新版为准。

我用夸克网盘给你分享了「Super_ADB」：

- 链接：<https://pan.quark.cn/s/9f4e9c4b916b>
- 口令：`/~a97c3a4KdV~/`

点击链接或复制整段内容，打开「夸克 APP」即可获取。
