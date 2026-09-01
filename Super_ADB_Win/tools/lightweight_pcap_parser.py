# -*- coding: utf-8 -*-
"""
轻量 pcap/pcapng 解析器 — 纯 Python 实现，无第三方依赖。
替代 Scapy 的 pcap 文件读取 + 协议头解析能力。

支持:
  - pcap (magic: d4c3b2a1 / a1b2c3d4)
  - pcapng (magic: 0a0d0d0a)
  - 链路类型: Ethernet(1), Raw IP(101), Linux SLL(113), Linux SLL2(276)
  - IPv4 / IPv6
  - TCP / UDP (含 flags, payload)
  - DNS (基础解析: transaction ID, queries, answers)
  - TLS ClientHello SNI 提取

API 设计模仿 Scapy:
  pkt.haslayer(IP)       -> bool
  pkt.haslayer(TCP)      -> bool
  pkt.haslayer(Raw)      -> bool
  pkt.haslayer(DNS)      -> bool
  pkt.haslayer(DNSQR)    -> bool
  pkt.haslayer(DNSRR)    -> bool
  pkt[IP].src / .dst / .type / .proto
  pkt[IPv6].src / .dst
  pkt[TCP].sport / .dport / .flags / .seq / .ack
  pkt[UDP].sport / .dport / .length
  pkt[Raw].load          -> bytes
  pkt[DNS].id            -> int
  pkt[DNSQR].qname       -> str
  pkt[DNSRR].rdata       -> str
  pkt.time               -> float (timestamp)
"""

import struct
import os
from dataclasses import dataclass, field
from typing import Optional, Iterator, Tuple, List


# ──────────────────────── 协议常量 ────────────────────────

LINK_ETHERNET = 1
LINK_RAW_IP = 101
LINK_SLL = 113
LINK_SLL2 = 276

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_IPV6 = 0x86DD
ETHERTYPE_ARP = 0x0806

IP_PROTO_ICMP = 1
IP_PROTO_TCP = 6
IP_PROTO_UDP = 17
IP_PROTO_IGMP = 2

TLS_CONTENT_HANDSHAKE = 22
TLS_HANDSHAKE_CLIENT_HELLO = 1


# ──────────────────────── 轻量 Layer 类 ────────────────────────

@dataclass
class _BaseLayer:
    """所有协议层的基类。"""
    pass


@dataclass
class IP(_BaseLayer):
    version: int = 4
    src: str = ''
    dst: str = ''
    proto: int = 0
    ttl: int = 0
    total_length: int = 0
    raw: bytes = b''


@dataclass
class IPv6(_BaseLayer):
    src: str = ''
    dst: str = ''
    next_header: int = 0
    payload_length: int = 0
    raw: bytes = b''


@dataclass
class TCP(_BaseLayer):
    sport: int = 0
    dport: int = 0
    seq: int = 0
    ack: int = 0
    flags: int = 0
    window: int = 0
    payload_offset: int = 0  # 数据偏移（从 TCP 头结束到 payload 的字节数）
    header_length: int = 20
    raw: bytes = b''


@dataclass
class UDP(_BaseLayer):
    sport: int = 0
    dport: int = 0
    length: int = 0
    raw: bytes = b''


@dataclass
class Raw(_BaseLayer):
    load: bytes = b''


@dataclass
class DNSQR(_BaseLayer):
    qname: str = ''
    qtype: int = 0
    qclass: int = 0


@dataclass
class DNSRR(_BaseLayer):
    rname: str = ''
    rtype: int = 0
    rclass: int = 0
    ttl: int = 0
    rdata: str = ''


@dataclass
class DNS(_BaseLayer):
    id: int = 0
    qr: bool = False  # True=response, False=query
    opcode: int = 0
    aa: bool = False
    tc: bool = False
    rd: bool = False
    ra: bool = False
    rcode: int = 0
    queries: List[DNSQR] = field(default_factory=list)
    answers: List[DNSRR] = field(default_factory=list)


