# -*- coding: utf-8 -*-
"""
主动 mDNS 查询（绕开多播分发竞争）
==================================

背景：Windows 上多个进程（豆包、Edge、系统服务等）同时监听 5353 端口时，
局域网多播包会被选择性分发，程序被动接收经常收不到手机广播的
_adb-tls-pairing / _adb-tls-connect 服务（而本机 loopback 多播不受影响）。

解决：本模块主动发送带 QU 位（unicast response 请求）的 PTR / SRV 查询：
  - 手机收到 QU 查询后按 mDNS 规范以【单播】方式响应到查询源端口，
    单播不受多播分发竞争影响，因此一定能收到。
  - 查询可多播发送（找配对服务，手机 IP 未知）或单播发送（取 connect 端口）。

流程：PTR 查询拿服务名 → 对每个服务名发 SRV 查询拿端口。

用法：
  query_mdns(PAIRING_TYPE)                      -> 多播查询，返回 [(name, ip, port)]
  query_mdns(CONNECT_TYPE, target_ip='1.2.3.4') -> 单播查询手机，返回 [(name, ip, port)]
"""

import socket
import struct
import time

from zeroconf import DNSIncoming
from zeroconf._dns import DNSPointer, DNSService, DNSAddress, DNSText

MCAST_GRP = '224.0.0.251'
MCAST_PORT = 5353
TYPE_PTR = 12
TYPE_SRV = 33
TYPE_TXT = 16
TYPE_A = 1
# QCLASS：IN(1) + QU 位(0x8000)，请求单播响应（RFC 6762 §5.4）
CLASS_IN_QU = 0x8001


def _encode_name(name):
    """DNS 域名编码：'a.b.local.' -> b'\\x01a\\x01b\\x05local\\x00'"""
    out = b''
    for part in name.rstrip('.').split('.'):
        b = part.encode('utf-8')
        if len(b) > 63:
            b = b[:63]
        out += bytes([len(b)]) + b
    return out + b'\x00'


def _build_ptr_query(type_name, qu=True):
    """构造 PTR 查询包。qu=True 带 QU 位（请求单播响应），
    部分实现不响应 QU 查询，需同时发普通查询。"""
    header = struct.pack('>HHHHHH', 0x0000, 0x0000, 1, 0, 0, 0)
    cls = CLASS_IN_QU if qu else 0x0001
    question = _encode_name(type_name) + struct.pack('>HH', TYPE_PTR, cls)
    return header + question


def _build_srv_query(service_name, qu=True):
    """构造 SRV 查询包。"""
    header = struct.pack('>HHHHHH', 0x0000, 0x0000, 1, 0, 0, 0)
    cls = CLASS_IN_QU if qu else 0x0001
    question = _encode_name(service_name) + struct.pack('>HH', TYPE_SRV, cls)
    return header + question


def _parse_response(data):
    """解析 DNS 响应。

    返回 {'ptr': [(name, target)], 'srv': {服务名: port}, 'a': {服务名: ip}}
    其中 srv 的 key 用【服务名】(记录 name)，便于按服务名查端口。
    """
    try:
        msg = DNSIncoming(data)
    except Exception:
        return None
    out = {'ptr': [], 'srv': {}, 'a': {}, 'txt': {}}
    for rec in msg.answers():
        try:
            name = str(rec.name)
            if isinstance(rec, DNSPointer):          # PTR：name=类型名, target=服务名
                out['ptr'].append((name, str(rec.target)))
            elif isinstance(rec, DNSService):        # SRV：name=服务名, port=端口
                out['srv'][name] = rec.port
            elif isinstance(rec, DNSAddress):        # A：name=服务名/主机名, address=IP
                if rec.type == TYPE_A:
                    out['a'][name] = socket.inet_ntoa(bytes(rec.address))
            elif isinstance(rec, DNSText):           # TXT
                out['txt'][name] = rec.text
        except Exception:
            continue
    return out


