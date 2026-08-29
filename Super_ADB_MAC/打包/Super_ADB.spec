# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\项目启动入口\\Super_ADB_主入口.py'],
    pathex=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win'],
    binaries=[],
    datas=[('G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\资源', '资源'), ('G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\外部扩展', '/外部扩展')],
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', '工具.收藏下拉框'],
    hookspath=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\打包\\hooks'],
    hooksconfig={},
    runtime_hooks=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\打包\\hooks\\runtime_pyzbar.py'],
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
    icon=['G:\\Python\\jcspy\\Super_ADB\\Super_ADB_Win\\资源\\Super_ADB.png'],
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
