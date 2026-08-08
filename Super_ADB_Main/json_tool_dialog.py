# -*- coding: utf-8 -*-
"""
JSON 工具弹窗
=============
点击主界面「便捷工具 → JSON工具」按钮弹出的独立窗口：
- Tab 1 「格式化/压缩」: 输入 JSON，选缩进 → 一键格式化 / 压缩 / 复制
- Tab 2 「差异对比」: 左右两栏输入 JSON → 一键对比，三栏联动滚动，
                      同色高亮（绿=新增 / 红=删除 / 黄=修改 / 灰=相同）

UI 完全以代码构建，沿用 Super_ADB 的深色主题（界面样式.STYLE_SHEET），
不做独立 .ui 资源——最大化复用主项目风格与字号一致性。

基于独立项目 ``G:/Python/jcspy/jsontool`` 的 JsonTool 改造：
- 去掉独立进程/单实例/托盘（主项目已有）；改为 QWidget 子窗口，单例复用
- 主题、字号、表格 padding 与主项目对齐
- 复用 JsonHighlighter（语法着色），独立项目的 difflib 差异解析逻辑搬过来
"""
import difflib
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QLabel, QPushButton, QComboBox, QTextEdit, QSplitter, QApplication,
)

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
import png_rc  # noqa: F401

from 界面样式 import ACCENT, FONT_FAMILY, STYLE_SHEET
from popup_style import HIGHLIGHT_CARD_STYLE, add_green_glow

# ─────────────────── JSON 语法高亮 ───────────────────
KEY_COLOR = QColor(138, 180, 248)
STR_COLOR = QColor(195, 232, 141)
NUM_COLOR = QColor(247, 140, 109)
BOOL_COLOR = QColor(199, 146, 234)
NULL_COLOR = QColor(199, 146, 234)
BRACE_COLOR = QColor(255, 213, 79)


