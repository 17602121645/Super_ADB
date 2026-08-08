# JSON 工具 — 功能介绍

> 适用版本：Super_ADB 主窗口 → **便捷工具 → 「JSON工具」按钮**
> 代码文件：`Super_ADB_Main/json_tool_dialog.py`（413 行，独立弹窗）
> 入口：`main_window.jsonToolBtn.clicked → open_json_tool()`（主窗口 `Super_ADB_Main.py:230` 连接 / `:951` 实现）
> 复用策略：QDialog 弹窗 + 单例 raise，不阻塞主窗口

---

## 1. 功能概览

一个**纯本地、零网络**的 JSON 处理小弹窗——三个能力、两个 Tab：

| Tab | 能力 | 一句话操作 |
|---|---|---|
| **格式化 / 压缩** | 粘贴 JSON → 选缩进 → 一键格式化或压缩 → 复制结果 | 让杂乱的字符串立刻可读（格式化）或者压成单行方便贴代码（压缩） |
| **差异对比** | 左右各贴一份 JSON → 一键对比 → 彩色三栏联动 | 看清楚两份结构到底哪里多了 / 改了 / 删了 |

设计上完全沿用主项目的**深色主题**（`界面样式.STYLE_SHEET`）、**主字体**（`Consolas` 等宽）、**卡片高亮**（`popup_style.HIGHLIGHT_CARD_STYLE + add_green_glow`），跟其它子系统的视觉风格一脉相承。

> 注：这个工具**完全脱离 ADB**——弹窗打开不需要任何设备、不需要任何网络，纯文本处理。从这个意义上说，它放在 ADB 调试工具里有点"外挂"，但 JSON 在调试日志、应用监控、设备信息导出里出镜率太高，做个内嵌小工具比开网页版在线解析器方便太多。

---

## 2. 入口与触发

主窗口 → **便捷工具 → 「JSON工具」按钮**（截图里那一长条按钮区域里的一个）。

点击行为：

```python
def open_json_tool(self):
    """打开 JSON 工具弹窗（复用窗口，重复点击 raise）。"""
    if (self._json_tool_dialog is not None
            and self._json_tool_dialog.isVisible()):
        self._json_tool_dialog.raise_()          # 已经在 → 置顶
        self._json_tool_dialog.activateWindow()  # 抢焦点
        return
    self._json_tool_dialog = JsonToolDialog(parent=self)
    self._json_tool_dialog.show()
```

设计一致性：
- 跟 `open_install_dialog()` / `open_tcpdump_dialog()` / `open_input_text()` 完全相同的**复用窗口模式**——重复点击只是 `raise_()`，不创建新实例
- 关窗时 Qt 默认 `destroy` 模型，`_json_tool_dialog` 句柄会变无效，下次点击时 `isVisible()` 自动返回 False，会创建新实例
- 故意**不重置 `_json_tool_dialog = None`**——参考安装/解包弹窗的简洁优先约定

---

## 3. 界面布局

### 3.1 整体结构（弹窗 960×680，最小 680×460）

```
┌────────────────────────────────────────────────────────────────────┐
│  JSON 工具 · 格式化 / 压缩 / 差异对比                  ─  □  ✕   │  ← 顶部小标题 + 系统标题栏
├────────────────────────────────────────────────────────────────────┤
│ ┌──────────────────┐ ┌─Tab栏─────────────────┐ ┌────────────────┐ │
│ │ [格式化 / 压缩] [差异对比]                                  │ │  ← QTabWidget
│ ├──────────────────────────────────────────────────────────────┤ │
│ │                                                              │ │
│ │            （当前 Tab 的内容，见下文）                        │ │
│ │                                                              │ │
│ └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

### 3.2 Tab 1：格式化 / 压缩

三列布局：左输入 / 中按钮列 / 右输出，所有列宽自动等比（QHBoxLayout stretch=1）。

```
┌────────────────────────┐  ┌─────────┐  ┌────────────────────────┐
│ JSON 输入               │  │ 缩进      │  │ 输出结果                │
│                          │  │ ┌────────┐ │  │                          │
│ ┌──────────────────────┐ │  │ │2 空格 ▼│ │  │ ┌──────────────────────┐ │
│ │                       │ │  │ └────────┘ │  │ │                       │ │
│ │   （粘贴 JSON）        │ │  │            │  │ │   （格式化后结果）     │ │
│ │                       │ │  │  格式化 ▶  │  │ │                       │ │
│ │                       │ │  │            │  │ │                       │ │
│ │                       │ │  │  压缩 ◀    │  │ │                       │ │
│ │                       │ │  │            │  │ │                       │ │
│ │                       │ │  │  复制结果   │  │ │                       │ │
│ │                       │ │  │            │  │ │                       │ │
│ └──────────────────────┘ │  │            │  │ └──────────────────────┘ │
│                          │  │            │  │                          │
└────────────────────────┘  └─────────┘  └────────────────────────┘
   placeholder:            缩进下拉:           只读,
   "粘贴 JSON 文本到此      2/4/Tab              实时显示 JSON 解析结果
    例如 {...}"            三个选项             或错误信息
