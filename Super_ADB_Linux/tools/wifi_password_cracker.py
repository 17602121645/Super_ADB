#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wifi_password_cracker.py — WiFi 密码审计工具（多进程 + 多线程混合并行）
=================================================================

功能（子命令）：
  crack   : WPA/WPA2-PSK 的 PMKID 模式密码强度测试
            （multiprocessing.Pool 按 CPU 核数分进程 + 每进程内 threading 分片，
             核心 PBKDF2-HMAC-SHA1 由 OpenSSL 实现、调用时释放 GIL，双级并行有真实加速）
  recover : 找回本机 Windows 已保存的 WiFi 密码（netsh，无需管理员权限）
  gen     : 生成纯数字密码字典

【合法使用边界】
本工具仅允许用于：
  * 你拥有或获得书面授权的无线网络（自家路由器 / 公司测试网络）的安全强度自测；
  * 找回你自己设备上已保存的 WiFi 密码。
未经授权对他人网络进行密码测试，涉嫌违反《中华人民共和国网络安全法》第 27 条
与《刑法》第 285 条，后果由使用者自行承担。

依赖：仅 Python 3.6+ 标准库，无第三方包。
"""

import argparse
import csv
import hashlib
import hmac
import re
import subprocess
import sys
import threading
import time
from multiprocessing import Pool, cpu_count, freeze_support

VERSION = '1.0.0'

# ──────────────────────────────── 核心算法 ────────────────────────────────


def mac_to_bytes(mac):
    """把 'AA:BB:CC:DD:EE:FF' / 'aabbccddeeff' / 'AA-BB-CC-DD-EE-FF' 转为 bytes(6)。"""
    clean = mac.strip().replace(':', '').replace('-', '').replace('.', '').replace(' ', '')
    if len(clean) != 12:
        raise ValueError('MAC 地址格式不正确: %r' % mac)
    return bytes.fromhex(clean)


def pmkid_check(password, ssid, ap_mac, client_mac, target):
    """
    WPA2 PMKID 校验（每尝试一个密码做一次）。
    公式：PMKID = HMAC-SHA1-128(PMK, "PMK Name" | AP_MAC | Client_MAC)
           PMK   = PBKDF2-HMAC-SHA1(passphrase, SSID, 4096, 32)
    同时对 AP/Client MAC 的原序与反转序各试一次，兼容不同抓包工具的输出字节序。
    password/ssid 均为 bytes。
    """
    pmk = hashlib.pbkdf2_hmac('sha1', password, ssid, 4096, 32)
    for ap, cl in ((ap_mac, client_mac), (ap_mac[::-1], client_mac[::-1])):
        calc = hmac.new(pmk, b'PMK Name' + ap + cl, hashlib.sha1).digest()[:16]
        if hmac.compare_digest(calc, target):
            return True
    return False


def _crack_chunk(args):
    """
    单个 chunk 的破解 worker（multiprocessing.Pool 的任务单元）。
    chunk 内再按 threads 参数切分给多个线程并行（hashlib.pbkdf2_hmac 释放 GIL，
    每个线程都在 OpenSSL C 代码内执行，不互相阻塞）。
    返回 (找到的密码 bytes 或 None, chunk 条数)。
    """
    chunk, ssid, ap, client, target, threads = args
    n = len(chunk)
    found = None

    if threads <= 1 or n < 256:  # 小 chunk 不值得开线程
        for pwd in chunk:
            if pmkid_check(pwd, ssid, ap, client, target):
                found = pwd
                break
        return (found, n)

    lock = threading.Lock()
    per = (n + threads - 1) // threads
    parts = [chunk[i:i + per] for i in range(0, n, per)]

    def scan(part):
        nonlocal found
        for pwd in part:
            if found is not None:      # 其它线程已命中，提前退出
                return
            if pmkid_check(pwd, ssid, ap, client, target):
                with lock:
                    if found is None:
                        found = pwd
                return

    ts = [threading.Thread(target=scan, args=(p,), daemon=True) for p in parts]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return (found, n)


def _iter_chunks(pwds, size, ssid, ap, client, target, threads):
    """把密码迭代器切成固定大小 chunk，逐个交给进程池（惰性，不吃内存）。"""
    buf = []
    for pwd in pwds:
        if isinstance(pwd, str):
            pwd = pwd.encode('utf-8')
        buf.append(pwd)
        if len(buf) >= size:
            yield (buf, ssid, ap, client, target, threads)
            buf = []
    if buf:
        yield (buf, ssid, ap, client, target, threads)


# ──────────────────────────────── 主破解流程 ────────────────────────────────


def _fmt(seconds):
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return '%d:%02d:%02d' % (h, m, s)
    return '%02d:%02d' % (m, s)


def crack_target(pmkid_hex, ap, client, ssid, pwds, workers=None,
                 threads=2, chunk_size=4096, progress=True):
    """
    对给定 PMKID 目标并行测试密码迭代器 pwds。
    返回 (found_password 或 None, 已测条数, 耗时秒)。
    """
    workers = workers or cpu_count()
    target = bytes.fromhex(pmkid_hex.strip())
    ssid_b = ssid.encode('utf-8')
    ap_b, cl_b = mac_to_bytes(ap), mac_to_bytes(client)

    if progress:
        print('[*] 目标  : SSID=%r  AP=%s  Client=%s' % (ssid, ap, client))
        print('[*] 并行  : %d 进程 x %d 线程 = %d 个工作单元' % (workers, threads, workers * threads))
        print('[*] PMKID : %s' % pmkid_hex.strip().upper())
        print('[*] 开始破解...')

    start = time.time()
    done = 0
    last_t = [0.0]
    found = None
    pool = Pool(processes=workers)
    try:
        it = pool.imap_unordered(
            _crack_chunk,
            _iter_chunks(pwds, chunk_size, ssid_b, ap_b, cl_b, target, threads),
            chunksize=1)
        for res, n in it:
            done += n
            if res is not None:
                found = res
                pool.terminate()
                break
            if progress:
                now = time.time()
                if now - last_t[0] >= 0.8:
                    rate = done / (now - start)
                    sys.stdout.write('\r[*] 已测试 %10d 条   %8.0f 条/秒   用时 %s   '
                                     % (done, rate, _fmt(now - start)))
                    sys.stdout.flush()
                    last_t[0] = now
    finally:
        pool.close()
        pool.join()

    elapsed = time.time() - start
    if progress:
        if found is not None:
            print('\r[OK] 密码已找到: %s    (测试 %d 条, 用时 %s)  '
                  % (found.decode('utf-8', 'replace'), done, _fmt(elapsed)))
        else:
            print('\r[!] 未找到匹配密码 (测试 %d 条, 用时 %s)  '
                  % (done, _fmt(elapsed)))
            print('    建议: 换更大的字典 / 加 --gen-digits 数字组合 / 该网络可能使用了高熵密码')
    return (found, done, elapsed)


def load_wordlist(path, dedup=True):
    """读取字典文件（UTF-8/GBK 兼容），返回 bytes 密码列表。"""
    seen = set()
    out = []
    total = 0
    with open(path, 'rb') as f:
        for raw in f:
            line = raw.rstrip(b'\r\n')
            if not line:
                continue
            total += 1
            if dedup:
                if line in seen:
                    continue
                seen.add(line)
            out.append(line)
            if len(out) >= 50_000_000:  # 防爆内存
                print('[!] 字典过大，截断在 5000 万条', file=sys.stderr)
                break
    return out


# ──────────────────────────────── 内置常见密码 ────────────────────────────────

COMMON_PASSWORDS = [
    # 8-10 位纯数字
    '12345678', '123456789', '1234567890', '88888888', '66666666',
    '00000000', '11111111', '22222222', '33333333', '44444444',
    '55555555', '77777777', '99999999', '11223344', '12341234',
    '1357924680', '147258369', '159357', '1472583690', '12344321',
    # 中文常用组合
    'woaini1314', 'woaini520', 'woaini123', 'iloveyou', 'iloveyou1314',
    '5201314', '52013145', '520520', '1314520', '1314521', '1314520x',
    '7758521', '7758520', 'wodemima', 'wodemima123', 'mima1234',
    'a12345678', 'a123456789', 'aa123456', 'abc12345', 'abcd1234',
    'abc888', 'abc123456', 'qq123456', 'taobao123', 'zhangsan123',
    # 键盘轨迹
    '1qaz2wsx', '1qazxsw2', 'qwer1234', 'qwe123', 'qweasd', 'zxc123',
    'asd123', 'zxcv1234', 'qweasdzxc', '!@#$%^&*', 'qwerty123',
    '1q2w3e4r', '1q2w3e4r5t', 'qazwsxedc',
    # 通用弱口令
    'password', 'password123', 'Password123', 'admin123', 'admin888',
    'admin12345', 'root123', 'root1234', 'test1234', 'test12345',
    'wifi12345', 'wifi888', 'wifi1234', 'router123', 'default123',
    '123456789a', '123456789b', 'abcdef123', '123321', '123321123',
    '00000000', '66668888', '168168', '147258', '369369', '123000',
    '520888', '131313', '121212', '198911', '198801', '19900101',
]


def gen_digits(start_int, end_int):
    """生成 [start_int, end_int] 闭区间内的纯数字密码（惰性）。"""
    for n in range(start_int, end_int + 1):
        yield str(n).encode()


# ──────────────────────────────── recover：找回本机已存密码 ────────────────────────────────


def _decode_netsh(data):
    """netsh 在管道模式下输出 UTF-8（实测），但部分系统/代码页下是 GBK：
    先严格按 UTF-8 解码，失败或出现替换符则回退 GBK。"""
    if not data:
        return ''
    try:
        t = data.decode('utf-8')
        if '\ufffd' not in t:
            return t
    except UnicodeDecodeError:
        pass
    return data.decode('gbk', errors='replace')


def cmd_recover(args):
    try:
        out = subprocess.run(['netsh', 'wlan', 'show', 'profiles'],
                             capture_output=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print('[!] 无法执行 netsh —— 仅支持 Windows 且需启用 WLAN 服务')
        return 2
    text = _decode_netsh(out.stdout) or _decode_netsh(out.stderr)
    names = re.findall(r'(?:所有用户配置文件|All User Profile)\s*:\s*(.+)$', text, re.M)
    names = [n.strip() for n in names if n.strip()]
    if not names:
        print('[!] 未发现已保存的 WiFi 配置文件')
        return 1

    print('[+] 本机已保存 WiFi 配置: %d 个\n' % len(names))
    rows = []
    for idx, name in enumerate(names, 1):
        safe = name.replace('"', '')
        try:
            out2 = subprocess.run(
                ['netsh', 'wlan', 'show', 'profile', 'name="%s"' % safe, 'key=clear'],
                capture_output=True, timeout=15)
            t2 = _decode_netsh(out2.stdout)
        except subprocess.TimeoutExpired:
            t2 = ''
        m = re.search(r'(?:关键内容|Key Content)\s*:\s*(.+)$', t2, re.M)
        pwd = m.group(1).strip() if m else ''
        rows.append((name, pwd))
        print('  %2d. %-24s 密码: %s' % (idx, name, pwd or '(无/企业认证或隐藏)'))

    if args.csv:
        with open(args.csv, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['SSID', '密码'])
            w.writerows(rows)
        print('\n[OK] 已导出到 %s' % args.csv)
    return 0


# ──────────────────────────────── 命令行入口 ────────────────────────────────


def parse_target_line(line):
    """解析 PMKID 目标行，兼容 hcxdumptool('*') 与 hashcat-16800(':') 两种格式。
    pmkid(32hex) * ap(12hex) * client(12hex) * ssid"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    parts = line.split('*', 3) if '*' in line else line.split(':', 3)
    if len(parts) < 4:
        return None
    pmkid, ap, client, ssid = parts
    if len(pmkid) != 32 or len(ap) != 12 or len(client) != 12:
        return None
    return pmkid, ap, client, ssid


