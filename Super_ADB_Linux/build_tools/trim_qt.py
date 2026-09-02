# -*- coding: UTF-8 -*-
"""
构建后裁剪：Super_ADB 只用 QtCore/Gui/Widgets/Network + OpenGL（投屏渲染）。
PyInstaller 内置 hook-PySide6 会把整套 Qt6 DLL + 全部插件 + 全部翻译收进 dist，
additional-hooks-dir 只能追加、不能覆盖内置 hook，故改为构建后物理删除：
  1) 传递依赖闭包外的 Qt6 DLL（仅 Windows，基于 PyInstaller 的 bindepend）；
  2) 孤儿 Qt 插件（platforms/imageformats/iconengines/generic/networkinformation/tls）；
  3) Windows 根目录死重 OpenSSL 三件套（应用无任何 in-app HTTPS，走原生 qschannel）；
  4) 多余翻译 .qm（仅留 zh_CN + en 兜底）。

跨平台说明：
  - Windows 构建产物在 dist/Super_ADB/_internal（PySide6 模块为 .pyd，Qt 库为 qt6*.dll）。
  - macOS 构建产物在 dist/Super_ADB.app/Contents（模块为 .so，Qt 库为 libQt6*.dylib，
    插件为 libqxxx.dylib）。原脚本只认 .pyd，导致 Mac 构建直接 return、完全不裁剪
    （这是 Mac 产物 300M+ 的根因）。本版本在 .app 存在时走 Mac 分支，至少裁剪孤儿
    插件与翻译；Qt 框架闭包的精细裁剪需在 Mac 真机验证，故 Mac 分支不做闭包计算。
  - Linux 构建产物在 dist/Super_ADB/_internal（模块为 .so，Qt 库为 libQt6*.so，
    插件为 libqxxx.so）。Linux 分支裁剪孤儿插件与翻译；Qt 框架闭包裁剪基于
    bindepend 的 .so 依赖分析，与 Windows 逻辑类似但库名前缀不同。

安全闸门：
  - 若关键模块缺失/为空，或闭包不含 4 个核心 DLL，立即中止、不删除任何文件。
  - 设 TRIM_MOVE=1：删除改为改名移入 _trimmed_trash_<ts>/（应对构建环境禁止批量删除）。
  - 设 DRY_RUN=1：只列出「将删除」的文件，不实际删除（用于验证清单无误）。
"""
import os
import shutil
import time
import sys
import subprocess

try:
    from PyInstaller.depend import bindepend
except Exception:  # PyInstaller 不可用时（极少见），闭包裁剪整段跳过
    bindepend = None


IS_WIN = sys.platform == 'win32'
IS_MAC = sys.platform == 'darwin'
IS_LINUX = sys.platform == 'linux'
MOD_EXT = '.pyd' if IS_WIN else '.so'
# 插件 / 库扩展名
LIB_EXT = '.dll' if IS_WIN else ('.dylib' if IS_MAC else '.so')
# Mac/Linux 上库名带 lib 前缀（libqjpeg.dylib / libqxcb.so），Win 不带（qjpeg.dll）
LIB_PREFIX = 'lib' if (IS_MAC or IS_LINUX) else ''

# 各插件子目录「保留项」（按去前缀/去扩展名后的 base name 匹配；None = 整目录保留）
KEEP_PLUGINS = {
    # 渲染平台：Win 留 qwindows；Mac 留 qcocoa；Linux 留 qxcb(X11) + qwayland(Wayland)。
    # 三平台都额外保留 qoffscreen（约 50KB）：CI 的 headless runner 没有窗口服务，
    # 不留它打包产物在 CI 里根本起不来，冒烟测试就无从做起 —— 一个无法自动验证的
    # 产物等于没有防线（见 .github/scripts/smoke_test.py）。
    'platforms': (['qwindows', 'qoffscreen'] if IS_WIN else
                  (['qcocoa', 'qoffscreen'] if IS_MAC else
                   ['qxcb', 'qwayland', 'qminimal', 'qoffscreen'])),
    # 图片格式：图标(ico/icns)+截图(jpeg) 必需，其余格式删
    'imageformats': ['qico', 'qicns', 'qjpeg'],
    # 以下目录本应用完全用不到，整目录清空
    'iconengines': [],
    'generic': [],
    'networkinformation': [],
}
if IS_WIN:
    # TLS：Windows 走原生 qschannelbackend，删 qopensslbackend（及其依赖的 OpenSSL 库）
    KEEP_PLUGINS['tls'] = ['qschannelbackend', 'qcertonlybackend']
