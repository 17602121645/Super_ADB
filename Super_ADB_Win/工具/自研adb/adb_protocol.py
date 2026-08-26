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
import threading
import concurrent.futures
from typing import Optional, Tuple, Callable, Set, Dict, List

# ADB 协议版本
ADB_VERSION = 0x01000001  # 官方adb使用0x01000001（skip checksum）
ADB_MAX_PAYLOAD = 1048576  # 1MB
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

# ADB 连接状态
STATE_OFFLINE = 0
STATE_AUTH = 1
STATE_DEVICE = 2

# 公钥格式：4字节魔数 "ADBP" + n(256) + e(3) = 263
ADB_PUBKEY_MAGIC = b'ADBP'


def _定位密钥路径():
    """统一定位 super_adb_key 私钥路径（TCP 与 USB 共用同一份密钥）。

    - 源码模式：项目根 配置/ 下；
    - frozen（打包 exe）：exe 旁 配置/ 下（可写目录，与 _config_path 一致），
      首次访问自动从以下旧位置迁移，保证源码与打包版共用同一密钥——
      设备已给源码密钥授权过，打包版迁移后可直接签名通过，无需重复授权：
        1. _internal/配置/（旧打包版 __file__ 推导路径）
        2. 源码目录 配置/（开发机上打包版直接复用源码密钥）
    """
    fname = 'super_adb_key'
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
        if sys.platform == 'darwin':
            # macOS 冻结版与 _config_path 一致：~/Library/Application Support/Super_ADB
            base = os.path.expanduser('~/Library/Application Support/Super_ADB')
        new_dir = os.path.join(base, '配置')
        new_path = os.path.join(new_dir, fname)
        if not os.path.isfile(new_path):
            candidates = [
                # 旧打包版路径：_internal/配置/（__file__ 推导）
                os.path.join(getattr(sys, '_MEIPASS', ''), '配置', fname),
                # 开发机：exe 在源码树内（平台根/打包/dist/Super_ADB）→ 上溯 3 级到平台根 配置/
                os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(base))), '配置', fname),
            ]
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
    return os.path.join(_项目根, '配置', fname)
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


def _magic(cmd: int) -> int:
    return cmd ^ 0xffffffff


def _checksum(data: bytes) -> int:
    """ADB协议checksum: payload所有字节的和，取低32位（不是CRC32！）"""
    return sum(data) & 0xffffffff


def 打包消息(command: int, arg0: int, arg1: int, payload: bytes = b'') -> bytes:
    # 认证阶段（CNXN/AUTH）：协商尚未完成，与官方 send_packet 一致计算真实校验和，
    # 部分老设备（如小米盒子 adbd）会校验该字段，恒发 0 会导致签名被拒、反复要求授权。
    if command in (CMD_CNXN, CMD_AUTH):
        checksum = _checksum(payload)
    else:
        checksum = 0  # 建连后协商版本 >= A_VERSION_SKIP_CHECKSUM，跳过校验和
    header = struct.pack('<IIIIII', command, arg0, arg1, len(payload), checksum, _magic(command))
    return header + payload


