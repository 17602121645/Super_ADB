# -*- coding: utf-8 -*-
"""
PCAP 解析器对话框
=================
类 Charles 的 pcap 解析展示工具。
从 tcpdump 抓包对话框点击「解析 PCAP」打开，或独立打开 pcap 文件。

功能:
  - 解析 pcap 文件，提取 HTTP 请求/响应对
  - 左栏：请求列表（时间/方法/URL/状态码/大小/耗时/源IP/目标IP）
  - 右栏：详情 Tab（概览/内容/协议信息/Hex/原始数据），内容内含文本/十六进制/JSON/原始数据/查询字符串等多视图切换
  - 过滤：方法/状态码/域名/关键词搜索，支持 domain:/method:/status: 高级语法
  - 支持大文件流式解析（后台线程，不卡 UI）
  - 导出：JSON / CSV / HAR 格式
  - Hex 查看器：二进制数据查看
  - 多协议识别：HTTP / HTTPS / DNS / TCP
"""

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning, message='.*FFDH.*')

import os
import json
import csv
import time
import threading
from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import Qt, Signal, QTimer, QPoint
from PySide6.QtGui import (
    QIcon, QColor, QFont, QAction, QPainter, QPen, QBrush,
    QPixmap, QKeySequence, QCursor,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QLabel,
    QLineEdit, QComboBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTabWidget, QTextEdit, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QFileDialog, QMessageBox, QAbstractItemView,
    QMenu, QPlainTextEdit, QProgressBar, QSizePolicy, QApplication,
    QToolTip,
)

from ui.ui_styles import (
    get_stylesheet, get_current_theme_id,
    THEMES, DEFAULT_THEME, _parse_rgb, FONT_FAMILY,
)
from ui.dialog_styles import add_green_glow

# 注册 png_rc 资源
from ui import png_rc  # noqa: F401


# ──────────────────────── 引入 PCAP 解析工具 ────────────────────────

from tools.pcap_parser import (
    网络流 as _HttpFlow,
    解析PCAP as _parse_pcap_http,
    格式化Body as _format_body,
    _智能解码,
    _提取JSON,
    状态码颜色 as _status_color,
    协议颜色 as _protocol_color,
    修复PCAP as _repair_pcap,
)


