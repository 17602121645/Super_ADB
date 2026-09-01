# -*- coding: utf-8 -*-
"""
文件哈希校验工具弹窗
==================
点击主界面「便捷工具 → MD5」按钮弹出的独立窗口：
- 算法可勾选：MD5 / SHA1 / SHA256 / SHA512 / SHA3-256 / CRC32（工厂 + 注册表，可扩展）
- 拖入文件 / 文件夹自动计算，文件夹弹窗选「递归 / 非递归 / 通配符」展开
- 每个文件独立显示进度条 + 各算法哈希值 + 复制按钮
- 大文件后台并发计算：QSemaphore 控制并发数（默认 4，可调）
- 「复制全部」→ 多行文本（含文件名/大小/各哈希）一次贴到 bug 报告
- 「导出 CSV / JSON」→ 批量算完一键导出索引表
- 「性能基准」→ 选个文件分别用 5 种算法算，显示吞吐量 MB/s
- QSettings 持久化上次算法勾选 + 并发数

说明：算法扩展只需往 ALGORITHMS 注册表加一项（如 xxHash pip install xxhash 后加工厂），UI 自动出现。
说明：第 9 项「文件管理器右键集成」是操作系统级 shell 集成（写注册表 + 独立可执行入口 + 管理员权限），
      无法在弹窗内实现；模块提供 `compute_hashes_batch()` 公共入口，可挂到外部 shell 脚本调用。
"""

import csv
import glob
import hashlib
from tools.json_io import save_json
import os
import sys
import time
import zlib

if sys.platform == 'win32':
    import winreg  # 注册表「计算哈希」右键菜单，仅 Windows 支持

from PySide6.QtCore import Qt, QThread, Signal, QSemaphore, QSettings, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget, QScrollArea, QCheckBox, QProgressBar,
    QMessageBox, QFileDialog, QGroupBox, QTableWidget, QTableWidgetItem,
    QRadioButton, QLineEdit, QSpinBox,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import THEMES, FONT_FAMILY, get_current_theme_id
from ui.dialog_styles import 拖拽区域, add_green_glow, highlight_card_style, _create_popup_card

from ui.dialog_base import 对话框基类


# ─────────────────── 算法注册表（可扩展 #11） ───────────────────


class 哈希算法:
    """一种哈希算法描述。工厂模式：新增算法只需在 ALGORITHMS 注册，UI 自动出现。"""

    def __init__(self, key, label, factory=None, is_crc=False):
        self.key = key
        self.label = label
        self.factory = factory        # callable() -> hash object with update()
        self.is_crc = is_crc          # True 表示用 zlib.crc32 增量计算

    def new_hasher(self):
        return self.factory() if self.factory else None

    def finalize(self, hasher):
        return hasher.hexdigest()


class _Pem主题哈希器:
    """PEM 证书旧式 subject hash 的 Python 等价写法：文件内容 MD5 取前 8 位。"""

    def __init__(self):
        self._md5 = hashlib.md5()

    def update(self, data):
        self._md5.update(data)

    def hexdigest(self):
        return self._md5.hexdigest()[:8]


# 注册表：显示顺序由 ALGO_ORDER 决定；新增算法加一项即可（如 XXH64）
ALGORITHMS = {
    'MD5':              哈希算法('MD5', 'MD5', hashlib.md5),
    'SHA1':             哈希算法('SHA1', 'SHA1', hashlib.sha1),
    'SHA256':           哈希算法('SHA256', 'SHA256', hashlib.sha256),
    'SHA512':           哈希算法('SHA512', 'SHA512', hashlib.sha512),
    'SHA3-256':         哈希算法('SHA3-256', 'SHA3-256', lambda: hashlib.sha3_256()),
    'CRC32':            哈希算法('CRC32', 'CRC32', is_crc=True),
    'PEM_SUBJECT_HASH': 哈希算法('PEM_SUBJECT_HASH', 'PEM subject-hash', _Pem主题哈希器),
}
ALGO_ORDER = ['MD5', 'SHA1', 'SHA256', 'SHA512', 'SHA3-256', 'CRC32', 'PEM_SUBJECT_HASH']
# 注：xxHash 需 `pip install xxhash`（本机联网受限），安装后可在此加：
#   'XXH64': 哈希算法('XXH64', 'XXH64', lambda: xxhash.xxh64()),
# 速度比 SHA256 快约 10x，且不需改任何 UI 代码。


# ─────────────────── 哈希计算线程（进度 + 并发） ───────────────────


