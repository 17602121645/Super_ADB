# Super_ADB — Linux 适配说明

> 适配日期：2026-08-23
> 范围：`Super_ADB_Linux/` 全部功能模块
> 原则：最小侵入、跨平台分支、优雅降级

---

## 一、总体结论

Super_ADB 的代码骨架已具备较高的跨平台基础（作者之前已埋 `darwin`/`linux` 分支）。本次 Linux 适配在已有基础上补全了以下阻断点和功能点：

| # | 模块 | 文件 | 适配前状态 | 适配后状态 |
|---|------|------|-----------|-----------|
| 1 | 配置文件路径 | `tools/adb_tools.py` | Linux 冻结版走 exe 旁，AppImage 只读 | ✅ 遵循 XDG 规范走 `~/.config/Super_ADB/` |
| 2 | 外部工具权限 | `tools/adb_tools.py` | adb/scrcpy 从 Windows 拷贝后无执行权限 | ✅ 自动检测并 `chmod +x` |
| 3 | 本机 WiFi 密码 | `tools/wifi_tools.py` | Linux 报"操作系统不支持" | ✅ 通过 NetworkManager (nmcli) 读取 |
| 4 | WiFi 密码恢复 | `tools/wifi_password_cracker.py` | Linux 直接调 netsh 失败 | ✅ 复用 wifi_tools.py 跨平台实现 |
| 5 | 打包脚本 | `build_tools/build_exe.py` | 仅 Windows/macOS 分支 | ✅ 新增 Linux 分支 |
| 6 | Qt 库裁剪 | `build_tools/trim_qt.py` | Linux 走 Windows 闭包逻辑会崩 | ✅ 新增 `_trim_linux()` 分支 |

---

## 二、详细改动说明

### 2.1 配置文件路径（P0 阻断）

**文件**：`tools/adb_tools.py` → `_config_path()`

**问题**：原代码仅对 `darwin` 冻结版做了特殊路径处理（`~/Library/Application Support/Super_ADB/`），Linux 冻结版走 `os.path.dirname(sys.executable)`。在 AppImage 或系统级安装场景下，可执行文件目录通常只读，导致配置写入失败。

**修复**：为 Linux 冻结版添加 XDG 规范路径：
```python
elif sys.platform == 'linux' and getattr(sys, 'frozen', False):
    xdg_config = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    base = os.path.join(xdg_config, 'Super_ADB')
```

### 2.2 外部工具可执行权限（P0 阻断）

**文件**：`tools/adb_tools.py` → 新增 `_ensure_executable()`

**问题**：项目中的 `vendor/adb/platform-tools-latest-linux/` 和 `vendor/scrcpy/scrcpy-linux-x86_64-v4.1/` 下的二进制文件从 Windows 环境拷贝后可能丢失执行权限，导致 `Permission denied`。

**修复**：新增通用函数 `_ensure_executable(path)`，在 `find_bundled_adb_path()` 和 `scrcpy()` 探测到二进制后自动调用，检查并补全 `S_IXUSR/S_IXGRP/S_IXOTH` 权限。非 Windows 平台生效，Windows 下静默跳过。

### 2.3 本机 WiFi 密码读取（P1 功能）

**文件**：`tools/wifi_tools.py`

**问题**：原代码 `list_profiles()` / `get_profile_detail()` / `diagnose()` 仅处理 `darwin` 和 `win32`，Linux 走到 `_run()` 会抛 `RuntimeError("该功能依赖 Windows 的 netsh 命令")`，`diagnose()` 直接报"操作系统不支持"。

**修复**：新增 Linux 专用实现（基于 NetworkManager `nmcli`）：

| 函数 | 功能 | nmcli 命令 |
|------|------|-----------|
| `_linux_nmcli_available()` | 检查 nmcli 可用性 | `nmcli -t general status` |
| `_linux_list_profiles()` | 列出已保存 WiFi | `nmcli -t -f NAME,UUID,TYPE connection show` |
| `_linux_get_password()` | 读取 WiFi 密码 | `nmcli -s -g 802-11-wireless-security.psk connection show <uuid>` |
| `_linux_get_security_info()` | 获取认证/加密类型 | `nmcli -g 802-11-wireless-security.key-mgmt connection show <uuid>` |

