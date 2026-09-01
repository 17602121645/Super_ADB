# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/guolai/A咪咕测试/Super_ADB_MAC/app/main.py'],
    pathex=['/Users/guolai/A咪咕测试/Super_ADB_MAC', '/Users/guolai/A咪咕测试/Super_ADB_MAC/ui'],
    binaries=[],
    datas=[('/Users/guolai/A咪咕测试/Super_ADB_MAC/resources', 'resources'), ('/Users/guolai/A咪咕测试/Super_ADB_MAC/vendor', 'vendor')],
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', 'tools.favorite_combobox', 'png_rc', 'ui.png_rc', 'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.rsa', 'cryptography.hazmat.primitives.asymmetric.padding', 'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.backends', 'tools.adb_native.mdns_discovery', 'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1'],
    hookspath=['/Users/guolai/A咪咕测试/Super_ADB_MAC/build_tools/hooks'],
    hooksconfig={},
    runtime_hooks=['/Users/guolai/A咪咕测试/Super_ADB_MAC/build_tools/hooks/runtime_pyzbar.py', '/Users/guolai/A咪咕测试/Super_ADB_MAC/build_tools/hooks/runtime_libusb.py'],
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
    icon=['/Users/guolai/A咪咕测试/Super_ADB_MAC/resources/Super_ADB.png'],
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
app = BUNDLE(
    coll,
    name='Super_ADB.app',
    icon='/Users/guolai/A咪咕测试/Super_ADB_MAC/resources/Super_ADB.png',
    bundle_identifier=None,
)
