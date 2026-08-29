# -*- coding: utf-8 -*-
"""关于弹窗：版本号上移 + 新增新版下载地址（从打包信息.json读取）；打包脚本加下载地址字段。
三平台同步。
"""
import os

DOWNLOAD_URL = 'https://pan.quark.cn/s/2b7b11ebe1e5?pwd=fAXN'

# ========== 1. 精简打包exe.py：打包信息.json 加下载地址 ==========
OLD_WRITE_INFO = """        _info = {
            '打包时间': _build_ver,
            '打包时间戳': _time.strftime('%Y-%m-%d %H:%M:%S'),
        }"""

NEW_WRITE_INFO = """        _info = {
            '打包时间': _build_ver,
            '打包时间戳': _time.strftime('%Y-%m-%d %H:%M:%S'),
            '下载地址': '""" + DOWNLOAD_URL + """',
        }"""


# ========== 2. 关于对话框.py：新增 _获取下载地址 + 布局修改 ==========

# 2a. 在 _获取版本号 后面加 _获取下载地址
OLD_ABOUT_FUNC_END = """    except Exception:
        pass
    return VERSION


# ----------------------------------------------------------------------
# 关于弹窗 QSS 模板"""

NEW_ABOUT_FUNC_END = """    except Exception:
        pass
    return VERSION


def _获取下载地址():
    \"\"\"从 exe 旁边的 打包信息.json 读取新版下载地址，缺失时返回空字符串。\"\"\"
    import json as _json
    try:
        if getattr(sys, 'frozen', False):
            _base = os.path.dirname(sys.executable)
        else:
            _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _info_path = os.path.join(_base, '配置', '打包信息.json')
        if os.path.exists(_info_path):
            with open(_info_path, 'r', encoding='utf-8') as _f:
                info = _json.load(_f)
            if isinstance(info, dict) and info.get('下载地址'):
                return info['下载地址']
    except Exception:
        pass
    return ''


# ----------------------------------------------------------------------
# 关于弹窗 QSS 模板"""

# 2b. 布局修改：去掉 addStretch，版本号下加下载地址
OLD_LAYOUT = """        content.addStretch()

        # 版本号（次要文字，从配置文件读取打包时间）
        self.version_lbl = QLabel(f'版本号：{_获取版本号()}')
        self.version_lbl.setAlignment(Qt.AlignCenter)
        content.addWidget(self.version_lbl)

        # 开源地址（可点击跳转）
        self.repo_lbl = QLabel(f'<a href="{REPO_URL}">开源地址：{REPO_URL}</a>')"""

NEW_LAYOUT = """        # 版本号（次要文字，从配置文件读取打包时间）
        self.version_lbl = QLabel(f'版本号：{_获取版本号()}')
        self.version_lbl.setAlignment(Qt.AlignCenter)
        content.addWidget(self.version_lbl)

        # 新版下载地址（从配置文件读取，样式同版本号）
        _dl_url = _获取下载地址()
        if _dl_url:
            self.download_lbl = QLabel(f'<a href="{_dl_url}">新版下载地址：{_dl_url}</a>')
            self.download_lbl.setObjectName('aboutRepo')
            self.download_lbl.setAlignment(Qt.AlignCenter)
            self.download_lbl.setOpenExternalLinks(True)
            self.download_lbl.setWordWrap(True)
            content.addWidget(self.download_lbl)
        else:
            self.download_lbl = None

        content.addStretch()

        # 开源地址（可点击跳转）
        self.repo_lbl = QLabel(f'<a href="{REPO_URL}">开源地址：{REPO_URL}</a>')"""


for plat in ['Super_ADB_Win', 'Super_ADB_Linux', 'Super_ADB_MAC']:
    print(f'=== {plat} ===')

    # 1. 修改精简打包exe.py
    p1 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '打包', '精简打包exe.py')
    if os.path.isfile(p1):
        d = open(p1, encoding='utf-8').read()
        if OLD_WRITE_INFO in d:
            d = d.replace(OLD_WRITE_INFO, NEW_WRITE_INFO, 1)
            open(p1, 'w', encoding='utf-8', newline='').write(d)
            print(f'  精简打包exe.py: 已加下载地址字段')
        elif '下载地址' in d:
            print(f'  精简打包exe.py: 已改过，跳过')
        else:
            print(f'  精简打包exe.py: 未找到旧块')
    else:
        print(f'  精简打包exe.py: 不存在')

    # 2. 修改关于对话框.py
    p2 = os.path.join(r'G:\Python\jcspy\Super_ADB', plat, '对话框', '关于对话框.py')
    if os.path.isfile(p2):
        d = open(p2, encoding='utf-8').read()
        changed = False

        # 2a. 加 _获取下载地址 函数
        if OLD_ABOUT_FUNC_END in d and 'def _获取下载地址' not in d:
            d = d.replace(OLD_ABOUT_FUNC_END, NEW_ABOUT_FUNC_END, 1)
            changed = True
            print(f'  关于对话框.py: 已加 _获取下载地址 函数')
        elif 'def _获取下载地址' in d:
            print(f'  关于对话框.py: _获取下载地址 已存在，跳过')
        else:
            print(f'  关于对话框.py: 未找到函数插入点')

        # 2b. 修改布局
        if OLD_LAYOUT in d:
            d = d.replace(OLD_LAYOUT, NEW_LAYOUT, 1)
            changed = True
            print(f'  关于对话框.py: 已修改布局（版本号上移+下载地址）')
        elif 'self.download_lbl' in d:
            print(f'  关于对话框.py: 布局已改过，跳过')
        else:
            print(f'  关于对话框.py: 未找到布局旧块')

        if changed:
            open(p2, 'w', encoding='utf-8', newline='').write(d)
            print(f'  关于对话框.py: 已保存')
    else:
        print(f'  关于对话框.py: 不存在')
    print()
