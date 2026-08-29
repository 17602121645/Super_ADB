# -*- coding: utf-8 -*-
"""临时诊断：真实弹窗环境下的端到端延迟（注入按键 → 画面变化）。

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


def 亮度(frame):
    """取 Y 平面中心区域平均亮度。"""
    import ctypes
    w, h = frame.width, frame.height
    stride = frame.planes[0].line_size
    base = int(frame.planes[0].buffer_ptr)
    总和 = 0
    个数 = 0
    for dy in range(h // 2 - 20, h // 2 + 20, 4):
        行 = ctypes.string_at(base + dy * stride + w // 2 - 40, 80)
        总和 += sum(行)
        个数 += len(行)
    return 总和 / 个数 if 个数 else 0


def main():
    app = QApplication(sys.argv)
    settings = load_scrcpy_settings()
    adb = AdbHelper()
    dlg = 投屏窗口对话框(adb, SERIAL, settings=settings)
    dlg.show()

    状态 = {'轮': 0, '注入时刻': 0.0, '基线': None, '等待': False, '结果': []}

    def 采样():
        c = dlg.client
        if c is None:
            return
        f = c.获取原始帧()
        if f is None:
            return
        b = 亮度(f)
        if 状态['等待'] and 状态['基线'] is not None:
            if abs(b - 状态['基线']) > 12:
                延迟 = time.monotonic() - 状态['注入时刻']
                状态['结果'].append(延迟)
                print(f'[延迟] 第{状态["轮"]}轮 端到端 = {延迟*1000:.0f} ms '
                      f'(亮度 {状态["基线"]:.1f} → {b:.1f})')
                状态['等待'] = False

    def 触发():
        c = dlg.client
        if c is None:
            return
        f = c.获取原始帧()
        if f is None:
            return
        状态['轮'] += 1
        状态['基线'] = 亮度(f)
        状态['注入时刻'] = time.monotonic()
        状态['等待'] = True
        # 187 = 最近任务键，会引起全屏画面剧变
        try:
            c.按键(187, 0)
            c.按键(187, 1)
        except Exception as e:
            print('[延迟] 注入失败:', e)

    采样器 = QTimer()
    采样器.timeout.connect(采样)

    def 开始():
        采样器.start(5)
        for i in range(4):
            QTimer.singleShot(i * 3000, 触发)

    QTimer.singleShot(6000, 开始)

    def 收尾():
        采样器.stop()
        r = 状态['结果']
        if r:
            print(f'[延迟] 汇总 {len(r)} 次: ' +
                  ' / '.join(f'{x*1000:.0f}ms' for x in r) +
                  f'  平均 {sum(r)/len(r)*1000:.0f}ms')
        else:
            print('[延迟] 未采集到有效数据')
        app.quit()

    QTimer.singleShot(22000, 收尾)
    app.exec()


if __name__ == '__main__':
    main()
