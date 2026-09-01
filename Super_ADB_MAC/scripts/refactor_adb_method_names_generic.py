# -*- coding: utf-8 -*-
"""通用 ADB方法名英改中 重构脚本，可指定项目根目录。"""
import os
import re
import sys

# 映射表
MODULE_FUNCTIONS = {
    'load_json_config': '加载json配置',
    'save_json_config': '保存json配置',
    'format_device_label': '格式化设备标签',
    'readonly_guidance': '只读分区引导',
    'find_bundled_adb_path': '查找内置adb路径',
}

SAFE_METHODS = {
    'check_adb': '检查adb',
    'get_devices': '获取设备列表',
    'disconnect': '断开设备',
    'get_oaid': '获取oaid',
    'get_device_info_dict': '获取设备信息字典',
    'get_device_info': '获取设备信息',
    'set_proxy': '设置代理',
    'clear_proxy': '清除代理',
    'reboot': '重启设备',
    'root_and_remount': 'root并重新挂载',
    'screenshot': '截图',
    'screen_record': '录屏',
    'get_app_list': '获取应用列表',
    'get_running_apps': '获取运行中应用',
    'get_window_app': '获取当前界面应用',
    'start_app': '启动应用',
    'stop_app': '停止应用',
    'clear_app': '清除应用',
    'uninstall_app': '卸载应用',
    'install_apk': '安装apk',
    'get_app_info': '获取应用信息',
    'get_meminfo': '获取内存信息',
    'logcat_to_desktop': 'logcat到桌面',
    'push_stream': '流式推送',
    'run_shell': '执行shell',
    'run_direct': '直接执行',
    'run_batch_script': '执行批量脚本',
    'list_dir': '列出目录',
    'read_text': '读取文本文件',
    'delete_path': '删除路径',
    'rename_path': '重命名路径',
    'scrcpy': '投屏',
    'find_scrcpy_dir': '查找scrcpy目录',
}

RISKY_METHODS = {
    'connect': '连接设备',
    'pair': '配对设备',
    'install': '安装',
    'push': '推送文件',
    'pull': '拉取文件',
    'chmod': '修改权限',
}

ALL_METHODS = {}
ALL_METHODS.update(SAFE_METHODS)
ALL_METHODS.update(RISKY_METHODS)
ALL_EN_NAMES = list(MODULE_FUNCTIONS.keys()) + list(ALL_METHODS.keys())


def collect_py_files(root):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        if '.git' in dirpath or '__pycache__' in dirpath:
            continue
        for fn in filenames:
            if fn.endswith('.py'):
                files.append(os.path.join(dirpath, fn))
    return files


def replace_in_file(filepath, replacements):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        print(f'  跳过 {filepath}: {e}')
        return 0
    total = 0
    for pattern, repl in replacements:
        new_content, count = re.subn(pattern, repl, content)
        if count > 0:
            content = new_content
            total += count
    if total > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    return total


