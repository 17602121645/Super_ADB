# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— ADB 命令封装层
======================================
统一封装常用 adb 命令，所有耗时调用均通过 subprocess 在后台线程执行，
主线程只负责刷新 UI。
"""

import base64
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import sys

CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

# 延迟导入 ADB 协议客户端（避免循环导入）
def _获取协议客户端类():
    from tools.adb_protocol_client import Adb协议客户端
    return Adb协议客户端


# ----------------------------------------------------------------------
# 通用配置读写（UI 状态持久化）
# ----------------------------------------------------------------------
# 向后兼容：目录/文件重命名为英文前的旧名。老用户升级后仍能自动迁出旧设置，
# 因此这里必须保留中文字面量（下同）。
_LEGACY_CONFIG_DIR = '配置'
# 新文件名 → 历史用过的旧文件名（按新→旧顺序尝试，命中即一次性迁移）
_LEGACY_CONFIG_RENAMES = {
    'super_adb_config.json': ['Super_ADB配置.json', 'adb_shell_config.json'],
    'build_info.json': ['打包信息.json'],
}


def _config_path(name):
    """配置文件统一放 <base>/config/ 子目录，文件名 = basename(name)。

    自动迁移旧位置文件（首次访问新路径不存在时）覆盖以下情形：
      - base/<filename>      （旧 frozen 行为：直接散落在 exe 旁）
      - base/config/<filename> （新路径）；base/配置/<filename>（旧中文目录）
      - base/<name>          （调用方原始参数，含前缀）
      - 主配置特例：旧名 adb_shell_config.json → 新名 super_adb_config.json

    macOS 冻结版走 ~/Library/Application Support/Super_ADB/。
    """
    if sys.platform == 'darwin' and getattr(sys, 'frozen', False):
        base = os.path.expanduser('~/Library/Application Support/Super_ADB')
    elif getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        # 源码模式：本文件位于 Super_ADB_Win/工具/ 下，配置在项目根（上一级）
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    fname = os.path.basename(name)
    if not fname:
        return None
    new_dir = os.path.join(base, 'config')
    new_path = os.path.join(new_dir, fname)

    if not os.path.exists(new_path):
        candidates = [
            os.path.join(base, fname),                        # 旧 frozen 行为
            os.path.join(base, _LEGACY_CONFIG_DIR, fname),    # 旧中文目录 配置/
            os.path.join(base, name),                         # 调用方原 name（含前缀）
        ]
        for legacy_fname in _LEGACY_CONFIG_RENAMES.get(fname, ()):
            candidates.extend([
                os.path.join(base, legacy_fname),
                os.path.join(base, 'config', legacy_fname),
                os.path.join(base, _LEGACY_CONFIG_DIR, legacy_fname),
            ])
        new_abs = os.path.normcase(os.path.abspath(new_path))
        seen = set()
        for old in candidates:
            old_abs = os.path.normcase(os.path.abspath(old))
            if old_abs in seen or old_abs == new_abs:
                continue
            seen.add(old_abs)
            if os.path.isfile(old):
                try:
                    os.makedirs(new_dir, exist_ok=True)
                    os.replace(old, new_path)
                except OSError:
                    pass
                break

    os.makedirs(new_dir, exist_ok=True)
    return new_path


def 加载json配置(name):
    """读取配置，失败/缺失时返回空 dict，由调用方回退默认值。

    注意：配置既可能是 dict 也可能是 list（如设备指纹列表、历史记录），
    两者都原样返回；仅当文件不存在/损坏时才回退空 dict。
    """
    import logging
    path = _config_path(name)
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, (dict, list)):
            return data
        logging.getLogger(__name__).warning(
            '配置 %s 顶层类型异常，已回退空: %r', name, type(data).__name__)
        return {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # 文件损坏/权限异常：明确告警，不再静默返回 {} 让用户配置被无声清空
        logging.getLogger(__name__).warning(
            '读取配置 %s 失败（文件可能损坏），已回退空: %s', name, e)
        return {}


def 保存json配置(name, data):
    import logging
    try:
        with open(_config_path(name), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        # 统一走 logging，不再用裸 print
        logging.getLogger(__name__).warning('保存配置 %s 失败: %s', name, e)


def 格式化设备标签(d: dict) -> str:
    """设备下拉框条目显示文本（展示层，与业务逻辑分离）。"""
    return f"{d.get('model') or d.get('serial')}  [{d.get('serial')}]"


class AdbError(Exception):
    pass


def 只读分区引导(serial):
    """推送/写入遇只读分区时的解锁引导（按设备类型区分）。

    - 模拟器（emulator-*）：/system 只读由启动参数控制，正解是 -writable-system
      重启，disable-verity 流程无效，不引导。
    - 真机（userdebug 固件）：引导 disable-verity 流程；root/remount 按钮已内置
      自动执行该流程。
    """
    if serial and serial.startswith('emulator-'):
        return ('目标分区只读。模拟器的 /system 受 verified boot 保护，需以可写模式重启：\n'
                '   1) 关闭模拟器后执行: emulator -avd <AVD名称> -writable-system -no-snapshot\n'
                '   2) 重启完成后执行: adb root && adb remount，再重新推送')
    return ('目标分区只读。请先在「系统操作」执行 root/remount 解锁'
            '（真机会自动尝试 disable-verity 强开），或手动执行：\n'
            '   adb disable-verity && adb reboot && adb root && adb remount')


def 查找系统adb路径():
    """在系统 PATH 中查找 adb，排除项目自带的 adb 目录。

    用于「使用系统环境变量的 ADB」模式，确保用的是用户自己安装的 adb，
    而不是本工具内置的 adb。

    注意：只排除包含「外部扩展」的路径（项目特有目录名）。
    不能排除 platform-tools-latest-*，因为那是 Google 官方 platform-tools
    的标准解压目录名，用户自己下载的 adb 也常放在该目录下。

    Windows 下会额外从注册表读取最新的 PATH（用户刚修改环境变量时，
    当前进程的 os.environ 还是旧的，需要重新读取）。
    """
    import platform
    sysname = platform.system().lower()
    exe_name = 'adb.exe' if sysname == 'windows' else 'adb'

    # 项目自带 adb 一定在「外部扩展」目录下，只排除这个项目特有目录名
    内置关键词 = ['vendor']

    # 收集所有 PATH 目录（当前进程 + 注册表最新值）
    path_dirs = []
    # 1) 当前进程的 PATH
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if d and d not in path_dirs:
            path_dirs.append(d)
    # 2) Windows 下从注册表读取最新的 PATH（用户刚修改环境变量时）
    if sysname == 'windows':
        try:
            import winreg
            # 用户级 PATH
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment')
                user_path, _ = winreg.QueryValueEx(key, 'Path')
                winreg.CloseKey(key)
                for d in user_path.split(os.pathsep):
                    if d and d not in path_dirs:
                        path_dirs.append(d)
            except Exception:
                pass
            # 系统级 PATH
            try:
                key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Control\Session Manager\Environment')
                sys_path, _ = winreg.QueryValueEx(key, 'Path')
                winreg.CloseKey(key)
                for d in sys_path.split(os.pathsep):
                    if d and d not in path_dirs:
                        path_dirs.append(d)
            except Exception:
                pass
        except Exception:
            pass

    for d in path_dirs:
        if not d:
            continue
        # 排除项目自带的 adb 目录
        if any(kw in d for kw in 内置关键词):
            continue
        full = os.path.join(d, exe_name)
        if os.path.isfile(full):
            return os.path.abspath(full)
    return None


def 查找内置adb路径():
    """按当前操作系统探测本工具内置 adb 的绝对路径，找不到返回 None。

    跨平台子目录约定（与「vendor/adb/」下三个目录一致）：

    - **Windows**： ``platform-tools-latest-windows/platform-tools/adb.exe``
    - **macOS**：   ``platform-tools-latest-darwin/platform-tools/adb``
    - **Linux**：   ``platform-tools-latest-linux/platform-tools/adb``

    路径回退（与 ``find_scrcpy_dir`` 同款）：源码模式基目录 → 父目录 → 当前工作目录，
    兼容 ``Super_ADB_Win/vendor/...`` 与 ``_internal/vendor/...``（冻结模式）两种布局。
    """
    import platform
    sysname = platform.system().lower()
    if sysname == 'windows':
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-windows',
                              'platform-tools', 'adb.exe')
    elif sysname == 'darwin':
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-darwin',
                              'platform-tools', 'adb')
    else:
        suffix = os.path.join('vendor', 'adb', 'platform-tools-latest-linux',
                              'platform-tools', 'adb')

    here = os.path.dirname(os.path.abspath(__file__))
    candidates_root = [
        os.path.dirname(here),  # Super_ADB_Win/（源码模式）
        here,                   # _internal/工具/（冻结模式）
        os.getcwd(),
    ]
    for root in candidates_root:
        full = os.path.join(root, suffix)
        if os.path.isfile(full):
            return os.path.abspath(full)

    # 回退：scrcpy 发行包自带的官方 adb（外部扩展/scrcpy/.../adb.exe / adb）。
    # 支持「外部扩展/scrcpy-win64-vX.Y」与「外部扩展/scrcpy/scrcpy-win64-vX.Y」两种布局，
    # 按版本号降序取最新。这样可删除 外部扩展/adb 目录，统一使用 scrcpy 里的官方 adb。
    try:
        scrcpy_dirs = Adb设备操作.查找scrcpy目录()
        # 兼容两种返回形态：字符串（单个路径）/ 列表（多版本）
        if isinstance(scrcpy_dirs, str):
            scrcpy_dirs = [scrcpy_dirs]
        for scrcpy_dir in scrcpy_dirs or []:
            cand = os.path.join(scrcpy_dir,
                                'adb.exe' if sys.platform == 'win32' else 'adb')
            if os.path.isfile(cand):
                return os.path.abspath(cand)
    except Exception:
        pass
    return None


class AdbHelper:
    """ADB 命令辅助类：提供设备扫描、命令执行、常用信息获取等能力。

    关键设计：自研 ADB 客户端缓存为**类级别**（进程共享），
    解决 TCPDump/Monkey/性能监控/日志查看器/局域网扫描等窗口各自
    `AdbHelper()` new 独立实例 → 各自 `_自研adb缓存` 为空 →
    重复建连 + 重复 AUTH 授权弹窗 的问题。
    （历史上一次打开抓包或设备信息会触发 3~4 次完整 TOKEN+签名流程。）
    """

    # ── 类级：自研adb 连接缓存（所有 AdbHelper / Adb设备操作 实例共享）──
    # 同 serial 永远只做一次 AUTH 建连，new 再多实例也直接复用。
    _类级_自研adb缓存: dict = {}       # serial -> 自研adb客户端（TCP）
    _类级_自研adb_usb缓存: dict = {}   # serial -> UsbAdbConnection（USB）
    _类级_重连锁: dict = {}            # serial -> threading.Lock，执行shell失败重连时防多线程雪崩
    _类级_自研adb锁 = None             # 在首次使用前按需创建（避免 import 时引入 threading 副作用）
    _最近断开的设备: dict = {}          # serial -> 断开时间戳，10秒冷却期内不显示在设备列表中
    # 缓存回写日志回调：首个缓存成功的实例会保存 log_callback，
    # 后续其它实例首次调用时若 client 已就绪，同步把新回调写入 client
    # （确保每个对话框窗口里的输出面板也能看到日志）。

    def __init__(self, adb_path=None, log_callback=None):
        # 探测链：显式传入值 > shutil.which('adb') > 内置 adb > 'adb' 兜底
        #
        # 解决 Windows PATH 进程缓存陷阱：
        # 环境配置弹窗通过 winreg 写注册表 PATH 后，WM_SETTINGCHANGE 只通知
        # explorer，不会反向写回当前 Python 进程的 os.environ；导致即便注册表
        # 已更新，当前进程的 shutil.which('adb') 仍用旧 PATH 找不到 adb。
        # 解法有两层：
        #   1) 环境配置弹窗的 _add_to_windows_path 写完注册表后同步 os.environ
        #      + SetEnvironmentVariableW（让当前进程立即生效）
        #   2) 本 __init__ 不再硬编码 'adb'，而是主动探测：先 shutil.which('adb')
        #      （PATH 已配置的情况），再回退到 查找内置adb路径()（本工具自
        #      带的 platform-tools），最后才兜底 'adb'（交给系统 FileNotFoundError
        #      让上层提示用户配置环境）。
        if adb_path and adb_path != 'adb':
            self.adb_path = adb_path
        else:
            probed = shutil.which('adb')
            if not probed:
                probed = 查找内置adb路径()
            self.adb_path = probed or 'adb'
        # 命令日志回调：每次执行前输出完整命令，便于排查命令错误
        self.log_callback = log_callback
        # 纯 Python ADB 协议客户端（懒加载，替代 subprocess）
        self._协议客户端 = None
        # 自研 ADB 客户端（懒加载，直连设备 5555，不依赖 adb server）
        # ── 兼容旧代码：self._自研adb缓存 / _自研adb锁 现在**指向类级共享字典** ──
        # 这样 ADB工具.py 里原有所有 self._自研adb缓存 / self._自研adb锁 访问
        # （含 setdefault/pop/clear/with lock）无需逐个重写，也能跨实例共享。
        # 类级锁懒初始化：避免 import 时就创建 threading.Lock（某些打包环境有副作用）。
        if AdbHelper._类级_自研adb锁 is None:
            import threading as _th
            try:
                # 简单 CAS：多实例并发 __init__ 时只写入一把锁即可
                AdbHelper._类级_自研adb锁 = _th.Lock()
            except Exception:
                pass
        # 引用赋给实例属性：既有代码 `with self._自研adb锁:` /
        # `if serial in self._自研adb缓存:` 语法保持 100% 兼容。
        self._自研adb缓存 = AdbHelper._类级_自研adb缓存
        self._自研adb_usb缓存 = AdbHelper._类级_自研adb_usb缓存
        self._自研adb锁 = AdbHelper._类级_自研adb锁
        # 读取 ADB 配置（环境配置对话框保存到 配置/Super_ADB配置.json）
        try:
            cfg = 加载json配置('config/super_adb_config.json')
            adb_cfg = cfg.get('adb', {}) if isinstance(cfg, dict) else {}
            self._用协议客户端 = adb_cfg.get('socket_direct', False)
            self._用自研adb = adb_cfg.get('self_built', False)
            self._用系统adb = adb_cfg.get('system_adb', False)
        except Exception as e:
            # 配置加载失败（如干净打包后未包含 配置/ 目录）：默认自研 adb，
            # 避免静默回退到官方 adb 导致用户困惑。
            import logging as _lg
            _lg.getLogger(__name__).warning(
                'ADB 配置加载失败（%s），默认使用自研 adb。请确认 config/super_adb_config.json 存在。', e)
            self._用协议客户端 = False
            self._用自研adb = True
            self._用系统adb = False

        # 如果勾选了使用系统环境变量的 adb，强制用 PATH 中的 adb（排除项目自带的）
        # 即使没找到也用 'adb'（让系统去 PATH 中找），绝不回退到内置 adb
        if self._用系统adb:
            self.adb_path = 查找系统adb路径() or 'adb'

        # 自研 ADB 模式：不依赖官方 adb server，启动时后台清理残留的 adb 进程，
        # 避免之前其他模式留下的 adb server 继续占用 5037 端口或消耗资源。
        if self._用自研adb:
            self._清理残留adb()

    def _清理残留adb(self):
        """后台清理残留的官方 adb server 进程（自研模式独占盒子单槽位时释放连接）。

        单客户端盒子（IPTV 机顶盒等）adbd 只允许 1 个 TCP 连接：官方 adb server
        若还占着槽位，自研直连必然超时。进入自研模式时杀掉官方 server，把槽位让给
        自研客户端。Windows 杀 adb.exe，mac/linux 用 pkill -f adb。后台执行。
        """
        import threading
        def _kill():
            import subprocess
            import platform
            try:
                if platform.system().lower() == 'windows':
                    subprocess.run(
                        ['taskkill', '/F', '/IM', 'adb.exe', '/T'],
                        capture_output=True, timeout=5,
                        creationflags=CREATE_NO_WINDOW,
                    )
                else:
                    subprocess.run(['pkill', '-f', 'adb'], capture_output=True, timeout=5)
            except Exception:
                pass
        threading.Thread(target=_kill, daemon=True).start()

    def _诊断非自研不可用(self, serial, err):
        """官网/socket 路径命令失败时诊断：查官方 adb devices，若目标设备离线、
        未连接等（单客户端盒子唯一槽位被占用时多见此状），改写为可操作提示。

        返回改写后的错误消息字符串。
        """
        if not serial:
            return err
        low = str(err).lower()
        if not any(k in low for k in (
                'offline', 'timed out', 'timeout', '超时', 'cannot connect',
                'connection', 'not found', '未找到', '离线', 'closed', '断开')):
            return err
        target = serial
        try:
            import subprocess as _sp
            run_kwargs = dict(capture_output=True, text=True, timeout=3)
            if os.name == 'nt':
                run_kwargs['creationflags'] = 0x08000000
            r = _sp.run([self.adb_path, 'devices'], **run_kwargs)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == target:
                    st = parts[1]
                    if st == 'offline':
                        return (f'设备 {target} 在官方 adb server 侧为 offline：'
                                f'单客户端盒子唯一槽位可能被占用，请先执行 '
                                f'adb disconnect {target}，并确认无自研直连残留后重试')
                    if st in ('unauthorized', 'authorizing'):
                        return (f'设备 {target} 在官方 adb server 侧为 {st}（未授权）：'
                                f'请在设备弹窗上确认授权后重试')
                    return f'设备 {target} 官方 adb 状态为 {st}，操作失败: {err}'
            return (f'设备 {target} 未出现在官方 adb devices 中（server 未能连接）：'
                    f'可能被其他连接占用唯一槽位，请先 adb connect {target} '
                    f'或断开其他占用后重试')
        except Exception:
            return err

    def 刷新设置(self):
        """重新从 JSON 配置读取 ADB 设置，重置协议客户端和自研adb缓存。

        环境配置对话框中切换开关后调用，让已创建的 AdbHelper 实例立即生效，无需重启程序。
        """
        try:
            cfg = 加载json配置('config/super_adb_config.json')
            adb_cfg = cfg.get('adb', {}) if isinstance(cfg, dict) else {}
            self._用协议客户端 = adb_cfg.get('socket_direct', False)
            self._用自研adb = adb_cfg.get('self_built', False)
            self._用系统adb = adb_cfg.get('system_adb', False)
        except Exception as e:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                '刷新设置时配置加载失败（%s），默认使用自研 adb。', e)
            self._用协议客户端 = False
            self._用自研adb = True
            self._用系统adb = False

        # 如果勾选了使用系统环境变量的 adb，强制用 PATH 中的 adb（排除项目自带的）
        # 即使没找到也用 'adb'（让系统去 PATH 中找），绝不回退到内置 adb
        if self._用系统adb:
            self.adb_path = 查找系统adb路径() or 'adb'

        # ★ 切到自研模式时，清理残留官方 adb server：单客户端盒子（IPTV 机顶盒）
        # 只允许 1 个 TCP 连接，官方 server 还占着槽位会让自研直连一直超时
        # （被误报为"连接失败"）。__init__ 已做，这里补上运行时切换的场景。
        if self._用自研adb:
            self._清理残留adb()

        # 重置协议客户端和自研adb缓存，下次访问时按新设置重建
        self._协议客户端 = None
        # ── 类级共享缓存：必须用 .clear() 清空内容，不能赋新 dict ──
        # 赋新 dict 会让当前实例的 self._自研adb缓存 脱离类级共享，
        # 导致其它实例（TCPDump/Monkey/日志页等）仍持旧共享 dict，
        # 设置变更无法同步。.clear() 原地清空，所有实例同步生效。
        # 加锁：清缓存前先关闭所有已连接 client，避免与并发建连产生悬垂引用。
        with self._自研adb锁:
            try:
                for _client in list(self._自研adb缓存.values()):
                    try:
                        _client.关闭()
                    except Exception:
                        pass
                for _usb in list(self._自研adb_usb缓存.values()):
                    try:
                        _usb.关闭()
                    except Exception:
                        pass
            finally:
                self._自研adb缓存.clear()
                self._自研adb_usb缓存.clear()

    def _cmd_str(self, cmd_list):
        """把命令列表拼成 shell 字符串（含空格的路径自动加引号）。"""
        parts = []
        for p in cmd_list:
            p = str(p)
            if ' ' in p or '\t' in p:
                p = f'"{p}"'
            parts.append(p)
        return ' '.join(parts)

    @property
    def 协议客户端(self):
        """懒加载纯 Python ADB 协议客户端。"""
        if self._协议客户端 is None:
            try:
                cls = _获取协议客户端类()
                self._协议客户端 = cls(adb_path=self.adb_path, 自动启动server=True)
            except Exception as e:
                print(f'[ADB] 协议客户端初始化失败，回退 subprocess: {e}')
                self._用协议客户端 = False
        return self._协议客户端

    def _获取自研adb(self, serial):
        """懒加载自研 ADB 客户端，按 serial 缓存。

        serial 格式:
          - 192.168.1.100:5555 → 直接解析 IP 和端口
          - 其他格式 → 返回 None（不支持，回退 subprocess）

        缓存现为**类级共享**：TCPDump/Monkey/性能监控/局域网扫描等各自 new
        的 AdbHelper 实例，也能命中主窗口已建立的 client，避免重复 AUTH。
        """
        if not serial:
            return None
        # 快速路径：USB 缓存优先（USB 设备 serial 不含冒号）
        if serial in self._自研adb_usb缓存:
            _usb_client = self._自研adb_usb缓存[serial]
            if _usb_client is not None and _usb_client.state == 2:  # STATE_DEVICE
                return _usb_client
        # 快速路径：已缓存直接返回（并把当前实例的 log_callback 写回 client，
        # 这样 TCPDump 对话框/局域网扫描弹窗 内部的日志面板也能收到 client 日志）
        if serial in self._自研adb缓存:
            client = self._自研adb缓存[serial]
            if client is not None and self.log_callback is not None:
                # 只有当 client 当前没设置回调，或设置的就是当前回调，才不覆盖；
                # 否则把当前实例的回调一起附加上 —— 简单合并为最新回调即可。
                # （client.log_callback 为 1 对 1，不需要多播；保持简单。）
                try:
                    if client.log_callback is None:
                        client.log_callback = self.log_callback
                except Exception:
                    pass
            return client
        # 加锁：确保同一设备只有一个线程做首次连接（避免多次授权弹窗）
        with self._自研adb锁:
            # 双重检查：USB 缓存
            if serial in self._自研adb_usb缓存:
                _usb_client = self._自研adb_usb缓存[serial]
                if _usb_client is not None and _usb_client.state == 2:
                    return _usb_client
            # 双重检查：锁内再查一次缓存
            if serial in self._自研adb缓存:
                client = self._自研adb缓存[serial]
                if client is not None and self.log_callback is not None:
                    try:
                        if client.log_callback is None:
                            client.log_callback = self.log_callback
                    except Exception:
                        pass
                return client
            # 解析 IP:port
            if ':' in serial:
                parts = serial.split(':')
                host = parts[0]
                port = int(parts[1]) if len(parts) > 1 else 5555
                try:
                    from tools.adb_native import 自研adb客户端
                    print(f'[自研adb] 尝试连接 {host}:{port}...')
                    client = 自研adb客户端(host, port)
                    client.log_callback = self.log_callback
                    ok = client.连接()
                    if ok:
                        print(f'[自研adb] 连接成功，状态={client._conn.state if client._conn else "None"}')
                        self._自研adb缓存[serial] = client
                        # 建连成功即清除「最近断开」标记：所有建连路径的必经之地，
                        # 确保设备不会被 _最近断开的设备 过滤而导致下拉框为空。
                        try:
                            AdbHelper._最近断开的设备.pop(serial, None)
                        except Exception:
                            pass
                        return client
                    else:
                        _err = getattr(client, '最后错误', '') or '未知原因'
                        print(f'[自研adb] 连接失败，状态={client._conn.state if client._conn else "None"}，原因: {_err}')
                except Exception as e:
                    import traceback
                    print(f'[自研adb] 连接异常 {serial}: {e}')
                    traceback.print_exc()
            else:
                # 不含冒号 → 尝试 USB 设备连接
                try:
                    from tools.adb_native.usb_connection import UsbAdbConnection, 枚举adb设备
                    # 先枚举确认设备存在
                    usb_devs = 枚举adb设备()
                    target = None
                    for _d in usb_devs:
                        if _d.标识 == serial:
                            target = _d
                            break
                    if target is None:
                        print(f'[自研adb] USB 设备未找到: {serial}')
                        return None
                    print(f'[自研adb] 尝试 USB 连接 {serial}...')
                    usb_conn = UsbAdbConnection(target, timeout=10.0)
                    usb_conn.log_callback = self.log_callback
                    ok = usb_conn.连接()
                    if ok:
                        print(f'[自研adb] USB 连接成功，状态={usb_conn.state}')
                        self._自研adb_usb缓存[serial] = usb_conn
                        try:
                            AdbHelper._最近断开的设备.pop(serial, None)
                        except Exception:
                            pass
                        return usb_conn
                    else:
                        print(f'[自研adb] USB 连接失败，状态={usb_conn.state}')
                        usb_conn.关闭()
                except Exception as e:
                    import traceback
                    print(f'[自研adb] USB 连接异常 {serial}: {e}')
                    traceback.print_exc()
        return None

    def _解析serial为ip(self, serial):
        """从 serial 解析 IP 地址，用于自研adb。"""
        if ':' in serial:
            return serial.split(':')[0]
        return None

    def _run(self, cmd_list, timeout=30, shell=False):
        """执行 adb 命令，返回 CompletedProcess；出错时抛出 AdbError。

        采用整条命令字符串 + shell=True 方式执行（与 migu 项目一致），
        保证 shell 命令中的管道、重定向等能被正确解析。
        """
        # 自研 ADB 模式下禁止任何 subprocess 调用，从根源防止启动官方 adb server
        if self._用自研adb:
            raise AdbError(f'自研adb模式禁止调用官方adb: {" ".join(str(c) for c in cmd_list)}')
        cmd_str = self._cmd_str(cmd_list)
        if self.log_callback:
            try:
                self.log_callback(f'$ {cmd_str}')
            except Exception:
                pass
        try:
            result = subprocess.run(
                cmd_str,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                shell=True,
            )
            return result
        except subprocess.TimeoutExpired:
            raise AdbError(f"命令执行超时: {cmd_str}")
        except FileNotFoundError:
            raise AdbError(f"未找到 adb 命令: {self.adb_path}")
        except Exception as e:
            raise AdbError(f"命令执行异常: {e}")

    def 检查adb(self):
        # 自研 ADB 模式：不依赖官方 adb，直接返回可用
        if self._用自研adb:
            return True
        try:
            r = self._run([self.adb_path, 'version'], timeout=10)
            return r.returncode == 0
        except Exception:
            return False

    def 获取设备列表(self):
        """返回设备列表 [{'serial': ..., 'model': ..., 'state': ...}, ...]

        优先级: 自研adb(局域网扫描 + 已连接缓存) > Socket直连(host:devices-l) > subprocess
        """
        # 自研 ADB 模式：USB 枚举 + 局域网扫描 + 已连接缓存
        if self._用自研adb:
            try:
                from tools.adb_native import 自研adb客户端, 获取已连接设备
                devices = []
                seen = set()
                # 0) USB 设备枚举（pyusb 不可用时静默跳过）
                try:
                    from tools.adb_native.usb_connection import 枚举adb设备 as _枚举usb
                    from tools.adb_native.usb_transport import _native_win as _nw, _pyusb as _pu, _native_error as _ne, _pyusb_error as _pe
                    if self.log_callback:
                        try:
                            self.log_callback(f'[自研adb] USB枚举诊断: native={_nw is not None}(err={_ne}), pyusb={_pu}(err={_pe})')
                        except Exception:
                            pass
                    usb_devs = _枚举usb()
                    if self.log_callback:
                        try:
                            self.log_callback(f'[自研adb] USB枚举完成: 找到 {len(usb_devs)} 个设备')
                            # 输出 pyusb 详细诊断
                            from tools.adb_native.usb_transport import _枚举诊断
                            for _diag in _枚举诊断:
                                self.log_callback(f'[自研adb][USB诊断] {_diag}')
                        except Exception:
                            pass
                    for _d in usb_devs:
                        _serial = _d.标识
                        if _serial and _serial not in seen:
                            seen.add(_serial)
                            _model = _d.product or _d.manufacturer or ''
                            devices.append({'serial': _serial, 'model': _model, 'state': 'device'})
                            if self.log_callback:
                                try:
                                    self.log_callback(f'[自研adb] USB 设备: {_serial} {_model}')
                                except Exception:
                                    pass
                except Exception as _e:
                    if self.log_callback:
                        try:
                            self.log_callback(f'[自研adb] USB 枚举跳过: {_e}')
                        except Exception:
                            pass
                # 1) 局域网扫描
                if self.log_callback:
                    try:
                        self.log_callback('$ 局域网扫描 ADB 设备 [自研adb]')
                    except Exception:
                        pass
                found = 自研adb客户端.扫描设备(timeout=0.5)
                for d in found:
                    serial = f'{d["ip"]}:{d["port"]}'
                    if serial not in seen:
                        seen.add(serial)
                        devices.append({'serial': serial, 'model': '', 'state': 'device'})
                # 2) 连接池中已连接的设备（可能扫描超时没扫到，但已认证连接）
                try:
                    for host, port in 获取已连接设备():
                        serial = f'{host}:{port}'
                        if serial not in seen:
                            seen.add(serial)
                            devices.append({'serial': serial, 'model': '', 'state': 'device'})
                            if self.log_callback:
                                try:
                                    self.log_callback(f'[自研adb] 从连接池恢复设备: {serial}')
                                except Exception:
                                    pass
                except Exception:
                    pass
                # 3) ADB工具缓存中已连接的自研adb客户端（主连接从池剥离，不在池中）
                try:
                    for serial, client in getattr(self, '_自研adb缓存', {}).items():
                        if serial not in seen:
                            # 确认连接仍然有效
                            try:
                                if client._主连接 and client._主连接.state == 2:  # STATE_DEVICE
                                    seen.add(serial)
                                    devices.append({'serial': serial, 'model': '', 'state': 'device'})
                                    if self.log_callback:
                                        try:
                                            self.log_callback(f'[自研adb] 从缓存恢复设备: {serial}')
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                except Exception:
                    pass
                # 过滤掉最近10秒内主动断开的设备（避免刚断开又被局域网扫描扫回来）
                try:
                    import time as _time
                    _now = _time.time()
                    _expired = [_s for _s, _t in AdbHelper._最近断开的设备.items() if _now - _t > 10]
                    for _s in _expired:
                        AdbHelper._最近断开的设备.pop(_s, None)
                    if AdbHelper._最近断开的设备:
                        devices = [_d for _d in devices if _d.get('serial') not in AdbHelper._最近断开的设备]
                except Exception:
                    pass
                return devices
            except Exception as e:
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 自研adb扫描失败: {e}')
                    except Exception:
                        pass
                return []

        # Socket 直连模式：优先走协议客户端，避免与 subprocess 混用导致 adb server 重启
        if self._用协议客户端:
            try:
                raw = self.协议客户端.获取设备列表详细()
                return [
                    {
                        'serial': d.get('serial', ''),
                        'state': d.get('state', ''),
                        'model': d.get('model', ''),
                    }
                    for d in raw
                ]
            except Exception as e:
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 协议客户端获取设备列表失败，回退 subprocess: {e}')
                    except Exception:
                        pass
        # 回退 subprocess
        r = self._run([self.adb_path, 'devices', '-l'], timeout=10)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout or '获取设备列表失败')
        devices = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith('List of devices'):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial = parts[0]
            state = parts[1]
            model = ''
            for token in parts[2:]:
                if token.startswith('model:'):
                    model = token.split(':', 1)[1]
                    break
            devices.append({'serial': serial, 'model': model, 'state': state})
        return devices

    def 连接设备(self, ip, timeout=15):
        # 自研 ADB 模式：真正建立连接并缓存（否则设备列表刷新时看不到该设备）
        if self._用自研adb:
            if ':' not in ip:
                ip = f'{ip}:5555'
            if self.log_callback:
                try:
                    self.log_callback(f'$ adb connect {ip} [自研adb]')
                except Exception:
                    pass
            client = self._获取自研adb(ip)
            if client:
                return f'connected to {ip}'
            # 连接失败：优先取 _获取自研adb 保存的具体原因（如"等待授权超时"/"设备断开"）
            _detail = getattr(self, '_最后连接错误', '') or ''
            _err = ''
            try:
                import time as _t
                from tools.adb_native.adb_client import 自研adb客户端 as _cli
                _host, _, _port = ip.rpartition(':')
                _key = (_host, int(_port))
                with _cli._负缓存锁:
                    if _key in _cli._负缓存:
                        _sec = int(_t.time() - _cli._负缓存[_key])
                        _err = f'（{_sec}秒前失败，冷却{int(_cli._负缓存秒 - _sec)}秒）'
            except Exception:
                pass
            if _detail:
                return f'failed to connect to {ip}: {_detail}{_err}'
            return f'failed to connect to {ip}{_err}'
        if ':' not in ip:
            ip = f'{ip}:5555'
        r = self._run([self.adb_path, 'connect', ip], timeout=timeout)
        return r.stdout.strip() or r.stderr.strip()

    def 断开设备(self, serial=None):
        # 自研 ADB 模式：关闭并清除缓存的连接（TCP + USB）
        if self._用自研adb:
            import time as _time
            if serial:
                # 断开前先在设备端重启 adbd 服务，保证彻底断开（设备端主动关闭所有连接）
                if serial in self._自研adb缓存:
                    try:
                        self._自研adb缓存[serial].执行shell('setprop ctl.restart adbd', timeout=3)
                    except Exception:
                        pass
                # TCP 缓存
                if serial in self._自研adb缓存:
                    try:
                        client = self._自研adb缓存.pop(serial)
                        client.关闭()
                    except Exception:
                        pass
                # USB 缓存
                if serial in self._自研adb_usb缓存:
                    try:
                        usb_conn = self._自研adb_usb缓存.pop(serial)
                        usb_conn.关闭()
                    except Exception:
                        pass
                # 写入最近断开标记，10秒内刷新设备列表时过滤掉
                AdbHelper._最近断开的设备[serial] = _time.time()
            elif not serial:
                # 记录所有待断开的 serial（用于写入断开标记）
                _all_serials = list(self._自研adb缓存.keys())
                # 断开前先在设备端重启 adbd
                for _s in _all_serials:
                    try:
                        self._自研adb缓存[_s].执行shell('setprop ctl.restart adbd', timeout=3)
                    except Exception:
                        pass
                # 断开所有（TCP + USB）
                for client in self._自研adb缓存.values():
                    try:
                        client.关闭()
                    except Exception:
                        pass
                self._自研adb缓存.clear()
                for usb_conn in self._自研adb_usb缓存.values():
                    try:
                        usb_conn.关闭()
                    except Exception:
                        pass
                self._自研adb_usb缓存.clear()
                # 写入断开标记
                for _s in _all_serials:
                    AdbHelper._最近断开的设备[_s] = _time.time()
            if self.log_callback:
                try:
                    self.log_callback(f'$ adb disconnect {serial or ""} [自研adb]')
                except Exception:
                    pass
            return 'disconnected'
        cmd = [self.adb_path, 'disconnect']
        if serial:
            cmd.append(serial)
        r = self._run(cmd, timeout=10)
        return r.stdout.strip() or r.stderr.strip()

    def 配对设备(self, target, code, timeout=20):
        """执行 adb pair <target> <code>，返回 (ok, message)。

        target 形如 ip:port（手机「无线调试」配对弹窗里的地址）。
        成功判定同时兼容中英文回显（successfully paired / 配对成功）。
        """
        # 自研 ADB 模式：使用纯 Python 实现的配对客户端（SPAKE2 + AES-128-GCM）
        if self._用自研adb:
            if ':' not in target:
                raise AdbError("pair 目标需包含端口（格式 ip:port）")
            host, _, port_str = target.rpartition(':')
            try:
                port = int(port_str)
            except ValueError:
                raise AdbError(f'无效的端口: {port_str}')
            try:
                from tools.adb_native.pair_client import 配对设备
                def _pair_log(msg):
                    if self.log_callback:
                        try:
                            self.log_callback(msg)
                        except Exception:
                            pass
                ok, msg = 配对设备(host, port, code, timeout=timeout,
                                      log_callback=_pair_log)
                return ok, msg
            except ImportError as e:
                return False, f'自研配对模块加载失败: {e}'
            except Exception as e:
                return False, f'自研配对失败: {e}'
        if ':' not in target:
            raise AdbError("pair 目标需包含端口（格式 ip:port）")
        r = self._run([self.adb_path, 'pair', target, code], timeout=timeout)
        out = (r.stdout or '').strip()
        err = (r.stderr or '').strip()
        combined = out or err or '无返回'
        ok = r.returncode == 0 and (
            'successfully paired' in combined.lower()
            or '配对成功' in combined
            or 'successfully' in combined.lower()
        )
        return ok, combined

    def _log(self, text):
        """写一条日志到 log_callback（未设置或回调异常时静默）。

        后台线程调用也安全——主入口的日志回调内部走 QueuedConnection。
        """
        if self.log_callback:
            try:
                self.log_callback(text)
            except Exception:
                pass

    def 执行shell(self, serial, command, timeout=30):
        """执行 adb [-s serial] shell <command>，返回 stdout。"""
        # 优先用自研 ADB（直连设备，不依赖 adb server），失败不回退 subprocess
        if self._用自研adb and serial:
            client = self._获取自研adb(serial)
            if not client:
                raise AdbError(f'自研adb连接设备失败: {serial}')
            try:
                if self.log_callback:
                    try:
                        self.log_callback(f'$ adb -s {serial} shell {command} [自研adb]')
                    except Exception:
                        pass
                return client.执行shell(command, timeout=timeout)
            except Exception as e:
                # 连接可能断开，清除缓存重试一次
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 自研adb失败，清除缓存重连: {e}')
                    except Exception:
                        pass
                # ★ 防多线程雪崩：同一设备只有一个线程做重连，其他线程等待重连结果后复用新client。
                # 绝不能关闭 old_client——设备信息/性能监控等场景多线程并发执行shell，
                # 其他线程可能正在用同一个 old_client，强制关闭会导致它们全部失败又触发重连，形成死循环。
                # old_client 只从缓存移除，等引用它的线程用完后自然失效。
                if serial not in AdbHelper._类级_重连锁:
                    import threading as _th
                    try:
                        AdbHelper._类级_重连锁[serial] = _th.Lock()
                    except Exception:
                        pass
                rlock = AdbHelper._类级_重连锁.get(serial)
                if rlock is not None:
                    got = rlock.acquire(timeout=15)
                else:
                    got = True
                try:
                    # 双重检查：拿到锁后可能别的线程已经重连好了，直接复用
                    if serial in self._自研adb缓存:
                        client = self._自研adb缓存[serial]
                    else:
                        # 只从缓存移除，不关闭（其他线程可能在用）
                        self._自研adb缓存.pop(serial, None)
                        client = self._获取自研adb(serial)
                    if client:
                        try:
                            return client.执行shell(command, timeout=timeout)
                        except Exception as e2:
                            raise AdbError(f'自研adb执行失败: {e2}')
                    raise AdbError(f'自研adb执行失败: {e}')
                finally:
                    if rlock is not None and got:
                        try:
                            rlock.release()
                        except Exception:
                            pass
        # 其次用纯 Python 协议客户端
        if self._用协议客户端 and serial:
            try:
                if self.log_callback:
                    try:
                        self.log_callback(f'$ adb -s {serial} shell {command}')
                    except Exception:
                        pass
                return self.协议客户端.执行shell(serial, command, timeout=timeout)
            except Exception as e:
                # 协议客户端失败，回退 subprocess
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 协议客户端失败，回退 subprocess: {e}')
                    except Exception:
                        pass
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', command]
        r = self._run(cmd, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or '').strip()
            raise AdbError(self._诊断非自研不可用(serial, self._translate_error(err)))
        return r.stdout

    def 直接执行(self, serial, args, timeout=30):
        """执行 adb [-s serial] <args...>，返回 stdout。

        优先级: 自研adb > 协议客户端 > subprocess
        支持的命令:
          - shell <cmd>     → 执行shell
          - push <local> <remote> → 推送文件
          - pull <remote> <local> → 拉取文件
          - forward ...     → 端口转发
        其他命令回退到 subprocess。
        """
        # 优先用自研 ADB
        if self._用自研adb and serial and args:
            cmd = args[0] if args else ''
            client = self._获取自研adb(serial)
            if not client:
                raise AdbError(f'自研adb连接设备失败: {serial}')
            try:
                if cmd == 'shell' and len(args) >= 2:
                    shell_cmd = ' '.join(str(a) for a in args[1:])
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} shell {shell_cmd} [自研adb]')
                        except Exception:
                            pass
                    return client.执行shell(shell_cmd, timeout=timeout)
                elif cmd == 'push' and len(args) >= 3:
                    local, remote = args[1], args[2]
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} push {local} {remote} [自研adb]')
                        except Exception:
                            pass
                    ok = client.推送文件(local, remote, timeout=timeout)
                    if not ok:
                        raise AdbError(f'推送失败: {local} -> {remote}')
                    return ''
                elif cmd == 'pull' and len(args) >= 3:
                    remote, local = args[1], args[2]
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} pull {remote} {local} [自研adb]')
                        except Exception:
                            pass
                    client.拉取文件(remote, local, timeout=timeout)
                    return ''
                elif cmd == 'forward' and len(args) >= 3:
                    if args[1] == '--remove' and len(args) >= 3:
                        local_port = int(args[2].split(':')[1])
                        client.取消端口转发(local_port)
                        return ''
                    elif ':' in args[1] and ':' in args[2]:
                        local_port = int(args[1].split(':')[1])
                        remote = args[2]
                        client.端口转发(local_port, remote)
                        return ''
            except Exception as e:
                # 提取详细错误信息
                原始错误 = str(e)
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 自研adb失败: {原始错误}')
                    except Exception:
                        pass
                # 根据错误类型添加诊断提示
                诊断提示 = ''
                if '字节数不一致' in 原始错误 or '0B' in 原始错误:
                    诊断提示 = '（可能原因: 设备权限不足、/system 分区只读或空间已满）'
                elif '只读' in 原始错误 or 'read-only' in 原始错误.lower():
                    诊断提示 = '（目标分区为只读，需执行 adb root && adb remount）'
                elif '不存在' in 原始错误 or 'No such file' in 原始错误:
                    诊断提示 = '（目标目录或文件不存在）'
                elif 'Permission denied' in 原始错误:
                    诊断提示 = '（权限被拒绝，检查设备端权限设置）'
                elif '超时' in 原始错误 or 'timeout' in 原始错误.lower():
                    诊断提示 = '（操作超时，可能是设备连接不稳定或文件过大）'
                # 自研 ADB 模式下不回退 subprocess，避免启动 adb server
                raise AdbError(f'自研adb执行失败: {原始错误}{诊断提示}')

        # 其次用纯 Python 协议客户端
        if self._用协议客户端 and serial and args:
            cmd = args[0] if args else ''
            try:
                if cmd == 'shell' and len(args) >= 2:
                    shell_cmd = ' '.join(str(a) for a in args[1:])
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} shell {shell_cmd}')
                        except Exception:
                            pass
                    return self.协议客户端.执行shell(serial, shell_cmd, timeout=timeout)
                elif cmd == 'push' and len(args) >= 3:
                    local, remote = args[1], args[2]
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} push {local} {remote}')
                        except Exception:
                            pass
                    self.协议客户端.推送文件(serial, local, remote, timeout=timeout)
                    return ''
                elif cmd == 'pull' and len(args) >= 3:
                    remote, local = args[1], args[2]
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} pull {remote} {local}')
                        except Exception:
                            pass
                    self.协议客户端.拉取文件(serial, remote, local, timeout=timeout)
                    return ''
                elif cmd == 'forward' and len(args) >= 3:
                    # forward tcp:port remote
                    if args[1] == '--remove' and len(args) >= 3:
                        local_port = int(args[2].split(':')[1])
                        self.协议客户端.取消端口转发(serial, local_port)
                        return ''
                    elif ':' in args[1] and ':' in args[2]:
                        local_port = int(args[1].split(':')[1])
                        remote = args[2]
                        self.协议客户端.端口转发(serial, local_port, remote)
                        return ''
            except Exception as e:
                if self.log_callback:
                    try:
                        self.log_callback(f'[ADB] 协议客户端失败，回退 subprocess: {e}')
                    except Exception:
                        pass

        # 回退到 subprocess
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += args
        r = self._run(cmd, timeout=timeout)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or '').strip()
            raise AdbError(self._translate_error(err))
        return r.stdout

    def 执行批量脚本(self, serial, script, timeout=15):
        """安全地执行多行 shell 脚本。

        通过 base64 编码避开 Windows cmd.exe 嵌套引号 + 管道符拆 args 的坑。
        用 shell=False + 列表形式调用 subprocess, 整个命令作为 1 个字符串
        传给 adb, 命令内部的 | & < > 等都按字面量处理 (原 shell=True
        会被 cmd.exe 拆管道)。
        Android 自带 base64 (toybox/busybox), Android 7+ 标准支持。
        """
        encoded = base64.b64encode(script.encode('utf-8')).decode('ascii')
        cmd_str = f'echo {encoded} | base64 -d | sh'
        # 走执行shell（优先协议客户端，协议客户端不经 cmd.exe，无管道符拆分问题）
        return self.执行shell(serial, cmd_str, timeout=timeout)

    def _run_no_shell(self, cmd_list, timeout=30):
        """执行命令 (list 形式, 绕过 cmd.exe)。

        适用于参数中含 cmd.exe 特殊字符 (|, &, <, >) 的场景。
        """
        # 自研 ADB 模式下禁止任何 subprocess 调用，从根源防止启动官方 adb server
        if self._用自研adb:
            raise AdbError(f'自研adb模式禁止调用官方adb: {" ".join(str(c) for c in cmd_list)}')
        if self.log_callback:
            try:
                self.log_callback(f'$ {" ".join(str(p) for p in cmd_list)}')
            except Exception:
                pass
        try:
            return subprocess.run(
                cmd_list, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=timeout, creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            raise AdbError(f"命令执行超时: {cmd_list}")
        except FileNotFoundError:
            raise AdbError(f"未找到 adb 命令: {self.adb_path}")
        except Exception as e:
            raise AdbError(f"命令执行异常: {e}")

    def _translate_error(self, text):
        if not text:
            return ''
        low = text.lower()
        if 'permission denied' in low:
            return '权限不足（Permission denied），可能需要 root 权限'
        if 'no such file' in low:
            return '文件或目录不存在（No such file or directory）'
        if 'read-only file system' in low:
            return '只读文件系统，无法写入'
        if 'device not found' in low or 'no devices' in low:
            return '未找到设备，请检查连接'
        if 'device offline' in low:
            return '设备离线（offline）——单客户端盒子可能被其他连接占用唯一槽位，请先 adb disconnect 后重试'
        if 'cannot connect' in low or 'connection refused' in low or 'unable to connect' in low:
            return '无法连接设备（被占用或未监听 adb 端口），请检查设备地址/端口后重试'
        if 'more than one device' in low:
            return '连接了多个设备，请在下拉框中选择具体设备'
        return text.strip()

    def 流式推送(self, serial, local_path, remote_path, progress_cb=None):
        """推送文件到设备，实时回调进度。

        与 AdbFileManager.push() 不同，本方法流式读取 adb push 输出并解析进度
        （兼容老版本 `[ 25%]` 与新版本 `(bytes in ...)` 两种回显），
        通过 progress_cb(sent:int, total:int, elapsed:float) 实时上报；
        不传则静默推送。
        复用 AdbHelper 的 adb 路径与 CREATE_NO_WINDOW；失败抛 AdbError。
        """
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['push', local_path, remote_path]
        if self.log_callback:
            try:
                self.log_callback('$ ' + ' '.join(str(p) for p in cmd))
            except Exception:
                pass
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding='utf-8', errors='replace',
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError:
            raise AdbError(f'未找到 adb 命令: {self.adb_path}')
        out_lines = []
        try:
            size = 0
            try:
                size = os.path.getsize(local_path)
            except OSError:
                pass
            t0 = time.time()
            if proc.stdout:
                for line in proc.stdout:
                    line = line.rstrip('\n')
                    if not line.strip():
                        continue
                    out_lines.append(line)
                    if self.log_callback:
                        try:
                            self.log_callback(line)
                        except Exception:
                            pass
                    if progress_cb:
                        sent = None
                        m = re.search(r'\[\s*(\d+)%\]', line)
                        if m:
                            pct = int(m.group(1))
                            sent = int(pct / 100 * size) if size else 0
                        else:
                            m2 = re.search(r'\((\d+)\s*bytes', line)
                            if m2:
                                sent = int(m2.group(1))
                        if sent is not None:
                            try:
                                progress_cb(sent, size, time.time() - t0)
                            except Exception:
                                pass
            proc.wait()
            # 确保结束时上报 100%（adb push 有时最后一行是其他提示）
            if progress_cb and proc.returncode == 0 and size:
                try:
                    progress_cb(size, size, time.time() - t0)
                except Exception:
                    pass
        except Exception as e:
            try:
                proc.kill()
                proc.wait()
            except Exception:
                pass
            raise AdbError(f'推送异常: {e}')
        if proc.returncode != 0:
            raise AdbError(self._push_fail_msg(serial, out_lines, proc.returncode))

    @staticmethod
    def _push_fail_msg(serial, out_lines, returncode):
        """组装 push 失败消息；检测到只读分区时自动附上解锁引导。"""
        msg = f'推送失败 (returncode={returncode})'
        tail = out_lines[-1].strip() if out_lines else ''
        low = '\n'.join(out_lines).lower()
        if 'read-only file system' in low or 'read-only filesystem' in low:
            msg += f'：{tail}' if tail else ''
            msg += f'\n{只读分区引导(serial)}'
        elif tail:
            msg += f'：{tail}'
        return msg


class Adb设备操作(AdbHelper):
    """面向设备操作的封装，所有方法均接受 serial 参数。"""

    # OAID/AAID 标准 UUID 格式
    UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')

    def 获取oaid(self, serial):
        """尝试通过多种厂商内容提供者/Settings 获取 OAID/AAID。

        小米 / 华为 / OPPO / vivo / Google 等各家路径不同,这里做集中回退。
        返回第一个匹配标准 UUID 的字符串; 获取失败返回空字符串。
        """
        script = '''OAID_RAW=""
# 小米 / MiTV 专用路径优先 (com.miui.idprovider 的 uniform_id)
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.idprovider/uniform_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.id.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure advertising_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.google.android.gms.id/id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.bun.miitmdid.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.mdid.msa.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.huawei.hwid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.heytap.openid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.coloros.mcs.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.vivo.vms.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure oaid 2>/dev/null)"
echo "$OAID_RAW"'''
        try:
            raw = self.执行批量脚本(serial, script, timeout=15)
            m = self.UUID_RE.search(raw or '')
            return m.group(0) if m else ''
        except Exception:
            return ''

    def 获取设备信息字典(self, serial):
        """一次性批量获取设备硬件/系统信息, 返回 dict。

        11 次独立 shell 调用 → 1 次批量调用 + 1 次 get-serialno, 节省 ~5s 延迟。
        新增: cpu_model (SoC 型号), gpu (GPU 信息)。
        脚本通过 base64 编码执行, 彻底避开 Windows cmd.exe 嵌套引号陷阱
        —— 否则 adb shell 内层的 $() 命令替换会被 cmd.exe 拆成多个 args,
        导致 $(getprop x.y) 被切成两半, 整个命令失效。

        返回的 dict 每个 value 都是字符串, 获取失败时为 None 或 '未知'。
        """
        # 脚本必须没有空格的命令替换 (命令替换会被 cmd.exe 拆 args),
        # 或通过 base64 编码传递。后者更通用, 任何脚本都能跑。
        script = '''echo "___ANDROID_RELEASE___:$(getprop ro.build.version.release)"
echo "___ANDROID_SDK___:$(getprop ro.build.version.sdk)"
echo "___ANDROID_ID___:$(getprop ro.build.id)"
echo "___SECURITY_PATCH___:$(getprop ro.build.version.security_patch)"
echo "___MODEL___:$(getprop ro.product.model)"
echo "___BRAND___:$(getprop ro.product.brand)"
echo "___MANUFACTURER___:$(getprop ro.product.manufacturer)"
echo "___DEVICE___:$(getprop ro.product.device)"
echo "___CPU_ABI___:$(getprop ro.product.cpu.abi)"
echo "___CPU_ABILIST___:$(getprop ro.product.cpu.abilist)"
echo "___CPU_CHIPNAME___:$(getprop ro.hardware.chipname)"
echo "___CPU_HARDWARE___:$(getprop ro.hardware)"
echo "___CPU_BOARD___:$(getprop ro.board.platform)"
echo "___CPU_SOC___:$(getprop ro.boot.soc_id)"
HW=$(grep -m1 "^Hardware" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | awk '{print $NF}')
echo "___CPU_PROC___:$HW"
GPU=$(dumpsys SurfaceFlinger 2>/dev/null | grep -m1 -i GLES | head -c 300)
echo "___GPU___:$GPU"
echo "___EGL___:$(getprop ro.hardware.egl)"
echo "___WM_SIZE___:$(wm size 2>/dev/null)"
echo "___WM_DENSITY___:$(wm density 2>/dev/null)"
# MAC 多路径回退获取(过滤 Android 10+ 占位符 02:00:00:00:00:00)
_get_mac() {
    for iface in wlan0 eth0 wlan1; do
        path="/sys/class/net/$iface/address"
        [ -r "$path" ] && cat "$path" 2>/dev/null && return
    done
    ip link show wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ip link show eth0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ifconfig wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    ifconfig eth0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1
    settings get secure wifi_mac_address 2>/dev/null
}
MAC=$(_get_mac | head -n1)
MAC=$(echo "$MAC" | tr '[:upper:]' '[:lower:]')
[ "$MAC" = "02:00:00:00:00:00" ] && MAC=""
[ -z "$MAC" ] && MAC="N/A"
echo "___MAC___:$MAC"
echo "___AID___:$(settings get secure android_id 2>/dev/null)"
# OAID/AAID 多厂商候选获取, 由 Python 端统一提取 UUID
OAID_RAW=""
# 小米 / MiTV 专用路径优先
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.idprovider/uniform_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.miui.id.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure advertising_id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.google.android.gms.id/id 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.bun.miitmdid.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.mdid.msa.provider/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.huawei.hwid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.heytap.openid.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.coloros.mcs.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(content query --uri content://com.vivo.vms.oaid/oaid 2>/dev/null)"
OAID_RAW="$OAID_RAW $(settings get secure oaid 2>/dev/null)"
echo "___OAID_RAW___:$OAID_RAW"
MEM=$(grep -m1 "^MemTotal" /proc/meminfo 2>/dev/null)
echo "___MEMTOTAL___:$MEM"
MEMAVAIL=$(grep -m1 "^MemAvailable" /proc/meminfo 2>/dev/null)
echo "___MEMAVAIL___:$MEMAVAIL"
echo "___END___"'''

        info = {}
        try:
            raw = self.执行批量脚本(serial, script, timeout=15)
            for line in (raw or '').splitlines():
                m = re.match(r'___([A-Z_]+)___:(.*)', line)
                if m:
                    info[m.group(1).lower()] = m.group(2).strip()
        except Exception as e:
            info['_error'] = f'批量命令失败: {e}'

        # 备选方案：批量脚本失败时，用 getprop 逐个获取基本信息
        if info.get('_error') or not info.get('model'):
            try:
                # 用 getprop <key> 逐个获取，比解析整个 getprop 输出更可靠
                def _getprop(key):
                    try:
                        v = self.执行shell(serial, f'getprop {key}', timeout=5).strip()
                        return v if v else ''
                    except Exception:
                        return ''
                fallback = {
                    'android_release': _getprop('ro.build.version.release'),
                    'android_sdk': _getprop('ro.build.version.sdk'),
                    'android_id': _getprop('ro.build.id'),
                    'security_patch': _getprop('ro.build.version.security_patch'),
                    'model': _getprop('ro.product.model'),
                    'brand': _getprop('ro.product.brand'),
                    'manufacturer': _getprop('ro.product.manufacturer'),
                    'device': _getprop('ro.product.device'),
                    'cpu_abi': _getprop('ro.product.cpu.abi'),
                    'cpu_abilist': _getprop('ro.product.cpu.abilist'),
                    'cpu_chipname': _getprop('ro.hardware.chipname'),
                    'cpu_hardware': _getprop('ro.hardware'),
                    'cpu_board': _getprop('ro.board.platform'),
                    'cpu_soc': _getprop('ro.boot.soc_id'),
                }
                # 只填充批量脚本没获取到的字段
                for k, v in fallback.items():
                    if not info.get(k) and v:
                        info[k] = v
                if info.get('_error') and info.get('model'):
                    del info['_error']  # getprop 成功获取到基本信息，清除错误标记
            except Exception as e2:
                if not info.get('_error'):
                    info['_error'] = f'getprop 也失败: {e2}'

        # 从 OAID 候选输出中提取标准 UUID 格式的 OAID/AAID
        oaid_raw = info.get('oaid_raw', '')
        if oaid_raw:
            m = self.UUID_RE.search(oaid_raw)
            if m:
                info['oaid'] = m.group(0)
        if 'oaid' not in info:
            info['oaid'] = ''

        # get-serialno 是 adb 级命令, 无法批量 (单独一次, 很快)
        try:
            serialno = self._run(
                [self.adb_path, '-s', serial, 'get-serialno'], timeout=5).stdout.strip()
        except Exception:
            serialno = ''
        info['serialno'] = serialno

        return info

    def 获取设备信息(self, serial):
        """获取设备信息并格式化为人类可读字符串。

        内部委托 get_device_info_dict 然后格式化 (避免重复 ADB 调用)。
        """
        info = self.获取设备信息字典(serial)

        def _v(key, default='未知'):
            val = info.get(key, default)
            return val if val else default

        ram_kb = ''
        m = re.search(r'MemTotal:\s*(\d+)', _v('memtotal', ''))
        if m:
            ram_kb = int(m.group(1))

        avail_kb = ''
        m2 = re.search(r'MemAvailable:\s*(\d+)', _v('memavail', ''))
        if m2:
            avail_kb = int(m2.group(1))

        if ram_kb:
            ram_total_gb = ram_kb / 1024 / 1024
            if avail_kb:
                used_kb = ram_kb - avail_kb
                used_gb = used_kb / 1024 / 1024
                pct = used_kb / ram_kb * 100
                ram_str = f'{ram_total_gb:.1f} GB / 已用 {used_gb:.1f} GB ({pct:.0f}%)'
            else:
                ram_str = f'{ram_total_gb:.1f} GB'
        else:
            ram_str = '未解析到 MemTotal'

        lines = [
            f'序列号: {_v("serialno")}',
            f'设备型号: {_v("model")}',
            f'厂商名称: {_v("brand")} ({_v("manufacturer")})',
            f'Android版本: {_v("android_release")} (SDK {_v("android_sdk")}, Build {_v("android_id")})',
            f'安全补丁: {_v("security_patch")}',
            f'CPU 架构: {_v("cpu_abi")} ({_v("cpu_abilist")})',
            f'CPU 型号: {_v("cpu_chipname") or _v("cpu_soc") or _v("cpu_proc") or _v("cpu_hardware") or _v("cpu_board")}',
            f'GPU: {_v("egl") or (_v("gpu")[:80] if _v("gpu") else "未知")}',
            f'屏幕分辨率: {_v("wm_size")}',
            f'屏幕密度: {_v("wm_density")}',
            f'运行内存(RAM): {ram_str}',
            f'MAC 地址: {_v("mac")}',
            f'OAID/AAID: {_v("oaid") if _v("oaid") else "未获取"}',
            f'Android ID: {_v("aid")}',
        ]
        return '\n'.join(lines)

    def 设置代理(self, serial, host_port):
        self.执行shell(serial, f'settings put global http_proxy {host_port}', timeout=5)
        return self.执行shell(serial, 'settings get global http_proxy', timeout=5).strip()

    def 清除代理(self, serial):
        self.执行shell(serial, 'settings put global http_proxy :0', timeout=5)
        return self.执行shell(serial, 'settings get global http_proxy', timeout=5).strip()

    def 重启设备(self, serial):
        self.执行shell(serial, 'reboot', timeout=5)
        return '已发送重启命令'

    def 获取root权限(self, serial):
        """获取 root 权限（兼容自研 adb / 官方 adb），成功后自动重连。

        自研 adb 模式：client.获取root() → sleep(2) → 自动重连()；
        官方 adb 模式：adb -s <serial> root。
        返回 (是否成功, 说明字符串)。root 会重启 adbd，调用方后续命令需等重连完成。
        """
        import time as _t
        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if not client:
                    return False, '自研adb连接失败'
                ok = client.获取root()
                if not ok:
                    return False, '设备不支持 root（非 userdebug 镜像）'
                _t.sleep(2)
                try:
                    client.自动重连()
                except Exception:
                    pass
                return True, 'root 已获取，adbd 重启完成'
            else:
                r = self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
                if r.returncode == 0:
                    return True, 'root 已获取'
                err = (r.stderr or r.stdout or '').strip() or f'返回码 {r.returncode}'
                return False, err
        except Exception as e:
            return False, str(e)

    def root并重新挂载(self, serial):
        """尝试把 system 分区设为 rw，返回每步详细报告字符串。

        新版 Android (10+, emulator 默认) 用 system-as-root，/system 是 / 的一部分,
        旧命令 ``mount -o rw,remount /system`` 会报 "/system not in /proc/mounts"。
        这里多策略: adb root -> adb remount -> 按 /proc/mounts 选择 remount 路径 ->
        写真实文件验证可写性。验证失败时自动分流:
        - 真机（userdebug 固件）: 自动执行 disable-verity -> reboot -> root -> remount
          再复验，实现一键强开;
        - 模拟器: disable-verity 流程无效，提示用 -writable-system 参数重启。
        每步独立捕获 AdbError, 永不抛到上层。"""
        lines = []

        # 1) adb root —— 没 root 后续都没戏, 直接结束
        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    ok = client.获取root()
                    if ok:
                        lines.append('① adb root：请求已发送（设备将重启adbd，需重新连接）')
                        # root 后设备会断开，需要重新连接
                        time.sleep(2)
                        try:
                            client.自动重连()
                        except Exception:
                            pass
                    else:
                        lines.append('① adb root：失败（设备不支持或非userdebug镜像）')
                        return '\n'.join(lines)
                else:
                    lines.append('① adb root：失败（自研adb连接失败）')
                    return '\n'.join(lines)
            else:
                r = self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
                if r.returncode == 0:
                    lines.append('① adb root：成功')
                else:
                    err = (r.stderr or r.stdout or '').strip() or f'返回码 {r.returncode}'
                    lines.append(f'① adb root：失败（{err}）')
                    # 可能不是 userdebug 镜像, 继续尝试也行, 但毫无意义, 直接告知用户
                    return '\n'.join(lines)
        except AdbError as e:
            lines.append(f'① adb root：异常（{e}）')
            return '\n'.join(lines)

        # 2) adb remount —— Android 内建 remount, system-as-root 走的就是这条
        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    # 自研adb用 shell 命令尝试 remount
                    try:
                        out = client.执行shell('remount', timeout=10)
                        lines.append(f'② adb remount：成功{(" — " + out.strip()) if out.strip() else ""}')
                    except Exception as e:
                        lines.append(f'② adb remount：失败（{e}）')
                else:
                    lines.append('② adb remount：失败（自研adb连接失败）')
            else:
                r = self._run([self.adb_path, '-s', serial, 'remount'], timeout=10)
                out = (r.stdout or r.stderr or '').strip()
                if r.returncode == 0:
                    lines.append(f'② adb remount：成功{(" — " + out) if out else ""}')
                else:
                    lines.append(f'② adb remount：返回码 {r.returncode}（{out or "失败"}）')
        except AdbError as e:
            lines.append(f'② adb remount：异常（{e}）')

        # 3) 探测 /system 是否独立挂载
        system_is_separate = False
        try:
            mounts = self.执行shell(serial, 'cat /proc/mounts', timeout=5)
            system_is_separate = bool(re.search(
                r'^[^ ]+ +/system ', mounts or '', re.MULTILINE))
        except AdbError:
            pass

        # 4) 按情况 remount
        if system_is_separate:
            lines.append('③ 检测：/system 是独立挂载点')
            try:
                self.执行shell(serial, 'mount -o rw,remount /system', timeout=10)
                lines.append('④ mount -o rw,remount /system：成功')
            except AdbError as e:
                lines.append(f'④ mount -o rw,remount /system：失败（{e}）')
        else:
            lines.append('③ 检测：/system 是根文件系统的一部分（system-as-root，跳过 /system）')
            try:
                self.执行shell(serial, 'mount -o rw,remount /', timeout=10)
                lines.append('④ mount -o rw,remount /：成功')
            except AdbError as e:
                lines.append(f'④ mount -o rw,remount /：失败（{e}；'
                             f'内核可能禁止 remount 根分区, 实际可写性看 ⑤）')

        # 5) 真实写入验证 —— 最可靠判据；失败则按设备类型自动强开
        probe = '/system/.super_adb_rw_probe'
        try:
            self.执行shell(
                serial, f'touch {probe} && rm {probe}', timeout=5)
            lines.append('⑤ 验证：可在 /system 写入 ✓')
            return '\n'.join(lines)
        except AdbError as e:
            lines.append(f'⑤ 验证：/system 仍只读（{e}）')

        # 模拟器：只读由启动参数控制，disable-verity 流程无意义，给出正解
        if serial and serial.startswith('emulator-'):
            lines.append('⑥ 提示：模拟器请以可写模式重启后再点本按钮：')
            lines.append('   emulator -avd <AVD名称> -writable-system -no-snapshot')
            lines.append('   重启完成后重新点击本按钮，即可完成 /system 解锁。')
            return '\n'.join(lines)

        # 真机（userdebug 固件）：自动执行 disable-verity -> reboot -> root -> remount
        lines.append('⑥ 真机检测到只读分区，自动尝试强开（disable-verity 流程）…')
        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    # 自研adb用 shell 命令尝试 disable-verity
                    try:
                        out = client.执行shell('avbctl disable-verity', timeout=15)
                        lines.append(f'   6-1 disable-verity：已发送{(" — " + out.strip()) if out.strip() else ""}')
                    except Exception as e:
                        lines.append(f'   6-1 disable-verity：失败（{e}）')
                        lines.append('   自研adb模式下不支持 disable-verity，无法自动强开。')
                        return '\n'.join(lines)
                else:
                    lines.append('   6-1 disable-verity：失败（自研adb连接失败）')
                    return '\n'.join(lines)
            else:
                r = self._run([self.adb_path, '-s', serial, 'disable-verity'], timeout=15)
                out = (r.stdout or r.stderr or '').strip()
                if r.returncode == 0:
                    lines.append(f'   6-1 adb disable-verity：成功{(" — " + out) if out else ""}')
                else:
                    detail = f'（{out}）' if out else f'（返回码 {r.returncode}）'
                    lines.append(f'   6-1 adb disable-verity：失败{detail}')
                    lines.append('   固件不支持关闭 verity（需 userdebug 版本），无法自动强开。')
                    return '\n'.join(lines)
        except AdbError as ex:
            lines.append(f'   6-1 adb disable-verity：异常（{ex}）')
            return '\n'.join(lines)

        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    try:
                        client.执行shell('reboot', timeout=5)
                    except Exception:
                        pass
                    lines.append('   6-2 adb reboot：已重启，等待设备重连…')
                    # 等待设备重连（自研adb模式）
                    time.sleep(5)
                    for _ in range(30):
                        try:
                            if client.自动重连():
                                break
                        except Exception:
                            pass
                        time.sleep(2)
                    lines.append('   6-3 设备已重连')
                else:
                    lines.append('   6-2/6-3 等待设备重连失败（自研adb连接失败）')
                    return '\n'.join(lines)
            else:
                self._run([self.adb_path, '-s', serial, 'reboot'], timeout=10)
                lines.append('   6-2 adb reboot：已重启，等待设备重连…')
                self._run([self.adb_path, '-s', serial, 'wait-for-device'], timeout=90)
                lines.append('   6-3 设备已重连')
        except AdbError as ex:
            lines.append(f'   6-2/6-3 等待设备重连失败（{ex}），请稍后手动执行: adb root && adb remount')
            return '\n'.join(lines)

        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    ok = client.获取root()
                    if ok:
                        lines.append('   6-4 adb root：请求已发送（adbd 重启中…）')
                        time.sleep(2)
                        try:
                            client.自动重连()
                        except Exception:
                            pass
                        lines.append('   6-5 adbd 重启完成，已重新连接')
                    else:
                        lines.append('   6-4 adb root：失败（设备不支持）')
                        return '\n'.join(lines)
                else:
                    lines.append('   6-4/6-5 adb root / 重连失败（自研adb连接失败）')
                    return '\n'.join(lines)
            else:
                r = self._run([self.adb_path, '-s', serial, 'root'], timeout=10)
                if r.returncode != 0:
                    err = (r.stderr or r.stdout or '').strip()
                    lines.append(f'   6-4 adb root：失败（{err or f"返回码 {r.returncode}"}）')
                    return '\n'.join(lines)
                lines.append('   6-4 adb root：成功（adbd 重启中…）')
                self._run([self.adb_path, '-s', serial, 'wait-for-device'], timeout=30)
                lines.append('   6-5 adbd 重启完成，已重新连接')
        except AdbError as ex:
            lines.append(f'   6-4/6-5 adb root / 重连失败（{ex}）')
            return '\n'.join(lines)

        try:
            if self._用自研adb and serial:
                client = self._获取自研adb(serial)
                if client:
                    try:
                        out = client.执行shell('remount', timeout=15)
                        lines.append(f'   6-6 adb remount：成功{(" — " + out.strip()) if out.strip() else ""}')
                    except Exception as e:
                        lines.append(f'   6-6 adb remount：失败（{e}）')
                else:
                    lines.append('   6-6 adb remount：失败（自研adb连接失败）')
            else:
                r = self._run([self.adb_path, '-s', serial, 'remount'], timeout=15)
                out = (r.stdout or r.stderr or '').strip()
                if r.returncode == 0:
                    lines.append(f'   6-6 adb remount：成功{(" — " + out) if out else ""}')
                else:
                    detail = f'（{out}）' if out else f'（返回码 {r.returncode}）'
                    lines.append(f'   6-6 adb remount：失败{detail}')
        except AdbError as ex:
            lines.append(f'   6-6 adb remount：异常（{ex}）')

        try:
            self.执行shell(serial, f'touch {probe} && rm {probe}', timeout=5)
            lines.append('⑦ 复验：/system 现已可写 ✓ 解锁成功！')
        except AdbError as ex:
            lines.append(f'⑦ 复验：/system 仍只读（{ex}）')
            lines.append('   强开未生效。若设备是 userdebug 固件，请手动核对：')
            lines.append('   adb disable-verity && adb reboot && adb root && adb remount')

        return '\n'.join(lines)

    def 截图(self, serial):
        timestamp = time.strftime('%Y%m%d%H%M%S')
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        remote = '/sdcard/adb_shell_screen.png'
        local = os.path.join(desktop, f'{timestamp}screen.png')
        self.执行shell(serial, f'screencap -p {remote}', timeout=15)
        # 拉取文件：自研ADB模式用自研拉取，否则用 subprocess
        if self._用自研adb and serial:
            client = self._获取自研adb(serial)
            if client:
                client.拉取文件(remote, local, timeout=30)
            else:
                raise AdbError('自研adb连接失败，无法拉取截图')
        else:
            r = self._run([self.adb_path, '-s', serial, 'pull', remote, local], timeout=30)
            if r.returncode != 0:
                raise AdbError(r.stderr or r.stdout)
        self.执行shell(serial, f'rm {remote}', timeout=5)
        return local

    def 录屏(self, serial, duration, stop_event):
        """录制屏幕；stop_event 为 threading.Event，调用 set() 可提前停止。"""
        # 自研 ADB 模式下 screenrecord 需持续流，暂不支持，禁止调用官方 adb
        if self._用自研adb:
            raise AdbError('自研adb模式暂不支持录屏（screenrecord 持续流）')
        timestamp = time.strftime('%Y%m%d%H%M%S')
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        remote = '/sdcard/adb_shell_record.mp4'
        local = os.path.join(desktop, f'{timestamp}record.mp4')
        cmd = [self.adb_path, '-s', serial, 'shell', 'screenrecord',
               '--time-limit', str(duration), '--size', '1280x720', remote]
        # 录屏输出无需捕获；用 DEVNULL 避免 stderr 管道缓冲 (64KB) 触发的死锁
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
        start = time.time()
        while proc.poll() is None:
            if stop_event.is_set():
                proc.terminate()
                break
            if time.time() - start > duration + 30:
                proc.terminate()
                break
            time.sleep(0.3)
        # wait 超时后补 kill 兜底，防止 terminate 不生效导致进程变僵尸
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        r = self._run([self.adb_path, '-s', serial, 'pull', remote, local], timeout=60)
        if r.returncode != 0:
            raise AdbError(r.stderr or r.stdout)
        self.执行shell(serial, f'rm {remote}', timeout=5)
        return local

    @staticmethod
    def 查找scrcpy目录():
        """探测项目 vendor/ 下匹配当前平台的最新 scrcpy 目录。

        返回 scrcpy 目录绝对路径；未找到时返回 None。
        支持两种目录布局:
          - vendor/scrcpy-win64-vX.Y/...
          - vendor/scrcpy/scrcpy-win64-vX.Y/...
        按目录名中的版本号降序取最新版本。
        """
        # 本文件位于 工具/ 下，外部扩展/ 在项目根（上一级）；冻结后 __file__
        # 位于 _internal/ 顶层（base 即项目根），故 base 与其上一级都探测
        base = os.path.dirname(os.path.abspath(__file__))
        parent = os.path.dirname(base)
        prefix_map = {'darwin': 'scrcpy-mac-', 'linux': 'scrcpy-linux-', 'win32': 'scrcpy-win64-'}
        prefix = prefix_map.get(sys.platform, 'scrcpy-win64-')
        candidates = []
        for root in (base, parent, os.getcwd()):
            data_dir = os.path.join(root, 'vendor')
            if not os.path.isdir(data_dir):
                continue
            for name in os.listdir(data_dir):
                full = os.path.join(data_dir, name)
                if not os.path.isdir(full):
                    continue
                if name.startswith(prefix):
                    candidates.append(full)
                else:
                    try:
                        for sub in os.listdir(full):
                            sub_full = os.path.join(full, sub)
                            if os.path.isdir(sub_full) and sub.startswith(prefix):
                                candidates.append(sub_full)
                    except (PermissionError, OSError):
                        pass
        if not candidates:
            return None

        def _ver_key(path):
            ver_str = os.path.basename(path)[len(prefix):]
            return [int(t) if t.isdigit() else 0 for t in re.split(r'[.\-]', ver_str)]

        candidates.sort(key=_ver_key, reverse=True)
        return candidates[0]

    def 投屏(self, serial, extra_args=None):
        """启动 scrcpy 投屏；优先使用 vendor/ 下匹配平台的最新版本 scrcpy 目录。

        参数从「投屏设置」对话框读取（官方 scrcpy 参数映射，默认=官方默认即不传参）。
        extra_args: 可选的额外命令行参数列表，追加在设置参数之后（可覆盖同名参数）。
        """
        # 从投屏设置读取官方 scrcpy 参数（默认全部=官方默认，即不传该参数）
        try:
            from dialogs.scrcpy_settings_dialog import load_scrcpy_settings, build_scrcpy_args
            args = build_scrcpy_args(load_scrcpy_settings())
        except Exception:
            args = []
        if extra_args:
            args += list(extra_args)

        is_win = sys.platform == 'win32'
        scrcpy_dir = self.查找scrcpy目录()

        if scrcpy_dir:
            exe_name = 'scrcpy.exe' if is_win else 'scrcpy'
            exe_path = os.path.join(scrcpy_dir, exe_name)
            if not os.path.isfile(exe_path):
                raise FileNotFoundError(
                    f'在 {scrcpy_dir} 下未找到 {exe_name}，\n'
                    '请确认下载的是 scrcpy release 包（含 scrcpy.exe 和 scrcpy-server）。'
                )
            cmd = [exe_path, '-s', serial] + args
            cwd = scrcpy_dir
        else:
            exe_name = 'scrcpy.exe' if is_win else 'scrcpy'
            # 尝试在 PATH 中找 scrcpy
            found = False
            for d in os.environ.get('PATH', '').split(os.pathsep):
                if d and os.path.isfile(os.path.join(d, exe_name)):
                    found = True
                    break
            if not found:
                # 动态生成当前平台的目录名和 scrcpy 包前缀
                _plat_dir = {'darwin': 'Super_ADB_MAC', 'linux': 'Super_ADB_Linux', 'win32': 'Super_ADB_Win'}.get(sys.platform, 'Super_ADB_Win')
                _scrcpy_prefix = {'darwin': 'scrcpy-mac-', 'linux': 'scrcpy-linux-', 'win32': 'scrcpy-win64-'}.get(sys.platform, 'scrcpy-win64-')
                raise FileNotFoundError(
                    '未找到 scrcpy 可执行文件。\n'
                    f'请下载对应平台 release 包并放到 {_plat_dir}/外部扩展/scrcpy/{_scrcpy_prefix}vX.Y/ 下。'
                )
            cmd = [exe_name, '-s', serial] + args
            cwd = None

        # 自研/TCP 模式兜底：scrcpy 依赖 adb server 看到设备。
        # 若 serial 是 IP:端口（自研直连/无线），先 adb connect 把设备挂到 server，
        # 再启动官方 scrcpy；adbd 支持多连接，与自研直连并存。
        if ':' in serial:
            try:
                connect_adb = getattr(self, 'adb_path', None) or 查找内置adb路径() or 'adb'
                connect_kwargs = {}
                if sys.platform == 'win32':
                    connect_kwargs['creationflags'] = CREATE_NO_WINDOW
                subprocess.run(
                    [connect_adb, 'connect', serial],
                    capture_output=True, timeout=15, **connect_kwargs
                )
                time.sleep(0.5)
            except Exception:
                pass

        # 启动 scrcpy: 用 CREATE_NO_WINDOW 隐藏控制台黑框
        popen_kwargs = {}
        if sys.platform == 'win32':
            popen_kwargs['creationflags'] = CREATE_NO_WINDOW
        if cwd:
            popen_kwargs['cwd'] = cwd
        try:
            subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:
            raise RuntimeError(f'启动 scrcpy 失败: {e}')
        return f'已启动投屏: {exe_path}'

    def 获取应用列表(self, serial, flag=''):
        args = ['shell', 'pm', 'list', 'packages', '-f']
        if flag:
            args.append(flag)
        return self.直接执行(serial, args, timeout=30)

    def 获取运行中应用(self, serial):
        return self.执行shell(serial, 'pm list packages -e', timeout=30)

    def 获取当前界面应用(self, serial):
        try:
            out = self.执行shell(serial, 'dumpsys window | grep mCurrentFocus', timeout=10)
            m = re.search(r'\{(.*?)\}', out)
            if m:
                parts = m.group(1).split()
                if len(parts) >= 3:
                    return parts[2]
            return out.strip() or '未获取到当前界面'
        except Exception as e:
            return f'获取失败: {e}'

    def 启动应用(self, serial, package_name):
        if '/' in package_name:
            self.执行shell(serial, f'am start -n {package_name}', timeout=10)
            return f'已启动 {package_name}'

        # 先检查 monkey 是否可用 (部分模拟器/精简系统不含 monkey)
        # command -v monkey 在无 monkey 时返回非零 → run_shell 会抛 AdbError
        try:
            mk = self.执行shell(serial, 'command -v monkey', timeout=5)
            has_monkey = 'monkey' in (mk or '').lower()
        except AdbError:
            has_monkey = False

        if not has_monkey:
            # 没 monkey → 用 am start 回退: 先查入口 Activity
            resolve = self.执行shell(
                serial, f'cmd package resolve-activity --brief {package_name}',
                timeout=10)
            activity = ''
            for ln in (resolve or '').strip().splitlines():
                ln = ln.strip()
                if '/' in ln:
                    activity = ln
                    break
            if not activity:
                return f'{package_name} 未找到入口 Activity (设备无 monkey 且 resolve-activity 无结果)'
            self.执行shell(serial, f'am start -n {activity}', timeout=10)
            return f'已启动 {package_name} (via am start: {activity})'

        # 有 monkey → 正常用 monkey 启动
        out = self.执行shell(serial, f'monkey -p {package_name} -v -v -v 1', timeout=15)
        if 'No activities found' in out:
            return f'{package_name} 没找到入口，检查包名是否正确'
        return f'已启动 {package_name}'

    def 停止应用(self, serial, package_name):
        return self.执行shell(serial, f'am force-stop {package_name}', timeout=10).strip()

    def 清除应用(self, serial, package_name):
        return self.执行shell(serial, f'pm clear {package_name}', timeout=15).strip()

    def 卸载应用(self, serial, package_name):
        # 自研 ADB 模式：用自研 adb 卸载
        if self._用自研adb and serial:
            client = self._获取自研adb(serial)
            if client:
                try:
                    if self.log_callback:
                        try:
                            self.log_callback(f'$ adb -s {serial} uninstall {package_name} [自研adb]')
                        except Exception:
                            pass
                    return client.执行shell(f'pm uninstall {package_name}', timeout=30)
                except Exception as e:
                    if self.log_callback:
                        try:
                            self.log_callback(f'[ADB] 自研adb卸载失败: {e}')
                        except Exception:
                            pass
                    raise AdbError(f'自研adb卸载失败: {e}')
        r = self._run([self.adb_path, '-s', serial, 'uninstall', package_name], timeout=30)
        return r.stdout.strip() or r.stderr.strip()

    def 安装apk(self, serial, apk_path, extra_args=None, timeout=180):
        """安装 APK。

        extra_args: adb install 的附加参数列表, 例如 ['-r', '-t']。
        路径含空格/中文时由 _cmd_str 自动加引号 (shell=True)。
        返回 (returncode, stdout, stderr), 由上层决定如何展示。
        """
        # 自研 ADB 模式：用自研 adb 安装（push + pm install）
        if self._用自研adb and serial:
            client = self._获取自研adb(serial)
            if client:
                try:
                    if self.log_callback:
                        try:
                            opts = ' '.join(str(a) for a in (extra_args or []))
                            self.log_callback(f'$ adb -s {serial} install {opts} {apk_path} [自研adb]')
                        except Exception:
                            pass
                    result = client.安装应用(apk_path, timeout=timeout, extra_args=extra_args)
                    if 'Success' in result or 'success' in result:
                        return 0, result, ''
                    return 1, '', result
                except Exception as e:
                    if self.log_callback:
                        try:
                            self.log_callback(f'[ADB] 自研adb安装失败: {e}')
                        except Exception:
                            pass
                    return 1, '', str(e)
        cmd = [self.adb_path, '-s', serial, 'install']
        if extra_args:
            cmd.extend(str(a) for a in extra_args)
        cmd.append(apk_path)
        r = self._run(cmd, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    def _安装失败诊断(self, error_msg, remote_path, client=None):
        """根据安装失败错误信息生成友好的诊断提示。

        返回多行字符串，附加到错误信息末尾。
        """
        diag_lines = []
        try:
            err = error_msg or ''
            # INSTALL_FAILED_MEDIA_UNAVAILABLE
            if 'MEDIA_UNAVAILABLE' in err or 'restorecon' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: /data 分区 SELinux 上下文异常或存储空间不足')
                diag_lines.append('建议:')
                diag_lines.append('  1. 尝试重启设备后再安装')
                diag_lines.append('  2. 检查设备内部存储空间是否充足')
                diag_lines.append('  3. 模拟器/TV盒子可尝试切换到官方 adb 模式')
            # INSTALL_FAILED_INSUFFICIENT_STORAGE
            elif 'INSUFFICIENT_STORAGE' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: 设备存储空间不足')
                diag_lines.append('建议: 清理设备内部存储空间后重试')
            # INSTALL_FAILED_VERSION_DOWNGRADE
            elif 'VERSION_DOWNGRADE' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: 已安装的版本比当前 APK 版本高')
                diag_lines.append('建议: 先卸载旧版本，或使用 -d 参数允许降级安装')
            # INSTALL_FAILED_ALREADY_EXISTS
            elif 'ALREADY_EXISTS' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: 应用已存在')
                diag_lines.append('建议: 使用 -r 参数覆盖安装')
            # INSTALL_FAILED_INVALID_APK
            elif 'INVALID_APK' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: APK 文件无效或损坏')
                diag_lines.append('建议: 检查 APK 文件是否完整，重新下载后重试')
            # INSTALL_FAILED_UID_CHANGED
            elif 'UID_CHANGED' in err:
                diag_lines.append('')
                diag_lines.append('--- 诊断建议 ---')
                diag_lines.append('原因: 应用 UID 不一致（旧版本卸载不干净）')
                diag_lines.append('建议: 先彻底卸载应用（pm uninstall 包名）再安装')

            # 通用诊断：检查 /data 空间
            if client:
                try:
                    df = client.执行shell('df -k /data', timeout=5) or ''
                    self._log(f'[安装] /data 空间: {df.strip()}')
                except Exception:
                    pass
                # 检查 /data/local/tmp 下的临时文件
                try:
                    ls_tmp = client.执行shell('ls -la /data/local/tmp/', timeout=5) or ''
                    self._log(f'[安装] /data/local/tmp 内容: {ls_tmp.strip()[:200]}')
                except Exception:
                    pass
        except Exception as de:
            self._log(f'[安装] 诊断异常: {de}')
        return '\n'.join(diag_lines)

    def 安装(self, serial, apk_path, extra_args=None, timeout=300, progress_cb=None):
        """完整安装流程：push → pm install → cleanup，返回 (ok:bool, message:str)。

        progress_cb(pct:int, msg:str) 可选，用于 UI 进度反馈
        （推送阶段映射到 5%-75%，安装 80%，清理 95%，完成 100%）。
        apk 文件名中的特殊字符会被替换为 `_`，避免 adb shell 传参问题。
        """
        # 自研 ADB 模式：直接调用推送 + pm install，有详细进度
        if self._用自研adb and serial:
            client = self._获取自研adb(serial)
            if not client:
                return False, '自研adb连接失败'
            base = os.path.basename(apk_path)
            remote = f'/sdcard/Super_ADB/Super_ADB_install_{int(time.time())}_{base}'
            # 输出自研adb日志
            opts = ' '.join(str(a) for a in (extra_args or ['-r']))
            if self.log_callback:
                try:
                    self.log_callback(f'$ adb -s {serial} install {opts} {apk_path} [自研adb]')
                except Exception:
                    pass
            try:
                # 阶段1：推送APK（实时进度）
                file_size = os.path.getsize(apk_path)
                if progress_cb:
                    progress_cb(5, f'正在推送 APK ({file_size // 1024 // 1024} MB)...')
                def _推送进度(sent, total):
                    if progress_cb and total > 0:
                        pct = 5 + int(sent / total * 70)  # 5% - 75%
                        progress_cb(pct, f'正在推送 APK... {sent // 1024}KB / {total // 1024}KB')
                # 推送超时给足（大文件需要时间），sync 内部会用 min(timeout, 120)
                client.推送文件(apk_path, remote, timeout=300, progress_cb=_推送进度)
                # 阶段2：安装
                if progress_cb:
                    progress_cb(80, '推送完成，正在安装...')
                args_str = ' '.join(str(a) for a in (extra_args or ['-r']))
                # 安装前恢复 SELinux 上下文，避免模拟器/盒子上常见的
                # INSTALL_FAILED_MEDIA_UNAVAILABLE: Failed to restorecon
                try:
                    client.执行shell(f'restorecon "{remote}"', timeout=10)
                    self._log('[安装] restorecon 执行成功')
                except Exception:
                    self._log('[安装] restorecon 跳过（设备不支持）')
                result = client.执行shell(f'pm install {args_str} "{remote}"', timeout=timeout)
                # 阶段3：清理
                if progress_cb:
                    progress_cb(95, '清理临时文件...')
                try:
                    client.执行shell(f'rm "{remote}"', timeout=10)
                except Exception:
                    pass
                if progress_cb:
                    progress_cb(100, '安装完成')
                if 'Success' in result or 'success' in result:
                    return True, result
                # 安装失败：附加诊断提示
                diag = self._安装失败诊断(result, remote, client)
                return False, result + diag
            except Exception as e:
                if progress_cb:
                    progress_cb(0, f'安装失败: {e}')
                return False, f'安装失败: {e}'

        size = 0
        try:
            size = os.path.getsize(apk_path)
        except OSError:
            pass
        base = os.path.basename(apk_path)
        safe_base = re.sub(r'[^\w.\-]', '_', base)
        remote = f'/data/local/tmp/Super_ADB_install_{int(time.time())}_{safe_base}'

        if progress_cb:
            progress_cb(5, '准备传输 APK...')

        # 清理历史残留的临时 APK（异常退出可能留下旧文件）
        try:
            self.执行shell(serial, 'rm -f /data/local/tmp/Super_ADB_install_*.apk', timeout=10)
        except Exception:
            pass

        # 阶段 2：推送（流式进度映射到 5%-75%）
        if progress_cb:
            def _push_cb(sent, total, elapsed):
                pct = min(100, int(sent / total * 100)) if total else 0
                progress_cb(5 + int(pct * 0.70), f'推送中 {pct}%（APK 安装阶段）')
            push_cb = _push_cb
        else:
            push_cb = None
        try:
            self.流式推送(serial, apk_path, remote, progress_cb=push_cb)
        except AdbError as e:
            return False, f'推送失败: {e}'
        if progress_cb:
            progress_cb(78, '推送完成，准备安装...')

        # 阶段 3：pm install（远端路径，避免本地路径含空格/中文的坑）
        if progress_cb:
            progress_cb(80, '正在安装，请稍候...')
        # 安装前恢复 SELinux 上下文，避免模拟器上常见的
        # INSTALL_FAILED_MEDIA_UNAVAILABLE: Failed to restorecon
        try:
            self._run_no_shell(
                [self.adb_path] + (['-s', serial] if serial else [])
                + ['shell', 'restorecon', remote], timeout=10)
        except AdbError:
            pass  # 部分设备无 restorecon 命令，忽略即可
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        cmd += ['shell', 'pm', 'install']
        if extra_args:
            cmd.extend(str(a) for a in extra_args)
        cmd.append(remote)
        try:
            r = self._run_no_shell(cmd, timeout=timeout)
        except AdbError as e:
            try:
                self._run_no_shell(
                    [self.adb_path] + (['-s', serial] if serial else [])
                    + ['shell', 'rm', '-f', remote], timeout=10)
            except AdbError:
                pass
            return False, f'安装失败: {e}'
        out = (r.stdout or '') + (r.stderr or '')
        if r.returncode == 0 and 'Success' in (r.stdout or ''):
            if progress_cb:
                progress_cb(95, '清理临时文件...')
            try:
                self._run_no_shell(
                    [self.adb_path] + (['-s', serial] if serial else [])
                    + ['shell', 'rm', '-f', remote], timeout=10)
            except AdbError:
                pass
            if progress_cb:
                progress_cb(100, '安装完成')
            return True, '安装成功。'
        # pm install 失败：若是 restorecon / MEDIA_UNAVAILABLE 类错误，
        # 回退到 adb install（直接传本地文件，不走 /data/local/tmp）
        low = out.lower()
        if 'restorecon' in low or 'media_unavailable' in low:
            if progress_cb:
                progress_cb(85, 'pm install 失败，回退到 adb install...')
            try:
                cmd2 = [self.adb_path]
                if serial:
                    cmd2 += ['-s', serial]
                cmd2 += ['install']
                if extra_args:
                    cmd2.extend(str(a) for a in extra_args)
                cmd2.append(apk_path)
                r2 = self._run_no_shell(cmd2, timeout=timeout)
                out2 = (r2.stdout or '') + (r2.stderr or '')
                if r2.returncode == 0 and 'Success' in (r2.stdout or ''):
                    if progress_cb:
                        progress_cb(95, '清理临时文件...')
                    try:
                        self._run_no_shell(
                            [self.adb_path] + (['-s', serial] if serial else [])
                            + ['shell', 'rm', '-f', remote], timeout=10)
                    except AdbError:
                        pass
                    if progress_cb:
                        progress_cb(100, '安装完成')
                    return True, '安装成功（adb install 回退）。'
                out = out2
                r = r2
            except AdbError:
                pass  # 回退异常，继续返回原始错误
        try:
            self._run_no_shell(
                [self.adb_path] + (['-s', serial] if serial else [])
                + ['shell', 'rm', '-f', remote], timeout=10)
        except AdbError:
            pass
        # 附加诊断信息
        diag = self._安装失败诊断(out, remote)
        return False, f'安装失败 (returncode={r.returncode}):\n{out.strip()}{diag}'

    def 获取应用信息(self, serial, package_name):
        """获取应用信息 (安装路径 + PID)。

        批量执行 pm path + pidof, 1 次 RTT 替代 2 次, 节省 ~1-2s。
        脚本通过 base64 编码执行, 避免 Windows cmd.exe 嵌套引号问题。
        """
        script = (
            'echo "===PATH==="\n'
            f'pm path {package_name} 2>&1\n'
            'echo "===PID==="\n'
            f'pidof {package_name} 2>&1\n'
            'echo "===END==="\n'
        )
        path = ''
        pid = ''
        try:
            raw = self.执行批量脚本(serial, script, timeout=10)
            section = None
            path_lines = []
            for line in (raw or '').splitlines():
                line = line.strip()
                if line == '===PATH===':
                    section = 'path'
                    continue
                if line == '===PID===':
                    section = 'pid'
                    continue
                if line == '===END===':
                    break
                if section == 'path' and line:
                    path_lines.append(line)
                elif section == 'pid' and line:
                    pid = line
            path = '\n'.join(path_lines).replace('package:', '').strip()
        except Exception as e:
            path = f'获取失败: {e}'
            pid = '未运行'
        if not pid:
            pid = '未运行'
        return f'包名: {package_name}\n安装路径: {path or "未安装"}\n进程 PID: {pid}'

    def 获取内存信息(self, serial, package_name):
        # 去掉尾随 `/` 与 `pkg/Activity` 中的 Activity 部分，避免非法包名导致解析全空
        pkg = package_name.rstrip('/').split('/', 1)[0].strip() if package_name else package_name
        return self.执行shell(serial, f'dumpsys meminfo {pkg}', timeout=15)

    def logcat到桌面(self, serial):
        """打开一个独立终端窗口实时输出 logcat 到桌面文件。"""
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        if sys.platform == 'darwin':
            # macOS：用 osascript 调 Terminal.app
            ts = time.strftime('%Y-%m-%d %H时%M分%S秒')
            log_file = os.path.join(desktop, f'日志{ts}.log')
            script = (
                f'tell application "Terminal"\n'
                f'  activate\n'
                f'  do script "adb -s {serial} logcat -v time > \\"{log_file}\\""\n'
                f'end tell'
            )
            subprocess.Popen(['osascript', '-e', script])
            return '已在终端中启动 logcat，关闭终端窗口或按 Ctrl+C 结束'
        elif sys.platform == 'linux':
            # Linux：尝试用 x-terminal-emulator 或 gnome-terminal
            ts = time.strftime('%Y-%m-%d %H时%M分%S秒')
            log_file = os.path.join(desktop, f'日志{ts}.log')
            cmd = f'adb -s {serial} logcat -v time > "{log_file}"'
            subprocess.Popen(
                ['x-terminal-emulator', '-e', f'bash -c \'{cmd}; exec bash\''],
                stderr=subprocess.DEVNULL
            )
            return '已在终端中启动 logcat，关闭终端窗口或按 Ctrl+C 结束'
        else:
            # Windows：start cmd /k + %date% %time%
            cmd = (f'start cmd /k "adb -s {serial} logcat -v time '
                   f'> \\"{desktop}\\日志%date:~0,4%-%date:~5,2%-%date:~8,2% %time:~0,2%时%time:~3,2%分%time:~6,2%.log\\""')
            subprocess.Popen(cmd, shell=True)
            return '已在独立窗口中启动 logcat，关闭窗口或按 Ctrl+C 结束'


# Android ls -la 常见时间格式：
#   drwxrwxrwx 3 root root 4096 Jul 27 14:05 Alarms
#   drwxrwxrwx 3 root root 4096 2026-07-27 14:05 Alarms
#   drwxrwxrwx 3 root root 4096 Jul 27 2026 Alarms
_MONTHS = {'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'}

# ----------------------------------------------------------------------
# 文件管理封装（供 file_manager_page 使用）
# ----------------------------------------------------------------------
def _decode_adb_output(b):
    """稳健解码 adb 输出字节流：优先 UTF-8，失败回退 GB18030/GBK，最后 latin-1。

    部分老 ROM 的 shell 输出并非 UTF-8（如 GBK 中文环境），若按系统 locale
    直接解码会出现中文文件名乱码；此函数可自动还原正确文本，专治 list_dir
    的中文文件名乱码问题。
    """
    if not b:
        return ''
    for enc in ('utf-8', 'gb18030', 'latin-1'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode('utf-8', errors='replace')


def _unescape_ls_name(name):
    """还原 ls 输出中文件名的 shell 风格转义。

    Android toybox / GNU coreutils 的 ls 在 stdout 非终端（管道）时，
    默认用 shell-escape 风格引用文件名，把空格、引号、反斜杠等转义为
    反斜杠前缀形式（如 "a b" → "a\\ b"）。_parse_ls_line 按空白分割后
    再 join 会保留这些转义符，导致 UI 显示 "a\\ b" 而非 "a b"，且拼接
    出的 path 也带反斜杠，后续下载/删除会找不到文件。此函数逐个还原
    常见转义序列，未知转义则去掉反斜杠保留原字符。
    """
    if '\\' not in name:
        return name
    result = []
    i = 0
    n = len(name)
    while i < n:
        ch = name[i]
        if ch == '\\' and i + 1 < n:
            nxt = name[i + 1]
            if nxt == ' ':
                result.append(' ')
            elif nxt == '\\':
                result.append('\\')
            elif nxt == '"':
                result.append('"')
            elif nxt == "'":
                result.append("'")
            elif nxt == 'n':
                result.append('\n')
            elif nxt == 't':
                result.append('\t')
            elif nxt == 'r':
                result.append('\r')
            else:
                # 未知转义：去掉反斜杠，保留原字符（与 shell 解析一致）
                result.append(nxt)
            i += 2
        else:
            result.append(ch)
            i += 1
    return ''.join(result)


class AdbFileManager(AdbHelper):
    """adb 文件管理：列出目录、上传、下载、删除、重命名、授权(chmod)。"""

    # Android ls -la 常见时间格式：
    #   drwxrwxrwx 3 root root 4096 Jul 27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 2026-07-27 14:05 Alarms
    #   drwxrwxrwx 3 root root 4096 Jul 27 2026 Alarms

    def 列出目录(self, serial, path):
        ls_path = path if path == '/' else path.rstrip('/') + '/'
        # 自研 ADB 模式：走执行shell（优先自研adb客户端），避免启动官方 adb server
        if self._用自研adb:
            out = self.执行shell(serial, f'ls -la "{ls_path}"', timeout=20)
            err = ''
        else:
            cmd = self._base_cmd(serial) + ['shell', 'ls', '-la', f'"{ls_path}"']
            # 直接以字节流执行（shell=False，避开 Windows cmd.exe 对管道/引号的坑），
            # 再按 UTF-8→GBK 顺序稳健解码，根治老 ROM 中文文件名乱码。
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, shell=False,
                    timeout=20, creationflags=CREATE_NO_WINDOW,
                )
            except subprocess.TimeoutExpired:
                raise AdbError('列出目录超时')
            except FileNotFoundError:
                raise AdbError(f'未找到 adb 命令: {self.adb_path}')
            out = _decode_adb_output(proc.stdout)
            err = _decode_adb_output(proc.stderr)
            if proc.returncode != 0 and not out.strip():
                raise AdbError(self._诊断非自研不可用(serial, self._translate_error(err or out)))

        entries = []
        for line in out.splitlines():
            line = line.rstrip('\r\n')
            if not line.strip() or line.strip().startswith('total'):
                continue
            parsed = self._parse_ls_line(line, path)
            if parsed:
                entries.append(parsed)
        return entries

    def 读取文本文件(self, serial, remote_path, max_bytes=2_000_000):
        """读取文本文件内容（供文件管理器预览用）。

        走 adb pull 落地到临时目录后按 UTF-8→GBK→latin-1 解码，可正确还原
        中文内容；超过 max_bytes 的部分会被截断并返回 truncated 标记。
        """
        import tempfile
        import shutil
        td = tempfile.mkdtemp(prefix='super_adb_read_')
        try:
            self.拉取文件(serial, remote_path, td)
            files = [os.path.join(td, f) for f in os.listdir(td)]
            if not files:
                raise AdbError('拉取内容为空')
            fpath = files[0]
            with open(fpath, 'rb') as fh:
                raw = fh.read()
            truncated = len(raw) > max_bytes
            if truncated:
                raw = raw[:max_bytes]
            text = _decode_adb_output(raw)
            return {'text': text, 'truncated': truncated, 'size': len(raw)}
        finally:
            shutil.rmtree(td, ignore_errors=True)

    @staticmethod
    def _parse_ls_line(line, parent_path):
        """解析 ls -la 单行，支持多种时间/日期格式。"""
        parts = line.strip().split(None, 8)
        if len(parts) < 7:
            return None
        perm = parts[0]
        size_str = parts[4]

        # 判断时间格式
        if re.match(r'\d{4}-\d{2}-\d{2}', parts[5]):
            # YYYY-MM-DD HH:MM name
            if len(parts) < 8:
                return None
            mtime = f"{parts[5]} {parts[6]}"
            name = ' '.join(parts[7:])
        elif parts[5] in _MONTHS:
            # MMM DD HH:MM / MMM DD YYYY name
            if len(parts) < 9:
                return None
            mtime = f"{parts[5]} {parts[6]} {parts[7]}"
            name = ' '.join(parts[8:])
        else:
            # HH:MM name (或其他单字段时间)
            mtime = parts[5]
            name = ' '.join(parts[6:])

        # 还原 ls 对文件名的 shell 风格转义（空格 → \ 等），
        # 必须在构造 child_path 和符号链接分割之前完成。
        name = _unescape_ls_name(name)

        is_dir = perm[0] == 'd'
        is_link = perm[0] == 'l'
        if is_link and ' -> ' in name:
            name = name.split(' -> ', 1)[0].strip()
        if name in ('.', '..'):
            return None
        try:
            size = int(size_str)
        except ValueError:
            size = 0
        base = parent_path.rstrip('/')
        child_path = base + '/' + name if base else '/' + name
        return {
            'name': name, 'path': child_path, 'is_dir': is_dir,
            'size': size, 'perm': perm, 'is_link': is_link, 'mtime': mtime,
        }

    def 推送文件(self, serial, local_path, remote_dir, progress_cb=None):
        # 自研 ADB 模式：直接推送到目标目录，取消临时目录+移动步骤
        if self._用自研adb:
            filename = os.path.basename(local_path)
            try:
                local_size = os.path.getsize(local_path)
            except OSError:
                local_size = -1
            # 计算目标路径（目录则拼接文件名）
            if remote_dir.endswith('/'):
                target = remote_dir + filename
                target_dir = remote_dir.rstrip('/')
            else:
                target = remote_dir
                target_dir = os.path.dirname(remote_dir)
            # 确保目标目录存在
            if target_dir:
                mkdir_out = (self.执行shell(
                    serial, f'mkdir -p "{target_dir}" 2>&1 && echo MKDIR_OK',
                    timeout=10) or '').strip()
                self._log(f'[上传] 创建目录: {target_dir} -> {mkdir_out or "无输出"}')
                if 'MKDIR_OK' not in mkdir_out:
                    self._log(f'[上传] ✗ 创建目录失败: {mkdir_out}')
                    raise AdbError(f'上传失败: 无法创建目标目录 {target_dir}（{mkdir_out}）')
            # 直接推送到目标路径
            self._log(f'[上传] push: {local_path} ({local_size}B) -> {target}')
            try:
                client = self._获取自研adb(serial)
                if not client:
                    raise AdbError(f'自研adb连接设备失败: {serial}')
                ok = client.推送文件(local_path, target, timeout=300, progress_cb=progress_cb)
                if not ok:
                    raise AdbError(f'推送失败: {local_path} -> {target}')
                self._log(f'[上传] push完成')
            except Exception as e:
                # 推送失败，输出详细诊断
                self._log(f'[上传] ✗ push失败: {e}')
                self._log(f'[上传] --- 诊断信息 ---')
                self._log(f'[上传] 本地文件: {local_path} ({local_size}B)')
                self._log(f'[上传] 远程目标: {target}')
                # 检查本地文件是否存在
                if not os.path.isfile(local_path):
                    self._log('[上传] ✗ 本地文件不存在')
                    raise AdbError(f'上传失败: 本地文件不存在 {local_path}')
                # 检查本地文件大小
                try:
                    actual_size = os.path.getsize(local_path)
                    self._log(f'[上传] 本地文件实际大小: {actual_size}B')
                except Exception:
                    pass
                # 诊断提示
                err_msg = str(e)
                诊断建议 = self._生成上传诊断(err_msg, target, target_dir)
                self._log(f'[上传] --- 诊断结束 ---')
                raise AdbError(f'上传失败: {err_msg}{诊断建议}')
            # 验证目标文件确实落盘且大小一致
            verify = (self.执行shell(
                serial, f'ls -l "{target}"', timeout=10) or '').strip()
            self._log(f'[上传] 验证: {verify or "无输出"}')
            if not verify or 'No such file' in verify or filename not in _unescape_ls_name(verify):
                self._log(f'[上传] ✗ 验证失败: 文件不存在 {verify or "无输出"}')
                # 进一步诊断
                self._log(f'[上传] --- 验证失败诊断 ---')
                # 检查目录权限
                try:
                    dir_perm = (self.执行shell(
                        serial, f'ls -ld "{target_dir}" 2>&1', timeout=5) or '').strip()
                    self._log(f'[上传] 目标目录权限: {dir_perm}')
                except Exception:
                    pass
                # 检查 /system 分区空间
                if target_dir.startswith('/system'):
                    try:
                        space = (self.执行shell(
                            serial, 'df -k /system 2>&1', timeout=5) or '').strip()
                        self._log(f'[上传] /system 分区空间: {space}')
                    except Exception:
                        pass
                self._log(f'[上传] --- 诊断结束 ---')
                raise AdbError(f'上传失败: 目标文件不存在 {target} '
                               f'({verify or "无输出"})')
            # 验证文件大小是否一致
            try:
                # 从 ls -l 输出中解析文件大小（第5列）
                parts = verify.split()
                remote_size = int(parts[4]) if len(parts) >= 5 else -1
                local_size_actual = os.path.getsize(local_path)
                self._log(f'[上传] 大小校验: 本地={local_size_actual}B 远程={remote_size}B')
                if remote_size < 0 or remote_size != local_size_actual:
                    raise AdbError(
                        f'上传失败: 文件大小不一致 '
                        f'(本地={local_size_actual}B, 远程={remote_size}B)')
            except OSError:
                self._log('[上传] ⚠ 无法读取本地文件大小，跳过大小校验')
            except (ValueError, IndexError):
                self._log('[上传] ⚠ 无法解析远程文件大小，跳过大小校验')
            self._log(f'[上传] 成功: {target}')
            return '推送成功'
        # 复用 AdbHelper.push_stream（支持进度回调）
        self._log(f'[上传] 流式推送: {local_path} -> {remote_dir}')
        self.流式推送(serial, local_path, remote_dir, progress_cb=progress_cb)
        # 验证目标文件确实落盘且大小一致
        filename = os.path.basename(local_path)
        if remote_dir.endswith('/'):
            target = remote_dir + filename
            target_dir = remote_dir.rstrip('/')
        else:
            target = remote_dir
            target_dir = os.path.dirname(remote_dir)
        verify_cmd = self._base_cmd(serial) + ['shell', 'ls', '-l', f'"{target}"']
        verify_r = self._run(verify_cmd, timeout=10)
        verify_out = (verify_r.stdout or '').strip()
        self._log(f'[上传] 验证: {verify_out or "无输出"}')
        if not verify_out or 'No such file' in verify_out or filename not in _unescape_ls_name(verify_out):
            self._log(f'[上传] ✗ 验证失败: 文件不存在 {verify_out or "无输出"}')
            raise AdbError(f'上传失败: 目标文件不存在 {target} ({verify_out or "无输出"})')
        # 验证文件大小是否一致
        try:
            parts = verify_out.split()
            remote_size = int(parts[4]) if len(parts) >= 5 else -1
            local_size_actual = os.path.getsize(local_path)
            self._log(f'[上传] 大小校验: 本地={local_size_actual}B 远程={remote_size}B')
            if remote_size < 0 or remote_size != local_size_actual:
                raise AdbError(
                    f'上传失败: 文件大小不一致 '
                    f'(本地={local_size_actual}B, 远程={remote_size}B)')
        except OSError:
            self._log('[上传] ⚠ 无法读取本地文件大小，跳过大小校验')
        except (ValueError, IndexError):
            self._log('[上传] ⚠ 无法解析远程文件大小，跳过大小校验')
        self._log(f'[上传] 成功: {remote_dir}')
        return '推送成功'

    def _生成上传诊断(self, err_msg, target, target_dir):
        """根据错误信息生成诊断建议。"""
        诊断建议 = ''
        if '字节数不一致' in err_msg or '0B' in err_msg:
            诊断建议 = ' | 诊断: 设备权限不足或分区空间已满，请确认 root 权限和分区空间'
        elif '只读' in err_msg or 'read-only' in err_msg.lower():
            诊断建议 = ' | 诊断: 目标分区为只读，需执行 adb root && adb remount'
        elif '不存在' in err_msg or 'No such file' in err_msg:
            诊断建议 = f' | 诊断: 目标目录 {target_dir} 不存在，请先创建'
        elif 'Permission denied' in err_msg:
            诊断建议 = ' | 诊断: 权限被拒绝，请检查设备端权限设置'
        elif '超时' in err_msg or 'timeout' in err_msg.lower():
            诊断建议 = ' | 诊断: 操作超时，可能是设备连接不稳定或文件过大'
        return 诊断建议

    def 拉取文件(self, serial, remote_path, local_dir):
        # 自研 ADB 模式：优先用 sync 协议拉取，失败回退 base64
        if self._用自研adb:
            filename = os.path.basename(remote_path.rstrip('/'))
            # local_dir 可能是目录或完整文件路径
            if os.path.isdir(local_dir):
                local_path = os.path.join(local_dir, filename)
            else:
                local_path = local_dir
            # 拉取前确保目标目录存在（sync 和 base64 回退都需要）
            local_parent = os.path.dirname(local_path)
            if local_parent and not os.path.isdir(local_parent):
                os.makedirs(local_parent, exist_ok=True)
            self._log(f'[下载] 拉取: {remote_path} -> {local_path}')
            qpath = shlex.quote(remote_path)
            try:
                client = self._获取自研adb(serial)
                if not client:
                    raise AdbError(f'自研adb连接设备失败: {serial}')
                ok = client.拉取文件(remote_path, local_path, timeout=300)
                if not ok:
                    raise AdbError(f'拉取失败: {remote_path} -> {local_path}')
            except Exception as e:
                self._log(f'[下载] sync拉取失败: {e}')
                # 回退到 base64 方式
                self._log(f'[下载] 回退 base64 方式拉取...')
                import base64
                b64_data = self.执行shell(serial, f'base64 {qpath}', timeout=300)
                b64_clean = ''.join((b64_data or '').split())
                if not b64_clean:
                    raise AdbError("拉取失败：文件为空或不存在")
                file_data = base64.b64decode(b64_clean)
                with open(local_path, 'wb') as f:
                    f.write(file_data)
            # 验证文件大小（用 wc -c 不受文件名空格影响）
            try:
                wc_out = (self.执行shell(
                    serial, f'wc -c < {qpath}', timeout=10) or '').strip()
                remote_size = int(wc_out.split()[0]) if wc_out else -1
                local_size = os.path.getsize(local_path)
                self._log(f'[下载] 大小校验: 远程={remote_size}B 本地={local_size}B')
                if remote_size > 0 and local_size != remote_size:
                    raise AdbError(
                        f'下载失败: 文件大小不一致 '
                        f'(远程={remote_size}B, 本地={local_size}B)')
            except (ValueError, IndexError, OSError) as ve:
                self._log(f'[下载] ⚠ 大小校验跳过: {ve}')
            self._log(f'[下载] 成功: {local_path}')
            return '拉取成功'
        cmd = self._base_cmd(serial) + ['pull', remote_path, local_dir]
        r = self._run(cmd, timeout=300)
        if r.returncode != 0:
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        # 验证文件是否存在
        if os.path.isdir(local_dir):
            local_path = os.path.join(local_dir, os.path.basename(remote_path.rstrip('/')))
        else:
            local_path = local_dir
        if not os.path.exists(local_path):
            raise AdbError(f'下载失败: 本地文件不存在 {local_path}')
        self._log(f'[下载] 成功: {local_path}')
        return '拉取成功'

    def 删除路径(self, serial, path):
        # 自研 ADB 模式：走执行shell，删除后验证是否真的删除成功
        if self._用自研adb:
            self._log(f'[删除] 开始删除: {path}')
            qpath = shlex.quote(path)
            # 先检查路径是否存在
            exist_check = (self.执行shell(
                serial, f'ls -ld {qpath} 2>&1', timeout=10) or '').strip()
            if 'No such file' in exist_check or not exist_check:
                self._log(f'[删除] 路径不存在，无需删除: {path}')
                return '删除成功（路径不存在）'
            self._log(f'[删除] 路径存在: {exist_check}')
            # 执行删除
            del_out = (self.执行shell(
                serial, f'rm -rf {qpath} 2>&1 && echo RM_OK', timeout=30) or '').strip()
            self._log(f'[删除] rm输出: {del_out or "无输出"}')
            # 验证是否真的删除成功
            verify = (self.执行shell(
                serial, f'ls -ld {qpath} 2>&1', timeout=10) or '').strip()
            if 'No such file' in verify or not verify:
                self._log(f'[删除] 成功，路径已不存在: {path}')
                return '删除成功'
            else:
                self._log(f'[删除] 失败，路径仍存在: {verify}')
                raise AdbError(f'删除失败: 路径仍存在 {path} ({verify})')
        cmd = self._base_cmd(serial) + ['shell', 'rm', '-rf', shlex.quote(path)]
        r = self._run(cmd, timeout=30)
        if r.returncode != 0 or r.stderr.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        # 验证是否真的删除成功
        verify_cmd = self._base_cmd(serial) + ['shell', 'ls', '-ld', shlex.quote(path)]
        verify_r = self._run(verify_cmd, timeout=10)
        verify_out = (verify_r.stdout or '') + (verify_r.stderr or '')
        if 'No such file' in verify_out or not verify_out.strip():
            self._log(f'[删除] 成功，路径已不存在: {path}')
            return '删除成功'
        else:
            self._log(f'[删除] 失败，路径仍存在: {verify_out.strip()}')
            raise AdbError(f'删除失败: 路径仍存在 {path}')

    def 重命名路径(self, serial, old_path, new_path):
        # 自研 ADB 模式：走执行shell，重命名后验证是否成功
        if self._用自研adb:
            self._log(f'[重命名] {old_path} -> {new_path}')
            qold = shlex.quote(old_path)
            qnew = shlex.quote(new_path)
            mv_out = (self.执行shell(
                serial, f'mv {qold} {qnew} 2>&1 && echo MV_OK',
                timeout=30) or '').strip()
            self._log(f'[重命名] mv输出: {mv_out or "无输出"}')
            if 'MV_OK' not in mv_out:
                self._log(f'[重命名] 失败: {mv_out}')
                raise AdbError(f'重命名失败: {mv_out or "无输出"}')
            # 验证新路径是否存在
            verify = (self.执行shell(
                serial, f'ls -ld {qnew} 2>&1', timeout=10) or '').strip()
            if 'No such file' in verify or not verify:
                self._log(f'[重命名] 失败: 新路径不存在 {verify}')
                raise AdbError(f'重命名失败: 新路径不存在 {new_path} ({verify})')
            self._log(f'[重命名] 成功: {verify}')
            return '重命名成功'
        cmd = self._base_cmd(serial) + ['shell', 'mv', shlex.quote(old_path), shlex.quote(new_path)]
        r = self._run(cmd, timeout=30)
        if r.returncode != 0 or r.stderr.strip():
            raise AdbError(self._translate_error(r.stderr or r.stdout))
        return '重命名成功'

    def 修改权限(self, serial, path, mode='777'):
        """修改设备文件/目录权限（adb shell chmod），默认 777。

        用 && echo CHMOD_OK 确认命令真正执行成功——执行shell 不返回退出码，
        仅凭无异常无法判断 chmod 是否生效（如 /sdcard 为 FAT32 不支持
        Unix 权限、或权限不足时 chmod 会静默失败）。
        """
        qpath = shlex.quote(path)
        self._log(f'[权限] 开始修改权限: chmod {mode} "{path}"')
        # 先检查路径是否存在
        exist_check = (self.执行shell(
            serial, f'ls -ld {qpath} 2>&1', timeout=10) or '').strip()
        if 'No such file' in exist_check or not exist_check:
            self._log(f'[权限] 路径不存在: {path}')
            raise AdbError(f'修改权限失败: 路径不存在 {path}')
        self._log(f'[权限] 修改前: {exist_check}')
        # 执行chmod
        result = self.执行shell(
            serial, f'chmod {mode} {qpath} 2>&1 && echo CHMOD_OK', timeout=30)
        self._log(f'[权限] chmod输出: {result or "无输出"}')
        if 'CHMOD_OK' not in (result or ''):
            self._log(f'[权限] 失败: chmod未生效')
            raise AdbError(f'修改权限失败（可能是 FAT32/sdcard 不支持 Unix 权限，或需要 root）：{result or ""}')
        # 验证权限是否真的修改了
        verify = (self.执行shell(
            serial, f'ls -ld {qpath}', timeout=10) or '').strip()
        self._log(f'[权限] 修改后: {verify}')
        # 校验权限位是否真的变成了目标 mode
        try:
            # ls -l 输出第1列是权限位，如 drwxrwxrwx 或 -rw-rw-rw-
            parts = verify.split()
            perm_str = parts[0] if parts else ''
            # 提取权限位（去掉第一个文件类型字符）
            if len(perm_str) >= 10:
                actual_perm = perm_str[1:10]  # 如 rwxrwxrwx
                # 将 mode 数字转为权限字符串进行比对
                def _perm_octal_to_str(octal):
                    """将八进制权限如 '777' 转为字符串如 rwxrwxrwx"""
                    bits = ''
                    for ch in octal:
                        n = int(ch)
                        bits += 'r' if n & 4 else '-'
                        bits += 'w' if n & 2 else '-'
                        bits += 'x' if n & 1 else '-'
                    return bits
                expected_perm = _perm_octal_to_str(str(mode))
                self._log(f'[权限] 权限校验: 期望={expected_perm} 实际={actual_perm}')
                if actual_perm != expected_perm:
                    raise AdbError(
                        f'修改权限失败: 权限未生效 '
                        f'(期望 {expected_perm}, 实际 {actual_perm})，'
                        f'可能是 FAT32/sdcard 不支持 Unix 权限，或需要 root')
        except (ValueError, IndexError) as e:
            self._log(f'[权限] ⚠ 权限校验跳过: {e}')
        self._log(f'[权限] 成功: chmod {mode} "{path}"')
        return result

    def 修改时间(self, serial, path, timestamp=None):
        """修改设备文件时间戳（adb shell touch）。

        Args:
            serial: 设备序列号
            path: 文件路径
            timestamp: 时间字符串，如 '202401011200.00' (YYYYMMDDHHMM.SS)
                       或 '2024-01-01 12:00:00'；None 则设为当前时间
        """
        if timestamp:
            # 支持两种格式：YYYYMMDDHHMM.SS 或 YYYY-MM-DD HH:MM:SS
            if '-' in timestamp:
                # YYYY-MM-DD HH:MM:SS → touch -d
                cmd = f'touch -d "{timestamp}" "{path}"'
            else:
                # YYYYMMDDHHMM.SS → touch -t
                cmd = f'touch -t {timestamp} "{path}"'
        else:
            cmd = f'touch "{path}"'
        return self.执行shell(serial, cmd, timeout=30)

    def _base_cmd(self, serial=None):
        cmd = [self.adb_path]
        if serial:
            cmd += ['-s', serial]
        return cmd