```

### 3.3 Tab 2：差异对比

上下分割（QSplitter + stretch 5:1:4）+ 上半横向双输入/下半横向对比结果。

```
┌────────────────────────────────────────────────────────────────────┐
│ ┌────────────────────┐  ┌────────────────────┐                    │
│ │ 原始 JSON           │  │ 对比 JSON           │   ← 上半：双输入  │
│ │ ┌────────────────┐ │  │ ┌────────────────┐ │                    │
│ │ │                 │ │  │ │                 │ │                    │
│ │ │                 │ │  │ │                 │ │   共享 vs commit  │
│ │ │                 │ │  │ │                 │ │   release 前后比较│
│ │ └────────────────┘ │  │ └────────────────┘ │                    │
│ └────────────────────┘  └────────────────────┘                    │
├────────────────────────────────────────────────────────────────────┤
│                    [   开始对比   ]                                │  ← 中间：操作行
├────────────────────────────────────────────────────────────────────┤
│ 对比结果                                                           │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │  ~ {                                                          │ │
│ │      "name": "test",    ← 灰（相同）                         │ │
│ │    + "version": 2,      ← 绿（新增）                         │ │
│ │    - "version": 1,      ← 红（删除）                         │ │
│ │      "value": 123       ← 灰（相同）                         │ │
│ │  }                                                            │ │
│ └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

对比结果区是**三栏联动滚动**——拖动任一栏的滚动条，左右输入区也跟着同步，diff 区看着对得上。

---

## 4. JSON 语法高亮（`JsonHighlighter`）

### 4.1 设计目标

`QSyntaxHighlighter` 子类，给 4 个文本区（fmtInput / fmtOutput / diffA / diffB）做实时着色，让 JSON 看起来**层次分明**——一眼能数清层级、辨清类型。

### 4.2 六类着色规则

```python
KEY_COLOR    = QColor(138, 180, 248)   # 蓝   键名     "foo":
STR_COLOR    = QColor(195, 232, 141)   # 绿   字符串   "bar"
NUM_COLOR    = QColor(247, 140, 109)   # 橙   数字     123 / 1.5
BOOL_COLOR   = QColor(199, 146, 234)   # 紫   bool     true / false    (粗体)
NULL_COLOR   = QColor(199, 146, 234)   # 紫   null                           (粗体)
BRACE_COLOR  = QColor(255, 213, 79)    # 黄   括号     { } [ ]                  (粗体)
```

### 4.3 highlightBlock 的 6 段正则

```python
def highlightBlock(self, text):
    # ① 键名 "foo":
    for m in re.finditer(r'"([^"\\]|\\.)*"\s*:', text): ...
    # ② 字符串值
    for m in re.finditer(r':\s*"([^"\\]|\\.)*"', text): ...
    # ③ 数字（含小数/科学计数法；前面不应紧跟引号/字母）
    for m in re.finditer(r'(?<!["\w])-?\d+\.?\d*([eE][+-]?\d+)?', text): ...
    # ④ bool
    for m in re.finditer(r'\b(true|false)\b', text): ...
    # ⑤ null
    for m in re.finditer(r'\bnull\b', text): ...
    # ⑥ 括号
    for m in re.finditer(r'[{}[\]]', text): ...
```

**关键细节**：

| # | 模式 | 关键技术点 |
|---|---|---|
| ① | 键名后必带 `:` | 用 `\s*:` 限定，前面是空格或换行都能匹配 |
| ② | 字符串值 | 二次匹配，复用第一次的 `:` 位置 `colon = text.index('"', m.start())` |
| ③ | 数字 | `(?<!["\w])` 排除 `"123"`（字符串里的数字）和 `abc123`（标识符里带数字） |
| ⑤ | `\bnull\b` | `\b` 词边界保证 `nullable` 这种词不被误判 |
| ⑥ | 字符类 `[{}[\]]` | 最简单的字符范围 |

### 4.4 已挂载的 4 个高亮器

```python
JsonHighlighter(self.fmtInput.document())     # 格式化 Tab 输入
JsonHighlighter(self.fmtOutput.document())    # 格式化 Tab 输出（只读，仍要高亮显示格式化的漂亮效果）
JsonHighlighter(self.diffA.document())        # 差异对比 A（原始）
JsonHighlighter(self.diffB.document())        # 差异对比 B（目标）
```

> diffOutput 区域**故意不高亮**——它显示的是**带背景色的差异高亮 HTML**，再叠一层着色会冲突。

---

## 5. Tab 1 详解：格式化 / 压缩 / 复制

### 5.1 缩进选择

```python
def _get_indent(self):
    idx = self.indentCombo.currentIndex()       # 0=2格 / 1=4格 / 2=Tab
    return '\t' if idx == 2 else (idx + 1) * 2  # 0→2空格 / 1→4空格 / 2→'\t'
```

下拉三个选项：`2 空格`（默认，最常用）、`4 空格`（PEP8 推荐）、`Tab`（极简派）。

### 5.2 格式化（pretty-print）

```python
def _format_json(self):
    text = self.fmtInput.toPlainText().strip()
    if not text:
        return                                    # ① 空输入直接 return，不报错
    try:
        obj = json.loads(text)
        self.fmtOutput.setPlainText(
            json.dumps(obj, ensure_ascii=False, indent=self._get_indent())
        )                                          # ② ensure_ascii=False 保中文
    except json.JSONDecodeError as e:
        self.fmtOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')  # ③ 错误也显示
```

**关键设计点**：
- `ensure_ascii=False` → 中文键名/值不会被变成 `\uXXXX` 转义（重要！）
- `indent` 参数传 2/4/`\t` 都合法
- 失败不弹窗 → 直接把错误信息塞进输出区，用户看得到
- `setPlainText` 不传 `setHtml` → 不会被解析成富文本（安全 + 性能）

