# -*- coding: utf-8 -*-
import py_compile, os, json

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    # 语法检查
    p1 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    p2 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '对话框', '关于对话框.py')
    py_compile.compile(p1, doraise=True)
    py_compile.compile(p2, doraise=True)

    d1 = open(p1, encoding='utf-8').read()
    d2 = open(p2, encoding='utf-8').read()

    write_info = '打包信息.json' in d1
    write_user_cfg = "Super_ADB配置.json" in d1 and '_dist_config_path' in d1
    read_info = '打包信息.json' in d2
    read_user_cfg = 'Super_ADB配置.json' in d2

    print(f'{plat}: 语法OK | 打包脚本写打包信息.json={write_info} | 打包脚本仍写用户配置={write_user_cfg} | 关于对话框读打包信息.json={read_info} | 关于对话框仍读用户配置={read_user_cfg}')

# 检查源码配置文件里是否有打包时间字段
print()
for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    cfg_path = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '配置', 'Super_ADB配置.json')
    if os.path.exists(cfg_path):
        cfg = json.load(open(cfg_path, encoding='utf-8'))
        has_build_time = '打包时间' in cfg or '打包时间戳' in cfg
        print(f'{plat} 配置文件: 含打包时间字段={has_build_time}')
        if has_build_time:
            print(f'  打包时间={cfg.get("打包时间")}, 打包时间戳={cfg.get("打包时间戳")}')
    else:
        print(f'{plat} 配置文件: 不存在')
