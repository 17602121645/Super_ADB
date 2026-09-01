# -*- coding: utf-8 -*-
"""
安装 / 解包 弹窗
================
点击主界面「安装/解包」按钮弹出的独立对话框，模仿 Android Studio APK Analyzer：

- 支持把 APK / ZIP / AAR / JAR 等 zip 包拖入（也可点击选择文件）
- 以树形展示包内文件，点击文件可查看内容（文本直接预览 / 二进制显示大小与十六进制片段）
- 底部勾选 adb install 参数，默认勾选 -r (替换) 与 -t (允许测试包)
- 「安装」按钮执行 adb install 把拖入的包安装到当前设备
- 「解包」按钮把包内全部文件提取到指定目录

UI 与逻辑分离：本模块只依赖 adb_utils.Adb设备操作 实例与 get_serial 回调。
"""
import os
import re
import shutil
import subprocess
import zipfile

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QDialog, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QWidget, QPlainTextEdit, QTreeWidget, QTreeWidgetItem,
                               QCheckBox, QProgressBar, QFileDialog, QMessageBox,
                               QSplitter, QSizePolicy, QApplication, QStyle)

# 注册 png_rc 资源（应用图标 :/Super_ADB.png）
from ui import png_rc  # noqa: F401

from ui.dialog_base import 对话框基类
from ui.dialog_styles import add_green_glow, 拖拽区域
from ui.ui_styles import THEMES, FONT_FAMILY
from tools.axml_decoder import decode_axml, is_axml
from tools import cert_parser

# 文本类扩展名（即使解码失败也优先尝试当文本看）。
# 注意：`.xml` 不在此列，因为 APK 里的 XML 都是 Android Binary XML（二进制），
# 普通 zip 里的 XML 会靠可打印字符比例自动识别为文本。
_TEXT_EXT = {
    '.txt', '.json', '.html', '.htm', '.css', '.js', '.java', '.kt',
    '.properties', '.prop', '.pro', '.gradle', '.md', '.mf', '.sf', '.csv',
    '.yml', '.yaml', '.cfg', '.ini', '.text', '.日志',
}
# 二进制扩展名（绝不预览文本）
_BIN_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.arsc', '.so', '.dex', '.odex',
    '.oat', '.ttf', '.otf', '.wav', '.mp3', '.mp4', '.RSA', '.DSA', '.EC',
    '.pdf', '.db', '.sqlite',
}

# 文件类型 → (徽标文字, 背景色)
_TYPE_ICONS = {
    '.dex': ('DEX', '#7ee787'),
    '.odex': ('DEX', '#7ee787'),
    '.oat': ('DEX', '#7ee787'),
    '.txt': ('TXT', '#8b949e'),
    '.text': ('TXT', '#8b949e'),
    '.md': ('TXT', '#8b949e'),
    '.properties': ('TXT', '#8b949e'),
    '.prop': ('TXT', '#8b949e'),
    '.pro': ('TXT', '#8b949e'),
    '.gradle': ('TXT', '#8b949e'),
    '.cfg': ('TXT', '#8b949e'),
    '.ini': ('TXT', '#8b949e'),
    '.日志': ('TXT', '#8b949e'),
    '.mf': ('TXT', '#8b949e'),
    '.sf': ('TXT', '#8b949e'),
    '.json': ('JSON', '#79c0ff'),
    '.csv': ('CSV', '#79c0ff'),
    '.yml': ('YML', '#79c0ff'),
    '.yaml': ('YML', '#79c0ff'),
    '.html': ('HTML', '#79c0ff'),
    '.htm': ('HTML', '#79c0ff'),
    '.css': ('CSS', '#79c0ff'),
    '.js': ('JS', '#79c0ff'),
    '.java': ('JAVA', '#79c0ff'),
    '.kt': ('KT', '#79c0ff'),
    '.png': ('PNG', '#ffab40'),
    '.jpg': ('JPG', '#ffab40'),
    '.jpeg': ('JPG', '#ffab40'),
    '.gif': ('GIF', '#ffab40'),
    '.webp': ('WEBP', '#ffab40'),
    '.so': ('SO', '#f78166'),
    '.bin': ('BIN', '#d2a8ff'),
    '.arsc': ('ARSC', '#d2a8ff'),
    '.ttf': ('FONT', '#ff7b72'),
    '.otf': ('FONT', '#ff7b72'),
    '.wav': ('AV', '#a371f7'),
    '.mp3': ('AV', '#a371f7'),
    '.mp4': ('AV', '#a371f7'),
    '.pdf': ('PDF', '#ff7b72'),
    '.db': ('DB', '#39d0d8'),
    '.sqlite': ('DB', '#39d0d8'),
    '.RSA': ('CERT', '#ffca5a'),
    '.DSA': ('CERT', '#ffca5a'),
    '.EC': ('CERT', '#ffca5a'),
}

# 文件类型徽标缓存，避免为每个文件重复绘制 QPixmap/QPainter。
_ICON_CACHE: dict[tuple[str, str], QIcon] = {}


# ----------------------------------------------------------------------
# 拖拽区（共用 弹窗样式.拖拽区域；为保持单文件入口单独桥接一次）
# ----------------------------------------------------------------------


def _accent_rgb_str(accent: str) -> tuple[int, int, int]:
    """把 ``rgb(r,g,b)`` / ``rgb(r, g, b)`` 解析为 (r, g, b) 三元组。

    仅用于本文件内的 RGBA 透明度派生（hover/选中态），错误时回退黑，避免 QSS 报错。
    """
    s = accent
    if s.startswith('rgb(') and s.endswith(')'):
        s = s[4:-1]
    try:
        parts = [int(p.strip()) for p in s.split(',') if p.strip()][:3]
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
    except Exception as e:
        print(f'[安装弹窗] accent 解析失败，回退默认色: {e!r}')
    return 29, 233, 182  # fall-back to dark_teal accent


def _accent_rgba(accent: str, alpha: int) -> str:
    r, g, b = _accent_rgb_str(accent)
    return f'rgba({r},{g},{b},{alpha})'


