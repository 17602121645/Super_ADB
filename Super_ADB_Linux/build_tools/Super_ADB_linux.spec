# -*- mode: python ; coding: utf-8 -*-
"""
Super_ADB Linux 打包配置
========================
使用相对路径，基于本 spec 文件所在目录（build_tools/）的上一级（项目根）。
生成 onedir 模式的可执行文件（dist/Super_ADB/Super_ADB）。

用法：
    cd 项目根目录
    pyinstaller build_tools/Super_ADB_linux.spec

或使用一键脚本：
    bash build_tools/build_linux.sh
"""

import os
import sys

# 项目根目录 = spec 文件所在目录的上一级
_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
_PROJECT_ROOT = os.path.dirname(_SPEC_DIR)

# 入口脚本
_ENTRY = os.path.join(_PROJECT_ROOT, 'app', 'main.py')

# 资源目录
_RES_DIR = os.path.join(_PROJECT_ROOT, 'resources')
_EXT_DIR = os.path.join(_PROJECT_ROOT, 'vendor')

# 图标：Linux 用 .png
_ICON = os.path.join(_PROJECT_ROOT, 'resources', 'Super_ADB.png')

# datas：资源 + 外部扩展（Linux 用 ':' 作为 SRC:DST 分隔符）
_datas = [
    (_RES_DIR, 'resources'),
]
if os.path.isdir(_EXT_DIR):
    _datas.append((_EXT_DIR, 'vendor'))

# ★ 与 Super_ADB_MAC/build_tools/Super_ADB_mac.spec、Super_ADB_Win/build_tools/Super_ADB.spec
# 保持同步，缺一不可。此前本文件只声明了前 6 项，导致：
#   - 缺 png_rc / ui.png_rc → ui/Super_ADB.py:26 的裸 import png_rc 在冻结后找不到模块，
#     打包产物启动即 ModuleNotFoundError（CI 冒烟测试抓到的就是这个）
#   - 缺 cryptography.* → adb pair 配对客户端顶层 import 崩溃，手机扫码后一直转圈
#   - 缺 usb.* → 自研 adb 的 USB 通道不可用
#   - 缺 mdns_discovery → 无线调试找不到 _adb-tls-connect 真实调试端口
_hiddenimports = [
    'segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', 'tools.favorite_combobox',
    'png_rc', 'ui.png_rc',
    'cryptography', 'cryptography.hazmat',
    'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.asymmetric.rsa',
    'cryptography.hazmat.primitives.asymmetric.padding',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.backends',
    'tools.adb_native.mdns_discovery',
    'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1',
]
try:
    import brotli  # noqa: F401
    _hiddenimports.append('brotli')
except ImportError:
    pass

a = Analysis(
    [_ENTRY],
    # ui 目录必须入 pathex：ui/Super_ADB.py 是 Qt Designer 产物，里面是裸的
    # `import png_rc`，而 png_rc.py 位于 ui/ 下。只在 hiddenimports 里写
    # 'png_rc' 不够 —— PyInstaller 得先能按这个名字找到它。
    # Windows 的 spec 用的同样是这个办法；mac 则靠 hooks/runtime_pkg_alias.py。
    pathex=[_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, 'ui')],
    binaries=[],
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[os.path.join(_SPEC_DIR, 'hooks')],
    hooksconfig={},
    runtime_hooks=[os.path.join(_SPEC_DIR, 'hooks', 'runtime_pyzbar.py')],
    excludes=['numpy', 'cv2', 'pyzbar.tests', 'PIL._avif', 'PIL._webp',
              'PIL._imagingtk', 'unicodedata', 'zstandard', '_zstd', '_decimal',
              'PIL._imagingcms', 'PIL._imagingmath'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Super_ADB',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[_ICON],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Super_ADB',
)
