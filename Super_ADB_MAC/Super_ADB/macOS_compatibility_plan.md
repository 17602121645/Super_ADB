# Super_ADB — macOS 兼容性扫描报告与改造方案

> 范围：扫描 `Super_ADB_Main/` 全部功能，判断 macOS 兼容性，给出改造方案。
> 原则：**仅分析 + 出方案，未改动任何代码**。

---

## 一、总体结论

Super_ADB 的代码骨架**已具备较高的跨平台基础**（作者之前已埋了 `darwin` 分支）：

- `adb_utils.py`：配置路径、scrcpy 子目录、logcat 终端唤醒、shell 执行策略都已按 `darwin/linux/win32` 分支处理。
- `界面样式.py`：字体已按平台选择（macOS → `PingFang SC`）。
- `Super_ADB_Main.py`：单实例用 `QLocalServer`/`QLocalSocket`（跨平台）；无边框窗口用鼠标事件拖动（未见 `nativeEvent`/Windows 消息）。
- `pyinstall_y.py`：已有 `darwin` 分支，会生成 `.app`。
- `requirements.txt`：PySide6 / Pillow / segno / zeroconf / ifaddr 全部跨平台。

**但存在 1 个硬崩溃点 + 2 个打包/依赖缺口 + 2 个 Windows 专属功能不可用**，需按方案改造才能跑起来且功能完整。

---

## 二、功能兼容性逐块评估

| # | 功能模块 | 文件 | macOS 结论 | 说明 |
|---|---------|------|-----------|------|
| 1 | ADB 命令封装 | `adb_utils.py` | ⚠️ 核心可用 | 逻辑跨平台；但 `adb` 二进制依赖 PATH（见方案 A3）`creationflags=CREATE_NO_WINDOW` 在 macOS 为 0，无害 |
| 2 | 设备扫描/连接/配对 | `adb_utils.py` | ✅ | 纯 `adb` 命令 |
| 3 | 设备信息 / OAID / MAC | `adb_utils.py` | ✅ | 设备端 shell 脚本，跨平台 |
| 4 | 截图 / 录屏 | `adb_utils.py` | ✅ | 落 `~/Desktop`（macOS 有桌面目录） |
| 5 | 文件管理 | `file_manager_page.py` | ✅ | `adb push/pull` |
| 6 | 应用管理（启停/装/卸） | `adb_utils.py` | ✅ | `am`/`pm`/`monkey` |
| 7 | Monkey 压测 | `Monkey压测窗口.py` | ✅ | `adb shell monkey` |
| 8 | 日志抓取 logcat | `log_viewer_page.py` | ✅ | `QProcess` 流式 |
| 9 | tcpdump 抓包 | `TCPDump对话框.py` | ✅ | **设备端** `tcpdump`，落 `~/Desktop/Super_ADB/`；纯 PySide6，无任何 Windows 依赖 |
| 10 | 性能监控（设备/应用） | `设备性能监控.py` / `应用性能监控.py` | ✅ | `dumpsys` 跨平台 |
| 11 | WiFi 配对 / 扫码连接 | `WiFi配对对话框.py` / `二维码连接页.py` | ✅ | 二维码走 `pyzbar`（跨平台）；`adb pair` 跨平台 |
| 12 | 局域网扫描发现 | `局域网扫描对话框.py` | ✅ | `zeroconf`/`ifaddr` 跨平台 |
| 13 | 二维码生成 | `segno` | ✅ | 纯 Python |
| 14 | JSON 工具 | `JSON工具对话框.py` | ✅ | 纯 PySide6 |
| 15 | 投屏 scrcpy | `adb_utils.py` | ⚠️ 需打包补充 | 代码已选 `scrcpy-mac-v2.6` 子目录，但：(a) 该目录未随包分发（需 `--add-data`）(b) 未捆绑时回退 PATH 上的 `scrcpy` |
| 16 | 单实例 / 主窗口 / 托盘 | `Super_ADB_Main.py` | ⚠️ 需实测 | 逻辑跨平台；无边框窗口在 macOS 的拖动/阴影/全屏需真机微调；托盘走菜单栏 |
| 17 | **本机 WiFi 密码查看** | `WiFi工具.py` + `WiFi对话框.py` | ❌ 不可用 | 依赖 Windows `netsh wlan`，macOS 无此命令；`diagnose()` 已返回"不支持"，但 `collect_all()` 会抛 `RuntimeError` |
| 18 | **计算哈希 + 右键菜单** | `MD5对话框.py` + `哈希上下文菜单.py` | ❌→⚠️ 硬崩 | `MD5对话框.py:27` **顶部无条件 `import winreg`**，macOS 导入即 `ModuleNotFoundError`；右键菜单是 Windows 注册表机制 |
| 19 | 剪贴板写设备 | `Super_ADB_Main.py:807` | ⚠️ 已降级 | `ctypes.windll.kernel32/user32` 在 `try` 内，macOS 抛 `AttributeError` 被捕获→功能失效但不崩 |