# ----------------------------------------------------------------------
# 安装弹窗 QSS 模板（优化项6：替代 _style 中 30+ 行 f-string 拼接）
# 所有 {xxx} 为占位符，由 _style 方法用主题色填充；QSS 自身的花括号已双写。
# ----------------------------------------------------------------------
安装弹窗样式模板 = """
QDialog{{background: {bg_window}; color: {text_primary}; font: 10pt "{font}";}}
#popupCard{{background: {bg_window}; border: 4px solid {accent}; border-radius: 12px;}}
#popupCard QLabel{{background: transparent; border: none; color: {text_primary};}}
QPushButton{{background: {bg_button}; color: {accent}; border: 1px solid {accent}; border-radius: 6px; padding: 6px 14px; font: 9pt "{font}";}}
QPushButton:hover{{background: {accent}; color: {text_pressed};}}
QPushButton:pressed{{background: {accent_180}; color: {text_pressed};}}
QPushButton:disabled{{color: {text_disabled}; border: 1px solid {border_disabled}; background: {bg_window};}}
QPushButton#primaryBtn{{background: {accent}; color: {text_pressed}; font-weight: bold; border: none;}}
QPushButton#primaryBtn:hover{{background: {accent_180};}}
QTreeWidget{{background: {bg_input}; border: 1px solid {border_color}; border-radius: 6px; color: {text_primary}; outline: none; font: 9pt "{font}";}}
QTreeWidget::item{{padding: 4px 6px; border-radius: 4px;}}
QTreeWidget::item:hover{{background: {accent_50};}}
QTreeWidget::item:selected{{background: {accent_140}; color: #ffffff;}}
QHeaderView::section{{background: {bg_menu}; color: {accent}; border: none; padding: 4px;}}
QCheckBox{{spacing: 4px; background: transparent; color: {text_primary};}}
QCheckBox::indicator{{width: 16px; height: 16px; border: 1px solid {border_color}; border-radius: 4px; background: {bg_input};}}
QCheckBox::indicator:hover{{border: 1px solid {accent};}}
QCheckBox::indicator:checked{{background: {accent}; border: 1px solid {accent};}}
"""


# ----------------------------------------------------------------------
# 后台任务线程
# ----------------------------------------------------------------------
class 任务线程(QThread):
    """在子线程执行 install / extract，避免卡 UI。"""
    progress = Signal(str)
    done = Signal(bool, object)  # 第二参数兼容 str / dict 等任意类型

    def __init__(self, target, *args, **kwargs):
        super().__init__()
        self._target = target
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            ok, msg = self._target(*self._args, **self._kwargs)
            self.done.emit(bool(ok), msg)
        except Exception as e:
            self.done.emit(False, f'执行异常: {e}')


class 加载包线程(QThread):
    """在子线程打开 zip 包并读取文件目录，避免大 APK 拖入时卡死 UI。"""
    ok = Signal(object, list, str, int)   # zf, entries, path, size
    bad_zip = Signal(str, int)            # path, size
    error = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            size = os.path.getsize(self.path)
            zf = zipfile.ZipFile(self.path, 'r')
            entries = zf.infolist()
            self.ok.emit(zf, entries, self.path, size)
        except zipfile.BadZipFile:
            try:
                size = os.path.getsize(self.path)
            except Exception:
                size = 0
            self.bad_zip.emit(self.path, size)
        except Exception as e:
            self.error.emit(str(e))


class 构建目录树线程(QThread):
    """在子线程把 zip entries 整理成目录树 dict，不在子线程创建 GUI 对象。"""
    done = Signal(object, int)   # tree dict, file_count
    error = Signal(str)          # 构建失败时的错误信息

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.entries = entries

    def run(self):
        try:
            root = {'name': '', 'full_path': '', 'is_dir': True, 'size': 0, 'children': {}}
            file_count = 0
            for info in self.entries:
                name = getattr(info, 'filename', '')
                if not name:
                    continue
                is_dir_entry = name.endswith('/')
                parts = name.rstrip('/').split('/')
                if not parts or (len(parts) == 1 and parts[0] == ''):
                    continue
                node = root
                for i, part in enumerate(parts):
                    if not part:
                        continue
                    is_last = (i == len(parts) - 1)
                    children = node['children']
                    if part not in children:
                        full_path = '/'.join(parts[:i + 1])
                        if is_last and is_dir_entry:
                            full_path += '/'
                        children[part] = {
                            'name': part,
                            'full_path': full_path,
                            'is_dir': True,
                            'size': 0,
                            'children': {},
                        }
                    node = children[part]
                    if is_last and not is_dir_entry:
                        node['is_dir'] = False
                        node['size'] = getattr(info, 'file_size', 0)
                        file_count += 1
            self.done.emit(root, file_count)
        except Exception as e:
            import traceback
            self.error.emit(f'目录树构建异常: {e}\n{traceback.format_exc()}')