# ──────────────────────── Packet 类 ────────────────────────

class Packet:
    """模拟 Scapy Packet 的轻量实现。"""

    def __init__(self, timestamp: float, layers: dict):
        self.time = timestamp
        self._layers = layers  # {IP: ip_obj, TCP: tcp_obj, Raw: raw_obj, ...}
        # 预存 payload 字节方便访问
        self._payload = layers.get(Raw, Raw()).load

    def haslayer(self, layer_type: type) -> bool:
        return layer_type in self._layers

    def __getitem__(self, layer_type: type):
        if layer_type in self._layers:
            return self._layers[layer_type]
        raise KeyError(f'Layer {layer_type.__name__} not found')

    def getlayer(self, layer_type: type, default=None):
        return self._layers.get(layer_type, default)

    @property
    def payload(self) -> bytes:
        return self._payload


# ──────────────────────── 解析辅助函数 ────────────────────────

def _ip_to_str(ip_bytes: bytes) -> str:
    """4 字节转 IPv4 地址字符串。"""
    return '.'.join(str(b) for b in ip_bytes)


def _ipv6_to_str(ip_bytes: bytes) -> str:
    """16 字节转 IPv6 地址字符串（简化）。"""
    parts = []
    for i in range(0, 16, 2):
        parts.append(f'{ip_bytes[i]:02x}{ip_bytes[i+1]:02x}')
    return ':'.join(parts)


def _parse_dns_name(data: bytes, offset: int = 0) -> Tuple[str, int]:
    """解析 DNS 域名，返回 (name, new_offset)。支持压缩指针。"""
    labels = []
    jumped = False
    orig_offset = offset
    max_jumps = 10
    jumps = 0
    while offset < len(data):
        if jumps > max_jumps:
            break
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        # 压缩指针 (0xc0)
        if length & 0xc0 == 0xc0:
            if offset + 1 < len(data):
                pointer = ((length & 0x3f) << 8) | data[offset + 1]
                offset = pointer
                jumped = True
                jumps += 1
                continue
            else:
                break
        else:
            if offset + 1 + length > len(data):
                break
            labels.append(data[offset + 1:offset + 1 + length].decode('utf-8', errors='replace'))
            offset += 1 + length
    name = '.'.join(labels)
    if not jumped:
        orig_offset = offset
    return name, orig_offset


def _parse_dns(data: bytes) -> Optional[DNS]:
    """解析 DNS 报文。"""
    if len(data) < 12:
        return None
    try:
        id_val, flags, qdcount, ancount, nscount, arcount = struct.unpack('!HHHHHH', data[:12])
        dns = DNS(
            id=id_val,
            qr=bool(flags & 0x8000),
            opcode=(flags >> 11) & 0xf,
            aa=bool(flags & 0x0400),
            tc=bool(flags & 0x0200),
            rd=bool(flags & 0x0100),
            ra=bool(flags & 0x80),
            rcode=flags & 0xf,
        )
        offset = 12

        # Questions
        for _ in range(qdcount):
            if offset >= len(data):
                break
            qname, offset = _parse_dns_name(data, offset)
            if offset + 4 > len(data):
                break
            qtype, qclass = struct.unpack('!HH', data[offset:offset + 4])
            offset += 4
            dns.queries.append(DNSQR(qname=qname, qtype=qtype, qclass=qclass))

        # Answers
        for _ in range(ancount):
            if offset >= len(data):
                break
            rname, offset = _parse_dns_name(data, offset)
            if offset + 10 > len(data):
                break
            rtype, rclass, ttl, rdlength = struct.unpack('!HHIH', data[offset:offset + 10])
            offset += 10
            if offset + rdlength > len(data):
                rdlength = len(data) - offset
            rdata_raw = data[offset:offset + rdlength]
            offset += rdlength
            # 简单解析 A 记录
            rdata = ''
            if rtype == 1 and rdlength == 4:  # A
                rdata = _ip_to_str(rdata_raw)
            elif rtype == 28 and rdlength == 16:  # AAAA
                rdata = _ipv6_to_str(rdata_raw)
            elif rtype == 5:  # CNAME
                rdata, _ = _parse_dns_name(data, offset - rdlength)
            else:
                rdata = rdata_raw.hex()
            dns.answers.append(DNSRR(rname=rname, rtype=rtype, rclass=rclass, ttl=ttl, rdata=rdata))

        return dns
    except Exception:
        return None


