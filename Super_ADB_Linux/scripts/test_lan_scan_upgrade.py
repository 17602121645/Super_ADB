# -*- coding: utf-8 -*-
"""局域网扫描升级项离线验证（offscreen）。

验证：
  1. 端口 SpinBox 存在、默认 5555，_on_port_changed 同步 _port + 提示文案
  2. _resort_by_latency 按延迟升序重建表格并同步 _found_ips
  3. _apply_enrich 在 _closing=False 时回填机型名
  4. _closing=True 时 _on_scan_finished/_on_scan_stopped 早退不碰 widget
  5. closeEvent 在无线程运行时安全退出
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
MAIN = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(MAIN))
sys.path.insert(0, os.path.join(os.path.abspath(MAIN), "dialogs"))

from PySide6.QtWidgets import QApplication, QTableWidgetItem
from PySide6.QtGui import QCloseEvent
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

import lan_scan_dialog as M

checks = []


def check(cond, msg):
    checks.append((bool(cond), msg))
    print(("✅" if cond else "❌"), msg)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = M.LanScannerDialog()

    # ── 1. 端口 SpinBox ──
    check(hasattr(dlg, "port_spin"), "端口 SpinBox 存在")
    check(dlg._port == 5555, "初始化 _port = 5555")
    check(dlg.port_spin.value() == 5555, "端口 SpinBox 默认 5555")
    dlg._on_port_changed(6666)
    check(dlg._port == 6666, "_on_port_changed 同步 _port=6666")
    check("6666" in dlg.hint_label.text(), "提示文案同步显示端口 6666")
    dlg._on_port_changed(5555)  # 还原

    # ── 1b. 表格列宽与按钮尺寸（UI 收紧） ──
    from PySide6.QtWidgets import QHeaderView
    check(dlg.table.columnWidth(0) == 200, "IP 列默认 200px")
    check(dlg.table.columnWidth(3) == 130, "操作列 130px（容纳按钮）")
    check(dlg.table.horizontalHeader().sectionResizeMode(0) == QHeaderView.Interactive,
          "IP 列是 Interactive 而非 Stretch")
    check(dlg.table.horizontalHeader().sectionResizeMode(1) == QHeaderView.Stretch,
          "状态列是 Stretch（吃掉剩余空间，填满表格）")
    btn = dlg._make_connect_btn("10.0.0.99")
    check(btn.minimumWidth() == 80, "连接按钮 minWidth=80")
    check(btn.height() == 28 or btn.minimumHeight() == 28,
          "连接按钮高度 28")

    # ── 2. 结果按延迟排序 ──
    dlg.table.setRowCount(0)
    dlg._found_ips = []
    sample = [("10.0.0.3", 8.7), ("10.0.0.1", 3.2), ("10.0.0.2", 41.0)]
    for ip, lat in sample:
        r = dlg.table.rowCount()
        dlg.table.insertRow(r)
        dlg.table.setItem(r, 0, QTableWidgetItem(ip))
        st = QTableWidgetItem("🟢 在线")
        st.setForeground(QColor("#00CC66"))
        st.setTextAlignment(Qt.AlignCenter)
        dlg.table.setItem(r, 1, st)
        li = QTableWidgetItem(f"{lat}")
        li.setTextAlignment(Qt.AlignCenter)
        dlg.table.setItem(r, 2, li)
        dlg._found_ips.append(ip)
    dlg._resort_by_latency()
    ips_after = [dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())]
    lats_after = [float(dlg.table.item(r, 2).text()) for r in range(dlg.table.rowCount())]
    check(ips_after == ["10.0.0.1", "10.0.0.3", "10.0.0.2"], f"排序后 IP 顺序={ips_after}")
    check(lats_after == sorted(lats_after), f"排序后延迟升序={lats_after}")
    check(dlg._found_ips == ips_after, "_found_ips 与表格顺序同步")

    # ── 3. 状态列手动设置（新版走 _enrich_after_connect 异步线程,这里直接喂状态） ──
    dlg._set_status_for_ip("10.0.0.1", "🟢 在线 · Pixel 7", M.ACCENT_COLOR_GREEN)
    st_txt = dlg.table.item(0, 1).text() if dlg.table.item(0, 1) else dlg.table.item(0, 1)
    check(dlg.table.item(0, 1).text() == "🟢 在线 · Pixel 7",
          f"状态列回填成功: {dlg.table.item(0, 1).text()}")

    # ── 4. _closing 守卫 ──
    dlg._closing = True
    try:
        dlg._on_scan_finished([("1.2.3.4", 1.0)])
        dlg._on_scan_stopped()
        guard_ok = True
    except Exception as e:  # noqa
        guard_ok = False
        print("   guard exception:", repr(e))
    check(guard_ok, "_closing=True 时回调早退无异常")
    dlg._closing = False

    # ── 5. closeEvent 无运行时安全 ──
    try:
        dlg.closeEvent(QCloseEvent())
        close_ok = True
    except Exception as e:  # noqa
        close_ok = False
        print("   closeEvent exception:", repr(e))
    check(close_ok, "closeEvent 在无线程运行时安全退出")

    # 汇总
    passed = sum(1 for c, _ in checks if c)
    print(f"\n通过 {passed}/{len(checks)}")
    failed = [m for c, m in checks if not c]
    if failed:
        print("失败项：")
        for m in failed:
            print("  -", m)
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
