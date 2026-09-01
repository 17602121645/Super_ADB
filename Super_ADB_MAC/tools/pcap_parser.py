# -*- coding: utf-8 -*-
"""
PCAP 解析工具 - 纯文件解析，不涉及抓包功能。
使用轻量PCAP解析模块（纯Python实现，无第三方依赖）解析 pcap/pcapng 文件，
提取 HTTP/HTTPS/DNS/TCP/WebSocket 流。
"""

import os
import json
import time
import gzip
import zlib
from typing import List, Dict, Optional, Callable, Tuple

try:
    import brotli
    _HAS_BROTLI = True
except ImportError:
    _HAS_BROTLI = False


# ──────────────────────── 数据结构 ────────────────────────

class 网络流:
    """一个网络请求-响应对。支持中英文属性名。"""

    # 中文属性名 → 英文属性名（对话框代码使用）
    _中文到英文 = {
        '序号': 'idx', '时间戳': 'ts', '方法': 'method', '地址': 'url',
        '路径': 'path', '主机': 'host', '协议': 'protocol',
        '状态码': 'status_code', '状态文本': 'status_text',
        '请求头': 'req_headers', '响应头': 'resp_headers',
        '请求体': 'req_body', '响应体': 'resp_body',
        '源IP': 'src_ip', '源端口': 'src_port',
        '目标IP': 'dst_ip', '目标端口': 'dst_port',
        '请求长度': 'req_len', '响应长度': 'resp_len', '耗时': 'duration',
        '原始请求': 'raw_request', '原始响应': 'raw_response',
        'TLS_SNI': 'tls_sni', 'TLS版本': 'tls_version',
        'DNS查询': 'dns_query', 'DNS类型': 'dns_type', 'DNS响应': 'dns_response',
        '是否WebSocket': 'is_websocket',
    }

    # 英文属性名 → 中文属性名
    _英文到中文 = {v: k for k, v in _中文到英文.items()}

    def __init__(self):
        self.序号 = 0
        self.时间戳 = 0.0
        self.方法 = ''
        self.地址 = ''
        self.路径 = ''
        self.主机 = ''
        self.协议 = 'HTTP'
        self.状态码 = 0
        self.状态文本 = ''
        self.请求头 = {}
        self.响应头 = {}
        self.请求体 = b''
        self.响应体 = b''
        self.源IP = ''
        self.源端口 = 0
        self.目标IP = ''
        self.目标端口 = 0
        self.请求长度 = 0
        self.响应长度 = 0
        self.耗时 = 0.0
        self.原始请求 = b''
        self.原始响应 = b''
        self.TLS_SNI = ''
        self.TLS版本 = ''
        self.DNS查询 = ''
        self.DNS类型 = ''
        self.DNS响应 = ''
        self.是否WebSocket = False

    def __getattr__(self, name):
        """支持英文属性名访问。"""
        if name.startswith('_') or name in ('_中文到英文', '_英文到中文'):
            raise AttributeError(name)
        # 计算属性
        if name == 'size':
            return self.请求长度 + self.响应长度
        中文键 = self._英文到中文.get(name)
        if 中文键:
            return getattr(self, 中文键)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        """支持英文属性名赋值。"""
        if name.startswith('_') or name in ('_中文到英文', '_英文到中文'):
            super().__setattr__(name, value)
            return
        中文键 = self._英文到中文.get(name)
        if 中文键:
            super().__setattr__(中文键, value)
        else:
            super().__setattr__(name, value)


# 兼容旧类名
_HttpFlow = 网络流

MAX_PACKETS = 500000


# ──────────────────────── 主入口 ────────────────────────

