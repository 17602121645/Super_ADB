# -*- coding: utf-8 -*-
"""
自研 ADB 协议核心模块（最终修复版）
====================================
修复点:
  1. [关键] _获取公钥 使用 ADB 标准格式: 4字节魔数 "ADBP" + n(256字节,小端) + e(3字节,小端)
     总长 = 263 字节。旧版用 struct.pack('<IIII', total_len,256,3,1) 生成 275 字节，
     导致设备端 adb_keys 存储的公钥 n/e 偏移 12 字节，签名永远验证失败。
  2. 连接池 _连接池 支持并发首次建连去重（_建连中 Event），避免多线程同时认证。
  3. 保留原有 sync 推送/拉取稳定性修复。

除 _获取公钥 / 新增连接池外，其余协议实现（CNXN/AUTH/OPEN/WRTE 等）与原版一致。
"""
from __future__ import annotations

import struct
import socket
import sys
import time
import zlib
import os
import queue
import threading
import concurrent.futures
from typing import Optional, Tuple, Callable, Set, Dict, List

# ADB 协议版本
ADB_VERSION = 0x01000001  # 官方adb使用0x01000001（skip checksum）
# ★ 版本协商常量：低于该版本的旧 adbd（如部分 IPTV 盒子）不认「跳过校验和」，
# 仍强制校验每个消息帧的 checksum 字段，恒发 0 会被直接断连（shell 一开即断）。
A_VERSION_SKIP_CHECKSUM = 0x01000001
ADB_MAX_PAYLOAD = 1048576  # 1MB
INITIAL_DELAYED_ACK_BYTES = 32 * 1024 * 1024  # delayed_ack 初始发送窗口(32MB, 对齐官方 adb.h)
# 设备端 sync 服务单个 DATA 块上限固定 64KB（adb-master/file_sync_service.h 的
# SYNC_DATA_MAX，官方 adb 客户端也按它分块）；超过会被设备端
# 以 "oversize data message" 拒绝并中止推送。
SYNC_DATA_MAX = 64 * 1024

# ADB 命令常量
CMD_CNXN = 0x4e584e43  # "CNXN"
CMD_AUTH = 0x48545541  # "AUTH"
CMD_OPEN = 0x4e45504f  # "OPEN"
CMD_OKAY = 0x59414b4f  # "OKAY"
CMD_WRTE = 0x45545257  # "WRTE"
CMD_CLSE = 0x45534c43  # "CLSE"

# AUTH 类型
AUTH_TOKEN = 1
AUTH_SIGNATURE = 2
AUTH_RSAPUBLICKEY = 3

# A_STLS（无线调试 TLS 端口）：0x534c5453 = "SLTS"
CMD_STLS = 0x534c5453

# ADB 连接状态
STATE_OFFLINE = 0
STATE_AUTH = 1
STATE_DEVICE = 2

# 公钥格式：4字节魔数 "ADBP" + n(256) + e(3) = 263
ADB_PUBKEY_MAGIC = b'ADBP'


# 密钥所在目录候选（按新→旧顺序）。'配置' 为向后兼容常量：v1 用中文目录名，
# 老用户升级后设备已授权的密钥仍能被找到 / 迁移，勿删。
_LEGACY_KEY_DIRS = ['config', '配置']


def _定位密钥路径():
    """统一定位 super_adb_key 私钥路径（TCP 与 USB 共用同一份密钥）。

    - 源码模式：项目根 config/ 下；
    - frozen（打包 exe）：exe 旁 config/ 下（可写目录，与 _config_path 一致），
      首次访问自动从以下旧位置迁移，保证源码与打包版共用同一密钥——
      设备已给源码密钥授权过，打包版迁移后可直接签名通过，无需重复授权：
        1. _internal/config/（旧打包版 __file__ 推导路径）
        2. 源码目录 config/（开发机上打包版直接复用源码密钥）
        3. 上述两处的旧中文目录 配置/（v1 命名，见 _LEGACY_KEY_DIRS）
    """
    fname = 'super_adb_key'
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
        if sys.platform == 'darwin':
            # macOS 冻结版与 _config_path 一致：~/Library/Application Support/Super_ADB
            base = os.path.expanduser('~/Library/Application Support/Super_ADB')
        new_dir = os.path.join(base, 'config')
        new_path = os.path.join(new_dir, fname)
        if not os.path.isfile(new_path):
            _meipass = getattr(sys, '_MEIPASS', '')
            _src_root = os.path.dirname(os.path.dirname(os.path.dirname(base)))
            candidates = []
            for _d in _LEGACY_KEY_DIRS:
                # 旧打包版路径：_internal/<d>/（__file__ 推导）
                candidates.append(os.path.join(_meipass, _d, fname))
                # 开发机：exe 在源码树内（平台根/build_tools/dist/Super_ADB）→ 上溯 3 级到平台根 <d>/
                candidates.append(os.path.join(_src_root, _d, fname))
            for old in candidates:
                if old and os.path.isfile(old):
                    try:
                        os.makedirs(new_dir, exist_ok=True)
                        import shutil
                        shutil.copy(old, new_path)
                        pub_old = old + '.pub'
                        if os.path.isfile(pub_old):
                            shutil.copy(pub_old, new_path + '.pub')
                        print(f'[自研adb] 密钥已迁移: {old} -> {new_path}')
                    except Exception as e:
                        print(f'[自研adb] 密钥迁移失败: {e}')
                    break
        return new_path
    _项目根 = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _new = os.path.join(_项目根, 'config', fname)
    if not os.path.isfile(_new):
        for _d in _LEGACY_KEY_DIRS[1:]:
            _old = os.path.join(_项目根, _d, fname)
            if os.path.isfile(_old):
                return _old
    return _new
ADB_PUBKEY_SIZE = 4 + 256 + 3  # 263


def 编码adb公钥(private_key) -> bytes:
    """把 RSA 公钥编码为 524 字节 android_pubkey_t 结构。

    与官方 adb 的 android_pubkey_encode 完全一致（设备端 adbd_auth_verify
    要求 base64 解码后必须恰好是 524 字节，否则该行公钥被直接丢弃）。
    结构（小端）:
      uint32_t len = 64       // n 的 32 位字数
      uint32_t n0inv          // -n^(-1) mod 2^32
      uint32_t r[64]          // n，小端 32 位字数组（256字节）
      uint32_t rr[64]         // R^2 mod n，小端 32 位字数组，R=2^2048
      uint32_t exponent       // 65537
    """
    nums = private_key.public_key().public_numbers()
    n, e = nums.n, nums.e

    n0inv = (-pow(n % (1 << 32), -1, 1 << 32)) % (1 << 32)

    r_words = []
    n_tmp = n
    for _ in range(64):
        r_words.append(n_tmp & 0xFFFFFFFF)
        n_tmp >>= 32

    rr = (1 << 4096) % n  # R^2 mod n, R = 2^2048
    rr_words = []
    rr_tmp = rr
    for _ in range(64):
        rr_words.append(rr_tmp & 0xFFFFFFFF)
        rr_tmp >>= 32

    key_data = struct.pack('<II', 64, n0inv)
    key_data += struct.pack('<64I', *r_words)
    key_data += struct.pack('<64I', *rr_words)
    key_data += struct.pack('<I', e)
    assert len(key_data) == 524, f'公钥长度异常: {len(key_data)} (应为524)'
    return key_data


def 从公钥串提取模数(content: bytes) -> int:
    """从 'base64(524字节) 备注' 格式的公钥串中提取 RSA 模数 n，
    用于校验 .pub 文件与当前私钥是否配对。"""
    import base64
    b64 = content.split()[0]
    decoded = base64.b64decode(b64)
    if len(decoded) != 524:
        raise ValueError(f'公钥解码长度 {len(decoded)} != 524')
    words = struct.unpack_from('<64I', decoded, 8)
    n = 0
    for i in range(63, -1, -1):
        n = (n << 32) | words[i]
    return n


def _生成adb连接证书(key_path):
    """从持久 adbkey（RSA）生成自签名客户端证书，用于 A_STLS 无线调试 TLS 连接。

    ★ 必须逐字段对齐 AOSP crypto/x509_generator.cpp 的 GenerateX509Certificate：
      - CN=Adb（注意大写 A；小写 adb 曾被实测被 adbd 拒绝）
      - serial = 1（AOSP: ASN1_INTEGER_set(serial, 1)）
      - 扩展: basicConstraints critical CA:TRUE；keyUsage critical
        keyCertSign,cRLSign,digitalSignature；subjectKeyIdentifier=hash
      - 有效期 10 年，SHA256 自签名
    实测：缺失以上任一关键字段（如 CN 大小写 / 扩展缺失）会导致 adbd 回
    CERTIFICATE_UNKNOWN 拒绝客户端证书。
    返回 (cert_pem, key_pem)。
    """
    import datetime
    from cryptography.hazmat.primitives import serialization
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    with open(key_path, 'rb') as f:
        priv = serialization.load_pem_private_key(f.read(), password=None)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, 'US'),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Android'),
        x509.NameAttribute(NameOID.COMMON_NAME, 'Adb'),
    ])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(priv.public_key()).serial_number(1)
            .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, key_cert_sign=True, crl_sign=True,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(priv.public_key()),
                          critical=False)
            .sign(priv, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())
    return cert_pem, key_pem


def _计算魔数(cmd: int) -> int:
    return cmd ^ 0xffffffff


def _计算校验和(data: bytes) -> int:
    """ADB协议checksum: payload所有字节的和，取低32位（不是CRC32！）"""
    return sum(data) & 0xffffffff


def 打包消息(command: int, arg0: int, arg1: int, payload: bytes = b'',
             force_checksum: bool = False) -> bytes:
    # 认证阶段（CNXN/AUTH）：协商尚未完成，与官方 send_packet 一致计算真实校验和，
    # 部分老设备（如小米盒子 adbd）会校验该字段，恒发 0 会导致签名被拒、反复要求授权。
    # force_checksum=True：协商后仍强制补真实校验和（设备版本 < A_VERSION_SKIP_CHECKSUM
    # 的老 adbd，见 AdbConnection._发送）。
    if command in (CMD_CNXN, CMD_AUTH) or force_checksum:
        checksum = _计算校验和(payload)
    else:
        checksum = 0  # 建连后协商版本 >= A_VERSION_SKIP_CHECKSUM，跳过校验和
    header = struct.pack('<IIIIII', command, arg0, arg1, len(payload), checksum, _计算魔数(command))
    return header + payload


