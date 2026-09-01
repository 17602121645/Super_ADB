# -*- coding: utf-8 -*-
"""
ADB 交互式终端页面 —— 自研 ADB 模式专属
==========================================
类似 adb shell 不带参数进入的交互式终端，可持续敲命令、实时看输出。
仅在自研 ADB 模式下显示入口。

技术要点:
  - 自研adb客户端.交互式Shell() 打开 shell: 空服务，双向 WRTE
  - 输出通过信号回主线程追加到 QPlainTextEdit
  - 输入框回车发送，支持命令历史（上下箭头）
  - Ctrl+C 按钮发送 \\x03，Ctrl+D 发送 \\x04
  - 设备下拉框复用 AdbHelper 的设备列表
"""

import os
import re
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QTextCursor, QKeyEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton,
    QLabel, QLineEdit, QPlainTextEdit, QSizePolicy,
)

from tools.adb_tools import AdbHelper, 格式化设备标签


# ANSI 转义序列过滤（颜色/光标控制等，终端页面暂不渲染，直接去掉）
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x0f|\x0e')


class _终端信号桥(QObject):
    """跨线程信号桥：交互式Shell 的回调在后台线程，通过信号回主线程。"""
    输出 = Signal(bytes)
    关闭 = Signal()


class 终端页面(QWidget):
    """交互式 ADB Shell 终端页面（自研 ADB 模式专属）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._adb = AdbHelper()
        self._shell = None  # 交互式Shell 实例
        self._信号桥 = _终端信号桥()
        self._信号桥.输出.connect(self._追加输出)
        self._信号桥.关闭.connect(self._会话已关闭)
        self._命令历史 = deque(maxlen=200)
        self._历史索引 = -1
        self._构建UI()

    # ── UI 构建 ──

    def _构建UI(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 顶部工具栏：设备选择 + 连接按钮 + 状态
        top = QHBoxLayout()
        top.addWidget(QLabel('设备:'))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(280)
        self.device_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top.addWidget(self.device_combo)

        self.btn_refresh = QPushButton('刷新')
        self.btn_refresh.clicked.connect(self.刷新设备)
        top.addWidget(self.btn_refresh)

        self.btn_connect = QPushButton('连接终端')
        self.btn_connect.clicked.connect(self._切换连接)
        top.addWidget(self.btn_connect)

        self.btn_ctrlc = QPushButton('Ctrl+C')
        self.btn_ctrlc.setToolTip('发送中断信号 (\\x03)')
        self.btn_ctrlc.clicked.connect(lambda: self._发送控制字符(b'\x03'))
        self.btn_ctrlc.setEnabled(False)
        top.addWidget(self.btn_ctrlc)

        self.btn_ctrl_d = QPushButton('Ctrl+D')
        self.btn_ctrl_d.setToolTip('发送 EOF (\\x04)，退出当前 shell')
        self.btn_ctrl_d.clicked.connect(lambda: self._发送控制字符(b'\x04'))
        self.btn_ctrl_d.setEnabled(False)
        top.addWidget(self.btn_ctrl_d)

        self.btn_clear = QPushButton('清屏')
        self.btn_clear.clicked.connect(self._清屏)
        top.addWidget(self.btn_clear)

        top.addStretch()
        self.status_label = QLabel('未连接')
        self.status_label.setStyleSheet('color: #888;')
        top.addWidget(self.status_label)

        layout.addLayout(top)

        # 终端输出区
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)  # 限制行数，防内存膨胀
        # 等宽字体，类似终端
        font = QFont('Consolas', 10)
        font.setStyleHint(QFont.Monospace)
        self.output.setFont(font)
        self.output.setStyleSheet('''
            QPlainTextEdit {
                background-color: #1e1e1e;
                color: #d4d4d4;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 4px;
            }
        ''')
        layout.addWidget(self.output, 1)

        # 底部输入栏
        bottom = QHBoxLayout()
        self.input_edit = _历史输入框()
        self.input_edit.setPlaceholderText('输入命令，回车发送；上下箭头翻历史')
        self.input_edit.setFont(font)
        self.input_edit.returnPressed.connect(self._发送输入)
        self.input_edit.历史向上.connect(self._历史向上)
        self.input_edit.历史向下.connect(self._历史向下)
        self.input_edit.setEnabled(False)
        bottom.addWidget(self.input_edit, 1)

        self.btn_send = QPushButton('发送')
        self.btn_send.clicked.connect(self._发送输入)
        self.btn_send.setEnabled(False)
        bottom.addWidget(self.btn_send)

        layout.addLayout(bottom)

    # ── 设备管理 ──

    def 刷新设备(self):
        """刷新设备下拉框（复用 AdbHelper 的设备列表）。"""
        try:
            devices = self._adb.获取设备列表()
        except Exception:
            devices = []
        self.device_combo.clear()
        for serial, state in devices:
            label = 格式化设备标签(serial, state)
            self.device_combo.addItem(label, serial)
        if not devices:
            self.device_combo.addItem('（无设备）', None)
        self.status_label.setText(f'找到 {len(devices)} 个设备')

    def _当前设备序列号(self):
        idx = self.device_combo.currentIndex()
        if idx < 0:
            return None
        return self.device_combo.itemData(idx)

    # ── 连接 / 断开 ──

    def _切换连接(self):
        if self._shell and not self._shell.已关闭:
            self._断开()
        else:
            self._连接()

    def _连接(self):
        serial = self._当前设备序列号()
        if not serial:
            self.status_label.setText('请先选择设备')
            return

        try:
            # 通过 AdbHelper 获取自研 ADB 客户端（TCP）或 USB 连接
            连接源 = self._adb._获取自研adb(serial)
            if 连接源 is None:
                self.status_label.setText('自研 ADB 客户端不可用')
                return

            self.status_label.setText('正在打开终端...')
            # 交互式Shell 自动识别连接源类型：
            #   自研adb客户端 → TCP 池化独占
            #   UsbAdbConnection → USB 共享直连
            from tools.adb_native.adb_client import 交互式Shell
            self._shell = 交互式Shell(
                连接源,
                on_output=lambda data: self._信号桥.输出.emit(data),
                on_close=lambda: self._信号桥.关闭.emit(),
            )
            self._shell.启动()
            self._已连接UI()
            self.status_label.setText(f'已连接 {serial}')
            # 聚焦输入框
            self.input_edit.setFocus()
        except Exception as e:
            self.status_label.setText(f'连接失败: {e}')
            self._shell = None

    def _断开(self):
        if self._shell:
            try:
                self._shell.关闭()
            except Exception:
                pass
        self._shell = None
        self._已断开UI()
        self.status_label.setText('已断开')

    def _会话已关闭(self):
        """设备主动关闭或连接断开。"""
        self._shell = None
        self._已断开UI()
        self.status_label.setText('会话已结束')
        self._追加输出('[连接已关闭]\n'.encode('utf-8'))

    def _已连接UI(self):
        self.btn_connect.setText('断开')
        self.btn_ctrlc.setEnabled(True)
        self.btn_ctrl_d.setEnabled(True)
        self.input_edit.setEnabled(True)
        self.btn_send.setEnabled(True)
        self.device_combo.setEnabled(False)
        self.btn_refresh.setEnabled(False)

    def _已断开UI(self):
        self.btn_connect.setText('连接终端')
        self.btn_ctrlc.setEnabled(False)
        self.btn_ctrl_d.setEnabled(False)
        self.input_edit.setEnabled(False)
        self.btn_send.setEnabled(False)
        self.device_combo.setEnabled(True)
        self.btn_refresh.setEnabled(True)

    # ── 输入 / 输出 ──

    def _发送输入(self):
        text = self.input_edit.text()
        if not text:
            # 空行也发送（回车）
            self._shell.发送输入('\n')
            return
        # 记录历史
        self._命令历史.append(text)
        self._历史索引 = -1
        # 发送命令 + 换行
        self._shell.发送输入(text + '\n')
        self.input_edit.clear()

    def _发送控制字符(self, data: bytes):
        if self._shell and not self._shell.已关闭:
            self._shell.发送输入(data)

    def _追加输出(self, data: bytes):
        """将设备输出追加到终端显示区（主线程）。"""
        try:
            text = data.decode('utf-8', errors='replace')
        except Exception:
            text = repr(data)
        # 过滤 ANSI 转义序列
        text = _ANSI_RE.sub('', text)
        # 换行符统一：设备发 \n，显示需要 \n（QPlainTextEdit 自动处理）
        # 但 \r 可能导致行首覆盖，去掉单独的 \r
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 追加到末尾并滚动到底部
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    def _清屏(self):
        self.output.clear()

    # ── 命令历史 ──

    def _历史向上(self):
        if not self._命令历史:
            return
        if self._历史索引 < 0:
            self._历史索引 = len(self._命令历史) - 1
        elif self._历史索引 > 0:
            self._历史索引 -= 1
        self.input_edit.setText(self._命令历史[self._历史索引])

    def _历史向下(self):
        if not self._命令历史 or self._历史索引 < 0:
            return
        if self._历史索引 < len(self._命令历史) - 1:
            self._历史索引 += 1
            self.input_edit.setText(self._命令历史[self._历史索引])
        else:
            self._历史索引 = -1
            self.input_edit.clear()

    # ── 页面生命周期 ──

    def 页面显示时(self):
        """页面被切换到前台时调用，自动刷新设备列表。"""
        if not self._shell or self._shell.已关闭:
            self.刷新设备()

    def 页面隐藏时(self):
        """页面被切走时不自动断开（保持会话）。"""
        pass

    def 应用退出时(self):
        """应用退出时断开连接。"""
        self._断开()


class _历史输入框(QLineEdit):
    """支持上下箭头翻历史的输入框。"""
    历史向上 = Signal()
    历史向下 = Signal()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key_Up:
            self.历史向上.emit()
            return
        if event.key() == Qt.Key_Down:
            self.历史向下.emit()
            return
        super().keyPressEvent(event)