elif IS_MAC:
    # Mac 的 TLS 后端命名与依赖不同，且无法在此环境验证，整目录保留不动
    KEEP_PLUGINS['tls'] = None
else:
    # Linux：TLS 走 qopensslbackend（依赖系统 OpenSSL），必须保留；qcertonlybackend 也保留
    KEEP_PLUGINS['tls'] = ['qopensslbackend', 'qcertonlybackend']

# Windows 根目录死重（仅 Windows）：OpenSSL 全套 ~14MB，应用无 HTTPS 不需要。
# PyInstaller 可能同时收带/不带 -x64 后缀的两套（libssl-3.dll + libssl-3-x64.dll 等），
# 故用前缀匹配一并清理，避免漏网。
ORPHAN_ROOT_DLLS_PREFIX = ['libssl-3', 'libcrypto-3'] if IS_WIN else []

# Windows 根目录额外孤儿 DLL（精确文件名，整枚删除）：
#   SDL3.dll —— 投屏走 外部扩展/scrcpy/ 下的 scrcpy 自带 SDL3（其 exe 目录优先加载），
#              根目录这枚是 PyInstaller 误收集的孤儿，PE 导入表扫描确认无任何文件引用。
ORPHAN_ROOT_DLLS = ['SDL3.dll'] if IS_WIN else []


def _base(fname):
    """libqjpeg.dylib / qjpeg.dll -> qjpeg（去 lib 前缀 + 扩展名，小写）。"""
    n = fname
    if LIB_PREFIX and n.lower().startswith(LIB_PREFIX):
        n = n[len(LIB_PREFIX):]
    low = n.lower()
    for ext in ('.dll', '.dylib', '.so'):
        if low.endswith(ext):
            n = n[: -len(ext)]
            break
    return n.lower()


def _discard(root, fname, trash):
    """按 DRY_RUN / TRIM_MOVE 策略处理文件；返回 (action, path)。"""
    full = os.path.join(root, fname)
    if not os.path.isfile(full):
        return (None, full)
    if os.environ.get('DRY_RUN'):
        return ('DRY', full)
    if trash is not None:
        shutil.move(full, os.path.join(trash, fname))
        return ('MOVE', full)
    os.remove(full)
    return ('DEL', full)


def _trim_orphan_plugins(plugins_dir, removed_log):
    """删除 plugins 下各目录中不在 KEEP_PLUGINS 里的库文件（仅 LIB_EXT 后缀）。"""
    if not os.path.isdir(plugins_dir):
        return
    for sub, keep in KEEP_PLUGINS.items():
        d = os.path.join(plugins_dir, sub)
        if not os.path.isdir(d):
            continue
        if keep is None:  # 整目录保留
            continue
        keep_set = set(x.lower() for x in keep)
        for fname in os.listdir(d):
            if not fname.lower().endswith(LIB_EXT):
                continue
            if _base(fname) in keep_set:
                continue
            action, full = _discard(d, fname, removed_log)
            if action:
                print(f'  [{action}] 孤儿插件 {os.path.relpath(full)}')


def _trim_translations(trans_dir, removed_log):
    """翻译仅留 zh_cn / en。"""
    if not os.path.isdir(trans_dir):
        return
    keep = ('zh_cn', 'en')
    for fname in os.listdir(trans_dir):
        if not fname.lower().endswith('.qm'):
            continue
        base = fname.lower().rsplit('.', 1)[0]
        if base.endswith(keep):
            continue
        action, full = _discard(trans_dir, fname, removed_log)
        if action:
            print(f'  [{action}] 多余翻译 {os.path.relpath(full)}')