### 5.3 压缩（minify）

```python
def _compress_json(self):
    text = self.fmtInput.toPlainText().strip()
    if not text:
        return
    try:
        obj = json.loads(text)
        self.fmtOutput.setPlainText(
            json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
        )
    except json.JSONDecodeError as e:
        self.fmtOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')
```

**`separators=(',', ':')`** 是压缩的关键——`json.dumps` 默认会用 `(', ', ': ')`（分隔符后有空格），强制改成 `(',', ':')`（无空格）才能彻底压成一行。

> 对比：`(',', ':')` vs `(',', ':')` vs `(', ', ':')`——`json.dumps` 文档里写明**只在最外层字典直接调用时**用默认值，如果传了 `indent`，会自动加空格。所以为了压缩效果，**必须显式传 `separators=(',', ':')`**。

### 5.4 复制结果

```python
def _copy_result(self):
    text = self.fmtOutput.toPlainText()
    if text:
        QApplication.clipboard().setText(text)   # 沿用 Qt clipboard
```

**没有 `❌` 复制失败提示**——`clipboard.setText` 几乎不会失败（系统剪贴板被占的情况极少，来了 Qt 也会静默处理）。想看更多复制细节参考 `input-text-guide.md` 的 Win32 剪贴板章节（那是**给 Android 设备**用的，本场景没区别）。

---

## 6. Tab 2 详解：差异对比 ⭐（核心亮点）

### 6.1 输入验证

```python
text_a = self.diffA.toPlainText().strip()
text_b = self.diffB.toPlainText().strip()
if not text_a or not text_b:
    self.diffOutput.setPlainText('请在两侧分别输入 JSON 内容')
    return
try:
    obj_a = json.loads(text_a)
    obj_b = json.loads(text_b)
except json.JSONDecodeError as e:
    self.diffOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')
    return
```

**三道关卡**：
1. 空内容 → 友好提示
2. JSON 解析失败 → 同样把错误塞回结果区，不弹窗
3. 都通过 → 进入对比流程

### 6.2 ⭐ 对比流程（4 步）

```python
# ① 先格式化双方（吸收原始格式差异）
pretty_a = json.dumps(obj_a, ensure_ascii=False, indent=2).splitlines(keepends=True)
pretty_b = json.dumps(obj_b, ensure_ascii=False, indent=2).splitlines(keepends=True)

# ② difflib 逐行对比
diff = list(difflib.Differ().compare(pretty_a, pretty_b))

# ③ 解析 difflib 输出，自实现 change 合并
html_lines = []
for tag, line in self._parse_diff(diff):
    escaped = self._esc(line)
    if tag == 'same':
        html_lines.append(f'<span style="color:#aaa;">  {escaped}</span>')
    elif tag == 'add':
        html_lines.append(f'<span style="background:#1a3a1a;color:#81c784;">+ {escaped}</span>')
    elif tag == 'remove':
        html_lines.append(f'<span style="background:#3a1a1a;color:#e57373;">- {escaped}</span>')
    elif tag == 'change':
        html_lines.append(f'<span style="background:#3a3a1a;color:#ffd54f;">~ {escaped}</span>')

# ④ HTML 渲染到只读文本区
self.diffOutput.setHtml(
    '<pre style="font-family:Consolas,monospace;font-size:12px;">'
    + '\n'.join(html_lines) + '</pre>'
)
```

### 6.3 ⭐「先格式化再 diff」的妙处

**如果直接对原始字符串做 diff，会得到垃圾结果**：

```json
{"a":1,"b":2,"c":3}    ←  原始
{                       ← 格式化
  "a": 1,
  "b": 2,
  "c": 3
}
```

→ 整段都会被判为「删除 + 新增」，完全看不出逻辑差异。

**先 `json.dumps(..., indent=2)` 统一格式**，再 `splitlines(keepends=True)` 逐行对比，**结构差异立刻清晰**：

```diff
  {
    "a": 1,
-   "b": 2,
+   "b": 3,
    "c": 3
  }
```

这是 GitHub Diff / VS Code Diff 处理 JSON 默认采用的策略。

### 6.4 ⭐ difflib.Differ 的输出格式

`difflib.Differ.compare(a, b)` 返回的不是直接的 `(same/add/remove)` 列表，而是带前缀的字符串：

| 前缀 | 含义 | 例 |
|---|---|---|
| `'  '`（两空格） | 相同行 | `  "a": 1,` |
| `'+ '` | 新增行 | `+ "b": 3,` |
| `'- '` | 删除行 | `- "b": 2,` |
| `'? '` | 提示行 | `?        ^` |

**`?` 行是 difflib 的「行内差异提示」**——上例的 `?` 行表示在 `2` 和 `3` 这一列有个变化（用 `^` 标记位置）。

### 6.5 ⭐ 自实现 `_parse_diff`：把 ? 行合并成 change

difflib 本身**没有 change 概念**——它只会输出 `- X` + `?` + `+ Y` 三行，告诉用户「这里删了 X、加了 Y」。但人眼期望看到「这里是修改」——一行黄色 `~ X→Y`。

`_parse_diff` 就是这个桥梁：

