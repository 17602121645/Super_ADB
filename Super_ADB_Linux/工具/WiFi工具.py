# -*- coding: utf-8 -*-
"""
本机已保存 WiFi 配置及密码读取（跨平台：Windows / netsh wlan，macOS / security）
================================================================================

纯逻辑层，**不依赖 Qt**，可单独运行：
    python WiFi工具.py            # 表格输出（密码掩码）
    python WiFi工具.py --plain    # 明文密码
    python WiFi工具.py --json     # JSON
    python WiFi工具.py --doctor   # 环境诊断（排查读不到的原因）

对外 API：
    list_profiles()          -> ['CMCC-1234', 'TP-LINK_A8']
    get_profile_detail(ssid) -> dict
    collect_all(workers=8)   -> [dict, ...]
    diagnose()               -> [(level, title, detail), ...]

detail 字典结构::

    {
        'ssid':     'CMCC-1234',
        'password': 'abc12345',   # str=取到；''=无密码(开放/企业)；None=失败
        'auth':     'WPA2-个人',
        'cipher':   'CCMP',
        'open':     False,
        'reason':   None,         # password 非明文时的中文原因
        'error':    None,         # 执行层错误
    }

Windows 实现要点（都是踩过坑的）：
  1. netsh 走控制台代码页输出，**不能写死 encoding**，否则中文关键字解成乱码 → 全部匹配失败。
  2. 中/英文 Windows 字段名不同，必须两套关键字都匹配，否则英文系统一个都读不出来。
  3. `netsh wlan show profiles` 同时有「所有用户配置文件」和「当前用户配置文件」两类，
     只匹配前者会漏掉一部分 WiFi。
  4. 取值时**不能用 strip()**：SSID 本身可能以空格开头（真实存在），
     strip 后拿去查 netsh 会报 Profile not found。
  5. 密码行 `line.split(':')[1][1:-1]` 这种写法会把密码最后一位切掉。

macOS 实现要点：
  1. 列出首选网络：`networksetup -listpreferredwirelessnetworks <iface>`
  2. 读取密码：`security find-generic-password -wa "<SSID>"`（需钥匙串授权，会弹系统对话框）
  3. 无线接口名通过 `networksetup -listallhardwareports` 探测（通常是 en0/en1）
  4. 认证/加密类型通过 `networksetup -getpreferredwirelessnetworks` 或 `system_profiler` 获取
"""

import json
import locale
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── GUI 中调用时避免弹出黑色控制台窗口 ──
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ── netsh 字段名（按「冒号前的 key」精确匹配，中/英文 Windows 兼容） ──
_K_PROFILE = ("所有用户配置文件", "当前用户配置文件",
              "All User Profile", "Current User Profile")
_K_PASSWORD = ("关键内容", "Key Content")
_K_AUTH = ("身份验证", "Authentication")
_K_CIPHER = ("密码", "Cipher")          # 中文里「密码」其实是加密算法(CCMP/TKIP)
_K_KEYSTATE = ("安全密钥", "Security key")

# ── 值关键字 ──
_V_OPEN = ("开放式", "开放", "Open", "None", "无")
_V_ABSENT = ("不存在", "缺席", "Absent")
_V_ENTERPRISE = ("企业", "Enterprise", "802.1X")

# 解码校验用（命中任一即认为该编码正确）
_DECODE_MARKERS = _K_PROFILE + _K_PASSWORD + _K_AUTH


# ══════════════════════════════════════════════════════════════════
# 内部工具
# ══════════════════════════════════════════════════════════════════
def _decode(raw, markers=()):
    """按候选编码解码 netsh 原始字节。

    gbk 几乎能解码任意字节（不报错但结果是乱码），所以额外用关键字命中做校验。
    """
    try:
        preferred = locale.getpreferredencoding(False)
    except Exception:
        preferred = None
    candidates = [e for e in (preferred, "utf-8", "gbk", "cp936", "utf-16-le") if e]

    first_ok = None
    for enc in dict.fromkeys(candidates):          # 去重且保序
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if first_ok is None:
            first_ok = text
        if not markers or any(m in text for m in markers):
            return text
    return first_ok if first_ok is not None else raw.decode("utf-8", errors="replace")