def 解包消息(data: bytes) -> Tuple[int, int, int, bytes]:
    if len(data) < 24:
        raise ValueError(f"消息太短: {len(data)} 字节")
    command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', data[:24])
    if magic != _计算魔数(command):
        raise ValueError(f"magic 不匹配: 期望 {_计算魔数(command):#x}, 实际 {magic:#x}")
    payload = data[24:24 + length]
    # 设备端在版本协商前可能发 crc=0 的包，且协商后双方都跳过校验，仅在头部声明非0时校验
    if crc != 0 and _计算校验和(payload) != crc:
        raise ValueError("checksum 校验失败")
    return command, arg0, arg1, payload


class AdbMessage:
    def __init__(self, command: int, arg0: int = 0, arg1: int = 0, payload: bytes = b''):
        self.command = command
        self.arg0 = arg0
        self.arg1 = arg1
        self.payload = payload

    def 打包(self) -> bytes:
        return 打包消息(self.command, self.arg0, self.arg1, self.payload)

    def 拆分打包(self):
        # 拆分为 (24字节消息头, payload)，用于 USB 分两次发送。
        # USB 上必须先发头再发 payload（与官方 adb windows.cpp:332 一致），
        # 一次性发送会导致部分设备（荣耀/华为等）不响应。
        if self.command in (CMD_CNXN, CMD_AUTH):
            checksum = _计算校验和(self.payload)
        else:
            checksum = 0
        header = struct.pack('<IIIIII', self.command, self.arg0, self.arg1,
                             len(self.payload), checksum, _计算魔数(self.command))
        return header, self.payload

    @classmethod
    def 解包(cls, data: bytes) -> 'AdbMessage':
        cmd, a0, a1, payload = 解包消息(data)
        return cls(cmd, a0, a1, payload)

    @property
    def 命令名(self) -> str:
        names = {CMD_CNXN: 'CNXN', CMD_AUTH: 'AUTH', CMD_OPEN: 'OPEN',
                 CMD_OKAY: 'OKAY', CMD_WRTE: 'WRTE', CMD_CLSE: 'CLSE'}
        return names.get(self.command, f'UNKNOWN({self.command:#x})')

    def __repr__(self):
        return f'<AdbMessage {self.命令名} arg0={self.arg0:#x} arg1={self.arg1:#x} len={len(self.payload)}>'


# ─────────────────── 连接池（线程安全 + 并发建连去重）───────────────────

class _池化连接:
    """池中的连接条目。"""
    def __init__(self, conn: 'AdbConnection'):
        self.conn = conn
        self.借用时间 = time.time()
        self.空闲起始 = 0.0

    @property
    def 已空闲秒(self) -> float:
        return time.time() - self.空闲起始 if self.空闲起始 else 0.0

    def 关闭(self):
        try:
            self.conn.关闭()
        except Exception:
            pass


class _连接池:
    """每 (host, port) 维护一组已认证连接。

    特性:
    - 借用优先: 线程绑定 > 空闲池 > 新建
    - 设备级建连锁：同一设备只有一个线程能真正建连（包括AUTH授权），
      其他线程等待建连完成后复用空闲连接，避免多次授权弹窗。
    - 归还: 放回空闲列表，记录空闲起始。
    - 清理: 空闲超过 最大空闲秒 的连接自动关闭。
    """
    最大连接数 = 8
    最大空闲秒 = 90
    借用超时秒 = 20

    def __init__(self):
        self._锁 = threading.Lock()
        self._空闲: Dict[Tuple[str, int], List[_池化连接]] = {}
        self._借出: Set[_池化连接] = set()
        self._线程绑定: Dict[int, _池化连接] = {}
        # 设备级建连锁：同一设备只有一个线程能真正建连（包括AUTH授权）
        self._建连锁: Dict[Tuple[str, int], threading.Lock] = {}

    def 借用(self, host: str, port: int, timeout: float, key_path: str,
             log_callback=None, burst: Optional[bool] = None) -> AdbConnection:
        tid = threading.get_ident()
        key = (host, port)

        while True:
            with self._锁:
                # 1. 当前线程已绑定且可用 → 直接复用
                bound = self._线程绑定.get(tid)
                if bound and self._连接可用(bound):
                    if burst is None or (bound.conn._delayed_ack if burst else not bound.conn._delayed_ack):
                        return bound.conn

                # 2. 有空闲连接 → 取一个
                pool = self._空闲.get(key, [])
                alive = [c for c in pool if self._连接可用(c) and c.已空闲秒 < self.最大空闲秒]
                if burst is True:
                    alive = [c for c in alive if c.conn._delayed_ack]
                elif burst is False:
                    alive = [c for c in alive if not c.conn._delayed_ack]
                if alive:
                    c = alive.pop()
                    # ★ alive 是局部副本，必须回写空闲表，否则连接"借出"后仍留在
                    # 空闲表里，会被后续借用再次分发——同一条物理 socket 被多方
                    # 并发读写（串报文、server 独占连接被隧道借用关闭等事故根源）
                    self._空闲[key] = alive
                    self._借出.add(c)
                    self._线程绑定[tid] = c
                    return c.conn
                self._空闲.pop(key, None)

                # 3. 池空：获取设备级建连锁（确保同一设备只有一个线程在建连/授权）
                if key not in self._建连锁:
                    self._建连锁[key] = threading.Lock()
                dev_lock = self._建连锁[key]

            # ★ 在锁外获取设备级建连锁，持有整个建连+授权过程
            print(f'[自研adb][T{tid}] 等待设备建连锁 {host}:{port}...')
            acquired = dev_lock.acquire(timeout=self.借用超时秒)
            if not acquired:
                raise RuntimeError(f"ADB 连接池建连等待超时: {host}:{port}")
            print(f'[自研adb][T{tid}] 获取设备建连锁成功，开始建连 {host}:{port}')
            try:
                # 拿到锁后再检查一次（可能别的线程已经建连好了）
                with self._锁:
                    pool = self._空闲.get(key, [])
                    alive = [c for c in pool if self._连接可用(c) and c.已空闲秒 < self.最大空闲秒]
                    if burst is True:
                        alive = [c for c in alive if c.conn._delayed_ack]
                    elif burst is False:
                        alive = [c for c in alive if not c.conn._delayed_ack]
                    if alive:
                        c = alive.pop()
                        self._空闲[key] = alive  # ★ 同上：弹出后必须回写空闲表
                        self._借出.add(c)
                        self._线程绑定[tid] = c
                        return c.conn

                # 真正建连（包括AUTH授权，整个过程持有dev_lock）
                new_conn = self._新建(host, port, timeout, key_path, log_callback, burst)
                print(f'[自研adb][T{tid}] 建连成功，释放设备建连锁')

                with self._锁:
                    c = _池化连接(new_conn)
                    self._借出.add(c)
                    self._线程绑定[tid] = c
                    return new_conn
            finally:
                dev_lock.release()

    def 剥离(self, conn: AdbConnection):
        """把连接从池的跟踪结构（借出/线程绑定）中移除，但不关闭。

        调用方将借出的连接提升为长期持有的“主连接”时使用：
        剥离后池不会再通过线程绑定/空闲池把同一连接分发给其他借用路径，
        避免两个调用方并发读写同一条 socket（协议帧交错损坏）。
        连接的生命周期此后由调用方负责。
        """
        tid = threading.get_ident()
        with self._锁:
            for c in list(self._借出):
                if c.conn is conn:
                    self._借出.discard(c)
                    break
            # 空闲表也必须移除：剥离后的连接由调用方独占，
            # 绝不能留在空闲表里被后续借用再次分发
            key = (conn.host, conn.port)
            pool = self._空闲.get(key)
            if pool:
                self._空闲[key] = [c for c in pool if c.conn is not conn]
            bound = self._线程绑定.get(tid)
            if bound is not None and bound.conn is conn:
                self._线程绑定.pop(tid, None)
        return conn

    def 归还(self, conn: AdbConnection):
        tid = threading.get_ident()
        with self._锁:
            for c in list(self._借出):
                if c.conn is conn:
                    self._借出.discard(c)
                    c.空闲起始 = time.time()
                    pool = self._空闲.setdefault((conn.host, conn.port), [])
                    pool.append(c)
                    break
            self._线程绑定.pop(tid, None)

    def 已有可用连接(self, host: str, port: int) -> bool:
        """检查池里是否已有该设备的可用连接（空闲或借出中）。"""
        with self._锁:
            key = (host, port)
            pool = self._空闲.get(key, [])
            # 空闲且未超时的
            if any(self._连接可用(c) and c.已空闲秒 < self.最大空闲秒 for c in pool):
                return True
            # 借出中的（说明该设备已认证过）
            if any(c.conn.host == host and c.conn.port == port for c in self._借出):
                return True
            return False

    def 关闭(self, host: str = None, port: int = None):
        """关闭指定设备（或所有）的连接，用于 root 重启后清池。"""
        with self._锁:
            if host is not None:
                key = (host, port)
                for c in list(self._借出):
                    if (c.conn.host, c.conn.port) == key:
                        self._借出.discard(c)
                        c.关闭()
                if key in self._空闲:
                    for c in self._空闲[key]:
                        c.关闭()
                    del self._空闲[key]
                for tid, c in list(self._线程绑定.items()):
                    if (c.conn.host, c.conn.port) == key:
                        self._线程绑定.pop(tid, None)
                self._建连锁.pop(key, None)
            else:
                all_conns = list(self._借出) + [
                    c for pool in self._空闲.values() for c in pool
                ]
                self._借出.clear()
                self._空闲.clear()
                self._线程绑定.clear()
                self._建连锁.clear()
                for c in all_conns:
                    c.关闭()

    def 清理空闲(self):
        with self._锁:
            for key, pool in list(self._空闲.items()):
                alive = [c for c in pool if c.已空闲秒 < self.最大空闲秒 and self._连接可用(c)]
                for c in pool:
                    if c not in alive:
                        c.关闭()
                if alive:
                    self._空闲[key] = alive
                else:
                    del self._空闲[key]

    @staticmethod
    def _连接可用(c: _池化连接) -> bool:
        return c.conn.state == STATE_DEVICE and c.conn.sock is not None

    def _新建(self, host: str, port: int, timeout: float, key_path: str,
             log_callback=None, burst: Optional[bool] = None) -> 'AdbConnection':
        """新建连接（调用方应持有设备级建连锁）。"""
        conn = AdbConnection(host, port, timeout=timeout, key_path=key_path, burst=burst)
        conn.log_callback = log_callback
        try:
            ok = conn.连接()
        except Exception as e:
            # 保留原始异常细节（TCP 拒绝/超时/协议异常），否则上层只见
            # "ADB 连接失败: ip:port" 一句，无法定位原因
            # ★ 必须关闭 socket：失败的连接如果不关，设备端会残留半开连接。
            #   adbd 对同一客户端的并发连接数有限，残留连接会让后续建连
            #   收不到 AUTH TOKEN（表现为「不弹窗」「第二次连接失败」）。
            try:
                conn.关闭()
            except Exception:
                pass
            raise RuntimeError(f"ADB 连接失败: {host}:{port} ({e})") from e
        if not ok:
            原因 = conn._认证失败原因 or '认证未通过'
            try:
                conn.关闭()      # 同上：认证未通过也要释放 socket
            except Exception:
                pass
            raise RuntimeError(
                f"ADB 连接失败: {host}:{port}（{原因}。请在设备上允许 USB/无线调试授权；"
                f"若设备无授权弹窗，可将已授权机器的 config/super_adb_key(+.pub) 复制到本程序 "
                f"config/ 目录，密钥={conn._key_path}）")
        return conn


