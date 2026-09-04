# -*- mode: python ; coding: utf-8 -*-
import os

# 路径全部相对 spec 文件解析，便于在任意机器 / CI 上构建。
# （原先是 G:\Python\jcspy\Super_ADB\... 的硬编码绝对路径，换台机器/CI 就跑不了。）
_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
_PROJECT_ROOT = os.path.dirname(_SPEC_DIR)
_ENTRY = os.path.join(_PROJECT_ROOT, '项目启动入口', 'Super_ADB_主入口.py')
_RES_DIR = os.path.join(_PROJECT_ROOT, '资源')
_EXT_DIR = os.path.join(_PROJECT_ROOT, '外部扩展')
_ICON = os.path.join(_RES_DIR, 'Super_ADB.png')

_datas = [(_RES_DIR, '资源')]
if os.path.isdir(_EXT_DIR):
    _datas.append((_EXT_DIR, '外部扩展'))

a = Analysis(
    [_ENTRY],
    pathex=[_PROJECT_ROOT, os.path.join(_PROJECT_ROOT, '项目UI')],
    binaries=[],
    datas=_datas,
    hiddenimports=['segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', '工具.收藏下拉框', 'png_rc', '项目UI.png_rc', 'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.rsa', 'cryptography.hazmat.primitives.asymmetric.padding', 'cryptography.hazmat.primitives.serialization', 'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.backends', '工具.自研adb.mdns发现', 'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1', 'brotli'],
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

# ========== 构建后裁剪 Qt 死重（原 精简打包exe.py 末尾逻辑，下沉到 spec 自包含） ==========
# 说明：CI 直接执行本 spec、不会走 精简打包exe.py，原 裁剪_qt.py 一次都没跑，
# 闭包外 Qt6 DLL / opengl32sw / FFmpeg / OpenSSL / 孤儿插件 / 多余翻译全部进包
# （Windows 产物曾达 79MB）。本地直接跑本 spec 也能自动精简。
import sys
sys.path.insert(0, _SPEC_DIR)
try:
    import 裁剪_qt
    裁剪_qt.main()
except Exception as _e:
    print('裁剪_qt.py 执行失败（不影响主构建，可手动跑）:', _e)
