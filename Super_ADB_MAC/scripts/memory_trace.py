#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Super_ADB 启动内存实测脚本（P2 量化工具）

用途
----
量化「延迟 import 优化」前后，主窗口构造完成时的内存占用差异，并逐项给出
每个重型子模块（设备性能监控 / Monkey / 应用监控 / 安装解包 / 各类弹窗等）
被加载时带来的 RSS 增量，便于确认优化真实生效。

原理
----
1. 在 offscreen 平台下启动 Qt，避免真实显示器依赖；
2. 用 tracemalloc 统计 Python 堆分配；
3. 用 RSS（常驻物理内存 / Working Set）统计进程真实内存；
   - 优先 psutil；缺失时 Windows 用 ctypes 调 GetProcessMemoryInfo，
     POSIX 用 resource.getrusage（Linux 单位 KB、macOS 单位字节）。
4. 分阶段测量：
   S0 基线（仅最小 import）
   S1 导入 PySide6 核心
   S2 导入 Super_ADB_Win（此时重型子模块不应被加载 = 延迟 import 生效）
   S3 构造 QApplication + MainWindow
   S4 逐个 eager 导入重型子模块，记录各自 RSS 增量

运行
----
    set QT_QPA_PLATFORM=offscreen
    D:/Python/Python314/python.exe Super_ADB_Win/scripts/memory_trace.py

