# 输入文本对话框（InputTextDialog）— 功能介绍

> 适用版本：Super_ADB 主窗口 → 系统操作 → 「输入文本」按钮
> 代码文件：`Super_ADB_Main/Super_ADB_Main.py` 行 442–758
> 入口：`main_window.btnInputText.clicked → open_input_text_dialog()`
> 截图位置：本文档配套截图保存在 `feature_intro/input-text.png`

---

## 1. 功能概览

在调试 Android 设备时，向**当前焦点输入框**批量发送文本是一个高频动作——比如填写账号、测试搜索框、验证中文输入、处理多行文本。手动敲键盘又慢又容易出错，按键映射也覆盖不到所有字符。

「输入文本」弹窗用一行 ADB 命令替代键盘，按文本内容**自动选策略**：

| 输入内容 | 走的策略 | 是否需要装 APK |
|---|---|---|
| **纯 ASCII**（英文/数字/标点） | `adb shell input text`（系统命令）| ✗ |
| **含非 ASCII**（中文/日文/emoji 等） | 先 `Win32 剪贴板 + KEYCODE_PASTE`（仅模拟器有效） | ✗ |
| ↑ 失败 | 启用 `ADBKeyBoard` IME 接收 base64 广播 | ✓ 需装 ADBKeyBoard.apk |
| ↑ 失败 | 引导用户去 GitHub 下载 ADBKeyBoard | — |

关键能力：

- 🈶 **中文支持**——多策略级联兜底，常见场景不用装额外 APK
- ↩ **回车换行识别**——多行文本自动逐行发送，行间补一个 `KEYCODE_ENTER (66)`
- ⌨ **快捷键** `Ctrl+Enter` 立即发送
- 🔁 **重复点击复用窗口**——主窗口的 `self._input_text_dialog` 成员句柄
- 🟢 **绿色高亮卡样式**——`HIGHLIGHT_CARD_STYLE` + `add_green_glow()`，跟其它弹窗一致

---

## 2. 入口与触发

```
┌────────────────────────────────────────────────────┐
│  主窗口「系统操作」分区                              │
│   ┌────────────────┐                                │
│   │ 设备性能监控   │  输入文本   │  界面包获取       │
│   └────────────────┘  [← 红框]    └────────────────  │
└────────────────────────────────────────────────────┘
```

点击后：

```python
def open_input_text_dialog(self):
    serial = self._ensure_serial()
    if not serial:
        return
    if self._input_text_dialog is not None and self._input_text_dialog.isVisible():
        self._input_text_dialog.raise_()           # 已开 → 置顶
        self._input_text_dialog.activateWindow()
        return
    dlg = QDialog(self)
    # ... 构造弹窗
    dlg.show()
    self._input_text_dialog = dlg
```

跟「安装/解包」、「设备性能监控」、「Monkey 压测」同样是**复用窗口**模式——第二次点同一个按钮不会开新窗。

---

## 3. 界面布局

截图复刻（弹窗 560×300 起步）：

```
╔ 输入文本 (支持中文) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ [—] [□] [×] ╗
║ ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌ ║
║  输入要发送到设备焦点输入框的文本:                                  ║
║                                                                  ║
║  ┌────────────────────────────────────────────────────────────┐ ║
║  │ 在此输入文本，支持中文和多行…                                │ ║
║  │ • 纯 ASCII → 直接 adb shell input text                     │ ║
║  │ • 含中文 → 先试 Win32 剪贴板粘贴 (免安装)                  │ ║
║  │          失败再用 ADBKeyBoard (需安装)                       │ ║
║  │                                                            │ ║
║  │                                                            │ ║
║  └────────────────────────────────────────────────────────────┘ ║
║                                                                  ║
║  ⚠ 未检测到 ADBKeyBoard (中文输入需先安装)                          ║
║  (策略提示行, 发送后展示执行结果)                                  ║
║                                                                  ║
║                          [📥 下载 ADBKeyBoard]  [🚀 发送]          ║
╚══════════════════════════════════════════════════════════════════╝
```

视觉元素逐项说明：