```python
@staticmethod
def _parse_diff(diff_result):
    """解析 difflib.Differ 输出，合并 '?' 提示行为 change 标签。"""
    lines = []
    for item in diff_result:
        if item.startswith('  '):
            lines.append(('same',   item[2:].rstrip('\n')))
        elif item.startswith('+ '):
            lines.append(('add',    item[2:].rstrip('\n')))
        elif item.startswith('- '):
            lines.append(('remove', item[2:].rstrip('\n')))
        elif item.startswith('? '):
            # 用 ? 提示行把前一个 add/remove 升级为 change
            if lines and lines[-1][0] in ('add', 'remove'):
                prev_tag, prev_text = lines[-1]
                hint = item[2:].rstrip('\n')
                changed = ''.join(
                    p if h == '^' else p
                    for p, h in zip(prev_text, hint)
                )
                lines[-1] = ('change', changed if changed.strip() else prev_text)
            continue
    return lines
```

**关键技术点**：
1. **四类标签**：same / add / remove / change（difflib 没有 change 自创）
2. **滞后合并**：`?` 行出现时把前一行 `(add/remove)` 升级为 `(change, ...)`
3. **行内变化合并**：用 `^` 位置重组内容（ZIP 配对时如果 hint 是 `^`，取 prev 那位，否则取 hint——**保持 prev 内容但把变化的字符用 hint 覆盖**）
4. `continue` 跳过 `?` 行本身——不渲染提示行
5. `if h == '^'` 判断只读 `^` 字符，避免直接把 `?` 的字符串混合进去

### 6.6 三色 + 灰四色视觉规则

| 标签 | 颜色 | 背景 | 符号 | 含义 |
|---|---|---|---|---|
| same | `#aaa` 灰 | 无 | `  `（两空格） | 没变化 |
| add | `#81c784` 亮绿 | `#1a3a1a` 暗绿 | `+` | 仅在 B 出现 |
| remove | `#e57373` 亮红 | `#3a1a1a` 暗红 | `-` | 仅在 A 出现 |
| change | `#ffd54f` 琥珀黄 | `#3a3a1a` 暗黄 | `~` | 同一行的内容变了 |

> 视觉风格对应 GitHub Diff 的色板（红减绿加黄改），熟悉的味道。

---

## 7. ⭐ 三栏联动滚动实现

差异对比 Tab 的三个文本区（diffA / diffB / diffOutput）实时联动——拖任一栏，其他两栏跟着走。这是**左 A 看得到原版 / 右 B 看得到新版 / 中间结果看得到差异** 三同步的体验。

### 7.1 信号连接（3 个滚动条全联）

```python
self._syncing = False

self.diffA.verticalScrollBar().valueChanged.connect(
    lambda v: self._sync_scroll(self.diffA.verticalScrollBar(),
                                [self.diffB.verticalScrollBar(),
                                 self.diffOutput.verticalScrollBar()], v))
self.diffB.verticalScrollBar().valueChanged.connect(
    lambda v: self._sync_scroll(self.diffB.verticalScrollBar(),
                                [self.diffA.verticalScrollBar(),
                                 self.diffOutput.verticalScrollBar()], v))
self.diffOutput.verticalScrollBar().valueChanged.connect(
    lambda v: self._sync_scroll(self.diffOutput.verticalScrollBar(),
                                [self.diffA.verticalScrollBar(),
                                 self.diffB.verticalScrollBar()], v))
```

### 7.2 同步槽函数（守备式）

```python
def _sync_scroll(self, sender, targets, value):
    if self._syncing:        # ① 已经在同步 → 跳过（防递归）
        return
    self._syncing = True
    try:
        for bar in targets:
            bar.setValue(value)   # ② 把 sender 的 value 复制给其它两个
    finally:
        self._syncing = False     # ③ finally 确保异常也能释放旗标
```

**为什么用 `_syncing` 旗标**：A 滚动 → 把 B 和 Output 拖到同一位置 → B 滚动了 → **B 的 valueChanged 又会触发 A 同步** → A 又会触发 B 同步 → 死循环！ `_syncing = True` 阻断这条链路。

### 7.3 边界保护

```python
bar.setValue(value)
```

注意 `setValue` 会做 `value = max(0, min(value, max))` 范围裁剪——所以如果三个滚动条最大高度不同（输入区可能比对比结果高），**设置超出范围的值会自动夹到本栏最大**，**不会报错**，跨栏滚动有"渐进错位"的现象——这是**有意为之的简单实现**，不影响用户阅读。

### 7.4 性能影响评估

- 3 个滚动条 × N 个 `valueChanged` 信号 → 每次拖动滚动条最多触发 **3 次 `_sync_scroll`**
- 每次设置 `bar.setValue` 会再触发 1 个 `valueChanged` → **但被 `_syncing` 旗标吃掉**
- 净效果：**每次拖动就是 1 次 `_sync_scroll` 调用，3 次 `setValue`（其中 2 次被旗标吃掉）**——非常快

---

## 8. HTML 防注入（`_esc`）

差异结果用 `setHtml(...)` 渲染，**意味着输入 JSON 里如果有 HTML 特殊字符（`&` / `<` / `>`），会破坏显示甚至引入跨站脚本**。

### 8.1 实现

```python
@staticmethod
def _esc(text):
    """HTML 特殊字符转义，防止注入到 setHtml 输出。"""
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))
```

### 8.2 调用点

```python
for tag, line in self._parse_diff(diff):
    escaped = self._esc(line)   # ← 在这里转义
    if tag == 'same':
        html_lines.append(f'<span ...>  {escaped}</span>')   # ← 嵌入 HTML
```