def 解包消息(data: bytes) -> Tuple[int, int, int, bytes]:
    if len(data) < 24:
        raise ValueError(f"消息太短: {len(data)} 字节")
    command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', data[:24])
    if magic != _magic(command):
        raise ValueError(f"magic 不匹配: 期望 {_magic(command):#x}, 实际 {magic:#x}")
    payload = data[24:24 + length]
    # 设备端在版本协商前可能发 crc=0 的包，且协商后双方都跳过校验，仅在头部声明非0时校验
    if crc != 0 and _checksum(payload) != crc:
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
        self._空闲: Dict[Tuple[str, int], List[_池化Connection]] = {}
        self._借出: Set[_池化Connection] = set()
        self._线程绑定: Dict[int, _池化Connection] = {}
        # 设备级建连锁：同一设备只有一个线程能真正建连（包括AUTH授权）
        self._建连锁: Dict[Tuple[str, int], threading.Lock] = {}

    def 借用(self, host: str, port: int, timeout: float, key_path: str) -> AdbConnection:
        tid = threading.get_ident()
        key = (host, port)

        while True:
            with self._锁:
                # 1. 当前线程已绑定且可用 → 直接复用
                bound = self._线程绑定.get(tid)
                if bound and self._连接可用(bound):
                    return bound.conn

                # 2. 有空闲连接 → 取一个
                pool = self._空闲.get(key, [])
                alive = [c for c in pool if self._连接可用(c) and c.已空闲秒 < self.最大空闲秒]
                if len(alive) != len(pool):
                    self._空闲[key] = alive
                if alive:
                    c = alive.pop()
                    self._借出.add(c)
                    self._线程绑定[tid] = c
                    return c.conn

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
                    if alive:
                        c = alive.pop()
                        self._借出.add(c)
                        self._线程绑定[tid] = c
                        return c.conn

                # 真正建连（包括AUTH授权，整个过程持有dev_lock）
                new_conn = self._新建(host, port, timeout, key_path)
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
            bound = self._线程绑定.get(tid)
            if bound is not None and bound.conn is conn:
                self._线程绑定.pop(tid, None)

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
    def _连接可用(c: _池化Connection) -> bool:
        return c.conn.state == STATE_DEVICE and c.conn.sock is not None

    def _新建(self, host: str, port: int, timeout: float, key_path: str) -> 'AdbConnection':
        """新建连接（调用方应持有设备级建连锁）。"""
        conn = AdbConnection(host, port, timeout=timeout, key_path=key_path)
        try:
            ok = conn.连接()
        except Exception as e:
            # 保留原始异常细节（TCP 拒绝/超时/协议异常），否则上层只见
            # "ADB 连接失败: ip:port" 一句，无法定位原因
            raise RuntimeError(f"ADB 连接失败: {host}:{port} ({e})") from e
        if not ok:
            raise RuntimeError(
                f"ADB 连接失败: {host}:{port}（认证未通过：请在设备上允许 USB/无线调试授权，"
                f"密钥={conn._key_path}）")
        return conn


_全局池 = _连接池()


def 借用连接(host: str, port: int = 5555, timeout: float = 10.0,
            key_path: str = None) -> AdbConnection:
    return _全局池.借用(host, port, timeout, key_path)


def 归还连接(conn: AdbConnection):
    _全局池.归还(conn)


def 剥离连接(conn: AdbConnection):
    _全局池.剥离(conn)


def 关闭设备连接(host: str, port: int = 5555):
    _全局池.关闭(host, port)


def 关闭全部连接():
    _全局池.关闭()


def 清理空闲连接():
    _全局池.清理空闲()


def 已有可用连接(host: str, port: int = 5555) -> bool:
    return _全局池.已有可用连接(host, port)


# ─────────────────── 单连接（协议层，线程不安全，由池管理）───────────────────

