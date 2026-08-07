"""模拟生成一份完整的应用性能监控 HTML 测试报告 (含闪退/ANR/卡顿日志).

场景设计:
  0-25s   正常运行 — CPU 平稳, 内存稳定, FPS 60
  26-55s  内存泄漏 — PSS 持续攀升, Java Heap 增长, GC 频繁, Jank 上升
  56-60s  严重卡顿 — FPS 暴跌至 15-25, Jank 40%+, CPU 90%+
  60-62s  OOM 崩溃 — 进程被 LMK 杀死, 数据中断 (None)
  63-68s  自动重启 — 冷启动, 内存回落, 但出现 ANR
  69-80s  恢复正常 — 各指标趋于平稳
"""
import math
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ''))
from app_perf_monitor import AppPerfMonitor

# ---- 模拟参数 ----
PKG = 'com.tencent.qqlive'
PID = 12345
SERIAL = 'emulator-5554'
SAMPLE_INTERVAL = 2
MAX_POINTS = 120
N = 80

random.seed(42)

# ---- 场景时间线 (采样点索引) ----
# 0-25: 正常 | 26-55: 内存泄漏 | 56-59: 严重卡顿 | 60-62: OOM崩溃(进程死亡)
# 63-68: 重启+ANR | 69-79: 恢复正常
CRASH_START = 60   # 进程死亡起点
RESTART_AT = 63    # 重启点
ANR_AT = 66        # ANR 发生点


def gen_cpu(n):
    """CPU: 正常 15% → 泄漏期 35% → 卡顿期 90% → 崩溃 None → 重启 60% → 恢复 15%."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)  # 进程死亡
        elif RESTART_AT <= i < ANR_AT:
            v = 55 + random.uniform(-5, 10)  # 重启高 CPU
            vals.append(round(v, 1))
        elif i == ANR_AT:
            vals.append(round(95 + random.uniform(-3, 3), 1))  # ANR 时 CPU 飙升
        elif ANR_AT < i:
            v = 15 + random.uniform(-3, 3)  # 恢复正常
            vals.append(round(max(v, 0), 1))
        elif i >= 56:  # 严重卡顿
            v = 85 + 10 * math.sin((i - 56) * 0.8) + random.uniform(-5, 5)
            vals.append(round(max(v, 0), 1))
        elif i >= 26:  # 内存泄漏期
            v = 30 + (i - 26) * 0.5 + random.uniform(-3, 3)
            vals.append(round(max(v, 0), 1))
        else:  # 正常
            v = 12 + 5 * math.sin(i * 0.15) + random.uniform(-2, 2)
            vals.append(round(max(v, 0), 1))
    return vals


def gen_pss(n):
    """PSS: 180MB 稳定 → 泄漏期持续攀升至 480MB → 崩溃 → 重启回落 200MB."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= RESTART_AT:
            v = 195 + (i - RESTART_AT) * 0.3 + random.uniform(-3, 3)
            vals.append(round(v, 1))
        elif i >= 26:
            # 泄漏: 从 190 加速增长到 ~480
            v = 190 + (i - 26) ** 1.4 * 0.6 + random.uniform(-3, 3)
            vals.append(round(v, 1))
        else:
            v = 180 + random.uniform(-3, 3)
            vals.append(round(v, 1))
    return vals


def gen_java_heap(n):
    """Java Heap: 45MB → 泄漏期升至 180MB → 崩溃 → 重启 48MB."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= RESTART_AT:
            v = 48 + (i - RESTART_AT) * 0.1 + random.uniform(-1, 1)
            vals.append(round(max(v, 5), 1))
        elif i >= 26:
            v = 45 + (i - 26) ** 1.3 * 0.4 + random.uniform(-1, 1)
            vals.append(round(max(v, 5), 1))
        else:
            v = 45 + 3 * math.sin(i * 0.2) + random.uniform(-1, 1)
            vals.append(round(max(v, 10), 1))
    return vals


def gen_native_heap(n):
    """Native Heap: 65MB 稳定, 泄漏期微升."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= RESTART_AT:
            v = 62 + random.uniform(-2, 2)
            vals.append(round(max(v, 5), 1))
        elif i >= 26:
            v = 65 + (i - 26) * 0.15 + random.uniform(-2, 2)
            vals.append(round(max(v, 5), 1))
        else:
            v = 65 + 3 * math.sin(i * 0.1) + random.uniform(-2, 2)
            vals.append(round(max(v, 5), 1))
    return vals


