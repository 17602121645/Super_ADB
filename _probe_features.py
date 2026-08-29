# -*- coding: utf-8 -*-
"""探测设备 adbd 的 feature 列表（临时脚本）"""
import socket, struct, time

HOST = '192.168.1.3'
PORT = 5555

def pack_msg(cmd, arg0, arg1, payload=b''):
    return struct.pack('<IIIIII', cmd, arg0, arg1, len(payload), 0, cmd ^ 0xffffffff) + payload

def recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        c = sock.recv(n - len(buf))
        if not c:
            raise ConnectionError('断开')
        buf += c
    return buf

def recv_msg(sock):
    h = recv_exact(sock, 24)
    cmd, a0, a1, length, crc, magic = struct.unpack('<IIIIII', h)
    payload = recv_exact(sock, length) if length else b''
    return cmd, a0, a1, payload

CMD_CNXN = 0x4e584e43
ADB_MAX_PAYLOAD = 1024 * 1024

sock = socket.create_connection((HOST, PORT), timeout=8)
sock.settimeout(8)
# 发 CNXN（不含 delayed_ack，先看设备自己声明什么）
banner = b'host::features=shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,abb_exec,fixed_push_symlink_timestamp,app_process_install_32bit_override,hires_shell_v2,remount_shell,track_app,sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,list_v2'
sock.sendall(pack_msg(CMD_CNXN, 0x01000001, ADB_MAX_PAYLOAD, banner))
# 接收设备响应（可能先 AUTH 再 CNXN）
for _ in range(6):
    cmd, a0, a1, payload = recv_msg(sock)
    name = {0x4e584e43: 'CNXN', 0x41555448: 'AUTH', 0x534c5453: 'STLS'}.get(cmd, hex(cmd))
    print(f'收到 {name} arg0={a0:#x} arg1={a1:#x} len={len(payload)}')
    if cmd == CMD_CNXN:
        txt = payload.decode('utf-8', errors='replace')
        print('设备 banner:', txt)
        feats = set(txt.split('features=')[-1].split(','))
        print('包含 delayed_ack:', 'delayed_ack' in feats)
        print('所有 feature:', sorted(feats))
        break
    if cmd == 0x41555448:
        # 无密钥，仅探测用：回复空签名会失败，但我们只想要 CNXN banner
        # 直接结束（探测目的达到与否取决于设备是否先发 CNXN）
        print('设备要求认证（AUTH），需要密钥。尝试用项目密钥...')
        break
sock.close()
print('[探测完成]')