| 元素 | 作用 |
|---|---|
| **说明标签** | 提示用户弹窗用途 |
| **`QTextEdit`** | 多行文本框，placeholder 内嵌策略提示 |
| **ADBKeyBoard 状态标签** | 启动时后台检测，绿/黄/红三色反馈 |
| **策略提示标签** | 发送中/成功后展示具体走了哪条路径 |
| **「下载 ADBKeyBoard」按钮** | 默认隐藏，**仅当中文输入失败且未安装**时显示，一键打开 GitHub 项目页 |
| **「发送」按钮** | 触发级联发送；发送中禁用避免重复点；支持 `Ctrl+Enter` 快捷键 |

> ⚠ 截图里黄色提示「⚠ 未检测到 ADBKeyBoard」是常态——大部分用户没装，正常情况下应该走 Win32 剪贴板方案（仅模拟器）。点击「发送」后会看执行结果标签变 `✓ 非ASCII → Win32 剪贴板粘贴 (N 行)` 才算成功。

---

## 4. 三层策略详解（核心亮点）

代码里这段注释把分层讲清楚了：

```python
"""弹文本输入弹窗，支持多行和中文。

策略:
1. 纯 ASCII → adb shell input text (Android 系统命令)
2. 含非 ASCII (中文等) → 先试 Win32 剪贴板 (免安装, 仅模拟器)
   失败再用 ADBKeyBoard 广播 (需设备装 ADBKeyBoard APK)
   全部失败则引导用户安装 ADBKeyBoard
"""
```

### 4.1 层级 1：纯 ASCII → `adb shell input text`

```python
has_non_ascii = any(ord(c) >= 128 for c in text)
if not has_non_ascii:
    lines = text.split('\n')
    ok_count = 0
    for i, line in enumerate(lines):
        if i > 0:
            self.adb.run_shell(serial, 'input keyevent 66', timeout=5)  # 回车
        if not line:
            continue
        # 反斜杠 + 双引号转义: input text "abc\"def\\g"
        safe = line.replace('\\', '\\\\').replace('"', '\\"')
        self.adb.run_shell(serial, f'input text "{safe}"', timeout=10)
        ok_count += 1
```

**关键细节**：

- **多行用回车连接**——Android 没有 `input text` 多行参数，只能 N 次 `input text` + N-1 次 `keyevent 66 (KEYCODE_ENTER)`
- **转义**：`\` → `\\`、`"` → `\"`——避免 shell 解析失败
- **失败容忍**：任一行失败立刻 `break`，已成功的行保留，状态栏展示「已发送 N 行」

### 4.2 层级 2：非 ASCII → Win32 剪贴板（仅模拟器）

```python
def _send_text_via_native_clipboard(self, serial, text):
    # 1. ctypes 调用 OpenClipboard + SetClipboardData
    # 2. 等 1.5s 让模拟器同步
    # 3. input keyevent 279 (KEYCODE_PASTE)
    # 4. 恢复旧剪贴板
    ...
```

**为什么必须用 Win32 API（ctypes）而不是 Qt？**

```python
# Qt 的方式（不可靠）
clipboard.setText(text)        # 设置归设置，但「模拟器剪贴板同步通知」依赖系统级事件
# Win32 API 的方式（直接调 Windows 内核）
user32.OpenClipboard(0)
user32.EmptyClipboard()
user32.SetClipboardData(CF_UNICODETEXT, h_mem)
```

代码里注释说得很清楚：

> Qt 的 `clipboard.setText()` 不触发模拟器剪贴板同步，所以用 Win32 API（ctypes）直接调 `OpenClipboard/SetClipboardData`，更底层、更可靠地触发 Windows 剪贴板变更通知。

**完整流程**（7 步）：

```
① Qt 读旧剪贴板内容 (用于恢复)
② UTF-16LE 编码 (Win32 CF_UNICODETEXT 要求)
③ GlobalAlloc(GMEM_MOVEABLE) 分配全局内存
④ GlobalLock + memmove 拷贝数据
⑤ OpenClipboard(0) → EmptyClipboard → SetClipboardData → CloseClipboard
⑥ sleep(1.5) 等模拟器剪贴板同步
⑦ input keyevent 279 (KEYCODE_PASTE) 触发粘贴
⑧ sleep(0.3) 等粘贴完成
⑨ 恢复旧剪贴板 (如果存在)
```

