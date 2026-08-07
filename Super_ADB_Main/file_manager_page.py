# -*- coding: utf-8 -*-
"""
ADB 文件管理器 —— 内嵌子页面
================================
提供设备文件树浏览、上传/下载/删除/重命名功能。
后台操作通过 QRunnable 线程池执行，UI 通过信号更新。
"""

import os
import shutil
import tempfile

from PySide6.QtCore import (
    Qt, QThreadPool, QRunnable, Signal, QObject, QEvent, QTimer)
from PySide6.QtGui import QStandardItemModel, QStandardItem, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeView, QComboBox, QPushButton,
    QLabel, QHeaderView, QFileDialog, QInputDialog, QMessageBox, QMenu,
    QAbstractItemView, QLineEdit, QDialog, QPlainTextEdit)

from adb_utils import (AdbFileManager, format_device_label,
                       load_json_config, save_json_config, AdbError)


# 内置文本预览器支持的文件扩展名（双击即用 QuickLook 式预览）
PREVIEW_EXT = {
    '.xml', '.txt', '.json', '.log', '.csv', '.conf', '.prop', '.ini',
    '.md', '.yml', '.yaml', '.gradle', '.sh', '.bat', '.cfg', '.properties',
}

LOADED_ROLE = Qt.UserRole + 1

# 四列宽度占比：名称 / 大小 / 权限 / 修改时间
COL_RATIOS = (0.4582, 0.0776, 0.1014, 0.24)
CONFIG_NAME = 'adb_shell_config.json'


# ----------------------------------------------------------------------
# 后台 Worker
# ----------------------------------------------------------------------
class _WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class _CmdWorker(QRunnable):
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = _WorkerSignals()
        self.setAutoDelete(False)

    def run(self):
        try:
            r = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


# ----------------------------------------------------------------------
# 内置文本预览器（仿 macOS QuickLook）
# ----------------------------------------------------------------------
class TextPreviewDialog(QDialog):
    """只读展示文本文件内容；支持复制全部、超大文件截断提示。"""

    def __init__(self, entry, parent=None):
        super().__init__(parent)
        self.entry = entry
        self.setWindowTitle(f'预览 — {entry["name"]}')
        self.resize(760, 540)
        if parent is not None:
            try:
                self.setWindowIcon(parent.window().windowIcon())
            except Exception:
                pass
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        size = entry.get('size', '—')
        info = QLabel(f'路径: {entry["path"]}    大小: {size} B')
        info.setWordWrap(True)
        lay.addWidget(info)

        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont('Consolas, "DejaVu Sans Mono", "Courier New", monospace')
        mono.setPointSize(11)
        self.edit.setFont(mono)
        self.edit.setPlainText('加载中…')
        lay.addWidget(self.edit, 1)

        btn_box = QHBoxLayout()
        btn_box.addStretch(1)
        self.btn_copy = QPushButton('复制全部')
        self.btn_copy.clicked.connect(self._copy_all)
        btn_box.addWidget(self.btn_copy)
        btn_close = QPushButton('关闭')
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        btn_box.addWidget(btn_close)
        lay.addLayout(btn_box)

    def set_content(self, text, truncated=False):
        self.edit.setPlainText(text)
        if truncated:
            self.edit.appendPlainText('\n\n—— 文件过大，仅显示前 2 MB ——')

    def set_error(self, msg):
        self.edit.setPlainText(f'读取失败：{msg}')

    def _copy_all(self):
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(self.edit.toPlainText())


