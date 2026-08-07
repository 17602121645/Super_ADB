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

UI 与逻辑分离：本模块只依赖 adb_utils.AdbDeviceOps 实例与 get_serial 回调。
"""
import os
import zipfile

from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QIcon, QFont, QColor, QPainter, QPixmap, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QPlainTextEdit, QCheckBox, QFileDialog, QMessageBox,
    QSplitter, QApplication, QStyle,
)

from 界面样式 import ACCENT, FONT_FAMILY
from popup_style import HIGHLIGHT_CARD_STYLE, add_green_glow
from axml_decoder import decode_axml, is_axml

# 文本类扩展名（即使解码失败也优先尝试当文本看）。
# 注意：`.xml` 不在此列，因为 APK 里的 XML 都是 Android Binary XML（二进制），
# 普通 zip 里的 XML 会靠可打印字符比例自动识别为文本。
_TEXT_EXT = {
    '.txt', '.json', '.html', '.htm', '.css', '.js', '.java', '.kt',
    '.properties', '.prop', '.pro', '.gradle', '.md', '.mf', '.sf', '.csv',
    '.yml', '.yaml', '.cfg', '.ini', '.text', '.log',
}
# 二进制扩展名（绝不预览文本）
_BIN_EXT = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.arsc', '.so', '.dex', '.odex',
    '.oat', '.ttf', '.otf', '.wav', '.mp3', '.mp4', '.RSA', '.DSA', '.EC',
    '.pdf', '.db', '.sqlite',
}

# 文件类型 → (徽标文字, 背景色)
_TYPE_ICONS = {
    '.xml': ('XML', '#4aa8ff'),
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
    '.log': ('TXT', '#8b949e'),
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
# 拖拽区
# ----------------------------------------------------------------------
class DropArea(QLabel):
    """可拖入文件 / 点击选择文件的虚线框区域。"""
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setMinimumHeight(72)
        self.setText('拖拽 APK / ZIP 安装包到此处\n（或点击选择文件）')
        self.setStyleSheet(
            f'QLabel{{background: rgba(255,255,255,0.03); border: 2px dashed '
            f'#3a3a3a; border-radius: 8px; color: #8b949e; '
            f'font: 10pt "{FONT_FAMILY}"; padding: 12px;}}'
            f'QLabel:hover{{border-color: {ACCENT}; color: #e0e0e0;}}')

    def mousePressEvent(self, ev):
        dlg = QFileDialog(self, '选择 APK / ZIP 文件', '',
                          '安装包 (*.apk *.zip *.aar *.jar);;所有文件 (*.*)')
        if dlg.exec():
            files = dlg.selectedFiles()
            if files:
                self.file_dropped.emit(files[0])

    def dragEnterEvent(self, ev: QDragEnterEvent):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()
            self.setStyleSheet(
                f'QLabel{{background: rgba(29,233,182,0.10); border: 2px '
                f'dashed {ACCENT}; border-radius: 8px; color: {ACCENT}; '
                f'font: 10pt "{FONT_FAMILY}"; padding: 12px;}}')

    def dragLeaveEvent(self, ev):
        self._restore_style()

    def dropEvent(self, ev: QDropEvent):
        self._restore_style()
        urls = ev.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.file_dropped.emit(path)
                ev.acceptProposedAction()

    def _restore_style(self):
        self.setStyleSheet(
            f'QLabel{{background: rgba(255,255,255,0.03); border: 2px dashed '
            f'#3a3a3a; border-radius: 8px; color: #8b949e; '
            f'font: 10pt "{FONT_FAMILY}"; padding: 12px;}}'
            f'QLabel:hover{{border-color: {ACCENT}; color: #e0e0e0;}}')


# ----------------------------------------------------------------------
# 后台任务线程
# ----------------------------------------------------------------------
class TaskThread(QThread):
    """在子线程执行 install / extract，避免卡 UI。"""
    progress = Signal(str)
    done = Signal(bool, str)

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


class LoadPackageThread(QThread):
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


class BuildTreeThread(QThread):
    """在子线程把 zip entries 整理成目录树 dict，不在子线程创建 GUI 对象。"""
    done = Signal(object, int)   # tree dict, file_count

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.entries = entries

    def run(self):
        root = {'name': '', 'full_path': '', 'is_dir': True, 'size': 0, 'children': {}}
        file_count = 0
        for info in self.entries:
            name = info.filename
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
                    node['size'] = info.file_size
                    file_count += 1
        self.done.emit(root, file_count)


# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class InstallZipDialog(QDialog):
    def __init__(self, adb, get_serial, parent=None):
        super().__init__(parent)
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

        self.setWindowTitle('安装 / 解包')
        self.setMinimumSize(760, 560)
        self.setStyleSheet(self._style())

        # 卡片容器：绿色高亮边框 + 发光
        self.card = QWidget(self)
        self.card.setObjectName('popupCard')
        self.card.setStyleSheet(HIGHLIGHT_CARD_STYLE)
        add_green_glow(self.card)

        self._build_ui()

        # 把卡片放入对话框
        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(self.card)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 10, 12, 10)

        # 拖拽区
        self.drop_area = DropArea(self)
        self.drop_area.file_dropped.connect(self.open_package)
        lay.addWidget(self.drop_area)

        # 文件信息
        self.info_label = QLabel('未选择文件')
        self.info_label.setStyleSheet(
            f'background: transparent; border: none; color: #8b949e; '
            f'font: 9pt "{FONT_FAMILY}";')
        lay.addWidget(self.info_label)

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
            except Exception:
                pass
            self._zf = None

        self._zip_path = None
        self._set_loading(True)
        self._load_thread = LoadPackageThread(path, self)
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
        self.setCursor(Qt.WaitCursor if loading else Qt.ArrowCursor)

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
        self._build_tree_thread = BuildTreeThread(entries, self)
        self._build_tree_thread.done.connect(self._on_tree_built)
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
        self._set_loading(False)

    def _on_package_error(self, msg):
        QMessageBox.warning(self, '打开失败', f'无法打开文件:\n{msg}')
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

        self._log(f'→ adb -s {serial} install {opts} {self._zip_path}')
        self.btn_install.setEnabled(False)
        self.btn_install.setText('安装中…')

        def _task():
            rc, out, err = self.adb.install_apk(
                serial, self._zip_path, extra_args=extra, timeout=180)
            msg = out or err or '(无输出)'
            return rc == 0, f'[returncode={rc}]\n{msg}'

        self._thread = TaskThread(_task)
        self._thread.done.connect(self._on_install_done)
        self._thread.start()

    def _on_install_done(self, ok, msg):
        self._log(msg)
        self.btn_install.setEnabled(True)
        self.btn_install.setText('安装')
        if ok:
            QMessageBox.information(self, '安装完成', '安装成功。')
        else:
            QMessageBox.warning(self, '安装失败',
                                '安装失败，详情见下方日志。')

    # ------------------------------------------------------------------
    # 解包
    # ------------------------------------------------------------------
    def extract_package(self):
        if self._zf is None or not self._zip_path:
            return
        dest = QFileDialog.getExistingDirectory(self, '选择解包目录',
                                                os.path.dirname(self._zip_path))
        if not dest:
            return
        base = os.path.splitext(os.path.basename(self._zip_path))[0]
        out_dir = os.path.join(dest, base + '_extracted')
        os.makedirs(out_dir, exist_ok=True)

        self.btn_extract.setEnabled(False)
        self._log(f'→ 解包到: {out_dir}')

        def _task():
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

        self._thread = TaskThread(_task)
        self._thread.done.connect(lambda ok, msg: (
            self._log(msg),
            self.btn_extract.setEnabled(True),
        ))
        self._thread.start()

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

    def _style(self):
        return (
            f'QDialog{{background: #2b2b2b; color: #e0e0e0; '
            f'font: 10pt "{FONT_FAMILY}";}}'
            f'QPushButton{{background: #333333; color: {ACCENT}; '
            f'border: 1px solid {ACCENT}; border-radius: 6px; padding: 6px 14px; '
            f'font: 9pt "{FONT_FAMILY}";}}'
            f'QPushButton:hover{{background: {ACCENT}; color: #1b1b1b;}}'
            f'QPushButton:pressed{{background: rgba(29,233,182,180); color: #1b1b1b;}}'
            f'QPushButton:disabled{{color: #777777; border: 1px solid #555555; '
            f'background: #2b2b2b;}}'
            f'QPushButton#primaryBtn{{background: {ACCENT}; color: #1b1b1b; '
            f'font-weight: bold; border: none;}}'
            f'QPushButton#primaryBtn:hover{{background: rgba(29,233,182,180);}}'
            f'QTreeWidget{{background: #1f1f1f; border: 1px solid #3a3a3a; '
            f'border-radius: 6px; color: #e0e0e0; outline: none; '
            f'font: 9pt "{FONT_FAMILY}";}}'
            f'QTreeWidget::item{{padding: 4px 6px; border-radius: 4px;}}'
            f'QTreeWidget::item:hover{{background: rgba(29,233,182,0.12);}}'
            f'QTreeWidget::item:selected{{background: rgba(29,233,182,0.36); color: #ffffff;}}'
            f'QHeaderView::section{{background: #2b2b2b; color: #8b949e; '
            f'border: none; padding: 4px;}}'
            f'QCheckBox{{spacing: 4px; background: transparent; color: #e0e0e0;}}'
            f'QCheckBox::indicator{{width: 16px; height: 16px; '
            f'border: 1px solid #3a3a3a; border-radius: 4px; background: #1f1f1f;}}'
            f'QCheckBox::indicator:hover{{border: 1px solid {ACCENT};}}'
            f'QCheckBox::indicator:checked{{background: {ACCENT}; border: 1px solid {ACCENT};}}'
        )

    def closeEvent(self, ev):
        if self._zf is not None:
            try:
                self._zf.close()
            except Exception:
                pass
        for t in (self._load_thread, self._build_tree_thread):
            if t is not None and t.isRunning():
                t.wait(1000)
        super().closeEvent(ev)
