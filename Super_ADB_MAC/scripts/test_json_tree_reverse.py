"""
测试 JSON 树视图反向定位是否工作的最小化脚本。
执行: cd Super_ADB_Win && D:/Python/Python314/python.exe scripts/test_json_tree_reverse.py
"""
import sys, os
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _here)
for _sub in ('dialogs', 'pages', 'monitoring', 'tools'):
    sd = os.path.join(_here, _sub)
    if os.path.isdir(sd):
        sys.path.insert(0, sd)

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QTreeWidget, QTreeWidgetItem, QTextEdit
from PySide6.QtGui import QTextCursor
from PySide6.QtCore import Qt

app = QApplication([])

from dialogs.json_tool_dialog import pretty_with_paths

test_data = {
    'app': {'id': 'com.migu.aijia', 'ver': 100},
    'device': {
        'did': 'DOH51118253888122',
        'oaid': '',
        'mac': '0C:58:7B:07:88:1C',
    },
    'keyword': ['足球', '世界杯'],
}
tree_text_content, path_lines = pretty_with_paths(test_data, 2)

tree_w = QTreeWidget()
tree_w.setColumnCount(2)
text_edit = QTextEdit()
text_edit.setPlainText(tree_text_content)


def _add_items(parent, value, path):
    if isinstance(value, dict):
        for k, val in value.items():
            cp = path + (k,)
            item = QTreeWidgetItem([str(k)])
            item.setData(0, Qt.ItemDataRole.UserRole, cp)
            if isinstance(val, (dict, list)):
                item.setText(1, f'{type(val).__name__}({len(val)})')
            parent.addChild(item)
            _add_items(item, val, cp)
    elif isinstance(value, list):
        for i, val in enumerate(value):
            cp = path + (f'[{i}]',)
            item = QTreeWidgetItem([f'[{i}]'])
            item.setData(0, Qt.ItemDataRole.UserRole, cp)
            if isinstance(val, (dict, list)):
                item.setText(1, f'{type(val).__name__}({len(val)})')
            parent.addChild(item)
            _add_items(item, val, cp)


def _find_and_select(target_path):
    def _search(parent):
        for i in range(parent.childCount()):
            it = parent.child(i)
            if it.data(0, Qt.ItemDataRole.UserRole) == target_path:
                tree_w.blockSignals(True)
                try:
                    tree_w.setCurrentItem(it)
                finally:
                    tree_w.blockSignals(False)
                return True
            if _search(it):
                return True
        return False
    return _search(tree_w.invisibleRootItem())


def _on_cursor():
    cur = text_edit.textCursor()
    ln = cur.block().blockNumber() + 1
    best, best_len = None, -1
    for pp, (s, e) in path_lines.items():
        if s <= ln <= e and len(pp) > best_len:
            best = pp
            best_len = len(pp)
    if best is not None:
        return _find_and_select(best)
    return None


_add_items(tree_w.invisibleRootItem(), test_data, ())
tree_w.expandAll()
text_edit.cursorPositionChanged.connect(_on_cursor)


def move_cursor_to_line(target_ln):
    cur = text_edit.textCursor()
    cur.movePosition(QTextCursor.MoveOperation.Start)
    for _ in range(target_ln - 1):
        cur.movePosition(QTextCursor.MoveOperation.Down)
    text_edit.setTextCursor(cur)
    QApplication.processEvents()


# 测试用例 + 期望反向定位的 path
cases = [
    (3, ('app', 'id')),
    (4, ('app', 'ver')),
    (7, ('device', 'did')),
    (8, ('device', 'oaid')),
    (9, ('device', 'mac')),
    (12, ('keyword', '[0]')),
    (13, ('keyword', '[1]')),
]

print('=== 反向定位测试 ===\n')
all_pass = True
for target_ln, expected_path in cases:
    move_cursor_to_line(target_ln)
    cur_item = tree_w.currentItem()
    actual_path = cur_item.data(0, Qt.ItemDataRole.UserRole) if cur_item else None
    ok = actual_path == expected_path
    mark = '✅' if ok else '❌'
    print(f'{mark} 第 {target_ln:2d} 行 → 期望 {expected_path!r}, 实际 {actual_path!r}')
    if not ok:
        all_pass = False

print()
print('=== 所有测试通过 ===' if all_pass else '=== 有测试失败 ===')
sys.exit(0 if all_pass else 1)
