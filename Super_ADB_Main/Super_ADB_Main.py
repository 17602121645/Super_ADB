# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— 主入口
==============================
整合常用 ADB 快捷命令、文件管理器、日志查看器于一体。
UI 布局由 Super_ADB.ui 定义，通过 Ui_MainWindow 驱动。
Super_ADB
# -*- coding: UTF-8 -*-
@author:JCS
@time:2022/11/26
"""

import re
import socket
import os
import sys
import threading
import time

# 确保直接运行时也能找到同目录模块及子目录模块
_here = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)
for _sub in ('dialogs', 'pages', 'monitors', 'utils'):
    _sub_dir = __import__('os').path.join(_here, _sub)
    if _sub_dir not in sys.path and __import__('os').path.isdir(_sub_dir):
        sys.path.insert(0, _sub_dir)

try:
    from PySide6.QtCore import (Qt, QThreadPool, QRunnable, Signal, QObject,
                                QMetaObject, Q_ARG, QTimer, QEvent, QRect, QPoint,
                                QTranslator, QLocale, QByteArray)
    from PySide6.QtGui import (QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPen)
    from PySide6.QtWidgets import (
        QApplication, QWidget, QPushButton, QTextEdit, QPlainTextEdit,
        QMessageBox, QStatusBar, QSystemTrayIcon, QMenu, QLayout,
        QListView, QAbstractSpinBox, QScrollBar, QComboBox,
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    )
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
except ImportError as e:
    print(f'错误: 未找到 PySide6 ({e})')
    print('请使用已安装 PySide6 的 Python 运行本工具，例如：')
    print('  D:/Python/Python314/python.exe Super_ADB_Main.py')
    sys.exit(1)

# 投屏参数设置对话框（同目录模块，已由上面的 sys.path 注入包含）
import scrcpy_settings_dialog

from Super_ADB import Ui_MainWindow
from adb_utils import AdbDeviceOps, format_device_label, load_json_config, save_json_config
from 界面样式 import (STYLE_SHEET, FONT_FAMILY, get_stylesheet, get_theme_ids,
                      get_theme_name, DEFAULT_THEME, THEMES)

# 注册 png_rc 资源（含应用图标 :/Super_ADB.png 与公众号二维码），import 即执行 qInitResources()
import png_rc  # noqa: F401

from file_manager_page import FileManagerPage
from log_viewer_page import LogViewerPage
from desk_cat import create_desk_cat
# 以下重型子模块改在「用到时才 import」（见各 open_xxx 方法内的局部 import），
# 避免启动即加载 app_perf_monitor(3400+ 行) 与全部 dialog 模块，降低启动内存。
from popup_style import HIGHLIGHT_CARD_STYLE, add_green_glow, ACCENT_CSS

CONFIG_NAME = 'adb_shell_config.json'
# 首次启动 / 配置缺失或损坏时的默认窗口几何
DEFAULT_GEOMETRY = {'x': 71, 'y': 126, 'w': 1400, 'h': 780}
# 首次启动默认几何（saveGeometry 的 base64 编码，源自 adb_shell_config.json 的 geometry.b64，
# 含位置/大小/窗口状态；配置文件缺失时优先使用它，比 DEFAULT_GEOMETRY 更精确）
DEFAULT_GEOMETRY_B64 = 'AdnQywADAAAAAAQaAAAAWwAABksAAAOwAAAEGgAAAFsAAAZLAAADsAAAAAAAAAAABkAAAAQaAAAAWwAABksAAAOw'
# 配置文件中主题字段的 key
THEME_CONFIG_KEY = 'theme'


# ----------------------------------------------------------------------
# 后台 Worker
# ----------------------------------------------------------------------
class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class CmdWorker(QRunnable):
    """后台执行返回字符串的函数，并通过信号回传。"""

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class _TextSender(QObject):
    """后台执行「输入文本」发送逻辑，避免主线程 time.sleep / 同步 adb 调用阻塞 UI。

    - progress/done 信号回写 info_label / status_bar（主线程）。
    - logmsg 信号转发到主线程日志面板（self.log 内部用 QueuedConnection，线程安全）。
    - 对 Qt 剪贴板的「读取」由调用方在主线程完成并传入 old_text；本类内写/恢复
      剪贴板统一用 ctypes 直接操作 Win32 API，不混用 Qt clipboard，可在后台线程安全执行。
    """

    progress = Signal(str)              # 更新 info_label 文本
    done = Signal(bool, str, str)       # (ok, status_text, info_text)
    logmsg = Signal(str)                # 转发到主线程日志面板

    def __init__(self, adb, serial, text, old_text, adbkb_installed):
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.text = text
        self.old_text = old_text
        self.adbkb_installed = adbkb_installed  # 可变 list 引用，与调用方共享

    def run(self):
        try:
            self._do_work()
        except Exception as e:
            self.logmsg.emit(f'发送异常: {e}')
            self.done.emit(False, '中文输入失败', f'✗ 发送异常: {e}')

    def _do_work(self):
        has_non_ascii = any(ord(c) >= 128 for c in self.text)
        if not has_non_ascii:
            self._send_ascii()
        else:
            self._send_non_ascii()

    def _send_ascii(self):
        lines = self.text.split('\n')
        ok_count = 0
        for i, line in enumerate(lines):
            if i > 0:
                try:
                    self.adb.run_shell(self.serial, 'input keyevent 66',
                                       timeout=5)
                except Exception as e:
                    self.logmsg.emit(f'发送回车失败: {e}')
            if not line:
                continue
            safe = line.replace('\\', '\\\\').replace('"', '\\"')
            try:
                self.adb.run_shell(self.serial, f'input text "{safe}"',
                                   timeout=10)
                ok_count += 1
            except Exception as e:
                self.logmsg.emit(f'输入文本失败: {e}')
                break
        self.done.emit(True, f'已发送 {ok_count} 行 ASCII 文本',
                       f'✓ ASCII → input text ({ok_count} 行)')

    def _send_non_ascii(self):
        self.progress.emit('尝试 Win32 剪贴板粘贴…')
        if self._send_text_via_native_clipboard(self.serial, self.text,
                                                self.old_text):
            line_count = self.text.count('\n') + 1
            self.done.emit(True, f'已通过剪贴板粘贴 {line_count} 行文本',
                           f'✓ 非ASCII → Win32 剪贴板粘贴 ({line_count} 行)')
            return
        self.progress.emit('剪贴板失败, 尝试 ADBKeyBoard…')
        if not self.adbkb_installed[0]:
            self.adbkb_installed[0] = self._check_adbkb()
        if self.adbkb_installed[0]:
            if self._send_text_via_adbkeyboard(self.serial, self.text):
                self.done.emit(True, '已通过 ADBKeyBoard 发送文本',
                               '✓ 非ASCII → ADBKeyBoard 广播')
            else:
                self.done.emit(False, '中文输入失败',
                               '✗ ADBKeyBoard 发送失败 (查看日志)')
        else:
            self.done.emit(False, '中文输入失败',
                           '✗ 剪贴板方案未生效 (模拟器未同步剪贴板)\n'
                           '   → 方案 A: 检查模拟器设置是否开启剪贴板共享\n'
                           '   → 方案 B: 安装 ADBKeyBoard (点击下方按钮)\n'
                           '   → 方案 C: 使用网盘里的 Super_ADB.apk '
                           '(内部集成了键盘)，安装后打开 ADB 键盘')

    def _check_adbkb(self):
        try:
            ime_list = self.adb.run_shell(self.serial, 'ime list -s',
                                          timeout=5) or ''
            return 'adbkeyboard' in ime_list.lower()
        except Exception:
            return False

    def _send_text_via_adbkeyboard(self, serial, text):
        """通过 ADBKeyBoard 广播发送文本 (需设备已安装 ADBKeyBoard APK)。"""
        import base64
        try:
            ime_list = self.adb.run_shell(serial, 'ime list -s',
                                          timeout=5) or ''
            if 'adbkeyboard' not in ime_list.lower():
                return False
            self.adb.run_shell(serial,
                'ime enable com.android.adbkeyboard/.AdbIME', timeout=5)
            self.adb.run_shell(serial,
                'ime set com.android.adbkeyboard/.AdbIME', timeout=5)
            time.sleep(0.3)
            b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
            self.adb.run_shell(serial,
                f'am broadcast -a ADB_INPUT_B64 --es msg "{b64}"', timeout=5)
            return True
        except Exception as e:
            self.logmsg.emit(f'ADBKeyBoard 发送失败: {e}')
            return False

    def _send_text_via_native_clipboard(self, serial, text, old_text):
        """通过 Win32 API 写剪贴板 + 设备粘贴键实现免安装中文输入。

        全程使用 ctypes 直接操作 Win32 剪贴板（不混用 Qt clipboard），
        旧剪贴板内容 old_text 由主线程读取后传入，结束后同样用 ctypes 恢复。
        仅模拟器 (或开启剪贴板共享的设备) 有效。
        """
        try:
            import ctypes
            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            data = (text + '\0').encode('utf-16-le')
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not h_mem:
                self.logmsg.emit('Win32 剪贴板: GlobalAlloc 失败')
                return False
            ptr = kernel32.GlobalLock(h_mem)
            if not ptr:
                kernel32.GlobalFree(h_mem)
                return False
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h_mem)

            if not user32.OpenClipboard(0):
                kernel32.GlobalFree(h_mem)
                self.logmsg.emit('Win32 剪贴板: OpenClipboard 失败')
                return False
            user32.EmptyClipboard()
            result = user32.SetClipboardData(CF_UNICODETEXT, h_mem)
            user32.CloseClipboard()
            if not result:
                kernel32.GlobalFree(h_mem)
                self.logmsg.emit('Win32 剪贴板: SetClipboardData 失败')
                return False

            time.sleep(1.5)
            self.adb.run_shell(serial, 'input keyevent 279', timeout=5)
            time.sleep(0.3)

            if old_text:
                odata = (old_text + '\0').encode('utf-16-le')
                oh = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(odata))
                if oh:
                    optr = kernel32.GlobalLock(oh)
                    if optr:
                        ctypes.memmove(optr, odata, len(odata))
                        kernel32.GlobalUnlock(oh)
                        if user32.OpenClipboard(0):
                            user32.EmptyClipboard()
                            user32.SetClipboardData(CF_UNICODETEXT, oh)
                            user32.CloseClipboard()
                        else:
                            kernel32.GlobalFree(oh)
            return True
        except Exception as e:
            self.logmsg.emit(f'Win32 剪贴板发送失败: {e}')
            return False



# ----------------------------------------------------------------------
# 主窗口（多重继承 Ui_MainWindow）
# ----------------------------------------------------------------------
class MainWindow(QWidget, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        # ── 主题先于标题栏按钮加载：后面所有 setStyleSheet 都会用 self._current_theme ──
        self._current_theme = self._load_theme_from_config()
        # ── 标题栏按钮：.ui 中只放关闭按钮（winBtnClose）
        # 关于 / 主题按钮在 __init__ 下文以代码动态创建（避免 Designer 重命名 bug） ──
        # ── 关于按钮 ─────────────────────────────────────────────
        if not hasattr(self, 'btnAbout'):
            self.btnAbout = QPushButton('关于', self)
            self.btnAbout.setFixedSize(50, 26)
            self.btnAbout.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btnAbout.setToolTip('关于 Super_ADB')
            self.btnAbout.setStyleSheet(self._about_btn_style())
            # 放在标题栏最左侧
            self.horizontalLayout_4.insertWidget(0, self.btnAbout)
            self.btnAbout.clicked.connect(self.open_about_dialog)
        # ── 主题按钮（关于按钮右侧，下拉菜单切换 6 套主题） ──
        if not hasattr(self, 'btnTheme'):
            self.btnTheme = QPushButton('主题▾', self)
            self.btnTheme.setFixedSize(60, 26)
            self.btnTheme.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btnTheme.setToolTip('切换主题')
            self.btnTheme.setStyleSheet(self._theme_btn_style())
            # 紧贴关于按钮右侧（位置 1）
            self.horizontalLayout_4.insertWidget(1, self.btnTheme)
            self._init_theme_menu()
        # ── 无边框窗口 ──────────────────────────────────────────
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        # 窗口标题由 .ui 文件 (Super_ADB.ui) 的 windowTitle 定义，
        # 这里不再硬覆盖，保持 UI 与逻辑分离。
        # 页面容器不再用工具栏最小宽度顶住 splitter，
        # 修复左侧折叠/窗口变窄后右侧内容溢出被裁剪、需手动拉窗口才恢复的问题
        for _lay in (self.leftPanel.layout(),
                     self.layoutWidget.layout(),
                     self.layoutWidget1.layout()):
            if _lay is not None:
                _lay.setSizeConstraint(QLayout.SetNoConstraint)
        # 放开主窗口最小尺寸限制：否则窗口缩到比内容所需还小就被布局撑回，
        # 用户「缩小」其实没生效、存的也是被弹回的大尺寸（持久化失效的根因）。
        top_lay = self.layout()
        if top_lay is not None:
            top_lay.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(1, 1)
        self._restore_geometry()
        self.splitter.setSizes([600, 1200])
        # 默认打开时折叠左侧 adb 调试工具栏，只保留右侧内容区
        self.splitter_2.setCollapsible(1, True)
        self.splitter_2.setStretchFactor(0, 1)
        self.splitter_2.setStretchFactor(1, 0)
        self.splitter_2.setSizes([1, 0])
        self.splitter_2.splitterMoved.connect(self._on_splitter_moved)
        # 压小设备下拉框最小宽度，让右栏可以缩得更窄而不裁剪控件
        self.deviceCombo.setMinimumWidth(160)
        self.fileMgr_deviceCombo.setMinimumWidth(160)
        self.logViewer_deviceCombo.setMinimumWidth(160)
        self.setWindowIcon(self._create_icon())

        self.adb = AdbDeviceOps(log_callback=self.log)
        self.pool = QThreadPool()
        # UI 后台任务（connect / 设备信息 / 各 CmdWorker）并发量有限，
        # 6 偏多：每线程 ~1MB 栈常驻。按 CPU 核数收敛到 4，足够且省内存。
        self.pool.setMaxThreadCount(min(os.cpu_count() or 4, 4))
        self._live_workers = []
        self._dpm_window = None
        self._monkey_window = None
        self._app_monitor_window = None
        self._input_text_dialog = None
        self._install_dialog = None
        self._tcpdump_dialog = None
        self._json_tool_dialog = None
        self._md5_dialog = None
        self._timestamp_dialog = None
        self._wireless_debug_dialog = None
        self._wifi_dialog = None
        self._desk_cat = None  # 桌面宠物小猫
        self._pending_select_serial = None  # 连接成功后自动选中并切到该设备
        # 无边框窗口交互状态（拖拽移动 / 边缘缩放）
        self._dragging = False
        self._resizing = False
        self._resize_dir = None
        self._margin = 8                     # 窗口四边 8px 内为缩放热区
        self._drag_pos = QPoint()            # 按下点相对窗口左上角的偏移
        self._drag_start = QPoint()          # 按下时的全局坐标（阈值判定用）
        self._drag_moved = False            # 是否已越过拖拽阈值开始真实位移

        self._wire_signals()
        self._add_status_bar()
        self._init_pages()
        self.setStyleSheet(get_stylesheet(self._current_theme))
        # 无边框窗口标题栏按钮：仅关闭按钮由 .ui 定义；关于/主题在 __init__ 上方代码创建
        self._no_track = set()
        self._btn_close = self.winBtnClose
        self._btn_close.setStyleSheet(self._win_btn_style(True))
        self._no_track.add(self._btn_close)

        self._btn_about = self.btnAbout
        self._btn_about.setStyleSheet(self._about_btn_style())
        self._no_track.add(self._btn_about)

        self._btn_theme = self.btnTheme
        self._btn_theme.setStyleSheet(self._theme_btn_style())
        self._no_track.add(self._btn_theme)
        self._refresh_theme_menu_checks()  # 同步菜单项选中态

        self._reposition_win_buttons()
        self._setup_child_tracking()          # 必须在 UI 全部构建后：为子控件安装事件过滤器
        self._init_pc_ip_input()
        self._init_tray()
        self._init_desk_cat()

        if not self.adb.check_adb():
            QMessageBox.warning(self, '缺少 adb', '未检测到 adb 命令，请检查环境变量后重启本程序。')
            self.status_bar.showMessage('adb 不可用', 0)
        else:
            self.refresh_devices()

        # 点击设备下拉框自动刷新：记录三处 combo 集合与冷却时间戳
        self._device_combos = {
            self.deviceCombo,
            self.fileMgr_deviceCombo,
            self.logViewer_deviceCombo,
        }
        self._last_device_combo_refresh = 0

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------
    def _wire_signals(self):
        """连接 .ui 中所有按钮的信号到业务方法。"""
        # 顶部设备栏
        self.btnRefresh.clicked.connect(self.refresh_devices)
        self.btnDisconnect.clicked.connect(self.disconnect_device)
        # 连接
        self.btnConnect.clicked.connect(self.connect_device)
        # 系统操作
        self.btnSetProxy.clicked.connect(self.set_proxy)
        self.btnClearProxy.clicked.connect(self.clear_proxy)
        self.btnReboot.clicked.connect(self.reboot_device)
        self.btnDeviceInfo.clicked.connect(self.show_device_info)
        # ── 投屏按钮（双按钮组合：左侧启动 + 右侧下拉菜单）──
        # 控件结构写在 .ui（btnScrcpyContainer 内含 btnScrcpyMain/btnScrcpyMenu），此处只接信号+挂菜单
        self.btnScrcpyMenu.setFixedWidth(24)
        self.btnScrcpyMain.clicked.connect(self.start_scrcpy)
        _scrcpy_menu = QMenu(self)
        _act = _scrcpy_menu.addAction('⚙ 投屏设置…')
        _act.triggered.connect(self.open_scrcpy_settings)
        self.btnScrcpyMenu.setMenu(_scrcpy_menu)
        self.btnDpm.clicked.connect(self.open_perf_monitor)
        self.btnSystemRoot.clicked.connect(self.system_root)
        self.btnInputText.clicked.connect(self.open_input_text_dialog)
        # 应用操作
        self.btnStartApp.clicked.connect(self.start_app)
        self.btnStopApp.clicked.connect(self.stop_app)
        self.btnMeminfo.clicked.connect(self.show_meminfo)
        self.btnClearApp.clicked.connect(self.clear_app)
        self.btnUninstall.clicked.connect(self.uninstall_app)
        self.btnAppInfo.clicked.connect(self.show_app_info)
        # ── 获取包列表按钮（双按钮组合：左侧默认动作 + 右侧下拉菜单）──
        # 控件结构写在 .ui（btnPkgListContainer 内含 btnPkgMain/btnPkgMenu），此处只接信号+挂菜单
        self.btnPkgMenu.setFixedWidth(24)
        self.btnPkgMain.clicked.connect(self.show_window_app)
        _pkg_menu = QMenu(self)
        for _label, _slot in [
            ('📱 界面包', self.show_window_app),
            ('🔄 运行中列表', self.show_running_apps),
            ('📦 第三方包', self.list_apps_3),
            ('⚙ 系统包', self.list_apps_s),
            ('📋 所有包', self.list_apps_all),
        ]:
            _a = _pkg_menu.addAction(_label)
            _a.triggered.connect(_slot)
        self.btnPkgMenu.setMenu(_pkg_menu)
        self.btnRunningApps_2.clicked.connect(self.open_monkey_runner)
        self.btnpm.clicked.connect(self.open_app_monitor)
        self.btninstallzip.clicked.connect(self.open_install_dialog)
        # 便捷工具
        self.cmdBtn.clicked.connect(self.open_cmd)
        self.jsonToolBtn.clicked.connect(self.open_json_tool)
        self.md5Btn.clicked.connect(self.open_md5)
        self.timestampBtn.clicked.connect(self.open_timestamp)
        self.btnWirelessDebug.clicked.connect(self.open_wireless_debug)
        self.wifiBtn.clicked.connect(self.open_wifi)
        # 输出
        self.btnClear.clicked.connect(self.output.clear)
        self.btnCopy.clicked.connect(self.copy_output)

    def _add_status_bar(self):
        """.ui 中已定义 QStatusBar，直接引用。"""
        self.status_bar = self.statusBar
        self.status_bar.showMessage('就绪')

    def _init_pages(self):
        """创建文件管理器和日志查看器控制器，注入 .ui 中预定义的控件。"""
        self.file_mgr = FileManagerPage()
        self.file_mgr.inject_widgets(
            tree=self.fileMgr_tree,
            device_combo=self.fileMgr_deviceCombo,
            btn_refresh=self.fileMgr_btnRefresh,
            btn_root=self.fileMgr_btnRoot,
            path_label=self.fileMgr_pathLabel,
            status_label=self.fileMgr_statusLabel,
        )
        self.log_viewer = LogViewerPage()
        self.log_viewer.inject_widgets(
            device_combo=self.logViewer_deviceCombo,
            btn_refresh=self.logViewer_btnRefresh,
            btn_start=self.logViewer_btnStart,
            btn_pause=self.logViewer_btnPause,
            btn_clear=self.logViewer_btnClear,
            status_label=self.logViewer_statusLabel,
            tag_combo=self.logViewer_tagCombo,
            proc_combo=self.logViewer_procCombo,
            msg_combo=self.logViewer_msgCombo,
            tag_star=self.logViewer_tagStar,
            proc_star=self.logViewer_procStar,
            msg_star=self.logViewer_msgStar,
            btn_reset=self.logViewer_btnReset,
            text_edit=self.logViewer_textEdit,
            follow_chk=self.logViewer_followChk,
            regex_chk=self.logViewer_regexChk,
            count_label=self.logViewer_countLabel,
            mode_label=self.logViewer_modeLabel,
            btn_load_file=self.btnLf,
        )
        # 抓取中设备意外断开（logcat 进程退出）：自动刷新三处设备下拉框
        self.log_viewer.device_disconnected.connect(self.refresh_devices)

    # ------------------------------------------------------------------
    # 图标
    # ------------------------------------------------------------------
    def _create_icon(self):
        # 优先使用编译进 png_rc 的资源图标 :/Super_ADB.png
        # （任务栏、系统托盘、各弹窗标题栏统一使用此图标）
        icon = QIcon(':/Super_ADB.png')
        if not icon.isNull():
            return icon
        # 兜底: 磁盘文件
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'Super_ADB.png')
        if os.path.isfile(icon_path):
            return QIcon(icon_path)
        # 最后兜底: 动态生成 SuperADB 文字图标
        pm = QPixmap(64, 64)
        pm.fill(QColor(29, 233, 182))
        p = QPainter(pm)
        p.setPen(QColor(27, 27, 27))
        f = QFont(FONT_FAMILY, 10, QFont.Bold)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, 'SuperADB')
        p.end()
        return QIcon(pm)

    # ------------------------------------------------------------------
    # 线程安全输出
    # ------------------------------------------------------------------
    def log(self, text: str):
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        html = self._format_log_html(str(text), now)
        QMetaObject.invokeMethod(
            self.output, 'append',
            Qt.QueuedConnection,
            Q_ARG(str, html),
        )

    @staticmethod
    def _escape_log_html(text: str) -> str:
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    def _format_log_html(self, text: str, timestamp: str = '') -> str:
        """把纯文本日志转成带配色 HTML，命令/输出/错误/状态分色显示。"""
        lines = str(text).splitlines()
        body_parts = []
        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            esc = self._escape_log_html(line)

            if stripped.startswith('$ '):
                # 命令行：青绿色，并对 adb 关键子命令高亮
                colored = esc
                for kw in ('adb', 'shell', 'getprop', 'dumpsys', 'wm',
                           'am', 'pm', 'settings', 'input', 'monkey',
                           'screencap', 'screenrecord', 'cmd', 'logcat',
                           'tcpdump', 'ifconfig', 'ip', 'netstat', 'ps',
                           'top', 'cat', 'echo', 'grep', 'sed', 'awk'):
                    colored = re.sub(
                        rf'(?<![\w-])({re.escape(kw)})(?![\w-])',
                        rf'<span style="color:#a7ffeb;">\1</span>',
                        colored,
                        flags=re.IGNORECASE,
                    )
                body_parts.append(
                    f'<div style="color:#1de9b6;font-weight:400;margin-top:3px;">'
                    f'{colored}</div>')
                continue

            low = stripped.lower()
            if (stripped.startswith('错误:') or stripped.startswith('执行异常:')
                    or stripped.startswith('命令执行异常:')
                    or stripped.startswith('失败:') or '失败' in low
                    or 'error:' in low or 'permission denied' in low):
                body_parts.append(
                    f'<div style="color:#ff6b6b;margin-top:1px;">{esc}</div>')
                continue

            if (stripped.startswith('已') or '成功' in low or '完成' in low
                    or '完成' in low or stripped in ('OK', 'PASS', 'DONE')):
                body_parts.append(
                    f'<div style="color:#69f0ae;margin-top:1px;">{esc}</div>')
                continue

            if stripped.startswith('警告:') or stripped.startswith('注意:'):
                body_parts.append(
                    f'<div style="color:#ffd54f;margin-top:1px;">{esc}</div>')
                continue

            # 普通输出：对常见的 "键: 值" / "键：值" 做键名高亮
            colored = re.sub(
                r'^(\s*[\u4e00-\u9fa5\w\s\(\)/\[\]-]+[:：])\s*(.*)$',
                rf'<span style="color:#80deea;">\1</span> \2',
                esc,
            )
            body_parts.append(
                f'<div style="color:#e0e0e0;margin-top:1px;">{colored}</div>')

        body = ''.join(body_parts)
        if not body:
            return ''
        ts_html = (f'<span style="color:#888;font-size:11px;">[{timestamp}]</span>'
                   if timestamp else '')
        return (f'<div style="margin:4px 0 8px;">'
                f'{ts_html}{body}'
                f'</div>')

    def set_status(self, text: str, ok: bool = None):
        prefix = '' if ok is None else ('● ' if ok else '✕ ')
        QMetaObject.invokeMethod(
            self.status_bar, 'showMessage',
            Qt.QueuedConnection,
            Q_ARG(str, prefix + text),
        )

    @staticmethod
    def _is_device_offline(text: str) -> bool:
        """命令结果/错误信息里是否提示设备离线或授权丢失（需要刷新设备列表）。"""
        low = (text or '').lower()
        return ('device offline' in low
                or 'device unauthorized' in low
                or 'device not found' in low
                or 'no devices' in low)

    def _run_async(self, func, *args, **kwargs):
        """将函数放入线程池后台执行，结果通过 log / set_status 展示。"""
        self.output.clear()
        worker = CmdWorker(func, *args, **kwargs)

        def _on_result(r):
            text = str(r)
            self.log(text)
            # 执行报错提示设备离线/掉线：自动刷新三处设备下拉框
            if self._is_device_offline(text):
                self.refresh_devices()

        def _on_error(e):
            text = str(e)
            self.log(f'错误: {text}')
            if self._is_device_offline(text):
                self.refresh_devices()

        worker.signals.result.connect(_on_result)
        worker.signals.error.connect(_on_error)
        worker.signals.finished.connect(lambda: self._drop_worker(worker))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _drop_worker(self, worker):
        try:
            self._live_workers.remove(worker)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # 设备管理
    # ------------------------------------------------------------------
    def current_serial(self):
        idx = self.deviceCombo.currentIndex()
        if idx < 0:
            return None
        return self.deviceCombo.itemData(idx)

    def _ensure_serial(self):
        serial = self.current_serial()
        if not serial:
            self.log('请先选择或连接一个设备')
        return serial

    def refresh_devices(self):
        self.set_status('正在扫描设备…')
        worker = CmdWorker(self.adb.get_devices)
        worker.signals.result.connect(self._on_devices_loaded)
        worker.signals.error.connect(lambda e: self.set_status(f'扫描失败: {e}'))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _on_devices_loaded(self, devices):
        online = [d for d in devices if d.get('state') == 'device']
        # 选中优先级：刚连上的设备 > 原选中设备
        select = self._pending_select_serial
        self._pending_select_serial = None
        if select is None:
            select = self.current_serial()
        self.deviceCombo.blockSignals(True)
        self.deviceCombo.clear()
        for d in online:
            self.deviceCombo.addItem(format_device_label(d), d.get('serial'))
        idx = self.deviceCombo.findData(select) if select else -1
        if idx >= 0:
            self.deviceCombo.setCurrentIndex(idx)
        self.deviceCombo.blockSignals(False)
        self.set_status(f'已连接 {len(online)} 台设备', ok=len(online) > 0)
        # 同步文件管理器与日志页的设备下拉框
        if getattr(self, 'file_mgr', None) is not None:
            self.file_mgr.sync_devices(devices, select)
        if getattr(self, 'log_viewer', None) is not None:
            self.log_viewer.sync_devices(devices, select)

    def connect_device(self):
        ip = self.ipInput.text().strip()
        if not ip:
            self.log('请输入设备 IP')
            return
        # 记录目标 serial（与 adb connect 一致：缺端口自动补 :5555）
        target = ip if ':' in ip else f'{ip}:5555'
        self._pending_select_serial = target
        self.set_status(f'正在连接 {ip}…')
        worker = CmdWorker(self.adb.connect, ip)
        worker.signals.result.connect(self._on_connected)
        worker.signals.error.connect(lambda e: self.set_status(f'连接失败: {e}'))
        worker.signals.finished.connect(lambda: self._drop_worker(worker))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _on_connected(self, result):
        self.log(str(result))
        # 连接命令返回后重新扫描，让三处下拉框加载到新设备
        self.refresh_devices()

    def disconnect_device(self):
        serial = self.current_serial()
        if serial:
            self._run_async(self.adb.disconnect, serial)
        else:
            self._run_async(self.adb.disconnect)

    # ------------------------------------------------------------------
    # 系统操作
    # ------------------------------------------------------------------
    def set_proxy(self):
        serial = self._ensure_serial()
        if not serial:
            return
        host_port = (self.pcIpInput.text().strip() if hasattr(self, 'pcIpInput')
                     else f'{self._get_local_ip()}:8888')
        if not host_port:
            self.log('请先在「PC本机IP」输入框填写 本机IP:端口')
            return
        self._run_async(self.adb.set_proxy, serial, host_port)

    def clear_proxy(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.clear_proxy, serial)

    def reboot_device(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.reboot, serial)

    def start_scrcpy(self):
        """启动 scrcpy 投屏。

        说明:
        - 优先使用项目 data/ 下匹配平台的最新版本 scrcpy 目录（支持嵌套在
          data/scrcpy/ 下或直接放在 data/ 下）；未找到则回退 PATH。
        - 受 DRM/HDCP 保护的视频内容会黑屏/绿屏，这是 SurfaceFlinger 截屏方案
          的硬件限制，与 scrcpy 本身无关。
        """
        serial = self._ensure_serial()
        if not serial:
            return
        # 预检 scrcpy 二进制是否存在，避免用户未放置二进制时直接报错
        scrcpy_dir = self.adb.find_scrcpy_dir()
        found = bool(scrcpy_dir) and os.path.isfile(
            os.path.join(scrcpy_dir, 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy')
        )
        if not found:
            # 也允许 PATH 中的 scrcpy
            path_scrcpy = 'scrcpy.exe' if sys.platform == 'win32' else 'scrcpy'
            in_path = any(
                os.path.isfile(os.path.join(d, path_scrcpy))
                for d in os.environ.get('PATH', '').split(os.pathsep) if d
            )
            if not in_path:
                example_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    'data', 'scrcpy', 'scrcpy-win64-v4.1'
                )
                QMessageBox.information(
                    self, '未找到 scrcpy',
                    '请下载对应平台的 scrcpy 压缩包并解压到项目 data/ 目录下。\n\n'
                    f'示例路径：\n{example_dir}\\scrcpy.exe\n\n'
                    '支持以下两种布局（程序会自动识别最新版本）：\n'
                    '  1) data\\scrcpy-win64-vX.Y\\scrcpy.exe\n'
                    '  2) data\\scrcpy\\scrcpy-win64-vX.Y\\scrcpy.exe\n\n'
                    '受 DRM/HDCP 保护的视频内容投屏会黑屏，属于硬件限制。'
                )
                return
        try:
            settings = scrcpy_settings_dialog.load_scrcpy_settings()
            args = scrcpy_settings_dialog.build_scrcpy_args(settings)
            result = self.adb.scrcpy(serial, extra_args=args)
            self.log(result)
        except Exception as e:
            self.log(f'启动 scrcpy 失败: {e}')
            QMessageBox.warning(self, '投屏失败', f'启动 scrcpy 失败:\n{e}')

    def open_scrcpy_settings(self):
        """打开 scrcpy 投屏参数设置对话框（分辨率/码率/帧率/编码/渲染驱动）。"""
        dlg = scrcpy_settings_dialog.ScrcpySettingsDialog(self)
        dlg.exec()

    def show_device_info(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_device_info, serial)

    def show_logcat(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self.output.clear()
        self.log('正在打开独立 logcat 窗口...')
        threading.Thread(target=lambda: self.log(self.adb.logcat_to_desktop(serial)), daemon=True).start()

    def system_root(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.root_and_remount, serial)

    # ------------------------------------------------------------------
    # 输入文本
    # ------------------------------------------------------------------
    def open_input_text_dialog(self):
        """弹文本输入弹窗，支持多行和中文。

        策略:
        1. 纯 ASCII → adb shell input text (Android 系统命令)
        2. 含非 ASCII (中文等) → 先试 Win32 剪贴板 (免安装, 仅模拟器)
           失败再用 ADBKeyBoard 广播 (需设备装 ADBKeyBoard APK)
           全部失败则引导用户安装 ADBKeyBoard

        说明: Qt 的 clipboard.setText() 不触发模拟器剪贴板同步,
        所以用 Win32 API (ctypes) 直接调 OpenClipboard/SetClipboardData,
        更底层, 更可靠地触发 Windows 剪贴板变更通知。
        """
        serial = self._ensure_serial()
        if not serial:
            return
        if self._input_text_dialog is not None and self._input_text_dialog.isVisible():
            self._input_text_dialog.raise_()
            self._input_text_dialog.activateWindow()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle('输入文本 (支持中文)')
        dlg.setMinimumSize(560, 300)
        dlg.setStyleSheet(get_stylesheet(self._current_theme))

        card = QWidget(dlg)
        card.setObjectName('popupCard')
        card.setStyleSheet(HIGHLIGHT_CARD_STYLE)
        add_green_glow(card)

        lay = QVBoxLayout(card)
        lay.setSpacing(8)
        lay.setContentsMargins(12, 10, 12, 10)
        hint = QLabel('输入要发送到设备焦点输入框的文本:')
        hint.setStyleSheet('background: transparent; border: none;')
        lay.addWidget(hint)
        edit = QTextEdit()
        edit.setPlaceholderText('在此输入文本，支持中文和多行…\n'
                                '• 纯 ASCII → 直接 adb shell input text\n'
                                '• 含中文 → 先试 Win32 剪贴板粘贴 (免安装)\n'
                                '         失败再用 ADBKeyBoard (需安装)')
        lay.addWidget(edit, 1)

        # 策略提示
        info_label = QLabel('')
        info_label.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #8b949e; '
            f'background: transparent; border: none;')
        info_label.setWordWrap(True)
        lay.addWidget(info_label)

        # ADBKeyBoard 安装状态指示
        adbkb_status = QLabel('检测 ADBKeyBoard 状态…')
        adbkb_status.setStyleSheet(
            f'font: 9pt "{FONT_FAMILY}"; color: #8b949e; '
            f'background: transparent; border: none;')
        lay.addWidget(adbkb_status)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        # 下载安装 ADBKeyBoard 按钮 (默认隐藏, 需中文输入且未装时显示)
        btn_install = QPushButton('下载 ADBKeyBoard')
        btn_install.setVisible(False)
        btn_install.setStyleSheet(
            'QPushButton { background: #1de9b6; color: #1a1a2e; '
            f'font: 9pt "{FONT_FAMILY}"; font-weight: bold; '
            'border: none; padding: 6px 14px; border-radius: 4px; }'
            ' QPushButton:hover { background: #14cfa1; }')
        btn_row.addWidget(btn_install)

        btn_send = QPushButton('发送')
        btn_send.setFixedWidth(100)
        btn_row.addWidget(btn_send)
        lay.addLayout(btn_row)

        # ---- ADBKeyBoard 安装状态 (用 list 引用避免闭包问题) ----
        adbkb_installed = [False]

        def _check_adbkb():
            try:
                ime_list = self.adb.run_shell(
                    serial, 'ime list -s', timeout=5) or ''
                adbkb_installed[0] = 'adbkeyboard' in ime_list.lower()
            except Exception:
                adbkb_installed[0] = False
            if adbkb_installed[0]:
                adbkb_status.setText('✓ ADBKeyBoard 已安装 (中文输入可用)')
                adbkb_status.setStyleSheet(
                    f'font: 9pt "{FONT_FAMILY}"; color: #98c379; '
                    f'background: transparent; border: none;')
                btn_install.setVisible(False)
            else:
                adbkb_status.setText(
                    '⚠ 未检测到 ADBKeyBoard (中文输入需先安装)')
                adbkb_status.setStyleSheet(
                    f'font: 9pt "{FONT_FAMILY}"; color: #e5c07b; '
                    f'background: transparent; border: none;')

        def _open_download():
            """打开 ADBKeyBoard GitHub 项目页。"""
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(
                    'https://github.com/senzhk/ADBKeyBoard'))
                info_label.setText(
                    '已打开 ADBKeyBoard 项目页, 下载 APK 并在设备安装后, '
                    '执行: adb shell ime enable '
                    'com.android.adbkeyboard/.AdbIME')
            except Exception as e:
                self.log(f'打开下载页失败: {e}')
                info_label.setText(
                    f'请手动访问: https://github.com/senzhk/ADBKeyBoard ({e})')

        btn_install.clicked.connect(_open_download)

        # 启动时异步检测
        threading.Thread(target=_check_adbkb, daemon=True).start()

        def _do_send():
            text = edit.toPlainText()
            if not text:
                return
            btn_send.setEnabled(False)
            dlg.setWindowTitle('发送中…')

            # 旧剪贴板读取必须在主线程 (Qt clipboard 属 GUI 资源)
            from PySide6.QtGui import QGuiApplication
            old_text = QGuiApplication.clipboard().text()

            sender = _TextSender(self.adb, serial, text, old_text,
                                 adbkb_installed)
            sender.progress.connect(info_label.setText)
            sender.logmsg.connect(self.log)
            sender.done.connect(_on_send_done)

            thread = QThread()
            sender.moveToThread(thread)
            thread.started.connect(sender.run)
            sender.done.connect(thread.quit)
            sender.done.connect(sender.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self._input_sender = (sender, thread)  # 防止被提前 GC
            thread.start()

        def _on_send_done(ok, status_text, info_text):
            self.set_status(status_text, ok=ok)
            info_label.setText(info_text)
            dlg.setWindowTitle('输入文本 (支持中文)')
            edit.clear()
            btn_send.setEnabled(True)


        btn_send.clicked.connect(_do_send)
        # 回车快捷发送
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence('Ctrl+Return'), dlg, activated=_do_send)

        main_lay = QVBoxLayout(dlg)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(card)

        dlg.show()
        self._input_text_dialog = dlg

    # 注：中文输入发送逻辑已重构为模块级 _TextSender worker（见文件顶部），
    # 原 _send_text_via_* 两个 MainWindow 方法整体迁入，避免在按钮回调主线程
    # 内 time.sleep(1.5/0.3) + 多次同步 adb 调用导致 UI 冻结。

    # ------------------------------------------------------------------
    # 设备性能监控
    # ------------------------------------------------------------------
    def open_perf_monitor(self):
        """打开设备性能监控独立窗口 (重复点击复用已开窗口)。"""
        serial = self._ensure_serial()
        if not serial:
            return
        if self._dpm_window is not None and self._dpm_window.isVisible():
            self._dpm_window.raise_()
            self._dpm_window.activateWindow()
            return
        from device_perf_monitor import DevicePerfMonitor
        self._dpm_window = DevicePerfMonitor(serial, parent=self)
        self._dpm_window.show()

    # ------------------------------------------------------------------
    # Monkey 压力测试
    # ------------------------------------------------------------------
    def open_monkey_runner(self):
        """打开 Monkey 压测配置窗口 (重复点击复用已开窗口)。"""
        serial = self._ensure_serial()
        if not serial:
            return
        if self._monkey_window is not None and self._monkey_window.isVisible():
            self._monkey_window.raise_()
            self._monkey_window.activateWindow()
            return
        # 默认带入主窗口已填的包名
        default_pkg = self.pkgInput.text().strip()
        from monkey_runner_window import MonkeyRunnerWindow
        self._monkey_window = MonkeyRunnerWindow(
            serial, default_pkg=default_pkg, parent=self)
        self._monkey_window.show()

    # ------------------------------------------------------------------
    # 应用性能监控
    # ------------------------------------------------------------------
    def open_app_monitor(self):
        """打开应用性能监控独立窗口 (重复点击复用已开窗口)。"""
        serial = self._ensure_serial()
        if not serial:
            return
        pkg = self._package_name()
        if not pkg:
            self.log('请先在包名输入框填写要监控的包名')
            return
        if self._app_monitor_window is not None and self._app_monitor_window.isVisible():
            self._app_monitor_window.raise_()
            self._app_monitor_window.activateWindow()
            return
        from app_perf_monitor import AppPerfMonitor
        self._app_monitor_window = AppPerfMonitor(serial, pkg, parent=self)
        self._app_monitor_window.show()

    # ------------------------------------------------------------------
    # 安装 / 解包
    # ------------------------------------------------------------------
    def open_install_dialog(self):
        """打开 安装/解包 弹窗（拖入 APK/ZIP 查看内容并执行 adb install）。"""
        if self._install_dialog is not None and self._install_dialog.isVisible():
            self._install_dialog.raise_()
            self._install_dialog.activateWindow()
            return
        from install_zip_dialog import InstallZipDialog
        self._install_dialog = InstallZipDialog(
            self.adb, self.current_serial, parent=self)
        self._install_dialog.show()

    def open_cmd(self):
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
            self.set_status(msg, ok=True)
            self.log(msg)
        except Exception as e:
            err = f'启动命令行失败：{e}'
            self.set_status(err, ok=False)
            self.log(f'错误: {err}')

    def open_json_tool(self):
        """打开 JSON 工具弹窗（复用窗口，重复点击 raise）。

        弹窗内容来自独立项目 G:/Python/jcspy/jsontool 的核心功能
        （格式化/压缩 + diff 差异对比 + JSON 语法高亮），
        已改造为 QDialog 子窗口，沿用主项目深色主题与字号规范。"""
        if (self._json_tool_dialog is not None
                and self._json_tool_dialog.isVisible()):
            self._json_tool_dialog.raise_()
            self._json_tool_dialog.activateWindow()
            return
        from json_tool_dialog import JsonToolDialog
        self._json_tool_dialog = JsonToolDialog(parent=self)
        self._json_tool_dialog.show()

    def open_md5(self):
        """打开 MD5 校验弹窗（复用窗口，重复点击 raise）。"""
        if self._md5_dialog is not None and self._md5_dialog.isVisible():
            self._md5_dialog.raise_()
            self._md5_dialog.activateWindow()
            return
        from md5_dialog import Md5Dialog
        self._md5_dialog = Md5Dialog(parent=self)
        self._md5_dialog.show()

    def open_timestamp(self):
        """打开时间戳转换弹窗（复用窗口，重复点击 raise）。"""
        if self._timestamp_dialog is not None and self._timestamp_dialog.isVisible():
            self._timestamp_dialog.raise_()
            self._timestamp_dialog.activateWindow()
            return
        from timestamp_dialog import TimestampDialog
        self._timestamp_dialog = TimestampDialog(parent=self)
        self._timestamp_dialog.show()

    def open_wireless_debug(self):
        """打开统一无线调试面板（局域网扫描 + WiFi 配对码连接，复用窗口，重复点击 raise）。"""
        if self._wireless_debug_dialog is not None and self._wireless_debug_dialog.isVisible():
            self._wireless_debug_dialog.raise_()
            self._wireless_debug_dialog.activateWindow()
            return

        def _on_pair_success(ip, port):
            # 配对成功后刷新设备列表，并把当前 IP:端口 填到主窗口输入框方便下一步 connect
            if ip:
                self.ipInput.setText(f'{ip}:{port}')
            self.refresh_devices()

        def _on_device_connected(serial):
            # 局域网扫描里「adb connect 成功」后：把刚连上的设备设为期望选中项，
            # 触发一次刷新——主窗口 + 文件管理页 + 日志页的三处下拉框会同步更新。
            if serial:
                self._pending_select_serial = serial
            self.refresh_devices()

        from wireless_debug_dialog import WirelessDebugDialog
        self._wireless_debug_dialog = WirelessDebugDialog(
            parent=self,
            on_pair_success=_on_pair_success,
            on_device_connected=_on_device_connected)
        self._wireless_debug_dialog.show()

    def open_wifi(self):
        """打开本机 WiFi 密码查看弹窗（复用窗口，重复点击 raise）。"""
        if self._wifi_dialog is not None and self._wifi_dialog.isVisible():
            self._wifi_dialog.raise_()
            self._wifi_dialog.activateWindow()
            return
        from wifi_dialog import WifiDialog
        self._wifi_dialog = WifiDialog(parent=self)
        self._wifi_dialog.show()


    def open_tcpdump_dialog(self):
        """打开 tcpdump 抓包弹窗（复用窗口，重复点击 raise）。"""
        if self._tcpdump_dialog is not None and self._tcpdump_dialog.isVisible():
            self._tcpdump_dialog.raise_()
            self._tcpdump_dialog.activateWindow()
            return
        serial = self._ensure_serial()
        if not serial:
            self.set_status('请先选择设备', ok=False)
            return
        from tcpdump_dialog import TcpdumpDialog
        self._tcpdump_dialog = TcpdumpDialog(serial, parent=self)
        self._tcpdump_dialog.show()

    def open_about_dialog(self):
        """打开关于弹窗：展示公众号二维码、版本号与反馈引导。"""
        from about_dialog import AboutDialog
        dlg = AboutDialog(parent=self)
        dlg.exec()

    # ------------------------------------------------------------------
    # 应用操作
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_package(raw):
        """把用户可能粘贴的 `pkg/Activity`、尾随 `/` 规范成纯包名。

        dumpsys meminfo / pidof / monkey 等命令只接受纯包名，带 `/` 或
        Activity 后缀会导致命令失败（表现为内存各项全部「未获取」）。"""
        if not raw:
            return raw
        s = raw.strip().rstrip('/')
        if '/' in s:
            s = s.split('/', 1)[0]
        return s.strip()

    def _package_name(self):
        name = self._normalize_package(self.pkgInput.text())
        if not name:
            self.log('请输入包名')
        return name

    def start_app(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self._run_async(self.adb.start_app, serial, pkg)

    def stop_app(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self._run_async(self.adb.stop_app, serial, pkg)

    def show_meminfo(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self.output.clear()

        def _task():
            raw = self.adb.get_meminfo(serial, pkg)
            return self._format_meminfo(raw, pkg)

        self._run_async(_task)

    # ------------------------------------------------------------------
    # meminfo 结果简化（展示层）：只保留关键内存指标
    # ------------------------------------------------------------------
    @staticmethod
    def _fmt_kb(text: str) -> str:
        """把 KB 数值格式化为 KB + MB 双单位。"""
        try:
            kb = int(text.strip())
        except ValueError:
            return text.strip()
        return f'{kb} KB ({kb / 1024:.1f} MB)' if kb >= 1024 else f'{kb} KB'

    @classmethod
    def _format_meminfo(cls, raw: str, pkg: str) -> str:
        lines = [f'包名: {pkg}']
        m = re.search(r'MEMINFO in pid (\d+)', raw)
        if m:
            lines.append(f'进程 PID: {m.group(1)}')

        # 优先用 app_perf_monitor 里已兼容新旧 Android 的解析器
        from app_perf_monitor import _parse_meminfo
        parsed = _parse_meminfo(raw)
        if 'pss_mb' in parsed:
            lines.append(f'总 PSS: {cls._fmt_kb(str(int(parsed["pss_mb"] * 1024)))}')
        if 'rss_mb' in parsed:
            lines.append(f'总 RSS: {cls._fmt_kb(str(int(parsed["rss_mb"] * 1024)))}')

        lines.append('-' * 32)
        mapping = [
            ('Java 堆', 'java_mb', 'Java Heap'),
            ('Native 堆', 'native_mb', 'Native Heap'),
            ('代码', None, 'Code'),
            ('栈', None, 'Stack'),
            ('图形', 'graphics_mb', 'Graphics'),
            ('私有其他', None, 'Private Other'),
            ('系统占用', None, 'System'),
        ]
        for name, parsed_key, raw_key in mapping:
            if parsed_key and parsed_key in parsed:
                val_kb = int(parsed[parsed_key] * 1024)
                lines.append(f'{name}: {cls._fmt_kb(str(val_kb))}')
            else:
                m = re.search(rf'{re.escape(raw_key)}:\s*(\d+)', raw)
                lines.append(f'{name}: {cls._fmt_kb(m.group(1)) if m else "未获取"}')
        return '\n'.join(lines)

    def clear_app(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self._run_async(self.adb.clear_app, serial, pkg)

    def uninstall_app(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self._run_async(self.adb.uninstall_app, serial, pkg)

    def show_app_info(self):
        serial = self._ensure_serial()
        pkg = self._package_name()
        if not serial or not pkg:
            return
        self._run_async(self.adb.get_app_info, serial, pkg)

    def list_apps_3(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_app_list, serial, '-3')

    def list_apps_s(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_app_list, serial, '-s')

    def list_apps_all(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_app_list, serial, '')

    def show_window_app(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_window_app, serial)

    def show_running_apps(self):
        serial = self._ensure_serial()
        if not serial:
            return
        self._run_async(self.adb.get_running_apps, serial)

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------
    def copy_output(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.output.toPlainText())
        self.set_status('已复制输出', ok=True)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    # PC 本机 IP 输入框（系统操作栏）
    # ------------------------------------------------------------------
    def _init_pc_ip_input(self):
        """系统操作栏「PC本机IP」输入框与「tcpdump 抓包」按钮已在 ui/Super_ADB.ui
        的 sysGroup 顶部定义（pcIpLabel / pcIpInput / btnRefreshIp / btnTcpdump），
        由 setupUi 创建。这里只补设动态属性与信号连接（控件本身不再由代码 new）。"""
        self.pcIpInput.setPlaceholderText('本机IP:端口')
        self.pcIpInput.setClearButtonEnabled(True)
        self.pcIpInput.setToolTip('本机(电脑)IP:端口，设置代理时使用。默认本机IP:8888，可手动修改')
        self.pcIpInput.setText(self._compose_default_ip_port())
        self.btnRefreshIp.setToolTip('重新获取本机 IP（切换网络后点击更新）')
        self.btnRefreshIp.clicked.connect(self._refresh_pc_ip)
        self.btnTcpdump.setFixedWidth(120)
        self.btnTcpdump.clicked.connect(self.open_tcpdump_dialog)
        self.pcIpLabel.setToolTip('本机(电脑)IP，用于给手机设置代理。格式 IP:端口，例如 192.168.1.10:8888')

    def _refresh_pc_ip(self):
        """刷新 PC 本机 IP，保留用户当前输入的端口。"""
        current = self.pcIpInput.text().strip()
        port = '8888'
        if ':' in current:
            _, port = current.rsplit(':', 1)
            if not port or not port.isdigit():
                port = '8888'
        new_ip = self._get_local_ip()
        self.pcIpInput.setText(f'{new_ip}:{port}')
        self.log(f'本机 IP 已刷新: {new_ip}:{port}')

        # 若无线调试面板正打开，同步刷新其局域网扫描页的本机网段
        if (self._wireless_debug_dialog is not None
                and self._wireless_debug_dialog.isVisible()):
            try:
                self._wireless_debug_dialog._lan_dialog.refresh_network_range()
            except Exception as e:
                self.log(f'刷新无线调试网段失败: {e}')

    @staticmethod
    def _compose_default_ip_port(port='8888'):
        return f'{MainWindow._get_local_ip()}:{port}'

    @staticmethod
    def _get_local_ip():
        """获取本机局域网 IP，优先通过默认路由出口地址获得，避免 hostname 解析到 127.0.0.1。"""
        # 方法 1：UDP connect 到公网 DNS（不真正发包）， getsockname 拿到默认路由出口 IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(2)
            s.connect(('223.5.5.5', 53))
            ip = s.getsockname()[0]
            s.close()
            if ip and not ip.startswith('127.'):
                return ip
        except Exception:
            pass

        # 方法 2：解析主机名，过滤 loopback 与链路本地地址
        try:
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for addr in addrs:
                ip = addr[4][0]
                if ip and not ip.startswith('127.') and not ip.startswith('169.254.'):
                    return ip
        except Exception:
            pass

        # 兜底：仍尝试 gethostbyname，给用户一个可编辑的值
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception:
            return '127.0.0.1'

    # ------------------------------------------------------------------
    # 窗口几何持久化
    # ------------------------------------------------------------------
    def _restore_geometry(self):
        """加载窗口几何到 self._restore_blob（新格式 QByteArray）或 self._restore_rect
        （旧格式 QRect）。真正应用延迟到首次 showEvent，避免无边框窗口在 show() 时被
        WM 重置几何。

        新格式存 saveGeometry() 的 base64 字节，自动包含位置/大小/窗口状态（最大化/
        还原）与多屏坐标；旧版本 {x,y,w,h} 字典格式向后兼容。"""
        self._geometry_restored = False
        g = load_json_config(CONFIG_NAME).get('geometry') or {}
        # 新格式：base64(QByteArray)
        if isinstance(g, dict) and 'b64' in g:
            try:
                self._restore_blob = QByteArray.fromBase64(g['b64'].encode('ascii'))
                self._restore_rect = None
                return
            except Exception:
                pass
        # 旧格式 / 缺失：转成 QRect（记录值或默认值）
        try:
            x, y, w, h = int(g['x']), int(g['y']), int(g['w']), int(g['h'])
            if w > 0 and h > 0:
                self._restore_rect = QRect(x, y, w, h)
                self._restore_blob = None
                return
        except (KeyError, TypeError, ValueError):
            pass
        # 首次启动 / 配置缺失：优先用内嵌的默认几何 blob（源自 adb_shell_config.json
        # 的 geometry.b64，精确包含位置/大小/最大化状态），无效时才回退旧格式矩形。
        d = DEFAULT_GEOMETRY
        blob = QByteArray.fromBase64(DEFAULT_GEOMETRY_B64.encode('ascii'))
        if not blob.isEmpty():
            self._restore_blob = blob
            self._restore_rect = None
        else:
            self._restore_rect = QRect(d['x'], d['y'], d['w'], d['h'])
            self._restore_blob = None

    def _save_geometry(self):
        # 始终记录当前几何（含窗口状态）。saveGeometry 会编码最小化/最大化等状态，
        # 因此即便最小化时退出，下次也会还原到对应状态，不会丢尺寸/位置。
        blob = self.saveGeometry()
        cfg = load_json_config(CONFIG_NAME)
        cfg['geometry'] = {'b64': bytes(blob.toBase64()).decode('ascii')}
        save_json_config(CONFIG_NAME, cfg)

    def _save_geometry_debounced(self):
        """移动/缩放防抖保存：停顿 300ms 后才写盘，避免拖动过程高频写入。"""
        if not hasattr(self, '_geo_timer'):
            self._geo_timer = QTimer(self)
            self._geo_timer.setSingleShot(True)
            self._geo_timer.timeout.connect(self._save_geometry)
        self._geo_timer.start(300)

    def showEvent(self, ev):
        super().showEvent(ev)
        # 仅在首次显示时还原窗口位置/大小；之后从托盘恢复时不再跳回记录位置
        if not self._geometry_restored:
            blob = getattr(self, '_restore_blob', None)
            if isinstance(blob, QByteArray):
                self.restoreGeometry(blob)
            else:
                rect = getattr(self, '_restore_rect', None)
                if rect is not None:
                    self.setGeometry(rect)
            self._geometry_restored = True
        # 主窗口显示（含托盘恢复）时，小猫同步出现在主页面上
        if self._desk_cat is not None and not self._desk_cat._hidden_by_user:
            self._desk_cat.show()
            self._desk_cat.raise_()
            self._update_desk_cat_bounds()

    def changeEvent(self, ev):
        """窗口状态变化（最小化/最大化/还原）时持久化，下次启动沿用。"""
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.WindowStateChange:
            self._save_geometry_debounced()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._close_active_popups()
        self._save_geometry_debounced()
        self._update_desk_cat_bounds()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._close_active_popups()
        self._reposition_win_buttons()
        self._save_geometry_debounced()
        self._update_desk_cat_bounds()

    def hideEvent(self, ev):
        """主窗口隐藏（最小化 / 入托盘）时，小猫同步隐藏。"""
        super().hideEvent(ev)
        if self._desk_cat is not None:
            self._desk_cat.hide()

    def paintEvent(self, ev):
        """在窗口边缘绘制 4px 青绿色高亮边框（无边框窗口专用）。"""
        super().paintEvent(ev)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(29, 233, 182), 4))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))

    def _close_active_popups(self):
        """主窗口移动或缩放时关闭已弹出的 QComboBox 下拉框，避免错位。"""
        popup = QApplication.activePopupWidget()
        if popup is not None and popup is not self:
            popup.close()

    def closeEvent(self, ev):
        """点 ✕ 直接关闭窗口并退出程序。"""
        self._save_geometry()
        ev.accept()

    def _hide_to_tray(self):
        self._save_geometry()
        self.hide()
        self.tray_icon.showMessage(
            'Super_ADB', '已隐藏到托盘，单击托盘图标恢复，右键"退出"可彻底关闭。',
            QSystemTrayIcon.MessageIcon.Information, 3000)

    def _on_tray_activated(self, reason):
        """单击托盘图标恢复窗口。"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.raise_()
            self.activateWindow()

    def _init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self._create_icon())
        self.tray_icon.setToolTip('Super_ADB')
        tray_menu = QMenu()
        show_action = QAction('显示', self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        # 开机自动启动（仅打包后的 exe 生效；勾选写入当前用户 Run 键）
        autostart_action = QAction('开机自动启动', self)
        autostart_action.setCheckable(True)
        autostart_action.setChecked(is_autostart_enabled())
        autostart_action.setToolTip('勾选后开机自动在后台托盘运行（不弹主窗口）')
        def _on_autostart_toggled(checked):
            set_autostart(checked)
            autostart_action.setChecked(is_autostart_enabled())
        autostart_action.triggered.connect(_on_autostart_toggled)
        tray_menu.addAction(autostart_action)

        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _init_desk_cat(self):
        """初始化桌面宠物小猫，使用打包进资源的橘白小猫图片（:/desk_cat.png）。"""
        # 图片已编译进 png_rc.py（ui/png.qrc），打包后无需外部文件
        image_path = ':/desk_cat.png'
        try:
            self._desk_cat = create_desk_cat(self, image_path=image_path, size=85)
            self._update_desk_cat_bounds()
        except Exception as e:
            # 小猫加载失败不阻塞主程序启动
            print(f'[desk_cat] 初始化失败: {e}')
            self._desk_cat = None

    def _update_desk_cat_bounds(self):
        """把主窗口客户区映射为小猫的活动边界。

        小猫在主页面（标题栏以下、状态栏以上）的整个客户区内活动，
        不区分左侧工具栏或右侧内容区；左侧折叠/展开、窗口缩放时都能保持可见。
        """
        if self._desk_cat is None:
            return

        margin = 12
        geo = self.geometry()
        status_h = self.status_bar.height() if hasattr(self, 'status_bar') else 22
        title_h = 34

        left = margin
        top = title_h + margin
        right = geo.width() - margin
        bottom = geo.height() - status_h - margin

        # 保证边界至少能放下小猫本身
        cat_w = self._desk_cat.width()
        cat_h = self._desk_cat.height()
        if right - left < cat_w:
            right = left + cat_w
        if bottom - top < cat_h:
            bottom = top + cat_h

        bounds = QRect(left, top, right - left, bottom - top)
        self._desk_cat.set_bounds(bounds)

    def _quit_app(self):
        """托盘退出：先保存窗口几何，再退出程序。"""
        self._save_geometry()
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # 无边框窗口：拖拽移动与边缘缩放（同 adb_Exp / jsontool 模式）
    # ------------------------------------------------------------------
    def _setup_child_tracking(self):
        """为子控件启用鼠标追踪并安装事件过滤器，
        使父窗口能统一处理子控件区域内的拖拽和缩放事件。
        跳过 QComboBox 的内部 view / QListView / QMenu 等会被 reparent 到
        独立 popup 窗口的控件，避免坐标映射失败导致误触发缩放/拖拽。"""
        skip_types = (QListView, QMenu, QAbstractSpinBox, QScrollBar)
        for child in self.findChildren(QWidget):
            # 标题栏按钮已在 _no_track 中放行，这里仍需安装过滤器以便 hover
            if isinstance(child, skip_types):
                continue
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def _is_child_of_self(self, obj):
        """判断 obj 是否仍在本窗口树内（popup 子控件会被 reparent 到独立窗口）。"""
        try:
            return obj.window() is self
        except RuntimeError:
            # 对象已被销毁
            return False

    @staticmethod
    def _is_interactive(widget):
        """判断控件是否为交互型（点击应触发其自身行为，不应发起窗口拖拽）。

        关键点：QTextEdit / QPlainTextEdit / QTreeView 等 QAbstractScrollArea
        真正接收鼠标事件的其实是它们的 viewport()（一个普通 QWidget），而非控件本身。
        若只认控件类，viewport 会被误判为"非交互" → 发起窗口拖拽并吞掉鼠标移动事件，
        导致无法在输出框/日志框里用光标框选文本（左侧输出框不能选择的根因）。
        因此这里先把"裸 viewport"映射回其滚动区父控件，再做判断。"""
        from PySide6.QtWidgets import (QAbstractButton, QPushButton, QComboBox,
                                       QLineEdit, QAbstractSpinBox, QScrollBar,
                                       QMenu, QTextEdit, QPlainTextEdit,
                                       QAbstractScrollArea, QAbstractItemView,
                                       QTreeView, QHeaderView,
                                       QSplitter, QSplitterHandle)
        w = widget
        # 认领 viewport：把"裸 QWidget 的 viewport"映射回其滚动区父控件
        parent = w.parent() if isinstance(w, QWidget) else None
        if isinstance(parent, QAbstractScrollArea) and parent.viewport() is w:
            w = parent
        # 认领表头：QHeaderView 是 QTreeView/QTableView 的子控件，
        # 若不加识别，文件管理器表头拖拽列宽会被误判为窗口拖拽。
        if isinstance(w, QHeaderView) and isinstance(parent, QAbstractItemView):
            w = parent
        return isinstance(w, (QAbstractButton, QPushButton, QComboBox,
                              QLineEdit, QAbstractSpinBox, QScrollBar,
                              QMenu, QTextEdit, QPlainTextEdit, QTreeView,
                              QHeaderView, QSplitter, QSplitterHandle))

    def eventFilter(self, obj, event):
        """拦截子控件的鼠标事件，实现子控件区域内的窗口缩放和拖拽。"""
        # 标题栏按钮（最小化/关闭）不参与拖拽缩放，直接放行
        if obj in getattr(self, '_no_track', ()):
            return super().eventFilter(obj, event)
        # 只处理仍属于本窗口的控件；popup / 独立窗口的控件直接放行，
        # 否则 mapTo(self, ...) 可能失败并产生错误坐标，误触发缩放。
        if not self._is_child_of_self(obj):
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                parent_pos = obj.mapTo(self, event.position().toPoint())
                resize_dir = self._get_resize_dir(parent_pos)
                if resize_dir:
                    self._resizing = True
                    self._resize_dir = resize_dir
                    self._resize_origin = event.globalPosition().toPoint()
                    self._resize_geom = self.geometry()
                    return True
                # 非交互控件（空白处/标签/分组框/日志列表等）：发起窗口拖拽，
                # 让无边框窗口任意非控件区域都可拖动。交互控件（按钮/输入框/下拉/
                # 滚动条/文本框等）放行，保持自身点击行为。
                if not self._is_interactive(obj):
                    self._dragging = True
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    self._drag_start = event.globalPosition().toPoint()
                    self._drag_moved = False
                # 点击任意一处「设备」下拉框时自动刷新设备列表（500ms 冷却，避免连点刷爆）
                elif (obj in getattr(self, '_device_combos', ())
                      and not self._resizing):
                    now = time.monotonic()
                    if now - self._last_device_combo_refresh > 0.5:
                        self._last_device_combo_refresh = now
                        self.refresh_devices()
        elif et == QEvent.Type.MouseButtonRelease:
            if self._resizing or self._dragging:
                self._dragging = False
                self._resizing = False
                self._resize_dir = None
                self._drag_moved = False
                self.unsetCursor()
                self._save_geometry_debounced()
                return True
        elif et == QEvent.Type.MouseMove:
            if self._resizing:
                self._do_resize(event.globalPosition().toPoint())
                return True
            elif self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
                # 拖拽阈值：按下后小幅移动（如点选日志行）不触发窗口位移，避免整窗微抖
                if not self._drag_moved:
                    if (event.globalPosition().toPoint() - self._drag_start).manhattanLength() < 4:
                        return True
                    self._drag_moved = True
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            else:
                parent_pos = obj.mapTo(self, event.position().toPoint())
                rd = self._get_resize_dir(parent_pos)
                self._update_cursor(rd)
        elif et == QEvent.Type.HoverMove:
            if not self._resizing and not self._dragging:
                parent_pos = obj.mapTo(self, event.position().toPoint())
                rd = self._get_resize_dir(parent_pos)
                self._update_cursor(rd)
                if rd is not None:
                    return True
        return super().eventFilter(obj, event)

    def _get_resize_dir(self, pos):
        """根据鼠标在窗口内的坐标判断边缘缩放方向，不在边缘返回 None。"""
        rect = self.rect()
        m = self._margin
        left = pos.x() < m
        right = pos.x() > rect.width() - m
        top = pos.y() < m
        bottom = pos.y() > rect.height() - m
        if top and left:
            return Qt.Edge.TopEdge | Qt.Edge.LeftEdge
        elif top and right:
            return Qt.Edge.TopEdge | Qt.Edge.RightEdge
        elif bottom and left:
            return Qt.Edge.BottomEdge | Qt.Edge.LeftEdge
        elif bottom and right:
            return Qt.Edge.BottomEdge | Qt.Edge.RightEdge
        elif left:
            return Qt.Edge.LeftEdge
        elif right:
            return Qt.Edge.RightEdge
        elif bottom:
            return Qt.Edge.BottomEdge
        # 纯顶部（标题栏区域：含 horizontalSpacer_7 那块）不缩放，留给窗口拖拽
        return None

    def _update_cursor(self, resize_dir):
        """根据缩放方向更新鼠标光标形状。"""
        if resize_dir is None:
            self.unsetCursor()
            return
        CS = Qt.CursorShape
        if resize_dir in (Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
                          Qt.Edge.BottomEdge | Qt.Edge.RightEdge):
            self.setCursor(CS.SizeFDiagCursor)
        elif resize_dir in (Qt.Edge.TopEdge | Qt.Edge.RightEdge,
                            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge):
            self.setCursor(CS.SizeBDiagCursor)
        elif resize_dir in (Qt.Edge.LeftEdge, Qt.Edge.RightEdge):
            self.setCursor(CS.SizeHorCursor)
        elif resize_dir in (Qt.Edge.TopEdge, Qt.Edge.BottomEdge):
            self.setCursor(CS.SizeVerCursor)
        else:
            self.setCursor(CS.SizeAllCursor)

    def _do_resize(self, global_pos):
        """根据鼠标全局位移量执行窗口缩放。最小限制放开（1×1），可自由缩到极小。"""
        delta = global_pos - self._resize_origin
        geom = QRect(self._resize_geom)
        min_w, min_h = 1, 1
        if self._resize_dir & Qt.Edge.RightEdge:
            geom.setWidth(max(min_w, self._resize_geom.width() + delta.x()))
        if self._resize_dir & Qt.Edge.LeftEdge:
            new_w = max(min_w, self._resize_geom.width() - delta.x())
            geom.setLeft(self._resize_geom.left() + self._resize_geom.width() - new_w)
            geom.setWidth(new_w)
        if self._resize_dir & Qt.Edge.BottomEdge:
            geom.setHeight(max(min_h, self._resize_geom.height() + delta.y()))
        if self._resize_dir & Qt.Edge.TopEdge:
            new_h = max(min_h, self._resize_geom.height() - delta.y())
            geom.setTop(self._resize_geom.top() + self._resize_geom.height() - new_h)
            geom.setHeight(new_h)
        self.setGeometry(geom)

    # ------------------------------------------------------------------
    # 无边框窗口：标题栏按钮（最小化 / 关闭）+ 主题切换
    # ------------------------------------------------------------------
    def _parse_accent_rgb(self):
        """把当前主题的 accent 字符串 'rgb(29,233,182)' 解析成 (r, g, b) 三元组。"""
        accent = THEMES.get(self._current_theme, THEMES[DEFAULT_THEME])['accent']
        s = accent
        if s.startswith('rgb(') and s.endswith(')'):
            s = s[4:-1]
        parts = [int(p.strip()) for p in s.split(',')[:3]]
        return parts[0], parts[1], parts[2]

    def _load_theme_from_config(self):
        """启动时从 adb_shell_config.json 读 theme 字段，缺省/非法回退默认。"""
        try:
            tid = load_json_config(CONFIG_NAME).get(THEME_CONFIG_KEY)
            if isinstance(tid, str) and tid in THEMES:
                return tid
        except Exception:
            pass
        return DEFAULT_THEME

    def _save_theme_to_config(self, theme_id):
        """把当前主题 id 写入配置。"""
        try:
            cfg = load_json_config(CONFIG_NAME)
            cfg[THEME_CONFIG_KEY] = theme_id
            save_json_config(CONFIG_NAME, cfg)
        except Exception as e:
            print(f'[主题] 保存配置失败: {e}')

    def _init_theme_menu(self):
        """构建主题下拉菜单，6 套主题各一项，点击触发 _switch_theme。"""
        self._theme_menu = QMenu(self)
        self._theme_action_map = {}  # id -> QAction
        for tid in get_theme_ids():
            act = QAction(get_theme_name(tid), self)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, t=tid: self._switch_theme(t))
            self._theme_menu.addAction(act)
            self._theme_action_map[tid] = act
        self.btnTheme.setMenu(self._theme_menu)

    def _refresh_theme_menu_checks(self):
        """把当前主题的菜单项勾上，其余取消。"""
        for tid, act in self._theme_action_map.items():
            act.setChecked(tid == self._current_theme)

    def _switch_theme(self, theme_id):
        """切换主题：setStyleSheet + 重应用标题栏按钮局部样式 + 持久化。"""
        if theme_id not in THEMES or theme_id == self._current_theme:
            return
        self._current_theme = theme_id
        self.setStyleSheet(get_stylesheet(theme_id))
        # 标题栏按钮用 setStyleSheet 单独覆盖过 QSS，必须重新应用以跟主题
        if hasattr(self, '_btn_about') and self._btn_about is not None:
            self._btn_about.setStyleSheet(self._about_btn_style())
        if hasattr(self, '_btn_theme') and self._btn_theme is not None:
            self._btn_theme.setStyleSheet(self._theme_btn_style())
        self._save_theme_to_config(theme_id)
        self._refresh_theme_menu_checks()
        # 把主题同步到已打开的弹窗/子窗口，避免它们停留在旧主题
        self._propagate_theme_to_dialogs(theme_id)

    def _propagate_theme_to_dialogs(self, theme_id):
        """主题切换后，把新样式表刷到已打开的弹窗子窗口。"""
        from PySide6.QtWidgets import QWidget
        sheet = get_stylesheet(theme_id)
        for attr in (
            '_json_tool_dialog', '_wireless_debug_dialog', '_md5_dialog',
            '_timestamp_dialog', '_wifi_dialog', '_tcpdump_dialog',
            '_input_text_dialog', '_lan_scanner_dialog', '_install_dialog',
            '_hash_context_dialog',
        ):
            dlg = getattr(self, attr, None)
            if dlg is None or not isinstance(dlg, QWidget):
                continue
            try:
                # 部分弹窗自己缓存了 _theme_id 并动态生成内部样式，
                # 先尝试调用 apply_theme；否则直接 setStyleSheet。
                apply = getattr(dlg, 'apply_theme', None)
                if callable(apply):
                    apply(theme_id)
                else:
                    dlg.setStyleSheet(sheet)
            except Exception:
                pass

    def _about_btn_style(self):
        """标题栏「关于」按钮样式：跟当前主题强调色一致，hover 高亮。"""
        r, g, b = self._parse_accent_rgb()
        return (f"QPushButton{{background:transparent;border:none;color:rgb({r},{g},{b});"
                f"font:700 10px '{FONT_FAMILY}';border-radius:4px;}}"
                f"QPushButton:hover{{background:rgba({r},{g},{b},35);color:#ffffff;}}"
                f"QPushButton:pressed{{background:rgba({r},{g},{b},60);color:#ffffff;}}")

    def _theme_btn_style(self):
        """标题栏「主题」按钮样式：跟当前主题强调色一致，hover 高亮。"""
        r, g, b = self._parse_accent_rgb()
        return (f"QPushButton{{background:transparent;border:none;color:rgb({r},{g},{b});"
                f"font:700 10px '{FONT_FAMILY}';border-radius:4px;}}"
                f"QPushButton:hover{{background:rgba({r},{g},{b},35);color:#ffffff;}}"
                f"QPushButton:pressed{{background:rgba({r},{g},{b},60);color:#ffffff;}}"
                f"QPushButton::menu-indicator{{image:none;width:0;height:0;}}")

    def _win_btn_style(self, is_close=True):
        """生成标题栏「关闭」按钮的局部样式表。hover 为红色（Windows 风格）。"""
        common = (f"QPushButton{{background:transparent;border:none;color:#cccccc;"
                  f"font:16px 'Segoe UI','{FONT_FAMILY}';border-radius:4px;}}")
        if is_close:
            return (common +
                    "QPushButton:hover{background:#e81123;color:#ffffff;}"
                    "QPushButton:pressed{background:#b0091a;color:#ffffff;}")
        return (common +
                "QPushButton:hover{background:rgba(255,255,255,30);color:#ffffff;}"
                "QPushButton:pressed{background:rgba(255,255,255,55);color:#ffffff;}")

    def _reposition_win_buttons(self):
        """把关闭按钮钉在窗口右上角，在 resizeEvent 和初始化时调用。"""
        if not hasattr(self, '_btn_close'):
            return
        m = 4
        bw = self._btn_close.width()
        self._btn_close.move(self.width() - bw - m, m)
        self._btn_close.raise_()

    def mousePressEvent(self, event):
        """边缘区域进入缩放模式，其余区域进入拖拽模式。"""
        if event.button() == Qt.MouseButton.LeftButton:
            resize_dir = self._get_resize_dir(event.position().toPoint())
            if resize_dir:
                self._resizing = True
                self._resize_dir = resize_dir
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geom = self.geometry()
                event.accept()
            else:
                self._dragging = True
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                self._drag_start = event.globalPosition().toPoint()
                self._drag_moved = False
                event.accept()

    def mouseMoveEvent(self, event):
        """缩放模式下缩放窗口，拖拽模式下移动窗口，空闲时更新光标。"""
        if self._resizing:
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
        elif self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
            if not self._drag_moved:
                if (event.globalPosition().toPoint() - self._drag_start).manhattanLength() < 4:
                    event.accept()
                    return
                self._drag_moved = True
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
        else:
            resize_dir = self._get_resize_dir(event.position().toPoint())
            self._update_cursor(resize_dir)

    def mouseReleaseEvent(self, event):
        """结束拖拽/缩放状态，重置光标。"""
        was_active = self._dragging or self._resizing
        self._dragging = False
        self._resizing = False
        self._resize_dir = None
        self._drag_moved = False
        self.unsetCursor()
        if was_active:
            self._save_geometry_debounced()

    def _on_splitter_moved(self, *_):
        """折叠/拖动左右分隔条后立即重算布局，避免右侧控件残留旧宽度被裁剪。"""
        for _w in (self.splitter, self.layoutWidget, self.layoutWidget1):
            _w.updateGeometry()
        if self.layout() is not None:
            self.layout().activate()
        self.splitter_2.update()

    def bring_to_front(self):
        """被第二个实例触发：把已运行的窗口恢复到前台。"""
        # 从最小化状态恢复
        if self.windowState() & Qt.WindowState.WindowMinimized:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()


