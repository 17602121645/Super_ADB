# -*- coding: UTF-8 -*-
"""
跨平台打包脚本（Windows / macOS）
@author:JCS
@file:精简打包exe.py
"""
import os
import sys
import shutil

# 确保 Super_ADB_MAC 根目录在 sys.path 中，支持 from 打包 import xxx
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)


def install(main):
    # 包式导入改造后，pathex 只需指向 Super_ADB_MAC/ 根目录
    # 各子目录（对话框/页面/监控/工具/项目UI）均含 __init__.py 成为正规包，
    # PyInstaller 通过根包路径自动发现所有子包模块。
    here = os.path.dirname(os.path.abspath(__file__))
    base_dir = os.path.dirname(here)
    # 项目UI目录也加入 paths，因为 Super_ADB.py 中是裸导入 import png_rc
    ui_dir = os.path.join(base_dir, '项目UI')
    path_args = '--paths "%s" --paths "%s"' % (base_dir, ui_dir)
    # 入口脚本解析为绝对路径，避免依赖运行时的 cwd
    if not os.path.isabs(main):
        main = os.path.join(base_dir, main)

    # 显式声明隐藏依赖，避免 PyInstaller 在冻结时漏打包仅被局部 import 的模块
    hidden = " ".join(
        f'--hidden-import {m}' for m in (
            'segno', 'segno.helpers',
            'zeroconf', 'ifaddr',
            'pyzbar',   # 二维码扫码解码（替代原 OpenCV，省 ~140MB）
            '工具.收藏下拉框',  # .ui 自定义控件，显式导入确保打包
            'png_rc', '项目UI.png_rc',  # .ui 资源文件，显式导入确保打包
            # ★ 自研ADB新增依赖
            'cryptography', 'cryptography.hazmat', 'cryptography.hazmat.primitives',
            'cryptography.hazmat.primitives.asymmetric', 'cryptography.hazmat.primitives.asymmetric.rsa',
            'cryptography.hazmat.primitives.asymmetric.padding',
            'cryptography.hazmat.primitives.serialization',
            'cryptography.hazmat.primitives.hashes',
            'cryptography.hazmat.backends',
            'usb', 'usb.core', 'usb.util', 'usb.backend.libusb1',
        )
    )

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

    # ★ 投屏解码已弃用 PyAV，改用内置 openh264（外部扩展/openh264/，~4MB）：
    #   av        : PyAV Python 层 2.9MB；排除后其 hook 不再收集 av.libs
    #               （全量 ffmpeg 编码器 DLL 62.5MB）一并消失，省 ~63MB
    #   OpenGL_accelerate : PyOpenGL 加速包 0.93MB，缺失时自动回退纯 Python 路径
    #   OpenGL.DLLS       : PyOpenGL 捆绑的 freeglut/gle 废件 1.74MB（只用 OpenGL.GL）
    excludes += ' --exclude-module av --exclude-module av.libs' \
                ' --exclude-module OpenGL_accelerate --exclude-module OpenGL.DLLS'

    # ★ cryptography 死重：自研ADB只用 RSA+SHA1+PKCS1v15+序列化，以下曲线/算法永不使用
    # 排除可省 ~3-5MB（主要是椭圆曲线、密钥派生、对称加密、X.509证书等模块的Python层）
    # 注意：cryptography的核心C扩展(_rust.pyd/.so)无法拆分，仍会整体打包
    excludes += ' --exclude-module cryptography.hazmat.primitives.asymmetric.x25519' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.x448' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.ed25519' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.ed448' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.dh' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.dsa' \
                ' --exclude-module cryptography.hazmat.primitives.asymmetric.ec' \
                ' --exclude-module cryptography.hazmat.primitives.kdf' \
                ' --exclude-module cryptography.hazmat.primitives.ciphers' \
                ' --exclude-module cryptography.hazmat.primitives.twofactor' \
                ' --exclude-module cryptography.fernet' \
                ' --exclude-module cryptography.x509' \
                ' --exclude-module cryptography.ocsp'

    # 运行时资源（导出 HTML 报告用的 chart.umd.min.js）：随包分发，离线可用。
    # scrcpy 投屏二进制（可选）：若 外部扩展/ 目录存在则一并打包，未放置时不报错。
    # PyInstaller 的 SRC:DST 分隔符在 Windows 上为 ';'、其余平台为 ':'。
    # 注意：ADB工具.py（原 adb_utils.py，位于 工具/）用 __file__ 定位 外部扩展/，
    # 打包后 __file__ 在 _internal/ 顶层，所以 外部扩展 必须放到
    # Super_ADB_MAC/外部扩展 才能和源码目录结构保持一致。
    add_data_sep = ';' if sys.platform == 'win32' else ':'
    res_arg = f'--add-data "{os.path.join(base_dir, "资源")}{add_data_sep}资源"'
    data_dir = os.path.join(base_dir, '外部扩展')
    # 注意：目标路径不能带前导 / —— Windows 下会静默失败导致 外部扩展 没进包，
    # 相对名 外部扩展 会落到 _internal/外部扩展，与源码目录结构一致
    data_arg = f'--add-data "{data_dir}{add_data_sep}外部扩展"' if os.path.isdir(data_dir) else ''

    name = f"Super_ADB"
    # 构建前清空旧输出目录，避免 COLLECT 报 "output directory not empty" 而中断。
    # 默认 rmtree 真删；若设 CLEAN_MOVE=1（如构建环境禁止批量删除）则改名为
    # Super_ADB_prev / _prev2 ... 移开，功能等价且可手动清理。
    out_dir = os.path.join(base_dir, '打包', 'dist', name)
    if sys.platform == 'darwin':
        out_dir = os.path.join(base_dir, '打包', 'dist', f'{name}.app')
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
        icon = os.path.join(base_dir, 'adb.icns') if os.path.exists(os.path.join(base_dir, 'adb.icns')) else os.path.join(base_dir, '资源', 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} --distpath "{base_dir}/打包/dist" --workpath "{base_dir}/打包/build" {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {data_arg} {path_args} "{main}"'
    else:
        # Windows: 生成 .exe
        icon = os.path.join(base_dir, '资源', 'Super_ADB.png')
        cmd = f'pyinstaller --clean -w -i "{icon}" -n {name} --distpath "{base_dir}/打包/dist" --workpath "{base_dir}/打包/build" {hidden} {hooks} {runtime_hooks} {excludes} {res_arg} {data_arg} {path_args} "{main}"'
    os.system(cmd)
    print('配置文件生成成功')

    # PyOpenGL 捆绑的 freeglut/gle 原生库（OpenGL/DLLS，~1.7MB）是 hook 按
    # 数据文件收集的，--exclude-module 挡不住；项目只用纯 OpenGL.GL，构建后直删。
    try:
        if sys.platform == 'darwin':
            opengl_dlls = os.path.join(base_dir, '打包', 'dist', f'{name}.app',
                                       'Contents', 'Frameworks', 'OpenGL', 'DLLS')
        else:
            opengl_dlls = os.path.join(base_dir, '打包', 'dist', name,
                                       '_internal', 'OpenGL', 'DLLS')
        if os.path.isdir(opengl_dlls):
            shutil.rmtree(opengl_dlls)
            print('已删除 OpenGL/DLLS（freeglut/gle 废件）')
    except Exception as e:
        print('删除 OpenGL/DLLS 失败（不影响运行）:', e)

    # 构建后裁剪 PySide6 用不到的 Qt 库/翻译。
    # 说明：PyInstaller 的 additional-hooks-dir 是「追加」而非「覆盖」内置
    # hook-PySide6，内置 hook 会把整套 Qt6 DLL + 全部翻译收进来；无法靠 hook
    # 覆盖，故改为构建后按「保留 .pyd/.so 的库依赖闭包」物理删除闭包外的文件。
    try:
        from 打包 import 裁剪_qt
        裁剪_qt.main()
    except Exception as e:
        print('裁剪_qt.py 执行失败（不影响主构建，可手动跑）:', e)


def install1(s):
    install = f'pyinstaller {s}'
    os.system(install)
    print('打包完成')


if __name__ == '__main__':
    install(os.path.join("项目启动入口", "Super_ADB_主入口.py"))
