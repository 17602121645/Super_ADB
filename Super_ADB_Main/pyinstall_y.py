# -*- coding: UTF-8 -*-
"""
跨平台打包脚本（Windows / macOS）
@author:JCS
@file:pyinstall_y.py
"""
import os
import sys
import time
import shutil


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
            'pyzbar',   # 二维码扫码解码（替代原 OpenCV，省 ~140MB）
        )
    )

    # pyzbar 的 DLL 用 hook 收集（见 hooks/hook-pyzbar.py），这里只挂目录
    hooks_dir = os.path.join(here, 'hooks')
    hooks = f'--additional-hooks-dir "{hooks_dir}"' if os.path.isdir(hooks_dir) else ''
    # 运行时钩子：把 pyzbar 的 DLL 加载路径重定向到打包产物里的确定位置
    # （避免冻结后 __file__ 是虚拟路径找不到 libzbar-64.dll）
    rt_hook = os.path.join(hooks_dir, 'runtime_pyzbar.py')
    runtime_hooks = f'--runtime-hook "{rt_hook}"' if os.path.isfile(rt_hook) else ''

    # numpy 仅被 PIL.Image 在 fromarray/np.asarray 里惰性局部 import，
    # 本工程从不调用 fromarray，纯 Image.open 路径无需 numpy；
    # 排除可省 ~26MB，且 PySide6 顶层不依赖 numpy，安全。
    # cv2 已被 qr_connect_page 改用 pyzbar+PIL 取代，整包排除（避免被
    # pyzbar.tests 间接拉回 ~111MB）。
    # pyzbar.tests 仅含单元测试，运行期不需要，且会间接 import cv2/numpy。
    excludes = '--exclude-module numpy --exclude-module cv2 --exclude-module pyzbar.tests'

    # 运行时资源（导出 HTML 报告用的 chart.umd.min.js）：随包分发，离线可用。
    # PyInstaller 的 SRC:DST 分隔符在 Windows 上为 ';'、其余平台为 ':'。
    add_data_sep = ';' if sys.platform == 'win32' else ':'
    res_arg = f'--add-data "{os.path.join(here, "resources")}{add_data_sep}resources"'

    name = f"Super_ADB"
    # 构建前清空旧输出目录，避免 COLLECT 报 "output directory not empty" 而中断。
    # 默认 rmtree 真删；若设 CLEAN_MOVE=1（如构建环境禁止批量删除）则改名为
    # Super_ADB_prev / _prev2 ... 移开，功能等价且可手动清理。
    out_dir = os.path.join(here, 'dist', name)
    if os.path.isdir(out_dir):
        if os.environ.get('CLEAN_MOVE'):
            prev = out_dir + '_prev'
            i = 2
            while os.path.isdir(prev):
                prev = out_dir + f'_prev{i}'
                i += 1
            shutil.move(out_dir, prev)
            print('旧构建已改名移开:', prev)
        else:
            shutil.rmtree(out_dir)
    if sys.platform == 'darwin':
        # macOS: 生成 .app，图标用 .icns（如有）否则 .png
        icon = os.path.join(here, 'adb.icns') if os.path.exists(os.path.join(here, 'adb.icns')) else os.path.join(here, 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {path_args} "{main}"'
    else:
        # Windows: 生成 .exe
        icon = os.path.join(here, 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {path_args} "{main}"'
    os.system(cmd)
    print('配置文件生成成功')

    # 构建后裁剪 PySide6 用不到的 Qt 库/翻译。
    # 说明：PyInstaller 的 additional-hooks-dir 是「追加」而非「覆盖」内置
    # hook-PySide6，内置 hook 会把整套 Qt6 DLL + 全部翻译收进来；无法靠 hook
    # 覆盖，故改为构建后按「保留 .pyd 的 DLL 依赖闭包」物理删除闭包外的文件。
    try:
        import trim_qt
        trim_qt.main()
    except Exception as e:
        print('trim_qt 执行失败（不影响主构建，可手动跑）:', e)


def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')


if __name__ == '__main__':
    install("Super_ADB_Main.py")
