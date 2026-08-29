# -*- coding: utf-8 -*-
"""临时诊断：GUI 线程渲染路径耗时（信号次数/重复帧/paintGL 耗时）。用完即删。"""
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

cnt = {'信号': 0, '应用True': 0, '应用False': 0,
       'paint': 0, 'paint耗时': 0.0, 'paint最大': 0.0,
       '上传耗时': 0.0, '绘制耗时': 0.0}


def 打补丁():
    V = GLV.OpenGL投屏视图
    原有新帧 = V._有新帧
    原应用 = V._应用新帧

    def 有新帧(self):
        cnt['信号'] += 1
        return 原有新帧(self)

    def 应用(self, frame):
        r = 原应用(self, frame)
        cnt['应用True' if r else '应用False'] += 1
        return r

    V._有新帧 = 有新帧
    V._应用新帧 = 应用

    W = GLV._GL渲染控件 if hasattr(GLV, '_GL渲染控件') else None
    if W is None:
        for name in dir(GLV):
            o = getattr(GLV, name)
            if isinstance(o, type) and hasattr(o, 'paintGL'):
                W = o
                break
    原paint = W.paintGL
    原上传 = W._上传帧纹理
    原绘制 = W._绘制

    def paintGL(self):
        t = time.perf_counter()
        原paint(self)
        d = time.perf_counter() - t
        cnt['paint'] += 1
        cnt['paint耗时'] += d
        cnt['paint最大'] = max(cnt['paint最大'], d)

    def 上传(self, 帧):
        t = time.perf_counter()
        r = 原上传(self, 帧)
        cnt['上传耗时'] += time.perf_counter() - t
        return r

    def 绘制(self):
        t = time.perf_counter()
        r = 原绘制(self)
        cnt['绘制耗时'] += time.perf_counter() - t
        return r

    W.paintGL = paintGL
    W._上传帧纹理 = 上传
    W._绘制 = 绘制
    print('[GUI诊断] 已挂钩渲染控件:', W.__name__)


def main():
    打补丁()
    app = QApplication(sys.argv)
    settings = load_scrcpy_settings()
    adb = AdbHelper()
    dlg = 投屏窗口对话框(adb, SERIAL, settings=settings)
    dlg.show()

    步 = {'i': 0}

    def 滑动():
        c = dlg.client
        if c is None:
            return
        步['i'] += 1
        try:
            y0, y1 = (600, 200) if 步['i'] % 2 else (200, 600)
            c.滑动(360, y0, 360, y1, 0.25)
        except Exception:
            pass

    def 报告():
        c = dlg.client
        print(f'[GUI诊断] 解码={getattr(c, "_帧计数", -1)} 信号={cnt["信号"]} '
              f'应用成功={cnt["应用True"]} 重复跳过={cnt["应用False"]} '
              f'paint次数={cnt["paint"]} '
              f'paint均={cnt["paint耗时"]/max(cnt["paint"],1)*1000:.2f}ms '
              f'paint最大={cnt["paint最大"]*1000:.1f}ms '
              f'上传均={cnt["上传耗时"]/max(cnt["paint"],1)*1000:.2f}ms '
              f'绘制均={cnt["绘制耗时"]/max(cnt["paint"],1)*1000:.2f}ms '
              f'视图尺寸={dlg.view.width()}x{dlg.view.height()}')

    滑动器 = QTimer(); 滑动器.timeout.connect(滑动)
    报告器 = QTimer(); 报告器.timeout.connect(报告)
    QTimer.singleShot(5000, lambda: (滑动器.start(700), 报告器.start(5000)))
    QTimer.singleShot(32000, app.quit)
    app.exec()


if __name__ == '__main__':
    main()