class 哈希工作线程(QThread):
    """后台线程：逐块读取文件，按勾选算法计算，发进度与结果信号。"""

    progress = Signal(str, int, int)         # filepath, bytes_read, total
    finished = Signal(str, dict)             # filepath, {size, elapsed, key: digest}
    error = Signal(str, str)                 # filepath, error_msg

    def __init__(self, filepath, algo_keys, semaphore=None):
        super().__init__()
        self.filepath = filepath
        self.algo_keys = list(algo_keys)
        self._algos = [ALGORITHMS[k] for k in self.algo_keys]
        self._sem = semaphore

    def run(self):
        if self._sem is not None:
            self._sem.acquire()
        try:
            size = os.path.getsize(self.filepath)
            hashers = {a.key: a.new_hasher() for a in self._algos if not a.is_crc}
            crc = 0
            read = 0
            t0 = time.time()
            with open(self.filepath, 'rb') as f:
                while True:
                    chunk = f.read(2 * 1024 * 1024)  # 2 MB chunks
                    if not chunk:
                        break
                    read += len(chunk)
                    for h in hashers.values():
                        h.update(chunk)
                    if any(a.is_crc for a in self._algos):
                        crc = zlib.crc32(chunk, crc)
                    self.progress.emit(self.filepath, read, size)
            result = {'size': size, 'elapsed': time.time() - t0}
            for a in self._algos:
                if a.is_crc:
                    result[a.key] = format(crc & 0xffffffff, '08x')
                else:
                    result[a.key] = hashers[a.key].hexdigest()
            self.finished.emit(self.filepath, result)
        except Exception as e:
            self.error.emit(self.filepath, str(e))
        finally:
            if self._sem is not None:
                self._sem.release()


# ─────────────────── 单行结果控件（含进度条 + 每算法一行） ───────────────────


