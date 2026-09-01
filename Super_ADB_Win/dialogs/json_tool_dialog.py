# -*- coding: utf-8 -*-
"""
JSON 工具弹窗
=============
点击主界面「便捷工具 → JSON工具」按钮弹出的独立窗口。

Tab 1 「格式化 / 压缩」:
  - 输入 JSON，选缩进 → 一键格式化 / 压缩 / 复制
  - 历史记录下拉（复用 收藏下拉框 模式，最近 5 个粘贴的 JSON，✕ 可删）
  - 校验按钮：仅校验不格式化，✅ 合法 / ❌ 失败 + 错误位置
  - 修复按钮：单引号→双引号 / 去除末尾逗号 / 剥离 // 与 /* */ 注释 / 未引号键名加引号
  - 解析错误可点击定位：用 setExtraSelections 高亮出错行并滚动
  - 「JSON 树」按钮：弹出独立窗口展示折叠树视图 + 双向同步
Tab 2 「差异对比」: 左右两栏输入 → 一键对比，三色高亮（绿=新增 / 红=删除 / 黄=修改）
Tab 3 「YAML 互转」: JSON↔YAML 转换（纯 Python 实现常见配置子集）
Tab 4 「Schema 校验」: 上传 .schema.json，对格式化页输入做 type/required/properties 校验

UI 完全以代码构建，沿用 Super_ADB 的深色主题（ui_styles.STYLE_SHEET）。

说明：环境无法联网安装 json5 / PyYAML / jsonschema，故三处功能均用纯 Python 内置
实现（JSON 修复扫描器、YAML 子集编解码器、最小 Schema 校验器），无外部依赖。
"""
import html
import json
import re

from PySide6.QtCore import Qt, QSize, QRect
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QTextEdit, QSplitter, QApplication,
    QTreeWidget, QTreeWidgetItem, QFileDialog, QTextBrowser,
    QPlainTextEdit, QHeaderView,
)

from ui import png_rc  # noqa: F401

from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QFont, QIcon, QTextCursor, QPixmap, QPainter, QPainterPath, QPen
from ui.ui_styles import THEMES, get_stylesheet
from ui.dialog_base import 对话框基类
from ui.dialog_styles import add_green_glow, highlight_card_style, _create_popup_card
from tools.favorite_combobox import 收藏委托, _收藏列表视图

# ─────────────────── JSON 语法高亮 ───────────────────
KEY_COLOR = QColor(138, 180, 248)
STR_COLOR = QColor(195, 232, 141)
NUM_COLOR = QColor(247, 140, 109)
BOOL_COLOR = QColor(199, 146, 234)
NULL_COLOR = QColor(199, 146, 234)
BRACE_COLOR = QColor(255, 213, 79)


