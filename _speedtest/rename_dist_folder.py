# -*- coding: utf-8 -*-
"""打包输出文件夹按平台命名：Super_ADB_Win / Super_ADB_MAC / Super_ADB_Linux。
新增 _重命名输出文件夹 公共函数，install 和 install1 都调用。
三平台同步。
"""
import os

# 公共函数定义（放在 _写入打包完成时间 之后）
PUBLIC_RENAME_FUNC = """

def _重命名输出文件夹(base_dir, name='Super_ADB'):
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

# 在 _写入打包完成时间 函数结束后插入公共函数
# 找 _写入打包完成时间 的结束位置（下一个 def 或 if __name__）
INSERT_AFTER = """        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')


# ----------------------------------------------------------------------"""

INSERT_WITH = """        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')

""" + PUBLIC_RENAME_FUNC + """# ----------------------------------------------------------------------"""

# install 函数里：裁剪之后调用重命名
OLD_INSTALL_END = """    try:
        from 打包 import 裁剪_qt
        裁剪_qt.main()
    except Exception as e:
        print('裁剪_qt.py 执行失败（不影响主构建，可手动跑）:', e)


def install1(s):"""

NEW_INSTALL_END = """    try:
        from 打包 import 裁剪_qt
        裁剪_qt.main()
    except Exception as e:
        print('裁剪_qt.py 执行失败（不影响主构建，可手动跑）:', e)

    # 打包完成后，输出文件夹按平台命名（Super_ADB_Win / _MAC / _Linux）
    _重命名输出文件夹(base_dir, name)


def install1(s):"""

# install1 函数里：_写入打包完成时间 之后调用重命名
OLD_INSTALL1_END = """    _name = _m.group(1) if _m else 'Super_ADB'
    _写入打包完成时间(_base, _name)


if __name__ == '__main__':"""

NEW_INSTALL1_END = """    _name = _m.group(1) if _m else 'Super_ADB'
    _写入打包完成时间(_base, _name)
    _重命名输出文件夹(_base, _name)


if __name__ == '__main__':"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if not os.path.isfile(p):
        print(f'{plat}: 文件不存在')
        continue
    d = open(p, encoding='utf-8').read()
    changed = False

    # 1. 插入公共函数
    if 'def _重命名输出文件夹' not in d:
        if INSERT_AFTER in d:
            d = d.replace(INSERT_AFTER, INSERT_WITH, 1)
            changed = True
            print(f'{plat}: 已插入 _重命名输出文件夹 公共函数')
        else:
            print(f'{plat}: 未找到公共函数插入点')
    else:
        print(f'{plat}: 公共函数已存在')

    # 2. install 里裁剪后调用
    if OLD_INSTALL_END in d:
        d = d.replace(OLD_INSTALL_END, NEW_INSTALL_END, 1)
        changed = True
        print(f'{plat}: install 里已加裁剪后重命名')
    elif '_重命名输出文件夹(base_dir, name)' in d:
        print(f'{plat}: install 里重命名已存在')
    else:
        print(f'{plat}: 未找到 install 结束块')

    # 3. install1 里写时间后调用
    if OLD_INSTALL1_END in d:
        d = d.replace(OLD_INSTALL1_END, NEW_INSTALL1_END, 1)
        changed = True
        print(f'{plat}: install1 里已加重命名')
    elif '_重命名输出文件夹(_base, _name)' in d:
        print(f'{plat}: install1 里重命名已存在')
    else:
        print(f'{plat}: 未找到 install1 结束块')

    if changed:
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已保存')
    print()