def _run(cmd, markers=(), timeout=15):
    """执行命令并返回解码后的 stdout。失败抛 RuntimeError。（Windows netsh 专用）"""
    if sys.platform != "win32":
        raise RuntimeError("该功能依赖 Windows 的 netsh 命令，当前系统不支持")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,                    # 拿字节，编码自行判断
            creationflags=_CREATE_NO_WINDOW,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"找不到命令：{cmd[0]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("命令执行超时")

    stdout = _decode(proc.stdout or b"", markers)
    if proc.returncode != 0:
        stderr = _decode(proc.stderr or b"").strip()
        raise RuntimeError(stderr or stdout.strip() or f"返回码 {proc.returncode}")
    return stdout


# ══════════════════════════════════════════════════════════════════
# macOS 专用实现
# ══════════════════════════════════════════════════════════════════
def _mac_run(cmd, timeout=15):
    """执行 macOS 命令并返回 stdout 文本。失败抛 RuntimeError。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"找不到命令：{cmd[0]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("命令执行超时")
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(stderr or (proc.stdout or "").strip() or f"返回码 {proc.returncode}")
    return proc.stdout or ""


def _mac_find_wifi_interface():
    """探测 macOS 无线网卡接口名（如 en0 / en1），失败返回 None。"""
    try:
        out = _mac_run(['networksetup', '-listallhardwareports'])
    except RuntimeError:
        return None
    # 解析 Hardware Port / Device / Ethernet Address 三段一组
    lines = out.splitlines()
    current_port = None
    for line in lines:
        line = line.strip()
        if line.startswith('Hardware Port:'):
            current_port = line.split(':', 1)[1].strip()
        elif line.startswith('Device:') and current_port:
            device = line.split(':', 1)[1].strip()
            if 'Wi-Fi' in current_port or 'AirPort' in current_port or 'en' in device:
                return device
            current_port = None
    # 兜底：尝试常见接口名
    for iface in ('en0', 'en1'):
        try:
            _mac_run(['networksetup', '-getairportnetwork', iface])
            return iface
        except RuntimeError:
            continue
    return None


def _mac_list_profiles(iface=None):
    """macOS：列出首选 WiFi 网络 SSID 列表。"""
    if iface is None:
        iface = _mac_find_wifi_interface()
    if iface is None:
        raise RuntimeError("未找到无线网卡接口")
    out = _mac_run(['networksetup', '-listpreferredwirelessnetworks', iface])
    names = []
    for line in out.splitlines():
        # 第一行是 "Preferred networks on en0:" 之类的标题，跳过
        if ':' in line and not line.startswith(' '):
            continue
        ssid = line.strip()
        if ssid and ssid not in names:
            names.append(ssid)
    return names


def _mac_get_password(ssid):
    """macOS：通过 security 命令读取钥匙串中保存的 WiFi 密码。
    成功返回密码字符串，失败返回 None（含用户拒绝授权、未保存等）。
    注意：会弹出系统钥匙串授权对话框。
    """
    try:
        out = _mac_run(['security', 'find-generic-password', '-wa', ssid], timeout=10)
        pwd = out.strip()
        return pwd if pwd else None
    except RuntimeError:
        return None


def _mac_get_security_info(ssid, iface=None):
    """macOS：尝试获取 WiFi 的认证/加密类型。
    通过 `networksetup -getpreferredwirelessnetworks` 详细输出或 system_profiler。
    返回 (auth, cipher, open)。
    """
    # macOS 没有像 netsh 那样直接的详细配置查询，简化处理：
    # 能读到密码说明是个人级加密（WPA/WPA2/WPA3 个人）
    # 读不到密码可能是开放网络或企业级 802.1X
    return '', '', False


# ══════════════════════════════════════════════════════════════════
# Linux 专用实现（NetworkManager / nmcli）
# ══════════════════════════════════════════════════════════════════
def _linux_run(cmd, timeout=15):
    """执行 Linux 命令并返回 stdout 文本。失败抛 RuntimeError。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"找不到命令：{cmd[0]}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("命令执行超时")
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(stderr or (proc.stdout or "").strip() or f"返回码 {proc.returncode}")
    return proc.stdout or ""


def _linux_nmcli_available():
    """检查 nmcli 是否可用（NetworkManager 是否安装并运行）。"""
    try:
        _linux_run(['nmcli', '-t', 'general', 'status'], timeout=5)
        return True
    except RuntimeError:
        return False