class Json语法高亮(QSyntaxHighlighter):
    """JSON 语法高亮：键名、字符串值、数字、bool/null、括号分别着色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fmt_key = QTextCharFormat()
        self.fmt_key.setForeground(KEY_COLOR)
        self.fmt_str = QTextCharFormat()
        self.fmt_str.setForeground(STR_COLOR)
        self.fmt_num = QTextCharFormat()
        self.fmt_num.setForeground(NUM_COLOR)
        self.fmt_bool = QTextCharFormat()
        self.fmt_bool.setForeground(BOOL_COLOR)
        self.fmt_bool.setFontWeight(QFont.Weight.Bold)
        self.fmt_null = QTextCharFormat()
        self.fmt_null.setForeground(NULL_COLOR)
        self.fmt_null.setFontWeight(QFont.Weight.Bold)
        self.fmt_brace = QTextCharFormat()
        self.fmt_brace.setForeground(BRACE_COLOR)
        self.fmt_brace.setFontWeight(QFont.Weight.Bold)

    def highlightBlock(self, text):
        for m in re.finditer(r'"([^"\\]|\\.)*"\s*:', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_key)
        for m in re.finditer(r':\s*"([^"\\]|\\.)*"', text):
            try:
                colon = text.index('"', m.start())
            except ValueError:
                continue
            self.setFormat(colon, m.end() - colon, self.fmt_str)
        for m in re.finditer(r'(?<!["\w])-?\d+\.?\d*([eE][+-]?\d+)?', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_num)
        for m in re.finditer(r'\b(true|false)\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_bool)
        for m in re.finditer(r'\bnull\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_null)
        for m in re.finditer(r'[{}[\]]', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_brace)


# ─────────────────── JSON 树：类型徽标 + 代码编辑框 ───────────────────
# 类型 → (徽标字母, 颜色)
_BADGE_COLORS = {
    'D': QColor(99, 155, 255),    # dict   蓝
    'L': QColor(45, 212, 191),    # list   青
    '"': QColor(152, 195, 121),   # string 绿
    '#': QColor(230, 160, 90),    # number 橙
    'B': QColor(190, 150, 230),   # bool   紫
    '∅': QColor(140, 140, 140),   # null   灰
}


def _type_badge(v):
    """返回 (徽标字母, 颜色)。bool 必须在 int 之前判断（bool 是 int 子类）。"""
    if isinstance(v, bool):
        return 'B', _BADGE_COLORS['B']
    if isinstance(v, dict):
        return 'D', _BADGE_COLORS['D']
    if isinstance(v, list):
        return 'L', _BADGE_COLORS['L']
    if isinstance(v, str):
        return '"', _BADGE_COLORS['"']
    if isinstance(v, (int, float)):
        return '#', _BADGE_COLORS['#']
    return '∅', _BADGE_COLORS['∅']


def _type_label(v):
    if isinstance(v, (dict, list)):
        return f'{type(v).__name__} ({len(v)})'
    vs = _scalar_repr(v)
    if len(vs) > 60:
        vs = vs[:57] + '...'
    return f'{type(v).__name__}: {vs}'


def _make_badge_pixmap(letter, bg):
    """渲染 18×18 圆角色块 + 居中字母的徽标图标（用于树节点列 0）。"""
    size = 18
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(bg)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(1, 1, size - 2, size - 2, 4, 4)
    p.setPen(QColor('#0d0d0d'))
    p.setFont(QFont('Consolas', 10, QFont.Weight.Bold))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    return pm


class 行号区域(QWidget):
    """内嵌在 代码文本编辑框 左侧的自定义行号区。"""

    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, e):
        self.editor.line_number_area_paint_event(e)


class 代码文本编辑框(QPlainTextEdit):
    """JSON 树视图右侧文本：等宽字体 + 行号 + 当前行高亮 + 语法高亮。

    QPlainTextEdit 的 setExtraSelections 同时承载「当前行高亮」与
    「树节点范围高亮」，二者通过 _refresh_highlight() 合并应用。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        font = QFont('Consolas')
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        self.setFont(font)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._sel_extra = None  # 树节点范围高亮（ExtraSelection）
        self.line_number_area = 行号区域(self)
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.cursorPositionChanged.connect(self._refresh_highlight)
        self.update_line_number_area_width(0)
        self._refresh_highlight()

    def line_number_area_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 10 + self.fontMetrics().horizontalAdvance('9') * digits

    def update_line_number_area_width(self, _=0):
        # +1 让开 QSS 左边框（边框不计入 contentsMargins，行号区从 x=1 起步）
        self.setViewportMargins(self.line_number_area_width() + 1, 0, 0, 0)

    def update_line_number_area(self, rect, dy):
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(
                0, rect.y(), self.line_number_area.width(), rect.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        cr = self.contentsRect()
        # x=1 让开 QSS 左边框，避免行号区不透明背景盖住边框线
        self.line_number_area.setGeometry(
            QRect(1, cr.top(), self.line_number_area_width(), cr.height()))

    def line_number_area_paint_event(self, e):
        painter = QPainter(self.line_number_area)
        # 圆角填充（跟随编辑器 border-radius 8px），避免直角盖住左侧圆角边框
        path = QPainterPath()
        path.addRoundedRect(e.rect().adjusted(0, 0, 1, 1), 8, 8)
        painter.fillPath(path, QColor('#161616'))
        cur_num = self.textCursor().block().blockNumber() + 1
        block = self.firstVisibleBlock()
        block_num = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(
            self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        fm = self.fontMetrics()
        while block.isValid() and top <= e.rect().bottom():
            if block.isVisible() and bottom >= e.rect().top():
                num = block_num + 1
                color = QColor('#7fd7c4') if num == cur_num else QColor('#555')
                painter.setPen(color)
                painter.drawText(0, top, self.line_number_area.width() - 4,
                                 fm.height(), Qt.AlignmentFlag.AlignRight, str(num))
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_num += 1
        painter.end()

    def _refresh_highlight(self):
        """合并「当前行高亮」+ 已有的「树节点范围高亮」并一次性应用。"""
        extras = []
        cur = QTextEdit.ExtraSelection()
        cur.format.setBackground(QColor(255, 255, 255, 18))
        cur.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        cur.cursor = self.textCursor()
        cur.cursor.clearSelection()
        extras.append(cur)
        if self._sel_extra is not None:
            extras.append(self._sel_extra)
        self.setExtraSelections(extras)

    def set_selection_range(self, extra_sel):
        """由 _select_lines 调用：设置树节点范围高亮并滚动到可见。"""
        self._sel_extra = extra_sel
        if extra_sel is not None:
            self.setTextCursor(extra_sel.cursor)
            self.ensureCursorVisible()
        self._refresh_highlight()


# ─────────────────── 圆角树控件：手动补画 QSS 缺失的圆角弧线 ───────────────────
class 圆角树控件(QTreeWidget):
    """QTreeWidget 子类：paintEvent 中手动绘制完整圆角矩形边框。

    根因：QTreeWidget 作为 QAbstractScrollArea 子类，QSS 的 border-radius
    在圆角弧线处常绘制不完整——顶部/底部直线边框能画出，但四角的圆弧
    边线缺失，导致直线与竖线在圆角处"断开"。本类在 super.paintEvent 之后
    用 QPainter 补画一条完整的 1px 圆角矩形边框线，覆盖 QSS 直线部分
    （同色不可见）并补上缺失的圆弧。
    """

    def __init__(self, border_color, parent=None):
        super().__init__(parent)
        # 关键：去掉 QFrame 默认直角 frame，避免其覆盖 QSS border-radius 的圆角弧线
        # （与右栏 代码文本编辑框.setFrameShape(NoFrame) 同款修复）
        self.setFrameShape(QTreeWidget.Shape.NoFrame)
        self._border_color = QColor(border_color)
        self._radius = 8

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._border_color, 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # 向内缩 0.5px，避免 1px 边框被控件边缘裁剪掉半像素
        rect = self.rect().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, self._radius, self._radius)
        painter.end()


# ─────────────────── 历史记录下拉（复用 收藏下拉框 模式） ───────────────────
class Json历史下拉框(QComboBox):
    """最近 5 个粘贴的 JSON 缓存下拉（可删除）。

    直接复用 收藏下拉框 的 收藏委托 / _收藏列表视图（✕ 删除视觉与点击逻辑），
    仅把收藏项改为「历史项」语义：显示截断预览，完整 JSON 存于 itemData。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumHeight(28)
        self.setPlaceholderText('最近粘贴的 JSON（点击载入 / ✕ 删除）')
        self.setView(_收藏列表视图(self))
        self.setItemDelegate(收藏委托(self))
        self.setMaxCount(6)

    def push(self, text):
        text = (text or '').strip()
        if not text:
            return
        for i in range(self.count()):
            if self.itemData(i, Qt.ItemDataRole.UserRole) == text:
                self.setCurrentIndex(i)
                return
        preview = text if len(text) <= 60 else text[:57] + '...'
        self.insertItem(0, preview, text)
        while self.count() > 5:
            self.removeItem(self.count() - 1)
        self.setCurrentIndex(0)

    def current_full(self):
        idx = self.currentIndex()
        if idx < 0:
            return ''
        data = self.itemData(idx, Qt.ItemDataRole.UserRole)
        return data if data else self.currentText()

    def remove_favorite_at(self, row):
        """供 收藏委托 / _收藏列表视图 的 ✕ 点击调用。"""
        if 0 <= row < self.count():
            self.removeItem(row)


# ─────────────────── 纯 Python：带行号映射的 pretty 打印 ───────────────────
def _scalar_repr(v):
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def pretty_with_paths(obj, indent=2):
    """返回 (text, path_lines)。

    path_lines: path 元组 → (start_line, end_line)（1-based 含两端），
    用于树视图节点 ↔ 文本框行号双向定位。
    """
    lines = []
    path_lines = {}

    def emit(line):
        lines.append(line)
        return len(lines)

    def rec(value, path, depth, key_prefix=''):
        pad = ' ' * (depth * indent)
        if isinstance(value, dict) and value:
            start = emit(f'{pad}{key_prefix}{{')
            items = list(value.items())
            for i, (k, v) in enumerate(items):
                child_path = path + (k,)
                _, v_end = rec(v, child_path, depth + 1, key_prefix=f'"{k}": ')
                if i < len(items) - 1:
                    lines[v_end - 1] += ','
            end = emit(f'{pad}}}')
            path_lines[path] = (start, end)
            return (start, end)
        if isinstance(value, list) and value:
            start = emit(f'{pad}{key_prefix}[')
            for i, v in enumerate(value):
                child_path = path + (f'[{i}]',)
                _, v_end = rec(v, child_path, depth + 1, key_prefix='')
                if i < len(value) - 1:
                    lines[v_end - 1] += ','
            end = emit(f'{pad}]')
            path_lines[path] = (start, end)
            return (start, end)
        # 标量或空容器
        if isinstance(value, dict):
            text = '{}'
        elif isinstance(value, list):
            text = '[]'
        else:
            text = _scalar_repr(value)
        s = emit(f'{pad}{key_prefix}{text}')
        path_lines[path] = (s, s)
        return (s, s)

    rec(obj, (), 0, '')
    return '\n'.join(lines), path_lines


# ─────────────────── 纯 Python：JSON 自动修复扫描器 ───────────────────
def fix_json_text(text):
    """常见 JSON 错误自动修复（无 json5 依赖的等效实现）。

    - 单引号字符串 → 双引号字符串（内容中的 " 转义）
    - 去除末尾逗号（, 后紧跟 } 或 ]）
    - 剥离 // 行注释 与 /* */ 块注释
    - 未加引号的键名 → 加双引号
    仅处理 JSONC / JS 字面量常见写法；不保证覆盖 json5 全部语法。
    """
    res = []
    i = 0
    n = len(text)
    in_str = None
    sq_buf = []
    sig = ''

    def skip_ws_cmt(s, k):
        """跳过空白与注释（// 、# 、/* */），返回下一个有意义的字符位置。"""
        while k < len(s):
            c = s[k]
            if c in ' \t\r\n':
                k += 1
            elif c == '/' and k + 1 < len(s) and s[k + 1] == '/':
                while k < len(s) and s[k] != '\n':
                    k += 1
            elif c == '/' and k + 1 < len(s) and s[k + 1] == '*':
                k += 2
                while k + 1 < len(s) and not (s[k] == '*' and s[k + 1] == '/'):
                    k += 1
                k += 2
            elif c == '#':
                while k < len(s) and s[k] != '\n':
                    k += 1
            else:
                break
        return k

    while i < n:
        c = text[i]
        if in_str == '"':
            if c == '\\':
                res.append(c)
                if i + 1 < n:
                    res.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if c == '"':
                res.append('"')
                in_str = None
                sig = '"'
                i += 1
                continue
            res.append(c)
            i += 1
            continue
        if in_str == "'":
            if c == '\\':
                sq_buf.append('\\')
                if i + 1 < n:
                    sq_buf.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                continue
            if c == "'":
                content = ''.join(sq_buf).replace('\\', '\\\\').replace('"', '\\"')
                res.append('"')
                res.append(content)
                res.append('"')
                in_str = None
                sq_buf = []
                sig = '"'
                i += 1
                continue
            sq_buf.append(c)
            i += 1
            continue
        if c == '"':
            in_str = '"'
            res.append('"')
            sig = '"'
            i += 1
            continue
        if c == "'":
            in_str = "'"
            sq_buf = []
            i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        if c == '#' and (sig == '' or sig in '{,[:'):
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == ',':
            j = skip_ws_cmt(text, i + 1)
            if j < n and text[j] in '}]':
                i = j
                sig = ','
                continue
            res.append(c)
            sig = ','
            i += 1
            continue
        if (c.isalpha() or c == '_' or c == '$') and sig in ('{', ',', '[', ':'):
            j = i
            while j < n and (text[j].isalnum() or text[j] in '_$'):
                j += 1
            ident = text[i:j]
            k = j
            while k < n and text[k] in ' \t\r\n':
                k += 1
            if k < n and text[k] == ':':
                res.append('"')
                res.append(ident)
                res.append('"')
                i = j
                sig = '"'
                continue
            res.append(ident)
            i = j
            sig = ident[-1] if ident else sig
            continue
        res.append(c)
        if not c.isspace():
            sig = c
        i += 1
    return ''.join(res)


