# -*- coding: utf-8 -*-
"""实施 delayed_ack（Burst Mode）协议级改造：adb协议.py + 自研adb客户端.py
保持 UTF-8 BOM + CRLF 编码。每个替换带计数断言，防止静默失败。
"""
import io

WIN = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\工具\自研adb\adb协议.py'
CLI = r'G:\Python\jcspy\Super_ADB\Super_ADB_Win\工具\自研adb\自研adb客户端.py'


def read(path):
    with io.open(path, 'r', encoding='utf-8-sig', newline='') as f:
        return f.read()


def write(path, text):
    # 保持 UTF-8 BOM + CRLF
    with io.open(path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(text)


def apply(path, subs):
    text = read(path)
    for old, new, exp in subs:
        cnt = text.count(old)
        if cnt != exp:
            raise SystemExit(
                f'[失败] {path}\n  期望 {exp} 次，实际 {cnt} 次:\n  {old[:80]!r}')
        text = text.replace(old, new)
        print(f'  ok x{cnt}: {old[:60]!r} -> {new[:40]!r}')
    write(path, text)
    print(f'[完成] {path}')


print('==== 修改 adb协议.py ====')
apply(WIN, [
    # R1 常量
    ("ADB_MAX_PAYLOAD = 1048576  # 1MB",
     "ADB_MAX_PAYLOAD = 1048576  # 1MB\r\n"
     "INITIAL_DELAYED_ACK_BYTES = 32 * 1024 * 1024  # delayed_ack 初始发送窗口(32MB, 对齐官方 adb.h)",
     1),
    # R2 __init__ 加状态字段
    ("        self._max_payload = ADB_MAX_PAYLOAD\r\n"
     "        self._认证失败原因 = ''   # 认证未通过时的具体原因，供上层错误消息展示",
     "        self._max_payload = ADB_MAX_PAYLOAD\r\n"
     "        self._delayed_ack = False   # 连接级：双方协商 delayed_ack 成功才 True\r\n"
     "        self._流ASB = 0             # 当前流可用发送额度（delayed_ack 窗口记账）\r\n"
     "        self._认证失败原因 = ''   # 认证未通过时的具体原因，供上层错误消息展示",
     1),
    # R3 新增辅助方法（插在 _协商载荷 之后）
    ("    def _协商载荷(self, device_max: int) -> int:\r\n"
     "        if 256 <= device_max <= 1024 * 1024:\r\n"
     "            return device_max\r\n"
     "        return ADB_MAX_PAYLOAD",
     "    def _协商载荷(self, device_max: int) -> int:\r\n"
     "        if 256 <= device_max <= 1024 * 1024:\r\n"
     "            return device_max\r\n"
     "        return ADB_MAX_PAYLOAD\r\n"
     "\r\n"
     "    def _解析设备features(self, banner: bytes) -> None:\r\n"
     "        \"\"\"从设备 CNXN banner 解析 features，决定是否启用 delayed_ack。\r\n"
     "\r\n"
     "        delayed_ack 是连接级协商：客户端 banner 声明 + 设备 banner 声明，双方\r\n"
     "        都支持才启用（对应官方 CanUseFeature(\"delayed_ack\")）。启用后该连接\r\n"
     "        所有流的 OKAY 都必须带 4 字节 int32 增量确认（见 _回OKAY）。\r\n"
     "        \"\"\"\r\n"
     "        self._delayed_ack = False\r\n"
     "        try:\r\n"
     "            txt = banner.decode('utf-8', errors='replace')\r\n"
     "            feats = txt.split('features=')[-1].split(',')\r\n"
     "            if 'delayed_ack' in feats:\r\n"
     "                self._delayed_ack = True\r\n"
     "                print('[自研adb] 设备支持 delayed_ack（Burst Mode），传输已启用')\r\n"
     "        except Exception:\r\n"
     "            self._delayed_ack = False\r\n"
     "\r\n"
     "    def _发OPEN(self, local_id: int, service: str) -> None:\r\n"
     "        \"\"\"发 OPEN 报文。delayed_ack 下 arg1 = INITIAL_DELAYED_ACK_BYTES 宣告启用。\r\n"
     "\r\n"
     "        官方语义（sockets.cpp connect_to_remote）：arg1 非零表示客户端想用\r\n"
     "        delayed_ack；设备端若不支持（或客户端没声明而设备支持）会回 CLSE 拒绝。\r\n"
     "        因此 arg1 必须与 _delayed_ack 严格一致。\r\n"
     "        \"\"\"\r\n"
     "        arg1 = INITIAL_DELAYED_ACK_BYTES if self._delayed_ack else 0\r\n"
     "        self._发送(AdbMessage(CMD_OPEN, local_id, arg1, service.encode() + b'\\0'))\r\n"
     "\r\n"
     "    def _回OKAY(self, local_id: int, remote_id: int, 确认字节: int = 0) -> None:\r\n"
     "        \"\"\"回 OKAY 报文。delayed_ack 下带 4 字节 int32 增量确认，否则空 payload。\r\n"
     "\r\n"
     "        官方语义（adb.cpp send_ready + local_socket_ack）：OKAY 的 payload 是\r\n"
     "        「自上次 OKAY 以来实际冲刷到 fd 的字节数」（增量，可负）。接收方收到\r\n"
     "        带 payload 的 OKAY 后 ASB += 该值恢复发送。注意：delayed_ack 协商成功\r\n"
     "        后所有 OKAY 都必须带 payload，否则对端 available_send_bytes 不匹配\r\n"
     "        会直接丢弃该 OKAY → 死锁。\r\n"
     "        \"\"\"\r\n"
     "        payload = struct.pack('<i', 确认字节) if self._delayed_ack else b''\r\n"
     "        self._发送(AdbMessage(CMD_OKAY, local_id, remote_id, payload))\r\n"
     "\r\n"
     "    def _解析OKAY字节(self, msg) -> int:\r\n"
     "        \"\"\"从 OKAY 消息解析 acked_bytes（增量确认）。无 payload 或非 4 字节返回 0。\"\"\"\r\n"
     "        if self._delayed_ack and len(msg.payload) == 4:\r\n"
     "            try:\r\n"
     "                return struct.unpack('<i', msg.payload)[0]\r\n"
     "            except Exception:\r\n"
     "                return 0\r\n"
     "        return 0\r\n"
     "\r\n"
     "    def _发送流内(self, local_id: int, payload: bytes, 场景: str = '推送') -> None:\r\n"
     "        \"\"\"发送一个 WRTE（传输层流控）。\r\n"
     "\r\n"
     "        delayed_ack：窗口化发送——ASB 充足才发，不足则收 OKAY 补充额度再发；\r\n"
     "        期间带外 WRTE（sync 的 FAIL 响应等）就地回 OKAY 并处理。传统模式：\r\n"
     "        发后等一个 OKAY（window=1），行为与旧代码一致。\r\n"
     "        \"\"\"\r\n"
     "        if self._delayed_ack:\r\n"
     "            while self._流ASB < len(payload):\r\n"
     "                msg = self._接收消息()\r\n"
     "                if msg.command == CMD_OKAY:\r\n"
     "                    if msg.arg1 != local_id:\r\n"
     "                        continue  # 旧流残留\r\n"
     "                    self._流ASB += self._解析OKAY字节(msg)\r\n"
     "                elif msg.command == CMD_WRTE:\r\n"
     "                    if msg.arg1 != local_id:\r\n"
     "                        # 旧流残留数据：回 OKAY 维持流控，丢弃\r\n"
     "                        self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))\r\n"
     "                        continue\r\n"
     "                    if msg.payload[:4] == b'FAIL':\r\n"
     "                        err_len = struct.unpack('<I', msg.payload[4:8])[0]\r\n"
     "                        err = msg.payload[8:8 + err_len].decode('utf-8', errors='replace')\r\n"
     "                        raise RuntimeError(f\"{场景}失败: {err}\")\r\n"
     "                    # 其它带外数据：回 ack 后继续等额度\r\n"
     "                    self._回OKAY(local_id, msg.arg0, len(msg.payload))\r\n"
     "                elif msg.command == CMD_CLSE:\r\n"
     "                    raise RuntimeError(f\"设备在{场景}过程中关闭连接\")\r\n"
     "                else:\r\n"
     "                    raise RuntimeError(f\"{场景}失败，收到 {msg.命令名}\")\r\n"
     "            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, payload))\r\n"
     "            self._流ASB -= len(payload)\r\n"
     "        else:\r\n"
     "            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, payload))\r\n"
     "            self._等待流OKAY(local_id, 场景)",
     1),
    # R4 banner 加 delayed_ack feature
    ("...sendrecv_v2_zstd,list_v2'",
     "...sendrecv_v2_zstd,list_v2,delayed_ack'",
     1),
    # R5 连接() 解析设备 features
    ("        if msg.command == CMD_CNXN:\r\n"
     "            self._max_payload = self._协商载荷(msg.arg1)\r\n"
     "            self.state = STATE_DEVICE\r\n"
     "            return True",
     "        if msg.command == CMD_CNXN:\r\n"
     "            self._max_payload = self._协商载荷(msg.arg1)\r\n"
     "            self._解析设备features(msg.payload)\r\n"
     "            self.state = STATE_DEVICE\r\n"
     "            return True",
     1),
    # R6 _处理认证 CNXN 解析 features
    ("                if msg.command == CMD_CNXN:\r\n"
     "                    self._max_payload = self._协商载荷(msg.arg1)\r\n"
     "                    self.state = STATE_DEVICE\r\n"
     "                    print(f'[自研adb][T{tid}] 认证成功'",
     "                if msg.command == CMD_CNXN:\r\n"
     "                    self._max_payload = self._协商载荷(msg.arg1)\r\n"
     "                    self._解析设备features(msg.payload)\r\n"
     "                    self.state = STATE_DEVICE\r\n"
     "                    print(f'[自研adb][T{tid}] 认证成功'",
     1),
    # R7 流对象._读循环 回 OKAY 带 payload
    ("                        conn._发送(AdbMessage(CMD_OKAY, self._local_id, conn._remote_id))",
     "                        conn._回OKAY(self._local_id, conn._remote_id, len(msg.payload))",
     1),
    # R8+R12 所有 OPEN 报文改 _发OPEN（含打开服务、_读取主机服务、转发系列）
    ("self._发送(AdbMessage(CMD_OPEN, local_id, 0, service.encode() + b'\\0'))",
     "self._发OPEN(local_id, service)",
     7),
    # R9 打开服务 收 OKAY 时初始化 _流ASB
    ("                self._remote_id = msg.arg0\r\n"
     "                return local_id",
     "                self._remote_id = msg.arg0\r\n"
     "                self._流ASB = self._解析OKAY字节(msg)\r\n"
     "                return local_id",
     1),
    # R10+R14+R15+R17 旧流残留 WRTE 回 OKAY（msg.arg1/msg.arg0 形式）
    ("self._发送(AdbMessage(CMD_OKAY, msg.arg1, msg.arg0))",
     "self._回OKAY(msg.arg1, msg.arg0, len(msg.payload))",
     3),
    # R11+R13+R18+R21 本流 WRTE 回 OKAY（local_id, msg.arg0 形式）覆盖 打开服务预读/获取root/等待流OKAY/最终应答
    ("self._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))",
     "self._回OKAY(local_id, msg.arg0, len(msg.payload))",
     4),
    # R16+R25 执行shell 本流 / 列出转发 本流 WRTE 回 OKAY
    ("                    output += msg.payload\r\n"
     "                    self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))",
     "                    output += msg.payload\r\n"
     "                    self._回OKAY(local_id, self._remote_id, len(msg.payload))",
     2),
    # R19 push SEND 命令走 _发送流内
    ("            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, send_cmd))\r\n"
     "            self._等待流OKAY(local_id, 'SEND')",
     "            self._发送流内(local_id, send_cmd, 'SEND')",
     1),
    # R20 push 数据帧走 _发送流内（窗口化）
    ("                    self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, payload))\r\n"
     "                    # 严格 window=1：一个 WRTE 等一个 OKAY，符合协议且不会误判\r\n"
     "                    self._等待流OKAY(local_id, '推送')",
     "                    self._发送流内(local_id, payload, '推送')",
     1),
    # R21 push DONE 走 _发送流内
    ("            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, done_cmd))",
     "            self._发送流内(local_id, done_cmd, 'DONE')",
     1),
    # R22 pull RECV 走 _发送流内，去掉强制等 OKAY
    ("            self._发送(AdbMessage(CMD_WRTE, local_id, self._remote_id, recv_cmd))\r\n"
     "            msg = self._接收消息()\r\n"
     "            if msg.command != CMD_OKAY:\r\n"
     "                raise RuntimeError(f\"RECV 失败，期望 OKAY，收到 {msg.命令名}\")",
     "            self._发送流内(local_id, recv_cmd, 'RECV')",
     1),
    # R23 pull 每 WRTE 回 OKAY 带 payload
    ("                        # 而不是每个 DATA 块一个——后者会多发 ack 打乱流控）\r\n"
     "                        self._发送(AdbMessage(CMD_OKAY, local_id, self._remote_id))",
     "                        # 而不是每个 DATA 块一个——后者会多发 ack 打乱流控）\r\n"
     "                        self._回OKAY(local_id, self._remote_id, len(msg.payload))",
     1),
])

print('==== 修改 自研adb客户端.py ====')
apply(CLI, [
    # shell流 收 WRTE 回 OKAY 带 payload
    ("                        conn._发送(AdbMessage(CMD_OKAY, local_id, msg.arg0))",
     "                        conn._回OKAY(local_id, msg.arg0, len(msg.payload))",
     1),
    # logcat _发送okay
    ("            self._conn._发送(AdbMessage(CMD_OKAY, self._conn._local_id, msg.arg0))",
     "            self._conn._回OKAY(self._conn._local_id, msg.arg0, len(msg.payload))",
     1),
    # 交互式Shell 收 WRTE 回 OKAY 带 payload（多行调用）
    ("                    with self._send_lock:\r\n"
     "                        self._conn._发送(AdbMessage(CMD_OKAY, self._local_id,\r\n"
     "                                                     msg.arg0))",
     "                    with self._send_lock:\r\n"
     "                        self._conn._回OKAY(self._local_id, msg.arg0, len(msg.payload))",
     1),
])

print('[全部替换完成]')
