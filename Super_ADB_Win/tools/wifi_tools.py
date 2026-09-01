# -*- coding: utf-8 -*-
"""
本机已保存 WiFi 配置及密码读取（Windows / netsh wlan）
=====================================================

纯逻辑层，**不依赖 Qt**，可单独运行：
    python wifi_tools.py            # 表格输出（密码掩码）
    python wifi_tools.py --plain    # 明文密码
    python wifi_tools.py --json     # JSON
    python wifi_tools.py --doctor   # 环境诊断（排查读不到的原因）

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

实现要点（都是踩过坑的）：
  1. netsh 走控制台代码页输出，**不能写死 encoding**，否则中文关键字解成乱码 → 全部匹配失败。
  2. 中/英文 Windows 字段名不同，必须两套关键字都匹配，否则英文系统一个都读不出来。
  3. `netsh wlan show profiles` 同时有「所有用户配置文件」和「当前用户配置文件」两类，
     只匹配前者会漏掉一部分 WiFi。
  4. 取值时**不能用 strip()**：SSID 本身可能以空格开头（真实存在），
     strip 后拿去查 netsh 会报 Profile not found。
  5. 密码行 `line.split(':')[1][1:-1]` 这种写法会把密码最后一位切掉。
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
    """执行命令并返回解码后的 stdout。失败抛 RuntimeError。"""
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
def list_profiles():
    """返回本机已保存的全部 WiFi 配置文件名称（含「当前用户配置文件」）。"""
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
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def diagnose():
    """逐项体检，返回 [(level, title, detail)]，level ∈ ok / warn / error。"""
    items = []

    # 1. 操作系统
    if sys.platform != "win32":
        items.append(("error", "操作系统不支持",
                      "该功能依赖 Windows 的 netsh wlan 命令，"
                      "macOS / Linux 无法使用。"))
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
