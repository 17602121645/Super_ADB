# 「安装/解包」功能介绍

> 适用版本：Super_ADB Main 2026-08-07+
> 模块位置：`Super_ADB_Main/install_zip_dialog.py`
> 关联文件：`adb_utils.py` (`AdbDeviceOps.install_apk`)、`axml_decoder.py`、`popup_style.py`

---

## 一、功能概览

「安装/解包」是一个独立弹窗，模仿 **Android Studio APK Analyzer** 的形态，把"安装 APK 到设备"和"浏览/解包 APK 内部资源"这两件原本要切换多个工具的事，整合到一个面板里。

打开后能干三件事：

1. **浏览包内容** —— 拖入 APK / ZIP / AAR / JAR 后，左侧树形展示包内全部条目（按目录分层），点击文件在右侧查看内容。
2. **安装到设备** —— 一键 `adb install` 到主窗口当前选中的设备，可选 `-r / -t / -d / -g` 四个常用参数。
3. **批量解包** —— 把包内所有文件提取到本地目录（默认 `<包名>_extracted/`），方便二次分析或 diff。

简单说：**「我有个 APK」** → **「看它里面啥 → 装到设备 → 或者拆出来」**，全程一个弹窗搞定。

---

## 二、入口与触发

- **位置**：主窗口左侧「应用操作」区，**「安装/解包」** 按钮（紧邻「卸载」「path/pi...」）。
- **行为**：点击后在主窗口之上弹出独立 `QDialog` 窗口。
- **重复点击**：再次点击同一按钮时，如果窗口已开，会 `raise_()` + `activateWindow()` 把它前置，不会重复创建。

```python
# Super_ADB_Main.py:815
def open_install_dialog(self):
    if self._install_dialog is not None and self._install_dialog.isVisible():
        self._install_dialog.raise_()
        self._install_dialog.activateWindow()
        return
    self._install_dialog = InstallZipDialog(self.adb, self.current_serial, parent=self)
    self._install_dialog.show()
```

---

## 三、界面布局

弹窗默认 760 × 560，深色主题 + 青绿色高亮边框（与项目所有弹窗统一风格），从上到下分为 5 段：

```
┌─ 安装 / 解包 ─────────────────────────────────────────────┐
│  ┌──────── 拖拽区（虚线框，可点击） ────────┐              │
│  │  拖拽 APK / ZIP 安装包到此处              │              │
│  │  （或点击选择文件）                        │              │
│  └──────────────────────────────────────────┘              │
│  文件名 (3.18 MB) · 共 1096 个文件 · 点击文件夹展开       │
│  ┌──────────┬──────────────────────────┐                  │
│  │ 文件 │ 大 │                            │                  │
│  │ 📄 AndroidManifest.xml  9.8 KB        │  XML 文本预览   │
│  │ 📄 DebugProbesKotlin.bin 1.7 KB       │  ……             │
│  │ 📁 META-INF/                          │                  │
│  │ 📁 assets/                            │                  │
│  │ 🟢 classes.dex  3.38 MB               │                  │
│  │ 📁 kotlin/                            │                  │
│  │ 📄 kotlin-tooling-metadata.json 627 B │                  │
│  └──────────┴──────────────────────────┘                  │
│  ☑ -r 替换已安装  ☑ -t 允许测试包  □ -d 允许降级  □ -g 授予权限 │
│                            [ 解包/提取 ]  [ 安装 ]  [ 关闭 ]   │
│  ┌──── 日志区（最大 96px 高） ─────────────────────────┐   │
│  │ → adb -s 9abc install -r -t xxx.apk                 │   │
│  │ [returncode=0] Success                              │   │
│  └────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────┘
```

### 关键 UI 细节

