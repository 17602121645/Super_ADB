# -*- coding: UTF-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）：冻结环境下把源码里对「子包模块」的
裸导入（top-level import）重定向到其包限定名，等价于开发期把 工具/、项目UI/
等目录加入 sys.path 的效果。

背景
----
开发期 项目启动入口/Super_ADB_主入口.py 会执行：
    sys.path.insert(0, <root>/工具)
    sys.path.insert(0, <root>/项目UI)
于是源码里大量「裸导入」可以工作，例如：
    import png_rc                 # -> 项目UI/png_rc.py
    import ADB工具                # -> 工具/ADB工具.py
    from 收藏下拉框 import FavComboBox   # -> 工具/收藏下拉框.py

但 PyInstaller 把纯 Python 模块编进 PYZ 归档（包名 项目UI.png_rc / 工具.ADB工具…），
冻结后 <root>/工具、<root>/项目UI 并不是磁盘上的真实目录，sys.path 注入失效，
裸导入便报 ModuleNotFoundError（首杀是 项目UI/Super_ADB.py 的 import png_rc）。

本钩子在冻结启动早期运行，给每个裸名在 sys.modules 里建立「别名 -> 已收集的
包限定模块」的映射，使 import png_rc / import ADB工具 等都命中同一个模块对象，
既修复运行，又避免「同一模块被当成两个对象」导致的 isinstance 不一致。
开发环境（非 frozen）完全不干预。
"""
import importlib
import sys

if getattr(sys, 'frozen', False):
    # 裸名 -> 实际被 PyInstaller 收集的包限定模块
    _ALIASES = {
        'png_rc': '项目UI.png_rc',
        '收藏下拉框': '工具.收藏下拉框',
        'ADB工具': '工具.ADB工具',
        'JSON工具对话框': '对话框.JSON工具对话框',
        '哈希校验对话框': '对话框.哈希校验对话框',
        '局域网扫描对话框': '对话框.局域网扫描对话框',
        '环境配置对话框': '对话框.环境配置对话框',
    }
    for _name, _real in _ALIASES.items():
        if _name in sys.modules:
            continue
        try:
            _mod = importlib.import_module(_real)
            sys.modules[_name] = _mod
        except Exception as _e:  # 个别模块未收集也不阻断启动
            print('[runtime_pkg_alias][WARN] 别名失败 %s -> %s: %s'
                  % (_name, _real, _e))
