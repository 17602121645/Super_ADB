# Super_ADB —— ADB 集成调试工具

> 基于 **PySide6 + .ui 布局 + QSplitter 分屏** 的 Android 调试一体化工具箱，深色主题。

## 简介

Super_ADB 把日常 Android 调试中最常用的操作——设备连接、系统操作、应用管理、文件传输、日志抓取、性能监控、Monkey 压测，以及一系列**纯本地**小工具（命令行 / JSON / MD5 / 时间戳转换）——集成到一个界面里。免命令行也能完成大部分调试工作，同时保留了直接敲命令的入口。

## 功能特性

| 模块 | 能力 |
|---|---|
| **设备连接** | 刷新设备列表、连接 / 断开、一键重启（recovery / bootloader） |
| **系统操作** | 设置 / 清除代理、system rw/ro 切换、设备信息、DPM 等 |
| **应用操作** | 启动 / 停止 / 卸载 / 清除应用、运行应用列表、应用信息 |
| **文件管理** | 设备文件树浏览、上传 / 下载、权限操作 |
| **日志抓取** | 多标签 logcat、关键字过滤、标签 / 进程 / 消息星标、实时流式输出 |
| **性能监控** | 设备级（CPU / 内存 / 温度 / FPS）+ 应用级（12 项图表指标、内存泄漏检测、ANR / OOM 检测） |
| **Monkey 压测** | 命令模板、暂停 / 继续、实时事件饼图、崩溃报告拉取、事件回放 |
| **便捷工具** | 命令行、JSON 工具、MD5 校验、时间戳转换 |

### 便捷工具详解

- **命令行**：打开系统 PowerShell（Windows）/ 终端（macOS, Linux）。
- **JSON 工具**：格式化 / 压缩、差异对比、YAML 互转、Schema 校验、树形视图。
- **MD5**：MD5 / SHA1 / SHA256 / SHA512 / SHA3-256 / CRC32 多算法校验，拖入文件即算，进度条 + 复制全部 + CSV/JSON 导出；支持注册 Windows 右键菜单「计算哈希」。
- **时间戳转换**：Unix 时间戳 ↔ 北京时间实时双向互转，自动识别 秒 / 毫秒 / 微秒 / 纳秒。

## 界面预览

<p align="center">
  <img src="feature_intro/main.png" width="720" alt="Super_ADB 主界面"/>
</p>

| 设备性能监控 | 应用性能监控 |
|:---:|:---:|
| <img src="feature_intro/device-perf-monitor-v2.png" width="340" alt="设备性能监控"/> | <img src="feature_intro/perf_app.png" width="340" alt="应用性能监控"/> |

| Monkey 压测 | 安装 / 解包 |
|:---:|:---:|
| <img src="feature_intro/monkey.png" width="340" alt="Monkey 压测"/> | <img src="feature_intro/install.png" width="340" alt="安装解包"/> |

| 无线调试（配对码连接） | 无线调试（二维码连接） |
|:---:|:---:|
| <img src="feature_intro/wireless-debug-pair.png" width="360" alt="无线调试-配对码连接"/> | <img src="feature_intro/wireless-debug-qr.png" width="360" alt="无线调试-二维码连接"/> |

| 无线调试（二维码弹窗大图） | 无线调试（局域网扫描） |
|:---:|:---:|
| <img src="feature_intro/wireless-debug-qr-popup.png" width="360" alt="无线调试-二维码弹窗大图"/> | <img src="feature_intro/wireless-debug-lan.png" width="360" alt="无线调试-局域网扫描"/> |

<p align="center">
  <img src="feature_intro/system-ops.png" width="480" alt="系统操作"/>
</p>

## 功能增强总览（2026-08）

> 自「按清单顺序实现 + 完成后检查 + 推送」工作流启动以来，所有代码类需求均已实现并推送，无遗留未交付任务。

### 已实现功能

