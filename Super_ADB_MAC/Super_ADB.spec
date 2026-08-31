# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/项目启动入口/Super_ADB_主入口.py'],
    pathex=['/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC', '/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/项目UI'],
    binaries=[],
    datas=[('/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/资源', '资源'), ('/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/外部扩展', '外部扩展')],
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', '工具.收藏下拉框', 'png_rc', '项目UI.png_rc', 'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.rsa', 'cryptography.hazmat.primitives.asymmetric.padding', 'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.backends', '工具.自研adb.mdns发现', 'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1'],
    hookspath=['/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/打包/hooks'],
    hooksconfig={},
    runtime_hooks=['/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/打包/hooks/runtime_pyzbar.py'],
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
    icon=['/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/资源/Super_ADB.png'],
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
    icon='/Users/guolai/A咪咕测试/咪咕车载/Super_ADB_MAC/资源/Super_ADB.png',
    bundle_identifier=None,
)
