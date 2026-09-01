# -*- coding: utf-8 -*-
"""
修改系统时间对话框
==================
展示设备系统时间，支持一键同步北京时间和手动编辑修改。
主题跟随主窗口，apply_theme 切换时更新全局样式。
"""
import time as _time
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import QDate, QTime, QObject, QRunnable, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QDateEdit, QTimeEdit,
)

from ui.ui_styles import get_stylesheet, THEMES
from ui.dialog_styles import add_green_glow, highlight_card_style, _create_popup_card


class _工作器信号(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class 命令工作器(QRunnable):
    """通用线程池工作器: 在后台执行 func, 通过信号回传结果/错误/完成。"""
    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = _工作器信号()
        self.setAutoDelete(False)

    def run(self):
        try:
            r = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(r)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class 修改时间对话框(QDialog):
    """修改设备系统时间弹窗。"""

    def __init__(self, adb, serial, theme_id, pool=None, 状态回调=None, parent=None):
        super().__init__(parent)
        self.adb = adb
        self.serial = serial
        self._theme_id = theme_id
        self.pool = pool
        self._状态回调 = 状态回调  # 主窗口状态栏回调 (文本, ok=bool)
        self._live_workers = []

        self.setWindowTitle(f'修改系统时间 — 设备: {serial}')
        self.setMinimumSize(420, 220)
        self.setStyleSheet(get_stylesheet(theme_id))

        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, theme_id)

        self._build_ui()
        self._获取设备时间()

    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        self.当前时间标签 = QLabel('设备时间：获取中…')
        lay.addWidget(self.当前时间标签)

        北京 = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        编辑行 = QHBoxLayout()
        self.日期编辑 = QDateEdit(QDate(北京.year, 北京.month, 北京.day))
        self.日期编辑.setDisplayFormat('yyyy-MM-dd')
        self.日期编辑.setCalendarPopup(True)
        self.日期编辑.setMinimumWidth(160)
        self.日期编辑.setStyleSheet("QDateEdit:focus{border:2px solid #4caf50;}")
        _日历 = self.日期编辑.calendarWidget()
        if _日历:
            _日历.setMinimumSize(340, 260)
        self.时间编辑 = QTimeEdit(QTime(北京.hour, 北京.minute, 北京.second))
        self.时间编辑.setDisplayFormat('HH:mm:ss')
        self.时间编辑.setMinimumWidth(120)
        self.时间编辑.setStyleSheet("QTimeEdit:focus{border:2px solid #4caf50;}")
        编辑行.addWidget(self.日期编辑, 1)
        编辑行.addWidget(self.时间编辑, 1)
        lay.addLayout(编辑行)

        按钮行 = QHBoxLayout()
        self.同步按钮 = QPushButton('设备同步北京时间')
        self.修改按钮 = QPushButton('修改为编辑时间')
        按钮行.addWidget(self.同步按钮)
        按钮行.addWidget(self.修改按钮)
        lay.addLayout(按钮行)

        self.状态标签 = QLabel('')
        lay.addWidget(self.状态标签)

        self.同步按钮.clicked.connect(self._同步北京时间)
        self.修改按钮.clicked.connect(self._修改为编辑时间)

    def apply_theme(self, theme_id):
        """主题切换时更新全局样式。"""
        if theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES.get(theme_id, {}).get('accent', '#00B7FF')))
        self.update()

    # ─────────────── 异步操作 ───────────────
    def _丢弃工作器(self, w):
        try:
            self._live_workers.remove(w)
        except ValueError:
            pass

    def _获取设备时间(self):
        def _取时间():
            return self.adb.执行shell(self.serial, 'date 2>/dev/null', timeout=5).strip()

        def _时间回来(raw):
            self.当前时间标签.setText(f'设备当前时间：{raw}')

        w = 命令工作器(_取时间)
        w.signals.result.connect(_时间回来)
        w.signals.error.connect(lambda e: self.当前时间标签.setText(f'设备时间：获取失败（{e}）'))
        w.signals.finished.connect(lambda: self._丢弃工作器(w))
        self._live_workers.append(w)
        self.pool.start(w)

    def _执行修改(self, dt):
        date_str = dt.strftime('%m%d%H%M%Y.%S')
        self.修改按钮.setEnabled(False)
        self.同步按钮.setEnabled(False)
        self.状态标签.setText('正在修改时间…')

        def _执行():
            ok, msg = self.adb.获取root权限(self.serial)
            if not ok:
                raise RuntimeError(f'获取 root 失败：{msg}')
            _time.sleep(1)
            self.adb.执行shell(self.serial, f'date {date_str}', timeout=10)
            return self.adb.执行shell(self.serial, 'date 2>/dev/null', timeout=5).strip()

        def _成功(验证):
            self.状态标签.setText(f'✅ 修改成功！设备当前时间：{验证}')
            self.当前时间标签.setText(f'设备当前时间：{验证}')
            self.修改按钮.setEnabled(True)
            self.同步按钮.setEnabled(True)
            if self._状态回调:
                self._状态回调('设备时间修改成功', ok=True)

        def _失败(e):
            self.状态标签.setText(f'❌ 修改失败：{e}（可能需要 root 权限）')
            self.修改按钮.setEnabled(True)
            self.同步按钮.setEnabled(True)
            if self._状态回调:
                self._状态回调('设备时间修改失败', ok=False)

        w2 = 命令工作器(_执行)
        w2.signals.result.connect(_成功)
        w2.signals.error.connect(_失败)
        w2.signals.finished.connect(lambda: self._丢弃工作器(w2))
        self._live_workers.append(w2)
        self.pool.start(w2)

    def _同步北京时间(self):
        现在 = datetime.now(timezone(timedelta(hours=8))).replace(tzinfo=None)
        self.日期编辑.setDate(QDate(现在.year, 现在.month, 现在.day))
        self.时间编辑.setTime(QTime(现在.hour, 现在.minute, 现在.second))
        self._执行修改(现在)

    def _修改为编辑时间(self):
        d = self.日期编辑.date()
        t = self.时间编辑.time()
        dt = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute(), t.second())
        self._执行修改(dt)