# ----------------------------------------------------------------------
# 带进度反馈的安装线程
# ----------------------------------------------------------------------
class 安装线程(QThread):
    """分阶段执行 push + pm install，实时回传进度与日志。"""
    progress = Signal(int, str)   # percent, stage_text
    日志 = Signal(str)
    done = Signal(bool, str)

    def __init__(self, adb, serial, apk_path, extra_args, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self.apk_path = apk_path
        self.extra_args = list(extra_args or [])

    def run(self):
        # 把命令/输出日志回传到对话框；install 内部复用 Adb助手 的日志回调
        old_cb = self.adb.log_callback
        self.adb.log_callback = self.日志.emit
        try:
            ok, msg = self.adb.安装(
                self.serial, self.apk_path, self.extra_args, 300, self.progress.emit)
        except Exception as e:
            ok, msg = False, f'安装异常: {e}'
        finally:
            self.adb.log_callback = old_cb
        self.done.emit(ok, msg)

    @staticmethod
    def _fmt_size(n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return str(n)
        if n >= 1024 * 1024:
            return f'{n / 1024 / 1024:.2f} MB'
        if n >= 1024:
            return f'{n / 1024:.1f} KB'
        return f'{n} B'


# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class 安装解包对话框(对话框基类):
    def __init__(self, adb, get_serial, parent=None):
        self.adb = adb
        self.get_serial = get_serial
        self._zf = None              # 当前打开的 ZipFile
        self._zip_path = None        # 当前包路径
        self._zip_size = 0           # 当前包大小
        self._thread = None          # install / extract 任务线程
        self._load_thread = None     # 打开包线程
        self._build_tree_thread = None   # 目录树构建线程
        self._tree_data = None       # 完整的目录树 dict
        self._folder_icon = None

        # 标题栏显示当前设备
        序列号 = get_serial() if callable(get_serial) else None
        标题 = f'安装 / 解包 — 设备: {序列号}' if 序列号 else '安装 / 解包 — 未连接设备'

        super().__init__(parent, 标题=标题, 最小尺寸=(760, 560), 发光=False)
        self._theme_id = self._主题id  # 兼容旧代码引用
        self.setStyleSheet(self._style(self._主题id))

        # 卡片容器：主题色高亮边框 + 发光（背景色由 _style 里的 #popupCard 规则随主题下发）
        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        _accent_color = QColor(THEMES[self._theme_id]['accent'])
        add_green_glow(self.card, accent=_accent_color)

        self._build_ui()

        # 把子控件的局部样式（meta_label / preview / progress_bar 等）也跟主题走
        self._apply_widget_styles(self._theme_id)

        # 把卡片放入对话框
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

    # ------------------------------------------------------------------
    # 主题切换
    # ------------------------------------------------------------------
    def apply_theme(self, theme_id):
        """主窗口切换主题时调用：刷新弹窗 QSS + 拖拽区域 虚线框颜色 + 子控件独立样式。

        文字 / 图标 / 按钮等大部分样式由 ``界面样式.get_stylesheet`` 提供；
        本类里还存在若干局部 ``setStyleSheet``（预览面板 / 进度条 / 日志 / 卡片），
        因此通过 ``_apply_widget_styles`` 重发一次，让它们跟着变。
        """
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self._主题id = theme_id
        self.setStyleSheet(self._style(theme_id))
        # 强制刷新 card 边框样式（Qt 样式缓存问题，切换主题后 border 可能不更新）
        try:
            from PySide6.QtWidgets import QStyle
            _st = self.card.style()
            if _st is not None:
                _st.unpolish(self.card)
                _st.polish(self.card)
            self.card.update()
        except Exception:
            pass
        if getattr(self, 'drop_area', None) is not None:
            self.drop_area.apply_theme(theme_id)
        self._apply_widget_styles(theme_id)
        # 更新卡片外发光颜色
        if hasattr(self, 'card') and self.card is not None:
            add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))

    def 子控件样式映射(self, tid):
        """返回 {属性名: 样式字符串} 的集中映射，供 _apply_widget_styles 循环下发。

        新增子控件只需在此加一行，避免在 _apply_widget_styles 里重复
        ``if getattr(self, 'xxx', None) is not None: self.xxx.setStyleSheet(...)``。
        progress_label 因含动态逻辑（安装失败态保持红色）不放入此映射，单独处理。
        """
        t = THEMES[tid]
        accent = t['accent']
        bg_input = t['bg_input']
        bg_button = t['bg_button']
        text_primary = t['text_primary']
        placeholder = '#5f6b6a' if tid == 'light_soft' else '#8b949e'
        return {
            'meta_label': (
                f'QLabel{{background: {_accent_rgba(accent, 30)}; border: 1px solid {accent}; '
                f'border-radius: 6px; color: {text_primary}; padding: 8px 10px; '
                f'font: 9pt "{FONT_FAMILY}";}}'),
            'info_label': (
                f'background: transparent; border: none; color: {placeholder}; '
                f'font: 9pt "{FONT_FAMILY}";'),
            'preview': (
                f'QPlainTextEdit{{background: {bg_input}; border: 1px solid {bg_button}; '
                f'border-radius: 6px; color: {text_primary}; font: 10pt "Consolas", '
                f'"{FONT_FAMILY}";}}'),
            'progress_bar': (
                f'QProgressBar{{background: {bg_input}; border: 1px solid {bg_button}; '
                f'border-radius: 6px; color: {text_primary}; text-align: center; '
                f'font: 9pt "{FONT_FAMILY}";}}'
                f'QProgressBar::chunk{{background: {accent}; border-radius: 6px;}}'),
            'log_edit': (
                f'QPlainTextEdit{{background: {bg_input}; border: 1px solid {bg_button}; '
                f'border-radius: 6px; color: {placeholder}; font: 9pt "Consolas", '
                f'"{FONT_FAMILY}";}}'),
        }

    def _apply_widget_styles(self, theme_id=None):
        """把所有 ``self.xxx.setStyleSheet`` 集中重发一次，统一跟随主题。

        为什么单独抽出来：这些子控件的 QSS 写死了 ``#1f1f1f`` / ``#c9d1d9`` 等固定色，
        不重发就会停留在旧主题；当 ``apply_theme`` 时再被调用一次整体覆盖。

        优化项3：改用 ``子控件样式映射`` + 循环，消除重复的 getattr 判空模式。
        """
        tid = theme_id or self._theme_id
        if tid not in THEMES:
            return
        # 静态样式子控件：循环统一下发
        for 属性名, 样式 in self.子控件样式映射(tid).items():
            控件 = getattr(self, 属性名, None)
            if 控件 is not None:
                控件.setStyleSheet(样式)
        # 复选框批量处理（文字色跟随主题）
        text_primary = THEMES[tid]['text_primary']
        for c in (getattr(self, 'chk_r', None), getattr(self, 'chk_t', None),
                  getattr(self, 'chk_d', None), getattr(self, 'chk_g', None),
                  getattr(self, 'chk_jadx', None)):
            if c is not None:
                c.setStyleSheet(
                    f'color: {text_primary}; font: 9pt "{FONT_FAMILY}"; '
                    f'background: transparent;')
        # progress_label：含动态逻辑（安装失败态保持错误红），单独处理
        if getattr(self, 'progress_label', None) is not None:
            err_color = '#c62828' if tid == 'light_soft' else '#ff7b72'
            placeholder = '#5f6b6a' if tid == 'light_soft' else '#8b949e'
            is_err = self.progress_label.text() == '安装失败'
            color = err_color if is_err else placeholder
            self.progress_label.setStyleSheet(
                f'background: transparent; border: none; color: {color}; '
                f'font: 9pt "{FONT_FAMILY}";')

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 10, 12, 10)

        # 拖拽区（共用 弹窗样式.拖拽区域；file_mode=single 仅取首个文件）
        self.drop_area = 拖拽区域(
            self,
            text='拖拽 APK / ZIP 安装包到此处\n（或点击选择文件）',
            file_filter='安装包 (*.apk *.zip *.aar *.jar);;所有文件 (*.*)',
            file_mode='single',
            theme_id=self._theme_id,
        )
        # 桥接：单文件入口与新版多文件 拖拽区域 对齐
        self.drop_area.paths_dropped.connect(
            lambda paths: self.open_package(paths[0]) if paths else None)
        lay.addWidget(self.drop_area)

        # 文件信息
        self.info_label = QLabel('未选择文件')
        self.info_label.setStyleSheet(
            f'background: transparent; border: none; color: #8b949e; '
            f'font: 9pt "{FONT_FAMILY}";')
        lay.addWidget(self.info_label)

        # APK 元信息 + 签名证书卡片
        self.meta_label = QLabel()
        self.meta_label.setWordWrap(True)
        self.meta_label.setTextFormat(Qt.RichText)       # 支持 <b> 等富文本
        self.meta_label.setOpenExternalLinks(False)
        self.meta_label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.meta_label.setMinimumHeight(80)              # 至少能容纳 4 行内容不被裁切
        self.meta_label.setStyleSheet(
            f'QLabel{{background: {_accent_rgba(THEMES[self._theme_id]["accent"], 30)}; '
            f'border: 1px solid {THEMES[self._theme_id]["accent"]}; '
            f'border-radius: 6px; color: {THEMES[self._theme_id]["text_primary"]}; '
            f'padding: 8px 10px; font: 9pt "{FONT_FAMILY}";}}')
        self.meta_label.setText('APK 元信息将显示在此处')
        self.meta_label.setVisible(False)
        lay.addWidget(self.meta_label)

        # 树 + 预览
        self.splitter = QSplitter(Qt.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['文件', '大小'])
        self.tree.setColumnWidth(0, 260)
        self.tree.setIconSize(QSize(24, 16))
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setStyleSheet(
            f'QPlainTextEdit{{background: #1f1f1f; border: 1px solid #3a3a3a; '
            f'border-radius: 6px; color: #e0e0e0; font: 10pt "Consolas", '
            f'"{FONT_FAMILY}";}}')
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.preview)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        lay.addWidget(self.splitter, 1)

        # adb install 参数
        opt_lay = QHBoxLayout()
        opt_lay.setSpacing(14)
        self.chk_r = QCheckBox('-r 替换已安装')
        self.chk_t = QCheckBox('-t 允许测试包')
        self.chk_d = QCheckBox('-d 允许降级')
        self.chk_g = QCheckBox('-g 授予权限')
        self.chk_r.setChecked(True)
        self.chk_t.setChecked(True)
        for c in (self.chk_r, self.chk_t, self.chk_d, self.chk_g):
            c.setStyleSheet(f'color: #c9d1d9; font: 9pt "{FONT_FAMILY}"; '
                            f'background: transparent;')
            opt_lay.addWidget(c)
        opt_lay.addStretch(1)
        self.chk_jadx = QCheckBox('解包后自动反编译 classes.dex (jadx)')
        self.chk_jadx.setChecked(True)
        self.chk_jadx.setStyleSheet(f'color: #c9d1d9; font: 9pt "{FONT_FAMILY}"; '
                                    f'background: transparent;')
        opt_lay.addWidget(self.chk_jadx)
        lay.addLayout(opt_lay)

        # 按钮行
        btn_lay = QHBoxLayout()
        btn_lay.addStretch(1)
        self.btn_extract = QPushButton('解包 / 提取')
        self.btn_extract.clicked.connect(self.extract_package)
        self.btn_extract.setEnabled(False)
        btn_lay.addWidget(self.btn_extract)
        self.btn_install = QPushButton('安装')
        self.btn_install.setObjectName('primaryBtn')
        self.btn_install.clicked.connect(self.install_package)
        self.btn_install.setEnabled(False)
        btn_lay.addWidget(self.btn_install)
        self.btn_close = QPushButton('关闭')
        self.btn_close.clicked.connect(self.close)
        btn_lay.addWidget(self.btn_close)
        lay.addLayout(btn_lay)

        # 进度
        progress_lay = QHBoxLayout()
        progress_lay.setSpacing(8)
        self.progress_label = QLabel('准备...')
        self.progress_label.setStyleSheet(
            f'background: transparent; border: none; color: #8b949e; '
            f'font: 9pt "{FONT_FAMILY}";')
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignCenter)
        self.progress_bar.setStyleSheet(
            f'QProgressBar{{background: {THEMES[self._theme_id]["bg_input"]}; '
            f'border: 1px solid {THEMES[self._theme_id]["bg_button"]}; '
            f'border-radius: 6px; color: {THEMES[self._theme_id]["text_primary"]}; '
            f'text-align: center; font: 9pt "{FONT_FAMILY}";}}'
            f'QProgressBar::chunk{{background: {THEMES[self._theme_id]["accent"]}; '
            f'border-radius: 6px;}}')
        progress_lay.addWidget(self.progress_label, 0)
        progress_lay.addWidget(self.progress_bar, 1)
        lay.addLayout(progress_lay)

        # 日志
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumHeight(96)
        self.log_edit.setPlaceholderText('安装 / 解包日志…')
        self.log_edit.setStyleSheet(
            f'QPlainTextEdit{{background: #1f1f1f; border: 1px solid #3a3a3a; '
            f'border-radius: 6px; color: #8b949e; font: 9pt "Consolas", '
            f'"{FONT_FAMILY}";}}')
        lay.addWidget(self.log_edit)

    # ------------------------------------------------------------------
    # 打开 / 解析包
    # ------------------------------------------------------------------
    def open_package(self, path: str):
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, '无效文件', f'文件不存在:\n{path}')
            return
        if self._load_thread is not None and self._load_thread.isRunning():
            self._log('已有包正在打开中，请稍候…')
            return
        # 关闭旧包
        if self._zf is not None:
            try:
                self._zf.close()
            except Exception as e:
                print(f'[安装弹窗] 关闭旧包失败: {e!r}')
            self._zf = None

        self._zip_path = None
        self._set_loading(True)
        self._load_thread = 加载包线程(path, self)
        self._load_thread.ok.connect(self._on_package_loaded)
        self._load_thread.bad_zip.connect(self._on_package_bad_zip)
        self._load_thread.error.connect(self._on_package_error)
        self._load_thread.start()

    def _set_loading(self, loading: bool):
        self.drop_area.setEnabled(not loading)
        self.btn_extract.setEnabled(not loading and self._zf is not None)
        self.btn_install.setEnabled(not loading and self._zip_path is not None)
        if loading:
            self.info_label.setText('正在打开…')
            self.preview.setPlainText('正在解析安装包，请稍候…')
            self.tree.clear()

    def _on_package_loaded(self, zf, entries, path, size):
        self._zf = zf
        self._zip_path = path
        self._zip_size = size
        self.info_label.setText(
            f'{os.path.basename(path)}  （{self._fmt_size(size)}）'
            f'  ·  共 {len(entries)} 个条目  ·  正在构建目录树…')
        self.btn_install.setEnabled(True)
        self.btn_extract.setEnabled(True)
        # 在子线程构建目录树 dict，主线程只创建可见的顶层节点
        self._build_tree_thread = 构建目录树线程(entries, self)
        self._build_tree_thread.done.connect(self._on_tree_built)
        self._build_tree_thread.error.connect(self._on_tree_error)
        self._build_tree_thread.start()

    def _on_package_bad_zip(self, path, size):
        self._zip_path = path
        self.info_label.setText(
            f'{os.path.basename(path)}  （{self._fmt_size(size)}）'
            f'  ·  非 zip 包，无法浏览内部文件')
        self.tree.clear()
        self.preview.setPlainText('该文件不是 zip 类包（APK/ZIP/AAR/JAR），'
                                  '无法解包浏览，但仍可直接「安装」。')
        self.btn_extract.setEnabled(False)
        self.btn_install.setEnabled(True)
        self.meta_label.setVisible(False)
        self.meta_label.setText('APK 元信息将显示在此处')
        self._set_loading(False)

    def _on_package_error(self, msg):
        QMessageBox.warning(self, '打开失败', f'无法打开文件:\n{msg}')
        self._set_loading(False)

    def _on_tree_error(self, msg):
        self._log(msg)
        self.preview.setPlainText(f'目录树构建失败:\n{msg}')
        self.info_label.setText(
            f'{os.path.basename(self._zip_path)}  （{self._fmt_size(self._zip_size)}）'
            f'  ·  目录树构建失败')
        self._set_loading(False)

    def _on_tree_built(self, tree_data, file_count):
        self._tree_data = tree_data
        self.tree.clear()
        self.tree.setUniformRowHeights(True)
        self._folder_icon = QApplication.style().standardIcon(QStyle.SP_DirIcon)
        try:
            self.tree.setUpdatesEnabled(False)
            self.tree.blockSignals(True)
            for child in sorted(tree_data['children'].values(), key=lambda n: n['name']):
                self._add_tree_node(self.tree.invisibleRootItem(), child)
        except Exception as e:
            import traceback
            self._log(f'建树异常: {e}\n{traceback.format_exc()}')
            self.preview.setPlainText(f'文件列表构建失败: {e}')
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
        self.info_label.setText(
            f'{os.path.basename(self._zip_path)}  （{self._fmt_size(self._zip_size)}）'
            f'  ·  共 {file_count} 个文件  ·  点击文件夹展开')
        self.preview.setPlainText('左侧选择文件可查看内容，点击文件夹展开子目录。')
        self._set_loading(False)
        if self._zip_path and self._zip_path.lower().endswith('.apk'):
            self._start_apk_meta_load()
        else:
            self.meta_label.setVisible(False)
            self.meta_label.setText('APK 元信息将显示在此处')

    def _add_tree_node(self, parent_item, node):
        item = QTreeWidgetItem(parent_item)
        item.setText(0, node['name'])
        # 文件夹以 '/' 结尾，方便 _on_item_clicked 区分文件/目录
        path = node['full_path'] + '/' if node['is_dir'] else node['full_path']
        item.setData(0, Qt.UserRole, path)
        if node['is_dir']:
            item.setIcon(0, self._folder_icon)
            if node['children']:
                item.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        else:
            item.setIcon(0, self._icon_for_entry(node['full_path']))
            item.setText(1, self._fmt_size(node['size']))

    # 文件夹展开时一次性创建太多 QTreeWidgetItem 会卡 UI，改为分批加载
    _EXPAND_BATCH = 50

    def _on_item_expanded(self, item):
        if item.childCount() > 0:
            return
        entry = item.data(0, Qt.UserRole)
        if not entry:
            return
        parts = entry.rstrip('/').split('/')
        node = self._tree_data
        for part in parts:
            if not part:
                continue
            if part in node['children']:
                node = node['children'][part]
            else:
                return
        if not node['is_dir'] or not node['children']:
            return
        children = sorted(node['children'].values(), key=lambda n: n['name'])
        item._lazy_children = children
        item._lazy_index = 0
        self._expand_batch(item)

    def _expand_batch(self, item):
        children = getattr(item, '_lazy_children', None)
        if not children:
            return
        index = item._lazy_index
        n = len(children)
        end = min(n, index + self._EXPAND_BATCH)
        try:
            self.tree.setUpdatesEnabled(False)
            self.tree.blockSignals(True)
            while index < end:
                self._add_tree_node(item, children[index])
                index += 1
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)
        item._lazy_index = index
        if index < n:
            QTimer.singleShot(0, lambda: self._expand_batch(item))

    def _icon_for_entry(self, name: str) -> QIcon:
        """根据扩展名返回对应类型徽标，未知类型回退系统默认文件图标。"""
        ext = os.path.splitext(name)[1]
        if not ext and '.' in name:
            # 处理 Android 签名证书扩展名 .RSA/.DSA/.EC 等
            ext = '.' + name.rsplit('.', 1)[-1]
        label, color = _TYPE_ICONS.get(ext.upper() if ext.startswith('.') else ext.lower(), (None, None))
        if label:
            return self._make_type_icon(label, color)
        return QApplication.style().standardIcon(QStyle.SP_FileIcon)

    @staticmethod
    def _make_type_icon(label: str, color: str) -> QIcon:
        """绘制 24x16 圆角小徽标（带缓存，避免每个文件重复绘制）。"""
        key = (label, color)
        cached = _ICON_CACHE.get(key)
        if cached is not None:
            return cached
        pm = QPixmap(24, 16)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(0, 0, 24, 16, 4, 4)
        p.setPen(Qt.white)
        f = QFont(FONT_FAMILY, 7)
        f.setBold(True)
        p.setFont(f)
        # 按文字长度微调字号
        if len(label) > 3:
            f2 = QFont(FONT_FAMILY, 6)
            f2.setBold(True)
            p.setFont(f2)
        p.drawText(pm.rect(), Qt.AlignCenter, label.upper())
        p.end()
        icon = QIcon(pm)
        _ICON_CACHE[key] = icon
        return icon

    # ------------------------------------------------------------------
    # APK 元信息 + 签名证书
    # ------------------------------------------------------------------
    def _start_apk_meta_load(self):
        """后台解析 AndroidManifest.xml 和签名证书，避免卡 UI。"""
        self.meta_label.setVisible(True)
        self.meta_label.setText('正在解析 APK 元信息 / 签名证书…')

        def _任务():
            return self._parse_apk_meta(self._zip_path)

        self._meta_thread = 任务线程(_任务)
        self._meta_thread.done.connect(self._on_meta_ready)
        self._meta_thread.start()

    @staticmethod
    def _parse_apk_meta(apk_path: str) -> tuple:
        """解析 AndroidManifest.xml 与签名证书，返回 (ok, result_dict|error_msg)。"""
        result = {
            'ok': True,
            'manifest': {},
            'cert': {'ok': False, 'certs': [], 'error': ''},
        }
        if not apk_path or not os.path.isfile(apk_path):
            return False, '文件不存在'

        try:
            with zipfile.ZipFile(apk_path, 'r') as zf:
                data = zf.read('AndroidManifest.xml')
        except KeyError:
            result['manifest'] = {'has_manifest': False}
        except Exception as e:
            return False, f'读取清单失败: {e}'
        else:
            xml = ''
            try:
                if is_axml(data):
                    xml = decode_axml(data)
                else:
                    xml = data.decode('utf-8', errors='replace')
            except Exception as e:
                result['manifest'] = {'has_manifest': True, 'parse_error': str(e)}
            else:
                m = re.search(r'package\s*=\s*"([^"]+)"', xml)
                vc = re.search(r'android:versionCode\s*=\s*"(\d+)"', xml)
                vn = re.search(r'android:versionName\s*=\s*"([^"]+)"', xml)
                mins = re.search(r'android:minSdkVersion\s*=\s*"(\d+)"', xml)
                tgt = re.search(r'android:targetSdkVersion\s*=\s*"(\d+)"', xml)
                result['manifest'] = {
                    'has_manifest': True,
                    'package': m.group(1) if m else '',
                    'versionCode': vc.group(1) if vc else '',
                    'versionName': vn.group(1) if vn else '',
                    'minSdk': mins.group(1) if mins else '',
                    'targetSdk': tgt.group(1) if tgt else '',
                }

        try:
            result['cert'] = cert_parser.parse_apk_certs(apk_path, timeout=30)
        except Exception as e:
            result['cert'] = {'ok': False, 'certs': [], 'error': str(e)}
        return True, result

    def _on_meta_ready(self, ok, msg_or_result):
        if not ok or not isinstance(msg_or_result, dict):
            self.meta_label.setText(f'APK 元信息解析失败: {msg_or_result}')
            self.meta_label.setVisible(True)
            return
        r = msg_or_result
        m = r.get('manifest', {})
        c = r.get('cert', {})

        parts = []
        if m.get('has_manifest'):
            meta_items = []
            if m.get('package'):
                meta_items.append(f'包名: <b>{m["package"]}</b>')
            if m.get('versionCode'):
                meta_items.append(f'versionCode: <b>{m["versionCode"]}</b>')
            if m.get('versionName'):
                meta_items.append(f'versionName: <b>{m["versionName"]}</b>')
            if m.get('minSdk'):
                meta_items.append(f'minSdk: <b>{m["minSdk"]}</b>')
            if m.get('targetSdk'):
                meta_items.append(f'targetSdk: <b>{m["targetSdk"]}</b>')
            if meta_items:
                parts.append(' · '.join(meta_items))
        else:
            parts.append('未找到 AndroidManifest.xml')

        if c.get('ok') and c.get('certs'):
            cert = c['certs'][0]
            issuer = cert.get('issuer') or cert.get('owner') or '未知'
            valid = ''
            if cert.get('valid_from') and cert.get('valid_until'):
                valid = f'{cert["valid_from"]} 至 {cert["valid_until"]}'
            sha1 = cert.get('sha1', '')
            # SHA1 指纹完整 40 位十六进制，不再截断（wordWrap 会自动换行）
            sha1_display = sha1.replace(':', '') if sha1 else ''
            parts.append(f'签发者: <b>{issuer}</b>')
            if valid:
                parts.append(f'有效期: <b>{valid}</b>')
            if sha1:
                parts.append(f'SHA1: <b>{sha1_display}</b>')
        elif not m.get('has_manifest') and not c.get('ok'):
            pass
        else:
            err = c.get('error', '')
            if err:
                parts.append(f'签名解析: {err}')

        self.meta_label.setText('<br>'.join(parts) if parts else '未解析到 APK 元信息')
        self.meta_label.setVisible(bool(parts))

    # ------------------------------------------------------------------
    # 内容预览
    # ------------------------------------------------------------------
    def _on_item_clicked(self, item, _col):
        entry = item.data(0, Qt.UserRole)
        if not entry or self._zf is None:
            return
        if entry.endswith('/'):
            # 点击文件夹：自动展开/折叠并给出提示
            item.setExpanded(not item.isExpanded())
            self.preview.setPlainText('文件夹，点击左侧箭头可展开/折叠子目录。')
            return

        MAX_PREVIEW_BYTES = 200_000
        ext = os.path.splitext(entry)[1].lower()
        try:
            info = self._zf.getinfo(entry)
            # 非 XML 大文本只读前 200KB，避免大文件解码卡死；
            # XML（AXML）需要完整文件结构，通常也不大，直接读完整。
            if info.file_size > MAX_PREVIEW_BYTES and ext != '.xml':
                with self._zf.open(entry) as f:
                    data = f.read(MAX_PREVIEW_BYTES)
                truncated = True
            else:
                data = self._zf.read(entry)
                truncated = False
        except Exception as e:
            self.preview.setPlainText(f'读取失败: {e}')
            return

        if ext in _BIN_EXT:
            self._show_binary(entry, data)
            return
        # APK 里的 .xml（如 AndroidManifest.xml、res/*.xml）是 Android Binary XML，
        # 用 is_axml 识别后解码成可读文本；解码失败时在二进制预览上方附加错误信息。
        if ext == '.xml' and is_axml(data):
            try:
                text = decode_axml(data)
                if not text.strip():
                    raise ValueError('AXML 解码结果为空')
                self.preview.setPlainText(text)
            except Exception as e:
                binary = self._binary_preview(entry, data)
                self.preview.setPlainText(
                    f'Android Binary XML 解码失败: {e}\n'
                    f'已回退到二进制预览:\n\n{binary}')
            return
        if self._looks_text(data, ext):
            try:
                text = self._decode(data)
            except Exception:
                self._show_binary(entry, data)
                return
            if len(text) > 200_000:
                text = text[:200_000] + '\n\n…（内容过大，仅显示前 200000 字符）'
            if truncated:
                text += (f'\n\n[文件总大小 {self._fmt_size(info.file_size)}，'
                         f'仅预览前 {MAX_PREVIEW_BYTES} 字节]')
            self.preview.setPlainText(text)
        else:
            self._show_binary(entry, data)

    @staticmethod
    def _looks_text(data: bytes, ext: str) -> bool:
        if not data:
            return True
        sample = data[:1024]
        # 任何含空字节的样本都是二进制，扩展名不能推翻
        if b'\x00' in sample:
            return False
        if ext in _TEXT_EXT:
            return True
        printable = sum(1 for b in sample
                        if 32 <= b < 127 or b in (9, 10, 13))
        return printable / len(sample) > 0.7 if sample else True

    @staticmethod
    def _decode(data: bytes) -> str:
        for enc in ('utf-8', 'gb18030', 'latin-1'):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode('utf-8', errors='replace')

    def _binary_preview(self, entry, data) -> str:
        size = len(data)
        head = data[:256]
        hexlines = []
        for i in range(0, len(head), 16):
            chunk = head[i:i + 16]
            hexpart = ' '.join(f'{b:02x}' for b in chunk)
            asciipart = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
            hexlines.append(f'{i:08x}  {hexpart:<47}  {asciipart}')
        hex_text = '\n'.join(hexlines)
        return (f'二进制文件: {entry}\n大小: {self._fmt_size(size)}\n\n'
                f'前 256 字节十六进制预览:\n{hex_text}')

    def _show_binary(self, entry, data):
        self.preview.setPlainText(self._binary_preview(entry, data))

    # ------------------------------------------------------------------
    # 安装
    # ------------------------------------------------------------------
    def install_package(self):
        if not self._zip_path:
            return
        serial = self.get_serial()
        if not serial:
            QMessageBox.warning(self, '未选择设备',
                                '请先在主窗口选择或连接一个设备。')
            return
        extra = []
        if self.chk_r.isChecked():
            extra.append('-r')
        if self.chk_t.isChecked():
            extra.append('-t')
        if self.chk_d.isChecked():
            extra.append('-d')
        if self.chk_g.isChecked():
            extra.append('-g')
        opts = ' '.join(extra)

        self.btn_install.setEnabled(False)
        self.btn_install.setText('安装中…')
        self.btn_extract.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText('准备...')
        placeholder = '#5f6b6a' if self._theme_id == 'light_soft' else '#8b949e'
        self.progress_label.setStyleSheet(
            f'background: transparent; border: none; color: {placeholder}; '
            f'font: 9pt "{FONT_FAMILY}";')

        self._thread = 安装线程(
            self.adb, serial, self._zip_path, extra, self)
        self._thread.progress.connect(self._on_install_progress)
        self._thread.日志.connect(self._log)
        self._thread.done.connect(self._on_install_done)
        self._thread.start()

    def _on_install_progress(self, percent, text):
        self.progress_bar.setValue(percent)
        self.progress_label.setText(text)

    def _on_install_done(self, ok, msg):
        self._log(msg)
        self.btn_install.setEnabled(True)
        self.btn_install.setText('安装')
        self.btn_extract.setEnabled(True)
        if ok:
            self.progress_bar.setValue(100)
            self.progress_label.setText('安装完成')
            self._auto_close_msg('安装完成', '安装成功。')
        else:
            self.progress_label.setText('安装失败')
            err_color = '#c62828' if self._theme_id == 'light_soft' else '#ff7b72'
            self.progress_label.setStyleSheet(
                f'background: transparent; border: none; color: {err_color}; '
                f'font: 9pt "{FONT_FAMILY}";')
            self._auto_close_msg('安装失败', '安装失败，详情见下方日志。',
                                icon=QMessageBox.Icon.Warning)

    # ------------------------------------------------------------------
    # 解包
    # ------------------------------------------------------------------
    def extract_package(self):
        if self._zf is None or not self._zip_path:
            return
        # 默认解包目录：桌面
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        if not os.path.isdir(desktop):
            # macOS/Linux 兼容
            desktop = os.path.expanduser('~')
        base = os.path.splitext(os.path.basename(self._zip_path))[0]
        default_dir = os.path.join(desktop, base)
        dest = QFileDialog.getExistingDirectory(self, '选择解包目录',
                                                default_dir)
        if not dest:
            return
        # 如果选择的目录就是默认目录（桌面/APK名），直接用；否则在选择的目录下创建 APK 名子目录
        if os.path.normpath(dest) == os.path.normpath(default_dir):
            out_dir = dest
        else:
            out_dir = os.path.join(dest, base)
        os.makedirs(out_dir, exist_ok=True)

        self.btn_extract.setEnabled(False)
        self._log(f'→ 解包到: {out_dir}')

        def _任务():
            total = 0
            for info in self._zf.infolist():
                if info.is_dir():
                    continue
                target = os.path.join(out_dir, info.filename)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with self._zf.open(info) as src, open(target, 'wb') as f:
                    f.write(src.read())
                total += 1
            return True, f'解包完成，共提取 {total} 个文件到:\n{out_dir}'

        self._extract_out_dir = out_dir
        self._thread = 任务线程(_任务)
        self._thread.done.connect(self._on_extract_done)
        self._thread.start()

    def _on_extract_done(self, ok, msg):
        self._log(msg)
        self.btn_extract.setEnabled(True)
        if ok:
            # 解包成功弹窗（自动关闭）
            out_dir = getattr(self, '_extract_out_dir', '')
            self._auto_close_msg('解包完成', f'已成功解包到:\n{out_dir}')
            if getattr(self, 'chk_jadx', None) and self.chk_jadx.isChecked():
                if self._zip_path and self._zip_path.lower().endswith('.apk'):
                    dex = os.path.join(self._extract_out_dir, 'classes.dex')
                    if os.path.isfile(dex):
                        self._start_jadx_decompile(dex)
        else:
            # 解包失败弹窗（自动关闭）
            self._auto_close_msg('解包失败', msg, icon=QMessageBox.Icon.Warning)

    def _start_jadx_decompile(self, dex_path):
        jadx = self._find_jadx()
        if not jadx:
            self._log('jadx 未找到，跳过反编译（请确保 jadx 在 PATH 中）')
            return
        out = os.path.join(os.path.dirname(dex_path), 'jadx_src')
        self._log(f'→ 启动 jadx 反编译: {jadx} -d {out}')
        self.btn_extract.setEnabled(False)
        self.btn_extract.setText('反编译中…')

        def _任务():
            try:
                proc = subprocess.run(
                    [jadx, '-d', out, dex_path],
                    capture_output=True, text=True, timeout=300,
                    encoding='utf-8', errors='replace',
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                tail = (proc.stdout or '') + (proc.stderr or '')
                tail = tail[:1200]
                return proc.returncode == 0, f'jadx 反编译完成（returncode={proc.returncode}）:\n{tail}'
            except Exception as e:
                return False, f'jadx 反编译失败: {e}'

        self._thread = 任务线程(_任务)
        self._thread.done.connect(self._on_jadx_done)
        self._thread.start()

    def _on_jadx_done(self, ok, msg):
        self._log(msg)
        self.btn_extract.setEnabled(True)
        self.btn_extract.setText('解包 / 提取')

    @staticmethod
    def _find_jadx() -> str | None:
        """在 PATH 与常见路径里找 jadx 可执行文件。"""
        for name in ('jadx', 'jadx.bat', 'jadx.exe'):
            exe = shutil.which(name)
            if exe:
                return exe
        candidates = [
            r'C:\Program Files\jadx\bin\jadx.bat',
            r'C:\Program Files\jadx\bin\jadx',
            r'D:\tools\jadx\bin\jadx.bat',
            r'D:\tools\jadx\bin\jadx',
            r'D:\jadx\bin\jadx.bat',
            r'D:\jadx\bin\jadx',
            os.path.expanduser(r'~\tools\jadx\bin\jadx.bat'),
            os.path.expanduser(r'~\scoop\apps\jadx\current\bin\jadx.bat'),
        ]
        for c in candidates:
            if os.path.isfile(c):
                return c
        return None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_size(n):
        try:
            n = int(n)
        except (TypeError, ValueError):
            return str(n)
        if n >= 1024 * 1024:
            return f'{n / 1024 / 1024:.2f} MB'
        if n >= 1024:
            return f'{n / 1024:.1f} KB'
        return f'{n} B'

    def _log(self, text):
        self.log_edit.appendPlainText(text)

    def _auto_close_msg(self, title, message, icon=QMessageBox.Icon.Information, timeout_ms=2000):
        """显示一个自动关闭的消息框。

        Args:
            title: 标题
            message: 内容
            icon: 图标类型
            timeout_ms: 自动关闭延迟（毫秒），默认 2 秒
        """
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.setIcon(icon)
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        QTimer.singleShot(timeout_ms, msg_box.accept)
        msg_box.exec()

    def _style(self, theme_id=None):
        """生成弹窗 QSS。颜色全部跟随主题，未指定时回退当前主题。

        使用模块级 ``安装弹窗样式模板`` + ``str.format()`` 填充，
        替代旧版 30+ 行 f-string 拼接，QSS 结构一目了然。
        """
        if not theme_id or theme_id not in THEMES:
            theme_id = self._theme_id if hasattr(self, '_theme_id') else 'dark_cyan'
        t = THEMES[theme_id]
        accent = t['accent']
        return 安装弹窗样式模板.format(
            accent=accent,
            bg_window=t['bg_window'],
            bg_button=t['bg_button'],
            bg_input=t['bg_input'],
            bg_menu=t['bg_menu'],
            text_primary=t['text_primary'],
            text_pressed=t['text_pressed'],
            text_disabled=t['text_disabled'],
            border_disabled=t['border_disabled'],
            border_color=t['bg_button'],
            font=FONT_FAMILY,
            accent_180=_accent_rgba(accent, 180),
            accent_140=_accent_rgba(accent, 140),
            accent_50=_accent_rgba(accent, 50),
        )

    def closeEvent(self, ev):
        if self._zf is not None:
            try:
                self._zf.close()
            except Exception as e:
                print(f'[安装弹窗] 关闭时释放 zip 失败: {e!r}')
        for t in (self._load_thread, self._build_tree_thread,
                  getattr(self, '_meta_thread', None), getattr(self, '_thread', None)):
            if t is not None and t.isRunning():
                t.wait(1000)
        super().closeEvent(ev)