# ─────────────────── 纯 Python：JSON ↔ YAML 互转 ───────────────────
def _yaml_scalar(v):
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    s = str(v)
    if s == '':
        return '""'
    if (re.search(r'[:#\[\]{}&*?|<>=!%@` ,]', s)
            or s[0] in '-?!<>|*&%@`'
            or s != s.strip()):
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def json_to_yaml(obj):
    """JSON → YAML 发射器（块式，覆盖常见配置子集）。"""
    out = []

    def walk(node, indent):
        pad = '  ' * indent
        if isinstance(node, dict):
            if not node:
                out.append(f'{pad}{{}}')
                return
            for k, v in node.items():
                if isinstance(v, (dict, list)) and len(v) > 0:
                    out.append(f'{pad}{k}:')
                    walk(v, indent + 1)
                else:
                    out.append(f'{pad}{k}: {_yaml_scalar(v)}')
        elif isinstance(node, list):
            if not node:
                out.append(f'{pad}[]')
                return
            for item in node:
                if isinstance(item, (dict, list)) and len(item) > 0:
                    out.append(f'{pad}-')
                    walk(item, indent + 1)
                else:
                    out.append(f'{pad}- {_yaml_scalar(item)}')
        else:
            out.append(f'{pad}{_yaml_scalar(node)}')

    walk(obj, 0)
    return '\n'.join(out)


def _strip_yaml_comment(line):
    out = []
    in_s = None
    i = 0
    while i < len(line):
        c = line[i]
        if in_s:
            out.append(c)
            if c == in_s:
                in_s = None
            i += 1
            continue
        if c in '"\'':
            in_s = c
            out.append(c)
            i += 1
            continue
        if c == '#':
            break
        out.append(c)
        i += 1
    return ''.join(out)


