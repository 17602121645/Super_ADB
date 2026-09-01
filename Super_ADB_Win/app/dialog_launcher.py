# -*- coding: utf-8 -*-
"""
主入口 Mixin：弹窗打开
====================
所有 open_xxx 方法，负责创建并显示各类dialogs/窗口。
通过多继承混入 主窗口，可访问 self 的所有属性和方法。
"""
import sys

from ui.ui_styles import get_stylesheet


class 弹窗打开Mixin:
    """弹窗打开方法集合。"""

    # ------------------------------------------------------------------
    # 设备性能监控
    # ------------------------------------------------------------------
    def 打开性能监控(self):
        """打开设备性能监控独立窗口 (重复点击复用已开窗口)。"""
        serial = self._确保序列号()
        if not serial:
            return
        if self._dpm_window is not None and self._dpm_window.isVisible():
            self._dpm_window.raise_()
            self._dpm_window.activateWindow()
            return
        from monitoring.device_performance_monitor import 设备性能监控
        self._dpm_window = 设备性能监控(serial)
        self._dpm_window.show()

    # ------------------------------------------------------------------
    # Monkey 压力测试
    # ------------------------------------------------------------------
    def 打开monkey压测(self):
        """打开 Monkey 压测配置窗口 (重复点击复用已开窗口)。"""
        serial = self._确保序列号()
        if not serial:
            return
        if self._monkey_window is not None and self._monkey_window.isVisible():
            self._monkey_window.raise_()
            self._monkey_window.activateWindow()
            return
        # 默认带入主窗口已填的包名
        default_pkg = self.pkgInput.text().strip()
        from dialogs.monkey_stress_window import Monkey压测窗口
        self._monkey_window = Monkey压测窗口(
            serial, default_pkg=default_pkg)
        self._monkey_window.show()

    # ------------------------------------------------------------------
    # 应用性能监控
    # ------------------------------------------------------------------
    def 打开应用监控(self):
        """打开应用性能监控独立窗口 (重复点击复用已开窗口)。"""
        serial = self._确保序列号()
        if not serial:
            return
        pkg = self._包名()
        if not pkg:
            self.日志('请先在包名输入框填写要监控的包名')
            return
        if self._app_monitor_window is not None and self._app_monitor_window.isVisible():
            self._app_monitor_window.raise_()
            self._app_monitor_window.activateWindow()
            return
        from monitoring.app_performance_monitor import 应用性能监控
        self._app_monitor_window = 应用性能监控(serial, pkg)
        self._app_monitor_window.show()

    # ------------------------------------------------------------------
    # 安装 / 解包
    # ------------------------------------------------------------------
    def 打开安装对话框(self):
        """打开 安装/解包 弹窗（拖入 APK/ZIP 查看内容并执行 adb install）。"""
        if self._install_dialog is not None and self._install_dialog.isVisible():
            self._install_dialog.raise_()
            self._install_dialog.activateWindow()
            return
        from dialogs.install_unpack_dialog import 安装解包对话框
        self._install_dialog = 安装解包对话框(
            self.adb, self.当前序列号)
        self._install_dialog.show()

    def 打开证书安装对话框(self):
        """打开 证书安装 弹窗（拖拽证书 → 检查权限 → 计算哈希 → 推送 → chmod）。"""
        if getattr(self, '_cert_dialog', None) is not None and self._cert_dialog.isVisible():
            self._cert_dialog.raise_()
            self._cert_dialog.activateWindow()
            return
        from dialogs.cert_install_dialog import 证书安装对话框
        self._cert_dialog = 证书安装对话框(
            self.adb, self.当前序列号)
        self._cert_dialog.show()

    def 打开命令行(self):
        """打开命令行。
        - 自研 ADB 模式：打开 ADB 交互式终端弹窗
        - 其他模式：打开系统 PowerShell（Windows）/ Terminal（macOS, Linux）
        任何异常都打到输出框 + 状态栏, 不弹窗骚扰。"""
        # 自研 ADB 模式：打开交互式终端弹窗
        if getattr(self.adb, '_用自研adb', False):
            try:
                if (self._adb_终端_dialog is not None
                        and self._adb_终端_dialog.isVisible()):
                    self._adb_终端_dialog.raise_()
                    self._adb_终端_dialog.activateWindow()
                    return
                from dialogs.adb_terminal_dialog import ADB终端对话框
                self._adb_终端_dialog = ADB终端对话框(self)
                # 弹窗内设备切换 → 同步主窗口三个设备选择栏
                self._adb_终端_dialog.设备已切换.connect(self._终端弹窗设备切换)
                self._adb_终端_dialog.show()
                return
            except Exception as e:
                err = f'打开 ADB 终端失败：{e}'
                self.设置状态(err, ok=False)
                self.日志(f'错误: {err}')
                return

        # 非自研模式：原功能，打开系统命令行
        import subprocess
        import shutil as _shutil
        try:
            if sys.platform.startswith('win'):
                CREATE_NEW_CONSOLE = getattr(subprocess, 'CREATE_NEW_CONSOLE', 0)
                subprocess.Popen(
                    ['powershell', '-NoExit'],
                    creationflags=CREATE_NEW_CONSOLE,
                )
                msg = '已打开 PowerShell'
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', '-a', 'Terminal'])
                msg = '已打开 Terminal'
            else:
                terminal = next(
                    (t for t in ('gnome-terminal', 'konsole',
                                 'xfce4-terminal', 'xterm')
                     if _shutil.which(t)),
                    None,
                )
                if not terminal:
                    raise OSError('未找到可用的终端模拟器'
                                  '（gnome-terminal / konsole / xfce4-terminal / xterm）')
                subprocess.Popen([terminal])
                msg = f'已打开 {terminal}'
            self.设置状态(msg, ok=True)
            self.日志(msg)
        except Exception as e:
            err = f'启动命令行失败：{e}'
            self.设置状态(err, ok=False)
            self.日志(f'错误: {err}')

    def _终端弹窗设备切换(self, serial):
        """终端弹窗内设备切换 → 同步主窗口三个设备选择栏（只选中，不清空列表）。"""
        try:
            # 更新主设备下拉框（blockSignals 避免递归）
            idx = self.deviceCombo.findData(serial)
            if idx >= 0:
                self.deviceCombo.blockSignals(True)
                self.deviceCombo.setCurrentIndex(idx)
                self.deviceCombo.blockSignals(False)
            # 同步文件管理器：在已有列表中选中目标设备
            if getattr(self, 'file_mgr', None) is not None:
                fidx = self.file_mgr.device_combo.findData(serial)
                if fidx >= 0:
                    self.file_mgr.device_combo.blockSignals(True)
                    self.file_mgr.device_combo.setCurrentIndex(fidx)
                    self.file_mgr.device_combo.blockSignals(False)
            # 同步日志查看器
            if getattr(self, 'log_viewer', None) is not None:
                lidx = self.log_viewer.device_combo.findData(serial)
                if lidx >= 0:
                    self.log_viewer.device_combo.blockSignals(True)
                    self.log_viewer.device_combo.setCurrentIndex(lidx)
                    self.log_viewer.device_combo.blockSignals(False)
        except Exception:
            pass

    def _设备手动切换(self, source_combo):
        """用户在三个主设备下拉框中任意一处切换 → 同步其它两处 + ADB终端弹窗。"""
        if getattr(self, '_syncing_device', False):
            return
        serial = source_combo.currentData()
        if not serial:
            return
        self._syncing_device = True
        try:
            # 同步另外两个主下拉框
            for combo in (self.deviceCombo, self.fileMgr_deviceCombo, self.logViewer_deviceCombo):
                if combo is not source_combo:
                    idx = combo.findData(serial)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            # 同步 ADB 终端弹窗（自研模式专属，弹窗可能已打开）
            if getattr(self, '_adb_终端_dialog', None) is not None and self._adb_终端_dialog.isVisible():
                tidx = self._adb_终端_dialog.device_combo.findData(serial)
                if tidx >= 0 and tidx != self._adb_终端_dialog.device_combo.currentIndex():
                    self._adb_终端_dialog.device_combo.blockSignals(True)
                    self._adb_终端_dialog.device_combo.setCurrentIndex(tidx)
                    self._adb_终端_dialog.device_combo.blockSignals(False)
                    # 触发终端连接切换
                    self._adb_终端_dialog._连接终端(serial)
        except Exception:
            pass
        finally:
            self._syncing_device = False

    def 打开json工具(self):
        """打开 JSON 工具弹窗（复用窗口，重复点击 raise）。"""
        if (self._json_tool_dialog is not None
                and self._json_tool_dialog.isVisible()):
            self._json_tool_dialog.raise_()
            self._json_tool_dialog.activateWindow()
            return
        from dialogs.json_tool_dialog import Json工具对话框
        self._json_tool_dialog = Json工具对话框()
        self._json_tool_dialog.show()

    def 打开md5校验(self):
        """打开 MD5 校验弹窗（复用窗口，重复点击 raise）。"""
        if self._md5_dialog is not None and self._md5_dialog.isVisible():
            self._md5_dialog.raise_()
            self._md5_dialog.activateWindow()
            return
        from dialogs.hash_check_dialog import 哈希校验对话框
        self._md5_dialog = 哈希校验对话框()
        self._md5_dialog.show()

    def 打开时间戳(self):
        """打开时间戳转换弹窗（复用窗口，重复点击 raise）。"""
        if self._timestamp_dialog is not None and self._timestamp_dialog.isVisible():
            self._timestamp_dialog.raise_()
            self._timestamp_dialog.activateWindow()
            return
        from dialogs.timestamp_dialog import 时间戳对话框
        self._timestamp_dialog = 时间戳对话框()
        self._timestamp_dialog.show()

    def 打开无线调试(self):
        """打开统一无线调试面板（局域网扫描 + WiFi 配对码连接，复用窗口，重复点击 raise）。"""
        if self._wireless_debug_dialog is not None and self._wireless_debug_dialog.isVisible():
            self._wireless_debug_dialog.raise_()
            self._wireless_debug_dialog.activateWindow()
            return

        def _配对成功时(ip, port):
            # 配对成功后刷新设备列表，并把当前 IP:端口 填到主窗口输入框方便下一步 connect
            if ip:
                self.ipInput.setText(f'{ip}:{port}')
            self.刷新设备()

        def _设备连接时(serial):
            # 局域网扫描里「adb connect 成功」后：把刚连上的设备设为期望选中项，
            # 触发一次刷新——主窗口 + 文件管理页 + 日志页的三处下拉框会同步更新。
            if serial:
                self._pending_select_serial = serial
            self.刷新设备()

        from dialogs.wireless_debug_dialog import 无线调试对话框
        self._wireless_debug_dialog = 无线调试对话框(
            on_pair_success=_配对成功时,
            on_device_connected=_设备连接时,
            adb=self.adb)
        # 与关于/环境配置弹窗一致：创建后立即应用当前主题，确保边框/背景/tab 样式
        # 首次显示就与主题一致（__init__ 已按主题初始化，此处双重保险并触发子页同步）
        self._wireless_debug_dialog.apply_theme(self._current_theme)
        self._wireless_debug_dialog.show()

    def 打开wifi(self):
        """打开本机 WiFi 密码查看弹窗（复用窗口，重复点击 raise）。"""
        if self._wifi_dialog is not None and self._wifi_dialog.isVisible():
            self._wifi_dialog.raise_()
            self._wifi_dialog.activateWindow()
            return
        from dialogs.wifi_dialog import WiFi对话框
        self._wifi_dialog = WiFi对话框()
        self._wifi_dialog.show()

    def 打开tcpdump对话框(self):
        """打开 tcpdump 抓包弹窗（复用窗口，重复点击 raise）。"""
        if self._tcpdump_dialog is not None and self._tcpdump_dialog.isVisible():
            self._tcpdump_dialog.raise_()
            self._tcpdump_dialog.activateWindow()
            return
        serial = self._确保序列号()
        if not serial:
            self.设置状态('请先选择设备', ok=False)
            return
        from dialogs.tcpdump_dialog import Tcpdump对话框
        # 独立窗口，不绑定 parent，与主页可自由切换前后层级。
        # 传入主窗口的 self.adb（已与自研adb 类级缓存共享 client），
        # 对话框内部优先复用，避免 new AdbHelper 造成独立实例建连。
        self._tcpdump_dialog = Tcpdump对话框(serial, adb=self.adb)
        self._tcpdump_dialog.show()

    def 打开关于对话框(self):
        """打开关于弹窗：复用同一窗口实例，支持运行时切换主题。"""
        from dialogs.about_dialog import 关于对话框
        dlg = self._about_dialog
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
            except RuntimeError:
                # C++ 端已被销毁，安全回落到重建
                self._about_dialog = None
                dlg = None
        dlg = 关于对话框()
        dlg.setStyleSheet(get_stylesheet(self._current_theme))
        dlg.apply_theme(self._current_theme)
        # 关闭（accept/reject/destroy）后释放引用，避免持有 Qt 已删对象
        dlg.destroyed.connect(lambda _obj=None, _self=self: setattr(_self, '_about_dialog', None))
        self._about_dialog = dlg
        dlg.show()

    def 打开环境配置对话框(self):
        """打开环境配置弹窗：复用同一窗口实例，支持运行时切换主题。"""
        from dialogs.env_config_dialog import 环境配置对话框
        dlg = self._env_config_dialog
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
            except RuntimeError:
                self._env_config_dialog = None
                dlg = None
        dlg = 环境配置对话框()
        dlg.setStyleSheet(get_stylesheet(self._current_theme))
        dlg.apply_theme(self._current_theme)
        # 设置变更时热更新 adb 实例配置并刷新设备列表（无需重启程序）
        dlg.设置变更.connect(self._on_adb_settings_changed)
        dlg.destroyed.connect(lambda _obj=None, _self=self: setattr(_self, '_env_config_dialog', None))
        self._env_config_dialog = dlg
        dlg.show()

    def 打开pcap解析器(self):
        """打开 PCAP 解析器独立窗口，支持拖拽 pcap 文件。"""
        dlg = self._pcap_parser_dialog
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
            except RuntimeError:
                self._pcap_parser_dialog = None
                dlg = None
        from dialogs.pcap_parse_dialog import Pcap解析对话框
        dlg = Pcap解析对话框()  # 独立窗口，不绑定 parent，点击主界面时可正常前置
        dlg.destroyed.connect(lambda _obj=None, _self=self: setattr(_self, '_pcap_parser_dialog', None))
        self._pcap_parser_dialog = dlg
        dlg.show()

    def 打开ip扫描(self):
        """打开 IP 局域网扫描弹窗（复用窗口，重复点击 raise）。"""
        if self._ip_scan_dialog is not None:
            try:
                if self._ip_scan_dialog.isVisible():
                    self._ip_scan_dialog.raise_()
                    self._ip_scan_dialog.activateWindow()
                    return
            except RuntimeError:
                self._ip_scan_dialog = None
        from dialogs.ip_scan_dialog import IP扫描对话框
        self._ip_scan_dialog = IP扫描对话框()
        self._ip_scan_dialog.destroyed.connect(
            lambda _obj=None, _self=self: setattr(_self, '_ip_scan_dialog', None))
        self._ip_scan_dialog.show()

    def _on_adb_settings_changed(self):
        """环境配置对话框中 ADB 设置（socket_direct / self_built）变更时触发。"""
        if hasattr(self, 'adb') and self.adb is not None:
            self.adb.刷新设置()
        self._更新命令行按钮文字()
        self.刷新设备()

    def _更新命令行按钮文字(self):
        """根据当前 ADB 模式更新便捷工具中「命令行」按钮文字。
        自研 ADB 模式 → ADB命令行；其它模式 → 命令行。"""
        try:
            用自研 = getattr(self.adb, '_用自研adb', False)
            if hasattr(self, 'cmdBtn'):
                self.cmdBtn.setText('ADB命令行' if 用自研 else '命令行')
        except Exception:
            pass