- **拖拽区**：悬停时边框由灰转青绿，松开时还原；松开后自动触发打开。
- **文件类型徽标**：左侧树形控件上每种文件类型有专属颜色的小徽标（24×16 圆角矩形），比如 XML=蓝、DEX=绿、JSON=浅蓝、SO=橙、CERT=黄、DB=青。带缓存避免重复绘制。
- **解包/安装/关闭** 三个按钮：未加载包时安装/解包为禁用；安装按钮是高亮（青绿实心）的主操作。
- **绿色高亮边框** + **外发光** 来自 `popup_style.py` 的 `HIGHLIGHT_CARD_STYLE` + `add_green_glow()`，与「关于」等其他弹窗保持视觉一致。

---

## 四、支持的文件类型

| 扩展名      | 类型   | 加载方式                | 可浏览 | 可安装 | 可解包 |
|-------------|--------|-------------------------|--------|--------|--------|
| `.apk`      | ZIP    | 异步线程打开             | ✅     | ✅     | ✅     |
| `.zip`      | ZIP    | 异步线程打开             | ✅     | ❌     | ✅     |
| `.aar`      | ZIP    | 异步线程打开             | ✅     | ❌     | ✅     |
| `.jar`      | ZIP    | 异步线程打开             | ✅     | ❌     | ✅     |
| 其它任意文件 | 非 ZIP | 标记为「非 zip 包」      | ❌     | ✅     | ❌     |

> 非 zip 包（如 `.so`、`.img` 单文件）拖进来仍可以直接点「安装」走 `adb install`，但左侧不建树。

---

## 五、内容预览（核心亮点）

点左侧文件 → 右侧立即按内容类型走不同渲染路径：

### 1. 文本类（`.txt` `.json` `.html` `.md` `.properties` 等）

- 走 `utf-8 → gb18030 → latin-1` 三段式解码（中文包体常见 utf-8 解析失败，自动回退 gb18030 兜底）。
- 启发式判断：取前 1024 字节统计可打印字符比例，>70% 视为文本；含 `\x00` 一律判为二进制。
- 单文件 > 200 KB 时**只读前 200 KB** 并在底部追加提示，避免大 JSON / log 撑爆 UI。
- 解码后 > 200 000 字符会截断。

### 2. Android Binary XML（**关键能力**）

- APK 里的 `AndroidManifest.xml`、`res/*.xml` 在磁盘上其实是 **AXML 二进制**（Android 编译时把 XML 压成 AXML），用记事本/VSCode 直接打开全是乱码。
- 本工具用自研的 `axml_decoder.py`（2026-08-07 修复过 `_StringPool` header 错位、UTF-8 flag 误判等 bug）**直接还原成可读 XML 源码**。
- 解码失败会回退到二进制预览，并在文本前注明失败原因。

```python
# install_zip_dialog.py:591
if ext == '.xml' and is_axml(data):
    try:
        text = decode_axml(data)
        ...
    except Exception as e:
        binary = self._binary_preview(entry, data)
        self.preview.setPlainText(f'Android Binary XML 解码失败: {e}\n...')
```

### 3. 二进制（`.png` `.dex` `.so` `.arsc` `.RSA` 等）

不直接渲染，显示「大小 + 前 256 字节十六进制 + ASCII 镜像」，例如：

```
二进制文件: classes.dex
大小: 3.38 MB

前 256 字节十六进制预览:
00000000  64 65 78 0a 30 33 35 00 11 00 00 00 00 00 00 00  dex.035.........
00000010  70 00 00 00 00 00 00 00 70 00 00 00 00 00 00 00  p.......p.......
...
```

### 4. 其它未知类型

- 命中 `_TEXT_EXT` 白名单 → 当文本看。
- 命中 `_BIN_EXT` 黑名单 → 当二进制看。
- 都不命中 → 启发式判断后归类。

---

## 六、安装功能

### 工作流

1. 加载包后「安装」按钮启用。
2. 点击 → 校验主窗口 `current_serial()` 有选中设备；没有则弹 `QMessageBox` 提示。
3. 根据四个 `QCheckBox` 拼接 `extra_args` 列表。
4. 启动 `TaskThread(QThread)` 后台执行 `AdbDeviceOps.install_apk(serial, path, extra_args, timeout=180)`。
5. 完成后在底部日志区输出 `returncode` + 原始输出；成功弹「安装完成」提示，失败弹「安装失败」并指明看日志。