def _trim_windows(internal):
    ps = os.path.join(internal, 'PySide6')
    if not os.path.isdir(ps):
        print('未找到', ps, '（先跑 build_exe.py）')
        return

    # ---- 1) 校验关键 .pyd 存在且非空（构建残缺时为空文件，必须拦截） ----
    KEEP_MODS = ['QtCore', 'QtGui', 'QtWidgets', 'QtNetwork']
    for m in KEEP_MODS:
        p = os.path.join(ps, m + MOD_EXT)
        if not os.path.exists(p):
            print('ABORT: 关键模块缺失:', p)
            return
        if os.path.getsize(p) == 0:
            print('ABORT: 关键模块为空(构建残缺):', p)
            return

    trash = _make_trash(internal)

    # ---- 2) 计算保留 .pyd 的 Qt6 DLL 传递依赖闭包 ----
    closure = set()
    if bindepend is not None:
        keep_pyd = [os.path.join(ps, m + MOD_EXT) for m in KEEP_MODS]
        # 投屏 OpenGL 渲染视图用到 PySide6.QtOpenGL/QtOpenGLWidgets，其依赖的
        # Qt6OpenGL/Qt6OpenGLWidgets.dll 必须进闭包，否则第 3 步会删掉 DLL 而
        # pyd 保留 → 打包后 import 报 "DLL load failed"。存在才加入种子。
        keep_pyd += [os.path.join(ps, m + MOD_EXT)
                     for m in ('QtOpenGL', 'QtOpenGLWidgets')
                     if os.path.exists(os.path.join(ps, m + MOD_EXT))]
        stack = list(keep_pyd)
        while stack:
            f = stack.pop()
            try:
                imps = bindepend.get_imports(f, [ps])
            except Exception as e:
                print('  跳过依赖解析', os.path.basename(f), e)
                continue
            for _name, path in imps:
                if path is None:
                    continue
                bn = os.path.basename(path).lower()
                if bn.startswith('qt6') and bn.endswith('.dll'):
                    if bn not in closure:
                        closure.add(bn)
                        stack.append(path)
        # 安全闸门：闭包必须包含 4 个核心 DLL，否则视为解析失败，绝不删除
        CORE = {'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll', 'qt6network.dll'}
        if not CORE.issubset(closure):
            print('ABORT: 依赖闭包异常(缺少核心 DLL)，不删除任何 Qt6 DLL。closure=', sorted(closure))
            return
        print('Qt6 DLL 依赖闭包:', sorted(closure))

        # ---- 3) 删除闭包外的 Qt6 DLL ----
        for f in os.listdir(ps):
            if f.lower().startswith('qt6') and f.lower().endswith('.dll'):
                if f.lower() not in closure:
                    action, full = _discard(ps, f, trash)
                    if action:
                        print(f'  [{action}] 闭包外 Qt6 DLL {f}')

        # ---- 4) 额外 Qt 死重 DLL（非 qt6 命名）：SwiftShader 软件 OpenGL 渲染器 ~20MB ----
        for f in ('opengl32sw.dll',):
            if os.path.exists(os.path.join(ps, f)):
                action, full = _discard(ps, f, trash)
                if action:
                    print(f'  [{action}] 额外 Qt 死重 DLL {f}')

        # ---- 5) FFmpeg 死重（Qt6Multimedia 的 FFmpeg 解码后端，落在 _internal 根，
        #        非 qt6* 前缀，闭包裁剪漏网；Qt6Multimedia.dll 被删后成为孤岛）----
        #        PE 导入表扫描确认：这四枚 DLL 仅在彼此间互相 import，无任何
        #        .pyd / Qt6 DLL / 应用 exe 引用它们，删除零风险。
        for f in ('avcodec-62.dll', 'avformat-62.dll', 'avutil-60.dll', 'swresample-6.dll'):
            if os.path.exists(os.path.join(internal, f)):
                action, full = _discard(internal, f, trash)
                if action:
                    print(f'  [{action}] FFmpeg 死重 {f}')
    else:
        print('  (跳过 Qt6 闭包裁剪：PyInstaller 不可用)')

    # ---- 5) 孤儿插件 + 翻译 ----
    _trim_orphan_plugins(os.path.join(ps, 'plugins'), trash)
    _trim_translations(os.path.join(ps, 'translations'), trash)

    # ---- 6) 根目录死重 OpenSSL 全套（仅 Windows，前缀匹配覆盖带/不带 -x64 变体） ----
    for fn in os.listdir(internal):
        low = fn.lower()
        if low.endswith('.dll') and any(low.startswith(p) for p in ORPHAN_ROOT_DLLS_PREFIX):
            action, full = _discard(internal, fn, trash)
            if action:
                print(f'  [{action}] 根目录死重 {fn}')
        # 精确文件名孤儿 DLL（如 SDL3.dll）
        if low in (o.lower() for o in ORPHAN_ROOT_DLLS):
            action, full = _discard(internal, fn, trash)
            if action:
                print(f'  [{action}] 根目录孤儿 DLL {fn}')

    _report(internal)