### 4.3 层级 3：ADBKeyBoard 广播（通用兜底）

```python
def _send_text_via_adbkeyboard(self, serial, text):
    # 启用 + 切换 IME
    self.adb.run_shell(serial, 'ime enable com.android.adbkeyboard/.AdbIME', timeout=5)
    self.adb.run_shell(serial, 'ime set com.android.adbkeyboard/.AdbIME', timeout=5)
    time.sleep(0.3)
    # base64 广播
    b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
    self.adb.run_shell(serial, f'am broadcast -a ADB_INPUT_B64 --es msg "{b64}"', timeout=5)
    return True
```

**为什么用 base64？**

```python
# ADBKeyBoard 是自定义 IME, 监听 ADB_INPUT_B64 广播,
# 支持 base64 编码的任意 Unicode 文本
```

广播的参数用 `--es msg` 传字符串，**任何字符都可能干扰 shell 解析**。`base64` 输出只含 `[A-Za-z0-9+/=]`，**完全 ASCII**——彻底绕开转义问题。

**`time.sleep(0.3)` 的必要性**：`ime set` 是异步操作，立刻发广播 IME 可能还没切完，文本会丢到上一个 IME 里。300ms 是经验值，刚好覆盖 IME 切换。

### 4.4 失败兜底：引导安装

```python
else:
    btn_install.setVisible(True)
    info_label.setText(
        '✗ 剪贴板方案未生效 (模拟器未同步剪贴板)\n'
        '   → 方案 A: 检查模拟器设置是否开启剪贴板共享\n'
        '   → 方案 B: 安装 ADBKeyBoard (点击下方按钮)')
```

当 Win32 剪贴板方案也没成功（真机、未开启剪贴板共享的模拟器）：

1. 「下载 ADBKeyBoard」按钮**自动显示**出来
2. 提示行展示「A 检查模拟器 / B 安装 ADBKeyBoard」两条出路
3. 用户点击按钮 → `QDesktopServices.openUrl(QUrl('https://github.com/senzhk/ADBKeyBoard'))`

---

## 5. Win32 剪贴板实现细节（**与系统底层交互的典型案例**）

这一段是这个模块最值得保留的实现细节——直接演示了 Python 调 Win32 内核的完整套路。

### 5.1 为什么是 CF_UNICODETEXT 而不是 CF_TEXT？

```python
CF_UNICODETEXT = 13      # Unicode 文本 (UTF-16LE)
GMEM_MOVEABLE = 0x0002   # 标准全局内存可移动标志

# UTF-16LE 编码 (Win32 标准)
data = (text + '\0').encode('utf-16-le')
```

- **CF_TEXT 是 ANSI**，每个字符 1 字节，无法承载中文
- **CF_UNICODETEXT 是 UTF-16LE**，每个字符 2 字节，完整 Unicode 覆盖
- **必须末尾加 `\0`**——Win32 全局内存的字符串惯例是 C 风格以 NULL 结尾
- **UTF-16LE 而非 UTF-8**——这是 Win32 内部编码，零字节序转换开销

### 5.2 资源管理的 4 处 try/finally 范式

```python
h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
if not h_mem:
    return False                              # 早退

ptr = kernel32.GlobalLock(h_mem)
if not ptr:
    kernel32.GlobalFree(h_mem)                # 失败要释放
    return False

ctypes.memmove(ptr, data, len(data))
kernel32.GlobalUnlock(h_mem)

if not user32.OpenClipboard(0):
    kernel32.GlobalFree(h_mem)                # 失败要释放
    return False

user32.EmptyClipboard()
result = user32.SetClipboardData(CF_UNICODETEXT, h_mem)
user32.CloseClipboard()

if not result:
    kernel32.GlobalFree(h_mem)                # 失败要释放
    return False
```

**Win32 资源漏一次就崩溃**——这里每一步失败都手动释放前一步分配的句柄/内存，是 Win32 编程的**典型范式**。如果用 `with` 上下文管理器会更优雅，但 ctypes 没现成的，所以只能 `if-else`。

