# -*- coding: utf-8 -*-
"""
无线调试配对客户端 (兼容官方 AOSP libadb_pairing_connection)
==============================================================
纯 Python 实现 Android 11+ 无线调试配对 (adb pair)，完全不依赖官方 adb.exe。

精确兼容官方协议 (pairing_connection/pairing_connection.cpp):
  1. TCP 连接配对端口
  2. TLS 1.3 握手 (自签名 ECDSA P-256 证书, 不验证对端证书)
  3. 导出 TLS 密钥材料 (SSL_export_keying_material, label="adb-label", 64 字节)
  4. 将 TLS 密钥材料追加到配对码后, 作为 SPAKE2 密码
  5. SPAKE2 消息交换 (在 TLS 加密通道内, PairingPacket type=0)
  6. 用 SPAKE2 共享密钥派生 AES-128-GCM 密钥 (HKDF-SHA256)
  7. 加密 PeerInfo 并交换 (PairingPacket type=1)
  8. 配对成功, 设备将客户端 ADB 公钥加入 adb_keys

PairingPacket 帧格式:
  1B version (=1) | 1B type | 4B payload_length (大端) | payload
  type 0 = SPAKE2_MSG, type 1 = PEER_INFO

PeerInfo 格式 (固定 8192 字节):
  1B type | 8191B data
  type 0 = ADB_RSA_PUB_KEY, data = adb_auth_get_userkey() 返回的公钥文本行
           (base64(524 字节 android_pubkey_t) + ' 备注') + 零填充
"""
from __future__ import annotations

import ctypes
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
from typing import Optional, Callable, Tuple

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.backends import default_backend
import datetime

try:
    from .pair_auth import PairingAuth
except ImportError:
    from pair_auth import PairingAuth


# ── 常量 ──────────────────────────────────────────────────
PAIRING_PACKET_VERSION = 1
TYPE_SPAKE2_MSG = 0
TYPE_PEER_INFO = 1
MAX_PEER_INFO_SIZE = 8192
PEER_INFO_TYPE_RSA_PUB_KEY = 0
# 公钥以 adb_auth_get_userkey() 的文本行形式发送：base64(524 字节) + ' 备注'
ADB_PUBKEY_STRUCT_SIZE = 524  # android_pubkey_t 结构大小（base64 前）
# 官方 tls_connection.cpp 用 sizeof(kExportedKeyLabel) 传长度, 即 10 字节,
# 包含字符串结尾的 NUL。label 参与 TLS exporter 的 HKDF-Expand,
# 少这个 \x00 会导出完全不同的密钥材料, 最终表现为 PeerInfo 解密 InvalidTag。
TLS_KEY_EXPORT_LABEL = b'adb-label\x00'
TLS_KEY_EXPORT_LENGTH = 64

# PairingPacket 头部大小: version(1) + type(1) + payload(4) = 6
HEADER_SIZE = 6


# ── TLS 密钥材料导出 (ctypes) ─────────────────────────────
def _查找libssl() -> Optional[str]:
    """找到 libssl 库路径。"""
    python_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(python_dir, 'DLLs', 'libssl-3-x64.dll'),
        os.path.join(python_dir, 'libssl-3-x64.dll'),
        os.path.join(python_dir, 'DLLs', 'libssl-3.dll'),
        os.path.join(python_dir, 'libssl-3.dll'),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    # 尝试从已加载的 _ssl 模块获取
    try:
        import _ssl
        if hasattr(_ssl, '__file__') and _ssl.__file__:
            d = os.path.dirname(_ssl.__file__)
            for name in ['libssl-3-x64.dll', 'libssl-3.dll']:
                p = os.path.join(d, name)
                if os.path.exists(p):
                    return p
    except Exception:
        pass
    return None


_libssl_cache = None
_libssl_path_cache = None


def _获取libssl():
    """获取 libssl ctypes 实例 (缓存)。"""
    global _libssl_cache, _libssl_path_cache
    if _libssl_cache is not None:
        return _libssl_cache
    path = _查找libssl()
    if path is None:
        return None
    _libssl_path_cache = path
    lib = ctypes.CDLL(path)
    lib.SSL_export_keying_material.restype = ctypes.c_int
    lib.SSL_export_keying_material.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int,
    ]
    _libssl_cache = lib
    return lib


