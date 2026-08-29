# -*- coding: utf-8 -*-
"""修正：在 _写入打包完成时间 后、def install 前插入 _重命名输出文件夹 公共函数。
三平台同步。
"""
import os

PUBLIC_RENAME_FUNC = """def _重命名输出文件夹(base_dir, name='Super_ADB'):
    \"\"\"打包完成后，把 dist/name 重命名为 dist/Super_ADB_<平台>（按平台命名）。

    Windows/Linux: dist/Super_ADB/ → dist/Super_ADB_Win/ (或 _Linux)
    macOS:         dist/Super_ADB.app → dist/Super_ADB_MAC.app
    目标已存在时先删除。
    \"\"\"
    import shutil as _shutil
    _platform_suffix = {'win32': 'Win', 'darwin': 'MAC', 'linux': 'Linux'}.get(sys.platform, sys.platform)
    _target_name = f'Super_ADB_{_platform_suffix}'
    _dist_root = os.path.join(base_dir, '打包', 'dist')
    if sys.platform == 'darwin':
        _src = os.path.join(_dist_root, f'{name}.app')
        _dst = os.path.join(_dist_root, f'{_target_name}.app')
    else:
        _src = os.path.join(_dist_root, name)
        _dst = os.path.join(_dist_root, _target_name)
    if not os.path.exists(_src):
        print(f'重命名跳过：源不存在 {_src}')
        return
    if os.path.abspath(_src) == os.path.abspath(_dst):
        return
    if os.path.exists(_dst):
        _shutil.rmtree(_dst, ignore_errors=True)
    os.rename(_src, _dst)
    print(f'已重命名输出文件夹: {os.path.basename(_src)} → {os.path.basename(_dst)}')


"""

# 插入点：_写入打包完成时间 结束后、def install 前
OLD_INSERT = """        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')


def install(main):"""

NEW_INSERT = """        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')


""" + PUBLIC_RENAME_FUNC + """def install(main):"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if not os.path.isfile(p):
        print(f'{plat}: 文件不存在')
        continue
    d = open(p, encoding='utf-8').read()

    if 'def _重命名输出文件夹' in d:
        print(f'{plat}: 公共函数已存在，跳过')
        continue

    if OLD_INSERT in d:
        d = d.replace(OLD_INSERT, NEW_INSERT, 1)
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已插入 _重命名输出文件夹 公共函数')
    else:
        print(f'{plat}: 未找到插入点')
    print()