def _trim_mac(contents):
    """macOS .app 分支：至少裁剪孤儿插件 + 翻译（不依赖 bindepend 闭包，Qt 框架由
    PyInstaller 自身依赖分析已做裁剪）。路径不存在时静默跳过对应步骤。"""
    frameworks = os.path.join(contents, 'Frameworks')
    resources = os.path.join(contents, 'Resources')
    plugins = os.path.join(contents, 'PlugIns')
    trash = _make_trash(contents)
    print('macOS 分支：裁剪孤儿插件 + 翻译（Qt 框架闭包裁剪需在 Mac 真机验证，暂跳过）')

    # 插件可能在 PlugIns/（PyInstaller 习惯）或 Frameworks/ 下，两处都扫
    for pdir in (plugins, os.path.join(frameworks, 'PySide6', 'plugins')):
        _trim_orphan_plugins(pdir, trash)
    # 翻译：Resources/translations 或 Frameworks/PySide6/translations
    for tdir in (os.path.join(resources, 'translations'),
                 os.path.join(frameworks, 'PySide6', 'translations')):
        _trim_translations(tdir, trash)

    # Qt 框架死重（如 libQt6Qml/libQt6Quick 等未被闭包引用的 .dylib）——
    # 此处保守地不自动删，留待 Mac 真机用闭包逻辑补全，避免误删。
    _report(contents)


def _iter_files(base, predicate):
    """递归收集 base 下所有文件名(小写)满足 predicate 的文件绝对路径。"""
    out = []
    if not os.path.isdir(base):
        return out
    for dp, _, fs in os.walk(base):
        for fn in fs:
            if predicate(fn.lower()):
                out.append(os.path.join(dp, fn))
    return out


def _find_subdirs(base, name):
    """递归收集 base 下所有名为 name 的子目录绝对路径。"""
    res = []
    if not os.path.isdir(base):
        return res
    for dp, dns, _ in os.walk(base):
        for d in dns:
            if d == name:
                res.append(os.path.join(dp, d))
    return res


def _elf_needed(f):
    """从 ELF 文件读取 NEEDED SONAME 列表（objdump/readelf 兜底，bindepend 不可用时用）。"""
    for cmd in (['objdump', '-p'], ['readelf', '-d']):
        try:
            r = subprocess.run(cmd + [f], capture_output=True, text=True, timeout=30)
        except Exception:
            continue
        out = (r.stdout or '') + (r.stderr or '')
        names = []
        for line in out.splitlines():
            if 'NEEDED' not in line:
                continue
            for tok in line.split():
                if '.so' in tok:
                    names.append(tok)
                    break
        if names:
            return names
    return []


