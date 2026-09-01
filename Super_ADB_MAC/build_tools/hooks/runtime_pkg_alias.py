# -*- coding: UTF-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）：冻结环境下把源码里对「子包模块」的
裸导入（top-level import）重定向到其包限定名，等价于开发期把 tools/、ui/
等目录加入 sys.path 的效果。

背景
----
开发期 app/main.py 会执行：
    sys.path.insert(0, <root>/tools)
    sys.path.insert(0, <root>/ui)
于是源码里大量「裸导入」可以工作，例如：
    import png_rc                 # -> ui/png_rc.py
    import adb_tools                # -> tools/adb_tools.py
    from favorite_combobox import FavComboBox   # -> tools/favorite_combobox.py

但 PyInstaller 把纯 Python 模块编进 PYZ 归档（包名 ui.png_rc / tools.adb_tools…），
冻结后 <root>/tools、<root>/ui 并不是磁盘上的真实目录，sys.path 注入失效，
裸导入便报 ModuleNotFoundError（首杀是 ui/Super_ADB.py 的 import png_rc）。

本钩子在冻结启动早期运行，给每个裸名在 sys.modules 里建立「别名 -> 已收集的
包限定模块」的映射，使 import png_rc / import adb_tools 等都命中同一个模块对象，
既修复运行，又避免「同一模块被当成两个对象」导致的 isinstance 不一致。
开发环境（非 frozen）完全不干预。
"""
import importlib
import sys

if getattr(sys, 'frozen', False):
    # 裸名 -> 实际被 PyInstaller 收集的包限定模块
    _ALIASES = {
        'png_rc': 'ui.png_rc',
        'favorite_combobox': 'tools.favorite_combobox',
        'adb_tools': 'tools.adb_tools',
        'json_tool_dialog': 'dialogs.json_tool_dialog',
        'hash_check_dialog': 'dialogs.hash_check_dialog',
        'lan_scan_dialog': 'dialogs.lan_scan_dialog',
        'env_config_dialog': 'dialogs.env_config_dialog',
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
