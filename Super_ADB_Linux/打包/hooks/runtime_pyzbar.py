# -*- coding: UTF-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）：在 pyzbar 被导入前，把它加载 zbar DLL
的逻辑重定向到打包产物里我们确定存在的目录，避免冻结后
`zbar_library.load()` 用虚拟 `__file__` 找不到 libzbar-64.dll 而崩溃。

DLL 由 hook-pyzbar.py 的 collect_dynamic_libs('pyzbar') 收集到：
  onedir : <exe 目录>/_internal/pyzbar/libzbar-64.dll (+ libiconv.dll)
  onefile: <_MEIPASS>/_internal/pyzbar/...
"""
import os
import sys


def _patch_pyzbar_loader():
    try:
        import pyzbar.zbar_library as zl
    except Exception:
        return

    from ctypes import cdll
    from pathlib import Path

    # 候选 DLL 目录：优先打包产物里的确定位置，再退回 pyzbar 自带逻辑
    candidates = []
    if getattr(sys, 'frozen', False):
        base = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, '_internal', 'pyzbar'))
        candidates.append(os.path.join(base, 'pyzbar'))
    # 开发期：pyzbar 包目录（site-packages/pyzbar）
    try:
        candidates.append(str(Path(zl.__file__).parent))
    except Exception:
        pass
    candidates.append(str(Path('')))

    def load():
        fname, dependencies = zl._windows_fnames()
        last_err = None
        for directory in candidates:
            try:
                dep_path = os.path.join(directory, dependencies[0])
                lib_path = os.path.join(directory, fname)
                if not (os.path.exists(dep_path) and os.path.exists(lib_path)):
                    continue
                dep = cdll.LoadLibrary(dep_path)
                lib = cdll.LoadLibrary(lib_path)
                return lib, [dep]
            except OSError as e:
                last_err = e
                continue
        raise ImportError(
            'Unable to find zbar shared library (tried: %s)' % ', '.join(candidates)
        ) from last_err

    zl.load = load


_patch_pyzbar_loader()
