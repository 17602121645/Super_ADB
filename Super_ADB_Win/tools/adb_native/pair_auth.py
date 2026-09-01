# -*- coding: utf-8 -*-
"""
配对认证模块 (兼容 BoringSSL libadb_pairing_auth)
====================================================
封装 SPAKE2 + HKDF-SHA256 + AES-128-GCM，用于 Android 11+ 无线调试配对。

精确兼容官方实现 (pairing_auth/aes_128_gcm.cpp):
  - SPAKE2 输出 64 字节共享密钥 → HKDF-SHA256 派生 16 字节 AES 密钥
  - HKDF info = "adb pairing_auth aes-128-gcm key" (不含 null 终止符, 34 字节)
  - HKDF salt = 空
  - AES-128-GCM nonce = 12 字节: 前 8 字节计数器(小端序) + 后 4 字节 0
  - 加密/解密各自维护独立计数器 (enc_sequence_ / dec_sequence_)
  - 无 AAD (additional data)
  - 密文 = 明文 + 16 字节 GCM tag
"""
from __future__ import annotations

from typing import Optional, Tuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

try:
    from .key_exchange import SPAKE2Context
except ImportError:
    from key_exchange import SPAKE2Context


# HKDF info 字符串 (BoringSSL: sizeof(info) - 1, 不含 null 终止符)
HKDF_INFO = b'adb pairing_auth aes-128-gcm key'
AES_KEY_SIZE = 16
GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16


class Aes128Gcm:
    """
    AES-128-GCM 加解密 (兼容 BoringSSL Aes128Gcm)。

    从 key_material 经 HKDF-SHA256 派生 16 字节 AES 密钥。
    nonce = 8 字节计数器(小端序) + 4 字节 0, 加密/解密计数器独立。
    """

    def __init__(self, key_material: bytes):
        if not key_material:
            raise ValueError("key_material 不能为空")

        # HKDF-SHA256 派生 AES 密钥 (salt=空, info=HKDF_INFO)
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=AES_KEY_SIZE,
            salt=None,
            info=HKDF_INFO,
            backend=default_backend(),
        )
        self._aes_key = hkdf.derive(key_material)
        self._aesgcm = AESGCM(self._aes_key)
        self._enc_sequence = 0
        self._dec_sequence = 0

    def _构建随机数(self, counter: int) -> bytes:
        """构建 nonce: 8 字节计数器(小端序) + 4 字节 0。"""
        nonce = bytearray(GCM_NONCE_SIZE)
        nonce[:8] = counter.to_bytes(8, 'little')
        return bytes(nonce)

    def 加密(self, plaintext: bytes) -> bytes:
        """加密: 返回 明文 + 16 字节 tag。"""
        if not plaintext:
            raise ValueError("plaintext 不能为空")
        nonce = self._构建随机数(self._enc_sequence)
        # 注意: self._aesgcm 是第三方库 cryptography 的 AESGCM 对象，
        # 只能调用其原生英文方法 encrypt/decrypt，不可改成中文名。
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        self._enc_sequence += 1
        return ciphertext

    def 解密(self, ciphertext: bytes) -> bytes:
        """解密: 验证 tag 后返回明文。"""
        if not ciphertext:
            raise ValueError("ciphertext 不能为空")
        nonce = self._构建随机数(self._dec_sequence)
        # 同上: 第三方库对象，用原生 decrypt。
        plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
        self._dec_sequence += 1
        return plaintext

    def 加密后大小(self, plaintext_len: int) -> int:
        """加密后大小 = 明文大小 + 16 字节 tag。"""
        return plaintext_len + GCM_TAG_SIZE

    def 解密后大小(self, ciphertext_len: int) -> int:
        """解密后大小 = 密文大小 (tag 被验证后丢弃)。"""
        return ciphertext_len


