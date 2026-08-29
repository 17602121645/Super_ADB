# -*- coding: utf-8 -*-
import py_compile, os, re

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    py_compile.compile(p, doraise=True)
    d = open(p, encoding='utf-8').read()

    # 检查打包前是否还有写时间逻辑
    has_before = '打包前写入打包时间' in d or '打包前写入' in d
    # 检查打包后是否有写时间逻辑
    has_after = '打包完成后，写入打包完成时间' in d
    # 检查是否还有引用已删除的 _config_path（打包前定义的）
    # 注意：打包后逻辑里用的是 _src_config_path 和 _dist_config_path，不是 _config_path
    has_old_config_path = bool(re.search(r'[^_]_config_path[^_]', d)) and '_src_config_path' not in d and '_dist_config_path' not in d
    # 检查 os.system(cmd) 之后是否有写时间逻辑
    cmd_pos = d.find('os.system(cmd)')
    after_cmd = d[cmd_pos:cmd_pos+2000] if cmd_pos > 0 else ''
    has_write_after = '打包完成时间' in after_cmd or '_cfg[\'打包时间\']' in after_cmd

    print(f'{plat}: 语法OK | 打包前写时间={has_before} | 打包后写时间={has_after} | os.system后写时间={has_write_after}')
    # 打印打包后逻辑的关键部分
    i = d.find('打包完成后，写入打包完成时间')
    if i > 0:
        print(d[i:i+300])
    print()
