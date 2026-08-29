# -*- coding: utf-8 -*-
"""重构精简打包exe.py：提取_写入打包完成时间公共函数，install和install1都调用。
三平台同步。
"""
import os, re

# install 函数里现有的写时间块（从注释到 except 结束）
OLD_INSTALL_BLOCK = """    # 打包完成后，写入打包完成时间到 dist 的 配置/ 目录（不修改用户源码配置）
    # 跨平台：Windows/Linux → dist/Super_ADB/配置/；macOS → dist/Super_ADB.app/Contents/MacOS/配置/
    # 关于对话框直接读取 exe 旁边的配置文件获取打包时间
    try:
        import json as _json
        import time as _time
        import shutil as _shutil
        if sys.platform == 'darwin':
            _dist_dir = os.path.join(base_dir, '打包', 'dist', f'{name}.app', 'Contents', 'MacOS')
        else:
            _dist_dir = os.path.join(base_dir, '打包', 'dist', name)
        _dist_config_dir = os.path.join(_dist_dir, '配置')
        os.makedirs(_dist_config_dir, exist_ok=True)
        _dist_config_path = os.path.join(_dist_config_dir, 'Super_ADB配置.json')
        _src_config_path = os.path.join(base_dir, '配置', 'Super_ADB配置.json')
        # 优先从源码配置复制（保留用户的 adb 模式等配置），再更新打包时间
        if os.path.exists(_src_config_path):
            _shutil.copy2(_src_config_path, _dist_config_path)
        # 读取 dist 配置（可能刚复制，也可能已存在），更新打包完成时间
        _cfg = {}
        if os.path.exists(_dist_config_path):
            with open(_dist_config_path, 'r', encoding='utf-8') as _f:
                _cfg = _json.load(_f)
        _build_ver = 'v' + _time.strftime('%Y.%m.%d')
        _cfg['打包时间'] = _build_ver
        _cfg['打包时间戳'] = _time.strftime('%Y-%m-%d %H:%M:%S')
        with open(_dist_config_path, 'w', encoding='utf-8') as _f:
            _json.dump(_cfg, _f, ensure_ascii=False, indent=2)
        print(f'已写入打包完成时间到 dist 配置: {_build_ver} ({_dist_config_path})')
    except Exception as _e:
        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')"""

# 替换为调用公共函数
NEW_INSTALL_CALL = """    # 打包完成后，写入打包完成时间到 dist 配置
    _写入打包完成时间(base_dir, name)"""

# 公共函数定义（放在 install 函数之前）
PUBLIC_FUNC = """
def _写入打包完成时间(base_dir, name='Super_ADB'):
    \"\"\"打包完成后，把打包完成时间写入 dist 的 配置/Super_ADB配置.json。

    不修改用户源码配置：优先从源码配置复制（保留 adb 模式等用户配置），
    再更新打包时间字段。跨平台：Windows/Linux → dist/name/配置/；
    macOS → dist/name.app/Contents/MacOS/配置/。
    \"\"\"
    try:
        import json as _json
        import time as _time
        import shutil as _shutil
        if sys.platform == 'darwin':
            _dist_dir = os.path.join(base_dir, '打包', 'dist', f'{name}.app', 'Contents', 'MacOS')
        else:
            _dist_dir = os.path.join(base_dir, '打包', 'dist', name)
        _dist_config_dir = os.path.join(_dist_dir, '配置')
        os.makedirs(_dist_config_dir, exist_ok=True)
        _dist_config_path = os.path.join(_dist_config_dir, 'Super_ADB配置.json')
        _src_config_path = os.path.join(base_dir, '配置', 'Super_ADB配置.json')
        if os.path.exists(_src_config_path):
            _shutil.copy2(_src_config_path, _dist_config_path)
        _cfg = {}
        if os.path.exists(_dist_config_path):
            with open(_dist_config_path, 'r', encoding='utf-8') as _f:
                _cfg = _json.load(_f)
        _build_ver = 'v' + _time.strftime('%Y.%m.%d')
        _cfg['打包时间'] = _build_ver
        _cfg['打包时间戳'] = _time.strftime('%Y-%m-%d %H:%M:%S')
        with open(_dist_config_path, 'w', encoding='utf-8') as _f:
            _json.dump(_cfg, _f, ensure_ascii=False, indent=2)
        print(f'已写入打包完成时间到 dist 配置: {_build_ver} ({_dist_config_path})')
    except Exception as _e:
        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')


"""

# install1 的旧代码
OLD_INSTALL1 = """def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')"""

# install1 的新代码：打包完成后写时间
NEW_INSTALL1 = """def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')
    # 打包完成后写入时间配置（从参数解析 -n 应用名，默认 Super_ADB）
    _here = os.path.dirname(os.path.abspath(__file__))
    _base = os.path.dirname(_here)
    _m = re.search(r'-n\\s+(\\S+)', s)
    _name = _m.group(1) if _m else 'Super_ADB'
    _写入打包完成时间(_base, _name)"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if not os.path.isfile(p):
        print(f'{plat}: 文件不存在，跳过')
        continue
    d = open(p, encoding='utf-8').read()

    changed = False

    # 1. 在 install 函数前插入公共函数（如果还没有）
    if 'def _写入打包完成时间' not in d:
        # 找 def install(main): 的位置，在它前面插入
        idx = d.find('def install(main):')
        if idx > 0:
            d = d[:idx] + PUBLIC_FUNC + d[idx:]
            changed = True
            print(f'{plat}: 已插入公共函数 _写入打包完成时间')
        else:
            print(f'{plat}: 未找到 def install(main)')
    else:
        print(f'{plat}: 公共函数已存在')

    # 2. 替换 install 函数里的内联写时间块为函数调用
    if OLD_INSTALL_BLOCK in d:
        d = d.replace(OLD_INSTALL_BLOCK, NEW_INSTALL_CALL, 1)
        changed = True
        print(f'{plat}: 已替换 install 里的内联写时间为函数调用')
    else:
        print(f'{plat}: 未找到 install 里的旧写时间块（可能已替换）')

    # 3. 替换 install1 函数
    if OLD_INSTALL1 in d:
        d = d.replace(OLD_INSTALL1, NEW_INSTALL1, 1)
        changed = True
        print(f'{plat}: 已更新 install1，打包完成后写时间')
    else:
        print(f'{plat}: 未找到旧 install1（可能已更新）')

    if changed:
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已保存')
    print()
