# -*- coding: utf-8 -*-
"""
edwards25519 曲线运算 + SPAKE2 协议实现（BoringSSL 兼容版）
================================================================
纯 Python 实现，精确兼容 BoringSSL 的 SPAKE2 over edwards25519，
用于 Android 11+ 无线调试配对 (adb pair)。

与 BoringSSL (crypto/curve25519/spake25519.cc) 的兼容性:
  - M/N 点: BoringSSL 自定义值 (非 RFC 9382)
  - 密码标量: SHA-512(password) → sc_reduce → password_scalar_hack
  - 私钥: 64 字节随机 → sc_reduce → 乘以 8 (清除余因子)
  - 共享密钥: 带 8 字节小端长度前缀的 SHA-512
  - Alice 生成消息用 M, 解除掩码用 N; Bob 相反

曲线参数:
  p = 2^255 - 19
  a = -1
  d = -121665 * inv(121666) mod p
  L (阶) = 2^252 + 27742317777372353535851937790883648493
"""
from __future__ import annotations

import hashlib
import os
from typing import Tuple, Optional

# ── 曲线参数 ──────────────────────────────────────────────
P = 2**255 - 19
A = -1 % P
D = (-121665 * pow(121666, P - 2, P)) % P

# 基点坐标
GX = 15112221349535400772501151409588531511454012693041857206046113283949847762202
GY = 46316835694926478169428394003475163141307993866256225615783033603165251855960

# 基点的阶 (prime-order subgroup order)
L = 2**252 + 27742317777372353535851937790883648493

# BoringSSL SPAKE2 M, N 生成元 (压缩点编码)
# 来源: BoringSSL crypto/curve25519/spake25519.cc
# M seed: "edwards25519 point generation seed (M)"
# N seed: "edwards25519 point generation seed (N)"
M_COMPRESSED = bytes.fromhex(
    '5ada7e4bf6ddd9adb6626d32131c6b5c51a1e347a3478f53cfcf441b88eed12e')
N_COMPRESSED = bytes.fromhex(
    '10e3df0ae37d8e7a99b5fe74b44672103dbddcbd06af680d71329a11693bc778')


# ── 模运算工具 ────────────────────────────────────────────
def _求逆(x: int) -> int:
    """模 p 求逆 (费马小定理)。"""
    return pow(x % P, P - 2, P)