def _获取ssl指针(sslsock) -> Optional[int]:
    """
    从 ssl.SSLSocket 获取内部 OpenSSL SSL* 指针。
    在 64 位 CPython 3.13 中, _ssl._SSLSocket 的 SSL* 成员偏移为 24。
    """
    try:
        sslobj = sslsock._sslobj
        # Python 3.13: 偏移 24; 旧版本可能是 16
        for offset in [24, 16, 32, 40]:
            ptr = ctypes.c_void_p.from_address(id(sslobj) + offset).value
            if ptr and ptr > 0x10000:
                return ptr
    except Exception:
        pass
    return None


def 导出tls密钥材料(sslsock, length: int = TLS_KEY_EXPORT_LENGTH,
                                label: bytes = TLS_KEY_EXPORT_LABEL) -> Optional[bytes]:
    """
    导出 TLS 密钥材料 (兼容 BoringSSL SSL_export_keying_material)。
    label="adb-label", context=null, use_context=false。

    优先使用 Python 3.13+ 标准库 SSLSocket.export_keying_material()，
    避免 ctypes 硬编码偏移量在不同 Python 版本下取错 SSL* 指针导致
    导出数据异常（表现为后续 AES-GCM 解密报 InvalidTag）。
    标准库不可用时回退到 ctypes 方案。
    """
    # 方案 A：Python 3.13+ 标准库方法（推荐）
    if hasattr(sslsock, 'export_keying_material'):
        try:
            data = sslsock.export_keying_material(label, length, context=None)
            if data and len(data) == length:
                return data
        except Exception:
            pass  # 回退到 ctypes
    # 方案 B：ctypes 调用 OpenSSL（兼容旧 Python）
    lib = _获取libssl()
    if lib is None:
        return None
    ptr = _获取ssl指针(sslsock)
    if ptr is None:
        return None
    out = ctypes.create_string_buffer(length)
    ret = lib.SSL_export_keying_material(
        ptr, out, length, label, len(label), None, 0, 0)
    if ret != 1:
        return None
    return out.raw[:length]


# ── 证书生成 ───────────────────────────────────────────────
def 生成自签名证书() -> Tuple[bytes, bytes]:
    """
    生成自签名 ECDSA P-256 证书 (兼容官方测试用证书类型)。
    返回 (cert_pem, priv_key_pem)。
    """
    key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'US'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Android'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'localhost'),
    ])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256(), default_backend())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


# ── ADB 公钥构造 ───────────────────────────────────────────
def _导入adb协议():
    """延迟导入 adb协议 中的密钥工具（避免模块级循环依赖）。"""
    try:
        from .adb_protocol import _定位密钥路径, 编码adb公钥, 从公钥串提取模数
    except ImportError:
        from adb_protocol import _定位密钥路径, 编码adb公钥, 从公钥串提取模数
    return _定位密钥路径, 编码adb公钥, 从公钥串提取模数


def 生成adb密钥对() -> Tuple[rsa.RSAPrivateKey, bytes]:
    """
    生成 ADB RSA 密钥对 (2048 位, 兼容官方 adb keygen)。
    返回 (private_key, 公钥文本行)。

    公钥格式与 adb_auth_get_userkey() 一致：
      base64(524 字节 android_pubkey_t) + ' 备注'
    设备端把 PeerInfo 里的这一行原样写入 adb_keys，因此必须是文本行；
    若发二进制结构，设备列表里的设备名会变成乱码，且后续 CNXN 验签失败。
    """
    import base64
    _, 编码adb公钥, _ = _导入adb协议()
    private_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048, backend=default_backend())
    b64 = base64.b64encode(编码adb公钥(private_key)).decode('ascii')
    return private_key, (b64 + ' super_adb@python').encode('utf-8')