def _parse_tls_client_hello(payload: bytes) -> Optional[str]:
    """从 TLS ClientHello 中提取 SNI。"""
    if len(payload) < 11:
        return None
    # TLS Record: type(1) + version(2) + length(2) + data
    offset = 0
    # 跳过可能的 TLS Record 头部
    if payload[0] == TLS_CONTENT_HANDSHAKE:
        tls_len = struct.unpack('!H', payload[3:5])[0]
        offset = 5
        # Handshake: type(1) + length(3)
        if offset + 4 <= len(payload):
            hs_type = payload[offset]
            hs_len = (payload[offset + 1] << 16) | (payload[offset + 2] << 8) | payload[offset + 3]
            offset += 4
            if hs_type == TLS_HANDSHAKE_CLIENT_HELLO and offset + hs_len <= len(payload):
                # ClientHello: version(2) + random(32)
                offset += 34
                # session_id
                if offset >= len(payload):
                    return None
                sid_len = payload[offset]
                offset += 1 + sid_len
                # cipher_suites
                if offset + 2 > len(payload):
                    return None
                cs_len = struct.unpack('!H', payload[offset:offset + 2])[0]
                offset += 2 + cs_len
                # compression_methods
                if offset >= len(payload):
                    return None
                cm_len = payload[offset]
                offset += 1 + cm_len
                # extensions
                if offset + 2 > len(payload):
                    return None
                ext_len = struct.unpack('!H', payload[offset:offset + 2])[0]
                offset += 2
                ext_end = offset + ext_len
                while offset + 4 <= min(ext_end, len(payload)):
                    ext_type = struct.unpack('!H', payload[offset:offset + 2])[0]
                    ext_size = struct.unpack('!H', payload[offset + 2:offset + 4])[0]
                    offset += 4
                    ext_data = payload[offset:offset + ext_size]
                    if ext_type == 0x0000 and len(ext_data) >= 5:  # SNI
                        # SNI: list_length(2) + entries...
                        list_len = struct.unpack('!H', ext_data[:2])[0]
                        pos = 2
                        while pos + 3 <= len(ext_data) and pos < 2 + list_len:
                            name_type = ext_data[pos]
                            name_len = struct.unpack('!H', ext_data[pos + 1:pos + 3])[0]
                            pos += 3
                            if pos + name_len <= len(ext_data):
                                if name_type == 0:  # host_name
                                    return ext_data[pos:pos + name_len].decode('utf-8', errors='replace')
                                pos += name_len
                            else:
                                break
                    offset += ext_size
                return None
    return None


# ──────────────────────── 核心解析 ────────────────────────

def _parse_ipv4(data: bytes) -> Tuple[Optional[IP], int]:
    """解析 IPv4 头，返回 (IP 对象, 头长度)。不完整返回 (None, 0)。"""
    if len(data) < 20:
        return None, 0
    ver_ihl = data[0]
    version = ver_ihl >> 4
    ihl = ver_ihl & 0xf
    header_len = ihl * 4
    if version != 4 or header_len < 20 or header_len > len(data):
        return None, 0
    total_length = struct.unpack('!H', data[2:4])[0]
    proto = data[9]
    ttl = data[8]
    src = _ip_to_str(data[12:16])
    dst = _ip_to_str(data[16:20])
    ip = IP(version=4, src=src, dst=dst, proto=proto, ttl=ttl,
            total_length=total_length, raw=data[:header_len])
    return ip, header_len