def 解析PCAP(文件路径: str, 进度回调=None, 取消回调=None,
             progress_cb=None, cancel_cb=None, max_packets=None) -> Tuple[List[网络流], int, Optional[str], dict]:
    """解析 pcap 文件，提取所有网络流。

    Args:
        文件路径: pcap 文件路径
        进度回调 / progress_cb: 进度上报函数，参数为已读包数
        取消回调 / cancel_cb: 取消检查函数，返回 True 时停止

    Returns:
        (网络流列表, 总包数, 错误信息)
    """
    # 兼容中英文参数名
    if 进度回调 is None:
        进度回调 = progress_cb
    if 取消回调 is None:
        取消回调 = cancel_cb

    # 文件完整性检查
    if not os.path.isfile(文件路径):
        return [], 0, f'文件不存在: {文件路径}'
    
    文件大小 = os.path.getsize(文件路径)
    if 文件大小 == 0:
        return [], 0, '文件为空，无数据可解析'
    
    # 检查 pcap 魔数
    try:
        with open(文件路径, 'rb') as f:
            头部 = f.read(4)
        if len(头部) < 4:
            return [], 0, '文件过小，不是有效的 pcap 文件'
        
        有效魔数 = [
            b'\xd4\xc3\xb2\xa1',  # pcap LE
            b'\xa1\xb2\xc3\xd4',  # pcap BE
            b'\x0a\x0d\x0d\x0a',  # pcapng
        ]
        if 头部[:4] not in 有效魔数:
            return [], 0, f'文件格式无效，不是 pcap/pcapng 文件 (魔数: {头部[:4].hex()})'
    except Exception as e:
        return [], 0, f'读取文件失败: {str(e)}'
    try:
        from tools.lightweight_pcap_parser import (
            PcapReader, Packet,
            IP, IPv6, TCP, UDP, Raw, DNS, DNSQR, DNSRR,
        )
    except ImportError:
        return [], 0, '轻量PCAP解析模块导入失败，请检查 tools/lightweight_pcap_parser.py 是否存在'

    流列表 = []
    待处理 = {}
    DNS待处理 = {}
    已见TCP = set()
    总包数 = 0
    错误信息 = None
    上次进度时间 = 0.0
    最小进度间隔 = 0.1
    # 包类型统计
    统计 = {'ip': 0, 'non_ip': 0, 'tcp': 0, 'udp': 0, 'http': 0, 'tls': 0, 'other': 0}

    try:
        读取器 = PcapReader(文件路径)
        上次时间戳 = 0.0

        for 数据包 in 读取器:
            if 取消回调 and 取消回调():
                错误信息 = 'cancelled'
                break
            总包数 += 1

            if 总包数 >= (max_packets or MAX_PACKETS):
                错误信息 = f'已达到最大解析包数限制 ({max_packets or MAX_PACKETS})'
                break

            # 限制进度上报频率
            当前时间 = time.time()
            if 进度回调 and (总包数 % 50 == 0 or 当前时间 - 上次进度时间 >= 最小进度间隔):
                进度回调(总包数)
                上次进度时间 = 当前时间

            # 获取 IP 信息
            有IP = 数据包.haslayer(IP)
            有IPv6 = 数据包.haslayer(IPv6)
            if 有IP or 有IPv6:
                统计['ip'] += 1
            else:
                统计['non_ip'] += 1
            if 有IP:
                源IP = 数据包[IP].src
                目标IP = 数据包[IP].dst
            elif 有IPv6:
                源IP = 数据包[IPv6].src
                目标IP = 数据包[IPv6].dst
            else:
                continue

            # DNS 检测 (UDP port 53)
            if 数据包.haslayer(UDP):
                统计['udp'] += 1
            elif 数据包.haslayer(TCP):
                统计['tcp'] += 1

            if 数据包.haslayer(UDP) and (数据包[UDP].sport == 53 or 数据包[UDP].dport == 53):
                if 数据包.haslayer(DNS):
                    # DNS 查询
                    if 数据包.haslayer(DNSQR) and 数据包[UDP].dport == 53:
                        流 = _解析DNS查询(数据包, 源IP, 目标IP)
                        if 流:
                            事务ID = 数据包[DNS].id
                            键 = (事务ID, 源IP, 目标IP)
                            DNS待处理[键] = 流
                    # DNS 响应
                    elif 数据包[UDP].sport == 53:
                        事务ID = 数据包[DNS].id
                        键 = (事务ID, 目标IP, 源IP)
                        流 = DNS待处理.pop(键, None)
                        if 流 is None:
                            for k, v in list(DNS待处理.items()):
                                if k[0] == 事务ID:
                                    流 = DNS待处理.pop(k)
                                    break
                        if 流:
                            _解析DNS响应(数据包, 流)
                            流列表.append(流)
                        elif 数据包.haslayer(DNSQR):
                            流 = _解析DNS查询(数据包, 源IP, 目标IP)
                            if 流:
                                _解析DNS响应(数据包, 流)
                                流列表.append(流)
                    continue

            # HTTPS/TLS 检测
            if 数据包.haslayer(TCP) and 数据包.haslayer(Raw):
                载荷 = bytes(数据包[Raw].load)
                if not 载荷:
                    continue

                源端口 = 数据包[TCP].sport
                目标端口 = 数据包[TCP].dport
                时间戳 = float(数据包.time)

                # 检查 TLS/SSL 握手
                TLS信息 = _尝试解析TLS(载荷)
                if TLS信息 and (目标端口 == 443 or 源端口 == 443):
                    统计['tls'] += 1
                    流 = 网络流()
                    流.时间戳 = 时间戳
                    流.源IP = 源IP
                    流.源端口 = 源端口
                    流.目标IP = 目标IP
                    流.目标端口 = 目标端口
                    流.协议 = 'HTTPS'
                    流.TLS_SNI = TLS信息.get('sni', '')
                    流.TLS版本 = TLS信息.get('version', '')
                    流.方法 = 'TLS'
                    流.主机 = 流.TLS_SNI or ''
                    流.地址 = f'https://{流.TLS_SNI or 目标IP}:{目标端口}'
                    流.路径 = '/'
                    流.请求长度 = len(载荷)
                    流.原始请求 = 载荷
                    流列表.append(流)
                    continue

                # HTTP 请求/响应
                是请求 = _检查HTTP请求(载荷)
                是响应 = _检查HTTP响应(载荷)

                if 是请求:
                    统计['http'] += 1
                    流 = _解析HTTP请求(载荷)
                    if 流:
                        流.源IP = 源IP
                        流.源端口 = 源端口
                        流.目标IP = 目标IP
                        流.目标端口 = 目标端口
                        流.时间戳 = 时间戳
                        流.请求长度 = len(载荷)
                        流.原始请求 = 载荷
                        流.协议 = 'HTTP'
                        # WebSocket 检测
                        if 流.请求头.get('upgrade', '').lower() == 'websocket':
                            流.是否WebSocket = True
                            流.协议 = 'WebSocket'
                        键 = (源IP, 源端口, 目标IP, 目标端口)
                        待处理[键] = 流
                        上次时间戳 = 时间戳

                elif 是响应:
                    统计['http'] += 1
                    请求键 = (目标IP, 目标端口, 源IP, 源端口)
                    流 = 待处理.pop(请求键, None)
                    if 流 is None:
                        for k, v in list(待处理.items()):
                            if (k[2] == 源IP and k[3] == 源端口
                                    and k[0] == 目标IP and k[1] == 目标端口):
                                流 = 待处理.pop(k)
                                break
                    if 流 is not None:
                        _解析HTTP响应(载荷, 流)
                        流.响应长度 = len(载荷)
                        流.原始响应 = 载荷
                        流.耗时 = 时间戳 - 流.时间戳
                        if 流.响应头.get('upgrade', '').lower() == 'websocket':
                            流.是否WebSocket = True
                            流.协议 = 'WebSocket'
                        流列表.append(流)
                        上次时间戳 = 时间戳

                else:
                    # 续包处理
                    请求键 = (源IP, 源端口, 目标IP, 目标端口)
                    流 = 待处理.get(请求键)
                    if 流 is not None:
                        # 检查请求体是否已完整接收
                        请求CL = 流.请求头.get('content-length', '')
                        te = 流.请求头.get('transfer-encoding', '').lower()
                        需追加 = True
                        if 'chunked' in te:
                            # chunked 编码：检查是否以 '0\r\n\r\n' 结尾
                            if 流.请求体.endswith(b'0\r\n\r\n') or 流.请求体.endswith(b'0\n\n'):
                                需追加 = False
                        elif 请求CL:
                            try:
                                cl_val = int(请求CL)
                                if len(流.请求体) >= cl_val:
                                    需追加 = False
                            except ValueError:
                                pass
                        if 需追加:
                            流.请求体 += 载荷
                            流.请求长度 += len(载荷)
                            流.原始请求 += 载荷
                    else:
                        # 响应体续包（从已完成的流列表中查找）
                        响应流 = None
                        for f in reversed(流列表):
                            if (f.源IP == 源IP and f.源端口 == 源端口
                                    and f.目标IP == 目标IP and f.目标端口 == 目标端口):
                                # 检查 Content-Length
                                cl = f.响应头.get('content-length', '0')
                                try:
                                    cl_val = int(cl)
                                except ValueError:
                                    cl_val = 0
                                # 检查 chunked 编码
                                te = f.响应头.get('transfer-encoding', '').lower()
                                is_chunked = 'chunked' in te
                                if is_chunked:
                                    # chunked 编码，检查是否以结尾标记结束
                                    if not (f.响应体.endswith(b'0\r\n\r\n') or f.响应体.endswith(b'0\n\n')):
                                        响应流 = f
                                        break
                                elif cl_val > 0 and len(f.响应体) < cl_val:
                                    响应流 = f
                                    break
                        if 响应流:
                            响应流.响应体 += 载荷
                            响应流.响应长度 += len(载荷)
                            响应流.原始响应 += 载荷
            elif 数据包.haslayer(TCP):
                # TCP 连接记录（扩展：不仅限于 SYN 包，也跟踪已有连接的数据传输）
                源端口 = 数据包[TCP].sport
                目标端口 = 数据包[TCP].dport
                时间戳 = float(数据包.time)
                TCP_Flags = 数据包[TCP].flags
                有效载荷 = bytes(数据包[Raw].load) if 数据包.haslayer(Raw) else b''

                if TCP_Flags & 0x02:  # SYN
                    连接键 = (源IP, 源端口, 目标IP, 目标端口)
                    if 连接键 not in 已见TCP and not (TCP_Flags & 0x10):
                        已见TCP.add(连接键)
                        流 = 网络流()
                        流.时间戳 = 时间戳
                        流.源IP = 源IP
                        流.源端口 = 源端口
                        流.目标IP = 目标IP
                        流.目标端口 = 目标端口
                        流.协议 = 'TCP'
                        流.方法 = 'TCP'
                        流.地址 = f'{源IP}:{源端口} → {目标IP}:{目标端口}'
                        流.路径 = ''
                        流.主机 = ''
                        流.请求长度 = 0
                        流列表.append(流)
                    elif 有效载荷:
                        # SYN+data，可能是已建立连接的新流量
                        响应键 = (目标IP, 目标端口, 源IP, 源端口)
                        已存在流 = 待处理.get(响应键) or 待处理.get(连接键)
                        if not 已存在流:
                            # 从已有流列表查找匹配
                            for f in reversed(流列表):
                                if (f.源IP == 源IP and f.源端口 == 源端口
                                        and f.目标IP == 目标IP and f.目标端口 == 目标端口):
                                    已存在流 = f
                                    break
                            if not 已存在流:
                                for f in reversed(流列表):
                                    if (f.源IP == 目标IP and f.源端口 == 目标端口
                                            and f.目标IP == 源IP and f.目标端口 == 源端口):
                                        已存在流 = f
                                        break
                        if 已存在流 and 有效载荷:
                            已存在流.请求体 += 有效载荷
                            已存在流.请求长度 += len(有效载荷)
                            已存在流.原始请求 += 有效载荷
                        elif not 已存在流:
                            流 = 网络流()
                            流.时间戳 = 时间戳
                            流.源IP = 源IP
                            流.源端口 = 源端口
                            流.目标IP = 目标IP
                            流.目标端口 = 目标端口
                            流.协议 = 'TCP'
                            流.方法 = 'TCP'
                            流.地址 = f'{源IP}:{源端口} → {目标IP}:{目标端口}'
                            流.路径 = ''
                            流.主机 = ''
                            流.请求体 = 有效载荷
                            流.请求长度 = len(有效载荷)
                            流.原始请求 = 有效载荷
                            流列表.append(流)
                elif 有效载荷 and not (TCP_Flags & 0x04):  # 非RST且有载荷
                    # 已有连接上的数据传输（非SYN包但有载荷）
                    匹配流 = None
                    # 查找已有流
                    for f in reversed(流列表):
                        if (f.源IP == 源IP and f.源端口 == 源端口
                                and f.目标IP == 目标IP and f.目标端口 == 目标端口):
                            匹配流 = f
                            break
                        elif (f.源IP == 目标IP and f.源端口 == 目标端口
                                and f.目标IP == 源IP and f.目标端口 == 源端口):
                            匹配流 = f
                            break
                    if 匹配流:
                        # 判断方向：源端口匹配则为请求方向
                        if 匹配流.源IP == 源IP and 匹配流.源端口 == 源端口:
                            匹配流.请求体 += 有效载荷
                            匹配流.请求长度 += len(有效载荷)
                            匹配流.原始请求 += 有效载荷
                        else:
                            匹配流.响应体 += 有效载荷
                            匹配流.响应长度 += len(有效载荷)
                            匹配流.原始响应 += 有效载荷
                    elif len(有效载荷) > 20:  # 忽略很小的控制数据包
                        # 创建新的 TCP 流（pcap 开始时连接已建立）
                        流 = 网络流()
                        流.时间戳 = 时间戳
                        流.源IP = 源IP
                        流.源端口 = 源端口
                        流.目标IP = 目标IP
                        流.目标端口 = 目标端口
                        流.协议 = 'TCP'
                        流.方法 = 'TCP'
                        流.地址 = f'{源IP}:{源端口} → {目标IP}:{目标端口}'
                        流.路径 = ''
                        流.主机 = ''
                        流.请求体 = 有效载荷
                        流.请求长度 = len(有效载荷)
                        流.原始请求 = 有效载荷
                        流列表.append(流)

        # 最终进度上报
        if 进度回调:
            进度回调(总包数)

        # 加入未响应的请求
        for 流 in 待处理.values():
            流列表.append(流)
        # 加入未匹配的 DNS 查询
        for 流 in DNS待处理.values():
            流列表.append(流)

        # 按时间排序
        流列表.sort(key=lambda f: f.时间戳)
        
        # 后处理：解码 chunked / 解压 gzip-deflate-brotli（body 完整后统一处理）
        for 流 in 流列表:
            if 流.协议 in ('HTTP', 'WebSocket'):
                # 请求体 — 检查加密
                if 流.请求体:
                    xenc = 流.请求头.get('x-encryption', '').lower()
                    if xenc:
                        # 加密body保持原始字节，不解压
                        pass
                    else:
                        te = 流.请求头.get('transfer-encoding', '').lower()
                        if 'chunked' in te:
                            try:
                                流.请求体 = _解码Chunked(流.请求体)
                            except Exception:
                                pass
                        ce = 流.请求头.get('content-encoding', '').lower()
                        if ce:
                            流.请求体 = _按内容编码解压(流.请求体, ce)
                # 响应体
                if 流.响应体:
                    xenc = 流.响应头.get('x-encryption', '').lower()
                    if xenc:
                        pass
                    else:
                        te = 流.响应头.get('transfer-encoding', '').lower()
                        if 'chunked' in te:
                            try:
                                流.响应体 = _解码Chunked(流.响应体)
                            except Exception:
                                pass
                        ce = 流.响应头.get('content-encoding', '').lower()
                        if ce:
                            流.响应体 = _按内容编码解压(流.响应体, ce)
        
        for i, f in enumerate(流列表):
            f.序号 = i + 1

    except Exception as e:
        错误信息 = str(e)

    return 流列表, 总包数, 错误信息, 统计


