# Super_ADB — macOS 兼容性扫描报告与改造方案

> 范围：扫描 `Super_ADB_Main/` 全部功能，判断 macOS 兼容性，给出改造方案与最新落地状态。
> 工作副本：`Super_ADB_MAC/Super_ADB/`（2026-08-13 提交 `e5f1d2a` 推送，已含 macOS platform-tools）。
> 原则：**plan → 落地状态双向核对**。

---

## 一、总体结论

Super_ADB 的代码骨架**已具备较高的跨平台基础**（作者之前已埋 `darwin` 分支）：

- `adb_utils.py`：配置路径（`~/Library/Application Support/Super_ADB/`）、scrcpy 子目录探测（`scrcpy-mac-` 前缀）、shell 执行策略都已按 `darwin/linux/win32` 分支处理。
- `ui_styles.py`：字体已按平台选择（macOS → `PingFang SC`），6 套主题字典对所有平台通用。
- `Super_ADB_Main.py`：单实例用 `QLocalServer`/`QLocalSocket`（跨平台）；无边框窗口用鼠标事件拖动。
- `pyinstall_y.py`：已有 `darwin` 分支，会生成 `.app`，且 `add_data_sep = ';' if win32 else ':'` 已正确处理。
- `requirements.txt`：PySide6 / Pillow / segno / zeroconf / ifaddr 全部跨平台。

**目前仍有 1 个硬崩溃点 + 2 个 Windows 专属功能不可用**。剩余阻断都需要 macOS 真机构建机验证 + 单独实施。

---

## 二、功能兼容性逐块评估

| # | 功能模块 | 文件 | macOS 结论 | 说明 |
|---|---------|------|-----------|------|
| 1 | ADB 命令封装 | `adb_utils.py` | ✅ 核心可用 | 已含 darwin 分支；`adb` 二进制来源见 B3 |
| 2 | 设备扫描/连接/配对 | `adb_utils.py` | ✅ | 纯 `adb` 命令 |
| 3 | 设备信息 / OAID / MAC | `adb_utils.py` | ✅ | 设备端 shell 脚本，跨平台 |
| 4 | 截图 / 录屏 | `adb_utils.py` | ✅ | 落 `~/Desktop`（macOS 有桌面目录） |
| 5 | 文件管理 | `file_manager_page.py` | ✅ | `adb push/pull` |
| 6 | 应用管理（启停/装/卸） | `adb_utils.py` | ✅ | `am`/`pm`/`monkey`；`AdbDeviceOps.install` 已统三阶段 push→pm→rm |
| 7 | Monkey 压测 | `monkey_stress_window.py` | ✅ | `adb shell monkey` |
| 8 | 日志抓取 logcat | `log_viewer_page.py` | ✅ | `QProcess` 流式 |
| 9 | tcpdump 抓包 | `tcpdump_dialog.py` | ✅ | **设备端** `tcpdump`，落 `~/Desktop/Super_ADB/`；纯 PySide6 |
| 10 | 性能监控（设备/应用） | `device_performance_monitor.py` / `app_performance_monitor.py` | ✅ | `dumpsys` 跨平台；`tools/chart_js.py` 内联图表库避免 CDN |
| 11 | WiFi 配对 / 扫码连接 | `wifi_pair_dialog.py` / `qrcode_connect_page.py` | ✅ | 二维码走 `pyzbar`（需 zbar，见 C6） |
| 12 | 局域网扫描发现 | `lan_scan_dialog.py` | ✅ | `zeroconf`/`ifaddr` 跨平台 |
| 13 | 二维码生成 | `segno` | ✅ | 纯 Python |
| 14 | JSON 工具 | `json_tool_dialog.py` | ✅ | 纯 PySide6；新增「字典互转」 Tab |
| 15 | 投屏 scrcpy | `adb_utils.py` | ✅ 已有 darwin 分支 | `adb_utils.scrcpy()` 自动探测 `data/scrcpy/scrcpy-mac-vX.Y/`；参数 `extra_args` 透传完全覆盖 |
| 16 | 单实例 / 主窗口 / 托盘 | `Super_ADB_Main.py` | ⚠️ 需实测 | 逻辑跨平台；无边框窗口在 macOS 的拖动/阴影需真机微调；托盘走菜单栏 |
| 17 | **本机 WiFi 密码查看** | `wifi_tools.py` + `wifi_dialog.py` | ❌ 不可用 | 依赖 Windows `netsh wlan`；macOS 无此命令；`diagnose()` 已返回"不支持"，但主功能仍会抛 `RuntimeError` |
| 18 | **计算哈希 + 右键菜单** | `MD5对话框.py` + `hash_context_menu.py` | ❌ **硬崩** | `MD5对话框.py:27` **仍为无条件 `import winreg`**（⚠️ 截至 2026-08-20 未修，仍是 P0 阻断） |
| 19 | 剪贴板写设备 | `Super_ADB_Main.py:229-249` | ⚠️ 已降级 | `ctypes.windll.kernel32/user32` 在 `try` 内，macOS 抛 `AttributeError` 被捕获→功能失效但不崩 |
| 20 | **只读分区 disable-verity 流程** | `adb_utils.py` + `push_stream` | ⚠️ Windows only | `root_and_remount` 自动跑 disable-verity 流程仅限真机 userdebug；macOS 实操不常用（macOS 是开发机，不连模拟器 push） |
| 21 | **WiFi 密码审计** | `tools/wifi_password_cracker.py` | ⚠️ 仅 recover/crypt 不可用 | `crack` 是 WPA PMKID 模式，跨平台；`recover` 走 netsh，恢复本机已存 WiFi 密码仅 Windows |
| 22 | **桌面宠物小猫** | `desk_cat.py` | ✅ | 纯 PySide6 + qrc 资源，跨平台 |