class 哈希结果行(QWidget):
    """一行哈希结果：顶部 文件名+大小+进度条；每个算法独立一行（值 + 复制）。"""

    # 调色用的颜色受主题控制；这里集中查表便于切换主题时刷新
    _CLR_OK = '#a7ffeb'    # 计算成功（hash 文字颜色，仅在深色主题用足够亮）
    _CLR_SIZE = '#9e9e9e'  # 大小文字
    _CLR_ERR = '#e57373'   # 失败红

    def __init__(self, filepath, algo_keys, theme_id=None, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.algo_keys = list(algo_keys)
        self._results = {}
        self._worker = None
        self._val_labels = {}
        self._copy_btns = {}
        self._theme_id = theme_id or get_current_theme_id(self)
        self._accent = THEMES[self._theme_id]['accent']
        self._text_primary = THEMES[self._theme_id]['text_primary']
        # 颜色按主题计算：浅色主题下没有"亮色 hash"，用 accent 即可
        if self._theme_id == 'light_soft':
            self._clr_ok = self._accent
            self._clr_size = '#5f6b6a'  # 浅色主题下的次级文字（深灰偏蓝绿）
        else:
            self._clr_ok = self._CLR_OK
            self._clr_size = self._CLR_SIZE
        self._clr_err = self._CLR_ERR
        # 用「_roles」字典保存每个标签的主题角色，刷新时直接遍历，
        # 避免依赖 QLabel.dynamicProperty（PySide6 在某些版本下读 dynamic property 有兼容问题）
        self._roles = {}

        fname = os.path.basename(filepath)
        try:
            fsize = os.path.getsize(filepath)
            size_str = self._fmt_size(fsize)
        except Exception:
            size_str = "?"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # 顶部行：文件名 + 大小 + 进度条
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl_name = QLabel(fname)
        lbl_name.setToolTip(filepath)
        lbl_name.setStyleSheet(f"color: {self._accent}; font-weight: bold;")
        lbl_name.setMinimumWidth(100)
        self._roles[lbl_name] = 'filename'
        top.addWidget(lbl_name)
        lbl_size = QLabel(size_str)
        lbl_size.setStyleSheet(f"color: {self._clr_size}; font-size: 9pt;")
        self._roles[lbl_size] = 'size'
        top.addWidget(lbl_size)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(14)
        self.bar.setTextVisible(False)
        top.addWidget(self.bar, 1)
        layout.addLayout(top)

        # 每个算法一行；按当前字体中最长标签计算固定宽度，避免 HiDPI 下被截断。
        _tag_font = QFont(FONT_FAMILY, 9, QFont.Weight.Bold)
        _tag_fm = QFontMetrics(_tag_font)
        _max_tag_width = max(
            (_tag_fm.horizontalAdvance(ALGORITHMS[k].label) for k in ALGO_ORDER), default=72
        ) + 12  # 左右各留 6px 呼吸空间

        for key in self.algo_keys:
            h = QHBoxLayout()
            h.setSpacing(6)
            tag = QLabel(ALGORITHMS[key].label)
            tag.setStyleSheet(f"color: {self._accent}; font-size: 9pt; font-weight: bold;")
            # 固定宽度按字体内容计算，保证 SHA3-256 / PEM subject-hash 完整显示。
            tag.setFixedWidth(max(_max_tag_width, 72))
            self._roles[tag] = 'algo_tag'
            h.addWidget(tag)

            val = QLabel("计算中...")
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setFont(QFont(FONT_FAMILY, 10))
            val.setStyleSheet(f"color: {self._clr_size}; background: transparent;")
            val.setWordWrap(True)
            self._roles[val] = 'hash_pending'
            h.addWidget(val, 1)
            self._val_labels[key] = val

            btn = QPushButton("复制")
            btn.setFixedWidth(60)
            btn.setFixedHeight(26)
            btn.setFont(QFont(FONT_FAMILY, 9))
            btn.setEnabled(False)
            btn.clicked.connect(lambda checked, v=val: self._copy_hash(v))
            h.addWidget(btn)
            self._copy_btns[key] = btn
            layout.addLayout(h)

        self._start()

    @staticmethod
    def _fmt_size(b):
        for unit in ("B", "KB", "MB", "GB"):
            if abs(b) < 1024:
                return f"{b:.1f} {unit}"
            b /= 1024
        return f"{b:.1f} TB"

    def _start(self):
        dlg = self.window()
        sem = getattr(dlg, '_sem', None) if dlg is not None else None
        self._worker = 哈希工作线程(self.filepath, self.algo_keys, sem)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._结果返回时)
        self._worker.error.connect(self._出错时)
        self._worker.start()

    def _on_progress(self, filepath, read, total):
        if filepath != self.filepath:
            return
        self.bar.setValue(int(read / total * 100) if total else 100)

    def _结果返回时(self, filepath, result):
        if filepath != self.filepath:
            return
        self._results = result
        self.bar.setValue(100)
        for key, val in result.items():
            if key in ('size', 'elapsed'):
                continue
            lbl = self._val_labels.get(key)
            if lbl:
                lbl.setText(val)
                lbl.setStyleSheet(f"color: {self._clr_ok}; background: transparent;")
                self._roles[lbl] = 'hash_ok'
            btn = self._copy_btns.get(key)
            if btn:
                btn.setEnabled(True)

    def _出错时(self, filepath, err):
        if filepath != self.filepath:
            return
        self.bar.setValue(0)
        for key in self.algo_keys:
            lbl = self._val_labels.get(key)
            if lbl:
                lbl.setText("失败")
                lbl.setStyleSheet(f"color: {self._clr_err};")
                self._roles[lbl] = 'hash_err'
            btn = self._copy_btns.get(key)
            if btn:
                btn.setEnabled(False)
        lbl_name = self.findChild(QLabel)
        if lbl_name:
            lbl_name.setToolTip(f"{self.filepath}\n错误: {err}")

    def _copy_hash(self, val_label):
        text = val_label.text()
        if text and text not in ("计算中...", "失败"):
            QApplication.clipboard().setText(text)
            btn = self.sender()
            if btn:
                old = btn.text()
                btn.setText("已复制")
                btn.setEnabled(False)
                QApplication.processEvents()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(800, lambda: (btn.setText(old), btn.setEnabled(True)))

    def _refresh_theme(self):
        """主题切换时由 哈希校验对话框 触发：按每个标签的角色重新染色。

        role ∈ {'filename', 'size', 'algo_tag', 'hash_pending', 'hash_ok', 'hash_err'}
        """
        if self._theme_id == 'light_soft':
            self._clr_ok = self._accent
            self._clr_size = '#5f6b6a'
        else:
            self._clr_ok = self._CLR_OK
            self._clr_size = self._CLR_SIZE
        for lbl, role in self._roles.items():
            if role == 'filename':
                lbl.setStyleSheet(f"color: {self._accent}; font-weight: bold;")
            elif role == 'size':
                lbl.setStyleSheet(f"color: {self._clr_size}; font-size: 9pt;")
            elif role == 'algo_tag':
                lbl.setStyleSheet(
                    f"color: {self._accent}; font-size: 9pt; font-weight: bold;")
            elif role == 'hash_pending':
                lbl.setStyleSheet(
                    f"color: {self._clr_size}; background: transparent;")
            elif role == 'hash_ok':
                lbl.setStyleSheet(
                    f"color: {self._clr_ok}; background: transparent;")
            elif role == 'hash_err':
                lbl.setStyleSheet(f"color: {self._clr_err};")

    def get_result_text(self):
        """复制全部 / 导出用的单行文本：文件名 + 大小 + 各算法哈希。"""
        lines = [os.path.basename(self.filepath)]
        size = self._results.get('size')
        lines.append(f"大小: {self._fmt_size(size) if size else '?'}")
        for key in self.algo_keys:
            v = self._results.get(key)
            if v:
                lines.append(f"{key}: {v}")
        return "\n".join(lines)


