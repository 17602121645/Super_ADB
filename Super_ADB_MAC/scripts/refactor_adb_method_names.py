# -*- coding: utf-8 -*-
"""
adb_tools.py 方法名英改中 批量重构脚本
====================================
安全策略：
1. 长方法名（不会与其他库冲突）：全局替换 .method( → .中文名(
2. 短方法名（易与 Qt/Python 内置冲突）：仅替换 adb.method( / self.adb.method( 等明确是 adb 对象的调用
3. 方法定义：def method( → def 中文名(
4. 模块级函数：全局替换 function( → 中文名(
"""
import os
import re
import sys

PROJECT_ROOT = r'G:\Python\jcspy\Super_ADB\Super_ADB_MAC'

# ══════════════════════════════════════════════════════════════════
# 映射表：英文 → 中文
# ══════════════════════════════════════════════════════════════════

# 模块级函数（全局替换，因为名字独特）
MODULE_FUNCTIONS = {
    'load_json_config': '加载json配置',
    'save_json_config': '保存json配置',
    'format_device_label': '格式化设备标签',
    'readonly_guidance': '只读分区引导',
    'find_bundled_adb_path': '查找内置adb路径',
}

# AdbHelper / AdbFileManager 类方法
# 分为两类：安全长名（全局替换 .method(）和 易冲突短名（仅替换 adb.method(）

# 安全长方法名：名字足够独特，不会与 Qt/Python/其他库冲突
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
}

# 易冲突短方法名：仅替换明确是 adb 对象的调用
# 这些名字太短，可能与 Qt 信号连接（connect）、其他对象方法冲突
RISKY_METHODS = {
    'connect': '连接设备',
    'pair': '配对设备',
    'install': '安装',
    'push': '推送文件',
    'pull': '拉取文件',
    'chmod': '修改权限',
}

# 合并所有方法名映射（用于定义替换）
ALL_METHODS = {}
ALL_METHODS.update(SAFE_METHODS)
ALL_METHODS.update(RISKY_METHODS)


def collect_py_files(root):
    """收集所有 .py 文件，排除 .git 和 __pycache__。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        if '.git' in dirpath or '__pycache__' in dirpath:
            continue
        for fn in filenames:
            if fn.endswith('.py'):
                files.append(os.path.join(dirpath, fn))
    return files


def replace_in_file(filepath, replacements):
    """对单个文件执行替换。replacements: list of (pattern, replacement)。
    返回替换次数。"""
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


def main():
    print('=' * 60)
    print('adb_tools.py 方法名英改中 批量重构')
    print('=' * 60)

    py_files = collect_py_files(PROJECT_ROOT)
    print(f'\n找到 {len(py_files)} 个 .py 文件')

    # ══════════════════════════════════════════════════════════════
    # 阶段1：替换方法定义（def method( → def 中文名(）
    # 只在 adb_tools.py 中替换类方法定义
    # ══════════════════════════════════════════════════════════════
    print('\n[阶段1] 替换方法定义...')
    adb_file = os.path.join(PROJECT_ROOT, 'tools', 'adb_tools.py')

    def_repls = []
    for en, cn in ALL_METHODS.items():
        # 匹配 def method( （类方法和模块函数都包含）
        def_repls.append((r'\bdef ' + re.escape(en) + r'\s*\(', f'def {cn}('))

    count = replace_in_file(adb_file, def_repls)
    print(f'  adb_tools.py: 替换了 {count} 处方法定义')

    # ══════════════════════════════════════════════════════════════
    # 阶段2：替换模块级函数调用（全局替换 function( → 中文名(）
    # 这些函数名字独特，不会冲突
    # ══════════════════════════════════════════════════════════════
    print('\n[阶段2] 替换模块级函数调用...')
    func_repls = []
    for en, cn in MODULE_FUNCTIONS.items():
        # 匹配 function( 但排除 def function(（定义已在阶段1替换）
        # 用 (?<!def ) 负向后行断言
        func_repls.append((r'(?<!def )\b' + re.escape(en) + r'\s*\(', f'{cn}('))

    total_func = 0
    for fp in py_files:
        c = replace_in_file(fp, func_repls)
        if c > 0:
            print(f'  {os.path.relpath(fp, PROJECT_ROOT)}: {c} 处')
            total_func += c
    print(f'  模块级函数总计: {total_func} 处')

    # ══════════════════════════════════════════════════════════════
    # 阶段3：替换安全长方法名调用（全局替换 .method( → .中文名(）
    # ══════════════════════════════════════════════════════════════
    print('\n[阶段3] 替换安全长方法名调用...')
    safe_repls = []
    for en, cn in SAFE_METHODS.items():
        safe_repls.append((r'\.' + re.escape(en) + r'\s*\(', f'.{cn}('))

    total_safe = 0
    for fp in py_files:
        c = replace_in_file(fp, safe_repls)
        if c > 0:
            print(f'  {os.path.relpath(fp, PROJECT_ROOT)}: {c} 处')
            total_safe += c
    print(f'  安全方法总计: {total_safe} 处')

    # ══════════════════════════════════════════════════════════════
    # 阶段4：替换易冲突短方法名（仅替换明确是 adb 对象的调用）
    # 匹配模式：adb.method( / self.adb.method( / self._adb.method( / adb_helper.method( 等
    # ══════════════════════════════════════════════════════════════
    print('\n[阶段4] 替换易冲突短方法名调用（仅 adb 对象）...')

    # adb 对象可能的变量名前缀
    adb_prefixes = [
        r'self\.adb',
        r'self\._adb',
        r'\badb',
        r'adb_helper',
        r'helper',
        r'self\.adb_helper',
        r'device_ops',
        r'self\.device_ops',
    ]
    adb_prefix_pattern = '(?:' + '|'.join(adb_prefixes) + r')'

    risky_repls = []
    for en, cn in RISKY_METHODS.items():
        # 匹配 adb对象.method(
        risky_repls.append((
            adb_prefix_pattern + r'\.' + re.escape(en) + r'\s*\(',
            lambda m, cn=cn: m.group(0).rsplit('.', 1)[0] + f'.{cn}('
        ))

    total_risky = 0
    for fp in py_files:
        c = replace_in_file(fp, risky_repls)
        if c > 0:
            print(f'  {os.path.relpath(fp, PROJECT_ROOT)}: {c} 处')
            total_risky += c
    print(f'  短方法总计: {total_risky} 处')

    # ══════════════════════════════════════════════════════════════
    # 阶段5：验证 - 搜索是否还有遗漏的英文方法调用
    # ══════════════════════════════════════════════════════════════
    print('\n[阶段5] 验证遗漏...')
    remaining = {}
    for en, cn in ALL_METHODS.items():
        for fp in py_files:
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    content = f.read()
                # 搜索 .en( 调用（排除已替换的中文名）
                matches = re.findall(r'\.' + re.escape(en) + r'\s*\(', content)
                if matches:
                    key = f'{en} → {cn}'
                    if key not in remaining:
                        remaining[key] = []
                    remaining[key].append((os.path.relpath(fp, PROJECT_ROOT), len(matches)))
            except:
                pass

    if remaining:
        print('  ⚠ 发现可能遗漏的调用（需人工确认是否为 adb 方法）:')
        for key, files in remaining.items():
            print(f'    {key}:')
            for fp, count in files:
                print(f'      {fp}: {count} 处')
    else:
        print('  ✓ 未发现遗漏')

    print('\n' + '=' * 60)
    print('重构完成！')
    print('=' * 60)


if __name__ == '__main__':
    main()