# ──────────────────────── HTTP 解析 ────────────────────────

def _检查HTTP请求(数据: bytes) -> bool:
    """判断是否是 HTTP 请求头。"""
    方法列表 = (b'GET ', b'POST ', b'PUT ', b'DELETE ', b'HEAD ',
               b'OPTIONS ', b'PATCH ', b'CONNECT ', b'TRACE ')
    return any(数据.startswith(m) for m in 方法列表)


def _检查HTTP响应(数据: bytes) -> bool:
    """判断是否是 HTTP 响应头。"""
    return 数据.startswith(b'HTTP/')


def _解析HTTP请求(数据: bytes) -> Optional[网络流]:
    """解析 HTTP 请求头。只解析头部，body 保持原始字节 — 不在此处解压/解码。"""
    try:
        if b'\r\n\r\n' in 数据:
            头部字节, 体 = 数据.split(b'\r\n\r\n', 1)
        elif b'\n\n' in 数据:
            头部字节, 体 = 数据.split(b'\n\n', 1)
        else:
            头部字节, 体 = 数据, b''

        头部文本 = 头部字节.decode('utf-8', errors='replace')
        行列表 = 头部文本.split('\n')

        if not 行列表:
            return None
        部分 = 行列表[0].strip().split()
        if len(部分) < 2:
            return None

        流 = 网络流()
        流.方法 = 部分[0]
        流.路径 = 部分[1]

        for 行 in 行列表[1:]:
            行 = 行.strip().rstrip('\r')
            if ':' in 行:
                键, _, 值 = 行.partition(':')
                流.请求头[键.strip().lower()] = 值.strip()

        流.主机 = 流.请求头.get('host', '')
        if 流.主机:
            流.地址 = f'http://{流.主机}{流.路径}'
        else:
            流.地址 = 流.路径

        # body 保持原始字节，后处理统一解码
        流.请求体 = 体
        return 流
    except Exception:
        return None


