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
- **模块划分**：按功能划分子目录 `dialogs/`（弹窗）、`pages/`（主窗口子页面）、`monitors/`（性能监控）、`utils/`（工具模块）、`scripts/`（构建脚本）。`Super_ADB_Main.py` 启动时把各子目录加入 `sys.path`，因此模块间仍可用裸模块名互相 import，无需改任何 import 语句。
- **资源管理**：图标等资源由 `ui/png.qrc` 经 `pyside6-rcc` 编译为 `Super_ADB_Main/png_rc.py`，同样勿手改。

## 环境要求

- Python ≥ 3.13
- 已安装并配置 ADB（在 PATH 中）
- Windows / macOS / Linux（核心功能跨平台；Windows 右键「计算哈希」集成仅限 Windows）

## 许可证

详见仓库 `LICENSE`。
