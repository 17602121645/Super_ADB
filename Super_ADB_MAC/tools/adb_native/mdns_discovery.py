# -*- coding: utf-8 -*-
"""
Android 无线调试 mDNS 服务发现助手（对应官方 adb 机制）
=========================================================

背景（AOSP adb_wifi.md + 官方 adb 行为）：
  - 手机开启「无线调试」后，设备端（adbd / Framework）会同时广播三类 mDNS 服务：
      _adb._tcp             传统 `adb tcpip` 起的服务（固定 5555）
      _adb-tls-pairing._tcp 配对服务（一次性，配完即失效）
      _adb-tls-connect._tcp adbd 的 TLS 服务端口 = **真实调试端口（随机）**
  - 官方 adb server 启动后持续浏览这三类服务，并对已配对设备的
    _adb-tls-connect 实例自动执行 connect（ADB_MDNS_AUTO_CONNECT=adb-tls-connect）。
  - 因此「调试端口」不应写死 5555，而应从 _adb-tls-connect 服务解析。

本模块提供模块级单例 _ADB_MDNS：
  - ensure_running()：启动持续浏览（幂等、线程安全）
  - get_connect_port(ip, timeout=0)：返回该 IP 的真实调试端口；缓存无且
    timeout>0 时最多等待 timeout 秒（请在后台线程使用）
  - stop()：释放资源（应用退出时调用）
"""

import socket
import threading
import time

try:
    from zeroconf import ServiceBrowser, Zeroconf
    _ZEROCONF_AVAILABLE = True
except ImportError:
    # zeroconf 未安装时优雅降级：mDNS 发现不可用，调用方走手动输入 IP/端口 的 fallback
    ServiceBrowser = None
    Zeroconf = None
    _ZEROCONF_AVAILABLE = False

PAIRING_TYPE = '_adb-tls-pairing._tcp.local.'
CONNECT_TYPE = '_adb-tls-connect._tcp.local.'

# 调试日志（写入 Super_ADB_Win/mdns_debug.log，用于排查无线调试自动连接）
_DEBUG_LOG = True


def _debug_log(msg):
    """把诊断信息打印到控制台（不影响正常流程）。"""
    if not _DEBUG_LOG:
        return
    try:
        print('[mdns] %s' % msg, flush=True)
    except Exception:
        pass