class PairingAuth:
    """
    配对认证上下文 (兼容 BoringSSL PairingAuthCtx)。

    封装 SPAKE2 协议 + AES-128-GCM 加解密。

    用法 (客户端/Alice):
        auth = PairingAuth(role='client', password=b'123456')
        msg = auth.获取spake2消息()    # 32 字节
        auth.初始化加密器(their_msg)    # 用对端消息初始化加密器
        encrypted = auth.加密(data)  # 加密
        decrypted = auth.解密(data)  # 解密
    """

    def __init__(self, role: str, password: bytes):
        if role not in ('client', 'server'):
            raise ValueError("role 必须是 'client' 或 'server'")
        if not password:
            raise ValueError("password 不能为空")

        spake_role = 'alice' if role == 'client' else 'bob'
        self._spake2 = SPAKE2Context(spake_role, password)
        self._cipher: Optional[Aes128Gcm] = None
        self._msg: Optional[bytes] = None

    def 获取spake2消息(self) -> bytes:
        """生成 SPAKE2 消息 (32 字节压缩点)。"""
        if self._msg is None:
            self._msg = self._spake2.生成消息()
        return self._msg

    def 初始化加密器(self, their_msg: bytes) -> bool:
        """用对端 SPAKE2 消息初始化 AES-128-GCM 加密器。"""
        if self._cipher is not None:
            raise RuntimeError("init_cipher 只能调用一次")
        if not their_msg or len(their_msg) != 32:
            return False
        try:
            shared_key = self._spake2.处理消息(their_msg)
            self._cipher = Aes128Gcm(shared_key)
            return True
        except Exception:
            return False

    def 加密(self, data: bytes) -> bytes:
        if self._cipher is None:
            raise RuntimeError("必须先调用 init_cipher")
        return self._cipher.加密(data)

    def 解密(self, data: bytes) -> bytes:
        if self._cipher is None:
            raise RuntimeError("必须先调用 init_cipher")
        return self._cipher.解密(data)

    def 安全加密后大小(self, length: int) -> int:
        if self._cipher is None:
            raise RuntimeError("必须先调用 init_cipher")
        return self._cipher.加密后大小(length)

    def 安全解密后大小(self, length: int) -> int:
        if self._cipher is None:
            raise RuntimeError("必须先调用 init_cipher")
        return self._cipher.解密后大小(length)


# 便捷函数
def 配对认证客户端新建(password: bytes) -> PairingAuth:
    return PairingAuth('client', password)


def 配对认证服务端新建(password: bytes) -> PairingAuth:
    return PairingAuth('server', password)


# ── 自测试 ────────────────────────────────────────────────
def 配对认证自测试():
    """配对认证自测试。"""
    password = b'917846'
    client = PairingAuth('client', password)
    server = PairingAuth('server', password)

    c_msg = client.获取spake2消息()
    s_msg = server.获取spake2消息()

    assert client.初始化加密器(s_msg), "客户端 init_cipher 失败"
    assert server.初始化加密器(c_msg), "服务端 init_cipher 失败"

    # 测试加密/解密 (客户端→服务端)
    plaintext = b'Hello, Android Pairing!'
    encrypted = client.加密(plaintext)
    decrypted = server.解密(encrypted)
    assert decrypted == plaintext, f"加解密不匹配: {decrypted} != {plaintext}"

    # 测试加密/解密 (服务端→客户端)
    plaintext2 = b'Response from server'
    encrypted2 = server.加密(plaintext2)
    decrypted2 = client.解密(encrypted2)
    assert decrypted2 == plaintext2, f"加解密不匹配: {decrypted2} != {plaintext2}"

    # 验证加密后大小
    assert client.安全加密后大小(100) == 116, "encrypted_size 计算错误"
    assert client.安全解密后大小(116) == 116, "decrypted_size 计算错误"

    # 验证 HKDF info 长度 (32 字节, 不含 null)
    assert len(HKDF_INFO) == 32, f"HKDF info 长度应为 32, 实际 {len(HKDF_INFO)}"

    print("pairing_auth 自测试通过!")
    print(f"  HKDF info: {HKDF_INFO} ({len(HKDF_INFO)} 字节)")
    print(f"  AES 密钥大小: {AES_KEY_SIZE} 字节")
    print(f"  GCM nonce 大小: {GCM_NONCE_SIZE} 字节")
    return True


if __name__ == '__main__':
    配对认证自测试()
