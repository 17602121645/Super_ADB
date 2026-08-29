# -*- coding: utf-8 -*-
"""下载地址链接样式改为和版本号一致（灰色次要文字），新增 aboutDownload QSS。
三平台同步。
"""
import os

# 1. QSS 模板：在 aboutRepo 后面加 aboutDownload 样式
OLD_QSS = """QLabel#aboutRepo {{ color: {accent}; font: 9pt '{font}'; background: transparent; }}
QLabel#aboutRepo a {{ color: {accent}; text-decoration: none; }}
QLabel#aboutRepo a:hover {{ text-decoration: underline; }}"""

NEW_QSS = """QLabel#aboutRepo {{ color: {accent}; font: 9pt '{font}'; background: transparent; }}
QLabel#aboutRepo a {{ color: {accent}; text-decoration: none; }}
QLabel#aboutRepo a:hover {{ text-decoration: underline; }}
QLabel#aboutDownload {{ color: {text_disabled}; font: 9pt '{font}'; background: transparent; }}
QLabel#aboutDownload a {{ color: {text_disabled}; text-decoration: none; }}
QLabel#aboutDownload a:hover {{ text-decoration: underline; }}"""

# 2. 布局：下载地址 objectName 从 aboutRepo 改为 aboutDownload
OLD_LAYOUT = """            self.download_lbl = QLabel(f'<a href="{_dl_url}">新版下载地址：{_dl_url}</a>')
            self.download_lbl.setObjectName('aboutRepo')"""

NEW_LAYOUT = """            self.download_lbl = QLabel(f'<a href="{_dl_url}">新版下载地址：{_dl_url}</a>')
            self.download_lbl.setObjectName('aboutDownload')"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    p = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '对话框', '关于对话框.py')
    if not os.path.isfile(p):
        print(f'{plat}: 文件不存在')
        continue
    d = open(p, encoding='utf-8').read()
    changed = False

    if OLD_QSS in d:
        d = d.replace(OLD_QSS, NEW_QSS, 1)
        changed = True
        print(f'{plat}: 已加 aboutDownload QSS')
    elif 'aboutDownload' in d:
        print(f'{plat}: QSS 已存在，跳过')
    else:
        print(f'{plat}: 未找到 QSS 插入点')

    if OLD_LAYOUT in d:
        d = d.replace(OLD_LAYOUT, NEW_LAYOUT, 1)
        changed = True
        print(f'{plat}: 下载地址 objectName 改为 aboutDownload')
    elif "setObjectName('aboutDownload')" in d:
        print(f'{plat}: objectName 已改，跳过')
    else:
        print(f'{plat}: 未找到布局旧块')

    if changed:
        open(p, 'w', encoding='utf-8', newline='').write(d)
        print(f'{plat}: 已保存')
    print()