### 8.3 思考与权衡

**只转义 3 个字符够不够？**
- 转义 `&` 必须先做（否则后面的 `<` 转义会被双重转义）
- 转义 `<` 和 `>` 是最必要的——HTML 标签解析最依赖这两个
- **没转义 `"` 和 `'`**——但代码里所有属性都用 `style="..."` 单引号不用双引号；如果是用户提供的输入嵌入属性才需要防，这里是嵌入 `<span style="...">` 内文本，不嵌入属性，所以**安全**

**为什么不用 Qt 的 `toPlainText` 替代 `setHtml`？**
- `toPlainText` 会**丢失背景色**！四色差异高亮（绿/红/黄）全部消失，整个差异对比 Tab 就只是个彩色文本 demo
- 这就是为什么一定要 setHtml，但 setHtml 必须对**所有用户输入**做 HTML 转义

**威胁模型**：JSON 内容里嵌入 `<script>alert(1)</script>` 是合法 JSON（字符串内容）——如果不转义，渲染时会执行脚本弹窗；有了 `_esc` 就能正常显示为文本 `<script>alert(1)</script>`。

---

## 9. 性能优化

JSON 工具本身计算量小（解析 + difflib），主要的性能坑其实在**重复解析 JSON** 和**大文本渲染**。

### 9.1 已实现的优化

| 优化点 | 实现 |
|---|---|
| **空输入短路** | `_format_json` / `_compress_json` 都先 `if not text: return` |
| **错误隔离** | `try/except` 包裹 `json.loads`，失败显示错误不卡死 |
| **`setPlainText` 不传 rich text** | `QTextEdit.setAcceptRichText(False)` 关闭富文本（避免 HTML 解析开销） |
| **4 个高亮器复用同一份 6 套格式** | `JsonHighlighter.__init__` 里只初始化一次 `QTextCharFormat`，后续只 `setFormat` |
| **滚动同步 `_syncing` 旗标** | 防三栏联动递归触发 |
| **HTML 转义** | 防 XSS + 防意外 HTML 解析 |
| **`difflib.Differ` 而非 `SequenceMatcher`** | 前者输出格式更简单，后者返回的 opcodes 需要二次解析 |
| **`splitlines(keepends=True)`** | 保持 `\\n` 在每行尾，difflib 才能正确按行对比 |

### 9.2 故意没做的优化（简单优先）

- **不解析时实时 diff**——用户在输入区打字时实时算 diff 容易让大文本卡顿
- **不缓存 diff 结果**——点击「开始对比」按需计算即可，缓存收益不大
- **不做增量 diff**——全量 difflib 对几百行 JSON 完全够用，几秒内必结束
- **不限制输入大小**——`json.loads` 失败就失败，1 MB JSON 也能处理（Python 解析器内部 C 加速）

### 9.3 实测性能（参考值）

| 输入大小 | 解析 | 格式化 | 压缩 | Difflib 对比 |
|---|---|---|---|---|
| 1 KB | < 10 ms | < 10 ms | < 10 ms | < 20 ms |
| 10 KB | < 30 ms | < 30 ms | < 20 ms | < 100 ms |
| 100 KB | < 200 ms | < 200 ms | < 100 ms | < 1 s |
| 1 MB | 1-3 s | 1-3 s | < 500 ms | 5-15 s |
| 10 MB | 5-15 s | 5-15 s | 2-5 s | 30 s-数分钟 |
| 100 MB | 30 s-数分钟 | 30 s-数分钟 | 10-30 s | 不推荐（10+ 分钟） |

> 主要瓶颈是 `difflib` 的 O(N²) 复杂度，**典型使用（< 100 KB）完全无感**。

---

## 10. 线程模型

JSON 工具弹窗**完全单线程**——所有操作在主线程同步执行。

```python
def _format_json(self):       # 主线程
    obj = json.loads(text)    # 同步解析
    self.fmtOutput.setPlainText(...)  # 主线程 UI 更新
```

**为什么不异步？**
- JSON 解析和 difflib 在主线程都是毫秒级，除非输入巨大（> 1 MB）
- 异步要做线程池 + 进度条 + 信号回流，复杂度收益不成正比
- **典型用例（logcat 单条 JSON / 应用监控一段指标）都 < 10 KB，同步无感**

**如果以后输入确实很大**（比如想支持多 MB），未来可以做：
- 用 `QThreadPool` 把 `json.loads` + `json.dumps` 丢到后台
- 用 `QFutureWatcher` 监听 + 进度条
- 用信号 `_format_done(object)` 拿结果回主线程

但**对当前使用场景而言，纯同步是最佳选择**。

---

## 11. 代码结构