# ─────────────────── 目录展开弹窗（递归 / 非递归 / 通配符） ───────────────────


class 目录拖拽对话框(QDialog):
    def __init__(self, dirpath, parent=None):
        super().__init__(parent)
        self.setWindowTitle("目录展开方式")
        self.setModal(True)
        self.mode = 'recursive'
        self._dir = dirpath

        v = QVBoxLayout(self)
        info = QLabel(f"检测到文件夹：\n{dirpath}\n请选择展开方式：")
        info.setWordWrap(True)
        v.addWidget(info)

        self.rb_rec = QRadioButton("递归（含子目录所有文件）")
        self.rb_non = QRadioButton("仅当前目录（不含子目录）")
        self.rb_glob = QRadioButton("按通配符匹配")
        self.rb_rec.setChecked(True)
        self.rb_rec.toggled.connect(lambda on: on and setattr(self, 'mode', 'recursive'))
        self.rb_non.toggled.connect(lambda on: on and setattr(self, 'mode', 'nonrecursive'))
        self.rb_glob.toggled.connect(lambda on: on and setattr(self, 'mode', 'glob'))
        v.addWidget(self.rb_rec)
        v.addWidget(self.rb_non)
        v.addWidget(self.rb_glob)

        self.pat_edit = QLineEdit("*")
        self.pat_edit.setPlaceholderText("例如 *.apk  *.zip")
        self.pat_edit.setEnabled(False)
        self.rb_glob.toggled.connect(lambda on: self.pat_edit.setEnabled(on))
        v.addWidget(QLabel("通配符："))
        v.addWidget(self.pat_edit)

        btns = QHBoxLayout()
        ok = QPushButton("确定")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(ok)
        btns.addWidget(cancel)
        v.addLayout(btns)

    @property
    def pattern(self):
        return self.pat_edit.text().strip() or '*'

    def expand(self):
        if self.mode == 'recursive':
            matches = glob.glob(os.path.join(self._dir, '**', '*'), recursive=True)
        elif self.mode == 'nonrecursive':
            matches = [os.path.join(self._dir, n) for n in os.listdir(self._dir)]
        else:
            matches = glob.glob(os.path.join(self._dir, self.pattern), recursive=True)
        return [m for m in matches if os.path.isfile(m)]


# ─────────────────── 性能基准弹窗（#12） ───────────────────