---

## 三、阻断级问题（必须修，否则跑不起来 / 打开即崩）

### ✅ 已修复（2026-08-13 → 2026-08-20 期间落地）

#### B2. 打包脚本 `pyinstall_y.py` 的 darwin 分支不完整
- **原现象**：
  1. 没有 `--add-data` 把 `data/scrcpy-mac-v2.6`、图标资源、adb 打进 `.app`；
  2. `trim_qt.main()` 仅对 Windows 有效（依赖 `*.pyd`，macOS 为 `*.dylib`，安全 abort → 构建不瘦身，体积巨大）。
- **修复落地（`e5f1d2a`, 2026-08-13）**：
  - `add_data_sep = ';' if sys.platform == 'win32' else ':'` 已正确处理；
  - `pyinstall_y.py` 第 75 行条件 `--add-data "data:data"`（仅当 `Super_ADB_Main/data/` 存在时）；
  - darwin 分支生成 `.app` 时使用 `.icns`（如有）或退回 `.png`；
  - macOS 体积裁剪：未做 trim_qt 等价物——`.dylib` 路径不同，需要新建 `trim_qt_mac.py`，**P1 待办**。
- **macOS adb 来源**：已在 `Super_ADB_MAC/Super_ADB/platform-tools/` 预置 macOS 版 platform-tools；主项目尚未捆绑，因此**B3 仍未解决**（需在 macOS 构建机上把 platform-tools 拷贝到 `Super_ADB_Main/data/adb/macosx/` 并修改 `adb_utils.scrcpy()` 同款探测逻辑）。

#### B3. `macOS_compatibility_plan.md`（自身）
- **状态**：本文件已作为改造追溯与决策记录保留，仍指导未完成的 mac 兼容性工作（如 B1 / B3 / C3 / C6）。

### ❌ 待修复（按优先级排列）

#### B1. `MD5对话框.py` 顶部无条件 `import winreg`（**仍为硬崩溃**）

- **现象**：`MD5对话框.py:27` 写的是 `import winreg`（无 try/except 守卫），macOS 上只要打开「计算哈希 / MD5」功能（懒加载 `MD5对话框`），`import winreg` 失败 → 整个功能崩溃。
- **当前现状**：截至 2026-08-20 commit `3ec5436`，**仍未修复**。
- **修复方案**（仍适用）：
  ```python
  try:
      import winreg
  except ImportError:
      winreg = None
  ```
  并把 `_install_ctx_menu()` / `_uninstall_ctx_menu()` 中 `winreg.*` 调用包一层 `if winreg is not None:`；非 Windows 时这两个方法禁用并在 UI 提示"右键菜单仅 Windows 支持"。
- **影响面**：MD5 弹窗的所有功能在 macOS 上**完全不可用**（点击即崩）。其它弹窗（监控/日志/文件等）不受影响。