### 可选参数（与官方 `adb install` 一致）

| 参数 | 说明 | 默认 |
|:---:|:---|:---:|
| `-r` | 替换已安装（reinstall，保留数据） | ✅ 默认勾选 |
| `-t` | 允许测试包（test-only APK）        | ✅ 默认勾选 |
| `-d` | 允许版本降级（downgrade）            | ⬜ |
| `-g` | 安装时自动授予所有运行时权限          | ⬜ |

> `-t` 默认开启是很多公司内测包场景的刚需（test-only APK 装普通 release 设备会失败）。`-r` 默认开启方便覆盖重测。

### 底层命令

```
adb -s <serial> install [-r] [-t] [-d] [-g] <apk_path>
```

由 `AdbDeviceOps.install_apk()` 封装（`adb_utils.py:572`），含空格/中文路径自动加引号（`shell=True`）。

---

## 七、解包功能

### 工作流

1. 加载包后「解包/提取」按钮启用。
2. 点击 → `QFileDialog.getExistingDirectory()` 选目标目录。
3. 输出目录默认 `<目标>/<包名>_extracted/`，同名目录已存在时复用。
4. 后台线程遍历 `_zf.infolist()`，逐个 `makedirs + open + write`。
5. 完成后日志区输出「解包完成，共提取 N 个文件到: 路径」。

### 输出目录结构

```
<目标>/
└── 97ce3112-dfd8-494e-93c8-20a0649f148a_extracted/   ← 来自 APK 文件名
    ├── AndroidManifest.xml
    ├── DebugProbesKotlin.bin
    ├── META-INF/
    │   ├── ...
    ├── assets/
    ├── classes.dex
    ├── kotlin/
    │   └── ...
    └── kotlin-tooling-metadata.json
```

> 解包只解**文件条目**（`is_dir()` 跳过），目录结构由 `os.makedirs(..., exist_ok=True)` 自动建立。

---

## 八、性能优化（重点）

这个弹窗踩过几次明显的卡顿坑，都已修掉：

### 1. 大 APK 拖入卡死（>3000 文件）

- **`LoadPackageThread`**：把 `zipfile.ZipFile(path).infolist()` 放后台线程跑，主线程只 set 等待光标。
- **`BuildTreeThread`**：在子线程把 `entries` 整理成嵌套 `dict`（仅 dict，不创建任何 GUI 对象），主线程拿到 dict 后再创建 `QTreeWidgetItem`。
- **分批建树**（`_populate_batch` 100 条/帧，1 ms 让出事件循环）：对超大包分层建树，避免一次性 `for child in children: addChild` 阻塞 UI。
- **懒加载子目录**（`_on_item_expanded` + `_expand_batch` 50 条/帧）：用户点开文件夹箭头时才构建该层子节点。

**实测**：

| 条目数 | 树构建耗时 |
|:---:|:---:|
| 3 005  | 0.165 s |
| 4 779  | 0.66 s  |

### 2. 文件图标重复绘制

`_TYPE_ICONS` 是固定映射，但 `QPainter` 每次绘 24×16 圆角徽标也要开销。`_ICON_CACHE` 缓存 `(label, color) → QIcon`，3000 个文件实际上只画 20+ 次（XML/DEX/TXT/JSON/SO/BIN/CERT/DB 等）。

### 3. 大文本预览卡死

- 启发式 + 扩展名判断后再读数据，避免 100 MB `classes.dex` 被错判为文本后 `decode()` 卡 30 秒。
- `_BIN_EXT` 命中直接走二进制预览分支，零额外解码开销。
- 单文件 > 200 KB 非 XML 走「前 200 KB 截断 + 提示」。

---

## 九、线程模型一览