### 5.3 为什么保存并恢复旧剪贴板？

```python
old_text = clipboard.text()   # Qt 读
# ... 写剪贴板 ...
if old_text:
    clipboard.setText(old_text)   # Qt 恢复
```

用户本来可能在剪贴板里复制了一段代码或账号，**不要因为我们的弹窗就把这个状态覆盖掉**。这是个好习惯——**有借有还**。

但这里有个细节值得注意：**保存用 Qt 读、恢复用 Qt 写、写剪贴板用 Win32**。因为：

- **读**：Qt 读稳定（不需要担心编码、句柄、剪贴板独占）
- **写**：必须用 Win32（Qt 的 setText 不触发模拟器同步通知）
- **恢复**：既然不是性能关键路径，用 Qt 即可

### 5.4 1.5 秒 vs 0.3 秒 —— 两段 sleep 的不同意义

| sleep 时长 | 位置 | 作用 |
|---|---|---|
| `1.5` | SetClipboardData 之后 | **模拟器剪贴板同步延迟**——这是经验值，太短会粘贴空内容 |
| `0.3` | `input keyevent 279` 之后 | **IME 焦点稳定**——IME 切换需要时间，太短会粘贴到错的字段 |

两个时长都偏长，是为了**对抗安卓系统的不可预测性**。如果觉得太慢，可以试 1.0s + 0.2s，但实测多数场景稳定性下降。

---

## 6. ADBKeyBoard 检测与启用

启动弹窗时**立刻后台检测 ADBKeyBoard 是否安装**，避免用户发送失败后再去发现没装：

```python
# 闭包外层用 list 引用 adbkb_installed 避免 Python 闭包陷阱
adbkb_installed = [False]

def _check_adbkb():
    try:
        ime_list = self.adb.run_shell(serial, 'ime list -s', timeout=5) or ''
        adbkb_installed[0] = 'adbkeyboard' in ime_list.lower()
    except Exception:
        adbkb_installed[0] = False
    if adbkb_installed[0]:
        adbkb_status.setText('✓ ADBKeyBoard 已安装 (中文输入可用)')
        # 绿色 #98c379
    else:
        adbkb_status.setText('⚠ 未检测到 ADBKeyBoard (中文输入需先安装)')
        # 黄色 #e5c07b

# 启动时后台检测 (不阻塞弹窗渲染)
threading.Thread(target=_check_adbkb, daemon=True).start()
```

**Python 闭包陷阱**：

```python
# ❌ 错误写法: 直接 adbkb_installed = False 在内层 if 不可见
# ✅ 正确写法: 用 list/tuple 封装, [0] 引用修改
adbkb_installed = [False]
adbkb_installed[0] = True    # 内层函数可见
```

这是 Python 初学者常踩的坑——内部函数对外层变量赋值会创建局部变量，导致整个闭包失效。**用 list 单元素做引用容器**是最简洁的变通方案。

**`ime list -s`** 这是 Android 官方命令：
- 无 `-s` 输出详情（含 IME 标签）
- 加 `-s` 只输出 ID 列表（一行一个）
- 实际是 `adb shell ime list -s` → `com.android.inputmethod.latin/.LatinIME\ncom.android.adbkeyboard/.AdbIME` 这样的多行字符串

然后用 `'adbkeyboard' in ime_list.lower()` 做大小写不敏感匹配就够了，**不需要严格匹配完整 ID**。

---

## 7. ADBKeyBoard 安装步骤（提示用户）

```python
def _open_download():
    QDesktopServices.openUrl(QUrl('https://github.com/senzhk/ADBKeyBoard'))
    info_label.setText(
        '已打开 ADBKeyBoard 项目页, 下载 APK 并在设备安装后, '
        '执行: adb shell ime enable '
        'com.android.adbkeyboard/.AdbIME')
```

`QDesktopServices.openUrl` 一行调系统默认浏览器打开 GitHub 链接，然后在策略提示行展示**完整安装步骤**——

> 下载 APK → 设备安装 → `adb shell ime enable com.android.adbkeyboard/.AdbIME`