def _linux_list_profiles():
    """Linux：通过 nmcli 列出已保存的 WiFi 连接名称列表。

    返回 (names, uuid_map)，其中 uuid_map 是 {name: uuid} 用于后续查密码。
    如果 nmcli 不可用则抛 RuntimeError。
    """
    if not _linux_nmcli_available():
        raise RuntimeError("NetworkManager (nmcli) 不可用，请确认已安装并启用 NetworkManager 服务")
    out = _linux_run(['nmcli', '-t', '-f', 'NAME,UUID,TYPE', 'connection', 'show'], timeout=10)
    names = []
    uuid_map = {}
    for line in out.splitlines():
        # nmcli -t 输出格式：NAME:UUID:TYPE
        parts = line.split(':')
        if len(parts) < 3:
            continue
        name = parts[0]
        uuid = parts[1]
        ctype = parts[2]
        if ctype == '802-11-wireless' and name and name not in names:
            names.append(name)
            uuid_map[name] = uuid
    return names, uuid_map


def _linux_get_password(ssid, uuid_map=None):
    """Linux：通过 nmcli 读取已保存 WiFi 的密码。

    使用 `nmcli -s -g 802-11-wireless-security.psk connection show <uuid>`
    （-s 表示 --show-secrets，需要 polkit 授权或 root 权限）。
    成功返回密码字符串，失败返回 None（含无权限、开放网络、未保存等）。
    """
    if uuid_map is None:
        try:
            _, uuid_map = _linux_list_profiles()
        except RuntimeError:
            return None
    uuid = uuid_map.get(ssid)
    if not uuid:
        return None
    try:
        out = _linux_run([
            'nmcli', '-s', '-g', '802-11-wireless-security.psk',
            'connection', 'show', uuid
        ], timeout=10)
        pwd = out.strip()
        return pwd if pwd else None
    except RuntimeError:
        return None


def _linux_get_security_info(ssid, uuid_map=None):
    """Linux：通过 nmcli 获取 WiFi 的认证/加密类型。
    返回 (auth, cipher, open)。
    """
    if uuid_map is None:
        try:
            _, uuid_map = _linux_list_profiles()
        except RuntimeError:
            return '', '', False
    uuid = uuid_map.get(ssid)
    if not uuid:
        return '', '', False
    try:
        key_mgmt = _linux_run([
            'nmcli', '-g', '802-11-wireless-security.key-mgmt',
            'connection', 'show', uuid
        ], timeout=5).strip()
    except RuntimeError:
        key_mgmt = ''
    auth = ''
    cipher = 'CCMP'
    open_net = False
    if key_mgmt in ('wpa-psk', 'wpa-none'):
        auth = 'WPA/WPA2/WPA3 个人'
    elif key_mgmt == 'wpa-eap':
        auth = 'WPA/WPA2 企业 (802.1X)'
    elif not key_mgmt or key_mgmt == 'none':
        auth = '开放网络'
        open_net = True
        cipher = ''
    return auth, cipher, open_net


def _split_kv(line):
    """把 `    键              : 值` 拆成 (key, value)。

    key 去首尾空白；value **只剥离分隔用的那一个空格**，
    保留值自身的前导空白（SSID 可能以空格开头）。
    """
    for sep in (":", "："):
        idx = line.find(sep)
        if idx != -1:
            key = line[:idx].strip()
            value = line[idx + 1:]
            if value.startswith(" "):
                value = value[1:]
            return key, value.rstrip("\r\n")
    return None, None


def _hit(text, words):
    return any(w in text for w in words)


# ══════════════════════════════════════════════════════════════════
# 对外 API
# ══════════════════════════════════════════════════════════════════
# 缓存 Linux nmcli 查询到的 {name: uuid} 映射，供 get_profile_detail 复用
_linux_uuid_cache = {}


def list_profiles():
    """返回本机已保存的全部 WiFi 配置文件名称（含「当前用户配置文件」）。"""
    global _linux_uuid_cache
    if sys.platform == 'darwin':
        return _mac_list_profiles()
    if sys.platform == 'linux':
        names, uuid_map = _linux_list_profiles()
        _linux_uuid_cache = uuid_map
        return names
    out = _run(["netsh", "wlan", "show", "profiles"], markers=_K_PROFILE)
    names = []
    for line in out.splitlines():
        key, value = _split_kv(line)
        if key in _K_PROFILE and value and value not in names:
            names.append(value)
    return names


