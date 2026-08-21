# -*- coding: utf-8 -*-
"""
文件管理器右键「计算哈希」独立入口
=================================
由 Windows 资源管理器右键菜单调用：
  pythonw 哈希上下文菜单.py "%1"
接收任意数量的文件路径，用 compute_hashes_batch 计算 MD5/SHA1/SHA256，
弹窗展示每个文件的哈希值（带复制按钮 + 一键复制全部）。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QLabel, QPushButton,
    QHBoxLayout, QScrollArea, QWidget, QGroupBox, QMessageBox,
)

import png_rc  # noqa: F401

from 界面样式 import ACCENT, FONT_FAMILY, STYLE_SHEET
from MD5对话框 import compute_hashes_batch, ALGO_ORDER


class HashContextDialog(QDialog):
    def __init__(self, results, parent=None):
        super().__init__(parent)
        self.setWindowTitle("哈希计算结果")
        self.setStyleSheet(STYLE_SHEET)
        self.setMinimumWidth(640)
        self.setWindowIcon(QIcon(':/Super_ADB.png'))

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        root.addWidget(QLabel("右键菜单「计算哈希」结果："))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setSpacing(10)

        for path, res in results:
            box = QGroupBox(os.path.basename(path))
            bv = QVBoxLayout(box)
            box.setToolTip(path)
            if 'error' in res:
                err = QLabel(f"计算失败：{res['error']}")
                err.setStyleSheet("color: #e57373;")
                bv.addWidget(err)
            else:
                for key in ALGO_ORDER:
                    val = res.get(key)
                    if not val:
                        continue
                    row = QHBoxLayout()
                    tag = QLabel(key)
                    tag.setFixedWidth(64)
                    tag.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
                    row.addWidget(tag)
                    val_lbl = QLabel(val)
                    val_lbl.setFont(QFont(FONT_FAMILY, 10))
                    val_lbl.setWordWrap(True)
                    val_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    row.addWidget(val_lbl, 1)
                    btn = QPushButton("复制")
                    btn.setFixedWidth(60)
                    btn.setFixedHeight(24)
                    btn.clicked.connect(
                        lambda checked, v=val: self._copy(v, btn))
                    row.addWidget(btn)
                    bv.addLayout(row)
            v.addWidget(box)

        scroll.setWidget(container)
        root.addWidget(scroll, 1)

        bottom = QHBoxLayout()
        btn_all = QPushButton("复制全部")
        btn_all.setFixedWidth(100)
        btn_all.clicked.connect(lambda: self._copy_all(results))
        bottom.addWidget(btn_all)
        bottom.addStretch()
        btn_close = QPushButton("关闭")
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(self.accept)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    def _copy(self, text, btn):
        QApplication.clipboard().setText(text)
        old = btn.text()
        btn.setText("已复制")
        btn.setEnabled(False)
        QApplication.processEvents()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, lambda: (btn.setText(old), btn.setEnabled(True)))

    def _copy_all(self, results):
        lines = []
        for path, res in results:
            lines.append(os.path.basename(path))
            if 'error' in res:
                lines.append(f"  错误: {res['error']}")
                continue
            for key in ALGO_ORDER:
                if res.get(key):
                    lines.append(f"  {key}: {res[key]}")
        QApplication.clipboard().setText("\n".join(lines))
        QMessageBox.information(self, "已复制", "所有哈希已复制到剪贴板。")


def main():
    app = QApplication(sys.argv)
    paths = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if not paths:
        return
    results = compute_hashes_batch(paths)
    HashContextDialog(results).exec()


if __name__ == '__main__':
    main()
