"""环境配置对话框的冒烟测试：offscreen 实例化 + 单主题截图（命令行参数）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ_ROOT = os.path.dirname(HERE)
for sub in ('app', 'dialogs', 'tools', 'monitoring', 'pages', 'ui', 'resources'):
    p = os.path.join(PROJ_ROOT, sub)
    if p not in sys.path:
        sys.path.insert(0, p)
if PROJ_ROOT not in sys.path:
    sys.path.insert(0, PROJ_ROOT)

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer

app = QApplication(sys.argv)
# 全局 hold 住 parent + dlg 避免被 GC
HOLD = {}


class MockParent(QWidget):
    def __init__(self, theme_id):
        super().__init__()
        self._current_theme = theme_id


def smoke(theme_id):
    from env_config_dialog import (
        EnvConfigDialog, detect_current_adb, 查找内置adb路径, add_to_user_path,
    )
    parent = MockParent(theme_id)
    parent.show()
    HOLD['parent'] = parent

    dlg = EnvConfigDialog(parent=parent)
    dlg.apply_theme(theme_id)
    dlg._refresh_adb_info()
    dlg.show()
    HOLD['dlg'] = dlg
    print(f'[OK] 对话框实例化: theme={theme_id} size={dlg.width()}x{dlg.height()}')

    bundled = 查找内置adb路径()
    ver, path = detect_current_adb()
    if bundled:
        print(f'[OK] 内置 ADB: size={os.path.getsize(bundled)} bytes')
    if ver and path:
        print(f'[OK] 当前 ADB: {ver}')
    if bundled:
        target_dir = os.path.dirname(bundled)
        ok1, msg1 = add_to_user_path(target_dir)
        print(f'[INFO] PATH 去重测试: ok={ok1}, msg={msg1!r}')

    def shoot():
        try:
            pix = dlg.grab()
            out = os.path.abspath(os.path.join(PROJ_ROOT, '..', f'smoke_envconfig_{theme_id}.png'))
            pix.save(out, 'PNG')
            print(f'[OK] 截图: {out} ({pix.width()}x{pix.height()})')
        except Exception as e:
            print(f'[WARN] 截图失败: {e}')
        app.quit()

    QTimer.singleShot(300, shoot)


theme = sys.argv[1] if len(sys.argv) > 1 else 'dark_neon'
smoke(theme)
app.exec()
print('[OK] 全部完成')