def _添加头(头字典: dict, 键: str, 值: str):
    """添加头信息，支持多值头（如 Set-Cookie）以列表存储。"""
    键 = 键.strip().lower()
    if 键 in ('set-cookie', 'set-cookie2', 'www-authenticate', 'proxy-authenticate'):
        if 键 in 头字典:
            现有 = 头字典[键]
            if isinstance(现有, list):
                现有.append(值)
            else:
                头字典[键] = [现有, 值]
        else:
            头字典[键] = 值
    else:
        头字典[键] = 值


def _解析HTTP响应(数据: bytes, 流: 网络流) -> None:
    """解析 HTTP 响应头。只解析头部，body 保持原始字节 — 不在此处解压/解码。"""
    try:
        if b'\r\n\r\n' in 数据:
            头部字节, 体 = 数据.split(b'\r\n\r\n', 1)
        elif b'\n\n' in 数据:
            头部字节, 体 = 数据.split(b'\n\n', 1)
        else:
            头部字节, 体 = 数据, b''

        头部文本 = 头部字节.decode('utf-8', errors='replace')
        行列表 = 头部文本.split('\n')

        if 行列表:
            部分 = 行列表[0].strip().split()
            if len(部分) >= 2:
                try:
                    流.状态码 = int(部分[1])
                except ValueError:
                    pass
                if len(部分) >= 3:
                    流.状态文本 = ' '.join(部分[2:])

        for 行 in 行列表[1:]:
            行 = 行.strip().rstrip('\r')
            if ':' in 行:
                键, _, 值 = 行.partition(':')
                _添加头(流.响应头, 键, 值)

        # body 保持原始字节，后处理统一解码
        流.响应体 = 体
    except Exception:
        pass


