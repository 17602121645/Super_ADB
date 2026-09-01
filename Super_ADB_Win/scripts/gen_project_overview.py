# -*- coding: utf-8 -*-
"""
生成项目全景文档
================
自动扫描 Super_ADB_Win/ 项目结构、类继承、依赖关系，
生成包含 mermaid 图表的完整 HTML 项目全景文档。

用法：
    python Super_ADB_Win/scripts/gen_project_overview.py

输出：
    项目根目录/project_overview.html
"""
import ast
import os
import re
import sys
import importlib
import inspect
from pathlib import Path
from datetime import datetime


# ============================================================
# 配置
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # G:\Python\jcspy\Super_ADB
WIN_ROOT = PROJECT_ROOT / 'Super_ADB_Win'
OUTPUT_DIR = PROJECT_ROOT / 'docs'
OUTPUT_HTML = OUTPUT_DIR / 'project_overview.html'
DIALOG_DIR = WIN_ROOT / 'dialogs'
DIALOG_SHOT_DIR = OUTPUT_DIR / '截图' / 'dialogs'
MAIN_SHOT_DIR = OUTPUT_DIR / '截图'

# 把项目根目录加入 sys.path，用于动态导入界面样式模块
if str(WIN_ROOT) not in sys.path:
    sys.path.insert(0, str(WIN_ROOT))


def 获取包描述():
    """动态从每个包的 __init__.py 读取 docstring 作为描述，无则用包名。"""
    desc = {}
    for pkg_dir in sorted(WIN_ROOT.iterdir()):
        if not pkg_dir.is_dir():
            continue
        init_file = pkg_dir / '__init__.py'
        if init_file.exists():
            try:
                text = init_file.read_text(encoding='utf-8-sig').strip()
                if text:
                    # 取第一行非空内容作为描述
                    for line in text.splitlines():
                        line = line.strip().strip('"').strip("'").strip('#').strip()
                        if line:
                            desc[pkg_dir.name] = line
                            break
            except Exception:
                pass
        if pkg_dir.name not in desc:
            desc[pkg_dir.name] = pkg_dir.name
    return desc


def 获取主题列表():
    """动态从 ui.ui_styles 模块导入 THEMES 字典。"""
    try:
        from ui import ui_styles
        themes = []
        for tid, info in ui_styles.THEMES.items():
            name = info.get('name', tid)
            accent = info.get('accent', '')
            themes.append((tid, name, accent))
        return themes
    except Exception as e:
        print(f'  ⚠️ 动态获取主题列表失败: {e}')
        return []


def 分类对话框(classes):
    """根据类继承关系动态分类对话框。
    返回 (标准对话框列表, 无边框对话框列表, QWidget窗口列表)
    """
    base_dialogs = []
    frameless_dialogs = []
    widget_dialogs = []
    for rel, name, bases in classes:
        # 只处理对话框/窗口类
        if not any(k in name for k in ('Dialog', 'Window', 'Page', '对话框', '窗口', '页面')):
            continue
        if name in ('QDialog', 'QWidget', '对话框基类', '无边框缩放Mixin'):
            continue
        base_names = set(bases)
        if '对话框基类' in base_names:
            base_dialogs.append(name)
        elif '无边框缩放Mixin' in base_names:
            frameless_dialogs.append(name)
        elif 'QWidget' in base_names and 'QDialog' not in base_names:
            widget_dialogs.append(name)
    return sorted(base_dialogs), sorted(frameless_dialogs), sorted(widget_dialogs)


def 获取按钮功能清单():
    """动态从主入口提取按钮信号连接，从编译UI提取按钮文字。
    返回 [(按钮名, 按钮文字, 处理函数)]
    """
    main_file = WIN_ROOT / 'app' / 'main.py'
    ui_file = WIN_ROOT / 'ui' / 'Super_ADB.py'
    if not main_file.exists():
        return []
    main_text = main_file.read_text(encoding='utf-8-sig')
    conns = re.findall(r'self\.(\w+)\.clicked\.connect\(self\.(\w+)\)', main_text)

    # 从编译后的 UI 提取按钮 text
    btn_texts = {}
    if ui_file.exists():
        ui_text = ui_file.read_text(encoding='utf-8-sig')
        # 匹配 self.btnXxx.setText(QCoreApplication.translate("主窗口", u"文字", None))
        for m in re.finditer(r'self\.(\w+)\.setText\(QCoreApplication\.translate\([^,]+,\s*u?"([^"]*)"', ui_text):
            btn_name = m.group(1)
            text = m.group(2)
            # 解码 unicode 转义
            try:
                text = text.encode('utf-8').decode('unicode_escape')
            except Exception:
                pass
            btn_texts[btn_name] = text
        # 匹配 self.btnXxx.setText("直接文字")
        for m in re.finditer(r'self\.(\w+)\.setText\("([^"]*)"\)', ui_text):
            btn_name = m.group(1)
            if btn_name not in btn_texts:
                btn_texts[btn_name] = m.group(2)

    result = []
    for btn, fn in conns:
        text = btn_texts.get(btn, '')
        result.append((btn, text, fn))
    return result


def 获取配置文件字段():
    """动态读取配置文件，返回 [(字段名, 类型, 示例值)]。"""
    cfg_file = WIN_ROOT / 'config' / 'super_adb_config.json'
    if not cfg_file.exists():
        return []
    try:
        import json
        data = json.loads(cfg_file.read_text(encoding='utf-8-sig'))
        result = []
        for k, v in data.items():
            result.append((k, type(v).__name__, str(v)[:60]))
        return result
    except Exception:
        return []


def 获取第三方依赖():
    """动态读取 requirements.txt，返回 [(包名, 版本)]。"""
    req_file = PROJECT_ROOT / 'requirements.txt'
    if not req_file.exists():
        return []
    deps = []
    for line in req_file.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            if '==' in line:
                name, ver = line.split('==', 1)
                deps.append((name, ver))
            else:
                deps.append((line, 'latest'))
    return deps


def 获取快捷键():
    """动态从主入口提取快捷键定义。"""
    main_file = WIN_ROOT / 'app' / 'main.py'
    if not main_file.exists():
        return []
    text = main_file.read_text(encoding='utf-8-sig')
    shortcuts = []
    for m in re.finditer(r"QShortcut\(QKeySequence\('([^']+)'\)", text):
        shortcuts.append(m.group(1))
    for m in re.finditer(r'setShortcut\([^)]+\)', text):
        shortcuts.append(m.group()[:60])
    return shortcuts


