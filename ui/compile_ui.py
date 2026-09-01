# -*- coding: utf-8 -*-
"""
编译 UI 脚本
==========
自动编译 Super_ADB.ui 为 Super_ADB.py，并给 import png_rc 加 try-except 容错。

用法：
    python compile_ui.py
"""
import subprocess
import sys
from pathlib import Path

# 路径配置
UI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = UI_DIR.parent
UI_FILE = UI_DIR / 'Super_ADB.ui'
OUTPUT_FILE = PROJECT_ROOT / 'Super_ADB_Win' / 'ui' / 'Super_ADB.py'


def 编译UI():
    """编译 .ui 文件并自动加 png_rc 容错。"""
    print(f'编译 {UI_FILE} ...')

    # 运行 pyside6-uic
    result = subprocess.run(
        ['pyside6-uic', str(UI_FILE), '-o', str(OUTPUT_FILE)],
        capture_output=True,
        text=True,
        encoding='utf-8'
    )

    # 输出警告（不影响编译）
    if result.stderr:
        for line in result.stderr.strip().splitlines():
            if line.strip():
                print(f'  警告: {line.strip()}')

    if result.returncode != 0:
        print(f'❌ 编译失败: {result.stderr}')
        return False

    print(f'✅ 编译成功: {OUTPUT_FILE}')

    # 给 import png_rc 加 try-except 容错
    print('添加 png_rc 容错...')
    content = OUTPUT_FILE.read_text(encoding='utf-8')

    old_import = 'import png_rc'
    new_import = '''try:
    import png_rc  # noqa: F401
except ImportError:
    pass'''

    if old_import in content:
        content = content.replace(old_import, new_import, 1)
        OUTPUT_FILE.write_text(content, encoding='utf-8')
        print('✅ 已添加 png_rc 容错')
    elif 'try:' in content and 'import png_rc' in content:
        print('ℹ️  已有容错，跳过')
    else:
        print('ℹ️  未找到 import png_rc，跳过')

    # 语法检查
    print('语法检查...')
    check = subprocess.run(
        [sys.executable, '-m', 'py_compile', str(OUTPUT_FILE)],
        capture_output=True,
        text=True
    )
    if check.returncode == 0:
        print('✅ 语法检查通过')
    else:
        print(f'❌ 语法错误: {check.stderr}')
        return False

    print('\n🎉 完成！')
    return True


if __name__ == '__main__':
    编译UI()
