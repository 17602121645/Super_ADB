# -*- coding: utf-8 -*-
"""
可收藏 / 可删除的下拉输入框（FavComboBox）
=========================================
用于 标签 / PID / 包名 / 消息 过滤字段。

  · 可编辑（QComboBox editable）：直接输入关键字，或从下拉列表选择已收藏的关键字。
  · 下拉列表的每一项右侧带一个"✕"删除按钮，点击即可删除该收藏。
  · 收藏的持久化由页面负责写入配置文件；本控件只负责增删元素并发信号。
"""
from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QComboBox, QListView, QStyledItemDelegate, QWidget

from ui.ui_styles import ACCENT, FONT_FAMILY

_BTN_W = 24  # 右侧删除按钮宽度


class 收藏委托(QStyledItemDelegate):
    """下拉项代理：在每项右侧绘制"✕"删除按钮（仅绘制，点击由 _FavListView 处理）"""

    def sizeHint(self, option, index):
        h = super().sizeHint(option, index)
        return QSize(h.width(), max(h.height(), 28))

    @staticmethod
    def btn_rect(option):
        r = option.rect
        return QRect(r.right() - _BTN_W, r.top(), _BTN_W, r.height())

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        painter.save()
        painter.setPen(QColor(ACCENT))
        painter.setFont(QFont(FONT_FAMILY, 10))
        painter.drawText(self.btn_rect(option), Qt.AlignCenter, '✕')
        painter.restore()


class _收藏列表视图(QListView):
    """下拉视图：拦截 ✕ 区域的点击以删除收藏，其余点击交给 QComboBox 默认行为"""

    def __init__(self, combo, parent=None):
        super().__init__(parent)
        self._combo = combo

    def _hit_delete_btn(self, pos):
        idx = self.indexAt(pos)
        if not idx.isValid():
            return -1
        rect = self.visualRect(idx)
        btn = QRect(rect.right() - _BTN_W, rect.top(), _BTN_W, rect.height())
        if btn.contains(pos):
            return idx.row()
        return -1

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            row = self._hit_delete_btn(event.pos())
            if row >= 0:
                self._combo.remove_favorite_at(row)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._hit_delete_btn(event.pos()) >= 0:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class 收藏下拉框(QComboBox):
    """可收藏下拉输入框"""
    favoritesChanged = Signal(str, list)  # (key, favorites)

    def __init__(self, key: str = '', placeholder: str = '', parent=None):
        if isinstance(key, QWidget):
            key, parent = '', key
        super().__init__(parent)
        self._key = key
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumHeight(28)
        self.setPlaceholderText(placeholder)

        view = _收藏列表视图(self)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setView(view)
        self._delegate = 收藏委托(self)
        self.setItemDelegate(self._delegate)

    def set_key(self, key: str):
        self._key = key

    def set_favorites(self, items):
        self.blockSignals(True)
        self.clear()
        for it in (items or []):
            self.addItem(it)
        self.blockSignals(False)

    def favorites(self):
        return [self.itemText(i) for i in range(self.count())]

    def add_favorite(self, text):
        text = (text or '').strip()
        if not text:
            return
        if text not in self.favorites():
            self.addItem(text)
            self.favoritesChanged.emit(self._key, self.favorites())

    def remove_favorite_at(self, row):
        if 0 <= row < self.count():
            self.removeItem(row)
            self.favoritesChanged.emit(self._key, self.favorites())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            text = self.currentText().strip()
            if text:
                self.add_favorite(text)
                return
        super().keyPressEvent(event)

# 旧名别名（编译后 UI 文件 from favorite_combobox import FavComboBox 引用）
FavComboBox = 收藏下拉框
FavDelegate = 收藏委托
