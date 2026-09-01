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

a = Analysis(
    [_ENTRY],
    pathex=[_PROJECT_ROOT],
    binaries=[],
    datas=_datas,
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', 'tools.favorite_combobox'],
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