`AdbIME` 是 ADBKeyBoard 的 Service 类全名，**`enable` 只是登记**，还需要用户在系统设置里手动选一次（或调用 `ime set`）才能切到它。

**安装备注**：本项目**没有提供本地 APK**，因为：

1. APK 体积几百 KB 不算小，与代码仓库混在一起不优雅
2. 第三方 APK 频繁被反编译二次打包，从官方下载更安全
3. `QDesktopServices.openUrl` 调本地浏览器已经够用

---

## 8. 快捷键 & 用户体验细节

| 行为 | 实现 |
|---|---|
| **Ctrl+Enter 立即发送** | `QShortcut(QKeySequence('Ctrl+Return'), dlg, activated=_do_send)` |
| **发送中禁用按钮** | `btn_send.setEnabled(False)`，`_do_send` 末尾还原 |
| **发送中修改标题** | `dlg.setWindowTitle('发送中…')`，结束后还原 |
| **异步状态刷新** | `QApplication.processEvents()` 在长操作中插入，让 UI 响应 |
| **失败逐行打印日志** | `self.log(f'输入文本失败: {e}')` 走主窗口底部输出区 |
| **完成后清空输入框** | `edit.clear()`（不是清标签，是清文本框） |

**`QApplication.processEvents()` 在哪用**：

```python
def _do_send():
    text = edit.toPlainText()
    btn_send.setEnabled(False)
    dlg.setWindowTitle('发送中…')
    QApplication.processEvents()         # ← 让标题立刻更新
    
    # ... 长操作 ...
    info_label.setText('尝试 Win32 剪贴板粘贴…')
    QApplication.processEvents()         # ← 让标签立刻显示
```

Qt 是事件驱动 UI，不调 `processEvents` 的话窗口标题、按钮文字这类更新会等当前函数返回才会被主事件循环消费。**长操作里手动 processEvents** 让 UI 表现更跟手——这是 PySide 编程的常用技巧。

---

## 9. 关闭与生命周期

`QDialog` 默认是「关掉就销毁」模型，**但 `_input_text_dialog` 这个句柄怎么办？**

```python
if self._input_text_dialog is not None and self._input_text_dialog.isVisible():
    # ... 复用逻辑 ...
dlg = QDialog(self)
# ...
dlg.show()
self._input_text_dialog = dlg
```

代码**没有显式 `closeEvent` 处理 `_input_text_dialog = None`**——这是有意的：

- 复用逻辑只看 `isVisible()`，**关掉的 dialog 还存在但不可见，下次点击会新开**
- 旧的 dialog 等 PySide6 主事件循环自然 GC（`dlg.parent = self`，主窗口不关就一直挂在树上）
- 避免「重置句柄导致正在关闭的 dialog 引用错乱」的并发陷阱

这是一个**「简单优先于严谨」** 的取舍——长跑进程（调试工具）不会在意额外的 1KB 内存占位，但写 `closeEvent + 重置句柄` 反而容易引入 bug。

---

## 10. 代码结构

```
Super_ADB_Main.py (行 442–758)
├── open_input_text_dialog(self)              # 主入口, 构造 QDialog
│   ├── card (HIGHLIGHT_CARD_STYLE + 发光)
│   ├── QTextEdit (placeholder 三行策略提示)
│   ├── info_label (策略提示行)
│   ├── adbkb_status (ADBKeyBoard 状态行)
│   ├── 按钮: 「下载 ADBKeyBoard」(默认隐藏)
│   ├── 按钮: 「发送」(Ctrl+Enter 快捷键)
│   ├── _check_adbkb()                        # 异步检测
│   ├── _open_download()                      # 打开 GitHub
│   └── _do_send()                            # 入口, 三层策略分发
│       ├── ASCII → input text + keyevent 66
│       └── 非ASCII → Win32 剪贴板
│                     ├─ 成功 → 完成
│                     └─ 失败 → ADBKeyBoard
│                                ├─ 成功 → 完成
│                                └─ 失败 → 提示 + 显示下载按钮
├── _send_text_via_adbkeyboard(serial, text)  # ADBKeyBoard 广播发送
│   ├── ime list -s 检测
│   ├── ime enable / ime set
│   ├── base64 编码
│   └── am broadcast -a ADB_INPUT_B64
└── _send_text_via_native_clipboard(serial, text)  # Win32 剪贴板
    ├── old_text = clipboard.text()          # Qt 读
    ├── UTF-16LE 编码
    ├── GlobalAlloc + GlobalLock + memmove
    ├── OpenClipboard + EmptyClipboard + SetClipboardData + CloseClipboard
    ├── sleep(1.5) 等同步
    ├── input keyevent 279 (KEYCODE_PASTE)
    └── 恢复旧剪贴板
```