| # | 模块 | 关键能力 | 提交 |
|---|------|---------|------|
| 1 | Monkey 压测 | 模板 / 暂停恢复 / 事件饼图 / tombstone 拉取 / 回放 | `9dc7c0e` |
| 2 | 应用性能监控 | 内存泄漏自动 hprof 抓取 + 手动按钮；修复 ScrollChart 多系列 | `5289e0d` |
| 3 | .ui 布局同步 | PC IP + tcpdump 按钮移入 `ui/Super_ADB.ui` | `dd7bc62` |
| 4 | 设备性能监控 | 多核 CPU 分核 / 点数可配 + HTML 导出 / 网络速率 / 电池温度 | `fddf5c6` |
| 5 | 日志查看器 | 包名过滤修复 / 标签精确匹配 / 高亮增强 | `ddff4cb` + `a22d2bd` |
| 6 | 文件管理器 | 列表头列宽拖拽失效修复 | `6c2296b` |
| 7 | 命令输出 | 语法配色 + 关键字字重调细 | `4033232` + `27e654c` |
| 8 | 界面样式 | 控件字重 700→400 | `22199e9` |
| 9 | 关于对话框 | 开源地址改为 `super_-adb-2026` + 公众号引导 | `cbbfaf9` + `433c2cb` |
| 10 | 安装 / 解包 | APK 元信息解析崩溃修复 | `d3ce214` |
| 11 | tcpdump 抓包 | 「停止」按钮无响应修复 | `894cc13` |
| 12 | WiFi 密码查看器 | 本机已保存 WiFi / 密码（netsh 中英双关键字） | `972c582` |
| 13 | MD5 右键菜单 | 右键「计算哈希」图标修复 | `cfe58b6` |
| 14 | 设备连接 | 新增「WiFi 配对」弹窗（`adb pair` 配对码流程） | `1c7805f` |
| 15 | 统一无线调试入口 | 合并「局域网扫描」+「WiFi 配对」为单一面板；后续扩展为「局域网扫描/配对码连接/二维码连接」三标签页 | `e657c0a` |
| 16 | 二维码连接页 | 新增独立「二维码连接」标签页；扫码识别手机配对二维码并回填配对页；PC 端生成二维码供手机扫描后，Super_ADB 通过 mDNS 自动监听手机广播的 `_adb-tls-pairing._tcp` 服务并执行 `adb pair` 完成配对 | `061eecf` |

各模块详细文档见 [`feature_intro/`](feature_intro/)。

### 待办（文档侧）

- `super_-adb-2026` 功能介绍子仓内容同步（主仓 `关于对话框` 已指向该地址）
- 功能介绍文档模拟操作截图批量补齐

## 目录结构

详见 [`项目结构图.md`](项目结构图.md)。

## 安装

```bash
# 需要 Python 3.13+（推荐 3.14），并确认 adb 已配置且在 PATH 中
pip install -r requirements.txt
```

项目运行仅依赖 **PySide6** 与 **Pillow** 两项第三方包，其余均为 Python 标准库。

## 运行

```bash
python Super_ADB_Main/Super_ADB_Main.py
```

## 开发说明

- **UI 与逻辑分离**：界面布局由 `ui/Super_ADB.ui`（Qt Designer）定义，通过 `pyside6-uic` 生成 `Super_ADB_Main/Super_ADB.py`。
  ```bash
  pyside6-uic ui/Super_ADB.ui -o Super_ADB_Main/Super_ADB.py
  ```
  ⚠️ **不要手改 `Super_ADB.py`**——它是自动生成的，下次重新生成会被整体覆盖。
- **新增主页控件**：改 `ui/Super_ADB.ui` → 重新 uic 生成 `Super_ADB.py` → 在主窗口用 `self.xxxBtn` 接信号即可。
- **模块划分**：按功能划分子目录 `对话框/`（弹窗）、`pages/`（主窗口子页面）、`监控/`（性能监控）、`工具/`（工具模块）、`脚本/`（构建脚本）。`Super_ADB_Main.py` 启动时把各子目录加入 `sys.path`，因此模块间仍可用裸模块名互相 import，无需改任何 import 语句。
- **资源管理**：图标等资源由 `ui/png.qrc` 经 `pyside6-rcc` 编译为 `Super_ADB_Main/png_rc.py`，同样勿手改。

## 环境要求

- Python ≥ 3.13
- 已安装并配置 ADB（在 PATH 中）
- Windows / macOS / Linux（核心功能跨平台；Windows 右键「计算哈希」集成仅限 Windows）

## 许可证

详见仓库 `LICENSE`。

## 关注公众号

微信公众号：**Super_ADB**（微信搜索 `Super_ADB`）

![微信公众号二维码](feature_intro/0fcdc7cdcb444c1be158451aeb2e1749.jpg)

使用过程中遇到 Bug、有改进建议，或想看详细使用教程，欢迎扫码关注公众号留言反馈。

## 下载地址

> 夸克网盘分享链接会定期更新，请以最新版为准。

我用夸克网盘给你分享了「Super_ADB」：

- 链接：<https://pan.quark.cn/s/9f4e9c4b916b>
- 口令：`/~a97c3a4KdV~/`

点击链接或复制整段内容，打开「夸克 APP」即可获取。