def 获取持久adb公钥() -> bytes:
    """读取（必要时生成）项目持久密钥 super_adb_key，返回其 .pub 文本行。

    配对必须复用与 CNXN 认证同一把密钥，否则：
      1. 每次配对都是一把新公钥 → 手机"已配对设备"列表无限增长；
      2. 手机存的公钥与签名私钥不配对 → 连接时仍反复弹授权框。
    """
    import base64
    _定位密钥路径, 编码adb公钥, 从公钥串提取模数 = _导入adb协议()
    key_path = _定位密钥路径()
    pub_path = key_path + '.pub'

    private_key = None
    if os.path.isfile(key_path):
        try:
            with open(key_path, 'rb') as f:
                private_key = serialization.load_pem_private_key(
                    f.read(), password=None, backend=default_backend())
        except Exception:
            private_key = None
    if private_key is None:
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend())
        with open(key_path, 'wb') as f:
            f.write(private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()))

    local_n = private_key.public_key().public_numbers().n
    if os.path.isfile(pub_path):
        try:
            with open(pub_path, 'rb') as f:
                content = f.read().strip()
            if 从公钥串提取模数(content) == local_n:
                return content
        except Exception:
            pass

    content = (base64.b64encode(编码adb公钥(private_key)).decode('ascii')
               + ' super_adb@python').encode('utf-8')
    try:
        with open(pub_path, 'wb') as f:
            f.write(content)
    except Exception:
        pass
    return content


def 构建对端信息(public_key: bytes, info_type: int = PEER_INFO_TYPE_RSA_PUB_KEY) -> bytes:
    """
    构造 PeerInfo (固定 8192 字节):
      1B type | 8191B data (公钥 + 零填充)
    """
    if len(public_key) > MAX_PEER_INFO_SIZE - 1:
        raise ValueError(f"公钥过长: {len(public_key)} > {MAX_PEER_INFO_SIZE - 1}")
    data = bytearray(MAX_PEER_INFO_SIZE)
    data[0] = info_type & 0xFF
    data[1:1 + len(public_key)] = public_key
    return bytes(data)


def 解析对端信息(data: bytes) -> Tuple[int, bytes]:
    """解析 PeerInfo, 返回 (type, data_without_padding)。"""
    if len(data) != MAX_PEER_INFO_SIZE:
        raise ValueError(f"PeerInfo 大小应为 {MAX_PEER_INFO_SIZE}, 实际 {len(data)}")
    info_type = data[0]
    payload = data[1:]
    # 去除末尾零填充
    payload = payload.rstrip(b'\x00')
    return info_type, payload


# ── PairingPacket 帧读写 ───────────────────────────────────
def 写入数据包(sock, pkt_type: int, payload: bytes):
    """写入 PairingPacket (通过 TLS 加密通道)。"""
    header = bytearray(HEADER_SIZE)
    header[0] = PAIRING_PACKET_VERSION
    header[1] = pkt_type & 0xFF
    header[2:6] = len(payload).to_bytes(4, 'big')
    sock.sendall(bytes(header) + payload)


def 读取数据包(sock) -> Tuple[int, bytes]:
    """读取 PairingPacket (通过 TLS 加密通道)。"""
    header = _精确接收(sock, HEADER_SIZE)
    if len(header) < HEADER_SIZE:
        raise ConnectionError("连接关闭, 无法读取 PairingPacket 头部")
    version = header[0]
    pkt_type = header[1]
    payload_len = int.from_bytes(header[2:6], 'big')
    if version != PAIRING_PACKET_VERSION:
        raise ValueError(f"PairingPacket 版本不匹配: 期望 {PAIRING_PACKET_VERSION}, 实际 {version}")
    if payload_len == 0 or payload_len > MAX_PEER_INFO_SIZE * 2:
        raise ValueError(f"PairingPacket payload 长度无效: {payload_len}")
    payload = _精确接收(sock, payload_len)
    if len(payload) < payload_len:
        raise ConnectionError("连接关闭, 无法读取 PairingPacket payload")
    return pkt_type, payload


def _精确接收(sock, size: int) -> bytes:
    """从 socket 精确读取 size 字节。"""
    data = b''
    while len(data) < size:
        try:
            chunk = sock.recv(size - len(data))
        except ssl.SSLWantReadError:
            time.sleep(0.01)
            continue
        if not chunk:
            break
        data += chunk
    return data