两个 `_send_*` 私有方法**完全是模块化的**——复制到任何项目都能独立工作，只依赖一个 `AdbHelper.run_shell(serial, cmd, timeout)`。

---

## 11. 线程模型

整个对话框的线程交互比日志/性能监控简单得多：

```
主线程: QDialog 事件循环
  ├─ 用户点发送 → _do_send() (主线程, 同步 IO)
  │   ├─ 多行 input text (串行同步)
  │   ├─ Win32 剪贴板 (主线程, sleep)
  │   └─ ADBKeyBoard (串行同步)
  └─ 启动时后台线程: _check_adbkb() (daemon=True)
      └─ ime list → 改 adbkb_status 文本
```

**所有 ADB 调用都在主线程同步执行**，依赖两个事实：

1. `AdbHelper.run_shell` 自带 `timeout` 参数（5s 或 10s），**永远会返回**
2. **多行发送的数量级很小**——典型调试场景就几行到几十行，几秒内完成

后台线程只有 `_check_adbkb` 一个——只为**不阻塞弹窗弹出**。等几秒后状态行从「检测中…」变成绿色/黄色对用户已经足够友好。

---

## 12. 边界限制与已知约束

| 限制 | 说明 |
|---|---|
| **Win32 剪贴板仅模拟器有效** | 真机沙箱隔离，Win32 写剪贴板无法同步到设备端 → 要真机用 ADBKeyBoard |
| **`input text` 对 `%s` 不友好** | Android `input text` 不展开 `%s` 占位符（系统行为），需要用户用 `%s` → 真实按键依赖 |
| **特殊符号丢失** | `input text` 不支持部分 Unicode 字符（Android 5 之前），但现代设备基本完备 |
| **IME 切换会被用户察觉** | ADBKeyBoard 路径会自动 `ime set`，**用户当前输入法的状态会被改**——发送完没还原（设计上认为一次调试行为） |
| **多行换行符** | ASCII 路径靠 `keyevent 66`，但**不是所有 IME 都把 KEYCODE_ENTER 当作「行结束」**——某些 IME 触发搜索/确认而非换行 |
| **IME 焦点依赖** | 若设备当前**没有聚焦到任何输入框**，粘贴会落到无处可去的位置——`input text` 直接丢弃，剪贴板粘贴可能粘贴到桌面便签类全局应用 |
| **APK 提示而非内置** | 软件本身不分发 ADBKeyBoard.apk，引导用户去 GitHub 下载 |
| **KeyEvent 编码固定** | 66 (Enter) 和 279 (Paste) 是 Android 标准 keycode，**不能由用户配置** |

---

## 13. 典型用例

### 用例 1：纯 ASCII 测试用例快速粘贴

```
场景: 单元测试跑之前的设备数据准备
操作:
  1. 打开 APP 登录页
  2. 点「输入文本」
  3. 粘贴账号: testuser@example.com → Ctrl+Enter
  4. 点「下一项」焦点切到密码框
  5. 打开「输入文本」粘贴 Test@1234 → Ctrl+Enter
  6. 点「登录」
```

### 用例 2：搜索「上海市金桥镇」等中文

```
场景: 测试 APP 是否支持中文搜索
操作:
  1. 切到搜索框
  2. 点「输入文本」
  3. 输入「上海市金桥镇」 → Ctrl+Enter
  → 走 Win32 剪贴板, 不需要装 APK
```

### 用例 3：批量地址/账号自动化

```
场景: 跑稳定性测试, 需要大量预设文本
操作:
  1. 准备好一批多行文本
  2. 依次切到对应输入框, 打开「输入文本」粘贴
  3. Ctrl+Enter 一次性发送所有行
  → 多行模式自动按 ENTER 分隔
```