_全局池 = _连接池()


def 借用连接(host: str, port: int = 5555, timeout: float = 10.0,
            key_path: str = None, log_callback=None,
            burst: Optional[bool] = None) -> AdbConnection:
    return _全局池.借用(host, port, timeout, key_path, log_callback, burst)


def 归还连接(conn: AdbConnection):
    _全局池.归还(conn)


class _AdbStreamSocket:
    """把一条 adbd 流包装成 socket 风格对象（recv/sendall/settimeout/close）。

    后台线程解析 ADB 帧、payload 入队列；sendall 拆块 WRTE 并按 OKAY 流控。
    供 ScrcpySession 直连 localabstract 隧道使用（forward 模式无需端口转发）。
    """

    def __init__(self, conn: 'AdbConnection', local_id: int):
        self._conn = conn
        self._local_id = local_id
        self._超时 = None
        self._队列 = queue.Queue()
        self._EOF = False
        self._已关 = False
        self._残余 = b''
        self._发送锁 = threading.Lock()
        self._待确认 = threading.Event()
        self._待确认.set()  # 初始允许发第一包
        conn.sock.settimeout(30.0)
        self._读线程 = threading.Thread(target=self._读循环, daemon=True)
        self._读线程.start()

    def _读循环(self):
        conn = self._conn
        try:
            while True:
                try:
                    msg = conn._接收消息()
                except socket.timeout:
                    continue  # 流空闲，继续等
                except Exception:
                    break
                if msg.command == CMD_WRTE and msg.arg1 == self._local_id:
                    self._队列.put(msg.payload)
                    try:
                        conn._回OKAY(self._local_id, conn._remote_id, len(msg.payload))
                    except Exception:
                        break
                elif msg.command == CMD_OKAY and msg.arg1 == self._local_id:
                    self._待确认.set()
                elif msg.command == CMD_CLSE:
                    if msg.arg1 == self._local_id:
                        break
                # 其他帧（旧流残留）忽略
        except Exception:
            pass
        self._EOF = True
        self._待确认.set()  # 唤醒可能阻塞在发送等待的线程
        try:
            self._队列.put_nowait(b'')  # EOF 标记
        except Exception:
            pass

    # ── socket 风格接口 ──

    def settimeout(self, t):
        self._超时 = t

    def setsockopt(self, *args, **kwargs):
        pass  # 兼容旧代码调用，无实际作用

    def recv(self, n):
        buf = self._残余
        if buf:
            data, self._残余 = buf[:n], buf[n:]
            return data
        if self._EOF:
            return b''
        try:
            item = self._队列.get(timeout=self._超时)
        except queue.Empty:
            raise socket.timeout('recv 超时')
        if item == b'':
            self._EOF = True
            return b''
        if len(item) > n:
            self._残余 = item[n:]
            item = item[:n]
        return item

    def sendall(self, data):
        view = memoryview(data)
        for i in range(0, len(view), ADB_MAX_PAYLOAD):
            chunk = bytes(view[i:i + ADB_MAX_PAYLOAD])
            with self._发送锁:
                while not self._待确认.wait(timeout=10.0):
                    if self._EOF:
                        raise ConnectionError('隧道已关闭')
                if self._EOF:
                    raise ConnectionError('隧道已关闭')
                self._待确认.clear()
                self._conn._发送(AdbMessage(CMD_WRTE, self._local_id,
                                            self._conn._remote_id, chunk))
        return len(data)

    def close(self):
        if self._已关:
            return
        self._已关 = True
        self._EOF = True
        self._待确认.set()
        try:
            self._conn._发送(AdbMessage(CMD_CLSE, self._local_id, self._conn._remote_id))
        except Exception:
            pass
        try:
            self._conn.关闭()
        except Exception:
            pass
        try:
            self._队列.put_nowait(b'')
        except Exception:
            pass


def 剥离连接(conn: AdbConnection):
    return _全局池.剥离(conn)


def 关闭设备连接(host: str, port: int = 5555):
    _全局池.关闭(host, port)


def 关闭全部连接():
    _全局池.关闭()


def 清理空闲连接():
    _全局池.清理空闲()


def 已有可用连接(host: str, port: int = 5555) -> bool:
    return _全局池.已有可用连接(host, port)


def 获取已连接设备() -> list:
    """返回连接池中所有已连接设备的 [(host, port), ...]（去重）。"""
    devices = set()
    with _全局池._锁:
        # 空闲池中的设备
        for (host, port), pool in _全局池._空闲.items():
            if any(_全局池._连接可用(c) for c in pool):
                devices.add((host, port))
        # 借出中的设备
        for c in _全局池._借出:
            devices.add((c.conn.host, c.conn.port))
        # 线程绑定中的设备
        for c in _全局池._线程绑定.values():
            devices.add((c.conn.host, c.conn.port))
    return sorted(devices, key=lambda x: [int(p) for p in x[0].split('.')])


# ─────────────────── 单连接（协议层，线程不安全，由池管理）───────────────────

