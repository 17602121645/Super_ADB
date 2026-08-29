# -*- coding: utf-8 -*-
import py_compile, os
for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '工具', 'ADB工具.py')
    py_compile.compile(p, doraise=True)
    d = open(p, encoding='utf-8').read()
    false_count = d.count('self._用自研adb = False')
    true_count = d.count('self._用自研adb = True')
    print(f'{plat}: 语法OK | False={false_count} | True={true_count}')

spec = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\打包\Super_ADB.spec'
d = open(spec, encoding='utf-8').read()
has_config = '配置' in d and 'Super_ADB_Win\\配置' in d
print(f'spec 包含配置目录: {has_config}')