def get_profile_detail(ssid):
    """获取单个 WiFi 的详情（含密码 / 认证方式 / 加密算法 / 失败原因）。"""
    info = {"ssid": ssid, "password": None, "auth": "", "cipher": "",
            "open": False, "reason": None, "error": None}

    if sys.platform == 'darwin':
        # macOS：通过 security 命令读取钥匙串密码
        pwd = _mac_get_password(ssid)
        if pwd is not None:
            info["password"] = pwd
            info["auth"] = "WPA/WPA2/WPA3 个人"
            info["cipher"] = "CCMP"
        else:
            # 读不到密码：可能是开放网络、企业级 802.1X、或用户拒绝钥匙串授权
            info["reason"] = "未能从钥匙串读取密码（可能是开放网络、企业级 802.1X，或用户拒绝了钥匙串授权）"
        return info

    if sys.platform == 'linux':
        # Linux：通过 nmcli (NetworkManager) 读取已保存的 WiFi 密码
        uuid_map = _linux_uuid_cache
        if not uuid_map:
            try:
                _, uuid_map = _linux_list_profiles()
            except RuntimeError as e:
                info["error"] = str(e)
                info["reason"] = "NetworkManager 不可用"
                return info
        auth, cipher, open_net = _linux_get_security_info(ssid, uuid_map)
        info["auth"] = auth
        info["cipher"] = cipher
        info["open"] = open_net
        if open_net:
            info["password"] = ""
            info["reason"] = "开放网络，本就无密码"
        else:
            pwd = _linux_get_password(ssid, uuid_map)
            if pwd is not None:
                info["password"] = pwd
            else:
                info["reason"] = ("未能读取密码（可能需要 root 权限或 polkit 授权，"
                                   "或该网络为企业级 802.1X 认证）")
        return info

    try:
        # shell=False 时列表参数会自动正确转义含空格的 SSID，
        # 千万不要手动加引号（引号会变成 SSID 的一部分）。
        out = _run(["netsh", "wlan", "show", "profile", f"name={ssid}", "key=clear"],
                   markers=_K_PASSWORD + _K_AUTH)
    except RuntimeError as e:
        info["error"] = str(e)
        info["reason"] = "读取该配置失败"
        return info

    key_state = ""
    for line in out.splitlines():
        key, value = _split_kv(line)
        if key is None:
            continue
        if key in _K_PASSWORD:
            info["password"] = value
        elif key in _K_AUTH:
            info["auth"] = value.strip()
        elif key in _K_CIPHER:
            info["cipher"] = value.strip()
        elif key in _K_KEYSTATE:
            key_state = value.strip()

    if _hit(info["auth"], _V_OPEN) or _hit(info["cipher"], _V_OPEN):
        info["open"] = True

    # 没拿到明文密码时，判定具体原因（这是"为什么读不到"的核心分流）
    if info["password"] is None:
        if info["open"]:
            info["password"] = ""
            info["reason"] = "开放网络，本就无密码"
        elif _hit(info["auth"], _V_ENTERPRISE):
            info["password"] = ""
            info["reason"] = "企业级 802.1X 认证，用账号/证书登录，不存在共享密码"
        elif _hit(key_state, _V_ABSENT):
            info["password"] = ""
            info["reason"] = "系统未保存该网络的密钥（连接时未勾选自动连接）"
        else:
            info["reason"] = "未输出密钥字段：可能是组策略下发的配置，或需管理员权限"
    return info


def collect_all(workers=8, progress_cb=None, should_stop=None):
    """并发获取全部 WiFi 详情。

    :param workers:     并发线程数
    :param progress_cb: 回调 (done, total, detail)，每完成一条调用一次
    :param should_stop: 无参可调用对象，返回 True 时提前中止
    :return: detail 列表（顺序与 list_profiles 一致）
    """
    profiles = list_profiles()
    total = len(profiles)
    if not total:
        return []

    results = [None] * total
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as pool:
        futures = {pool.submit(get_profile_detail, name): i
                   for i, name in enumerate(profiles)}
        done = 0
        for fut in as_completed(futures):
            if should_stop and should_stop():
                for f in futures:
                    f.cancel()
                break
            idx = futures[fut]
            try:
                detail = fut.result()
            except Exception as e:                      # 兜底，单条失败不影响整体
                detail = {"ssid": profiles[idx], "password": None, "auth": "",
                          "cipher": "", "open": False,
                          "reason": "读取异常", "error": str(e)}
            results[idx] = detail
            done += 1
            if progress_cb:
                progress_cb(done, total, detail)
    return [r for r in results if r is not None]


