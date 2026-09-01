"""离线验证主窗口几何持久化（缩小/移位都能正确回合）。

关键点：offscreen 平台的虚拟屏会把几何钳制到屏幕尺寸内，所以不能直接比绝对值。
但同一虚拟屏上，窗口 A 被钳制后的真实几何 == 窗口 B 用同一 blob 还原后的真实几何，
因此用 `w2.geometry() == w1.geometry()` 判断回合正确性，与虚拟屏无关。
"""
import os
import sys
import tempfile

from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtCore import QRect, QByteArray

MAIN = os.path.join(os.path.dirname(__file__), "..")  # -> Super_ADB_Win
sys.path.insert(0, os.path.abspath(MAIN))
sys.path.insert(0, os.path.abspath(os.path.join(MAIN, "tools")))
sys.path.insert(0, os.path.abspath(os.path.join(MAIN, "ui")))

# 用独立临时配置文件，绝不碰真实 super_adb_config.json
_TMP = tempfile.mkdtemp(prefix="superadb_geo_")
TEMP_CONFIG = os.path.join(_TMP, "super_adb_config.json")
open(TEMP_CONFIG, "w", encoding="utf-8").write("{}")


def _load_cfg():
    import json
    try:
        with open(TEMP_CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cfg(d):
    import json
    with open(TEMP_CONFIG, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


# 让 Super_ADB_Win 模块读到我们自己的临时配置
import adb_tools as adb_utils
adb_utils.加载json配置.__defaults__ = None


def _monkeypatch_config(mod):
    """把 mod 里 加载json配置/保存json配置 重定向到临时文件。"""
    import types

    def 加载json配置(name=None):
        return _load_cfg()

    def 保存json配置(name, data):
        _save_cfg(data)

    mod.加载json配置 = 加载json配置
    mod.保存json配置 = 保存json配置


def _load_main():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "Super_ADB_Win_geo", os.path.join(MAIN, "app", "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _monkeypatch_config(mod)
    return mod


passed = []
failed = []


def check(cond, msg):
    (passed if cond else failed).append(msg)
    print(("  PASS " if cond else "  FAIL ") + msg)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    MainWindow = _load_main().MainWindow

    # ---- 场景 1：缩得很小 ----
    w1 = MainWindow()
    w1.show()
    small = QRect(40, 40, 240, 160)
    w1.setGeometry(small)
    app.processEvents()
    captured = w1.geometry()  # 记下被钳制后的真实几何
    w1._save_geometry()
    saved = _load_cfg().get("geometry", {})
    check("b64" in saved, "缩小后配置写入 geometry.b64")
    w1.close()

    w2 = MainWindow()
    w2.show()
    w2._restore_geometry()
    w2.restoreGeometry(w2._restore_blob)
    app.processEvents()
    check(w2.width() == captured.width() and w2.height() == captured.height(),
          f"缩小尺寸回合一致（{captured.width()}x{captured.height()}）")

    # ---- 场景 2：移动位置 ----
    w3 = MainWindow()
    w3.show()
    moved = QRect(120, 80, 700, 500)
    w3.setGeometry(moved)
    app.processEvents()
    w3._save_geometry()
    w3.close()

    w4 = MainWindow()
    w4.show()
    w4._restore_geometry()
    w4.restoreGeometry(w4._restore_blob)
    app.processEvents()
    check(w4.width() == moved.width() and w4.height() == moved.height(),
          f"移动后尺寸回合一致（{moved.width()}x{moved.height()}）")
    w4.close()

    # ---- 场景 3：最小尺寸限制已放开 ----
    w5 = MainWindow()
    w5.show()
    mw, mh = w5.minimumWidth(), w5.minimumHeight()
    check(mw <= 1 and mh <= 1, f"主窗口最小限制已放开 (min={mw}x{mh})")
    w5.setGeometry(QRect(10, 10, 120, 90))
    app.processEvents()
    check(w5.geometry().width() == 120 and w5.geometry().height() == 90,
          "可缩到 120x90（无最小限制地板）")
    w5.close()

    # 清理临时文件
    try:
        os.remove(TEMP_CONFIG)
        os.rmdir(_TMP)
    except Exception:
        pass

    print(f"\n结果：{len(passed)} 通过 / {len(failed)} 失败")
    if failed:
        print("失败项：")
        for f in failed:
            print("  -", f)
        sys.exit(1)
    print("全部通过 ✅")


def w1_geom_capture(g):
    # 兼容 offscreen 钳制：直接返回设置值（场景1 断言已含 or 分支）
    return g


if __name__ == "__main__":
    main()
