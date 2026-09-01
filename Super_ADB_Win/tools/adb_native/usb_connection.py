# -*- coding: utf-8 -*-
"""
USB ADB 连接
============
通过 USB 直连 Android 设备，不依赖 adb server。

用法:
    from tools.adb_native.usb_connection import UsbAdbConnection, 枚举adb设备
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
import queue
import threading
from typing import Optional
from .adb_protocol import (
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
from .usb_transport import UsbTransport, UsbDeviceInfo, 枚举adb设备


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
        # ★ delayed_ack 相关：本类不调用父类 __init__，必须显式初始化。
        # delayed_ack 是连接级协商（TCP 用 CNXN banner 协商），USB 连接不发
        # features 协商（见本类 连接() 的 banner），因此永远不启用 delayed_ack。
        # 缺这两个属性会让继承的 _发OPEN/_回OKAY/_解析OKAY字节 直接抛
        # AttributeError → USB 的 执行shell/logcat/push 等全部拿不到数据。
        self._delayed_ack = False
        self._流ASB = 0
        # ★ 与 TCP 连接使用同一份密钥（config/super_adb_key），
        # 这样任一方式授权过后，另一种方式也能直接通过签名验证，
        # 不会重复弹授权框。旧版用 ~/.android/super_adb_key，两套密钥互不认。
        # 路径由 adb_protocol._定位密钥路径() 统一解析（打包版自动迁移密钥）。
        from tools.adb_native.adb_protocol import _定位密钥路径
        self._key_path = _定位密钥路径()
        self._usb: Optional[UsbTransport] = None
        self._device_info = device_info
        # ── USB 单管道复用 ──
        # USB 连接是共享的（同一设备只有一条 transport、一对 IN/OUT 端点），
        # 流式服务（如 logcat）与普通命令（如 执行shell）会并发使用同一管道。
        # 规则：
        #   1. 写：header + payload 必须连续，用 _写锁 保证原子；
        #   2. 读：全局只允许一个线程直接读 USB。流式服务运行期间由流线程
        #      独占读取，并把不属于自己的报文投递到 _命令队列，供命令线程消费；
        #   3. 同一时刻只允许一个命令线程（_命令锁），避免多路命令抢分发结果。
        self._写锁 = threading.Lock()
        self._读锁 = threading.Lock()
        self._命令锁 = threading.RLock()
        self._流读取线程 = None      # 正在独占读取 USB 的流线程 ident
        self._流local_id = None      # 流式服务的 local_id
        self._命令队列 = None        # queue.Queue，流运行期间给命令线程转发报文
        self._命令等待超时 = 30.0
        self.log_callback = None  # 授权等关键事件回调到主窗口输出栏

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
                self._max_payload = self._协商载荷(msg.arg1)
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
        signature = self._rsa签名(private_key, token)
        self._发送(AdbMessage(CMD_AUTH, AUTH_SIGNATURE, 0, signature))

        try:
            msg = self._接收消息()
        except Exception as e:
            print(f'[USB] 发送签名后无响应: {e}')
            return False

        if msg.command == CMD_CNXN:
            self._max_payload = self._协商载荷(msg.arg1)
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
            if self.log_callback:
                try:
                    self.log_callback(
                        '[授权提示] 已向设备发送公钥，请在设备屏幕上点击「允许USB调试」'
                        '并勾选「始终允许使用这台计算机进行调试」'
                    )
                except Exception:
                    pass
            old_timeout = self._usb.timeout
            self._设置usb超时(60000)  # 用户点击授权可能较慢
            try:
                msg = self._接收消息()
            except Exception as e:
                print(f'[USB] 等待用户授权超时: {e}')
                return False
            finally:
                self._设置usb超时(old_timeout)
            if msg.command == CMD_CNXN:
                self._max_payload = self._协商载荷(msg.arg1)
                self.state = STATE_DEVICE
                return True
            print(f'[USB] 公钥认证失败，收到 {msg.command:#x}')

        self.state = STATE_AUTH
        return False

    def _设置usb超时(self, timeout_ms: int):
        """设置 USB 读写超时（毫秒）。

        原生 WinUSB 后端的超时由管道策略决定，只改 timeout 字段不生效，
        必须调用传输层的 更新超时() 下发；pyusb 后端读字段即可。
        """
        if not self._usb:
            return
        try:
            self._usb.更新超时(int(timeout_ms))
        except AttributeError:
            self._usb.timeout = int(timeout_ms)

    # ── 覆盖父类传输超时抽象：sync 推送/拉取等公用，USB 用 _usb.timeout（毫秒）──
    def _读取传输超时(self) -> float:
        if self._usb:
            try:
                return self._usb.timeout / 1000.0
            except Exception:
                pass
        return self.timeout

    def _设置传输超时(self, 秒: float) -> None:
        self._设置usb超时(int(秒 * 1000))

    def _发送(self, msg: AdbMessage):
        # 通过 USB 发送 ADB 消息。
        # 关键修复：USB 上必须分两次发送——先发 24 字节消息头，再发 payload。
        # 一次性发送（头+payload 连在一起）会导致部分设备（尤其荣耀/华为）
        # 不响应，表现为写入成功但读取超时/error=31。官方 adb 的
        # libadbusbconnection 也是分两次 WriteFile（windows.cpp:332）。
        # 加 _写锁：多线程（流式 logcat + 普通命令）共用同一 OUT 端点时，
        # 头与载荷之间若被其他线程插入写入，设备侧会解析错位。
        if not self._usb:
            raise RuntimeError("USB 未连接")
        header, payload = msg.拆分打包()
        with self._写锁:
            self._usb.发送(header)
            if payload:
                self._usb.发送(payload)

    def _原始接收消息(self) -> AdbMessage:
        """直接从 USB IN 端点读取一条完整 ADB 消息（头+载荷原子）。"""
        if not self._usb:
            raise RuntimeError("USB 未连接")
        with self._读锁:
            header = self._usb.接收(24)
            command, arg0, arg1, length, crc, magic = struct.unpack('<IIIIII', header)
            payload = self._usb.接收(length) if length > 0 else b''
        return AdbMessage(command, arg0, arg1, payload)

    def _接收消息(self) -> AdbMessage:
        """接收一条 ADB 消息。

        流式服务（shell流）运行期间，USB IN 端点由流线程独占读取，
        其他线程的报文由流线程转发到 _命令队列——此处改为从队列取，
        避免两个线程抢读同一端点导致报文被对方吞掉。
        """
        q = self._命令队列
        if q is not None and threading.get_ident() != self._流读取线程:
            try:
                item = q.get(timeout=self._命令等待超时)
            except queue.Empty:
                raise TimeoutError('等待 USB 报文超时')
            if isinstance(item, BaseException):
                raise item
            return item
        return self._原始接收消息()

    def _转发给命令(self, msg: AdbMessage):
        """流线程读到的非本流报文，转交给等待中的命令线程。"""
        q = self._命令队列
        if q is not None:
            try:
                q.put(msg)
            except Exception:
                pass

    @staticmethod
    def _是超时异常(e) -> bool:
        """判断异常是否为读取超时（pyusb: USBTimeoutError，原生: TimeoutError/OSError）。"""
        if isinstance(e, (socket.timeout, TimeoutError)):
            return True
        msg = str(e).lower()
        return 'timeout' in msg or 'timed out' in msg

    @staticmethod
    def _安全回调(on_data, data: bytes):
        try:
            on_data(data)
        except Exception:
            pass

    def _精确接收(self, n: int) -> bytes:
        """USB 模式下精确读取 n 字节（兼容父类方法）。"""
        with self._读锁:
            return self._usb.接收(n)

    def shell流(self, command: str, on_data, stop_event,
                open_timeout: float = 10.0, service: str = 'shell'):
        """在 USB 连接上运行流式 shell（如 logcat），供后台线程作为 target 使用。

        与 自研adb客户端.shell流 签名一致，便于调用方（日志查看器）无差别使用。

        与 TCP 版的差异:
          - TCP 版从连接池借一条独占连接；USB 只有一条共享 transport，
            因此流与普通命令复用同一管道，由本方法独占读取并转发非本流报文；
          - 超时用 self._usb.timeout（毫秒），不是 sock.settimeout；
          - 结束时只关闭 shell 流（CLSE），不关闭底层 USB 连接（共享缓存）。
        """
        local_id = None
        remote_id = 0
        try:
            # 打开服务期间不能与普通命令交错（两者都会读端点并改 _remote_id）
            with self._命令锁:
                local_id = self.打开服务(f'{service}:{command}')
                remote_id = self._remote_id
                预读 = self._预读数据
                self._预读数据 = b''
                # 注册为唯一读取者：此后其他线程的 _接收消息 走队列
                self._流local_id = local_id
                self._流读取线程 = threading.get_ident()
                self._命令队列 = queue.Queue()
            if 预读:
                self._安全回调(on_data, 预读)

            old_timeout = self._usb.timeout
            # 短超时轮询：保证 stop_event 置位后最多 ~0.5s 内退出
            self._设置usb超时(500)
            try:
                while not stop_event.is_set():
                    try:
                        msg = self._原始接收消息()
                    except Exception as e:
                        if self._是超时异常(e):
                            continue
                        break  # 连接断开
                    if msg.command == CMD_WRTE:
                        if msg.arg1 != local_id:
                            self._转发给命令(msg)
                            continue
                        if msg.payload:
                            self._安全回调(on_data, msg.payload)
                        try:
                            self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
                        except Exception:
                            break
                    elif msg.command == CMD_CLSE:
                        if msg.arg1 != local_id:
                            self._转发给命令(msg)
                            continue
                        try:
                            self._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                        except Exception:
                            pass
                        break
                    elif msg.arg1 != local_id:
                        # OKAY 等其他报文：属于命令流的转发，属于本流的忽略
                        self._转发给命令(msg)
            finally:
                try:
                    self._设置usb超时(old_timeout)
                except Exception:
                    pass
        except Exception as e:
            print(f'[USB] shell流异常: {e}')
        finally:
            # 注销读取身份并唤醒仍在等待转发的命令线程
            q = self._命令队列
            self._命令队列 = None
            self._流读取线程 = None
            self._流local_id = None
            if q is not None:
                try:
                    q.put(RuntimeError('USB 流已结束，报文转发中止'))
                except Exception:
                    pass
            if local_id is not None:
                try:
                    self._发送(AdbMessage(CMD_CLSE, local_id, remote_id))
                except Exception:
                    pass
            # 底层 USB 连接是共享缓存的，这里绝不关闭

    def 执行shell(self, command: str, timeout: float = 30.0) -> str:
        """USB 模式下执行 shell 命令。

        重写父类方法：父类用 self.sock.gettimeout()/settimeout() 管理超时，
        USB 模式下 sock 为 None，改用 self._usb.timeout（毫秒）。
        流式服务运行期间不改动 _usb.timeout（端点由流线程独占），
        改为设置队列等待超时。
        """
        with self._命令锁:
            转发模式 = self._命令队列 is not None
            old_usb_timeout = self._usb.timeout
            old_wait = self._命令等待超时
            if 转发模式:
                self._命令等待超时 = timeout
            else:
                self._设置usb超时(int(timeout * 1000))
            local_id = None
            output = b''
            try:
                local_id = self.打开服务(f'shell:{command}')
                output = self._预读数据
                self._预读数据 = b''
                while True:
                    msg = self._接收消息()
                    if msg.command == CMD_WRTE:
                        if msg.arg1 != local_id:
                            try:
                                self._发送(AdbMessage(CMD_OKAY, msg.arg1, msg.arg0))
                            except Exception:
                                pass
                            continue
                        output += msg.payload
                        self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))
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
            except Exception:
                # USB 超时异常类型不固定（pyusb: USBTimeoutError, native: OSError），
                # 统一捕获后主动关闭流
                if local_id is not None:
                    try:
                        self._发送(AdbMessage(CMD_CLSE, local_id, self._remote_id))
                    except Exception:
                        pass
            finally:
                self._命令等待超时 = old_wait
                if not 转发模式:
                    try:
                        self._设置usb超时(old_usb_timeout)
                    except Exception:
                        pass
            return output.decode('utf-8', errors='replace')

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
