# tcpdump 二进制文件目录

位置：`Super_ADB_Win/外部扩展/tcpdump/`（与 scrcpy、adb 等外部工具同级）

将对应架构的 tcpdump 静态编译二进制放入此目录，命名规则：

- `tcpdump_arm64` — ARM64 架构（大部分现代手机）
- `tcpdump_arm` — ARM 32位架构（旧设备）
- `tcpdump_x86` — x86 架构（模拟器）

## 获取方式

从 https://www.androidtcpdump.com/ 下载静态编译版，或自行用 NDK 编译。

要求：
- 静态链接（不依赖设备上的 libpcap.so）
- strip 调试符号以减小体积
- 单架构约 600KB - 1.2MB

## 工作流程

1. 启动抓包时检测设备上是否已有 tcpdump（which / --version）
2. 没有则自动推送对应架构二进制到 /sdcard/Super_ADB/tcpdump
3. 复制到 /data/local/tmp/tcpdump 并 chmod +x（/sdcard 通常 noexec 无法直接执行）
4. 用 /data/local/tmp/tcpdump 执行抓包
