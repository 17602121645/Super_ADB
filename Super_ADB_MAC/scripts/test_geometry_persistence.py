# -*- coding: utf-8 -*-
"""离线验证窗口几何持久化机制（与虚拟屏无关的稳健断言）。

offscreen 平台只有一个很小的虚拟屏，restoreGeometry 的“绝对像素”会被钳制，
因此不比较绝对像素，只验证由本代码负责的、与环境无关的环节：
  1) saveGeometry -> base64 -> 还原 的字节无损；
  2) geometry.b64 写入配置文件后可被 加载json配置 无损读回；
  3) 旧 {x,y,w,h} 字典格式仍可被正常读回（向后兼容路径）；
  4) restoreGeometry 冒烟：用保存的 blob 还原到窗口后几何有效（宽高为正）。
"""
import os
import sys
import unittest

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QApplication, QWidget

MAIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(MAIN))
sys.path.insert(0, os.path.abspath(os.path.join(MAIN, "tools")))
from adb_tools import 加载json配置, 保存json配置  # noqa: E402

CONFIG_NAME = "geo_test_config.json"  # 独立临时配置，绝不污染真实 super_adb_config.json


class GeometryPersistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        cls.app.setApplicationName("SuperADB_GeoTest")

    @classmethod
    def tearDownClass(cls):
        # 清理独立临时配置文件（_config_path 现统一放到 config/ 子目录）
        for p in (os.path.join(MAIN, CONFIG_NAME),
                  os.path.join(MAIN, 'config', CONFIG_NAME)):
            try:
                os.remove(p)
            except OSError:
                pass

    def _make_win(self):
        w = QWidget()
        w.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        return w

    def _saved_blob(self, x, y, w, h):
        win = self._make_win()
        win.setGeometry(x, y, w, h)
        win.show()
        QApplication.processEvents()
        blob = win.saveGeometry()
        win.close()
        return blob

    def test_b64_lossless(self):
        """saveGeometry -> base64 -> 解码，字节应完全无损（本代码存储格式正确）。"""
        blob = self._saved_blob(300, 200, 900, 600)
        b64 = bytes(blob.toBase64()).decode("ascii")
        self.assertEqual(QByteArray.fromBase64(b64.encode("ascii")), blob,
                         "geometry.b64 编解码应无损")

    def test_config_roundtrip_b64(self):
        """geometry.b64 写入配置文件后读回，base64 字符串无损。"""
        blob = self._saved_blob(150, 90, 760, 540)
        b64 = bytes(blob.toBase64()).decode("ascii")

        cfg = 加载json配置(CONFIG_NAME)
        cfg["geometry"] = {"b64": b64}
        保存json配置(CONFIG_NAME, cfg)

        reloaded = 加载json配置(CONFIG_NAME).get("geometry") or {}
        self.assertIn("b64", reloaded)
        self.assertEqual(reloaded["b64"], b64, "配置落盘的 geometry.b64 应无损读回")

    def test_restore_smoke(self):
        """用保存的 blob 还原到窗口，restoreGeometry 不崩且几何有效。"""
        blob = self._saved_blob(120, 80, 700, 500)
        w = self._make_win()
        w.restoreGeometry(blob)
        w.show()
        QApplication.processEvents()
        g = w.geometry()
        w.close()
        self.assertGreater(g.width(), 0, "还原后窗口宽度应为正")
        self.assertGreater(g.height(), 0, "还原后窗口高度应为正")

    def test_old_dict_format_compat(self):
        """旧 {x,y,w,h} 格式仍能被正常读回（向后兼容路径）。"""
        cfg = 加载json配置(CONFIG_NAME)
        cfg["geometry"] = {"x": 100, "y": 80, "w": 700, "h": 500}
        保存json配置(CONFIG_NAME, cfg)
        g = 加载json配置(CONFIG_NAME).get("geometry") or {}
        self.assertEqual(int(g["w"]), 700)
        self.assertEqual(int(g["x"]), 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
