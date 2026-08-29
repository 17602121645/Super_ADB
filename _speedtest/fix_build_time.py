# -*- coding: utf-8 -*-
"""修复精简打包exe.py：打包时间应在打包完成后写入，不应打包前修改用户源码配置。
三平台同步。
"""
import os, re

# 要删除的打包前写时间块（从注释到 except 结束）
OLD_BLOCK = """    # 打包前写入打包时间到配置文件（关于弹窗版本号读取此字段）
    import json as _json
    import time as _time
    _config_path = os.path.join(base_dir, '配置', 'Super_ADB配置.json')
    try:
        _cfg = {}
        if os.path.exists(_config_path):
            with open(_config_path, 'r', encoding='utf-8') as _f:
                _cfg = _json.load(_f)
        _build_ver = 'v' + _time.strftime('%Y.%m.%d')
        _cfg['打包时间'] = _build_ver
        _cfg['打包时间戳'] = _time.strftime('%Y-%m-%d %H:%M:%S')
        os.makedirs(os.path.dirname(_config_path), exist_ok=True)
        with open(_config_path, 'w', encoding='utf-8') as _f:
            _json.dump(_cfg, _f, ensure_ascii=False, indent=2)
        print(f'已写入打包版本号到配置文件: {_build_ver}')
    except Exception as _e:
        print(f'写入打包时间失败（不影响打包）: {_e}')

"""

# 打包完成后的旧复制逻辑
OLD_AFTER = """    # 打包完成后，把配置文件（含打包时间）复制到 exe 旁边的 配置/ 目录
    # 跨平台：Windows/Linux → dist/Super_ADB/配置/；macOS → dist/Super_ADB.app/Contents/MacOS/配置/
    # 关于对话框直接读取 exe 旁边的配置文件获取打包时间（不影响其他功能的用户配置路径）
    try:
        if sys.platform == 'darwin':
            # macOS: .app 包内可执行文件在 Contents/MacOS/ 下
            _dist_dir = os.path.join(base_dir, '打包', 'dist', f'{name}.app', 'Contents', 'MacOS')
        else:
            # Windows/Linux: 可执行文件在 dist/name/ 下
            _dist_dir = os.path.join(base_dir, '打包', 'dist', name)
        _dist_config_dir = os.path.join(_dist_dir, '配置')
        os.makedirs(_dist_config_dir, exist_ok=True)
        _dist_config_path = os.path.join(_dist_config_dir, 'Super_ADB配置.json')
        # 从源码目录的配置文件复制（已包含打包时间）
        if os.path.exists(_config_path):
            import shutil as _shutil
            _shutil.copy2(_config_path, _dist_config_path)
            print(f'已复制配置文件到 dist: {_dist_config_path}')
        else:
            # 源码目录配置文件不存在，直接写入打包时间
            with open(_dist_config_path, 'w', encoding='utf-8') as _f:
                _json.dump({'打包时间': _build_ver, '打包时间戳': _time.strftime('%Y-%m-%d %H:%M:%S')}, _f, ensure_ascii=False, indent=2)
            print(f'已创建 dist 配置文件: {_dist_config_path}')
    except Exception as _e:
        print(f'复制配置文件到 dist 失败（不影响打包）: {_e}')"""

# 新的打包完成后写时间逻辑（不碰用户源码配置）
NEW_AFTER = """    # 打包完成后，写入打包完成时间到 dist 的 配置/ 目录（不修改用户源码配置）
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


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if not os.path.isfile(p):
        print(f'{plat}: 文件不存在，跳过')
        continue
    d = open(p, encoding='utf-8').read()

    changed = False
    # 1. 删除打包前写时间块
    if OLD_BLOCK in d:
        d = d.replace(OLD_BLOCK, '', 1)
        changed = True
        print(f'{plat}: 已删除打包前写时间块')
    else:
        print(f'{plat}: 未找到打包前写时间块（可能已改过）')

    # 2. 替换打包后的复制逻辑为新的写时间逻辑
    if OLD_AFTER in d:
        d = d.replace(OLD_AFTER, NEW_AFTER, 1)
        changed = True
        print(f'{plat}: 已替换打包后写时间逻辑')
    else:
        print(f'{plat}: 未找到旧打包后复制逻辑（可能已改过）')

    if changed:
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已保存')
    print()