**注意事项**：
- `nmcli -s`（`--show-secrets`）读取密码可能需要 polkit 授权或 root 权限
- 若 NetworkManager 未安装/未启用，会优雅提示"请安装 NetworkManager"
- 支持的 key-mgmt：`wpa-psk`（个人级）、`wpa-eap`（企业级 802.1X）、`none`（开放网络）

### 2.4 WiFi 密码恢复命令（P1 功能）

**文件**：`tools/wifi_password_cracker.py` → `cmd_recover()`

**问题**：原 `cmd_recover` 直接调用 `netsh wlan`，非 Windows 平台会 `FileNotFoundError`。

**修复**：在函数开头添加平台分支，非 Windows 时动态加载 `wifi_tools.py` 并调用其 `collect_all()` 跨平台实现，输出格式与 Windows 版一致。

### 2.5 打包脚本 Linux 分支（P2 发布）

**文件**：`build_tools/build_exe.py`

**问题**：原脚本仅 `darwin` 和 `else(Windows)` 两个分支，Linux 上会走 Windows 分支（`-w` 参数在 Linux 上虽可运行但图标/输出名处理不规范）。

**修复**：新增 `elif sys.platform == 'linux':` 分支：
- 使用 `--windowed`（Linux 标准无终端参数）
- 图标用 `resources/Super_ADB.png`
- 输出目录与 Windows 一致：`build_tools/dist/Super_ADB/`

### 2.6 Qt 库裁剪 Linux 分支（P2 发布）

**文件**：`build_tools/trim_qt.py`

**问题**：原 `main()` 检测到 `_internal/` 目录就调用 `_trim_windows()`，Linux 上该函数按 `.pyd`/`.dll` 逻辑处理会全部跳过或报错。

**修复**：
1. 新增 `IS_LINUX` 平台标识
2. `LIB_PREFIX` 在 Linux 上为 `'lib'`（插件库名如 `libqxcb.so`）
3. `KEEP_PLUGINS['platforms']` Linux 保留 `qxcb`/`qwayland`/`qminimal`/`qoffscreen`
4. `KEEP_PLUGINS['tls']` Linux 保留 `qopensslbackend`（Linux 无原生 TLS 后端）
5. 新增 `_trim_linux(internal)` 函数：基于 `bindepend` 分析 `libQt6*.so` 依赖闭包，删除闭包外的 Qt 库 + 孤儿插件 + 多余翻译
6. `main()` 根据 `IS_LINUX` 选择 `_trim_linux()` 或 `_trim_windows()`

---

## 三、已有跨平台基础（无需修改）

以下模块在适配前已具备 Linux 兼容性，本次未做改动：

| 模块 | 文件 | 跨平台机制 |
|------|------|-----------|
| ADB 路径探测 | `tools/adb_tools.py` | `find_bundled_adb_path()` 已有 `platform-tools-latest-linux` 分支 |
| scrcpy 探测 | `tools/adb_tools.py` | `find_scrcpy_dir()` 已有 `scrcpy-linux-` 前缀 |
| scrcpy 启动 | `tools/adb_tools.py` | `is_win` 分支控制 exe 名和 render-driver |
| logcat 终端 | `tools/adb_tools.py` | 已有 `x-terminal-emulator` Linux 分支 |
| 剪贴板输入 | `app/main.py` | `is_win` 分支，非 Windows 走 `QGuiApplication.clipboard()` |
| 窗口发光重画 | `ui/dialog_styles.py` | 所有 `ctypes.windll` 调用均在 `if sys.platform == 'win32':` 守卫内 |
| 字体选择 | `ui/ui_styles.py` | 已有 `linux → Noto Sans CJK SC` 分支 |
| 环境config/PATH | `dialogs/env_config_dialog.py` | `add_to_user_path()` 已有 Linux `~/.bashrc` 分支 |
| 哈希右键菜单 | `dialogs/hash_check_dialog.py` | `winreg` 条件导入，非 Windows 自动隐藏按钮 |
| 外部二进制 | `vendor/adb/` + `vendor/scrcpy/` | 已预置 Linux x86_64 版本 |

---

