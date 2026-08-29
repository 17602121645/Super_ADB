# -*- coding: utf-8 -*-
"""
自研 ADB 客户端（连接池化 —— 最终版）
======================================
薄包装层：对 AdbConnection 的每次操作从全局连接池借用独立连接，用完归还，
实现:
  - 多线程并发安全（每线程/操作独占一个物理连接）
  - 认证复用（首次认证后，池内连接不再重复弹窗）
  - 并发首次建连去重（由 adb协议._连接池 的 _建连事件 保证）
  - root 重启 adbd 后清池重建

连接池实现位于 adb协议 模块（借用连接/归还连接/关闭设备连接 等），
本模块只做薄封装，避免双池冲突。

ADB工具.py 通过 `from 工具.自研adb import 自研adb客户端` 使用，接口保持稳定。
"""

import os
import socket
import time
import threading
from typing import Optional, Callable

from 工具.自研adb.adb协议 import (
    AdbConnection,
    AdbMessage,
    CMD_OPEN, CMD_OKAY, CMD_WRTE, CMD_CLSE,
    STATE_DEVICE,
    # 连接池 API
    借用连接 as _池借用,
    归还连接 as _池归还,
    关闭设备连接 as _池关闭设备,
    关闭全部连接 as _池关闭全部,
    清理空闲连接 as _池清理空闲,
    已有可用连接 as _池已有可用连接,
    剥离连接 as _池剥离,
    _AdbStreamSocket,
)


def _归还后(conn: Optional[AdbConnection]):
    """归还连接（供 finally 使用）。"""
    if conn is not None:
        try:
            _池归还(conn)
        except Exception:
            pass


