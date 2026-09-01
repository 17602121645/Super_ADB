# -*- coding: UTF-8 -*-
"""
PyInstaller 运行时钩子（runtime hook）：让冻结后的 pyusb 找到随包内置的
libusb-1.0.dylib，使 USB 直连（libusb 后端）在「系统未安装 libusb」的
目标机器上也能离线工作。

原理：
  pyusb 的 usb.backend.libusb1.get_backend() 默认调用
  ctypes.util.find_library('usb-1.0') 去系统路径找 libusb。打包环境里我们
  已经把 libusb-1.0.dylib 复制进了 .app/Contents/MacOS，于是这里在 pyusb
  被导入「之前」monkey-patch 掉 ctypes.util.find_library，对 usb-1.0 /
  libusb-1.0 / usb 这三个候选名直接返回随包 dylib 的绝对路径，
  pyusb 即可 ctypes.CDLL(绝对路径) 成功加载。

仅在 sys.frozen（PyInstaller 产物）下生效；开发环境（源码运行）完全不干预，
也不会影响 Windows 构建（Windows 上找不到 .dylib，自动 no-op）。
"""
import os
import sys


def _patch():
    if not getattr(sys, 'frozen', False):
        return

    # 随包 dylib 可能的位置（PyInstaller 把 .dylib 放进 .app 的位置不固定，
    # 不同版本/布局下可能落在 Contents/MacOS、Contents/Frameworks 或
    # Contents/Resources（符号链接->Frameworks），全部兜底搜索）：
    #   1) sys._MEIPASS（PyInstaller 运行目录，macOS .app 下即 Contents/MacOS）
    #   2) 可执行文件所在目录（.app 的 Contents/MacOS）
    #   3) Contents/Frameworks、4) Contents/Resources（exe 目录的上一级）
    search = []
    mp = getattr(sys, '_MEIPASS', '')
    if mp:
        search.append(mp)
    exe_dir = os.path.dirname(sys.executable)
    search.append(exe_dir)
    parent = os.path.dirname(exe_dir)  # Contents/
    search.append(os.path.join(parent, 'Frameworks'))
    search.append(os.path.join(parent, 'Resources'))

    dylib = None
    for d in search:
        cand = os.path.join(d, 'libusb-1.0.dylib')
        if os.path.isfile(cand):
            # 优先用可直接 ctypes 加载的（跳过指向自身的符号链接也无害，
            # 因为 CDLL 会解析到真实文件；这里只要存在一个真实 dylib 即可）
            dylib = cand
            break
    if dylib is None:
        return  # 没随包 dylib，交回系统查找（macOS 真机若装了 libusb 仍可用）

    import ctypes.util
    _orig = ctypes.util.find_library

    def _find(name):
        if name in ('usb-1.0', 'libusb-1.0', 'usb'):
            return dylib
        return _orig(name)

    ctypes.util.find_library = _find

    # 预加载：即便后续有代码按裸名 dlopen，也能命中已加载镜像。
    try:
        import ctypes
        ctypes.CDLL(dylib)
    except OSError:
        pass


_patch()
