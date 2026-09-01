# -*- coding: UTF-8 -*-
"""
跨平台打包脚本（Windows / macOS）
@author:JCS
@file:build_exe.py
"""
import os
import sys
import shutil

# 确保 Super_ADB_Win 根目录在 sys.path 中，支持 from build_tools import xxx
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)



def _写入打包完成时间(base_dir, name='Super_ADB'):
    """打包完成后，把打包完成时间写入 dist 的 config/build_info.json（独立文件，不混入用户配置）。

    跨平台：Windows/Linux → dist/name/config/；
    macOS → dist/name.app/Contents/Resources/config/。

    macOS 必须放 Contents/Resources 而不是 Contents/MacOS：codesign 会把
    Contents/MacOS 下除主程序以外的内容当作「嵌套代码对象」，一个 json 就足以
    让 codesign --verify --deep --strict 报 code object is not signed at all。
    """
    try:
        import json as _json
        import time as _time
        if sys.platform == 'darwin':
            _dist_dir = os.path.join(base_dir, 'build_tools', 'dist', f'{name}.app', 'Contents', 'Resources')
        else:
            _dist_dir = os.path.join(base_dir, 'build_tools', 'dist', name)
        _dist_config_dir = os.path.join(_dist_dir, 'config')
        os.makedirs(_dist_config_dir, exist_ok=True)
        _dist_info_path = os.path.join(_dist_config_dir, 'build_info.json')
        _build_ver = 'v' + _time.strftime('%Y.%m.%d')
        _info = {
            '打包时间': _build_ver,
            '打包时间戳': _time.strftime('%Y-%m-%d %H:%M:%S'),
            '下载地址': 'https://pan.quark.cn/s/2b7b11ebe1e5?pwd=fAXN',
        }
        with open(_dist_info_path, 'w', encoding='utf-8') as _f:
            _json.dump(_info, _f, ensure_ascii=False, indent=2)
        print(f'已写入打包完成时间到 dist: {_build_ver} ({_dist_info_path})')
    except Exception as _e:
        print(f'写入打包时间到 dist 失败（不影响打包）: {_e}')


def _重命名输出文件夹(base_dir, name='Super_ADB'):
    """打包完成后，把 dist/name 重命名为 dist/Super_ADB_<平台>（按平台命名）。

    Windows/Linux: dist/Super_ADB/ → dist/Super_ADB_Win/ (或 _Linux)
    macOS:         dist/Super_ADB.app → dist/Super_ADB_MAC.app
    目标已存在时先删除。
    """
    import shutil as _shutil
    _platform_suffix = {'win32': 'Win', 'darwin': 'MAC', 'linux': 'Linux'}.get(sys.platform, sys.platform)
    _target_name = f'Super_ADB_{_platform_suffix}'
    _dist_root = os.path.join(base_dir, 'build_tools', 'dist')
    if sys.platform == 'darwin':
        _src = os.path.join(_dist_root, f'{name}.app')
        _dst = os.path.join(_dist_root, f'{_target_name}.app')
    else:
        _src = os.path.join(_dist_root, name)
        _dst = os.path.join(_dist_root, _target_name)
    if not os.path.exists(_src):
        print(f'重命名跳过：源不存在 {_src}')
        return
    if os.path.abspath(_src) == os.path.abspath(_dst):
        return
    if os.path.exists(_dst):
        _shutil.rmtree(_dst, ignore_errors=True)
    os.rename(_src, _dst)
    print(f'已重命名输出文件夹: {os.path.basename(_src)} → {os.path.basename(_dst)}')


