# -*- coding: utf-8 -*-
"""
生成项目全景文档
================
自动扫描 Super_ADB_Win/ 项目结构、类继承、依赖关系，
生成包含 mermaid 图表的完整 HTML 项目全景文档。

用法：
    python Super_ADB_Win/脚本/生成项目全景文档.py

输出：
    项目根目录/项目全景文档.html
"""
import ast
import re
import sys
from pathlib import Path
from datetime import datetime


# ============================================================
# 配置
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # G:\Python\jcspy\Super_ADB
WIN_ROOT = PROJECT_ROOT / 'Super_ADB_Win'
OUTPUT_DIR = PROJECT_ROOT / '项目说明'
OUTPUT_HTML = OUTPUT_DIR / '项目全景文档.html'

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
                text = init_file.read_text(encoding='utf-8').strip()
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
    """动态从 项目UI.界面样式 模块导入 THEMES 字典。"""
    try:
        from 项目UI import 界面样式
        themes = []
        for tid, info in 界面样式.THEMES.items():
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
    main_file = WIN_ROOT / '项目启动入口' / 'Super_ADB_主入口.py'
    ui_file = WIN_ROOT / '项目UI' / 'Super_ADB.py'
    if not main_file.exists():
        return []
    main_text = main_file.read_text(encoding='utf-8')
    conns = re.findall(r'self\.(\w+)\.clicked\.connect\(self\.(\w+)\)', main_text)

    # 从编译后的 UI 提取按钮 text
    btn_texts = {}
    if ui_file.exists():
        ui_text = ui_file.read_text(encoding='utf-8')
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
    cfg_file = WIN_ROOT / '配置' / 'Super_ADB配置.json'
    if not cfg_file.exists():
        return []
    try:
        import json
        data = json.loads(cfg_file.read_text(encoding='utf-8'))
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
    for line in req_file.read_text(encoding='utf-8').splitlines():
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
    main_file = WIN_ROOT / '项目启动入口' / 'Super_ADB_主入口.py'
    if not main_file.exists():
        return []
    text = main_file.read_text(encoding='utf-8')
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
            lines = len(p.read_text(encoding='utf-8').splitlines())
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
            tree = ast.parse(p.read_text(encoding='utf-8'))
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
            text = p.read_text(encoding='utf-8')
        except Exception:
            continue
        # 匹配 from 包名.模块 import ...
        for m in re.finditer(r'from\s+([\u4e00-\u9fa5\w]+)\.', text):
            target = m.group(1)
            if target != src_pkg and target in 已知包:
                deps.append((src_pkg, target))
    return list(set(deps))


# ============================================================
# HTML 生成器
# ============================================================
def build_structure_tree(files, package_desc):
    """生成可折叠项目结构树 HTML（默认折叠）。"""
    # 构建嵌套字典树
    tree = {}
    for rel, lines in files.items():
        parts = rel.split('\\')
        node = tree
        for part in parts[:-1]:
            if part not in node:
                node[part] = {'_files': {}}
            node = node[part]
        node['_files'][parts[-1]] = lines

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
            text = p.read_text(encoding='utf-8')
        except Exception:
            continue
        # 匹配 from 包名.模块 import ...
        for m in re.finditer(r'from\s+([\u4e00-\u9fa5\w\.]+)\s+import', text):
            target = m.group(1)
            if target != src_mod and target.startswith(('项目启动入口', '项目UI', '对话框', '页面', '监控', '工具', '配置', '脚本', '打包', '资源')):
                deps.append((src_mod, target))
        # 匹配 import 包名.模块
        for m in re.finditer(r'^import\s+([\u4e00-\u9fa5\w\.]+)', text, re.MULTILINE):
            target = m.group(1)
            if target != src_mod and target.startswith(('项目启动入口', '项目UI', '对话框', '页面', '监控', '工具', '配置', '脚本', '打包', '资源')):
                deps.append((src_mod, target))
    return list(set(deps))


