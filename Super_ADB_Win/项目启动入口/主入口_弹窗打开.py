# -*- coding: utf-8 -*-
"""
主入口 Mixin：弹窗打开
====================
所有 open_xxx 方法，负责创建并显示各类对话框/窗口。
通过多继承混入 主窗口，可访问 self 的所有属性和方法。
"""
import sys

from 项目UI.界面样式 import get_stylesheet


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
        from 监控.设备性能监控 import 设备性能监控
        self._dpm_window = 设备性能监控(serial, parent=self)
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
        from 对话框.Monkey压测窗口 import Monkey压测窗口
        self._monkey_window = Monkey压测窗口(
            serial, default_pkg=default_pkg, parent=self)
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
        from 监控.应用性能监控 import 应用性能监控
        self._app_monitor_window = 应用性能监控(serial, pkg, parent=self)
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
        from 对话框.安装解包对话框 import 安装解包对话框
        self._install_dialog = 安装解包对话框(
            self.adb, self.当前序列号, parent=self)
        self._install_dialog.show()

    def 打开证书安装对话框(self):
        """打开 证书安装 弹窗（拖拽证书 → 检查权限 → 计算哈希 → 推送 → chmod）。"""
        if getattr(self, '_cert_dialog', None) is not None and self._cert_dialog.isVisible():
            self._cert_dialog.raise_()
            self._cert_dialog.activateWindow()
            return
        from 对话框.证书安装对话框 import 证书安装对话框
        self._cert_dialog = 证书安装对话框(
            self.adb, self.当前序列号, parent=self)
        self._cert_dialog.show()

    def 打开命令行(self):
        """打开系统命令行（独立新窗口，不阻塞主 UI）。
        - Windows: PowerShell（新控制台窗口，-NoExit 保持打开）
        - macOS:   Terminal.app
        - Linux:   按顺序探测 gnome-terminal / konsole / xfce4-terminal / xterm
        任何异常都打到输出框 + 状态栏, 不弹窗骚扰。"""
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

    def 打开json工具(self):
        """打开 JSON 工具弹窗（复用窗口，重复点击 raise）。"""
        if (self._json_tool_dialog is not None
                and self._json_tool_dialog.isVisible()):
            self._json_tool_dialog.raise_()
            self._json_tool_dialog.activateWindow()
            return
        from 对话框.JSON工具对话框 import Json工具对话框
        self._json_tool_dialog = Json工具对话框(parent=self)
        self._json_tool_dialog.show()

    def 打开md5校验(self):
        """打开 MD5 校验弹窗（复用窗口，重复点击 raise）。"""
        if self._md5_dialog is not None and self._md5_dialog.isVisible():
            self._md5_dialog.raise_()
            self._md5_dialog.activateWindow()
            return
        from 对话框.哈希校验对话框 import 哈希校验对话框
        self._md5_dialog = 哈希校验对话框(parent=self)
        self._md5_dialog.show()

    def 打开时间戳(self):
        """打开时间戳转换弹窗（复用窗口，重复点击 raise）。"""
        if self._timestamp_dialog is not None and self._timestamp_dialog.isVisible():
            self._timestamp_dialog.raise_()
            self._timestamp_dialog.activateWindow()
            return
        from 对话框.时间戳对话框 import 时间戳对话框
        self._timestamp_dialog = 时间戳对话框(parent=self)
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

        from 对话框.无线调试对话框 import 无线调试对话框
        self._wireless_debug_dialog = 无线调试对话框(
            parent=self,
            on_pair_success=_配对成功时,
            on_device_connected=_设备连接时)
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
        from 对话框.WiFi对话框 import WiFi对话框
        self._wifi_dialog = WiFi对话框(parent=self)
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
        from 对话框.TCPDump对话框 import Tcpdump对话框
        self._tcpdump_dialog = Tcpdump对话框(serial, parent=self)
        self._tcpdump_dialog.show()

    def 打开关于对话框(self):
        """打开关于弹窗：复用同一窗口实例，支持运行时切换主题。"""
        from 对话框.关于对话框 import 关于对话框
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
        dlg = 关于对话框(parent=self)
        dlg.setStyleSheet(get_stylesheet(self._current_theme))
        dlg.apply_theme(self._current_theme)
        # 关闭（accept/reject/destroy）后释放引用，避免持有 Qt 已删对象
        dlg.destroyed.connect(lambda _obj=None, _self=self: setattr(_self, '_about_dialog', None))
        self._about_dialog = dlg
        dlg.show()

    def 打开环境配置对话框(self):
        """打开环境配置弹窗：复用同一窗口实例，支持运行时切换主题。"""
        from 对话框.环境配置对话框 import 环境配置对话框
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
        dlg = 环境配置对话框(parent=self)
        dlg.setStyleSheet(get_stylesheet(self._current_theme))
        dlg.apply_theme(self._current_theme)
        dlg.destroyed.connect(lambda _obj=None, _self=self: setattr(_self, '_env_config_dialog', None))
        self._env_config_dialog = dlg
        dlg.show()