def _解码Chunked(数据: bytes) -> bytes:
    """解码 HTTP chunked transfer encoding。"""
    try:
        结果 = b''
        位置 = 0
        while 位置 < len(数据):
            行结束 = 数据.find(b'\r\n', 位置)
            if 行结束 < 0:
                break
            大小字符串 = 数据[位置:行结束].decode('ascii', errors='replace').strip()
            if not 大小字符串:
                break
            try:
                块大小 = int(大小字符串, 16)
            except ValueError:
                break
            if 块大小 == 0:
                break
            位置 = 行结束 + 2
            结果 += 数据[位置:位置 + 块大小]
            位置 += 块大小
            if 位置 < len(数据) and 数据[位置:位置 + 2] == b'\r\n':
                位置 += 2
        return 结果
    except Exception:
        return 数据


def _解压Gzip(数据: bytes) -> bytes:
    """gzip 解压，支持截断/损坏数据的容错处理。"""
    if not 数据:
        return 数据
    # 检查 gzip 魔数
    if not 数据.startswith(b'\x1f\x8b'):
        return 数据
    # 尝试1: 标准解压
    try:
        return gzip.decompress(数据)
    except (EOFError, Exception):
        pass
    # 尝试2: GzipFile 流式读取（可处理缺尾的截断数据）
    try:
        import io
        buf = io.BytesIO(数据)
        with gzip.GzipFile(fileobj=buf) as f:
            result = f.read()
            if result:
                return result
    except (EOFError, Exception):
        pass
    # 尝试3: 跳过 gzip 头（10字节）+ 尾（8字节），zlib 解压 deflate 数据
    try:
        if len(数据) > 18:
            return zlib.decompress(数据[10:-8])
    except Exception:
        pass
    # 尝试4: 只跳过 gzip 头，不解码尾（处理尾部损坏）
    try:
        if len(数据) > 10:
            return zlib.decompress(数据[10:])
    except Exception:
        pass
    # 尝试5: 原始 deflate 解压（无 zlib 头）
    try:
        return zlib.decompress(数据[10:], -zlib.MAX_WBITS)
    except Exception:
        pass
    # 所有尝试都失败，返回原始数据
    return 数据


def _解压Deflate(数据: bytes) -> bytes:
    """deflate 解压。"""
    if not 数据:
        return 数据
    # 尝试1: 标准 zlib 解压
    try:
        return zlib.decompress(数据)
    except Exception:
        pass
    # 尝试2: 原始 deflate（无 zlib 头）
    try:
        return zlib.decompress(数据, -zlib.MAX_WBITS)
    except Exception:
        pass
    # 尝试3: 自动检测窗口大小
    try:
        return zlib.decompress(数据, zlib.MAX_WBITS | 32)
    except Exception:
        pass
    return 数据


def _解压Brotli(数据: bytes) -> bytes:
    """brotli 解压。"""
    if not _HAS_BROTLI:
        return 数据
    try:
        return brotli.decompress(数据)
    except Exception:
        return 数据


def _按内容编码解压(数据: bytes, content_encoding: str) -> bytes:
    """根据 Content-Encoding 值自动选择解压方式。"""
    ce = content_encoding.lower()
    if 'gzip' in ce:
        return _解压Gzip(数据)
    elif 'deflate' in ce:
        return _解压Deflate(数据)
    elif 'br' in ce or 'brotli' in ce:
        return _解压Brotli(数据)
    return 数据


# ──────────────────────── TLS 解析 ────────────────────────

def _尝试解析TLS(载荷: bytes) -> Optional[Dict]:
    """尝试解析 TLS 握手，返回 {sni, version} 或 None。"""
    if len(载荷) < 5:
        return None
    内容类型 = 载荷[0]
    if 内容类型 != 0x16:  # Handshake
        return None
    版本号 = (载荷[1] << 8) | 载荷[2]
    
    版本映射 = {
        0x0301: 'TLSv1.0',
        0x0302: 'TLSv1.1',
        0x0303: 'TLSv1.2',
        0x0304: 'TLSv1.3',
    }
    版本字符串 = 版本映射.get(版本号, f'TLSv{版本号 >> 8}.{版本号 & 0xFF}')

    sni = ''
    try:
        if len(载荷) > 42 and 载荷[5] == 0x01:  # ClientHello
            偏移 = 43
            if 偏移 < len(载荷):
                session_id_len = 载荷[偏移]
                偏移 += 1 + session_id_len
                if 偏移 + 2 < len(载荷):
                    cipher_suites_len = (载荷[偏移] << 8) | 载荷[偏移 + 1]
                    偏移 += 2 + cipher_suites_len
                    if 偏移 < len(载荷):
                        压缩_len = 载荷[偏移]
                        偏移 += 1 + 压缩_len
                        if 偏移 + 2 < len(载荷):
                            扩展_len = (载荷[偏移] << 8) | 载荷[偏移 + 1]
                            偏移 += 2
                            扩展结束 = 偏移 + 扩展_len
                            while 偏移 + 4 < min(扩展结束, len(载荷)):
                                扩展类型 = (载荷[偏移] << 8) | 载荷[偏移 + 1]
                                扩展长度 = (载荷[偏移 + 2] << 8) | 载荷[偏移 + 3]
                                偏移 += 4
                                if 扩展类型 == 0x0000:  # SNI
                                    if 偏移 + 3 < len(载荷):
                                        sni列表_len = (载荷[偏移] << 8) | 载荷[偏移 + 1]
                                        偏移 += 2
                                        if 偏移 + 1 < len(载荷):
                                            名称类型 = 载荷[偏移]
                                            名称长度 = (载荷[偏移 + 1] << 8) | 载荷[偏移 + 2]
                                            偏移 += 3
                                            if 名称类型 == 0 and 偏移 + 名称长度 <= len(载荷):
                                                sni = 载荷[偏移:偏移 + 名称长度].decode('utf-8', errors='replace')
                                                break
                                偏移 += 扩展长度
    except Exception:
        pass

    return {'sni': sni, 'version': 版本字符串}


# ──────────────────────── DNS 解析 ────────────────────────