def _copied(text):
    """写入剪贴板并弹出主题跟随的浮层提示（1 秒后消失，不受鼠标事件影响）。"""
    if not text:
        return
    QApplication.clipboard().setText(text)
    # 每次显示时动态取主题色，确保主题切换后下次复制能看到新样式
    theme_id = get_current_theme_id()
    theme = THEMES.get(theme_id, THEMES[DEFAULT_THEME])
    accent = theme['accent']          # 强调色（toast 边框 + 文字）
    bg = theme.get('bg_combo', theme['bg_button'])  # 浮层背景（比 bg_window 稍亮）
    if not hasattr(_copied, '_toast'):
        _copied._toast = QLabel()
        _copied._toast.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        _copied._toast.setAttribute(Qt.WA_TransparentForMouseEvents)
        _copied._toast.setAlignment(Qt.AlignCenter)
    # 主题跟随样式（每次刷新，accent/bg 会随当前主题变化）
    _copied._toast.setStyleSheet(
        f'background: {bg}; color: {accent}; border: 1px solid {accent};'
        'border-radius: 4px; padding: 4px 10px; font-size: 12px;')
    toast = _copied._toast
    toast.setText('已复制到剪贴板')
    if hasattr(_copied, '_timer') and _copied._timer is not None:
        _copied._timer.stop()
    pos = QCursor.pos()
    toast.adjustSize()
    toast.move(pos.x() - toast.width() // 2, pos.y() - toast.height() - 20)
    toast.show()
    toast.raise_()
    _copied._timer = QTimer.singleShot(1000, toast.hide)


# ──────────────────────── 可复制文本编辑控件 ────────────────────────

class _CopyTextEdit(QTextEdit):
    """支持双击复制选中内容和右键复制菜单的 QTextEdit。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        text = self.textCursor().selectedText()
        if text:
            _copied(text)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        act_copy = QAction('复制', self)
        act_copy.setEnabled(has_selection)
        act_copy.triggered.connect(lambda: _copied(cursor.selectedText()))
        act_copy_all = QAction('复制全部', self)
        act_copy_all.triggered.connect(lambda: _copied(self.toPlainText()))
        act_select_all = QAction('全选', self)
        act_select_all.triggered.connect(self.selectAll)
        menu.addAction(act_copy)
        menu.addAction(act_copy_all)
        menu.addSeparator()
        menu.addAction(act_select_all)
        menu.exec(event.globalPos())


class _CopyPlainTextEdit(QPlainTextEdit):
    """支持双击复制选中内容和右键复制菜单的 QPlainTextEdit。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        text = self.textCursor().selectedText()
        if text:
            _copied(text)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        act_copy = QAction('复制', self)
        act_copy.setEnabled(has_selection)
        act_copy.triggered.connect(lambda: _copied(cursor.selectedText()))
        act_copy_all = QAction('复制全部', self)
        act_copy_all.triggered.connect(lambda: _copied(self.toPlainText()))
        act_select_all = QAction('全选', self)
        act_select_all.triggered.connect(self.selectAll)
        menu.addAction(act_copy)
        menu.addAction(act_copy_all)
        menu.addSeparator()
        menu.addAction(act_select_all)
        menu.exec(event.globalPos())


class _CopyTreeWidget(QTreeWidget):
    """支持双击复制单元格、右键菜单、Ctrl+C 的 QTreeWidget。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemDoubleClicked.connect(self._on_double_click)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSelectionMode(QAbstractItemView.SingleSelection)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            items = self.selectedItems()
            if items:
                item = items[0]
                texts = []
                for col in range(item.columnCount()):
                    t = item.text(col)
                    if t:
                        texts.append(t)
                _copied('  '.join(texts))
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_double_click(self, item, column):
        text = item.text(column)
        if text:
            _copied(text)

    def _show_menu(self, pos):
        item = self.itemAt(pos)
        menu = QMenu(self)
        if item:
            col = self.columnAt(pos.x())
            if col < 0:
                col = 0
            cell_text = item.text(col) if 0 <= col < item.columnCount() else ''
            
            if cell_text:
                act_copy_cell = QAction(f'复制单元格', self)
                act_copy_cell.triggered.connect(lambda: _copied(cell_text))
                menu.addAction(act_copy_cell)
            
            texts = []
            for c in range(item.columnCount()):
                t = item.text(c)
                if t:
                    texts.append(t)
            full_text = '  '.join(texts)
            act_copy_row = QAction('复制整行', self)
            act_copy_row.triggered.connect(lambda: _copied(full_text))
            menu.addAction(act_copy_row)
            
            if item.columnCount() > 1:
                menu.addSeparator()
                header = self.headerItem()
                for c in range(item.columnCount()):
                    t = item.text(c)
                    if t:
                        label = header.text(c) if header else f'列{c}'
                        display_text = t[:80] + '...' if len(t) > 80 else t
                        act = QAction(f'复制 [{label}]: {display_text}', self)
                        act.triggered.connect(lambda _=False, txt=t: _copied(txt))
                        menu.addAction(act)
        else:
            act_copy_all = QAction('复制全部', self)
            act_copy_all.triggered.connect(self._copy_all)
            menu.addAction(act_copy_all)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_all(self):
        lines = []
        for i in range(self.topLevelItemCount()):
            item = self.topLevelItem(i)
            texts = []
            for col in range(item.columnCount()):
                texts.append(item.text(col))
            lines.append('  '.join(texts))
        _copied('\n'.join(lines))


class _CopyTableWidget(QTableWidget):
    """支持双击复制单元格、右键菜单、Ctrl+C 的 QTableWidget。"""
    def __init__(self, rowCount=0, columnCount=0, parent=None):
        super().__init__(rowCount, columnCount, parent)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.cellDoubleClicked.connect(self._on_double_click)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Copy):
            rows = self.selectionModel().selectedRows()
            if rows:
                row = rows[0].row()
                row_texts = []
                for c in range(self.columnCount()):
                    it = self.item(row, c)
                    row_texts.append(it.text() if it else '')
                _copied('  '.join(row_texts))
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_double_click(self, row, col):
        item = self.item(row, col)
        if item:
            text = item.text()
            if text:
                _copied(text)

    def _show_menu(self, pos):
        row = self.rowAt(pos.y())
        col = self.columnAt(pos.x())
        menu = QMenu(self)
        if row >= 0 and col >= 0:
            item = self.item(row, col)
            if item:
                text = item.text()
                act_copy = QAction('复制单元格', self)
                act_copy.triggered.connect(lambda: _copied(text))
                menu.addAction(act_copy)
            # 复制整行
            row_texts = []
            headers = []
            for c in range(self.columnCount()):
                it = self.item(row, c)
                hd = self.horizontalHeaderItem(c)
                headers.append(hd.text() if hd else '')
                row_texts.append(it.text() if it else '')
            row_data = '\n'.join(f'{h}: {t}' for h, t in zip(headers, row_texts) if t)
            if row_data:
                act_copy_row = QAction('复制整行', self)
                act_copy_row.triggered.connect(lambda: _copied(row_data))
                menu.addAction(act_copy_row)
        else:
            act_copy_all = QAction('复制全部', self)
            act_copy_all.triggered.connect(self._copy_all)
            menu.addAction(act_copy_all)
        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_all(self):
        lines = []
        for r in range(self.rowCount()):
            row_texts = []
            for c in range(self.columnCount()):
                it = self.item(r, c)
                row_texts.append(it.text() if it else '')
            lines.append('  '.join(row_texts))
        _copied('\n'.join(lines))


# ──────────────────────── 自定义树形控件 ────────────────────────

class _BranchTreeWidget(_CopyTreeWidget):
    """带 +/- 展开折叠指示器和图标的树形控件。"""

    _icon_cache = {}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._branch_color = QColor(120, 120, 120)

    def set_branch_colors(self, indicator_color, bg_color):
        self._branch_color = QColor(indicator_color)
        self.viewport().update()

    @staticmethod
    def make_icon(kind, color='#666666'):
        """生成 16x16 图标。kind: 'folder'/'globe'/'lock'/'page'/'dns'/'flow'/'group'"""
        cache_key = (kind, color)
        if cache_key in _BranchTreeWidget._icon_cache:
            return _BranchTreeWidget._icon_cache[cache_key]

        pm = QPixmap(16, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        c = QColor(color)
        pen = QPen(c)
        pen.setWidth(1)
        p.setPen(pen)

        if kind == 'folder':
            # 文件夹
            fc = QColor(color)
            fc.setAlpha(80)
            p.setBrush(QBrush(fc))
            p.drawRoundedRect(2, 5, 12, 9, 2, 2)
            p.setBrush(Qt.NoBrush)
            p.drawLine(2, 7, 6, 4)
            p.drawLine(6, 4, 14, 4)
            p.drawLine(14, 4, 14, 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(c))
            p.drawRoundedRect(2, 4, 5, 2, 1, 1)

        elif kind == 'globe':
            # 地球
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(1, 1, 14, 14)
            p.drawLine(1, 8, 15, 8)
            p.drawLine(8, 1, 8, 15)
            p.drawArc(3, 3, 10, 10, 0, 180 * 16)
            p.drawArc(3, 3, 10, 10, 180 * 16, 180 * 16)

        elif kind == 'lock':
            # 锁
            p.setBrush(Qt.NoBrush)
            p.drawRect(3, 8, 10, 7)
            p.drawArc(4, 2, 8, 8, 180 * 16, 180 * 16)
            p.drawLine(4, 6, 4, 8)
            p.drawLine(12, 6, 12, 8)
            p.setBrush(QBrush(c))
            p.drawRect(7, 10, 2, 3)

        elif kind == 'page':
            # 文档页
            fc = QColor(color)
            fc.setAlpha(50)
            p.setBrush(QBrush(fc))
            p.drawRect(3, 1, 10, 14)
            p.setBrush(Qt.NoBrush)
            p.drawRect(3, 1, 10, 14)
            p.drawLine(10, 1, 13, 4)
            p.drawLine(10, 4, 13, 4)
            p.drawLine(10, 1, 10, 4)
            p.setPen(pen)
            p.drawLine(5, 7, 11, 7)
            p.drawLine(5, 10, 11, 10)
            p.drawLine(5, 13, 9, 13)

        elif kind == 'dns':
            # DNS 放大镜
            p.drawEllipse(2, 1, 9, 9)
            p.setBrush(QBrush(c))
            p.drawLine(8, 8, 14, 14)
            p.drawLine(10, 10, 14, 14)

        elif kind == 'flow':
            # 数据流
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(1, 3, 14, 10, 2, 2)
            p.drawLine(4, 8, 8, 8)
            p.drawLine(8, 8, 7, 6)
            p.drawLine(8, 8, 7, 10)
            p.drawLine(12, 8, 9, 8)

        elif kind == 'group':
            # 分组
            fc = QColor(color)
            fc.setAlpha(60)
            p.setBrush(QBrush(fc))
            p.drawRect(2, 2, 12, 12)
            p.setBrush(Qt.NoBrush)
            p.drawRect(2, 2, 12, 12)
            p.drawLine(5, 6, 11, 6)
            p.drawLine(5, 9, 11, 9)
            p.drawLine(5, 12, 8, 12)

        p.restore()
        icon = QIcon(pm)
        _BranchTreeWidget._icon_cache[cache_key] = icon
        return icon

    def drawBranches(self, painter, rect, index):
        painter.save()
        painter.setPen(Qt.NoPen)

        item = self.itemFromIndex(index)
        if not item:
            painter.restore()
            return

        indent = self.indentation()
        depth = 0
        idx = index.parent()
        while idx.isValid():
            depth += 1
            idx = idx.parent()

        x = rect.left() + depth * indent + indent // 2
        y = rect.center().y()

        if item.childCount() > 0:
            painter.setBrush(QBrush(self._branch_color))
            if item.isExpanded():
                painter.drawRect(int(x - 5), int(y - 1), 12, 2)
            else:
                painter.drawRect(int(x - 5), int(y - 1), 12, 2)
                painter.drawRect(int(x - 1), int(y - 5), 2, 12)
        else:
            painter.setPen(QPen(self._branch_color, 1, Qt.DashLine))
            painter.drawLine(int(x), int(y + 6), int(x), int(rect.bottom()))

        painter.restore()


# ──────────────────────── 请求/响应体查看器 ────────────────────────

class _BodyViewer(QWidget):
    """Charles 风格的请求/响应体查看器，头部 + 多视图切换。"""

    def __init__(self, mode='request', parent=None):
        super().__init__(parent)
        self._mode = mode
        self._body_bytes = b''
        self._content_type = ''
        self._headers = {}
        self._url = ''

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 子 Tab 行
        self.view_tabs = QTabWidget()
        self.view_tabs.setObjectName('bodyViewTabs')
        self.view_tabs.setDocumentMode(True)
        self.view_tabs.tabBar().setExpanding(False)
        self.view_tabs.currentChanged.connect(self._on_view_changed)
        lay.addWidget(self.view_tabs)

        # 创建视图
        self._editors = {}
        self._build_tabs(mode)

    def _build_tabs(self, mode):
        """根据模式创建不同的子 Tab。"""
        # 头部视图（第一个 Tab）
        e = _BranchTreeWidget()
        e.setHeaderLabels(['名称', '值'])
        e.setColumnWidth(0, 200)
        self._editors['headers'] = e
        self.view_tabs.addTab(e, '头部')

        # 文本视图
        e = _CopyTextEdit()
        e.setReadOnly(True)
        e.setFont(QFont('Consolas', 10))
        self._editors['text'] = e
        self.view_tabs.addTab(e, '文本')

        # 十六进制视图
        e = _CopyPlainTextEdit()
        e.setReadOnly(True)
        e.setFont(QFont('Consolas', 9))
        self._editors['hex'] = e
        self.view_tabs.addTab(e, '十六进制')

        # JSON 视图
        e = _CopyTextEdit()
        e.setReadOnly(True)
        e.setFont(QFont('Consolas', 10))
        self._editors['json'] = e
        self.view_tabs.addTab(e, 'JSON')

        # 原始数据视图
        e = _CopyTextEdit()
        e.setReadOnly(True)
        e.setFont(QFont('Consolas', 9))
        self._editors['raw'] = e
        self.view_tabs.addTab(e, '原始数据')

        if mode == 'request':
            # 查询字符串视图
            e = _BranchTreeWidget()
            e.setHeaderLabels(['参数名', '参数值'])
            e.setColumnWidth(0, 200)
            self._editors['query'] = e
            self.view_tabs.addTab(e, '查询字符串')
        else:
            # Set Cookie 视图
            e = _BranchTreeWidget()
            e.setHeaderLabels(['Cookie 属性', '值'])
            e.setColumnWidth(0, 180)
            self._editors['cookie'] = e
            self.view_tabs.addTab(e, 'Set Cookie')

    def set_data(self, body_bytes, content_type, headers, url=''):
        """设置数据并刷新所有视图。"""
        self._body_bytes = body_bytes or b''
        self._content_type = content_type or ''
        self._headers = headers or {}
        self._url = url or ''
        # 刷新当前视图
        self._on_view_changed(self.view_tabs.currentIndex())

    def clear_all(self):
        """清空所有视图。"""
        for k, e in self._editors.items():
            if isinstance(e, QTreeWidget):
                e.clear()
            else:
                e.clear()

    def _on_view_changed(self, index):
        tab_name = self.view_tabs.tabText(index)
        try:
            if tab_name == '头部':
                self._update_headers()
            elif tab_name == '文本':
                self._update_text()
            elif tab_name == '十六进制':
                self._update_hex()
            elif tab_name == 'JSON':
                self._update_json()
            elif tab_name == '原始数据':
                self._update_raw()
            elif tab_name == '查询字符串':
                self._update_query()
            elif tab_name == 'Set Cookie':
                self._update_cookie()
        except KeyError:
            # _build_tabs 过程中 currentChanged 可能在某些 editor 还未创建时触发
            pass

    def _update_headers(self):
        """头部视图：显示所有头信息。"""
        tree = self._editors['headers']
        tree.clear()
        if not self._headers:
            item = QTreeWidgetItem(['（无头信息）', ''])
            tree.addTopLevelItem(item)
            return
        for k, v in self._headers.items():
            if isinstance(v, list):
                for i, val in enumerate(v):
                    label = k if i == 0 else ''
                    item = QTreeWidgetItem([label, str(val)])
                    tree.addTopLevelItem(item)
            else:
                item = QTreeWidgetItem([k, str(v)])
                tree.addTopLevelItem(item)

    def _update_text(self):
        """文本视图：智能格式化显示。"""
        text = _format_body(self._body_bytes, self._content_type, self._headers)
        self._editors['text'].setPlainText(text or '（空）')

    def _update_hex(self):
        """十六进制视图。"""
        if not self._body_bytes:
            self._editors['hex'].setPlainText('（空）')
            return
        data = self._body_bytes
        lines = []
        for offset in range(0, len(data), 16):
            chunk = data[offset:offset + 16]
            hex_part = ' '.join(f'{b:02x}' for b in chunk)
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            lines.append(f'{offset:08x}  {hex_part:<48s}  {ascii_part}')
        self._editors['hex'].setPlainText('\n'.join(lines))

    def _update_json(self):
        """JSON 视图：尝试解析并格式化，支持从混合文本中提取 JSON。"""
        raw = self._body_bytes
        if not raw:
            self._editors['json'].setPlainText('（空）')
            return
        xenc = self._headers.get('x-encryption', '') if self._headers else ''
        if xenc:
            text = raw.decode('utf-8', errors='replace')
            self._editors['json'].setPlainText(text[:50000])
            return
        text, used_enc = _智能解码(raw, self._content_type, self._headers)
        try:
            import json as _json
            obj = _json.loads(text)
            pretty = _json.dumps(obj, indent=2, ensure_ascii=False)
            self._editors['json'].setPlainText(pretty)
            return
        except Exception:
            pass
        extracted = _提取JSON(text)
        if extracted:
            try:
                import json as _json
                obj = _json.loads(extracted)
                pretty = _json.dumps(obj, indent=2, ensure_ascii=False)
                self._editors['json'].setPlainText(pretty)
                return
            except Exception:
                pass
        self._editors['json'].setPlainText(text[:20000])

    def _update_raw(self):
        """原始数据视图。"""
        if not self._body_bytes:
            self._editors['raw'].setPlainText('（空）')
            return
        text, enc = _智能解码(self._body_bytes, self._content_type, self._headers)
        self._editors['raw'].setPlainText(text)

    def _update_query(self):
        """查询字符串视图：解析 URL 参数。"""
        tree = self._editors['query']
        tree.clear()
        from urllib.parse import parse_qs, urlparse
        if not self._url:
            return
        parsed = urlparse(self._url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        if not params:
            item = QTreeWidgetItem(['（无查询参数）', ''])
            tree.addTopLevelItem(item)
            return
        for key, values in params.items():
            for val in values:
                item = QTreeWidgetItem([key, val])
                tree.addTopLevelItem(item)

    def _update_cookie(self):
        """Set Cookie 视图：解析 Set-Cookie 头。"""
        tree = self._editors['cookie']
        tree.clear()
        set_cookies = self._headers.get('set-cookie', '')
        if not set_cookies:
            item = QTreeWidgetItem(['（无 Set-Cookie）', ''])
            tree.addTopLevelItem(item)
            return
        # Set-Cookie 可能是 list 或 string
        if isinstance(set_cookies, list):
            cookies = set_cookies
        else:
            cookies = [set_cookies]
        for cookie in cookies:
            # 解析: name=value; attr1=val1; attr2
            parts = cookie.split(';')
            if parts:
                first = parts[0].strip()
                if '=' in first:
                    name, value = first.split('=', 1)
                else:
                    name, value = first, ''
                item = QTreeWidgetItem([name.strip(), value.strip()])
                for part in parts[1:]:
                    part = part.strip()
                    if '=' in part:
                        attr_name, attr_val = part.split('=', 1)
                    else:
                        attr_name, attr_val = part, ''
                    child = QTreeWidgetItem([attr_name.strip(), attr_val.strip()])
                    item.addChild(child)
                tree.addTopLevelItem(item)
                item.setExpanded(True)


# ──────────────────────── 对话框 ────────────────────────

class Pcap解析对话框(QWidget):
    """PCAP 解析器独立窗口。支持拖拽 pcap 文件。"""

    _parse_done = Signal(list, int, str, dict)  # flows, total_pkts, error, stats
    _parse_progress = Signal(int)

    def __init__(self, pcap_path='', parent=None):
        super().__init__(parent)
        self._pcap_path = pcap_path
        self._flows = []
        self._filtered_flows = []
        self._current_flow = None
        self._parse_thread = None
        self._cancel_parse = False

        self.setWindowTitle('PCAP 解析器')
        self.setWindowIcon(QIcon(':/Super_ADB.png'))
        self.setMinimumSize(1100, 680)
        self.resize(1280, 800)
        self._theme_id = get_current_theme_id(self)
        # 全局样式：所有子控件跟随主题
        self.setStyleSheet(self._global_style(self._theme_id))
        # 独立窗口：不设 parent，避免 Windows owned-window 始终置顶行为
        self.setWindowFlag(Qt.Window, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)

        # 启用拖拽
        self.setAcceptDrops(True)
        self.setToolTip('PCAP 解析器')

        # 主布局：让 card 铺满整个窗口
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(8, 8, 8, 8)
        main_lay.setSpacing(0)

        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        # 不给 card 单独设样式表，否则会阻止 self 全局样式应用到子控件
        # card 的圆角边框/背景由全局样式中的 #popupCard 选择器负责
        accent = THEMES.get(self._theme_id, THEMES[DEFAULT_THEME])['accent']
        r, g, b = _parse_rgb(accent)
        add_green_glow(self.card, accent=QColor(r, g, b))
        main_lay.addWidget(self.card)

        self._build_ui()
        self._parse_done.connect(self._on_parse_done)
        self._parse_progress.connect(self._on_parse_progress)

        if pcap_path and os.path.isfile(pcap_path):
            self._set_path_label(pcap_path)
            QTimer.singleShot(100, self._start_parse)
        else:
            QTimer.singleShot(200, self._show_drag_overlay)

    # ── 拖拽支持 ──

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            for url in urls:
                path = url.toLocalFile()
                if path.lower().endswith(('.pcap', '.pcapng', '.cap')):
                    event.acceptProposedAction()
                    self._set_drag_highlight(True)
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(('.pcap', '.pcapng', '.cap')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_highlight(False)

    def dropEvent(self, event):
        self._set_drag_highlight(False)
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(('.pcap', '.pcapng', '.cap')) and os.path.isfile(path):
                    self._load_pcap(path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def _set_drag_highlight(self, on):
        """设置拖拽高亮：用动态属性 + 全局样式选择器，避免覆盖子控件样式。"""
        self.card.setProperty('drag_highlight', 'true' if on else 'false')
        # 强制重新应用样式
        self.card.style().unpolish(self.card)
        self.card.style().polish(self.card)
        self.card.update()

    def _show_drag_overlay(self):
        """显示拖拽提示覆盖层。"""
        if not hasattr(self, '_drag_overlay') or self._drag_overlay is None:
            t = self._resolve_theme(self._theme_id)
            self._drag_overlay = QWidget(self.card)
            self._drag_overlay.setObjectName('dragOverlay')
            self._drag_overlay.setStyleSheet(
                'QWidget#dragOverlay { background-color: rgba(0,0,0,0.75); }'
            )
            lay = QVBoxLayout(self._drag_overlay)
            lay.setAlignment(Qt.AlignCenter)
            tip = QLabel('📂 拖拽 PCAP 文件到这里')
            tip.setAlignment(Qt.AlignCenter)
            tip.setStyleSheet(f'font-size: 22px; color: {t["accent"]}; font-weight: bold; background: transparent;')
            sub = QLabel('支持 .pcap / .pcapng / .cap')
            sub.setAlignment(Qt.AlignCenter)
            sub.setStyleSheet(f'font-size: 14px; color: {t["text_secondary"]}; background: transparent;')
            lay.addWidget(tip)
            lay.addWidget(sub)
            btn = QPushButton('📂 点击选择文件')
            btn.setFixedWidth(180)
            btn.setStyleSheet('font-size: 14px; padding: 8px 16px;')
            btn.clicked.connect(self._open_file)
            btn_wrap = QHBoxLayout()
            btn_wrap.addStretch()
            btn_wrap.addWidget(btn)
            btn_wrap.addStretch()
            lay.addLayout(btn_wrap)
        self._drag_overlay.setGeometry(self.card.rect())
        self._drag_overlay.raise_()
        self._drag_overlay.show()

    def _hide_drag_overlay(self):
        """隐藏拖拽提示覆盖层。"""
        if hasattr(self, '_drag_overlay') and self._drag_overlay is not None:
            self._drag_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_drag_overlay') and self._drag_overlay is not None and self._drag_overlay.isVisible():
            self._drag_overlay.setGeometry(self.card.rect())

    def _load_pcap(self, path):
        """加载并解析 pcap 文件。"""
        self._pcap_path = path
        self._set_path_label(path)
        self._hide_drag_overlay()
        self._start_parse()

    def _set_path_label(self, path):
        """设置窗口标题显示文件名，完整路径放 tooltip。"""
        filename = os.path.basename(path)
        self.setWindowTitle(f'PCAP 解析器 — {filename}')
        self.setToolTip(path)

    # ── UI ──

    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        # 工具栏
        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.setContentsMargins(0, 0, 0, 0)

        self.btn_open = QPushButton('📂 打开')
        self.btn_open.setMinimumWidth(80)
        self.btn_open.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_open.clicked.connect(self._open_file)
        bar.addWidget(self.btn_open)

        self.btn_export = QPushButton('📤 导出')
        self.btn_export.setMinimumWidth(80)
        self.btn_export.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_export.clicked.connect(self._export_data)
        self.btn_export.setEnabled(False)
        bar.addWidget(self.btn_export)

        self.btn_cancel = QPushButton('⏹ 取消')
        self.btn_cancel.setMinimumWidth(70)
        self.btn_cancel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_cancel.clicked.connect(self._cancel_parsing)
        self.btn_cancel.setEnabled(False)
        bar.addWidget(self.btn_cancel)

        self.btn_repair = QPushButton('🔧 修复')
        self.btn_repair.setMinimumWidth(70)
        self.btn_repair.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_repair.clicked.connect(self._repair_file)
        bar.addWidget(self.btn_repair)

        bar.addSpacing(6)

        # 方法过滤
        bar.addWidget(QLabel('方法:'))
        self.method_combo = QComboBox()
        self.method_combo.addItems(['全部', 'GET', 'POST', 'PUT', 'DELETE', 'HEAD', 'OPTIONS', 'PATCH', 'TLS', 'DNS', 'TCP'])
        self.method_combo.setMinimumWidth(70)
        self.method_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.method_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.method_combo)

        # 状态码过滤
        bar.addWidget(QLabel('状态:'))
        self.status_combo = QComboBox()
        self.status_combo.addItems(['全部', '2xx', '3xx', '4xx', '5xx', '无响应'])
        self.status_combo.setMinimumWidth(60)
        self.status_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.status_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.status_combo)

        # 协议过滤
        bar.addWidget(QLabel('协议:'))
        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(['全部', 'HTTP', 'HTTPS', 'WebSocket', 'DNS', 'TCP'])
        self.protocol_combo.setMinimumWidth(75)
        self.protocol_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.protocol_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.protocol_combo)

        # 域名过滤
        bar.addWidget(QLabel('域名:'))
        self.domain_combo = QComboBox()
        self.domain_combo.setMinimumWidth(100)
        self.domain_combo.setEditable(True)
        self.domain_combo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.domain_combo.currentIndexChanged.connect(self._apply_filter)
        bar.addWidget(self.domain_combo)

        # 搜索框
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText('搜索 (domain: method: status:)')
        self.search_edit.setMinimumWidth(150)
        self.search_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search_edit)

        self.btn_clear = QPushButton('清除')
        self.btn_clear.setMinimumWidth(55)
        self.btn_clear.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_clear.clicked.connect(self._clear_filter)
        bar.addWidget(self.btn_clear)

        lay.addLayout(bar)

        # 统计栏
        stat_bar = QHBoxLayout()
        self._stat_label = QLabel('就绪')
        self._stat_label.setObjectName('tipLabel')
        stat_bar.addWidget(self._stat_label)
        stat_bar.addStretch(1)
        lay.addLayout(stat_bar)

        # 主体：左右分栏
        splitter = QSplitter(Qt.Horizontal)

        # 左栏：请求列表
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)

        # 搜索提示
        self._search_hint = QLabel('')
        self._search_hint.setObjectName('tipLabel')
        self._search_hint.setStyleSheet('color: #FFC107;')
        self._search_hint.setVisible(False)
        left_lay.addWidget(self._search_hint)

        # 使用 QTabWidget 支持双视图切换 (Charles风格: 结构/序列)
        self.left_tabs = QTabWidget()
        self.left_tabs.setObjectName('leftTabs')
        self.left_tabs.setDocumentMode(True)
        self.left_tabs.tabBar().setExpanding(False)
        self.left_tabs.tabBar().setFixedHeight(28)
        self.left_tabs.currentChanged.connect(self._on_tab_changed)
        
        # --- 结构视图 (Tree) ---
        self.structure_tree = _BranchTreeWidget()
        self.structure_tree.setHeaderLabels(['名称', '方法/状态', '大小'])
        self.structure_tree.setRootIsDecorated(True)
        self.structure_tree.setIndentation(16)
        self.structure_tree.setAnimated(True)
        self.structure_tree.setExpandsOnDoubleClick(True)
        tree_header = self.structure_tree.header()
        tree_header.setMinimumSectionSize(30)
        tree_header.setStretchLastSection(True)
        self.structure_tree.setColumnWidth(0, 250)
        self.structure_tree.setColumnWidth(1, 100)
        tree_header.setSectionResizeMode(0, QHeaderView.Interactive)
        tree_header.setSectionResizeMode(1, QHeaderView.Interactive)
        tree_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.structure_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.structure_tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.structure_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.structure_tree.customContextMenuRequested.disconnect(self.structure_tree._show_menu)
        self.structure_tree.customContextMenuRequested.connect(self._show_context_menu)
        self.structure_tree.itemClicked.connect(self._on_tree_item_clicked)

        # 用容器包裹，留出 pane 边框可见空间
        tree_container = QWidget()
        tree_container.setObjectName('leftPage')
        tree_lay = QVBoxLayout(tree_container)
        tree_lay.setContentsMargins(1, 0, 1, 1)
        tree_lay.setSpacing(0)
        tree_lay.addWidget(self.structure_tree)
        self.left_tabs.addTab(tree_container, '📁 结构')

        # 同步结构树分支颜色
        theme = self._resolve_theme(self._theme_id)
        self.structure_tree.set_branch_colors(
            theme.get('text_secondary', '#888888'),
            theme.get('bg', '#ffffff'),
        )
        
        # --- 序列视图 (Table) ---
        self.table = _CopyTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            '#', '协议', '时间', '方法', 'URL', '状态', '大小'
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        
        # 允许所有列手动拖拽调整宽度
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(30)
        header.setStretchLastSection(True)
        
        # 设置各列初始宽度和调整模式
        self.table.setColumnWidth(0, 45)
        self.table.setColumnWidth(1, 65)
        self.table.setColumnWidth(2, 95)
        self.table.setColumnWidth(3, 70)
        self.table.setColumnWidth(4, 400)
        self.table.setColumnWidth(5, 65)
        self.table.setColumnWidth(6, 80)
        
        # 所有列设为 Interactive（可手动拖拽），最后一列用 Stretch
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        header.setSectionResizeMode(4, QHeaderView.Interactive)
        header.setSectionResizeMode(5, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
        
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().sortIndicatorChanged.connect(
            self._on_sort_changed)
        self.table.itemSelectionChanged.connect(self._on_select_flow)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.disconnect(self.table._show_menu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.clicked.connect(self._on_clicked)

        tbl_container = QWidget()
        tbl_container.setObjectName('leftPage')
        tbl_lay = QVBoxLayout(tbl_container)
        tbl_lay.setContentsMargins(1, 0, 1, 1)
        tbl_lay.setSpacing(0)
        tbl_lay.addWidget(self.table)
        self.left_tabs.addTab(tbl_container, '📋 序列')
        
        left_lay.addWidget(self.left_tabs, 1)

        splitter.addWidget(left)

        # 右栏：详情
        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setFixedHeight(28)
        
        # 通用表格列设置函数
        def _setup_tree_columns(tree, col0_width):
            h = tree.header()
            h.setMinimumSectionSize(40)
            h.setStretchLastSection(True)
            tree.setColumnWidth(0, col0_width)
            h.setSectionResizeMode(0, QHeaderView.Interactive)
            h.setSectionResizeMode(1, QHeaderView.Stretch)
            tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
            return tree
        
        # 概览
        self.overview_tree = _BranchTreeWidget()
        self.overview_tree.setHeaderLabels(['属性', '值'])
        _setup_tree_columns(self.overview_tree, 140)
        self.tabs.addTab(self.overview_tree, '概览')

        # 内容 — 上：请求体（含头部） / 下：响应体（含头部）
        self.content_tab = QWidget()
        content_lay = QVBoxLayout(self.content_tab)
        content_lay.setContentsMargins(4, 4, 4, 4)
        content_lay.setSpacing(2)

        self.req_body_viewer = _BodyViewer(mode='request')
        self.resp_body_viewer = _BodyViewer(mode='response')

        self.content_splitter = QSplitter(Qt.Orientation.Vertical)
        self.content_splitter.addWidget(self.req_body_viewer)
        self.content_splitter.addWidget(self.resp_body_viewer)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 1)
        self.content_splitter.setSizes([300, 300])

        # 初始化内容体内视图分支颜色
        _t = self._resolve_theme(self._theme_id)
        for viewer in (self.req_body_viewer, self.resp_body_viewer):
            for editor in viewer._editors.values():
                if isinstance(editor, _BranchTreeWidget):
                    editor.set_branch_colors(
                        _t['text_secondary'], _t['bg']
                    )

        content_lay.addWidget(self.content_splitter)
        self.tabs.addTab(self.content_tab, '内容')

        # 协议信息
        self.protocol_tree = _CopyTreeWidget()
        self.protocol_tree.setHeaderLabels(['属性', '值'])
        _setup_tree_columns(self.protocol_tree, 160)
        self.tabs.addTab(self.protocol_tree, '协议信息')

        # Hex 查看器
        self.hex_edit = _CopyPlainTextEdit()
        self.hex_edit.setReadOnly(True)
        self.hex_edit.setFont(QFont('Consolas', 9))
        self.tabs.addTab(self.hex_edit, 'Hex')

        # 原始数据
        self.raw_edit = _CopyTextEdit()
        self.raw_edit.setReadOnly(True)
        self.raw_edit.setFont(QFont('Consolas', 9))
        self.tabs.addTab(self.raw_edit, '原始数据')

        right_lay.addWidget(self.tabs, 1)

        splitter.addWidget(right)
        splitter.setSizes([520, 760])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        lay.addWidget(splitter, 1)

    # ── 打开文件 ──

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择 PCAP 文件', '',
            'PCAP 文件 (*.pcap *.pcapng *.cap);;所有文件 (*.*)'
        )
        if path:
            self._load_pcap(path)

    # ── 解析 ──

    def _start_parse(self):
        if not self._pcap_path or not os.path.isfile(self._pcap_path):
            return

        if not self._check_dependencies():
            return

        # 取消之前的解析
        self._cancel_parse = True
        if self._parse_thread and self._parse_thread.is_alive():
            self._parse_thread.join(timeout=1)

        self._cancel_parse = False
        self._flows = []
        self._filtered_flows = []
        self.table.setRowCount(0)
        self._clear_detail()

        # 进度条
        if not hasattr(self, '_progress_bar'):
            self._progress_bar = QProgressBar()
            self._progress_bar.setMaximum(0)  # 不确定模式
            self._progress_bar.setFixedHeight(4)
            self._progress_bar.setTextVisible(False)
            self._progress_bar.setStyleSheet(
                'QProgressBar{border:none;background:transparent;}'
                'QProgressBar::chunk{border-radius:2px;}'
            )
            # 插入到状态栏
            layout = self._stat_label.parentWidget().layout()
            idx = layout.indexOf(self._stat_label)
            layout.insertWidget(idx + 1, self._progress_bar, 1)

        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(0)
        self._stat_label.setText('正在解析... 初始化中')
        self.btn_open.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._parse_start_time = time.time()

        path = self._pcap_path

        def _worker():
            flows, total, error, stats = _parse_pcap_http(
                path,
                progress_cb=lambda n: self._parse_progress.emit(n),
                cancel_cb=lambda: self._cancel_parse,
            )
            self._parse_done.emit(flows, total, error, stats)

        self._parse_thread = threading.Thread(target=_worker, daemon=True)
        self._parse_thread.start()

    def _on_parse_progress(self, n):
        elapsed = max(time.time() - self._parse_start_time, 0.001)
        speed = n / elapsed
        if speed >= 1000:
            speed_str = f'{speed / 1000:.1f}K包/s'
        elif speed >= 1:
            speed_str = f'{speed:.0f}包/s'
        else:
            speed_str = f'{speed * 1000:.0f}ms/包'
        self._stat_label.setText(
            f'正在解析... 已读 {n} 包 · {speed_str} · 用时 {elapsed:.1f}s'
        )

    def _cancel_parsing(self):
        if self._parse_thread and self._parse_thread.is_alive():
            self._cancel_parse = True
            self._stat_label.setText('正在取消...')
            self.btn_cancel.setEnabled(False)

    def _repair_file(self):
        """修复损坏的 pcap 文件。"""
        if not self._pcap_path or not os.path.isfile(self._pcap_path):
            file_path, _ = QFileDialog.getOpenFileName(
                self, '选择要修复的 PCAP 文件', '',
                'PCAP 文件 (*.pcap *.pcapng *.cap);;所有文件 (*)')
            if not file_path:
                return
        else:
            file_path = self._pcap_path

        self._stat_label.setText(f'正在修复 {os.path.basename(file_path)}...')
        QApplication.processEvents()

        success, message, count = _repair_pcap(file_path)
        
        if success:
            QMessageBox.information(
                self, '修复成功',
                f'{message}\n\n是否打开修复后的文件？'
            )
            fixed_path = file_path + '.fixed'
            if os.path.isfile(fixed_path):
                self._load_pcap(fixed_path)
        else:
            QMessageBox.warning(
                self, '修复失败',
                f'{message}\n\n建议：\n1. 尝试重新抓包\n2. 使用 Wireshark 的编辑功能手动修复\n3. 使用其他工具转换格式'
            )
            self._stat_label.setText(f'修复失败: {message}')

    def _on_parse_done(self, flows, total_pkts, error, stats=None):
        self.btn_open.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if hasattr(self, '_progress_bar'):
            self._progress_bar.setVisible(False)

        elapsed = time.time() - self._parse_start_time
        is_cancelled = (error == 'cancelled') or self._cancel_parse

        if is_cancelled:
            if flows:
                self.btn_export.setEnabled(len(flows) > 0)
                self._flows = flows
                self._filtered_flows = list(flows)
                for f in self._flows:
                    f._searchable_text = (f.url + f.method + f.host + f.protocol
                                          + f.req_body.decode('utf-8', errors='replace')
                                          + f.resp_body.decode('utf-8', errors='replace')).lower()
                self._populate_table()
                self._populate_domains()
                self._stat_label.setText(
                    f'已取消 · 已解析 {len(flows)} 个流 / {total_pkts} 包 · 用时 {elapsed:.1f}s'
                )
            else:
                self._stat_label.setText(
                    f'已取消 · 已读 {total_pkts} 包，未解析到流 · 用时 {elapsed:.1f}s'
                )
            return

        self._flows = flows
        self._filtered_flows = list(flows)

        for f in self._flows:
            f._searchable_text = (f.url + f.method + f.host + f.protocol
                                  + f.req_body.decode('utf-8', errors='replace')
                                  + f.resp_body.decode('utf-8', errors='replace')).lower()

        self._populate_table()
        self._populate_domains()
        self._build_structure_tree()
        self.btn_export.setEnabled(len(flows) > 0)

        proto_counts = defaultdict(int)
        for f in flows:
            proto_counts[f.protocol] += 1

        stat_parts = [f'共 {len(flows)} 个流']
        for proto, count in sorted(proto_counts.items(), key=lambda x: -x[1]):
            stat_parts.append(f'{proto}:{count}')
        stat_parts.append(f'总包: {total_pkts}')

        # 添加包类型统计
        if stats and stats.get('ip', 0) + stats.get('non_ip', 0) > 0:
            total = stats.get('ip', 0) + stats.get('non_ip', 0)
            ip_pct = stats.get('ip', 0) / max(total, 1) * 100
            non_ip_pct = stats.get('non_ip', 0) / max(total, 1) * 100
            stat_parts.append(f'IP:{stats.get("ip",0)}({ip_pct:.0f}%)')
            stat_parts.append(f'非IP:{stats.get("non_ip",0)}({non_ip_pct:.0f}%)')
            if stats.get('tcp', 0) > 0:
                stat_parts.append(f'TCP:{stats.get("tcp",0)}')
            if stats.get('udp', 0) > 0:
                stat_parts.append(f'UDP:{stats.get("udp",0)}')
            if stats.get('http', 0) > 0:
                stat_parts.append(f'HTTP包:{stats.get("http",0)}')
            if stats.get('tls', 0) > 0:
                stat_parts.append(f'TLS包:{stats.get("tls",0)}')

        stat_parts.append(f'用时 {elapsed:.1f}s')

        if error:
            self._stat_label.setText(
                f'解析完成 [{"/".join(stat_parts)}] (错误: {error})'
            )
        else:
            self._stat_label.setText(
                f'解析完成 [{"/".join(stat_parts)}]'
            )

        if not flows:
            stats_hint = ''
            if stats and stats.get('non_ip', 0) > stats.get('ip', 0):
                stats_hint = f'多数为非IP流量({stats.get("non_ip",0)}包)，可能是组播/ARP等'
            self._stat_label.setText(
                f'未找到可解析的流量 (共 {total_pkts} 包, {elapsed:.1f}s)。'
                f'{stats_hint}'
                f'可能是 HTTPS(加密)、DNS 或其他协议。'
            )

    # ── 表格 ──

    def _populate_table(self):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for flow_idx, flow in enumerate(self._filtered_flows):
            row = self.table.rowCount()
            self.table.insertRow(row)

            # 序号（存储 flow_idx 在 UserRole 用于排序后索引映射）
            item = QTableWidgetItem(str(flow.idx))
            item.setTextAlignment(Qt.AlignCenter)
            item.setData(Qt.UserRole, flow_idx)
            self.table.setItem(row, 0, item)

            # 协议
            item = QTableWidgetItem(flow.protocol)
            item.setTextAlignment(Qt.AlignCenter)
            item.setForeground(QColor(_protocol_color(flow.protocol)))
            self.table.setItem(row, 1, item)

            # 时间
            ts_str = time.strftime('%H:%M:%S', time.localtime(flow.ts))
            ts_str += f'.{int((flow.ts % 1) * 1000):03d}'
            item = QTableWidgetItem(ts_str)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, item)

            # 方法
            item = QTableWidgetItem(flow.method)
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 3, item)

            # URL (智能截断)
            display_url = flow.url
            if len(display_url) > 100:
                display_url = display_url[:47] + '...' + display_url[-50:]
            item = QTableWidgetItem(display_url)
            item.setData(Qt.UserRole, flow.url)  # 存完整 URL
            self.table.setItem(row, 4, item)

            # 状态码
            if flow.status_code:
                status_str = str(flow.status_code)
                item = QTableWidgetItem(status_str)
                item.setForeground(QColor(_status_color(flow.status_code)))
            else:
                item = QTableWidgetItem('—')
                item.setForeground(QColor('#999999'))
            item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 5, item)

            # 大小
            total_len = flow.req_len + flow.resp_len
            size_str = _format_size(total_len)
            item = QTableWidgetItem(size_str)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 6, item)

            # 行染色（按状态码）
            if flow.status_code:
                bg_color = QColor(_status_color(flow.status_code))
                bg_color.setAlpha(30)  # 半透明
                for col in range(10):
                    cell = self.table.item(row, col)
                    if cell:
                        cell.setBackground(bg_color)

        self.table.setSortingEnabled(True)
        # 重新应用列宽调整模式（排序可能重置）
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        header.setSectionResizeMode(6, QHeaderView.Stretch)
    
    def _on_tab_changed(self, index: int):
        """当用户切换tab时触发。"""
        if index == 0:  # 结构视图
            self._build_structure_tree()

    def _build_structure_tree(self):
        """构建结构视图：按域名分组，再按 URL 路径层级多级折叠展示。"""
        try:
            self.structure_tree.clear()
            
            if not self._filtered_flows:
                return
            
            # 如果在后台解析中，等数据到达再构建
            if not self._flows and hasattr(self, '_pcap_parser'):
                return
            
            theme = self._resolve_theme(self._theme_id)
            icon_text = theme['text_secondary']
            icon_accent = theme['accent']
            
            # 按域名分组
            域名映射 = {}
            未知列表 = []
            for flow_idx, flow in enumerate(self._filtered_flows):
                try:
                    域名 = flow.host or flow.tls_sni or ''
                    if not 域名 or 域名 in ('—', '未知'):
                        未知列表.append((flow_idx, flow))
                        continue
                    if 域名 not in 域名映射:
                        域名映射[域名] = []
                    域名映射[域名].append((flow_idx, flow))
                except Exception:
                    未知列表.append((flow_idx, flow))
            
            # 添加域名分组
            for 域名, 流列表 in sorted(域名映射.items()):
                try:
                    域名项 = QTreeWidgetItem(self.structure_tree, [域名, '', str(len(流列表))])
                    域名项.setData(0, Qt.UserRole, ('domain', 域名))
                    域名项.setExpanded(False)
                    
                    # 根据协议选择图标
                    has_https = any(
                        (f.protocol or '').upper() in ('TLS', 'HTTPS')
                        for _, f in 流列表
                    )
                    has_http = any(
                        (f.protocol or '').upper() in ('HTTP', 'WEBSOCKET')
                        for _, f in 流列表
                    )
                    if has_https and not has_http:
                        域名项.setIcon(0, _BranchTreeWidget.make_icon('lock', icon_text))
                    elif has_http:
                        域名项.setIcon(0, _BranchTreeWidget.make_icon('globe', icon_accent))
                    else:
                        域名项.setIcon(0, _BranchTreeWidget.make_icon('folder', icon_text))
                    
                    # 按 URL 路径层级构建子树
                    self._build_path_tree(域名项, 流列表, theme)
                except Exception:
                    pass
            
            # 未知/无域名流量
            if 未知列表:
                try:
                    域名项 = QTreeWidgetItem(self.structure_tree, ['其他 (无域名)', '', str(len(未知列表))])
                    域名项.setData(0, Qt.UserRole, ('domain', '其他'))
                    域名项.setIcon(0, _BranchTreeWidget.make_icon('group', icon_text))
                    for flow_idx, flow in 未知列表:
                        try:
                            self._add_flow_tree_item(域名项, flow_idx, flow)
                        except Exception:
                            pass
                except Exception:
                    pass

            self.structure_tree.collapseAll()
        except Exception:
            pass

    def _build_path_tree(self, parent_item, 流列表, theme):
        """按 URL 路径层级构建多级折叠树。"""
        path_tree = {}
        非HTTP列表 = []
        
        for flow_idx, flow in 流列表:
            协议 = (flow.protocol or '').upper()
            if 协议 not in ('HTTP', 'WEBSOCKET'):
                非HTTP列表.append((flow_idx, flow))
                continue
            
            路径 = flow.path or flow.url or '/'
            # 保存完整路径（含查询参数）用于显示最后一级
            完整路径 = 路径
            # 解析路径段（不含查询参数）
            纯路径 = 路径.split('?')[0].split('#')[0].rstrip('/')
            if not 纯路径:
                纯路径 = '/'
            段列表 = [s for s in 纯路径.split('/') if s]
            if not 段列表:
                段列表 = ['/']
            
            # 最后一级显示名：最后一个路径段 + 查询参数
            最后段 = 段列表[-1] if 段列表 else ''
            if '?' in 完整路径:
                查询串 = 完整路径.split('?', 1)[1]
                显示名 = f'{最后段}?{查询串}'
            else:
                显示名 = 最后段
            
            node = path_tree
            for 段 in 段列表:
                if 'children' not in node:
                    node['children'] = {}
                if 段 not in node['children']:
                    node['children'][段] = {'_flows': [], 'children': {}}
                node = node['children'][段]
            node['_flows'].append((flow_idx, flow, 显示名))
        
        # 渲染路径树
        self._render_path_nodes(parent_item, path_tree, theme, 0)
        
        # 非 HTTP 协议直接挂在域名下
        for flow_idx, flow in 非HTTP列表:
            try:
                self._add_flow_tree_item(parent_item, flow_idx, flow)
            except Exception:
                pass

    def _render_path_nodes(self, parent_item, node, theme, depth):
        """递归渲染路径节点。"""
        icon_color = theme['text_secondary']
        
        # 先渲染当前节点的 flows（使用预计算的显示名）
        for flow_idx, flow, 显示名 in node.get('_flows', []):
            try:
                self._add_flow_tree_item(parent_item, flow_idx, flow, 显示名)
            except Exception:
                pass
        
        # 渲染子目录
        for 段名, 子节点 in sorted(node.get('children', {}).items()):
            子项 = QTreeWidgetItem(parent_item, [段名, '', ''])
            子项.setData(0, Qt.UserRole, ('path', 段名))
            子项.setExpanded(False)
            flow_count = self._count_flows(子节点)
            子项.setText(2, str(flow_count))
            子项.setIcon(0, _BranchTreeWidget.make_icon('folder', icon_color))
            self._render_path_nodes(子项, 子节点, theme, depth + 1)

    def _count_flows(self, node):
        """递归统计节点下的 flow 数量。"""
        count = len(node.get('_flows', []))
        for 子节点 in node.get('children', {}).values():
            count += self._count_flows(子节点)
        return count

    def _add_flow_tree_item(self, parent_item, flow_idx, flow, 显示名称=None):
        """为结构树添加单个流条目。显示名称为 None 时自动生成。"""
        协议 = flow.protocol or 'TCP'
        大小 = _format_size(flow.req_len + flow.resp_len)
        
        theme = self._resolve_theme(self._theme_id)
        icon_color = theme['text_secondary']
        
        if 协议 in ('HTTP', 'WebSocket'):
            方法 = flow.method or 'GET'
            if 显示名称 is not None:
                显示文本 = 显示名称[:80] + ('...' if len(显示名称) > 80 else '')
            else:
                路径 = flow.path or flow.url or '/'
                显示文本 = 路径[:80] + ('...' if len(路径) > 80 else '')
            if flow.status_code:
                状态 = f'{方法} {flow.status_code}'
                颜色 = _status_color(flow.status_code)
            else:
                状态 = 方法
                颜色 = '#CCCCCC'
            
            请求项 = QTreeWidgetItem(parent_item, [显示文本, 状态, 大小])
            请求项.setData(0, Qt.UserRole, ('flow', flow_idx))
            请求项.setForeground(1, QColor(颜色))
            请求项.setIcon(0, _BranchTreeWidget.make_icon('page', theme['accent']))
            try:
                ts_str = time.strftime('%H:%M:%S', time.localtime(flow.ts))
                请求项.setToolTip(0, f'完整URL: {flow.url}\n时间: {ts_str}')
            except Exception:
                pass
        elif 协议 == 'TLS':
            SNI = flow.tls_sni or ''
            请求项 = QTreeWidgetItem(parent_item, [
                f'{SNI or "TLS握手"}', 'TLS', 大小
            ])
            请求项.setData(0, Qt.UserRole, ('flow', flow_idx))
            请求项.setForeground(1, QColor('#FFC107'))
            请求项.setIcon(0, _BranchTreeWidget.make_icon('lock', '#FFC107'))
        elif 协议 == 'DNS':
            查询 = flow.dns_query or 'DNS'
            请求项 = QTreeWidgetItem(parent_item, [
                f'{查询}', 'DNS', 大小
            ])
            请求项.setData(0, Qt.UserRole, ('flow', flow_idx))
            请求项.setForeground(1, QColor('#03A9F4'))
            请求项.setIcon(0, _BranchTreeWidget.make_icon('dns', '#03A9F4'))
        else:
            src = f'{flow.src_ip}:{flow.src_port}' if flow.src_ip else ''
            dst = f'{flow.dst_ip}:{flow.dst_port}' if flow.dst_ip else ''
            if src and dst:
                显示文本 = f'{src} → {dst}'
            elif src:
                显示文本 = src
            else:
                显示文本 = f'流#{flow_idx}'
            请求项 = QTreeWidgetItem(parent_item, [显示文本, 协议, 大小])
            请求项.setData(0, Qt.UserRole, ('flow', flow_idx))
            请求项.setIcon(0, _BranchTreeWidget.make_icon('flow', icon_color))

    def _on_tree_item_clicked(self, item, column):
        """结构视图点击事件。"""
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        类型, 索引 = data
        if 类型 == 'flow':
            if 0 <= 索引 < len(self._filtered_flows):
                self._current_flow = self._filtered_flows[索引]
                self._show_detail(self._current_flow)
        elif 类型 == 'domain':
            # 域名分组点击时展开/收起
            item.setExpanded(not item.isExpanded())

    def _populate_domains(self):
        """从 flows 中提取所有唯一域名，填充域名下拉框。"""
        domains = set()
        for f in self._flows:
            if f.host and f.host not in ('', '—', '未知'):
                domains.add(f.host)
            if f.protocol == 'HTTPS' and f.tls_sni:
                domains.add(f.tls_sni)
            if f.dns_query:
                domains.add(f.dns_query)
        self.domain_combo.blockSignals(True)
        self.domain_combo.clear()
        self.domain_combo.addItem('全部域名')
        for d in sorted(domains):
            self.domain_combo.addItem(d)
        self.domain_combo.setCurrentIndex(0)
        self.domain_combo.blockSignals(False)

    # ── 选中详情 ──

    def _on_select_flow(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        if row >= self.table.rowCount():
            return
        # 通过第一列的 UserRole 获取 flow 在 _filtered_flows 中的索引
        idx_item = self.table.item(row, 0)
        if idx_item is None:
            return
        flow_idx = idx_item.data(Qt.UserRole)
        if flow_idx is not None and 0 <= flow_idx < len(self._filtered_flows):
            self._current_flow = self._filtered_flows[flow_idx]
            self._show_detail(self._current_flow)

    def _on_clicked(self, index):
        """单击选中行，显示详情并切换到概览Tab。"""
        self._on_select_flow()
        if self._current_flow:
            self.tabs.setCurrentIndex(0)

    def _on_sort_changed(self, column, order):
        """排序变化后，更新行染色和保持选中。"""
        # 重新应用行染色（因为排序后行位置变了）
        for row in range(self.table.rowCount()):
            idx_item = self.table.item(row, 0)
            if idx_item is None:
                continue
            flow_idx = idx_item.data(Qt.UserRole)
            if flow_idx is None or flow_idx >= len(self._filtered_flows):
                continue
            flow = self._filtered_flows[flow_idx]
            if flow.status_code:
                bg_color = QColor(_status_color(flow.status_code))
                bg_color.setAlpha(30)
                for col in range(10):
                    cell = self.table.item(row, col)
                    if cell:
                        cell.setBackground(bg_color)

    def _show_context_menu(self, pos):
        """右键菜单：复制URL/cURL/JSON/保存请求响应数据。"""
        flow = None
        viewport = None
        
        current = self.left_tabs.currentIndex()
        if current == 1:  # 序列视图
            viewport = self.table.viewport()
            rows = self.table.selectionModel().selectedRows()
            if not rows:
                return
            row = rows[0].row()
            if row >= self.table.rowCount():
                return
            idx_item = self.table.item(row, 0)
            if idx_item is None:
                return
            flow_idx = idx_item.data(Qt.UserRole)
            if flow_idx is None or flow_idx >= len(self._filtered_flows):
                return
            flow = self._filtered_flows[flow_idx]
        else:  # 结构视图
            viewport = self.structure_tree.viewport()
            item = self.structure_tree.itemAt(pos)
            if item is None:
                return
            data = item.data(0, Qt.UserRole)
            if data is None or data[0] != 'flow':
                return
            flow_idx = data[1]
            if flow_idx >= len(self._filtered_flows):
                return
            flow = self._filtered_flows[flow_idx]
        
        if flow is None:
            return
        
        menu = QMenu(self)

        # --- 复制组 ---
        act_copy_url = QAction('📋 复制 URL', self)
        act_copy_url.triggered.connect(lambda: self._copy_to_clipboard(flow.url))
        menu.addAction(act_copy_url)

        act_copy_curl = QAction('🔄 复制 cURL 命令', self)
        act_copy_curl.triggered.connect(lambda: self._copy_to_clipboard(self._build_curl(flow)))
        menu.addAction(act_copy_curl)

        act_copy_json = QAction('📄 复制 JSON', self)
        act_copy_json.triggered.connect(lambda: self._copy_to_clipboard(self._flow_to_json(flow)))
        menu.addAction(act_copy_json)

        menu.addSeparator()

        # --- 保存请求数据 ---
        if flow.req_body:
            act_save_req_body = QAction('💾 保存请求体', self)
            act_save_req_body.triggered.connect(lambda: self._save_data_to_file(
                flow.req_body, flow))
            menu.addAction(act_save_req_body)
        
        if flow.req_headers:
            act_save_req_headers = QAction('💾 保存请求头', self)
            act_save_req_headers.triggered.connect(lambda: self._save_headers_to_file(
                flow.req_headers, flow))
            menu.addAction(act_save_req_headers)
        
        if flow.raw_request:
            act_save_raw_req = QAction('💾 保存原始请求', self)
            act_save_raw_req.triggered.connect(lambda: self._save_data_to_file(
                flow.raw_request, flow, '.txt'))
            menu.addAction(act_save_raw_req)

        # --- 保存响应数据 ---
        if flow.resp_body:
            act_save_resp_body = QAction('💾 保存响应体', self)
            act_save_resp_body.triggered.connect(lambda: self._save_data_to_file(
                flow.resp_body, flow))
            menu.addAction(act_save_resp_body)
        
        if flow.resp_headers:
            act_save_resp_headers = QAction('💾 保存响应头', self)
            act_save_resp_headers.triggered.connect(lambda: self._save_headers_to_file(
                flow.resp_headers, flow))
            menu.addAction(act_save_resp_headers)
        
        if flow.raw_response:
            act_save_raw_resp = QAction('💾 保存原始响应', self)
            act_save_raw_resp.triggered.connect(lambda: self._save_data_to_file(
                flow.raw_response, flow, '.txt'))
            menu.addAction(act_save_raw_resp)

        menu.addSeparator()

        # --- 查看详情 ---
        act_show_detail = QAction('🔍 查看详情', self)
        act_show_detail.triggered.connect(lambda: self.tabs.setCurrentIndex(0))
        menu.addAction(act_show_detail)

        menu.exec(viewport.mapToGlobal(pos))

    def _copy_to_clipboard(self, text):
        _copied(text)
        self._stat_label.setText(f'已复制到剪贴板: {text[:80]}...' if len(text) > 80 else f'已复制: {text}')

    def _build_curl(self, flow):
        """构建 cURL 命令。"""
        if flow.protocol not in ('HTTP', 'WebSocket'):
            return f'# {flow.protocol} 流量，无法生成 cURL'
        parts = [f'curl -X {flow.method}']
        for k, v in flow.req_headers.items():
            val = v if not isinstance(v, list) else v[0]
            parts.append(f"-H '{k}: {val}'")
        if flow.req_body:
            body_str = flow.req_body.decode('utf-8', errors='replace')
            if len(body_str) < 2000:
                parts.append(f"-d '{body_str}'")
        parts.append(f"'{flow.url}'")
        return ' '.join(parts)

    def _flow_to_json(self, flow):
        """将 flow 转为 JSON 字符串。"""
        data = {
            'index': flow.idx,
            'timestamp': flow.ts,
            'protocol': flow.protocol,
            'method': flow.method,
            'url': flow.url,
            'host': flow.host,
            'status_code': flow.status_code,
            'status_text': flow.status_text,
            'source': f'{flow.src_ip}:{flow.src_port}',
            'destination': f'{flow.dst_ip}:{flow.dst_port}',
            'request_headers': flow.req_headers,
            'response_headers': flow.resp_headers,
            'request_size': flow.req_len,
            'response_size': flow.resp_len,
            'duration_ms': round(flow.duration * 1000, 1),
        }
        if flow.tls_sni:
            data['tls_sni'] = flow.tls_sni
            data['tls_version'] = flow.tls_version
        if flow.dns_query:
            data['dns_query'] = flow.dns_query
            data['dns_type'] = flow.dns_type
        if flow.is_websocket:
            data['websocket'] = True
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _get_flow_filename(self, flow) -> str:
        """根据URL生成文件名：优先取最后路径段，无路径时用域名/IP。
        
        示例:
          http://example.com/api/data/report?id=1  → report
          http://example.com/api/data/list         → list
          https://example.com (无路径)             → example.com
          TLS/TCP流 (无URL)                        → 36.155.98.104
        """
        name = ''
        url = flow.url or flow.path or ''
        
        if url:
            # 移除查询参数和锚点
            path = url.split('?')[0].split('#')[0]
            # 按 / 分割
            parts = [p for p in path.split('/') if p]
            
            if len(parts) >= 3:
                # 有协议 + 域名 + 路径: http://host/path → 取最后路径段
                name = parts[-1]
            elif len(parts) == 2:
                # 只有协议 + 域名: http://host → 取域名（去端口）
                domain = parts[1]
                if ':' in domain:
                    domain = domain.rsplit(':', 1)[0]
                name = domain
            elif len(parts) == 1:
                # 只有一段: 可能是纯路径或纯域名
                seg = parts[0]
                if ':' in seg and seg.count('.') >= 2:
                    # IP:端口
                    name = seg.rsplit(':', 1)[0]
                else:
                    name = seg
            # len(parts) == 0: 根路径 /，name 保持空
        
        # 回退方案：用域名或IP
        if not name:
            host = flow.host or ''
            if host:
                if ':' in host:
                    name = host.rsplit(':', 1)[0]
                else:
                    name = host
            elif flow.tls_sni:
                name = flow.tls_sni
            elif flow.dns_query:
                name = flow.dns_query
            elif flow.src_ip:
                name = flow.src_ip
            elif flow.dst_ip:
                name = flow.dst_ip
            else:
                name = f'flow_{flow.idx}'
        
        # 清理非法文件名字符
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, '_')
        
        # IP地址/域名中的点号在Windows保存对话框中会被误认为扩展名，替换为_
        # 但保留正常文件扩展名（由调用方在 _save_data_to_file 中追加）
        if name and '.' in name and not os.path.splitext(name)[1]:
            name = name.replace('.', '_')
        
        return name or 'untitled'

    def _save_data_to_file(self, data: bytes, flow, default_ext: str = ''):
        """保存二进制数据到文件。文件名基于最后路径段，支持无后缀保存。"""
        if not data:
            self._stat_label.setText('无数据可保存')
            return
        
        base_name = self._get_flow_filename(flow)
        
        # 判断数据类型以确定扩展名（仅用于默认建议）
        suggested_ext = ''
        if default_ext:
            suggested_ext = default_ext
        else:
            try:
                text = data.decode('utf-8')
                if text.strip().startswith('{') or text.strip().startswith('['):
                    suggested_ext = '.json'
                elif text.strip().startswith('<'):
                    suggested_ext = '.xml'
                elif '=' in text and '&' in text and ('=' in text.split('&')[0]):
                    suggested_ext = '.form'
                else:
                    suggested_ext = '.txt'
            except UnicodeDecodeError:
                suggested_ext = '.bin'
        
        default_name = f'{base_name}{suggested_ext}'
        
        # 使用空过滤器，Windows不会强制追加扩展名，支持无后缀保存
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存数据', default_name, '')
        if not file_path:
            return
        
        try:
            with open(file_path, 'wb') as f:
                f.write(data)
            size = len(data)
            self._stat_label.setText(f'已保存 {size} 字节到: {file_path}')
        except Exception as e:
            QMessageBox.warning(self, '保存失败', f'无法保存文件: {e}')

    def _save_headers_to_file(self, headers: dict, flow):
        """保存请求/响应头到文件。文件名基于最后路径段，支持无后缀保存。"""
        if not headers:
            self._stat_label.setText('无数据可保存')
            return
        
        base_name = self._get_flow_filename(flow)
        
        # 使用空过滤器，支持无后缀保存
        file_path, _ = QFileDialog.getSaveFileName(
            self, '保存', f'{base_name}.txt', '')
        if not file_path:
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                for key, value in headers.items():
                    f.write(f'{key}: {value}\r\n')
            self._stat_label.setText(f'已保存请求头到: {file_path}')
        except Exception as e:
            QMessageBox.warning(self, '保存失败', f'无法保存文件: {e}')

    def _show_detail(self, flow):
        self._clear_detail()

        # ── 概览（Charles 风格分组） ──
        theme = self._resolve_theme(self._theme_id)
        accent = theme['accent']
        text_sec = theme['text_secondary']
        text_primary = theme['text_primary']

        # 同步分支指示器颜色
        self.overview_tree.set_branch_colors(text_sec, theme['bg'])

        def _group(name, items, group_color=None):
            g = QTreeWidgetItem([name, ''])
            g.setExpanded(True)
            # 分组标题用强调色
            g.setForeground(0, QColor(group_color or accent))
            g.setForeground(1, QColor(group_color or accent))
            g.setIcon(0, _BranchTreeWidget.make_icon('folder', group_color or accent))
            for k, v in items:
                子 = QTreeWidgetItem([k, str(v)])
                子.setForeground(0, QColor(text_primary))
                子.setForeground(1, QColor(text_primary))
                g.addChild(子)
            self.overview_tree.addTopLevelItem(g)

        # 1. 基本信息
        _group('📋 基本信息', [
            ('请求方法', flow.method or '—'),
            ('协议', flow.protocol or '—'),
            ('完整 URL', flow.url or flow.path or '—'),
            ('请求路径', flow.path or '—'),
            ('Host', flow.host or '—'),
        ], accent)

        # 2. 状态
        状态文本 = f'{flow.status_code} {flow.status_text}'.strip() if flow.status_code else '无响应'
        _group('📊 状态', [
            ('状态码', 状态文本),
            ('完成', '是' if flow.status_code else '否'),
        ], accent)

        # 3. 地址信息
        _group('🌐 地址', [
            ('源地址', f'{flow.src_ip}:{flow.src_port}' if flow.src_ip else '—'),
            ('目标地址', f'{flow.dst_ip}:{flow.dst_port}' if flow.dst_ip else '—'),
        ], accent)

        # 4. 时间
        ts_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(flow.ts))
        ts_str += f'.{int((flow.ts % 1) * 1000):03d}'
        dur_ms = f'{flow.duration * 1000:.1f} ms' if flow.duration > 0 else '—'
        _group('⏱ 时间', [
            ('请求时间', ts_str),
            ('耗时', dur_ms),
        ], accent)

        # 5. 大小
        _group('📦 大小', [
            ('请求大小', _format_size(flow.req_len)),
            ('响应大小', _format_size(flow.resp_len)),
            ('总大小', _format_size(flow.req_len + flow.resp_len)),
        ], accent)

        # 6. TLS 信息
        if flow.tls_sni or (flow.protocol or '').upper() in ('HTTPS', 'TLS'):
            tls_items = []
            if flow.tls_sni:
                tls_items.append(('SNI', flow.tls_sni))
            if flow.tls_version:
                tls_items.append(('TLS 版本', flow.tls_version))
            tls_items.append(('加密流量', '明文内容无法解析（TLS 握手可见）'))
            _group('🔒 TLS', tls_items, accent)

        # 7. DNS 信息
        if flow.dns_query:
            dns_items = [
                ('查询域名', flow.dns_query),
                ('查询类型', flow.dns_type or '—'),
            ]
            if flow.dns_response:
                dns_items.append(('响应结果', flow.dns_response))
            dns_items.append(('状态', flow.status_text or f'RCODE {flow.status_code}'))
            _group('🔍 DNS', dns_items, '#03A9F4')

        # 8. 请求头关键字段
        req_headers = flow.req_headers
        if req_headers:
            key_fields = []
            for key in ('Content-Type', 'Content-Length', 'User-Agent',
                         'Accept', 'Accept-Encoding', 'Authorization',
                         'Cookie', 'Referer', 'Origin', 'Cache-Control'):
                if key in req_headers:
                    key_fields.append((key, req_headers[key]))
            if key_fields:
                _group('📤 请求头关键字段', key_fields, accent)

        # 9. 响应头关键字段
        resp_headers = flow.resp_headers
        if resp_headers:
            key_fields = []
            for key in ('Content-Type', 'Content-Length', 'Content-Encoding',
                         'Set-Cookie', 'Cache-Control', 'Server',
                         'Location', 'ETag', 'Vary', 'X-Request-Id'):
                if key in resp_headers:
                    key_fields.append((key, resp_headers[key]))
            if key_fields:
                _group('📥 响应头关键字段', key_fields, accent)

        # 10. WebSocket
        if flow.is_websocket:
            _group('🔌 WebSocket', [
                ('协议升级', 'WebSocket'),
                ('消息数', '—'),
            ], accent)

        # 协议信息 Tab
        proto_items = [
            ('协议类型', flow.protocol),
            ('方法', flow.method),
        ]
        if flow.protocol == 'HTTPS':
            proto_items.append(('🔒 加密流量', '明文内容无法解析（TLS 握手可见）'))
            proto_items.append(('SNI', flow.tls_sni or '未知'))
            proto_items.append(('TLS 版本', flow.tls_version or '未知'))
        elif flow.protocol == 'DNS':
            proto_items.append(('查询域名', flow.dns_query or '未知'))
            proto_items.append(('查询类型', flow.dns_type or '未知'))
            if flow.dns_response:
                proto_items.append(('响应结果', flow.dns_response))
            proto_items.append(('状态', flow.status_text or f'RCODE {flow.status_code}'))
        elif flow.protocol == 'WebSocket':
            proto_items.append(('升级协议', 'WebSocket'))
        elif flow.protocol == 'TCP':
            proto_items.append(('TCP 连接', f'{flow.src_ip}:{flow.src_port} → {flow.dst_ip}:{flow.dst_port}'))
        for k, v in proto_items:
            self.protocol_tree.addTopLevelItem(QTreeWidgetItem([k, str(v)]))

        # 请求体（含头部）
        req_ct = flow.req_headers.get('content-type', '')
        self.req_body_viewer.set_data(flow.req_body, req_ct, flow.req_headers, flow.url or '')

        # 响应体（含头部）
        resp_ct = flow.resp_headers.get('content-type', '')
        self.resp_body_viewer.set_data(flow.resp_body, resp_ct, flow.resp_headers)

        # Hex 查看器
        hex_text = self._generate_hex(flow)
        self.hex_edit.setPlainText(hex_text)

        # 原始数据
        raw_parts = []
        if flow.raw_request:
            raw_parts.append('═══ 请求原始数据 ═══')
            raw_parts.append(flow.raw_request.decode('utf-8', errors='replace'))
        if flow.raw_response:
            raw_parts.append('\n═══ 响应原始数据 ═══')
            raw_parts.append(flow.raw_response.decode('utf-8', errors='replace'))
        if not raw_parts:
            raw_parts.append('（无原始数据）')
        self.raw_edit.setPlainText('\n'.join(raw_parts))

    def _generate_hex(self, flow):
        """生成 Hex 查看文本。"""
        parts = []
        data_to_show = b''
        label = ''
        if flow.raw_request:
            data_to_show = flow.raw_request
            label = '═══ 请求 Hex ═══'
        if flow.raw_response:
            if data_to_show:
                data_to_show += b'\n'
            data_to_show += flow.raw_response
            if not label:
                label = '═══ 响应 Hex ═══'
            else:
                label += '\n═══ 响应 Hex ═══'
        if not data_to_show:
            return '（无 Hex 数据）'
        lines = [label, '']
        offset = 0
        for chunk_start in range(0, len(data_to_show), 16):
            chunk = data_to_show[chunk_start:chunk_start + 16]
            hex_str = ' '.join(f'{b:02X}' for b in chunk)
            ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            lines.append(f'{offset:08X}  {hex_str:<48s}  |{ascii_str}|')
            offset += 16
        lines.append(f'\n共 {len(data_to_show)} 字节')
        return '\n'.join(lines)

    def _clear_detail(self):
        self.overview_tree.clear()
        self.req_body_viewer.clear_all()
        self.resp_body_viewer.clear_all()
        self.protocol_tree.clear()
        self.hex_edit.clear()
        self.raw_edit.clear()

    # ── 过滤 ──

    def _parse_search_syntax(self, text):
        """解析高级搜索语法，返回 (filters_dict, remaining_keyword)。"""
        filters = {
            'domains': [],
            'methods': [],
            'status_codes': [],
            'protocols': [],
        }
        remaining = []
        for token in text.split():
            if ':' in token:
                prefix, _, value = token.partition(':')
                prefix = prefix.lower()
                values = [v.strip() for v in value.split(',') if v.strip()]
                if prefix in ('domain', 'host'):
                    filters['domains'].extend(values)
                elif prefix in ('method', 'm'):
                    filters['methods'].extend(values)
                elif prefix in ('status', 'code', 's'):
                    filters['status_codes'].extend(values)
                elif prefix in ('protocol', 'proto', 'p'):
                    filters['protocols'].extend(values)
                else:
                    remaining.append(token)
            else:
                remaining.append(token)
        keyword = ' '.join(remaining)
        return filters, keyword

    def _apply_filter(self):
        method = self.method_combo.currentText()
        status = self.status_combo.currentText()
        protocol = self.protocol_combo.currentText()
        domain = self.domain_combo.currentText()
        search_text = self.search_edit.text().strip()

        # 解析高级搜索语法
        filters, keyword = self._parse_search_syntax(search_text)
        keyword_lower = keyword.lower()

        filtered = []
        for f in self._flows:
            # 方法过滤
            if method != '全部' and f.method != method:
                continue
            # 状态码过滤
            if status == '2xx' and not (200 <= f.status_code < 300):
                continue
            if status == '3xx' and not (300 <= f.status_code < 400):
                continue
            if status == '4xx' and not (400 <= f.status_code < 500):
                continue
            if status == '5xx' and not (f.status_code >= 500):
                continue
            if status == '无响应' and f.status_code != 0:
                continue
            # 协议过滤
            if protocol != '全部' and f.protocol != protocol:
                continue
            # 域名过滤
            if domain and domain != '全部域名':
                if f.host != domain and f.tls_sni != domain:
                    continue
            # 高级语法：domain
            if filters['domains']:
                if not any(d in (f.host + f.tls_sni) for d in filters['domains']):
                    continue
            # 高级语法：method
            if filters['methods']:
                if f.method.upper() not in [m.upper() for m in filters['methods']]:
                    continue
            # 高级语法：status
            if filters['status_codes']:
                matched = False
                for sc in filters['status_codes']:
                    sc = sc.strip()
                    if sc.endswith('xx') and len(sc) == 3:
                        prefix = int(sc[0])
                        if f.status_code // 100 == prefix:
                            matched = True
                            break
                    elif sc.isdigit():
                        if f.status_code == int(sc):
                            matched = True
                            break
                    elif '-' in sc:
                        parts = sc.split('-')
                        if len(parts) == 2:
                            lo, hi = int(parts[0]), int(parts[1])
                            if lo <= f.status_code <= hi:
                                matched = True
                                break
                if not matched:
                    continue
            # 关键词搜索（使用预计算的搜索文本）
            if keyword_lower:
                searchable = getattr(f, '_searchable_text', '')
                if not searchable:
                    # 首次搜索时计算并缓存
                    searchable = (f.url + f.method + f.host + f.protocol
                                  + f.req_body.decode('utf-8', errors='replace')
                                  + f.resp_body.decode('utf-8', errors='replace')).lower()
                    f._searchable_text = searchable
                if keyword_lower not in searchable:
                    continue
            filtered.append(f)

        self._filtered_flows = filtered
        self._populate_table()
        self._build_structure_tree()
        count_info = f'显示 {len(filtered)} / {len(self._flows)} 个请求'
        if search_text:
            count_info += f' (过滤: "{search_text}")'
        self._stat_label.setText(count_info)

    def _clear_filter(self):
        self.method_combo.setCurrentIndex(0)
        self.status_combo.setCurrentIndex(0)
        self.protocol_combo.setCurrentIndex(0)
        self.domain_combo.setCurrentIndex(0)
        self.search_edit.clear()

    # ── 导出 ──

    def _export_data(self):
        if not self._flows:
            QMessageBox.warning(self, '导出', '没有数据可导出。')
            return
        path, _ = QFileDialog.getSaveFileName(
            self, '导出 PCAP 解析结果', '',
            'JSON (*.json);;CSV (*.csv);;HAR (*.har);;所有文件 (*.*)'
        )
        if not path:
            return
        try:
            ext = os.path.splitext(path)[1].lower()
            if not ext:
                # 无后缀时默认用 .json
                path += '.json'
                ext = '.json'
            if ext == '.json':
                self._export_json(path)
            elif ext == '.csv':
                self._export_csv(path)
            elif ext == '.har':
                self._export_har(path)
            else:
                # 其他后缀默认用 JSON 格式
                self._export_json(path)
            self._stat_label.setText(f'导出成功: {path}')
        except Exception as e:
            QMessageBox.critical(self, '导出失败', str(e))

    def _export_json(self, path):
        """导出为 JSON 格式。"""
        data = []
        for flow in self._filtered_flows:
            data.append(json.loads(self._flow_to_json(flow)))
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _export_csv(self, path):
        """导出为 CSV 格式。"""
        with open(path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                '序号', '协议', '时间', '方法', 'URL', '状态码',
                '大小(B)', '耗时(ms)', '源IP', '源端口', '目标IP', '目标端口',
                'Content-Type', 'Host'
            ])
            for flow in self._filtered_flows:
                ts_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(flow.ts))
                writer.writerow([
                    flow.idx,
                    flow.protocol,
                    ts_str,
                    flow.method,
                    flow.url,
                    flow.status_code,
                    flow.req_len + flow.resp_len,
                    round(flow.duration * 1000, 1),
                    flow.src_ip,
                    flow.src_port,
                    flow.dst_ip,
                    flow.dst_port,
                    flow.resp_headers.get('content-type', ''),
                    flow.host,
                ])

    def _export_har(self, path):
        """导出为 HAR 1.2 格式（兼容 Chrome DevTools）。"""
        har = {
            'log': {
                'version': '1.2',
                'creator': {'name': 'Super_ADB PCAP Parser', 'version': '1.0'},
                'entries': [],
            }
        }
        for flow in self._filtered_flows:
            entry = {
                'pageref': f'page_{flow.idx}',
                'startedDateTime': datetime.fromtimestamp(flow.ts).isoformat() + 'Z',
                'time': round(flow.duration * 1000, 1),
                'request': {
                    'method': flow.method,
                    'url': flow.url,
                    'httpVersion': 'HTTP/1.1',
                    'cookies': [],
                    'headers': [{'name': k, 'value': v if not isinstance(v, list) else v[0]}
                                for k, v in flow.req_headers.items()],
                    'queryString': [],
                    'headersSize': -1,
                    'bodySize': flow.req_len,
                },
                'response': {
                    'status': flow.status_code,
                    'statusText': flow.status_text,
                    'httpVersion': 'HTTP/1.1',
                    'cookies': [],
                    'headers': [{'name': k, 'value': v if not isinstance(v, list) else v[0]}
                                for k, v in flow.resp_headers.items()],
                    'content': {
                        'size': flow.resp_len,
                        'mimeType': flow.resp_headers.get('content-type', ''),
                    },
                    'redirectURL': '',
                    'headersSize': -1,
                    'bodySize': flow.resp_len,
                },
                'cache': {},
                'timings': {
                    'send': 0,
                    'wait': round(flow.duration * 1000, 1),
                    'receive': 0,
                },
                '_protocol': flow.protocol,
                '_source': f'{flow.src_ip}:{flow.src_port}',
                '_destination': f'{flow.dst_ip}:{flow.dst_port}',
            }
            har['log']['entries'].append(entry)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(har, f, ensure_ascii=False, indent=2)

    # ── 依赖检查 ──

    def _check_dependencies(self):
        """检查轻量PCAP解析模块是否可用。"""
        try:
            from tools.lightweight_pcap_parser import PcapReader
            return True
        except ImportError:
            QMessageBox.critical(
                self, '缺少依赖',
                'PCAP 解析模块加载失败。\n\n'
                '请检查 tools/lightweight_pcap_parser.py 是否存在。'
            )
            return False

    # ── 样式 ──

    def apply_theme(self, theme_id):
        """主窗口切换主题时调用，同步刷新弹窗样式与图标。"""
        if theme_id not in THEMES:
            return
        self._theme_id = theme_id
        self.setStyleSheet(self._global_style(theme_id))
        # 更新发光效果
        accent = THEMES[theme_id]['accent']
        r, g, b = _parse_rgb(accent)
        add_green_glow(self.card, accent=QColor(r, g, b))
        # 更新结构树分支颜色
        theme = self._resolve_theme(theme_id)
        self.structure_tree.set_branch_colors(
            theme['text_secondary'], theme['bg']
        )
        # 更新概览树分支颜色
        self.overview_tree.set_branch_colors(
            theme['text_secondary'], theme['bg']
        )
        # 更新内容体内视图分支颜色
        for viewer in (self.req_body_viewer, self.resp_body_viewer):
            for editor in viewer._editors.values():
                if isinstance(editor, _BranchTreeWidget):
                    editor.set_branch_colors(
                        theme['text_secondary'], theme['bg']
                    )
        # 重建结构树以刷新图标颜色
        if self._filtered_flows:
            self._build_structure_tree()
        # 如果概览有选中流，重新渲染以更新字体颜色
        if self._current_flow:
            self._show_detail(self._current_flow)
        self.update()

    @staticmethod
    def _resolve_theme(theme_id):
        """将 THEMES 中的键映射为 _card_style 所需的键。"""
        raw = THEMES.get(theme_id, THEMES[DEFAULT_THEME])
        accent = raw['accent']
        r, g, b = _parse_rgb(accent)
        border = f'rgb({r},{g},{b})'

        def _lighten(rgb_str, pct):
            rr, gg, bb = _parse_rgb(rgb_str)
            rr = min(255, int(rr + (255 - rr) * pct))
            gg = min(255, int(gg + (255 - gg) * pct))
            bb = min(255, int(bb + (255 - bb) * pct))
            return f'rgb({rr},{gg},{bb})'

        def _darken(rgb_str, pct):
            rr, gg, bb = _parse_rgb(rgb_str)
            rr = max(0, int(rr * (1 - pct)))
            gg = max(0, int(gg * (1 - pct)))
            bb = max(0, int(bb * (1 - pct)))
            return f'rgb({rr},{gg},{bb})'

        return {
            'card_bg': raw['bg_window'],
            'bg': raw['bg_input'],
            'bg_alt': raw.get('bg_button', raw['bg_window']),
            'border': border,
            'border_light': raw.get('border_disabled', '#444'),
            'text_primary': raw['text_primary'],
            'text_secondary': raw.get('text_disabled', '#888888'),
            'text_disabled': raw.get('text_disabled', '#666666'),
            'accent': accent,
            'accent_hover': _lighten(accent, 0.15),
            'accent_pressed': _darken(accent, 0.1),
            'input_bg': raw['bg_input'],
            'button_bg': raw.get('bg_button', raw['bg_window']),
            'combo_bg': raw.get('bg_combo', raw['bg_input']),
            'menu_bg': raw.get('bg_menu', raw['bg_window']),
            'splitter_bg': raw.get('bg_splitter', raw['bg_window']),
        }

    def _global_style(self, theme_id):
        """全局样式：所有子控件跟随主题。参考安装弹窗，用具体控件选择器。"""
        t = self._resolve_theme(theme_id)
        return f"""
        /* card 圆角边框和背景（4px 主题色亮边框，与无线调试对话框一致） */
        #popupCard {{
            background-color: {t['card_bg']};
            border: 4px solid {t['accent']};
            border-radius: 12px;
        }}
        #popupCard[drag_highlight="true"] {{
            border: 3px dashed {t['accent']};
        }}
        QWidget {{
            background-color: {t['card_bg']};
            color: {t['text_primary']};
            font-family: "{FONT_FAMILY}";
            font-size: 12px;
        }}
        #popupCard QLabel {{
            background: transparent;
            border: none;
            color: {t['text_primary']};
        }}
        QLabel#tipLabel {{
            color: {t['text_secondary']};
            font-size: 12px;
        }}
        /* ── 表格 ── */
        QTableWidget {{
            background-color: {t['bg']};
            alternate-background-color: {t['bg_alt']};
            gridline-color: {t['border_light']};
            color: {t['text_primary']};
            font-size: 12px;
            border: none;
            border-radius: 0px;
            outline: none;
        }}
        QTableWidget::item {{
            padding: 4px 6px;
            border: none;
            background: transparent;
        }}
        QTableWidget::item:selected {{
            background-color: {t['accent']};
            color: white;
        }}
        QTableWidget::item:hover {{
            background-color: {t['accent_pressed']};
        }}
        QHeaderView::section {{
            background-color: {t['bg_alt']};
            color: {t['text_primary']};
            padding: 6px 8px;
            border: none;
            border-bottom: 2px solid {t['accent']};
            border-right: 1px solid {t['border_light']};
            border-radius: 0px;
            font-weight: bold;
            font-size: 12px;
        }}
        /* ── 树形控件 ── */
        QTreeWidget {{
            background-color: {t['bg']};
            color: {t['text_primary']};
            alternate-background-color: {t['bg_alt']};
            font-size: 12px;
            border: none;
            border-radius: 0px;
            outline: none;
        }}
        QTreeWidget::item {{
            padding: 3px 4px;
            border: none;
            background: transparent;
            color: {t['text_primary']};
        }}
        QTreeWidget::item:selected {{
            background-color: {t['accent']};
            color: white;
        }}
        QTreeWidget::item:hover {{
            background-color: {t['accent_pressed']};
        }}
        QTreeWidget::item:selected:hover {{
            background-color: {t['accent']};
        }}
        QTreeWidget::branch {{
            background: transparent;
        }}
        QHeaderView {{
            background-color: {t['bg_alt']};
            border-radius: 0px;
        }}
        /* ── Tab 控件 ── */
        QTabWidget {{
            background-color: transparent;
            border: none;
        }}
        QTabWidget::tab-bar {{
            background-color: transparent;
            border: none;
        }}
        QTabWidget::pane {{
            background-color: transparent;
            border: 1px solid {t['accent']};
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            border-bottom-left-radius: 6px;
            border-bottom-right-radius: 6px;
        }}
        QTabWidget QStackedWidget {{
            background-color: transparent;
        }}
        QTabWidget QStackedWidget > QWidget {{
            background-color: transparent;
        }}
        QTabBar {{
            background-color: transparent;
            border: none;
            qproperty-drawBase: 0;
        }}
        QTabBar::tab {{
            background-color: transparent;
            color: {t['text_secondary']};
            border: 1px solid transparent;
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 6px 16px;
            margin-right: 2px;
            font-size: 12px;
        }}
        QTabBar::tab:selected {{
            background-color: {t['card_bg']};
            color: {t['accent']};
            border-top: 1px solid {t['accent']};
            border-left: 1px solid {t['accent']};
            border-right: 1px solid {t['accent']};
            border-bottom: none;
        }}
        QTabBar::tab:hover:!selected {{
            background-color: {t['bg_alt']};
            color: {t['text_primary']};
        }}
        /* ── 左侧视图Tab (结构/序列) ── */
        #leftTabs {{
            background: transparent;
        }}
        #leftTabs::pane {{
            background-color: transparent;
            border: 1px solid {t['accent']};
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            border-bottom-left-radius: 6px;
            border-bottom-right-radius: 6px;
        }}
        #leftTabs QTabBar::tab {{
            background-color: transparent;
            color: {t['text_secondary']};
            border: 1px solid transparent;
            border-bottom: none;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 4px 10px;
            margin-right: 0px;
            font-size: 12px;
        }}
        #leftTabs QTabBar::tab:selected {{
            background-color: {t['card_bg']};
            color: {t['accent']};
            border-top: 1px solid {t['accent']};
            border-left: 1px solid {t['accent']};
            border-right: 1px solid {t['accent']};
            border-bottom: none;
        }}
        #leftTabs QTabBar::tab:hover:!selected {{
            background-color: {t['bg_alt']};
            color: {t['text_primary']};
        }}
        #leftPage {{
            background: transparent;
            border: none;
        }}
        /* ── 内容子 Tab ── */
        #contentTabs {{
            background: transparent;
        }}
        #contentTabs::pane {{
            border: none;
            background-color: transparent;
            top: -1px;
        }}
        #contentTabs QTabBar::tab {{
            background-color: transparent;
            color: {t['text_secondary']};
            padding: 6px 14px;
            border: none;
            border-bottom: 2px solid transparent;
            margin-right: 0px;
            font-size: 12px;
        }}
        #contentTabs QTabBar::tab:selected {{
            background-color: transparent;
            color: {t['text_primary']};
            border-bottom: 2px solid {t['accent']};
        }}
        #contentTabs QTabBar::tab:hover:!selected {{
            background-color: transparent;
            color: {t['text_primary']};
            border-bottom: 2px solid {t['accent_pressed']};
        }}
        /* ── 内容体内视图 Tab ── */
        #bodyViewTabs {{
            background: transparent;
        }}
        #bodyViewTabs::pane {{
            background-color: transparent;
            border: 1px solid {t['accent']};
            border-top: none;
            border-left: none;
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            border-bottom-left-radius: 6px;
            border-bottom-right-radius: 6px;
        }}
        #bodyViewTabs QTabBar::tab {{
            background-color: transparent;
            color: {t['text_secondary']};
            padding: 4px 10px;
            border: none;
            border-bottom: 2px solid transparent;
            margin-right: 2px;
            font-size: 11px;
        }}
        #bodyViewTabs QTabBar::tab:selected {{
            background-color: {t['bg']};
            color: {t['text_primary']};
            border-bottom: 2px solid {t['accent']};
        }}
        #bodyViewTabs QTabBar::tab:hover:!selected {{
            background-color: transparent;
            color: {t['text_primary']};
            border-bottom: 2px solid {t['accent_pressed']};
        }}
        /* ── 文本编辑 ── */
        QTextEdit, QPlainTextEdit {{
            background-color: {t['bg']};
            color: {t['text_primary']};
            border: none;
            border-radius: 0px;
            padding: 4px;
            selection-background-color: {t['accent']};
        }}
        /* ── 输入框 ── */
        QLineEdit {{
            background-color: {t['input_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            padding: 6px 10px;
            border-radius: 6px;
            selection-background-color: {t['accent']};
        }}
        QLineEdit:focus {{
            border: 1px solid {t['accent']};
        }}
        /* ── 下拉框 ── */
        QComboBox {{
            background-color: {t['combo_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            padding: 5px 10px;
            border-radius: 6px;
            min-height: 20px;
        }}
        QComboBox:hover {{
            border: 1px solid {t['accent']};
        }}
        QComboBox::drop-down {{
            border: none;
            width: 20px;
        }}
        QComboBox::down-arrow {{
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: {t['menu_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            selection-background-color: {t['accent']};
            selection-color: white;
            outline: none;
        }}
        /* ── 按钮 ── */
        QPushButton {{
            background-color: {t['button_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            padding: 6px 14px;
            border-radius: 6px;
            font-weight: bold;
            min-height: 20px;
        }}
        QPushButton:hover {{
            background-color: {t['accent']};
            color: white;
            border-color: {t['accent']};
        }}
        QPushButton:pressed {{
            background-color: {t['accent_pressed']};
        }}
        QPushButton:disabled {{
            background-color: {t['bg_alt']};
            color: {t['text_disabled']};
            border-color: {t['border_light']};
        }}
        /* ── 菜单 ── */
        QMenu {{
            background-color: {t['menu_bg']};
            color: {t['text_primary']};
            border: 1px solid {t['border_light']};
            border-radius: 6px;
            padding: 4px;
        }}
        QMenu::item {{
            padding: 6px 24px;
            border-radius: 4px;
        }}
        QMenu::item:selected {{
            background-color: {t['accent']};
            color: white;
        }}
        QMenu::separator {{
            height: 1px;
            background: {t['border_light']};
            margin: 4px 8px;
        }}
        /* ── 分割器 ── */
        QSplitter::handle {{
            background-color: {t['border_light']};
            width: 4px;
            height: 4px;
        }}
        QSplitter::handle:hover {{
            background-color: {t['accent']};
        }}
        /* ── 滚动条 ── */
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {t['border_light']};
            border-radius: 5px;
            min-height: 30px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {t['accent']};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 10px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {t['border_light']};
            border-radius: 5px;
            min-width: 30px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {t['accent']};
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        """


def _format_size(n):
    """格式化字节大小。"""
    if n < 1024:
        return f'{n} B'
    elif n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    else:
        return f'{n / (1024 * 1024):.1f} MB'


# ──────────────────────── 测试入口 ────────────────────────
if __name__ == '__main__':
    import sys
    import os
    # 把项目根目录加入模块搜索路径
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = Pcap解析对话框()
    dlg.show()
    sys.exit(app.exec())
