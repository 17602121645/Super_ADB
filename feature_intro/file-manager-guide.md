# 「文件管理器」功能介绍

> 适用版本：Super_ADB Main 2026-08-07+
> 模块位置：`Super_ADB_Main/file_manager_page.py`
> 关联文件：`adb_utils.py` (`AdbFileManager`)

---

## 一、功能概览

文件管理器是 Super_ADB 主窗口的一个**内嵌子页面**（不是弹窗），通过右侧分屏（QSplitter）显示，提供 5 件套能力：

1. **浏览设备文件系统** —— 树形结构，按目录懒加载展开
2. **上传文件** —— 从本机推送任意文件到设备
3. **下载文件/目录** —— 把设备上的文件或目录拉回本机
4. **重命名** —— 在线改文件名
5. **删除** —— 删文件/目录（带二次确认）

根目录可一键在 **`/`**（系统根）和 **`/sdcard`**（用户存储）之间切换，避免无 root 设备访问受限目录时一直报错。

设计目标：**adb 文件管理常用操作一站式完成**，不用切到命令行。

---

## 二、入口与触发

- **位置**：主窗口右侧分屏的「文件管理」标签页（与日志、性能监控等并列）
- **首次进入**：`FileManagerPage` 子页面构造时会自动 `check_adb()` + `scan_devices()` 拉一次设备列表
- **设备同步**：主窗口在「连接/刷新设备」时调用 `sync_devices(devices)` 统一更新三处下拉框（日志/文件管理器/性能监控），只在选中设备真正变化时重载根目录，**避免刷新打断浏览**

```python
# file_manager_page.py:295
def sync_devices(self, devices, select_serial=None):
    prev = self.device_combo.currentData()
    self._fill_devices(devices, select_serial)
    new = self.device_combo.currentData()
    if new and new != prev:
        self._on_device()
```

---

## 三、界面布局

顶部工具栏 + 文件树主体，与截图一一对应：

```
┌─────────────────────────────────────────────────────────────────┐
│ 设备:[25102RKBEC [emu] ▼] [刷新设备] [根目录: /]  ─  已加载 /data/data/android.ext.services/ │
├─────────────────────┬──────┬────────┬──────────────────────────┤
│ 名称                │ 大小 │ 权限   │ 修改时间                  │
├─────────────────────┼──────┼────────┼──────────────────────────┤
│ ▶ android           │  —   │ drwx-… │ 2026-07-27 14:05          │
│ ▼ android.ext.servi…│  —   │ drwxr… │ 2026-07-27 14:05          │
│   ▶ cache           │  —   │ drwxr… │ 2026-07-27 14:05          │
│   ▶ code_cache      │  —   │ drwxr… │ 2026-07-27 14:05          │
│   ▶ files           │  —   │ drwxr… │ 2026-07-27 14:05          │
│   ▶ android.ext.sh… │  —   │ drwx-… │ 2026-07-27 14:05          │
│ ▶ cn.miguvideo.mig… │  —   │ drwx-… │ 2026-08-08 01:23          │
│ ▶ com.android.adbk… │  —   │ drwx-… │ 2026-08-06 17:28          │
└─────────────────────┴──────┴────────┴──────────────────────────┘
```

### 关键 UI 细节

- **设备下拉框**：格式 `model [serial]`，由 `format_device_label()` 统一渲染
- **根目录切换按钮**：点击在 `/` ↔ `/sdcard` 间切换，文案实时更新
- **状态栏**：右上角显示当前路径 + 操作进度（「正在扫描设备…」「加载: /data/data…」「上传: x.apk…」）
- **四列表格**：名称 / 大小 / 权限 / 修改时间。文件夹的「大小」显示 `—`，文件显示自适应单位（B/KB/MB/GB/TB）
- **列宽可拖拽** + **占比持久化**：用户拖过的列宽下次启动自动恢复（见第七节）

---

## 四、核心能力详解

### 1. 浏览（懒加载）

- **根节点默认展开**：进入页面或切换设备/根目录时自动展开根节点并触发首层加载
- **按需加载**：用户**点开文件夹箭头**才触发 `adb shell ls -la`，避免一次拉全树
- **加载状态机**（`_on_expanded` + `_loading` set）：
  - 未加载 → 置 LOADED_ROLE=False + 加占位空行
  - 用户展开 → 起 worker → set `_loading` 防并发
  - 加载完成 → `item.removeRows(0, item.rowCount())` 清占位 + 填真实行 + LOADED_ROLE=True
  - 加载失败 → 清占位 + 折叠回 + 状态栏报错
- **每层独立缓存**：`_dir_items[path] → item` 哈希表，让重命名/删除后能精准 `_refresh_dir(parent)` 局部刷新

