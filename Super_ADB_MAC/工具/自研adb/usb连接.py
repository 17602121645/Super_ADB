# -*- coding: utf-8 -*-
"""
USB ADB 连接
============
通过 USB 直连 Android 设备，不依赖 adb server。

用法:
    from 工具.自研adb.usb连接 import UsbAdbConnection, 枚举adb设备
    devices = 枚举adb设备()
    if devices:
        conn = UsbAdbConnection(devices[0])
        conn.连接()
        print(conn.执行shell('getprop ro.build.version.release'))
        conn.关闭()
"""

import os
import struct
import socket
from typing import Optional
from .adb协议 import (
    AdbConnection,
    AdbMessage,
    CMD_CNXN,
    CMD_AUTH,
    CMD_OPEN,
    CMD_OKAY,
    CMD_WRTE,
    CMD_CLSE,
    STATE_OFFLINE,
    STATE_AUTH,
    STATE_DEVICE,
    ADB_VERSION,
    ADB_MAX_PAYLOAD,
    AUTH_TOKEN,
    AUTH_SIGNATURE,
    AUTH_RSAPUBLICKEY,
)
from .usb传输层 import UsbTransport, UsbDeviceInfo, 枚举adb设备


class UsbAdbConnection(AdbConnection):
    """USB ADB 连接，继承自 AdbConnection，重写传输层。"""

    def __init__(self, device_info: UsbDeviceInfo, timeout: float = 10.0):
        # 不调用父类 __init__（它会创建 socket）
        self.host = device_info.标识
        self.port = 0
        self.timeout = timeout
        self.sock = None  # USB 模式下不使用 socket
        self.state = STATE_OFFLINE
        self._local_id = 0
        self._remote_id = 0
        self._预读数据 = b''
        self._max_payload = ADB_MAX_PAYLOAD
        # ★ 与 TCP 连接使用同一份密钥（配置/super_adb_key），
        # 这样任一方式授权过后，另一种方式也能直接通过签名验证，
        # 不会重复弹授权框。旧版用 ~/.android/super_adb_key，两套密钥互不认。
        # 路径由 adb协议._定位密钥路径() 统一解析（打包版自动迁移密钥）。
        from 工具.自研adb.adb协议 import _定位密钥路径
        self._key_path = _定位密钥路径()
        self._usb: Optional[UsbTransport] = None
        self._device_info = device_info

    def 连接(self) -> bool:
        """通过 USB 连接设备并完成握手。

        处理 Android 14 偶发首个 CNXN 不回包的问题：
        仅当接收超时时重发 CNXN（不再 reset USB 设备，
        reset 会打断正在进行中的握手/认证）。
        """
        self._usb = UsbTransport(self._device_info, timeout=int(self.timeout * 1000))
        self._usb.打开()

        banner = b'host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,abb_exec'
        for attempt in range(4):  # 1 次首发 + 最多 3 次重发
            self._发送(AdbMessage(CMD_CNXN, ADB_VERSION, ADB_MAX_PAYLOAD, banner))
            try:
                msg = self._接收消息()
            except Exception as e:
                print(f'[USB] CNXN 无响应({e})，重发 ({attempt + 1}/3)')
                continue
            if msg.command == CMD_CNXN:
                self._max_payload = self._协商payload(msg.arg1)
                self.state = STATE_DEVICE
                return True
            if msg.command == CMD_AUTH and msg.arg0 == AUTH_TOKEN:
                return self._处理认证_usb(msg.payload)
            break
        self.state = STATE_AUTH
        return False

    def _处理认证_usb(self, token: bytes) -> bool:
        """USB 模式下的认证处理，流程与官方 adb 客户端一致：

        1. 用私钥对 token 做 PKCS#1 v1.5 + SHA1 签名（等价官方 RSA_sign(NID_sha1)），
           发送 AUTH SIGNATURE；
        2. 设备验证通过 → 直接回 CNXN；
        3. 验证失败（设备没存过对应公钥）→ 设备回新的 AUTH TOKEN，
           此时发送 AUTH RSAPUBLICKEY，公钥必须是 524 字节 android_pubkey_t
           结构的 base64（设备端 adbd_auth_verify 对解码长度严格校验，
           不是 524 字节的 key 会被直接丢弃，导致每次连接都重新弹授权框）。
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = self._加载私钥()
        if private_key is None:
            private_key = self._生成密钥对()
        if private_key is None:
            print('[USB] 无法加载/生成私钥，认证失败')
            return False

        # 签名 token（与官方 adb_auth_sign 一致：NID_sha1 + PKCS#1 v1.5）
        signature = private_key.sign(token, padding.PKCS1v15(), hashes.SHA1())
        self._发送(AdbMessage(CMD_AUTH, AUTH_SIGNATURE, 0, signature))

        try:
            msg = self._接收消息()
        except Exception as e:
            print(f'[USB] 发送签名后无响应: {e}')
            return False

        if msg.command == CMD_CNXN:
            self._max_payload = self._协商payload(msg.arg1)
            self.state = STATE_DEVICE
            return True

        if msg.command == CMD_AUTH and msg.arg0 == AUTH_TOKEN:
            # 签名验证失败 → 发送公钥请求用户授权（复用父类的标准 524 字节公钥编码）
            public_key = self._获取公钥()
            if not public_key:
                print('[USB] 无法获取公钥，认证失败')
                return False
            # adbd 要求 null 结尾字符串（见官方 send_auth_publickey）
            self._发送(AdbMessage(CMD_AUTH, AUTH_RSAPUBLICKEY, 0, public_key + b'\0'))
            print('[USB] 已发送公钥，请在设备上点击“允许 USB 调试”...')
            old_timeout = self._usb.timeout
            self._usb.timeout = 60000  # 用户点击授权可能较慢
            try:
                msg = self._接收消息()
            except Exception as e:
                print(f'[USB] 等待用户授权超时: {e}')
                return False
            finally:
                self._usb.timeout = old_timeout
            if msg.command == CMD_CNXN:
                self._max_payload = self._协商payload(msg.arg1)
                self.state = STATE_DEVICE
                return True
            print(f'[USB] 公钥认证失败，收到 {msg.command:#x}')

        self.state = STATE_AUTH
        return False

    def _发送(self, msg: AdbMessage):
        """通过 USB 发送 ADB 消息。"""
        if not self._usb:
            raise RuntimeError("USB 未连接")
        self._usb.发送(msg.打包())

    def _接收消息(self) -> AdbMessage:
        """通过 USB 接收 ADB 消息。"""
        if not self._usb:
            raise RuntimeError("USB 未连接")
        # 先读 24 字节头
        header = self._usb.接收(24)
        command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', header)
        # 读 payload
        payload = self._usb.接收(length) if length > 0 else b''
        return AdbMessage(command, arg0, arg1, payload)

    def _recv_exact(self, n: int) -> bytes:
        """USB 模式下精确读取 n 字节（兼容父类方法）。"""
        return self._usb.接收(n)

    def 关闭(self):
        """关闭 USB 连接。"""
        if self._usb:
            self._usb.关闭()
            self._usb = None
        self.state = STATE_OFFLINE


def 测试usb连接():
    """测试 USB 连接。"""
    print('枚举 ADB USB 设备...')
    devices = 枚举adb设备()
    print(f'找到 {len(devices)} 个设备')
    for d in devices:
        print(f'  {d}')

    if devices:
        print(f'\n连接 {devices[0].标识}...')
        conn = UsbAdbConnection(devices[0])
        try:
            if conn.连接():
                print('连接成功!')
                result = conn.执行shell('getprop ro.build.version.release')
                print(f'Android 版本: {result.strip()}')
            else:
                print('连接失败，需要设备授权')
        finally:
            conn.关闭()


if __name__ == '__main__':
    测试usb连接()