### 用例 4：粘贴包含特殊字符的字符串

```
场景: 测试 JSON 转义、SQL 注入等
输入: {"key": "value with \"quotes\" and \n newlines"}
  → 走 input text, 反斜杠/引号自动转义
```

### 用例 5：超长文本（百行级别日志）

```
场景: 一次性粘贴 100 行测试日志到日志记录 APP
操作:
  1. 在主窗口文本框粘贴 100 行
  2. 点发送
  → 每行一次 input text, 中间 99 次 keyevent 66
  → 串行执行, 预计耗时 ~10s, 进度提示在底部输出区
```

---

## 14. 未来扩展点

1. **导入文本文件** —— 增加「从 .txt/.json 导入」按钮，把内容直接灌进 `QTextEdit`，省得复制粘贴
2. **保存常用文本** —— 像「过滤收藏」一样做「输入收藏」下拉框，常用地址/账号一键选
3. **支持正则宏** —— 用户写 `$HOME/...` 这种占位符，发之前展开为真实路径
4. **自定义 IME 切换还原** —— 发送完调 `ime set` 切回用户原本的 IME（不影响 IME 设置）
5. **批量发送队列** —— 把"粘 1 行 → 按 Tab → 粘 1 行 → 按 Tab"做成工作流模板
6. **发送历史** —— 最近 10 次输入记录，可一键再发
7. **拖拽支持** —— 拖入 .txt 文件自动读入文本框
8. **进度反馈** —— 多行发送时弹个 `QProgressDialog`，显示「已发送 45/100 行」
9. **输入法状态显示** —— 在弹窗顶部显示当前设备 IME，让用户知道粘贴会落到哪
10. **拼音/五笔支持** —— 用 `cmd input keyboard text "pinyin"` 模拟拼音打字（实验性）

---

## 附录 A：常见问题排查

### Q1：点了「发送」但设备上没东西？

**检查清单**：

```
① 设备当前有焦点输入框吗？
   → 点一下设备的输入框, 让光标闪一下
② 模拟器剪贴板共享开了吗？(模拟器用户)
   → 多模拟器(Android Studio AVD/MuMu/夜神) 在设置里有「共享剪贴板」开关
③ 走 Win32 路径的还是 ADBKeyBoard 路径?
   → 看策略提示行, 走对了才是关键
④ 输错了栏?
   → 看焦点是不是还在上一栏
```

### Q2：装完 ADBKeyBoard 还是不生效？

```
① 系统设置 → 语言和输入法 → 当前输入法 → 切到 ADBKeyBoard ?
   → ime set 只是 *把它标记为可选*, 用户当前 IME 还要手动选
② 弹窗启动时的检测线程跑完了吗?
   → 看状态行的图标, 应该变成绿色 ✓
③ 是不是用了魔改系统把 ADBKeyBoard 给屏蔽了?
   → 试一下 adb shell ime list -s 看返回值
```

### Q3：多行文本只能进第一行？

```
KEYCODE_ENTER 在某些 IME 上不是"换行"而是"完成"或"搜索"
→ 临时切到 Android 默认 IME (Gboard) 再试
→ 或者把多行文本改成空格或逗号分隔
```

---

## 附录 B：与「过滤收藏」的对照

| 维度 | 输入文本 | 过滤收藏 |
|---|---|---|
| **持久化载体** | 不持久（每次弹窗临时构造） | `adb_shell_config.json:log_favs` |
| **复用窗口** | `self._input_text_dialog` 句柄 | `FavComboBox._favs_cache` |
| **快捷键** | `Ctrl+Enter` 发送 | 无（按 Enter 触发下拉） |
| **后台线程** | 仅 1 个 IME 检测线程 | 无（主线程读 config） |
| **保存历史** | 无 | 下拉列表即历史 |
| **样式** | 绿色高亮卡 + 发光 | 普通绿色边框 |

两者都是「高频小工具」类对话框，体量小但细节重要。

---

_文档版本：v1 · 与 `Super_ADB_Main.py` 当前代码一致_
_最近更新：2026-08-08_