class 自研adb客户端:
    """自研 ADB 客户端（连接池化）。

    用法:
        client = 自研adb客户端('192.168.1.100', 5555)
        client.连接()                       # 触发认证，预热连接
        out = client.执行shell('ls /sdcard')
        client.推送文件('a.apk', '/sdcard/a.apk')
        client.关闭()                       # 关闭该设备的所有连接

    多线程安全：每个方法调用从池借用独立连接，用完归还。
    """
    # 类级别：设备首次认证锁，确保同一设备只有一个线程做首次认证
    _认证锁字典: dict = {}
    _认证锁字典锁 = threading.Lock()
    # ★ 负缓存：连接/认证失败后一段时间内不再重试。未授权设备每次重试都要
    # 走完整 AUTH 流程（部分 ROM 还会重复弹授权框），上层高频调用（扫描回填/
    # 监控轮询）会造成重试风暴；冷却期内直接返回 False。
    _负缓存: dict = {}          # (host, port) -> 失败时间戳
    _负缓存锁 = threading.Lock()
    _负缓存秒 = 10.0   # 冷却10秒（原30秒过长，网络波动后恢复慢）

    def __init__(self, host: str, port: int = 5555, key_path: str = None,
                 log_callback=None):
        self.host = host
        self.port = port
        self.key_path = key_path
        self.log_callback = log_callback  # 可能为 None，用 _log 安全调用
        self.最后错误 = ''   # 最近一次连接失败的原因，供上层打印诊断
        # 实例级主连接及其锁（短操作共享，加锁串行，避免多次授权弹窗）
        self._主连接: Optional[AdbConnection] = None
        # ★ 必须是 RLock：本类历史上出现过「执行shell 持有锁后调用的
        # _获取主连接 内部再次加同一把锁」的结构，非重入 Lock 会让同一线程
        # 自死锁，表现为「连接成功但所有 shell 命令永久卡死」。
        # 现约定 _获取主连接 由调用者持锁（内部不再加锁），
        # 但仍用 RLock 对嵌套加锁免疫，杜绝此类回归。
        self._主连接锁 = threading.RLock()
        # 兼容旧代码：保留 _conn 引用（指向最近使用的连接），但不作为唯一连接
        self._conn: Optional[AdbConnection] = None

    def _日志(self, msg: str):
        """安全调用日志回调（log_callback 可能为 None）。"""
        if self.log_callback:
            try:
                self.log_callback(msg)
            except Exception:
                pass

    # ── 连接管理 ──

    def 连接(self, timeout: float = 5.0) -> bool:
        """建立首次连接（触发认证），预热池。已有连接时静默复用。"""
        # 快速路径：已有主连接，直接复用
        with self._主连接锁:
            if self._主连接 and self._主连接.state == STATE_DEVICE:
                self._conn = self._主连接
                self._日志(f'[自研adb] 复用主连接 {self.host}:{self.port}')
                return True

        # 设备级首次认证锁：同一设备只有一个线程做首次认证，其他等待
        key = (self.host, self.port)
        # 负缓存检查：冷却期内直接失败，避免高频重试风暴
        with self._负缓存锁:
            失败于 = self._负缓存.get(key)
        if 失败于 is not None:
            已过 = time.time() - 失败于
            if 已过 < self._负缓存秒:
                self.最后错误 = f'{int(已过)}秒前刚失败，冷却期内（剩余{int(self._负缓存秒 - 已过)}秒）'
                self._日志(f'[自研adb] 跳过连接（{int(已过)}秒前刚失败，'
                          f'{int(self._负缓存秒 - 已过)}秒冷却期内）: {self.host}:{self.port}')
                return False
        with self._认证锁字典锁:
            if key not in self._认证锁字典:
                self._认证锁字典[key] = threading.Lock()
            dev_lock = self._认证锁字典[key]

        with dev_lock:
            tid = threading.get_ident()
            # 拿到锁后再检查一次（可能别的线程已经认证完了）
            with self._主连接锁:
                if self._主连接 and self._主连接.state == STATE_DEVICE:
                    self._conn = self._主连接
                    self._日志(f'[自研adb][T{tid}] 复用主连接 {self.host}:{self.port}')
                    return True
            try:
                self._日志(f'[自研adb][T{tid}] 尝试连接 {self.host}:{self.port}...')
                conn = _池借用(self.host, self.port, timeout, self.key_path,
                                    log_callback=self.log_callback)
                # 探活确认（echo __ok__）
                try:
                    conn.执行shell('echo __ok__', timeout=3)
                except Exception:
                    pass
                self._conn = conn  # 缓存引用（仅兼容）
                # ★ 设为主连接（不归还到空闲池），短操作共享，避免多次授权
                with self._主连接锁:
                    self._主连接 = conn
                # ★ 从池的借出/线程绑定中剥离：主连接由本客户端独占管理，
                # 防止池再把它分发给同线程的 push/pull 等借用路径，
                # 造成两路并发读写同一条 socket（协议帧交错损坏）
                _池剥离(conn)
                with self._负缓存锁:
                    self._负缓存.pop(key, None)  # 成功后清除负缓存
                self._日志(f'[自研adb] 连接成功 {self.host}:{self.port}')
                return True
            except Exception as e:
                self.最后错误 = str(e)
                with self._负缓存锁:
                    self._负缓存[key] = time.time()
                self._日志(f'[自研adb] 连接失败: {e}')
                return False

    def 自动重连(self, timeout: float = 15.0) -> bool:
        """root 重启 adbd 后调用：清池 + 重建。"""
        _池关闭设备(self.host, self.port)
        with self._负缓存锁:
            self._负缓存.pop((self.host, self.port), None)  # 重连是显式操作，清除冷却
        with self._主连接锁:
            old = self._主连接
            self._主连接 = None
            self._conn = None
        if old is not None:
            try:
                old.关闭()
            except Exception:
                pass
        time.sleep(2)  # 等待 adbd 重启
        return self.连接(timeout)

    def 关闭(self):
        """关闭该设备的所有连接（含已剥离的主连接）。"""
        _池关闭设备(self.host, self.port)
        with self._主连接锁:
            old = self._主连接
            self._主连接 = None
            self._conn = None
        if old is not None:
            try:
                old.关闭()
            except Exception:
                pass

    @property
    def state(self) -> int:
        """兼容旧代码，返回当前是否有可用连接。"""
        if self._conn and self._conn.state == STATE_DEVICE:
            return STATE_DEVICE
        return 0

    # ── 核心操作（走连接池：借用 → 使用 → 归还/关闭）──

    def _获取主连接(self, timeout: float = 30.0) -> AdbConnection:
        """获取或创建主连接（调用者必须已持有_主连接锁）。"""
        # 主连接还能用 → 直接返回（不做逐次探活：每条命令前都 echo 探活
        # 会让开流数量翻倍，部分设备 adbd 高频开流时会瞬时拒绝 OPEN）。
        # 连接真坏了由 执行shell 的异常路径探活并重建。
        if self._主连接 and self._主连接.state == STATE_DEVICE:
            return self._主连接
        # 创建新主连接
        conn = _池借用(self.host, self.port, timeout, self.key_path)
        self._主连接 = conn
        self._conn = conn
        return conn

    def _用连接(self, func, timeout=30.0):
        """通用连接借用模式：成功则归还，失败则探活后决定归还或关闭。"""
        conn = _池借用(self.host, self.port, timeout, self.key_path)
        成功 = False
        try:
            result = func(conn)
            成功 = True
            return result
        except Exception:
            # 执行失败后探活：连接还能用就归还（可能只是命令本身失败），连接损坏才关闭
            try:
                old = conn.sock.gettimeout()
                conn.sock.settimeout(2.0)
                try:
                    conn.执行shell('echo __alive__', timeout=2)
                    # 探活成功，连接还能用，归还
                    _归还后(conn)
                except Exception:
                    # 探活失败，连接已损坏，关闭
                    try:
                        conn.关闭()
                    except Exception:
                        pass
                finally:
                    conn.sock.settimeout(old)
            except Exception:
                try:
                    conn.关闭()
                except Exception:
                    pass
            raise
        finally:
            if 成功:
                _归还后(conn)

    def 执行shell(self, command: str, timeout: float = 30.0) -> str:
        """短操作：使用主连接，加锁串行，避免多次授权弹窗。"""
        with self._主连接锁:
            conn = self._获取主连接(timeout)
            try:
                return conn.执行shell(command, timeout)
            except Exception:
                # 执行失败，探活后主连接还能用就保留，损坏就关闭
                try:
                    old = conn.sock.gettimeout()
                    conn.sock.settimeout(2.0)
                    try:
                        conn.执行shell('echo __alive__', timeout=2)
                        # 探活成功，保留主连接
                    except Exception:
                        # 探活失败，关闭主连接
                        try:
                            conn.关闭()
                        except Exception:
                            pass
                        self._主连接 = None
                    finally:
                        conn.sock.settimeout(old)
                except Exception:
                    try:
                        conn.关闭()
                    except Exception:
                        pass
                    self._主连接 = None
                raise

    def shell流(self, command: str, on_data, stop_event, open_timeout: float = 10.0, service: str = 'shell'):
        """在独立连接上运行流式 shell（如 logcat / tcpdump），供后台线程作为 target 使用。

        Parameters
        ----------
        service : str
            服务类型：'shell'（默认，会做换行符转换，适合文本）或
            'exec'（不做换行符转换，适合二进制输出如 tcpdump -w -）。

        用法（日志查看器页面）:
            threading.Thread(target=client.shell流,
                             args=('logcat -v threadtime', on_data, stop_evt))
        每收到一块数据回调 on_data(bytes)；stop_event 置位或设备关闭流时返回。
        使用独立连接（不占用主连接），结束后直接关闭、不归还池。
        """
        conn = _池借用(self.host, self.port, open_timeout, self.key_path)
        # 流式连接由本线程独占：从池剥离，防止被其他借用路径拿走
        _池剥离(conn)
        local_id = None
        try:
            local_id = conn.打开服务(f'{service}:{command}')
            # 短超时轮询：保证 stop_event 置位后最多 ~0.5s 内退出
            conn.sock.settimeout(0.5)
            # 打开服务期间设备已先发来的数据（预读缓冲）
            if conn._预读数据:
                self._安全回调(on_data, conn._预读数据)
                conn._预读数据 = b''
            while not stop_event.is_set():
                try:
                    msg = conn._接收消息()
                except socket.timeout:
                    continue
                except (RuntimeError, OSError):
                    break  # 连接断开
                if msg.command == CMD_WRTE:
                    if msg.payload:
                        self._安全回调(on_data, msg.payload)
                    try:
                        conn._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))
                    except Exception:
                        break
                elif msg.command == CMD_CLSE:
                    try:
                        conn._发送(AdbMessage(CMD_CLSE, local_id, msg.arg0))
                    except Exception:
                        pass
                    break
                # OKAY 等其他消息忽略
        except Exception as e:
            self._日志(f'[自研adb] shell流异常: {e}')
        finally:
            if local_id is not None:
                try:
                    conn._发送(AdbMessage(CMD_CLSE, local_id, conn._remote_id))
                except Exception:
                    pass
            try:
                conn.关闭()
            except Exception:
                pass

    @staticmethod
    def _安全回调(on_data, data: bytes):
        try:
            on_data(data)
        except Exception:
            pass

    def 推送文件(self, local_path: str, remote_path: str, timeout: float = 120.0,
                progress_cb: Callable = None) -> bool:
        return self._用连接(lambda c: c.推送文件(local_path, remote_path, timeout, progress_cb), timeout)

    def 拉取文件(self, remote_path: str, local_path: str, timeout: float = 120.0) -> bool:
        return self._用连接(lambda c: c.拉取文件(remote_path, local_path, timeout), timeout)

    def 安装应用(self, apk_path: str, timeout: float = 300.0, extra_args: list = None) -> str:
        return self._用连接(lambda c: c.安装应用(apk_path, timeout, extra_args), timeout)

    def 获取root(self) -> bool:
        """获取 root（会重启 adbd，之后必须调用 自动重连）。"""
        return self._用连接(lambda c: c.获取root(), 10.0)

    def 获取版本(self) -> int:
        return self._用连接(lambda c: c.获取版本(), 10.0)

    def 获取设备列表(self) -> list:
        return self._用连接(lambda c: c.获取设备列表(), 10.0)

    def 端口转发(self, local_port: int, remote: str) -> bool:
        return self._用连接(lambda c: c.端口转发(local_port, remote), 10.0)

    def 取消端口转发(self, local_port: int) -> bool:
        return self._用连接(lambda c: c.取消端口转发(local_port), 10.0)

    def 反向转发(self, remote, local_port: int) -> bool:
        # remote 支持 int（tcp 端口）或字符串（如 localabstract:scrcpy_xxx）
        return self._用连接(lambda c: c.反向转发(remote, local_port), 10.0)

    def 取消反向转发(self, remote) -> bool:
        return self._用连接(lambda c: c.取消反向转发(remote), 10.0)

    def 列出转发(self) -> list:
        return self._用连接(lambda c: c.列出转发(), 10.0)

    def 打开隧道socket(self, remote: str) -> '_AdbStreamSocket':
        """直连 adbd 打开一条到 remote（如 localabstract:scrcpy_xxx）的隧道流。

        返回 socket 风格对象（recv/sendall/settimeout/close），供投屏等
        需要裸 TCP 语义的场景使用，等价于官方 adb forward 后的本地连接。
        连接从池剥离独占，关闭 socket 时一并关闭。
        """
        conn = _池借用(self.host, self.port, 10.0, self.key_path)
        try:
            local_id = conn.打开服务(remote)
        except Exception:
            # 典型场景：server 端 listener 未就绪（JVM 初始化需数秒），
            # 连接本身健康 → 归还池复用，避免轮询重试每次都重新认证；
            # 连接真坏了由后续借用的探活/异常路径处理
            _归还后(conn)
            raise
        _池剥离(conn)  # 服务已打开，隧道独占，不归还
        return _AdbStreamSocket(conn, local_id)

    # ── 长连接操作（不自动归还，调用方负责关闭）──

    def 打开服务(self, service: str) -> int:
        """打开一个长连接服务（如 logcat 流）。

        返回的 local_id 绑定到当前借用的连接，调用方必须在同一线程使用，
        结束后调用 关闭服务 归还连接。
        """
        conn = _池借用(self.host, self.port, 30.0, self.key_path)
        self._conn = conn  # 缓存，供 关闭服务 使用
        return conn.打开服务(service)

    def 关闭服务(self, local_id: int):
        """关闭 打开服务 得到的长连接。"""
        if self._conn is not None:
            try:
                self._conn._发送(AdbMessage(CMD_CLSE, local_id, self._conn._remote_id))
            except Exception:
                pass
            _归还后(self._conn)
            self._conn = None

    def _接收消息(self):
        """供 logcat 流读取使用（需在 打开服务 之后、同一线程）。"""
        if not self._conn:
            raise RuntimeError("未打开服务，请先调用 打开服务()")
        return self._conn._接收消息()

    def _发送okay(self, msg):
        if self._conn is not None:
            self._conn._发送(AdbMessage(CMD_OKAY, self._conn._local_id, msg.arg0))

    # ── 类方法 ──

    @classmethod
    def 扫描设备(cls, timeout: float = 0.5, 网段: str = None):
        """局域网扫描。"""
        from 工具.自研adb.adb协议 import 扫描局域网设备
        return 扫描局域网设备(timeout=timeout, 网段=网段)

    @classmethod
    def 清理空闲连接(cls):
        """清理池中空闲超时的连接（可定时调用）。"""
        _池清理空闲()

    @classmethod
    def 关闭全部连接(cls):
        """关闭所有设备的连接（应用退出时）。"""
        _池关闭全部()