def cmd_crack(args):
    # 目标来源：--pmkid 优先，否则读 --pmkid-file
    targets = []
    if args.pmkid:
        if len(args.pmkid) != 32:
            print('[!] --pmkid 必须是 32 位 hex')
            return 2
        targets.append((args.pmkid, args.ap, args.client, args.ssid))
    elif args.pmkid_file:
        with open(args.pmkid_file, 'r', encoding='utf-8', errors='ignore') as f:
            for ln in f:
                t = parse_target_line(ln)
                if t:
                    targets.append(t)
        if not targets:
            print('[!] --pmkid-file 中没有解析到有效目标行')
            return 2
    else:
        print('[!] 必须提供 --pmkid 或 --pmkid-file')
        return 2

    # 字典来源：--wordlist / --gen-common / --gen-digits，三者可叠加
    pwds = []
    if args.wordlist:
        print('[*] 读取字典 %s ...' % args.wordlist)
        pwds.extend(load_wordlist(args.wordlist, dedup=not args.no_dedup))
        print('[*] 字典 %d 条' % len(pwds))
    if args.gen_common:
        pwds.extend(COMMON_PASSWORDS)
    if args.gen_digits:
        lo, hi = int(args.gen_digits[0]), int(args.gen_digits[1])
        if hi - lo > 50_000_000:
            print('[!] 数字范围超过 5000 万，可能需数小时，请确认')
        pwds.extend(gen_digits(lo, hi))

    if not pwds:
        print('[!] 没有可用的密码源。请提供 --wordlist / --gen-common / --gen-digits')
        return 2

    # 去重（若未用文件去重且量不大）
    if args.gen_common or args.gen_digits:
        seen = set()
        out = []
        for p in pwds:
            if p not in seen:
                seen.add(p)
                out.append(p)
        pwds = out
        print('[*] 去重后 %d 条' % len(pwds))

    workers = args.workers or cpu_count()
    for pmkid, ap, client, ssid in targets:
        found, done, el = crack_target(
            pmkid, ap, client, ssid, pwds,
            workers=workers, threads=args.threads, progress=not args.quiet)
        if found:
            print('\n[OK] 目标 SSID=%r 破解成功' % ssid)
            return 0
    print('\n[!] 所有目标均未破解')
    return 1