def _trim_linux(internal):
    """Linux 分支：裁剪孤儿插件 + 翻译 + Qt6 闭包外 .so 库。

    兼容两种产物布局（解决此前 119MB 纹丝不动的根因）：
      - flat：libQt6*.so* 与 PySide6 模块同处 PySide6/ 下；
      - 嵌套：libQt6*.so* 在 PySide6/Qt/lib/，插件在 PySide6/Qt/plugins/。
    旧实现只对 PySide6/ 顶层 os.listdir：嵌套布局下闭包虽算出来却删错目录、
    插件/翻译目录也找不到 → 静默不裁。本版改为递归发现库文件与插件/翻译目录，
    并加 objdump/readelf 兜底解析依赖，确保嵌套布局也能正确裁剪。
    """
    ps = os.path.join(internal, 'PySide6')
    if not os.path.isdir(ps):
        alt = _find_subdirs(internal, 'PySide6')
        if alt:
            ps = alt[0]
        else:
            print('未找到', os.path.join(internal, 'PySide6'), '（先跑 build）')
            return

    # ---- 1) 校验关键 .so 存在且非空 ----
    KEEP_MODS = ['QtCore', 'QtGui', 'QtWidgets', 'QtNetwork']
    for m in KEEP_MODS:
        p = os.path.join(ps, m + MOD_EXT)
        if not os.path.exists(p):
            print('ABORT: 关键模块缺失:', p)
            return
        if os.path.getsize(p) == 0:
            print('ABORT: 关键模块为空(构建残缺):', p)
            return

    trash = _make_trash(internal)

    # ---- 2) 递归收集所有 libQt6*.so*（兼容 flat / Qt/lib 两种布局） ----
    qt_libs = _iter_files(ps, lambda l: l.startswith('libqt6')
                          and (l.endswith('.so') or '.so.' in l))
    qt_paths = set(qt_libs)
    soname_to_path = {os.path.basename(p).lower(): p for p in qt_libs}
    lib_dirs = list({os.path.dirname(p) for p in qt_libs}) or [ps]

    # ---- 3) 计算 libQt6*.so* 传递依赖闭包（bindepend 优先，objdump 兜底） ----
    closure_paths = set()
    seeds = [os.path.join(ps, m + MOD_EXT) for m in KEEP_MODS]
    seeds += [os.path.join(ps, m + MOD_EXT)
              for m in ('QtOpenGL', 'QtOpenGLWidgets')
              if os.path.exists(os.path.join(ps, m + MOD_EXT))]
    search = [ps] + lib_dirs + [internal]
    stack = list(seeds)
    seen = set()
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        needed = set()
        if bindepend is not None:
            try:
                for _name, path in bindepend.get_imports(f, search):
                    if path:
                        needed.add(os.path.basename(path).lower())
            except Exception as e:
                print('  bindepend 解析失败', os.path.basename(f), e)
        if not needed and IS_LINUX:
            needed.update(n.lower() for n in _elf_needed(f))
        for son in needed:
            if son.startswith('libqt6') and (son.endswith('.so') or '.so.' in son):
                tgt = soname_to_path.get(son)
                if tgt and tgt not in closure_paths:
                    closure_paths.add(tgt)
                    stack.append(tgt)

    # 安全闸门：闭包必须包含 4 个核心库（按 SONAME 前缀匹配，容忍版本号）
    names = [os.path.basename(p).lower() for p in closure_paths]
    CORE = ('libqt6core.so', 'libqt6gui.so', 'libqt6widgets.so', 'libqt6network.so')
    if not all(any(n.startswith(c) for n in names) for c in CORE):
        print('ABORT: 依赖闭包异常(缺少核心库)，不删除任何 Qt6 库。closure=',
              sorted(names))
        return
    print('libQt6*.so 依赖闭包:', sorted(names))

    # ---- 4) 删除闭包外的 libQt6*.so*（直接对递归收集到的文件操作，避免漏掉 Qt/lib 子目录） ----
    for p in qt_libs:
        if p not in closure_paths:
            action, _ = _discard(os.path.dirname(p), os.path.basename(p), trash)
            if action:
                print(f'  [{action}] 闭包外 libQt6 库 {os.path.relpath(p, internal)}')

    # ---- 5) 孤儿插件 + 翻译（递归找 plugins/translations 目录） ----
    for pdir in _find_subdirs(ps, 'plugins'):
        _trim_orphan_plugins(pdir, trash)
    for tdir in _find_subdirs(ps, 'translations'):
        _trim_translations(tdir, trash)

    _report(internal)


def _make_trash(base):
    if os.environ.get('TRIM_MOVE'):
        # 回收目录必须落在目标构建目录之外（如 dist/_trimmed_trash_xxx），
        # 否则文件只是从 _internal 移到同目录子文件夹，无法减小打包体积。
        # base 示例：dist/Super_ADB/_internal 或 dist/Super_ADB.app/Contents
        # -> dirname 两级到达 dist/ 或 .app 同级目录，再把 trash 放那里。
        parent = os.path.dirname(base)
        grand = os.path.dirname(parent)
        trash = os.path.join(grand, '_trimmed_trash_' + time.strftime('%Y%m%d%H%M%S'))
        os.makedirs(trash, exist_ok=True)
        return trash
    return None


def _du(p):
    t = 0
    for dp, _, fs in os.walk(p, followlinks=False):
        for fn in fs:
            fp = os.path.join(dp, fn)
            try:
                if not os.path.islink(fp):
                    t += os.path.getsize(fp)
            except OSError:
                pass
    return t


def _report(base):
    print('\n裁剪后:')
    print(f'  目标目录: {_du(base) / 1024 / 1024:.1f}MB')
    if os.environ.get('DRY_RUN'):
        print('  [DRY_RUN] 以上仅为将删除清单，未实际删除任何文件')


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    internal = os.path.join(here, 'dist', 'Super_ADB', '_internal')
    app_contents = os.path.join(here, 'dist', 'Super_ADB.app', 'Contents')
    if os.path.isdir(internal):
        if IS_LINUX:
            _trim_linux(internal)
        else:
            _trim_windows(internal)
    elif os.path.isdir(app_contents):
        _trim_mac(app_contents)
    else:
        print('未找到构建产物 dist/Super_ADB/_internal 或 dist/Super_ADB.app（先跑 build_exe.py）')
        return


if __name__ == '__main__':
    main()
