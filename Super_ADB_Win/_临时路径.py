# -*- coding: utf-8 -*-
"""临时诊断：确认上屏到底走「帧就绪信号」还是「200ms 兜底定时器」。用完即删。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from 工具.ADB工具 import AdbHelper
from 对话框.投屏窗口对话框 import 投屏窗口对话框
from 对话框.scrcpy_设置对话框 import load_scrcpy_settings
import 工具.OpenGL投屏视图 as GLV

SERIAL = '192.168.1.3:5555'
c = {'信号路径': 0, '定时路径': 0, '应用True': 0, '应用False': 0}


def 打补丁():
    V = GLV.OpenGL投屏视图
    原信号, 原定时, 原应用 = V._有新帧, V._定时刷新, V._应用新帧

    def 信号(self):
        c['信号路径'] += 1
        return 原信号(self)

    def 定时(self):
        c['定时路径'] += 1
        return 原定时(self)

    def 应用(self, frame):
        r = 原应用(self, frame)
        c['应用True' if r else '应用False'] += 1
        return r

    V._有新帧, V._定时刷新, V._应用新帧 = 信号, 定时, 应用


def main():
    打补丁()
    app = QApplication(sys.argv)
    adb = AdbHelper()
    dlg = 投屏窗口对话框(adb, SERIAL, settings=load_scrcpy_settings())
    dlg.show()

    步 = {'i': 0}

    def 滑动():
        cl = dlg.client
        if cl is None:
            return
        步['i'] += 1
        try:
            y0, y1 = (600, 200) if 步['i'] % 2 else (200, 600)
            cl.滑动(360, y0, 360, y1, 0.25)
        except Exception:
            pass

    t0 = {'v': 0.0}

    def 报告():
        if t0['v'] == 0.0:
            t0['v'] = time.monotonic()
            return
        dt = time.monotonic() - t0['v']
        cl = dlg.client
        print(f'[路径] {dt:4.1f}s 解码={getattr(cl,"_帧计数",-1)} '
              f'信号路径={c["信号路径"]} 定时路径={c["定时路径"]} '
              f'上屏={c["应用True"]} 重复={c["应用False"]} '
              f'上屏fps={c["应用True"]/dt:.1f}')

    滑动器 = QTimer(); 滑动器.timeout.connect(滑动)
    报告器 = QTimer(); 报告器.timeout.connect(报告)
    QTimer.singleShot(5000, lambda: (滑动器.start(700), 报告器.start(5000)))
    QTimer.singleShot(32000, app.quit)
    app.exec()


if __name__ == '__main__':
    main()