def _open_sockets():
    """打开查询 socket：s1=绑定5353+多播组(收多播响应)，s2=临时端口(收单播响应)。"""
    s1 = None
    try:
        s1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s1.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s1.bind(('', MCAST_PORT))
        s1.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                      socket.inet_aton(MCAST_GRP) + socket.inet_aton('0.0.0.0'))
        s1.settimeout(0.7)
    except Exception:
        s1 = None
    s2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s2.bind(('', 0))
    s2.settimeout(0.7)
    return s1, s2


def _send(s1, s2, pkt, target_ip):
    """向多播组(及手机单播)发送查询包。"""
    dests = [(MCAST_GRP, MCAST_PORT)]
    if target_ip:
        dests.append((target_ip, MCAST_PORT))
    for sock in (s1, s2):
        if sock is None:
            continue
        for d in dests:
            try:
                sock.sendto(pkt, d)
            except Exception:
                pass


def _recv_all(s1, s2, wait):
    """同时从两个 socket 收集响应。"""
    out = []
    t1 = time.time() + wait
    while time.time() < t1:
        for sock in (s1, s2):
            if sock is None:
                continue
            try:
                data, addr = sock.recvfrom(8192)
                out.append((data, addr))
            except socket.timeout:
                continue
            except Exception:
                continue
    return out


def query_mdns(type_name, target_ip=None, timeout=3.0):
    """主动查询指定 mDNS 服务类型（强化：5353多播 + 临时端口 + PTR/SRV 二段 + QU/非QU + 单播手机）。

    Args:
        type_name: 服务类型，如 '_adb-tls-connect._tcp.local.'
        target_ip: None 表示多播查询（找手机配对服务）；
                   传手机 IP 表示同时单播查询该 IP（取该手机的 connect 端口）。
        timeout: 总超时秒数。

    Returns:
        [(服务名, ip, port)]，去重。
    """
    s1, s2 = _open_sockets()
    try:
        deadline = time.time() + timeout
        names = {}      # 服务名 -> ip
        srv_ports = {}  # 服务名 -> 端口
        # 阶段1：PTR 查询（QU + 非QU + 单播）收集服务名与 IP
        # ★ Android adb mDNS 响应常不带 PTR，直接回 SRV+A+TXT，
        #   故 SRV 记录也要建立服务名（ip 取响应源地址），循环条件同时看 srv_ports。
        t_end = time.time() + max(0.8, timeout * 0.6)
        while time.time() < t_end and time.time() < deadline and not names and not srv_ports:
            _send(s1, s2, _build_ptr_query(type_name, qu=True), target_ip)
            _send(s1, s2, _build_ptr_query(type_name, qu=False), target_ip)
            for data, addr in _recv_all(s1, s2, 0.7):
                p = _parse_response(data)
                if not p:
                    continue
                for _, target in p['ptr']:
                    if target:
                        names.setdefault(target, p['a'].get(target) or addr[0])
                for n, ip in p['a'].items():
                    names.setdefault(n, ip)
                for svc, port in p['srv'].items():
                    srv_ports[svc] = port
                    names.setdefault(svc, addr[0])
        # 阶段2：对每个服务名发 SRV 查询（QU + 非QU + 单播）拿端口
        for name in list(names):
            if name in srv_ports:
                continue
            _send(s1, s2, _build_srv_query(name, qu=True), target_ip)
            _send(s1, s2, _build_srv_query(name, qu=False), target_ip)
            for data, addr in _recv_all(s1, s2, 0.7):
                p = _parse_response(data)
                if p:
                    srv_ports.update(p['srv'])
        # 组装结果
        results = []
        seen = set()
        for name, ip in names.items():
            port = srv_ports.get(name)
            if port and (name, ip, port) not in seen:
                seen.add((name, ip, port))
                results.append((name, ip, port))
        return results
    finally:
        try:
            if s1:
                s1.close()
        except Exception:
            pass
        try:
            s2.close()
        except Exception:
            pass


if __name__ == '__main__':
    print('自测：查询 pairing 与 connect（各 4 秒）...')
    r1 = query_mdns('_adb-tls-pairing._tcp.local.', timeout=4)
    print('pairing:', r1)
    r2 = query_mdns('_adb-tls-connect._tcp.local.', timeout=4)
    print('connect:', r2)