| 线程类型       | 用途                                              |
|----------------|---------------------------------------------------|
| 主线程 (Qt)    | 全部 UI 交互、QPixmap 绘制、QTreeWidgetItem 创建 |
| `LoadPackageThread` (QThread) | 异步 `zipfile.ZipFile(path).infolist()` |
| `BuildTreeThread` (QThread)   | 异步把 entries 整理成目录树 dict         |
| `TaskThread` (QThread)        | 后台执行 `adb install` 或批量解包       |

所有后台线程都通过 `Signal` 把结果交回主线程，**绝不跨线程碰 UI**。

---

## 十、代码结构速查

| 文件 | 关键内容 |
|---|---|
| `install_zip_dialog.py` | `DropArea`（拖拽区）、`TaskThread`/`LoadPackageThread`/`BuildTreeThread`（3 个 QThread）、`InstallZipDialog`（主对话框，800+ 行） |
| `adb_utils.py:572`     | `AdbDeviceOps.install_apk(serial, apk_path, extra_args, timeout)` |
| `axml_decoder.py`      | AXML 二进制 XML 解码器（`_StringPool`、`decode_axml`、`is_axml`） |
| `popup_style.py`       | `HIGHLIGHT_CARD_STYLE` + `add_green_glow()` 弹窗统一高亮 |
| `Super_ADB_Main.py:815` | `open_install_dialog()` 入口 |
| `界面样式.py`           | `ACCENT`、`FONT_FAMILY` 常量（青绿主题色 + 字体） |

---

## 十一、边界与限制

| 场景 | 行为 |
|---|---|
| 拖入非 zip 文件（`.txt` `.so`） | 标记为「非 zip 包，无法浏览内部文件」，但「安装」按钮仍可点 |
| 拖入损坏 zip | 弹 `QMessageBox.warning('打开失败')` |
| 没有任何已选设备就点安装 | 弹「未选择设备」提示 |
| AXML 解码失败 | 文本区先显示错误原因，下面回退十六进制预览 |
| 二进制超大文件 | 只读前 200 KB（XML 除外，结构必须完整） |
| 文件树极深、条目极多 | 子目录懒加载 + 分批插入，单次不超过 50 项 |
| 路径含空格 / 中文 | `_cmd_str` 自动加引号；AXML 解码器支持 utf-8 / gb18030 双兜底 |
| 重复点「安装/解包」按钮 | 当前任务未完成时按钮 disabled，避免并发 |

---

## 十二、快速用例

### 用例 1：拿到一个陌生 APK 想知道它要什么权限

1. 把 APK 拖入弹窗。
2. 左侧点 `AndroidManifest.xml`。
3. 右侧直接看到解码后的 XML，搜 `uses-permission` 即可列出全部权限。

### 用例 2：装一个公司内测 test-only 包

1. 拖入 `.apk`。
2. 确认 `-r`、`-t` 已勾选（默认就是）。
3. 点「安装」。

### 用例 3：对比两个版本的资源差异

1. 把 v1 APK 拖入 → 点「解包/提取」选目录，得到 `v1_extracted/`。
2. 关弹窗，再把 v2 APK 拖入 → 解包到同目录上层，得到 `v2_extracted/`。
3. 用任意 diff 工具（Beyond Compare、VSCode）对比两份目录。

### 用例 4：提取 DEX 用来做反编译

1. 拖入 APK → 点 `classes.dex` 看二进制头确认是 DEX。
2. 点「解包/提取」→ 得到 `xxx_extracted/classes.dex`。
3. 用 jadx / Ghidra / Android逆向助手 进一步分析。

---

## 十三、未来可扩展点（idea，未实现）

- [ ] 文件搜索框（按名字过滤树）
- [ ] APK 元信息卡片（applicationId / versionCode / minSdk / targetSdk 一键汇总）
- [ ] 签名证书解析（`META-INF/*.RSA` 提取签发者、有效期、指纹）
- [ ] 多 APK 批量安装队列
- [ ] 解包后自动用 jadx 命令调用反编译 classes.dex
- [ ] 拖入多个文件时显示队列，按顺序逐个安装

---

> 📌 文档版本 v1 · 2026-08-08 · 悠悠整理 🐱
