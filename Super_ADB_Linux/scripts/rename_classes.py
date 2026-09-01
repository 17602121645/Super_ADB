# -*- coding: utf-8 -*-
"""
类名批量重命名脚本
==================
把项目中的英文类名批量改成中文，同时更新所有引用。
用法：python rename_classes.py
"""
import re
from pathlib import Path

WIN_ROOT = Path(__file__).resolve().parent.parent

# 类名映射表：英文名 -> 中文名
# 跳过：Ui_MainWindow(uic生成)、PROCESS_MEMORY_COUNTERS/_MSG(ctypes结构体)
CLASS_MAP = {
    # 对话框类
    '关于对话框': '关于对话框',
    '环境配置对话框': '环境配置对话框',
    '无线调试对话框': '无线调试对话框',
    '安装解包对话框': '安装解包对话框',
    'Json工具对话框': 'Json工具对话框',
    '哈希校验对话框': '哈希校验对话框',
    '时间戳对话框': '时间戳对话框',
    'WiFi对话框': 'WiFi对话框',
    'WiFi历史对话框': 'WiFi历史对话框',
    'WiFi配对对话框': 'WiFi配对对话框',
    '局域网扫描对话框': '局域网扫描对话框',
    'Scrcpy设置对话框': 'Scrcpy设置对话框',
    '哈希上下文菜单': '哈希上下文菜单',
    'Tcpdump对话框': 'Tcpdump对话框',
    '回放对话框': '回放对话框',
    '基准测试对话框': '基准测试对话框',
    '目录拖拽对话框': '目录拖拽对话框',
    '文本预览对话框': '文本预览对话框',
    # 窗口/页面类
    'Monkey压测窗口': 'Monkey压测窗口',
    '二维码连接页': '二维码连接页',
    '文件管理页': '文件管理页',
    '日志查看器页': '日志查看器页',
    '桌面小猫组件': '桌面小猫组件',
    '主窗口': '主窗口',
    # 监控类
    '设备性能监控': '设备性能监控',
    '应用性能监控': '应用性能监控',
    '滚动图表': '滚动图表',
    '应用滚动图表': '应用滚动图表',
    '事件饼图': '事件饼图',
    # 工具类
    'Adb助手': 'Adb助手',
    'Adb设备操作': 'Adb设备操作',
    'Adb文件管理器': 'Adb文件管理器',
    'Adb错误': 'Adb错误',
    'Axml解析器': 'Axml解析器',
    'Dex解析器': 'Dex解析器',
    '收藏下拉框': '收藏下拉框',
    '收藏委托': '收藏委托',
    '拖拽区域': '拖拽区域',
    # 线程/工作器类
    '命令工作器': '命令工作器',
    '工作器信号': '工作器信号',
    '哈希工作线程': '哈希工作线程',
    '任务线程': '任务线程',
    '加载包线程': '加载包线程',
    '构建目录树线程': '构建目录树线程',
    '安装线程': '安装线程',
    '单实例': '单实例',
    # JSON工具内部类
    'Json语法高亮': 'Json语法高亮',
    'Json历史下拉框': 'Json历史下拉框',
    '代码文本编辑框': '代码文本编辑框',
    '行号区域': '行号区域',
    # 哈希校验内部类
    '哈希算法': '哈希算法',
    '哈希结果行': '哈希结果行',
    # WiFi内部类
    '_加载工作器': '_加载工作器',
    '_状态卡片': '_状态卡片',
    # WiFi配对内部类
    '_配对工作器': '_配对工作器',
    '_连接工作器': '_连接工作器',
    # 局域网扫描内部类
    '_扫描工作器': '_扫描工作器',
    '_富化工作器': '_富化工作器',
    '_范围下拉框': '_范围下拉框',
    # 二维码内部类
    '_Mdns桥接': '_Mdns桥接',
    '_配对Mdns监听器': '_配对Mdns监听器',
    '_二维码配对工作器': '_二维码配对工作器',
    '_二维码生成工作器': '_二维码生成工作器',
    # AXML内部类
    '_字符串池': '_字符串池',
    '_元素': '_元素',
    # 收藏下拉框内部类
    '_收藏列表视图': '_收藏列表视图',
    # 页面内部类
    '_命令工作器': '_命令工作器',
    '_工作器信号': '_工作器信号',
    # 主入口内部类
    '_文本发送器': '_文本发送器',
    '_中文上下文菜单过滤器': '_中文上下文菜单过滤器',
    # 测试脚本类
    '模拟运行': '模拟运行',
    '_Monkey模拟': '_Monkey模拟模拟',
    '几何持久化测试': '几何持久化测试',
    '模拟父窗口': '模拟父窗口',
    # PEM哈希器
    '_Pem主题哈希器': '_Pem主题哈希器',
}

# 不处理的文件
SKIP_FILES = {'png_rc.py', 'Super_ADB.py'}  # uic生成的不动


def 替换文件(file_path):
    """替换单个文件中的类名。"""
    text = file_path.read_text(encoding='utf-8')
    changed = False
    for old, new in CLASS_MAP.items():
        # 单词边界替换，避免部分匹配
        # 匹配：类名前不是字母数字下划线，后不是字母数字下划线
        pattern = r'(?<![a-zA-Z0-9_])' + re.escape(old) + r'(?![a-zA-Z0-9_])'
        new_text, count = re.subn(pattern, new, text)
        if count > 0:
            text = new_text
            changed = True
            print(f'  {old} -> {new} ({count}处)')
    if changed:
        file_path.write_text(text, encoding='utf-8')
    return changed


def main():
    print('扫描项目文件...')
    py_files = [p for p in WIN_ROOT.rglob('*.py')
                if '__pycache__' not in p.parts and p.name not in SKIP_FILES]
    print(f'  发现 {len(py_files)} 个文件')

    total_changed = 0
    for f in sorted(py_files):
        rel = f.relative_to(WIN_ROOT)
        if 替换文件(f):
            print(f'✓ {rel}')
            total_changed += 1

    print(f'\n完成：修改了 {total_changed} 个文件，{len(CLASS_MAP)} 个类名映射')


if __name__ == '__main__':
    main()