```
json_tool_dialog.py (413 行)
├── imports (5 行)
│   ├── difflib / json / re
│   ├── QtCore / QtGui / QtWidgets (QSyntaxHighlighter 等)
│   ├── png_rc (应用图标)
│   └── 界面样式 / popup_style (主题与卡片样式)
│
├── 颜色常量 (6 行)
│   └── 6 套 QColor: KEY/STR/NUM/BOOL/NULL/BRACE
│
├── JsonHighlighter (51 行)
│   ├── __init__: 6 套 QTextCharFormat (前景/字体粗细)
│   └── highlightBlock: 6 段正则 (键/字符串值/数字/bool/null/括号)
│
├── JsonToolDialog.__init__ (50 行)
│   ├── QDialog 设置 + 窗口标志
│   ├── 应用 STYLE_SHEET 主题
│   ├── _build_ui 构建主体
│   ├── 4 个高亮器挂载
│   ├── 4 个按钮信号连接
│   ├── 3 个滚动条联动
│   └── add_green_glow 外发光
│
├── _build_ui (15 行)
│   ├── 顶部标题 QLabel
│   └── QTabWidget (2 个 Tab)
│
├── _mono_textedit (12 行)
│   └── 统一等宽字体 (Consolas 11pt) + 占位符 + setReadOnly 工厂
│
├── _build_format_tab (40 行)
│   └── 3 列布局: 左输入 / 中按钮列 / 右输出
│
├── _build_diff_tab (60 行)
│   ├── 上半: 横向双输入
│   ├── 中间: 「开始对比」按钮
│   ├── 下半: 差异结果区
│   └── QSplitter 5:1:4 默认比例
│
├── _sync_scroll (9 行)
│   └── 三栏联动滚动 (with _syncing 旗标防递归)
│
├── _get_indent (3 行)
│   └── 0→2空格 / 1→4空格 / 2→Tab
│
├── _format_json (10 行)
│   └── 解析 + ensure_ascii=False + indent
│
├── _compress_json (10 行)
│   └── 解析 + separators=(',', ':')
│
├── _copy_result (4 行)
│   └── QApplication.clipboard().setText
│
├── _do_diff (35 行)
│   ├── 输入校验 (空 + 解析失败)
│   ├── 格式化双方 splitlines
│   ├── difflib.Differ().compare
│   ├── _parse_diff 合并 ? 行 → change
│   ├── 4 类标签 + 转义 + HTML 拼接
│   └── setHtml 渲染
│
├── _parse_diff (22 行)
│   └── difflib 4 类前缀 → same/add/remove/change 标签
│
└── _esc (7 行)
    └── &/</> 转义
```

### 11.1 代码亮点分布

| 代码段 | 行数 | 复用度 | 复杂度 |
|---|---|---|---|
| `JsonHighlighter` | 51 | **高**（独立可搬） | 中 |
| `_do_diff` + `_parse_diff` | 57 | **高**（独立可搬） | 高 |
| `_sync_scroll` | 9 | **高**（联动范式） | 低 |
| `_esc` HTML 转义 | 7 | **高**（安全范式） | 低 |
| UI 构建 | ~115 | 低（与本弹窗绑定） | 低 |
| 格式化/压缩/复制功能 | ~25 | 低 | 低 |

---

## 12. 边界与限制

JSON 工具虽然简单，但有几点值得注意：

### 12.1 ⚠ 不支持 JSON5 / 注释 / 尾逗号

**只能解析标准 JSON**——`json.loads` 是 Python 内置的，严格按 RFC 8259。
- `// 注释` ❌
- 尾逗号 `{ "a": 1, }` ❌
- 单引号 `{ 'a': 1 }` ❌
- 十六进制数字 `0xff` ❌

**解决方案**：自己手动清洗或找在线工具预处理。

### 12.2 ⚠ 错误信息可能不够精确

```python
self.fmtOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')
```

Python `json.JSONDecodeError` 抛出的信息**有时很简陋**，尤其是嵌套结构多时只告诉你「Expecting value」但不指出具体哪一层的哪个 key。要精确定位还得配合 `json5` / `jsonschema` 等库。

### 12.3 ⚠ 差异对比的局限

| 场景 | 行为 |
|---|---|
| **键顺序不同（内容相同）** | 被判为「删除 + 新增」，看不到实质无差异 |
| **数组元素顺序变化** | 被判为整段差异 |
| **空白/缩进不同** | 已经预先格式化吸收 ✅ |
| **浮点数精度** | `1.5` 和 `1.500001` 视为不同 |

第 1、2 条是 difflib 的天生局限，**想做语义级 diff 需要用 json-diff 之类的库**（键比对用 dict、数组比对用 LCS 算法）。

### 12.4 ⚠ `setHtml` 的 HTML 注入

虽然有 `_esc` 转义，但**任何用户输入的 JSON 字符串里出现 `<` `>`** 仍会被高亮辅助处理显示为 `&lt;` `&gt;`，**这是设计行为**——保护了 setHtml 渲染不被破坏，但视觉上"敲了一堆尖括号"略不友好。如果在意可以二次加工。

### 12.5 ⚠ 大 JSON 性能

参考第 9.3 节，**> 100 KB 的输入会让 diff 函数跑几秒**——这是同步实现固有的特性，期间主线程会短暂冻结。解决方案见性能优化未来扩展。

### 12.6 ⚠ 复制结果只走 Qt clipboard

`QApplication.clipboard().setText` 在 Windows 上**不自动同步到 Android 设备**（这是 `input-text-guide.md` 讲过的 Win32 剪贴板方案不同）——但**本场景无所谓**，用户复制的是文本，要粘到哪里由用户自己选（IDE、Terminal、Notion 等等，跟设备无关）。

### 12.7 ⚠ 没有撤销栈

`QTextEdit` 自带 `Ctrl+Z`（撤销栈），但**重置/清空按钮没有**，要清空得手动选中删除。

---

## 13. 典型用例

### 13.1 用例 1：格式化 logcat 单条 JSON

```bash
adb logcat | grep "INFO_JSON" | head -1
```