def _解析DNS查询(数据包, 源IP: str, 目标IP: str) -> Optional[网络流]:
    """解析 DNS 查询包。"""
    try:
        dns = 数据包[DNS]
        流 = 网络流()
        流.时间戳 = float(数据包.time)
        流.源IP = 源IP
        流.源端口 = 数据包[UDP].sport if 数据包.haslayer(UDP) else 0
        流.目标IP = 目标IP
        流.目标端口 = 数据包[UDP].dport if 数据包.haslayer(UDP) else 0
        流.协议 = 'DNS'
        流.方法 = 'DNS'
        流.状态码 = dns.rcode

        if 数据包.haslayer(DNSQR):
            qr = 数据包[DNSQR]
            流.DNS查询 = qr.qname.rstrip('.')
            类型映射 = {1: 'A', 28: 'AAAA', 5: 'CNAME', 16: 'TXT', 2: 'NS', 15: 'MX', 12: 'PTR'}
            流.DNS类型 = 类型映射.get(qr.qtype, f'TYPE{qr.qtype}')
            流.主机 = 流.DNS查询
            流.地址 = f'DNS {流.DNS类型} {流.DNS查询}'
            流.路径 = 流.DNS查询
            流.请求长度 = len(数据包.payload) if hasattr(数据包, 'payload') else 0

        return 流
    except Exception:
        return None


def _解析DNS响应(数据包, 流: 网络流) -> None:
    """解析 DNS 响应包。"""
    try:
        dns = 数据包[DNS]
        流.状态码 = dns.rcode

        答案列表 = []
        for rr in dns.answers[:5]:
            try:
                答案列表.append(str(rr.rdata))
            except Exception:
                pass

        if 答案列表:
            流.DNS响应 = ', '.join(答案列表)
            流.响应体 = 流.DNS响应.encode('utf-8')
            流.响应长度 = len(流.DNS响应)
            流.原始响应 = bytes(数据包.payload) if hasattr(数据包, 'payload') else b''
            流.耗时 = float(数据包.time) - 流.时间戳
        else:
            流.响应体 = '(无响应记录)'.encode('utf-8')
            流.响应长度 = 0
            流.原始响应 = b''
            流.耗时 = float(数据包.time) - 流.时间戳

        状态码映射 = {0: 'NOERROR', 1: 'FORMERR', 2: 'SERVFAIL', 3: 'NXDOMAIN', 4: 'NOTIMP', 5: 'REFUSED'}
        流.状态文本 = 状态码映射.get(流.状态码, f'RCODE_{流.状态码}')
    except Exception:
        pass


# ──────────────────────── 辅助函数 ────────────────────────

def _智能解码(body_bytes: bytes, content_type: str = '', headers: dict = None) -> tuple:
    """智能解码：尝试多种编码，返回 (解码文本, 使用的编码)。"""
    if not body_bytes:
        return '', ''

    # 从 Content-Type 提取 charset
    charset = ''
    if content_type:
        import re
        m = re.search(r'charset=([^\s;]+)', content_type, re.IGNORECASE)
        if m:
            charset = m.group(1).strip('"').strip("'")

    # 优先使用声明的 charset
    if charset:
        try:
            return body_bytes.decode(charset), charset
        except (UnicodeDecodeError, LookupError):
            pass

    # 尝试 UTF-8
    try:
        text = body_bytes.decode('utf-8')
        return text, 'utf-8'
    except UnicodeDecodeError:
        pass

    # 尝试 GBK（中文常见编码）
    try:
        text = body_bytes.decode('gbk')
        return text, 'gbk'
    except UnicodeDecodeError:
        pass

    # 尝试 GB18030
    try:
        text = body_bytes.decode('gb18030')
        return text, 'gb18030'
    except UnicodeDecodeError:
        pass

    # 尝试 Big5（繁体中文）
    try:
        text = body_bytes.decode('big5')
        return text, 'big5'
    except UnicodeDecodeError:
        pass

    # 最后回退到 UTF-8 with replacement
    return body_bytes.decode('utf-8', errors='replace'), 'utf-8(replace)'


def 格式化Body(body_bytes: bytes, content_type: str = '', headers: dict = None) -> str:
    """格式化 body 为可读文本。"""
    if not body_bytes:
        return '(空)'

    # 检测加密（如 X-encryption: MIGUEncryption），直接展示原数据
    if headers:
        xenc = headers.get('x-encryption', '').lower()
        if xenc:
            text, enc = _智能解码(body_bytes, content_type, headers)
            return text[:50000]

    # 检测是否仍为压缩数据 — 尝试再次解压（容错）
    if body_bytes.startswith(b'\x1f\x8b'):
        解压结果 = _解压Gzip(body_bytes)
        if 解压结果 != body_bytes:
            return 格式化Body(解压结果, content_type, headers)
        # gzip 解压失败，继续尝试正常解码（可能是截断数据）
    if body_bytes.startswith(b'\x78\x9c') or body_bytes.startswith(b'\x78\xda'):
        解压结果 = _解压Deflate(body_bytes)
        if 解压结果 != body_bytes:
            return 格式化Body(解压结果, content_type, headers)

    # 智能解码（自动检测编码）
    text, used_enc = _智能解码(body_bytes, content_type, headers)

    # 检测是否为二进制数据
    if used_enc == 'utf-8(replace)':
        printable_ratio = sum(1 for c in text if c.isprintable() or c in '\r\n\t') / max(len(text), 1)
        if printable_ratio < 0.3:
            return f'(二进制数据, {len(body_bytes)} 字节)'

    ct = content_type.lower()

    # JSON 相关：优先提取 JSON 部分
    if 'json' in ct or text.strip().startswith(('{', '[')):
        extracted = _提取JSON(text)
        if extracted is not None:
            try:
                obj = json.loads(extracted)
                return json.dumps(obj, indent=2, ensure_ascii=False)
            except Exception:
                return extracted
        # 回退：整段尝试解析
        try:
            obj = json.loads(text)
            return json.dumps(obj, indent=2, ensure_ascii=False)
        except Exception:
            pass

    if 'x-www-form-urlencoded' in ct:
        try:
            from urllib.parse import unquote_plus
            pairs = text.split('&')
            lines = []
            for p in pairs:
                if '=' in p:
                    k, v = p.split('=', 1)
                    lines.append(f'{unquote_plus(k)} = {unquote_plus(v)}')
                else:
                    lines.append(unquote_plus(p))
            return '\n'.join(lines)
        except Exception:
            pass

    return text