def build_module_dependency_mermaid(module_deps):
    """生成模块级依赖关系 mermaid 图（仅显示核心模块）。"""
    # 只显示核心模块（主入口、对话框基类、工具等）
    核心模块 = {
        '项目启动入口.Super_ADB_主入口': '主入口',
        '项目启动入口.主入口_弹窗打开': '弹窗打开Mixin',
        '项目启动入口.主入口_设备管理': '设备管理Mixin',
        '项目启动入口.主入口_主题系统': '主题系统Mixin',
        '项目UI.对话框基类': '对话框基类',
        '项目UI.界面样式': '界面样式',
        '项目UI.弹窗样式': '弹窗样式',
        '工具.ADB工具': 'ADB工具',
        '工具.自研adb.adb协议': '自研ADB协议层',
        '工具.自研adb.自研adb客户端': '自研ADB客户端',
        '工具.自研adb.scrcpy会话': 'scrcpy会话',
        '工具.投屏客户端': '投屏客户端',
        '工具.h264解码器': 'openh264解码器',
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
    dialog_count = len([f for f in files if '对话框' in f or '窗口' in f])
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
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-brand">Super_ADB</div>
  <h2>📋 文档导航</h2>
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
  <a href="#engineering">工程规范</a>
  <a href="#extension">扩展指南</a>
</nav>

<div class="main">

<h1>Super_ADB 项目全景文档</h1>
<p class="subtitle">PySide6 桌面 ADB 工具 · 项目结构 / 依赖 / 继承 / 风格 / 扩展指南</p>

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
    <span class="badge">三种ADB模式切换</span>
  </p>
</div>

<!-- 项目结构 -->
<h2 id="structure">📁 项目结构</h2>
<p class="section-intro">Super_ADB_Win/ 为项目根目录，按功能划分为 8 个包，所有包均含 <code>__init__.py</code>。</p>
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
    <tr><td>新建文件</td><td>中文命名</td><td><code>证书安装对话框.py</code></td></tr>
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
    <tr><td>编译输出</td><td><code>项目UI/Super_ADB.py</code></td><td>pyside6-uic 自动生成</td></tr>
    <tr><td>样式设置</td><td>主入口代码</td><td>setStyleSheet / 主题色</td></tr>
    <tr><td>信号连接</td><td>主入口代码</td><td>clicked.connect / 功能绑定</td></tr>
    <tr><td>资源文件</td><td><code>ui/png.qrc</code> → <code>png_rc.py</code></td><td>pyside6-rcc 编译</td></tr>
  </table>
  <h4>编译命令</h4>
<pre><code>pyside6-uic "ui\\Super_ADB.ui" -o "Super_ADB_Win\\项目UI\\Super_ADB.py"
pyside6-rcc "ui\\png.qrc" -o "Super_ADB_Win\\项目UI\\png_rc.py"</code></pre>
</div>

<h3>主题系统</h3>
<div class="card">
  <p>7 套主题，统一由 <code>界面样式.py</code> 管理，通过 <code>get_stylesheet(theme_id)</code> 获取 QSS。</p>
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
    <code>from 对话框.证书安装对话框 import 证书安装对话框</code>
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
  <h4>Super_ADB_主入口.py — 主窗口</h4>
  <p>主窗口类，继承 QWidget + Ui_MainWindow + 3个 Mixin。负责窗口初始化、信号连接、ADB 实例管理、线程池、配置持久化。</p>
  <p><strong>关键属性：</strong><code>self.adb</code>(Adb设备操作)、<code>self.pool</code>(QThreadPool)、<code>self._current_theme</code>、<code>self._live_workers</code></p>
</div>
<div class="card-grid">
  <div class="card">
    <h4>主入口_弹窗打开.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 14个 open_xxx 方法，创建并显示对话框/窗口，支持实例复用（重复点击 raise）。</p>
  </div>
  <div class="card">
    <h4>主入口_设备管理.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 设备连接/断开/扫描，<code>当前序列号()</code> 获取当前选中设备序列号。</p>
  </div>
  <div class="card">
    <h4>主入口_主题系统.py</h4>
    <p><span class="tag tag-mixin">Mixin</span> 7套主题切换、标题栏按钮样式、品牌标识、弹窗主题传播。</p>
  </div>
</div>

<h3>UI 层</h3>
<div class="card-grid">
  <div class="card">
    <h4>对话框基类.py</h4>
    <p><span class="tag tag-base">基类</span> 统一对话框图标、样式、发光效果、主题切换。参数：<code>标题</code>/<code>最小尺寸</code>/<code>发光</code>。</p>
  </div>
  <div class="card">
    <h4>界面样式.py</h4>
    <p>7套主题 QSS 定义，<code>get_stylesheet(theme_id)</code> / <code>get_theme_ids()</code> / <code>get_theme_name()</code>。</p>
  </div>
  <div class="card">
    <h4>弹窗样式.py</h4>
    <p><code>无边框缩放Mixin</code>（边缘拖拽缩放）、<code>add_green_glow</code>（发光效果）、<code>拖拽区域</code>（拖拽区）。</p>
  </div>
</div>

<h3>工具层</h3>
<div class="card">
  <h4>ADB工具.py — Adb设备操作（2065行）</h4>
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

<h3>对话框层（21个对话框/窗口）</h3>
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
      <li><code>设备信息对话框</code> — 设备属性/标识符</li>
      <li><code>投屏窗口对话框</code> — scrcpy投屏窗口</li>
      <li><code>scrcpy_设置对话框</code> — 投屏参数设置</li>
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

<h3>自研 ADB 协议栈（工具/自研adb/）</h3>
<p class="section-intro">不依赖官方 adb 二进制的纯 Python ADB 实现，TCP/USB 双通道，与官方 adb 可切换。7个模块，共约2700行。</p>
<div class="card-grid">
  <div class="card">
    <h4>adb协议.py — 协议层（1247行）</h4>
    <p>实现 CNXN/AUTH/OPEN/WRTE/CLSE 状态机与 sync 协议。认证：RSA2048 + SHA1 PKCS1v15 签名；公钥为 524 字节 android_pubkey_t 的 base64。ADB_VERSION=0x01000001（skip checksum）。<code>_定位密钥路径()</code> 统一解析密钥位置：打包版放 exe 旁 <code>配置/</code> 并自动迁移已授权密钥。</p>
  </div>
  <div class="card">
    <h4>自研adb客户端.py — 连接池（385行）</h4>
    <p>设备级建连锁（RLock）+ 连接池借用/剥离；<strong>30 秒负缓存</strong>防认证失败重试风暴；公钥授权 <strong>60 秒循环等待</strong>；主连接模式（短操作共享主连接加锁串行，长操作用独立连接）；认证失败原因精确上报。</p>
  </div>
  <div class="card">
    <h4>usb连接.py + usb传输层.py</h4>
    <p>基于 pyusb/libusb1 的 USB ADB 实现，与 TCP 共用同一份密钥。支持USB设备热插拔检测。</p>
  </div>
  <div class="card">
    <h4>scrcpy会话.py — scrcpy会话（584行）</h4>
    <p>推送 scrcpy-server、建立视频 socket、解析 H.264 流配置，交给投屏客户端渲染。支持reverse模式。</p>
  </div>
  <div class="card">
    <h4>多设备管理器.py</h4>
    <p>多设备连接管理，统一调度各设备的连接池和认证状态。</p>
  </div>
</div>

<h3>投屏解码链路（工具/）</h3>
<div class="card">
  <h4>投屏客户端.py + h264解码器.py</h4>
  <p>scrcpy 视频流 → <strong>h264解码器</strong>（ctypes 封装内置 openh264，外部扩展/openh264/，~4MB）→ OpenGL 纹理渲染。<strong>已弃用 PyAV</strong>（其 hook 会收集全量 ffmpeg 编码器 62.5MB）。解码器接口兼容原 av 用法，设备不支持 H.264 时优雅降级提示。</p>
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

<h3>配置文件（Super_ADB配置.json）</h3>
<div class="card">
{config_table}
<p style="margin-top:10px;color:var(--text2);font-size:12px;">配置文件位于 <code>Super_ADB_Win/配置/</code> 目录，启动时自动加载，退出时保存窗口几何和主题。</p>
</div>

<h3>第三方依赖（requirements.txt）</h3>
<div class="card">
{deps_table}
</div>

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
    <p>密钥 <code>super_adb_key(+.pub)</code> 源码模式在 <code>配置/</code>，打包版在 exe 旁 <code>配置/</code>（首次访问自动从旧位置/源码树迁移）。认证失败后 <strong>30 秒负缓存</strong>冷却；发公钥后 <strong>60 秒循环等待</strong>设备授权（盒子/TV 等无授权弹窗的 ROM 会断开连接，错误消息提示复制已授权密钥）。</p>
  </div>
  <div class="card">
    <h4>🎬 投屏 H.264 解码链路</h4>
    <p>scrcpy-server 推送 H.264 NAL → <code>h264解码器</code>（ctypes 调 openh264 DLL）→ YUV → OpenGL 纹理上屏。解码线程与渲染线程解耦，停屏时快速退出并释放解码器。</p>
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
  <p>使用 <strong>PyInstaller</strong> 打包，入口脚本 <code>打包/精简打包exe.py</code>。</p>
  <ul style="margin:10px 0 10px 20px;color:var(--text);">
    <li><code>打包/裁剪_qt.py</code> — 构建后按 DLL 依赖闭包裁剪 Qt 插件/翻译</li>
    <li><code>打包/hooks/hook-pyzbar.py</code> — pyzbar 运行时钩子</li>
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
  <p><code>脚本/</code> 目录包含工具脚本和测试脚本：</p>
  <ul style="margin:10px 0 10px 20px;color:var(--text);">
    <li><code>生成依赖关系图.py</code> — 生成 .dot/.svg/.png 和依赖关系图.md</li>
    <li><code>生成项目全景文档.py</code> — 生成本 HTML 文档</li>
    <li><code>生成图标.py</code> — 生成应用图标</li>
    <li><code>smoke_test.py</code> — 冒烟测试</li>
    <li><code>内存追踪.py</code> — 内存使用追踪</li>
    <li><code>测试_*.py</code> — 各模块单元测试（8个）</li>
  </ul>
</div>

<!-- 扩展指南 -->
<h2 id="extension">🚀 扩展指南</h2>
<p class="section-intro">新增功能时的规范流程和注意事项。</p>

<h3>新增对话框（标准）</h3>
<div class="card">
<pre><code># 1. 在 对话框/ 目录新建文件，中文命名
# 对话框/我的新功能对话框.py

from 项目UI.对话框基类 import 对话框基类

class 我的新功能对话框(对话框基类):
    def __init__(self, parent=None):
        super().__init__(parent, 标题='我的新功能', 最小尺寸=(520, 400), 发光=True)
        # 构建 UI...

    def apply_theme(self, theme_id):
        # 可选：自定义主题切换逻辑
        super().apply_theme(theme_id)

# 2. 在 主入口_弹窗打开.py 添加打开方法
def open_my_dialog(self):
    if self._my_dialog is not None and self._my_dialog.isVisible():
        self._my_dialog.raise_()
        return
    from 对话框.我的新功能对话框 import 我的新功能对话框
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
from 项目UI.弹窗样式 import 无边框缩放Mixin

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
<pre><code># 在 工具/ADB工具.py 的 Adb设备操作 类中添加
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
<div class="warn-box"><strong>⚠️ 包式导入：</strong>所有 import 使用包名前缀（<code>from 对话框.xxx import ...</code>），不要使用裸导入。sys.path 只加 Super_ADB_Win/ 根目录。</div>
<div class="warn-box"><strong>⚠️ 线程安全：</strong>ADB 命令必须异步执行（QThreadPool + QRunnable），不能在主线程直接调用。结果通过信号回传。</div>
<div class="warn-box"><strong>⚠️ 主题传播：</strong>自定义对话框必须实现 <code>apply_theme(theme_id)</code> 方法，主题切换时会自动调用。无边框对话框需手动同步样式。</div>
<div class="warn-box"><strong>⚠️ 弹窗控件样式勿写死：</strong>弹窗内的 QLabel / QTextEdit / QPushButton 等子控件<strong>不要单独调用 setStyleSheet 写死颜色</strong>（如 <code>color:#58a6ff;background:#0d1117</code>），否则会覆盖全局主题，切换主题后控件样式不变。正确做法：只给对话框本身 <code>setStyleSheet(get_stylesheet(theme_id))</code>，子控件自动继承全局样式（界面样式.py 已定义 QTextEdit/QLabel/QPushButton 等主题样式）。若必须自定义，用 <code>THEMES[theme_id]</code> 取色并在主题切换时重新 apply。弹窗打开状态下切主题：在 <code>_切换主题</code> 中检查 <code>self._xxx弹窗</code> 是否存在，存在则调用 <code>setStyleSheet(get_stylesheet(新主题))</code>。参考：设备信息弹窗实现。</div>
<div class="warn-box"><strong>⚠️ 实例复用：</strong>弹窗打开方法必须检查实例是否已存在且可见，重复点击应 raise 而非新建。关闭后通过 destroyed 信号清空引用。</div>
<div class="warn-box"><strong>⚠️ 设备序列号：</strong>对话框标题应包含设备信息（<code>xxx — 设备: {{serial}}</code>），通过 <code>get_serial()</code> 回调获取，未连接时显示「未连接设备」。</div>

<h3>常用命令</h3>
<div class="card">
<pre><code># 编译 UI
pyside6-uic "ui\\Super_ADB.ui" -o "Super_ADB_Win\\项目UI\\Super_ADB.py"
pyside6-rcc "ui\\png.qrc" -o "Super_ADB_Win\\项目UI\\png_rc.py"

# 运行
D:\\Python\\Python314\\python.exe Super_ADB_Win\\项目启动入口\\Super_ADB_主入口.py

# 生成依赖图
python Super_ADB_Win\\脚本\\生成依赖关系图.py

# 生成项目全景文档（本脚本）
python Super_ADB_Win\\脚本\\生成项目全景文档.py

# 打包
python Super_ADB_Win\\打包\\精简打包exe.py

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
        date=datetime.now().strftime('%Y-%m-%d %H:%M'),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'\n✅ 生成完成: {OUTPUT_HTML}')
    print(f'   文件大小: {OUTPUT_HTML.stat().st_size / 1024:.1f} KB')


if __name__ == '__main__':
    main()
