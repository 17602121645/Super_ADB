# -*- coding: utf-8 -*-
"""离线验证 JSON 树视图美化：徽标 / CodeTextEdit / 正反向同步。"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
MAIN = os.path.join(ROOT, 'Super_ADB_Win')
for p in (MAIN, os.path.join(MAIN, 'tools'), os.path.join(MAIN, 'dialogs')):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

import json_tool_dialog as M

SAMPLE = '''{
  "device": {
    "oaid": "abc-123",
    "androidId": "xyz",
    "level": 7,
    "enabled": true,
    "nickname": null,
    "tags": ["a", "b", "c"]
  },
  "list": [10, 20, {"k": "v"}]
}'''

fails = []
def check(cond, msg):
    if cond:
        print('  PASS', msg)
    else:
        print('  FAIL', msg)
        fails.append(msg)


def test_helpers():
    print('[1] 徽标辅助函数')
    for v, letter in [(True, 'B'), ({}, 'D'), ([], 'L'), ('x', '"'),
                      (3, '#'), (None, '∅'), (2.5, '#')]:
        got, _ = M._type_badge(v)
        check(got == letter, f'_type_badge({v!r}) -> {got} (expect {letter})')
    pm = M._make_badge_pixmap('D', QColor(99, 155, 255))
    check(pm.width() == 18 and pm.height() == 18, '徽标 pixmap 18x18')


def test_code_text():
    print('[2] CodeTextEdit 行号 + 高亮')
    te = M.CodeTextEdit()
    te.setPlainText('{\n  "a": 1,\n  "b": [2, 3]\n}')
    M.JsonHighlighter(te.document())
    check(te.line_number_area_width() > 20, f'行号区宽度={te.line_number_area_width()}')
    # 范围高亮
    M.JsonToolDialog._select_lines(te, 2, 3, QColor(29, 233, 182))
    check(te._sel_extra is not None, '_sel_extra 已设置（树节点范围高亮）')
    extras = te.extraSelections()
    check(len(extras) == 2, f'合并后 extra 数={len(extras)}（当前行+范围）')
    # 清除后仅剩当前行
    te.set_selection_range(None)
    check(te._sel_extra is None and len(te.extraSelections()) == 1,
          '清空范围后仅保留当前行高亮')


def test_popup_e2e():
    print('[3] 弹窗端到端构建')
    captured = []
    orig_exec = QDialog_exec = M.QDialog.exec
    def fake_exec(self):
        captured.append(self)
        return M.QDialog.DialogCode.Accepted
    M.QDialog.exec = fake_exec

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = M.JsonToolDialog()
    dlg.fmtInput.setPlainText(SAMPLE)
    dlg._open_json_tree_popup()
    M.QDialog.exec = orig_exec

    popup = captured[0]
    tree_w = popup.findChild(QTreeWidget)
    code = popup.findChild(M.CodeTextEdit)
    check(tree_w is not None, '树控件已创建')
    check(code is not None, 'CodeTextEdit 已创建')
    check(tree_w.topLevelItemCount() == 2, f'顶层节点数={tree_w.topLevelItemCount()} (expect 2)')
    # 徽标图标
    dev = tree_w.topLevelItem(0)
    check(not dev.icon(0).isNull(), '顶层节点列0 带有类型徽标图标')
    # 列1 类型标签
    check('dict' in dev.text(1).lower() or 'Dict' in dev.text(1), f'列1 标签={dev.text(1)!r}')
    # 行号区宽度
    check(code.line_number_area_width() > 20, '弹窗内 CodeTextEdit 行号区正常')

    # 捕获 Qt 槽里的异常（PySide6 默认打印到 stderr 而不抛出）
    import io
    err_buf = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = err_buf

    # 正向定位：选中 device.oaid 树节点 → 文本应出现范围高亮
    oaid_item = None

    def _walk(it):
        nonlocal oaid_item
        for i in range(it.childCount()):
            c = it.child(i)
            if c.data(0, Qt.ItemDataRole.UserRole) == ('device', 'oaid'):
                oaid_item = c
            _walk(c)

    _walk(tree_w.invisibleRootItem())
    check(oaid_item is not None, '找到 device.oaid 树节点')
    tree_w.setCurrentItem(oaid_item)  # 触发 itemSelectionChanged → _on_select
    check(code._sel_extra is not None, '正向定位：文本出现范围高亮 (_sel_extra)')

    # 反向定位：把光标移到 "level" 行 → 选中对应树节点（真实信号）
    txt, path_lines = M.pretty_with_paths(__import__('json').loads(SAMPLE), 2)
    lines = txt.split('\n')
    lvl_line = next(i + 1 for i, l in enumerate(lines) if '"level"' in l)
    cur = code.textCursor()
    cur.movePosition(cur.MoveOperation.Start)
    for _ in range(lvl_line - 1):
        cur.movePosition(cur.MoveOperation.Down)
    code.setTextCursor(cur)  # 触发 cursorPositionChanged → _on_cursor
    sel = tree_w.currentItem()
    check(sel is not None
          and sel.data(0, Qt.ItemDataRole.UserRole) == ('device', 'level'),
          f'反向定位选中 {sel.data(0, Qt.ItemDataRole.UserRole) if sel else None}')

    sys.stderr = old_stderr
    err_txt = err_buf.getvalue()
    check('Error' not in err_txt and 'Traceback' not in err_txt,
          f'信号联动无异常 (stderr 长度={len(err_txt)})')


if __name__ == '__main__':
    app = QApplication.instance() or QApplication(sys.argv)
    test_helpers()
    test_code_text()
    test_popup_e2e()
    print()
    if fails:
        print(f'结果：{len(fails)} 项失败')
        sys.exit(1)
    print('结果：全部通过 ✅')