def _parse_ipv6(data: bytes) -> Tuple[Optional[IPv6], int]:
    """解析 IPv6 头。"""
    if len(data) < 40:
        return None, 0
    ver = (data[0] >> 4) & 0xf
    if ver != 6:
        return None, 0
    payload_length = struct.unpack('!H', data[4:6])[0]
    next_header = data[6]
    src = _ipv6_to_str(data[8:24])
    dst = _ipv6_to_str(data[24:40])
    return IPv6(src=src, dst=dst, next_header=next_header,
               payload_length=payload_length, raw=data[:40]), 40


def _parse_tcp(data: bytes) -> Tuple[Optional[TCP], bytes]:
    """解析 TCP 头，返回 (TCP 对象, payload)。"""
    if len(data) < 20:
        return None, b''
    sport, dport, seq, ack_num = struct.unpack('!HHII', data[:12])
    data_offset_byte = data[12]
    header_len = (data_offset_byte >> 4) * 4
    if header_len < 20 or header_len > len(data):
        return None, b''
    flags = data[13]
    window = struct.unpack('!H', data[14:16])[0]
    payload = data[header_len:]
    tcp = TCP(sport=sport, dport=dport, seq=seq, ack=ack_num,
              flags=flags, window=window, header_length=header_len,
              payload_offset=header_len, raw=data[:header_len])
    return tcp, payload


def _parse_udp(data: bytes) -> Tuple[Optional[UDP], bytes]:
    """解析 UDP 头，返回 (UDP 对象, payload)。"""
    if len(data) < 8:
        return None, b''
    sport, dport, length, checksum = struct.unpack('!HHHH', data[:8])
    payload = data[8:]
    return UDP(sport=sport, dport=dport, length=length, raw=data[:8]), payload


def _parse_ethernet(data: bytes) -> Tuple[bytes, int]:
    """解析以太网头，返回 (payload, ethertype)。"""
    if len(data) < 14:
        return b'', 0
    ethertype = struct.unpack('!H', data[12:14])[0]
    payload = data[14:]
    return payload, ethertype


def _parse_sll(data: bytes) -> Tuple[bytes, int]:
    """解析 Linux SLL 头。"""
    if len(data) < 16:
        return b'', 0
    # SLL: packet_type(2) + link_layer_address(2) + link_layer_address_length(2) + ethertype(2)
    # + ... but we just need the ethertype at offset 14
    # Actually SLL format: type(2) + addr_len(2) + addr(8) + ethertype(2)
    # = 14 bytes before ethertype... let me check
    # SLL: uint16_t packet_type; uint16_t link_layer_address_type; uint16_t link_layer_address_length; uint8_t[8] address; uint16_t ethertype
    # Total header: 2+2+2+8+2 = 16 bytes
    if len(data) < 16:
        return b'', 0
    ethertype = struct.unpack('!H', data[14:16])[0]
    payload = data[16:]
    return payload, ethertype


def _parse_sll2(data: bytes) -> Tuple[bytes, int]:
    """解析 Linux SLL2 头。"""
    if len(data) < 20:
        return b'', 0
    # SLL2: length(2) + link_address_type(2) + reserved(2) + ethertype(2) + ...
    # ethertype is at offset 16
    ethertype = struct.unpack('!H', data[16:18])[0]
    payload = data[20:]
    return payload, ethertype


