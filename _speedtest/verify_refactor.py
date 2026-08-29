# -*- coding: utf-8 -*-
import py_compile, os

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    py_compile.compile(p, doraise=True)
    d = open(p, encoding='utf-8').read()

    has_public_func = 'def _写入打包完成时间' in d
    has_install_call = '_写入打包完成时间(base_dir, name)' in d
    has_install1_call = '_写入打包完成时间(_base, _name)' in d
    has_inline = '打包完成后，写入打包完成时间到 dist' in d

    print(f'{plat}: 语法OK | 公共函数={has_public_func} | install调用={has_install_call} | install1调用={has_install1_call} | 内联残留={has_inline}')

    # 打印 install1 函数
    i = d.find('def install1')
    if i > 0:
        print(d[i:i+350])
    print()