def gen_gfx(n):
    """Graphics: 视频播放时段 25MB, 其他 8MB."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif 20 <= i <= 55:
            v = 25 + 5 * math.sin(i * 0.3) + random.uniform(-2, 2)
            vals.append(round(max(v, 0), 1))
        else:
            v = 8 + random.uniform(-1, 1)
            vals.append(round(max(v, 0), 1))
    return vals


def gen_threads(n):
    """线程数: 85 → 泄漏期缓增至 120 → 崩溃 → 重启 50 → 恢复 85."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= RESTART_AT:
            v = 50 + (i - RESTART_AT) * 2 + random.uniform(-1, 1)
            vals.append(round(max(v, 10), 1))
        elif i >= 26:
            v = 85 + (i - 26) * 0.6 + random.uniform(-1, 1)
            vals.append(round(v))
        else:
            v = 85 + (i // 20) + random.uniform(-1, 1)
            vals.append(round(v))
    return vals


def gen_jank(n):
    """Jank: 2% → 泄漏期 8% → 卡顿期 40%+ → 崩溃 → 重启 15% → 恢复 2%."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i == ANR_AT:
            vals.append(100.0)  # ANR 时完全卡死
        elif i > ANR_AT:
            v = 2 + random.uniform(-1, 2)
            vals.append(round(max(v, 0), 1))
        elif i >= RESTART_AT:
            v = 15 + random.uniform(-3, 5)
            vals.append(round(max(v, 0), 1))
        elif i >= 56:  # 严重卡顿
            v = 35 + 10 * math.sin((i - 56) * 0.6) + random.uniform(-3, 5)
            vals.append(round(max(v, 0), 1))
        elif i >= 26:
            v = 5 + (i - 26) * 0.2 + random.uniform(-1, 2)
            vals.append(round(max(v, 0), 1))
        else:
            v = 2 + random.uniform(-1, 2)
            vals.append(round(max(v, 0), 1))
    return vals


def gen_power(n):
    """应用耗电: 累计增长, 每 15 点一个值, 崩溃期 None."""
    vals = []
    cumulative = 0
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i % 15 == 0 or i == RESTART_AT:
            cumulative += random.uniform(2, 8)
            vals.append(round(cumulative, 1))
    return vals


def gen_fps(n):
    """FPS: 60 → 泄漏期 55 → 卡顿期 15-25 → 崩溃 → 重启 40 → 恢复 60."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i == ANR_AT:
            vals.append(0.0)  # ANR 时 FPS=0
        elif i > ANR_AT:
            v = 59 + random.uniform(-1, 1)
            vals.append(round(max(v, 0), 1))
        elif i >= RESTART_AT:
            v = 40 + (i - RESTART_AT) * 3 + random.uniform(-2, 2)
            vals.append(round(min(max(v, 0), 60), 1))
        elif i >= 56:  # 严重卡顿
            v = 18 + 8 * math.sin((i - 56) * 0.7) + random.uniform(-3, 3)
            vals.append(round(max(v, 0), 1))
        elif i >= 26:
            v = 56 - (i - 26) * 0.1 + random.uniform(-2, 2)
            vals.append(round(max(v, 0), 1))
        else:
            v = 59 + random.uniform(-1, 1)
            vals.append(round(max(v, 0), 1))
    return vals


def gen_net(n):
    """网络流量: 视频播放高, 崩溃期 None, ANR 时极低."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i == ANR_AT:
            vals.append(0.5)
        elif 20 <= i <= 55:
            v = 200 + 80 * math.sin(i * 0.2) + random.uniform(-20, 20)
            vals.append(round(max(v, 0), 1))
        elif i >= RESTART_AT:
            v = 80 + 30 * math.sin((i - RESTART_AT) * 0.3) + random.uniform(-10, 10)
            vals.append(round(max(v, 0), 1))
        else:
            v = 5 + random.uniform(-2, 5)
            vals.append(round(max(v, 0), 1))
    return vals


def gen_fd(n):
    """FD: 120 → 泄漏期增至 200 → 崩溃 → 重启 60 → 恢复 85."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= RESTART_AT:
            v = 60 + (i - RESTART_AT) * 1.5 + random.uniform(-2, 2)
            vals.append(round(max(v, 10), 1))
        elif i >= 26:
            v = 120 + (i - 26) * 1.0 + random.uniform(-2, 2)
            vals.append(round(v))
        else:
            v = 120 + i * 0.3 + random.uniform(-2, 2)
            vals.append(round(v))
    return vals


def gen_io(n):
    """磁盘 I/O: 偶尔大读写, 崩溃期 None, GC 频繁期 I/O 高."""
    vals = []
    for i in range(n):
        if CRASH_START <= i < RESTART_AT:
            vals.append(None)
        elif i >= 56 and i < CRASH_START:  # GC 频繁, I/O 飙升
            v = 80 + random.uniform(-10, 30)
            vals.append(round(max(v, 0), 1))
        elif i >= 26 and i < 56:  # 泄漏期 I/O 稍高
            v = 25 + random.uniform(-5, 15)
            vals.append(round(max(v, 0), 1))
        elif i in (10, 25, 40, 55, 70):
            v = 150 + random.uniform(0, 50)
            vals.append(round(max(v, 0), 1))
        else:
            v = 10 + random.uniform(-5, 10)
            vals.append(round(max(v, 0), 1))
    return vals


def pad_to(vals, n):
    while len(vals) < n:
        vals.append(None)
    return vals[:n]


def make_chart_data(key, title, color, unit, vals):
    valid = [v for v in vals if v is not None]
    if valid:
        stats = {
            'max': round(max(valid), 2),
            'avg': round(sum(valid) / len(valid), 2),
            'min': round(min(valid), 2),
        }
    else:
        stats = {'max': None, 'avg': None, 'min': None}
    return {
        'id': key,
        'title': title,
        'color': color,
        'unit': unit,
        'y_max': round(max(valid) * 1.3 if valid else 100, 1),
        'data': [round(v, 2) if v is not None else None for v in vals],
        'stats': stats,
    }


# ---- 生成数据 ----
cpu_vals = pad_to(gen_cpu(N), N)
pss_vals = pad_to(gen_pss(N), N)
java_vals = pad_to(gen_java_heap(N), N)
native_vals = pad_to(gen_native_heap(N), N)
gfx_vals = pad_to(gen_gfx(N), N)
thread_vals = pad_to(gen_threads(N), N)
jank_vals = pad_to(gen_jank(N), N)
power_vals = pad_to(gen_power(N), N)
fps_vals = pad_to(gen_fps(N), N)
net_vals = pad_to(gen_net(N), N)
fd_vals = pad_to(gen_fd(N), N)
io_vals = pad_to(gen_io(N), N)

charts = [
    make_chart_data('cpu', 'CPU 使用率', '#1de9b6', '%', cpu_vals),
    make_chart_data('pss', '内存 PSS (TOTAL)', '#ffab40', 'MB', pss_vals),
    make_chart_data('java', 'Java Heap', '#61afef', 'MB', java_vals),
    make_chart_data('native', 'Native Heap', '#e06c75', 'MB', native_vals),
    make_chart_data('gfx', 'Graphics 显存', '#c678dd', 'MB', gfx_vals),
    make_chart_data('thread', '线程数', '#d19a66', '', thread_vals),
    make_chart_data('jank', 'Jank 丢帧率', '#56b6c2', '%', jank_vals),
    make_chart_data('power', '应用耗电', '#ff6b9d', 'mAh', power_vals),
    make_chart_data('fps', 'FPS 帧率', '#e5c07b', 'fps', fps_vals),
    make_chart_data('net', '网络流量 (TX+RX)', '#61afef', 'KB/s', net_vals),
    make_chart_data('fd', '文件描述符 (FD)', '#e06c75', '', fd_vals),
    make_chart_data('io', '磁盘 I/O (R+W)', '#c678dd', 'KB/s', io_vals),
]

# ---- 泄漏检测详情 ----
leak_details = [
    {'name': 'PSS', 'status': 'leak', 'slope': 2.4},
    {'name': 'Java', 'status': 'leak', 'slope': 1.8},
    {'name': 'Native', 'status': 'stable', 'slope': 0.05},
]

# ---- 崩溃 / ANR 日志 (模拟真实 logcat 输出) ----
crash_log_text = (
    '===== OOM 崩溃日志 (采样点 60, 15:28:30) =====\n'
    '>> 15:28:28.453  12345  12360 E AndroidRuntime: FATAL EXCEPTION: main\n'
    '>> 15:28:28.454  12345  12360 E AndroidRuntime: Process: com.tencent.qqlive, PID: 12345\n'
    '>> 15:28:28.455  12345  12360 E AndroidRuntime: java.lang.OutOfMemoryError: Failed to allocate a 8388608 byte allocation with 6291456 free bytes and 6MB until OOM\n'
    '>> 15:28:28.456  12345  12360 E AndroidRuntime: \tat com.tencent.qqlive.player.VideoBufferManager.allocBuffer(VideoBufferManager.java:245)\n'
    '>> 15:28:28.457  12345  12360 E AndroidRuntime: \tat com.tencent.qqlive.player.VideoDecoder.dequeueFrame(VideoDecoder.java:178)\n'
    '>> 15:28:28.458  12345  12360 E AndroidRuntime: \tat com.tencent.qqlive.player.PlayerCore.onVideoFrame(PlayerCore.java:312)\n'
    '>> 15:28:28.459  12345  12360 E AndroidRuntime: \tat com.tencent.qqlive.player.PlayerCore.nativeLoop(Native Method)\n'
    '   15:28:28.460  12345  12360 E AndroidRuntime: \tat com.tencent.qqlive.player.PlayerCore.run(PlayerCore.java:156)\n'
    '   15:28:28.461  12345  12360 E AndroidRuntime: \tat java.lang.Thread.run(Thread.java:1012)\n'
    '   15:28:29.102   852   852 W lowmemorykiller: Using in_psi kill to kill com.tencent.qqlive (12345)\n'
    '   15:28:29.103   852   852 W lowmemorykiller: Killing \'com.tencent.qqlive\' (12345), uid 10156, oom_score_adj 100\n'
    '   15:28:29.104   852   852 I ActivityManager: Process com.tencent.qqlive (pid 12345) has died: prc 0\n'
    '   ...\n'
    '\n'
    '===== ANR 日志 (采样点 66, 15:28:42) =====\n'
    '>> 15:28:41.234  12480  12480 E ActivityManager: ANR in com.tencent.qqlive (com.tencent.qqlive/.ui.MainActivity)\n'
    '>> 15:28:41.235  12480  12480 E ActivityManager: PID: 12480, Reason: Input dispatching timed out (Waiting to send non-key event because the touched window has not finished processing certain input events that were delivered to it over 500.0ms ago.)\n'
    '>> 15:28:41.236  12480  12480 E ActivityManager: CPU usage from 0ms to 5823ms ago:\n'
    '>> 15:28:41.237  12480  12480 E ActivityManager:   95% 12480/com.tencent.qqlive: 85% user + 10% kernel / faults: 12840 minor 32 major\n'
    '>> 15:28:41.238  12480  12480 E ActivityManager:   12%  852/system_server: 8% user + 3% kernel / faults: 3204 minor\n'
    '>> 15:28:41.239  12480  12480 E ActivityManager:   8%  2100/com.android.systemui: 5% user + 3% kernel\n'
    '   15:28:41.240  12480  12495 W art     : Long monitor contention with owner SamplerThread (12495)\n'
    '   15:28:41.241  12480  12495 W art     : at com.tencent.qqlive.player.PlayerCore.lockSurface(PlayerCore.java:445)\n'
    '   15:28:41.242  12480  12495 W art     : - locked <0x0a3f> (a com.tencent.qqlive.player.PlayerCore)\n'
    '   15:28:41.243  12480  12480 I art     : Thread[1,tid=12480,Native,Thread*,0,0,0] recursion=0 depth=1\n'
    '   15:28:41.244  12480  12480 I am_anr  : 0 10156 com.tencent.qqlive 5000 com.tencent.qqlive.ui.MainActivity\n'
    '   ...\n'
    '\n'
    '===== GC 频繁警告 (采样点 50-58) =====\n'
    '   15:28:12.345  12345  12360 I art     : Background partial concurrent mark sweep GC freed 12MB(45MB) AllocSpace objects, 8MB(32MB) LOS objects, 40% free, 180MB/256MB, paused 3.2ms total 85ms\n'
    '   15:28:14.567  12345  12360 I art     : Background partial concurrent mark sweep GC freed 8MB(42MB) AllocSpace objects, 6MB(30MB) LOS objects, 35% free, 195MB/256MB, paused 4.1ms total 92ms\n'
    '   15:28:16.890  12345  12360 I art     : Background partial concurrent mark sweep GC freed 5MB(38MB) AllocSpace objects, 4MB(28MB) LOS objects, 28% free, 210MB/256MB, paused 5.8ms total 108ms\n'
    '>> 15:28:18.123  12345  12360 W art     : Clamp target GC heap from 230MB to 256MB\n'
    '>> 15:28:20.456  12345  12360 W art     : Clamp target GC heap from 242MB to 256MB\n'
    '>> 15:28:22.789  12345  12360 W art     : Clamp target GC heap from 248MB to 256MB\n'
    '   15:28:24.012  12345  12360 I art     : Background concurrent mark sweep GC freed 2MB(30MB) AllocSpace objects, 1MB(25MB) LOS objects, 15% free, 250MB/256MB, paused 12ms total 145ms'
)

# ---- 构建 report dict ----
valid_count = len([v for v in cpu_vals if v is not None])
total_count = len(cpu_vals)

report = {
    'package': PKG,
    'serial': SERIAL,
    'pid': '12480 (重启后)',
    'uid': '10156',
    'start_time': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time() - 160)),
    'export_time': time.strftime('%Y-%m-%d %H:%M:%S'),
    'sample_interval_s': SAMPLE_INTERVAL,
    'max_points': MAX_POINTS,
    'max_heap_mb': '256MB',
    'app_power_mah': '28.7mAh',
    'total_power_mah': '412mAh',
    'battery_info': '62% | 3.82V | -450mA | 28.3°C | 放电中',
    'info_text': (
        f'包名: {PKG}    PID: 12480    CPU: 15.2%    PSS: 198MB    线程: 85线程    已运行 12s\n'
        '  ⚠ 监控期间发生 1 次 OOM 崩溃 (采样点 60) + 1 次 ANR (采样点 66)'
    ),
    'leak_text': '内存泄漏检测: ⚠️ PSS: +2.4 MB/min (疑似泄漏) | Java: +1.8 MB/min (疑似泄漏) | Native: +0.05 MB/min (稳定)',
    'oom_text': (
        '内存溢出检测: ☠️ OOM 崩溃已发生!\n'
        '  崩溃前 Java Heap: 180MB / 256MB (70%) | PSS: 480MB\n'
        '  崩溃原因: OutOfMemoryError (VideoBufferManager.allocBuffer)\n'
        '  进程被 lowmemorykiller 终止 (pid 12345)'
    ),
    'anr_text': (
        'ANR 检测: ⚠️ 检测到 ANR!\n'
        '  发生时间: 采样点 66 (15:28:42)\n'
        '  原因: Input dispatching timed out (>500ms)\n'
        '  CPU: 95% (85% user + 10% kernel)\n'
        '  锁竞争: PlayerCore.lockSurface 持锁过长'
    ),
    'extra_info_text': (
        '📊 扩展指标:\n'
        '  🔄 GC: 12次 (崩溃前 GC 频繁, 最后 3 次触发 Clamp target GC heap)\n'
        '  🌡 CPU温度: 48°C (⚠ 崩溃前达 52°C)\n'
        '  🔒 WakeLock: 持有 1 个 (VideoPlayerWakeLock, 8.2s)\n'
        '  📦 存储: 234M\n'
        '  📉 掉电: 8.5%/h (高负载)\n'
        '  ⚠ FD: 峰值 200 (崩溃前持续增长, 疑似 FD 泄漏)'
    ),
    'startup_text': '冷启动 2156ms (Wait 2341ms) — 重启后测量',
    'app_info_text': (
        '📦 应用信息: v9.8.0.12345 (98012345)  |  Target SDK 33 / Min SDK 24  |  '
        '安装: 2026-07-15 10:30:00  |  更新: 2026-08-01 14:20:33  |  '
        '🐛 Debuggable  |  UID: 10156  |  数据目录: /data/data/com.tencent.qqlive'
    ),
    'crash_log_text': crash_log_text,
    'power_text': '⏱ 已运行 12s (重启后) | 崩溃前运行 2m00s',
    'app_power_text': '🔌 应用耗电: ~28.7 mAh (UID: 10156) / 总 412mAh (7.0%)',
    'battery_text': '🔋 电池: 62% | 3.82V | -450mA | 28.3°C | 放电中 (⚠ 温度偏高)',
    'device_text': (
        '序列号: emulator-5554\n'
        '设备型号: Pixel 6 (API 33)\n'
        '厂商: google (Google)\n'
        'Android: 13 (SDK 33, Build TQ3A.230705.001)\n'
        '安全补丁: 2023-07-05\n'
        'CPU 架构: arm64-v8a\n'
        'CPU 型号: Tensor G2\n'
        'GPU: Mali-G710 MC10\n'
        '屏幕: 1080x2400 @ 420dpi\n'
        '运行内存: 8.0 GB / 已用 4.8 GB (60%)\n'
        'MAC: A4:50:46:XX:XX:XX'
    ),
    'leak_details': leak_details,
    'charts': charts,
}

# ---- 生成 HTML ----
html = AppPerfMonitor._build_html_template(report)

# ---- 保存 ----
desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
save_dir = os.path.join(desktop, 'Super_ADB')
os.makedirs(save_dir, exist_ok=True)
filepath = os.path.join(save_dir, f'test_report_{PKG}_{time.strftime("%Y%m%d_%H%M%S")}.html')
with open(filepath, 'w', encoding='utf-8') as f:
    f.write(html)

print(f'报告已保存: {filepath}')
print(f'图表数: {len(charts)}')
print(f'有效采样点: {valid_count}/{total_count} ({valid_count*100//total_count}%)')
print(f'场景: 正常(0-25) → 内存泄漏(26-55) → 严重卡顿(56-59) → OOM崩溃(60-62) → 重启+ANR(63-68) → 恢复(69-79)')
print()
for c in charts:
    s = c['stats']
    print(f'  {c["title"]:25s}  最高:{s["max"]}  平均:{s["avg"]}  最低:{s["min"]} {c["unit"]}')