def _parse_packet_layers(data: bytes, link_type: int) -> Optional[Packet]:
    """解析单个数据包的所有协议层。"""
    layers = {}
    raw_payload = b''

    if link_type == LINK_ETHERNET:
        payload, ethertype = _parse_ethernet(data)
    elif link_type == LINK_SLL:
        payload, ethertype = _parse_sll(data)
    elif link_type == LINK_SLL2:
        payload, ethertype = _parse_sll2(data)
    elif link_type == LINK_RAW_IP:
        payload = data
        # 尝试检测是 IPv4 还是 IPv6
        if len(payload) >= 1 and (payload[0] >> 4) == 4:
            ethertype = ETHERTYPE_IPV4
        elif len(payload) >= 1 and (payload[0] >> 4) == 6:
            ethertype = ETHERTYPE_IPV6
        else:
            return None
    else:
        # 不支持的链路类型
        return None

    # IP 层
    ip_data = payload
    if ethertype == ETHERTYPE_IPV4:
        ip_obj, ip_hdr_len = _parse_ipv4(ip_data)
        if ip_obj is None:
            return None
        layers[IP] = ip_obj
        transport_data = ip_data[ip_hdr_len:]
    elif ethertype == ETHERTYPE_IPV6:
        ipv6_obj, ip_hdr_len = _parse_ipv6(ip_data)
        if ipv6_obj is None:
            return None
        layers[IPv6] = ipv6_obj
        transport_data = ip_data[ip_hdr_len:]
        proto = ipv6_obj.next_header
        # 简化：如果有扩展头，跳过它们
        while proto in (0, 60, 115):  # Hop, Dest, Route
            if len(transport_data) < 2:
                break
            hdr_len = (transport_data[1] + 1) * 8 if proto == 0 else 8  # 简化
            proto = transport_data[0]
            if hdr_len >= len(transport_data):
                break
            transport_data = transport_data[hdr_len:]
        ip_obj = IP(version=6, src=ipv6_obj.src, dst=ipv6_obj.dst,
                    proto=proto, raw=b'')
        layers[IP] = ip_obj
    elif ethertype == ETHERTYPE_ARP:
        return None  # 跳过 ARP
    else:
        return None

    # 传输层
    proto = layers[IP].proto
    if proto == IP_PROTO_TCP and len(transport_data) >= 20:
        tcp_obj, tcp_payload = _parse_tcp(transport_data)
        if tcp_obj:
            layers[TCP] = tcp_obj
            raw_payload = tcp_payload
    elif proto == IP_PROTO_UDP and len(transport_data) >= 8:
        udp_obj, udp_payload = _parse_udp(transport_data)
        if udp_obj:
            layers[UDP] = udp_obj
            raw_payload = udp_payload

    # DNS 检测
    if UDP in layers and (layers[UDP].sport == 53 or layers[UDP].dport == 53):
        dns_obj = _parse_dns(raw_payload)
        if dns_obj:
            layers[DNS] = dns_obj
            # 判断查询/响应
            if dns_obj.queries:
                layers[DNSQR] = dns_obj.queries[0]
            if dns_obj.answers:
                layers[DNSRR] = dns_obj.answers[0]

    # Raw payload
    if raw_payload:
        layers[Raw] = Raw(load=raw_payload)

    return Packet(timestamp=0.0, layers=layers)


# ──────────────────────── Pcap 读取器 ────────────────────────