### 2. 上传（Push）

右键 → 上传文件… → `QFileDialog` 选本机文件 → 后台 `adb push <local> <target_dir>` → 成功后**刷新目标目录**。

```python
# file_manager_page.py:447
def _upload(self):
    if not self._current_serial:
        return
    target = self._target_dir()        # 选中项若是文件夹就用它，否则用父目录
    local, _ = QFileDialog.getOpenFileName(...)
    if not local: return
    self._status(f'上传: {os.path.basename(local)}…')
    w = _CmdWorker(self._mgr.push, self._current_serial, local, target)
    self._track(w,
                on_result=lambda r: (self._status('上传成功'), self._refresh_dir(target)),
                on_error=lambda e: self._status(f'上传失败: {e}'))
```

> **`_target_dir()`**：智能选上传目标 —— 选中文件 → 它的父目录；选中文件夹 → 它本身；没选 → 根目录

### 3. 下载（Pull）

- 选中**文件** → `QFileDialog.getSaveFileName`，默认位置 `~/Desktop/<name>`
- 选中**文件夹** → `QFileDialog.getExistingDirectory`，拉整个目录到本地

### 4. 重命名（Rename）

`QInputDialog` 弹窗输入新名 → 校验非空且与原名不同 → 后台 `adb shell mv <old> <new>` → 刷新父目录。

```python
new_path = parent.rstrip('/') + '/' + new
```

> 暂不支持重命名跨目录移动；想换路径可以先重命名 + 下载/上传组合。

### 5. 删除（Delete）

`QMessageBox.question` 二次确认 → 后台 `adb shell rm -rf <path>` → 刷新父目录。

> 用 `rm -rf` 是因为可能是目录；如果只删文件会被 Android 误报无操作，但 `rm -rf` 对单文件同样安全。

---

## 五、根目录切换

按钮 `根目录: /` ↔ `根目录: /sdcard` 一键切换：

```python
# file_manager_page.py:310
def _toggle_root(self):
    self._root_path = '/' if self._root_path != '/' else '/sdcard'
    self.btn_root.setText(f'根目录: {self._root_path}')
    if self._current_serial:
        self._build_root()
```

**典型场景**：

| 想要访问 | 用 | 原因 |
|---|---|---|
| `/data/data/<pkg>/`、`/system/` 等 | `/` | 系统根，需要 root |
| `/sdcard/`、`/sdcard/Pictures/` | `/sdcard` | 普通 App 也能访问 |
| `/data/local/tmp/` 调试 | `/` | 在 system root 下 |

> 切换会清空整个树模型（`model.clear()` + `model.setHorizontalHeaderLabels(...)`），所以状态栏会先回到 `—`，再重新加载。

---

## 六、线程模型

所有 ADB 调用都走后台线程池，**主线程绝不阻塞**：

| 组件 | 角色 |
|---|---|
| 主线程 (Qt) | UI 交互、QStandardItemModel 创建/刷新、状态栏文本 |
| `QThreadPool` (max 4) | 跑所有 `_CmdWorker` |
| `_CmdWorker(QRunnable)` | 通用 worker，包装 `func(*args, **kwargs)`，setAutoDelete(False) 自己管理生命周期 |
| `_WorkerSignals(QObject)` | result/error/finished 三信号回主线程 |

```python
# file_manager_page.py:39
class _CmdWorker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self.setAutoDelete(False)       # 自己持有引用，由 _track/_drop 管理

    def run(self):
        try:
            r = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()
```

`_live_workers` 列表显式持有每个 worker 引用，等 `finished` 信号来了再 remove，**避免被 QThreadPool 自动 GC 后回调找不到信号源**。

---

## 七、列宽持久化

用户拖列宽改的占比会实时写入 `adb_shell_config.json`，下次启动恢复：

```
存储位置: adb_shell_config.json (主程序目录)
键名:     col_ratios  → [0.4582, 0.0776, 0.1014, 0.24]
```

### 实现要点

```python
# 默认占比（4 列宽度比例：名称 45.8% / 大小 7.8% / 权限 10.1% / 修改时间 24%）
COL_RATIOS = (0.4582, 0.0776, 0.1014, 0.24)

# 启动时读取
def _restore_col_ratios(self):
    ratios = load_json_config(CONFIG_NAME).get('col_ratios')
    if (isinstance(ratios, (list, tuple)) and len(ratios) == 4
            and all(isinstance(v, (int, float)) and v > 0 for v in ratios)):
        self._col_ratios = tuple(float(v) for v in ratios)

# 用户拖动列宽 → 写新占比
def _on_section_resized(self, logical_index, old_w, new_w):
    if self._applying or logical_index >= 3:    # _applying 是程序内调整时屏蔽
        return
    w = self.tree.viewport().width()
    if w <= 0: return
    ratios = list(self._col_ratios)
    ratios[logical_index] = new_w / w
    ...
    QTimer.singleShot(200, self._save_col_ratios)   # 200ms 防抖后落盘
```

