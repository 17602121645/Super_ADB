# -*- coding: utf-8 -*-
"""
主入口 Mixin：设备管理
====================
设备连接/断开/扫描/序列号获取等方法。
通过多继承混入 主窗口，可访问 self 的所有属性和方法。
"""
from tools.adb_tools import 格式化设备标签


class 设备管理Mixin:
    """设备管理方法集合。"""

    # ------------------------------------------------------------------
    # 设备管理
    # ------------------------------------------------------------------
    def 当前序列号(self):
        idx = self.deviceCombo.currentIndex()
        if idx < 0:
            return None
        return self.deviceCombo.itemData(idx)

    def _确保序列号(self):
        serial = self.当前序列号()
        if not serial:
            self.日志('请先选择或连接一个设备')
        return serial

    def 刷新设备(self):
        from app.main import 命令工作器
        self.设置状态('正在扫描设备…')
        worker = 命令工作器(self.adb.获取设备列表)
        worker.signals.result.connect(self._设备加载完成时)
        worker.signals.error.connect(lambda e: self.设置状态(f'扫描失败: {e}'))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _设备加载完成时(self, devices):
        online = [d for d in devices if d.get('state') == 'device']
        # 选中优先级：刚连上的设备 > 原选中设备
        select = self._pending_select_serial
        self._pending_select_serial = None
        if select is None:
            select = self.当前序列号()
        self.deviceCombo.blockSignals(True)
        self.deviceCombo.clear()
        for d in online:
            self.deviceCombo.addItem(格式化设备标签(d), d.get('serial'))
        idx = self.deviceCombo.findData(select) if select else -1
        if idx >= 0:
            self.deviceCombo.setCurrentIndex(idx)
        self.deviceCombo.blockSignals(False)
        self.设置状态(f'已连接 {len(online)} 台设备', ok=len(online) > 0)
        # 同步文件管理器与日志页的设备下拉框（传入过滤后的在线设备）
        if getattr(self, 'file_mgr', None) is not None:
            self.file_mgr.sync_devices(online, select)
        if getattr(self, 'log_viewer', None) is not None:
            self.log_viewer.sync_devices(online, select)
        # 同步 ADB 终端弹窗（自研模式专属，弹窗可能已打开）
        if getattr(self, '_adb_终端_dialog', None) is not None and self._adb_终端_dialog.isVisible():
            self._adb_终端_dialog.sync_devices(devices, select)

    def 连接设备(self):
        from app.main import 命令工作器
        ip = self.ipInput.text().strip()
        if not ip:
            self.日志('请输入设备 IP')
            return
        # 记录目标 serial（与 adb connect 一致：缺端口自动补 :5555）
        target = ip if ':' in ip else f'{ip}:5555'
        self._pending_select_serial = target
        self.设置状态(f'正在连接 {ip}…')
        worker = 命令工作器(self.adb.连接设备, ip)
        worker.signals.result.connect(self._连接完成时)
        worker.signals.error.connect(lambda e: self.设置状态(f'连接失败: {e}'))
        worker.signals.finished.connect(lambda: self._丢弃工作器(worker))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _连接完成时(self, result):
        self.日志(str(result))
        # 连接命令返回后重新扫描，让三处下拉框加载到新设备
        self.刷新设备()

    def 断开设备(self):
        from app.main import 命令工作器
        serial = self.当前序列号()
        if serial:
            worker = 命令工作器(self.adb.断开设备, serial)
        else:
            worker = 命令工作器(self.adb.断开设备)
        worker.signals.result.connect(self._断开完成时)
        worker.signals.error.connect(lambda e: self.设置状态(f'断开失败: {e}'))
        worker.signals.finished.connect(lambda: self._丢弃工作器(worker))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _断开完成时(self, result):
        self.日志(str(result))
        # 断开命令返回后重新扫描，让三处下拉框移除已断开设备
        self.刷新设备()
