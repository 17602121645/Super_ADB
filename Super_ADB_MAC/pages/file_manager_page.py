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
    QAbstractItemView, QLineEdit, QDialog, QPlainTextEdit, QProgressBar)

from tools.adb_tools import (AdbFileManager, 格式化设备标签,
                       加载json配置, 保存json配置, AdbError)


# 内置文本预览器支持的文件扩展名（双击即用 QuickLook 式预览）
PREVIEW_EXT = {
    '.xml', '.txt', '.json', '.log', '.csv', '.conf', '.prop', '.ini',
    '.md', '.yml', '.yaml', '.gradle', '.sh', '.bat', '.cfg', '.properties',
}

LOADED_ROLE = Qt.UserRole + 1

# 四列宽度占比：名称 / 大小 / 权限 / 修改时间
# 首次启动默认值（源自 super_adb_config.json 的 col_ratios）
COL_RATIOS = (0.3195, 0.2344, 0.1747, 0.24)
CONFIG_NAME = 'config/super_adb_config.json'


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


class _ProgressWorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    progress = Signal(int, int, float)  # sent, total, elapsed


class _ProgressCmdWorker(QRunnable):
    """支持进度回调的 Worker，用于文件上传/下载等耗时操作。"""

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = _ProgressWorkerSignals()
        self.setAutoDelete(False)

    def run(self):
        def _progress_cb(sent, total, elapsed=0.0):
            self.signals.progress.emit(int(sent), int(total), float(elapsed))

        try:
            kwargs = dict(self.kwargs)
            kwargs['progress_cb'] = _progress_cb
            r = self.func(*self.args, **kwargs)
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
class 文件管理页(QWidget):
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
        self._tree_evf_installed = False  # tree/viewport 事件过滤器只装一次（_build_ui / inject_widgets 双路径）
        self._wired = False          # 双击/搜索只连接一次
        self._search_wired = False   # 搜索框 textChanged 只连一次
        self.search_edit = None      # 搜索框（动态创建，.ui 同步时再固化）
        self._search_text = ''       # 当前搜索关键字（小写）
        self._deep_search_mode = False  # 深度搜索模式（回车触发 find 递归搜索）
        self.progress_bar = None     # 上传进度条（动态创建）

        self._built = False
        self._build_ui()
        # 不在构造期扫描设备：由主窗口 刷新设备() 统一触发，经 sync_devices() 下发。
        # 否则启动时会并发扫描三次（主窗口 + 本页 + 日志页），且本页此刻 log_callback
        # 尚未挂上、_build_ui 创建的下拉框随后又被 inject_widgets 替换，纯属浪费。

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
        from PySide6.QtCore import QSize
        self.tree.setIconSize(QSize(0, 0))
        self._apply_header_modes()
        # 表头跟随主题
        from PySide6.QtWidgets import QFrame
        self.tree.header().setSortIndicatorShown(False)
        self.tree.header().setIconSize(QSize(0, 0))
        self.tree.header().setFrameShape(QFrame.Shape.NoFrame)
        self.tree.header().sectionResized.connect(self._on_section_resized)
        if not getattr(self, '_tree_evf_installed', False):
            self.tree.installEventFilter(self)
            self.tree.viewport().installEventFilter(self)
            self._tree_evf_installed = True
        QTimer.singleShot(0, self._apply_col_widths)

        self.device_combo = device_combo
        self.device_combo.currentIndexChanged.connect(self._on_device)
        self.btn_refresh = btn_refresh
        self.btn_refresh.clicked.connect(self._scan_devices)
        self.btn_root = btn_root
        self.btn_root.clicked.connect(self._toggle_root)
        self.path_label = path_label
        self.status_label = status_label

        # 创建进度条并插入到 status_label 前面
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat('%p%')
        self.progress_bar.hide()
        # 找到 status_label 在其父布局中的位置并插入进度条
        status_parent = self.status_label.parentWidget()
        if status_parent and status_parent.layout():
            status_layout = status_parent.layout()
            for i in range(status_layout.count()):
                item = status_layout.itemAt(i)
                if item.widget() is self.status_label:
                    status_layout.insertWidget(i, self.progress_bar)
                    break
            else:
                status_layout.addWidget(self.progress_bar)

        # 清理旧控件（_build_ui 创建的）。注入模式下本页仅作逻辑控制器，
        # 可见控件来自 .ui，自身不再建立布局——直接卸下 _build_ui 遗留布局，
        # 避免重复 setLayout 触发
        # “QLayout: Attempting to add QLayout … which already has a layout” 告警。
        old_layout = self.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
                elif item.layout() is not None:
                    item.layout().deleteLater()
            # 注意：PySide6 6.11.1 的 QWidget 未暴露 takeLayout()（C++ Qt6 有，
            # 但本绑定未提供），故用 QLayout.deleteLater() 安全卸下旧布局，
            # 避免二次 setLayout 触发 “already has a layout” 告警，也不会崩。
            old_layout.deleteLater()

        # 搜索框挂到 tree 所在布局顶部；双击预览 + 过滤只连一次
        self._place_search_box()
        self._wired = False  # tree 对象已替换为 .ui 注入的新实例，需重连双击
        self._wire_tree_interactions()
        # 不在此自动扫描设备：由主窗口 刷新设备() 统一触发，
        # 通过 sync_devices() 同步下拉框，避免与主窗口扫描竞态互相覆盖。

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 工具栏
        bar = QHBoxLayout()
        bar.addWidget(QLabel('设备:'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        # 可编辑+只读：允许选中文本复制（Ctrl+C），但不允许修改
        self.device_combo.setEditable(True)
        self.device_combo.lineEdit().setReadOnly(True)
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

        # 上传进度条（默认隐藏）
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(200)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat('%p%')
        self.progress_bar.hide()
        bar.addWidget(self.progress_bar)

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
        if not getattr(self, '_tree_evf_installed', False):
            self.tree.installEventFilter(self)
            self.tree.viewport().installEventFilter(self)
            self._tree_evf_installed = True
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

    def 设置日志回调(self, cb):
        """把文件操作（上传/下载/删除等）的详细日志接到主窗口输出区。"""
        self._mgr.log_callback = cb

    def _log(self, msg):
        cb = getattr(self._mgr, 'log_callback', None)
        if cb:
            try:
                cb(msg)
            except Exception:
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
        # 防御：PySide6 绑定层会把非 QObject（如布局项 QWidgetItem）误传为
        # watched 参数，直接放行避免 super() 抛 TypeError（PYSIDE-3143 变体）
        if not isinstance(obj, QObject):
            return False
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
        ratios = 加载json配置(CONFIG_NAME).get('col_ratios')
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
        cfg = 加载json配置(CONFIG_NAME)
        cfg['col_ratios'] = [round(r, 4) for r in self._col_ratios]
        保存json配置(CONFIG_NAME, cfg)

    # ------------------------------------------------------------------
    # 设备
    # ------------------------------------------------------------------
    def _scan_devices(self):
        self._status('正在扫描设备…')
        w = _CmdWorker(self._mgr.获取设备列表)
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
            self.device_combo.addItem(格式化设备标签(d), d.get('serial'))
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
        self._deep_search_mode = False  # 重建根目录时退出深度搜索模式
        self._dir_items.clear()
        self._loading.clear()
        self.model.clear()
        self.model.setHorizontalHeaderLabels(['名称', '大小', '权限', '修改时间'])
        self._apply_header_modes()
        QTimer.singleShot(0, self._apply_col_widths)
        _rp = self._root_path
        _nm = _rp.rstrip('/').rsplit('/', 1)[-1] or _rp
        item = QStandardItem(_rp)
        item.setData({'is_dir': True, 'path': _rp, 'name': _nm}, Qt.UserRole)
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
        w = _CmdWorker(self._mgr.列出目录, self._current_serial, path)
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
        w = _CmdWorker(self._mgr.列出目录, self._current_serial, path)
        self._track(w, on_result=lambda e: self._populate(item, e),
                   on_error=lambda e: self._on_list_err(item, path, e),
                   on_finished=lambda: self._loading.discard(path))

    def _refresh_current(self):
        if self._deep_search_mode:
            self._on_deep_search()  # 深度搜索模式下刷新=重新搜索
            return
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
        act_ch = menu.addAction('授权 777')
        act_del = menu.addAction('删除…')
        menu.addSeparator()
        act_rf = menu.addAction('刷新')
        if entry is None:
            act_dl.setEnabled(False)
            act_ch.setEnabled(False)
            act_rn.setEnabled(False)
            act_del.setEnabled(False)
        elif entry.get('path') == self._root_path:
            # 根目录不允许删除和重命名
            act_rn.setEnabled(False)
            act_del.setEnabled(False)
        act_up.triggered.connect(lambda: self._upload())
        act_dl.triggered.connect(lambda: self._download())
        act_rn.triggered.connect(lambda: self._rename())
        act_ch.triggered.connect(lambda: self._chmod())
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
        target_dir = self._target_dir()
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        local, _ = QFileDialog.getOpenFileName(self, '选择要上传的文件', desktop, '所有文件 (*.*)')
        if not local:
            return
        # 目标是目录，拼接文件名（与 adb push local /dir/ 行为一致）
        target = target_dir.rstrip('/') + '/' + os.path.basename(local)
        file_name = os.path.basename(local)
        # 检查远程是否已有同名文件
        try:
            检查结果 = self._mgr.执行shell(
                self._current_serial, f'ls -la "{target}" 2>&1', timeout=5)
            检查文本 = (检查结果 or '').strip()
            文件已存在 = bool(检查文本) and 'No such file' not in 检查文本 and 'cannot access' not in 检查文本
        except Exception:
            文件已存在 = False
        if 文件已存在:
            # 弹窗询问是否覆盖
            回复 = QMessageBox.question(
                self, '文件已存在',
                f'设备上已存在同名文件:\n{file_name}\n\n是否覆盖？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if 回复 != QMessageBox.StandardButton.Yes:
                self._log(f'[上传] 用户取消覆盖: {file_name}')
                self._status(f'已取消上传: {file_name}')
                return
            self._log(f'[上传] 用户确认覆盖: {file_name}')
        try:
            size = os.path.getsize(local)
        except OSError as e:
            self._log(f'[上传] 本地文件不可读: {local} ({e})')
            self._status(f'上传失败: 本地文件不可读')
            return
        self._log(f'[上传] 设备={self._current_serial} 本地={local} '
                  f'({size}B) 目标={target}')
        # 显示进度条
        if self.progress_bar:
            self.progress_bar.setValue(0)
            self.progress_bar.setMaximum(size if size > 0 else 0)
            self.progress_bar.show()
        self._status(f'上传: {file_name} (0/{self._fmt_size(size)})')
        w = _ProgressCmdWorker(self._mgr.推送文件, self._current_serial, local, target)
        w.signals.progress.connect(
            lambda sent, total, elapsed: self._on_upload_progress(sent, total, elapsed, file_name))
        self._track(w,
                    on_result=lambda r: self._on_upload_success(target_dir, file_name),
                    on_error=lambda e: self._on_upload_error_ext(e, target, target_dir, local, size))

    def _on_upload_progress(self, sent, total, elapsed, file_name):
        """上传进度更新。"""
        if self.progress_bar and total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(sent)
            self.progress_bar.setFormat(
                f'{self._fmt_size(sent)} / {self._fmt_size(total)} (%p%)')
        speed = ''
        if elapsed > 0:
            speed = self._fmt_size(sent / elapsed) + '/s'
        self._status(f'上传: {file_name} ({self._fmt_size(sent)}/{self._fmt_size(total)} {speed})')

    def _on_upload_success(self, target_dir, file_name):
        """上传成功处理：弹窗提示 + 刷新目录。"""
        if self.progress_bar:
            self.progress_bar.hide()
        self._status('上传成功')
        self._log(f'[上传] 完成: {file_name}')
        # 先刷新目录
        self._refresh_dir(target_dir)
        # 弹窗提示（自动关闭）
        self._auto_close_msg('上传成功', f'已成功上传: {file_name}')

    def _on_upload_error_ext(self, error, target, target_dir, local, size):
        """上传失败时的详细错误处理，输出诊断信息。"""
        if self.progress_bar:
            self.progress_bar.hide()
        self._on_upload_error(error, target, target_dir, local, size)

    def _on_upload_error(self, error, target, target_dir, local, size):
        """上传失败时的详细错误处理，输出诊断信息。"""
        error_msg = str(error)
        self._status(f'上传失败: {error_msg.split("|")[0].strip()}')
        self._log(f'[上传] ✗ 失败: {target}')
        self._log(f'[上传] 错误详情: {error_msg}')
        # 诊断信息
        self._log('[上传] --- 诊断信息 ---')
        self._log(f'[上传] 本地文件: {local} ({size}B)')
        self._log(f'[上传] 远程目标: {target}')
        # 诊断1: 检查远程目录权限
        try:
            目录状态 = self._mgr.执行shell(
                self._current_serial, f'ls -ld "{target_dir}" 2>&1', timeout=5)
            self._log(f'[上传] 诊断: 目标目录状态: {(目录状态 or "").strip()}')
            if 'No such file' in (目录状态 or ''):
                self._log('[上传] ✗ 诊断: 目标目录不存在')
                error_msg += ' | 原因: 目标目录不存在'
            elif 'Permission denied' in (目录状态 or ''):
                self._log('[上传] ✗ 诊断: 目标目录权限不足')
                error_msg += ' | 原因: 目标目录权限不足'
        except Exception as de:
            self._log(f'[上传] 诊断: 目录检查异常: {de}')
        # 诊断2: 检查只读分区
        if '只读' in error_msg or 'read-only' in error_msg.lower():
            self._log('[上传] ✗ 诊断: 可能是只读分区，需要 adb root && adb remount')
            error_msg += ' | 建议: 执行 adb root && adb remount'
        # 诊断3: 检查字节数不一致
        if '字节数不一致' in error_msg or '0B' in error_msg:
            self._log('[上传] ✗ 诊断: 可能是设备权限不足或分区空间已满')
            error_msg += ' | 建议: 确认设备权限和分区空间'
        # 诊断4: 检查 /system 分区空间（如果目标在 /system 下）
        if target_dir.startswith('/system'):
            try:
                空间 = self._mgr.执行shell(
                    self._current_serial, 'df -k /system 2>&1', timeout=5)
                self._log(f'[上传] 诊断: /system 空间: {(空间 or "").strip()}')
            except Exception:
                pass
        self._log('[上传] --- 诊断结束 ---')
        # 更新状态栏显示详细错误
        self._status(f'上传失败: {error_msg.split("|")[0].strip()}')
        # 弹窗提示失败（自动关闭）
        file_name = os.path.basename(local)
        self._auto_close_msg('上传失败', f'上传 "{file_name}" 失败:\n{error_msg}',
                            icon=QMessageBox.Icon.Warning)

    def _download(self):
        entry = self._selected_path()
        if not entry:
            return
        _name = self._entry_name(entry)
        if entry.get('is_dir'):
            local_dir = QFileDialog.getExistingDirectory(self, '选择保存目录', os.path.expanduser('~'))
            if not local_dir:
                return
            target = local_dir
        else:
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            default_file = os.path.join(desktop, _name)
            # DontUseNativeDialog 避免 Windows 原生对话框对无后缀文件名（如 sdcard）
            # 误报"文件名无效"的 bug。
            target, _ = QFileDialog.getSaveFileName(
                self, '选择保存位置', default_file, '所有文件 (*.*)',
                options=QFileDialog.Option.DontUseNativeDialog)
            if not target:
                return
        self._status(f'下载: {_name}…')
        w = _CmdWorker(self._mgr.拉取文件, self._current_serial, entry['path'], target)
        self._track(w,
                    on_result=lambda r: self._on_download_success(_name, target),
                    on_error=lambda e: self._on_download_error(e, _name))

    def _on_download_success(self, name, target_path):
        """下载成功处理：弹窗提示。"""
        self._status('下载成功')
        self._log(f'[下载] 完成: {name} -> {target_path}')
        self._auto_close_msg('下载成功', f'已成功下载: {name}\n保存到: {target_path}')

    def _on_download_error(self, error, name):
        """下载失败处理。"""
        error_msg = str(error)
        self._status(f'下载失败: {error_msg}')
        self._log(f'[下载] ✗ 失败: {name} - {error_msg}')
        self._auto_close_msg('下载失败', f'下载 "{name}" 失败:\n{error_msg}',
                            icon=QMessageBox.Icon.Warning)

    def _rename(self):
        entry = self._selected_path()
        if not entry:
            return
        _name = self._entry_name(entry)
        new, ok = QInputDialog.getText(self, '重命名', '输入新名称：', text=_name)
        if not (ok and new and new != _name):
            return
        parent = self._dirname(entry['path'])
        new_path = parent.rstrip('/') + '/' + new
        self._status(f'重命名: {_name} → {new}…')
        w = _CmdWorker(self._mgr.重命名路径, self._current_serial, entry['path'], new_path)
        self._track(w,
                    on_result=lambda r: (self._status('重命名成功'), self._refresh_dir(parent)),
                    on_error=lambda e: self._status(f'重命名失败: {e}'))

    def _chmod(self):
        entry = self._selected_path()
        if not entry:
            return
        path = entry['path']
        _name = self._entry_name(entry)
        self._status(f'授权 777: {_name}…')
        w = _CmdWorker(self._mgr.修改权限, self._current_serial, path, '777')
        self._track(w,
                    on_result=lambda r: self._on_chmod_success(_name, path),
                    on_error=lambda e: self._on_chmod_error(e, _name))

    def _on_chmod_success(self, name, path):
        """授权成功处理：弹窗提示 + 刷新目录。"""
        self._status('授权成功')
        self._log(f'[授权] 完成: {name}')
        self._refresh_dir(self._dirname(path))
        self._auto_close_msg('授权成功', f'已成功授权 777: {name}')

    def _on_chmod_error(self, error, name):
        """授权失败处理。"""
        error_msg = str(error)
        self._status(f'授权失败: {error_msg}')
        self._log(f'[授权] ✗ 失败: {name} - {error_msg}')
        self._auto_close_msg('授权失败', f'授权 "{name}" 失败:\n{error_msg}',
                            icon=QMessageBox.Icon.Warning)

    def _delete(self):
        entry = self._selected_path()
        if not entry:
            return
        _name = self._entry_name(entry)
        reply = QMessageBox.question(self, '确认删除', f'确定删除 "{_name}" 吗？', QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        parent = self._dirname(entry['path'])
        self._status(f'删除: {_name}…')
        w = _CmdWorker(self._mgr.删除路径, self._current_serial, entry['path'])
        self._track(w,
                    on_result=lambda r: self._on_delete_success(_name, parent),
                    on_error=lambda e: self._on_delete_error(e, _name))

    def _on_delete_success(self, name, parent_dir):
        """删除成功处理：弹窗提示 + 刷新目录。"""
        self._status('删除成功')
        self._log(f'[删除] 完成: {name}')
        # 先刷新目录
        self._refresh_dir(parent_dir)
        # 弹窗提示（自动关闭）
        self._auto_close_msg('删除成功', f'已成功删除: {name}')

    def _on_delete_error(self, error, name):
        """删除失败处理。"""
        error_msg = str(error)
        self._status(f'删除失败: {error_msg}')
        self._log(f'[删除] ✗ 失败: {name} - {error_msg}')
        self._auto_close_msg('删除失败', f'删除 "{name}" 失败:\n{error_msg}',
                            icon=QMessageBox.Icon.Warning)

    # ------------------------------------------------------------------
    # 搜索 & 预览
    # ------------------------------------------------------------------
    def _ensure_search_edit(self):
        if self.search_edit is None:
            self.search_edit = QLineEdit()
            self.search_edit.setPlaceholderText('输入实时过滤当前目录；按回车深度递归搜索…')
            self.search_edit.setClearButtonEnabled(True)
        if not self._search_wired:
            self._search_wired = True
            self.search_edit.textChanged.connect(self._on_search_text_changed)
            self.search_edit.returnPressed.connect(self._on_deep_search)
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
        w = _CmdWorker(self._mgr.读取文本文件, serial, entry['path'])
        self._track(w,
                    on_result=lambda r: dlg.set_content(r['text'], r.get('truncated', False)),
                    on_error=lambda e: dlg.set_error(e))

    def _on_search_text_changed(self, text):
        self._search_text = (text or '').strip().lower()
        if not self._search_text and self._deep_search_mode:
            self._exit_deep_search()
            return
        if not self._deep_search_mode:
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
    # 深度递归搜索（回车触发 find）
    # ------------------------------------------------------------------
    def _on_deep_search(self):
        """搜索框按回车：在设备上用 find 递归搜索当前根目录下所有匹配文件。"""
        text = (self.search_edit.text() or '').strip()
        if not text:
            return
        if not self._current_serial:
            self._status('请先选择设备')
            return
        # 过滤单引号防止 shell 注入
        safe_kw = text.replace("'", "'\\''")
        search_path = self._root_path
        cmd = f"find {search_path} -iname '*{safe_kw}*' 2>/dev/null"
        self._status(f'深度搜索 "{text}" …')
        w = _CmdWorker(self._mgr.执行shell, self._current_serial, cmd)
        self._track(w, on_result=lambda out: self._show_deep_results(out, text),
                   on_error=lambda e: self._status(f'搜索失败: {e}'))

    def _show_deep_results(self, output, keyword):
        """解析 find 输出，把匹配路径作为平铺列表显示在 tree 中。"""
        paths = [p.strip() for p in (output or '').splitlines() if p.strip()]
        # 过滤 find 错误行（如 "find: /proc/xxx: Permission denied"）
        paths = [p for p in paths if not p.startswith('find:')]
        self.model.removeRows(0, self.model.rowCount())
        self._dir_items.clear()
        self._deep_search_mode = True
        for p in paths:
            name = p.rstrip('/').rsplit('/', 1)[-1] or p
            entry = {
                'is_dir': False,
                'path': p,
                'name': name,
                'size': '—',
                'perm': '—',
                'mtime': p,
            }
            ni = QStandardItem(name)
            ni.setData(entry, Qt.UserRole)
            ni.setData(True, LOADED_ROLE)  # 防止展开
            self.model.appendRow([ni, QStandardItem('—'), QStandardItem('—'), QStandardItem(p)])
        self._status(f'搜索 "{keyword}" 完成: {len(paths)} 个结果（清空搜索框恢复目录浏览）')

    def _exit_deep_search(self):
        """退出深度搜索模式，恢复正常目录浏览。"""
        self._deep_search_mode = False
        self.model.removeRows(0, self.model.rowCount())
        self._dir_items.clear()
        self._build_root()

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

    @staticmethod
    def _entry_name(entry):
        # 防御：缺省 'name' 时从 path 推导，避免 KeyError
        n = entry.get('name')
        if n:
            return n
        p = (entry.get('path') or '').rstrip('/')
        return p.rsplit('/', 1)[-1] or '/'

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
        # 设置自动关闭
        QTimer.singleShot(timeout_ms, msg_box.accept)
        msg_box.exec()

    def _status(self, msg):
        self.status_label.setText(msg)
