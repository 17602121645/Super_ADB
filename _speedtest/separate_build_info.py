# -*- coding: utf-8 -*-
"""打包时间单独写 打包信息.json，不再混入 Super_ADB配置.json。
修改：精简打包exe.py 的 _写入打包完成时间 + 关于对话框.py 的 _获取版本号。
三平台同步。
"""
import os

# ========== 1. 精简打包exe.py ==========
OLD_WRITE = """def _写入打包完成时间(base_dir, name='Super_ADB'):
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
        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')"""

NEW_WRITE = """def _写入打包完成时间(base_dir, name='Super_ADB'):
    \"\"\"打包完成后，把打包完成时间写入 dist 的 配置/打包信息.json（独立文件，不混入用户配置）。

    跨平台：Windows/Linux → dist/name/配置/；
    macOS → dist/name.app/Contents/MacOS/配置/。
    \"\"\"
    try:
        import json as _json
        import time as _time
        if sys.platform == 'darwin':
            _dist_dir = os.path.join(base_dir, '打包', 'dist', f'{name}.app', 'Contents', 'MacOS')
        else:
            _dist_dir = os.path.join(base_dir, '打包', 'dist', name)
        _dist_config_dir = os.path.join(_dist_dir, '配置')
        os.makedirs(_dist_config_dir, exist_ok=True)
        _dist_info_path = os.path.join(_dist_config_dir, '打包信息.json')
        _build_ver = 'v' + _time.strftime('%Y.%m.%d')
        _info = {
            '打包时间': _build_ver,
            '打包时间戳': _time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        with open(_dist_info_path, 'w', encoding='utf-8') as _f:
            _json.dump(_info, _f, ensure_ascii=False, indent=2)
        print(f'已写入打包完成时间到 dist: {_build_ver} ({_dist_info_path})')
    except Exception as _e:
        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')"""


# ========== 2. 关于对话框.py ==========
OLD_ABOUT = """def _获取版本号():
    \"\"\"从 exe 旁边的配置文件读取打包时间作为版本号，缺失时回退到硬编码 VERSION。

    跨平台路径：
      - Windows/Linux frozen: <exe_dir>/配置/Super_ADB配置.json
      - macOS frozen:          <.app>/Contents/MacOS/配置/Super_ADB配置.json
      - 源码模式:               项目根/配置/Super_ADB配置.json
    注意：不使用 加载json配置()，因为 macOS 上该函数指向 ~/Library/Application Support/，
    而打包时间配置在 .app 包内。
    \"\"\"
    import json as _json
    try:
        if getattr(sys, 'frozen', False):
            # 打包模式：配置文件在可执行文件旁边
            _base = os.path.dirname(sys.executable)
        else:
            # 源码模式：本文件位于 对话框/ 下，配置在项目根
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _cfg_path = os.path.join(_base, '配置', 'Super_ADB配置.json')
        if os.path.exists(_cfg_path):
            with open(_cfg_path, 'r', encoding='utf-8') as _f:
                cfg = _json.load(_f)
            if isinstance(cfg, dict) and cfg.get('打包时间'):
                return cfg['打包时间']
    except Exception:
        pass
    return VERSION"""

NEW_ABOUT = """def _获取版本号():
    \"\"\"从 exe 旁边的 打包信息.json 读取打包时间作为版本号，缺失时回退到硬编码 VERSION。

    跨平台路径：
      - Windows/Linux frozen: <exe_dir>/配置/打包信息.json
      - macOS frozen:          <.app>/Contents/MacOS/配置/打包信息.json
      - 源码模式:               项目根/配置/打包信息.json
    注意：不使用 加载json配置()，因为 macOS 上该函数指向 ~/Library/Application Support/，
    而打包信息在 .app 包内。
    \"\"\"
    import json as _json
    try:
        if getattr(sys, 'frozen', False):
            # 打包模式：配置文件在可执行文件旁边
            _base = os.path.dirname(sys.executable)
        else:
            # 源码模式：本文件位于 对话框/ 下，配置在项目根
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _info_path = os.path.join(_base, '配置', '打包信息.json')
        if os.path.exists(_info_path):
            with open(_info_path, 'r', encoding='utf-8') as _f:
                info = _json.load(_f)
            if isinstance(info, dict) and info.get('打包时间'):
                return info['打包时间']
    except Exception:
        pass
    return VERSION"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    print(f'=== {plat} ===')

    # 1. 修改精简打包exe.py
    p1 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if os.path.isfile(p1):
        d = open(p1, encoding='utf-8').read()
        if OLD_WRITE in d:
            d = d.replace(OLD_WRITE, NEW_WRITE, 1)
            open(p1, 'w', encoding='utf-8', newline='').write(d)
            print(f'  精简打包exe.py: 已改为写 打包信息.json')
        elif '打包信息.json' in d:
            print(f'  精简打包exe.py: 已改过，跳过')
        else:
            print(f'  精简打包exe.py: 未找到旧块')
    else:
        print(f'  精简打包exe.py: 不存在')

    # 2. 修改关于对话框.py
    p2 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '对话框', '关于对话框.py')
    if os.path.isfile(p2):
        d = open(p2, encoding='utf-8').read()
        if OLD_ABOUT in d:
            d = d.replace(OLD_ABOUT, NEW_ABOUT, 1)
            open(p2, 'w', encoding='utf-8', newline='').write(d)
            print(f'  关于对话框.py: 已改为读 打包信息.json')
        elif '打包信息.json' in d:
            print(f'  关于对话框.py: 已改过，跳过')
        else:
            print(f'  关于对话框.py: 未找到旧块')
    else:
        print(f'  关于对话框.py: 不存在')
    print()