- **`_applying` 旗标**：内部 `setColumnWidth` 触发的 `sectionResized` 会跟用户拖动混淆，所以程序主动调宽时置 True 屏蔽
- **窗口 Resize 重新铺满**：监听 `QEvent.Resize` 自动按比例重算列宽，最后一列 Stretch 补齐
- **200ms 防抖**：拖动列宽时高频触发 resize 信号，200ms 后只写一次盘

---

## 八、性能优化

### 1. 懒加载避免一次性拉全树

很多文件管理器会先 `adb shell ls -R /` 把整棵树拉下来，结果：

- root 设备 `/` 下可能有上万条目
- 一次 ls 几秒到十几秒
- 内存压力山大

本工具只对**用户主动展开的目录**触发 ls，且每层独立加载。

### 2. 状态机防并发 + 占位行避免闪烁

```python
item.appendRow(QStandardItem(''))   # 加占位空行
...
# 加载完成
item.removeRows(0, item.rowCount())  # 清占位
```

不先加占位行会导致用户在等待期间「以为这个文件夹是空的」多点几下。

### 3. `_loading` set 防重复触发

用户在加载中又点展开箭头，不会触发第二个 worker。

### 4. 局部刷新而非全局重载

上传/重命名/删除后只 `_refresh_dir(parent)`，树其它分支的展开状态完全保留 —— 用户体验上很重要。

### 5. 智能排序

```python
dirs = sorted([e for e in entries if e['is_dir']], key=lambda e: e['name'].lower())
files = sorted([e for e in entries if not e['is_dir']], key=lambda e: e['name'].lower())
for e in dirs + files:  # 文件夹在前
```

按 name 不区分大小写排序，文件夹永远在文件前面。

---

## 九、设备列表同步策略

主窗口和文件管理器的下拉框是**同一个**（`.ui` 文件里 `device_combo` 是文件管理器自己的，但 `sync_devices()` 接收主窗口给的列表），同步规则：

| 场景 | 行为 |
|---|---|
| 主窗口连接新设备 | 调用 `sync_devices()` → 文件管理器下拉框加新条目 → 选中项变化时**触发 `_on_device()`** 重载根目录 |
| 主窗口刷新设备列表 | 选中设备不变 → `_fill_devices` 后 `new == prev` → **不重载根目录**（避免刷新打断浏览） |
| 用户在文件管理器内切换下拉框 | `_on_device()` 立即生效 |
| `_scan_devices()` 失败 | 状态栏显示 `扫描失败: <错误>` |

---

## 十、代码结构速查

| 文件 | 关键内容 |
|---|---|
| `file_manager_page.py` | `FileManagerPage` 子页面 + `_CmdWorker(QRunnable)` + `_WorkerSignals` |
| `adb_utils.py:673`     | `AdbFileManager(AdbHelper)` —— `list_dir` / `push` / `pull` / `delete_path` / `rename_path` |
| `adb_utils.py:55`      | `format_device_label()` —— 下拉框文案格式 |
| `adb_utils.py:35-48`   | `load_json_config` / `save_json_config` —— 列宽占比持久化 |
| `adb_utils.py:698`     | `_parse_ls_line()` —— 解析 ls -la 多时间格式（Aug 5 14:05 / 2026-07-27 14:05 / 14:05） |
| `adb_shell_config.json` | 列宽占比持久化文件（与其它 Shell 配置共用） |

---

## 十一、边界与限制

| 场景 | 行为 |
|---|---|
| 设备未选 | 工具栏操作全部无效（`_current_serial` 为空） |
| 列表加载中再次展开 | 第二次 `_on_expanded` 直接 return（`_loading` set 已加） |
| 列表失败 | 清占位 + 折叠 + 状态栏报错，不影响其它目录 |
| 上传/下载/重命名/删除失败 | 状态栏 `…失败: <错误>`，不刷新目录（避免误判） |
| 删除目录 | 实际是 `rm -rf`，含子文件；`QMessageBox.question` 二次确认 |
| 文件名含空格/中文 | `adb shell mv "old" "new"` 用引号；底层 `_run` 自动转义 |
| 根目录权限不足（如 `/data/data/` 普通 App） | ls 返回空 + 报错，状态栏提示 |
| 用户拖列宽 | 实时改占比 + 200ms 防抖落盘 |
| 窗口 resize | 自动按当前占比重铺列宽，最后一列 Stretch |
| 跨目录移动 | 不支持 —— 重命名只在同目录换名 |

