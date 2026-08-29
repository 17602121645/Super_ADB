# -*- coding: utf-8 -*-
import py_compile, os

for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p1 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    p2 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '对话框', '关于对话框.py')
    py_compile.compile(p1, doraise=True)
    py_compile.compile(p2, doraise=True)

    d1 = open(p1, encoding='utf-8').read()
    d2 = open(p2, encoding='utf-8').read()

    has_dl_in_write = '下载地址' in d1 and 'pan.quark.cn' in d1
    has_dl_func = 'def _获取下载地址' in d2
    has_dl_layout = 'self.download_lbl' in d2 and '新版下载地址' in d2
    has_dl_qss = 'aboutDownload' in d2
    has_no_stretch_before_version = 'content.addStretch()' in d2
    # 检查版本号前是否还有 addStretch（应该没有，下载地址后才有）
    version_pos = d2.find('self.version_lbl = QLabel')
    stretch_before = d2.rfind('addStretch', 0, version_pos)
    stretch_after = d2.find('addStretch', version_pos)

    print(f'{plat}: 语法OK | 打包脚本含下载地址={has_dl_in_write} | _获取下载地址函数={has_dl_func} | 下载地址布局={has_dl_layout} | aboutDownload QSS={has_dl_qss}')
    print(f'  版本号前addStretch={stretch_before>0} (pos={stretch_before}) | 版本号后addStretch={stretch_after>0} (pos={stretch_after})')
    print()