# ----------------------------------------------------------------------
# 单实例控制
# ----------------------------------------------------------------------
class SingleInstance(QObject):
    """跨平台单实例。
    启动时尝试连接同名 QLocalServer：
      - 连接成功 → 已有实例在运行，发送激活指令后本进程退出；
      - 连接失败 → 本进程成为主实例并监听，收到连接即激活已有窗口。
    """
    activate = Signal()

    def __init__(self, app_id):
        super().__init__()
        self._app_id = app_id
        self._server = None
        self._primary = False

    def is_primary(self):
        # 1) 探测已有实例
        probe = QLocalSocket()
        probe.connectToServer(self._app_id)
        if probe.waitForConnected(300):
            try:
                probe.write(b'SHOW')
                probe.waitForBytesWritten(300)
            finally:
                probe.close()
            return False
        # 2) 无实例：清理残留并监听
        QLocalServer.removeServer(self._app_id)
        server = QLocalServer()
        if server.listen(self._app_id):
            server.newConnection.connect(self._on_new_connection)
            self._server = server
            self._primary = True
            return True
        # 监听失败（极端情况）退化为允许启动，避免彻底无法打开
        return True

    def _on_new_connection(self):
        server = self._server
        while server is not None and server.hasPendingConnections():
            sock = server.nextPendingConnection()
            sock.readAll()
            sock.disconnectFromServer()
            sock.deleteLater()
        self.activate.emit()

    def cleanup(self):
        if self._server is not None:
            try:
                self._server.close()
            finally:
                QLocalServer.removeServer(self._app_id)
                self._server = None


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    # 应用级窗口图标：任务栏 + 所有顶层窗口（含各弹窗）默认采用此图标
    app.setWindowIcon(QIcon(':/Super_ADB.png'))
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(True)    # 关窗口直接退出

    # ── 加载 Qt 中文翻译（右键菜单 Undo/Cut/Copy/Paste/Select All 等显示中文）──
    import importlib
    _pyside_dir = os.path.dirname(importlib.import_module('PySide6').__file__)
    _trans_dir = os.path.join(_pyside_dir, 'translations')
    for _name in ('qtbase_zh_CN', 'qt_zh_CN'):
        _t = QTranslator()
        if _t.load(_name, _trans_dir):
            app.installTranslator(_t)

    # ── 全局事件过滤器：将所有文本控件的右键菜单替换为中文 ──
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QMenu, QAbstractScrollArea

    _ZH_MENU_MAP = {
        'Undo': '撤消', 'Redo': '重做',
        'Cut': '剪切', '&Cut': '剪切(&T)', 'Cu&t': '剪切(&T)',
        'Copy': '复制', '&Copy': '复制(&C)',
        'Paste': '粘贴', '&Paste': '粘贴(&P)',
        'Delete': '删除',
        'Select All': '全选', 'Select&All': '全选(&A)',
    }

    class _ZhContextMenuFilter(QObject):
        """拦截文本控件右键事件，将标准菜单项文字替换为中文。

        关键：QTextEdit / QPlainTextEdit 等 QAbstractScrollArea 子类的实际鼠标事件
        （含右键 ContextMenu）可能由其内部 viewport()（一个普通 QWidget）接收，
        而非控件本身。因此需要把「裸 viewport」映射回其父滚动区控件再做判断，
        与 _is_interactive() 中的 viewport 认领逻辑保持一致。"""
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.ContextMenu:
                target = obj
                # 认领 viewport：如果事件目标是 QAbstractScrollArea 的 viewport，
                # 映射回父控件（QTextEdit / QPlainTextEdit / QLineEdit 等）
                parent = obj.parent() if isinstance(obj, QWidget) else None
                if (isinstance(parent, QAbstractScrollArea) and
                        parent.viewport() is obj):
                    target = parent
                if (isinstance(target, (QTextEdit, QLineEdit, QPlainTextEdit)) and
                        hasattr(target, 'createStandardContextMenu')):
                    # 先让控件创建默认菜单
                    menu = target.createStandardContextMenu()
                    if menu:
                        for action in menu.actions():
                            orig = action.text()
                            # 逐词匹配替换（保留快捷键标记 &X）
                            new_text = orig
                            for en, zh in _ZH_MENU_MAP.items():
                                if en in new_text:
                                    new_text = new_text.replace(en, zh)
                            if new_text != orig:
                                action.setText(new_text)
                        menu.exec(event.globalPos())
                        return True  # 已处理，不再弹出默认英文菜单
            return super().eventFilter(obj, event)

    _zh_filter = _ZhContextMenuFilter(app)
    app.installEventFilter(_zh_filter)

    # ── 右键「计算哈希」模式：由注册表 command 调用（Super_ADB.exe --hash "%1"）──
    # 在单实例锁之前处理，确保即使主程序已运行，右键哈希仍能独立弹出。
    if '--hash' in sys.argv:
        _hash_paths = [a for a in sys.argv[sys.argv.index('--hash') + 1:]
                        if os.path.isfile(a)]
        if _hash_paths:
            from hash_context_menu import HashContextDialog, compute_hashes_batch
            _hash_results = compute_hashes_batch(_hash_paths)
            _hash_dlg = HashContextDialog(_hash_results)
            _hash_dlg.exec()
        sys.exit(0)

    # ── 单实例：已运行时激活已有窗口而非开新实例 ──
    single = SingleInstance('SuperADB_SingleInstance_v1')
    if not single.is_primary():
        sys.exit(0)

    window = MainWindow()
    single.activate.connect(window.bring_to_front)
    # 开机自启动（--hidden）时不弹主窗口，仅托盘常驻；其余情况正常显示
    if '--hidden' in sys.argv:
        window.hide()
    else:
        window.show()
    rc = app.exec()
    single.cleanup()
    sys.exit(rc)