# ----------------------------------------------------------------------
# 子页面
# ----------------------------------------------------------------------
class FileManagerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mgr = AdbFileManager()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(4)
        self._current_serial = None
        self._root_path = '/sdcard'
        self._dir_items = {}
        self._loading = set()
        self._live_workers = []
        self._col_ratios = tuple(COL_RATIOS)
        self._applying = False
        self._restore_col_ratios()
        self._wired = False          # 双击/搜索只连接一次
        self._search_wired = False   # 搜索框 textChanged 只连一次
        self.search_edit = None      # 搜索框（动态创建，.ui 同步时再固化）
        self._search_text = ''       # 当前搜索关键字（小写）

        self._built = False
        self._build_ui()
        if self._mgr.check_adb():
            self._scan_devices()

    def inject_widgets(self, *, tree: QTreeView, device_combo: QComboBox,
                       btn_refresh: QPushButton, btn_root: QPushButton,
                       path_label: QLabel, status_label: QLabel):
        """将 .ui 中预定义的控件注入，替代 _build_ui() 创建的控件。"""
        if self._built:
            return
        self._built = True
        # 替换关键控件引用
        self.tree = tree
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(['名称', '大小', '权限', '修改时间'])
        self.tree.setModel(self.model)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context)
        self.tree.expanded.connect(self._on_expanded)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_header_modes()
        self.tree.header().sectionResized.connect(self._on_section_resized)
        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        QTimer.singleShot(0, self._apply_col_widths)

        self.device_combo = device_combo
        self.device_combo.currentIndexChanged.connect(self._on_device)
        self.btn_refresh = btn_refresh
        self.btn_refresh.clicked.connect(self._scan_devices)
        self.btn_root = btn_root
        self.btn_root.clicked.connect(self._toggle_root)
        self.path_label = path_label
        self.status_label = status_label

        # 清理旧控件（_build_ui 创建的）
        layout = self.layout()
        if layout:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
        self.setLayout(QVBoxLayout(self))
        self.layout().setContentsMargins(0, 0, 0, 0)
        self.layout().setSpacing(0)

        # 搜索框挂到 tree 所在布局顶部；双击预览 + 过滤只连一次
        self._place_search_box()
        self._wired = False  # tree 对象已替换为 .ui 注入的新实例，需重连双击
        self._wire_tree_interactions()

        if self._mgr.check_adb():
            self._scan_devices()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 工具栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel('设备:'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        self.device_combo.currentIndexChanged.connect(self._on_device)
        bar.addWidget(self.device_combo)

        self.btn_refresh = QPushButton('刷新设备')
        self.btn_refresh.clicked.connect(self._scan_devices)
        bar.addWidget(self.btn_refresh)

        self.btn_root = QPushButton(f'根目录: {self._root_path}')
        self.btn_root.clicked.connect(self._toggle_root)
        bar.addWidget(self.btn_root)
        bar.addWidget(self._ensure_search_edit())
        bar.addStretch(1)

        self.path_label = QLabel('—')
        bar.addWidget(self.path_label)

        self.status_label = QLabel('就绪')
        bar.addWidget(self.status_label)
        layout.addLayout(bar)

        # 文件树
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(['名称', '大小', '权限', '修改时间'])
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context)
        self.tree.expanded.connect(self._on_expanded)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._apply_header_modes()
        self.tree.header().sectionResized.connect(self._on_section_resized)
        self.tree.installEventFilter(self)
        self.tree.viewport().installEventFilter(self)
        QTimer.singleShot(0, self._apply_col_widths)
        layout.addWidget(self.tree, 1)
        self._wire_tree_interactions()

    # ------------------------------------------------------------------
    # Worker 管理
    # ------------------------------------------------------------------
    def _track(self, worker, on_result=None, on_error=None, on_finished=None):
        if on_result:
            worker.signals.result.connect(on_result)
        if on_error:
            worker.signals.error.connect(on_error)
        worker.signals.finished.connect(lambda: self._drop(worker))
        if on_finished:
            worker.signals.finished.connect(on_finished)
        self._live_workers.append(worker)
        self._pool.start(worker)

    def _drop(self, worker):
        try:
            self._live_workers.remove(worker)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # 列宽按比例铺满
    # ------------------------------------------------------------------
    def _apply_header_modes(self):
        """前三列可拖拽、最后一列（修改时间）Stretch 补齐剩余宽度。

        注意：model.clear() 重建表头后列模式会被重置，必须重新调用本方法。
        """
        header = self.tree.header()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            tree = getattr(self, 'tree', None)
            if tree is not None and (obj is tree or obj is tree.viewport()):
                self._apply_col_widths()
        return super().eventFilter(obj, ev)

    def _apply_col_widths(self):
        w = self.tree.viewport().width()
        if w <= 0:
            QTimer.singleShot(50, self._apply_col_widths)
            return
        ratios = self._col_ratios
        head = list(ratios[:-1])
        head_sum = sum(head)
        if head_sum > 0.95:
            scale = 0.95 / head_sum
            head = [r * scale for r in head]
        self._applying = True
        try:
            for i, r in enumerate(head):
                self.tree.setColumnWidth(i, int(w * r))
        finally:
            self._applying = False
        # 最后一列（修改时间）Stretch，自动补齐剩余宽度，保证水平铺满无缝隙

    # ------------------------------------------------------------------
    # 列宽占比持久化
    # ------------------------------------------------------------------
    def _restore_col_ratios(self):
        """启动时从配置恢复四列占比，缺失/非法则回退 COL_RATIOS 默认值。"""
        ratios = load_json_config(CONFIG_NAME).get('col_ratios')
        if (isinstance(ratios, (list, tuple)) and len(ratios) == 4
                and all(isinstance(v, (int, float)) and v > 0 for v in ratios)):
            self._col_ratios = tuple(float(v) for v in ratios)

    def _on_section_resized(self, logical_index, old_w, new_w):
        """手动拖拽列宽后记录新占比并写入配置（程序化调整由 _applying 屏蔽）。"""
        if self._applying or logical_index >= 3:
            return
        w = self.tree.viewport().width()
        if w <= 0:
            return
        ratios = list(self._col_ratios)
        ratios[logical_index] = new_w / w
        head_sum = sum(ratios[:3])
        if head_sum > 0.98:
            scale = 0.98 / head_sum
            ratios = [r * scale for r in ratios[:3]] + [ratios[3]]
        self._col_ratios = tuple(ratios)
        QTimer.singleShot(200, self._save_col_ratios)

    def _save_col_ratios(self):
        cfg = load_json_config(CONFIG_NAME)
        cfg['col_ratios'] = [round(r, 4) for r in self._col_ratios]
        save_json_config(CONFIG_NAME, cfg)

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    def _scan_devices(self):
        self._status('正在扫描设备…')
        w = _CmdWorker(self._mgr.get_devices)
        self._track(w, on_result=self._on_devices, on_error=lambda e: self._status(f'扫描失败: {e}'))

    def _on_devices(self, devices):
        self._fill_devices(devices)
        if self.device_combo.count() > 0:
            self._on_device()
        else:
            self._status('无设备')

    def _fill_devices(self, devices, select_serial=None):
        """填充设备下拉框；优先选中 select_serial，否则尽量保留当前选中项。"""
        if select_serial is None:
            select_serial = self.device_combo.currentData()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for d in devices:
            if d.get('state') != 'device':
                continue
            self.device_combo.addItem(format_device_label(d), d.get('serial'))
        idx = self.device_combo.findData(select_serial) if select_serial else -1
        if idx >= 0:
            self.device_combo.setCurrentIndex(idx)
        self.device_combo.blockSignals(False)

    # 供主窗口统一同步：连接/刷新后三处下拉框一起更新
    def sync_devices(self, devices, select_serial=None):
        prev = self.device_combo.currentData()
        self._fill_devices(devices, select_serial)
        new = self.device_combo.currentData()
        # 仅当选中设备真正变化时才重载根目录，避免刷新时打断浏览
        if new and new != prev:
            self._on_device()

    def _on_device(self):
        serial = self.device_combo.currentData()
        if not serial:
            return
        self._current_serial = serial
        self._build_root()

    def _toggle_root(self):
        self._root_path = '/' if self._root_path != '/' else '/sdcard'
        self.btn_root.setText(f'根目录: {self._root_path}')
        if self._current_serial:
            self._build_root()

    def _build_root(self):
        self._dir_items.clear()
        self._loading.clear()
        self.model.clear()
        self.model.setHorizontalHeaderLabels(['名称', '大小', '权限', '修改时间'])
        self._apply_header_modes()
        QTimer.singleShot(0, self._apply_col_widths)
        item = QStandardItem(self._root_path)
        item.setData({'is_dir': True, 'path': self._root_path}, Qt.UserRole)
        item.setData(False, LOADED_ROLE)
        item.appendRow(QStandardItem(''))
        self._dir_items[self._root_path] = item
        self.model.appendRow([item, QStandardItem('—'), QStandardItem('—'), QStandardItem('—')])
        self.tree.setExpanded(item.index(), True)
        self._apply_search_filter()

    # ------------------------------------------------------------------
    # 懒加载
    # ------------------------------------------------------------------
    def _on_expanded(self, index):
        item = self.model.itemFromIndex(index)
        if not item:
            return
        if item.data(LOADED_ROLE):
            return
        entry = item.data(Qt.UserRole) or {}
        path = entry.get('path', '')
        if not path or path in self._loading:
            return
        self._loading.add(path)
        self._status(f'加载: {path}…')
        w = _CmdWorker(self._mgr.list_dir, self._current_serial, path)
        self._track(w, on_result=lambda e: self._populate(item, e),
                   on_error=lambda e: self._on_list_err(item, path, e),
                   on_finished=lambda: self._loading.discard(path))

    def _populate(self, item, entries):
        was_exp = self.tree.isExpanded(item.index())
        item.removeRows(0, item.rowCount())
        dirs = sorted([e for e in entries if e['is_dir']], key=lambda e: e['name'].lower())
        files = sorted([e for e in entries if not e['is_dir']], key=lambda e: e['name'].lower())
        for e in dirs + files:
            ni = QStandardItem(e['name'])
            ni.setData(e, Qt.UserRole)
            ni.setData(False, LOADED_ROLE)
            sz = '—' if e['is_dir'] else self._fmt_size(e['size'])
            si = QStandardItem(sz)
            pi = QStandardItem(e['perm'])
            ti = QStandardItem(e['mtime'])
            if e['is_dir']:
                ni.appendRow(QStandardItem(''))
                self._dir_items[e['path']] = ni
            item.appendRow([ni, si, pi, ti])
        item.setData(True, LOADED_ROLE)
        if was_exp:
            self.tree.setExpanded(item.index(), True)
        self._apply_search_filter()
        self._status(f'已加载 {self._item_path(item)}（{len(entries)} 项）')

    def _on_list_err(self, item, path, err):
        item.removeRows(0, item.rowCount())
        self.tree.setExpanded(item.index(), False)
        item.setData(False, LOADED_ROLE)
        self._loading.discard(path)
        self._status(f'加载失败: {err}')

    def _refresh_dir(self, path):
        item = self._dir_items.get(path)
        if not item or path in self._loading:
            return
        item.removeRows(0, item.rowCount())
        item.setData(False, LOADED_ROLE)
        self._loading.add(path)
        w = _CmdWorker(self._mgr.list_dir, self._current_serial, path)
        self._track(w, on_result=lambda e: self._populate(item, e),
                   on_error=lambda e: self._on_list_err(item, path, e),
                   on_finished=lambda: self._loading.discard(path))

    def _refresh_current(self):
        idx = self.tree.currentIndex()
        if idx.isValid():
            item = self.model.itemFromIndex(idx)
            entry = item.data(Qt.UserRole) or {}
            path = entry.get('path', '')
            if entry.get('is_dir'):
                self._refresh_dir(path)
            else:
                self._refresh_dir(self._dirname(path))
        else:
            self._refresh_dir(self._root_path)

    # ------------------------------------------------------------------
    # 右键菜单
    # ------------------------------------------------------------------
    def _on_context(self, pos):
        idx = self.tree.indexAt(pos)
        item = self.model.itemFromIndex(idx) if idx.isValid() else None
        entry = item.data(Qt.UserRole) if item else None
        menu = QMenu(self)
        act_up = menu.addAction('上传文件…')
        act_dl = menu.addAction('下载…')
        act_rn = menu.addAction('重命名…')
        act_del = menu.addAction('删除…')
        menu.addSeparator()
        act_rf = menu.addAction('刷新')
        if entry is None:
            act_dl.setEnabled(False)
            act_rn.setEnabled(False)
            act_del.setEnabled(False)
        act_up.triggered.connect(lambda: self._upload())
        act_dl.triggered.connect(lambda: self._download())
        act_rn.triggered.connect(lambda: self._rename())
        act_del.triggered.connect(lambda: self._delete())
        act_rf.triggered.connect(lambda: self._refresh_current())
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------
    # 文件操作
    # ------------------------------------------------------------------
    def _selected_path(self):
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        item = self.model.itemFromIndex(idx)
        entry = item.data(Qt.UserRole) or {}
        return entry

    def _target_dir(self):
        entry = self._selected_path()
        if entry:
            return entry['path'] if entry.get('is_dir') else self._dirname(entry['path'])
        return self._root_path

    def _upload(self):
        if not self._current_serial:
            return
        target = self._target_dir()
        local, _ = QFileDialog.getOpenFileName(self, '选择要上传的文件', '', '所有文件 (*.*)')
        if not local:
            return
        self._status(f'上传: {os.path.basename(local)}…')
        w = _CmdWorker(self._mgr.push, self._current_serial, local, target)
        self._track(w,
                    on_result=lambda r: (self._status('上传成功'), self._refresh_dir(target)),
                    on_error=lambda e: self._status(f'上传失败: {e}'))

    def _download(self):
        entry = self._selected_path()
        if not entry:
            return
        if entry.get('is_dir'):
            local_dir = QFileDialog.getExistingDirectory(self, '选择保存目录', os.path.expanduser('~'))
            if not local_dir:
                return
            target = local_dir
        else:
            desktop = os.path.expanduser('~')
            default_file = os.path.join(desktop, 'Desktop', entry['name'])
            target, _ = QFileDialog.getSaveFileName(self, '选择保存位置', default_file, '所有文件 (*.*)')
            if not target:
                return
        self._status(f'下载: {entry["name"]}…')
        w = _CmdWorker(self._mgr.pull, self._current_serial, entry['path'], target)
        self._track(w,
                    on_result=lambda r: self._status('下载成功'),
                    on_error=lambda e: self._status(f'下载失败: {e}'))

    def _rename(self):
        entry = self._selected_path()
        if not entry:
            return
        new, ok = QInputDialog.getText(self, '重命名', '输入新名称：', text=entry['name'])
        if not (ok and new and new != entry['name']):
            return
        parent = self._dirname(entry['path'])
        new_path = parent.rstrip('/') + '/' + new
        self._status(f'重命名: {entry["name"]} → {new}…')
        w = _CmdWorker(self._mgr.rename_path, self._current_serial, entry['path'], new_path)
        self._track(w,
                    on_result=lambda r: (self._status('重命名成功'), self._refresh_dir(parent)),
                    on_error=lambda e: self._status(f'重命名失败: {e}'))

    def _delete(self):
        entry = self._selected_path()
        if not entry:
            return
        reply = QMessageBox.question(self, '确认删除', f'确定删除 "{entry["name"]}" 吗？', QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        parent = self._dirname(entry['path'])
        self._status(f'删除: {entry["name"]}…')
        w = _CmdWorker(self._mgr.delete_path, self._current_serial, entry['path'])
        self._track(w,
                    on_result=lambda r: (self._status('删除成功'), self._refresh_dir(parent)),
                    on_error=lambda e: self._status(f'删除失败: {e}'))

    # ------------------------------------------------------------------
    # 搜索 & 预览
    # ------------------------------------------------------------------
    def _ensure_search_edit(self):
        if self.search_edit is None:
            self.search_edit = QLineEdit()
            self.search_edit.setPlaceholderText('搜索当前目录文件名…')
            self.search_edit.setClearButtonEnabled(True)
        if not self._search_wired:
            self._search_wired = True
            self.search_edit.textChanged.connect(self._on_search_text_changed)
        return self.search_edit

    def _place_search_box(self):
        """inject 模式下把搜索框插到 tree 所在布局的顶部（正式界面可见）。"""
        self._ensure_search_edit()
        parent = self.tree.parentWidget()
        if parent is None:
            return
        layout = parent.layout()
        if layout is None:
            return
        idx = -1
        for i in range(layout.count()):
            w = layout.itemAt(i).widget()
            if w is self.tree:
                idx = i
                break
        if idx >= 0:
            layout.insertWidget(idx, self.search_edit)
        else:
            layout.addWidget(self.search_edit)

    def _wire_tree_interactions(self):
        """双击预览，仅连接一次（inject 路径替换 tree 后会重连）。"""
        if self._wired:
            return
        self._wired = True
        self.tree.doubleClicked.connect(self._on_double_clicked)

    def _on_double_clicked(self, index):
        item = self.model.itemFromIndex(index)
        if not item:
            return
        entry = item.data(Qt.UserRole) or {}
        if not entry or entry.get('is_dir'):
            return
        name = entry.get('name', '').lower()
        if any(name.endswith(ext) for ext in PREVIEW_EXT):
            self._preview_file(entry)

    def _preview_file(self, entry):
        dlg = TextPreviewDialog(entry, self)
        dlg.show()
        serial = self._current_serial
        if not serial:
            dlg.set_error('未选择设备')
            return
        w = _CmdWorker(self._mgr.read_text, serial, entry['path'])
        self._track(w,
                    on_result=lambda r: dlg.set_content(r['text'], r.get('truncated', False)),
                    on_error=lambda e: dlg.set_error(e))

    def _on_search_text_changed(self, text):
        self._search_text = (text or '').strip().lower()
        self._apply_search_filter()

    def _apply_search_filter(self):
        root = self.model.invisibleRootItem()
        if self._search_text:
            for i in range(root.rowCount()):
                self._filter_item(root.child(i), self._search_text)
        else:
            for i in range(root.rowCount()):
                self._unhide_all(root.child(i))

    def _filter_item(self, item, text):
        """返回 item 自身或其子孙是否匹配；不匹配则隐藏该行。"""
        if item is None:
            return False
        entry = item.data(Qt.UserRole) or {}
        name = (entry.get('name') or '').lower()
        children_visible = False
        if item.rowCount():
            for r in range(item.rowCount()):
                if self._filter_item(item.child(r), text):
                    children_visible = True
        is_dir = entry.get('is_dir', False)
        visible = (text in name) or (is_dir and children_visible)
        self.tree.setRowHidden(item.row(), item.index().parent(), not visible)
        return visible

    def _unhide_all(self, item):
        if item is None:
            return
        self.tree.setRowHidden(item.row(), item.index().parent(), False)
        for r in range(item.rowCount()):
            self._unhide_all(item.child(r))

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _item_path(self, item):
        entry = item.data(Qt.UserRole) or {}
        return entry.get('path', '')

    @staticmethod
    def _dirname(path):
        if path in ('/', ''):
            return '/'
        path = path.rstrip('/')
        if '/' not in path:
            return '/'
        return path.rsplit('/', 1)[0] or '/'

    @staticmethod
    def _fmt_size(size):
        if size <= 0:
            return '0 B'
        for u in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024 or u == 'TB':
                return f'{size:.0f} {u}' if u == 'B' else f'{size:.1f} {u}'
            size /= 1024
        return f'{size:.1f} PB'

    def _status(self, msg):
        self.status_label.setText(msg)