# ============================================================
# 扫描器
# ============================================================
def scan_python_files():
    """扫描所有 .py 文件，返回 {相对路径: 行数}。"""
    files = {}
    for p in sorted(WIN_ROOT.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        rel = p.relative_to(WIN_ROOT)
        try:
            lines = len(p.read_text(encoding='utf-8-sig').splitlines())
        except Exception:
            lines = 0
        files[str(rel)] = lines
    return files


def scan_classes():
    """扫描所有类定义，返回 [(文件, 类名, [基类])]。"""
    classes = []
    for p in sorted(WIN_ROOT.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        rel = str(p.relative_to(WIN_ROOT))
        try:
            tree = ast.parse(p.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = []
                for b in node.bases:
                    if isinstance(b, ast.Name):
                        bases.append(b.id)
                    elif isinstance(b, ast.Attribute):
                        bases.append(ast.unparse(b))
                classes.append((rel, node.name, bases))
    return classes


def scan_imports():
    """扫描模块间导入依赖，返回 [(源包, 目标包)]。"""
    # 动态获取所有包名（Super_ADB_Win/ 下的子目录）
    已知包 = {d.name for d in WIN_ROOT.iterdir() if d.is_dir()}
    deps = []
    for p in sorted(WIN_ROOT.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        rel = str(p.relative_to(WIN_ROOT))
        src_pkg = rel.split('\\')[0] if '\\' in rel else rel
        try:
            text = p.read_text(encoding='utf-8-sig')
        except Exception:
            continue
        # 匹配 from 包名.模块 import ...
        for m in re.finditer(r'from\s+([\u4e00-\u9fa5\w]+)\.', text):
            target = m.group(1)
            if target != src_pkg and target in 已知包:
                deps.append((src_pkg, target))
    return list(set(deps))


# ============================================================
# 对话框模拟运行截图（离屏渲染）
# ============================================================
def 找主对话框类(stem):
    """用 ast 定位对话框文件里的主弹窗类。优先与文件同名的类，其次第一个
    继承 QDialog/QWidget/对话框基类 的非下划线类。返回 (类名, 基类列表) 或 None。
    """
    f = DIALOG_DIR / f'{stem}.py'
    try:
        tree = ast.parse(f.read_text(encoding='utf-8-sig'))
    except Exception:
        return None
    对话框类 = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        bases = []
        for b in node.bases:
            bases.append(b.id if isinstance(b, ast.Name) else ast.unparse(b))
        if not any(k in node.name for k in ('对话框', '窗口', '页', '菜单', 'Dialog', 'Window', 'Page')):
            continue
        if node.name.startswith('_'):
            continue
        if not any(b in bases for b in ('QDialog', 'QWidget', '对话框基类', '无边框缩放Mixin')):
            continue
        if 'Mixin' in node.name:
            continue
        对话框类.append((node.name, bases))
    if not 对话框类:
        return None
    # 与文件同名的类优先
    for name, bases in 对话框类:
        if name == stem:
            return name, bases
    return 对话框类[0]


class _ADB桩:
    """离屏截图用的 ADB 替身：任何方法都返回 (False, '')，不碰真实设备。"""

    def __getattr__(self, name):
        def _调用(*args, **kwargs):
            return (False, '')
        return _调用


class _主窗口桩:
    """离屏截图用的主窗口替身：提供 .adb 等常见属性。"""

    def __init__(self):
        self.adb = _ADB桩()

    def __getattr__(self, name):
        def _调用(*args, **kwargs):
            return (False, '')
        return _调用


def _构造参数表(cls):
    """根据 __init__ 签名智能填充构造参数（离屏模拟运行所需的最小值）。"""
    from PySide6.QtCore import QThreadPool
    无返回 = lambda *a, **k: None
    映射 = {
        'parent': None, '父': None, 'pair_dialog': None,
        'adb': _ADB桩(), 'pool': QThreadPool(),
        'serial': '127.0.0.1:5555', '序列号': '127.0.0.1:5555',
        '主窗口': _主窗口桩(),
        'theme_id': 'dark_cyan',
        'get_serial': lambda *a, **k: None, '获取序列号': lambda *a, **k: None,
        '状态回调': 无返回, 'on_pair_success': 无返回, 'on_device_connected': 无返回,
        'on_discovered': 无返回, 'func': 无返回, 'factory': 无返回,
        'results': [], 'entries': [], 'events': [], 'ips': [], 'ports': [5555],
        '网段': '192.168.1.0/24', 'ip': '192.168.1.100', 'port': 5555,
        'code': '123456', '配对码': '123456',
        'path': '', 'pcap_path': '', '证书路径': '', 'filepath': '',
        'dirpath': '', 'apk_path': '', 'target': '',
        '任务类型': '用户证书', 'algo_keys': ['MD5', 'SHA1', 'SHA256'],
        'expected_name': 'superadb-TEST', 'name': 'superadb-TEST', 'payload': 'WIFI:T:ADB;S:superadb-TEST;P:123456;;',
        'timeout': 8, '超时ms': 500, '线程数': 64, 'workers': 8, 'max_workers': 64,
        'default_pkg': 'com.example.demo', 'mode': 'request', 'is_crc': False,
    }
    kwargs = {}
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return kwargs
    for name, param in sig.parameters.items():
        if name == 'self':
            continue
        有默认 = param.default is not inspect.Parameter.empty
        if name in 映射:
            kwargs[name] = 映射[name]
        elif not 有默认:
            kwargs[name] = None
    return kwargs


def _初始化离屏环境():
    """初始化离屏渲染环境：offscreen 平台、高DPI、中文字体。
    返回 (app, 是否首次初始化)。必须在任何 Qt 控件创建前调用。
    """
    # offscreen 平台必须在 QApplication 创建之前设置
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    # 高 DPI：2x 缩放让截图更清晰（文字更锐利）
    os.environ.setdefault('QT_SCALE_FACTOR', '2')
    os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')
    os.environ.setdefault('QT_FONT_DPI', '96')
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QFont, QFontDatabase, QIcon
        from PySide6.QtCore import Qt, QCoreApplication
    except Exception as e:
        print(f'  ⚠️ PySide6 不可用，跳过截图: {e}')
        return None, False

    app = QApplication.instance()
    首次 = app is None
    if 首次:
        # Qt6 默认已启用高 DPI，这里只通过环境变量 QT_SCALE_FACTOR 控制缩放倍率
        app = QApplication(sys.argv)

    # 显式设置中文字体：确保 offscreen 模式下中文不显示为方块
    # Windows 优先微软雅黑，回退到系统默认中文字体
    字体候选 = [
        'Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑',
        'PingFang SC', 'Noto Sans CJK SC', 'SimHei', 'SimSun',
        'Segoe UI', 'Arial',
    ]
    选中字体 = None
    for 字体名 in 字体候选:
        if QFontDatabase.hasFamily(字体名):
            选中字体 = 字体名
            break
    if not 选中字体:
        # 如果系统字体库为空（offscreen 常见问题），尝试加载系统字体文件
        import ctypes
        try:
            font_path = r'C:\Windows\Fonts\msyh.ttc'
            if os.path.exists(font_path):
                _id = QFontDatabase.addApplicationFont(font_path)
                if _id >= 0:
                    families = QFontDatabase.applicationFontFamilies(_id)
                    if families:
                        选中字体 = families[0]
        except Exception:
            pass
    if 选中字体:
        font = QFont(选中字体, 9)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        app.setFont(font)
        print(f'  🎨 截图字体: {选中字体}')
    else:
        print(f'  ⚠️ 未找到中文字体，截图可能显示方块')

    return app, 首次


def 生成对话框模拟截图(跳过=False):
    """离屏实例化 dialogs/ 下每个弹窗并截图。

    返回 (截图信息列表, 失败列表)：
      截图信息 = [(文件名stem, 类名, 相对图片路径, docstring, 按钮文字列表)]
      失败列表 = [(文件名stem, 错误摘要)]
    """
    if 跳过:
        return [], []
    app, _ = _初始化离屏环境()
    if app is None:
        return [], []

    from PySide6.QtGui import QPixmap, QPainter, QColor

    成功列表 = []
    失败列表 = []
    files = sorted(p.stem for p in DIALOG_DIR.glob('*.py')
                   if p.name != '__init__.py')
    if not files:
        return [], []

    DIALOG_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    print(f'  离屏渲染 {len(files)} 个对话框...')

    for stem in files:
        找到 = 找主对话框类(stem)
        if not 找到:
            失败列表.append((stem, '未找到主弹窗类'))
            continue
        cls_name, _bases = 找到
        try:
            mod = importlib.import_module(f'对话框.{stem}')
            cls = getattr(mod, cls_name)
        except Exception as e:
            失败列表.append((stem, f'导入失败: {type(e).__name__}: {e}'))
            continue

        dlg = None
        try:
            kwargs = _构造参数表(cls)
            dlg = cls(**kwargs)
            # 确保对话框使用应用字体（某些自定义控件可能单独设置了字体）
            from PySide6.QtGui import QFont
            dlg.setFont(app.font())
            # 让布局生效并触发一次绘制
            dlg.adjustSize()
            if dlg.width() < 420:
                dlg.resize(760, 540)
            dlg.show()
            # 多次 processEvents 确保布局、样式、动画全部生效
            for _ in range(8):
                app.processEvents()

            # ── 为特定对话框填充模拟数据 ──
            try:
                from PySide6.QtWidgets import QTableWidgetItem, QPushButton, QListWidgetItem
                from PySide6.QtGui import QColor
                from PySide6.QtCore import Qt

                if stem == 'adb_terminal_dialog':
                    # ADB 交互式终端：填充命令和输出
                    output = getattr(dlg, 'output', None)
                    if output is not None:
                        output.setPlainText(
                            'super_adb:~$ adb devices\n'
                            'List of devices attached\n'
                            '192.168.1.100:5555\tdevice\n'
                            '\n'
                            'super_adb:~$ adb -s 192.168.1.100:5555 shell\n'
                            'gemini:/ $ whoami\n'
                            'shell\n'
                            'gemini:/ $ pwd\n'
                            '/system/bin\n'
                            'gemini:/ $ ls -la /sdcard/ | head -10\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-29 09:15 Alarms\n'
                            'drwxrwx--x  4 root sdcard_rw 4096 2026-08-28 14:30 Android\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-29 11:20 DCIM\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-27 16:42 Download\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-26 08:10 Movies\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-25 10:05 Music\n'
                            'drwxrwx--x  2 root sdcard_rw 4096 2026-08-20 19:35 Pictures\n'
                            '-rw-rw----  1 root sdcard_rw 33554432 2026-08-26 15:30 app-release.apk\n'
                            'gemini:/ $ dumpsys battery | grep level\n'
                            '  level: 87\n'
                            'gemini:/ $ exit\n'
                            'super_adb:~$ _\n'
                        )
                        # 滚动到底部
                        sb = output.verticalScrollBar()
                        sb.setValue(sb.maximum())

                elif stem == 'lan_scan_dialog':
                    # 局域网扫描：填充模拟扫描结果
                    table = getattr(dlg, 'table', None)
                    if table is not None:
                        模拟设备 = [
                            ('192.168.1.100:5555', '🟢 在线 · 荣耀 ELZ-AN20', '23'),
                            ('192.168.1.101:5555', '🟢 在线 · 小米 13 Pro', '18'),
                            ('192.168.1.102:5555', '🟡 超时', ''),
                            ('192.168.1.103:5555', '🟢 在线 · Pixel 7', '45'),
                            ('192.168.1.104:5555', '🔴 连接被拒绝', ''),
                            ('192.168.1.105:5555', '🟢 在线 · 模拟器', '5'),
                            ('192.168.1.106:5555', '🟡 超时', ''),
                            ('192.168.1.108:5555', '🟢 在线 · OPPO Find X6', '31'),
                            ('192.168.1.110:5555', '🔴 主机不可达', ''),
                            ('192.168.1.120:5555', '🟢 在线 · vivo X90', '27'),
                        ]
                        table.setRowCount(len(模拟设备))
                        for row, (ip, status, latency) in enumerate(模拟设备):
                            # IP 地址
                            item_ip = QTableWidgetItem(ip)
                            table.setItem(row, 0, item_ip)
                            # 状态
                            item_status = QTableWidgetItem(status)
                            if '在线' in status:
                                item_status.setForeground(QColor('#6bcb77'))
                            elif '超时' in status:
                                item_status.setForeground(QColor('#ffd93d'))
                            else:
                                item_status.setForeground(QColor('#ff6b6b'))
                            table.setItem(row, 1, item_status)
                            # 延迟
                            item_latency = QTableWidgetItem(latency + (' ms' if latency else ''))
                            if latency and int(latency) < 30:
                                item_latency.setForeground(QColor('#6bcb77'))
                            elif latency:
                                item_latency.setForeground(QColor('#ffd93d'))
                            table.setItem(row, 2, item_latency)
                            # 操作按钮列
                            btn_widget = QPushButton('连接')
                            btn_widget.setFixedHeight(26)
                            btn_widget.setStyleSheet(
                                'QPushButton { background:#1e88e5; color:white; '
                                'border:none; border-radius:4px; padding:0 12px; }'
                                'QPushButton:hover { background:#42a5f5; }'
                            )
                            table.setCellWidget(row, 3, btn_widget)
                        # 更新进度/状态栏提示
                        hint = getattr(dlg, 'hint_label', None)
                        if hint is not None:
                            hint.setText(f'✅ 扫描完成：共扫描 256 个地址，发现 {sum(1 for _,s,_ in 模拟设备 if "在线" in s)} 台在线设备')

                elif stem == 'ip_scan_dialog':
                    # IP 局域网扫描：模拟"扫描中"状态（进度条 + 部分结果 + 本机高亮）
                    # 网段输入
                    try:
                        dlg.网段输入.setText('192.168.1.0/24')
                    except Exception:
                        pass
                    # 扫描按钮：扫描中，禁用
                    try:
                        dlg.扫描按钮.setText('扫描中...')
                        dlg.扫描按钮.setEnabled(False)
                    except Exception:
                        pass
                    # 本机信息标签
                    try:
                        dlg.本机信息标签.setText('本机 IP: 192.168.1.3 | 推断网段: 192.168.1.0/24')
                    except Exception:
                        pass
                    # 进度条：扫描中 15%
                    try:
                        dlg.进度条.setValue(15)
                        dlg.进度条.setFormat('扫描中... 15% (39/254)')
                    except Exception:
                        pass
                    # 结果表格：只显示扫描过程中发现的 3 台在线设备
                    try:
                        from PySide6.QtGui import QColor as _QColor2
                        扫描中设备 = [
                            ('192.168.1.1', '🟢 在线', 'c8-98-28-f6-e2-ec', '网关 / 路由器', False),
                            ('192.168.1.3', '🟢 在线', '—', '本机', True),
                            ('192.168.1.133', '🟢 在线', 'e4-27-61-ab-61-18', '小米(Xiaomi)', False),
                        ]
                        dlg.表格.setRowCount(len(扫描中设备))
                        for row, (ip, status, mac, remark, is_local) in enumerate(扫描中设备):
                            item_ip = QTableWidgetItem(ip)
                            item_status = QTableWidgetItem(status)
                            item_mac = QTableWidgetItem(mac)
                            item_remark = QTableWidgetItem(remark)
                            item_status.setForeground(_QColor2('#6bcb77'))
                            # 本机行红色高亮
                            if is_local:
                                for item in (item_ip, item_status, item_mac, item_remark):
                                    item.setBackground(_QColor2('#5c1a1a'))
                            dlg.表格.setItem(row, 0, item_ip)
                            dlg.表格.setItem(row, 1, item_status)
                            dlg.表格.setItem(row, 2, item_mac)
                            dlg.表格.setItem(row, 3, item_remark)
                    except Exception:
                        pass
                    # 底部状态标签
                    try:
                        dlg.状态标签.setText('已发现 3 台在线设备')
                    except Exception:
                        pass

                elif stem == 'json_tool_dialog':
                    # JSON 工具：填充左侧输入和右侧格式化输出
                    fmt_input = getattr(dlg, 'fmtInput', None)
                    fmt_output = getattr(dlg, 'fmtOutput', None)
                    示例JSON = (
                        '{\n'
                        '  "app_name": "Super_ADB",\n'
                        '  "version": "2026.08.30",\n'
                        '  "author": "JCS",\n'
                        '  "features": [\n'
                        '    "自研ADB协议栈",\n'
                        '    "无线调试",\n'
                        '    "文件管理",\n'
                        '    "日志抓取",\n'
                        '    "性能监控"\n'
                        '  ],\n'
                        '  "device": {\n'
                        '    "model": "ELZ-AN20",\n'
                        '    "android": 14,\n'
                        '    "serial": "192.168.1.100:5555",\n'
                        '    "battery": 87\n'
                        '  },\n'
                        '  "benchmark": {\n'
                        '    "upload_mbps": 245.6,\n'
                        '    "download_mbps": 187.3,\n'
                        '    "official_upload_mbps": 91.2\n'
                        '  },\n'
                        '  "themes": ["dark_teal", "dark_cyan", "dark_purple", "dark_amber", "light"],\n'
                        '  "open_source": true,\n'
                        '  "license": "MIT"\n'
                        '}\n'
                    )
                    if fmt_input is not None:
                        fmt_input.setPlainText(示例JSON)
                    if fmt_output is not None:
                        # 右侧显示格式化后的JSON（带语法高亮的HTML效果）
                        fmt_output.setPlainText(
                            '✅ 格式化完成\n\n'
                            + 示例JSON
                        )

                elif stem == 'tcpdump_dialog':
                    # tcpdump 抓包：模拟"抓包过程中"状态
                    # 加大窗口高度，让日志区域显示更多内容
                    try:
                        dlg.resize(760, 560)
                    except Exception:
                        pass
                    # 协议下拉框默认 HTTP/HTTPS，保持不变
                    # 按钮状态：开始禁用、停止启用
                    try:
                        dlg.btn_start.setEnabled(False)
                    except Exception:
                        pass
                    try:
                        dlg.btn_stop.setEnabled(True)
                        dlg.btn_stop.setText('■ 停止')
                    except Exception:
                        pass
                    # 状态标签：抓包中（绿色）
                    try:
                        dlg.status_label.setText('抓包中…')
                        dlg.status_label.setStyleSheet('color: #1de9b6;')
                    except Exception:
                        pass
                    # 统计标签：已抓数据量 + 包数 + 时长
                    try:
                        dlg.stat_label.setText('已抓 256 KB · ~1234 包 · 00:15')
                    except Exception:
                        pass
                    # U 盘标签：未检测到
                    try:
                        dlg.usb_label.setText('未检测到U盘')
                    except Exception:
                        pass
                    # 日志区域：填充抓包启动过程日志
                    try:
                        dlg.log_edit.setPlainText(
                            '[检查] 设备上是否安装 tcpdump...\n'
                            '[检查] which: /system/xbin/tcpdump\n'
                            '[检查] version: tcpdump version 4.9.2\n'
                            '[检查] tcpdump 可用: tcpdump version 4.9.2\n'
                            '[检查] 非 root 但 su 可用 → 以 su 提权抓包\n'
                            '$ adb -s 192.168.1.100:5555 shell su -c \'tcpdump -s 0 -w '
                            '/sdcard/Super_ADB/Super_ADB_capture_192.168.1.100_5555_20260830_070620.pcap '
                            '2>/sdcard/Super_ADB/Super_ADB_stderr_192.168.1.100_5555_20260830_070620.log '
                            '"tcp and (port 80 or port 443)"\'\n'
                            '  设备端 pcap:  /sdcard/Super_ADB/Super_ADB_capture_192.168.1.100_5555_20260830_070620.pcap\n'
                            '  本地路径将在停止后 pull 回来\n'
                            '[tcpdump] listening on any, link-type LINUX_SLL (Linux cooked v1), capture size 262144 bytes\n'
                        )
                        # 滚动到底部
                        sb = dlg.log_edit.verticalScrollBar()
                        sb.setValue(sb.maximum())
                    except Exception:
                        pass

                elif stem == 'wifi_pair_dialog':
                    # WiFi 配对码连接：模拟配对成功后的状态
                    try:
                        dlg.resize(620, 560)
                    except Exception:
                        pass
                    # 配对信息填充
                    try:
                        dlg.ip_edit.setText('192.168.1.16')
                    except Exception:
                        pass
                    try:
                        dlg.port_edit.setText('38973')
                    except Exception:
                        pass
                    try:
                        dlg.code_edit.setText('016813')
                    except Exception:
                        pass
                    try:
                        dlg.debug_port_edit.setText('5555')
                    except Exception:
                        pass
                    # 已配对设备：隐藏"暂无"标签，添加已配对设备列表
                    try:
                        from PySide6.QtWidgets import QLabel, QHBoxLayout, QPushButton, QWidget
                        dlg.paired_empty_lbl.setVisible(False)
                        # 清除已有子控件（除了 empty_lbl）
                        while dlg.paired_group_layout.count() > 1:
                            item = dlg.paired_group_layout.takeAt(1)
                            if item.widget():
                                item.widget().deleteLater()
                        已配对列表 = [
                            ('192.168.1.16:5555', '荣耀 ELZ-AN20', '2026-08-30 09:15'),
                            ('192.168.1.101:5555', '小米 13 Pro', '2026-08-29 18:42'),
                            ('192.168.1.105:5555', '模拟器 emulator-5554', '2026-08-28 14:20'),
                        ]
                        for addr, name, time_str in 已配对列表:
                            row = QWidget()
                            row_lay = QHBoxLayout(row)
                            row_lay.setContentsMargins(4, 2, 4, 2)
                            lbl = QLabel(f'📱 {name}  ({addr})  ·  {time_str}')
                            lbl.setStyleSheet('color: #1de9b6; font-size: 12px;')
                            row_lay.addWidget(lbl, 1)
                            btn = QPushButton('重连')
                            btn.setFixedWidth(60)
                            btn.setStyleSheet(
                                'QPushButton { background:#1e88e5; color:white; border:none; '
                                'border-radius:4px; padding:2px 8px; font-size:11px; }'
                                'QPushButton:hover { background:#42a5f5; }')
                            row_lay.addWidget(btn)
                            dlg.paired_group_layout.addWidget(row)
                    except Exception:
                        pass
                    # 连接调试端口按钮启用（配对成功后）
                    try:
                        dlg.btn_connect.setEnabled(True)
                    except Exception:
                        pass
                    # 结果输出：配对成功日志
                    try:
                        dlg.output.setPlainText(
                            '> adb pair 192.168.1.16:38973 016813\n'
                            'Enter pairing code: 016813\n'
                            'Successfully paired to 192.168.1.16:38973 [guid=adb-ELZ-AN20-abc123]\n'
                            '\n'
                            '✅ 配对成功！\n'
                            '> adb connect 192.168.1.16:5555\n'
                            'connected to 192.168.1.16:5555\n'
                            '\n'
                            '✅ 已连接到 192.168.1.16:5555（荣耀 ELZ-AN20）\n'
                        )
                        sb = dlg.output.verticalScrollBar()
                        sb.setValue(sb.maximum())
                    except Exception:
                        pass

                elif stem == 'install_unpack_dialog':
                    # 安装/解包：模拟已加载 APK 的状态
                    try:
                        dlg.resize(820, 620)
                    except Exception:
                        pass
                    # 隐藏拖拽区，显示文件信息
                    try:
                        dlg.drop_area.setVisible(False)
                    except Exception:
                        pass
                    try:
                        dlg.info_label.setText('📦 Super_ADB_v2026.08.30.apk  ·  12.4 MB  ·  2026-08-30 09:15')
                    except Exception:
                        pass
                    # APK 元信息
                    try:
                        dlg.meta_label.setVisible(True)
                        dlg.meta_label.setText(
                            '<b>应用名:</b> Super ADB  ·  <b>包名:</b> com.jcs.superadb<br>'
                            '<b>版本:</b> 2026.08.30 (versionCode=20260830)  ·  <b>SDK:</b> min 24 / target 34<br>'
                            '<b>签名:</b> v1+v2+v3  ·  <b>证书:</b> CN=JCS, O=SuperADB  ·  <b>有效期:</b> 2024-2050<br>'
                            '<b>权限:</b> INTERNET, ACCESS_NETWORK_STATE, WRITE_EXTERNAL_STORAGE ...'
                        )
                    except Exception:
                        pass
                    # 文件目录树
                    try:
                        from PySide6.QtWidgets import QTreeWidgetItem as _QTI3
                        dlg.tree.clear()
                        root = _QTI3(['APK 根目录', ''])
                        dirs = {
                            'META-INF': ['CERT.RSA (2.1 KB)', 'CERT.SF (3.5 KB)', 'MANIFEST.MF (4.2 KB)'],
                            'res': ['layout/', 'drawable/', 'values/', 'xml/'],
                            'assets': ['fonts/', 'icons/', 'config.json (1.2 KB)'],
                            'lib': ['arm64-v8a/', 'armeabi-v7a/', 'x86_64/'],
                        }
                        for dname, files in dirs.items():
                            dnode = _QTI3([dname + '/', f'{len(files)} 项'])
                            for f in files:
                                fnode = _QTI3([f, ''])
                                dnode.addChild(fnode)
                            root.addChild(dnode)
                        for f in ['AndroidManifest.xml', 'classes.dex (8.2 MB)', 'resources.arsc (1.5 MB)']:
                            root.addChild(_QTI3([f, '']))
                        dlg.tree.addTopLevelItem(root)
                        root.setExpanded(True)
                        dlg.tree.setCurrentItem(root.child(0))
                    except Exception:
                        pass
                    # 预览内容
                    try:
                        dlg.preview.setPlainText(
                            'Manifest-Version: 1.0\n'
                            'Created-By: 1.0 (Android)\n'
                            '\n'
                            'Name: res/layout/activity_main.xml\n'
                            'SHA-256-Digest: a1b2c3d4e5f6...\n'
                            '\n'
                            'Name: classes.dex\n'
                            'SHA-256-Digest: f6e5d4c3b2a1...\n'
                        )
                    except Exception:
                        pass
                    # 启用按钮
                    try:
                        dlg.btn_extract.setEnabled(True)
                        dlg.btn_install.setEnabled(True)
                    except Exception:
                        pass
                    # 进度和日志
                    try:
                        dlg.progress_label.setText('就绪')
                        dlg.progress_bar.setValue(0)
                    except Exception:
                        pass
                    try:
                        dlg.log_edit.setPlainText(
                            '[信息] 已加载 APK: Super_ADB_v2026.08.30.apk (12.4 MB)\n'
                            '[信息] 解析完成: 4 个 DEX, 256 个资源, 12 个权限\n'
                            '[信息] 签名验证通过 (v1+v2+v3)\n'
                        )
                    except Exception:
                        pass

                elif stem == 'monkey_stress_window':
                    # Monkey 压测：模拟运行中状态
                    try:
                        dlg.resize(780, 680)
                    except Exception:
                        pass
                    # 基本参数
                    try:
                        dlg.pkg_input.setText('com.jcs.superadb')
                    except Exception:
                        pass
                    try:
                        dlg.count_spin.setValue(1000)
                    except Exception:
                        pass
                    try:
                        dlg.throttle_spin.setValue(100)
                    except Exception:
                        pass
                    try:
                        dlg.seed_input.setText('42')
                    except Exception:
                        pass
                    # 事件比例
                    try:
                        dlg._pct_spins['pct_touch'].setValue(50)
                        dlg._pct_spins['pct_motion'].setValue(20)
                        dlg._pct_spins['pct_trackball'].setValue(-1)
                        dlg._pct_spins['pct_nav'].setValue(-1)
                        dlg._pct_spins['pct_majornav'].setValue(-1)
                        dlg._pct_spins['pct_appswitch'].setValue(10)
                        dlg._pct_spins['pct_anyevent'].setValue(-1)
                    except Exception:
                        pass
                    # 忽略选项
                    try:
                        dlg.ignore_crashes_chk.setChecked(True)
                        dlg.ignore_timeouts_chk.setChecked(True)
                    except Exception:
                        pass
                    # 运行中状态
                    try:
                        dlg.btn_run.setEnabled(False)
                        dlg.btn_stop.setEnabled(True)
                        dlg.btn_pause.setEnabled(True)
                        dlg.btn_replay.setEnabled(True)
                    except Exception:
                        pass
                    try:
                        dlg.status_label.setText('运行中…')
                        dlg.status_label.setStyleSheet('color: #1de9b6;')
                    except Exception:
                        pass
                    try:
                        dlg.version_label.setText('monkey: 1.0 (Android 14)')
                    except Exception:
                        pass
                    try:
                        dlg.stat_label.setText('事件: 634  ·  CRASH: 0  ·  ANR: 0  ·  耗时: 01:23')
                    except Exception:
                        pass
                    # 日志输出
                    try:
                        dlg.log_edit.setPlainText(
                            ':Monkey: seed=42 count=1000\n'
                            ':AllowPackage: com.jcs.superadb\n'
                            ':IncludeCategory: android.intent.category.LAUNCHER\n'
                            ':IncludeCategory: android.intent.category.MONKEY\n'
                            '// Event percentages:\n'
                            '//   0: 15.0% (touch events)\n'
                            '//   1: 10.0% (motion events)\n'
                            '//   2: 15.0% (trackball events)\n'
                            '//   3: 25.0% (nav events)\n'
                            '//   4: 15.0% (majornav events)\n'
                            '//   5: 2.0% (activity launches)\n'
                            '//   6: 2.0% (app switches)\n'
                            '//   7: 1.0% (flips)\n'
                            '//   8: 15.0% (any events)\n'
                            '// Switching to real time and real log\n'
                            ':Sending event #0 (touch) at 0ms\n'
                            ':Sending event #1 (motion) at 100ms\n'
                            ':Sending event #2 (touch) at 200ms\n'
                            '...\n'
                            ':Sending event #634 (nav) at 83400ms\n'
                            'Events injected: 634\n'
                        )
                        sb = dlg.log_edit.verticalScrollBar()
                        sb.setValue(sb.maximum())
                    except Exception:
                        pass
                    # 饼图
                    try:
                        dlg.pie_chart.setVisible(True)
                        dlg.pie_chart.set_data({
                            '触摸': 318, '滑动': 127, '导航': 95,
                            '主导航': 50, '应用切换': 13, '按键': 31,
                        })
                    except Exception:
                        pass
                    # 预览命令
                    try:
                        dlg.cmd_label.setText(
                            'adb shell monkey -p com.jcs.superadb -s 42 '
                            '--throttle 100 --pct-touch 50 --pct-motion 20 '
                            '--pct-appswitch 10 --ignore-crashes --ignore-timeouts -v 1000'
                        )
                    except Exception:
                        pass

                elif stem == 'device_info_dialog':
                    # 设备信息：填充 getprop 和标识符
                    try:
                        dlg.resize(820, 640)
                    except Exception:
                        pass
                    try:
                        dlg.edit_getprop.setPlainText(
                            '[ro.build.version.release]: [14]\n'
                            '[ro.build.version.sdk]: [34]\n'
                            '[ro.build.version.incremental]: [20260830]\n'
                            '[ro.build.display.id]: [SuperADB_OS_14.0.0_20260830]\n'
                            '[ro.product.brand]: [Google]\n'
                            '[ro.product.manufacturer]: [Google]\n'
                            '[ro.product.model]: [Pixel 8 Pro]\n'
                            '[ro.product.name]: [husky]\n'
                            '[ro.product.device]: [husky]\n'
                            '[ro.board.platform]: [tensor3]\n'
                            '[ro.hardware]: [husky]\n'
                            '[ro.build.fingerprint]: [google/husky/husky:14/AP1A.20260830/001:user/release-keys]\n'
                            '[ro.build.type]: [user]\n'
                            '[ro.build.tags]: [release-keys]\n'
                            '[ro.serialno]: [emulator-5554]\n'
                            '[ro.boot.serialno]: [emulator-5554]\n'
                            '[persist.sys.locale]: [zh-CN]\n'
                            '[ro.product.locale]: [zh-CN]\n'
                            '[gsm.sim.operator.alpha]: [China Mobile]\n'
                            '[gsm.network.type]: [LTE]\n'
                            '[wifi.interface]: [wlan0]\n'
                            '[dhcp.wlan0.ipaddress]: [192.168.1.16]\n'
                            '[ro.config.ringtone]: [miui.ogg]\n'
                            '[ro.config.notification_sound]: [notification.ogg]\n'
                            '[dalvik.vm.heapstartsize]: [8m]\n'
                            '[dalvik.vm.heapgrowthlimit]: [256m]\n'
                            '[dalvik.vm.heapsize]: [512m]\n'
                        )
                    except Exception:
                        pass
                    try:
                        dlg.edit_ids.setPlainText(
                            '✅ Android ID: a1b2c3d4e5f67890\n'
                            '✅ IMEI (IMEI1): 861234567890123\n'
                            '✅ IMEI (IMEI2): 861234567890124\n'
                            '✅ MEID: 99012345678901\n'
                            '✅ 序列号 (Serial): emulator-5554\n'
                            '✅ MAC 地址 (wlan0): 02:00:00:00:00:01\n'
                            '✅ Bluetooth MAC: 02:00:00:00:00:02\n'
                            '✅ 设备品牌: Google\n'
                            '✅ 设备型号: Pixel 8 Pro\n'
                            '✅ 设备代号: husky\n'
                            '✅ 制造商: Google\n'
                            '✅ Android 版本: 14 (SDK 34)\n'
                            '✅ 构建号: SuperADB_OS_14.0.0_20260830\n'
                            '✅ 指纹: google/husky/husky:14/AP1A.20260830/001:user/release-keys\n'
                            '✅ 区域: zh-CN\n'
                            '✅ 运营商: China Mobile\n'
                            '✅ 网络类型: LTE\n'
                            '✅ IP 地址: 192.168.1.16\n'
                            '\n'
                            '📊 共获取 22 项标识符，全部成功'
                        )
                    except Exception:
                        pass

                elif stem == 'hash_check_dialog':
                    # 哈希校验：模拟已计算完成的结果
                    try:
                        dlg.resize(780, 620)
                    except Exception:
                        pass
                    # 隐藏拖拽区
                    try:
                        dlg.drop_area.setVisible(False)
                    except Exception:
                        pass
                    # 文件计数
                    try:
                        dlg.lbl_count.setText('共 2 个文件')
                    except Exception:
                        pass
                    # 添加哈希结果行
                    try:
                        from PySide6.QtWidgets import (QLabel, QHBoxLayout, QVBoxLayout,
                                                         QPushButton, QProgressBar, QWidget)
                        from PySide6.QtGui import QFont
                        # 清除已有结果
                        while dlg.result_layout.count() > 1:
                            item = dlg.result_layout.takeAt(0)
                            if item.widget():
                                item.widget().deleteLater()

                        模拟结果 = [
                            ('Super_ADB_v2026.08.30.apk', '12.4 MB', {
                                'MD5': 'a1b2c3d4e5f6789012345678abcdef0',
                                'SHA1': 'fedcba9876543210fedcba9876543210fedcba9',
                                'SHA256': '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
                                'CRC32': '1a2b3c4d',
                            }),
                            ('README.md', '4.2 KB', {
                                'MD5': 'f0e1d2c3b4a5968778695a4b3c2d1e0f',
                                'SHA1': '0123456789abcdef0123456789abcdef01234567',
                                'SHA256': 'fedcba9876543210fedcba9876543210fedcba9876543210fedcba987654321',
                                'CRC32': '4d3c2b1a',
                            }),
                        ]

                        for fname, fsize, hashes in 模拟结果:
                            row = QWidget()
                            row_lay = QVBoxLayout(row)
                            row_lay.setContentsMargins(8, 6, 8, 6)
                            row_lay.setSpacing(4)
                            # 顶部行
                            top = QHBoxLayout()
                            top.setSpacing(8)
                            lbl_name = QLabel(fname)
                            lbl_name.setStyleSheet('color: #1de9b6; font-weight: bold;')
                            top.addWidget(lbl_name)
                            lbl_size = QLabel(fsize)
                            lbl_size.setStyleSheet('color: #9e9e9e; font-size: 9pt;')
                            top.addWidget(lbl_size)
                            bar = QProgressBar()
                            bar.setRange(0, 100)
                            bar.setValue(100)
                            bar.setFixedHeight(14)
                            bar.setTextVisible(False)
                            top.addWidget(bar, 1)
                            row_lay.addLayout(top)
                            # 每个算法一行
                            for algo, val in hashes.items():
                                h = QHBoxLayout()
                                h.setSpacing(6)
                                tag = QLabel(algo)
                                tag.setStyleSheet('color: #1de9b6; font-size: 9pt; font-weight: bold;')
                                tag.setFixedWidth(80)
                                h.addWidget(tag)
                                vlabel = QLabel(val)
                                vlabel.setFont(QFont('Consolas', 10))
                                vlabel.setStyleSheet('color: #a7ffeb;')
                                vlabel.setTextInteractionFlags(0x1)
                                h.addWidget(vlabel, 1)
                                btn = QPushButton('复制')
                                btn.setFixedWidth(60)
                                btn.setFixedHeight(26)
                                h.addWidget(btn)
                                row_lay.addLayout(h)
                            dlg.result_layout.insertWidget(dlg.result_layout.count() - 1, row)
                    except Exception:
                        pass

                elif stem == 'pcap_parse_dialog':
                    # PCAP 解析器：模拟解析完成后的完整状态（自做模拟，不改对话框源码）
                    # 覆盖延迟显示的拖拽遮罩方法（__init__ 中 QTimer.singleShot(200) 会调用它）
                    try:
                        dlg._show_drag_overlay = lambda: None
                    except Exception:
                        pass
                    # 隐藏可能已显示的遮罩
                    try:
                        if hasattr(dlg, '_drag_overlay') and dlg._drag_overlay is not None:
                            dlg._drag_overlay.hide()
                    except Exception:
                        pass
                    try:
                        dlg.resize(1280, 800)
                    except Exception:
                        pass
                    # 窗口标题显示文件名
                    try:
                        dlg.setWindowTitle('PCAP 解析器 — tcpdump_192.168.1.3_5555_20260830_072041.pcap')
                    except Exception:
                        pass
                    # 统计栏：解析完成信息
                    try:
                        dlg._stat_label.setText(
                            '解析完成 [共 295 个流/HTTPS:192/TCP:91/HTTP:12/'
                            '总包: 12535/IP:12535(100%)/非IP:0(0%)/'
                            'TCP:12535/HTTP包:24/TLS包:192/用时 0.2s]'
                        )
                    except Exception:
                        pass
                    # 导出按钮启用
                    try:
                        dlg.btn_export.setEnabled(True)
                    except Exception:
                        pass
                    # 域名筛选下拉框填充
                    try:
                        dlg.domain_combo.addItem('全部域名')
                        dlg.domain_combo.addItem('app-sc.a208.ottcn.com')
                        dlg.domain_combo.addItem('display-sc.a208.ottcn.com')
                        dlg.domain_combo.addItem('ggxtv.a208.ottcn.com')
                        dlg.domain_combo.setCurrentIndex(0)
                    except Exception:
                        pass

                    # 左侧结构树填充（域名 → 路径 → 请求）
                    try:
                        from PySide6.QtWidgets import QTreeWidgetItem
                        from PySide6.QtGui import QColor

                        tree = dlg.structure_tree
                        tree.clear()

                        def _make_item(text, method='', size='', color=None):
                            item = QTreeWidgetItem([text, method, size])
                            if color:
                                item.setForeground(1, QColor(color))
                            return item

                        # 域名1：display-sc.a208.ottcn.com（展开并选中）
                        domain1 = _make_item('display-sc.a208.ottcn.com', '', '30')
                        path1 = _make_item('request', '', '6')
                        path2 = _make_item('sdk10', '', '6')
                        req1 = _make_item('sdk10?cid=6E41B129BB...', 'POST 200', '2.3 KB', '#1de9b6')
                        req2 = _make_item('sdk10?cid=CD41566947...', 'POST 200', '1.7 KB', '#1de9b6')
                        req3 = _make_item('sdk10?cid=CD41566947...', 'POST 200', '1.6 KB', '#1de9b6')
                        req4 = _make_item('sdk10?cid=0A92C9705F...', 'POST 200', '1.6 KB', '#1de9b6')
                        req5 = _make_item('sdk10?cid=5FB138566C...', 'POST 200', '2.2 KB', '#1de9b6')
                        req6 = _make_item('sdk10?cid=B90B176D8F...', 'POST 200', '2.2 KB', '#1de9b6')
                        path2.addChildren([req1, req2, req3, req4, req5, req6])
                        path1.addChild(path2)
                        domain1.addChild(path1)
                        tree.addTopLevelItem(domain1)
                        domain1.setExpanded(True)
                        path1.setExpanded(True)
                        path2.setExpanded(True)

                        # 域名2：app-sc.a208.ottcn.com
                        domain2 = _make_item('app-sc.a208.ottcn.com', '', '3')
                        tree.addTopLevelItem(domain2)

                        # 域名3：display.a208.ottcn.com
                        domain3 = _make_item('display.a208.ottcn.com', '', '3')
                        tree.addTopLevelItem(domain3)

                        # 域名4：dpgwtm-cache.a208.ottcn.com
                        domain4 = _make_item('dpgwtm-cache.a208.ottcn.com', '', '1')
                        tree.addTopLevelItem(domain4)

                        # 域名5：ggc.a208.ottcn.com
                        domain5 = _make_item('ggc.a208.ottcn.com', '', '1')
                        tree.addTopLevelItem(domain5)

                        # 域名6：ggictv.a208.ottcn.com
                        domain6 = _make_item('ggictv.a208.ottcn.com', '', '4')
                        tree.addTopLevelItem(domain6)

                        # 域名7：ggv.a208.ottcn.com
                        domain7 = _make_item('ggv.a208.ottcn.com', '', '1')
                        tree.addTopLevelItem(domain7)

                        # 域名8：ggxtv.a208.ottcn.com（含子路径）
                        domain8 = _make_item('ggxtv.a208.ottcn.com', '', '6')
                        p8_1 = _make_item('request', '', '6')
                        p8_2 = _make_item('sdk10', '', '6')
                        domain8.addChild(p8_1)
                        p8_1.addChild(p8_2)
                        tree.addTopLevelItem(domain8)

                        # 更多域名
                        for name, cnt in [
                            ('gslbmgsplive.a208.ottcn.com', '1'),
                            ('hlszy mgsplive.a208.ottcn.com', '1'),
                            ('img.a208.ottcn.com', '2'),
                            ('img.cmvideo.cn', '4'),
                            ('middledata.ldmnq.com', '1'),
                            ('play.a208.ottcn.com', '6'),
                            ('program-sc.a208.ottcn.com', '1'),
                            ('public-operbiz7.miguvideo.com', '2'),
                            ('vmesh.a208.ottcn.com', '6'),
                            ('vms-sc.a208.ottcn.com', '6'),
                        ]:
                            tree.addTopLevelItem(_make_item(name, '', cnt))

                        # 选中第二个请求（POST 200 1.7KB）
                        tree.setCurrentItem(req2)
                    except Exception:
                        pass

                    # 右侧：切换到"内容"标签页
                    try:
                        content_idx = dlg.tabs.indexOf(dlg.content_tab)
                        if content_idx >= 0:
                            dlg.tabs.setCurrentIndex(content_idx)
                    except Exception:
                        pass

                    # 请求体查看器：填充请求头
                    try:
                        from PySide6.QtWidgets import QTreeWidgetItem as _QTI
                        req_headers = dlg.req_body_viewer._editors['headers']
                        req_headers.clear()
                        req_headers_data = [
                            ('keep-alive', 'false'),
                            ('charset', 'utf-8'),
                            ('content-type', 'application/json'),
                            ('x-protocol-ver', '2.1'),
                            ('x-encryption', 'MIGUEncryption'),
                            ('user-agent', 'ggxtv.a208.ottcn.com'),
                            ('host', 'ggxtv.a208.ottcn.com'),
                            ('connection', 'Keep-Alive'),
                            ('accept-encoding', 'gzip'),
                            ('content-length', '551'),
                        ]
                        for k, v in req_headers_data:
                            req_headers.addTopLevelItem(_QTI([k, v]))
                    except Exception:
                        pass

                    # 响应体查看器：填充响应头
                    try:
                        from PySide6.QtWidgets import QTreeWidgetItem as _QTI2
                        resp_headers = dlg.resp_body_viewer._editors['headers']
                        resp_headers.clear()
                        resp_headers_data = [
                            ('server', 'nginx'),
                            ('date', 'Sat, 29 Aug 2026 23:20:50 GMT'),
                            ('content-type', 'application/json; charset=utf-8'),
                            ('content-length', '403'),
                            ('connection', 'keep-alive'),
                            ('p3p', 'CP=CURa ADMa DEVa PSAo PSDo OUR BUS UNI PUR INT DEM STA PRE COM NAV OTC NOI DSP COR'),
                            ('set-cookie', 'REMEMBER_CODE=cb6b5126-fee4-423b-9d69-e44e784647fe;domain=ottcn.com;path=/;Max...'),
                        ]
                        for k, v in resp_headers_data:
                            resp_headers.addTopLevelItem(_QTI2([k, v]))
                        # 响应体查看器也切到"头部"子标签
                        dlg.resp_body_viewer.view_tabs.setCurrentIndex(0)
                    except Exception:
                        pass
                    # 最后再确保遮罩被隐藏（覆盖方法后理论上不会再显示）
                    try:
                        if hasattr(dlg, '_drag_overlay') and dlg._drag_overlay is not None:
                            dlg._drag_overlay.hide()
                    except Exception:
                        pass

                for _ in range(5):
                    app.processEvents()
            except Exception:
                pass
            # 使用 render 而非 grab 获得更高质量渲染
            from PySide6.QtCore import QRect, QPoint
            from PySide6.QtGui import QImage
            size = dlg.size()
            # 高分辨率渲染：以 devicePixelRatio 倍率输出
            dpr = max(dlg.devicePixelRatioF(), 2.0)
            img = QImage(int(size.width() * dpr), int(size.height() * dpr),
                         QImage.Format.Format_ARGB32)
            img.setDevicePixelRatio(dpr)
            img.fill(QColor('#141a22'))
            painter = QPainter(img)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            dlg.render(painter, QPoint(0, 0))
            painter.end()
            if img.width() < 20 or img.height() < 20:
                raise RuntimeError('截图尺寸异常')
            # 合成到带边距的深色画布上（半透明无边框弹窗需要衬底）
            margin = int(20 * dpr)
            canvas_w = img.width() + margin * 2
            canvas_h = img.height() + margin * 2
            canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32)
            canvas.setDevicePixelRatio(dpr)
            canvas.fill(QColor('#0f141a'))
            painter = QPainter(canvas)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(margin, margin, img)
            painter.end()
            out = DIALOG_SHOT_DIR / f'{stem}.png'
            canvas.save(str(out), 'PNG', 100)

            doc = (mod.__doc__ or '').strip()
            成功列表.append((stem, cls_name, f'docs/截图/dialogs/{stem}.png', doc))
            print(f'    ✅ {stem}')
        except Exception as e:
            失败列表.append((stem, f'{type(e).__name__}: {e}'))
            print(f'    ❌ {stem} — {type(e).__name__}: {e}')
        finally:
            if dlg is not None:
                try:
                    dlg.close()
                    dlg.deleteLater()
                except Exception:
                    pass
                app.processEvents()

    return 成功列表, 失败列表


def _高质量截图(widget, out_path, 背景色='#0f141a', 边距=24):
    """对 widget 进行高质量离屏截图（高DPI + 抗锯齿 + 文字抗锯齿）。
    保存为 PNG 到 out_path，返回 True/False。
    """
    from PySide6.QtGui import QImage, QPainter, QColor
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return False

    # 确保字体生效
    widget.setFont(app.font())
    widget.show()
    for _ in range(10):
        app.processEvents()

    size = widget.size()
    if size.width() < 50 or size.height() < 50:
        return False

    dpr = max(widget.devicePixelRatioF(), 2.0)
    img = QImage(int(size.width() * dpr), int(size.height() * dpr),
                 QImage.Format.Format_ARGB32)
    img.setDevicePixelRatio(dpr)
    img.fill(QColor(背景色))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    widget.render(painter, QPoint(0, 0))
    painter.end()

    if img.width() < 20 or img.height() < 20:
        return False

    # 添加边距画布
    margin = int(边距 * dpr)
    canvas_w = img.width() + margin * 2
    canvas_h = img.height() + margin * 2
    canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32)
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(QColor(背景色))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawImage(margin, margin, img)
    painter.end()

    return canvas.save(str(out_path), 'PNG', 100)


def 生成主界面截图(跳过=False):
    """生成主界面及核心功能页面截图。
    返回 {截图标识: 相对路径} 字典。
    """
    if 跳过:
        return {}
    app, _ = _初始化离屏环境()
    if app is None:
        return {}

    MAIN_SHOT_DIR.mkdir(parents=True, exist_ok=True)
    结果 = {}
    print(f'  生成主界面及功能截图...')

    # ── 1. 主界面截图 ──
    try:
        # 动态导入 png_rc 以加载资源（图标等）
        try:
            from ui import png_rc  # noqa: F401
        except Exception:
            pass

        from ui.ui_styles import get_stylesheet, DEFAULT_THEME

        # 用桩对象替换 ADB 操作，避免真实设备连接
        import tools.adb_tools as _adb_mod
        _原始Adb设备操作 = getattr(_adb_mod, 'Adb设备操作', None)

        class _MockAdb设备操作:
            def __init__(self, log_callback=None, **kwargs):
                self.已连接 = False
                self.设备列表 = []
                self._log_callback = log_callback
            def __getattr__(self, name):
                def _调用(*args, **kwargs):
                    return (False, '')
                return _调用

        _adb_mod.Adb设备操作 = _MockAdb设备操作

        # 导入主窗口
        from app.main import 主窗口

        win = None
        try:
            win = 主窗口()
            # 展开右侧面板（默认是折叠的）
            try:
                win.splitter_main.setSizes([380, 1420])
            except Exception:
                pass
            # 设置合理尺寸（宽度加大确保右侧面板完整显示，高度加大确保底部状态栏完整）
            win.resize(1800, 1050)
            win.show()
            for _ in range(15):
                app.processEvents()

            # ── 填充模拟数据，让截图更真实 ──
            try:
                from PySide6.QtWidgets import QListWidgetItem
                from PySide6.QtGui import QColor, QStandardItemModel, QStandardItem
                from PySide6.QtCore import Qt

                # 1. 设备下拉框填充模拟设备
                模拟设备 = ['192.168.1.100:5555  (荣耀 ELZ-AN20)',
                           'emulator-5554  (Android Emulator)',
                           'ABCDEF12345678  (USB · 小米 13)']
                for combo_name in ('deviceCombo', 'fileMgr_deviceCombo', 'logViewer_deviceCombo'):
                    combo = getattr(win, combo_name, None)
                    if combo is not None:
                        combo.clear()
                        combo.addItems(模拟设备)
                        combo.setCurrentIndex(0)

                # 2. IP 输入框填充示例IP
                ip_input = getattr(win, 'ipInput', None)
                if ip_input is not None:
                    ip_input.setText('192.168.1.100:5555')

                # 3. 包名输入框填充示例包名
                pkg_input = getattr(win, 'pkgInput', None)
                if pkg_input is not None:
                    pkg_input.setText('com.tencent.mm')

                # 4. 命令输出框填充模拟输出
                output_box = getattr(win, 'output', None)
                if output_box is not None:
                    output_box.setPlainText(
                        '$ adb devices\n'
                        'List of devices attached\n'
                        '192.168.1.100:5555\tdevice\n'
                        '\n'
                        '$ adb -s 192.168.1.100:5555 shell getprop ro.product.model\n'
                        'ELZ-AN20\n'
                        '\n'
                        '$ adb -s 192.168.1.100:5555 shell dumpsys battery\n'
                        '  level: 87\n'
                        '  scale: 100\n'
                        '  temperature: 320\n'
                        '  plugged: 2\n'
                        '\n'
                        '✓ 命令执行完成 (1.23s)'
                    )

                # 5. 日志列表填充模拟 logcat 输出
                log_list = getattr(win, 'logViewer_textEdit', None)
                if log_list is not None:
                    模拟日志 = [
                        ('I', 'ActivityManager', 'Start proc com.tencent.mm for activity com.tencent.mm/.ui.LauncherUI'),
                        ('D', 'AndroidRuntime', 'Calling main entry com.tencent.mm.app.MMApplication'),
                        ('W', 'ResourceType', 'No package identifier when getting name for resource number 0x00000000'),
                        ('E', 'SQLiteLog', '(1) no such table: message_tb'),
                        ('I', 'Choreographer', 'Skipped 36 frames!  The application may be doing too much work on its main thread.'),
                        ('D', 'ViewRootImpl', 'draw start, this = ViewRoot{2a8e9f5 显示界面},win = Window{c3a6d22 u0 显示界面}'),
                        ('I', 'System.out', 'onCreate: savedInstanceState = null'),
                        ('V', 'Camera2Manager', 'open camera id 0, package=com.tencent.mm'),
                        ('W', 'MediaPlayer', 'Couldn\'t open content://media/external/audio/media/12345: java.io.FileNotFoundException'),
                        ('E', 'AndroidRuntime', 'FATAL EXCEPTION: main'),
                        ('E', 'AndroidRuntime', 'Process: com.tencent.mm, PID: 12345'),
                        ('E', 'AndroidRuntime', 'java.lang.NullPointerException: Attempt to invoke virtual method'),
                    ]
                    log_list.clear()
                    for 级别, 标签, 消息 in 模拟日志:
                        item = QListWidgetItem(f'{级别}/{标签}: {消息}')
                        if 级别 == 'E':
                            item.setForeground(QColor('#ff6b6b'))
                        elif 级别 == 'W':
                            item.setForeground(QColor('#ffd93d'))
                        elif 级别 == 'I':
                            item.setForeground(QColor('#6bcb77'))
                        else:
                            item.setForeground(QColor('#9ca3af'))
                        log_list.addItem(item)
                    log_list.setCurrentRow(0)

                # 6. 文件管理树填充模拟文件列表
                file_tree = getattr(win, 'fileMgr_tree', None)
                if file_tree is not None:
                    model = QStandardItemModel()
                    model.setHorizontalHeaderLabels(['名称', '大小', '权限', '修改时间'])
                    root = model.invisibleRootItem()

                    def _加文件(父, 名称, 大小, 权限, 时间, 是目录=False):
                        items = [QStandardItem(名称), QStandardItem(大小),
                                 QStandardItem(权限), QStandardItem(时间)]
                        for it in items:
                            if 是目录:
                                it.setForeground(QColor('#4fc3f7'))
                        父.appendRow(items)
                        return items[0]

                    # 模拟文件结构
                    dcim = _加文件(root, 'DCIM', '', 'drwxrwxr-x', '2026-08-28 14:30', True)
                    _加文件(dcim, 'Camera', '', 'drwxrwxr-x', '2026-08-29 09:15', True)
                    _加文件(dcim, 'Screenshots', '', 'drwxrwxr-x', '2026-08-27 16:42', True)
                    _加文件(root, 'Download', '', 'drwxrwxr-x', '2026-08-29 11:20', True)
                    _加文件(root, 'Pictures', '', 'drwxrwxr-x', '2026-08-26 08:10', True)
                    _加文件(root, 'Movies', '', 'drwxrwxr-x', '2026-08-20 19:35', True)
                    _加文件(root, 'Music', '', 'drwxrwxr-x', '2026-08-15 12:00', True)
                    _加文件(root, 'Documents', '', 'drwxrwxr-x', '2026-08-25 10:05', True)
                    _加文件(root, 'Android', '', 'drwxrwx--x', '2026-08-29 07:00', True)
                    _加文件(root, 'IMG_20260829_091532.jpg', '3.2 MB', '-rw-rw----', '2026-08-29 09:15')
                    _加文件(root, 'VID_20260828_201500.mp4', '128.5 MB', '-rw-rw----', '2026-08-28 20:15')
                    _加文件(root, 'backup_20260827.ab', '256.0 MB', '-rw-rw-r--', '2026-08-27 23:10')
                    _加文件(root, 'app-release.apk', '45.2 MB', '-rw-rw-r--', '2026-08-26 15:30')
                    _加文件(root, 'logcat_20260829.txt', '8.7 MB', '-rw-rw-r--', '2026-08-29 08:45')

                    file_tree.setModel(model)
                    file_tree.setColumnWidth(0, 280)
                    file_tree.setColumnWidth(1, 80)
                    file_tree.setColumnWidth(2, 90)
                    file_tree.expand(model.index(0, 0))

                # 7. 更新状态栏文字
                if hasattr(win, 'statusBar'):
                    win.statusBar.showMessage('  ✓ 已连接: 192.168.1.100:5555  (荣耀 ELZ-AN20 · Android 14)  |  电池: 87%  |  自研ADB模式')

                # 8. 更新文件路径标签
                path_label = getattr(win, 'fileMgr_pathLabel', None)
                if path_label is not None:
                    path_label.setText('/storage/emulated/0')

                # 9. 日志计数标签
                count_label = getattr(win, 'logViewer_countLabel', None)
                if count_label is not None:
                    count_label.setText('累计 12 行 | 匹配 12')

                # 10. 日志模式标签
                mode_label = getattr(win, 'logViewer_modeLabel', None)
                if mode_label is not None:
                    mode_label.setText('实时抓取中')

                for _ in range(5):
                    app.processEvents()
            except Exception as e:
                print(f'    ℹ️ 部分模拟数据填充失败: {type(e).__name__}')

            # 主界面截图（截图前强制设置尺寸，防止 show() 触发布局重置）
            win.resize(1800, 1050)
            try:
                win.splitter_main.setSizes([380, 1420])
            except Exception:
                pass
            for _ in range(5):
                app.processEvents()
            out = MAIN_SHOT_DIR / '主界面.png'
            if _高质量截图(win, out, 背景色='#0a0e14', 边距=16):
                结果['主界面'] = 'docs/截图/主界面.png'
                print(f'    ✅ 主界面')
            else:
                print(f'    ❌ 主界面 — 截图失败')

            # ── 2. 文件管理 Tab 截图 ──
            try:
                # 切换到文件管理相关的tab
                if hasattr(win, 'tabWidget_2'):
                    win.tabWidget_2.setCurrentIndex(0)
                for _ in range(8):
                    app.processEvents()
                win.resize(1800, 1050)
                try:
                    win.splitter_main.setSizes([380, 1420])
                except Exception:
                    pass
                for _ in range(3):
                    app.processEvents()
                out = MAIN_SHOT_DIR / '文件管理.png'
                if _高质量截图(win, out, 背景色='#0a0e14', 边距=16):
                    结果['文件管理'] = 'docs/截图/文件管理.png'
                    print(f'    ✅ 文件管理')
            except Exception as e:
                print(f'    ❌ 文件管理 — {type(e).__name__}: {e}')

            # ── 3. 日志抓取 Tab 截图 ──
            try:
                # 右侧 tab 可能只有一个"文件管理与日志"，日志在下方
                # 尝试展开日志区域
                if hasattr(win, 'splitter_log'):
                    win.splitter_log.setSizes([400, 300])
                for _ in range(8):
                    app.processEvents()
                win.resize(1800, 1050)
                try:
                    win.splitter_main.setSizes([380, 1420])
                except Exception:
                    pass
                for _ in range(3):
                    app.processEvents()
                out = MAIN_SHOT_DIR / '日志抓取.png'
                if _高质量截图(win, out, 背景色='#0a0e14', 边距=16):
                    结果['日志抓取'] = 'docs/截图/日志抓取.png'
                    print(f'    ✅ 日志抓取')
            except Exception as e:
                print(f'    ❌ 日志抓取 — {type(e).__name__}: {e}')

        finally:
            if win is not None:
                try:
                    win.close()
                    win.deleteLater()
                except Exception:
                    pass
            # 恢复原始 Adb设备操作
            if _原始Adb设备操作 is not None:
                _adb_mod.Adb设备操作 = _原始Adb设备操作
            for _ in range(5):
                app.processEvents()
    except Exception as e:
        print(f'    ⚠️ 主界面截图跳过: {type(e).__name__}: {e}')

    # ── 4. 性能监控窗口截图（独立窗口，含 CPU/内存/网络/电池图表） ──
    try:
        from monitoring.device_performance_monitor import 设备性能监控
        perf_win = 设备性能监控(serial='emulator-5554')
        # 停止定时器并标记关闭，避免后台线程持续采样
        try:
            perf_win._timer.stop()
        except Exception:
            pass
        perf_win._closed = True
        perf_win._paused = True
        for _ in range(10):
            app.processEvents()

        # 向四张图表填充模拟数据，让截图有真实曲线
        try:
            import random
            random.seed(42)
            n_points = 60

            # CPU 使用率（5%~35% 波动）
            cpu_vals = [max(0.5, 15 + random.uniform(-10, 20)) for _ in range(n_points)]
            for v in cpu_vals:
                perf_win._cpu_chart.add_point('总CPU', v, failed=False)

            # 内存占用（400~500 MB）
            mem_vals = [450 + random.uniform(-50, 50) for _ in range(n_points)]
            for v in mem_vals:
                perf_win._mem_chart.add_point('内存', v, failed=False)

            # 网络速率（接收 0~200 KB/s，发送 0~50 KB/s）
            rx_vals = [max(0, 100 + random.uniform(-80, 120)) for _ in range(n_points)]
            tx_vals = [max(0, 20 + random.uniform(-15, 30)) for _ in range(n_points)]
            for rx, tx in zip(rx_vals, tx_vals):
                perf_win._net_chart.add_point('↓接收', rx, failed=False)
                perf_win._net_chart.add_point('↑发送', tx, failed=False)

            # 电池温度（30~35°C）
            batt_vals = [32 + random.uniform(-2, 3) for _ in range(n_points)]
            for v in batt_vals:
                perf_win._batt_chart.add_point('温度', v, failed=False)

            # 更新顶部信息栏（注意：UI 自带"保留点数:"标签，此处不要重复）
            perf_win._info_label.setText(
                f'CPU {cpu_vals[-1]:.1f}%  内存 {mem_vals[-1]:.0f} MB'
            )

            # 更新各图表下方的统计标签
            def _stats_text(values, unit='', precision=1):
                if not values:
                    return ''
                return (f'最高值: {max(values):.{precision}f}{unit}  '
                        f'平均值: {sum(values) / len(values):.{precision}f}{unit}  '
                        f'最低值: {min(values):.{precision}f}{unit}')

            perf_win._cpu_stats.setText(_stats_text(cpu_vals, '%'))
            perf_win._mem_stats.setText(_stats_text(mem_vals, 'MB', 0))
            perf_win._net_stats.setText(_stats_text(rx_vals, 'KB/s'))
            perf_win._batt_stats.setText(_stats_text(batt_vals, '°C'))
        except Exception as e:
            print(f'    ⚠️ 性能监控模拟数据填充跳过: {e}')

        for _ in range(8):
            app.processEvents()

        out = MAIN_SHOT_DIR / '性能监控.png'
        if _高质量截图(perf_win, out, 背景色='#0a0e14', 边距=16):
            结果['性能监控'] = 'docs/截图/性能监控.png'
            print(f'    ✅ 性能监控')
        try:
            perf_win.close()
            perf_win.deleteLater()
        except Exception:
            pass
        for _ in range(5):
            app.processEvents()
    except Exception as e:
        print(f'    ❌ 性能监控 — {type(e).__name__}: {e}')

    # ── 5. 其他核心功能截图（复用对话框截图） ──
    # 无线调试 = 无线调试对话框
    对话框映射 = {
        '无线调试': 'wireless_debug_dialog',
        'Monkey压测': 'monkey_stress_window',
        '安装解包': 'install_unpack_dialog',
        '网络抓包': 'tcpdump_dialog',
        'PCAP解析': 'pcap_parse_dialog',
        '投屏': 'scrcpy_settings_dialog',
        '便捷工具': 'hash_check_dialog',
        'ADB终端': 'adb_terminal_dialog',
        'IP扫描': 'ip_scan_dialog',
        'JSON工具': 'json_tool_dialog',
        'WiFi管理': 'wifi_dialog',
        'WiFi配对': 'wifi_pair_dialog',
        '环境配置': 'env_config_dialog',
        '设备信息': 'device_info_dialog',
        '时间戳转换': 'timestamp_dialog',
        '修改时间': 'change_time_dialog',
        '证书安装': 'cert_install_dialog',
        '局域网扫描': 'lan_scan_dialog',
        '哈希校验': 'hash_check_dialog',
    }
    for 目标名, 对话框名 in 对话框映射.items():
        if 目标名 in 结果:
            continue
        src = DIALOG_SHOT_DIR / f'{对话框名}.png'
        dst = MAIN_SHOT_DIR / f'{目标名}.png'
        if src.exists():
            try:
                import shutil
                shutil.copy2(src, dst)
                结果[目标名] = f'docs/截图/{目标名}.png'
                print(f'    ✅ {目标名}（复用{对话框名}）')
            except Exception:
                pass

    return 结果


def 提取按钮文字(stem, 上限=14):
    """从对话框源码提取按钮文字（QPushButton('xx') / setText('xx')）。"""
    f = DIALOG_DIR / f'{stem}.py'
    try:
        src = f.read_text(encoding='utf-8-sig')
    except Exception:
        return []
    btns = []
    for m in re.finditer(r"QPushButton\(\s*['\"]([^'\"]{1,24})['\"]", src):
        btns.append(m.group(1).strip())
    for m in re.finditer(r"\.setText\(\s*['\"]([^'\"]{1,24})['\"]", src):
        t = m.group(1).strip()
        if t and '{' not in t:
            btns.append(t)
    去重 = []
    for b in btns:
        if b and b not in 去重:
            去重.append(b)
    return 去重[:上限]


def _docstring转html(doc, 最大行数=12):
    """把模块 docstring 转成 HTML：首行为标题，其余按段落/项目符号渲染。"""
    if not doc:
        return '<p>（该模块未编写 docstring）</p>'
    lines = [l.rstrip() for l in doc.splitlines()]
    # 去掉标题下划线行（=== / ---）
    lines = [l for i, l in enumerate(lines)
             if not (l and set(l.strip()) <= set('=-~') and i > 0)]
    # 截断到「设计要点 / 实现要点 / 设计原则」等实现细节之前
    for idx, l in enumerate(lines):
        s = l.strip()
        if s.startswith(('设计要点', '实现要点', '设计原则', '实现原则', '注意：', '说明：')):
            lines = lines[:idx]
            break
    # 去尾部空行并限行数
    while lines and not lines[-1].strip():
        lines.pop()
    if len(lines) > 最大行数:
        lines = lines[:最大行数] + ['……']
    html, 段落 = [], []
    for l in lines[1:]:  # 首行是标题，跳过
        s = l.strip()
        if not s:
            continue
        if s.startswith(('-', '·', '•', '*')) or re.match(r'^\d+[.、）)]', s):
            if 段落:
                html.append('<p>' + '<br/>'.join(段落) + '</p>')
                段落 = []
            html.append(f'<li>{s.lstrip("-·•* ").strip()}</li>')
        else:
            段落.append(s)
    if 段落:
        html.append('<p>' + '<br/>'.join(段落) + '</p>')
    # 有 li 没有 ul 包裹时补上
    if any(h.startswith('<li>') for h in html):
        包裹 = []
        缓冲 = []
        for h in html:
            if h.startswith('<li>'):
                缓冲.append(h)
            else:
                if 缓冲:
                    包裹.append('<ul>' + ''.join(缓冲) + '</ul>')
                    缓冲 = []
                包裹.append(h)
        if 缓冲:
            包裹.append('<ul>' + ''.join(缓冲) + '</ul>')
        html = 包裹
    return ''.join(html) or '<p>（无描述）</p>'


def _提取使用方法(doc):
    """从 docstring 提取带操作动词的句子作为使用步骤。"""
    if not doc:
        return []
    steps = []
    for l in doc.splitlines():
        s = l.strip().lstrip('-·•* ').strip()
        if not (6 < len(s) < 90):
            continue
        if re.search(r'(点击|双击|拖入|拖拽|输入|选择|粘贴|勾选|按下|一键|扫描|导出|复制)', s):
            s = re.sub(r'^\d+[.、）)]\s*', '', s)
            if s not in steps:
                steps.append(s)
    return steps[:6]


def build_dialog_screenshots(shots, failures):
    """生成「对话框模拟运行截图」章节 HTML：左截图 + 右功能与使用说明。"""
    if not shots and not failures:
        return ('<div class="warn-box"><strong>⏭️ 对话框截图已跳过</strong>'
                '（使用 --跳过截图 参数运行本脚本时不再生成）。</div>')
    parts = []
    for stem, cls_name, rel_path, doc in shots:
        标题 = doc.splitlines()[0].strip() if doc else cls_name
        功能html = _docstring转html(doc)
        步骤 = _提取使用方法(doc)
        按钮 = 提取按钮文字(stem)
        if 步骤:
            步骤html = '<ol>' + ''.join(f'<li>{s}</li>' for s in 步骤) + '</ol>'
        elif 按钮:
            步骤html = ('<p>主要操作按钮：</p><p>'
                        + ''.join(f'<span class="badge">{b}</span>' for b in 按钮)
                        + '</p>')
        else:
            步骤html = '<p>从主界面相应入口按钮打开本弹窗（具体入口见「功能清单」章节的按钮映射表）。</p>'
        按钮html = ''
        if 按钮 and 步骤:
            按钮html = ('<p style="margin:10px 0 0;">主要操作：'
                        + ''.join(f'<span class="badge">{b}</span>' for b in 按钮)
                        + '</p>')
        parts.append(f'''
<h3>🪟 {标题}</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="{rel_path}" alt="{stem}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 {stem} 截图缺失<br><span>重新运行脚本可再生成</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍 <span style="color:var(--text2);font-weight:400;font-size:12px;">（{stem}.py · <code>{cls_name}</code>）</span></h4>
    {功能html}
    <h4>使用方法</h4>
    {步骤html}
    {按钮html}
  </div>
</div>''')
    if failures:
        rows = ''.join(f'<li><code>{stem}</code> — {err}</li>' for stem, err in failures)
        parts.append(f'''
<div class="warn-box"><strong>⚠️ 以下 {len(failures)} 个对话框离屏截图失败</strong>
（通常因构造依赖真实设备/主窗口上下文，不影响程序正常运行）：
<ul style="margin:8px 0 0 20px;">{rows}</ul></div>''')
    return '\n'.join(parts)


# ============================================================
# HTML 生成器
# ============================================================
def build_structure_tree(files, package_desc):
    """生成可折叠项目结构树 HTML（默认折叠）。"""
    # 构建嵌套字典树（根节点也带 _files，支持顶层文件）
    tree = {'_files': {}}
    for rel, lines in files.items():
        parts = rel.split('\\')
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {'_files': {}}
            node = node[part]
        node.setdefault('_files', {})[parts[-1]] = lines

    def render_node(name, node, depth=0, is_root=False):
        """递归渲染树节点。"""
        indent = '  ' * depth
        lines_out = []
        if is_root:
            # 根节点默认展开
            lines_out.append(f'{indent}<details open class="tree-node tree-root">')
            lines_out.append(f'{indent}  <summary>📁 <span class="tree-dir">{name}/</span></summary>')
            lines_out.append(f'{indent}  <div class="tree-children">')
        else:
            # 子节点默认折叠
            desc = package_desc.get(name, '')
            desc_html = f' <span class="tree-desc">{desc}</span>' if desc else ''
            lines_out.append(f'{indent}<details class="tree-node">')
            lines_out.append(f'{indent}  <summary>📁 <span class="tree-dir">{name}/</span>{desc_html}</summary>')
            lines_out.append(f'{indent}  <div class="tree-children">')

        # 先渲染子目录
        subdirs = [k for k in node.keys() if k != '_files']
        for subdir in sorted(subdirs):
            lines_out.extend(render_node(subdir, node[subdir], depth + 2))

        # 再渲染文件
        if '_files' in node:
            for fname in sorted(node['_files'].keys()):
                if fname == '__init__.py':
                    continue
                flines = node['_files'][fname]
                line_note = f' <span class="tree-lines">{flines}行</span>' if flines > 0 else ''
                lines_out.append(f'{indent}    <div class="tree-file">📄 <span class="tree-fname">{fname}</span>{line_note}</div>')

        lines_out.append(f'{indent}  </div>')
        lines_out.append(f'{indent}</details>')
        return lines_out

    html_lines = ['<div class="foldable-tree">']
    html_lines.extend(render_node('Super_ADB_Win', tree, is_root=True))
    html_lines.append('</div>')
    return '\n'.join(html_lines)


def scan_module_imports():
    """扫描模块间导入依赖（更细粒度），返回 [(源模块, 目标模块)]。"""
    deps = []
    for p in sorted(WIN_ROOT.rglob('*.py')):
        if '__pycache__' in p.parts:
            continue
        rel = str(p.relative_to(WIN_ROOT)).replace('\\', '.')
        src_mod = rel[:-3] if rel.endswith('.py') else rel
        try:
            text = p.read_text(encoding='utf-8-sig')
        except Exception:
            continue
        # 匹配 from 包名.模块 import ...
        for m in re.finditer(r'from\s+([\u4e00-\u9fa5\w\.]+)\s+import', text):
            target = m.group(1)
            if target != src_mod and target.startswith(('app', 'ui', 'dialogs', 'pages', 'monitoring', 'tools', 'config', 'scripts', 'build_tools', 'resources')):
                deps.append((src_mod, target))
        # 匹配 import 包名.模块
        for m in re.finditer(r'^import\s+([\u4e00-\u9fa5\w\.]+)', text, re.MULTILINE):
            target = m.group(1)
            if target != src_mod and target.startswith(('app', 'ui', 'dialogs', 'pages', 'monitoring', 'tools', 'config', 'scripts', 'build_tools', 'resources')):
                deps.append((src_mod, target))
    return list(set(deps))


def build_module_dependency_mermaid(module_deps):
    """生成模块级依赖关系 mermaid 图（仅显示核心模块）。"""
    # 只显示核心模块（主入口、对话框基类、工具等）
    核心模块 = {
        'app.main': '主入口',
        'app.dialog_launcher': '弹窗打开Mixin',
        'app.device_manager': '设备管理Mixin',
        'app.theme_system': '主题系统Mixin',
        'ui.dialog_base': '对话框基类',
        'ui.ui_styles': '界面样式',
        'ui.dialog_styles': '弹窗样式',
        'tools.adb_tools': 'ADB工具',
        'tools.adb_native.adb_protocol': '自研ADB协议层',
        'tools.adb_native.adb_client': '自研ADB客户端',
    }
    lines = ['graph LR']
    # 定义节点
    for mod, label in 核心模块.items():
        nid = 'M' + str(hash(mod) % 10000)
        lines.append(f'    {nid}["{label}"]')
    # 依赖边
    节点id = {mod: 'M' + str(hash(mod) % 10000) for mod in 核心模块}
    for src, dst in sorted(module_deps):
        if src in 核心模块 and dst in 核心模块:
            lines.append(f'    {节点id[src]} --> {节点id[dst]}')
    return '\n'.join(lines)


def build_dependency_mermaid(deps):
    """生成依赖关系 mermaid 图（动态扫描所有包）。"""
    # 动态获取所有包名
    所有包 = sorted({d.name for d in WIN_ROOT.iterdir() if d.is_dir() and not d.name.startswith('__')})
    # 包描述
    包描述 = 获取包描述()
    # 生成节点ID（包名首字母缩写，冲突时加序号）
    节点映射 = {}
    已用 = set()
    for pkg in 所有包:
        # 取每个中文字的拼音首字母或英文首字母，简化为前3个字符
        base = ''.join(c for c in pkg if c.isascii() and c.isalpha())[:3].upper()
        if not base:
            base = 'PKG'
        nid = base
        idx = 1
        while nid in 已用:
            idx += 1
            nid = f'{base}{idx}'
        已用.add(nid)
        节点映射[pkg] = nid

    lines = ['graph TD']
    # 定义所有包节点
    for pkg in 所有包:
        nid = 节点映射[pkg]
        desc = 包描述.get(pkg, pkg)
        # 节点标签：包名 + 描述（换行）
        label = f'{pkg}<br/>{desc}'
        lines.append(f'    {nid}["{label}"]')
    # 依赖边
    for src, dst in sorted(deps):
        if src in 节点映射 and dst in 节点映射:
            lines.append(f'    {节点映射[src]} --> {节点映射[dst]}')
    return '\n'.join(lines)


def build_wifi_connection_mermaid():
    """生成自研ADB无线（TCP/WiFi）连接流程图 mermaid。

    关键：设备首个响应自动决定通道——
      - CNXN → 明文直连（老设备/传统 tcpip 5555/模拟器）
      - STLS → 自动升级 TLS 1.3（Android 11+ 无线调试 mDNS 端口，A_STLS 证书互认证）
      - AUTH → RSA 认证流程
    客户端无需手动选择，完全由设备响应驱动。
    """
    return """flowchart TD
    A[用户输入 IP:端口<br/>或历史/扫码/mDNS获取] --> B[连接设备 记录 serial]
    B --> C[操作时 _获取自研adb serial]
    C --> D[创建 自研adb客户端 host port<br/>设置 log_callback]
    D --> E[client.连接 → 连接池借用]
    E --> F{有空闲连接?}
    F -->|是| G[复用空闲连接]
    F -->|否| H[_新建 AdbConnection<br/>设置 conn.log_callback]
    G --> K[STATE_DEVICE 连接成功]
    H --> I[发送 CNXN 握手<br/>banner 声明 delayed_ack]
    I --> J{设备首个响应?<br/>自动协商 无需手动选择}
    J -->|CNXN 明文直连| K
    J -->|STLS 要求TLS| J2[回 STLS + 升级 TLS 1.3<br/>A_STLS 证书互认证 CN=Adb]
    J2 --> J3[加密通道上等待设备响应]
    J3 -->|CNXN| K
    J3 -->|AUTH TOKEN| L
    J -->|AUTH TOKEN| L
    L --> M[_处理认证 加载私钥 有缓存<br/>签名 token 发送 AUTH SIGNATURE]
    M --> N{设备验证通过?}
    N -->|是 CNXN| K
    N -->|否 新TOKEN| O[发送公钥 AUTH RSAPUBLICKEY]
    O --> P[log_callback 输出 授权提示<br/>请在设备上点击允许USB调试]
    P --> Q[等待用户授权 60秒]
    Q --> R{用户点击允许?}
    R -->|是 CNXN| K
    R -->|否超时| S[认证失败 30秒负缓存冷却]
    K --> T[缓存到 _自研adb缓存<br/>从连接池剥离 主连接模式]
    style J2 fill:#1c2128,stroke:#bc8cff,color:#bc8cff
    style J fill:#161b22,stroke:#1de9b6,color:#1de9b6"""


def build_usb_connection_mermaid():
    """生成自研ADB USB连接流程图 mermaid。

    关键：USB 通道为明文传输，无 TLS/A_STLS（A_STLS 仅无线 TCP）。
    USB adbd 不会发 STLS，连接直接走 CNXN/AUTH 流程。
    """
    return """flowchart TD
    A[设备插入 USB 线] --> A2[USB 通道：明文传输<br/>无 TLS / A_STLS<br/>仅无线TCP才走TLS]
    A2 --> B[枚举adb设备 发现设备<br/>原生WinUSB优先 回退pyusb]
    B --> C[设备列表刷新 加入设备 state=device]
    C --> D[用户选择设备 操作时 _获取自研adb]
    D --> E[枚举确认设备存在]
    E --> F[创建 UsbAdbConnection<br/>设置 usb_conn.log_callback]
    F --> G[UsbTransport.打开 发送 CNXN<br/>最多重试4次]
    G --> H{收到 AUTH TOKEN?}
    H -->|否 直接CNXN| I[STATE_DEVICE 连接成功]
    H -->|是| J[_处理认证_usb 加载私钥 有缓存]
    J --> K[签名 token 发送 AUTH SIGNATURE]
    K --> L{设备验证通过?}
    L -->|是 CNXN| I
    L -->|否| M[发送公钥 AUTH RSAPUBLICKEY]
    M --> N[log_callback 输出 授权提示<br/>请在设备上点击允许USB调试]
    N --> O[等待用户授权 60秒]
    O --> P{用户点击允许?}
    P -->|是 CNXN| I
    P -->|否超时| Q[认证失败]
    I --> R[缓存到 _自研adb_usb缓存<br/>与TCP共用同一份密钥]
    style A2 fill:#1c2128,stroke:#f0883e,color:#f0883e"""


def build_qr_connection_mermaid():
    """生成扫码连接流程图 mermaid（双向：PC生成码手机扫 / 手机生成码PC扫）。"""
    return """flowchart TD
    subgraph 方向A PC生成二维码 手机扫描
        A1[用户点击 生成二维码并开始等待] --> A2[生成随机服务名+6位配对码]
        A2 --> A3[构造 WIFI:T:ADB;S:服务名;P:配对码;;]
        A3 --> A4[后台 segno 生成二维码 PNG 预览]
        A4 --> A5[启动 mDNS 监听 _adb-tls-pairing._tcp]
        A5 --> A6[手机 无线调试→使用二维码配对设备 扫描]
        A6 --> A7[手机广播 mDNS 配对服务]
        A7 --> A8[mDNS 发现匹配服务名<br/>获取手机IP:端口]
        A8 --> A9[后台执行 adb pair 手机IP:端口 配对码]
    end
    subgraph 方向B 手机生成二维码 PC扫描
        B1[用户截图手机无线调试二维码] --> B2[从剪贴板或选择图片文件扫码]
        B2 --> B3[pyzbar 解码二维码内容]
        B3 --> B4[正则提取 IP:端口 + 6位配对码]
        B4 --> B5[点击 填入配对页 自动填入]
        B5 --> B6[用户在配对页点击配对]
        B6 --> A9
    end
    A9 --> A10[自研配对客户端<br/>SPAKE2+密钥交换 AES-128-GCM加密]
    A10 --> C{配对成功?}
    C -->|是| D[回调刷新设备列表]
    D --> E[走无线连接流程 TCP 5555端口]
    C -->|否| F[提示配对失败<br/>建议改用配对码连接页手动配对]"""


def build_inheritance_tree(classes):
    """生成继承关系分层树形 HTML（按基类分组，可折叠，一目了然）。"""
    # 收集继承关系：{基类: [(子类, 文件), ...]}
    继承树 = {}
    所有类信息 = {}  # {类名: 文件}
    for rel, name, bases in classes:
        所有类信息[name] = rel
        for b in bases:
            # 只保留项目内的类和关键Qt基类
            if b in ('QDialog', 'QWidget', 'QMainWindow', 'QObject', 'QRunnable',
                     'QThread', 'QListWidget', 'QTreeWidget', 'QTableWidget', 'QTextEdit',
                     'QLineEdit', 'QComboBox', 'QPushButton', 'QLabel',
                     'QFrame', 'QScrollArea', 'QStackedWidget', 'QTabWidget',
                     'QSplitter', 'QToolBar', 'QStatusBar', 'QMenuBar',
                     'QSystemTrayIcon', 'QShortcut', 'QTimer',
                     'QSortFilterProxyModel', 'QAbstractItemModel',
                     'QStyledItemDelegate', 'QStyle',
                     'Ui_MainWindow', '对话框基类', '无边框缩放Mixin',
                     '弹窗打开Mixin', '设备管理Mixin', '主题系统Mixin',
                     '命令工作器', '工作器信号', '单实例', 'Adb助手', 'Adb设备操作',
                     'PemSubjectHasher', 'Json语法高亮', '滚动图表', 'ScrollChart',
                     '文件管理页', '日志查看器页面', '小猫', '主窗口',
                     'AdbFileManager', 'AdbConnection', '自研adb客户端',
                     'ScrcpySession', 'Adb协议客户端'):
                if b not in 继承树:
                    继承树[b] = []
                继承树[b].append((name, rel))

    # 分类：核心基类 / Mixin / Qt基类 / 工具基类
    核心基类 = ['对话框基类', 'Adb助手', 'Adb设备操作', 'AdbFileManager', '主窗口', 'Ui_MainWindow']
    Mixin类 = ['无边框缩放Mixin', '弹窗打开Mixin', '设备管理Mixin', '主题系统Mixin']
    Qt基类 = ['QDialog', 'QWidget', 'QMainWindow', 'QObject', 'QRunnable', 'QThread',
              'QListWidget', 'QTreeWidget', 'QTableWidget', 'QTextEdit', 'QLineEdit',
              'QComboBox', 'QPushButton', 'QLabel', 'QFrame', 'QScrollArea',
              'QStackedWidget', 'QTabWidget', 'QSplitter', 'QToolBar', 'QStatusBar',
              'QMenuBar', 'QSystemTrayIcon', 'QShortcut', 'QTimer',
              'QSortFilterProxyModel', 'QAbstractItemModel', 'QStyledItemDelegate', 'QStyle']
    工具基类 = ['命令工作器', '工作器信号', '单实例', 'PemSubjectHasher', 'Json语法高亮',
                '滚动图表', 'ScrollChart', '小猫', '自研adb客户端', 'ScrcpySession',
                'Adb协议客户端', 'AdbConnection']

    def 渲染分组(标题, 基类列表, 标签颜色, 默认展开=False):
        """渲染一个分组的继承树。"""
        lines = []
        open_attr = ' open' if 默认展开 else ''
        lines.append(f'<details class="inherit-group"{open_attr}>')
        lines.append(f'  <summary class="inherit-group-title" style="color:{标签颜色}">▸ {标题}</summary>')
        lines.append('  <div class="inherit-children">')
        for base in 基类列表:
            if base not in 继承树:
                continue
            children = 继承树[base]
            if not children:
                continue
            is_mixin = 'Mixin' in base
            base_tag = '<span class="tag tag-mixin">Mixin</span>' if is_mixin else '<span class="tag tag-base">基类</span>'
            lines.append(f'    <div class="inherit-base">')
            lines.append(f'      <code class="inherit-base-name">{base}</code> {base_tag}')
            lines.append(f'      <span class="inherit-count">({len(children)}个子类)</span>')
            lines.append(f'    </div>')
            lines.append(f'    <div class="inherit-child-list">')
            for child, rel in sorted(children):
                # 判断子类类型
                child_tag = ''
                if 'Dialog' in child or '对话框' in child or '窗口' in child:
                    child_tag = '<span class="tag tag-base">对话框</span>'
                elif 'Mixin' in child:
                    child_tag = '<span class="tag tag-mixin">Mixin</span>'
                elif 'Page' in child or '页面' in child:
                    child_tag = '<span class="tag tag-widget">页面</span>'
                elif 'Worker' in child or '工作器' in child:
                    child_tag = '<span class="tag tag-frameless">工作器</span>'
                lines.append(f'      <div class="inherit-child">')
                lines.append(f'        <span class="inherit-arrow">└─</span>')
                lines.append(f'        <code>{child}</code> {child_tag}')
                lines.append(f'        <span class="inherit-file">{rel}</span>')
                lines.append(f'      </div>')
            lines.append(f'    </div>')
        lines.append('  </div>')
        lines.append('</details>')
        return '\n'.join(lines)

    html_parts = []
    html_parts.append('<div class="inheritance-tree">')

    # 1. 核心基类（默认折叠）
    html_parts.append(渲染分组('核心基类（项目自定义）', 核心基类, 'var(--accent)', 默认展开=False))

    # 2. Mixin（默认折叠）
    html_parts.append(渲染分组('Mixin 多继承', Mixin类, 'var(--accent2)', 默认展开=False))

    # 3. 工具基类
    html_parts.append(渲染分组('工具/协议基类', 工具基类, 'var(--purple)', 默认展开=False))

    # 4. Qt基类（默认折叠）
    html_parts.append(渲染分组('Qt 原生基类', Qt基类, 'var(--text2)', 默认展开=False))

    html_parts.append('</div>')

    # 添加统计
    总类数 = len(所有类信息)
    继承关系数 = sum(len(v) for v in 继承树.values())
    html_parts.append(f'''
    <div class="card" style="margin-top:15px;">
      <h3>继承关系统计</h3>
      <p>
        <span class="badge">类定义总数: {总类数}</span>
        <span class="badge">继承关系数: {继承关系数}</span>
        <span class="badge">核心基类: {len([b for b in 核心基类 if b in 继承树])}</span>
        <span class="badge">Mixin: {len([b for b in Mixin类 if b in 继承树])}</span>
      </p>
    </div>''')

    return '\n'.join(html_parts)


def build_theme_table(themes):
    """生成主题表格 HTML。"""
    rows = []
    for tid, name, color in themes:
        rows.append(f'<tr><td><code>{tid}</code></td><td>{name}</td><td>{color}</td></tr>')
    return '\n'.join(rows)


def build_button_table(buttons):
    """生成按钮功能清单 HTML。"""
    if not buttons:
        return '<p>未找到按钮连接。</p>'
    rows = []
    for btn, text, fn in buttons:
        display_text = text if text else '<span style="color:var(--text2);">（无文字）</span>'
        rows.append(f'<tr><td>{display_text}</td><td><code>{btn}</code></td><td><code>{fn}</code></td></tr>')
    return '<table><tr><th>按钮文字</th><th>控件名</th><th>处理函数</th></tr>' + '\n'.join(rows) + '</table>'


def build_config_table(config_fields):
    """生成配置文件说明 HTML。"""
    if not config_fields:
        return '<p>配置文件不存在或为空。</p>'
    rows = []
    for name, typ, val in config_fields:
        rows.append(f'<tr><td><code>{name}</code></td><td>{typ}</td><td><code>{val}</code></td></tr>')
    extra = '''
    <tr><td><code>favorites</code></td><td>dict</td><td>收藏的IP/包名（运行时动态添加）</td></tr>
    <tr><td><code>proxy</code></td><td>str</td><td>ADB代理设置（运行时动态添加）</td></tr>
    '''
    return '<table><tr><th>字段名</th><th>类型</th><th>示例值</th></tr>' + '\n'.join(rows) + extra + '</table>'


def build_deps_table(deps):
    """生成第三方依赖 HTML。"""
    if not deps:
        return '<p>未找到 requirements.txt。</p>'
    rows = []
    for name, ver in deps:
        rows.append(f'<tr><td><code>{name}</code></td><td>{ver}</td></tr>')
    return '<table><tr><th>包名</th><th>版本</th></tr>' + '\n'.join(rows) + '</table>'


def build_shortcut_list(shortcuts):
    """生成快捷键列表 HTML。"""
    if not shortcuts:
        return '<p>未定义快捷键。</p>'
    items = ''.join(f'<li><code>{s}</code></li>' for s in shortcuts)
    return f'<ul>{items}</ul>'


def build_dialog_list(classes):
    """生成对话框完整列表 HTML（从类继承分析中提取）。"""
    dialogs = []
    for rel, name, bases in classes:
        if any(k in name for k in ('Dialog', 'Window', '对话框', '窗口')):
            if name in ('QDialog', 'QWidget', '对话框基类', '无边框缩放Mixin', '命令工作器'):
                continue
            base = ', '.join(bases) if bases else 'object'
            dialogs.append((name, base, rel))
    if not dialogs:
        return '<p>未找到对话框类。</p>'
    rows = []
    for name, base, rel in sorted(dialogs):
        rows.append(f'<tr><td><code>{name}</code></td><td>{base}</td><td>{rel}</td></tr>')
    return '<table><tr><th>类名</th><th>继承</th><th>文件</th></tr>' + '\n'.join(rows) + '</table>'


def build_stats(files, classes, themes):
    """生成统计卡片 HTML（动态计算）。"""
    total_lines = sum(files.values())
    py_count = len(files)
    dialog_count = len([f for f in files if 'dialog' in f or 'window' in f])
    pkg_count = len([d for d in WIN_ROOT.iterdir() if d.is_dir() and not d.name.startswith('__')])
    # 动态计算 Mixin 数量
    mixin_count = len([c for _, c, _ in classes if 'Mixin' in c])
    # 主题数量动态获取
    theme_count = len(themes) if themes else 0
    # 类总数
    class_count = len(classes)
    return f'''
    <div class="card-grid">
      <div class="stat"><div class="num">{py_count}</div><div class="label">Python 文件</div></div>
      <div class="stat"><div class="num">~{total_lines:,}</div><div class="label">总行数</div></div>
      <div class="stat"><div class="num">{pkg_count}</div><div class="label">功能包</div></div>
      <div class="stat"><div class="num">{dialog_count}</div><div class="label">对话框/窗口</div></div>
      <div class="stat"><div class="num">{mixin_count}</div><div class="label">Mixin 类</div></div>
      <div class="stat"><div class="num">{theme_count}</div><div class="label">主题方案</div></div>
      <div class="stat"><div class="num">{class_count}</div><div class="label">类定义</div></div>
    </div>'''


# ============================================================
# HTML 模板
# ============================================================
def build_benchmark_table():
    """上传/下载速度对比（自研ADB vs 官方 adb）实测结果表：USB + 无线 + 模拟器 三通道。

    实测：荣耀 ELZ-AN20（Android/MagicOS）+ 模拟器 · 128MB 随机数据文件 · 各方向 3 轮取平均 ·
    同一把密钥（官方 adb 经 ADB_VENDOR_KEYS=super_adb_key.pub 使用同一已授权公钥）：
      - USB 通道：自研走 USB 直连，官方以 -s 锁定 USB 设备；
      - 无线通道：自研走 A_STLS(TLS1.3+证书互认证)，官方 adb connect 后 -s host:port，
        端口经 mDNS(_adb-tls-connect) 动态解析；
      - 模拟器通道：localhost 回环直连，瓶颈在 ADB 协议栈本身。
    """
    transports = {
        'USB': {
            'condition': '荣耀 ELZ-AN20 · USB 直连 · 官方 adb 以 -s 锁定 USB 设备',
            '上传 push': {
                '自研adb': {'rounds': ['3.24s', '3.23s', '3.18s'], 'avg': '3.22s', 'mbps': '39.8'},
                '官方adb': {'rounds': ['3.54s', '4.73s', '3.51s'], 'avg': '3.93s', 'mbps': '32.6'},
            },
            '下载 pull': {
                '自研adb': {'rounds': ['3.17s', '3.13s', '3.13s'], 'avg': '3.15s', 'mbps': '40.7'},
                '官方adb': {'rounds': ['3.15s', '3.14s', '3.22s'], 'avg': '3.17s', 'mbps': '40.4'},
            },
            '结论': [
                ('上传（push）', '39.8', '32.6', '快约 22%',
                 '自研把多个 64KB DATA 块合并进同一个 WRTE 帧（每帧最多 15 块），把 137 次往返降到 9 次；官方 adb 每帧只发一块、等流控 OKAY 后才发下一块。'),
                ('下载（pull）', '40.7', '40.4', '基本持平',
                 '两者均已接近 USB 2.0 总线实际吞吐上限（约 40MB/s）。'),
            ],
        },
        '无线(USB调试)': {
            'condition': '荣耀 ELZ-AN20 · Wi-Fi 无线调试 · 端口经 mDNS 动态解析 · 官方 adb connect 后 -s host:port',
            '上传 push': {
                '自研adb': {'rounds': ['2.18s', '2.41s', '2.21s'], 'avg': '2.27s', 'mbps': '56.5'},
                '官方adb': {'rounds': ['6.79s', '5.73s', '6.06s'], 'avg': '6.19s', 'mbps': '20.7'},
            },
            '下载 pull': {
                '自研adb': {'rounds': ['3.79s', '4.29s', '3.46s'], 'avg': '3.85s', 'mbps': '33.3'},
                '官方adb': {'rounds': ['4.37s', '3.89s', '3.79s'], 'avg': '4.02s', 'mbps': '31.9'},
            },
            '结论': [
                ('上传（push）', '56.5', '20.7', '快约 173%（约 2.7 倍）',
                 '无线下批量合并的优势被放大：自研每帧合并 15 块 DATA，网络往返次数远少于官方「一块一等 OKAY」的串行流控，延迟敏感场景差距更明显。'),
                ('下载（pull）', '33.3', '31.9', '基本持平（略快）',
                 '拉取方向自研略快约 4%；两者均受 Wi-Fi 单向带宽约束，已接近当前无线链路实际吞吐。'),
            ],
        },
        '模拟器(无delayed_ack)': {
            'condition': '模拟器 192.168.1.3:5555 · 非TLS明文 · adbd不支持delayed_ack（仅合并发送生效）',
            '上传 push': {
                '自研adb': {'rounds': ['3.45s', '2.59s', '2.64s'], 'avg': '2.89s', 'mbps': '44.2'},
                '官方adb': {'rounds': ['4.11s', '3.23s', '3.61s'], 'avg': '3.65s', 'mbps': '35.1'},
            },
            '下载 pull': {
                '自研adb': {'rounds': ['2.34s', '1.94s', '2.00s'], 'avg': '2.09s', 'mbps': '61.2'},
                '官方adb': {'rounds': ['2.20s', '2.13s', '2.34s'], 'avg': '2.22s', 'mbps': '57.6'},
            },
            '结论': [
                ('上传（push）', '44.2', '35.1', '快约 26%',
                 '仅合并发送（64KB×15块/帧）生效，delayed_ack未启用。合并发送把2048块的137次往返降到9帧，比官方「一块一等OKAY」的串行流控快26%——这是合并发送单独带来的提速。'),
                ('下载（pull）', '61.2', '57.6', '基本持平（略快）',
                 '拉取方向数据由设备端adbd发送，合并发送是发送端优化，pull受设备端发送逻辑和模拟器磁盘IO约束，两者差距小。'),
            ],
        },
        '模拟器(delayed_ack生效)': {
            'condition': '本地模拟器 emulator-5554(127.0.0.1:5555) · 非TLS明文 · delayed_ack生效（合并发送+32MB大窗口）',
            '上传 push': {
                '自研adb': {'rounds': ['2.00s', '1.90s', '1.31s'], 'avg': '1.74s', 'mbps': '73.6'},
                '官方adb': {'rounds': ['3.51s', '3.73s', '4.47s'], 'avg': '3.90s', 'mbps': '32.8'},
            },
            '下载 pull': {
                '自研adb': {'rounds': ['3.11s', '2.86s', '2.44s'], 'avg': '2.80s', 'mbps': '45.6'},
                '官方adb': {'rounds': ['2.95s', '2.60s', '2.75s'], 'avg': '2.77s', 'mbps': '46.2'},
            },
            '结论': [
                ('上传（push）', '73.6', '32.8', '快约 124%（2.24倍）',
                 '合并发送+delayed_ack大窗口（初始32MB，对齐官方adb.h）双重优化：连续发多帧不等OKAY，第3轮冲到97.4MB/s。对比无delayed_ack的44.2MB/s，delayed_ack单独带来约1.7倍额外提升，是push提速的核心。'),
                ('下载（pull）', '45.6', '46.2', '基本持平（略慢）',
                 'pull方向设备端发数据，delayed_ack是发送端窗口优化，设备端adbd发送逻辑不受自研控制；自研OKAY确认策略在delayed_ack下可能影响设备端发送节奏，故pull无提升。'),
            ],
        },
    }

    cards = []
    for transport, t in transports.items():
        tr = []
        for act in ('上传 push', '下载 pull'):
            for impl in ('自研adb', '官方adb'):
                v = t[act][impl]
                tr.append(
                    '<tr><td><strong>%s</strong></td><td>%s</td><td>%s</td><td>%s</td>'
                    '<td>%s</td><td><strong>%s · %s MB/s</strong></td></tr>'
                    % (act, impl, v['rounds'][0], v['rounds'][1], v['rounds'][2],
                       v['avg'], v['mbps']))
        concl = ''
        for name, s_mb, o_mb, ratio, reason in t['结论']:
            concl += (
                '<li><strong>%s：</strong>自研 <strong>%s MB/s</strong> vs '
                '官方 <strong>%s MB/s</strong>，%s。%s</li>'
                % (name, s_mb, o_mb, ratio, reason))
        cards.append(_BENCH_CARD_TEMPLATE % (transport, t['condition'], ''.join(tr), concl))
    return ''.join(cards)


_BENCH_CARD_TEMPLATE = """
<div class="card">
  <h3>⚡ %s 上传/下载速度（实测结果）</h3>
  <p><strong>测试条件：</strong>%s · 128MB 随机数据 · 各方向 3 轮取平均 ·
  自研ADB 走 sync 协议（64KB DATA 块 × 15 块/帧合并发送）；官方 adb 1.0.41 走标准 sync 协议，
  同一把 super_adb_key 密钥。</p>
  <table>
    <tr><th>方向</th><th>实现</th><th>第1轮</th><th>第2轮</th><th>第3轮</th><th>平均 / 速率</th></tr>
    %s
  </table>
  <h4>结论</h4>
  <ul>%s</ul>
</div>
"""


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Super_ADB 项目全景文档</title>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
  :root {{
    --bg: #0d1117; --bg2: #161b22; --bg3: #1c2128; --border: #30363d;
    --text: #e6edf3; --text2: #8b949e; --accent: #1de9b6; --accent2: #58a6ff;
    --warn: #f0883e; --danger: #f85149; --purple: #bc8cff;
  }}
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Segoe UI','Microsoft YaHei',sans-serif; line-height:1.7; }}
  .nav {{ position:fixed; top:0; left:0; width:240px; height:100vh; background:var(--bg2); border-right:1px solid var(--border); padding:20px 0; overflow-y:auto; z-index:100; }}
  .nav-brand {{ color:var(--accent); font-size:20px; font-weight:700; padding:0 20px 15px; border-bottom:1px solid var(--border); margin-bottom:10px; letter-spacing:1px; }}
  .nav h2 {{ color:var(--accent); font-size:14px; padding:0 20px 10px; border-bottom:1px solid var(--border); margin-bottom:10px; }}
  .nav a {{ display:block; padding:8px 20px; color:var(--text2); text-decoration:none; font-size:13px; transition:all .2s; }}
  .nav a:hover {{ color:var(--accent); background:var(--bg3); padding-left:24px; }}
  .main {{ margin-left:240px; padding:40px 50px; max-width:1200px; }}
  h1 {{ font-size:32px; color:var(--accent); margin-bottom:8px; }}
  .subtitle {{ color:var(--text2); font-size:14px; margin-bottom:40px; }}
  h2 {{ font-size:24px; color:var(--accent2); margin:50px 0 20px; padding-bottom:10px; border-bottom:2px solid var(--border); }}
  h3 {{ font-size:18px; color:var(--accent); margin:30px 0 12px; }}
  h4 {{ font-size:15px; color:var(--purple); margin:20px 0 8px; }}
  p {{ margin-bottom:12px; color:var(--text); }}
  .card {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:20px; margin:15px 0; }}
  .card-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:15px; margin:15px 0; }}
  .stat {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:18px; text-align:center; }}
  .stat .num {{ font-size:28px; font-weight:700; color:var(--accent); }}
  .stat .label {{ font-size:12px; color:var(--text2); margin-top:4px; }}
  code {{ background:var(--bg3); padding:2px 6px; border-radius:4px; font-size:13px; color:var(--accent); font-family:'Consolas','Monaco',monospace; }}
  pre {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px; overflow-x:auto; margin:12px 0; }}
  pre code {{ background:none; padding:0; color:var(--text); }}
  table {{ width:100%; border-collapse:collapse; margin:15px 0; font-size:13px; }}
  th {{ background:var(--bg3); color:var(--accent); padding:10px 12px; text-align:left; border:1px solid var(--border); }}
  td {{ padding:8px 12px; border:1px solid var(--border); color:var(--text); }}
  tr:hover td {{ background:var(--bg3); }}
  .foldable-tree {{ font-family:'Consolas','Monaco',monospace; font-size:13px; line-height:2; }}
  .foldable-tree details {{ margin-left:4px; }}
  .foldable-tree summary {{ cursor:pointer; list-style:none; padding:2px 0; user-select:none; }}
  .foldable-tree summary::-webkit-details-marker {{ display:none; }}
  .foldable-tree summary::before {{ content:'▶'; display:inline-block; width:14px; font-size:10px; color:var(--text2); transition:transform .15s; }}
  .foldable-tree details[open] > summary::before {{ transform:rotate(90deg); }}
  .foldable-tree .tree-root > summary {{ font-size:15px; font-weight:700; }}
  .foldable-tree .tree-children {{ margin-left:18px; border-left:1px solid var(--border); padding-left:8px; }}
  .foldable-tree .tree-dir {{ color:var(--accent2); font-weight:600; }}
  .foldable-tree .tree-desc {{ color:var(--text2); font-size:11px; margin-left:8px; }}
  .foldable-tree .tree-file {{ color:var(--text); padding:1px 0; }}
  .foldable-tree .tree-fname {{ color:var(--text); }}
  .foldable-tree .tree-lines {{ color:var(--text2); font-size:11px; margin-left:6px; }}
  .tag {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; margin-right:4px; }}
  .tag-base {{ background:rgba(29,233,182,.15); color:var(--accent); }}
  .tag-mixin {{ background:rgba(88,166,255,.15); color:var(--accent2); }}
  .tag-frameless {{ background:rgba(188,140,255,.15); color:var(--purple); }}
  .tag-widget {{ background:rgba(240,136,62,.15); color:var(--warn); }}
  .warn-box {{ background:rgba(240,136,62,.1); border-left:4px solid var(--warn); padding:15px 20px; margin:15px 0; border-radius:0 8px 8px 0; }}
  .warn-box strong {{ color:var(--warn); }}
  .mermaid {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:20px; margin:15px 0; text-align:center; }}
  .badge {{ display:inline-block; background:var(--bg3); border:1px solid var(--border); padding:3px 10px; border-radius:12px; font-size:12px; color:var(--text2); margin:2px; }}
  .section-intro {{ color:var(--text2); font-size:14px; margin-bottom:20px; }}
  /* 继承关系树形结构 */
  .inheritance-tree {{ margin:15px 0; }}
  .inherit-group {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; margin-bottom:12px; overflow:hidden; }}
  .inherit-group > summary {{ cursor:pointer; padding:12px 16px; font-size:15px; font-weight:700; list-style:none; user-select:none; transition:background .2s; }}
  .inherit-group > summary:hover {{ background:var(--bg3); }}
  .inherit-group > summary::-webkit-details-marker {{ display:none; }}
  .inherit-group[open] > summary::before {{ content:'▼ '; font-size:10px; }}
  .inherit-group:not([open]) > summary::before {{ content:'▶ '; font-size:10px; }}
  .inherit-group-title {{ display:inline; }}
  .inherit-children {{ padding:8px 16px 16px; }}
  .inherit-base {{ padding:8px 0 4px; border-bottom:1px dashed var(--border); margin-bottom:6px; }}
  .inherit-base-name {{ font-size:14px; color:var(--accent); font-weight:600; }}
  .inherit-count {{ color:var(--text2); font-size:12px; margin-left:8px; }}
  .inherit-child-list {{ margin-left:20px; }}
  .inherit-child {{ padding:4px 0; display:flex; align-items:center; gap:8px; flex-wrap:wrap; }}
  .inherit-arrow {{ color:var(--text2); font-size:12px; font-family:monospace; }}
  .inherit-child code {{ font-size:13px; }}
  .inherit-file {{ color:var(--text2); font-size:11px; font-family:monospace; margin-left:auto; }}
  .feature-block {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:30px; }}
  .feature-screenshot {{ position:relative; min-height:240px; background:var(--bg2); border:1px solid var(--border); border-radius:8px; overflow:hidden; display:flex; align-items:center; justify-content:center; }}
  .feature-screenshot img {{ max-width:100%; max-height:400px; object-fit:contain; display:block; }}
  .screenshot-placeholder {{ display:none; flex-direction:column; align-items:center; justify-content:center; color:var(--text3); font-size:14px; text-align:center; padding:20px; }}
  .screenshot-placeholder span {{ font-size:12px; color:var(--text3); margin-top:8px; opacity:0.7; }}
  .feature-desc {{ background:var(--bg2); border:1px solid var(--border); border-radius:8px; padding:16px 20px; }}
  .feature-desc h4 {{ margin:0 0 8px 0; color:var(--accent); font-size:15px; }}
  .feature-desc p {{ margin:0 0 12px 0; color:var(--text); font-size:13px; line-height:1.7; }}
  .feature-desc ol {{ margin:0; padding-left:20px; color:var(--text); font-size:13px; line-height:1.8; }}
  .feature-desc li {{ margin-bottom:4px; }}
  @media (max-width:900px) {{ .feature-block {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-brand">Super_ADB</div>
  <h2>📋 文档导航</h2>
  <a href="#feature-guide">功能介绍与使用说明</a>
  <a href="#dialog-screenshots">对话框模拟运行截图</a>
  <a href="#overview">项目概览</a>
  <a href="#structure">项目结构</a>
  <a href="#dependency">依赖关系（包级/模块级）</a>
  <a href="#inheritance">继承关系</a>
  <a href="#style">项目风格</a>
  <a href="#modules">模块详解</a>
  <a href="#features">功能清单</a>
  <a href="#pages-monitor">页面与监控</a>
  <a href="#config-deps">配置与依赖</a>
  <a href="#architecture">架构机制</a>
  <a href="#connection-flow">自研ADB连接流程</a>
  <a href="#benchmark">性能测试</a>
  <a href="#engineering">工程规范</a>
  <a href="#extension">扩展指南</a>
</nav>

<div class="main">

<h1>Super_ADB 项目全景文档</h1>
<p class="subtitle">PySide6 桌面 ADB 工具 · 项目结构 / 依赖 / 继承 / 风格 / 扩展指南</p>

<!-- 功能介绍与使用说明 -->
<h2 id="feature-guide">📖 功能介绍与使用说明</h2>
<p class="section-intro">Super_ADB 核心功能模块的详细介绍、操作步骤与界面展示。截图可放置于 <code>docs/截图/</code> 目录，文件名与下方占位一致即可自动显示。</p>

<h3>🖥️ 主界面</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/主界面.png" alt="主界面" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 主界面截图<br><span>放置于 docs/截图/主界面.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>主界面采用左侧设备列表 + 右侧 Tab 页面的布局，顶部为系统操作区，底部为状态栏。集成设备连接、系统操作、应用管理、文件传输、日志抓取、性能监控六大核心功能于一体。</p>
    <h4>使用说明</h4>
    <ol>
      <li>启动后自动扫描已连接的 USB 设备和局域网设备</li>
      <li>左侧列表选择目标设备，右侧 Tab 切换功能页面</li>
      <li>顶部按钮区提供一键重启、代理设置、投屏、抓包等快捷操作</li>
      <li>标题栏下拉菜单可切换 6 套主题，设置自动保存</li>
    </ol>
  </div>
</div>

<h3>🔌 设备连接与无线调试</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/无线调试.png" alt="无线调试" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 无线调试截图<br><span>放置于 docs/截图/无线调试.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>三合一无线调试弹窗，支持局域网扫描自动发现、配对码连接（adb pair）、二维码连接（mDNS 自动监听 + 扫码回填）三种方式。同时支持 USB 直连，自研 ADB 协议栈与官方 adb 可一键切换。</p>
    <h4>使用说明</h4>
    <ol>
      <li><strong>USB 连接</strong>：手机开启 USB 调试，插入数据线，设备列表自动出现</li>
      <li><strong>局域网扫描</strong>：点击「无线调试」→「局域网扫描」，自动扫描 5555 端口设备</li>
      <li><strong>配对码连接</strong>：手机开发者选项 → 无线调试 → 使用配对码配对，输入 6 位配对码</li>
      <li><strong>二维码连接</strong>：手机展示配对二维码，PC 端扫码自动完成 mDNS 发现与配对</li>
    </ol>
  </div>
</div>

<h3>📁 文件管理</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/文件管理.png" alt="文件管理" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 文件管理截图<br><span>放置于 docs/截图/文件管理.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>设备文件树浏览器，支持上传/下载、删除、重命名、权限修改（右键「授权 777」）、文本预览、递归搜索。自研 ADB sync 协议快速传输，上传速度可达官方 adb 的 2.7 倍。只读分区自动检测并附解锁引导。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后自动加载根目录文件列表</li>
      <li>双击文件夹进入，点击路径栏可快速跳转</li>
      <li>拖拽本地文件到窗口即可上传，右键文件可下载/删除/重命名</li>
      <li>搜索框支持当前路径和递归搜索两种模式</li>
      <li>右键「授权 777」可快速修改文件权限（需 root）</li>
    </ol>
  </div>
</div>

<h3>📋 日志抓取</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/日志抓取.png" alt="日志抓取" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 日志抓取截图<br><span>放置于 docs/截图/日志抓取.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>多标签 logcat 查看器，支持关键字过滤、标签/进程/消息星标、实时流式输出、日志级别筛选、导出保存。可同时打开多个设备的日志标签页，互不干扰。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后自动启动 logcat 实时输出</li>
      <li>顶部过滤框输入关键字实时过滤，支持正则表达式</li>
      <li>点击日志行左侧星标可标记重要日志，过滤栏可只看星标</li>
      <li>右键可复制单行/全部日志，或导出为 .txt 文件</li>
      <li>「新建标签」可同时监控多个设备或多个过滤条件</li>
    </ol>
  </div>
</div>

<h3>📊 性能监控</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/性能监控.png" alt="性能监控" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 性能监控截图<br><span>放置于 docs/截图/性能监控.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>双层性能监控体系：设备级（CPU 多核分核/内存/温度/FPS/网络速率）+ 应用级（12 项图表指标、内存泄漏自动检测、ANR/OOM 检测、hprof 自动抓取）。支持 HTML 报告导出，数据实时刷新。</p>
    <h4>使用说明</h4>
    <ol>
      <li><strong>设备级监控</strong>：主界面「性能监控」Tab，实时显示 CPU/内存/温度/FPS 曲线</li>
      <li><strong>应用级监控</strong>：选择目标应用，点击「应用性能监控」打开独立窗口</li>
      <li>内存泄漏检测自动运行，发现泄漏时自动抓取 hprof 并提示</li>
      <li>监控结束后可导出 HTML 报告，包含所有图表和异常记录</li>
      <li>图表支持缩放、暂停、数据点查看</li>
    </ol>
  </div>
</div>

<h3>🐒 Monkey 压测</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/Monkey压测.png" alt="Monkey压测" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 Monkey压测截图<br><span>放置于 docs/截图/Monkey压测.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>Monkey 压力测试管理窗口，支持命令模板自定义、暂停/继续/停止控制、实时事件饼图统计、崩溃报告自动拉取、事件回放。可设置事件数、间隔、种子、触摸/手势/轨迹球比例等参数。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择目标应用，设置事件总数、间隔时间、种子等参数</li>
      <li>点击「开始」启动 Monkey，实时显示事件统计饼图</li>
      <li>运行中可随时「暂停」/「继续」/「停止」</li>
      <li>发生崩溃时自动拉取 tombstone 和 logcat 崩溃报告</li>
      <li>支持事件回放，用相同种子复现崩溃场景</li>
    </ol>
  </div>
</div>

<h3>📦 应用管理（安装/解包）</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/安装解包.png" alt="安装解包" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 安装解包截图<br><span>放置于 docs/截图/安装解包.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>APK 安装与解包工具，支持拖拽安装、批量安装、安装进度实时显示、APK 元信息解析（包名/版本/权限/组件）、解包查看资源。三阶段安装流程（push → pm install → rm），失败时自动诊断原因。</p>
    <h4>使用说明</h4>
    <ol>
      <li>拖拽 APK 文件到窗口，或点击「选择 APK」浏览</li>
      <li>自动解析 APK 信息（包名、版本、权限列表、四大组件）</li>
      <li>点击「安装」开始，进度条实时显示上传和安装进度</li>
      <li>安装失败时显示具体原因（空间不足/签名冲突/版本降级等）</li>
      <li>「解包」可查看 APK 内部资源文件结构</li>
    </ol>
  </div>
</div>

<h3>🌐 网络抓包</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/网络抓包.png" alt="网络抓包" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 网络抓包截图<br><span>放置于 docs/截图/网络抓包.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>tcpdump 网络抓包 + PCAP 解析一体化工具。自动检测设备架构并推送对应 tcpdump 二进制（arm64/arm），支持 BPF 过滤器、实时包数统计、停止后自动拉取 pcap 文件并解析。PCAP 解析器支持 HTTP/HTTPS/TCP/UDP 协议分析、流重组、请求/响应查看。</p>
    <h4>使用说明</h4>
    <ol>
      <li>点击「网络抓包」打开窗口，自动检测设备是否已安装 tcpdump</li>
      <li>未安装时自动推送对应架构的二进制到 /data/local/tmp/（需 root）</li>
      <li>输入 BPF 过滤器（如 "tcp and port 80"），点击「开始抓包」</li>
      <li>实时显示捕获包数、过滤器接收数、内核丢包率</li>
      <li>点击「停止」自动拉取 pcap 文件并进入解析界面</li>
      <li>解析界面可查看每个数据包的详细信息，支持 HTTP 请求/响应查看</li>
    </ol>
  </div>
</div>

<h3>📺 scrcpy 投屏</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/投屏.png" alt="投屏" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 投屏截图<br><span>放置于 docs/截图/投屏.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>集成官方 scrcpy 投屏工具，支持分辨率/码率/帧率/编码器/渲染驱动等参数自定义。低延迟投屏，支持键鼠反向控制、文件拖拽传输、屏幕录制。参数设置自动保存，下次启动自动加载。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后点击「投屏」按钮启动 scrcpy</li>
      <li>「投屏设置」可调整分辨率（默认 1080p）、码率（默认 8Mbps）、帧率（默认 60fps）</li>
      <li>可选择编码器（h264/h265）和渲染驱动（direct3d/opengl/metal）</li>
      <li>投屏窗口中可直接用键鼠控制手机，拖拽文件到窗口即可传输</li>
      <li>支持屏幕录制，录制文件保存到本地</li>
    </ol>
  </div>
</div>

<h3>🛠️ 便捷工具</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/便捷工具.png" alt="便捷工具" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 便捷工具截图<br><span>放置于 docs/截图/便捷工具.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>集成多款实用小工具：命令行（PowerShell/终端）、JSON 工具（格式化/压缩/差异对比/YAML 互转/Schema 校验/树形视图）、哈希校验（MD5/SHA1/SHA256/SHA512/CRC32 等 8 种算法，支持 Windows 右键菜单）、时间戳转换（秒/毫秒/微秒/纳秒自动识别）、ADB 交互式终端、设备信息查看。</p>
    <h4>使用说明</h4>
    <ol>
      <li><strong>命令行</strong>：一键打开系统终端，自动切换到项目目录</li>
      <li><strong>JSON 工具</strong>：粘贴 JSON 自动格式化，支持左右对比差异，树形视图展开</li>
      <li><strong>哈希校验</strong>：拖拽文件即算，支持多算法同时计算，可注册 Windows 右键菜单</li>
      <li><strong>时间戳转换</strong>：输入时间戳自动识别单位并转换为北京时间，双向互转</li>
      <li><strong>ADB 终端</strong>：交互式 adb shell，支持命令历史和自动补全</li>
      <li><strong>设备信息</strong>：一键查看设备型号、Android 版本、序列号、屏幕分辨率、IP 地址等</li>
    </ol>
  </div>
</div>

<h3>🔍 PCAP 解析器</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/PCAP解析.png" alt="PCAP解析" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 PCAP解析截图<br><span>放置于 docs/截图/PCAP解析.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>专业 PCAP 网络抓包解析器，支持 HTTP/HTTPS/TCP/UDP/ICMP 协议分析、流重组、域名树结构浏览、请求/响应头查看、Hex 原始数据查看。左侧域名树按域名→路径→请求层级展示，右侧详情面板支持概览/内容/协议信息/Hex/原始数据多标签查看。</p>
    <h4>使用说明</h4>
    <ol>
      <li>网络抓包停止后自动进入解析界面，或点击「打开」选择本地 pcap 文件</li>
      <li>左侧域名树展开查看具体请求，点击请求在右侧查看详情</li>
      <li>右侧「内容」标签查看请求头和响应头，「Hex」查看原始十六进制数据</li>
      <li>顶部筛选栏支持按方法/状态/协议/域名筛选，搜索框支持关键字过滤</li>
      <li>「导出」可将解析结果导出为 HTML/JSON/CSV 格式</li>
    </ol>
  </div>
</div>

<h3>💻 ADB 交互式终端</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/ADB终端.png" alt="ADB终端" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 ADB终端截图<br><span>放置于 docs/截图/ADB终端.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>交互式 adb shell 终端，支持命令历史记录（上下键切换）、Tab 自动补全、实时输出、多设备切换。内置常用命令快捷按钮，支持一键重启/关机/进入 recovery/进入 fastboot 等操作。自研 ADB 模式下直接走自研协议栈，无需官方 adb 二进制。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后自动进入 adb shell 交互模式</li>
      <li>输入命令后回车执行，输出实时显示在终端区域</li>
      <li>上下方向键切换历史命令，Tab 键自动补全文件名和命令</li>
      <li>顶部快捷按钮可一键执行常用操作（重启/关机/recovery/fastboot）</li>
      <li>支持多标签同时连接多个设备，互不干扰</li>
    </ol>
  </div>
</div>

<h3>📡 局域网 IP 扫描</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/IP扫描.png" alt="IP扫描" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 IP扫描截图<br><span>放置于 docs/截图/IP扫描.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>局域网设备扫描工具，自动推断本机所在网段，并发 ping 扫描 254 个 IP 地址，实时显示扫描进度和已发现设备。支持 MAC 地址获取和设备厂商识别（OUI 数据库），扫描结果可一键复制全部 IP，方便批量连接。</p>
    <h4>使用说明</h4>
    <ol>
      <li>打开后自动推断本机 IP 和网段（如 192.168.1.0/24）</li>
      <li>点击「扫描」开始并发 ping 扫描，进度条实时显示进度</li>
      <li>发现在线设备时实时添加到列表，显示 IP、状态、MAC 地址、厂商</li>
      <li>本机行高亮显示，方便识别</li>
      <li>扫描完成后点击「复制全部 IP」可一键复制所有在线设备地址</li>
    </ol>
  </div>
</div>

<h3>🧩 JSON 工具</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/JSON工具.png" alt="JSON工具" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 JSON工具截图<br><span>放置于 docs/截图/JSON工具.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>多功能 JSON 处理工具集，支持格式化/压缩/校验、左右差异对比、YAML 互转、JSON Schema 校验、树形视图展开、路径定位。错误时精确指出错误位置和原因，支持大文件流式处理。</p>
    <h4>使用说明</h4>
    <ol>
      <li>左侧粘贴或输入 JSON，自动检测格式并提示错误位置</li>
      <li>「格式化」自动缩进美化，「压缩」去除空白减小体积</li>
      <li>「差异对比」左右两栏对比两个 JSON，高亮显示差异部分</li>
      <li>「YAML 互转」支持 JSON ↔ YAML 双向转换</li>
      <li>「树形视图」以可折叠树状结构展示，支持路径复制和节点定位</li>
    </ol>
  </div>
</div>

<h3>📶 WiFi 管理</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/WiFi管理.png" alt="WiFi管理" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 WiFi管理截图<br><span>放置于 docs/截图/WiFi管理.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>设备 WiFi 管理工具，支持查看已保存 WiFi 列表、信号强度、加密方式、连接状态，支持连接/断开/忘记网络、添加新网络（支持 WEP/WPA/WPA2/企业级 802.1x）、WiFi 二维码分享。需 root 权限查看已保存密码。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后自动加载已保存 WiFi 列表和当前连接状态</li>
      <li>点击 WiFi 条目查看详细信息（SSID/BSSID/信号强度/加密方式/IP 地址）</li>
      <li>「连接」切换到指定网络，「忘记」删除已保存配置</li>
      <li>「添加网络」手动输入 SSID 和密码，支持多种加密方式</li>
      <li>「二维码分享」生成 WiFi 二维码，手机扫码即可连接</li>
    </ol>
  </div>
</div>

<h3>🔑 WiFi 配对</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/WiFi配对.png" alt="WiFi配对" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 WiFi配对截图<br><span>放置于 docs/截图/WiFi配对.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>Android 11+ 无线调试配对工具，支持配对码连接（adb pair）、一键粘贴「IP:端口 配对码」自动拆分、配对成功后自动连接调试端口。已配对设备自动保存，支持一键重连。配对和连接均在后台线程执行，不阻塞 UI。</p>
    <h4>使用说明</h4>
    <ol>
      <li>手机开发者选项 → 无线调试 → 使用配对码配对设备，获取 IP、端口、6 位配对码</li>
      <li>在配对窗口输入 IP 地址和配对端口，或直接粘贴「IP:端口」自动拆分</li>
      <li>输入 6 位配对码，点击「开始配对」执行 adb pair</li>
      <li>配对成功后自动连接调试端口（默认 5555），也可手动指定端口</li>
      <li>已配对设备自动保存，下次打开可一键「重连」</li>
    </ol>
  </div>
</div>

<h3>⚙️ 环境配置</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/环境配置.png" alt="环境配置" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 环境配置截图<br><span>放置于 docs/截图/环境配置.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>ADB 环境配置工具，支持官方 adb / 自研 adb / 混合模式三种模式切换，自动检测 adb 版本和路径，支持自定义 adb 路径、端口配置、超时设置、日志级别。环境检测自动检查 adb 可用性、fastboot 可用性、USB 驱动、开发者选项状态，异常时给出修复建议。</p>
    <h4>使用说明</h4>
    <ol>
      <li>首次启动自动检测 adb 环境，显示检测结果和修复建议</li>
      <li>「ADB 模式」选择官方 adb / 自研 adb / 混合模式，切换后自动生效</li>
      <li>「ADB 路径」手动指定 adb 可执行文件路径，支持浏览选择</li>
      <li>「端口配置」设置 adb server 端口（默认 5037）和连接超时时间</li>
      <li>「环境检测」一键重新检测所有依赖项，生成检测报告</li>
    </ol>
  </div>
</div>

<h3>📱 设备信息</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/设备信息.png" alt="设备信息" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 设备信息截图<br><span>放置于 docs/截图/设备信息.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>设备信息一键查看工具，通过 getprop 获取完整设备属性（品牌/型号/Android 版本/SDK/构建号/指纹/区域/运营商/网络类型/内存配置），并发获取设备标识符（Android ID/IMEI/MEID/序列号/MAC 地址/蓝牙 MAC/IP 地址）。多线程并发获取，进度实时显示。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择设备后点击「设备信息」打开窗口，自动开始获取</li>
      <li>上半部分显示 getprop 完整属性列表，支持滚动查看</li>
      <li>下半部分实时显示设备标识符获取进度，每项获取完成后立即显示</li>
      <li>获取完成后显示统计信息（共获取 N 项，成功/失败数）</li>
      <li>支持一键复制全部信息到剪贴板</li>
    </ol>
  </div>
</div>

<h3>⏰ 时间戳转换</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/时间戳转换.png" alt="时间戳转换" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 时间戳转换截图<br><span>放置于 docs/截图/时间戳转换.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>时间戳转换工具，支持秒/毫秒/微秒/纳秒自动识别，双向转换（时间戳→日期时间，日期时间→时间戳），支持北京时间/UTC/本地时区切换。内置常用时间戳快捷输入（当前时间/今天零点/本周一/本月初），支持批量转换和历史记录。</p>
    <h4>使用说明</h4>
    <ol>
      <li>输入时间戳（支持 10 位秒/13 位毫秒/16 位微秒/19 位纳秒自动识别）</li>
      <li>自动转换为日期时间，显示北京时间、UTC 时间、本地时间</li>
      <li>反向输入日期时间，自动转换为时间戳（秒/毫秒/微秒/纳秒）</li>
      <li>快捷按钮一键填入当前时间、今天零点、本周一、本月初等常用时间</li>
      <li>支持批量转换，每行一个时间戳，结果可一键复制</li>
    </ol>
  </div>
</div>

<h3>📝 文件修改时间</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/修改时间.png" alt="修改时间" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 修改时间截图<br><span>放置于 docs/截图/修改时间.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>设备文件时间戳修改工具，支持修改文件的访问时间（atime）、修改时间（mtime）、变更时间（ctime）。支持单个文件和批量目录递归修改，可设置为指定时间或当前时间。通过 adb shell touch 命令实现，需注意 ctime 在部分设备上不可手动修改。</p>
    <h4>使用说明</h4>
    <ol>
      <li>在文件管理器中选择目标文件或目录，右键「修改时间」</li>
      <li>选择要修改的时间类型（访问时间/修改时间/两者同时）</li>
      <li>输入目标时间，或点击「当前时间」一键填入</li>
      <li>目录修改时可选择「递归修改子目录和文件」</li>
      <li>点击「应用」执行，执行结果实时显示在日志区域</li>
    </ol>
  </div>
</div>

<h3>🔐 证书安装</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/证书安装.png" alt="证书安装" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 证书安装截图<br><span>放置于 docs/截图/证书安装.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>Android 证书安装工具，支持用户证书和系统证书（CA）安装，自动处理证书格式转换（PEM/DER/PKCS12），自动计算证书哈希文件名（Android 7+ 系统证书命名规则）。支持 Charles/Fiddler/mitmproxy 等抓包证书一键安装，root 设备可直接安装到系统分区实现全局信任。</p>
    <h4>使用说明</h4>
    <ol>
      <li>选择证书文件（支持 .pem/.crt/.cer/.der/.p12/.pfx 格式）</li>
      <li>自动解析证书信息（颁发者/主题/有效期/公钥算法/指纹）</li>
      <li>选择安装位置：用户证书（无需 root）或系统证书（需 root）</li>
      <li>系统证书安装自动计算哈希文件名并 push 到 /system/etc/security/cacerts/</li>
      <li>安装完成后自动验证证书是否生效，支持一键卸载</li>
    </ol>
  </div>
</div>

<h3>🔍 局域网设备扫描</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/局域网扫描.png" alt="局域网扫描" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 局域网扫描截图<br><span>放置于 docs/截图/局域网扫描.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>局域网 ADB 设备扫描工具，专门扫描 5555 端口和常见 ADB 端口，自动识别已开启无线调试的 Android 设备。支持自定义网段和端口范围，并发扫描提高速度，扫描结果显示设备 IP、端口、设备型号（通过 adb getprop 获取）、连接状态。双击即可一键连接。</p>
    <h4>使用说明</h4>
    <ol>
      <li>打开「无线调试」→「局域网扫描」标签页</li>
      <li>自动推断本机网段，可手动修改网段和端口范围</li>
      <li>点击「扫描」开始并发扫描，进度条实时显示</li>
      <li>发现 ADB 设备时自动获取设备型号和 Android 版本</li>
      <li>双击设备条目或点击「连接」一键连接到该设备</li>
    </ol>
  </div>
</div>

<h3>#️⃣ 哈希校验</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/哈希校验.png" alt="哈希校验" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 哈希校验截图<br><span>放置于 docs/截图/哈希校验.png</span></div>
  </div>
  <div class="feature-desc">
    <h4>功能介绍</h4>
    <p>多算法文件哈希校验工具，支持 MD5/SHA1/SHA256/SHA512/SHA3-256/CRC32/PEM subject-hash 共 7 种算法同时计算。支持拖拽文件/文件夹、批量计算、进度条显示、结果一键复制、导出 CSV/JSON。可注册 Windows 右键菜单，在资源管理器中右键直接计算哈希。内置算法性能基准测试。</p>
    <h4>使用说明</h4>
    <ol>
      <li>拖拽文件或文件夹到窗口，或点击「选择文件」浏览</li>
      <li>文件夹拖入时选择展开方式（递归/当前目录/通配符匹配）</li>
      <li>勾选需要计算的算法（默认全选），设置并发数（1-8）</li>
      <li>点击「开始计算」，进度条实时显示每个文件的计算进度</li>
      <li>计算完成后点击「复制」复制单个哈希值，或「复制全部」批量复制</li>
      <li>「性能基准」可测试各算法在当前机器上的计算速度</li>
    </ol>
  </div>
</div>

<!-- 对话框模拟运行截图 -->
<h2 id="dialog-screenshots">🖼️ 对话框模拟运行截图</h2>
<p class="section-intro">对 <code>dialogs/</code> 目录下 {dialog_shot_count} 个弹窗自动离屏渲染（QT_QPA_PLATFORM=offscreen，构造参数以桩对象模拟）生成的模拟运行截图，配功能与使用说明。截图在每次运行本脚本时自动更新，保存于 <code>docs/截图/dialogs/</code>；如需跳过（加快生成）可加 <code>--跳过截图</code> 参数。</p>
{dialog_screenshots}

<!-- 项目概览 -->
<h2 id="overview">📊 项目概览</h2>
<p class="section-intro">基于 PySide6 的 Android ADB 桌面工具集，支持设备管理、文件传输、性能监控、证书安装、WiFi 调试等功能。</p>
{stats}
<div class="card">
  <h3>技术栈</h3>
  <p>
    <span class="badge">Python 3.14</span>
    <span class="badge">PySide6 (Qt6)</span>
    <span class="badge">ADB 协议 (自研)</span>
    <span class="badge">RSA2048 认证</span>
    <span class="badge">QSS 主题系统 (7套)</span>
    <span class="badge">无边框自定义窗口</span>
    <span class="badge">多线程 (QThreadPool)</span>
    <span class="badge">PyInstaller 打包</span>
    <span class="badge">自研ADB协议栈 (纯Python)</span>
    <span class="badge">openh264 投屏解码</span>
    <span class="badge">scrcpy 投屏</span>
    <span class="badge">OpenGL 渲染</span>
    <span class="badge">cryptography</span>
    <span class="badge">pyusb (USB通道)</span>
    <span class="badge">原生 WinUSB (Windows)</span>
    <span class="badge">无线配对 SPAKE2+</span>
    <span class="badge">三种ADB模式切换</span>
    <span class="badge">ADB交互式终端</span>
  </p>
</div>

<!-- 项目结构 -->
<h2 id="structure">📁 项目结构</h2>
<p class="section-intro">Super_ADB_Win/ 为项目根目录，按功能划分为 12 个包，所有包均含 <code>__init__.py</code>。</p>
<div class="card">
{structure_tree}
</div>

<!-- 依赖关系 -->
<h2 id="dependency">🔗 依赖关系</h2>
<p class="section-intro">模块间的导入依赖关系，箭头表示「依赖于」方向。主入口为核心枢纽，对话框和页面依赖工具层。</p>

<h3>包级依赖</h3>
<div class="mermaid">
{dependency_mermaid}
</div>

<h3>核心模块依赖</h3>
<div class="mermaid">
{module_dependency_mermaid}
</div>

<div class="card">
  <h3>依赖规则</h3>
  <table>
    <tr><th>层级</th><th>可依赖</th><th>不可依赖</th></tr>
    <tr><td>入口层</td><td>所有层</td><td>—</td></tr>
    <tr><td>对话框层</td><td>UI层、工具层</td><td>入口层（延迟导入除外）</td></tr>
    <tr><td>页面层</td><td>工具层</td><td>对话框层、入口层</td></tr>
    <tr><td>监控层</td><td>工具层</td><td>对话框层、入口层</td></tr>
    <tr><td>工具层</td><td>无（纯逻辑）</td><td>所有UI层</td></tr>
  </table>
</div>

<!-- 继承关系 -->
<h2 id="inheritance">🏛️ 继承关系</h2>
<p class="section-intro">项目采用「基类 + Mixin」组合模式。对话框统一继承 <code>对话框基类</code>，主窗口通过多继承组合 3 个 Mixin。按基类分组展示，点击展开/折叠。</p>
{inheritance_tree}
<div class="card">
  <h3>对话框分类</h3>
  <table>
    <tr><th>类型</th><th>基类</th><th>说明</th></tr>
    <tr><td><span class="tag tag-base">标准对话框</span></td><td>对话框基类(QDialog)</td><td>统一图标/样式/发光/主题</td></tr>
    <tr><td><span class="tag tag-frameless">无边框对话框</span></td><td>QDialog + 无边框缩放Mixin</td><td>自定义标题栏/边框/缩放</td></tr>
    <tr><td><span class="tag tag-widget">QWidget窗口</span></td><td>QWidget</td><td>独立窗口/Tab页面</td></tr>
  </table>
</div>

<!-- 项目风格 -->
<h2 id="style">🎨 项目风格</h2>
<p class="section-intro">代码命名、UI 定义、主题系统、架构模式的统一规范。</p>

<h3>命名规范</h3>
<div class="card">
  <table>
    <tr><th>元素</th><th>规范</th><th>示例</th></tr>
    <tr><td>新建文件</td><td>中文命名</td><td><code>cert_install_dialog.py</code></td></tr>
    <tr><td>新建类</td><td>中文命名</td><td><code>class 证书安装对话框</code></td></tr>
    <tr><td>新建方法</td><td>中文命名</td><td><code>def 刷新标题栏按钮样式</code></td></tr>
    <tr><td>新建变量</td><td>中文命名</td><td><code>序列号 = 获取序列号()</code></td></tr>
    <tr><td>历史代码</td><td>英文命名（保持兼容）</td><td><code>class 安装解包对话框</code></td></tr>
    <tr><td>UI控件</td><td>驼峰命名（.ui定义）</td><td><code>btnSll</code> <code>brandText</code></td></tr>
  </table>
  <div class="warn-box">
    <strong>⚠️ 命名过渡策略：</strong>新建代码一律中文命名，历史英文代码保持不变。重构时可逐步迁移，但需同步更新所有引用。
  </div>
</div>

<h3>UI 与代码分离</h3>
<div class="card">
  <table>
    <tr><th>职责</th><th>位置</th><th>说明</th></tr>
    <tr><td>控件定义</td><td><code>ui/Super_ADB.ui</code></td><td>Qt Designer 可视化编辑</td></tr>
    <tr><td>编译输出</td><td><code>ui/Super_ADB.py</code></td><td>pyside6-uic 自动生成</td></tr>
    <tr><td>样式设置</td><td>主入口代码</td><td>setStyleSheet / 主题色</td></tr>
    <tr><td>信号连接</td><td>主入口代码</td><td>clicked.connect / 功能绑定</td></tr>
    <tr><td>资源文件</td><td><code>ui/png.qrc</code> → <code>png_rc.py</code></td><td>pyside6-rcc 编译</td></tr>
  </table>
  <h4>编译命令</h4>
<pre><code>pyside6-uic "ui\\Super_ADB.ui" -o "Super_ADB_Win\\ui\\Super_ADB.py"
pyside6-rcc "ui\\png.qrc" -o "Super_ADB_Win\\ui\\png_rc.py"</code></pre>
</div>

<h3>主题系统</h3>
<div class="card">
  <p>7 套主题，统一由 <code>ui_styles.py</code> 管理，通过 <code>get_stylesheet(theme_id)</code> 获取 QSS。</p>
  <table>
    <tr><th>主题ID</th><th>名称</th><th>强调色</th></tr>
    {theme_rows}
  </table>
  <p>主题切换流程：<code>_切换主题</code> → setStyleSheet → 刷新标题栏按钮 → 延迟 <code>_强制主题重绘</code> → 同步打开中的弹窗样式。</p>
  <h4>弹窗样式跟随主题（正确做法）</h4>
<pre><code># 1. 创建弹窗：只给对话框 setStyleSheet，子控件不单独设样式
dlg = QDialog(self)
dlg.setStyleSheet(get_stylesheet(self._current_theme))
# QLabel / QTextEdit 等子控件自动继承全局主题样式，不要 setStyleSheet

# 2. 主题切换时同步更新打开的弹窗（在 _切换主题 中）
if hasattr(self, '_设备信息弹窗') and self._设备信息弹窗 is not None:
    self._设备信息弹窗.setStyleSheet(get_stylesheet(theme_id))

# ❌ 错误：子控件写死颜色，切换主题后不变
label.setStyleSheet('color:#58a6ff;background:#0d1117')
edit.setStyleSheet('QTextEdit{{background:#0d1117;color:#e6edf3}}')</code></pre>
</div>

<h3>架构模式</h3>
<div class="card-grid">
  <div class="card">
    <h4>🔀 Mixin 多继承</h4>
    <p>主窗口通过多继承组合功能模块，每个 Mixin 独立文件，职责单一。</p>
    <code>主窗口(QWidget, Ui_MainWindow, 弹窗打开Mixin, 设备管理Mixin, 主题系统Mixin)</code>
  </div>
  <div class="card">
    <h4>📦 包式导入</h4>
    <p>所有 import 使用包名前缀，sys.path 只加项目根目录。</p>
    <code>from dialogs.cert_install_dialog import 证书安装对话框</code>
  </div>
  <div class="card">
    <h4>🧵 异步任务</h4>
    <p>ADB 命令通过 QThreadPool + QRunnable 异步执行，避免阻塞 UI。</p>
    <code>命令工作器(QRunnable)</code>
  </div>
  <div class="card">
    <h4>🪟 无边框窗口</h4>
    <p>自定义 paintEvent 绘制边框，<code>无边框缩放Mixin</code> 提供边缘拖拽缩放。</p>
  </div>
</div>

<!-- 模块详解 -->
<h2 id="modules">📖 模块详解</h2>
<p class="section-intro">核心模块的功能说明和关键接口。</p>

<h3>入口层</h3>
<div class="card">
  <h4>main.py — 主窗口</h4>
  <p>主窗口类，继承 QWidget + Ui_MainWindow + 3个 Mixin。负责窗口初始化、信号连接、ADB 实例管理、线程池、配置持久化。</p>
  <p><strong>关键属性：</strong><code>self.adb</code>(Adb设备操作)、<code>self.pool</code>(QThreadPool)、<code>self._current_theme</code>、<code>self._live_workers</code></p>
</div>
<div class="card-grid">
  <div class="card">
    <h4>dialog_launcher.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 14个 open_xxx 方法，创建并显示对话框/窗口，支持实例复用（重复点击 raise）。</p>
  </div>
  <div class="card">
    <h4>device_manager.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 设备连接/断开/扫描，<code>当前序列号()</code> 获取当前选中设备序列号。</p>
  </div>
  <div class="card">
    <h4>theme_system.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 7套主题切换、标题栏按钮样式、品牌标识、弹窗主题传播。</p>
  </div>
</div>

<h3>UI 层</h3>
<div class="card-grid">
  <div class="card">
    <h4>dialog_base.py</h4>
    <p><span class="tag tag-base">基类</span> 统一对话框图标、样式、发光效果、主题切换。参数：<code>标题</code>/<code>最小尺寸</code>/<code>发光</code>。</p>
  </div>
  <div class="card">
    <h4>ui_styles.py</h4>
    <p>7套主题 QSS 定义，<code>get_stylesheet(theme_id)</code> / <code>get_theme_ids()</code> / <code>get_theme_name()</code>。</p>
  </div>
  <div class="card">
    <h4>dialog_styles.py</h4>
    <p><code>无边框缩放Mixin</code>（边缘拖拽缩放）、<code>add_green_glow</code>（发光效果）、<code>拖拽区域</code>（拖拽区）。</p>
  </div>
</div>

<h3>工具层</h3>
<div class="card">
  <h4>adb_tools.py — Adb设备操作（2810行）</h4>
  <p>核心 ADB 操作封装，继承 Adb助手。支持三种模式切换：系统adb / Socket直连 / 自研ADB。关键方法：</p>
  <table>
    <tr><th>方法</th><th>功能</th></tr>
    <tr><td><code>执行shell(serial, cmd)</code></td><td>执行 shell 命令（自研模式优先）</td></tr>
    <tr><td><code>直接执行(serial, args)</code></td><td>执行 adb 原生命令（非shell）</td></tr>
    <tr><td><code>推送文件(serial, local, remote)</code></td><td>推送文件到设备（sync协议）</td></tr>
    <tr><td><code>拉取文件(serial, remote, local)</code></td><td>从设备拉取文件</td></tr>
    <tr><td><code>流式推送(serial, data, path)</code></td><td>内存数据流式推送</td></tr>
    <tr><td><code>安装apk / 安装(serial, apk)</code></td><td>安装APK（push + pm install）</td></tr>
    <tr><td><code>卸载应用(serial, pkg)</code></td><td>卸载应用</td></tr>
    <tr><td><code>获取应用列表 / 获取运行中应用</code></td><td>应用管理</td></tr>
    <tr><td><code>获取当前界面应用(serial)</code></td><td>获取当前前台Activity</td></tr>
    <tr><td><code>启动投屏(serial)</code></td><td>启动scrcpy投屏</td></tr>
    <tr><td><code>启动logcat(serial)</code></td><td>在独立窗口启动logcat</td></tr>
    <tr><td><code>列出目录 / 删除文件 / 修改权限</code></td><td>文件管理（含验证和日志）</td></tr>
  </table>
</div>

<h3>对话框层（23个对话框/窗口）</h3>
<p class="section-intro">按功能分类的对话框，均继承对话框基类或使用无边框缩放Mixin。</p>
<div class="card-grid">
  <div class="card">
    <h4>🔌 设备连接类</h4>
    <ul style="margin:8px 0 0 16px;color:var(--text);font-size:13px;">
      <li><code>WiFi对话框</code> — WiFi连接设备</li>
      <li><code>WiFi配对对话框</code> — Android 11+ 配对码</li>
      <li><code>WiFi历史对话框</code> — 历史连接记录</li>
      <li><code>无线调试对话框</code> — 无线调试管理</li>
      <li><code>局域网扫描对话框</code> — 网段扫描发现设备</li>
      <li><code>IP扫描对话框</code> — 指定IP段扫描</li>
      <li><code>二维码连接页</code> — 扫码连接设备</li>
      <li><code>环境配置对话框</code> — 三种ADB模式切换</li>
    </ul>
  </div>
  <div class="card">
    <h4>📦 应用管理类</h4>
    <ul style="margin:8px 0 0 16px;color:var(--text);font-size:13px;">
      <li><code>安装解包对话框</code> — APK安装/解包</li>
      <li><code>Monkey压测窗口</code> — Monkey压力测试</li>
      <li><code>证书安装对话框</code> — 证书安装管理</li>
    </ul>
  </div>
  <div class="card">
    <h4>🔧 工具类</h4>
    <ul style="margin:8px 0 0 16px;color:var(--text);font-size:13px;">
      <li><code>JSON工具对话框</code> — JSON格式化/编辑</li>
      <li><code>哈希校验对话框</code> — 文件哈希计算</li>
      <li><code>TCPDump对话框</code> — 网络抓包</li>
      <li><code>PCAP解析对话框</code> — PCAP文件解析</li>
      <li><code>ADB终端对话框</code> — ADB Shell交互式终端</li>
      <li><code>设备信息对话框</code> — 设备属性/标识符</li>
      <li><code>scrcpy_settings_dialog</code> — 投屏参数设置</li>
    </ul>
  </div>
  <div class="card">
    <h4>📝 其他</h4>
    <ul style="margin:8px 0 0 16px;color:var(--text);font-size:13px;">
      <li><code>关于对话框</code> — 关于/版本信息</li>
      <li><code>时间戳对话框</code> — 时间戳转换</li>
      <li><code>修改时间对话框</code> — 文件时间修改</li>
      <li><code>哈希上下文菜单</code> — 右键哈希菜单</li>
    </ul>
  </div>
</div>

<h3>自研 ADB 协议栈（tools/adb_native/）</h3>
<p class="section-intro">不依赖官方 adb 二进制的纯 Python ADB 实现，TCP/USB 双通道，与官方 adb 可切换。11个模块，共约6552行。支持无线配对（SPAKE2+AES-GCM）、原生 WinUSB 后端、连接池、scrcpy 投屏、ADB交互式终端。</p>
<div class="card-grid">
  <div class="card">
    <h4>adb_protocol.py — 协议层（1744行）</h4>
    <p>实现 CNXN/AUTH/OPEN/WRTE/CLSE 状态机与 sync 协议。认证：RSA2048 + SHA1 PKCS1v15 签名；公钥为 524 字节 android_pubkey_t 的 base64。ADB_VERSION=0x01000001（skip checksum）。<code>_定位密钥路径()</code> 统一解析密钥位置：打包版放 exe 旁 <code>config/</code> 并自动迁移已授权密钥。</p>
  </div>
  <div class="card">
    <h4>adb_client.py — 连接池（672行）</h4>
    <p>设备级建连锁（RLock）+ 连接池借用/剥离；<strong>30 秒负缓存</strong>防认证失败重试风暴；公钥授权 <strong>60 秒循环等待</strong>；主连接模式（短操作共享主连接加锁串行，长操作用独立连接）；认证失败原因精确上报。</p>
  </div>
  <div class="card">
    <h4>usb_connection.py + usb_transport.py</h4>
    <p>USB ADB 传输层，与 TCP 共用同一份密钥。双后端架构：Windows 优先原生 WinUSB（SetupAPI+WinUSB，不依赖 libusb），回退 pyusb/libusb1。支持 USB 设备热插拔检测、设备枚举、端点查找。</p>
  </div>
  <div class="card">
    <h4>usb_window_native.py — 原生 WinUSB 后端（869行）</h4>
    <p>Windows 原生 USB 实现，纯 ctypes 调用 SetupAPI + WinUSB，无需安装 libusb。通过 ADB 接口 GUID 枚举设备 + 暴力枚举全量USB接口兜底 + 设备节点诊断（检测无驱动设备），CreateFileW + FILE_FLAG_OVERLAPPED 打开，WinUsb_Initialize 初始化，Bulk 端点读写。支持从父设备实例 ID 读取序列号。</p>
  </div>
  <div class="card">
    <h4>pair_client.py — 无线配对客户端（686行）</h4>
    <p>Android 11+ 无线调试配对实现。TLS 连接 + SPAKE2+ 密钥交换 + AES-128-GCM 加密通信。支持配对码模式，生成自签名证书，导出 TLS 密钥材料。与官方 adb pair 命令兼容。</p>
  </div>
  <div class="card">
    <h4>pair_auth.py + key_exchange.py</h4>
    <p>配对认证层：AES-128-GCM 加解密、SPAKE2+ 椭圆曲线密钥交换（纯 Python 实现 Curve25519 标量乘法）。支持客户端/服务端双向认证，口令派生密钥，密钥确认。</p>
  </div>
  <div class="card">
    <h4>multi_device_manager.py</h4>
    <p>多设备连接管理，统一调度各设备的连接池和认证状态。</p>
  </div>
</div>

<!-- 功能清单 -->
<h2 id="features">🔘 功能清单</h2>
<p class="section-intro">主窗口所有按钮的信号连接，以及全部对话框/窗口类的完整列表。</p>

<h3>按钮功能映射（{button_count}个）</h3>
<div class="card">
{button_table}
</div>

<h3>对话框/窗口完整列表</h3>
<div class="card">
{dialog_list}
</div>

<!-- 页面层与监控层 -->
<h2 id="pages-monitor">📺 页面与监控</h2>
<p class="section-intro">主窗口 Tab 页面和独立监控窗口的功能说明。</p>

<div class="card-grid">
  <div class="card">
    <h4>文件管理页</h4>
    <p><span class="tag tag-widget">QWidget</span> 继承 QWidget，嵌入主窗口 Tab。提供设备文件浏览、上传下载、删除、重命名、修改权限、文本预览功能。异步执行 ADB 命令（_命令工作器 QRunnable），支持设备下拉框同步。</p>
  </div>
  <div class="card">
    <h4>日志查看器页面</h4>
    <p><span class="tag tag-widget">QWidget</span> 继承 QWidget，嵌入主窗口 Tab。实时显示 logcat 输出，支持设备切换、日志过滤、清空、复制。异步读取进程输出。</p>
  </div>
  <div class="card">
    <h4>设备性能监控</h4>
    <p><span class="tag tag-widget">独立窗口</span> 实时监控设备 CPU/内存/网络，滚动图表，独立窗口不阻塞主 UI。</p>
  </div>
  <div class="card">
    <h4>应用性能监控</h4>
    <p><span class="tag tag-widget">独立窗口</span> 按包名监控应用内存/PSS/CPU，应用滚动图表，支持多应用对比。</p>
  </div>
  <div class="card">
    <h4>APK分析器</h4>
    <p><span class="tag tag-widget">工具模块</span> 解析APK包名、权限、组件、签名。配合AXML解码器、DEX分析、清单解析模块。</p>
  </div>
  <div class="card">
    <h4>WiFi工具</h4>
    <p><span class="tag tag-widget">工具模块</span> WiFi连接管理、密码破解、历史记录。支持Android 11+配对码模式。</p>
  </div>
</div>

<!-- 配置与依赖 -->
<h2 id="config-deps">⚙️ 配置与依赖</h2>
<p class="section-intro">配置文件结构和第三方运行依赖。</p>

<h3>配置文件（super_adb_config.json）</h3>
<div class="card">
{config_table}
<p style="margin-top:10px;color:var(--text2);font-size:12px;">配置文件位于 <code>Super_ADB_Win/config/</code> 目录，启动时自动加载，退出时保存窗口几何和主题。</p>
</div>

<h3>第三方依赖（requirements.txt）</h3>
<div class="card">
{deps_table}
</div>

<!-- 自研ADB连接流程 -->
<h2 id="connection-flow">🔌 自研ADB连接流程</h2>
<p class="section-intro">自研ADB模式下三种连接方式的完整流程：无线（TCP/WiFi）、USB、扫码配对。三种方式共用同一套 RSA 密钥与认证逻辑，区别仅在传输层与入口。</p>

<div class="card">
  <h3>📡 无线连接（TCP / WiFi）</h3>
  <p>用户输入 IP:端口（或历史/扫码/mDNS 获取）→ 连接池借用 → 发送 CNXN 握手 → <strong>设备首个响应自动决定通道</strong>：回 CNXN 则明文直连（老设备/传统 tcpip 5555/模拟器）；回 STLS 则自动升级 TLS 1.3（A_STLS 证书互认证，Android 11+ 无线调试强制）；回 AUTH TOKEN 则走 RSA 认证 → 私钥签名 → 验证通过则直接连接，失败则发送公钥并提示用户在设备上授权 → 等待 60 秒 → 连接成功后缓存。客户端无需手动选择 TLS，完全由设备响应驱动。</p>
  <div class="mermaid">
{wifi_flow}
  </div>
</div>

<div class="card">
  <h3>🔌 USB 连接</h3>
  <p><strong>USB 通道为明文传输，无 TLS / A_STLS</strong>（A_STLS 仅无线 TCP，USB adbd 不会发 STLS）。设备插入 USB → 枚举设备（原生 WinUSB 优先，回退 pyusb）→ 创建 UsbAdbConnection → 发送 CNXN（最多重试 4 次）→ 认证逻辑与无线一致（RSA 签名/公钥授权）→ 成功后缓存到 USB 专用缓存。</p>
  <div class="mermaid">
{usb_flow}
  </div>
</div>

<div class="card">
  <h3>📷 扫码连接（双向）</h3>
  <p><strong>方向 A（PC 生成码，手机扫）：</strong>生成随机服务名+配对码 → 构造 Android 标准 WIFI:T:ADB 二维码 → 启动 mDNS 监听 → 手机扫描后广播配对服务 → 发现后自动执行 adb pair（SPAKE2+ 密钥交换 + AES-128-GCM）。<br/><strong>方向 B（手机生成码，PC 扫）：</strong>pyzbar 解码手机二维码 → 提取 IP:端口+配对码 → 填入配对页 → 用户手动触发配对。配对成功后均走无线连接流程。</p>
  <div class="mermaid">
{qr_flow}
  </div>
</div>

<!-- 性能测试 -->
<h2 id="benchmark">⚡ 性能测试</h2>
<p class="section-intro">上传/下载速度对比：自研ADB vs 官方 adb，USB + 无线双通道实测。128MB 随机文件、各方向 3 轮取平均（同一把 super_adb_key 密钥）。<strong>USB 通道为明文传输（无 TLS）</strong>，速度受 USB 2.0 总线带宽约束；<strong>无线通道走 A_STLS TLS 1.3 全程加密</strong>（mDNS 动态解析端口，Android 11+ 强制），TLS 加解密吞吐是无线通道主要瓶颈之一。自研ADB 走 sync 协议（64KB DATA 块 × 15 块/帧合并发送 + delayed_ack 大窗口）。</p>
{benchmark}

<!-- 架构机制 -->
<h2 id="architecture">🏗️ 架构机制</h2>
<p class="section-intro">线程模型、单实例、窗口持久化、日志系统等核心机制。</p>

<div class="card-grid">
  <div class="card">
    <h4>🔀 三种ADB模式</h4>
    <p><strong>系统adb</strong>：调用PATH中的adb.exe，最稳定。<strong>Socket直连</strong>：直连127.0.0.1:5037，不启动adb进程。<strong>自研ADB</strong>：纯Python实现ADB协议，直连设备5555端口，无需官方adb。</p>
  </div>
  <div class="card">
    <h4>🧵 线程模型</h4>
    <p><strong>命令工作器(QRunnable)</strong> + <strong>QThreadPool</strong> 异步执行 ADB 命令，避免阻塞 UI。结果通过 <strong>工作器信号</strong> 信号回传（result/error/finished）。长任务用 <strong>QThread</strong>（如安装线程、哈希线程）。</p>
  </div>
  <div class="card">
    <h4>🔒 单实例机制</h4>
    <p><strong>单实例(QObject)</strong> 通过系统互斥量（mutex）防止多开。第二个实例启动时检测到已有实例，自动退出并激活已有窗口。</p>
  </div>
  <div class="card">
    <h4>📐 窗口几何持久化</h4>
    <p>启动时从配置读取 <code>geometry.b64</code>（saveGeometry 的 base64 编码），调用 <code>restoreGeometry()</code> 恢复窗口位置/大小/状态。关闭时 <code>saveGeometry()</code> 写入配置。</p>
  </div>
  <div class="card">
    <h4>📝 日志输出系统</h4>
    <p>三级输出：<strong>日志()</strong> 输出框（主窗口文本区）、<strong>设置状态()</strong> 状态栏（底部提示，带成功/失败颜色）、<strong>日志查看器页</strong>（logcat 实时流）。</p>
  </div>
  <div class="card">
    <h4>🔑 自研ADB认证与密钥管理</h4>
    <p>密钥 <code>super_adb_key(+.pub)</code> 源码模式在 <code>config/</code>，打包版在 exe 旁 <code>config/</code>（首次访问自动从旧位置/源码树迁移）。认证失败后 <strong>30 秒负缓存</strong>冷却；发公钥后 <strong>60 秒循环等待</strong>设备授权（盒子/TV 等无授权弹窗的 ROM 会断开连接，错误消息提示复制已授权密钥）。<strong>无线配对</strong>：Android 11+ 配对码模式，SPAKE2+ 密钥交换 + AES-128-GCM 加密，与官方 adb pair 兼容。</p>
  </div>
  <div class="card">
    <h4>🔗 连接池架构</h4>
    <p>自研ADB采用<strong>设备级建连锁</strong> + <strong>连接池</strong>。短操作（shell命令）共享主连接加锁串行；长操作（推送/拉取/安装）用独立连接。后台daemon线程清理空闲连接。</p>
  </div>
</div>

<!-- 工程规范 -->
<h2 id="engineering">🔧 工程规范</h2>
<p class="section-intro">UI 控件命名、快捷键、打包、脚本等工程细节。</p>

<h3>UI 控件命名规范</h3>
<div class="card">
  <table>
    <tr><th>前缀</th><th>类型</th><th>示例</th></tr>
    <tr><td><code>btn</code></td><td>QPushButton</td><td>btnSll, btnAbout, btnConnect</td></tr>
    <tr><td><code>xxxInput</code></td><td>QLineEdit</td><td>ipInput, pkgInput</td></tr>
    <tr><td><code>xxxCombo</code></td><td>QComboBox</td><td>deviceCombo</td></tr>
    <tr><td><code>brandXxx</code></td><td>QLabel（品牌标识）</td><td>brandIcon, brandText</td></tr>
  </table>
  <p style="margin-top:10px;color:var(--text2);font-size:12px;">控件在 <code>.ui</code> 文件中定义，编译后通过 <code>Ui_MainWindow</code> 访问。代码只做样式和信号连接。</p>
</div>

<h3>快捷键</h3>
<div class="card">
{shortcut_list}
</div>

<h3>打包说明</h3>
<div class="card">
  <p>使用 <strong>PyInstaller</strong> 打包，入口脚本 <code>build_tools/build_exe.py</code>。</p>
  <ul style="margin:10px 0 10px 20px;color:var(--text);">
    <li><code>build_tools/trim_qt.py</code> — 构建后按 DLL 依赖闭包裁剪 Qt 插件/翻译</li>
    <li><code>build_tools/hooks/hook-pyzbar.py</code> — pyzbar 运行时钩子</li>
    <li>排除 <code>av/av.libs</code>（PyAV 全量 ffmpeg 62.5MB）→ 投屏改用内置 openh264</li>
    <li>构建后直删 <code>OpenGL/DLLS</code>（freeglut/gle 废件，--exclude-module 挡不住数据文件型收集）</li>
    <li><strong>cryptography</strong>：添加 hidden-import，<strong>禁止排除子模块</strong>（serialization/__init__ 硬导入 asymmetric.dh/ec 等，排除即 ModuleNotFoundError）</li>
    <li><strong>usb/pyusb</strong>：添加 hidden-import，支持USB通道</li>
    <li>pathex 只加 <code>Super_ADB_Win/</code> 根目录（包式导入）；add-data 目标路径不带前导 /</li>
    <li>subprocess 调用统一加 <code>CREATE_NO_WINDOW</code>，避免打包后弹出CMD黑框</li>
  </ul>
</div>

<h3>脚本层</h3>
<div class="card">
  <p><code>scripts/</code> 目录包含工具脚本和测试脚本：</p>
  <ul style="margin:10px 0 10px 20px;color:var(--text);">
    <li><code>gen_dependency_graph.py</code> — 生成 .dot/.svg/.png 和依赖关系图.md</li>
    <li><code>gen_project_overview.py</code> — 生成本 HTML 文档</li>
    <li><code>gen_icons.py</code> — 生成应用图标</li>
    <li><code>smoke_test.py</code> — 冒烟测试</li>
    <li><code>memory_trace.py</code> — 内存使用追踪</li>
    <li><code>测试_*.py</code> — 各模块单元测试（8个）</li>
  </ul>
</div>

<!-- 扩展指南 -->
<h2 id="extension">🚀 扩展指南</h2>
<p class="section-intro">新增功能时的规范流程和注意事项。</p>

<h3>新增对话框（标准）</h3>
<div class="card">
<pre><code># 1. 在 dialogs/ 目录新建文件，中文命名
# dialogs/我的新功能对话框.py

from ui.dialog_base import 对话框基类

class 我的新功能对话框(对话框基类):
    def __init__(self, parent=None):
        super().__init__(parent, 标题='我的新功能', 最小尺寸=(520, 400), 发光=True)
        # 构建 UI...

    def apply_theme(self, theme_id):
        # 可选：自定义主题切换逻辑
        super().apply_theme(theme_id)

# 2. 在 dialog_launcher.py 添加打开方法
def open_my_dialog(self):
    if self._my_dialog is not None and self._my_dialog.isVisible():
        self._my_dialog.raise_()
        return
    from dialogs.我的新功能对话框 import 我的新功能对话框
    self._my_dialog = 我的新功能对话框(parent=self)
    self._my_dialog.show()

# 3. 在 主窗口.__init__ 初始化引用
self._my_dialog = None

# 4. 连接按钮信号
self.btnMy.clicked.connect(self.open_my_dialog)</code></pre>
</div>

<h3>新增无边框对话框</h3>
<div class="card">
<pre><code>from PySide6.QtWidgets import QDialog
from ui.dialog_styles import 无边框缩放Mixin

class 我的无边框对话框(QDialog, 无边框缩放Mixin):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 自定义 paintEvent 绘制边框和背景
        # 标题栏含设备信息时：标题 = f'xxx — 设备: {{serial}}'</code></pre>
</div>

<h3>新增 ADB 命令</h3>
<div class="card">
<pre><code># 在 tools/adb_tools.py 的 Adb设备操作 类中添加
def my_adb_command(self, serial, param):
    \"\"\"执行自定义 ADB 命令，返回 (success, output)。\"\"\"
    return self.执行shell(serial, f'my command {{param}}')

# 主入口中异步调用
def _run_my_command(self):
    serial = self._确保序列号()
    if not serial: return
    self._异步运行(self.adb.my_adb_command, serial, 'param')</code></pre>
</div>

<h3>注意事项</h3>
<div class="warn-box"><strong>⚠️ 循环导入：</strong>Mixin 文件中如需导入主入口的类（如 命令工作器），必须在方法内部延迟导入，不能在文件顶部导入。</div>
<div class="warn-box"><strong>⚠️ UI 定义分离：</strong>新增按钮/控件必须在 <code>.ui</code> 文件中定义，编译后代码只做样式和信号连接。不要在代码中动态创建 UI 控件。</div>
<div class="warn-box"><strong>⚠️ 中文命名：</strong>新建文件/类/方法/变量一律使用中文命名。历史英文代码保持不变，重构时逐步迁移。</div>
<div class="warn-box"><strong>⚠️ 包式导入：</strong>所有 import 使用包名前缀（<code>from dialogs.xxx import ...</code>），不要使用裸导入。sys.path 只加 Super_ADB_Win/ 根目录。</div>
<div class="warn-box"><strong>⚠️ 线程安全：</strong>ADB 命令必须异步执行（QThreadPool + QRunnable），不能在主线程直接调用。结果通过信号回传。</div>
<div class="warn-box"><strong>⚠️ 主题传播：</strong>自定义对话框必须实现 <code>apply_theme(theme_id)</code> 方法，主题切换时会自动调用。无边框对话框需手动同步样式。</div>
<div class="warn-box"><strong>⚠️ 弹窗控件样式勿写死：</strong>弹窗内的 QLabel / QTextEdit / QPushButton 等子控件<strong>不要单独调用 setStyleSheet 写死颜色</strong>（如 <code>color:#58a6ff;background:#0d1117</code>），否则会覆盖全局主题，切换主题后控件样式不变。正确做法：只给对话框本身 <code>setStyleSheet(get_stylesheet(theme_id))</code>，子控件自动继承全局样式（ui_styles.py 已定义 QTextEdit/QLabel/QPushButton 等主题样式）。若必须自定义，用 <code>THEMES[theme_id]</code> 取色并在主题切换时重新 apply。弹窗打开状态下切主题：在 <code>_切换主题</code> 中检查 <code>self._xxx弹窗</code> 是否存在，存在则调用 <code>setStyleSheet(get_stylesheet(新主题))</code>。参考：设备信息弹窗实现。</div>
<div class="warn-box"><strong>⚠️ 实例复用：</strong>弹窗打开方法必须检查实例是否已存在且可见，重复点击应 raise 而非新建。关闭后通过 destroyed 信号清空引用。</div>
<div class="warn-box"><strong>⚠️ 设备序列号：</strong>对话框标题应包含设备信息（<code>xxx — 设备: {{serial}}</code>），通过 <code>get_serial()</code> 回调获取，未连接时显示「未连接设备」。</div>

<h3>常用命令</h3>
<div class="card">
<pre><code># 编译 UI
pyside6-uic "ui\\Super_ADB.ui" -o "Super_ADB_Win\\ui\\Super_ADB.py"
pyside6-rcc "ui\\png.qrc" -o "Super_ADB_Win\\ui\\png_rc.py"

# 运行
D:\\Python\\Python314\\python.exe Super_ADB_Win\\app\\main.py

# 生成依赖图
python Super_ADB_Win\\scripts\\gen_dependency_graph.py

# 生成项目全景文档（本脚本）
python Super_ADB_Win\\scripts\\gen_project_overview.py

# 打包
python Super_ADB_Win\\build_tools\\build_exe.py

# 语法检查
python -m py_compile <文件路径></code></pre>
</div>

<div style="text-align:center; color:var(--text2); font-size:12px; margin-top:60px; padding-top:20px; border-top:1px solid var(--border);">
  Super_ADB 项目全景文档 · 自动生成 · 最后更新：{date}
</div>

</div>

<script>
mermaid.initialize({{
  startOnLoad: true,
  theme: 'dark',
  themeVariables: {{
    primaryColor: '#161b22',
    primaryTextColor: '#e6edf3',
    primaryBorderColor: '#30363d',
    lineColor: '#8b949e',
    secondaryColor: '#1c2128',
    tertiaryColor: '#0d1117',
    fontFamily: 'Segoe UI, Microsoft YaHei, sans-serif',
    fontSize: '13px'
  }}
}});
</script>
</body>
</html>
"""


# ============================================================
# 主函数
# ============================================================


def 生成功能介绍Markdown():
    """生成「功能介绍与使用说明」Markdown（包含 docs/截图/ 下全部截图）。"""
    features = [
        ("🖥️ 主界面", "docs/截图/主界面.png",
         "主界面采用左侧设备列表 + 右侧 Tab 页面的布局，顶部为系统操作区，底部为状态栏。集成设备连接、系统操作、应用管理、文件传输、日志抓取、性能监控六大核心功能于一体。",
         ["启动后自动扫描已连接的 USB 设备和局域网设备",
          "左侧列表选择目标设备，右侧 Tab 切换功能页面",
          "顶部按钮区提供一键重启、代理设置、投屏、抓包等快捷操作",
          "标题栏下拉菜单可切换 6 套主题，设置自动保存"]),
        ("🔌 设备连接与无线调试", "docs/截图/无线调试.png",
         "三合一无线调试弹窗，支持局域网扫描自动发现、配对码连接（adb pair）、二维码连接（mDNS 自动监听 + 扫码回填）三种方式。同时支持 USB 直连，自研 ADB 协议栈与官方 adb 可一键切换。",
         ["**USB 连接**：手机开启 USB 调试，插入数据线，设备列表自动出现",
          "**局域网扫描**：点击「无线调试」→「局域网扫描」，自动扫描 5555 端口设备",
          "**配对码连接**：手机开发者选项 → 无线调试 → 使用配对码配对，输入 6 位配对码",
          "**二维码连接**：手机展示配对二维码，PC 端扫码自动完成 mDNS 发现与配对"]),
        ("📡 局域网扫描", "docs/截图/局域网扫描.png",
         "局域网设备扫描工具，自动探测网段内开启 ADB 调试（5555 端口）的设备，显示 IP、设备型号、连接状态。支持自定义网段和超时时间，扫描结果可一键连接。",
         ["点击「局域网扫描」自动探测当前网段",
          "扫描过程中实时显示发现的设备及响应时间",
          "点击设备行即可快速连接，连接成功后自动加入设备列表",
          "支持自定义 IP 段扫描，适配不同网络环境"]),
        ("🔍 IP 扫描", "docs/截图/IP扫描.png",
         "指定 IP 地址的设备连接工具，支持手动输入 IP 和端口，自动检测设备是否在线。适用于局域网扫描未覆盖到的设备，或已知 IP 的直连场景。",
         ["在 IP 输入框中填写设备 IP 地址（缺省端口自动补 5555）",
          "点击「连接」前自动 ping 检测设备是否在线",
          "连接成功后设备出现在主界面设备列表",
          "连接失败时显示具体原因（超时/拒绝/未授权等）"]),
        ("📶 WiFi 管理", "docs/截图/WiFi管理.png",
         "设备 WiFi 网络管理工具，查看已保存的 WiFi 列表、当前连接状态、信号强度，支持忘记网络、重新连接、查看 WiFi 密码（需 root）。",
         ["打开后自动加载设备已保存的 WiFi 列表",
          "显示每个 WiFi 的 SSID、信号强度、加密方式、连接状态",
          "点击「连接」切换 WiFi，「忘记」移除已保存网络",
          "root 设备可查看已保存 WiFi 的明文密码"]),
        ("🔑 WiFi 配对", "docs/截图/WiFi配对.png",
         "WiFi 配对连接工具，通过 adb pair 命令完成 Android 11+ 无线调试配对。输入配对码和端口，自动完成配对并连接，配对成功后设备可通过 WiFi 调试。",
         ["手机开发者选项 → 无线调试 → 使用配对码配对设备",
          "将手机显示的 6 位配对码和端口填入工具",
          "点击「配对」自动执行 adb pair，配对成功后自动连接",
          "配对信息保存后，后续可直接连接无需再次配对"]),
        ("📁 文件管理", "docs/截图/文件管理.png",
         "设备文件树浏览器，支持上传/下载、删除、重命名、权限修改（右键「授权 777」）、文本预览、递归搜索。自研 ADB sync 协议快速传输，上传速度可达官方 adb 的 2.7 倍。只读分区自动检测并附解锁引导。",
         ["选择设备后自动加载根目录文件列表",
          "双击文件夹进入，点击路径栏可快速跳转",
          "拖拽本地文件到窗口即可上传，右键文件可下载/删除/重命名",
          "搜索框支持当前路径和递归搜索两种模式",
          "右键「授权 777」可快速修改文件权限（需 root）"]),
        ("📦 应用管理（安装/解包）", "docs/截图/安装解包.png",
         "APK 安装与解包工具，支持拖拽安装、批量安装、安装进度实时显示、APK 元信息解析（包名/版本/权限/组件）、解包查看资源。三阶段安装流程（push → pm install → rm），失败时自动诊断原因。",
         ["拖拽 APK 文件到窗口，或点击「选择 APK」浏览",
          "自动解析 APK 信息（包名、版本、权限列表、四大组件）",
          "点击「安装」开始，进度条实时显示上传和安装进度",
          "安装失败时显示具体原因（空间不足/签名冲突/版本降级等）",
          "「解包」可查看 APK 内部资源文件结构"]),
        ("📋 日志抓取", "docs/截图/日志抓取.png",
         "多标签 logcat 查看器，支持关键字过滤、标签/进程/消息星标、实时流式输出、日志级别筛选、导出保存。可同时打开多个设备的日志标签页，互不干扰。",
         ["选择设备后自动启动 logcat 实时输出",
          "顶部过滤框输入关键字实时过滤，支持正则表达式",
          "点击日志行左侧星标可标记重要日志，过滤栏可只看星标",
          "右键可复制单行/全部日志，或导出为 .txt 文件",
          "「新建标签」可同时监控多个设备或多个过滤条件"]),
        ("📊 性能监控", "docs/截图/性能监控.png",
         "双层性能监控体系：设备级（CPU 多核分核/内存/温度/FPS/网络速率）+ 应用级（12 项图表指标、内存泄漏自动检测、ANR/OOM 检测、hprof 自动抓取）。支持 HTML 报告导出，数据实时刷新。",
         ["**设备级监控**：主界面「性能监控」Tab，实时显示 CPU/内存/温度/FPS 曲线",
          "**应用级监控**：选择目标应用，点击「应用性能监控」打开独立窗口",
          "内存泄漏检测自动运行，发现泄漏时自动抓取 hprof 并提示",
          "监控结束后可导出 HTML 报告，包含所有图表和异常记录",
          "图表支持缩放、暂停、数据点查看"]),
        ("🐒 Monkey 压测", "docs/截图/Monkey压测.png",
         "Monkey 压力测试管理窗口，支持命令模板自定义、暂停/继续/停止控制、实时事件饼图统计、崩溃报告自动拉取、事件回放。可设置事件数、间隔、种子、触摸/手势/轨迹球比例等参数。",
         ["选择目标应用，设置事件总数、间隔时间、种子等参数",
          "点击「开始」启动 Monkey，实时显示事件统计饼图",
          "运行中可随时「暂停」/「继续」/「停止」",
          "发生崩溃时自动拉取 tombstone 和 logcat 崩溃报告",
          "支持事件回放，用相同种子复现崩溃场景"]),
        ("🌐 网络抓包", "docs/截图/网络抓包.png",
         "tcpdump 网络抓包工具，自动检测设备架构并推送对应 tcpdump 二进制（arm64/arm），支持 BPF 过滤器、实时包数统计、停止后自动拉取 pcap 文件。非 root 设备通过 su 提权抓包，支持自定义保存路径。",
         ["点击「网络抓包」打开窗口，自动检测设备是否已安装 tcpdump",
          "未安装时自动推送对应架构的二进制到 /data/local/tmp/（需 root）",
          '输入 BPF 过滤器（如 "tcp and port 80"），点击「开始抓包」',
          "实时显示捕获包数、过滤器接收数、内核丢包率",
          "点击「停止」自动拉取 pcap 文件到本地"]),
        ("📈 PCAP 解析", "docs/截图/PCAP解析.png",
         "PCAP 抓包文件解析器，支持 HTTP/HTTPS/TCP/UDP 协议分析、流重组、请求/响应查看、数据包详细信息展示。可按协议、IP、端口过滤，支持导出单个流的原始数据。",
         ["打开 pcap 文件后自动解析并显示数据包列表",
          "支持按协议（HTTP/TCP/UDP）、源/目标 IP、端口过滤",
          "点击数据包查看详细信息（以太网/IP/TCP/HTTP 各层字段）",
          "TCP 流重组功能可查看完整的请求/响应交互",
          "支持导出单个流或过滤后的数据包为新 pcap"]),
        ("📺 scrcpy 投屏", "docs/截图/投屏.png",
         "集成官方 scrcpy 投屏工具，支持分辨率/码率/帧率/编码器/渲染驱动等参数自定义。低延迟投屏，支持键鼠反向控制、文件拖拽传输、屏幕录制。参数设置自动保存，下次启动自动加载。",
         ["选择设备后点击「投屏」按钮启动 scrcpy",
          "「投屏设置」可调整分辨率（默认 1080p）、码率（默认 8Mbps）、帧率（默认 60fps）",
          "可选择编码器（h264/h265）和渲染驱动（direct3d/opengl/metal）",
          "投屏窗口中可直接用键鼠控制手机，拖拽文件到窗口即可传输",
          "支持屏幕录制，录制文件保存到本地"]),
        ("💻 ADB 终端", "docs/截图/ADB终端.png",
         "交互式 ADB shell 终端，支持命令历史记录（上下键）、Tab 自动补全、多设备切换、实时输出。内置常用命令快捷按钮，支持自定义命令模板，输出可复制和导出。",
         ["选择设备后自动进入 adb shell 交互模式",
          "输入命令回车执行，上下键浏览历史命令，Tab 补全文件名/命令",
          "顶部设备下拉框可快速切换到其他设备的 shell",
          "常用命令（ls/cd/ps/top/dumpsys）一键执行",
          "输出内容可全选复制，支持导出为文本文件"]),
        ("🛠️ 便捷工具", "docs/截图/便捷工具.png",
         "集成多款实用小工具的统一入口，包括命令行、JSON 工具、哈希校验、时间戳转换、ADB 终端、设备信息、修改时间、证书安装、环境配置等。工具窗口支持独立打开和多开。",
         ["主界面「便捷工具」按钮打开工具面板",
          "点击工具图标在独立窗口中打开，支持同时打开多个工具",
          "工具窗口位置和大小自动保存，下次打开恢复",
          "每个工具有独立的使用说明和操作提示"]),
        ("📝 JSON 工具", "docs/截图/JSON工具.png",
         "JSON 格式化与处理工具，支持格式化/压缩、左右差异对比、YAML 互转、JSON Schema 校验、树形视图展开折叠。大文件自动分页，支持从文件加载和保存结果。",
         ["粘贴 JSON 文本自动格式化，语法错误时标红提示行号",
          "左右两栏对比差异，新增/删除/修改行高亮显示",
          "支持 JSON ↔ YAML 互相转换",
          "树形视图可展开/折叠节点，支持搜索定位",
          "可加载本地 JSON 文件，处理结果保存到本地"]),
        ("🔐 哈希校验", "docs/截图/哈希校验.png",
         "文件哈希校验工具，支持 MD5/SHA1/SHA256/SHA512/CRC32 等 8 种算法同时计算，拖拽文件即算，支持大文件分块读取。可注册 Windows 右键菜单，右键文件直接计算哈希。",
         ["拖拽文件到窗口即开始计算，支持多文件批量计算",
          "8 种算法同时显示，可复制任意一种哈希值",
          "支持输入目标哈希值进行比对，一致/不一致高亮显示",
          "「注册右键菜单」后可在资源管理器右键文件直接计算",
          "大文件采用分块流式读取，不占用过多内存"]),
        ("⏰ 时间戳转换", "docs/截图/时间戳转换.png",
         "时间戳与日期时间双向转换工具，自动识别秒/毫秒/微秒/纳秒单位，支持北京时间和 UTC 时间显示，可获取当前时间戳，支持批量转换。",
         ["输入时间戳自动识别单位并转换为北京时间",
          "输入日期时间自动转换为时间戳（秒/毫秒/微秒/纳秒）",
          "「现在」按钮一键获取当前时间戳和对应时间",
          "支持 UTC 时间和北京时间切换显示",
          "批量转换模式可一次处理多行时间戳"]),
        ("🕐 修改系统时间", "docs/截图/修改时间.png",
         "修改设备系统时间工具（需 root），支持手动设置日期时间、与 PC 时间同步、NTP 时间同步。修改前自动备份原时间，可一键恢复。",
         ["选择设备后显示当前系统时间",
          "手动设置日期和时间，点击「应用」修改设备时间（需 root）",
          "「同步 PC 时间」一键将设备时间同步为电脑当前时间",
          "「NTP 同步」从网络时间服务器获取标准时间并设置",
          "修改前自动备份，可一键恢复原时间"]),
        ("📋 设备信息", "docs/截图/设备信息.png",
         "设备详细信息查看工具，一键获取设备型号、Android 版本、序列号、屏幕分辨率、IP 地址、MAC 地址、CPU 架构、内存大小、存储容量、电池状态、Root 状态等全面信息。",
         ["选择设备后自动加载全部设备信息",
          "按类别分组显示（基本信息/硬件/网络/电池/系统）",
          "点击任意字段可复制该信息",
          "「导出」按钮可将全部设备信息保存为文本文件",
          "支持多设备信息对比查看"]),
        ("📜 证书安装", "docs/截图/证书安装.png",
         "CA 证书安装工具，支持将 Charles/Fiddler/mitmproxy 等抓包工具的 CA 证书安装到设备系统证书目录（需 root）。自动转换证书格式，安装后可直接抓取 HTTPS 流量。",
         ["选择抓包工具导出的 CA 证书文件（.pem/.crt/.cer）",
          "自动检测证书格式并转换为 Android 系统证书格式（hash.0）",
          "root 设备一键安装到 /system/etc/security/cacerts/",
          "安装后设备自动信任该 CA，可抓取 HTTPS 流量",
          "支持查看已安装的系统证书列表和卸载"]),
        ("⚙️ 环境配置", "docs/截图/环境配置.png",
         "全局环境配置工具，设置 ADB 路径、scrcpy 路径、tcpdump 路径、默认主题、语言、截图保存目录、日志级别等。配置自动保存，修改后即时生效。",
         ["「ADB 路径」设置官方 adb 可执行文件位置，留空使用内置",
          "「scrcpy 路径」设置投屏工具路径，留空使用内置",
          "「默认主题」选择启动时加载的主题",
          "「截图保存目录」设置自动生成截图的保存位置",
          "配置修改后自动保存，重启后生效"]),
    ]

    lines = ["# 功能介绍与使用说明", ""]
    lines.append("> Super_ADB 核心功能模块的详细介绍、操作步骤与界面展示。")
    lines.append("")
    for title, img, desc, steps in features:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(f"![{title}]({img})")
        lines.append("")
        lines.append("**功能介绍**")
        lines.append("")
        lines.append(desc)
        lines.append("")
        lines.append("**使用说明**")
        lines.append("")
        for i, s in enumerate(steps, 1):
            lines.append(f"{i}. {s}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ── 公众号引导 + 下载地址 ──
    lines.append("## 📢 关注公众号")
    lines.append("")
    lines.append("扫码关注公众号，获取最新版本更新、使用教程和技术分享。")
    lines.append("")
    lines.append("![公众号](Super_ADB_Win/resources/wechat_qrcode.jpg)")
    lines.append("")
    lines.append("## 📥 下载地址")
    lines.append("")
    lines.append("下载地址：https://pan.quark.cn/s/2b7b11ebe1e5?pwd=fAXN")
    lines.append("")

    out = PROJECT_ROOT / "docs" / "USAGE.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ Markdown 生成: {out}")
    print(f"   功能数: {len(features)} 个")
    print(f"   文件大小: {out.stat().st_size / 1024:.1f} KB")



def main():
    print('扫描项目文件...')
    files = scan_python_files()
    print(f'  发现 {len(files)} 个 Python 文件')

    print('扫描类继承关系...')
    classes = scan_classes()
    print(f'  发现 {len(classes)} 个类定义')

    print('扫描依赖关系...')
    deps = scan_imports()
    print(f'  发现 {len(deps)} 条包间依赖')
    module_deps = scan_module_imports()
    print(f'  发现 {len(module_deps)} 条模块间依赖')

    print('动态获取配置...')
    package_desc = 获取包描述()
    print(f'  包描述: {len(package_desc)} 个包')
    themes = 获取主题列表()
    print(f'  主题列表: {len(themes)} 套')
    base_dlgs, frameless_dlgs, widget_dlgs = 分类对话框(classes)
    print(f'  对话框分类: 标准{len(base_dlgs)} / 无边框{len(frameless_dlgs)} / QWidget{len(widget_dlgs)}')
    buttons = 获取按钮功能清单()
    print(f'  按钮连接: {len(buttons)} 个')
    config_fields = 获取配置文件字段()
    print(f'  配置字段: {len(config_fields)} 个')
    third_deps = 获取第三方依赖()
    print(f'  第三方依赖: {len(third_deps)} 个')
    shortcuts = 获取快捷键()
    print(f'  快捷键: {len(shortcuts)} 个')

    print('生成对话框模拟运行截图...')
    跳过截图 = ('--跳过截图' in sys.argv) or ('--skip-shots' in sys.argv)
    dialog_shots, dialog_failures = 生成对话框模拟截图(跳过=跳过截图)
    print(f'  截图成功: {len(dialog_shots)} 个 / 失败: {len(dialog_failures)} 个')

    print('生成主界面及功能截图...')
    main_shots = 生成主界面截图(跳过=跳过截图)
    print(f'  功能截图: {len(main_shots)} 个')

    print('生成 HTML...')
    html = HTML_TEMPLATE.format(
        stats=build_stats(files, classes, themes),
        structure_tree=build_structure_tree(files, package_desc),
        dependency_mermaid=build_dependency_mermaid(deps),
        module_dependency_mermaid=build_module_dependency_mermaid(module_deps),
        inheritance_tree=build_inheritance_tree(classes),
        theme_rows=build_theme_table(themes),
        button_table=build_button_table(buttons),
        button_count=len(buttons),
        config_table=build_config_table(config_fields),
        deps_table=build_deps_table(third_deps),
        shortcut_list=build_shortcut_list(shortcuts),
        dialog_list=build_dialog_list(classes),
        wifi_flow=build_wifi_connection_mermaid(),
        usb_flow=build_usb_connection_mermaid(),
        qr_flow=build_qr_connection_mermaid(),
        benchmark=build_benchmark_table(),
        dialog_screenshots=build_dialog_screenshots(dialog_shots, dialog_failures),
        dialog_shot_count=len(dialog_shots),
        date=datetime.now().strftime('%Y-%m-%d %H:%M'),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'\n✅ 生成完成: {OUTPUT_HTML}')
    print(f'   文件大小: {OUTPUT_HTML.stat().st_size / 1024:.1f} KB')

    生成功能介绍Markdown()


if __name__ == '__main__':
    main()
