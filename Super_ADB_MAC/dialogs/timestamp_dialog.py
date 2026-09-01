# -*- coding: utf-8 -*-
"""
时间戳转换弹窗
==============
点击主界面「便捷工具 → 时间戳转换」按钮弹出的独立窗口：
- 时间戳 → 北京时间：自动识别 秒 / 毫秒 / 微秒 / 纳秒，实时转换并显示秒级 + 毫秒级北京时间
- 北京时间 → 时间戳：用日期时间选择器（默认当前北京时间），实时给出 Unix 秒级 + 毫秒级时间戳
- 每个结果框带「复制」按钮，一键粘贴到日志 / bug 报告

说明：北京时间固定按 UTC+8 计算（datetime.timezone(timedelta(hours=8))），结果与时区无关；
      日期时间选择器读取时显式标注为北京时间，适用于中国用户本机（时区 = 北京时间）。
"""

import os
import sys
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QLineEdit, QPushButton, QGroupBox, QDateTimeEdit,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import FONT_FAMILY, STYLE_SHEET, get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import add_green_glow, highlight_card_style, _create_popup_card

BJ = timezone(timedelta(hours=8))  # 北京时间 UTC+8


def _norm_ts(ts: int) -> float:
    """把任意位数的 Unix 时间戳归一到秒级浮点（支持 秒/毫秒/微秒/纳秒）。"""
    s = str(abs(ts))
    if len(s) <= 10:
        return float(ts)
    if len(s) <= 13:
        return ts / 1000.0
    if len(s) <= 16:
        return ts / 1e6
    return ts / 1e9


class 时间戳对话框(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("时间戳转换")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(480)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id))
        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, self._theme_id)
        self._build_ui()

    def apply_theme(self, theme_id):
        """运行时切换主题：更新全局 QSS + 外发光。"""
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        self.update()

    def _build_ui(self):
        root = QVBoxLayout(self.card)
        root.setSpacing(12)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 时间戳 → 北京时间 ──
        g1 = QGroupBox("时间戳 → 北京时间")
        v1 = QVBoxLayout(g1)
        v1.setSpacing(8)

        h_in = QHBoxLayout()
        lbl = QLabel("时间戳")
        lbl.setFixedWidth(56)
        self.ts_input = QLineEdit()
        self.ts_input.setPlaceholderText("输入 Unix 时间戳，自动识别 秒 / 毫秒 / 微秒 / 纳秒")
        self.ts_input.textChanged.connect(self._on_ts_input)
        btn_now = QPushButton("现在")
        btn_now.setFixedWidth(56)
        btn_now.clicked.connect(self._fill_now_ts)
        h_in.addWidget(lbl)
        h_in.addWidget(self.ts_input)
        h_in.addWidget(btn_now)
        v1.addLayout(h_in)

        v1.addLayout(self._result_row("ts_sec", "秒级"))
        v1.addLayout(self._result_row("ts_ms", "毫秒级"))
        root.addWidget(g1)

        # ── 北京时间 → 时间戳 ──
        g2 = QGroupBox("北京时间 → 时间戳")
        v2 = QVBoxLayout(g2)
        v2.setSpacing(8)

        h_dt = QHBoxLayout()
        lbl2 = QLabel("北京时间")
        lbl2.setFixedWidth(56)
        self.dt_edit = QDateTimeEdit(datetime.now(BJ).replace(tzinfo=None), self)
        self.dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss.zzz")
        self.dt_edit.setCalendarPopup(True)
        self.dt_edit.dateTimeChanged.connect(self._on_dt_changed)
        btn_cur = QPushButton("现在")
        btn_cur.setFixedWidth(56)
        btn_cur.clicked.connect(self._fill_now_dt)
        h_dt.addWidget(lbl2)
        h_dt.addWidget(self.dt_edit)
        h_dt.addWidget(btn_cur)
        v2.addLayout(h_dt)

        v2.addLayout(self._result_row("dt_sec", "秒级"))
        v2.addLayout(self._result_row("dt_ms", "毫秒级"))
        root.addWidget(g2)

        # 复制按钮统一连接一次（点击时读当前文本）
        self.ts_sec_copy.clicked.connect(lambda: self._copy(self.ts_sec.text()))
        self.ts_ms_copy.clicked.connect(lambda: self._copy(self.ts_ms.text()))
        self.dt_sec_copy.clicked.connect(lambda: self._copy(self.dt_sec.text()))
        self.dt_ms_copy.clicked.connect(lambda: self._copy(self.dt_ms.text()))

        # 初次填充
        self._on_ts_input()
        self._on_dt_changed()

    def _result_row(self, attr_prefix, label_text):
        h = QHBoxLayout()
        lbl = QLabel(label_text)
        lbl.setFixedWidth(48)
        le = QLineEdit()
        le.setReadOnly(True)
        le.setFont(QFont(FONT_FAMILY, 10))
        btn = QPushButton("复制")
        btn.setFixedWidth(56)
        h.addWidget(lbl)
        h.addWidget(le)
        h.addWidget(btn)
        setattr(self, attr_prefix, le)
        setattr(self, attr_prefix + "_copy", btn)
        return h

    # —— 回调 ——
    def _on_ts_input(self):
        txt = self.ts_input.text().strip()
        if not txt:
            self.ts_sec.setText("")
            self.ts_ms.setText("")
            return
        try:
            ts = int(txt)
        except ValueError:
            self.ts_sec.setText("（输入无效，需为整数时间戳）")
            self.ts_ms.setText("")
            return
        dt = datetime.fromtimestamp(_norm_ts(ts), tz=timezone.utc).astimezone(BJ)
        self.ts_sec.setText(dt.strftime("%Y-%m-%d %H:%M:%S"))
        self.ts_ms.setText(dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{dt.microsecond // 1000:03d}")

    def _on_dt_changed(self):
        dt = self.dt_edit.dateTime().toPython()  # 本地 naive（本机 = 北京时间）
        aware = dt.replace(tzinfo=BJ)
        self.dt_sec.setText(str(int(aware.timestamp())))
        self.dt_ms.setText(str(int(aware.timestamp() * 1000)))

    def _fill_now_ts(self):
        self.ts_input.setText(str(int(datetime.now(BJ).timestamp())))

    def _fill_now_dt(self):
        self.dt_edit.setDateTime(datetime.now(BJ).replace(tzinfo=None))

    @staticmethod
    def _copy(text):
        if text:
            QApplication.clipboard().setText(text)


if __name__ == "__main__":
    import sys as _sys
    app = QApplication(_sys.argv)
    dlg = 时间戳对话框()
    dlg.show()
    _sys.exit(app.exec())