def _提取JSON(text: str) -> Optional[str]:
    """从混合文本中提取 JSON 对象或数组。"""
    # 寻找第一个 '{' 或 '[' 的位置
    for i, ch in enumerate(text):
        if ch in ('{', '['):
            # 从该位置尝试匹配
            depth = 0
            start = i
            in_string = False
            escape = False
            for j in range(i, len(text)):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c in ('{', '['):
                    depth += 1
                elif c in ('}', ']'):
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:j + 1]
                        try:
                            json.loads(candidate)
                            return candidate
                        except Exception:
                            break
    return None


def 状态码颜色(状态码: int) -> str:
    """状态码对应的颜色。"""
    if 200 <= 状态码 < 300:
        return '#4CAF50'
    elif 300 <= 状态码 < 400:
        return '#FFC107'
    elif 400 <= 状态码 < 500:
        return '#FF9800'
    elif 状态码 >= 500:
        return '#F44336'
    return '#999999'


def 协议颜色(协议: str) -> str:
    """协议对应的颜色。"""
    颜色映射 = {
        'HTTP': '#4CAF50',
        'HTTPS': '#2196F3',
        'WebSocket': '#9C27B0',
        'DNS': '#FF9800',
        'TCP': '#607D8B',
    }
    return 颜色映射.get(协议, '#999999')


def 生成cURL命令(流: 网络流) -> str:
    """生成 cURL 命令。"""
    if 流.协议 not in ('HTTP', 'WebSocket'):
        return ''
    parts = [f"curl -X {流.方法}"]
    if 流.请求头:
        for k, v in 流.请求头.items():
            val = v if not isinstance(v, list) else v[0]
            parts.append(f"-H '{k}: {val}'")
    if 流.请求体:
        try:
            body_text = 流.请求体.decode('utf-8', errors='replace')
            if body_text.strip():
                parts.append(f"-d '{body_text[:500]}'")
        except Exception:
            pass
    parts.append(f"'{流.地址}'")
    return ' \\\n    '.join(parts)


def 流转字典(流: 网络流) -> Dict:
    """将网络流转为字典。"""
    return {
        '序号': 流.序号,
        '时间戳': 流.时间戳,
        '方法': 流.方法,
        '地址': 流.地址,
        '路径': 流.路径,
        '主机': 流.主机,
        '协议': 流.协议,
        '状态码': 流.状态码,
        '状态文本': 流.状态文本,
        '源IP': 流.源IP,
        '源端口': 流.源端口,
        '目标IP': 流.目标IP,
        '目标端口': 流.目标端口,
        '请求长度': 流.请求长度,
        '响应长度': 流.响应长度,
        '耗时': 流.耗时,
        'TLS_SNI': 流.TLS_SNI,
        'TLS版本': 流.TLS版本,
        'DNS查询': 流.DNS查询,
        'DNS类型': 流.DNS类型,
        'DNS响应': 流.DNS响应,
        '是否WebSocket': 流.是否WebSocket,
        '请求头': 流.请求头,
        '响应头': 流.响应头,
        '请求体': 流.请求体.decode('utf-8', errors='replace') if 流.请求体 else '',
        '响应体': 流.响应体.decode('utf-8', errors='replace') if 流.响应体 else '',
    }


def 导出为JSON(流列表: List[网络流], 输出路径: str) -> None:
    """导出为 JSON。"""
    数据 = [流转字典(流) for 流 in 流列表]
    with open(输出路径, 'w', encoding='utf-8') as f:
        json.dump(数据, f, indent=2, ensure_ascii=False)


def 导出为CSV(流列表: List[网络流], 输出路径: str) -> None:
    """导出为 CSV。"""
    import csv
    字段 = ['序号', '时间戳', '方法', '协议', '地址', '状态码', '源IP', '源端口', '目标IP', '目标端口', '请求长度', '响应长度', '耗时']
    with open(输出路径, 'w', encoding='utf-8-sig', newline='') as f:
        写入器 = csv.DictWriter(f, fieldnames=字段)
        写入器.writeheader()
        for 流 in 流列表:
            行 = {k: getattr(流, k, '') for k in 字段}
            写入器.writerow(行)


def 导出为HAR(流列表: List[网络流], 输出路径: str) -> None:
    """导出为 HAR 1.2 格式。"""
    entries = []
    for 流 in 流列表:
        if 流.协议 not in ('HTTP', 'WebSocket'):
            continue
        entry = {
            'method': 流.方法,
            'url': 流.地址,
            'status': 流.状态码,
            'time': round(流.耗时 * 1000, 2),
            'request': {
                'method': 流.方法,
                'url': 流.地址,
                'httpVersion': 'HTTP/1.1',
                'headers': [{'name': k, 'value': v if not isinstance(v, list) else v[0]} for k, v in 流.请求头.items()],
                'queryString': [],
                'cookies': [],
                'headersSize': -1,
                'bodySize': len(流.请求体),
            },
            'response': {
                'status': 流.状态码,
                'statusText': 流.状态文本,
                'httpVersion': 'HTTP/1.1',
                'headers': [{'name': k, 'value': v if not isinstance(v, list) else v[0]} for k, v in 流.响应头.items()],
                'cookies': [],
                'content': {
                    'size': len(流.响应体),
                    'mimeType': 流.响应头.get('content-type', 'application/octet-stream'),
                },
                'redirectURL': 流.响应头.get('location', ''),
                'headersSize': -1,
                'bodySize': len(流.响应体),
            },
            'cache': {},
            'timings': {
                'send': 0,
                'wait': round(流.耗时 * 1000, 2),
                'receive': 0,
            },
        }
        entries.append(entry)

    har = {
        'log': {
            'version': '1.2',
            'creator': {'name': 'Super_ADB PCAP解析器', 'version': '1.0'},
            'entries': entries,
        }
    }

    with open(输出路径, 'w', encoding='utf-8') as f:
        json.dump(har, f, indent=2, ensure_ascii=False)