（脚本内部已强制 offscreen，无需手动 set 环境变量。）
"""

import os
import sys
import gc
import time

# 必须在导入任何 PySide6 模块之前设置，否则在服务器/无显示器环境会崩溃。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 让 `import Super_ADB_Win` 能找到 Super_ADB_Win/Super_ADB_Win.py
# （该目录下没有 __init__.py，必须把它自身加入 sys.path）。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_MAIN_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))  # -> Super_ADB_Win/
if _MAIN_DIR not in sys.path:
    sys.path.insert(0, _MAIN_DIR)

# --------------------------------------------------------------------------- #
# RSS 采集（跨平台回退）
# --------------------------------------------------------------------------- #
def get_rss_mb():
    """返回当前进程常驻内存（MB）。多策略回退，确保任意环境都能取到值。"""
    # 1) psutil（最准）
    try:
        import psutil  # type: ignore
        return psutil.Process().memory_info().rss / 1024.0 / 1024.0
    except Exception:
        pass
    # 2) Windows ctypes -> psapi.GetProcessMemoryInfo (WorkingSetSize)
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(counters)
        hproc = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(hproc, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize / 1024.0 / 1024.0
    except Exception:
        pass
    # 3) POSIX resource.getrusage
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux/FreeBSD 单位 KB；macOS 单位字节。
        if sys.platform == "darwin":
            return rss / 1024.0 / 1024.0
        return rss / 1024.0
    except Exception:
        pass
    return 0.0


def measure(tag):
    """gc 稳定后采样 RSS，返回 (tag, rss_mb)。"""
    gc.collect()
    time.sleep(0.05)
    return tag, get_rss_mb()


# --------------------------------------------------------------------------- #
# 重型子模块清单（本次优化前为顶层 import，优化后改为「用到才 import」）
# --------------------------------------------------------------------------- #
HEAVY_MODULES = [
    ("device_performance_monitor", "DevicePerfMonitor"),
    ("monkey_stress_window", "MonkeyRunnerWindow"),
    ("app_performance_monitor", "AppPerfMonitor"),
    ("install_unpack_dialog", "InstallZipDialog"),
    ("tcpdump_dialog", "TcpdumpDialog"),
    ("about_dialog", "AboutDialog"),
    ("json_tool_dialog", "JsonToolDialog"),
    ("MD5对话框", "Md5Dialog"),
    ("timestamp_dialog", "TimestampDialog"),
    ("wireless_debug_dialog", "WirelessDebugDialog"),
    ("wifi_dialog", "WifiDialog"),
]


def main():
    import tracemalloc

    print("=" * 72)
    print("Super_ADB 启动内存实测  (platform=%s, py=%s)"
          % (sys.platform, sys.version.split()[0]))
    print("=" * 72)

    tracemalloc.start(25)

    # ---- S0 基线 ----
    s0 = measure("S0 基线 (仅 tracemalloc/os/sys)")
    print("%-32s %8.1f MB" % (s0[0], s0[1]))

    # ---- S1 PySide6 核心 ----
    from PySide6 import QtWidgets, QtCore, QtGui  # noqa: F401
    s1 = measure("S1 +PySide6.Qt* 核心")
    print("%-32s %8.1f MB   (Δ vs S0: %+.1f MB)" % (s1[0], s1[1], s1[1] - s0[1]))

    # ---- S2 导入主窗口模块（延迟 import 应使重型子模块未加载）----
    import Super_ADB_Win  # noqa: F401
    s2 = measure("S2 +import Super_ADB_Win")
    print("%-32s %8.1f MB   (Δ vs S1: %+.1f MB)" % (s2[0], s2[1], s2[1] - s1[1]))

    # 验证延迟 import 生效：扫描 sys.modules，确认重型子模块尚未被加载
    loaded_early = [m for m, _ in HEAVY_MODULES if m in sys.modules]
    print("-" * 72)
    if loaded_early:
        print("⚠ 以下重型模块在 import 阶段已被加载（延迟 import 未完全生效）:")
        for m in loaded_early:
            print("    - %s" % m)
    else:
        print("✓ 延迟 import 生效：import Super_ADB_Win 后 11 个重型子模块均未被加载")
    print("-" * 72)

    # ---- S3 构造主窗口 ----
    s3_rss = None
    try:
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        win = Super_ADB_Win.MainWindow()
        s3 = measure("S3 +MainWindow 构造")
        s3_rss = s3[1]
        print("%-32s %8.1f MB   (Δ vs S2: %+.1f MB)" % (s3[0], s3[1], s3[1] - s2[1]))
        del win
    except Exception as exc:  # 无设备/无 ADB 环境下构造可能失败，忽略
        print("S3 MainWindow 构造跳过（环境无设备/ADB？）: %s" % exc)

    # ---- S4 逐个 eager 导入重型子模块，量化各自 RSS 增量 ----
    print("-" * 72)
    print("各重型子模块 eager 导入的 RSS 增量（即「延迟 import 省下的内存」）:")
    print("%-28s %10s %10s" % ("module", "ΔRSS(MB)", "累计(MB)"))
    base = measure("base")[1]
    cum = 0.0
    for mod, _cls in HEAVY_MODULES:
        if mod in sys.modules:
            continue
        before = measure("before")[1]
        try:
            __import__(mod)
        except Exception as exc:
            print("%-28s 导入失败: %s" % (mod, exc))
            continue
        after = measure("after")[1]
        delta = after - before
        cum += delta
        print("%-28s %+10.1f %+10.1f" % (mod, delta, cum))

    # ---- 汇总 ----
    print("=" * 72)
    eager_total = s2[1] + cum  # 若全部 eager 加载的估算总占用
    print("汇总：")
    print("  import 主窗口后 RSS (重型模块未加载): %8.1f MB" % s2[1])
    if s3_rss is not None:
        print("  构造主窗口后 RSS                  : %8.1f MB" % s3_rss)
    print("  11 个重型子模块 eager 加载合计增量  : %8.1f MB" % cum)
    print("  => 延迟 import 为用户启动省下的内存  : ~%5.1f MB (重型模块按需才加载)" % cum)
    print("=" * 72)

    # ---- tracemalloc Python 堆分配 Top 10 ----
    snapshot = tracemalloc.take_snapshot()
    top = snapshot.statistics("lineno")[:10]
    print("Python 堆分配 Top 10（tracemalloc）:")
    for stat in top:
        print("  %8.1f KB  %s" % (stat.size / 1024.0, stat.traceback.format()[0]
                                  if stat.traceback else ""))
    print("=" * 72)


if __name__ == "__main__":
    main()