def _yaml_scalar_parse(s):
    s = s.strip()
    if s == '' or s in ('~', 'null', 'Null', 'NULL'):
        return None
    if s in ('true', 'True', 'TRUE'):
        return True
    if s in ('false', 'False', 'FALSE'):
        return False
    if (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'"):
        return s[1:-1]
    try:
        return float(s) if ('.' in s or 'e' in s or 'E' in s) else int(s)
    except ValueError:
        return s


def _split_flow(s):
    parts, cur, depth, in_s = [], [], 0, None
    for c in s:
        if in_s:
            cur.append(c)
            if c == in_s:
                in_s = None
            continue
        if c in '"\'':
            in_s = c
            cur.append(c)
            continue
        if c in '[{':
            depth += 1
        elif c in ']}':
            depth -= 1
        if c == ',' and depth == 0:
            parts.append(''.join(cur))
            cur = []
            continue
        cur.append(c)
    if cur:
        parts.append(''.join(cur))
    return parts


def _yaml_inline(s):
    s = s.strip()
    if s.startswith('['):
        end = s.rstrip().rfind(']')
        inner = s[1:end] if end > 0 else s[1:-1]
        return [] if inner.strip() == '' else [_yaml_inline(x.strip()) for x in _split_flow(inner)]
    if s.startswith('{'):
        end = s.rstrip().rfind('}')
        inner = s[1:end] if end > 0 else s[1:-1]
        if inner.strip() == '':
            return {}
        d = {}
        for part in _split_flow(inner):
            k, _, v = part.partition(':')
            d[_yaml_scalar_parse(k.strip())] = _yaml_inline(v.strip())
        return d
    return _yaml_scalar_parse(s)


def yaml_to_json(text):
    """YAML → JSON 解析器（块式常见子集；不支持锚点 / 多文档 / 复杂流）。"""
    tokens = []
    for raw in text.split('\n'):
        stripped = _strip_yaml_comment(raw)
        if stripped.strip() == '':
            continue
        indent = len(stripped) - len(stripped.lstrip(' '))
        tokens.append((indent, stripped.strip()))
    if not tokens:
        return None

    pos = 0
    n = len(tokens)

    def parse_map(indent):
        nonlocal pos
        result = {}
        while pos < n:
            cur_indent, content = tokens[pos]
            if cur_indent != indent or content.startswith('-') or content == '-':
                break
            if ':' not in content:
                raise ValueError(f'无法解析映射行: {content!r}')
            key, _, rest = content.partition(':')
            key = _yaml_scalar_parse(key.strip())
            rest = rest.strip()
            pos += 1
            if rest == '':
                if pos < n and tokens[pos][0] > indent:
                    child_indent = tokens[pos][0]
                    result[key] = (parse_seq(child_indent) if tokens[pos][1].startswith('-')
                                   else parse_map(child_indent))
                else:
                    result[key] = None
            else:
                result[key] = _yaml_inline(rest)
        return result

    def parse_seq(indent):
        nonlocal pos
        result = []
        while pos < n:
            cur_indent, content = tokens[pos]
            if cur_indent != indent or not (content == '-' or content.startswith('- ')):
                break
            item_content = content[1:].strip() if content != '-' else ''
            pos += 1
            if item_content == '':
                if pos < n and tokens[pos][0] > indent:
                    child_indent = tokens[pos][0]
                    result.append(parse_seq(child_indent) if tokens[pos][1].startswith('-')
                                  else parse_map(child_indent))
                else:
                    result.append(None)
            elif item_content.startswith(('[', '{')):
                result.append(_yaml_inline(item_content))
            elif ':' in item_content and not item_content[0] in '"\'':
                key, _, rest = item_content.partition(':')
                key = _yaml_scalar_parse(key.strip())
                rest = rest.strip()
                m = {}
                if rest == '':
                    if pos < n and tokens[pos][0] > indent:
                        child_indent = tokens[pos][0]
                        m[key] = (parse_seq(child_indent) if tokens[pos][1].startswith('-')
                                  else parse_map(child_indent))
                    else:
                        m[key] = None
                else:
                    m[key] = _yaml_inline(rest)
                result.append(m)
            else:
                result.append(_yaml_inline(item_content))
        return result

    first_indent, first_content = tokens[0]
    if first_content.startswith('-') or first_content == '-':
        return parse_seq(first_indent)
    return parse_map(first_indent)


# ─────────────────── 纯 Python：最小 JSON Schema 校验 ───────────────────
def validate_schema(instance, schema, path=''):
    errors = []

    def loc(p):
        return p if p else '(root)'

    t = schema.get('type')
    if t:
        ok = {
            'object':  isinstance(instance, dict),
            'array':   isinstance(instance, list),
            'string':  isinstance(instance, str),
            'number':  isinstance(instance, (int, float)) and not isinstance(instance, bool),
            'integer': isinstance(instance, int) and not isinstance(instance, bool),
            'boolean': isinstance(instance, bool),
            'null':    instance is None,
        }.get(t, True)
        if not ok:
            errors.append(f'{loc(path)}: 类型应为 {t}，实际为 '
                          f'{type(instance).__name__}')

    if isinstance(instance, dict):
        for req in schema.get('required', []):
            if req not in instance:
                errors.append(f'{loc(path)}: 缺少必填字段 "{req}"')
        for k, v in instance.items():
            if k in schema.get('properties', {}):
                errors += validate_schema(
                    v, schema['properties'][k],
                    f'{path}.{k}' if path else k)
    if isinstance(instance, list) and 'items' in schema:
        for i, v in enumerate(instance):
            errors += validate_schema(
                v, schema['items'], f'{path}[{i}]' if path else f'[{i}]')
    if 'enum' in schema and instance not in schema['enum']:
        errors.append(f'{loc(path)}: 值 {instance!r} 不在枚举 {schema["enum"]} 中')
    if 'minimum' in schema and isinstance(instance, (int, float)) \
            and not isinstance(instance, bool) and instance < schema['minimum']:
        errors.append(f'{loc(path)}: {instance} < 最小值 {schema["minimum"]}')
    if 'maximum' in schema and isinstance(instance, (int, float)) \
            and not isinstance(instance, bool) and instance > schema['maximum']:
        errors.append(f'{loc(path)}: {instance} > 最大值 {schema["maximum"]}')
    return errors


# ─────────────────── 弹窗主体 ───────────────────
class Json工具对话框(对话框基类):
    """JSON 工具弹窗（QDialog，自定义标题关闭按钮免依赖系统框架）。"""

    def __init__(self, parent=None):
        super().__init__(parent, 标题='JSON 工具', 最小尺寸=(680, 460), 发光=False)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.resize(960, 680)
        self._theme_id = self._主题id  # 兼容旧代码引用
        self._accent = THEMES[self._主题id]['accent']
        # setWindowFlags 后需重设样式（Window 标志会重置部分样式）
        self.setStyleSheet(get_stylesheet(self._theme_id))

        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, self._theme_id, glow=False)

        self._build_ui()

        Json语法高亮(self.fmtInput.document())
        Json语法高亮(self.fmtOutput.document())
        Json语法高亮(self.diffA.document())
        Json语法高亮(self.diffB.document())
        Json语法高亮(self.yamlInput.document())
        Json语法高亮(self.yamlOutput.document())
        Json语法高亮(self.schemaEdit.document())
        Json语法高亮(self.dictInput.document())
        Json语法高亮(self.dictOutput.document())

        self.btnFormat.clicked.connect(self._format_json)
        self.btnCompress.clicked.connect(self._compress_json)
        self.btnCopy.clicked.connect(self._copy_result)
        self.btnValidate.clicked.connect(self._validate_json)
        self.btnFix.clicked.connect(self._fix_json)
        self.btnJsonTree.clicked.connect(self._open_json_tree_popup)
        self.btnDiff.clicked.connect(self._do_diff)

        self.historyCombo.activated.connect(
            lambda _: self.fmtInput.setPlainText(self.historyCombo.current_full()))

        self.fmtOutput.anchorClicked.connect(self._on_error_anchor)

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

        self.btnJsonToYaml.clicked.connect(self._json_to_yaml)
        self.btnYamlToJson.clicked.connect(self._yaml_to_json)
        self.btnLoadSchema.clicked.connect(self._load_schema)
        self.btnValidateSchema.clicked.connect(self._run_schema)
        self.btnJsonToDict.clicked.connect(self._json_to_dict)
        self.btnDictToJson.clicked.connect(self._dict_to_json)

        add_green_glow(self.card, blur_radius=18, alpha=140, accent=QColor(self._accent))

    def apply_theme(self, theme_id):
        """运行时切换主题：重刷标题颜色、外发光与全局样式表。"""
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self._accent = THEMES[theme_id]['accent']
        self.setStyleSheet(get_stylesheet(theme_id))
        # 强制刷新样式缓存（独立 Window 类型窗口的子控件样式缓存更顽固）
        try:
            from PySide6.QtWidgets import QStyle, QWidget
            _st = self.style()
            if _st is not None:
                _st.unpolish(self)
                _st.polish(self)
            # 遍历所有子控件，强制 unpolish/polish
            for _w in self.findChildren(QWidget):
                try:
                    _ws = _w.style()
                    if _ws is not None:
                        _ws.unpolish(_w)
                        _ws.polish(_w)
                except Exception:
                    pass
        except Exception:
            pass
        # 标题标签跟随新强调色
        if hasattr(self, '_title_label') and self._title_label is not None:
            self._title_label.setStyleSheet(
                f'color: {self._accent}; font-weight: bold; border: none; padding: 2px 4px;'
            )
        add_green_glow(self.card, blur_radius=18, alpha=140, accent=QColor(self._accent))
        self.card.setStyleSheet(highlight_card_style(theme_id))
        self.update()
        self.repaint()

    # ─────────────── UI 构建 ───────────────
    def _build_ui(self):
        root = QVBoxLayout(self.card)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self._title_label = QLabel('JSON 工具  ·  格式化 / 压缩 / 差异 / 树 / YAML / Schema')
        self._title_label.setStyleSheet(
            f'color: {self._accent}; font-weight: bold; border: none; padding: 2px 4px;'
        )
        root.addWidget(self._title_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_format_tab(), '格式化 / 压缩')
        self.tabs.addTab(self._build_diff_tab(), '差异对比')
        self.tabs.addTab(self._build_yaml_tab(), 'YAML 互转')
        self.tabs.addTab(self._build_schema_tab(), 'Schema 校验')
        self.tabs.addTab(self._build_dict_tab(), '字典互转')
        root.addWidget(self.tabs, 1)

    def _mono_textedit(self, read_only=False, placeholder='', browser=False):
        te = QTextBrowser() if browser else QTextEdit()
        font = QFont('Consolas')
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        te.setFont(font)
        te.setAcceptRichText(False)
        if browser:
            te.setOpenLinks(False)
        if read_only:
            te.setReadOnly(True)
        if placeholder:
            te.setPlaceholderText(placeholder)
        return te

    def _build_format_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        self.historyCombo = Json历史下拉框()
        v.addWidget(self.historyCombo)

        h = QHBoxLayout()
        h.setSpacing(10)

        left = QVBoxLayout()
        left.addWidget(QLabel('JSON 输入'))
        self.fmtInput = self._mono_textedit(
            placeholder='粘贴 JSON 文本到此\n例如 {"name":"test","value":123}')
        left.addWidget(self.fmtInput, 1)
        h.addLayout(left, 1)

        mid = QVBoxLayout()
        mid.setSpacing(6)
        lbl_indent = QLabel('缩进')
        lbl_indent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mid.addWidget(lbl_indent)
        self.indentCombo = QComboBox()
        self.indentCombo.addItems(['2 空格', '4 空格', 'Tab'])
        mid.addWidget(self.indentCombo)
        mid.addStretch(1)

        self.btnFormat = QPushButton('格式化 ▶')
        self.btnFormat.setMinimumWidth(110)
        mid.addWidget(self.btnFormat)
        self.btnCompress = QPushButton('压缩 ◀')
        self.btnCompress.setMinimumWidth(110)
        mid.addWidget(self.btnCompress)
        self.btnValidate = QPushButton('仅校验')
        self.btnValidate.setMinimumWidth(110)
        mid.addWidget(self.btnValidate)
        self.btnFix = QPushButton('自动修复')
        self.btnFix.setMinimumWidth(110)
        mid.addWidget(self.btnFix)

        self.btnJsonTree = QPushButton('JSON 树')
        self.btnJsonTree.setMinimumWidth(110)
        self.btnJsonTree.setToolTip('弹出独立窗口查看 JSON 树结构')
        mid.addWidget(self.btnJsonTree)
        mid.addStretch(1)
        self.btnCopy = QPushButton('复制结果')
        self.btnCopy.setMinimumWidth(110)
        mid.addWidget(self.btnCopy)
        h.addLayout(mid)

        right = QVBoxLayout()
        right.addWidget(QLabel('输出结果'))
        self.fmtOutput = self._mono_textedit(read_only=True, browser=True)
        right.addWidget(self.fmtOutput, 1)
        h.addLayout(right, 1)

        v.addLayout(h, 1)

        self.fmtStatus = QLabel('')
        self.fmtStatus.setStyleSheet('padding: 4px;')
        v.addWidget(self.fmtStatus)
        return w

    def _build_diff_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        hl = QHBoxLayout(top)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)

        lv = QVBoxLayout()
        lv.addWidget(QLabel('原始 JSON'))
        self.diffA = self._mono_textedit(placeholder='原始 JSON')
        lv.addWidget(self.diffA, 1)
        hl.addLayout(lv, 1)

        rv = QVBoxLayout()
        rv.addWidget(QLabel('对比 JSON'))
        self.diffB = self._mono_textedit(placeholder='目标 JSON')
        rv.addWidget(self.diffB, 1)
        hl.addLayout(rv, 1)

        splitter.addWidget(top)

        btn_row = QWidget()
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        self.btnDiff = QPushButton('开始对比')
        self.btnDiff.setMinimumWidth(120)
        bl.addStretch(1)
        bl.addWidget(self.btnDiff)
        bl.addStretch(1)
        splitter.addWidget(btn_row)

        bot = QWidget()
        bl2 = QVBoxLayout(bot)
        bl2.setContentsMargins(0, 0, 0, 0)
        bl2.addWidget(QLabel('对比结果'))
        self.diffOutput = QTextEdit()
        font = QFont('Consolas')
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        self.diffOutput.setFont(font)
        self.diffOutput.setReadOnly(True)
        bl2.addWidget(self.diffOutput, 1)
        splitter.addWidget(bot)

        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 4)
        splitter.setSizes([260, 50, 220])

        v.addWidget(splitter, 1)
        return w

    def _build_yaml_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        v.addWidget(QLabel('输入（JSON 或 YAML）'))
        self.yamlInput = self._mono_textedit(placeholder='粘贴 JSON 或 YAML')
        v.addWidget(self.yamlInput, 1)

        btn_row = QHBoxLayout()
        self.btnJsonToYaml = QPushButton('JSON → YAML')
        self.btnYamlToJson = QPushButton('YAML → JSON')
        btn_row.addStretch(1)
        btn_row.addWidget(self.btnJsonToYaml)
        btn_row.addWidget(self.btnYamlToJson)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addWidget(QLabel('输出'))
        self.yamlOutput = self._mono_textedit(read_only=True)
        v.addWidget(self.yamlOutput, 1)
        return w

    def _build_schema_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        h1 = QHBoxLayout()
        h1.addWidget(QLabel('Schema (.schema.json):'))
        self.btnLoadSchema = QPushButton('选择文件...')
        h1.addStretch(1)
        h1.addWidget(self.btnLoadSchema)
        v.addLayout(h1)

        self.schemaEdit = self._mono_textedit(
            placeholder='粘贴 JSON Schema，或点「选择文件」加载')
        v.addWidget(self.schemaEdit, 1)

        btn_row = QHBoxLayout()
        self.btnValidateSchema = QPushButton('校验（用格式化页输入）')
        btn_row.addStretch(1)
        btn_row.addWidget(self.btnValidateSchema)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addWidget(QLabel('校验结果:'))
        self.schemaResult = self._mono_textedit(read_only=True)
        v.addWidget(self.schemaResult, 1)
        return w

    def _build_dict_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        v.addWidget(QLabel('输入（JSON 或 Python 字典）'))
        self.dictInput = self._mono_textedit(placeholder='粘贴 JSON 或 Python 字典字面量')
        v.addWidget(self.dictInput, 1)

        btn_row = QHBoxLayout()
        self.btnJsonToDict = QPushButton('JSON → 字典')
        self.btnDictToJson = QPushButton('字典 → JSON')
        btn_row.addStretch(1)
        btn_row.addWidget(self.btnJsonToDict)
        btn_row.addWidget(self.btnDictToJson)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addWidget(QLabel('输出'))
        self.dictOutput = self._mono_textedit(read_only=True)
        v.addWidget(self.dictOutput, 1)
        return w

    # ─────────────── 滚动同步 ───────────────
    def _sync_scroll(self, sender, targets, value):
        if self._syncing:
            return
        self._syncing = True
        try:
            for bar in targets:
                bar.setValue(value)
        finally:
            self._syncing = False

    # ─────────────── 行号高亮 / 定位 ───────────────
    def _select_lines(self, te, start, end, bg):
        doc = te.document()
        first = doc.findBlockByLineNumber(start - 1)
        last = doc.findBlockByLineNumber(end - 1)
        cur = QTextCursor(first)
        cur.setPosition(last.position() + max(0, last.length() - 1),
                        QTextCursor.MoveMode.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(bg)
        sel = QTextEdit.ExtraSelection()
        sel.cursor = cur
        sel.format = fmt
        te.setExtraSelections([sel])
        te.setTextCursor(cur)
        te.ensureCursorVisible()

    def _show_error(self, msg, lineno=None, colno=None):
        esc = html.escape(str(msg))
        if lineno is not None:
            self.fmtOutput.setHtml(
                f'<p style="color:#e57373;margin:0;">'
                f'❌ 第 {lineno} 行，第 {colno} 列：{esc}</p>'
                f'<p style="margin:0;"><a href="jump:{lineno}:{colno}" '
                f'style="color:#1de9b6;">点击定位到出错位置 ▶</a></p>'
            )
        else:
            self.fmtOutput.setHtml(f'<p style="color:#e57373;margin:0;">❌ {esc}</p>')

    def _on_error_anchor(self, url):
        parts = url.toString().split(':', 1)
        if len(parts) == 2 and parts[0] == 'jump':
            try:
                ln, col = parts[1].split(':')
                self._jump_to_error(int(ln), int(col))
            except ValueError:
                pass

    def _jump_to_error(self, lineno, colno):
        self.tabs.setCurrentWidget(self.tabs.widget(0))
        self._select_lines(self.fmtInput, lineno, lineno, QColor(229, 76, 61))

    def _push_history(self, text):
        t = (text or '').strip()
        if t:
            self.historyCombo.push(t)

    # ─────────────── 功能：格式化 / 压缩 / 复制 / 校验 / 修复 ───────────────
    def _get_indent(self):
        idx = self.indentCombo.currentIndex()
        return '\t' if idx == 2 else (idx + 1) * 2

    def _format_json(self):
        text = self.fmtInput.toPlainText().strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            self.fmtStatus.setText(f'❌ 解析失败：第 {e.lineno} 行，第 {e.colno} 列')
            self.fmtStatus.setStyleSheet('color:#e57373; font-weight:bold;')
            self._show_error(str(e), e.lineno, e.colno)
            return
        self.fmtInput.setExtraSelections([])
        self.fmtOutput.setPlainText(
            json.dumps(obj, ensure_ascii=False, indent=self._get_indent()))
        self.fmtStatus.setText('✅ 格式化完成')
        self.fmtStatus.setStyleSheet('color:#81c784; font-weight:bold;')
        self._push_history(text)

    def _compress_json(self):
        text = self.fmtInput.toPlainText().strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            self.fmtStatus.setText(f'❌ 解析失败：第 {e.lineno} 行，第 {e.colno} 列')
            self.fmtStatus.setStyleSheet('color:#e57373; font-weight:bold;')
            self._show_error(str(e), e.lineno, e.colno)
            return
        self.fmtInput.setExtraSelections([])
        self.fmtOutput.setPlainText(
            json.dumps(obj, ensure_ascii=False, separators=(',', ':')))
        self.fmtStatus.setText('✅ 压缩完成')
        self.fmtStatus.setStyleSheet('color:#81c784; font-weight:bold;')
        self._push_history(text)

    def _validate_json(self):
        text = self.fmtInput.toPlainText().strip()
        if not text:
            self.fmtStatus.setText('')
            self.fmtStatus.setStyleSheet('')
            return
        try:
            json.loads(text)
        except json.JSONDecodeError as e:
            self.fmtStatus.setText(f'❌ 校验失败：第 {e.lineno} 行，第 {e.colno} 列')
            self.fmtStatus.setStyleSheet('color:#e57373; font-weight:bold;')
            self._show_error(str(e), e.lineno, e.colno)
            return
        self.fmtInput.setExtraSelections([])
        self.fmtStatus.setText('✅ JSON 合法')
        self.fmtStatus.setStyleSheet('color:#81c784; font-weight:bold;')
        self._push_history(text)

    def _fix_json(self):
        text = self.fmtInput.toPlainText()
        if not text.strip():
            return
        fixed = fix_json_text(text)
        try:
            obj = json.loads(fixed)
        except json.JSONDecodeError as e:
            self.fmtStatus.setText('❌ 自动修复后仍无法解析')
            self.fmtStatus.setStyleSheet('color:#e57373; font-weight:bold;')
            self.fmtOutput.setPlainText(fixed)
            return
        self.fmtInput.setPlainText(fixed)
        self.fmtInput.setExtraSelections([])
        self.fmtOutput.setPlainText(
            json.dumps(obj, ensure_ascii=False, indent=self._get_indent()))
        self.fmtStatus.setText('✅ 已自动修复并格式化')
        self.fmtStatus.setStyleSheet('color:#81c784; font-weight:bold;')
        self._push_history(fixed)

    def _copy_result(self):
        text = self.fmtOutput.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    # ─────────────── 功能：差异对比 ───────────────
    def _sort_json_keys(self, obj):
        """递归对 JSON 对象的所有字典 Key 排序（列表保持原序）。"""
        if isinstance(obj, dict):
            return {k: self._sort_json_keys(v) for k, v in sorted(obj.items())}
        if isinstance(obj, list):
            return [self._sort_json_keys(v) for v in obj]
        return obj

    def _do_diff(self):
        import difflib
        text_a = self.diffA.toPlainText().strip()
        text_b = self.diffB.toPlainText().strip()
        if not text_a or not text_b:
            self.diffOutput.setPlainText('请在两侧分别输入 JSON 内容')
            return
        try:
            obj_a = self._sort_json_keys(json.loads(text_a))
            obj_b = self._sort_json_keys(json.loads(text_b))
        except json.JSONDecodeError as e:
            self.diffOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')
            return

        sorted_a = json.dumps(obj_a, ensure_ascii=False, indent=2)
        sorted_b = json.dumps(obj_b, ensure_ascii=False, indent=2)
        # 把排序后的结果写回输入框，方便直接查看统一字段顺序
        self.diffA.setPlainText(sorted_a)
        self.diffB.setPlainText(sorted_b)

        pretty_a = sorted_a.splitlines(keepends=True)
        pretty_b = sorted_b.splitlines(keepends=True)
        diff = list(difflib.Differ().compare(pretty_a, pretty_b))

        html_lines = []
        for tag, line in self._parse_diff(diff):
            escaped = self._esc(line)
            if tag == 'same':
                html_lines.append(f'<span style="color:#aaa;">  {escaped}</span>')
            elif tag == 'add':
                html_lines.append(
                    f'<span style="background:#1a3a1a;color:#81c784;">+ {escaped}</span>')
            elif tag == 'remove':
                html_lines.append(
                    f'<span style="background:#3a1a1a;color:#e57373;">- {escaped}</span>')
            elif tag == 'change':
                html_lines.append(
                    f'<span style="background:#3a3a1a;color:#ffd54f;">~ {escaped}</span>')

        self.diffOutput.setHtml(
            '<pre style="font-family:Consolas,monospace;font-size:12px;">'
            + '\n'.join(html_lines) + '</pre>'
        )

    @staticmethod
    def _parse_diff(diff_result):
        lines = []
        for item in diff_result:
            if item.startswith('  '):
                lines.append(('same',   item[2:].rstrip('\n')))
            elif item.startswith('+ '):
                lines.append(('add',    item[2:].rstrip('\n')))
            elif item.startswith('- '):
                lines.append(('remove', item[2:].rstrip('\n')))
            elif item.startswith('? '):
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

    @staticmethod
    def _esc(text):
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    # ─────────────── 功能：JSON 树 ───────────────
    def _open_json_tree_popup(self):
        """格式化页「JSON 树」按钮 → 弹出独立窗口展示 JSON 树。"""
        text = self.fmtInput.toPlainText().strip()
        if not text:
            self.fmtStatus.setText('⚠️ 输入框为空')
            self.fmtStatus.setStyleSheet('color:#ffb74d; font-weight:bold;')
            return

        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            self.fmtStatus.setText(f'❌ 解析失败：第 {e.lineno} 行，第 {e.colno} 列')
            self.fmtStatus.setStyleSheet('color:#e57373; font-weight:bold;')
            self._show_error(str(e), e.lineno, e.colno)
            return

        # 无 parent：与主页/JSON 工具平级的独立窗口，点击谁谁在前
        dlg = QDialog()
        dlg.setWindowTitle('JSON 树视图')
        dlg.setWindowIcon(QIcon(':/Super_ADB.png'))
        # 允许最小化/最大化（大 JSON 时可放大查看）
        dlg.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        dlg.resize(860, 580)
        dlg.setMinimumSize(QSize(520, 360))
        # 无 parent 独立窗口继承不到全局样式，需自行挂载（滚动条等控件才会走主题样式）
        dlg.setStyleSheet(get_stylesheet(self._theme_id))

        # 内层亮边卡片（与其他弹窗同款 4px 主题色边框）
        card, v = _create_popup_card(dlg, self._theme_id)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(8, 8, 8, 8)
        inner.setSpacing(6)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(f'''
            QSplitter::handle {{
                background: #20232a;
                border-radius: 3px;
            }}
            QSplitter::handle:hover {{
                background: {self._accent};
            }}
        ''')

        # 圆角边框画在外层容器上：QAbstractScrollArea 的子控件（滚动条、
        # 交汇角）会不透明地盖住自身边框的转角弧线，导致右上/右下角
        # "断线"；内容内缩 3px 平铺进容器，子控件永远够不到边框弧线
        def _make_pane(w):
            pane = QWidget()
            pane.setObjectName('treePane')
            pane.setStyleSheet(f'''
                #treePane {{
                    border: 1px solid {self._accent};
                    border-radius: 8px;
                    background: #1b1d22;
                }}
            ''')
            lay = QVBoxLayout(pane)
            lay.setContentsMargins(3, 3, 3, 3)
            lay.setSpacing(0)
            lay.addWidget(w)
            return pane

        tree_w = 圆角树控件(self._accent)
        tree_w.setColumnCount(2)
        tree_w.setHeaderLabels(['字段 / 路径', '值（类型）'])
        tree_w.setIconSize(QSize(18, 18))
        tree_w.setTextElideMode(Qt.TextElideMode.ElideRight)
        tree_w.setUniformRowHeights(True)
        tree_w.setIndentation(18)
        hdr = tree_w.header()
        # 第 0 列可手动拖拽调宽，最后一列自动拉伸补满
        hdr.setStretchLastSection(True)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        tree_w.setStyleSheet(f'''
            /* QAbstractScrollArea 的 QSS 背景只作用于 viewport，滚动条背后
               区域会被全局 QWidget 底色(#2b2b2b)染灰；此规则压回深底色。
               圆角边框由外层 _make_pane 容器负责，内部全部平铺无边框 */
            QWidget {{
                background-color: #1b1d22;
            }}
            QTreeWidget {{
                border: none;
                background: #1b1d22;
                outline: 0;
            }}
            /* viewport 内圆角，与容器弧线呼应 */
            #qt_scrollarea_viewport {{
                background: #1b1d22;
                border-radius: 5px;
            }}
            /* 滚动条槽与内容同色（不透明），杜绝全局灰底透出 */
            QScrollBar:vertical {{
                background: #1b1d22;
                margin: 10px 4px 10px 0;
            }}
            QScrollBar:horizontal {{
                background: #1b1d22;
                margin: 0 10px 4px 10px;
            }}
            /* 收掉箭头按钮与分页区，避免默认灰色直角块 */
            QScrollBar::add-line, QScrollBar::sub-line {{
                width: 0;
                height: 0;
                background: none;
                border: none;
            }}
            QScrollBar::add-page, QScrollBar::sub-page {{
                background: none;
            }}
            /* 滚动条交汇角与内容同色，避免直角灰块 */
            QAbstractScrollArea::corner {{
                background: #1b1d22;
            }}
            QTreeWidget::item {{
                padding: 5px 8px;
                border-radius: 5px;
                color: #d7dade;
            }}
            QTreeWidget::item:hover {{ background: rgba(255, 255, 255, 0.06); }}
            QTreeWidget::item:selected {{
                background: rgba(29, 233, 182, 0.18);
                color: #e6fff8;
            }}
            QTreeWidget::branch {{ background: transparent; }}
            /* 表头本体透明，避免矩形背景盖住顶部圆角 */
            QHeaderView {{ background: transparent; }}
            QHeaderView::section {{
                background: #20232a;
                color: #9aa0a6;
                border: none;
                border-right: 1px solid {self._accent};
                border-bottom: 1px solid {self._accent};
                padding: 6px 8px;
                font-size: 12px;
            }}
            /* 表头首尾 section 圆角，呼应容器弧线；末列不画右分隔线，
               否则表头右端与滚动条列之间会留一根悬浮"短线" */
            QHeaderView::section:first {{
                border-top-left-radius: 5px;
            }}
            QHeaderView::section:last {{
                border-right: none;
                border-top-right-radius: 5px;
            }}
        ''')
        splitter.addWidget(_make_pane(tree_w))

        tree_text = 代码文本编辑框()
        tree_text.setStyleSheet(f'''
            /* 同左树：压掉滚动条背后的全局灰底，边框由外层容器负责 */
            QWidget {{
                background-color: #1b1d22;
            }}
            QPlainTextEdit {{
                border: none;
                background: #1b1d22;
                color: #d7dade;
                selection-background-color: rgba(29, 233, 182, 0.18);
            }}
            /* viewport 内圆角，与容器弧线呼应 */
            #qt_scrollarea_viewport {{
                background: #1b1d22;
                border-radius: 5px;
            }}
            /* 滚动条槽与内容同色（不透明），杜绝全局灰底透出 */
            QScrollBar:vertical {{
                background: #1b1d22;
                margin: 10px 4px 10px 0;
            }}
            QScrollBar:horizontal {{
                background: #1b1d22;
                margin: 0 10px 4px 10px;
            }}
            /* 收掉箭头按钮与分页区，避免默认灰色直角块 */
            QScrollBar::add-line, QScrollBar::sub-line {{
                width: 0;
                height: 0;
                background: none;
                border: none;
            }}
            QScrollBar::add-page, QScrollBar::sub-page {{
                background: none;
            }}
            /* 滚动条交汇角与内容同色，避免直角灰块 */
            QAbstractScrollArea::corner {{
                background: #1b1d22;
            }}
        ''')
        splitter.addWidget(_make_pane(tree_text))
        splitter.setSizes([360, 500])
        inner.addWidget(splitter, 1)

        # 构建树 + 文本
        tree_txt, path_lines = pretty_with_paths(obj, 2)
        tree_text.setPlainText(tree_txt)
        Json语法高亮(tree_text.document())
        # 注：原版的 syncing = [False] 已移除。
        # 反向定位的嵌套信号防护改用 Qt 官方的 blockSignals 机制，
        # 比 syncing flag 更可靠（详见 _on_select / _on_cursor）。

        def _add_items(parent_item, value, path):
            if isinstance(value, dict):
                for k, val in value.items():
                    cp = path + (k,)
                    item = QTreeWidgetItem([str(k)])
                    item.setData(0, Qt.ItemDataRole.UserRole, cp)
                    letter, bg = _type_badge(val)
                    item.setIcon(0, _make_badge_pixmap(letter, bg))
                    item.setForeground(1, bg)
                    item.setText(1, _type_label(val))
                    item.setToolTip(1, _type_label(val))
                    _add_items(item, val, cp)
                    parent_item.addChild(item)
            elif isinstance(value, list):
                for i, val in enumerate(value):
                    cp = path + (f'[{i}]',)
                    item = QTreeWidgetItem([f'[{i}]'])
                    item.setData(0, Qt.ItemDataRole.UserRole, cp)
                    letter, bg = _type_badge(val)
                    item.setIcon(0, _make_badge_pixmap(letter, bg))
                    item.setForeground(1, bg)
                    item.setText(1, _type_label(val))
                    item.setToolTip(1, _type_label(val))
                    _add_items(item, val, cp)
                    parent_item.addChild(item)
            # value 为标量时：其徽标与值标签已在父层循环里设置，无需建子节点

        _add_items(tree_w.invisibleRootItem(), obj, ())
        tree_w.expandAll()

        # 树选中文本高亮（正向定位）
        def _on_select():
            items = tree_w.selectedItems()
            if not items:
                return
            p = items[0].data(0, Qt.ItemDataRole.UserRole)
            rng = path_lines.get(p)
            if rng:
                # 关键：setExtraSelections 在 PySide6 中会让 textCursor() 跟着变，
                # 触发 cursorPositionChanged → nested _on_cursor → 死循环。
                # 这里 blockSignals 一次性屏蔽掉文本框的 nested signal。
                tree_text.blockSignals(True)
                try:
                    self._select_lines(tree_text, rng[0], rng[1], QColor(29, 233, 182))
                finally:
                    tree_text.blockSignals(False)

        tree_w.itemSelectionChanged.connect(_on_select)

        # 文本光标移动反向高亮树节点（反向定位）
        def _on_cursor():
            cur = tree_text.textCursor()
            ln = cur.block().blockNumber() + 1
            best, best_len = None, -1
            for pp, (s, e) in path_lines.items():
                if s <= ln <= e and len(pp) > best_len:
                    best = pp
                    best_len = len(pp)
            if best is not None:
                # 同步选中树节点：屏蔽树侧的 nested signal + 显式处理事件队列
                # 避免 PySide6 中 setCurrentItem 的信号回踩 _on_cursor
                tree_w.blockSignals(True)
                try:
                    self._find_and_select(tree_w, best)
                    QApplication.processEvents()
                finally:
                    tree_w.blockSignals(False)

        tree_text.cursorPositionChanged.connect(_on_cursor)

        # 底部提示（放进卡片内，避免撕裂卡片视觉）
        hint = QLabel('💡 点击树节点高亮对应文本 | 文本中移动光标反向定位树节点')
        hint.setStyleSheet('color: #888; font-size: 11px; padding: 2px; background: transparent; border: none;')
        inner.addWidget(hint)

        # 非模态：与主页/JSON 工具平级，点谁谁在前；WA_DeleteOnClose 关闭即释放
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if not hasattr(self, '_json_tree_dialogs'):
            self._json_tree_dialogs = []
        self._json_tree_dialogs.append(dlg)  # 防 GC

        def _cleanup():
            try:
                self._json_tree_dialogs.remove(dlg)
            except (ValueError, RuntimeError):
                pass

        dlg.finished.connect(_cleanup)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    # ── 辅助函数（弹窗内用）──
    @staticmethod
    def _select_lines(te, start_line, end_line, bg_color):
        """高亮 te 中 [start_line, end_line] 行范围（1-based 含两端）。

        修复：原版从 Start 走 Down KeepAnchor，导致 anchor 锁在 (0,0)，
        selection range 错位为「文档头 → 目标行末」（多涂一大片）。
        现在用独立的 anchor 记录起点，selection 只覆盖目标范围。
        """
        if start_line < 1:
            start_line = 1
        if end_line < start_line:
            end_line = start_line

        doc = te.document()
        cursor = QTextCursor(doc)

        # 把 cursor 移到 start_line 行首（不 keepAnchor），再记 anchor
        for _ in range(start_line - 1):
            cursor.movePosition(QTextCursor.MoveOperation.Down)
        cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        anchor_pos = cursor.position()

        # 向下延伸到 end_line 行末（KeepAnchor）
        if end_line > start_line:
            cursor.movePosition(QTextCursor.MoveOperation.Down,
                                QTextCursor.MoveMode.KeepAnchor,
                                n=end_line - start_line)
        cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock,
                            QTextCursor.MoveMode.KeepAnchor)

        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(bg_color)
        sel.format.setForeground(QColor('#1e1e1e'))
        sel.cursor = cursor
        # 把 cursor 重设回 anchor 后用 KeepAnchor 选定到当前 cursor，避开 (0,0) anchor
        sel.cursor.setPosition(anchor_pos, QTextCursor.MoveMode.KeepAnchor)

        if hasattr(te, 'set_selection_range'):
            te.set_selection_range(sel)
        else:
            te.setExtraSelections([sel])
        # 不调 ensureCursorVisible：set_selection_range 内部已处理滚动

    @staticmethod
    def _find_and_select(tree_w, path):
        """在树中按路径查找并选中节点。修复：移除 syncing_flag 参数，
        由调用方 blockSignals 控制嵌套回调（Qt 官方推荐方式）。"""
        def _search(parent):
            for i in range(parent.childCount()):
                it = parent.child(i)
                if it.data(0, Qt.ItemDataRole.UserRole) == path:
                    tree_w.setCurrentItem(it)
                    tree_w.scrollToItem(it)
                    return True
                if _search(it):
                    return True
            return False
        _search(tree_w.invisibleRootItem())

    # ─────────────── 功能：YAML 互转 ───────────────
    def _json_to_yaml(self):
        text = self.yamlInput.toPlainText().strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            self.yamlOutput.setPlainText(f'❌ JSON 解析错误: 第 {e.lineno} 行: {e}')
            return
        self.yamlOutput.setPlainText(json_to_yaml(obj))

    def _yaml_to_json(self):
        text = self.yamlInput.toPlainText()
        if not text.strip():
            return
        try:
            obj = yaml_to_json(text)
        except Exception as e:
            self.yamlOutput.setPlainText(f'❌ YAML 解析错误: {e}')
            return
        self.yamlOutput.setPlainText(json.dumps(obj, ensure_ascii=False, indent=2))

    # ─────────────── 功能：Schema 校验 ───────────────
    def _load_schema(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择 Schema 文件', '',
            'JSON Schema (*.json *.schema.json);;所有文件 (*)')
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self.schemaEdit.setPlainText(f.read())
            except Exception as e:
                self.schemaResult.setPlainText(f'读取失败: {e}')

    def _run_schema(self):
        schema_text = self.schemaEdit.toPlainText().strip()
        data_text = self.fmtInput.toPlainText().strip()
        if not schema_text or not data_text:
            self.schemaResult.setPlainText(
                '请提供 Schema 与待校验 JSON（格式化页输入）。')
            return
        try:
            schema = json.loads(schema_text)
        except json.JSONDecodeError as e:
            self.schemaResult.setPlainText(f'❌ Schema 解析错误: {e}')
            return
        try:
            data = json.loads(data_text)
        except json.JSONDecodeError as e:
            self.schemaResult.setPlainText(
                f'❌ 待校验 JSON 解析错误: 第 {e.lineno} 行: {e}')
            return
        errors = validate_schema(data, schema)
        if errors:
            self.schemaResult.setPlainText(
                f'❌ 校验未通过（{len(errors)} 处）:\n\n'
                + '\n'.join('• ' + e for e in errors))
        else:
            self.schemaResult.setPlainText('✅ 校验通过：JSON 完全符合 Schema。')

    # ─────────────── 功能：字典互转 ───────────────
    def _json_to_dict(self):
        text = self.dictInput.toPlainText().strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            self.dictOutput.setPlainText(f'❌ JSON 解析错误: 第 {e.lineno} 行: {e}')
            return
        self.dictOutput.setPlainText(self._format_python_literal(obj))

    def _dict_to_json(self):
        text = self.dictInput.toPlainText().strip()
        if not text:
            return
        try:
            import ast
            obj = ast.literal_eval(text)
        except Exception as e:
            self.dictOutput.setPlainText(f'❌ Python 字典解析错误: {e}')
            return
        try:
            out = json.dumps(self._jsonify_keys(obj), ensure_ascii=False, indent=2)
        except Exception as e:
            self.dictOutput.setPlainText(f'❌ 转为 JSON 失败: {e}')
            return
        self.dictOutput.setPlainText(out)

    def _format_python_literal(self, obj, indent=0):
        """把 JSON 对象格式化为 Python 字典/列表字面量（单引号字符串、True/False/None）。"""
        pad = '    ' * indent
        if isinstance(obj, dict):
            if not obj:
                return '{}'
            items = []
            next_pad = '    ' * (indent + 1)
            for k, v in obj.items():
                key = self._format_python_literal(k, indent)
                val = self._format_python_literal(v, indent + 1)
                items.append(f'{next_pad}{key}: {val}')
            return '{\n' + ',\n'.join(items) + '\n' + pad + '}'
        if isinstance(obj, list):
            if not obj:
                return '[]'
            items = []
            next_pad = '    ' * (indent + 1)
            for v in obj:
                val = self._format_python_literal(v, indent + 1)
                items.append(f'{next_pad}{val}')
            return '[\n' + ',\n'.join(items) + '\n' + pad + ']'
        if isinstance(obj, str):
            return repr(obj)
        if isinstance(obj, bool):
            return 'True' if obj else 'False'
        if obj is None:
            return 'None'
        if isinstance(obj, (int, float)):
            return str(obj)
        return repr(obj)

    def _jsonify_keys(self, obj):
        """递归把字典的 key 转为字符串，满足 JSON 对 key 类型的要求。"""
        if isinstance(obj, dict):
            return {str(k): self._jsonify_keys(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self._jsonify_keys(v) for v in obj]
        return obj