# ─────────────────────────────────────────────────────────────
# 开机自动启动（Windows 注册表 Run 键，仅打包后的 exe 生效）
# ─────────────────────────────────────────────────────────────
try:
    import winreg
except ImportError:
    winreg = None

_AUTOSTART_REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
_AUTOSTART_REG_NAME = 'Super_ADB'


def _autostart_target():
    """注册表中写入的启动命令：exe 绝对路径 + --hidden。"""
    exe = os.path.abspath(sys.executable)
    return '"%s" --hidden' % exe


def is_autostart_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY) as k:
            winreg.QueryValueEx(k, _AUTOSTART_REG_NAME)
        return True
    except OSError:
        return False


def set_autostart(enable):
    """启用/禁用开机自启动，返回操作是否成功。"""
    if winreg is None:
        print('[autostart] 当前平台不支持开机自启动注册（仅 Windows 有效）')
        return False
    # 仅打包后的 exe 才注册：开发模式（python 源码运行）下 sys.executable
    # 是 python.exe，注册后开机无法正确启动，故拒绝。
    if not getattr(sys, 'frozen', False):
        print('[autostart] 开发模式不注册开机自启动，请打包成 exe 后使用')
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if enable:
                winreg.SetValueEx(k, _AUTOSTART_REG_NAME, 0,
                                  winreg.REG_SZ, _autostart_target())
            else:
                try:
                    winreg.DeleteValue(k, _AUTOSTART_REG_NAME)
                except OSError:
                    pass
        return True
    except OSError as e:
        print('[autostart] 设置开机自启动失败:', e)
        return False


if __name__ == '__main__':
    main()