# ══════════════════════════════════════════════════════════════════
# 环境诊断：为什么有的电脑读不到？
# ══════════════════════════════════════════════════════════════════
def _is_admin():
    if sys.platform == 'darwin':
        # macOS：检查是否为 admin 组用户
        try:
            out = _mac_run(['id', '-Gn'])
            return 'admin' in out.split()
        except Exception:
            return False
    if sys.platform == 'linux':
        # Linux：检查是否为 root 或 sudo 组用户
        try:
            return os.geteuid() == 0
        except Exception:
            return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def diagnose():
    """逐项体检，返回 [(level, title, detail)]，level ∈ ok / warn / error。"""
    items = []

    # 1. 操作系统
    if sys.platform == 'darwin':
        items.append(("ok", "操作系统", "macOS，支持 security 钥匙串读取"))
        # 2. 无线网卡
        iface = _mac_find_wifi_interface()
        if iface:
            items.append(("ok", "无线网卡", f"检测到 WLAN 接口：{iface}"))
        else:
            items.append(("error", "无线网卡", "未检测到 WLAN 接口，可能是台式机或网卡被禁用"))
        # 3. 钥匙串权限
        items.append(("warn", "钥匙串授权",
                      "读取 WiFi 密码时 macOS 会弹出钥匙串授权对话框，"
                      "需输入当前用户密码并点击「始终允许」才能读取。"))
        # 4. 配置文件数量
        try:
            profiles = list_profiles()
            if profiles:
                items.append(("ok", "已保存的 WiFi", f"共 {len(profiles)} 个配置文件"))
            else:
                items.append(("warn", "没有已保存的 WiFi", "本机从未连接过 WiFi，或配置已被清理"))
        except RuntimeError as e:
            items.append(("error", "无法列出配置文件", str(e)))
        return items

    if sys.platform == 'linux':
        # Linux：NetworkManager / nmcli
        if _linux_nmcli_available():
            items.append(("ok", "操作系统", "Linux，NetworkManager (nmcli) 可用"))
        else:
            items.append(("error", "NetworkManager 不可用",
                          "未检测到 nmcli 命令。请安装 NetworkManager 并启用服务，"
                          "或使用其他网络管理器（如 iwd / wicd）时手动查看 WiFi 配置。"))
        # 无线网卡
        try:
            out = _linux_run(['nmcli', '-t', '-f', 'DEVICE,TYPE', 'device', 'status'], timeout=5)
            wifi_ifaces = [line.split(':')[0] for line in out.splitlines()
                          if ':wifi:' in line or line.endswith(':wifi')]
            if wifi_ifaces:
                items.append(("ok", "无线网卡", f"检测到 WLAN 接口：{', '.join(wifi_ifaces)}"))
            else:
                items.append(("warn", "无线网卡", "未检测到 WiFi 设备，可能是台式机或网卡被禁用"))
        except RuntimeError as e:
            items.append(("error", "无线网卡检测失败", str(e)))
        # 权限提示
        if _is_admin():
            items.append(("ok", "运行权限", "root，可读取全部 WiFi 密码"))
        else:
            items.append(("warn", "非 root 权限",
                          "普通用户读取 WiFi 密码可能需要 polkit 授权。"
                          "若无法读取密码，请以 root 身份运行或配置 polkit 规则。"))
        # 配置文件数量
        try:
            profiles = list_profiles()
            if profiles:
                items.append(("ok", "已保存的 WiFi", f"共 {len(profiles)} 个配置文件"))
            else:
                items.append(("warn", "没有已保存的 WiFi", "本机从未连接过 WiFi，或配置已被清理"))
        except RuntimeError as e:
            items.append(("error", "无法列出配置文件", str(e)))
        return items

    if sys.platform != "win32":
        items.append(("error", "操作系统不支持",
                      "该功能依赖 Windows 的 netsh wlan 命令或 macOS 的 security 命令，"
                      "当前系统无法使用。"))
        return items
    items.append(("ok", "操作系统", "Windows，支持 netsh wlan 命令"))

    # 2. WLAN AutoConfig 服务（wlansvc）
    try:
        out = _run(["sc", "query", "wlansvc"], timeout=8)
        if "RUNNING" in out.upper() or "正在运行" in out:
            items.append(("ok", "WLAN AutoConfig 服务", "wlansvc 正在运行"))
        else:
            items.append(("error", "WLAN AutoConfig 服务未运行",
                          "服务 wlansvc 已停止，netsh wlan 全部命令都会失败。"
                          "请在「服务」中启动 WLAN AutoConfig。"))
    except RuntimeError as e:
        items.append(("error", "WLAN AutoConfig 服务异常",
                      f"查询 wlansvc 失败：{e}。台式机若无无线网卡，该服务通常未安装。"))

    # 3. 无线网卡
    try:
        out = _run(["netsh", "wlan", "show", "interfaces"], timeout=8)
        if "GUID" in out or "名称" in out or "Name" in out:
            items.append(("ok", "无线网卡", "检测到可用的 WLAN 接口"))
        else:
            items.append(("warn", "无线网卡", "未检测到 WLAN 接口，可能是台式机或网卡被禁用"))
    except RuntimeError as e:
        items.append(("error", "无线网卡不可用",
                      f"{e}。没有无线网卡的机器（多数台式机）读不到任何 WiFi 配置。"))

    # 4. 管理员权限
    if _is_admin():
        items.append(("ok", "运行权限", "管理员，可读取全部配置文件的明文密钥"))
    else:
        items.append(("warn", "非管理员权限",
                      "普通权限下能读到自己保存的 WiFi，但组策略下发/其他用户的"
                      "配置可能无法输出明文密钥。以管理员身份重开可提高成功率。"))

    # 5. 配置文件数量
    try:
        profiles = list_profiles()
        if profiles:
            items.append(("ok", "已保存的 WiFi", f"共 {len(profiles)} 个配置文件"))
        else:
            items.append(("warn", "没有已保存的 WiFi",
                          "本机从未连接过 WiFi，或配置已被清理"
                          "（重装系统、用过网络重置）。"))
    except RuntimeError as e:
        items.append(("error", "无法列出配置文件", str(e)))

    # 6. 输出语言（历史坑：只匹配中文关键字的实现会在英文系统上全军覆没）
    try:
        out = _run(["netsh", "wlan", "show", "profiles"])
        if any(k in out for k in ("All User Profile", "Current User Profile")):
            lang = "英文"
        elif any(k in out for k in ("所有用户配置文件", "当前用户配置文件")):
            lang = "中文"
        else:
            lang = "未识别"
        items.append(("ok" if lang != "未识别" else "warn",
                      f"netsh 输出语言：{lang}",
                      "本工具已同时兼容中/英文字段名" if lang != "未识别"
                      else "字段名无法识别，可能是小语种系统或编码异常"))
    except RuntimeError:
        pass

    return items


# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════
def _mask(pwd):
    if not pwd:
        return ""
    if len(pwd) <= 2:
        return "*" * len(pwd)
    return pwd[0] + "*" * (len(pwd) - 2) + pwd[-1]


def _cli():
    argv = sys.argv[1:]
    if "--doctor" in argv:
        for level, title, detail in diagnose():
            flag = {"ok": "[ OK ]", "warn": "[WARN]", "error": "[FAIL]"}[level]
            print(f"{flag} {title}\n       {detail}")
        return

    data = collect_all()
    if "--json" in argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    plain = "--plain" in argv
    print(f"共 {len(data)} 个已保存的 WiFi 配置\n")
    print(f"{'SSID':<32} {'密码':<24} {'认证方式':<20} 说明")
    print("-" * 100)
    for d in data:
        pwd = d["password"]
        shown = (pwd if plain else _mask(pwd)) if pwd else ""
        note = d["reason"] or ""
        print(f"{d['ssid']:<32} {shown:<24} {d['auth']:<20} {note}")
    if not plain:
        print("\n（密码默认掩码显示，加 --plain 查看明文）")


if __name__ == "__main__":
    _cli()