class 基准测试对话框(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("哈希算法性能基准")
        self.setMinimumWidth(540)
        self._filepath = None
        self._theme_id = get_current_theme_id(self)

        v = QVBoxLayout(self)
        h = QHBoxLayout()
        self.btn_pick = QPushButton("选择文件...")
        self.btn_pick.clicked.connect(self._pick)
        h.addWidget(self.btn_pick)
        self.lbl_file = QLabel("未选择文件")
        self.lbl_file.setStyleSheet(f"color: {self._file_color()};")
        h.addWidget(self.lbl_file, 1)
        v.addLayout(h)

        self.btn_run = QPushButton("运行基准测试")
        self.btn_run.clicked.connect(self._run)
        v.addWidget(self.btn_run)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ['算法', '耗时 (s)', '吞吐量 (MB/s)', '结果 (前 16 位)'])
        v.addWidget(self.table, 1)

    def _file_color(self):
        return '#5f6b6a' if self._theme_id == 'light_soft' else '#9e9e9e'

    def apply_theme(self, theme_id):
        if theme_id not in THEMES:
            return
        self._theme_id = theme_id
        self.lbl_file.setStyleSheet(f"color: {self._file_color()};")

    def _pick(self):
        p, _ = QFileDialog.getOpenFileName(self, "选择测试文件", "", "所有文件 (*)")
        if p:
            self._filepath = p
            self.lbl_file.setText(os.path.basename(p))

    def _run(self):
        if not self._filepath:
            QMessageBox.warning(self, "未选择", "请先选择一个文件。")
            return
        size = os.path.getsize(self._filepath)
        self.table.setRowCount(len(ALGO_ORDER))
        for i, key in enumerate(ALGO_ORDER):
            a = ALGORITHMS[key]
            t0 = time.time()
            if a.is_crc:
                crc = 0
                with open(self._filepath, 'rb') as f:
                    while True:
                        c = f.read(4 * 1024 * 1024)
                        if not c:
                            break
                        crc = zlib.crc32(c, crc)
                digest = format(crc & 0xffffffff, '08x')
            else:
                h = a.new_hasher()
                with open(self._filepath, 'rb') as f:
                    while True:
                        c = f.read(4 * 1024 * 1024)
                        if not c:
                            break
                        h.update(c)
                digest = h.hexdigest()
            elapsed = time.time() - t0
            mbps = (size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
            self.table.setItem(i, 0, QTableWidgetItem(a.label))
            self.table.setItem(i, 1, QTableWidgetItem(f"{elapsed:.3f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{mbps:.1f}"))
            self.table.setItem(i, 3, QTableWidgetItem(digest[:16]))
        self.table.resizeColumnsToContents()


# ─────────────────── 主弹窗 ───────────────────


class 哈希校验对话框(对话框基类):
    """文件哈希校验弹窗 —— 拖入 / 选择文件或文件夹，多算法并发计算。"""

    def __init__(self, parent=None):
        super().__init__(parent, 标题="文件哈希校验", 最小尺寸=(860, 520), 发光=False)
        self._theme_id = self._主题id  # 兼容旧代码引用
        self._accent = THEMES[self._主题id]['accent']

        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, self._theme_id)

        # ── 持久化（#10）──
        self._settings = QSettings('Super_ADB', 'Md5Tool')
        self._concurrency = int(self._settings.value('concurrency', 4))
        # PEM_SUBJECT_HASH 永远默认勾选（用户偏好：「PEM 默认勾选」）。
        # 即使 saved 里被别人清空，加载时仍强制回写一次，避免被空白结果列表误导。
        saved = self._settings.value('algos', 'MD5,SHA1,SHA256,PEM_SUBJECT_HASH')
        if isinstance(saved, str):
            saved = [a for a in saved.split(',') if a in ALGORITHMS]
        saved = saved or []
        if 'PEM_SUBJECT_HASH' not in saved:
            saved.append('PEM_SUBJECT_HASH')
        self._enabled_algos = saved
        # 写回 settings，确保下次启动读到的是已经「包含 PEM」的列表
        self._settings.setValue('algos', ','.join(self._enabled_algos))
        self._sem = QSemaphore(self._concurrency)

        # ── 拖拽区（共用 dialog_styles.拖拽区域，主题颜色随 apply_theme 自动刷新） ──
        self.drop_area = 拖拽区域(
            self,
            text='拖拽文件 / 文件夹到此处\n（或点击选择文件）',
            file_filter='所有文件 (*.*)',
            file_mode='multi',
            theme_id=self._theme_id,
        )
        self.drop_area.paths_dropped.connect(self._on_paths_dropped)

        root = QVBoxLayout(self.card)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # 算法勾选栏
        algo_box = QGroupBox("校验算法")
        algo_layout = QHBoxLayout(algo_box)
        algo_layout.setSpacing(10)
        self._chk_algos = {}
        for key in ALGO_ORDER:
            chk = QCheckBox(ALGORITHMS[key].label)
            chk.setChecked(key in self._enabled_algos)
            chk.stateChanged.connect(self._on_algo_toggled)
            self._chk_algos[key] = chk
            algo_layout.addWidget(chk)
        btn_all = QPushButton("全选")
        btn_all.setFixedWidth(64)
        btn_all.clicked.connect(self._select_all_algos)
        algo_layout.addWidget(btn_all)
        algo_layout.addStretch()
        algo_layout.addWidget(QLabel("并发"))
        self.spin_conc = QSpinBox()
        self.spin_conc.setRange(1, 8)
        self.spin_conc.setValue(self._concurrency)
        self.spin_conc.valueChanged.connect(self._on_concurrency_changed)
        algo_layout.addWidget(self.spin_conc)
        root.addWidget(algo_box)

        root.addWidget(self.drop_area)

        # 选择按钮（与 拖拽区域 点击 = 等价触发，但保留可点击的备用入口）
        btn_bar = QHBoxLayout()
        self.btn_select = QPushButton("选择文件...")
        self.btn_select.setFixedHeight(32)
        self.btn_select.clicked.connect(self._browse_file)
        btn_bar.addWidget(self.btn_select)
        self.btn_dir = QPushButton("选择文件夹...")
        self.btn_dir.setFixedHeight(32)
        self.btn_dir.clicked.connect(self._browse_dir)
        btn_bar.addWidget(self.btn_dir)
        btn_bar.addStretch()
        self.lbl_count = QLabel("")
        self.lbl_count.setStyleSheet(
            f"color: {THEMES[self._theme_id]['text_disabled']};")
        btn_bar.addWidget(self.lbl_count)
        root.addLayout(btn_bar)

        # 结果区域（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ border: 1px solid {self._accent}; border-radius: 6px;"
            f" background: {THEMES[self._theme_id]['bg_input']}; }}")
        self.result_container = QWidget()
        self.result_layout = QVBoxLayout(self.result_container)
        self.result_layout.setContentsMargins(8, 8, 8, 8)
        self.result_layout.setSpacing(8)
        self.result_layout.addStretch()
        scroll.setWidget(self.result_container)
        root.addWidget(scroll, 1)

        # 底部操作栏
        bottom = QHBoxLayout()
        self.btn_copy_all = QPushButton("复制全部")
        self.btn_copy_all.setFixedWidth(100)
        self.btn_copy_all.clicked.connect(self._copy_all)
        self.btn_export = QPushButton("导出 CSV/JSON")
        self.btn_export.setFixedWidth(130)
        self.btn_export.clicked.connect(self._export)
        self.btn_bench = QPushButton("性能基准")
        self.btn_bench.setFixedWidth(100)
        self.btn_bench.clicked.connect(self._open_benchmark)
        bottom.addWidget(self.btn_copy_all)
        bottom.addWidget(self.btn_export)
        bottom.addWidget(self.btn_bench)
        bottom.addStretch()
        self.btn_ctx = QPushButton("右键菜单")
        self.btn_ctx.setFixedWidth(100)
        self.btn_ctx.clicked.connect(self._toggle_context_menu)
        bottom.addWidget(self.btn_ctx)
        if sys.platform != 'win32':
            # 注册表右键菜单仅 Windows 支持：非 Windows 隐藏按钮，避免点击报 winreg 错误
            self.btn_ctx.setVisible(False)
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.setFixedWidth(100)
        self.btn_clear.clicked.connect(self._clear_all)
        bottom.addWidget(self.btn_clear)
        root.addLayout(bottom)

        self.setAcceptDrops(True)

    def apply_theme(self, theme_id):
        """主窗口切换主题时调用：把弹窗与所有已添加的结果行一起刷新。

        具体动作：
        - 刷新本对话框 QSS（背景 / 控件 / 滚动区配色）
        - 通知 拖拽区域 重画虚线框颜色
        - 已添加的 哈希结果行 标签 / hash 值 / 错误态颜色全部更新
        """
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        super().apply_theme(theme_id)
        self._theme_id = theme_id
        self._accent = THEMES[theme_id]['accent']
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(self._accent))
        self.drop_area.apply_theme(theme_id)
        # 滚动区背景色：跟随主题的输入框底色，避免深色 / 浅色反差突兀
        scroll = self.findChild(QScrollArea)
        if scroll is not None:
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {self._accent}; border-radius: 6px;"
                f" background: {THEMES[theme_id]['bg_input']}; }}")
        # 已添加的结果行：把每个标签 / 按钮颜色按主题刷新
        for row in self.findChildren(哈希结果行):
            row._theme_id = theme_id
            row._accent = self._accent
            row._refresh_theme()

    # ── 算法勾选 ──

    def _on_algo_toggled(self, _state):
        self._enabled_algos = [k for k, c in self._chk_algos.items() if c.isChecked()]
        self._settings.setValue('algos', ','.join(self._enabled_algos))
        if self.findChildren(哈希结果行):
            QMessageBox.information(
                self, "提示",
                "算法变更后新文件将按新配置计算；已列出的结果需清空后重新添加。")

    def _select_all_algos(self):
        for c in self._chk_algos.values():
            c.setChecked(True)

    def _on_concurrency_changed(self, val):
        self._concurrency = val
        self._sem = QSemaphore(val)
        self._settings.setValue('concurrency', val)

    def _enabled(self):
        if not self._enabled_algos:
            QMessageBox.warning(self, "请选择算法", "至少勾选一种哈希算法。")
            return None
        return self._enabled_algos

    # ── 拖放 ──

    def _on_paths_dropped(self, paths: list):
        """拖拽区域 投递的本地路径（可能含文件 / 文件夹）。

        文件夹走 ``_expand_dir`` 让用户选递归 / 非递归 / 通配符，再统一进队列；
        文件直接进队列。
        """
        files = []
        for p in paths:
            if not p:
                continue
            if os.path.isdir(p):
                files.extend(self._expand_dir(p))
            elif os.path.isfile(p):
                files.append(p)
        if files:
            self._add_files(files)

    def _expand_dir(self, dirpath):
        dlg = 目录拖拽对话框(dirpath, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return []
        return dlg.expand()

    # ── 文件操作 ──

    def _browse_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "选择校验文件", "", "所有文件 (*)")
        if paths:
            self._add_files(paths)

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self._add_files(self._expand_dir(d))

    def _add_files(self, paths):
        algos = self._enabled()
        if algos is None:
            return
        for p in paths:
            if any(r.filepath == p for r in self.findChildren(哈希结果行)):
                continue
            row = 哈希结果行(p, algos)
            self.result_layout.insertWidget(self.result_layout.count() - 1, row)
        self._update_count()

    def _clear_all(self):
        for row in self.findChildren(哈希结果行):
            row.deleteLater()
        self._update_count()

    def _update_count(self):
        n = len(self.findChildren(哈希结果行))
        self.lbl_count.setText(f"共 {n} 个文件" if n else "")

    # ── 复制全部（#3）──

    def _copy_all(self):
        rows = self.findChildren(哈希结果行)
        rows = [r for r in rows if r._results]
        if not rows:
            QMessageBox.information(self, "无结果", "还没有计算完成的文件。")
            return
        text = "\n\n".join(r.get_result_text() for r in rows)
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "已复制", f"已将 {len(rows)} 个文件的哈希复制到剪贴板。")

    # ── 导出 CSV/JSON（#8）──

    @staticmethod
    def _get_desktop_dir():
        """获取真实桌面路径（处理 OneDrive 等重定向），失败时回退 ~/Desktop。"""
        try:
            import ctypes
            from ctypes import wintypes
            FOLDERID_Desktop = '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}'
            SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
            SHGetKnownFolderPath.argtypes = [
                ctypes.c_wchar_p, wintypes.DWORD, wintypes.HANDLE,
                ctypes.POINTER(ctypes.c_wchar_p)]
            SHGetKnownFolderPath.restype = wintypes.HRESULT
            p_path = ctypes.c_wchar_p()
            if SHGetKnownFolderPath(FOLDERID_Desktop, 0, None, ctypes.byref(p_path)) == 0:
                if p_path.value:
                    return p_path.value
        except Exception:
            pass
        return os.path.join(os.path.expanduser("~"), "Desktop")

    def _export(self):
        rows = [r for r in self.findChildren(哈希结果行) if r._results]
        if not rows:
            QMessageBox.information(self, "无结果", "还没有计算完成的文件。")
            return
        _default_dir = os.path.join(self._get_desktop_dir(), "Super_ADB")
        os.makedirs(_default_dir, exist_ok=True)
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "导出哈希结果",
            os.path.join(_default_dir, "hash_results"),
            "CSV 文件 (*.csv);;JSON 文件 (*.json)")
        if not path:
            return
        # 按选中的过滤器判定格式，并强制补扩展名（避免平台不自动追加导致格式误判）
        if '.json' in selected_filter.lower():
            if not path.lower().endswith('.json'):
                path += '.json'
            fmt = 'json'
        else:
            if not path.lower().endswith('.csv'):
                path += '.csv'
            fmt = 'csv'
        header = ['filename', 'path', 'size_bytes'] + self._enabled_algos
        records = []
        for r in rows:
            rec = {
                'filename': os.path.basename(r.filepath),
                'path': r.filepath,
                'size_bytes': r._results.get('size', 0),
            }
            for k in self._enabled_algos:
                rec[k] = r._results.get(k, '')
            records.append(rec)
        try:
            if fmt == 'json':
                ok = save_json(path, records)
            else:
                with open(path, 'w', encoding='utf-8-sig', newline='') as f:
                    w = csv.DictWriter(f, fieldnames=header)
                    w.writeheader()
                    for rec in records:
                        w.writerow(rec)
                ok = True
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
            return
        if not ok:
            QMessageBox.critical(self, "导出失败", "写入 JSON 失败，详见日志")
            return
        QMessageBox.information(self, "已导出", f"已导出 {len(records)} 条记录到：\n{path}")

    # ── 性能基准（#12）──

    def _open_benchmark(self):
        基准测试对话框(self).exec()

    # ── 文件管理器右键集成（#9）──

    _CTX_KEY = r"Software\Classes\*\shell\SuperADB计算哈希"
    _CTX_NAME = "计算哈希 (Super ADB)"

    def _ctx_menu_installed(self):
        try:
            h = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._CTX_KEY)
            winreg.CloseKey(h)
            return True
        except OSError:
            return False

    def _toggle_context_menu(self):
        if self._ctx_menu_installed():
            if QMessageBox.question(
                    self, "右键菜单",
                    "已安装「计算哈希」右键菜单。\n是否卸载？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) != QMessageBox.StandardButton.Yes:
                return
            self._uninstall_context_menu()
        else:
            self._install_context_menu()

    def _install_context_menu(self):
        try:
            # 冻结版（PyInstaller）中 __file__ 指向 _internal/ 里的 .py，
            # 磁盘上不存在，不能再用 pythonw + script 方式。
            # 统一改为调用主 exe 自身并带 --hash 参数，
            # 由 main() 入口解析后直接弹出 哈希上下文菜单。
            exe = sys.executable
            if not os.path.isfile(exe):
                QMessageBox.critical(self, "安装失败", f"找不到可执行文件：{exe}")
                return
            # 图标：Windows 右键菜单的 Icon 只认 .ico 或 "exe,索引"，不认 .png，
            # 故源码版用 Super_ADB.ico，冻结版直接复用 exe 自带图标（"exe",0）最稳妥。
            icon_path = ''
            if getattr(sys, 'frozen', False):
                # 冻结版：exe 已内嵌图标（PyInstaller -i），用 "exe,0" 引用，无需额外文件
                icon_path = f'"{exe}",0'
            else:
                icon_candidates = []
                _file_dir = os.path.dirname(os.path.abspath(__file__))
                icon_candidates.append(os.path.join(_file_dir, '..', 'ui', 'Super_ADB.ico'))
                icon_candidates.append(os.path.join(_file_dir, '..', 'resources', 'Super_ADB.ico'))
                icon_candidates.append(os.path.join(_file_dir, '..', 'Super_ADB.ico'))
                # 冻结版目录兜底（开发态若与打包目录混用）
                icon_candidates.append(os.path.join(os.path.dirname(exe), 'Super_ADB.ico'))
                for ic in icon_candidates:
                    if os.path.isfile(ic):
                        icon_path = os.path.abspath(ic)
                        break
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, self._CTX_KEY)
            winreg.SetValueEx(key, None, 0, winreg.REG_SZ, self._CTX_NAME)
            if icon_path:
                winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_path)
            cmd = winreg.CreateKey(key, "command")
            winreg.SetValueEx(cmd, None, 0, winreg.REG_SZ,
                              f'"{exe}" --hash "%1"')
            winreg.CloseKey(cmd)
            winreg.CloseKey(key)
            QMessageBox.information(
                self, "已安装",
                "右键菜单已添加。\n在任意文件上右键即可看到「"
                + self._CTX_NAME + "」，点击后弹出哈希结果窗口。")
        except Exception as e:
            QMessageBox.critical(self, "安装失败", str(e))

    def _uninstall_context_menu(self):
        try:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                 self._CTX_KEY + r"\command")
            except OSError:
                pass
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self._CTX_KEY)
            QMessageBox.information(self, "已卸载", "右键菜单已移除。")
        except Exception as e:
            QMessageBox.critical(self, "卸载失败", str(e))


