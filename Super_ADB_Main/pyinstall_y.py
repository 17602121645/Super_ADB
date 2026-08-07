# -*- coding: UTF-8 -*-
"""
跨平台打包脚本（Windows / macOS）
@author:JCS
@file:pyinstall_y.py
"""
import os
import sys
import time


def install(main):
    name = f"Super_ADB{time.strftime('%H%M%S')}"
    if sys.platform == 'darwin':
        # macOS: 生成 .app，图标用 .icns（如有）否则 .png
        icon = 'adb.icns' if os.path.exists('adb.icns') else 'Super_ADB.png'
        cmd = f'pyinstaller --clean -w -i {icon} -n {name} {main}'
    else:
        # Windows: 生成 .exe
        cmd = f'pyinstaller --clean -w -i Super_ADB.png -n {name} {main}'
    os.system(cmd)
    print('配置文件生成成功')


def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')


if __name__ == '__main__':
    install("Super_ADB_Main.py")
