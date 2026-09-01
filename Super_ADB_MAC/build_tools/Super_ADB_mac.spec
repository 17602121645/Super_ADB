# -*- mode: python ; coding: utf-8 -*-
"""
Super_ADB macOS 打包配置（唯一真相源）
======================================
本 spec 是 macOS 构建的唯一配置来源，build_mac_zip.sh 在装好依赖后会直接执行：
    pyinstaller --distpath build_tools/dist --workpath build_tools/build build_tools/Super_ADB_mac.spec

它在 PyInstaller 阶段一次性完成：
  - 入口/隐藏依赖声明（含自研 ADB 的 cryptography 配对链路、pyusb、zeroconf 等）
  - 资源与 外部扩展 随包
  - libusb 运行时钩子 + libusb-1.0.dylib 随包（USB 直连离线可用，目标机无需装 libusb）

相对路径基于本 spec 文件所在目录（build_tools/）的上一级（项目根）。
生成 .app 应用包，产物名固定为 Super_ADB_MAC（与一键脚本 / ZIP 名一致）。
"""

import os
import sys
import platform

# 项目根目录 = spec 文件所在目录的上一级
_SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
_PROJECT_ROOT = os.path.dirname(_SPEC_DIR)

# 入口脚本
_ENTRY = os.path.join(_PROJECT_ROOT, 'app', 'main.py')

# 资源目录
_RES_DIR = os.path.join(_PROJECT_ROOT, 'resources')
_EXT_DIR = os.path.join(_PROJECT_ROOT, 'vendor')

# 图标：优先 .icns，其次 .png
_ICON_ICNS = os.path.join(_PROJECT_ROOT, 'resources', 'Super_ADB.icns')
_ICON_PNG = os.path.join(_PROJECT_ROOT, 'resources', 'Super_ADB.png')
_ICON = _ICON_ICNS if os.path.exists(_ICON_ICNS) else _ICON_PNG

# ── 随包 libusb dylib（来自 PyPI 'libusb' 包）───────────────────────────────
# 按本机架构挑选 macOS 的 libusb-1.0.dylib，作为 binary 随包进入
# .app/Contents/MacOS；配合 hooks/runtime_libusb.py 在冻结启动后把
# ctypes.util.find_library('usb-1.0') 重定向到它，使 pyusb 的 libusb 后端
# 在「目标机未装 libusb」时也能离线加载（USB 直连可用）。
_extra_binaries = []
try:
    import libusb as _libusb
    import glob as _glob
    _mac = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
    _libusb_dylib = os.path.join(
        os.path.dirname(_libusb.__file__), '_platform', 'macos', _mac, 'libusb-1.0.dylib')
    if not os.path.isfile(_libusb_dylib):
        # 兜底：在 _platform/macos 下任意子架构目录找
        _cands = _glob.glob(os.path.join(
            os.path.dirname(_libusb.__file__), '_platform', 'macos', '*', 'libusb-1.0.dylib'))
        _libusb_dylib = _cands[0] if _cands else ''
    if _libusb_dylib and os.path.isfile(_libusb_dylib):
        _extra_binaries = [(_libusb_dylib, '.')]  # 落到 .app/Contents/MacOS
        print('[build] 内置 libusb dylib:', _libusb_dylib)
    else:
        print('[build][WARN] 未找到 libusb-1.0.dylib，USB 直连在打包版将不可用'
              '（请在构建环境 pip install libusb）')
except Exception as _e:
    print('[build][WARN] 定位 libusb 失败:', _e)

# ── datas：资源 + 外部扩展 ─────────────────────────────────────────────────
# 注意：目标路径不要带前导 '/'，否则 macOS 下会被当成绝对路径导致文件丢失。
# 相对名 外部扩展 会落到 .app/Contents/MacOS/vendor，与源码目录结构一致，
# adb_tools.py 用 __file__ 定位 vendor/ 才能正常工作。
_datas = [
    (_RES_DIR, 'resources'),
]
if os.path.isdir(_EXT_DIR):
    _datas.append((_EXT_DIR, 'vendor'))

# ── 隐藏依赖（与 Win spec / build_exe.py 同步，缺一不可）──────────────────
# 自研 ADB 配对链路硬依赖 cryptography：配对客户端顶层 `from cryptography import
# x509` 直接 ImportError → 配对握手不发 → 手机扫码后一直转圈。故必须显式声明。
# brotli 仅本机已安装才追加（源码用 try/except 包裹，未安装时 PyInstaller 会因
# 找不到模块而报错，故条件化）。
_hiddenimports = [
    'segno', 'segno.helpers', 'zeroconf', 'ifaddr', 'pyzbar', 'tools.favorite_combobox',
    'png_rc', 'ui.png_rc',
    # ★ 自研ADB新增依赖（与 Super_ADB_Win/build_tools/Super_ADB.spec 同步，缺一不可）：
    #   cryptography 缺失 → adb pair 配对客户端顶层 import 崩溃 → 手机扫码后一直转圈
    'cryptography', 'cryptography.hazmat',
    'cryptography.hazmat.primitives', 'cryptography.hazmat.primitives.asymmetric',
    'cryptography.hazmat.primitives.asymmetric.rsa',
    'cryptography.hazmat.primitives.asymmetric.padding',
    'cryptography.hazmat.primitives.serialization',
    'cryptography.hazmat.primitives.hashes', 'cryptography.hazmat.backends',
    'tools.adb_native.mdns_discovery',   # 无线调试 mDNS 发现助手（_adb-tls-connect 真实调试端口）
    'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1',
]
try:
    import brotli  # 仅本机已安装才声明，避免未安装时 PyInstaller 报错
    _hiddenimports.append('brotli')
except ImportError:
    pass

a = Analysis(
    [_ENTRY],
    pathex=[_PROJECT_ROOT],
    binaries=_extra_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[os.path.join(_SPEC_DIR, 'hooks')],
    hooksconfig={},
    runtime_hooks=[
        # ★ 裸导入别名（开发期靠 sys.path 注入 tools/、ui/ 解析的 import png_rc
        #   等，冻结后这些目录非物理存在，故在此把裸名映射到已收集的包限定模块）
        os.path.join(_SPEC_DIR, 'hooks', 'runtime_pkg_alias.py'),
        os.path.join(_SPEC_DIR, 'hooks', 'runtime_pyzbar.py'),
        # ★ USB 直连随包：pyusb 的 libusb 后端离线加载（目标机无需装 libusb）
        os.path.join(_SPEC_DIR, 'hooks', 'runtime_libusb.py'),
    ],
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
    name='Super_ADB_MAC',
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
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Super_ADB_MAC',
)

# macOS .app 应用包（产物名固定 Super_ADB_MAC，与一键脚本 / ZIP 名一致）
app = BUNDLE(
    coll,
    name='Super_ADB_MAC.app',
    icon=_ICON,
    bundle_identifier='com.superadb.app',
    info_plist={
        'CFBundleName': 'Super_ADB',
        'CFBundleDisplayName': 'Super_ADB',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'NSHighResolutionCapable': True,
        'LSUIElement': False,
        'NSRequiresAquaSystemAppearance': False,
    },
)