---

## 三、阻断级问题（必须修，否则跑不起来 / 打开即崩）

### B1. `MD5对话框.py` 顶部无条件 `import winreg`（硬崩溃）
- **现象**：macOS 上只要打开「计算哈希 / MD5」功能（懒加载 `MD5对话框`），`import winreg` 失败 → 整个功能崩溃。
- **修复**：
  ```python
  try:
      import winreg
  except ImportError:
      winreg = None
  ```
  并把 `_install_ctx_menu()` / `_uninstall_ctx_menu()` 中 `winreg.*` 调用包一层 `if winreg is not None:`；非 Windows 时这两个方法禁用并在 UI 提示"右键菜单仅 Windows 支持"。

### B2. 打包脚本 `pyinstall_y.py` 的 darwin 分支不完整
- **现象**：
  1. 没有 `--add-data` 把 `data/scrcpy-mac-v2.6`、图标资源、adb 打进 `.app`；
  2. 末尾 `import trim_qt; trim_qt.main()` 仅对 Windows 有效（依赖 `*.pyd`，macOS 为 `*.so`，会安全 abort → 构建不瘦身，体积巨大）。
- **修复**：
  - darwin 分支补 `--add-data "data:data"`（macOS 用 `:` 分隔，Windows 用 `;`）；
  - 新增 `trim_qt_mac.py`（删 `Qt6*.dylib` / `*.qm` / 孤儿 imageformat 插件），或在 `trim_qt.main()` 内按 `sys.platform` 分支；
  - 图标改用 `.icns`（可由 `Super_ADB.png` 生成），避免 pyinstaller 警告。

### B3. ADB 二进制来源（最大功能缺口）
- **现象**：`AdbHelper(adb_path='adb')` 默认依赖 `adb` 在 PATH。仓库**未捆绑任何 adb**（无 `platform-tools`、无 `data/adb`）。
  - Windows 用户本机有 Android SDK 故可用；
  - macOS 用户若没装 platform-tools，所有功能直接不可用。
- **修复（二选一，推荐 A）**：
  - **A（推荐，开箱即用）**：下载 macOS 版 `platform-tools`（含 `adb`），用 `--add-data` 捆绑进 `.app`；在 `AdbHelper.__init__` 内，当 `sys.platform=='darwin' and frozen` 时，把 `adb_path` 解析为 `.app/Contents/MacOS/<adb>` 或 `Resources/` 下的捆绑 `adb`。
  - **B（轻量，需用户自备）**：首次启动检测 `adb` 是否存在，没有则弹窗引导安装（brew install android-platform-tools 或下载 official platform-tools），并在设置里允许手动指定 adb 路径。

---

## 四、按需改造 / 优化（非阻断）

### C1. 剪贴板写设备跨平台化（`Super_ADB_Main.py:801-840`）
- 当前用 Win32 API 写设备剪贴板，macOS 走 `except` 降级（失效但不崩）。
- 改为：Windows 走原 Win32 路径；非 Windows 用 `QGuiApplication.clipboard().setText(text)`（Qt 跨平台）。功能在 macOS 即可正常。

### C2. 无边框窗口 macOS 视觉微调
- frameless + 半透明在 macOS 上可显示，但窗口阴影、圆角、全屏/分屏（Spaces）行为需在真机验证；必要时用 `QtWidgets` 设置 `WA_TranslucentBackground` 或调整 `setWindowFlags`。非阻断。

### C3. 本机 WiFi 密码功能（方案二选一）
- **禁用 + 提示**（最小改动）：`WiFi对话框` 在非 Windows 时禁用入口按钮，打开即提示"本机 WiFi 密码查看仅 Windows 支持（依赖 netsh）"。
- **macOS 重写**（功能完整）：用 `security find-generic-password -D "AirPort network password" -a <ssid> -w` 读取 Keychain 中的 WiFi 密码，替换 `WiFi工具.py` 的 netsh 实现（新增 `wifi_utils_mac.py` 或按平台分支）。注意：macOS 读 Keychain 需用户授权（首次弹 Touch ID / 密码）。

### C4. 右键「计算哈希」触发方式（macOS 对应实现）
- Windows 用注册表 `HKCU\Software\Classes\*\shell`；macOS 无等价注册表。
- 替代方案（任选）：
  1. 应用内提供「计算哈希」入口按钮（已有 `Md5Dialog`，跨平台可用）；
  2. 制作 **Finder 快速操作（Quick Action / Automator）** 或 **Service**，把选中文件传给 `Super_ADB.app --hash <paths>`；
  3. 复用已有的 `HashContextDialog`（纯 PySide6，跨平台），只换触发源。