---

## 十二、快速用例

### 用例 1：抓应用的 SharedPreferences XML

1. 选设备 → 切根目录到 `/`
2. 展开 `/data/data/<pkg>/`
3. 找到 `shared_prefs/` → 右键展开
4. 右键 `settings.xml` → 下载 → 选保存到 `~/Desktop/`
5. 用文本编辑器看 XML 内容

### 用例 2：往设备推一份测试 APK

1. 选设备
2. 切到 `/sdcard/Download/`（或 `/data/local/tmp/`）
3. 右键上传 → 选本机 APK → 上传
4. 状态栏显示「上传成功」+ 列表自动刷新出刚上传的文件

### 用例 3：清理某个 App 的 cache

1. 切到 `/`
2. 展开 `/data/data/<pkg>/cache/`
3. 全选（暂不支持批量选，先单个右键删）→ 删除
4. 父目录刷新后可看到 cache 已经空了

### 用例 4：批量导出日志

1. 切到 `/data/local/tmp/`
2. 展开 logcat 输出目录
3. 选中 `logcat.txt` → 下载 → 选本地目录
4. 状态栏「下载成功」

### 用例 5：调列宽更顺眼

1. 拖「修改时间」列到 200 px →「权限」列到 80 px
2. 关程序重开 → 列宽自动恢复

---

## 十三、本版新增（2026-08-08）

三项原「未来可扩展点」已落地，代码在 `file_manager_page.py` + `adb_utils.py`：

### 1. 文本文件预览（QuickLook 式）

双击 `.xml/.txt/.json/.log/.csv/.conf/.prop/.ini/.md/.yml/.yaml/.gradle/.sh/.bat/.cfg/.properties` 等文本文件，弹出只读预览窗（`TextPreviewDialog`），内容走 `adb pull` 落地临时文件后按 **UTF-8 → GB18030 → latin-1** 稳健解码，中文不乱码；超过 2 MB 自动截断并提示；支持「复制全部」。目录与二进制（apk/dex 等）不触发预览。

```python
# file_manager_page.py
def _on_double_clicked(self, index):
    entry = item.data(Qt.UserRole) or {}
    if entry.get('is_dir'): return
    if name.endswith(tuple(PREVIEW_EXT)):
        self._preview_file(entry)   # 后台 read_text → 异步填窗
```

### 2. 搜索框（按文件名过滤）

工具栏右侧新增搜索框，输入即时过滤当前已加载的目录树 —— 命中行的祖先目录自动保留可见、空目录自动隐藏；清空即还原全部。每次目录加载完成（`_populate` / `_build_root`）后自动重套当前过滤条件，保证懒加载新内容也跟着过滤。

```python
def _filter_item(self, item, text):
    visible = (text in name) or (is_dir and children_visible)
    self.tree.setRowHidden(item.row(), item.index().parent(), not visible)
```

### 3. 文件名 UTF-8 修正

`AdbFileManager.list_dir` 改为直接以字节流执行（`shell=False`）后按 **UTF-8 → GB18030 → latin-1** 顺序稳健解码，根治部分老 ROM（GBK locale）中文文件名乱码。新增 `_decode_adb_output()` 公共解码函数，`read_text` 预览同样复用。

---

## 十四、未来可扩展点（idea，未实现）

- [ ] **多选 + 批量操作**：Ctrl/Shift 多选，批量上传/下载/删除
- [ ] **拖拽上传**：本机文件直接拖到设备目录
- [x] **文本文件预览**：双击 .xml/.txt/.json/.log 用内置预览器打开（仿 macOS Finder QuickLook）
- [ ] **APK 关联到「安装/解包」弹窗**：右键 `.apk` 直接调出 install 弹窗
- [ ] **书签/常用目录**：常用路径保存为快捷入口
- [ ] **远程 Shell 一栏**：底部加一行 shell 输入框，`adb shell <cmd>` 实时回显
- [x] **搜索框**：按名称过滤当前目录结果
- [ ] **拷贝/粘贴/移动**：复制路径后再粘贴到另一目录
- [ ] **回收站**：删除先进回收站，二次确认后再真删
- [ ] **权限 0777 / 0644 等显示翻译**：把 `drwxrwxrwx` 翻译成「所有者/组/其他都可读写执行」自然语言
- [x] **文件名 UTF-8 修正**：老 ROM 中文文件名乱码已通过稳健解码根治

---

> 📌 文档版本 v1 · 2026-08-08 · 悠悠整理 🐱
