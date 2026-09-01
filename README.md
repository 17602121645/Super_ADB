# Super_ADB

> 一款跨平台的 ADB 集成调试工具，集设备连接、应用管理、文件传输、日志抓取、性能监控、网络抓包等功能于一体。

![主界面](docs/screenshots/主界面.png)

## ✨ 功能特性

### 🔌 设备连接
- **USB 直连**：即插即用，自动识别已连接设备
- **无线调试**：支持局域网扫描、配对码连接（adb pair）、二维码连接（mDNS）三种方式
- **自研 ADB 协议栈**：可与官方 adb 一键切换，传输速度最高达官方 2.7 倍

### 📁 文件管理
- 设备文件树浏览器，支持上传/下载/删除/重命名
- 拖拽上传、递归搜索、文本预览
- 权限修改（右键授权 777），只读分区自动检测并附解锁引导

### 📦 应用管理
- APK 拖拽安装、批量安装、实时进度显示
- APK 元信息解析（包名/版本/权限/四大组件）
- 解包查看资源，安装失败自动诊断原因

### 📋 日志抓取
- 多标签 logcat 查看器，实时流式输出
- 关键字过滤（支持正则）、日志级别筛选、星标标记
- 多设备同时监控，日志导出保存

### 📊 性能监控
- 设备级：CPU 多核分核/内存/温度/FPS/网络速率
- 应用级：12 项图表指标、内存泄漏自动检测、ANR/OOM 检测、hprof 自动抓取
- HTML 报告导出，数据实时刷新

### 🌐 网络抓包
- tcpdump 自动检测架构并推送二进制（arm64/arm）
- BPF 过滤器、实时包数统计、pcap 自动拉取
- PCAP 解析器：HTTP/HTTPS/TCP/UDP 协议分析、流重组

### 📺 scrcpy 投屏
- 低延迟投屏，键鼠反向控制
- 分辨率/码率/帧率/编码器/渲染驱动自定义
- 文件拖拽传输、屏幕录制

### 🛠️ 便捷工具集
- ADB 终端（命令历史/Tab 补全/多设备切换）
- JSON 工具（格式化/差异对比/YAML 互转/Schema 校验）
- 哈希校验（8 种算法，支持 Windows 右键菜单）
- 时间戳转换、修改系统时间、设备信息、证书安装、环境配置

### 🐒 Monkey 压测
- 命令模板自定义、暂停/继续/停止控制
- 实时事件饼图统计、崩溃报告自动拉取、事件回放

## 🖥️ 支持平台

| 平台 | 目录 | 状态 |
|------|------|------|
| Windows | `Super_ADB_Win/` | ✅ 完整支持 |
| macOS | `Super_ADB_MAC/` | ✅ 完整支持 |
| Linux | `Super_ADB_Linux/` | ✅ 完整支持 |

## 🚀 快速开始

### 环境要求
- Python 3.8+
- PySide6
- 对应平台的 ADB 工具（内置或自行配置路径）

### 源码运行

```bash
# 克隆仓库
git clone https://github.com/17602121645/Super_ADB.git
cd Super_ADB

# 安装依赖
pip install -r requirements.txt

# 运行（以 Windows 为例）
cd Super_ADB_Win
python app/main.py
```

### 下载安装包

前往 [Releases](https://github.com/17602121645/Super_ADB/releases) 下载对应平台的预编译版本，或关注公众号获取最新下载链接。

## 📁 项目结构

```
Super_ADB/
├── Super_ADB_Win/          # Windows 平台源码
├── Super_ADB_MAC/          # macOS 平台源码
├── Super_ADB_Linux/        # Linux 平台源码
├── ui/                     # 通用 UI 资源
├── docs/                   # 项目文档与截图
├── requirements.txt        # Python 依赖
└── LICENSE                 # MIT 许可证
```

## 📖 文档

- [功能介绍与使用说明](docs/USAGE.md) — 全部功能的详细介绍与操作步骤
- [安装说明](docs/INSTALL.md) — 安装包解压与使用说明

## 🔗 开源地址

- **GitHub**：https://github.com/17602121645/Super_ADB.git
- **Gitee**：https://gitee.com/jcs1995/super_adb.git

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！请先阅读 [贡献指南](CONTRIBUTING.md)。

## 📜 许可证

本项目基于 [MIT License](LICENSE) 开源。

## 📢 联系方式

扫码关注公众号 **Super_ADB**，获取最新版本更新、使用教程和技术分享。

![公众号](ui/wechat_qrcode.jpg)