### C5. macOS 签名 / 公证（仅"分发给别人"才需要，自用免）
- **自用场景（本机跑）不需要付费账号、不需要公证**：你在本机 `pyinstaller` 打出来的 `.app` 不带 `com.apple.quarantine` 隔离标记，Gatekeeper 不拦，没签名也能直接跑。万一被隔离（如拷到别的盘再拷回），`xattr -dr com.apple.quarantine /path/Super_ADB.app` 一行去掉即可，或右键"打开"→"打开 Anyway"。
- **可选省心**：`codesign --force --deep --sign -`（ad-hoc 签名，Xcode 命令行工具自带，免费、无需任何 Apple 账号）可避开偶尔的"app 已损坏"提示。
- **仅当你把 `.app` 发给其他 Mac 时才需要**：跨机器 Gatekeeper 强制要求 Developer ID 证书 + `notarytool` 公证（需 $99/年付费 Apple 开发者账号），否则对方报"无法验证开发者"。属发布事宜，非代码阻断、非自用阻断。

### C6. pyzbar 在 macOS 的动态库（构建期风险）
- `hook-pyzbar.py` 用 `collect_dynamic_libs('pyzbar')` 收集 `libzbar`/`libiconv`。macOS 上为 `.dylib`，且本机可能未装 `zbar` 系统库，`collect_dynamic_libs` 可能找不到。
- 需在 macOS 构建机上安装 `pyzbar` 依赖（如 `brew install zbar`）或显式捆绑 `libzbar.dylib`；`runtime_pyzbar.py` 的 `_internal/pyzbar` 路径在 `.app` 内仍可解析（onedir 时 exe 在 `MacOS/`，相对路径成立）。建议真机构建验证一次扫码。

---

## 五、改造步骤（按优先级）

### P0 — 必须（否则无法在 macOS 运行 / 打开即崩）
1. **B1**：`MD5对话框.py` 守卫 `import winreg`（1 个文件，约 10 行）。
2. **B3**：捆绑 / 定位 macOS `adb`（改 `adb_utils.py` 解析 + `pyinstall_y.py` 加 `--add-data` + 下载 macOS platform-tools）。
3. **B2**：`pyinstall_y.py` darwin 分支补 `--add-data` + macOS 瘦身脚本。
4. 在 macOS 真机执行 `python pyinstall_y.py`，产出 `.app` 并做启动冒烟（offscreen 不可用 → 需真实 GUI 会话）。

### P1 — 功能完整
5. **C3**：本机 WiFi 密码 → 禁用提示 或 `security` 重写。
6. **C4**：右键哈希 → 应用内按钮 / Finder 快速操作。
7. **C1**：剪贴板写设备跨平台化。
8. **C6**：macOS 上验证 pyzbar 扫码（装 zbar 或捆绑 dylib）。

### P2 — 打磨 / 发布（仅"发给别人"才需要，自用可跳过）
9. **C2**：无边框窗口 macOS 视觉微调。
10. **C5**：若分发给他人 → `codesign` + `notarytool` 公证（需 $99/年账号）；自用则跳过，ad-hoc 签名可选。

---

## 六、工作量与风险估计

- **代码改动量小**：真正阻断的只有 `MD5对话框` 一处守卫 + adb 定位 + 打包脚本；核心 ADB/UI 层已跨平台。预计 **1–2 天**代码改动。
- **最大风险不在代码，而在**：
  1. 捆绑 `adb` 的体积（~15MB）与许可证（platform-tools 可再分发，但需保留 NOTICE）；
  2. ~~macOS 签名公证需要 Apple 开发者账号（$99/年）~~ **更正**：仅"分发给他人"才需要；自用本机跑不需要任何账号、不需要公证（详见 C5）。本机打包自用的最大成本其实是"有一台 mac 构建机"，不是账号钱；
  3. 无边框窗口在 macOS 的细节体验需真机调；
  4. **本机无 macOS 真机** → 我只能静态分析 + 在 Windows 上模拟 `darwin` 分支逻辑做有限验证；最终必须在 mac 上跑打包 + 冒烟。
- **pyzbar / scrcpy 在 macOS 的二进制捆绑**需在 macOS 构建机处理，Windows 侧无法完全验证。

---

## 七、建议

**结论：迁移 macOS 可行性高。** 核心代码已大半跨平台，主要工作量集中在 5 件事：
1. 修 `MD5对话框` 的 `winreg` 硬崩；
2. 捆绑 / 定位 macOS `adb`；
3. 补全 mac 打包脚本（`--add-data` + macOS 瘦身）；
4. 本机 WiFi 密码功能：禁用提示 或 用 `security` 重写；
5. 右键哈希：换 Finder 快速操作 / 应用内按钮触发。

建议按 **P0 → P1 → P2** 推进，并在一台 macOS 真机上完成首次打包与冒烟验证。

> 注：本报告为只读扫描产物，未修改任何源码。如需我按上述方案落地代码，请另行指示（届时再逐文件改造并真机验证）。