def _开方(n: int) -> Optional[int]:
    """模 p 开平方 (p ≡ 5 mod 8)。返回 None 如果无解。"""
    x = pow(n % P, (P + 3) // 8, P)
    if (x * x - n) % P != 0:
        x = (x * pow(2, (P - 1) // 4, P)) % P
    if (x * x - n) % P != 0:
        return None
    return x


def _标量归约(input_bytes: bytes) -> bytes:
    """
    BoringSSL x25519_sc_reduce: 将 64 字节小端序整数 mod L,
    返回 32 字节小端序结果。
    """
    n = int.from_bytes(input_bytes, 'little')
    r = n % L
    return r.to_bytes(32, 'little')


def _口令标量兼容(s: int) -> int:
    """
    BoringSSL password_scalar_hack:
    由于历史 bug (漏掉 left_shift_3), password_scalar 不是 8 的倍数。
    通过添加 kOrder 的 1/2/4 倍使低 3 位变为 0, 从而将 mask 点
    移入素数阶子群。按位顺序检查 (每次添加后可能改变后续位)。
    """
    if s & 1:
        s += L
    if s & 2:
        s += 2 * L
    if s & 4:
        s += 4 * L
    return s


# ── 扩展坐标点表示 (X:Y:Z:T), x=X/Z, y=Y/Z, xy=T/Z ────
class Point:
    """edwards25519 扩展坐标点。"""
    __slots__ = ('X', 'Y', 'Z', 'T')

    def __init__(self, X: int = 0, Y: int = 1, Z: int = 1, T: int = 0):
        self.X = X % P
        self.Y = Y % P
        self.Z = Z % P
        self.T = T % P

    @classmethod
    def 零点(cls) -> 'Point':
        return cls(0, 1, 1, 0)

    @classmethod
    def 基点(cls) -> 'Point':
        return cls(GX, GY, 1, (GX * GY) % P)

    def 转仿射坐标(self) -> Tuple[int, int]:
        if self.Z == 0:
            return (0, 0)
        zinv = _求逆(self.Z)
        return ((self.X * zinv) % P, (self.Y * zinv) % P)

    def 编码(self) -> bytes:
        """压缩点编码: 32 字节小端 y + x 符号位。"""
        x, y = self.转仿射坐标()
        buf = bytearray(y.to_bytes(32, 'little'))
        if x & 1:
            buf[31] |= 0x80
        return bytes(buf)

    @classmethod
    def 解码(cls, data: bytes) -> 'Point':
        if len(data) != 32:
            raise ValueError(f"压缩点长度应为 32 字节, 实际 {len(data)}")
        data = bytearray(data)
        sign = (data[31] >> 7) & 1
        data[31] &= 0x7f
        y = int.from_bytes(bytes(data), 'little')
        if y >= P:
            raise ValueError("y 坐标超出范围")
        y2 = (y * y) % P
        num = (y2 - 1) % P
        den = (D * y2 + 1) % P
        x2 = (num * _求逆(den)) % P
        x = _开方(x2)
        if x is None:
            raise ValueError("无法恢复 x 坐标 (点不在曲线上)")
        if (x & 1) != sign:
            x = P - x
        return cls(x, y, 1, (x * y) % P)

    def 倍点(self) -> 'Point':
        X1, Y1, Z1 = self.X, self.Y, self.Z
        A = (X1 * X1) % P
        B = (Y1 * Y1) % P
        C = (2 * Z1 * Z1) % P
        D = (A * -1) % P
        E = (((X1 + Y1) * (X1 + Y1) - A - B)) % P
        G = (D + B) % P
        F = (G - C) % P
        H = (D - B) % P
        return Point((E * F) % P, (G * H) % P, (F * G) % P, (E * H) % P)

    def 加法(self, other: 'Point') -> 'Point':
        X1, Y1, Z1, T1 = self.X, self.Y, self.Z, self.T
        X2, Y2, Z2, T2 = other.X, other.Y, other.Z, other.T
        A = ((Y1 - X1) * (Y2 - X2)) % P
        B = ((Y1 + X1) * (Y2 + X2)) % P
        C = (T1 * 2 * D * T2) % P
        D_ = (Z1 * 2 * Z2) % P
        E = (B - A) % P
        F = (D_ - C) % P
        G = (D_ + C) % P
        H = (B + A) % P
        return Point((E * F) % P, (G * H) % P, (F * G) % P, (E * H) % P)

    def 标量乘法(self, scalar: int) -> 'Point':
        # 绝对不能在这里做 scalar %= L！
        # SPAKE2 依赖标量的"低 3 位为 0"这一性质来消掉 M/N 点的挠分量
        # (password_scalar_hack 加 L/2L/4L、私钥乘 8 都是为此)。
        # 一旦对 L 取模, 低 3 位性质丢失, 挠分量不再被消去,
        # 与 BoringSSL 算出的点就不一致, 最终导致 PeerInfo 解密 InvalidTag。
        if scalar < 0:
            raise ValueError("标量不能为负数")
        result = Point.零点()
        addend = Point(self.X, self.Y, self.Z, self.T)
        while scalar > 0:
            if scalar & 1:
                result = result.加法(addend)
            addend = addend.倍点()
            scalar >>= 1
        return result

    def __eq__(self, other) -> bool:
        if not isinstance(other, Point):
            return False
        x1, y1 = self.转仿射坐标()
        x2, y2 = other.转仿射坐标()
        return x1 == x2 and y1 == y2

    def __repr__(self) -> str:
        x, y = self.转仿射坐标()
        return f"Point(x={x:#x}, y={y:#x})"


# 预计算 M, N 点
M_POINT = Point.解码(M_COMPRESSED)
N_POINT = Point.解码(N_COMPRESSED)


# ── SPAKE2 共享密钥派生 ───────────────────────────────────
def _带长度前缀更新(sha, data: bytes):
    """BoringSSL update_with_length_prefix: 8 字节小端长度 + 数据。"""
    length_le = len(data).to_bytes(8, 'little')
    sha.update(length_le)
    sha.update(data)


def _派生共享密钥(is_alice: bool,
                        my_name: bytes, their_name: bytes,
                        my_msg: bytes, their_msg: bytes,
                        dh_shared: bytes, password_hash: bytes) -> bytes:
    """
    BoringSSL SPAKE2_process_msg 共享密钥派生:
    SHA512( 各字段带 8 字节小端长度前缀 )

    Alice 顺序: my_name, their_name, my_msg, their_msg, dh_shared, password_hash
    Bob   顺序: their_name, my_name, their_msg, my_msg, dh_shared, password_hash
    """
    sha = hashlib.sha512()
    if is_alice:
        _带长度前缀更新(sha, my_name)
        _带长度前缀更新(sha, their_name)
        _带长度前缀更新(sha, my_msg)
        _带长度前缀更新(sha, their_msg)
    else:
        _带长度前缀更新(sha, their_name)
        _带长度前缀更新(sha, my_name)
        _带长度前缀更新(sha, their_msg)
        _带长度前缀更新(sha, my_msg)
    _带长度前缀更新(sha, dh_shared)
    _带长度前缀更新(sha, password_hash)
    return sha.digest()


# ── SPAKE2 协议 (BoringSSL 兼容) ─────────────────────────
class SPAKE2Context:
    """
    SPAKE2 上下文 (兼容 BoringSSL SPAKE2_CTX)。

    支持 Alice (客户端) 和 Bob (服务端) 两种角色。

    用法:
        ctx = SPAKE2Context(role='alice', password=b'123456')
        msg = ctx.生成消息()       # 生成要发送的消息 (32 字节)
        key = ctx.处理消息(their_msg)  # 处理对端消息, 返回共享密钥 (64 字节)
    """

    # 名称包含 null 终止符 (BoringSSL 用 sizeof)
    CLIENT_NAME = b'adb pair client\x00'
    SERVER_NAME = b'adb pair server\x00'

    def __init__(self, role: str, password: bytes):
        if role not in ('alice', 'bob'):
            raise ValueError("role 必须是 'alice' 或 'bob'")
        if not password:
            raise ValueError("password 不能为空")
        self.role = role
        self.is_alice = (role == 'alice')
        self.password = password

        # BoringSSL: my_name / their_name (包含 null 终止符)
        if self.is_alice:
            self.my_name = self.CLIENT_NAME
            self.their_name = self.SERVER_NAME
            self.msg_point = M_POINT   # Alice 生成消息用 M
            self.mask_point = N_POINT  # Alice 解除掩码用 N
        else:
            self.my_name = self.SERVER_NAME
            self.their_name = self.CLIENT_NAME
            self.msg_point = N_POINT   # Bob 生成消息用 N
            self.mask_point = M_POINT  # Bob 解除掩码用 M

        # 状态
        self._state = 'init'  # init → msg_generated → key_generated
        self._private_key: Optional[int] = None
        self._password_scalar: Optional[int] = None
        self._password_hash: Optional[bytes] = None
        self._my_msg: Optional[bytes] = None

    def _派生口令标量(self):
        """BoringSSL 密码标量派生: SHA512 → sc_reduce → password_scalar_hack。"""
        # password_hash = 完整 64 字节 SHA-512(password)
        self._password_hash = hashlib.sha512(self.password).digest()
        # sc_reduce: 64 字节 → 32 字节 mod L
        reduced = _标量归约(self._password_hash)
        s = int.from_bytes(reduced, 'little')
        # password_scalar_hack: 使低 3 位为 0
        self._password_scalar = _口令标量兼容(s)

    def 生成消息(self) -> bytes:
        """
        BoringSSL SPAKE2_generate_msg:
        1. 生成私钥: 64 字节随机 → sc_reduce → 乘以 8
        2. P = private_key * G
        3. mask = password_scalar * (M or N)
        4. P* = P + mask
        5. 返回压缩点编码 (32 字节)
        """
        if self._state != 'init':
            raise RuntimeError("generate_msg 只能在 init 状态调用")

        # 派生密码标量
        self._派生口令标量()

        # 生成私钥: 64 字节随机 → sc_reduce → 乘以 8
        private_tmp = os.urandom(64)
        reduced = _标量归约(private_tmp)
        self._private_key = int.from_bytes(reduced, 'little') * 8

        # P = private_key * G
        P = Point.基点().标量乘法(self._private_key)

        # mask = password_scalar * msg_point
        mask = self.msg_point.标量乘法(self._password_scalar)

        # P* = P + mask
        Pstar = P.加法(mask)

        self._my_msg = Pstar.编码()
        self._state = 'msg_generated'
        return self._my_msg

    def 处理消息(self, their_msg: bytes) -> bytes:
        """
        BoringSSL SPAKE2_process_msg:
        1. 解码对端消息 Q*
        2. peers_mask = password_scalar * mask_point (解除掩码)
        3. Q = Q* - peers_mask
        4. dh_shared = private_key * Q
        5. 共享密钥 = SHA512(带长度前缀的各字段)
        返回 64 字节共享密钥。
        """
        if self._state != 'msg_generated':
            raise RuntimeError("process_msg 只能在 msg_generated 状态调用")
        if len(their_msg) != 32:
            raise ValueError(f"对端消息长度应为 32 字节, 实际 {len(their_msg)}")

        # 解码对端消息 Q*
        Qstar = Point.解码(their_msg)

        # peers_mask = password_scalar * mask_point
        peers_mask = self.mask_point.标量乘法(self._password_scalar)

        # Q = Q* - peers_mask (点减: Q* + (-peers_mask))
        neg_mask = Point((-peers_mask.X) % P, peers_mask.Y,
                         peers_mask.Z, (-peers_mask.T) % P)
        Q = Qstar.加法(neg_mask)

        # dh_shared = private_key * Q
        dh_shared_point = Q.标量乘法(self._private_key)
        dh_shared = dh_shared_point.编码()

        # 派生共享密钥
        key = _派生共享密钥(
            is_alice=self.is_alice,
            my_name=self.my_name,
            their_name=self.their_name,
            my_msg=self._my_msg,
            their_msg=their_msg,
            dh_shared=dh_shared,
            password_hash=self._password_hash,
        )

        self._state = 'key_generated'
        return key


# 便捷别名
class SPAKE2Client(SPAKE2Context):
    """SPAKE2 客户端 (Alice 角色)。"""
    def __init__(self, password: bytes):
        super().__init__('alice', password)


class SPAKE2Server(SPAKE2Context):
    """SPAKE2 服务端 (Bob 角色)。"""
    def __init__(self, password: bytes):
        super().__init__('bob', password)


# ── 自测试 ────────────────────────────────────────────────
def spake2自测试():
    """SPAKE2 自测试: 验证 Alice 和 Bob 能协商出相同的共享密钥。"""
    password = b'917846'
    alice = SPAKE2Context('alice', password)
    bob = SPAKE2Context('bob', password)

    a_msg = alice.生成消息()
    b_msg = bob.生成消息()

    assert len(a_msg) == 32, f"Alice 消息长度应为 32, 实际 {len(a_msg)}"
    assert len(b_msg) == 32, f"Bob 消息长度应为 32, 实际 {len(b_msg)}"

    a_key = alice.处理消息(b_msg)
    b_key = bob.处理消息(a_msg)

    assert a_key == b_key, f"共享密钥不匹配:\n  Alice: {a_key.hex()}\n  Bob:   {b_key.hex()}"
    assert len(a_key) == 64, f"共享密钥长度应为 64, 实际 {len(a_key)}"

    # 验证错误密码不能协商出相同密钥
    alice2 = SPAKE2Context('alice', b'wrong')
    bob2 = SPAKE2Context('bob', b'right')
    a2_msg = alice2.生成消息()
    b2_msg = bob2.生成消息()
    a2_key = alice2.处理消息(b2_msg)
    b2_key = bob2.处理消息(a2_msg)
    assert a2_key != b2_key, "错误密码不应协商出相同密钥"

    # 验证 password_scalar_hack 使低 3 位为 0
    for test_pw in [b'1', b'12', b'123', b'1234', b'12345', b'123456', b'917846']:
        h = hashlib.sha512(test_pw).digest()
        reduced = _标量归约(h)
        s = int.from_bytes(reduced, 'little')
        hacked = _口令标量兼容(s)
        assert (hacked & 7) == 0, f"password_scalar_hack 后低 3 位应为 0: {test_pw}"

    print(f"SPAKE2 自测试通过!")
    print(f"  共享密钥 (64 字节): {a_key.hex()[:64]}...")
    print(f"  M 点: {M_COMPRESSED.hex()}")
    print(f"  N 点: {N_COMPRESSED.hex()}")
    return True


if __name__ == '__main__':
    spake2自测试()
