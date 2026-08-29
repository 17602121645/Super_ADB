# -*- coding: utf-8 -*-
"""临时诊断：在真实 投屏窗口对话框 环境下测量上屏帧率与端到端延迟。

用完即删。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from 工具.ADB工具 import AdbHelper
from 对话框.投屏窗口对话框 import 投屏窗口对话框
from 对话框.scrcpy_设置对话框 import load_scrcpy_settings

SERIAL = '192.168.1.3:5555'


def main():
    app = QApplication(sys.argv)
    settings = load_scrcpy_settings()
    print('[诊断] 生效设置:', settings)

    adb = AdbHelper()
    dlg = 投屏窗口对话框(adb, SERIAL, settings=settings)
    dlg.show()

    统计 = {'上屏': 0, '起点': 0.0}

    def 记帧():
        统计['上屏'] += 1
        if 统计['起点'] == 0.0:
            统计['起点'] = time.monotonic()

    dlg.view.帧更新.connect(记帧)

    def 报告():
        用时 = time.monotonic() - 统计['起点'] if 统计['起点'] else 0
        上屏fps = 统计['上屏'] / 用时 if 用时 > 0 else 0
        c = dlg.client
        解码 = getattr(c, '_帧计数', -1)
        跳帧 = getattr(c, '_跳帧计数', -1)
        队列 = getattr(getattr(c, '_视频socket', None), '_队列', None)
        队列长 = 队列.qsize() if 队列 is not None else -1
        新鲜度 = time.monotonic() - getattr(c, '_最近帧时间', 0)
        print(f'[诊断] {用时:5.1f}s 解码={解码} 上屏={统计["上屏"]} '
              f'上屏fps={上屏fps:.1f} 追帧跳过={跳帧} 隧道队列={队列长} '
              f'最新帧距今={新鲜度*1000:.0f}ms')

    报告器 = QTimer()
    报告器.timeout.connect(报告)
    QTimer.singleShot(6000, lambda: 报告器.start(5000))
    QTimer.singleShot(45000, app.quit)

    app.exec()


if __name__ == '__main__':
    main()
