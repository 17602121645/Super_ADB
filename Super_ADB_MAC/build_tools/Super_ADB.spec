# -*- mode: python ; coding: utf-8 -*-
import os

# 路径全部相对 spec 文件解析，便于在任意机器 / CI 上构建。
# （原先是 G:\Python\jcspy\... 的硬编码绝对路径，换台机器就跑不了。）
_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
_PROJECT_ROOT = os.path.dirname(_SPEC_DIR)
_ENTRY = os.path.join(_PROJECT_ROOT, 'app', 'main.py')
_RES_DIR = os.path.join(_PROJECT_ROOT, 'resources')
_EXT_DIR = os.path.join(_PROJECT_ROOT, 'vendor')
_ICON = os.path.join(_RES_DIR, 'Super_ADB.png')

_datas = [(_RES_DIR, 'resources')]
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
    excludes=['numpy', 'cv2', 'pyzbar.tests', 'PIL._avif', 'PIL._webp', 'PIL._imagingtk', 'zstandard', '_zstd', '_decimal', 'PIL._imagingcms', 'PIL._imagingmath'],
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