class AdbConnection:
    """单个 ADB socket 连接，对应一个设备 transport。

    注意: 本类不是线程安全的（协议帧必须有序）。所有并发访问由上层 _连接池 保证——
    每个线程/操作独占一个 AdbConnection。
    """

    def __init__(self, host: str, port: int = 5555, timeout: float = 10.0, key_path: str = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.state = STATE_OFFLINE
        self._local_id = 0
        self._remote_id = 0
        self._预读数据 = b''
        self._max_payload = ADB_MAX_PAYLOAD
        if key_path:
            self._key_path = key_path
        else:
            self._key_path = _定位密钥路径()

    def _协商payload(self, device_max: int) -> int:
        if 256 <= device_max <= 1024 * 1024:
            return device_max
        return ADB_MAX_PAYLOAD

    def _设置keepalive(self):
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
            self._设置keepalive()
        except Exception:
            pass

        banner = b'host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,abb_exec,fixed_push_symlink_timestamp,app_process_install_32bit_override,hires_shell_v2,remount_shell,track_app,sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,list_v2'
        self._发送(AdbMessage(CMD_CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, banner))
        msg = self._接收消息()
        if msg.command == CMD_CNXN:
            self._max_payload = self._协商payload(msg.arg1)
            self.state = STATE_DEVICE
            return True
        elif msg.command == CMD_AUTH:
            if msg.arg0 == AUTH_TOKEN:
                return self._处理认证(msg.payload)
            raise RuntimeError(f"未知 AUTH 类型: {msg.arg0}")
        raise RuntimeError(f"期望 CNXN/AUTH，收到 {msg.命令名}")

    # ── ★ 修复核心: _处理认证 / _获取公钥 ──

    def _处理认证(self, token: bytes) -> bool:
        tid = threading.get_ident()
        print(f'[自研adb][T{tid}] 收到 AUTH 请求，类型=TOKEN，开始认证，token长度={len(token)}')
        private_key = self._加载私钥()
        if private_key is not None:
            try:
                signature = self._rsa_sign(private_key, token)
                print(f'[自研adb] 签名长度={len(signature)}, token长度={len(token)}')
                # 自证：用对应公钥验证本机签名，确认密钥对匹配（token 预哈希方式与设备端一致）
                try:
                    from cryptography.hazmat.primitives.asymmetric import padding, utils
                    from cryptography.hazmat.primitives import hashes
                    pub = private_key.public_key()
                    pub.verify(signature, token, padding.PKCS1v15(), utils.Prehashed(hashes.SHA1()))
                    print(f'[自研adb] ✓ 自证：密钥对匹配')
                except Exception as ex:
                    print(f'[自研adb] ✗ 自证失败：{ex}（密钥对不匹配！）')

                if signature:
                    auth_msg = AdbMessage(CMD_AUTH, AUTH_SIGNATURE, 0, signature)
                    self._发送(auth_msg)
                    print(f'[自研adb][T{tid}] 已发送签名，等待设备响应...')
                    msg = self._接收消息()
                    print(f'[自研adb][T{tid}] 收到响应: cmd={msg.命令名}, arg0={msg.arg0}, arg1={msg.arg1}, payload_len={len(msg.payload)}, payload前16={msg.payload[:16].hex()}')
                    if msg.command == CMD_CNXN:
                        print(f'[自研adb][T{tid}] 签名认证成功')
                        self._max_payload = self._协商payload(msg.arg1)
                        self.state = STATE_DEVICE
                        return True
                    print(f'[自研adb][T{tid}] 签名认证失败，收到 {msg.命令名}，尝试发送公钥')
            except Exception as e:
                print(f'[自研adb][T{tid}] 签名异常: {e}，尝试发送公钥')

        # 签名失败或无私钥 → 发送公钥（用户授权）
        public_key = self._获取公钥()
        if public_key:
            print(f'[自研adb][T{tid}] 发送公钥，等待用户授权（60秒超时）...')
            auth_msg = AdbMessage(CMD_AUTH, AUTH_RSAPUBLICKEY, 0, public_key + b'\0')
            self._发送(auth_msg)
            old_timeout = self.sock.gettimeout()
            self.sock.settimeout(60.0)
            try:
                # 循环等待授权结果：用户未在设备上点「允许」前，adbd 会反复发
                # AUTH TOKEN；旧版只读一条消息，非 CNXN 即放弃，导致连接瞬间
                # 判失败、上层反复重试。这里在 60s 内持续等待，用户点允许后
                # 设备立即发 CNXN，连接即刻成功。
                deadline = time.time() + 60.0
                msg = None
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        print(f'[自研adb][T{tid}] 公钥授权超时(60s)：用户未在设备上允许授权')
                        break
                    self.sock.settimeout(remaining)
                    msg = self._接收消息()
                    if msg.command == CMD_AUTH and msg.arg0 == AUTH_TOKEN:
                        continue  # 用户未授权期间设备反复发 TOKEN：继续等待
                    break
                if msg is not None and msg.command == CMD_CNXN:
                    print(f'[自研adb][T{tid}] 公钥认证成功，用户已授权')
                    self._max_payload = self._协商payload(msg.arg1)
                    self.state = STATE_DEVICE
                    # ★ 诊断：读取设备上保存的公钥，对比是否一致
                    try:
                        # 先检查目录和文件是否存在
                        ls_out = self.执行shell('ls -la /data/misc/adb/ 2>&1; echo "---"; ls -la /data/adb/ 2>&1', timeout=5)
                        print(f'[自研adb] 设备公钥目录检查: {ls_out.strip()[:300]}')
                        # 尝试多个可能的公钥存储路径
                        for path in ['/data/misc/adb/adb_keys', '/data/adb/adb_keys', '/data/local/tmp/adb_keys', '/data/misc/adb_keys']:
                            saved = self.执行shell(f'cat {path} 2>&1', timeout=5)
                            if saved and 'No such file' not in saved and 'Permission denied' not in saved:
                                print(f'[自研adb] 设备公钥路径: {path}')
                                print(f'[自研adb] 设备公钥内容: {saved.strip()[:200]}')
                                import base64
                                lines = saved.strip().split('\n')
                                for i, line in enumerate(lines):
                                    line = line.strip()
                                    if not line:
                                        continue
                                    parts = line.split(' ', 1)
                                    try:
                                        decoded = base64.b64decode(parts[0])
                                        if len(decoded) >= 524:
                                            # adb_pubkey_t 结构
                                            import struct
                                            pub_len, n0inv = struct.unpack_from('<II', decoded, 0)
                                            e_val = struct.unpack_from('<I', decoded, 520)[0]
                                            print(f'[自研adb] 设备公钥#{i}: adb_pubkey_t, 总长={len(decoded)}, len={pub_len}, n0inv=0x{n0inv:08x}, e={e_val}')
                                        else:
                                            print(f'[自研adb] 设备公钥#{i}: 总长={len(decoded)}, 前12字节={decoded[:12].hex()}')
                                    except Exception as e:
                                        print(f'[自研adb] 设备公钥#{i}解码失败: {e}, 前40={line[:40]}')
                                break
                        else:
                            print(f'[自研adb] 所有公钥路径均不存在或无权限')
                        # 对比本地公钥
                        local_pub = self._获取公钥()
                        if local_pub:
                            local_b64 = local_pub.split(b' ')[0].decode('ascii')
                            import base64
                            import struct
                            local_decoded = base64.b64decode(local_b64)
                            if len(local_decoded) >= 524:
                                pub_len, n0inv = struct.unpack_from('<II', local_decoded, 0)
                                e_val = struct.unpack_from('<I', local_decoded, 520)[0]
                                print(f'[自研adb] 本地公钥: adb_pubkey_t, 总长={len(local_decoded)}, len={pub_len}, n0inv=0x{n0inv:08x}, e={e_val}')
                            else:
                                print(f'[自研adb] 本地公钥: 总长={len(local_decoded)}, 前12字节={local_decoded[:12].hex()}')
                            print(f'[自研adb] 本地公钥base64: {local_b64[:80]}...')
                    except Exception as e:
                        print(f'[自研adb] 读取设备公钥失败: {e}')
                    return True
                if msg is not None:
                    print(f'[自研adb] 公钥认证失败，收到 {msg.命令名}')
            finally:
                self.sock.settimeout(old_timeout)
        else:
            print(f'[自研adb] 无法获取公钥，认证失败')
        self.state = STATE_AUTH
        return False

    def _加载私钥(self):
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend
            if os.path.isfile(self._key_path):
                with open(self._key_path, 'rb') as f:
                    key = serialization.load_pem_private_key(
                        f.read(), password=None, backend=default_backend())
                print(f'[自研adb] 私钥加载成功: {self._key_path}')
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

    def _rsa_sign(self, private_key, data: bytes) -> bytes:
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
        self.sock.sendall(msg.打包())

    def _接收消息(self) -> AdbMessage:
        if not self.sock:
            raise RuntimeError("未连接")
        header = self._recv_exact(24)
        command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', header)
        payload = self._recv_exact(length) if length > 0 else b''
        return AdbMessage(command, arg0, arg1, payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b''
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
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
        self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
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
                return local_id
            if msg.command == CMD_WRTE:
                if msg.arg1 != local_id:
                    # 旧流数据：按协议回 OKAY 免得设备端流控卡住，丢弃内容
                    try:
                        self._发送(AdbMessage(CMD_OKAY, msg.arg1, msg.arg0))
                    except Exception:
                        pass
                    continue
                self._预读数据 += msg.payload
                self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
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

    def _读取host服务(self, service: str, timeout: float = 5.0) -> bytes:
        if self.state != STATE_DEVICE:
            raise RuntimeError("设备未连接或未授权")
        self._local_id += 1
        local_id = self._local_id
        self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
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
                            self._发送(AdbMessage(CMD_OKAY, msg.arg1, msg.arg0))
                        except Exception:
                            pass
                        continue
                    output += msg.payload
                    self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
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
        data = self._读取host服务('host:version')
        if len(data) >= 4:
            return struct.unpack('<I', data[:4])[0]
        return 0

    def 获取root(self) -> bool:
        try:
            local_id = self.打开服务('root:')
            try:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
            except Exception:
                pass
            return True
        except Exception:
            return False

    def 获取设备列表(self) -> list:
        data = self._读取host服务('host:devices')
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
                            self._发送(AdbMessage(CMD_OKAY, msg.arg1, msg.arg0))
                        except Exception:
                            pass
                        continue
                    output += msg.payload
                    self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
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
            result = self._推送文件_sync(local_path, remote_path, max(timeout, estimated), progress_cb)
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
            return self._推送文件_shell(local_path, remote_path, max(timeout, 300), progress_cb)

    def _推送文件_sync(self, local_path: str, remote_path: str, timeout: float, progress_cb) -> bool:
        local_id = self.打开服务('sync:')
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            path_with_mode = f'{remote_path},0777'.encode('utf-8')
            send_cmd = b'SEND' + struct.pack('<I', len(path_with_mode)) + path_with_mode
            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, send_cmd))
            msg = self._接收消息()
            if msg.command != CMD_OKAY:
                raise RuntimeError(f"SEND 失败，收到 {msg.命令名}")

            file_size = os.path.getsize(local_path)
            # DATA 块既要装进 WRTE（≤_max_payload-8），又不能超过设备端
            # sync 服务的 64KB 固定缓冲区（SYNC_DATA_MAX）
            chunk_size = min(self._max_payload - 8, SYNC_DATA_MAX)
            window = 4
            total_chunks = (file_size + chunk_size - 1) // chunk_size
            print(f'[自研adb] sync推送: chunk={chunk_size}, 窗口={window}, 文件={file_size}字节({total_chunks}块)')
            sent = 0
            t0 = time.time()
            with open(local_path, 'rb') as f:
                while True:
                    batch = []
                    for _ in range(window):
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        batch.append(chunk)
                    if not batch:
                        break
                    for chunk in batch:
                        data_cmd = b'DATA' + struct.pack('<I', len(chunk)) + chunk
                        self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, data_cmd))
                    for chunk in batch:
                        msg = self._接收消息()
                        if msg.command == CMD_WRTE:
                            if msg.payload[:4] == b'FAIL':
                                err_len = struct.unpack('<I', msg.payload[4:8])[0]
                                err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')
                                raise RuntimeError(f"推送失败: {err}")
                            self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
                        elif msg.command == CMD_CLSE:
                            raise RuntimeError("设备在推送过程中关闭连接")
                        elif msg.command != CMD_OKAY:
                            raise RuntimeError(f"DATA 失败，收到 {msg.命令名}")
                        sent += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(sent, file_size)
                            except Exception:
                                pass
            elapsed = time.time() - t0
            rate = sent / elapsed / 1024 if elapsed > 0 else 0
            print(f'[自研adb] sync推送完成: {sent}字节, {elapsed:.1f}秒, {rate:.0f}KB/s')

            mtime = int(os.path.getmtime(local_path))
            done_cmd = b'DONE' + struct.pack('<I', mtime)
            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, done_cmd))
            msg = self._接收消息()
            if msg.command in (CMD_OKAY, CMD_WRTE, CMD_CLSE):
                return True
            raise RuntimeError(f"DONE 失败，收到 {msg.命令名}")
        finally:
            self.sock.settimeout(old)

    def _推送文件_shell(self, local_path: str, remote_path: str, timeout: float, progress_cb) -> bool:
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
            raise RuntimeError(
                f"shell推送初始化失败（目标目录不存在或只读）: "
                f"{touch_out or remote_path}")
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
                    raise last_err
                sent += len(chunk)
                if progress_cb:
                    try:
                        progress_cb(sent, file_size)
                    except Exception:
                        pass
        # 传完后落盘验证：文件存在且字节数一致，杜绝静默成功
        verify = (self.执行shell(f'ls -l "{remote_path}"', timeout=10) or '').strip()
        if not verify or 'No such file' in verify:
            raise RuntimeError(f"shell推送后文件不存在: {remote_path} ({verify or '无输出'})")
        size_out = (self.执行shell(f'wc -c < "{remote_path}"', timeout=10) or '').strip()
        try:
            remote_size = int(size_out.split()[0])
        except Exception:
            remote_size = -1
        if remote_size != file_size:
            raise RuntimeError(
                f"shell推送字节数不一致: 本地{file_size}B 设备{remote_size}B")
        return True

    # ── sync 拉取 ──

    def 拉取文件(self, remote_path: str, local_path: str, timeout: float = 60.0) -> bool:
        print(f'[自研adb] 开始拉取: {remote_path} -> {local_path}')
        try:
            return self._拉取文件_sync(remote_path, local_path, max(timeout, 30))
        except Exception as e:
            print(f'[自研adb] sync拉取失败，回退shell方式: {e}')
            try:
                return self._拉取文件_shell(remote_path, local_path, max(timeout, 120))
            except Exception as e2:
                print(f'[自研adb] shell拉取也失败: {e2}')
                raise

    def _拉取文件_sync(self, remote_path: str, local_path: str, timeout: float) -> bool:
        local_id = self.打开服务('sync:')
        old = self.sock.gettimeout()
        self.sock.settimeout(timeout)
        try:
            path_bytes = remote_path.encode('utf-8')
            recv_cmd = b'RECV' + struct.pack('<I', len(path_bytes)) + path_bytes
            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, recv_cmd))
            msg = self._接收消息()
            if msg.command != CMD_OKAY:
                raise RuntimeError(f"RECV 失败，期望 OKAY，收到 {msg.命令名}")

            file_data = b''
            got_done = False
            while True:
                try:
                    msg = self._接收消息()
                except socket.timeout:
                    if file_data:
                        break
                    raise RuntimeError("拉取超时，未收到完整数据")
                if msg.command == CMD_WRTE:
                    cmd = msg.payload[:4]
                    if cmd == b'DATA':
                        length = struct.unpack('<I', msg.payload[4:8])[0]
                        file_data += msg.payload[8:8 + length]
                        self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
                    elif cmd == b'DONE':
                        self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
                        got_done = True
                        break
                    elif cmd == b'FAIL':
                        err_len = struct.unpack('<I', msg.payload[4:8])[0]
                        err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')
                        raise RuntimeError(f"拉取失败: {err}")
                    else:
                        self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
                elif msg.command == CMD_CLSE:
                    break
                elif msg.command == CMD_OKAY:
                    continue

            if not got_done and not file_data:
                raise RuntimeError("拉取失败：未收到数据")

            with open(local_path, 'wb') as f:
                f.write(file_data)
            return True
        finally:
            self.sock.settimeout(old)
            try:
                self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
            except Exception:
                pass

    def _拉取文件_shell(self, remote_path: str, local_path: str, timeout: float) -> bool:
        import base64
        print(f'[自研adb] shell拉取: base64 {remote_path}')
        b64_data = self.执行shell(f'base64 "{remote_path}"', timeout=timeout)
        b64_clean = ''.join(b64_data.split())
        if not b64_clean:
            raise RuntimeError("shell拉取失败：文件为空或不存在")
        file_data = base64.b64decode(b64_clean)
        with open(local_path, 'wb') as f:
            f.write(file_data)
        return True

    # ── 端口转发 ──

    def 端口转发(self, local_port: int, remote: str) -> bool:
        service = f'host:forward:tcp:{local_port};{remote}'
        self._local_id += 1
        local_id = self._local_id
        self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
        try:
            msg = self._接收消息()
            return msg.command in (CMD_OKAY, CMD_CLSE)
        except Exception:
            return True

    def 取消端口转发(self, local_port: int) -> bool:
        service = f'host:killforward:tcp:{local_port}'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
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
        self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
        try:
            msg = self._接收_message()
            return msg.command in (CMD_OKAY, CMD_CLSE)
        except Exception:
            return False

    def 取消反向转发(self, remote) -> bool:
        remote_spec = remote if isinstance(remote, str) else f'tcp:{remote}'
        service = f'host:killreverse:{remote_spec}'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
            msg = self._接收消息()
            return msg.command in (CMD_OKAY, CMD_CLSE)
        except Exception:
            return False

    def 列出转发(self) -> list:
        service = 'host:list-forward'
        try:
            self._local_id += 1
            local_id = self._local_id
            self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\0'))
            output = b''
            while True:
                msg = self._接收消息()
                if msg.command == CMD_WRTE:
                    output += msg.payload
                    self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
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

    def _扫描单个(ip):
        try:
            s = socket.create_connection((ip, port), timeout=timeout)
            s.close()
            return {'ip': ip, 'port': port}
        except Exception:
            return None

    devices = []
    ips = [f'{网段}{i}' for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        futures = {executor.submit(_扫描单个, ip): ip for ip in ips}
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
