# -*- coding: utf-8 -*-
import os, re

all_methods = [
    'check_adb', 'get_devices', 'connect', 'disconnect', 'pair',
    'run_shell', 'run_direct', 'run_batch_script', 'push_stream',
    'get_oaid', 'get_device_info_dict', 'get_device_info',
    'set_proxy', 'clear_proxy', 'reboot', 'root_and_remount',
    'screenshot', 'screen_record', 'find_scrcpy_dir', 'scrcpy',
    'get_app_list', 'get_running_apps', 'get_window_app',
    'start_app', 'stop_app', 'clear_app', 'uninstall_app',
    'install_apk', 'install', 'get_app_info', 'get_meminfo',
    'logcat_to_desktop', 'list_dir', 'read_text', 'push', 'pull',
    'delete_path', 'rename_path', 'chmod',
    'load_json_config', 'save_json_config', 'format_device_label',
    'readonly_guidance', 'find_bundled_adb_path',
]

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
    'chmod': '修改权限', 'load_json_config': '加载json配置',
    'save_json_config': '保存json配置', 'format_device_label': '格式化设备标签',
    'readonly_guidance': '只读分区引导', 'find_bundled_adb_path': '查找内置adb路径',
}

for version in ['Super_ADB_MAC', 'Super_ADB_Win', 'Super_ADB_Linux']:
    project_root = r'G:\Python\jcspy\Super_ADB\\' + version
    print('=== ' + version + ' 无括号回调遗漏 ===')
    found = False
    for root, dirs, files in os.walk(project_root):
        if '.git' in root or '__pycache__' in root or 'scripts' in root:
            continue
        for fn in files:
            if not fn.endswith('.py'):
                continue
            fp = os.path.join(root, fn)
            with open(fp, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            for i, line in enumerate(lines, 1):
                for method in all_methods:
                    pattern = r'\.' + re.escape(method) + r'(?![\(\w])'
                    if re.search(pattern, line):
                        stripped = line.strip()
                        if stripped.startswith('#') or stripped.startswith('"""'):
                            continue
                        rel = os.path.relpath(fp, project_root)
                        print('  ' + rel + ':' + str(i) + ': ' + stripped[:120])
                        found = True
    if not found:
        print('  ✓ 无遗漏')
    print()