---

## 四、按需改造 / 优化（非阻断）

### C1. 剪贴板写设备跨平台化（`Super_ADB_Main.py:229-249`）
- 当前用 Win32 API 写设备剪贴板，macOS 走 `except` 降级（失效但不崩）。
- 改为：Windows 走原 Win32 路径；非 Windows 用 `QGuiApplication.clipboard().setText(text)`（Qt 跨平台）。
- **状态**：未修复（不影响功能可用性）。

### C2. 无边框窗口 macOS 视觉微调
- frameless + 半透明在 macOS 上可显示，但窗口阴影、圆角、全屏/分屏（Spaces）行为需在真机验证；必要时用 `QtWidgets` 设置 `WA_TranslucentBackground` 或调整 `setWindowFlags`。**非阻断**。

### C3. 本机 WiFi 密码功能（方案二选一）
- **禁用 + 提示**（最小改动）：`WiFi对话框` 在非 Windows 时禁用入口按钮，打开即提示"本机 WiFi 密码查看仅 Windows 支持（依赖 netsh）"。
- **macOS 重写**（功能完整）：用 `security find-generic-password -D "AirPort network password" -a <ssid> -w` 读取 Keychain 中的 WiFi 密码，替换 `wifi_tools.py` 的 netsh 实现（新增 `wifi_utils_mac.py` 或按平台分支）。注意：macOS 读 Keychain 需用户授权（首次弹 Touch ID / 密码）。
- **状态**：未实现，已在 `WiFi工具.diagnose()` 返回「不支持」时给用户友好提示。

### C4. 右键「计算哈希」触发方式（macOS 对应实现）
- Windows 用注册表 `HKCU\Software\Classes\*\shell`；macOS 无等价注册表。
- 替代方案（任选）：
  1. 应用内提供「计算哈希」入口按钮（已有 `Md5Dialog`，跨平台可用）；
  2. 制作 **Finder 快速操作（Quick Action / Automator）** 或 **Service**，把选中文件传给 `Super_ADB.app --hash <paths>`（主程序已支持 `--hash` 参数）；
  3. 复用已有的 `HashContextDialog`（纯 PySide6，跨平台），只换触发源。
- **状态**：仍待方案选择（待 B1 修好后此问题方可评估）。

### C5. macOS 签名 / 公证（仅"分发给别人"才需要，自用免）
- **自用场景（本机跑）不需要付费账号、不需要公证**：`pyinstaller` 打出来的 `.app` 不带 `com.apple.quarantine` 隔离标记，Gatekeeper 不拦，没签名也能直接跑。
- **可选省心**：`codesign --force --deep --sign -`（ad-hoc 签名，Xcode 命令行工具自带）可避开偶尔的"app 已损坏"提示。
- **分发**：跨机器 Gatekeeper 强制要求 Developer ID 证书 + `notarytool` 公证（需 $99/年付费 Apple 开发者账号）。

### C6. pyzbar 在 macOS 的动态库（构建期风险）
- `hook-pyzbar.py` 用 `collect_dynamic_libs('pyzbar')` 收集 `libzbar`/`libiconv`。macOS 上为 `.dylib`，本机可能未装 `zbar` 系统库。
- **修复路径**：
  - 在 macOS 构建机上 `brew install zbar`；
  - 或显式捆绑 `libzbar.dylib`；
  - 或在 hook 中补充 darwin 路径。
- **状态**：未验证（需 macOS 真机构建）。

---

## 五、改造步骤（按优先级 + 落地状态）

### P0 — 必须（否则无法在 macOS 运行 / 打开即崩）

| # | 任务 | 落地状态 | 修复细节 |
|---|------|---------|---------|
| ~~B1~~ | ~~`MD5对话框.py` 守卫 `import winreg`~~ | ❌ **未修复** | macOS 上点 MD5 按钮仍会硬崩 |
| ~~B3~~ | ~~捆绑 macOS `adb`~~ | ⚠️ 部分：macOS 子项目 `Super_ADB_MAC/Super_ADB/platform-tools/` 已就位；主项目未捆绑 | 需 macOS 真机构建机拷贝 `platform-tools` 到 `Super_ADB_Main/data/adb/macosx/`，并在 `adb_utils.py` 增加 darwin 探测分支（参考 `scrcpy()` 的实现） |
| ~~B2~~ | ~~`pyinstall_y.py` darwin 分支补全~~ | ✅ 已修复（`e5f1d2a`） | darwin 分支已生成 `.app`，图标 + scrcpy 二进制 + resources 已 `--add-data` |
| 4 | macOS 真机构建验证 | ⚠️ **本机无 macOS 真机** | 用户在 Windows 静态分析 + 模拟 `darwin` 分支逻辑做有限验证；最终需 mac 真机构建 + 冒烟 |

