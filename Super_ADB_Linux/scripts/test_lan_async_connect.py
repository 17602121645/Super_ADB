# -*- coding: utf-8 -*-
"""局域网扫描「连接按钮卡死」修复离线验证（offscreen）。

回归现场（用户截图）：
  * 端口 37997 扫描时正常扫到 1 台设备
  * 点击行内「连接」按钮后，主窗口标题变成「(未响应)」，按钮按下没反应
根因：
  * _connect_one 在 UI 主线程同步调用 subprocess.run([adb, connect, ...])
  * adb 进程卡在 TCP socket 等待子进程无法终止 → 整窗未响应
  * _enrich_after_connect 用 threading.Thread + QTimer 跨线程投递（不可靠）
修复方案：
  * 新增 _ConnectWorker / _EnrichWorker，统一走 QObject + moveToThread(QThread)
  * 点击后立刻「按钮禁用+改文字『连接中...』+ 状态列改『⏳ 连接中...』」
  * done 信号回调还原按钮、改状态列、弹窗（一次，绝不挂死）
  * adb connect 自带 timeout=10s，超过就 AdbError 通过信号汇报
"""
import os
import sys
import time
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
MAIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(MAIN))
sys.path.insert(0, os.path.join(os.path.abspath(MAIN), "dialogs"))

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt

import lan_scan_dialog as M


# ─── 桩：替代 subprocess.run ───────────────────────────────

class FakeRun:
    """替代 subprocess.run,所有调用都用可控返回 + 模拟延迟。"""
    def __init__(self):
        self.responses = {}     # cmd tuple -> str stdout
        self.delays = {}        # cmd tuple -> secs
        self.errors = {}        # cmd tuple -> Exception to raise
        self.calls = []         # history

    def __call__(self, cmd, **kwargs):
        key = tuple(cmd)
        self.calls.append(key)
        if key in self.errors:
            time.sleep(self.delays.get(key, 0.0))
            raise self.errors[key]
        time.sleep(self.delays.get(key, 0.0))
        output = self.responses.get(key, "")
        return subprocess.CompletedProcess(cmd, 0, output, "")


def install_fake_subprocess(monkey, adb_path="adb"):
    """把 局域网扫描对话框.subprocess 替换为可控 FakeRun。"""
    fake = FakeRun()
    # 关键:_ConnectWorker 用的是 局域网扫描对话框.subprocess 命名空间
    monkey.setattr(M.subprocess, "run", fake)
    # 同时让 AdbHelper.adb_path 是固定串,免得在没有 adb 的环境里失败
    fake.responses[(adb_path, 'connect', '192.168.1.16:5555')] = "connected to 192.168.1.16:5555"
    fake.responses[(adb_path, 'connect', '192.168.1.20:5555')] = "cannot connect to 192.168.1.20:5555: 由于目标计算机积极拒绝，无法连接。"
    return fake


def wait_ms(ms):
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def main():
    app = QApplication.instance() or QApplication(sys.argv)

    class _Monkey:
        def __init__(self):
            self._patches = []
        def setattr(self, mod, name, value):
            old = getattr(mod, name, None)
            self._patches.append((mod, name, old))
            setattr(mod, name, value)
        def undo(self):
            for mod, name, old in reversed(self._patches):
                if old is None:
                    try:
                        delattr(mod, name)
                    except Exception:
                        pass
                else:
                    setattr(mod, name, old)

    monkey = _Monkey()
    results = []

    # ── 1. 弹窗构造 + 注入假行 ──
    dlg = M.LanScannerDialog()
    dlg.show()
    QApplication.processEvents()
    fake_ips = ["192.168.1.16", "192.168.1.20"]
    dlg.table.setRowCount(0)
    for ip in fake_ips:
        dlg._on_device_found(ip, 12.0 + len(ip), None)
    QApplication.processEvents()
    btn = dlg.table.cellWidget(0, 3)
    assert isinstance(btn, QPushButton)
    assert btn.text() == "连接"
    results.append(("OK", f"注入两行 + 行 0 按钮初始文字『连接』"))

    # ── 2. patch subprocess.run ──
    fake = install_fake_subprocess(monkey)

    # ── 3. 点击连接：立即返回 + 文字变 + 状态列变 ──
    t0 = time.monotonic()
    btn.click()
    click_dt = time.monotonic() - t0
    assert click_dt < 0.5, f"点击卡死 {click_dt:.2f}s"
    QApplication.processEvents()
    assert btn.text() == "连接中...", f"按钮文字应为『连接中...』, 实为 {btn.text()!r}"
    assert not btn.isEnabled()
    st = dlg.table.item(0, 1)
    assert "连接中" in st.text(), f"状态列应为『连接中』, 实为 {st.text()!r}"
    results.append(("OK", f"点击立即返回 {click_dt*1000:.0f}ms,按钮禁用+『连接中...』"))

    # ── 4. 等 done 回调 ──
    waited = 0
    while waited < 3000 and btn.isEnabled() is False:
        wait_ms(50); waited += 50
    assert btn.isEnabled()
    assert btn.text() == "连接"
    assert "🟢" in dlg.table.item(0, 1).text(), \
        f"状态列应含 🟢, 实为 {dlg.table.item(0, 1).text()!r}"
    results.append(("OK", f"done 回调: 按钮恢复 enabled, 状态『{dlg.table.item(0,1).text()}』"))

    # ── 5. 失败路径 ──
    btn2 = dlg.table.cellWidget(1, 3)
    btn2.click()
    QApplication.processEvents()
    assert btn2.text() == "连接中..."
    waited = 0
    while waited < 3000 and btn2.text() != "连接":
        wait_ms(50); waited += 50
    assert btn2.text() == "连接"
    assert "❌" in dlg.table.item(1, 1).text()
    results.append(("OK", f"失败路径: 状态『{dlg.table.item(1,1).text()}』"))

    # ── 6. closeEvent 在后台 adb 卡住 3s 时仍能在 2~3s 内清理完毕 ──
    fake.delays[("adb", "connect", "192.168.1.16:5555")] = 3.0
    btn.click()  # 在已有 busy 状态下会被 ignore,先关闭 busy
    # 由于上一次成功完成,busy 已清空。可以再次点击。
    # 但等待 done 比较麻烦,直接 close 比较实际
    t0 = time.monotonic()
    dlg.close()
    QApplication.processEvents()
    close_dt = time.monotonic() - t0
    assert close_dt < 5.0, f"closeEvent 卡死 {close_dt:.2f}s"
    assert dlg._closing is True
    assert dlg._connect_threads == [], f"_connect_threads 未清空: {dlg._connect_threads}"
    results.append(("OK", f"closeEvent 后台 adb 慢时 {close_dt:.2f}s 内清理完"))

    monkey.undo()

    print("\n========== 局域网扫描「连接卡死」修复 验证结果 ==========")
    for status, msg in results:
        icon = "✅" if status == "OK" else "❌"
        print(f"  {icon} {msg}")
    print(f"\n共 {len(results)} 项全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