class JsonHighlighter(QSyntaxHighlighter):
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
        # 键名 "foo":
        for m in re.finditer(r'"([^"\\]|\\.)*"\s*:', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_key)
        # 字符串值
        for m in re.finditer(r':\s*"([^"\\]|\\.)*"', text):
            try:
                colon = text.index('"', m.start())
            except ValueError:
                continue
            self.setFormat(colon, m.end() - colon, self.fmt_str)
        # 数字（含小数/科学计数法；前面不应紧跟引号/字母）
        for m in re.finditer(r'(?<!["\w])-?\d+\.?\d*([eE][+-]?\d+)?', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_num)
        # bool
        for m in re.finditer(r'\b(true|false)\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_bool)
        # null
        for m in re.finditer(r'\bnull\b', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_null)
        # 括号
        for m in re.finditer(r'[{}[\]]', text):
            self.setFormat(m.start(), m.end() - m.start(), self.fmt_brace)


# ─────────────────── 弹窗主体 ───────────────────
class JsonToolDialog(QDialog):
    """JSON 工具弹窗（QDialog 但通过自定义标题关闭按钮免依赖系统框架）。

    接口说明：
        JsonToolDialog(parent=None) — 直接 show() 即可。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 独立窗口 + 应用级别图标（与主程序同源 png_rc）
        self.setWindowTitle('JSON 工具')
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowCloseButtonHint |
            Qt.WindowType.WindowMinMaxButtonsHint
        )
        self.resize(960, 680)
        self.setMinimumSize(680, 460)

        # 主题先于子控件生效
        self.setStyleSheet(STYLE_SHEET)

        # 自定义标题栏（与主项目"无边框标题栏按钮风格"对齐）
        self._build_ui()

        # 高亮挂到 4 个文本区
        JsonHighlighter(self.fmtInput.document())
        JsonHighlighter(self.fmtOutput.document())
        JsonHighlighter(self.diffA.document())
        JsonHighlighter(self.diffB.document())

        # 绑定按钮事件
        self.btnFormat.clicked.connect(self._format_json)
        self.btnCompress.clicked.connect(self._compress_json)
        self.btnCopy.clicked.connect(self._copy_result)
        self.btnDiff.clicked.connect(self._do_diff)

        # 三栏对比区域滚动条同步
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

        # 边框高亮 + 外发光（与主程序弹窗一致）
        add_green_glow(self, blur_radius=18, alpha=140)

    # ─────────────── UI 构建（纯代码，便于集中微调风格） ───────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # 顶部小标题
        title = QLabel('JSON 工具  ·  格式化 / 压缩 / 差异对比')
        title.setStyleSheet(
            f'color: {ACCENT}; font-weight: bold; border: none;'
            ' padding: 2px 4px;'
        )
        root.addWidget(title)

        # 主体 Tab
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_format_tab(), '格式化 / 压缩')
        self.tabs.addTab(self._build_diff_tab(),    '差异对比')
        root.addWidget(self.tabs, 1)

    def _mono_textedit(self, read_only=False, placeholder=''):
        """构造一个等宽字体深色背景的 QTextEdit，统一字号与对齐方式。"""
        te = QTextEdit()
        font = QFont('Consolas')
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        te.setFont(font)
        te.setAcceptRichText(False)
        if read_only:
            te.setReadOnly(True)
        if placeholder:
            te.setPlaceholderText(placeholder)
        return te

    def _build_format_tab(self):
        """Tab 1：左输入 + 中间按钮列 + 右输出。"""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 8, 8, 8)
        h.setSpacing(10)

        # 左：输入
        left = QVBoxLayout()
        left.addWidget(QLabel('JSON 输入'))
        self.fmtInput = self._mono_textedit(
            placeholder='粘贴 JSON 文本到此\n例如 {"name":"test","value":123}')
        left.addWidget(self.fmtInput, 1)
        h.addLayout(left, 1)

        # 中：缩进 + 按钮列
        mid = QVBoxLayout()
        mid.setSpacing(8)
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

        mid.addStretch(1)

        self.btnCopy = QPushButton('复制结果')
        self.btnCopy.setMinimumWidth(110)
        mid.addWidget(self.btnCopy)
        h.addLayout(mid)

        # 右：输出
        right = QVBoxLayout()
        right.addWidget(QLabel('输出结果'))
        self.fmtOutput = self._mono_textedit(read_only=True)
        right.addWidget(self.fmtOutput, 1)
        h.addLayout(right, 1)

        return w

    def _build_diff_tab(self):
        """Tab 2：上方左右双输入 + 对比按钮；下方结果区（三栏联动滚动）。"""
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # 上下分割：上 = 双输入 + 对比按钮；下 = 对比结果
        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── 上半：左右双输入 + 中间按钮 ──
        top = QWidget()
        hl = QHBoxLayout(top)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(10)

        # 左输入
        lv = QVBoxLayout()
        lv.addWidget(QLabel('原始 JSON'))
        self.diffA = self._mono_textedit(placeholder='原始 JSON')
        lv.addWidget(self.diffA, 1)
        hl.addLayout(lv, 1)

        # 右输入
        rv = QVBoxLayout()
        rv.addWidget(QLabel('对比 JSON'))
        self.diffB = self._mono_textedit(placeholder='目标 JSON')
        rv.addWidget(self.diffB, 1)
        hl.addLayout(rv, 1)

        splitter.addWidget(top)

        # ── 中间：对比按钮行（占一个独立行，更醒目） ──
        btn_row = QWidget()
        bl = QHBoxLayout(btn_row)
        bl.setContentsMargins(0, 0, 0, 0)
        self.btnDiff = QPushButton('开始对比')
        self.btnDiff.setMinimumWidth(120)
        bl.addStretch(1)
        bl.addWidget(self.btnDiff)
        bl.addStretch(1)
        splitter.addWidget(btn_row)

        # ── 下半：对比结果（彩色 HTML） ──
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

        # 默认上下比例 5 : 1 : 4
        splitter.setStretchFactor(0, 5)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 4)
        splitter.setSizes([260, 50, 220])

        v.addWidget(splitter, 1)
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

    # ─────────────── 功能：格式化 / 压缩 / 复制 ───────────────
    def _get_indent(self):
        idx = self.indentCombo.currentIndex()
        return '\t' if idx == 2 else (idx + 1) * 2

    def _format_json(self):
        text = self.fmtInput.toPlainText().strip()
        if not text:
            return
        try:
            obj = json.loads(text)
            self.fmtOutput.setPlainText(
                json.dumps(obj, ensure_ascii=False, indent=self._get_indent())
            )
        except json.JSONDecodeError as e:
            self.fmtOutput.setPlainText(f'❌ JSON 解析错误:\n{e}')

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

    def _copy_result(self):
        text = self.fmtOutput.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    # ─────────────── 功能：差异对比 ───────────────
    def _do_diff(self):
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

        # 先统一 2 空格缩进再逐行 diff（吸收原始格式差异）
        pretty_a = json.dumps(obj_a, ensure_ascii=False,
                              indent=2).splitlines(keepends=True)
        pretty_b = json.dumps(obj_b, ensure_ascii=False,
                              indent=2).splitlines(keepends=True)
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

    @staticmethod
    def _esc(text):
        """HTML 特殊字符转义，防止注入到 setHtml 输出。"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))
