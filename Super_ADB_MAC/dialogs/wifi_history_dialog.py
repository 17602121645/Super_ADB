# -*- coding: utf-8 -*-
"""
WiFi 配对/连接历史记录
======================
以表格展示配对、连接、重连的操作历史（时间 / 动作 / 目标 / 结果 / 详情），
支持导出 CSV / JSON 与清空。
"""

import csv
from tools.json_io import save_json
import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QMessageBox, QFileDialog,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import STYLE_SHEET, get_stylesheet, get_current_theme_id
from tools.adb_tools import 加载json配置, 保存json配置

_HISTORY_CFG = 'wifi_debug_history.json'

_COLUMNS = ['时间', '动作', '目标', '结果', '详情']


class WifiHistoryDialog(QDialog):
    """配对/连接操作历史查看器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("WiFi 配对/连接历史")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(420)
        self._build_ui()
        self.setStyleSheet(get_stylesheet(get_current_theme_id(self)))
        self._load()

    def apply_theme(self, theme_id):
        """运行时切换主题。"""
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self.setStyleSheet(get_stylesheet(theme_id))
        self.update()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(3, 70)
        root.addWidget(self.table, 1)

        h = QHBoxLayout()
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.clicked.connect(self._export_csv)
        h.addWidget(self.btn_csv)

        self.btn_json = QPushButton("导出 JSON")
        self.btn_json.clicked.connect(self._export_json)
        h.addWidget(self.btn_json)

        self.btn_clear = QPushButton("清空")
        self.btn_clear.clicked.connect(self._clear)
        h.addWidget(self.btn_clear)

        h.addStretch()
        self.btn_close = QPushButton("关闭")
        self.btn_close.setDefault(True)
        self.btn_close.clicked.connect(self.accept)
        h.addWidget(self.btn_close)
        root.addLayout(h)

    def _load(self):
        data = 加载json配置(_HISTORY_CFG)
        if not isinstance(data, list):
            data = []
        self.table.setRowCount(len(data))
        for i, e in enumerate(data):
            for c, key in enumerate(_COLUMNS):
                item = QTableWidgetItem(str(e.get(key, '')))
                if key == '结果':
                    item.setForeground(Qt.green if e.get(key) == '成功' else Qt.red)
                self.table.setItem(i, c, item)

    def _rows_as_dicts(self):
        data = []
        for i in range(self.table.rowCount()):
            row = {}
            for c, key in enumerate(_COLUMNS):
                item = self.table.item(i, c)
                row[key] = item.text() if item else ''
            data.append(row)
        return data

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 CSV", "wifi_debug_history.csv", "CSV (*.csv)")
        if not path:
            return
        data = self._rows_as_dicts()
        try:
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.DictWriter(f, fieldnames=_COLUMNS)
                w.writeheader()
                w.writerows(data)
            QMessageBox.information(self, "已导出",
                                    f"已导出 {len(data)} 条记录到：\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"{e}")

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出 JSON", "wifi_debug_history.json", "JSON (*.json)")
        if not path:
            return
        data = self._rows_as_dicts()
        if save_json(path, data):
            QMessageBox.information(self, "已导出",
                                    f"已导出 {len(data)} 条记录到：\n{path}")
        else:
            QMessageBox.warning(self, "导出失败", "写入 JSON 失败，详见日志")

    def _clear(self):
        r = QMessageBox.question(
            self, "清空历史", "确定要清空全部配对/连接历史记录吗？",
            QMessageBox.Yes | QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        保存json配置(_HISTORY_CFG, [])
        self._load()


if __name__ == "__main__":
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    dlg = WifiHistoryDialog()
    dlg.show()
    sys.exit(app.exec())