def _lan_ip_hint():
    """获取本机局域网 IPv4，用于挑选与 PC 同网段的手机地址。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        try:
            s.close()
        except Exception:
            pass


def _extract_ipv4(info, lan_ip):
    """从 ServiceInfo 提取手机 IPv4（优先与 PC 同网段）。"""
    candidates = []
    raw = getattr(info, 'addresses', None) or []
    for a in raw:
        if isinstance(a, (bytes, bytearray)) and len(a) == 4:
            candidates.append(socket.inet_ntoa(bytes(a)))
    ips = getattr(info, 'ip_addresses', None) or []
    for a in ips:
        s = str(a)
        if ':' not in s:                 # 只要 IPv4
            candidates.append(s)
    if not candidates:
        return None
    if lan_ip:
        subnet = '.'.join(lan_ip.split('.')[:3])
        for c in candidates:
            if c.startswith(subnet + '.'):
                return c
    return candidates[0]


class _CollectListener:
    """收集 _adb-tls-pairing / _adb-tls-connect 服务的 ip->端口 映射。"""

    def __init__(self, cache):
        self._cache = cache

    def add_service(self, zc, type_, name):
        self._update(zc, type_, name)

    def update_service(self, zc, type_, name):
        # 首次发现后 ServiceBrowser 也会回调 update，复用 add 逻辑即可
        self._update(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass

    def _update(self, zc, type_, name):
        try:
            info = zc.get_service_info(type_, name)
        except Exception:
            return
        if not info:
            return
        ip = _extract_ipv4(info, _lan_ip_hint())
        if not ip:
            return
        with self._cache._lock:
            if type_ == CONNECT_TYPE:
                self._cache._connect_ports[ip] = info.port
                _debug_log(f'[listener] connect 服务: {name} ip={ip} port={info.port}')
            elif type_ == PAIRING_TYPE:
                self._cache._pairing_ports[name] = (ip, info.port)
                self._cache._notify_pairing(name, ip, info.port)
                _debug_log(f'[listener] pairing 服务: {name} ip={ip} port={info.port}')


class _AdbMdnsCache:
    """模块级单例：持续浏览 adb 无线调试 mDNS 服务并缓存端口。"""

    def __init__(self):
        self._lock = threading.RLock()   # 可重入锁：_notify_pairing 在锁内回调需重入
        self._zc = None
        self._browsers = []
        self._listeners = []
        self._connect_ports = {}   # ip -> 真实调试端口
        self._pairing_ports = {}   # 服务实例名 -> (ip, 配对端口)
        self._pairing_callbacks = []   # 配对服务发现回调

    def ensure_running(self):
        with self._lock:
            if not _ZEROCONF_AVAILABLE:
                return
            if self._zc is not None:
                return
            try:
                zc = Zeroconf()
                _debug_log('[ensure_running] Zeroconf() 创建成功')
            except Exception as e:
                _debug_log(f'[ensure_running] Zeroconf() 创建失败: {e}')
                self._zc = None
                return
            self._zc = zc
            try:
                listeners = [_CollectListener(self), _CollectListener(self)]
                self._listeners = listeners
                self._browsers = [
                    ServiceBrowser(zc, CONNECT_TYPE, listeners[0]),
                    ServiceBrowser(zc, PAIRING_TYPE, listeners[1]),
                ]
                _debug_log('[ensure_running] ServiceBrowser 启动成功 '
                           f'(connect={CONNECT_TYPE}, pairing={PAIRING_TYPE})')
            except Exception as e:
                _debug_log(f'[ensure_running] ServiceBrowser 启动失败: {e}')
                self._browsers = []
                self._listeners = []
                try:
                    zc.close()
                except Exception:
                    pass
                self._zc = None

    def stop(self):
        with self._lock:
            for b in self._browsers:
                try:
                    b.cancel()
                except Exception:
                    pass
            self._browsers = []
            if self._zc is not None:
                try:
                    self._zc.close()
                except Exception:
                    pass
                self._zc = None
            self._listeners = []

    def register_pairing_listener(self, cb):
        with self._lock:
            if cb not in self._pairing_callbacks:
                self._pairing_callbacks.append(cb)

    def unregister_pairing_listener(self, cb):
        with self._lock:
            if cb in self._pairing_callbacks:
                self._pairing_callbacks.remove(cb)

    def _notify_pairing(self, name, ip, port):
        with self._lock:
            cbs = list(self._pairing_callbacks)
        for cb in cbs:
            try:
                cb(name, ip, port)
            except Exception:
                pass

    def peek(self, ip):
        with self._lock:
            return self._connect_ports.get(ip)

    def get_connect_port(self, ip, timeout=0.0):
        """返回 ip 的 _adb-tls-connect 调试端口；无缓存且 timeout>0 时等待解析。"""
        if not ip:
            return None
        self.ensure_running()
        p = self.peek(ip)
        if p or timeout <= 0:
            _debug_log(f'[get_connect_port] ip={ip} 立即返回={p}')
            return p
        deadline = time.time() + timeout
        # ★ 主动 QU 查询兜底：绕开局域网多播分发竞争（豆包/Edge 等抢占 5353）
        try:
            from tools.adb_native.mdns_active_query import query_mdns
            _debug_log(f'[get_connect_port] ip={ip} 主动多播查询 connect ...')
            for _name, _ip, _port in query_mdns(CONNECT_TYPE, target_ip=ip,
                                                timeout=min(timeout, 5)):
                if _ip == ip and _port:
                    with self._lock:
                        self._connect_ports[ip] = _port
                    _debug_log(f'[get_connect_port] ip={ip} 主动查询拿到端口={_port}')
                    return _port
        except Exception as e:
            _debug_log(f'[get_connect_port] 主动查询异常: {e}')
        while time.time() < deadline:
            time.sleep(0.2)
            p = self.peek(ip)
            if p:
                _debug_log(f'[get_connect_port] ip={ip} 等待后返回={p}')
                return p
        _debug_log(f'[get_connect_port] ip={ip} 超时无，最终={self.peek(ip)}')
        return self.peek(ip)


# 模块级单例（整个应用生命周期常驻，等价于官方 adb server 的 mdns 浏览）
_ADB_MDNS = _AdbMdnsCache()


def ensure_running():
    """确保 mDNS 浏览已启动（幂等；无网络时静默失败，调用方走 fallback）。"""
    _ADB_MDNS.ensure_running()


def get_connect_port(ip, timeout=0.0):
    """获取指定 IP 的真实调试端口（_adb-tls-connect 服务），无则返回 None。

    timeout>0 时会阻塞等待 mDNS 解析，适合在后台线程调用；缓存已有时立即返回。
    """
    return _ADB_MDNS.get_connect_port(ip, timeout=timeout)


def stop():
    """停止 mDNS 浏览并释放资源（应用退出时调用）。"""
    _ADB_MDNS.stop()


def register_pairing_listener(cb):
    """注册配对服务发现回调 cb(name, ip, port)。"""
    _ADB_MDNS.register_pairing_listener(cb)


def unregister_pairing_listener(cb):
    """反注册配对服务发现回调。"""
    _ADB_MDNS.unregister_pairing_listener(cb)