def 统计协议(流列表: List[网络流]) -> Dict[str, int]:
    """统计各协议数量。"""
    统计 = {}
    for 流 in 流列表:
        协议 = 流.协议
        统计[协议] = 统计.get(协议, 0) + 1
    return 统计


def 提取域名列表(流列表: List[网络流]) -> List[str]:
    """提取所有唯一域名。"""
    域名集合 = set()
    for 流 in 流列表:
        if 流.主机:
            域名集合.add(流.主机)
        elif 流.DNS查询:
            域名集合.add(流.DNS查询)
    return sorted(域名集合)


def 过滤流(流列表: List[网络流], 方法: str = '全部', 状态: str = '全部',
            协议: str = '全部', 域名: str = '', 搜索: str = '') -> List[网络流]:
    """过滤网络流。"""
    结果 = []
    for 流 in 流列表:
        # 方法过滤
        if 方法 != '全部' and 流.方法 != 方法:
            continue
        # 状态码过滤
        if 状态 != '全部':
            if 状态 == '无响应':
                if 流.状态码 != 0:
                    continue
            elif 状态.endswith('xx'):
                前缀 = 状态[0]
                if not (str(流.状态码).startswith(前缀) or (流.状态码 == 0 and 前缀 != '2')):
                    continue
            else:
                try:
                    状态值 = int(状态)
                    if 流.状态码 != 状态值:
                        continue
                except ValueError:
                    continue
        # 协议过滤
        if 协议 != '全部' and 流.协议 != 协议:
            continue
        # 域名过滤
        if 域名 and 域名 != '全部域名':
            if 域名 not in 流.主机 and 域名 not in 流.DNS查询:
                continue
        # 搜索
        if 搜索:
            搜索文本 = 搜索.lower()
            搜索目标 = f'{流.地址} {流.请求体.decode("utf-8", errors="replace")} {流.响应体.decode("utf-8", errors="replace")} {流.主机} {流.DNS查询}'.lower()
            if 搜索文本 not in 搜索目标:
                continue
        结果.append(流)
    return 结果


# ──────────────────────── PCAP 修复工具 ────────────────────────

def 修复PCAP(文件路径: str, 输出路径: str = '') -> Tuple[bool, str, int]:
    """尝试修复损坏的 pcap 文件。

    Args:
        文件路径: 损坏的 pcap 文件路径
        输出路径: 修复后文件的输出路径（为空则在原文件后加 .fixed）

    Returns:
        (是否成功, 消息, 恢复的数据包数)
    """
    import struct

    if not os.path.isfile(文件路径):
        return False, f'文件不存在: {文件路径}', 0

    文件大小 = os.path.getsize(文件路径)
    if 文件大小 < 24:
        return False, f'文件过小 ({文件大小} 字节)', 0

    try:
        with open(文件路径, 'rb') as f:
            data = f.read()
    except Exception as e:
        return False, f'读取文件失败: {e}', 0

    if len(data) < 24:
        return False, '数据不足', 0

    magic = data[:4]

    if magic == b'\xd4\xc3\xb2\xa1':
        endian = '<'
    elif magic == b'\xa1\xb2\xc3\xd4':
        endian = '>'
    elif magic == b'\x0a\x0d\x0d\x0a':
        return False, '不支持 pcapng 格式修复', 0
    else:
        return False, f'无效的 pcap 魔数: {magic.hex()}', 0

    # 保存全局头
    全局头 = data[:24]
    ver_major, ver_minor, thiszone, sigfigs, snaplen, network = struct.unpack(
        f'{endian}HHiIII', 全局头[4:24])

    max_packet_size = snaplen if snaplen > 0 else 65535

    # 扫描并提取有效数据包
    有效数据包 = []
    scan_offset = 24
    data_len = len(data)

    while scan_offset < data_len:
        剩余 = data_len - scan_offset

        if 剩余 < 16:
            break

        ts_sec, ts_usec, incl_len, orig_len = struct.unpack(
            f'{endian}IIII', data[scan_offset:scan_offset+16])

        # 检查数据包头部是否合理
        # 1. 时间戳应该在合理范围内（2000-2100年）
        if ts_sec < 946684800 or ts_sec > 4102444800:
            scan_offset += 1
            continue

        # 2. incl_len 应该在合理范围内
        if incl_len <= 0 or incl_len > max_packet_size:
            scan_offset += 1
            continue

        # 3. orig_len 应该 >= incl_len
        if orig_len < incl_len or orig_len > max_packet_size:
            scan_offset += 1
            continue

        # 4. 检查 incl_len 是否超过剩余数据
        if incl_len > 剩余 - 16:
            scan_offset += 1
            continue

        # 找到有效数据包
        包头 = data[scan_offset:scan_offset+16]
        包数据 = data[scan_offset+16:scan_offset+16+incl_len]

        有效数据包.append((包头, 包数据))
        scan_offset += 16 + incl_len

    if len(有效数据包) == 0:
        return False, '未找到有效数据包', 0

    # 生成修复后的文件
    if not 输出路径:
        输出路径 = 文件路径 + '.fixed'

    try:
        with open(输出路径, 'wb') as f:
            f.write(全局头)
            for 包头, 包数据 in 有效数据包:
                f.write(包头)
                f.write(包数据)
    except Exception as e:
        return False, f'写入修复文件失败: {e}', len(有效数据包)

    修复后大小 = os.path.getsize(输出路径)
    return True, f'修复成功: 恢复 {len(有效数据包)} 个数据包, 输出 {修复后大小} 字节', len(有效数据包)