class PcapReader:
    """轻量 pcap/pcapng 文件读取器，模拟 Scapy 的 PcapReader API。"""

    def __init__(self, filepath: str):
        self._filepath = filepath
        self._file = None
        self._link_type = 1
        self._is_pcapng = False
        self._endian = '<'
        self._offset = 0
        self._packet_index = 0
        self._read_header()

    def _read_header(self):
        with open(self._filepath, 'rb') as f:
            magic = f.read(4)

        if magic == b'\xd4\xc3\xb2\xa1':
            self._endian = '<'
            self._is_pcapng = False
        elif magic == b'\xa1\xb2\xc3\xd4':
            self._endian = '>'
            self._is_pcapng = False
        elif magic == b'\x0a\x0d\x0d\x0a':
            self._is_pcapng = True
            self._init_pcapng()
            return
        else:
            raise ValueError(f'Invalid pcap magic: {magic.hex()}')

        with open(self._filepath, 'rb') as f:
            header = f.read(24)
            if len(header) < 24:
                raise ValueError('pcap file too small')
            ver_major, ver_minor, thiszone, sigfigs, snaplen, network = \
                struct.unpack(f'{self._endian}HHiIII', header[4:24])
            self._link_type = network
            self._offset = 24

    def _init_pcapng(self):
        """初始化 pcapng 格式。"""
        with open(self._filepath, 'rb') as f:
            # 跳过 Section Header Block (28 字节)
            f.seek(28)
            # 查找 Interface Description Block
            while True:
                block_header = f.read(8)
                if len(block_header) < 8:
                    break
                block_len, block_type = struct.unpack('<II', block_header)
                if block_type == 0x00000001:  # Interface Description Block
                    body = f.read(min(block_len - 12, 100))
                    if len(body) >= 8:
                        linktype = struct.unpack('<H', body[:2])[0]
                        self._link_type = linktype
                        self._offset = f.tell()
                        return
                    f.seek(block_len - 8, 1)
                elif block_type == 0x0000000A:  # Decryption Secrets Block
                    f.seek(block_len - 8, 1)
                elif block_type == 0x0000000D:  # Custom Block
                    f.seek(block_len - 8, 1)
                elif block_type == 0x0000000E:  # Custom Block (with Interface ID)
                    f.seek(block_len - 8, 1)
                else:
                    f.seek(block_len - 8, 1)
            self._offset = 28  # fallback

    def __iter__(self) -> Iterator[Packet]:
        with open(self._filepath, 'rb') as f:
            f.seek(self._offset)

            if self._is_pcapng:
                yield from self._iter_pcapng(f)
            else:
                yield from self._iter_pcap(f)

    def _iter_pcap(self, f) -> Iterator[Packet]:
        endian = self._endian
        link_type = self._link_type
        while True:
            pkt_header = f.read(16)
            if len(pkt_header) < 16:
                break
            ts_sec, ts_usec, incl_len, orig_len = struct.unpack(f'{endian}IIII', pkt_header)
            if incl_len == 0:
                continue
            pkt_data = f.read(incl_len)
            if len(pkt_data) < incl_len:
                break
            ts = float(ts_sec) + float(ts_usec) / 1_000_000.0
            pkt = _parse_packet_layers(pkt_data, link_type)
            if pkt:
                pkt.time = ts
                yield pkt

    def _iter_pcapng(self, f) -> Iterator[Packet]:
        link_type = self._link_type
        while True:
            block_header = f.read(8)
            if len(block_header) < 8:
                break
            block_len, block_type = struct.unpack('<II', block_header)
            if block_type == 0x00000006:  # Enhanced Packet Block
                body = f.read(block_len - 12)
                if len(body) < 20:
                    f.seek(-4, 1)
                    continue
                # Enhanced Packet Block: Interface ID(4) + Timestamp(8) + Captured Packet Length(4) + Original Packet Length(4) + Packet Data
                interface_id, ts_high, ts_low, cap_len, orig_len = struct.unpack('<IIIIII', body[:24])
                ts = float((ts_high << 32) | ts_low) / 1_000_000.0
                pkt_data = body[24:24 + cap_len]
                if len(pkt_data) >= cap_len:
                    pkt = _parse_packet_layers(pkt_data, link_type)
                    if pkt:
                        pkt.time = ts
                        yield pkt
                # Block terminator (4 bytes)
                f.read(4)
            elif block_type == 0x00000001:  # Interface Description Block - update linktype
                body = f.read(min(block_len - 12, 100))
                if len(body) >= 8:
                    linktype = struct.unpack('<H', body[:2])[0]
                    link_type = linktype
                f.seek(block_len - 8, 1)
            elif block_type == 0x0000000A:  # Decryption Secrets Block
                f.seek(block_len - 8, 1)
            else:
                f.seek(block_len - 8, 1)


# ──────────────────────── 便捷工厂 ────────────────────────

def open_cap(filename: str) -> PcapReader:
    """打开 pcap/pcapng 文件，返回 PcapReader。"""
    return PcapReader(filename)