# ── 配对客户端状态机 ───────────────────────────────────────
class WirelessPairingClient:
    """
    无线调试配对客户端 (兼容官方 PairingConnectionCtx 客户端角色)。

    完整流程:
      1. TCP 连接
      2. TLS 1.3 握手
      3. 导出 TLS 密钥材料, 追加到配对码
      4. SPAKE2 消息交换
      5. 加密 PeerInfo 交换
      6. 配对成功
    """

    def __init__(self, host: str, port: int, code: str,
                 adb_public_key: Optional[bytes] = None,
                 cert_pem: Optional[bytes] = None,
                 priv_key_pem: Optional[bytes] = None,
                 timeout: float = 10.0,
                 log_callback: Optional[Callable[[str], None]] = None):
        self.host = host
        self.port = port
        self.code = code.encode('utf-8') if isinstance(code, str) else code
        self.timeout = timeout
        self.log = log_callback or (lambda msg: None)

        # ADB 公钥：默认复用项目持久密钥（与 CNXN 认证同一把），
        # 避免每次配对生成新密钥导致手机配对列表堆积 + 重复弹授权
        if adb_public_key is not None:
            self.adb_public_key = adb_public_key
        else:
            self.adb_public_key = 获取持久adb公钥()

        # TLS 证书
        if cert_pem is not None and priv_key_pem is not None:
            self.cert_pem = cert_pem
            self.priv_key_pem = priv_key_pem
        else:
            self.cert_pem, self.priv_key_pem = 生成自签名证书()

        self._sock: Optional[ssl.SSLSocket] = None
        self._raw_sock: Optional[socket.socket] = None
        self._auth: Optional[PairingAuth] = None

    def _日志(self, msg: str):
        try:
            self.log(msg)
        except Exception:
            pass

    def 配对(self) -> Tuple[bool, str]:
        """执行完整配对流程。返回 (成功, 消息)。"""
        try:
            self._日志("═══════ 自研ADB配对开始 ═══════")
            self._日志(f"目标: {self.host}:{self.port}")
            self._日志(f"配对码: {self.code.decode('utf-8', errors='replace')}")
            # 步骤 1: TCP 连接
            self._日志(f"正在连接 {self.host}:{self.port} ...")
            self._raw_sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._raw_sock.settimeout(self.timeout)

            # 步骤 2: TLS 1.3 握手
            self._日志("正在进行 TLS 1.3 握手 ...")
            self._tls握手()

            # 步骤 3: 导出 TLS 密钥材料, 追加到配对码
            self._日志("正在导出 TLS 密钥材料 ...")
            tls_key = 导出tls密钥材料(self._sock)
            if tls_key is None:
                return False, "无法导出 TLS 密钥材料 (ctypes 调用失败)"
            self._日志(f"TLS 密钥材料导出成功 ({len(tls_key)} 字节), 前8字节: {tls_key[:8].hex()}")
            spake_password = self.code + tls_key

            # 步骤 4: SPAKE2 消息交换
            self._日志("正在进行 SPAKE2 密钥交换 ...")
            self._auth = PairingAuth('client', spake_password)
            my_msg = self._auth.获取spake2消息()
            self._日志(f"客户端 SPAKE2 消息生成完成 ({len(my_msg)} 字节), 前8字节: {my_msg[:8].hex()}")
            写入数据包(self._sock, TYPE_SPAKE2_MSG, my_msg)
            self._日志("已发送客户端 SPAKE2 消息")

            pkt_type, their_msg = 读取数据包(self._sock)
            if pkt_type != TYPE_SPAKE2_MSG:
                return False, f"期望 SPAKE2_MSG (type={TYPE_SPAKE2_MSG}), 实际 type={pkt_type}"
            self._日志(f"收到服务端 SPAKE2 消息 ({len(their_msg)} 字节), 前8字节: {their_msg[:8].hex()}")

            if not self._auth.初始化加密器(their_msg):
                return False, "SPAKE2 密钥协商失败 (配对码错误或连接被窃取)"
            self._日志("加密器初始化成功 (SPAKE2 + HKDF + AES-128-GCM)")
            if hasattr(self._auth, '_cipher') and self._auth._cipher is not None:
                self._日志(f"  AES密钥前8字节: {self._auth._cipher._aes_key[:8].hex()}")

            # 步骤 5: 加密 PeerInfo 交换
            self._日志("正在交换 PeerInfo ...")
            peer_info = 构建对端信息(self.adb_public_key, PEER_INFO_TYPE_RSA_PUB_KEY)
            encrypted = self._auth.加密(peer_info)
            self._日志(f"PeerInfo 构造完成 ({len(peer_info)} 字节, 公钥 {len(self.adb_public_key)} 字节)")
            写入数据包(self._sock, TYPE_PEER_INFO, encrypted)
            self._日志(f"已发送加密的 PeerInfo ({len(encrypted)} 字节), 前16字节: {encrypted[:16].hex()}")

            pkt_type, their_encrypted = 读取数据包(self._sock)
            if pkt_type != TYPE_PEER_INFO:
                return False, f"期望 PEER_INFO (type={TYPE_PEER_INFO}), 实际 type={pkt_type}"
            self._日志(f"收到服务端响应 (type={pkt_type}, payload_len={len(their_encrypted)})")

            try:
                their_peer_info = self._auth.解密(their_encrypted)
            except Exception as _dec_err:
                # 解密失败（常见 InvalidTag）：打印诊断信息帮助定位
                self._日志(f"❌ PeerInfo 解密失败: {type(_dec_err).__name__}: {_dec_err}")
                self._日志(f"   收到加密 PeerInfo 长度: {len(their_encrypted)} 字节")
                self._日志(f"   收到加密 PeerInfo 前16字节: {their_encrypted[:16].hex()}")
                if hasattr(self._auth, '_cipher') and self._auth._cipher is not None:
                    _c = self._auth._cipher
                    self._日志(f"   AES密钥前8字节: {_c._aes_key[:8].hex()}")
                    self._日志(f"   解密nonce计数器: {_c._dec_sequence}")
                return False, (
                    f"PeerInfo解密失败({type(_dec_err).__name__})，"
                    f"通常因TLS密钥材料不一致或配对码不匹配。"
                    f"请确认手机与PC在同一WiFi、且手机扫描的是当前二维码。"
                )
            their_type, their_data = 解析对端信息(their_peer_info)
            self._日志(f"服务端 PeerInfo: type={their_type}, data_len={len(their_data)}")

            return True, f"Successfully paired to {self.host}:{self.port}"

        except socket.timeout:
            return False, f"连接超时 ({self.host}:{self.port})"
        except ConnectionRefusedError:
            return False, f"连接被拒绝 ({self.host}:{self.port})"
        except Exception as e:
            return False, f"配对失败: {type(e).__name__}: {e}"
        finally:
            self._清理()

    def _tls握手(self):
        """执行 TLS 1.3 客户端握手。"""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # 写入临时证书文件 (load_cert_chain 需要文件路径)
        cert_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        key_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        try:
            cert_file.write(self.cert_pem)
            cert_file.close()
            key_file.write(self.priv_key_pem)
            key_file.close()
            ctx.load_cert_chain(cert_file.name, key_file.name)
            self._sock = ctx.wrap_socket(self._raw_sock, server_hostname=None)
            self._日志(f"TLS 握手成功, 版本: {self._sock.version()}")
        finally:
            try:
                os.unlink(cert_file.name)
            except Exception:
                pass
            try:
                os.unlink(key_file.name)
            except Exception:
                pass

    def _清理(self):
        """清理资源。"""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._raw_sock is not None:
            try:
                self._raw_sock.close()
            except Exception:
                pass
            self._raw_sock = None