def refactor_project(project_root):
    print(f'\n{"="*60}')
    print(f'重构: {project_root}')
    print(f'{"="*60}')

    py_files = collect_py_files(project_root)
    adb_file = os.path.join(project_root, 'tools', 'adb_tools.py')

    if not os.path.exists(adb_file):
        print(f'  未找到 {adb_file}，跳过')
        return

    # 阶段1：替换方法定义（包括模块函数和类方法）
    print('\n[阶段1] 替换方法定义...')
    def_repls = []
    for en, cn in ALL_METHODS.items():
        def_repls.append((r'\bdef ' + re.escape(en) + r'\s*\(', f'def {cn}('))
    for en, cn in MODULE_FUNCTIONS.items():
        def_repls.append((r'\bdef ' + re.escape(en) + r'\s*\(', f'def {cn}('))
    count = replace_in_file(adb_file, def_repls)
    print(f'  adb_tools.py: {count} 处定义')

    # 阶段2：替换模块级函数调用（全局）
    print('\n[阶段2] 替换模块级函数调用...')
    func_repls = []
    for en, cn in MODULE_FUNCTIONS.items():
        func_repls.append((r'(?<!def )\b' + re.escape(en) + r'\b', cn))
    total = 0
    for fp in py_files:
        c = replace_in_file(fp, func_repls)
        total += c
    print(f'  总计: {total} 处')

    # 阶段3：替换安全长方法名调用
    print('\n[阶段3] 替换安全长方法名调用...')
    safe_repls = []
    for en, cn in SAFE_METHODS.items():
        safe_repls.append((r'\.' + re.escape(en) + r'\s*\(', f'.{cn}('))
    total = 0
    for fp in py_files:
        c = replace_in_file(fp, safe_repls)
        total += c
    print(f'  总计: {total} 处')

    # 阶段4：替换易冲突短方法名（仅 adb 对象）
    print('\n[阶段4] 替换短方法名调用（仅 adb 对象）...')
    adb_prefixes = [
        r'self\.adb', r'self\._adb', r'\badb', r'adb_helper',
        r'helper', r'self\.adb_helper', r'device_ops', r'self\.device_ops',
    ]
    adb_prefix_pattern = '(?:' + '|'.join(adb_prefixes) + r')'
    risky_repls = []
    for en, cn in RISKY_METHODS.items():
        risky_repls.append((
            adb_prefix_pattern + r'\.' + re.escape(en) + r'\s*\(',
            lambda m, cn=cn: m.group(0).rsplit('.', 1)[0] + f'.{cn}('
        ))
    # 也处理 AdbHelper().method( 直接实例调用
    for en, cn in RISKY_METHODS.items():
        risky_repls.append((
            r'AdbHelper\(\)\.' + re.escape(en) + r'\s*\(',
            lambda m, cn=cn: m.group(0).rsplit('.', 1)[0] + f'.{cn}('
        ))
    total = 0
    for fp in py_files:
        c = replace_in_file(fp, risky_repls)
        total += c
    print(f'  总计: {total} 处')

    # 阶段5：替换导入语句
    print('\n[阶段5] 替换导入语句...')
    import_repls = []
    for en, cn in MODULE_FUNCTIONS.items():
        import_repls.append((r'\b' + re.escape(en) + r'\b', cn))
    total = 0
    for fp in py_files:
        # 只处理包含 import 的行
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            changed = False
            for i, line in enumerate(lines):
                if 'import' in line:
                    new_line = line
                    for en, cn in MODULE_FUNCTIONS.items():
                        new_line = re.sub(r'\b' + re.escape(en) + r'\b', cn, new_line)
                    if new_line != line:
                        lines[i] = new_line
                        changed = True
                        total += 1
            if changed:
                with open(fp, 'w', encoding='utf-8') as f:
                    f.writelines(lines)
        except:
            pass
    print(f'  总计: {total} 处')

    # 验证
    print('\n[验证] 检查残留...')
    remaining = 0
    for fp in py_files:
        try:
            with open(fp, 'r', encoding='utf-8') as f:
                content = f.read()
            for en in ALL_EN_NAMES:
                if en in ['install', 'push', 'pull', 'connect', 'pair', 'chmod']:
                    # 短方法名只检查 adb 对象调用
                    if re.search(r'adb\.' + en + r'\s*\(', content):
                        print(f'  ⚠ {os.path.relpath(fp, project_root)}: adb.{en}(')
                        remaining += 1
                else:
                    if re.search(r'\.' + en + r'\s*\(', content):
                        print(f'  ⚠ {os.path.relpath(fp, project_root)}: .{en}(')
                        remaining += 1
        except:
            pass
    if remaining == 0:
        print('  ✓ 无残留')

    # 语法检查
    print('\n[语法检查]')
    import py_compile
    errors = 0
    for fp in py_files:
        try:
            py_compile.compile(fp, doraise=True)
        except py_compile.PyCompileError as e:
            print(f'  ⚠ {os.path.relpath(fp, project_root)}: {str(e)[:100]}')
            errors += 1
    if errors == 0:
        print(f'  ✓ 全部 {len(py_files)} 个文件语法通过')

    print(f'\n完成: {project_root}')


if __name__ == '__main__':
    if len(sys.argv) > 1:
        for root in sys.argv[1:]:
            refactor_project(root)
    else:
        print('用法: python refactor_adb.py <项目根目录1> [项目根目录2] ...')