def install(main):
    # 包式导入改造后，pathex 只需指向 Super_ADB_Win/ 根目录
    # 各子目录（对话框/页面/监控/工具/项目UI）均含 __init__.py 成为正规包，
    # PyInstaller 通过根包路径自动发现所有子包模块。
    here = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(here)
    # 项目UI目录也加入 paths，因为 Super_ADB.py 中是裸导入 import png_rc
    ui_dir = os.path.join(base_dir, 'ui')
    path_args = '--paths "%s" --paths "%s"' % (base_dir, ui_dir)
    # 入口脚本解析为绝对路径，避免依赖运行时的 cwd
    if not os.path.isabs(main):
        main = os.path.join(base_dir, main)

    # 显式声明隐藏依赖，避免 PyInstaller 在冻结时漏打包仅被局部 import 的模块
    hidden_modules = [
        'segno', 'segno.helpers',
        'zeroconf', 'ifaddr',
        'pyzbar',   # 二维码扫码解码（替代原 OpenCV，省 ~140MB）
        'tools.favorite_combobox',  # .ui 自定义控件，显式导入确保打包
        'png_rc', 'ui.png_rc',  # .ui 资源文件，显式导入确保打包
        # ★ 自研ADB新增依赖
        'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.rsa',
        'cryptography.hazmat.primitives.asymmetric.padding',
        'cryptography.hazmat.primitives.serialization',
        'cryptography.hazmat.primitives.hashes',
        'cryptography.hazmat.backends',
        'tools.adb_native.mdns_discovery',  # 无线调试 mDNS 发现助手（_adb-tls-connect 真实调试端口）
        'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1',
    ]
    # ★ PCAP 解析已弃用 scapy，改用纯 Python 的 工具.轻量PCAP解析（零依赖，
    #   会被 PyInstaller 通过 path_args 自动发现，无需显式声明）。
    #   brotli 是可选依赖（用于解压 HTTP Content-Encoding: br 的响应体），
    #   源码用 try/except 包裹，PyInstaller 静态分析容易漏掉；仅当本机已安装
    #   才加入 hidden-import，避免未安装时 PyInstaller 报错。
    try:
        import brotli  # noqa: F401
        hidden_modules.append('brotli')
    except ImportError:
        pass
    hidden = " ".join(f'--hidden-import {m}' for m in hidden_modules)

    # pyzbar 的 DLL 用 hook 收集（见 hooks/hook-pyzbar.py），这里只挂目录
    hooks_dir = os.path.join(here, 'hooks')
    hooks = f'--additional-hooks-dir "{hooks_dir}"' if os.path.isdir(hooks_dir) else ''
    # 运行时钩子：把 pyzbar 的 DLL 加载路径重定向到打包产物里的确定位置
    # （避免冻结后 __file__ 是虚拟路径找不到 libzbar-64.dll）
    rt_hook = os.path.join(hooks_dir, 'runtime_pyzbar.py')
    runtime_hooks = f'--runtime-hook "{rt_hook}"' if os.path.isfile(rt_hook) else ''

    # numpy 仅被 PIL.Image 在 fromarray/np.asarray 里惰性局部 import，
    # 本工程从不调用 fromarray，纯 Image.open 路径无需 numpy；
    # 排除可省 ~26MB，且 PySide6 顶层不依赖 numpy，安全。
    # cv2 已被 二维码连接页 改用 pyzbar+PIL 取代，整包排除（避免被
    # pyzbar.tests 间接拉回 ~111MB）。
    # pyzbar.tests 仅含单元测试，运行期不需要，且会间接 import cv2/numpy。
    excludes = '--exclude-module numpy --exclude-module cv2 --exclude-module pyzbar.tests'

    # 以下均为「零引用」或可惰性缺失的死重（已用 PE 导入表 + 源码 import 扫描确认）：
    #   PIL._avif   : AVIF 解码后端 7.5MB，截图/二维码全是 PNG/JPEG，永不打开 AVIF
    #   PIL._webp   : WEBP 后端 0.4MB，无 WEBP 读写需求
    #   PIL._imagingtk: Tk 接口 0.01MB，冻结环境无 Tk，纯废件
    #   unicodedata : Unicode 数据库 0.7MB，GUI 文本渲染走 Qt，不查 Python unicode DB
    #   zstandard   : zstd 压缩 0.5MB，项目无任何 import
    #   _decimal    : 高精度小数 0.3MB，无金额/定点计算需求
    excludes += ' --exclude-module PIL._avif --exclude-module PIL._webp' \
                ' --exclude-module PIL._imagingtk --exclude-module unicodedata' \
                ' --exclude-module zstandard --exclude-module _zstd' \
                ' --exclude-module _decimal --exclude-module PIL._imagingcms' \
                ' --exclude-module PIL._imagingmath'

    # ★ cryptography 排除项已移除：serialization/__init__ 硬导入 asymmetric 的
    # dh/dsa/ec/ed25519/x25519 等子模块（类型注解用），排除任一都会导致
    # import serialization 抛 ModuleNotFoundError → 打包版密钥生成/加载全灭。
    # 该排除仅省 ~3-5MB（_rust.pyd 本就不可拆分），不值得牺牲核心功能。

    # 运行时资源（导出 HTML 报告用的 chart.umd.min.js）：随包分发，离线可用。
    # 官方 scrcpy 投屏二进制（外部扩展/scrcpy/，~25MB）：投屏功能直接调用，随包分发。
    # tcpdump 抓包二进制（外部扩展/tcpdump/，~4MB）：无 root 设备自动推送，随包分发。
    # PyInstaller 的 SRC:DST 分隔符在 Windows 上为 ';'、其余平台为 ':'。
    # 注意：ADB工具.py（原 adb_utils.py，位于 工具/）用 __file__ 定位 外部扩展/，
    # 打包后 __file__ 在 _internal/ 顶层，所以 外部扩展 必须放到
    # Super_ADB_Win/外部扩展 才能和源码目录结构保持一致。
    add_data_sep = ';' if sys.platform == 'win32' else ':'
    res_arg = f'--add-data "{os.path.join(base_dir, "resources")}{add_data_sep}resources"'
    data_dir = os.path.join(base_dir, 'vendor')
    # 注意：目标路径不能带前导 / —— Windows 下会静默失败导致 外部扩展 没进包，
    # 相对名 外部扩展 会落到 _internal/外部扩展，与源码目录结构一致
    data_arg = f'--add-data "{data_dir}{add_data_sep}vendor"' if os.path.isdir(data_dir) else ''

    name = f"Super_ADB"
    # 构建前清空旧输出目录，避免 COLLECT 报 "output directory not empty" 而中断。
    # 默认 rmtree 真删；若设 CLEAN_MOVE=1（如构建环境禁止批量删除）则改名为
    # Super_ADB_prev / _prev2 ... 移开，功能等价且可手动清理。
    out_dir = os.path.join(base_dir, 'build_tools', 'dist', name)
    if os.path.isdir(out_dir):
        if os.environ.get('CLEAN_MOVE'):
            prev = out_dir + '_prev'
            i = 2
            while os.path.isdir(prev):
                prev = out_dir + f'_prev{i}'
                i += 1
            shutil.move(out_dir, prev)
            print('旧构建已改名移开:', prev)
        else:
            shutil.rmtree(out_dir)
    if sys.platform == 'darwin':
        # macOS: 生成 .app，图标用 .icns（如有）否则 .png
        icon = os.path.join(base_dir, 'adb.icns') if os.path.exists(os.path.join(base_dir, 'adb.icns')) else os.path.join(base_dir, 'resources', 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} --distpath "{base_dir}/build_tools/dist" --workpath "{base_dir}/build_tools/build" {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {data_arg} {path_args} "{main}"'
    else:
        # Windows: 生成 .exe
        icon = os.path.join(base_dir, 'resources', 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} --distpath "{base_dir}/build_tools/dist" --workpath "{base_dir}/build_tools/build" {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {data_arg} {path_args} "{main}"'
    os.system(cmd)
    print('配置文件生成成功')

    # 打包完成后，写入打包完成时间到 dist 配置
    _写入打包完成时间(base_dir, name)

    # 构建后裁剪 PySide6 用不到的 Qt 库/翻译。
    # 说明：PyInstaller 的 additional-hooks-dir 是「追加」而非「覆盖」内置
    # hook-PySide6，内置 hook 会把整套 Qt6 DLL + 全部翻译收进来；无法靠 hook
    # 覆盖，故改为构建后按「保留 .pyd 的 DLL 依赖闭包」物理删除闭包外的文件。
    try:
        from build_tools import trim_qt
        trim_qt.main()
    except Exception as e:
        print('trim_qt.py 执行失败（不影响主构建，可手动跑）:', e)

    # 打包完成后，输出文件夹按平台命名（Super_ADB_Win / _MAC / _Linux）
    _重命名输出文件夹(base_dir, name)


def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')
    # 打包完成后写入时间配置（从参数解析 -n 应用名，默认 Super_ADB）
    _here = os.path.dirname(os.path.abspath(__file__))
    _base = os.path.dirname(_here)
    _m = re.search(r'-n\s+(\S+)', s)
    _name = _m.group(1) if _m else 'Super_ADB'
    _写入打包完成时间(_base, _name)
    _重命名输出文件夹(_base, _name)


if __name__ == '__main__':
    install(os.path.join("app", "main.py"))
