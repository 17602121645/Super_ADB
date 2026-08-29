# -*- coding: utf-8 -*-
"""
主动 mDNS 查询（绕开多播分发竞争）
==================================

背景：Windows 上多个进程（豆包、Edge、系统服务等）同时监听 5353 端口时，
局域网多播包会被选择性分发，程序被动接收经常收不到手机广播的
_adb-tls-pairing / _adb-tls-connect 服务（而本机 loopback 多播不受影响）。

解决：本模块主动发送带 QU 位（unicast response 请求）的 PTR 查询：
  - 手机收到 QU 查询后按 mDNS 规范以【单播】方式响应到查询源端口，
    单播不受多播分发竞争影响，因此一定能收到。
  - 查询可多播发送（找配对服务，手机 IP 未知）或单播发送（取 connect 端口）。

用法：
  query_mdns(PAIRING_TYPE)                    -> 多播查询，返回 [(name, ip, port)]
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


def _build_query(type_name):
    """构造 PTR 查询包（QU 位，请求单播响应）。"""
    header = struct.pack('>HHHHHH', 0x0000, 0x0000, 1, 0, 0, 0)
    question = _encode_name(type_name) + struct.pack('>HH', TYPE_PTR, CLASS_IN_QU)
    return header + question


def _parse_response(data):
    """解析 DNS 响应，返回 {'ptr': [(name, target)], 'srv': {target: port}, 'a': {target: ip}}"""
    try:
        msg = DNSIncoming(data)
    except Exception:
        return None
    out = {'ptr': [], 'srv': {}, 'a': {}, 'txt': {}}
    for rec in msg.answers():
        try:
            name = str(rec.name)
            if isinstance(rec, DNSPointer):          # PTR
                out['ptr'].append((name, str(rec.target)))
            elif isinstance(rec, DNSService):        # SRV
                out['srv'][str(rec.target)] = rec.port
            elif isinstance(rec, DNSAddress):        # A
                if rec.type == TYPE_A:
                    out['a'][name] = socket.inet_ntoa(bytes(rec.address))
            elif isinstance(rec, DNSText):           # TXT
                out['txt'][name] = rec.text
        except Exception:
            continue
    return out


def query_mdns(type_name, target_ip=None, timeout=3.0):
    """主动查询指定 mDNS 服务类型。

    Args:
        type_name: 服务类型，如 '_adb-tls-connect._tcp.local.'
        target_ip: None 表示多播查询（找手机配对服务）；
                   传手机 IP 表示单播查询（取该手机的 connect 端口）。
        timeout: 总超时秒数。

    Returns:
        [(服务名, ip, port)]，去重。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        sock.bind(('', 0))   # 临时端口，接收单播响应
    except Exception:
        pass
    pkt = _build_query(type_name)
    results = []
    seen = set()
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                if target_ip:
                    # 单播查询手机：直接发到手机 5353
                    sock.sendto(pkt, (target_ip, MCAST_PORT))
                else:
                    # 多播查询：发到 mDNS 多播组
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                    sock.sendto(pkt, (MCAST_GRP, MCAST_PORT))
            except Exception:
                pass
            # 收响应（单播响应必达查询源端口）
            t1 = time.time() + 0.8
            while time.time() < t1 and time.time() < deadline:
                try:
                    data, addr = sock.recvfrom(8192)
                except socket.timeout:
                    break
                parsed = _parse_response(data)
                if not parsed:
                    continue
                src_ip = addr[0]
                for name, target in parsed['ptr']:
                    port = parsed['srv'].get(target)
                    ip = parsed['a'].get(target) or src_ip
                    if port and (name, port) not in seen:
                        seen.add((name, port))
                        results.append((name, ip, port))
            if results:
                break
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return results


if __name__ == '__main__':
    print('自测：多播查询 pairing 与 connect（6 秒）...')
    t0 = time.time()
    r1 = query_mdns('_adb-tls-pairing._tcp.local.', timeout=3)
    print('pairing 结果:', r1)
    r2 = query_mdns('_adb-tls-connect._tcp.local.', timeout=3)
    print('connect 结果:', r2)
    print('耗时 %.1fs' % (time.time() - t0))
