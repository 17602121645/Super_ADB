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
    # 把各子目录加入 pathex，让 PyInstaller 在构建期能找到
    # 目录重组后分散在 dialogs/pages/monitors/utils 下的模块
    # （与 Super_ADB_Main.py 启动时注入 sys.path 的子目录保持一致）。
    # 否则 PyInstaller 只在入口脚本所在目录找模块，会漏掉这些子目录模块，
    # 打包后运行时报 ModuleNotFoundError（如 fav_combo / timestamp_dialog 等）。
    here = os.path.dirname(os.path.abspath(__file__))
    subdirs = ('dialogs', 'pages', 'monitors', 'utils')
    path_args = " ".join(
        '--paths "%s"' % os.path.join(here, d)
        for d in subdirs
        if os.path.isdir(os.path.join(here, d))
    )
    # 入口脚本解析为绝对路径，避免依赖运行时的 cwd
    if not os.path.isabs(main):
        main = os.path.join(here, main)

    # 显式声明隐藏依赖，避免 PyInstaller 在冻结时漏打包仅被局部 import 的模块
    hidden = " ".join(
        f'--hidden-import {m}' for m in (
            'segno', 'segno.helpers',
            'zeroconf', 'ifaddr',
        )
    )

    name = f"Super_ADB"
    if sys.platform == 'darwin':
        # macOS: 生成 .app，图标用 .icns（如有）否则 .png
        icon = os.path.join(here, 'adb.icns') if os.path.exists(os.path.join(here, 'adb.icns')) else os.path.join(here, 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} {hidden} {path_args} "{main}"'
    else:
        # Windows: 生成 .exe
        icon = os.path.join(here, 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} {hidden} {path_args} "{main}"'
    os.system(cmd)
    print('配置文件生成成功')


def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')


if __name__ == '__main__':
    install("Super_ADB_Main.py")
