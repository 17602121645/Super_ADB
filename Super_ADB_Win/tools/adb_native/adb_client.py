# -*- coding: utf-8 -*-
"""
自研 ADB 客户端（连接池化 —— 最终版）
======================================
薄包装层：对 AdbConnection 的每次操作从全局连接池借用独立连接，用完归还，
实现:
  - 多线程并发安全（每线程/操作独占一个物理连接）
  - 认证复用（首次认证后，池内连接不再重复弹窗）
  - 并发首次建连去重（由 adb_protocol._连接池 的 _建连事件 保证）
  - root 重启 adbd 后清池重建

连接池实现位于 adb协议 模块（借用连接/归还连接/关闭设备连接 等），
本模块只做薄封装，避免双池冲突。

adb_tools.py 通过 `from tools.adb_native import 自研adb客户端` 使用，接口保持稳定。
"""

import os
import socket
import time
import threading
from typing import Optional, Callable

from tools.adb_native.adb_protocol import (
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
        # ★ 单客户端设备标记：CM211 等 IPTV 盒子 adbd 只接受 1 条 TCP 连接，
        # 借第二条连接 CNXN 永远超时。确认后跳过注定超时的第二连接借用，
        # 直接主连接串行（push/pull 免白等建连超时）。
        self._单客户端设备 = False
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
                # 探活确认（echo __ok__）：连接必须能真正执行 shell 才算可用。
                # ★ 不能吞异常：曾出现「CNXN 握手成功但 shell 一开就被设备踢断」
                #   （单客户端机顶盒被 adb server 抢占槽位）的场景，吞掉后返回
                #   True 会误报连接成功，后续所有命令才报「连接断开」。
                try:
                    conn.执行shell('echo __ok__', timeout=3)
                except Exception as e:
                    # 探活失败：清掉该设备全部池连接（含借出/空闲/线程绑定），
                    # 避免坏连接残留被后续借用复用（本连接尚未 _池剥离）
                    try:
                        _池关闭设备(self.host, self.port)
                    except Exception:
                        pass
                    raise RuntimeError(
                        f'连接建立但探活失败（echo __ok__ 未返回，'
                        f'可能是设备被其他客户端占用或被踢断）: {e}') from e
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
                # ★ 单客户端机顶盒诊断：若官方 adb server 正占用该设备，
                # 把笼统的「timed out」改写成可操作提示（先 disconnect）
                self._诊断被adb占用(e)
                with self._负缓存锁:
                    self._负缓存[key] = time.time()
                self._日志(f'[自研adb] 连接失败: {self.最后错误}')
                return False

    # 触发诊断的连接型故障关键词：超时 / 被踢断 / 探活失败。
    # 授权类故障（未授权、需弹窗）不触发，避免无谓拉起子进程。
    _占用诊断触发 = ('timed out', 'timeout', '超时', '连接断开', '探活失败')
    # ★ 单客户端设备（如 CM211 等 IPTV 机顶盒）第二条物理连接 CNXN 永远超时。
    # 借第二连接用短探测超时，避免每次 push/pull 都白等完整操作超时(默认120s)
    # 才触发降级；该值也应覆盖正常设备 LAN 建连握手（通常 <1s）。
    _单客户端建连超时秒 = 5.0

    def _诊断被adb占用(self, err: Exception) -> None:
        """连接失败时诊断：若官方 adb server 正占用该设备（单客户端机顶盒
        adbd 只允许 1 个 TCP 客户端），改写 最后错误 为可操作提示。

        通过 `adb devices` 查官方 adb server 的设备表：若目标 host:port 已在
        其中（device/offline/unauthorized 任一状态），说明槽位被官方通道占用，
        自研直连必然超时——提示用户先 `adb disconnect` 再重试。
        """
        msg = str(err).lower()
        if not any(k in msg for k in self._占用诊断触发):
            return
        target = f'{self.host}:{self.port}'
        try:
            import shutil
            import subprocess
            adb = shutil.which('adb') or 'adb'
            # CREATE_NO_WINDOW=0x08000000 仅 Windows 有效（避免诊断时闪黑框）；
            # mac/linux 的 subprocess 对非 0 的 creationflags 会抛 ValueError，
            # 故仅 Windows 附带该参数。
            run_kwargs = dict(capture_output=True, text=True, timeout=3)
            if os.name == 'nt':
                run_kwargs['creationflags'] = 0x08000000
            r = subprocess.run([adb, 'devices'], **run_kwargs)
            for line in r.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == target:
                    self.最后错误 = (
                        f'设备 {target} 正被官方 adb server 占用'
                        f'（adb devices 状态: {parts[1]}）。该机顶盒 adbd 仅支持'
                        f'单客户端连接，请先执行 adb disconnect {target} 后重试')
                    self._日志(f'[自研adb] 诊断: {self.最后错误}')
                    return
        except Exception:
            pass  # 诊断失败不掩盖原始错误

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

    def _主连接可用(self) -> bool:
        """主连接是否处于可用状态（持锁检查）。"""
        with self._主连接锁:
            return bool(self._主连接 and self._主连接.state == STATE_DEVICE)

    @staticmethod
    def _是建连超时(e: Exception) -> bool:
        """异常是否为建连/借连超时（区别于命令执行失败）。"""
        s = str(e).lower()
        return ('timed out' in s) or ('timeout' in s) or ('超时' in s)

    def _用连接(self, func, timeout=30.0, burst=None):
        """通用连接借用模式：成功则归还，失败则探活后决定归还或关闭。

        ★ 单客户端设备降级：部分设备 adbd（如 CM211 等 IPTV 机顶盒）只接受
        1 条 TCP 连接——主连接占用槽位时，借用第二条物理连接会在 CNXN 阶段
        超时。此时若主连接可用，改走「主连接串行执行」（与 执行shell 同款
        加锁），保证 push/pull/安装等长操作在这类设备上仍可用。
        """
        有主 = self._主连接可用()
        # 已确认单客户端：不再尝试注定超时的第二连接，直接走主连接串行
        if self._单客户端设备 and 有主:
            self._日志(f'[自研adb] 单客户端设备已确认，直接走主连接串行执行: '
                      f'{self.host}:{self.port}')
            return self._主连接串行(func, timeout)
        try:
            # 有主连接时，借第二连接用短探测超时：单客户端盒子第二条连接
            # CNXN 永远超时，用全量 timeout 会把降级拖到操作超时(默认120s)
            # 才触发，体验上等同卡死。
            借超时 = min(timeout, self._单客户端建连超时秒) if 有主 else timeout
            conn = _池借用(self.host, self.port, 借超时, self.key_path, burst=burst)
        except Exception as e:
            if 有主 and self._是建连超时(e):
                self._单客户端设备 = True
                self._日志(f'[自研adb] 借用第二连接超时（疑似单客户端设备），'
                          f'降级走主连接串行执行: {self.host}:{self.port}')
                return self._主连接串行(func, timeout)
            raise
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

    def _主连接串行(self, func, timeout):
        """在主连接上串行执行 func（持锁；失败后探活决定保留或关闭主连接）。

        与 执行shell 同款异常路径：命令本身失败但连接健康 → 保留主连接；
        连接损坏 → 关闭并置空，下次重新建立。
        """
        with self._主连接锁:
            conn = self._获取主连接(timeout)
            try:
                return func(conn)
            except Exception:
                try:
                    old = conn.sock.gettimeout()
                    conn.sock.settimeout(2.0)
                    try:
                        conn.执行shell('echo __alive__', timeout=2)
                    except Exception:
                        try:
                            conn.关闭()
                        except Exception:
                            pass
                        self._主连接 = None
                    finally:
                        try:
                            conn.sock.settimeout(old)
                        except Exception:
                            pass
                except Exception:
                    try:
                        conn.关闭()
                    except Exception:
                        pass
                    self._主连接 = None
                raise

    def 借用流连接(self, timeout: float = 10.0):
        """为长连接流（交互式 shell / logcat / tcpdump）借用连接。

        优先借独立连接（不占用主连接）；单客户端盒子第二连接 CNXN 超时时，
        降级复用主连接并持有 _主连接锁，调用方用完必须释放锁。
        已确认单客户端（_单客户端设备=True）时直接走主连接，不再试第二连接。

        Returns
        -------
        (conn, 需关闭底层, 锁对象)
            conn : AdbConnection — 可直接使用的连接
            需关闭底层 : bool — True=独立连接，用完必须 conn.关闭()；
                                False=主连接，禁止关闭底层
            锁对象 : threading.Lock | None — 非 None 时调用方必须 release()
        """
        有主 = (self._主连接 is not None and self._主连接.state == STATE_DEVICE)
        if self._单客户端设备 and 有主:
            self._主连接锁.acquire()
            return self._主连接, False, self._主连接锁
        借超时 = min(timeout, self._单客户端建连超时秒) if 有主 else timeout
        try:
            conn = _池借用(self.host, self.port, 借超时, self.key_path)
            _池剥离(conn)
            return conn, True, None
        except Exception as e:
            if 有主 and self._是建连超时(e):
                self._单客户端设备 = True
                self._主连接锁.acquire()
                return self._主连接, False, self._主连接锁
            raise

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
        # 优先借独立连接；单客户端盒子降级复用主连接（持锁，finally 释放）
        conn, 需关闭底层, 流锁 = self.借用流连接(open_timeout)
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
                        conn._回OKAY(local_id, msg.arg0, len(msg.payload))
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
            if 需关闭底层:
                try:
                    conn.关闭()
                except Exception:
                    pass
            if 流锁 is not None:
                try:
                    流锁.release()
                except Exception:
                    pass

    @staticmethod
    def _安全回调(on_data, data: bytes):
        try:
            on_data(data)
        except Exception:
            pass

    def 交互式shell(self, on_output: Callable, on_close: Callable = None,
                    open_timeout: float = 10.0) -> '交互式Shell':
        """创建交互式 shell 会话（类似 adb shell 不带参数进入终端，可双向输入输出）。

        打开 shell: 空服务，设备端起 PTY。后台线程持续读取输出，
        通过 on_output(bytes) 回调通知；调用方通过 返回对象.发送输入(data)
        向设备发送键入的字符/命令。

        Parameters
        ----------
        on_output : Callable[[bytes], None]
            设备有输出时回调，参数为原始字节（含 \\n 换行，可能含 ANSI 转义序列）。
        on_close : Callable[[], None], optional
            会话结束时回调（设备主动关闭 / 调用方关闭 / 连接断开）。
        open_timeout : float
            打开服务超时秒数。

        Returns
        -------
        交互式Shell
            会话对象，可调用 发送输入() / 关闭()。

        用法:
            shell = client.交互式shell(on_output=lambda d: print(d.decode(errors='replace')))
            shell.发送输入('ls\\n')
            shell.发送输入('\\x03')  # Ctrl+C
            shell.关闭()
        """
        shell = 交互式Shell(self, on_output, on_close)
        shell.启动(open_timeout)
        return shell

    def 推送文件(self, local_path: str, remote_path: str, timeout: float = 120.0,
                progress_cb: Callable = None) -> bool:
        return self._用连接(lambda c: c.推送文件(local_path, remote_path, timeout, progress_cb), timeout, burst=True)

    def 拉取文件(self, remote_path: str, local_path: str, timeout: float = 120.0) -> bool:
        return self._用连接(lambda c: c.拉取文件(remote_path, local_path, timeout), timeout, burst=False)

    def 安装应用(self, apk_path: str, timeout: float = 300.0, extra_args: list = None) -> str:
        return self._用连接(lambda c: c.安装应用(apk_path, timeout, extra_args), timeout)

    def 流式安装(self, apk_path: str, timeout: float = 300.0, extra_args: list = None,
                 progress_cb=None) -> str:
        """流式安装：APK 字节经 exec 流 stdin 直接交给 `cmd package install -S`。

        最快路径（不落 /data/local/tmp 临时文件），数据量大走 burst 连接。
        返回设备端输出（Success / Failure [...]）。
        """
        return self._用连接(
            lambda c: c.流式安装(apk_path, timeout, extra_args, progress_cb),
            timeout, burst=True)

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
            self._conn._回OKAY(self._conn._local_id, msg.arg0, len(msg.payload))

    # ── 类方法 ──

    @classmethod
    def 扫描设备(cls, timeout: float = 0.5, 网段: str = None):
        """局域网扫描。"""
        from tools.adb_native.adb_protocol import 扫描局域网设备
        return 扫描局域网设备(timeout=timeout, 网段=网段)

    @classmethod
    def 清理空闲连接(cls):
        """清理池中空闲超时的连接（可定时调用）。"""
        _池清理空闲()

    @classmethod
    def 关闭全部连接(cls):
        """关闭所有设备的连接（应用退出时）。"""
        _池关闭全部()


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
        self._流锁 = None  # 单客户端降级复用主连接时持有的锁，关闭时必须释放
        # 用于安全回调（直连模式下没有 client，用静态方法）
        self._安全回调_fn = getattr(连接源, '_安全回调', None) or 自研adb客户端._安全回调

    def 启动(self, open_timeout: float = 10.0):
        """打开交互式 shell 会话，启动后台读取线程。"""
        if isinstance(self._连接源, 自研adb客户端):
            # TCP 模式：优先借独立连接；单客户端盒子降级复用主连接（持锁）
            client = self._连接源
            self._conn, 需关闭, self._流锁 = client.借用流连接(open_timeout)
            self._共享连接 = not 需关闭
        else:
            # 直连模式（USB 等）：使用已有连接，不关闭底层
            self._conn = self._连接源
            self._共享连接 = True
            self._流锁 = None
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
        if self._流锁 is not None:
            try:
                self._流锁.release()
            except Exception:
                pass
            self._流锁 = None
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
                        self._conn._回OKAY(self._local_id, msg.arg0, len(msg.payload))
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
