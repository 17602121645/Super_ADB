# -*- coding: utf-8 -*-
import py_compile, os, re

# 1. 语法检查
for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '工具', 'ADB工具.py')
    py_compile.compile(p, doraise=True)
    d = open(p, encoding='utf-8').read()
    # 统计默认值
    init_false = d.count('self._用自研adb = False')
    init_true = d.count('self._用自研adb = True')
    print(f'{plat}: 语法OK | _用自研adb=False 出现{init_false}次 | _用自研adb=True 出现{init_true}次')

# 2. 检查刷新设置方法
print()
p = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\工具\ADB工具.py'
d = open(p, encoding='utf-8').read()
i = d.find('def 刷新设置')
print('=== 刷新设置 ===')
print(d[i:i+600])
