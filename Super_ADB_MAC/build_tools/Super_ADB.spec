# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\app\\main.py'],
    pathex=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win'],
    binaries=[],
    datas=[('G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\resources', 'resources'), ('G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\vendor', 'vendor')],
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', 'tools.favorite_combobox'],
    hookspath=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\build_tools\\hooks'],
    hooksconfig={},
    runtime_hooks=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\build_tools\\hooks\\runtime_pyzbar.py'],
    excludes=['numpy', 'cv2', 'pyzbar.tests', 'PIL._avif', 'PIL._webp', 'PIL._imagingtk', 'unicodedata', 'zstandard', '_zstd', '_decimal', 'PIL._imagingcms', 'PIL._imagingmath'],
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
    icon=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\resources\\Super_ADB.png'],
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
