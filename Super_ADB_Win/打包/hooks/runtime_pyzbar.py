# -*- coding: UTF-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）：在 pyzbar 被导入前，把它加载 zbar 共享库
的逻辑重定向到打包产物里我们确定存在的目录，避免冻结后
`zbar_library.load()` 用虚拟 `__file__` 找不到 libzbar 而崩溃。

共享库由 hook-pyzbar.py 的 collect_dynamic_libs('pyzbar') 收集到：
  onedir(Windows) : <exe 目录>/_internal/pyzbar/libzbar-64.dll (+ libiconv.dll)
  onefile         : <_MEIPASS>/_internal/pyzbar/...
  macOS .app      : <app>/Contents/Frameworks/pyzbar/libzbar.dylib
  Linux onedir    : <dist>/Super_ADB/_internal/pyzbar/libzbar.so.0

跨平台说明：
  - 按平台选择 pyzbar 的 fnames 函数（_osx_fnames / _linux_fnames /
    _windows_fnames），不再像旧版那样只按 Windows 的 .dll 名称查找
    （否则 macOS/Linux 打包版扫码会报 Unable to find zbar shared library）。
  - dependencies 兼容 str 与 list 两种返回值（不同 pyzbar 版本形态不一）。
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

    def _fnames():
        """按平台返回 (lib文件名, 依赖列表)。dependencies 兼容 str / list。"""
        if sys.platform == 'darwin':
            fn = getattr(zl, '_osx_fnames', None) or zl._windows_fnames
            fname, deps = fn()
        elif sys.platform.startswith('linux'):
            fn = getattr(zl, '_linux_fnames', None) or zl._windows_fnames
            fname, deps = fn()
        else:
            fname, deps = zl._windows_fnames()
        if isinstance(deps, str):
            deps = [deps] if deps else []
        return fname, deps

    def load():
        fname, dependencies = _fnames()
        last_err = None
        for directory in candidates:
            try:
                dep_paths = [os.path.join(directory, d) for d in dependencies]
                lib_path = os.path.join(directory, fname)
                if not os.path.exists(lib_path):
                    continue
                if any(not os.path.exists(p) for p in dep_paths):
                    continue
                libs = [cdll.LoadLibrary(p) for p in dep_paths]
                lib = cdll.LoadLibrary(lib_path)
                return lib, libs
            except OSError as e:
                last_err = e
                continue
        raise ImportError(
            'Unable to find zbar shared library (tried: %s)' % ', '.join(candidates)
        ) from last_err

    zl.load = load


_patch_pyzbar_loader()
