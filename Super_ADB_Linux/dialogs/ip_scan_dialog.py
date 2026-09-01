# -*- coding: utf-8 -*-
"""
IP 局域网扫描弹窗
==================
点击主界面「便捷工具 → IP扫描」按钮弹出的独立窗口：
- 自动识别本机 IP 所在网段
- 多线程并发 ping 扫描 1-254
- 结果表格展示：IP 地址、状态、MAC 地址、备注（网关/本机/未知）
- 双击 IP 可复制到剪贴板
- 扫描在后台线程执行，不阻塞 UI

与时间戳/哈希校验弹窗同款样式：内层亮边卡片 + 主题色边框。
"""

import os
import sys
import socket
import subprocess
import threading
import ipaddress

from PySide6.QtCore import Qt, Signal, QObject
from PySide6.QtGui import QIcon, QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QWidget,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QLineEdit, QGroupBox, QAbstractItemView,
)

from ui import png_rc  # noqa: F401
from ui.ui_styles import get_stylesheet, get_current_theme_id, THEMES
from ui.dialog_styles import highlight_card_style, _create_popup_card, add_green_glow

# 打包为 -w 窗口程序后，子进程（ping/arp/ipconfig）若不隐藏窗口会逐个弹出 CMD 黑框；
# 与全项目约定一致：Windows 用 CREATE_NO_WINDOW，其他平台传 0（默认）。
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ----------------------------------------------------------------------
# 后台扫描工作器
# ----------------------------------------------------------------------
class _扫描工作器(QObject):
    """后台线程执行 ping 扫描，通过信号汇报进度和结果。"""
    进度 = Signal(int, int)          # 当前完成数, 总数
    发现设备 = Signal(str, str)      # IP, MAC地址(可能为空)
    完成 = Signal(int)                # 发现的设备总数
    出错 = Signal(str)

    def __init__(self, 网段: str, 超时ms: int = 500, 线程数: int = 64):
        super().__init__()
        self.网段 = 网段
        self.超时ms = 超时ms
        self.线程数 = 线程数
        self._取消 = False
        self._锁 = threading.Lock()
        self._已完成 = 0
        self._总数 = 0
        self._arp_cache = {}  # IP -> MAC

    def 取消(self):
        self._取消 = True

    def _加载arp缓存(self):
        """执行 arp -a 加载当前ARP缓存，用于获取已通信设备的MAC。"""
        try:
            r = subprocess.run(
                ['arp', '-a'],
                capture_output=True, text=True, encoding='gbk', errors='replace',
                timeout=5, creationflags=_NO_WINDOW,
            )
            for line in (r.stdout or '').splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    ip = parts[0].strip('()')
                    mac = parts[1]
                    if self._看起来像ip(ip) and '-' in mac:
                        self._arp_cache[ip] = mac
        except Exception:
            pass

    @staticmethod
    def _看起来像ip(s: str) -> bool:
        try:
            ipaddress.IPv4Address(s)
            return True
        except Exception:
            return False

    def _ping一个(self, ip: str) -> bool:
        """ping 单个IP，返回是否在线。"""
        try:
            if sys.platform == 'win32':
                r = subprocess.run(
                    ['ping', '-n', '1', '-w', str(self.超时ms), ip],
                    capture_output=True, timeout=3, creationflags=_NO_WINDOW,
                )
            else:
                r = subprocess.run(
                    ['ping', '-c', '1', '-W', str(max(1, self.超时ms // 1000)), ip],
                    capture_output=True, timeout=3, creationflags=_NO_WINDOW,
                )
            return r.returncode == 0
        except Exception:
            return False

    def _工作线程(self, ip_list):
        for ip in ip_list:
            if self._取消:
                return
            在线 = self._ping一个(ip)
            with self._锁:
                self._已完成 += 1
                当前 = self._已完成
            self.进度.emit(当前, self._总数)
            if 在线:
                mac = self._arp_cache.get(ip, '')
                self.发现设备.emit(ip, mac)

    def run(self):
        try:
            # 解析网段
            网络 = ipaddress.IPv4Network(self.网段, strict=False)
            ip_list = [str(ip) for ip in 网络.hosts()]
            self._总数 = len(ip_list)
            self._已完成 = 0

            # 先加载ARP缓存
            self._加载arp缓存()

            # 分批多线程扫描
            批次大小 = max(1, self.线程数)
            线程列表 = []
            for i in range(0, len(ip_list), 批次大小):
                批次 = ip_list[i:i + 批次大小]
                t = threading.Thread(target=self._工作线程, args=(批次,), daemon=True)
                线程列表.append(t)
                t.start()
            for t in 线程列表:
                t.join(timeout=10)

            # 扫描完成后再刷新一次ARP缓存（新发现的设备可能已加入）
            self._加载arp缓存()
            self.完成.emit(self._总数)
        except Exception as e:
            self.出错.emit(str(e))


# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class IP扫描对话框(QDialog):
    """IP 局域网扫描弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("IP 局域网扫描")
        self.setWindowIcon(QIcon(":/Super_ADB.png"))
        self.setMinimumWidth(560)
        self.resize(640, 480)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id))
        # 内层亮边卡片
        self.card, _ = _create_popup_card(self, self._theme_id)
        self._扫描线程 = None
        self._工作器 = None
        self._build_ui()
        self._自动识别网段()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self.card)
        root.setSpacing(10)
        root.setContentsMargins(16, 16, 16, 16)

        # ── 顶部：网段输入 + 扫描按钮 ──
        top = QHBoxLayout()
        top.setSpacing(8)
        lbl = QLabel("网段:")
        lbl.setFixedWidth(40)
        self.网段输入 = QLineEdit()
        self.网段输入.setPlaceholderText("如 192.168.1.0/24")
        self.网段输入.returnPressed.connect(self._开始扫描)
        self.扫描按钮 = QPushButton("开始扫描")
        self.扫描按钮.setFixedWidth(100)
        self.扫描按钮.clicked.connect(self._开始扫描)
        top.addWidget(lbl)
        top.addWidget(self.网段输入, 1)
        top.addWidget(self.扫描按钮)
        root.addLayout(top)

        # ── 本机信息 ──
        self.本机信息标签 = QLabel("")
        self.本机信息标签.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self.本机信息标签)

        # ── 进度条 ──
        self.进度条 = QProgressBar()
        self.进度条.setRange(0, 100)
        self.进度条.setValue(0)
        self.进度条.setTextVisible(True)
        self.进度条.setFormat("就绪")
        root.addWidget(self.进度条)

        # ── 结果表格 ──
        self.表格 = QTableWidget(0, 4)
        self.表格.setHorizontalHeaderLabels(["IP 地址", "状态", "MAC 地址", "备注"])
        self.表格.verticalHeader().setVisible(False)
        self.表格.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.表格.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.表格.setAlternatingRowColors(True)
        self.表格.doubleClicked.connect(self._双击复制)
        header = self.表格.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.表格, 1)

        # ── 底部：状态 + 复制全部 ──
        bottom = QHBoxLayout()
        self.状态标签 = QLabel("")
        self.状态标签.setStyleSheet("color: #888;")
        self.复制全部按钮 = QPushButton("复制全部IP")
        self.复制全部按钮.setFixedWidth(100)
        self.复制全部按钮.clicked.connect(self._复制全部)
        bottom.addWidget(self.状态标签, 1)
        bottom.addWidget(self.复制全部按钮)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # 主题切换
    # ------------------------------------------------------------------
    def apply_theme(self, theme_id):
        if theme_id not in THEMES or theme_id == self._theme_id:
            return
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        self.update()

    # ------------------------------------------------------------------
    # 自动识别网段
    # ------------------------------------------------------------------
    def _自动识别网段(self):
        try:
            # 获取本机所有IPv4地址
            本机ips = []
            if sys.platform == 'win32':
                r = subprocess.run(
                    ['ipconfig'], capture_output=True, text=True,
                    encoding='gbk', errors='replace', timeout=5,
                    creationflags=_NO_WINDOW,
                )
                for line in (r.stdout or '').splitlines():
                    if 'IPv4' in line or 'IPv4 地址' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            ip = parts[-1].strip()
                            if self._看起来像ip(ip) and not ip.startswith('169.254'):
                                本机ips.append(ip)
            else:
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
                    s.close()
                    if self._看起来像ip(ip):
                        本机ips.append(ip)
                except Exception:
                    pass

            if 本机ips:
                本机ip = 本机ips[0]
                # 推断 /24 网段
                parts = 本机ip.split('.')
                网段 = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                self.网段输入.setText(网段)
                self.本机信息标签.setText(f"本机 IP: {本机ip}  |  推断网段: {网段}")
            else:
                self.本机信息标签.setText("未能自动识别本机 IP，请手动输入网段")
        except Exception as e:
            self.本机信息标签.setText(f"识别网段失败: {e}，请手动输入")

    @staticmethod
    def _看起来像ip(s: str) -> bool:
        try:
            ipaddress.IPv4Address(s)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 扫描控制
    # ------------------------------------------------------------------
    def _开始扫描(self):
        网段 = self.网段输入.text().strip()
        if not 网段:
            self.状态标签.setText("请输入网段")
            return
        try:
            ipaddress.IPv4Network(网段, strict=False)
        except Exception:
            self.状态标签.setText("网段格式不正确，如 192.168.1.0/24")
            return

        # 清空表格
        self.表格.setRowCount(0)
        self.进度条.setValue(0)
        self.进度条.setFormat("扫描中... 0%")
        self.状态标签.setText("正在扫描...")
        self.扫描按钮.setEnabled(False)
        self.扫描按钮.setText("扫描中...")

        # 创建工作器和线程
        self._工作器 = _扫描工作器(网段, 超时ms=500, 线程数=64)
        self._扫描线程 = threading.Thread(target=self._工作器.run, daemon=True)
        self._工作器.进度.connect(self._on进度)
        self._工作器.发现设备.connect(self._on发现设备)
        self._工作器.完成.connect(self._on完成)
        self._工作器.出错.connect(self._on出错)
        self._扫描线程.start()

    def _on进度(self, 当前, 总数):
        if 总数 > 0:
            百分比 = int(当前 * 100 / 总数)
            self.进度条.setValue(百分比)
            self.进度条.setFormat(f"扫描中... {百分比}% ({当前}/{总数})")

    def _on发现设备(self, ip, mac):
        row = self.表格.rowCount()
        self.表格.insertRow(row)

        # IP
        item_ip = QTableWidgetItem(ip)
        item_ip.setData(Qt.ItemDataRole.UserRole, ip)
        self.表格.setItem(row, 0, item_ip)

        # 状态
        item_state = QTableWidgetItem("🟢 在线")
        item_state.setForeground(QColor(46, 204, 113))
        self.表格.setItem(row, 1, item_state)

        # MAC
        self.表格.setItem(row, 2, QTableWidgetItem(mac or "—"))

        # 备注
        备注 = self._推断备注(ip, mac)
        self.表格.setItem(row, 3, QTableWidgetItem(备注))

        self.状态标签.setText(f"已发现 {self.表格.rowCount()} 台在线设备")

    def _推断备注(self, ip, mac):
        """根据IP和MAC推断设备备注。"""
        parts = ip.split('.')
        if len(parts) == 4 and parts[3] == '1':
            return "网关 / 路由器"
        # 检查是否是本机
        try:
            本机信息 = self.本机信息标签.text()
            if ip in 本机信息:
                return "本机"
        except Exception:
            pass
        # MAC厂商前缀识别（常见）
        mac_prefix = mac.replace('-', ':').upper()[:8] if mac else ''
        厂商表 = {
            'C8:98:28': '中兴(ZTE)',
            'E4:27:61': '小米(Xiaomi)',
            'DC:A6:32': '树莓派(RPi)',
            'B8:27:EB': '树莓派(RPi)',
            'AC:DE:48': '群晖(Synology)',
            '00:11:32': '群晖(Synology)',
            'F0:9F:C2': 'Ubiquiti',
        }
        if mac_prefix in 厂商表:
            return 厂商表[mac_prefix]
        return "未知设备"

    def _on完成(self, 总数):
        self.进度条.setValue(100)
        self.进度条.setFormat(f"完成 (共扫描 {总数} 个IP)")
        self.扫描按钮.setEnabled(True)
        self.扫描按钮.setText("开始扫描")
        self.状态标签.setText(f"扫描完成，共发现 {self.表格.rowCount()} 台在线设备")

    def _on出错(self, err):
        self.状态标签.setText(f"扫描出错: {err}")
        self.扫描按钮.setEnabled(True)
        self.扫描按钮.setText("开始扫描")

    # ------------------------------------------------------------------
    # 复制功能
    # ------------------------------------------------------------------
    def _双击复制(self, index):
        item = self.表格.item(index.row(), 0)
        if item:
            ip = item.data(Qt.ItemDataRole.UserRole) or item.text()
            QGuiApplication.clipboard().setText(ip)
            self.状态标签.setText(f"已复制: {ip}")

    def _复制全部(self):
        ips = []
        for row in range(self.表格.rowCount()):
            item = self.表格.item(row, 0)
            if item:
                ip = item.data(Qt.ItemDataRole.UserRole) or item.text()
                ips.append(ip)
        if ips:
            QGuiApplication.clipboard().setText('\n'.join(ips))
            self.状态标签.setText(f"已复制 {len(ips)} 个IP到剪贴板")
        else:
            self.状态标签.setText("没有可复制的IP")

    # ------------------------------------------------------------------
    # 关闭时清理
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        if self._工作器:
            self._工作器.取消()
        event.accept()
