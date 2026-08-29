# -*- coding: utf-8 -*-
"""临时诊断：帧就绪信号为何不触发（connect 结果 / emit 异常 / 线程归属）。用完即删。"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QThread

from 工具.ADB工具 import AdbHelper
from 对话框.投屏窗口对话框 import 投屏窗口对话框
from 对话框.scrcpy_设置对话框 import load_scrcpy_settings
import 工具.OpenGL投屏视图 as GLV

SERIAL = '192.168.1.3:5555'


def 打补丁():
    V = GLV.OpenGL投屏视图
    原绑定 = V.绑定客户端

    def 绑定(self, client):
        print('[信号] hasattr 帧就绪 =', hasattr(client, '帧就绪'))
        try:
            ok = client.帧就绪.connect(self._有新帧)
            print('[信号] connect 返回 =', ok)
        except Exception as e:
            print('[信号] connect 异常:', type(e).__name__, e)
        try:
            信号对象 = client.帧信号
            print('[信号] 帧信号对象线程 =', 信号对象.thread(),
                  '当前(GUI)线程 =', QThread.currentThread())
            t = 信号对象.thread()
            print('[信号] 信号对象线程状态 =',
                  'None' if t is None else ('finished' if t.isFinished() else 'alive'))
        except Exception as e:
            print('[信号] 线程查询异常:', e)
        # 直接在 GUI 线程 emit 一次，看槽是否被调用
        try:
            client.帧就绪.emit()
            print('[信号] GUI线程 emit 完成')
        except Exception as e:
            print('[信号] GUI线程 emit 异常:', type(e).__name__, e)
        return 原绑定(self, client)

    V.绑定客户端 = 绑定

    原有新帧 = V._有新帧

    def 有新帧(self):
        if not hasattr(self, '_信号计数'):
            self._信号计数 = 0
        self._信号计数 += 1
        if self._信号计数 <= 3:
            print('[信号] _有新帧 被调用，线程 =', threading.current_thread().name)
        return 原有新帧(self)

    V._有新帧 = 有新帧


def main():
    打补丁()
    app = QApplication(sys.argv)
    adb = AdbHelper()
    dlg = 投屏窗口对话框(adb, SERIAL, settings=load_scrcpy_settings())
    dlg.show()

    def 报告():
        print('[信号] 累计 _有新帧 次数 =', getattr(dlg.view, '_信号计数', 0),
              '解码帧 =', getattr(dlg.client, '_帧计数', -1))

    QTimer.singleShot(12000, 报告)
    QTimer.singleShot(14000, app.quit)
    app.exec()


if __name__ == '__main__':
    main()
