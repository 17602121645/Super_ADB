# -*- coding: utf-8 -*-
import py_compile, os

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    py_compile.compile(p, doraise=True)
    d = open(p, encoding='utf-8').read()

    has_func = 'def _重命名输出文件夹' in d
    has_install_call = '_重命名输出文件夹(base_dir, name)' in d
    has_install1_call = '_重命名输出文件夹(_base, _name)' in d
    has_platform_win = 'Super_ADB_Win' in d
    has_platform_mac = 'Super_ADB_MAC' in d
    has_platform_linux = 'Super_ADB_Linux' in d

    print(f'{plat}: 语法OK | 公共函数={has_func} | install调用={has_install_call} | install1调用={has_install1_call}')
    print(f'  平台名: Win={has_platform_win} MAC={has_platform_mac} Linux={has_platform_linux}')
    print()