# ── 后台定时清理空闲连接 ──
import threading as _threading

def _后台清理():
    """每 60 秒清理空闲超时的连接。"""
    while True:
        try:
            _threading.Event().wait(60)
            自研adb客户端.清理空闲连接()
        except Exception:
            pass

_threading.Thread(target=_后台清理, daemon=True).start()

class 交互式Shell:
    """交互式 shell 会话（双向输入输出）。

    打开 shell: 空服务，设备端启动 PTY。后台线程读取设备输出，
    调用方通过 发送输入() 向设备发送键入字符。

    支持两种连接源:
      - 自研adb客户端（TCP）：从连接池借用独占连接，关闭时释放
      - AdbConnection / UsbAdbConnection（直连，含 USB）：使用已有连接，
        关闭时只关 shell 流，不关底层连接（USB 连接是共享缓存的）

    控制字符:
        Ctrl+C = b'\\x03'
        Ctrl+D = b'\\x04'
        退格   = b'\\x7f'
        Tab    = b'\\t'
        回车   = b'\\n' 或 b'\\r'
    """

    def __init__(self, 连接源, on_output: Callable, on_close: Callable = None):
        """
        Parameters
        ----------
        连接源 : 自研adb客户端 或 AdbConnection / UsbAdbConnection
            TCP 模式传自研adb客户端（从池借连接）；USB/直连模式传已连接的 Connection。
        """
        self._连接源 = 连接源
        self._on_output = on_output
        self._on_close = on_close
        self._conn = None
        self._local_id = None
        self._remote_id = None
        self._共享连接 = False  # True=直连/USB，关闭时不关底层连接
        self._stop_event = threading.Event()
        self._read_thread = None
        self._send_lock = threading.Lock()
        self._closed = False
        # 用于安全回调（直连模式下没有 client，用静态方法）
        self._安全回调_fn = getattr(连接源, '_安全回调', None) or 自研adb客户端._安全回调

    def 启动(self, open_timeout: float = 10.0):
        """打开交互式 shell 会话，启动后台读取线程。"""
        if isinstance(self._连接源, 自研adb客户端):
            # TCP 模式：从池借用独占连接
            client = self._连接源
            self._conn = _池借用(client.host, client.port, open_timeout, client.key_path)
            _池剥离(self._conn)
            self._共享连接 = False
        else:
            # 直连模式（USB 等）：使用已有连接，不关闭底层
            self._conn = self._连接源
            self._共享连接 = True
        self._local_id = self._conn.打开服务('shell:')
        self._remote_id = self._conn._remote_id
        # 打开服务期间设备已先发来的数据（预读缓冲，如 shell 提示符）
        if self._conn._预读数据:
            self._安全回调_fn(self._on_output, self._conn._预读数据)
            self._conn._预读数据 = b''
        self._read_thread = threading.Thread(target=self._读取循环, daemon=True)
        self._read_thread.start()

    def 发送输入(self, data):
        """向 shell 发送输入（用户键入的字符/命令/控制字符）。

        Parameters
        ----------
        data : str or bytes
            字符串自动 UTF-8 编码；bytes 原样发送。
            发送命令需自行加换行符，如 shell.发送输入('ls\\n')。
        """
        if self._closed or not self._conn or self._local_id is None:
            return
        if isinstance(data, str):
            data = data.encode('utf-8')
        if not data:
            return
        try:
            with self._send_lock:
                self._conn._发送(AdbMessage(CMD_WRTE, self._local_id,
                                             self._remote_id, data))
        except Exception:
            pass  # 连接已断开，关闭流程会处理

    def 关闭(self):
        """关闭 shell 会话（发送 CLSE，停止读取线程）。

        TCP 模式：关闭底层 socket；USB/直连模式：只关 shell 流，不关底层连接。
        """
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        with self._send_lock:
            if self._conn and self._local_id is not None:
                try:
                    self._conn._发送(AdbMessage(CMD_CLSE, self._local_id,
                                                 self._remote_id))
                except Exception:
                    pass
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=2.0)
        self._关闭底层连接()
        if self._on_close:
            self._安全回调_fn(self._on_close, None)

    def _关闭底层连接(self):
        """关闭底层连接（仅非共享模式）。"""
        if self._共享连接:
            return  # USB/直连：底层连接是共享的，不关
        try:
            if self._conn and self._conn.sock:
                self._conn.sock.close()
        except Exception:
            pass

    def _设置读取超时(self, timeout秒: float):
        """设置读取超时（TCP 用 sock.settimeout，USB 用 _usb.timeout）。"""
        try:
            if self._conn.sock is not None:
                self._conn.sock.settimeout(timeout秒)
            elif hasattr(self._conn, '_usb') and self._conn._usb is not None:
                self._conn._usb.timeout = int(timeout秒 * 1000)
        except Exception:
            pass

    def _是超时异常(self, e) -> bool:
        """判断异常是否为读取超时（TCP: socket.timeout，USB: 各种超时异常）。"""
        if isinstance(e, socket.timeout):
            return True
        # USB 超时异常类型不固定，通过异常消息判断
        msg = str(e).lower()
        if 'timeout' in msg or 'timed out' in msg:
            return True
        return False

    @property
    def 已关闭(self) -> bool:
        return self._closed

    def _读取循环(self):
        """后台线程：持续读取设备输出。"""
        self._设置读取超时(0.5)
        while not self._stop_event.is_set():
            try:
                msg = self._conn._接收消息()
            except Exception as e:
                if self._是超时异常(e):
                    continue
                break  # 连接断开
            if msg.command == CMD_WRTE:
                if msg.payload:
                    self._安全回调_fn(self._on_output, msg.payload)
                try:
                    with self._send_lock:
                        self._conn._发送(AdbMessage(CMD_OKAY, self._local_id,
                                                     msg.arg0))
                except Exception:
                    break
            elif msg.command == CMD_CLSE:
                try:
                    with self._send_lock:
                        self._conn._发送(AdbMessage(CMD_CLSE, self._local_id,
                                                     msg.arg0))
                except Exception:
                    pass
                break
            # OKAY 等其他消息忽略
        # 会话结束（设备主动关闭 / 连接断开），通知调用方
        if not self._closed:
            self._closed = True
            self._关闭底层连接()
            if self._on_close:
                self._安全回调_fn(self._on_close, None)


# ── 后台定时清理空闲连接 ──
import threading as _threading

def _后台清理():
    """每 60 秒清理空闲超时的连接。"""
    while True:
        try:
            _threading.Event().wait(60)
            自研adb客户端.清理空闲连接()
        except Exception:
            pass

_threading.Thread(target=_后台清理, daemon=True).start()
