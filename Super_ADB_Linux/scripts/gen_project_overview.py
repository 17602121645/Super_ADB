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
OUTPUT_DIR = PROJECT_ROOT / 'docs'
OUTPUT_HTML = OUTPUT_DIR / 'project_overview.html'

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
    cfg_file = WIN_ROOT / 'config' / 'super_adb_config.json'
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
    main_file = WIN_ROOT / 'app' / 'main.py'
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
            text = p.read_text(encoding='utf-8')
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
<p class="section-intro">Super_ADB 核心功能模块的详细介绍、操作步骤与界面展示。截图可放置于 <code>docs/screenshots/</code> 目录，文件名与下方占位一致即可自动显示。</p>

<h3>🖥️ 主界面</h3>
<div class="feature-block">
  <div class="feature-screenshot">
    <img src="screenshots/主界面.png" alt="主界面" onerror="this.style.display='none';this.nextElementSibling.style.display='flex';">
    <div class="screenshot-placeholder">📷 主界面截图<br><span>放置于 docs/screenshots/主界面.png</span></div>
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
    <div class="screenshot-placeholder">📷 无线调试截图<br><span>放置于 docs/screenshots/无线调试.png</span></div>
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
    <div class="screenshot-placeholder">📷 文件管理截图<br><span>放置于 docs/screenshots/文件管理.png</span></div>
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
    <div class="screenshot-placeholder">📷 日志抓取截图<br><span>放置于 docs/screenshots/日志抓取.png</span></div>
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
    <div class="screenshot-placeholder">📷 性能监控截图<br><span>放置于 docs/screenshots/性能监控.png</span></div>
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
    <div class="screenshot-placeholder">📷 Monkey压测截图<br><span>放置于 docs/screenshots/Monkey压测.png</span></div>
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
    <div class="screenshot-placeholder">📷 安装解包截图<br><span>放置于 docs/screenshots/安装解包.png</span></div>
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
    <div class="screenshot-placeholder">📷 网络抓包截图<br><span>放置于 docs/screenshots/网络抓包.png</span></div>
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
    <div class="screenshot-placeholder">📷 投屏截图<br><span>放置于 docs/screenshots/投屏.png</span></div>
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
    <div class="screenshot-placeholder">📷 便捷工具截图<br><span>放置于 docs/screenshots/便捷工具.png</span></div>
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
        wifi_flow=build_wifi_connection_mermaid(),
        usb_flow=build_usb_connection_mermaid(),
        qr_flow=build_qr_connection_mermaid(),
        benchmark=build_benchmark_table(),
        date=datetime.now().strftime('%Y-%m-%d %H:%M'),
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'\n✅ 生成完成: {OUTPUT_HTML}')
    print(f'   文件大小: {OUTPUT_HTML.stat().st_size / 1024:.1f} KB')


if __name__ == '__main__':
    main()