def cmd_gen(args):
    length = args.length
    lo = args.start if args.start is not None else 0
    hi = args.end if args.end is not None else (10 ** length) - 1
    if hi <= lo:
        print('[!] --end 必须大于 --start')
        return 2
    if hi - lo > 100_000_000:
        print('[!] 数量超过 1 亿，文件将很大；如非必要请缩小范围')
    fmt = '%%0%dd' % length
    count = 0
    with open(args.output, 'w', encoding='utf-8') as f:
        for n in range(lo, hi + 1):
            f.write((fmt % n) + '\n')
            count += 1
    print('[OK] 已生成 %d 条数字密码 -> %s' % (count, args.output))
    return 0


def main():
    parser = argparse.ArgumentParser(
        prog='WiFi密码破解',
        description='WiFi 密码审计工具（多进程+多线程 PMKID 破解 / 找回本机已存密码 / 生成字典）。'
                    '仅限测试自己拥有或有授权的网络。')
    sub = parser.add_subparsers(dest='cmd')

    p_crack = sub.add_parser('crack', help='PMKID 模式并行破解')
    g1 = p_crack.add_mutually_exclusive_group(required=False)
    g1.add_argument('--pmkid', help='PMKID 32位hex')
    g1.add_argument('--pmkid-file', help='目标文件（每行 pmkid*ap*client*ssid，支持 # 注释）')
    p_crack.add_argument('--ap', help='AP MAC，如 AA:BB:CC:DD:EE:FF（--pmkid 时必须）')
    p_crack.add_argument('--client', help='Client MAC（--pmkid 时必须）')
    p_crack.add_argument('--ssid', help='WiFi 名称（--pmkid 时必须）')
    p_crack.add_argument('-w', '--wordlist', help='密码字典文件（UTF-8/GBK，每行一个）')
    p_crack.add_argument('--no-dedup', action='store_true', help='大字典跳过内存去重（省内存）')
    p_crack.add_argument('--gen-common', action='store_true', help='叠加内置常见弱密码列表')
    p_crack.add_argument('--gen-digits', nargs=2, metavar=('START', 'END'),
                         help='叠加数字密码 [START, END] 闭区间')
    p_crack.add_argument('--workers', type=int, default=0, help='进程数（默认=CPU核数）')
    p_crack.add_argument('--threads', type=int, default=2, help='每进程线程数（默认2）')
    p_crack.add_argument('--quiet', action='store_true', help='关闭进度输出')
    p_crack.set_defaults(func=cmd_crack)

    p_rec = sub.add_parser('recover', help='找回本机已保存的 WiFi 密码 (Windows)')
    p_rec.add_argument('--csv', help='导出为 CSV 文件')
    p_rec.set_defaults(func=cmd_recover)

    p_gen = sub.add_parser('gen', help='生成纯数字密码字典')
    p_gen.add_argument('length', type=int, help='密码位数，如 8')
    p_gen.add_argument('-o', '--output', required=True, help='输出文件')
    p_gen.add_argument('--start', type=int, default=None, help='起始数字（默认 0）')
    p_gen.add_argument('--end', type=int, default=None, help='结束数字（默认 10^length-1）')
    p_gen.set_defaults(func=cmd_gen)

    args = parser.parse_args()
    if not getattr(args, 'cmd', None):
        # 无子命令：打印帮助 + 快速上手示例，而不是报错退出
        parser.print_help()
        print('\n快速上手：')
        print('  wifi_password_cracker.py recover                # 找回本机已保存的 WiFi 密码')
        print('  wifi_password_cracker.py gen 8 -o digits.txt    # 生成 8 位纯数字字典')
        print('  wifi_password_cracker.py crack --help           # 查看 PMKID 破解详细用法')
        print('  wifi_password_cracker.py gen 4 -o d.txt --start 0 --end 99  # 自定义范围')
        sys.exit(0)
    sys.exit(args.func(args))


if __name__ == '__main__':
    # Windows 下 multiprocessing 采用 spawn 启动，必须由主模块保护
    freeze_support()
    main()
