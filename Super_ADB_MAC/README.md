# Super_ADB 使用说明（macOS）

Android 调试（ADB）整合桌面工具，基于 Python + PySide6，支持 USB 直连、无线调试、文件管理、日志查看、投屏（scrcpy）等。

---

## 一、直接运行已打包版本（给使用者）

1. 拿到 `build_tools/dist/Super_ADB_mac.zip`，解压得到 `Super_ADB_MAC.app`。
2. 双击 `Super_ADB_MAC.app` 启动。
3. 若 macOS 提示「无法验证开发者 / 已损坏」：
   - 右键 `Super_ADB_MAC.app` → 「打开」；或在
     「系统设置 → 隐私与安全性」中点击「仍要打开」。
   - 本包为 ad-hoc 自签名，无需额外证书。
4. 连接手机：开启「USB 调试」（USB 直连）或「无线调试」（配对码），即可在设备列表看到。

> 注：包内 `config/` 目录（含 `build_info.json`、用户配置）使用中文名，已通过专用打包流程确保不被丢弃。

---

## 二、源码运行（开发 / 调试）

使用项目固定构建 Python（已装齐依赖）：

```bash
/Users/guolai/.workbuddy/binaries/python/envs/default/bin/python3 \
    app/main.py
```

需本机已安装第五节全部「必需」依赖，且（开发期）系统装有 `zbar` 库供 `pyzbar` 使用。

---

## 三、如何打包（构建者）

**一条命令即可完成全部流程**：

```bash
bash build_tools/build_mac_zip.sh
```

脚本会自动：安装/校验依赖 → 执行 `pyinstaller build_tools/Super_ADB_mac.spec`
→ 写入 `config/build_info.json` → Qt 冗余裁剪 → 生成 UTF-8 安全的 zip。

产物：

- `build_tools/dist/Super_ADB_mac.zip`（分发用，约 63MB）
- `build_tools/dist/Super_ADB_MAC.app`（未压缩的 .app）

**唯一配置源**：`build_tools/Super_ADB_mac.spec`
（所有隐藏依赖、libusb 随包、运行时钩子都在这一份 spec 里声明，不要在别处再写内联 pyinstaller 命令）。

> 中文目录坑：macOS 自带 `ditto`/`zip` 会静默丢弃非 ASCII 目录名（如 `配置`），
> 因此打包使用 `build_tools/make_zip.py`（Python `zipfile`）替代。

---

## 四、依赖哪些库

构建 / 运行环境（Python 3.13.12，macOS arm64）：

| 库 | 版本 | 用途 | 是否必需 |
| --- | --- | --- | --- |
| PySide6 | 6.11.1 | GUI（Qt6 界面、信号槽、托盘、主题） | 必需 |
| cryptography | 50.0.1 | 无线调试配对握手（x509 / TLS），缺失会导致配对手机一直转圈 | 必需 |
| pyusb | 1.3.1 | USB 直连的 Python 后端 | 必需 |
| libusb | 1.0.29.post7 | USB 底层动态库，**随包内置**（目标机无需安装） | 必需（构建期） |
| pyzbar | 0.1.9 | 二维码解码（扫码连接 / 识别） | 必需 |
| segno | 1.6.6 | 二维码生成（配对码展示） | 必需 |
| zeroconf | 0.150.0 | 无线调试 mDNS 服务发现 | 必需 |
| ifaddr | 0.2.0 | 网络接口枚举（配合 zeroconf） | 必需 |
| brotli | （可选） | HTTP `Content-Encoding: br` 解压；未安装时源码 try/except 跳过 | 可选 |

补充（打包相关，非运行时导入）：
- 构建 Python：`/Users/guolai/.workbuddy/binaries/python/envs/default/bin/python3`
- 开发期 `pyzbar` 需要系统 `zbar` 库（`brew install zbar`）；打包版已把 `libzbar.dylib` 随包。
- 已排除的死重：`numpy`、`cv2`、`pyzbar.tests`、`PIL._avif/_webp/_imagingtk`、
  `unicodedata`、`zstandard`、`_zstd`、`_decimal`、`PIL._imagingcms`（省体积且不影响功能）。

---

## 五、已知问题与修复记录

- **配对手机一直转圈**：构建机必须安装 `cryptography`，否则 `Hidden import
  'cryptography.hazmat' not found` 被静默跳过 → 配对客户端 `from cryptography
  import x509` 直接 ImportError → 握手不发。已把 23 项隐藏依赖同步进 spec。
- **打包后启动报 `ModuleNotFoundError: No module named 'png_rc'`**：
  源码里对子包模块用了「裸导入」（`import png_rc` / `import adb_tools` /
  `from favorite_combobox import FavComboBox` 等），开发期靠把 `tools/`、`ui/`
  加入 `sys.path` 解析，但冻结后这些目录不是物理目录。已通过运行时钩子
  `build_tools/hooks/runtime_pkg_alias.py` 把裸名映射到已收集的包限定模块修复。
- **解压后 `config/` 目录丢失**：改用 `build_tools/make_zip.py` 替代 `ditto`。
- **USB 直连离线不可用**：`build_tools/hooks/runtime_libusb.py` 把
  `ctypes.util.find_library('usb-1.0')` 重定向到随包 `libusb-1.0.dylib`。

---

## 六、目录速查

```
Super_ADB_MAC/
├── app/main.py   # 源码入口
├── build_tools/
│   ├── Super_ADB_mac.spec             # ★ 唯一打包配置源
│   ├── build_mac_zip.sh               # ★ 一键打包入口
│   ├── make_zip.py                    # UTF-8 安全 zip 工具
│   └── hooks/                         # 运行时钩子（libusb / pyzbar / 裸导入别名）
├── resources/                              # 图标等
├── vendor/                          # adb platform-tools 等
├── tools/ ui/ dialogs/ pages/ monitoring/ scripts/
└── 使用说明.md                        # 本文件
```