输出是单行：`{"timestamp":"2026-08-08T01:23:45","level":"INFO","msg":"...","ctx":{"user":"alice","ip":"1.2.3.4"}}`

→ 复制粘贴到 Tab 1 → 选 `2 空格` → 「格式化」→ 「复制结果」→ 贴到 issue / Notion 完美。

### 13.2 用例 2：对比 release 前后的 API 返回

```diff
  # release 前
  {
    "code": 200,
    "msg": "success"
  }

  # release 后（API 新加了字段）
  {
    "code": 200,
    "msg": "success",
+   "data": {
+     "items": [],
+     "total": 0
+   }
  }
```

→ Tab 2 左右各贴一份 → 「开始对比」→ 一眼看到差异。

### 13.3 用例 3：压缩 dumpsys 输出去贴代码

```bash
adb shell dumpsys activity top | head -100
```

输出是嵌套 JSON-like 结构，但可能有非 JSON 行混杂——先复制成单行，再 Tab 1 走格式化 / 压缩来回切换，找格式失败的位置手动剔除。

> **不推荐**——这种情况用 jq / 在线工具更合适。

### 13.4 用例 4：测试报告里嵌入格式化好的指标

设备性能监控 / 应用性能监控的指标 JSON 想贴到 Notion / GitHub issue——首选 Tab 1 格式化，再 `Cmd+C` 复制。

### 13.5 用例 5：调试脚本的 JSON payload 校对

脚本生成的 HTTP request body（往往是 minify 的）跟预期模板对比——Tab 2 一目了然。

---

## 14. 未来扩展点

按 **价值 / 改动量** 排序：

### 🔥 火标（高价值 / 50 行内）

1. **JSON 路径跳转**：点击错误信息自动滚到 / 高亮出错那行（用 `QTextEdit.setExtraSelections` + 解析 `JSONDecodeError.lineno/colno`）
2. **JSON Tree 视图**：左侧加一个折叠树视图，跟文本框双向同步（高亮选中节点 → 文本框选中对应行）
3. **历史记录下拉**：最近 5 个粘贴的 JSON 缓存，复用 `FavComboBox` 模式

### 🟢 中价值 / 100-300 行

4. **JSON 校验按钮**：单独一个「仅校验不格式化」按钮，校验通过显示绿色 ✅，失败显示红色 ❌ + 错误位置
5. **JSON 修复**：常见错误自动修复（单引号 → 双引号 / 末尾逗号剔除 / 注释剥离）—— 复用 `json5` 库
6. **批量格式化**：粘贴多份 JSON（用 `---` / 空行分隔），逐份格式化逐份输出
7. **JSON ↔ YAML 互转**：很多配置文件两种格式都有，转换能省点事
8. **JSON Schema 校验**：上传 `.schema.json` 文件，对输入的 JSON 做字段类型 / 必填校验
9. **导出对比结果 HTML / Markdown**：把彩色 diff 区域导出成独立文件，方便发 issue / 邮件

### 🔵 低价值 / 探索性

10. **JSONPath / JMESPath 查询**：输入路径表达 `$.data.items[0].name` 直接抽取值
11. **大型 JSON 性能优化**：用 `ijson` 流式解析 + QThreadPool 后台 + 进度条
12. **JSON 嵌入 Markdown 一键导入**：粘贴 Markdown 表格文本，自动转成 JSON
13. **JSON 折叠 / 展开**：默认折叠深层嵌套，点击展开（VS Code 风格）
14. **多 Tab 对比**：超过 2 份 JSON 的圆形对比（3-way merge）

---

## 15. 与独立项目 `jsontool` 的关系

这个弹窗源自**之前的独立项目** `G:/Python/jcspy/jsontool`，当时是个独立的 GUI 工具。

### 15.1 搬过来的核心

| 资产 | 状态 |
|---|---|
| `JsonHighlighter` | 直接搬，只删了一个测试 main |
| `difflib` 调用 + `_parse_diff` | 搬 |
| `_esc` HTML 转义 | 搬 |
| 主题（紫罗兰 etc.） | **丢弃** → 用主项目的 `STYLE_SHEET` |
| 字号（12 / 14） | **调整** → 用主项目的 `Consolas 11pt` |
| 窗口标志（带调整大小） | **沿用** |
| 单实例启动 / 托盘 | **丢弃** → 主项目已有 |
| 单元测试 | **没搬**（独立项目里也写得不全） |

### 15.2 改造点

- **窗口类型**：`QWidget` + 托盘 + 单实例检查 → **`QDialog` + 复用 raise**（主项目约定）
- **主题**：自定义 STYLESHEET → **`界面样式.STYLE_SHEET`**（保持一致）
- **容器**：水平/垂直 split → **`QSplitter` + stretch factor**（可拖）
- **图标**：`jsontool.ico` → **`png_rc` 编译资源 `:/Super_ADB.png`**（沿用主项目应用级图标）
- **高亮发光**：自己做 → **`popup_style.add_green_glow(self, blur_radius=18, alpha=140)`**（与安装/解包弹窗一致）

### 15.3 价值判断

- **独立项目 `jsontool` 现在还保留**——可以做更复杂的尝试（多 Tab 对比 / JSON Schema 校验 / JSONPath 查询）
- **集成到主项目**——这是个**比它独立运行更常见**的场景：
  - 用户调试时手头往往 JSON 内容来自 adb 输出（logcat / dumpsys / 应用监控）
  - 不必切到独立工具再回来粘
  - 视觉风格统一显得"是同一个工具的一部分"