# ── 便捷函数 ───────────────────────────────────────────────
def 配对设备(host: str, port: int, code: str,
                timeout: float = 10.0,
                log_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """
    配对设备 (便捷函数)。

    Args:
        host: 设备 IP 地址
        port: 配对端口
        code: 配对码 (6 位数字)
        timeout: 连接超时 (秒)
        log_callback: 日志回调函数

    Returns:
        (成功, 消息)
    """
    client = WirelessPairingClient(host, port, code, timeout=timeout, log_callback=log_callback)
    return client.配对()


# ── 端到端测试 (mock 服务端) ───────────────────────────────
def _运行端到端测试():
    """端到端测试: 本地 mock 服务端 + 客户端。"""
    print("=" * 60)
    print("端到端配对测试 (TLS 1.3 + SPAKE2 + AES-GCM)")
    print("=" * 60)

    test_code = b'917846'
    server_cert, server_key = 生成自签名证书()
    client_cert, client_key = 生成自签名证书()
    _, client_adb_pub = 生成adb密钥对()
    _, server_adb_pub = 生成adb密钥对()

    server_result = {'paired': False, 'peer_pubkey': None}
    server_ready = threading.Event()
    server_port = [0]

    def 模拟服务端():
        """模拟 Android 设备的配对服务端。"""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        cert_f = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        key_f = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        try:
            cert_f.write(server_cert)
            cert_f.close()
            key_f.write(server_key)
            key_f.close()
            ctx.load_cert_chain(cert_f.name, key_f.name)
        finally:
            os.unlink(cert_f.name)
            os.unlink(key_f.name)

        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(('127.0.0.1', 0))
        srv.listen(1)
        server_port[0] = srv.getsockname()[1]
        server_ready.set()

        try:
            conn, _ = srv.accept()
            conn.settimeout(15)
            ssl_conn = ctx.wrap_socket(conn, server_side=True)
            print(f"[Server] TLS 握手成功: {ssl_conn.version()}")

            # 导出 TLS 密钥材料
            tls_key = 导出tls密钥材料(ssl_conn)
            if tls_key is None:
                print("[Server] 无法导出 TLS 密钥材料!")
                return
            spake_password = test_code + tls_key
            print(f"[Server] TLS 密钥材料导出成功 ({len(tls_key)} 字节)")

            # SPAKE2
            auth = PairingAuth('server', spake_password)
            my_msg = auth.获取spake2消息()

            # 读取客户端消息
            pkt_type, their_msg = 读取数据包(ssl_conn)
            print(f"[Server] 收到客户端 SPAKE2 消息 ({len(their_msg)} 字节)")

            # 发送服务端消息
            写入数据包(ssl_conn, TYPE_SPAKE2_MSG, my_msg)
            print(f"[Server] 发送服务端 SPAKE2 消息 ({len(my_msg)} 字节)")

            if not auth.初始化加密器(their_msg):
                print("[Server] SPAKE2 初始化失败!")
                return
            print("[Server] 加密器初始化成功")

            # 读取客户端 PeerInfo
            pkt_type, their_enc = 读取数据包(ssl_conn)
            their_peer = auth.解密(their_enc)
            their_type, their_data = 解析对端信息(their_peer)
            print(f"[Server] 客户端 PeerInfo: type={their_type}, pubkey_len={len(their_data)}")
            server_result['peer_pubkey'] = their_data

            # 发送服务端 PeerInfo
            server_peer = 构建对端信息(server_adb_pub, PEER_INFO_TYPE_RSA_PUB_KEY)
            enc = auth.加密(server_peer)
            写入数据包(ssl_conn, TYPE_PEER_INFO, enc)
            print("[Server] 已发送服务端 PeerInfo")

            server_result['paired'] = True
            print("[Server] 配对成功!")
            ssl_conn.close()
        except Exception as e:
            print(f"[Server] 错误: {e}")
        finally:
            srv.close()

    # 启动服务端
    t = threading.Thread(target=mock_server, daemon=True)
    t.start()
    server_ready.wait(timeout=5)
    time.sleep(0.2)

    port = server_port[0]
    print(f"模拟服务端监听端口: {port}")

    # 运行客户端 (使用与测试相同的公钥, 以便验证)
    logs = []
    client = WirelessPairingClient('127.0.0.1', port, '917846',
                                    adb_public_key=client_adb_pub,
                                    cert_pem=client_cert, priv_key_pem=client_key,
                                    timeout=15, log_callback=lambda m: logs.append(m))
    ok, msg = client.配对()
    for m in logs:
        print(f"[Pairing] {m}")

    print(f"\n客户端结果: ok={ok}, msg={msg}")
    print(f"服务端结果: paired={server_result['paired']}")

    if server_result['peer_pubkey']:
        print(f"服务端收到的客户端公钥: {server_result['peer_pubkey'][:16].hex()}...")
        if server_result['peer_pubkey'] == client_adb_pub:
            print("公钥匹配验证通过!")
        else:
            print("警告: 公钥不匹配!")

    t.join(timeout=5)

    if ok and server_result['paired']:
        print("\n" + "=" * 60)
        print("端到端配对测试通过!")
        print("=" * 60)
        return True
    else:
        print("\n" + "=" * 60)
        print("端到端配对测试失败!")
        print("=" * 60)
        return False


if __name__ == '__main__':
    _运行端到端测试()
