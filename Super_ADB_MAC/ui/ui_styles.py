# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— 主题系统
================================

支持运行时切换 7 套主题（6 深色 + 1 浅色）。每套主题通过同名 dict 定义
关键色板（背景/输入/按钮/菜单/分割条/文字/禁用态/强调色），由
``get_stylesheet(theme_id)`` 拼成完整 QSS。

设计要点：
- 主题切换只更新样式表本身，窗口结构/控件树不变
- QSS 中所有可主题化的颜色都用 ``{t[xxx]}`` 占位（str 替换，避开
  ``str.format`` 与 QSS ``{}`` 块语法冲突）
- 强调色的 rgba 透明变体按 accent rgb 动态派生，避免每套主题写死 8 个 alpha
- ``STYLE_SHEET`` 仍导出（默认主题 dark_teal）以保持旧调用兼容
"""

import os
import sys
import tempfile
import hashlib

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QPainter, QPolygon, QImage


# ----------------------------------------------------------------------
# 字体（跨平台）
# ----------------------------------------------------------------------
if sys.platform == 'darwin':
    FONT_FAMILY = "PingFang SC"
elif sys.platform == 'linux':
    FONT_FAMILY = "Noto Sans CJK SC"
else:
    FONT_FAMILY = "微软雅黑"


# ----------------------------------------------------------------------
# 主题字典
# ----------------------------------------------------------------------
# 每套主题必填字段：accent / bg_window / bg_button / bg_input / bg_menu /
# bg_combo / bg_statusbar / bg_splitter / text_primary / text_pressed /
# text_disabled / text_hover / border_disabled
# 选填：name（菜单显示名，默认等于 key）
THEMES = {
    'dark_teal': {
        'name': '深色·青绿',
        'accent': 'rgb(29,233,182)',
        'bg_window':    '#2b2b2b',
        'bg_button':    '#333333',
        'bg_input':     '#1f1f1f',
        'bg_menu':      '#2d2d2d',
        'bg_combo':     '#3a3a3a',
        'bg_statusbar': '#222222',
        'bg_splitter':  '#3a3a3a',
        'text_primary':   '#e0e0e0',
        'text_pressed':   '#1b1b1b',
        'text_disabled':  '#777777',
        'text_hover':     '#ffffff',
        'border_disabled':'#555555',
    },
    'dark_cyan': {
        'name': '深色·青蓝',
        'accent': 'rgb(0,229,255)',
        'bg_window':    '#1d2530',
        'bg_button':    '#28323e',
        'bg_input':     '#0f1820',
        'bg_menu':      '#1a242e',
        'bg_combo':     '#2d3845',
        'bg_statusbar': '#141c25',
        'bg_splitter':  '#2d3845',
        'text_primary':   '#e3eef7',
        'text_pressed':   '#0a1320',
        'text_disabled':  '#6f8090',
        'text_hover':     '#ffffff',
        'border_disabled':'#3e4d5c',
    },
    'dark_purple': {
        'name': '深色·紫罗兰',
        'accent': 'rgb(187,107,255)',
        'bg_window':    '#241d2e',
        'bg_button':    '#2e2638',
        'bg_input':     '#18111f',
        'bg_menu':      '#1d1626',
        'bg_combo':     '#352a45',
        'bg_statusbar': '#16101e',
        'bg_splitter':  '#352a45',
        'text_primary':   '#ece4f5',
        'text_pressed':   '#1a1228',
        'text_disabled':  '#7d6f8c',
        'text_hover':     '#ffffff',
        'border_disabled':'#3d3349',
    },
    'dark_amber': {
        'name': '深色·琥珀',
        'accent': 'rgb(255,193,7)',
        'bg_window':    '#2b2519',
        'bg_button':    '#332c1d',
        'bg_input':     '#1f180e',
        'bg_menu':      '#251e12',
        'bg_combo':     '#3a3221',
        'bg_statusbar': '#1a1409',
        'bg_splitter':  '#3a3221',
        'text_primary':   '#f3ead5',
        'text_pressed':   '#1a1408',
        'text_disabled':  '#8a7a5a',
        'text_hover':     '#ffffff',
        'border_disabled':'#4a3f24',
    },
    'dark_crimson': {
        'name': '深色·深红',
        'accent': 'rgb(255,82,82)',
        'bg_window':    '#2b1d1d',
        'bg_button':    '#332222',
        'bg_input':     '#1f0e0e',
        'bg_menu':      '#251515',
        'bg_combo':     '#3a2525',
        'bg_statusbar': '#1a0a0a',
        'bg_splitter':  '#3a2525',
        'text_primary':   '#f3dcdc',
        'text_pressed':   '#1a0808',
        'text_disabled':  '#8a5a5a',
        'text_hover':     '#ffffff',
        'border_disabled':'#4a2929',
    },
    'dark_neon': {
        'name': '深色·霓虹黑',
        'accent': 'rgb(0,255,128)',
        'bg_window':    '#080808',
        'bg_button':    '#111111',
        'bg_input':     '#050505',
        'bg_menu':      '#0c0c0c',
        'bg_combo':     '#151515',
        'bg_statusbar': '#020202',
        'bg_splitter':  '#1a1a1a',
        'text_primary':   '#d6ffe6',
        'text_pressed':   '#001a0d',
        'text_disabled':  '#3f5a4a',
        'text_hover':     '#ffffff',
        'border_disabled':'#252525',
    },
    'light_soft': {
        'name': '浅色·晴空',
        'accent': 'rgb(37,99,235)',
        'bg_window':    '#f8fafc',
        'bg_button':    '#ffffff',
        'bg_input':     '#ffffff',
        'bg_menu':      '#ffffff',
        'bg_combo':     '#f1f5f9',
        'bg_statusbar': '#e2e8f0',
        'bg_splitter':  '#cbd5e1',
        'text_primary':   '#0f172a',
        'text_pressed':   '#ffffff',
        'text_disabled':  '#94a3b8',
        'text_hover':     '#ffffff',
        'border_disabled':'#cbd5e1',
    },
}

DEFAULT_THEME = 'dark_cyan'


def get_theme_ids():
    """按固定顺序返回所有主题 id。"""
    return list(THEMES.keys())


def get_theme_name(theme_id):
    """取主题显示名（用于菜单），未知名回退 id 本身。"""
    return THEMES.get(theme_id, {}).get('name', theme_id)


# ----------------------------------------------------------------------
# 强调色 rgba 派生
# ----------------------------------------------------------------------
def _parse_rgb(rgb_str):
    """解析 'rgb(29,233,182)' / 'rgb(29, 233, 182)' → (29, 233, 182)。"""
    s = rgb_str
    if s.startswith('rgb(') and s.endswith(')'):
        s = s[4:-1]
    parts = [p.strip() for p in s.split(',') if p.strip()]
    return tuple(int(p) for p in parts[:3])


def _arrow_icon_path(theme_id):
    """程序化生成一张强调色「向下箭头」PNG，路径带主题 hash 让切主题后立刻生效。"""
    accent = THEMES.get(theme_id, THEMES[DEFAULT_THEME])['accent']
    r, g, b = _parse_rgb(accent)
    h = hashlib.md5(f'{r},{g},{b}'.encode()).hexdigest()[:8]
    path = os.path.join(tempfile.gettempdir(), f'adb_shell_down_arrow_{h}.png')
    if not os.path.exists(path):
        img = QImage(16, 16, QImage.Format_ARGB32)
        img.fill(0x00000000)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor(r, g, b))
        p.setBrush(QColor(r, g, b))
        p.drawPolygon(QPolygon([QPoint(3, 5), QPoint(13, 5), QPoint(8, 12)]))
        p.end()
        img.save(path)
    return path.replace('\\', '/')


# ----------------------------------------------------------------------
# 样式表模板
# ----------------------------------------------------------------------
def get_stylesheet(theme_id=DEFAULT_THEME):
    """按主题 id 拼一份完整 QSS 字符串。未知主题回退默认。"""
    t = THEMES.get(theme_id, THEMES[DEFAULT_THEME])
    accent = t['accent']
    r, g, b = _parse_rgb(accent)

    def rgba(alpha):
        return f'rgba({r},{g},{b},{alpha})'

    arrow = _arrow_icon_path(theme_id)

    return f"""
    /* ────────────── 全局窗口 ────────────── */
    QWidget {{
        background-color: {t['bg_window']};
        color: {t['text_primary']};
        font: 10pt "{FONT_FAMILY}";
    }}

    /* ────────────── 分组框 QGroupBox ────────────── */
    QGroupBox {{
        border: 1px solid {accent};
        border-radius: 8px;
        margin-top: 10px;
        padding-top: 10px;
        padding-bottom: 8px;
        padding-left: 8px;
        padding-right: 8px;
        font: 400 10pt "{FONT_FAMILY}";
        color: {accent};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        color: {accent};
    }}

    /* ────────────── 标签页 QTabWidget（页面透明，跟随主题背景） ────────────── */
    QTabWidget {{
        background-color: transparent;
        border: none;
    }}
    QTabWidget::pane {{
        background-color: transparent;
        border: 1px solid {accent};
        border-top-left-radius: 0px;
        border-top-right-radius: 6px;
        border-bottom-left-radius: 6px;
        border-bottom-right-radius: 6px;
        top: -1px;
    }}
    QTabBar {{
        background-color: transparent;
    }}
    QTabWidget::tab-bar {{
        background-color: transparent;
    }}
    QTabWidget QStackedWidget {{
        background-color: transparent;
    }}
    QTabWidget QStackedWidget > QWidget {{
        background-color: transparent;
    }}
    QTabWidget QSplitter {{
        background-color: transparent;
    }}
    QTabWidget QSplitter::handle {{
        background-color: transparent;
    }}
    QTabWidget QSplitter > QWidget {{
        background-color: transparent;
    }}
    QTabWidget QGroupBox {{
        background-color: transparent;
    }}
    QTabWidget QWidget#fileMgrContainer,
    QTabWidget QWidget#logViewerContainer {{
        background-color: transparent;
    }}
    /* fileMgr_tree 表头和表体透明 */
    QTreeView#fileMgr_tree {{
        background-color: transparent;
    }}
    QTreeView#fileMgr_tree QWidget {{
        background-color: transparent;
    }}
    QTreeView#fileMgr_tree::item {{
        background-color: transparent;
        border-radius: 0px;
    }}
    QTreeView#fileMgr_tree QHeaderView {{
        background-color: transparent;
    }}
    QTreeView#fileMgr_tree QHeaderView::section {{
        background-color: transparent;
        border: none;
        padding: 4px 8px;
    }}
    /* logViewer_textEdit 透明 */
    QListWidget#logViewer_textEdit {{
        background-color: transparent;
    }}
    QListWidget#logViewer_textEdit QWidget {{
        background-color: transparent;
    }}
    QListWidget#logViewer_textEdit::item {{
        background-color: transparent;
    }}
    QComboBox QAbstractItemView {{
        background-color: transparent;
    }}
    /* 复选框 + 表头透明 */
    QCheckBox {{
        background-color: transparent;
    }}
    QCheckBox::indicator {{
        background: transparent;
        background-color: transparent;
        border: 1px solid {accent};
        border-radius: 3px;
        width: 14px;
        height: 14px;
    }}
    QCheckBox::indicator:unchecked {{
        background: transparent;
        background-color: transparent;
    }}
    QCheckBox::indicator:checked {{
        background-color: {accent};
    }}
    QHeaderView {{
        background-color: transparent;
    }}
    QHeaderView::section {{
        background-color: transparent;
        border: none;
        padding: 4px 8px;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {t['text_disabled']};
        border: 1px solid transparent;
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 6px 16px;
        margin-right: 2px;
    }}
    QTabBar::tab:selected {{
        background-color: {t['bg_window']};
        color: {accent};
        border: 1px solid {accent};
        border-bottom: none;
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {t['bg_combo']};
        color: {t['text_primary']};
    }}

    /* ────────────── 下拉框 QComboBox ────────────── */
    QComboBox {{
        background-color: {t['bg_combo']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 4px 8px;
        min-height: 20px;
        text-align: left;
    }}
    QComboBox:hover {{
        border: 1px solid {accent};
        background-color: {t['bg_button']};
    }}
    QComboBox:focus {{
        border: 2px solid {accent};
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 22px;
        border-left: 1px solid {accent};
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
    }}
    QComboBox::down-arrow {{
        image: url({arrow});
        width: 12px;
        height: 12px;
        margin-right: 4px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t['bg_menu']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 4px;
        outline: none;
        selection-background-color: {rgba(80)};
        selection-color: #ffffff;
    }}
    QComboBox QAbstractItemView::item {{
        padding: 4px 8px;
        min-height: 22px;
    }}
    QComboBox QAbstractItemView::item:hover {{
        background-color: {rgba(50)};
    }}
    QComboBox QAbstractItemView::item:selected {{
        background-color: {rgba(90)};
        color: #ffffff;
    }}

    /* ────────────── 按钮 QPushButton ────────────── */
    QPushButton {{
        font: 400 10pt "{FONT_FAMILY}";
        color: {accent};
        background-color: {t['bg_button']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 6px 14px;
    }}
    QPushButton:hover {{
        background-color: {accent};
        color: {t['text_pressed']};
        border: 2px solid {accent};
    }}
    QPushButton:pressed {{
        background-color: {rgba(160)};
        color: {t['text_pressed']};
        border: 2px solid {t['text_pressed']};
        padding-left: 15px;
        padding-top: 7px;
    }}
    QPushButton:disabled {{
        color: {t['text_disabled']};
        border: 1px solid {t['border_disabled']};
        background-color: {t['bg_window']};
    }}

    /* ────────────── 分裂按钮（投屏/包列表：左主操作 + 右下拉菜单） ────────────── */
    QWidget#btnScrcpyContainer, QWidget#btnPkgListContainer {{
        background: transparent;
        border: none;
    }}
    QPushButton#btnScrcpyMain, QPushButton#btnPkgMain {{
        font: 400 10pt "{FONT_FAMILY}";
        color: {accent};
        background-color: {t['bg_button']};
        border: 1px solid {accent};
        border-top-left-radius: 6px;
        border-bottom-left-radius: 6px;
        border-top-right-radius: 0px;
        border-bottom-right-radius: 0px;
        padding: 6px 14px;
    }}
    QPushButton#btnScrcpyMain:hover, QPushButton#btnPkgMain:hover {{
        background-color: {accent};
        color: {t['text_pressed']};
        border: 2px solid {accent};
    }}
    QPushButton#btnScrcpyMain:pressed, QPushButton#btnPkgMain:pressed {{
        background-color: {rgba(160)};
        color: {t['text_pressed']};
        border: 2px solid {t['text_pressed']};
    }}
    QPushButton#btnScrcpyMain:disabled, QPushButton#btnPkgMain:disabled {{
        color: {t['text_disabled']};
        border-color: {t['border_disabled']};
        background-color: {t['bg_window']};
    }}
    QPushButton#btnScrcpyMenu, QPushButton#btnPkgMenu {{
        font: 400 10pt "{FONT_FAMILY}";
        color: {accent};
        background-color: {t['bg_button']};
        border: 1px solid {accent};
        border-top-right-radius: 6px;
        border-bottom-right-radius: 6px;
        border-top-left-radius: 0px;
        border-bottom-left-radius: 0px;
        padding: 6px 4px;
    }}
    QPushButton#btnScrcpyMenu:hover, QPushButton#btnPkgMenu:hover {{
        background-color: {accent};
        color: {t['text_pressed']};
        border: 2px solid {accent};
    }}
    QPushButton#btnScrcpyMenu:pressed, QPushButton#btnPkgMenu:pressed {{
        background-color: {rgba(160)};
        color: {t['text_pressed']};
        border: 2px solid {t['text_pressed']};
    }}
    QPushButton#btnScrcpyMenu::menu-indicator, QPushButton#btnPkgMenu::menu-indicator {{
        image: none;
        width: 0px;
        height: 0px;
    }}

    /* ────────────── 输入框 QLineEdit ────────────── */
    QLineEdit {{
        background-color: {t['bg_input']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 5px 8px;
        selection-background-color: {rgba(120)};
        selection-color: #ffffff;
    }}
    QLineEdit:focus {{
        border: 2px solid {accent};
    }}
    QLineEdit:disabled {{
        color: {t['text_disabled']};
        border: 1px solid {t['border_disabled']};
    }}

    /* ────────────── 数字框 QSpinBox ────────────── */
    QSpinBox {{
        background-color: {t['bg_input']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 4px 8px;
    }}
    QSpinBox:focus {{
        border: 2px solid {accent};
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        background-color: {t['bg_button']};
        border: 1px solid {accent};
        width: 18px;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background-color: {accent};
    }}
    QSpinBox::up-button {{
        border-top-right-radius: 4px;
    }}
    QSpinBox::down-button {{
        border-bottom-right-radius: 4px;
    }}

    /* ────────────── 文本编辑区 QTextEdit / QPlainTextEdit / QTextBrowser ────────────── */
    QTextEdit, QPlainTextEdit, QTextBrowser {{
        background-color: {t['bg_input']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 6px;
        selection-background-color: {rgba(120)};
        selection-color: #ffffff;
    }}
    QTextEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus {{
        border: 2px solid {accent};
    }}

    /* ────────────── 标签 QLabel ────────────── */
    QLabel {{
        background: transparent;
        border: none;
        color: {t['text_primary']};
    }}

    /* ────────────── 状态栏 QStatusBar ────────────── */
    QStatusBar {{
        background-color: {t['bg_statusbar']};
        color: {accent};
        border-top: 1px solid {t['bg_button']};
    }}
    QStatusBar::item {{
        border: none;
    }}

    /* ────────────── 菜单 QMenu ────────────── */
    QMenu {{
        background-color: {t['bg_menu']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 6px 24px 6px 16px;
        border-radius: 4px;
        background-color: transparent;
    }}
    QMenu::item:selected {{
        background-color: {rgba(110)};
        color: {t['text_hover']};
    }}
    QMenu::item:disabled {{
        color: {t['text_disabled']};
    }}
    QMenu::separator {{
        height: 1px;
        background-color: {t['bg_button']};
        margin: 4px 8px;
    }}

    /* ────────────── 滚动条 QScrollBar ────────────── */
    QScrollBar:vertical {{
        background: transparent;
        border: none;
        width: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {rgba(130)};
        min-height: 24px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {accent};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        border: none;
        background: none;
        height: 0px;
    }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        border: none;
        height: 10px;
        margin: 0px;
    }}
    QScrollBar::handle:horizontal {{
        background: {rgba(130)};
        min-width: 24px;
        border-radius: 5px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: {accent};
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        border: none;
        background: none;
        width: 0px;
    }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}

    /* ────────────── 树/列表/表格视图 QTreeView / QListView / QTableView / QListWidget ────────────── */
    QTreeView, QListView, QTableView, QListWidget {{
        background-color: {t['bg_input']};
        color: {t['text_primary']};
        border: 1px solid {accent};
        border-radius: 6px;
        outline: none;
        selection-background-color: {rgba(80)};
        selection-color: #ffffff;
    }}
    QTreeView::item, QListView::item, QTableView::item, QListWidget::item {{
        padding: 4px 6px;
        min-height: 22px;
        border-radius: 4px;
    }}
    QTreeView::item:selected, QListView::item:selected, QTableView::item:selected, QListWidget::item:selected {{
        background-color: {rgba(90)};
        color: #ffffff;
    }}
    QTreeView::item:hover, QListView::item:hover, QTableView::item:hover, QListWidget::item:hover {{
        background-color: {rgba(40)};
    }}

    /* ────────────── 表头 QHeaderView ────────────── */
    QHeaderView::section {{
        background-color: {t['bg_menu']};
        color: {accent};
        border: 1px solid {accent};
        border-left: none;
        border-top: none;
        padding: 6px 8px;
        font: 400 10pt "{FONT_FAMILY}";
    }}
    QHeaderView::section:first {{
        border-left: 1px solid {accent};
    }}
    QHeaderView::section:hover {{
        background-color: {rgba(40)};
    }}

    /* ────────────── 复选框 QCheckBox ────────────── */
    QCheckBox {{
        spacing: 8px;
        background: transparent;
        border: none;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        background-color: {t['bg_input']};
        border: 1px solid {accent};
        border-radius: 4px;
    }}
    QCheckBox::indicator:checked {{
        background-color: {accent};
    }}
    QCheckBox::indicator:hover {{
        border: 2px solid {accent};
    }}

    /* ────────────── 单选框 QRadioButton ────────────── */
    QRadioButton {{
        spacing: 8px;
        background: transparent;
        border: none;
    }}
    QRadioButton::indicator {{
        width: 18px;
        height: 18px;
        background-color: {t['bg_input']};
        border: 1px solid {accent};
        border-radius: 9px;
    }}
    QRadioButton::indicator:checked {{
        background-color: {accent};
    }}
    QRadioButton::indicator:hover {{
        border: 2px solid {accent};
    }}

    /* ────────────── 分割条 QSplitter ────────────── */
    QSplitter::handle {{
        background-color: transparent;
    }}
    QSplitter::handle:horizontal {{
        width: 8px;
        margin: 2px 0;
        border-radius: 4px;
        background-color: {rgba(40)};
    }}
    QSplitter::handle:horizontal:hover, QSplitter::handle:horizontal:pressed {{
        background-color: {accent};
    }}
    QSplitter::handle:vertical {{
        height: 8px;
        margin: 0 2px;
        border-radius: 4px;
        background-color: {rgba(40)};
    }}
    QSplitter::handle:vertical:hover, QSplitter::handle:vertical:pressed {{
        background-color: {accent};
    }}
"""


# 向后兼容：旧代码 `from ui_styles import STYLE_SHEET` 仍可工作（默认主题）
STYLE_SHEET = get_stylesheet(DEFAULT_THEME)
ACCENT = THEMES[DEFAULT_THEME]['accent']

# 主题持久化配置键（与 Super_ADB_Win.py 保持一致）
THEME_CONFIG_FILE = 'config/super_adb_config.json'
THEME_CONFIG_KEY = 'theme'


def load_saved_theme():
    """从配置文件读取用户上次选择的主题；读取失败或非法则回退默认主题。

    供无父窗口的独立进程/弹窗（如右键菜单「计算哈希」）在启动时跟随主题。
    """
    try:
        from adb_tools import 加载json配置
        tid = 加载json配置(THEME_CONFIG_FILE).get(THEME_CONFIG_KEY)
        if isinstance(tid, str) and tid in THEMES:
            return tid
    except Exception:
        pass
    return DEFAULT_THEME


def get_current_theme_id(widget=None):
    """从 widget 的父窗口链查找当前主题 id；找不到则读持久化配置，最后回退默认主题。

    用于弹窗/子窗口在创建时自动跟随主窗口当前主题，避免硬编码默认主题。
    独立进程（无父窗口）也能通过配置文件读到用户保存的主题。
    """
    p = widget
    while p is not None:
        theme = getattr(p, '_current_theme', None)
        if theme in THEMES:
            return theme
        p = p.parentWidget()
    return load_saved_theme()