### P1 — 功能完整

| # | 任务 | 落地状态 |
|---|------|---------|
| 5 | C3：本机 WiFi 密码 → 禁用提示 或 `security` 重写 | ⚠️ 未实施 |
| 6 | C4：右键哈希 → 应用内按钮 / Finder 快速操作 | ⚠️ 待 B1 修复后评估 |
| 7 | C1：剪贴板写设备跨平台化 | ⚠️ 未实施（不影响功能） |
| 8 | C6：macOS 上验证 pyzbar 扫码 | ⚠️ 未验证 |

### P2 — 打磨 / 发布（仅"发给别人"才需要，自用可跳过）

| # | 任务 | 落地状态 |
|---|------|---------|
| 9 | C2：无边框窗口 macOS 视觉微调 | ⚠️ 待真机验证 |
| 10 | C5：若分发 → `codesign` + `notarytool` 公证 | 可选；ad-hoc 签名即够自用 |

### 已完成（2026-08-13 → 2026-08-20 期间随主线改动隐式修复）

| 任务 | 提交 | 说明 |
|------|------|------|
| scrcpy darwin 分支 + 二进制自动探测 | `e5f1d2a` | `adb_utils.scrcpy()` 用 `prefix_map = {'darwin': 'scrcpy-mac-', ...}` 自动选目录 |
| 配置路径 darwin 分支 | `e5f1d2a` | `_config_path` 在 darwin 冻结态走 `~/Library/Application Support/Super_ADB/` |
| Super_ADB_MAC 子项目 | `e5f1d2a` | 完整 macOS 适配版已推 `super_adb.git/master` |
| 桌面宠物小猫 | `dffe8bb` | 纯 PySide6 + qrc，跨平台 |

---

## 六、工作量与风险估计

- **代码改动量小**：真正阻断的只有 B1（`MD5对话框` 守卫）一处；B3（B2 已修）需 macOS 真机做的体力活（拷 platform-tools），代码改动参考 `scrcpy()` 的探测模式即可。预计 **1–2 天**代码改动。
- **最大风险仍在外部依赖**：
  1. macOS 真机构建机（**目前缺失**）；
  2. 捆绑 `adb` 的体积（macOS platform-tools ~15MB）与许可证（platform-tools 可再分发，但需保留 NOTICE）；
  3. 无边框窗口在 macOS 的细节体验需真机调；
  4. **本机无 macOS 真机** → 我只能静态分析 + 在 Windows 上模拟 `darwin` 分支逻辑做有限验证；最终必须在 mac 上跑打包 + 冒烟。
- **pyzbar / scrcpy 在 macOS 的二进制捆绑**需在 macOS 构建机处理，Windows 侧无法完全验证。

---

## 七、建议

**结论：迁移 macOS 可行性高，进度约 85%。** 核心代码已大半跨平台；剩余工作集中在 3 件事：

1. **修 `MD5对话框.py` 的 `import winreg` 守卫**（10 行代码，1 个文件）；
2. **在 macOS 真机构建机上**：
   - 把 `Super_ADB_MAC/Super_ADB/platform-tools/` 拷到 `Super_ADB_Main/data/adb/macosx/`；
   - 在 `adb_utils.py` 加 `adb` 探测分支（同 `scrcpy()` 模式）；
   - 跑 `python pyinstall_y.py` → 产出 `.app` → 启动冒烟。
3. 选 `C3 / C4 / C6` 中需要的实施。

建议按 **P0 → P1 → P2** 推进，并在一台 macOS 真机上完成首次打包与冒烟验证。

> 注：本报告为扫描 + 改造追踪文档，未修改任何源码时**仅更新本文件**；如需我按上述方案落地代码，请明确指示（届时再逐文件改造并在 mac 真机验证）。
