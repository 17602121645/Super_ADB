# 更新日志

本项目所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增
- 新增 GitHub 仓库地址展示（关于弹窗）
- 新增 `.workbuddy/` 工具缓存目录排除规则
- 新增标准开源规范文件（LICENSE、README、CONTRIBUTING、CODE_OF_CONDUCT、CHANGELOG、SECURITY）

### 变更
- 关于弹窗移除 Gitee 开源地址，仅保留 GitHub 仓库地址
- 项目开源仓库同步托管至 GitHub 与 Gitee

## [2026.08.07] - 2026-08-07

### 新增
- **macOS 平台完整支持**：新增 `Super_ADB_MAC/` 平台目录，全部功能适配 macOS
- **跨平台 ADB 工具**：自研 ADB 协议栈支持 Windows/macOS/Linux 三平台
- **mac 打包脚本**：`build_mac_dmg.sh`，使用系统 python3 构建 .dmg 安装包
- **关于弹窗新版下载地址**：从 `config/build_info.json` 读取，样式同版本号
- **打包信息独立配置**：`config/build_info.json`，不再混入用户配置

### 变更
- 打包输出文件夹按平台命名：`Super_ADB_Win` / `Super_ADB_MAC` / `Super_ADB_Linux`
- mac 打包改用系统 python3 构建
- 关于弹窗版本号上移，新增下载地址显示

### 修复
- 修复打包后启动默认调起官方 adb：spec 添加 config 目录 + 配置加载失败时默认自研 adb（双保险）
- 修复修改系统时间在自研 adb 模式下误调官方 adb：新增获取 root 权限统一方法
- 修复无线调试二维码配对成功后无法自动连接：mDNS 发现 RLock 死锁 + 配对成功后无条件自动连接真实调试端口
- 抓包设备端缓存路径从 `/data/local/tmp/Super_ADB` 改为 `/sdcard/Super_ADB`（普通模式加 su 兜底）

## [历史版本]

### 核心功能
- 设备连接：USB 直连、局域网扫描、配对码连接、二维码连接（mDNS）
- 文件管理：文件树浏览、拖拽上传下载、权限修改、递归搜索
- 应用管理：APK 安装/解包、批量安装、元信息解析、失败诊断
- 日志抓取：多标签 logcat、关键字过滤、星标标记、导出保存
- 性能监控：设备级 + 应用级双层监控、内存泄漏检测、HTML 报告导出
- 网络抓包：tcpdump 自动推送、BPF 过滤、pcap 解析与流重组
- scrcpy 投屏：低延迟投屏、键鼠反向控制、参数自定义、屏幕录制
- Monkey 压测：命令模板、实时统计、崩溃报告、事件回放
- 便捷工具集：ADB 终端、JSON 工具、哈希校验、时间戳转换、设备信息、证书安装等

### 技术特性
- 自研 ADB 协议栈，传输速度最高达官方 adb 2.7 倍
- 自研 ADB 与官方 adb 一键切换
- 6 套主题切换，设置自动保存
- 三平台统一代码架构，平台差异隔离

---

> 完整提交历史请查看 [GitHub Commits](https://github.com/17602121645/Super_ADB/commits/master)