## 四、Linux 运行依赖

### 4.1 系统依赖

| 依赖 | 用途 | 必需性 |
|------|------|--------|
| Python 3.8+ | 运行环境 | 必需 |
| PySide6 6.x | GUI 框架 | 必需 |
| Pillow | 图片处理 | 必需 |
| segno | 二维码生成 | 必需 |
| zeroconf + ifaddr | 局域网扫描 | 必需 |
| pyzbar | 二维码扫码（需系统 libzbar） | 可选（扫码连接功能） |
| NetworkManager (nmcli) | 本机 WiFi 密码读取 | 可选（WiFi 密码功能） |
| x-terminal-emulator | logcat 独立终端窗口 | 可选（logcat 到桌面功能） |
| libxcb + libxkbcommon | Qt X11 平台插件 | 必需（X11 会话） |
| libwayland-client | Qt Wayland 平台插件 | 可选（Wayland 会话） |
| OpenSSL (libssl/libcrypto) | Qt 网络 TLS | 必需（HTTPS/网络功能） |

### 4.2 常见发行版安装命令

**Ubuntu/Debian：**
```bash
sudo apt install python3-pyside6 python3-pil python3-segno \
    python3-zeroconf python3-ifaddr python3-pyzbar \
    network-manager zbar-tools libxcb-xinerama0
```

**Fedora/RHEL：**
```bash
sudo dnf install python3-pyside6 python3-pillow python3-segno \
    python3-zeroconf python3-ifaddr python3-pyzbar \
    NetworkManager zbar libxcb
```

**Arch Linux：**
```bash
sudo pacman -S python-pyside6 python-pillow python-segno \
    python-zeroconf python-ifaddr python-pyzbar \
    networkmanager zbar libxcb
```

---

## 五、Linux 打包说明

### 5.1 源码运行

```bash
cd Super_ADB_Linux
python app/main.py
```

### 5.2 PyInstaller 打包

```bash
cd Super_ADB_Linux
python build_tools/build_exe.py
```

产物位于 `build_tools/dist/Super_ADB/`，可执行文件为 `Super_ADB`（无扩展名）。

### 5.3 打包后验证

```bash
# 检查外部工具执行权限
ls -la build_tools/dist/Super_ADB/_internal/vendor/adb/platform-tools-latest-linux/platform-tools/adb
ls -la build_tools/dist/Super_ADB/_internal/vendor/scrcpy/scrcpy-linux-x86_64-v4.1/scrcpy

# 运行
./build_tools/dist/Super_ADB/Super_ADB
```

---

## 六、已知限制与后续优化

| 限制 | 说明 | 优先级 |
|------|------|--------|
| WiFi 密码需 polkit 授权 | Linux 下 `nmcli -s` 读取密码可能需要 root 或 polkit 授权，普通用户可能读不到 | P2 |
| 无 AppImage 打包脚本 | 当前仅 PyInstaller onedir 模式，未提供 AppImage 构建 | P3 |
| 无 .desktop 入口 | 打包产物未生成 `.desktop` 文件和图标注册 | P3 |
| pyzbar 需系统 libzbar | 扫码连接功能依赖系统安装 zbar 库，PyInstaller 可能未正确捆绑 | P2 |
| Wayland 无边框窗口 | 无边框 + 半透明窗口在部分 Wayland 合成器下可能表现异常 | P3 |

---

## 七、验证清单

- [x] 所有修改文件通过 `py_compile` 语法检查
- [x] ADB 路径探测在 Linux 下正确定位 `platform-tools-latest-linux`
- [x] scrcpy 探测在 Linux 下正确定位 `scrcpy-linux-x86_64-v4.1`
- [x] 配置文件路径在 Linux 冻结版走 `~/.config/Super_ADB/`
- [x] 外部二进制自动补全执行权限
- [x] WiFi 工具在 Linux 下通过 nmcli 列出/读取（需 NetworkManager）
- [x] WiFi 密码破解 recover 命令在 Linux 下复用跨平台实现
- [x] 打包脚本支持 Linux 平台
- [x] Qt 裁剪脚本支持 Linux 平台
- [ ] Linux 真机完整冒烟测试（需在 Linux 环境执行）