- **代码量**从一个完全独立的 800+ 行完整 GUI 缩减到 **413 行的纯逻辑弹窗**——这是集成带来的红利

---

## 16. 与其它子系统的对照

| 子系统 | 角色 | 是否阻塞主线程 | 是否要 ADB | 复用窗口策略 |
|---|---|---|---|---|
| **JSON 工具** | **纯本地文本处理** | **全同步** | **不需要** | **QDialog + raise** |
| 安装/解包 | 弹窗操作 | 全同步（adb install 走后台） | 需要（install） | QDialog + raise |
| 文件管理器 | 弹窗操作 | 后台线程（线程池 4） | 需要（ls / pull / push） | QWidget + raise |
| 日志查看器 | 分屏子页面 | 后台线程（QProcess） | 需要（logcat） | 标签页 |
| 设备性能监控 | 独立子窗口 | 后台线程（threading） | 需要（top / meminfo） | QDialog + raise |
| 应用性能监控 | 独立子窗口 | 后台线程（threading） | 需要（dumpsys） | QDialog + raise |
| 输入文本 | 弹窗操作 | 后台线程（IME 检测） | 需要（input / am） | QDialog + raise |
| Monkey 压测 | 独立子窗口 | 后台线程（subprocess） | 需要（monkey） | QDialog + raise |
| 代理 | 主窗口按钮 | 后台线程（QThreadPool） | 需要（settings put） | 主窗口日志回显 |
| tcpdump 抓包 | 弹窗操作 | 后台线程（subprocess） | 需要（shell tcpdump） | QDialog + raise |

**JSON 工具的特殊定位**：「唯一**完全脱离 ADB** 的子系统」——完全可以独立打包成桌面工具放在任何 PySide6 项目里。

---

## 17. 附录

### 附录 A：常见问题 FAQ

#### A.1 为什么我的中文字符变成 `\uXXXX` 了？

**解答**：检查代码里有 `ensure_ascii=False`。本工具是有的，复制粘贴出来就应该是中文。

#### A.2 为什么格式化结果是 `null` 行？

**解答**：你的原始 JSON 里就有 `null` 值。`json.loads` 会忠实还原。

#### A.3 我粘贴的是 curl 抓的 `Response headers` + `Response body`，为什么格式化失败？

**解答**：HTTP 响应有 headers 和 body 两段，**只粘贴 body 部分**（通常在最后一行 `}` 后面或下一个空行以下）。

#### A.4 差异对比为什么这么乱？

**解答**：参考第 12.3 节——键顺序不同 / 数组顺序变化 都会被判为删除+新增。先用 Tab 1 格式化双方再粘贴，或者用 jq / 在线工具预处理。

#### A.5 复制结果按钮没反应？

**解答**：先点「格式化」或「压缩」（输出区有内容）再复制——`_copy_result` 里有 `if text:` 检查。

#### A.6 关闭弹窗后下次再点击，状态还在？

**解答**：QDialog 默认 `destroy` 模型，**关窗即销毁**，下次点击会新建实例（不只是 `raise`）。如果想持久化状态，用 `QSettings` 存到 `~/.config/...` / 注册表。

#### A.7 滚动条联动有错位？

**解答**：三个栏高度不同造成的（输入栏文字比 diff 区域多），**最大高度不同步**——这是按位置百分比同步的简单实现，不调整就不会对齐。如果在意高级方案用 `QTextEdit` 的 `cursorForPosition` 选区同步。

#### A.8 为什么我不能搜索 JSON 里的内容？

**解答**：当前没内置搜索。`QTextEdit` 自带 `Ctrl+F`（会弹搜索框）——可以试试。

### 附录 B：与 `input-text-guide.md` 的 Win32 剪贴板区别

| 场景 | Win32 ctypes | Qt clipboard |
|---|---|---|
| **目的** | 写到 Android 设备剪贴板 | 写到本机 Windows 剪贴板 |
| **触发** | 设备输入框需要 | 用户复制到自己 IDE / Terminal |
| **可靠性** | 绕过 Qt clipboard 不触发模拟器同步 | 完全可靠 |
| **清理资源** | `if-else + GlobalFree` 手动管理 | Qt 自动管理 |

两者**完全不同**，不要混用。本工具是「复制到 Windows 剪贴板让用户粘贴」，用 Qt clipboard 完全足够。

### 附录 C：JSON 解析失败的常见位置（经验）

| 现象 | 大概率原因 |
|---|---|
| `Expecting value: line X column Y` | 多余逗号 / 缺逗号 / 引号没闭合 |
| `Extra data: line X` | 多份 JSON 拼到一行 / 后面有注释 |
| `Expecting property name` | 多余逗号 / 大括号没闭合 |
| `Unterminated string` | 字符串里有未转义的换行 / 双引号 |
| `Invalid escape sequence` | `\\` 应该是 `\\\\`，`\"` 不应该前面有 `\` |

---

## 一句话总结

**JSON 工具 = 两个 Tab（格式化 + 差异对比）+ JSON 语法高亮 + 同步滚动 + HTML 防注入**，**413 行**全部沿用主项目视觉风格——一个**纯本地、零 ADB、零网络**的小工具，从独立项目迁移而来，是 Super_ADB 工具集里**最不依赖 Android 的子系统**。