# ─────────────────── 公共入口（供外部 shell 集成调用，#9 的复用点） ───────────────────


def compute_hashes_batch(paths, algo_keys=None):
    """批量计算哈希，返回 [(path, {size, key: digest})]。供 shell / CLI 调用。

    示例：从文件管理器右键调用时，把选中文件路径传进来即可拿到结果字典。
    """
    if algo_keys is None:
        algo_keys = ['MD5', 'SHA1', 'SHA256']
    out = []
    for p in paths:
        try:
            size = os.path.getsize(p)
            hashers = {k: ALGORITHMS[k].new_hasher() for k in algo_keys
                       if not ALGORITHMS[k].is_crc}
            crc = 0
            with open(p, 'rb') as f:
                while True:
                    chunk = f.read(2 * 1024 * 1024)
                    if not chunk:
                        break
                    for h in hashers.values():
                        h.update(chunk)
                    if any(ALGORITHMS[k].is_crc for k in algo_keys):
                        crc = zlib.crc32(chunk, crc)
            result = {'size': size}
            for k in algo_keys:
                if ALGORITHMS[k].is_crc:
                    result[k] = format(crc & 0xffffffff, '08x')
                else:
                    result[k] = hashers[k].hexdigest()
            out.append((p, result))
        except Exception as e:
            out.append((p, {'error': str(e)}))
    return out