class AdbConnection:
    """单个 ADB socket 连接，对应一个设备 transport。

    注意: 本类不是线程安全的（协议帧必须有序）。所有并发访问由上层 _连接池 保证——
    每个线程/操作独占一个 AdbConnection。
    """

    def __init__(self, host: str, port: int = 5555, timeout: float = 10.0, key_path: str = None,
                 burst: Optional[bool] = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.state = STATE_OFFLINE
        self._local_id = 0
        self._remote_id = 0
        self._预读数据 = b''
        self._max_payload = ADB_MAX_PAYLOAD
        self._delayed_ack = False   # 连接级：双方协商 delayed_ack 成功才 True
        # ★ 老设备校验和协商：设备 CNXN 宣告版本 < A_VERSION_SKIP_CHECKSUM 时，
        # 该连接所有帧必须带真实校验和（官方 adb 客户端同款自适应）。初始 False
        # 只影响 CNXN/AUTH 之前的帧——这两类帧本就强制带校验和，无副作用。
        self._设备版本 = 0
        self._跳过校验和 = False
        self._burst = burst         # 连接模式请求：None=自动 / True=强制burst / False=强制传统
        self._流ASB = 0             # 当前流可用发送额度（delayed_ack 窗口记账）
        self._认证失败原因 = ''   # 认证未通过时的具体原因，供上层错误消息展示
        self.log_callback = None  # 授权等关键事件回调到主窗口输出栏
        if key_path:
            self._key_path = key_path
        else:
            self._key_path = _定位密钥路径()

    # ── 传输超时抽象：TCP 用 sock，USB 子类覆盖为 _usb 超时（毫秒）──
    def _读取传输超时(self) -> float:
        """当前传输层读超时（秒）。USB 模式由 UsbAdbConnection 覆盖。"""
        if self.sock is not None:
            try:
                return self.sock.gettimeout()
            except Exception:
                pass
        return self.timeout

    def _设置传输超时(self, 秒: float) -> None:
        """设置传输层读超时（秒）。USB 模式由 UsbAdbConnection 覆盖。"""
        if self.sock is not None:
            try:
                self.sock.settimeout(秒)
            except Exception:
                pass

    def _协商载荷(self, device_max: int) -> int:
        if 256 <= device_max <= 1024 * 1024:
            return device_max
        return ADB_MAX_PAYLOAD

    def _解析设备features(self, banner: bytes) -> None:
        """从设备 CNXN banner 解析 features，决定是否启用 delayed_ack。

        delayed_ack 是连接级协商：客户端 banner 声明 + 设备 banner 声明，双方
        都支持才启用（对应官方 CanUseFeature("delayed_ack")）。启用后该连接
        所有流的 OKAY 都必须带 4 字节 int32 增量确认（见 _回OKAY）。
        """
        self._delayed_ack = False
        if os.environ.get('SUPER_ADB_NO_DELAYED_ACK'):
            return
        if self._burst is False:
            return
        try:
            txt = banner.decode('utf-8', errors='replace')
            feats = txt.split('features=')[-1].split(',')
            if 'delayed_ack' in feats:
                self._delayed_ack = True
                print('[自研adb] 设备支持 delayed_ack（Burst Mode），传输已启用')
        except Exception:
            self._delayed_ack = False

    def _发OPEN(self, local_id: int, service: str) -> None:
        """发 OPEN 报文。delayed_ack 下 arg1 = INITIAL_DELAYED_ACK_BYTES 宣告启用。

        官方语义（sockets.cpp connect_to_remote）：arg1 非零表示客户端想用
        delayed_ack；设备端若不支持（或客户端没声明而设备支持）会回 CLSE 拒绝。
        因此 arg1 必须与 _delayed_ack 严格一致。
        """
        arg1 = INITIAL_DELAYED_ACK_BYTES if self._delayed_ack else 0
        self._发送(AdbMessage(CMD_OPEN, local_id, arg1, service.encode() + b'\0'))

    def _回OKAY(self, local_id: int, remote_id: int, 确认字节: int = 0) -> None:
        """回 OKAY 报文。delayed_ack 下带 4 字节 int32 增量确认，否则空 payload。

        官方语义（adb.cpp send_ready + local_socket_ack）：OKAY 的 payload 是
        「自上次 OKAY 以来实际冲刷到 fd 的字节数」（增量，可负）。接收方收到
        带 payload 的 OKAY 后 ASB += 该值恢复发送。注意：delayed_ack 协商成功
        后所有 OKAY 都必须带 payload，否则对端 available_send_bytes 不匹配
        会直接丢弃该 OKAY → 死锁。
        """
        payload = struct.pack('<i', 确认字节) if self._delayed_ack else b''
        self._发送(AdbMessage(CMD_OKAY, local_id, remote_id, payload))

    def _解析OKAY字节(self, msg) -> int:
        """从 OKAY 消息解析 acked_bytes（增量确认）。无 payload 或非 4 字节返回 0。"""
        if self._delayed_ack and len(msg.payload) == 4:
            try:
                return struct.unpack('<i', msg.payload)[0]
            except Exception:
                return 0
        return 0

    def _发送流内(self, local_id: int, payload: bytes, 场景: str = '推送') -> None:
        """发送一个 WRTE（传输层流控）。

        delayed_ack：窗口化发送——ASB 充足才发，不足则收 OKAY 补充额度再发；
        期间带外 WRTE（sync 的 FAIL 响应等）就地回 OKAY 并处理。传统模式：
        发后等一个 OKAY（window=1），行为与旧代码一致。
        """
        if self._delayed_ack:
            while self._流ASB < len(payload):
                msg = self._接收消息()
                if msg.command == CMD_OKAY:
                    if msg.arg1 != local_id:
                        continue  # 旧流残留
                    self._流ASB += self._解析OKAY字节(msg)
                elif msg.command == CMD_WRTE:
                    if msg.arg1 != local_id:
                        # 旧流残留数据：回 OKAY 维持流控，丢弃
                        self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                        continue
                    if msg.payload[:4] == b'FAIL':
                        err_len = struct.unpack('<I', msg.payload[4:8])[0]
                        err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')
                        raise RuntimeError(f"{场景}失败: {err}")
                    # 其它带外数据：回 ack 后继续等额度
                    self._回OKAY(local_id, msg.arg0, len(msg.payload))
                elif msg.command == CMD_CLSE:
                    raise RuntimeError(f"设备在{场景}过程中关闭连接")
                else:
                    raise RuntimeError(f"{场景}失败，收到 {msg.命令名}")
            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, payload))
            self._流ASB -= len(payload)
        else:
            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, payload))
            self._等待流OKAY(local_id, 场景)

    def _设置保活(self):
        try:
            if os.name == 'nt':
                vals = struct.pack('III', 1, 10000, 3000)
                try:
                    self.sock.ioctl(0x98000004, vals)
                except AttributeError:
                    pass
            else:
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 5)
        except Exception:
            pass

    def 连接(self) -> bool:
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            self._设置保活()
        except Exception:
            pass

        _启用延迟ack = (not os.environ.get('SUPER_ADB_NO_DELAYED_ACK')) and self._burst is not False
        banner = (b'host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,abb_exec,fixed_push_symlink_timestamp,app_process_install_32bit_override,hires_shell_v2,remount_shell,track_app,sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,list_v2'
                     + (b',delayed_ack' if _启用延迟ack else b''))
        self._发送(AdbMessage(CMD_CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, banner))
        msg = self._接收消息()
        if msg.command == CMD_CNXN:
            self._设备版本 = msg.arg0
            self._跳过校验和 = (msg.arg0 >= A_VERSION_SKIP_CHECKSUM)
            self._max_payload = self._协商载荷(msg.arg1)
            self._解析设备features(msg.payload)
            self.state = STATE_DEVICE
            return True
        elif msg.command == CMD_AUTH:
            if msg.arg0 == AUTH_TOKEN:
                return self._处理认证(msg.payload)
            raise RuntimeError(f"未知 AUTH 类型: {msg.arg0}")
        elif msg.command == CMD_STLS:
            # A_STLS：无线调试 TLS 端口。回显 STLS 后升级 TLS（互认证）。
            # ★ 对齐 AOSP：客户端**不重发** CNXN——服务器校验客户端证书通过后
            #   自行回 CNXN（adbd_auth_verified → send_connect）。此前在此处
            #   重发 CNXN 会触发服务器 handle_new_connection 二次回 STLS，打乱流程。
            self._发送(AdbMessage(CMD_STLS, msg.arg0, 0, b''))  # AOSP send_tls_request: data_length=0
            self._升级为TLS()
            msg = self._接收消息()
            if msg.command == CMD_CNXN:
                self._设备版本 = msg.arg0
                self._跳过校验和 = (msg.arg0 >= A_VERSION_SKIP_CHECKSUM)
                self._max_payload = self._协商载荷(msg.arg1)
                self._解析设备features(msg.payload)
                self.state = STATE_DEVICE
                return True
            elif msg.command == CMD_AUTH:
                if msg.arg0 == AUTH_TOKEN:
                    return self._处理认证(msg.payload)
                raise RuntimeError(f"未知 AUTH 类型: {msg.arg0}")
            raise RuntimeError(f"期望 CNXN/AUTH，收到 {msg.命令名}")
        raise RuntimeError(f"期望 CNXN/AUTH，收到 {msg.命令名}")

    # ── ★ 修复核心: _处理认证 / _获取公钥 ──

    def _升级为TLS(self) -> None:
        """A_STLS 确认后，把已连接的 TCP socket 升级为 TLS 1.3 客户端。

        用持久 adbkey 的 CN=adb 证书做客户端认证，设备侧校验其公钥指纹
        是否在 /data/misc/adb/adb_keys（由配对阶段写入）。成功后 self.sock
        被替换为加密 socket，后续 _发送/_接收消息 自动走 TLS。
        """
        import ssl
        import tempfile
        cert_pem, key_pem = _生成adb连接证书(self._key_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        cert_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        key_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pem')
        try:
            cert_file.write(cert_pem)
            cert_file.close()
            key_file.write(key_pem)
            key_file.close()
            ctx.load_cert_chain(cert_file.name, key_file.name)
            self.sock = ctx.wrap_socket(self.sock, server_hostname=None)
        finally:
            for _f in (cert_file.name, key_file.name):
                try:
                    os.unlink(_f)
                except Exception:
                    pass

    def _处理认证(self, token: bytes) -> bool:
        """AUTH 状态机（对齐官方 adb 的 auth 流程）。

        官方流程：
          adbd 发 AUTH TOKEN(20 字节 SHA1) → 客户端逐个用本地私钥签名回
          AUTH SIGNATURE；每次签名被拒，adbd 会**换发一个新 TOKEN**；
          私钥全部用尽后客户端发 AUTH RSAPUBLICKEY，设备弹出授权框。

        ★ 旧实现的致命缺陷：发完公钥后只是 `continue` 干等 CNXN。
          而相当多 ROM（尤其国产 ROM / 盒子）在用户点「允许」后并不直接回
          CNXN，而是先把公钥写入 /data/misc/adb/adb_keys，然后**重发一个
          AUTH TOKEN**，等客户端用私钥签名来完成握手。此时双方互相等待：
          设备等签名、客户端等 CNXN → 60s 后超时失败。外部表现就是
          「有的设备不弹窗 / 弹窗点了允许也连不上」。
          现在收到 TOKEN 一律用私钥重新签名，握手可正常收敛。
        """
        tid = threading.get_ident()
        私钥 = self._加载私钥()
        已发公钥 = False
        签名次数 = 0
        # 本程序只持有一把私钥，同一把钥匙对新 token 反复签名不会改变结果，
        # 因此单轮即可；被拒后立刻发公钥去触发授权弹窗（官方 adb 是逐把钥匙试）。
        最大签名次数 = 1
        原超时 = self.sock.gettimeout() if self.sock else self.timeout
        deadline = time.time() + max(self.timeout, 10.0)
        print(f'[自研adb][T{tid}] 收到 AUTH TOKEN（{len(token)}字节），开始认证')
        try:
            while True:
                if time.time() >= deadline:
                    self._认证失败原因 = (
                        '等待设备授权超时(60s)：设备未确认调试授权' if 已发公钥
                        else f'认证超时({int(max(self.timeout, 10.0))}s)：设备未响应签名')
                    print(f'[自研adb][T{tid}] {self._认证失败原因}')
                    break

                # ── ① 有私钥且次数未用尽 → 对当前 token 签名 ──
                if 私钥 is not None and 签名次数 < 最大签名次数:
                    try:
                        签名 = self._rsa签名(私钥, token)
                    except Exception as e:
                        print(f'[自研adb][T{tid}] 签名异常: {e}，转为发送公钥')
                        签名 = b''
                    if not 签名:
                        签名次数 = 最大签名次数      # 放弃签名，下一轮走公钥
                        continue
                    签名次数 += 1
                    self._发送(AdbMessage(CMD_AUTH, AUTH_SIGNATURE, 0, 签名))
                    print(f'[自研adb][T{tid}] 已发送签名(第{签名次数}次)，等待设备响应...')

                # ── ② 没有可用签名 → 发公钥，触发设备授权弹窗（只发一次）──
                elif not 已发公钥:
                    公钥 = self._获取公钥()
                    if not 公钥:
                        self._认证失败原因 = '无法获取公钥'
                        print(f'[自研adb][T{tid}] 无法获取公钥，认证失败')
                        break
                    self._发送(AdbMessage(CMD_AUTH, AUTH_RSAPUBLICKEY, 0, 公钥 + b'\0'))
                    已发公钥 = True
                    签名次数 = 0                     # 授权后设备会重发 TOKEN，需再签名
                    # ★ 首次运行时本地还没有 super_adb_key，上面 _获取公钥() 内部
                    #   才刚生成密钥对。若此处不重新加载，私钥仍为 None，设备在用户
                    #   点「允许」后重发 TOKEN 时我们无法签名 → 双方互等直到超时。
                    #   表现正是「全新设备第一次永远连不上，只有官方 adb 授权过的能用」。
                    if 私钥 is None:
                        私钥 = self._加载私钥()
                    deadline = time.time() + 60.0    # 留足用户点「允许」的时间
                    print(f'[自研adb][T{tid}] 已发送公钥，等待用户在设备上授权（60秒）...')
                    if self.log_callback:
                        try:
                            self.log_callback(
                                '[授权提示] 已向设备发送公钥，请在设备屏幕上点击「允许USB调试」'
                                '并勾选「始终允许使用这台计算机进行调试」'
                            )
                        except Exception:
                            pass

                # ── ③ 公钥已发且签名次数用尽 → 纯等待设备侧结果 ──

                # ── 读取设备响应 ──
                剩余 = deadline - time.time()
                if 剩余 <= 0:
                    continue
                try:
                    self.sock.settimeout(剩余)
                    msg = self._接收消息()
                except socket.timeout:
                    continue                          # 交给循环顶部判超时
                except Exception as e:
                    # 部分 ROM（盒子/TV）收到公钥后不弹框而是直接断开
                    self._认证失败原因 = (
                        f'发送公钥后设备断开连接（{e}），该设备可能不支持无线授权弹窗'
                        if 已发公钥 else f'认证过程中连接中断（{e}）')
                    print(f'[自研adb][T{tid}] {self._认证失败原因}')
                    break

                if msg.command == CMD_CNXN:
                    self._max_payload = self._协商载荷(msg.arg1)
                    self._解析设备features(msg.payload)
                    self.state = STATE_DEVICE
                    print(f'[自研adb][T{tid}] 认证成功'
                          f'（{"公钥授权" if 已发公钥 else "签名复用"}）')
                    return True
                if msg.command == CMD_AUTH and msg.arg0 == AUTH_TOKEN:
                    token = msg.payload                # 设备换发新 token，继续下一轮
                    continue
                self._认证失败原因 = f'认证阶段收到非预期响应 {msg.命令名}'
                print(f'[自研adb][T{tid}] {self._认证失败原因}')
                break
        finally:
            try:
                if self.sock:
                    self.sock.settimeout(原超时)
            except Exception:
                pass
        self.state = STATE_AUTH
        return False

    def _加载私钥(self):
        # 实例级缓存：同一连接在认证流程中会多次调用（签名 + 获取公钥时校验配对），
        # 私钥文件运行期不会变化，缓存后避免重复读盘和打印日志。
        # 失败时不缓存 None，保证 _生成密钥对 生成后下次调用能重新加载。
        _cached = getattr(self, '_私钥缓存', None)
        if _cached is not None:
            return _cached
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            if os.path.isfile(self._key_path):
                with open(self._key_path, 'rb') as f:
                    key = serialization.load_pem_private_key(
                        f.read(), password=None, backend=default_backend())
                print(f'[自研adb] 私钥加载成功: {self._key_path}')
                self._私钥缓存 = key
                return key
            print(f'[自研adb] 私钥文件不存在: {self._key_path}')
        except ImportError:
            print(f'[自研adb] cryptography 库未安装')
        except Exception as e:
            print(f'[自研adb] 私钥加载失败: {e}')
        return None

    def _生成密钥对(self):
        """仅在私钥不存在时调用（程序启动时一次性生成，禁止在认证中途生成）。"""
        try:
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            key_dir = os.path.dirname(self._key_path)
            os.makedirs(key_dir, exist_ok=True)
            private_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048, backend=default_backend())
            with open(self._key_path, 'wb') as f:
                f.write(private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption()))
            print(f'[自研adb] 密钥对生成成功: {self._key_path}')
            return private_key
        except Exception as e:
            print(f'[自研adb] 生成密钥失败: {e}')
            return None

    def _rsa签名(self, private_key, data: bytes) -> bytes:
        """手动构造标准 PKCS#1 v1.5 签名（不依赖 cryptography 的 sign）。

        ADB 协议要求（与官方 RSA_sign(NID_sha1, token, 20) 完全一致）：
        1. digest = token 本身（20 字节，设备端视为预计算的 SHA1 摘要，禁止再哈希）
        2. DigestInfo = ASN.1 DER 编码的 AlgorithmIdentifier + digest
        3. PKCS#1 v1.5 填充：0x00 || 0x01 || 0xFF... || 0x00 || DigestInfo
        4. RSA 原始加密（modexp with private key）
        """
        # ★ ADB 协议关键约定：设备发的 20 字节 token 本身就是 SHA1 摘要。
        # 官方 adb_auth_sign 调用 RSA_sign(NID_sha1, token, 20)，直接把 token 填入
        # DigestInfo；设备端 RSA_verify(NID_sha1, token, 20, sig) 同样用原始 token。
        # 若对 token 再做一次 SHA1，签名与设备期望永远不一致 → 每次连接都被拒、反复弹授权。
        digest = data  # 20 字节 token 直接作为摘要，禁止再哈希！
        print(f'[自研adb] token十六进制: {data.hex()}（直接作为 SHA1 摘要填入 DigestInfo）')
        # 2. 构造 DigestInfo ASN.1 DER
        # SHA1 AlgorithmIdentifier: 1.3.14.3.2.26 (sha1)
        # SEQUENCE { OID 1.3.14.3.2.26, NULL } → 30 07 06 05 2B 0E 03 02 1A 05 00
        digest_info_prefix = bytes([
            0x30, 0x21,                     # SEQUENCE, length=33
            0x30, 0x09,                     # SEQUENCE, length=9
            0x06, 0x05, 0x2B, 0x0E, 0x03, 0x02, 0x1A,  # OID sha1(1.3.14.3.2.26)
            0x05, 0x00,                     # NULL
            0x04, 0x14,                     # OCTET STRING, length=20
        ])
        digest_info = digest_info_prefix + digest  # 共 15 + 20 = 35 字节
        # 3. PKCS#1 v1.5 填充
        key_size = private_key.key_size  # 2048
        padded_len = key_size // 8  # 256
        pad_len = padded_len - len(digest_info) - 3  # 256 - 35 - 3 = 218
        padded = b'\x00\x01' + b'\xff' * pad_len + b'\x00' + digest_info
        # 4. 转成整数，做 RSA 模幂
        padded_int = int.from_bytes(padded, 'big')
        nums = private_key.private_numbers()
        d = nums.d
        n = nums.public_numbers.n
        signature_int = pow(padded_int, d, n)
        signature = signature_int.to_bytes(padded_len, 'big')
        print(f'[自研adb] 手动PKCS1v15签名: 签名长度={len(signature)}')
        return signature

    def _获取公钥(self) -> Optional[bytes]:
        """生成 ADB 标准格式公钥（524字节 android_pubkey_t 的 base64 + 备注）。

        优先读取 .pub 文件，但必须先校验它与当前私钥配对（模数一致）——
        若 .pub 是旧私钥生成的（如私钥被重新生成过），设备端保存的公钥与
        签名私钥不匹配，签名验证永远失败，设备每次连接都会弹授权框。
        官方 adb 的做法是生成私钥的同时成对写出 .pub，这里发现不配对即重写。
        """
        import base64
        private_key = self._加载私钥()
        if not private_key:
            private_key = self._生成密钥对()
        if not private_key:
            return None
        try:
            pub_path = self._key_path + '.pub'
            local_n = private_key.public_key().public_numbers().n
            if os.path.exists(pub_path):
                try:
                    with open(pub_path, 'rb') as f:
                        content = f.read().strip()
                    if 从公钥串提取模数(content) == local_n:
                        print(f'[自研adb] 从.pub文件读取公钥: {pub_path}, 长度={len(content)}')
                        return content
                    print(f'[自研adb] ⚠ .pub 与当前私钥不配对（模数不一致），重新生成: {pub_path}')
                except Exception as e:
                    print(f'[自研adb] .pub 文件无效({e})，从私钥重新推导')

            # 从私钥推导，并回写 .pub（与官方 adb 保持一致）
            key_data = 编码adb公钥(private_key)
            b64 = base64.b64encode(key_data).decode('ascii')
            result = (b64 + ' super_adb@python').encode('utf-8')
            try:
                with open(pub_path, 'wb') as f:
                    f.write(result)
                print(f'[自研adb] 公钥已重写: {pub_path}')
            except Exception as e:
                print(f'[自研adb] 写回.pub失败(不影响本次认证): {e}')
            print(f'[自研adb] 公钥生成(从私钥推导): 总长={len(key_data)}, base64前32={b64[:32]}...')
            return result
        except Exception as ex:
            print(f'[自研adb] 公钥编码失败: {ex}')
            import traceback
            traceback.print_exc()
            return None

    # ── I/O ──

    def _发送(self, msg: AdbMessage):
        if not self.sock:
            raise RuntimeError("未连接")
        data = msg.打包()
        if not self._跳过校验和 and msg.command not in (CMD_CNXN, CMD_AUTH):
            # ★ 老设备 adbd（设备 CNXN 版本 < A_VERSION_SKIP_CHECKSUM，如部分
            # IPTV 机顶盒）不认「版本协商后跳过校验和」，仍强制校验每个帧的
            # checksum 字段；恒发 0 会让设备直接断连（表现：shell 一开就
            # 「连接断开」/ 探活失败）。按协商结果补真实校验和（官方 adb
            # 客户端同款自适应行为）。
            data = 打包消息(msg.command, msg.arg0, msg.arg1, msg.payload,
                             force_checksum=True)
        self.sock.sendall(data)

    def _接收消息(self) -> AdbMessage:
        if not self.sock:
            raise RuntimeError("未连接")
        header = self._精确接收(24)
        command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', header)
        
        # 校验 magic 字段
        expected_magic = command ^ 0xffffffff
        if magic != expected_magic:
            raise RuntimeError(f"magic 不匹配: 期望 {expected_magic:#x}, 实际 {magic:#x}, command={command:#x}")
        
        # 校验 length 字段（ADB_MAX_PAYLOAD = 1MB）
        if length > ADB_MAX_PAYLOAD:
            raise RuntimeError(f"payload 过长: {length} > {ADB_MAX_PAYLOAD}")
        
        payload = self._精确接收(length) if length > 0 else b''
        
        # CRC 校验（仅在 crc != 0 时）
        if crc != 0 and self.state == STATE_AUTH:
            actual_crc = _计算校验和(payload)
            if actual_crc != crc:
                raise RuntimeError(f"checksum 校验失败: 期望 {crc:#x}, 实际 {actual_crc:#x}")
        
        return AdbMessage(command, arg0, arg1, payload)

    def _精确接收(self, n: int) -> bytes:
        """读满 n 字节。

        ★ socket.timeout 只允许在「零字节」处向上抛出：
          认证等待期间会把 socket 超时设得很短并循环重试，若在读到半个
          24 字节头之后抛出超时，调用方 continue 再读就会从帧中间接着解析，
          magic/length 全部错位 → 后续报文永久性错乱。
          因此这里一旦读到过数据，超时就继续等（最多 60s 兜底），
          确保超时点始终落在报文边界上。
        """
        buf = b''
        半帧截止 = None
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                if not buf:
                    raise                      # 边界处超时，交给调用方重试
                if 半帧截止 is None:
                    半帧截止 = time.time() + 60.0
                elif time.time() >= 半帧截止:
                    raise RuntimeError(f"读取报文超时：已收 {len(buf)}/{n} 字节")
                continue                       # 半帧状态：必须读完，不能返回
            if not chunk:
                raise RuntimeError("连接断开")
            buf += chunk
        return buf

    # ── 服务 / Shell ──

    def 打开服务(self, service: str, _重试: int = 1) -> int:
        if self.state != STATE_DEVICE:
            raise RuntimeError("设备未连接或未授权")
        self._local_id += 1
        local_id = self._local_id
        self._发OPEN(local_id, service)
        self._预读数据 = b''
        # 按流 ID 过滤报文：旧流（如上次客户端超时放弃的流）的残留 WRTE/CLSE
        # 可能晚到。绝不能裸清接收缓冲区——recv 会撕裂报文，残留半截字节
        # 会把后续解析全部带偏（假 CLSE → 误报「设备关闭连接」）。
        for _ in range(10):
            msg = self._接收消息()
            if msg.command == CMD_OKAY:
                if msg.arg1 != local_id:
                    continue  # 旧流残留
                self._remote_id = msg.arg0
                self._流ASB = self._解析OKAY字节(msg)
                return local_id
            if msg.command == CMD_WRTE:
                if msg.arg1 != local_id:
                    # 旧流数据：按协议回 OKAY 免得设备端流控卡住，丢弃内容
                    try:
                        self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                    except Exception:
                        pass
                    continue
                self._预读数据 += msg.payload
                self._回OKAY(local_id, msg.arg0, len(msg.payload))
                continue
            if msg.command == CMD_CLSE:
                if msg.arg1 != local_id:
                    # 旧流关闭包：按协议回 CLSE，继续等本次 OPEN 的应答
                    try:
                        self._发送(AdbMessage(CMD_CLSE, msg.arg1, msg.arg0))
                    except Exception:
                        pass
                    continue
                # 设备确实拒绝本次服务：按协议回 CLSE
                try:
                    self._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                except Exception:
                    pass
                if _重试 > 0:
                    # 部分设备 adbd 在高频开流时会瞬时拒绝 OPEN，短延时重试一次
                    time.sleep(0.3)
                    return self.打开服务(service, _重试 - 1)
                raise RuntimeError(f"打开服务失败，设备关闭连接: {service}")
            # 其他类型报文视为残留，丢弃
        raise RuntimeError(f"打开服务失败，未收到 OKAY: {service}")

    def _读取主机服务(self, service: str, timeout: float = 5.0) -> bytes:
        if self.state != STATE_DEVICE:
            raise RuntimeError("设备未连接或未授权")
        self._local_id += 1
        local_id = self._local_id
        self._发OPEN(local_id, service)
        output = b''
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            while True:
                msg = self._接收消息()
                if msg.command == CMD_OKAY:
                    if msg.arg1 == local_id:
                        self._remote_id = msg.arg0
                    continue
                elif msg.command == CMD_WRTE:
                    if msg.arg1 != local_id:
                        try:
                            self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                        except Exception:
                            pass
                        continue
                    output += msg.payload
                    self._回OKAY(local_id, msg.arg0, len(msg.payload))
                elif msg.command == CMD_CLSE:
                    if msg.arg1 != local_id:
                        try:
                            self._发送(AdbMessage(CMD_CLSE, msg.arg1, msg.arg0))
                        except Exception:
                            pass
                        continue
                    try:
                        self._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                    except Exception:
                        pass
                    break
        finally:
            self.sock.settimeout(old)
        return output

    def 获取版本(self) -> int:
        data = self._读取主机服务('host:version')
        if len(data) >= 4:
            return struct.unpack('<I', data[:4])[0]
        return 0

    def 获取root(self) -> bool:
        try:
            local_id = self.打开服务('root:')
            try:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    self._回OKAY(local_id, msg.arg0, len(msg.payload))
            except Exception:
                pass
            return True
        except Exception:
            return False

    def 获取设备列表(self) -> list:
        data = self._读取主机服务('host:devices')
        devices = []
        for line in data.decode('utf-8', errors='replace').strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                devices.append({'serial': parts[0], 'state': parts[1]})
            elif len(parts) == 1:
                devices.append({'serial': parts[0], 'state': 'unknown'})
        return devices

    def 执行shell(self, command: str, timeout: float = 30.0) -> str:
        local_id = self.打开服务(f'shell:{command}')
        output = self._预读数据
        self._预读数据 = b''
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            while True:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    if msg.arg1 != local_id:
                        # 旧流残留数据：回 OKAY 维持流控，丢弃
                        try:
                            self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                        except Exception:
                            pass
                        continue
                    output += msg.payload
                    self._回OKAY(local_id, self._remote_id, len(msg.payload))
                elif msg.command == CMD_CLSE:
                    if msg.arg1 != local_id:
                        # 旧流关闭包：回 CLSE 后继续等本流的 CLSE
                        try:
                            self._发送(AdbMessage(CMD_CLSE, msg.arg1, msg.arg0))
                        except Exception:
                            pass
                        continue
                    # 协议要求：收到 CLSE 必须回 CLSE，释放设备端流资源
                    try:
                        self._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                    except Exception:
                        pass
                    break
                elif msg.command == CMD_OKAY:
                    continue
        except socket.timeout:
            # 超时也主动关闭流，避免设备端继续向半开流写数据
            try:
                self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
            except Exception:
                pass
        finally:
            self.sock.settimeout(old)
        return output.decode('utf-8', errors='replace')

    # ── sync 推送 ──

    def 推送文件(self, local_path: str, remote_path: str, timeout: float = 120.0,
                 progress_cb=None) -> bool:
        if not os.path.isfile(local_path):
            raise FileNotFoundError(f"本地文件不存在: {local_path}")
        file_size = os.path.getsize(local_path)
        estimated = max(120.0, file_size / (512 * 1024))
        try:
            result = self._推送文件_sync协议(local_path, remote_path, max(timeout, estimated), progress_cb)
            if result:
                # sync推送后验证文件是否真的存在（某些设备sync协议可能静默失败）
                try:
                    verify = self.执行shell(f'ls -l "{remote_path}"', timeout=10)
                    if not verify or 'No such file' in verify:
                        print(f'[自研adb] sync推送验证失败，文件不存在，回退shell方式: {remote_path}')
                        result = False
                    else:
                        print(f'[自研adb] sync推送验证成功: {verify.strip()}')
                except Exception as e:
                    print(f'[自研adb] sync推送验证异常，回退shell方式: {e}')
                    result = False
            if result:
                return True
            raise RuntimeError("sync推送验证失败")
        except Exception as e:
            print(f'[自研adb] sync推送失败，回退shell方式: {e}')
            if progress_cb:
                try:
                    progress_cb(0, file_size)
                except Exception:
                    pass
            return self._推送文件_shell方式(local_path, remote_path, max(timeout, 300), progress_cb)

    def _计算sync分帧(self) -> Tuple[int, int]:
        """返回 (单个DATA块大小, 每个WRTE报文承载的DATA块数)。

        sync 通道本质是字节流，WRTE 只是传输层分帧，因此一个 WRTE 报文里
        可以连续承载多个 DATA 块。官方 adb 正是靠「1MB 的 WRTE 里塞 15~16 个
        64KB DATA 块」把往返次数降到 1/16——这是唯一正确的提速手段。

        约束：
          - 单个 DATA 块 ≤ SYNC_DATA_MAX(64KB)，设备端 sync 服务缓冲区是定长的，
            超了会被以 "oversize data message" 拒绝。
          - 整个 WRTE payload ≤ 协商出的 _max_payload。
        """
        块头 = 8  # b'DATA' + <I 长度
        if self._max_payload < SYNC_DATA_MAX + 块头:
            # 老设备协商出很小的 payload：退化成一帧一块
            return max(1, self._max_payload - 块头), 1
        return SYNC_DATA_MAX, max(1, self._max_payload // (SYNC_DATA_MAX + 块头))

    def _等待流OKAY(self, local_id: int, 场景: str):
        """等待本流的一个 CMD_OKAY（传输层流控 ack）。

        ADB 传输层规定：未收到上一个 WRTE 的 OKAY 前不得发下一个 WRTE。
        且 adbd 的 OKAY **不是** 每个 WRTE 回一个——它在 local socket 真正
        冲刷完成时回一个，多个 WRTE 可能被合并成一个 OKAY。所以绝不能按
        「发了 N 个 WRTE 就去收 N 个 OKAY」来记账，那样必然死等不存在的包。
        期间设备可能插入 WRTE（sync 的 FAIL 响应等），需就地处理。

        ★ 必须按流 ID 过滤旧流残留帧（与 打开服务/执行shell/_读取主机服务/
        _发送流内-delayed_ack 分支 一致）：客户端关闭旧流后，设备会按协议
        回声一个 CLSE 作为应答，而 执行shell 发出 CLSE 后立即返回、不回读该
        应答——它残留在连接接收缓冲区里。降级复用主连接（单客户端设备
        push/pull）时，_等待流OKAY 若不按流过滤，会把旧流的残留 CLSE 误判
        为本流的关闭 → 误报「设备在SEND过程中关闭连接」。实测命中该问题。
        """
        while True:
            msg = self._接收消息()
            if msg.command == CMD_OKAY:
                if msg.arg1 != local_id:
                    continue  # 旧流残留
                return
            if msg.command == CMD_WRTE:
                if msg.arg1 != local_id:
                    # 旧流残留数据：回 ack 维持流控，丢弃
                    self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                    continue
                if msg.payload[:4] == b'FAIL':
                    err_len = struct.unpack('<I', msg.payload[4:8])[0]
                    err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')
                    raise RuntimeError(f"{场景}失败: {err}")
                # 其它带外数据：回 ack 后继续等本流的 OKAY
                self._回OKAY(local_id, msg.arg0, len(msg.payload))
                continue
            if msg.command == CMD_CLSE:
                if msg.arg1 != local_id:
                    continue  # 旧流关闭包，跳过
                raise RuntimeError(f"设备在{场景}过程中关闭连接")
            raise RuntimeError(f"{场景}失败，收到 {msg.命令名}")

    def _推送文件_sync协议(self, local_path: str, remote_path: str, timeout: float, progress_cb) -> bool:
        local_id = self.打开服务('sync:')
        old = self._读取传输超时()
        self._设置传输超时(timeout)
        try:
            path_with_mode = f'{remote_path},0777'.encode('utf-8')
            send_cmd = b'SEND' + struct.pack('<I', len(path_with_mode)) + path_with_mode
            self._发送流内(local_id, send_cmd, 'SEND')

            file_size = os.path.getsize(local_path)
            chunk_size, 每帧块数 = self._计算sync分帧()
            读取量 = chunk_size * 每帧块数
            total_chunks = (file_size + chunk_size - 1) // chunk_size
            往返次数 = (file_size + 读取量 - 1) // 读取量 if file_size else 0
            print(f'[自研adb] sync推送: DATA块={chunk_size}, 每帧{每帧块数}块(帧≤{读取量}B), '
                  f'文件={file_size}字节({total_chunks}块/{往返次数}次往返)')
            sent = 0
            t0 = time.time()
            with open(local_path, 'rb') as f:
                while True:
                    buf = f.read(读取量)
                    if not buf:
                        break
                    # 把多个 DATA 块拼进同一个 WRTE payload。
                    # ★ 提速：预分配整帧容量 + memoryview 视图填充，避免
                    # 「每块一次切片拷贝 + bytearray 逐块 extend 反复 realloc」。
                    块头 = 8  # b'DATA' + <I 长度
                    块数 = (len(buf) + chunk_size - 1) // chunk_size
                    帧 = bytearray(块数 * (块头 + chunk_size))
                    buf_view = memoryview(buf)
                    pos = 0
                    for off in range(0, len(buf), chunk_size):
                        blk_len = min(chunk_size, len(buf) - off)
                        帧[pos:pos + 4] = b'DATA'
                        帧[pos + 4:pos + 8] = struct.pack('<I', blk_len)
                        帧[pos + 8:pos + 8 + blk_len] = buf_view[off:off + blk_len]
                        pos += 块头 + blk_len
                    payload = bytes(帧) if pos == len(帧) else bytes(帧[:pos])
                    self._发送流内(local_id, payload, '推送')
                    sent += len(buf)
                    if progress_cb:
                        try:
                            progress_cb(sent, file_size)
                        except Exception:
                            pass

            mtime = int(os.path.getmtime(local_path))
            done_cmd = b'DONE' + struct.pack('<I', mtime)
            self._发送流内(local_id, done_cmd, 'DONE')
            # 等设备回 sync 层的最终应答：b'OKAY' 表示确实落盘，b'FAIL' 带原因
            确认 = False
            while not 确认:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    if msg.arg1 != local_id:
                        # 旧流残留 WRTE：回 ack 后丢弃
                        try:
                            self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                        except Exception:
                            pass
                        continue
                    tag = msg.payload[:4]
                    self._回OKAY(local_id, msg.arg0, len(msg.payload))
                    if tag == b'OKAY':
                        确认 = True
                    elif tag == b'FAIL':
                        err_len = struct.unpack('<I', msg.payload[4:8])[0]
                        err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')
                        raise RuntimeError(f"推送失败: {err}")
                    else:
                        raise RuntimeError(f"DONE 收到未知 sync 响应: {tag!r}")
                elif msg.command == CMD_OKAY:
                    continue
                elif msg.command == CMD_CLSE:
                    if msg.arg1 != local_id:
                        continue  # 旧流关闭包，跳过
                    # 少数 ROM 直接关流表示完成，交由上层大小校验兜底
                    break
                else:
                    raise RuntimeError(f"DONE 失败，收到 {msg.命令名}")

            elapsed = time.time() - t0
            rate = sent / elapsed / 1024 if elapsed > 0 else 0
            print(f'[自研adb] sync推送完成: {sent}字节, {elapsed:.1f}秒, {rate:.0f}KB/s')
            return True
        finally:
            self._设置传输超时(old)
            try:
                self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
            except Exception:
                pass

    def _推送文件_shell方式(self, local_path: str, remote_path: str, timeout: float, progress_cb) -> bool:
        import base64
        file_size = os.path.getsize(local_path)
        cmd_overhead = 41 + len(remote_path)
        max_b64_len = self._max_payload - cmd_overhead
        max_chunk = max(512, int(max_b64_len * 3 / 4))
        chunk_size = min(max_chunk, 2048)
        # touch 建文件并用标记验证：设备端失败（目录不存在/只读）不会抛异常，
        # 不验证就会像以前一样静默返回 True，上层误报推送成功
        touch_out = (self.执行shell(
            f'rm -f "{remote_path}" 2>/dev/null; '
            f'touch "{remote_path}" 2>&1 && echo TOUCH_OK',
            timeout=10) or '').strip()
        if 'TOUCH_OK' not in touch_out:
            # 根据输出分析失败原因
            原因分析 = '目标目录不存在或权限不足'
            if 'Read-only' in touch_out or 'read-only' in touch_out.lower():
                原因分析 = '目标分区为只读（需执行 adb root && adb remount）'
            elif 'Permission denied' in touch_out:
                原因分析 = '权限被拒绝（需 root 权限或检查目录权限）'
            elif 'No such file' in touch_out:
                原因分析 = f'目标目录不存在（请先创建 {os.path.dirname(remote_path)}）'
            raise RuntimeError(
                f"shell推送初始化失败: {原因分析} | "
                f"路径: {remote_path} | 详情: {touch_out or '无输出'}")
        sent = 0
        with open(local_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                b64 = base64.b64encode(chunk).decode('ascii')
                cmd = f'printf "%s" "{b64}" | base64 -d >> "{remote_path}"'
                last_err = None
                for retry in range(3):
                    try:
                        self.执行shell(cmd, timeout=10)
                        last_err = None
                        break
                    except Exception as e:
                        last_err = e
                        print(f'[自研adb] shell推送块失败，重试{retry+1}/3: {e}')
                        time.sleep(0.5)
                if last_err:
                    raise RuntimeError(
                        f"shell推送数据块失败（已重试3次）: {last_err} | "
                        f"目标: {remote_path} | 已发送: {sent}/{file_size}B")
                sent += len(chunk)
                if progress_cb:
                    try:
                        progress_cb(sent, file_size)
                    except Exception:
                        pass
        # 传完后落盘验证：文件存在且字节数一致，杜绝静默成功
        verify = (self.执行shell(f'ls -l "{remote_path}"', timeout=10) or '').strip()
        if not verify or 'No such file' in verify:
            raise RuntimeError(
                f"shell推送后文件不存在: {remote_path} ({verify or '无输出'}) | "
                f"可能原因: 设备权限不足、分区只读或空间已满")
        size_out = (self.执行shell(f'wc -c < "{remote_path}"', timeout=10) or '').strip()
        try:
            remote_size = int(size_out.split()[0])
        except Exception:
            remote_size = -1
        if remote_size != file_size:
            原因 = '设备端接收不完整'
            if remote_size == 0:
                原因 = '设备端文件为空（可能是权限不足或目录只读）'
            elif remote_size < file_size:
                原因 = f'设备端文件不完整（少 {file_size - remote_size}B）'
            raise RuntimeError(
                f"shell推送字节数不一致: 本地{file_size}B 设备{remote_size}B | "
                f"原因: {原因} | 目标: {remote_path}")
        return True

    # ── sync 拉取 ──

    def 拉取文件(self, remote_path: str, local_path: str, timeout: float = 60.0) -> bool:
        print(f'[自研adb] 开始拉取: {remote_path} -> {local_path}')
        try:
            return self._拉取文件_sync协议(remote_path, local_path, max(timeout, 30))
        except Exception as e:
            print(f'[自研adb] sync拉取失败，回退shell方式: {e}')
            try:
                return self._拉取文件_shell方式(remote_path, local_path, max(timeout, 120))
            except Exception as e2:
                print(f'[自研adb] shell拉取也失败: {e2}')
                raise

    def _拉取文件_sync协议(self, remote_path: str, local_path: str, timeout: float) -> bool:
        local_id = self.打开服务('sync:')
        old = self._读取传输超时()
        self._设置传输超时(timeout)
        # 确保目标目录存在
        import os as _os
        _parent = _os.path.dirname(local_path)
        if _parent and not _os.path.isdir(_parent):
            _os.makedirs(_parent, exist_ok=True)
        try:
            path_bytes = remote_path.encode('utf-8')
            recv_cmd = b'RECV' + struct.pack('<I', len(path_bytes)) + path_bytes
            self._发送流内(local_id, recv_cmd, 'RECV')

            got_done = False
            bytes_received = 0
            残余 = bytearray()   # 跨 WRTE 的未解析字节（DATA 头/块体都可能被切断）
            失败信息 = None
            # ★ 提速：写文件批量化——多个 DATA 块攒够再落盘，减少文件系统调用。
            # 缓冲目标 ≥ 1MB（或协商 payload），避免小写放大。
            写缓冲 = bytearray()
            写缓冲上限 = max(1024 * 1024, self._max_payload)
            # sync 拉取是字节流：一个 WRTE 可能含多个 DATA 块，也可能只含半个
            # 块头或半个块体。必须按流解析并保留残余，否则会静默丢数据（截断）。
            with open(local_path, 'wb') as f:
                while not got_done:
                    try:
                        msg = self._接收消息()
                    except socket.timeout:
                        # 超时且已收到部分数据：不完整文件必须删除并报错，
                        # 绝不能静默接受截断文件
                        if bytes_received > 0:
                            f.close()
                            try:
                                _os.remove(local_path)
                            except Exception:
                                pass
                            raise RuntimeError(
                                f"拉取超时，已收到 {bytes_received}B 但未收到 DONE，"
                                f"不完整文件已删除: {local_path}")
                        raise RuntimeError("拉取超时，未收到数据")
                    if msg.command == CMD_WRTE:
                        if msg.arg1 != local_id:
                            # 旧流残留数据：回 ack 维持流控，丢弃，不并入本文件
                            try:
                                self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                            except Exception:
                                pass
                            continue
                        # 先回 ack，让设备继续发下一帧（每个 WRTE 一个 OKAY，
                        # 而不是每个 DATA 块一个——后者会多发 ack 打乱流控）
                        self._回OKAY(local_id, self._remote_id, len(msg.payload))
                        残余 += msg.payload
                        while True:
                            if len(残余) < 8:
                                break
                            tag = bytes(残余[:4])
                            length = struct.unpack('<I', 残余[4:8])[0]
                            if tag == b'DATA':
                                if len(残余) < 8 + length:
                                    break        # 块体还没收全，等下一个 WRTE
                                写缓冲.extend(memoryview(残余)[8:8 + length])
                                bytes_received += length
                                del 残余[:8 + length]
                                if len(写缓冲) >= 写缓冲上限:
                                    f.write(写缓冲)
                                    写缓冲.clear()
                                continue
                            if tag == b'DONE':
                                got_done = True
                                break
                            if tag == b'FAIL':
                                if len(残余) < 8 + length:
                                    break
                                失败信息 = bytes(残余[8:8 + length]).decode(
                                    'utf-8', errors='replace')
                                break
                            raise RuntimeError(f"拉取收到未知 sync 标记: {tag!r}")
                        if 失败信息 is not None:
                            break
                    elif msg.command == CMD_CLSE:
                        break
                    elif msg.command == CMD_OKAY:
                        continue
                # 循环结束（DONE/CLSE/失败）：冲刷剩余写缓冲
                if 写缓冲:
                    f.write(写缓冲)
                    写缓冲.clear()

            if 失败信息 is not None:
                try:
                    _os.remove(local_path)
                except Exception:
                    pass
                raise RuntimeError(f"拉取失败: {失败信息}")

            if not got_done:
                # 未收到 DONE 但循环退出（CLSE），文件可能不完整
                try:
                    _os.remove(local_path)
                except Exception:
                    pass
                raise RuntimeError(f"拉取失败：未收到 DONE，连接被关闭 (已收 {bytes_received}B)")
            print(f'[自研adb] sync拉取完成: {bytes_received}B -> {local_path}')
            return True
        finally:
            self._设置传输超时(old)
            try:
                self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
            except Exception:
                pass

    def _拉取文件_shell方式(self, remote_path: str, local_path: str, timeout: float) -> bool:
        import base64
        import shlex as _shlex
        import os as _os
        # 确保目标目录存在
        _parent = _os.path.dirname(local_path)
        if _parent and not _os.path.isdir(_parent):
            _os.makedirs(_parent, exist_ok=True)
        qpath = _shlex.quote(remote_path)
        print(f'[自研adb] shell拉取: base64 {remote_path}')
        b64_data = self.执行shell(f'base64 {qpath}', timeout=timeout)
        b64_clean = ''.join(b64_data.split())
        if not b64_clean:
            raise RuntimeError("shell拉取失败：文件为空或不存在")
        file_data = base64.b64decode(b64_clean)
        with open(local_path, 'wb') as f:
            f.write(file_data)
        print(f'[自研adb] shell拉取完成: {len(file_data)}B -> {local_path}')
        return True

    # ── 端口转发 ──

    def 端口转发(self, local_port: int, remote: str) -> bool:
        service = f'host:forward:tcp:{local_port};{remote}'
        self._local_id += 1
        local_id = self._local_id
        self._发OPEN(local_id, service)
        try:
            msg = self._接收消息()
            # 直连 adbd 不支持 host:forward 服务（那是 ADB server 的服务），
            # 收到 CLSE 说明失败，必须如实返回 False
            return msg.command == CMD_OKAY
        except Exception:
            return False

    def 取消端口转发(self, local_port: int) -> bool:
        service = f'host:killforward:tcp:{local_port}'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发OPEN(local_id, service)
            msg = self._接收消息()
            return msg.command in (CMD_OKAY, CMD_CLSE)
        except Exception:
            return False

    def 反向转发(self, remote, local_port: int) -> bool:
        # remote 支持 int（→tcp:port）或字符串（如 localabstract:scrcpy_xxx，
        # scrcpy reverse 隧道需要后者，与官方 adb reverse 语义一致）
        remote_spec = remote if isinstance(remote, str) else f'tcp:{remote}'
        service = f'host:reverse:{remote_spec};tcp:{local_port}'
        self._local_id += 1
        local_id = self._local_id
        self._发OPEN(local_id, service)
        try:
            msg = self._接收消息()
            # 仅 OKAY 才算设置成功；CLSE = adbd 拒绝（直连 adbd 本就不支持
            # host:reverse 服务），必须如实返回 False 让上层走 forward 回退
            return msg.command == CMD_OKAY
        except Exception:
            return False

    def 取消反向转发(self, remote) -> bool:
        remote_spec = remote if isinstance(remote, str) else f'tcp:{remote}'
        service = f'host:killreverse:{remote_spec}'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发OPEN(local_id, service)
            msg = self._接收消息()
            return msg.command in (CMD_OKAY, CMD_CLSE)
        except Exception:
            return False

    def 列出转发(self) -> list:
        service = 'host:list-forward'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发OPEN(local_id, service)
            output = b''
            while True:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    output += msg.payload
                    self._回OKAY(local_id, self._remote_id, len(msg.payload))
                elif msg.command == CMD_CLSE:
                    break
                elif msg.command == CMD_OKAY:
                    continue
            forwards = []
            for line in output.decode('utf-8', errors='replace').strip().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    forwards.append({'serial': parts[0], 'remote': parts[1], 'local': parts[2]})
            return forwards
        except Exception:
            return []

    # ── 应用管理 ──

    def 安装应用(self, apk_path: str, timeout: float = 300.0, extra_args: list = None) -> str:
        if not os.path.isfile(apk_path):
            raise FileNotFoundError(f"APK 不存在: {apk_path}")
        remote_path = f'/data/local/tmp/{os.path.basename(apk_path)}'
        self.推送文件(apk_path, remote_path, timeout)
        args_str = ' '.join(extra_args) if extra_args else '-r'
        result = self.执行shell(f'pm install {args_str} "{remote_path}"', timeout)
        try:
            self.执行shell(f'rm "{remote_path}"', timeout=10)
        except Exception:
            pass
        return result

    def 流式安装(self, apk_path: str, timeout: float = 300.0, extra_args: list = None,
                 progress_cb=None) -> str:
        """流式安装（Streamed Install）：exec:cmd package install -S <size> <flags>。

        对齐官方 adb client/adb_install.cpp 的做法：向设备端 exec 流的 stdin 直接
        写入 APK 字节，由 `cmd package install` 交给 PackageManagerService 处理，
        **不在设备存储上落任何临时文件**，因此天然规避两类 Legacy 故障：
          - /sdcard 的 SELinux sdcardfs 读取限制（avc: denied { read }）；
          - /data/local/tmp 不存在或 shell 不可写。

        仅在 Legacy 模式（push 临时文件 → pm install）全部失败时兜底使用。
        返回设备端输出（含 Success 或 Failure [INSTALL_FAILED_XXX]）。
        """
        if not os.path.isfile(apk_path):
            raise FileNotFoundError(f"APK 不存在: {apk_path}")
        apk_size = os.path.getsize(apk_path)
        args_str = ' '.join(str(a) for a in (extra_args or ['-r']))
        # -S <size> 是流式安装必需的：告诉设备端待会儿要从 stdin 收多少字节
        service = f'exec:cmd package install -S {apk_size} {args_str}'
        local_id = self.打开服务(service)
        output = self._预读数据
        self._预读数据 = b''
        old = self.sock.gettimeout()
        self.sock.settimeout(max(timeout, 120.0))
        try:
            # 1) 把 APK 字节写进 exec 流的 stdin（设备端按 -S 声明的 size 收取）
            块 = max(4096, min(self._max_payload - 8, 64 * 1024))
            sent = 0
            with open(apk_path, 'rb') as f:
                while True:
                    data = f.read(块)
                    if not data:
                        break
                    self._发送流内(local_id, data, '流式安装')
                    sent += len(data)
                    if progress_cb:
                        try:
                            progress_cb(sent, apk_size)
                        except Exception:
                            pass
            # 2) 读回安装结果（Success / Failure [INSTALL_FAILED_XXX]）
            while True:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    if msg.arg1 != local_id:
                        # 旧流残留：回 OKAY 维持流控，丢弃内容
                        try:
                            self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))
                        except Exception:
                            pass
                        continue
                    output += msg.payload
                    self._回OKAY(local_id, msg.arg0, len(msg.payload))
                elif msg.command == CMD_CLSE:
                    if msg.arg1 != local_id:
                        try:
                            self._发送(AdbMessage(CMD_CLSE, msg.arg1, msg.arg0))
                        except Exception:
                            pass
                        continue
                    try:
                        self._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                    except Exception:
                        pass
                    break
                elif msg.command == CMD_OKAY:
                    continue
        except socket.timeout:
            try:
                self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
            except Exception:
                pass
            raise RuntimeError(f"流式安装超时（{timeout}s），设备未返回结果")
        finally:
            self.sock.settimeout(old)
        return output.decode('utf-8', errors='replace')

    def 卸载应用(self, package: str, timeout: float = 30.0) -> str:
        return self.执行shell(f'pm uninstall {package}', timeout)

    def 获取应用列表(self, 系统应用: bool = False, timeout: float = 30.0) -> list:
        cmd = 'pm list packages -f'
        if not 系统应用:
            cmd += ' -3'
        output = self.执行shell(cmd, timeout)
        packages = []
        for line in output.strip().splitlines():
            line = line.strip()
            if line.startswith('package:'):
                if '=' in line:
                    path, pkg = line[8:].rsplit('=', 1)
                    packages.append({'package': pkg, 'path': path})
                else:
                    packages.append({'package': line[8:], 'path': ''})
        return packages

    def 关闭(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.state = STATE_OFFLINE

    def __enter__(self):
        self.连接()
        return self

    def __exit__(self, *args):
        self.关闭()


# ─────────────────── 局域网扫描 ───────────────────

def 扫描局域网设备(port: int = 5555, timeout: float = 0.5, 网段: str = None) -> list:
    if 网段 is None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            local_ip = s.getsockname()[0]
            s.close()
            网段 = '.'.join(local_ip.split('.')[:3]) + '.'
        except Exception:
            网段 = '192.168.1.'

    import struct as _struct

    # ADB CNXN 消息常量
    _CNXN = 0x4e584e43
    _AUTH = 0x48545541

    def _验证adb设备(ip):
        """TCP 连上后发送 CNXN 验证是否真的是 ADB 设备。"""
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            try:
                # 发送 CNXN 消息
                banner = b'host::features=shell_v2,cmd'
                checksum = sum(banner) & 0xffffffff
                header = _struct.pack('<IIIIII',
                    _CNXN,          # command
                    0x01000000,    # version
                    1048576,       # max_payload
                    len(banner),   # data_length
                    checksum,      # data_checksum
                    _CNXN ^ 0xffffffff  # magic
                )
                s.sendall(header + banner)
                s.settimeout(2.0)
                # 读取 24 字节响应头
                resp = b''
                while len(resp) < 24:
                    chunk = s.recv(24 - len(resp))
                    if not chunk:
                        break
                    resp += chunk
                if len(resp) < 24:
                    return None
                # 解析响应
                cmd, arg0, arg1, data_len, data_crc, magic = _struct.unpack('<IIIIII', resp)
                # 验证 magic
                if magic != (cmd ^ 0xffffffff):
                    return None
                # 验证 data_len 合理性
                if data_len > 1024 * 1024:
                    return None
                # 必须是 AUTH 或 CNXN 响应
                if cmd not in (_AUTH, _CNXN):
                    return None
                # AUTH 时 arg0 应为 1
                if cmd == _AUTH and arg0 != 1:
                    return None
                # 读取可能的 payload（banner）
                if data_len > 0 and data_len < 1024:
                    try:
                        payload = s.recv(data_len)
                        # payload 应以 "host::" 开头
                        if payload and b'host::' in payload:
                            pass  # 确认是 ADB banner
                    except Exception:
                        pass
                return {'ip': ip, 'port': port}
            finally:
                s.close()
        except Exception:
            return None

    devices = []
    ips = [f'{网段}{i}' for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(_验证adb设备, ip): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                devices.append(result)
    devices.sort(key=lambda d: [int(x) for x in d['ip'].split('.')])
    return devices


def 测试连接(host: str, port: int = 5555):
    print(f'连接 {host}:{port}...')
    conn = AdbConnection(host, port)
    try:
        if conn.连接():
            print(f'连接成功，状态: {conn.state}')
            result = conn.执行shell('getprop ro.build.version.release')
            print(f'Android 版本: {result.strip()}')
            return True
        print('连接失败，需要设备授权')
        return False
    finally:
        conn.关闭()


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 2:
        测试连接(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 5555)
    else:
        print('用法: python adb_protocol.py <host> [port]')

