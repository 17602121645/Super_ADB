# -*- coding: utf-8 -*-
"""修复无括号回调形式的方法名遗漏。"""
import os, re

mapping = {
    'check_adb': '检查adb', 'get_devices': '获取设备列表',
    'connect': '连接设备', 'disconnect': '断开设备', 'pair': '配对设备',
    'run_shell': '执行shell', 'run_direct': '直接执行',
    'run_batch_script': '执行批量脚本', 'push_stream': '流式推送',
    'get_oaid': '获取oaid', 'get_device_info_dict': '获取设备信息字典',
    'get_device_info': '获取设备信息', 'set_proxy': '设置代理',
    'clear_proxy': '清除代理', 'reboot': '重启设备',
    'root_and_remount': 'root并重新挂载', 'screenshot': '截图',
    'screen_record': '录屏', 'find_scrcpy_dir': '查找scrcpy目录',
    'scrcpy': '投屏', 'get_app_list': '获取应用列表',
    'get_running_apps': '获取运行中应用', 'get_window_app': '获取当前界面应用',
    'start_app': '启动应用', 'stop_app': '停止应用',
    'clear_app': '清除应用', 'uninstall_app': '卸载应用',
    'install_apk': '安装apk', 'install': '安装',
    'get_app_info': '获取应用信息', 'get_meminfo': '获取内存信息',
    'logcat_to_desktop': 'logcat到桌面', 'list_dir': '列出目录',
    'read_text': '读取文本文件', 'push': '推送文件', 'pull': '拉取文件',
    'delete_path': '删除路径', 'rename_path': '重命名路径',
    'chmod': '修改权限',
}

# 需要匹配的对象前缀（明确是 adb 或 fileMgr 对象）
object_prefixes = [
    r'self\._mgr',
    r'self\.adb',
    r'self\._adb',
    r'\badb',
    r'adb_helper',
    r'helper',
    r'self\.adb_helper',
    r'device_ops',
    r'self\.device_ops',
    r'self\._mgr',
]

prefix_pattern = '(?:' + '|'.join(object_prefixes) + ')'


def fix_file(filepath):
    """修复单个文件中的无括号回调。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except:
        return 0

    total = 0
    for en, cn in mapping.items():
        # 匹配 对象.method 但后面不是 ( 或 字母/下划线
        # 即作为回调传递的情况
        pattern = r'(' + prefix_pattern + r'\.)' + re.escape(en) + r'(?![\(\w])'
        new_content, count = re.subn(pattern, r'\1' + cn, content)
        if count > 0:
            content = new_content
            total += count

    if total > 0:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    return total


for version in ['Super_ADB_MAC', 'Super_ADB_Win', 'Super_ADB_Linux']:
    project_root = r'G:\Python\jcspy\Super_ADB\\' + version
    print('=== ' + version + ' ===')
    total_all = 0
    for root, dirs, files in os.walk(project_root):
        if '.git' in root or '__pycache__' in root or 'scripts' in root:
            continue
        for fn in files:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(root, fn)
            c = fix_file(fp)
            if c > 0:
                rel = os.path.relpath(fp, project_root)
                print('  ' + rel + ': ' + str(c) + ' 处')
                total_all += c
    print('  总计: ' + str(total_all) + ' 处')
    print()
