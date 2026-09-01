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

# 确保直接运行时也能找到项目根目录（包式导入：Super_ADB_Win/ 为根包搜索路径）
# （本入口位于 项目启动入口/ 下，项目根为其上一级 Super_ADB_Win/）
_here = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
_root = __import__('os').path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)
from ui import png_rc
# 编译后 UI 文件用裸导入 from 收藏下拉框 import FavComboBox / import png_rc，需把对应目录加入 sys.path
for _sub in ('tools', 'ui'):
    _p = __import__('os').path.join(_root, _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from PySide6.QtCore import (Qt, QThreadPool, QRunnable, Signal, QObject,
                                QMetaObject, Q_ARG, QTimer, QEvent, QRect, QPoint,
                                QTranslator, QByteArray, QThread)
    from PySide6.QtGui import (QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPen, QPainterPath)
    from PySide6.QtWidgets import (
        QApplication, QWidget, QPushButton, QTextEdit, QPlainTextEdit,
        QMessageBox, QSystemTrayIcon, QMenu, QLayout,
        QListView, QAbstractSpinBox, QScrollBar, QComboBox,
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
        QAbstractScrollArea, QAbstractItemView,
    )
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
except ImportError as e:
    print(f'错误: 未找到 PySide6 ({e})')
    print('请使用已安装 PySide6 的 Python 运行本工具，例如：')
    print('  D:/Python/Python314/python.exe Super_ADB_Win/app/main.py')
    sys.exit(1)

# 投屏参数设置对话框（已移入 对话框/，由上面的 sys.path 注入包含；
# 以 scrcpy_settings_dialog 别名导入，供 启动scrcpy / 打开scrcpy设置 使用）
from dialogs import scrcpy_settings_dialog as scrcpy_settings_dialog

from ui.Super_ADB import Ui_MainWindow
from tools.adb_tools import Adb设备操作, 加载json配置, 保存json配置
from ui.ui_styles import get_stylesheet, DEFAULT_THEME, THEMES, FONT_FAMILY
from ui.dialog_styles import add_green_glow, highlight_card_style

# 注册 png_rc 资源（含应用图标 :/Super_ADB.png 与公众号二维码），import 即执行 qInitResources()
from ui import png_rc  # noqa: F401

# 包式导入：所有子模块通过 包名.模块名 引用，配合上方 sys.path 注入 Super_ADB_Win/ 根目录
# PyInstaller 打包时 pathex 指向 Super_ADB_Win/，各子目录含 __init__.py 成为正规包
from pages.file_manager_page import 文件管理页
from pages.log_viewer_page import 日志查看器页
from pages.desk_cat import create_desk_cat
# 以下重型子模块改在「用到时才 import」（见各 open_xxx 方法内的局部 import），
# 避免启动即加载 应用性能监控(3400+ 行) 与全部 dialog 模块，降低启动内存。
from app.dialog_launcher import 弹窗打开Mixin
from app.device_manager import 设备管理Mixin
from app.theme_system import 主题系统Mixin

CONFIG_NAME = 'config/super_adb_config.json'
# 首次启动 / 配置缺失或损坏时的默认窗口几何
DEFAULT_GEOMETRY = {'x': 71, 'y': 126, 'w': 1400, 'h': 780}
# 首次启动默认几何（saveGeometry 的 base64 编码，源自 Super_ADB配置.json 的 geometry.b64，
# 含位置/大小/窗口状态；配置文件缺失时优先使用它，比 DEFAULT_GEOMETRY 更精确）
DEFAULT_GEOMETRY_B64 = 'AdnQywADAAAAAAQaAAAAWwAABksAAAOwAAAEGgAAAFsAAAZLAAADsAAAAAAAAAAABkAAAAQaAAAAWwAABksAAAOw'
# 配置文件中主题字段的 key
THEME_CONFIG_KEY = 'theme'

# ----------------------------------------------------------------------
# 主题切换相关常量（优化项7：集中管理魔法数字）
# ----------------------------------------------------------------------
主题重绘延迟毫秒 = 100          # QTimer.singleShot 延迟，避开 QMenu 关闭流程吞掉重绘
主题边框宽度 = 4                 # 主窗口 paintEvent 中 4px 主题色实色边框
线程等待超时毫秒 = 1000          # closeEvent 中等待后台线程的超时


# ----------------------------------------------------------------------
# 后台 Worker
# ----------------------------------------------------------------------
class 工作器信号(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()


class 命令工作器(QRunnable):
    """后台执行返回字符串的函数，并通过信号回传。"""

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.signals = 工作器信号()
        self.setAutoDelete(False)

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class _文本发送器(QObject):
    """后台执行「输入文本」发送逻辑，避免主线程 time.sleep / 同步 adb 调用阻塞 UI。

    - progress/done 信号回写 info_label / status_bar（主线程）。
    - logmsg 信号转发到主线程日志面板（self.日志 内部用 QueuedConnection，线程安全）。
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
            self._执行工作()
        except Exception as e:
            self.logmsg.emit(f'发送异常: {e}')
            self.done.emit(False, '中文输入失败', f'✗ 发送异常: {e}')

    def _执行工作(self):
        has_non_ascii = any(ord(c) >= 128 for c in self.text)
        if not has_non_ascii:
            self._发送ascii()
        else:
            self._发送非ascii()

    def _发送ascii(self):
        lines = self.text.split('\n')
        ok_count = 0
        for i, line in enumerate(lines):
            if i > 0:
                try:
                    self.adb.执行shell(self.serial, 'input keyevent 66',
                                       timeout=5)
                except Exception as e:
                    self.logmsg.emit(f'发送回车失败: {e}')
            if not line:
                continue
            safe = line.replace('\\', '\\\\').replace('"', '\\"')
            try:
                self.adb.执行shell(self.serial, f'input text "{safe}"',
                                   timeout=10)
                ok_count += 1
            except Exception as e:
                self.logmsg.emit(f'输入文本失败: {e}')
                break
        self.done.emit(True, f'已发送 {ok_count} 行 ASCII 文本',
                       f'✓ ASCII → input text ({ok_count} 行)')

    def _发送非ascii(self):
        self.progress.emit('尝试 Win32 剪贴板粘贴…')
        if self._通过原生剪贴板发送文本(self.serial, self.text,
                                                self.old_text):
            line_count = self.text.count('\n') + 1
            self.done.emit(True, f'已通过剪贴板粘贴 {line_count} 行文本',
                           f'✓ 非ASCII → Win32 剪贴板粘贴 ({line_count} 行)')
            return
        self.progress.emit('剪贴板失败, 尝试 ADBKeyBoard…')
        if not self.adbkb_installed[0]:
            self.adbkb_installed[0] = self._检查adb键盘()
        if self.adbkb_installed[0]:
            if self._通过adb键盘发送文本(self.serial, self.text):
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

    def _检查adb键盘(self):
        try:
            ime_list = self.adb.执行shell(self.serial, 'ime list -s',
                                          timeout=5) or ''
            return 'adbkeyboard' in ime_list.lower()
        except Exception:
            return False

    def _通过adb键盘发送文本(self, serial, text):
        """通过 ADBKeyBoard 广播发送文本 (需设备已安装 ADBKeyBoard APK)。"""
        import base64
        try:
            ime_list = self.adb.执行shell(serial, 'ime list -s',
                                          timeout=5) or ''
            if 'adbkeyboard' not in ime_list.lower():
                return False
            self.adb.执行shell(serial,
                'ime enable com.android.adbkeyboard/.AdbIME', timeout=5)
            self.adb.执行shell(serial,
                'ime set com.android.adbkeyboard/.AdbIME', timeout=5)
            time.sleep(0.3)
            b64 = base64.b64encode(text.encode('utf-8')).decode('ascii')
            self.adb.执行shell(serial,
                f'am broadcast -a ADB_INPUT_B64 --es msg "{b64}"', timeout=5)
            return True
        except Exception as e:
            self.logmsg.emit(f'ADBKeyBoard 发送失败: {e}')
            return False

    def _通过原生剪贴板发送文本(self, serial, text, old_text):
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
            self.adb.执行shell(serial, 'input keyevent 279', timeout=5)
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
class 主窗口(QWidget, Ui_MainWindow, 弹窗打开Mixin, 设备管理Mixin, 主题系统Mixin):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        # tabWidget 所有页面设透明背景（QSS 选择器难命中 QStackedWidget 子页面）
        # 注意：WA_NoSystemBackground 只给容器/布局控件设置，交互型控件（按钮/输入框/
        # 下拉框/文本框/列表等）必须排除，否则它们的 hover/pressed 背景色不会刷新，
        # 表现为"鼠标移上去/点下去没有视觉反馈"。
        # QAbstractScrollArea 覆盖 QTextEdit/QPlainTextEdit/QListView/QTreeView/QTableView 等。
        _透明跳过类型 = (QPushButton, QComboBox, QLineEdit, QAbstractSpinBox,
                        QScrollBar, QAbstractScrollArea, QAbstractItemView)
        for _tw in (self.tabWidget, self.tabWidget_2):
            # tabWidget 本身及所有子控件透明
            _tw.setAutoFillBackground(False)
            _tw.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            for _child in _tw.findChildren(QWidget):
                if isinstance(_child, _透明跳过类型):
                    continue
                _child.setAutoFillBackground(False)
                _child.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
            for _i in range(_tw.count()):
                _page = _tw.widget(_i)
                if _page is not None:
                    _page.setStyleSheet("background-color: transparent;")
                    _page.setAutoFillBackground(False)
            # tabBar 标签栏透明
            _tw.tabBar().setStyleSheet("background-color: transparent;")
            _tw.tabBar().setAutoFillBackground(False)
        # leftPanel / splitter_main 也透明
        for _name in ('leftPanel', 'splitter_main'):
            _w = getattr(self, _name, None)
            if _w is not None:
                _w.setStyleSheet("background-color: transparent;")
                _w.setAutoFillBackground(False)
        # ── 主题先于标题栏按钮加载：后面所有 setStyleSheet 都会用 self._current_theme ──
        self._current_theme = self._从配置加载主题()
        # ── 标题栏按钮：关于/环境配置/主题由 .ui 定义，这里只设样式+大小+tooltip+信号 ──
        # 关于按钮
        self.btnAbout.setFixedSize(50, 26)
        self.btnAbout.setToolTip('关于 Super_ADB')
        self.btnAbout.setStyleSheet(self._关于按钮样式())
        self.btnAbout.clicked.connect(self.打开关于对话框)
        # 主题按钮（下拉菜单切换 7 套主题）
        self.btnTheme.setFixedSize(60, 26)
        self.btnTheme.setToolTip('切换主题')
        self.btnTheme.setStyleSheet(self._主题按钮样式())
        self._初始化主题菜单()
        # 环境配置按钮（弹窗展示 ADB 版本/路径 + 一键加 PATH）
        self.btnEnvConfig.setFixedSize(70, 26)
        self.btnEnvConfig.setToolTip('查看当前 ADB 版本/路径，一键配置系统 PATH')
        self.btnEnvConfig.setStyleSheet(self._环境配置按钮样式())
        self.btnEnvConfig.clicked.connect(self.打开环境配置对话框)
        # ── 标题栏品牌标识：关于按钮左侧放「图标 + Super_ADB」 ──
        self._初始化品牌标签()
        # ── 无边框窗口 ──────────────────────────────────────────
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        # 窗口标题由 .ui 文件 (Super_ADB.ui) 的 windowTitle 定义，
        # 这里不再硬覆盖，保持 UI 与逻辑分离。
        # 页面容器不再用工具栏最小宽度顶住 splitter，
        # 修复左侧折叠/窗口变窄后右侧内容溢出被裁剪、需手动拉窗口才恢复的问题
        for _lay in (self.leftPanel.layout(),
                     self.leftPanelWidget.layout(),
                     self.toolsPanelWidget.layout()):
            if _lay is not None:
                _lay.setSizeConstraint(QLayout.SetNoConstraint)
        # 放开主窗口最小尺寸限制：否则窗口缩到比内容所需还小就被布局撑回，
        # 用户「缩小」其实没生效、存的也是被弹回的大尺寸（持久化失效的根因）。
        top_lay = self.layout()
        if top_lay is not None:
            top_lay.setSizeConstraint(QLayout.SetNoConstraint)
        self.setMinimumSize(1, 1)
        self._恢复几何()
        self.splitter_log.setSizes([600, 1200])
        # 默认打开时折叠左侧 adb 调试工具栏，只保留右侧内容区
        self.splitter_main.setCollapsible(1, True)
        self.splitter_main.setStretchFactor(0, 1)
        self.splitter_main.setStretchFactor(1, 0)
        self.splitter_main.setSizes([1, 0])
        self.splitter_main.splitterMoved.connect(self._分割条移动时)
        # 压小设备下拉框最小宽度，让右栏可以缩得更窄而不裁剪控件
        self.deviceCombo.setMinimumWidth(160)
        self.fileMgr_deviceCombo.setMinimumWidth(160)
        self.logViewer_deviceCombo.setMinimumWidth(160)
        self.setWindowIcon(self._创建图标())

        self.adb = Adb设备操作(log_callback=self.日志)
        self.pool = QThreadPool()
        # UI 后台任务（connect / 设备信息 / 各 命令工作器）并发量有限，
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
        self._adb_终端_dialog = None  # 自研 ADB 模式交互式终端弹窗
        self._wireless_debug_dialog = None
        self._wifi_dialog = None
        self._pcap_parser_dialog = None
        self._ip_scan_dialog = None
        self._about_dialog = None
        self._env_config_dialog = None  # 环境配置弹窗（复用同一窗口实例）
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

        self._连接信号()
        self._添加状态栏()
        self._初始化页面()
        self._更新命令行按钮文字()
        # 启用半透明背景以支持圆角窗口
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(self._主样式表(self._current_theme))
        # 强制所有按钮非 flat：flat 按钮在 QSS 下 hover/pressed 背景可能不刷新，
        # 导致鼠标移上去/点下去没有视觉反馈。遍历整个窗口树统一设置。
        for _btn in self.findChildren(QPushButton):
            try:
                _btn.setFlat(False)
            except Exception:
                pass
        self._设置列表背景色(self._current_theme)
        # 无边框窗口标题栏按钮：仅关闭按钮由 .ui 定义；关于/主题在 __init__ 上方代码创建
        self._no_track = set()
        self._btn_close = self.winBtnClose
        self._btn_close.setStyleSheet(self._窗口按钮样式(True))
        self._no_track.add(self._btn_close)

        self._btn_about = self.btnAbout
        self._btn_about.setStyleSheet(self._关于按钮样式())
        self._no_track.add(self._btn_about)

        self._btn_theme = self.btnTheme
        self._btn_theme.setStyleSheet(self._主题按钮样式())
        self._no_track.add(self._btn_theme)

        self._btn_env = self.btnEnvConfig
        self._btn_env.setStyleSheet(self._环境配置按钮样式())
        self._no_track.add(self._btn_env)
        self._刷新主题菜单勾选()  # 同步菜单项选中态

        self._重定位窗口按钮()
        self._设置子控件追踪()          # 必须在 UI 全部构建后：为子控件安装事件过滤器
        self._初始化电脑ip输入()
        self._初始化托盘()
        self._初始化桌面小猫()

        if not self.adb.检查adb():
            self.status_bar.showMessage('adb 不可用（点击右上角「环境配置」一键添加 PATH）', 0)
        else:
            self.刷新设备()

        # 点击设备下拉框自动刷新：记录三处 combo 集合与冷却时间戳
        self._device_combos = {
            self.deviceCombo,
            self.fileMgr_deviceCombo,
            self.logViewer_deviceCombo,
        }
        self._last_device_combo_refresh = 0

        # 调试：按 F12 打印鼠标下控件层级，定位背景色来源
        from PySide6.QtGui import QKeySequence, QShortcut
        self._debug_shortcut = QShortcut(QKeySequence("F12"), self)
        self._debug_shortcut.activated.connect(self._debug_print_widget_under_cursor)

    def _debug_print_widget_under_cursor(self):
        """调试用：打印鼠标光标下控件层级。"""
        from PySide6.QtGui import QCursor
        pos = QCursor.pos()
        w = QApplication.widgetAt(pos)
        chain = []
        while w is not None:
            chain.append(f"{w.metaObject().className()}#{w.objectName() or '?'}")
            w = w.parentWidget()
        print("[控件定位] " + " -> ".join(chain))

    def _连接信号(self):
        """连接 .ui 中所有按钮的信号到业务方法。"""
        # 顶部设备栏
        self.btnRefresh.clicked.connect(self.刷新设备)
        self.btnDisconnect.clicked.connect(self.断开设备)
        # 设备下拉框联动：任意一处切换，其它两处 + ADB终端弹窗同步
        self._syncing_device = False
        self.deviceCombo.currentIndexChanged.connect(lambda _i: self._设备手动切换(self.deviceCombo))
        self.fileMgr_deviceCombo.currentIndexChanged.connect(lambda _i: self._设备手动切换(self.fileMgr_deviceCombo))
        self.logViewer_deviceCombo.currentIndexChanged.connect(lambda _i: self._设备手动切换(self.logViewer_deviceCombo))
        # 连接
        self.btnConnect.clicked.connect(self.连接设备)
        # 系统操作
        self.btnSetProxy.clicked.connect(self.设置代理)
        self.btnClearProxy.clicked.connect(self.清除代理)
        self.btnReboot.clicked.connect(self.重启设备)
        self.btnDeviceInfo.clicked.connect(self.显示设备信息)
        # ── 投屏按钮（双按钮组合：左侧启动 + 右侧下拉菜单）──
        # 控件结构写在 .ui（btnScrcpyContainer 内含 btnScrcpyMain/btnScrcpyMenu），此处只接信号+挂菜单
        self.btnScrcpyMenu.setFixedWidth(24)
        self.btnScrcpyMain.clicked.connect(self.启动scrcpy)
        _scrcpy_menu = QMenu(self)
        _act = _scrcpy_menu.addAction('⚙ 投屏设置…')
        _act.triggered.connect(self.打开scrcpy设置)
        self.btnScrcpyMenu.setMenu(_scrcpy_menu)
        self.btnDpm.clicked.connect(self.打开性能监控)
        self.btnSystemRoot.clicked.connect(self.系统root)
        self.btnInputText.clicked.connect(self.打开输入文本对话框)
        # 应用操作
        self.btnStartApp.clicked.connect(self.启动应用)
        self.btnStopApp.clicked.connect(self.停止应用)
        self.btnMeminfo.clicked.connect(self.显示内存信息)
        self.btnClearApp.clicked.connect(self.清除应用)
        self.btnUninstall.clicked.connect(self.卸载应用)
        self.btnAppInfo.clicked.connect(self.显示应用信息)
        # ── 获取包列表按钮（双按钮组合：左侧默认动作 + 右侧下拉菜单）──
        # 控件结构写在 .ui（btnPkgListContainer 内含 btnPkgMain/btnPkgMenu），此处只接信号+挂菜单
        self.btnPkgMenu.setFixedWidth(24)
        self.btnPkgMain.clicked.connect(self.显示窗口应用)
        _pkg_menu = QMenu(self)
        for _label, _slot in [
            ('📱 界面包', self.显示窗口应用),
            ('🔄 运行中列表', self.显示运行中应用),
            ('📦 第三方包', self.列出第三方应用),
            ('⚙ 系统包', self.列出系统应用),
            ('📋 所有包', self.列出所有应用),
        ]:
            _a = _pkg_menu.addAction(_label)
            _a.triggered.connect(_slot)
        self.btnPkgMenu.setMenu(_pkg_menu)
        self.btnRunningApps_2.clicked.connect(self.打开monkey压测)
        self.btnpm.clicked.connect(self.打开应用监控)
        self.btninstallzip.clicked.connect(self.打开安装对话框)
        self.btnSll.clicked.connect(self.打开证书安装对话框)
        self.btnModifiedTime.clicked.connect(self.打开修改时间对话框)
        # 便捷工具
        self.cmdBtn.clicked.connect(self.打开命令行)
        self.jsonToolBtn.clicked.connect(self.打开json工具)
        self.md5Btn.clicked.connect(self.打开md5校验)
        self.timestampBtn.clicked.connect(self.打开时间戳)
        self.btnWirelessDebug.clicked.connect(self.打开无线调试)
        self.wifiBtn.clicked.connect(self.打开wifi)
        self.pcapParserBtn.clicked.connect(self.打开pcap解析器)
        # 动态添加 IP 扫描按钮到便捷工具区域（第0行第6列，PCAP解析右侧）
        self.ipScanBtn = QPushButton("IP扫描", self.toolsGroup)
        self.ipScanBtn.setObjectName("ipScanBtn")
        self.ipScanBtn.setToolTip("扫描当前局域网内所有在线 IP 设备")
        self.gridLayout_tools.addWidget(self.ipScanBtn, 0, 6, 1, 1)
        self.ipScanBtn.clicked.connect(self.打开ip扫描)
        # 输出
        self.btnClear.clicked.connect(self.output.clear)
        self.btnCopy.clicked.connect(self.复制输出)
        # 页面切换时激活主窗口到前台（避免被独立弹窗挡住）
        self.tabWidget.currentChanged.connect(lambda _: self._激活主窗口到前台())
        self.tabWidget_2.currentChanged.connect(lambda _: self._激活主窗口到前台())

    def _激活主窗口到前台(self):
        """切换页面/点击主窗口时，把主窗口激活到最上层，避免被独立弹窗挡住。"""
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def _添加状态栏(self):
        """.ui 中已定义 QStatusBar，直接引用。"""
        self.status_bar = self.statusBar
        self.status_bar.showMessage('就绪')

    def _初始化页面(self):
        """创建文件管理器和日志查看器控制器，注入 .ui 中预定义的控件。"""
        self.file_mgr = 文件管理页()
        self.file_mgr.inject_widgets(
            tree=self.fileMgr_tree,
            device_combo=self.fileMgr_deviceCombo,
            btn_refresh=self.fileMgr_btnRefresh,
            btn_root=self.fileMgr_btnRoot,
            path_label=self.fileMgr_pathLabel,
            status_label=self.fileMgr_statusLabel,
        )
        # 文件操作（上传/下载等）详细日志输出到主窗口输出区
        self.file_mgr.设置日志回调(self.日志)
        self.log_viewer = 日志查看器页()
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
            hl_edit=self.logViewer_hlEdit,
        )
        # 抓取中设备意外断开（logcat 进程退出）：自动刷新三处设备下拉框
        self.log_viewer.device_disconnected.connect(self.刷新设备)

    # ------------------------------------------------------------------
    # 图标
    # ------------------------------------------------------------------
    def _创建图标(self):
        # 优先使用编译进 png_rc 的资源图标 :/Super_ADB.png
        # （任务栏、系统托盘、各弹窗标题栏统一使用此图标）
        icon = QIcon(':/Super_ADB.png')
        if not icon.isNull():
            return icon
        # 兜底: 磁盘文件（图标已移入 资源/，本文件在 项目启动入口/ 下需上跳一级）
        icon_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'resources', 'Super_ADB.png')
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
    def 日志(self, text: str):
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        html = self._格式化日志html(str(text), now)
        QMetaObject.invokeMethod(
            self.output, 'append',
            Qt.QueuedConnection,
            Q_ARG(str, html),
        )

    @staticmethod
    def _转义日志html(text: str) -> str:
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;'))

    def _格式化日志html(self, text: str, timestamp: str = '') -> str:
        """把纯文本日志转成带配色 HTML，命令/输出/错误/状态分色显示。"""
        # 主题感知配色：根据背景色亮度判断深浅，浅色主题用深色文字，反之亦然
        _t = THEMES.get(self._current_theme, THEMES.get('dark_cyan', {}))
        _bg = _t.get('bg_window', '#1e1e1e')
        try:
            _r, _g, _b = int(_bg[1:3], 16), int(_bg[3:5], 16), int(_bg[5:7], 16)
            _亮度 = (0.299 * _r + 0.587 * _g + 0.114 * _b)
        except Exception:
            _亮度 = 30  # 默认深色
        _is_light = _亮度 > 128
        if _is_light:
            _c_cmd = '#00695c'      # 命令行（深青）
            _c_kw = '#00838f'       # 命令关键字（深青）
            _c_out = '#333333'      # 普通输出（深灰）
            _c_label = '#00838f'    # 键名标签（深青）
            _c_err = '#c62828'      # 错误（深红）
            _c_ok = '#2e7d32'       # 成功（深绿）
            _c_warn = '#f57f17'     # 警告（深黄）
            _c_ts = '#666666'       # 时间戳（中灰）
        else:
            _c_cmd = '#1de9b6'      # 命令行（青）
            _c_kw = '#a7ffeb'       # 命令关键字（浅青）
            _c_out = '#e0e0e0'      # 普通输出（浅灰）
            _c_label = '#80deea'    # 键名标签（青）
            _c_err = '#ff6b6b'      # 错误（红）
            _c_ok = '#69f0ae'       # 成功（绿）
            _c_warn = '#ffd54f'     # 警告（黄）
            _c_ts = '#aaaaaa'       # 时间戳（浅灰，比之前更亮）

        lines = str(text).splitlines()
        body_parts = []
        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            esc = self._转义日志html(line)

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
                        rf'<span style="color:{_c_kw};">\1</span>',
                        colored,
                        flags=re.IGNORECASE,
                    )
                body_parts.append(
                    f'<div style="color:{_c_cmd};font-weight:400;margin-top:3px;">'
                    f'{colored}</div>')
                continue

            low = stripped.lower()
            if (stripped.startswith('错误:') or stripped.startswith('执行异常:')
                    or stripped.startswith('命令执行异常:')
                    or stripped.startswith('失败:') or '失败' in low
                    or 'error:' in low or 'permission denied' in low):
                body_parts.append(
                    f'<div style="color:{_c_err};margin-top:1px;">{esc}</div>')
                continue

            if (stripped.startswith('已') or '成功' in low or '完成' in low
                    or '完成' in low or stripped in ('OK', 'PASS', 'DONE')):
                body_parts.append(
                    f'<div style="color:{_c_ok};margin-top:1px;">{esc}</div>')
                continue

            if stripped.startswith('警告:') or stripped.startswith('注意:'):
                body_parts.append(
                    f'<div style="color:{_c_warn};margin-top:1px;">{esc}</div>')
                continue

            # 普通输出：对常见的 "键: 值" / "键：值" 做键名高亮
            colored = re.sub(
                r'^(\s*[\u4e00-\u9fa5\w\s\(\)/\[\]-]+[:：])\s*(.*)$',
                rf'<span style="color:{_c_label};">\1</span> \2',
                esc,
            )
            body_parts.append(
                f'<div style="color:{_c_out};margin-top:1px;">{colored}</div>')

        body = ''.join(body_parts)
        if not body:
            return ''
        ts_html = (f'<span style="color:{_c_ts};font-size:11px;">[{timestamp}]</span>'
                   if timestamp else '')
        return (f'<div style="margin:4px 0 8px;">'
                f'{ts_html}{body}'
                f'</div>')

    def 设置状态(self, text: str, ok: bool = None):
        prefix = '' if ok is None else ('● ' if ok else '✕ ')
        QMetaObject.invokeMethod(
            self.status_bar, 'showMessage',
            Qt.QueuedConnection,
            Q_ARG(str, prefix + text),
        )

    @staticmethod
    def _设备是否离线(text: str) -> bool:
        """命令结果/错误信息里是否提示设备离线或授权丢失（需要刷新设备列表）。"""
        low = (text or '').lower()
        return ('device offline' in low
                or 'device unauthorized' in low
                or 'device not found' in low
                or 'no devices' in low)

    def _异步运行(self, func, *args, **kwargs):
        """将函数放入线程池后台执行，结果通过 日志 / 设置状态 展示。"""
        self.output.clear()
        worker = 命令工作器(func, *args, **kwargs)

        def _结果返回时(r):
            text = str(r)
            self.日志(text)
            # 执行报错提示设备离线/掉线：自动刷新三处设备下拉框
            if self._设备是否离线(text):
                self.刷新设备()

        def _出错时(e):
            text = str(e)
            self.日志(f'错误: {text}')
            if self._设备是否离线(text):
                self.刷新设备()

        worker.signals.result.connect(_结果返回时)
        worker.signals.error.connect(_出错时)
        worker.signals.finished.connect(lambda: self._丢弃工作器(worker))
        self._live_workers.append(worker)
        self.pool.start(worker)

    def _丢弃工作器(self, worker):
        try:
            self._live_workers.remove(worker)
        except ValueError:
            pass

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 系统操作
    # ------------------------------------------------------------------
    def 设置代理(self):
        serial = self._确保序列号()
        if not serial:
            return
        host_port = (self.pcIpInput.text().strip() if hasattr(self, 'pcIpInput')
                     else f'{self._获取本机ip()}:8888')
        if not host_port:
            self.日志('请先在「PC本机IP」输入框填写 本机IP:端口')
            return
        self._异步运行(self.adb.设置代理, serial, host_port)

    def 清除代理(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.清除代理, serial)

    def 重启设备(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.重启设备, serial)

    def 启动scrcpy(self):
        """启动投屏（官方 scrcpy，独立窗口）。"""
        serial = self._确保序列号()
        if not serial:
            return
        try:
            msg = self.adb.投屏(serial)
            self.日志(msg)
        except Exception as e:
            self.日志(f'启动投屏失败: {e}')
            QMessageBox.warning(self, '投屏失败', f'启动投屏失败:\n{e}')


    def 打开scrcpy设置(self):
        """打开 scrcpy 投屏参数设置对话框（分辨率/码率/帧率/编码/渲染驱动）。平级非模态窗口。"""
        # 已存在则前置，否则新建（和设备信息弹窗/ADB终端弹窗同款平级模式）
        if (hasattr(self, '_scrcpy设置_dialog')
                and self._scrcpy设置_dialog is not None
                and self._scrcpy设置_dialog.isVisible()):
            self._scrcpy设置_dialog.raise_()
            self._scrcpy设置_dialog.activateWindow()
            return
        self._scrcpy设置_dialog = scrcpy_settings_dialog.Scrcpy设置对话框()
        self._scrcpy设置_dialog.show()
        self._scrcpy设置_dialog.raise_()
        self._scrcpy设置_dialog.activateWindow()

    def 显示设备信息(self):
        """弹出设备信息对话框（getprop + 多线程标识符）。"""
        serial = self._确保序列号()
        if not serial:
            return
        self.设置状态('正在获取设备信息…')
        from dialogs.device_info_dialog import 设备信息对话框
        # 关闭旧弹窗
        if hasattr(self, '_设备信息弹窗') and self._设备信息弹窗 is not None:
            try:
                self._设备信息弹窗.close()
            except Exception:
                pass
        self._设备信息弹窗 = 设备信息对话框(
            adb=self.adb, serial=serial,
            theme_id=self._current_theme, pool=self.pool,
        )
        # 弹窗关闭时恢复状态栏
        self._设备信息弹窗.finished.connect(lambda _: self.设置状态('就绪'))
        self._设备信息弹窗.show()
        self._设备信息弹窗.raise_()
        self._设备信息弹窗.activateWindow()
        # 弹窗已打开，立即恢复状态栏（数据在弹窗内异步加载）
        self.设置状态('就绪')

    def _更新getprop框(self, 文本):
        """主线程更新上面的 getprop 框。"""
        if hasattr(self, '_设备信息getprop框') and self._设备信息getprop框 is not None:
            try:
                self._设备信息getprop框.setPlainText(文本)
            except Exception:
                pass

    @staticmethod
    def _显示宽度(s):
        """计算字符串等宽显示宽度（中文/全角算2，英文/半角算1）。"""
        w = 0
        for c in str(s):
            cp = ord(c)
            if (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
                    or 0xFF00 <= cp <= 0xFFEF or 0x2E80 <= cp <= 0x2EFF
                    or 0x3400 <= cp <= 0x4DBF):
                w += 2
            else:
                w += 1
        return w

    @classmethod
    def _对齐填充(cls, s, width):
        """按显示宽度右填充空格到指定宽度。"""
        actual = cls._显示宽度(s)
        if actual >= width:
            return str(s)
        return str(s) + ' ' * (width - actual)

    def _追加标识符行(self, 名称, 值):
        """主线程追加一行标识符到下面的框。"""
        if not hasattr(self, '_设备信息标识符框') or self._设备信息标识符框 is None:
            return
        try:
            当前 = self._设备信息标识符框.toPlainText()
            if 当前.endswith('正在并发获取标识符…\n'):
                当前 = ''
            名称对齐 = self._对齐填充(名称, 16)
            行 = f'  {名称对齐} {值}\n'
            self._设备信息标识符框.setPlainText(当前 + 行)
            # 滚动到底部
            from PySide6.QtGui import QTextCursor
            self._设备信息标识符框.moveCursor(QTextCursor.MoveOperation.End)
        except Exception:
            pass

    # ---- 各标识符独立获取函数（后台线程调用） ----
    def _获取MAC(self, serial):
        """获取 MAC 地址，多路径回退。"""
        for cmd in [
            'cat /sys/class/net/wlan0/address 2>/dev/null',
            "ip link show wlan0 2>/dev/null | grep -oE '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}' | head -n1",
            'settings get secure wifi_mac_address 2>/dev/null',
        ]:
            try:
                v = self.adb.执行shell(serial, cmd, timeout=3).strip()
                if v and v != '02:00:00:00:00:00':
                    return v
            except Exception:
                continue
        return 'N/A(隐私保护)'

    def _获取IMEI(self, serial):
        """获取 IMEI，多路径回退。"""
        for cmd in [
            'getprop gsm.imei 2>/dev/null',
            'getprop ro.ril.imei 2>/dev/null',
            "timeout 3 service call iphonesubinfo 1 2>/dev/null | tr -d \"'\" | grep -oE '[0-9]{15}' | head -n1",
            "timeout 3 dumpsys telephony.registry 2>/dev/null | grep -i mImei | head -n1 | grep -oE '[0-9]{15}'",
        ]:
            try:
                v = self.adb.执行shell(serial, cmd, timeout=5).strip()
                if v:
                    return v
            except Exception:
                continue
        return 'N/A(权限受限)'

    def _获取GAID(self, serial):
        """获取 Google 广告 ID。"""
        try:
            v = self.adb.执行shell(serial, 'settings get secure advertising_id 2>/dev/null', timeout=3).strip()
            if v and v != 'null':
                return v
        except Exception:
            pass
        return 'N/A'

    def _获取OAID(self, serial):
        """获取 OAID，查询多厂商 content provider，提取 UUID。"""
        import re as _re
        uris = [
            'content://com.miui.idprovider/uniform_id',
            'content://com.miui.id.provider/oaid',
            'content://com.bun.miitmdid.provider/oaid',
            'content://com.mdid.msa.provider/oaid',
            'content://com.huawei.hwid.oaid/oaid',
            'content://com.heytap.openid.oaid/oaid',
            'content://com.coloros.mcs.oaid/oaid',
            'content://com.vivo.vms.oaid/oaid',
        ]
        for uri in uris:
            try:
                raw = self.adb.执行shell(serial, f'timeout 1 content query --uri {uri} 2>/dev/null', timeout=3)
                m = _re.search(
                    r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', raw or '')
                if m:
                    return m.group(0)
            except Exception:
                continue
        try:
            v = self.adb.执行shell(serial, 'settings get secure oaid 2>/dev/null', timeout=2).strip()
            if v:
                return v
        except Exception:
            pass
        return 'N/A(未安装移动安全联盟SDK)'

    def _获取AndroidID(self, serial):
        """获取 Android ID。"""
        try:
            v = self.adb.执行shell(serial, 'settings get secure android_id 2>/dev/null', timeout=3).strip()
            if v:
                return v
        except Exception:
            pass
        return 'N/A'

    def _获取系统时间(self, serial):
        """获取设备系统时间。"""
        try:
            v = self.adb.执行shell(serial, "date '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null", timeout=3).strip()
            if v:
                return v
        except Exception:
            pass
        try:
            return self.adb.执行shell(serial, 'date 2>/dev/null', timeout=3).strip()
        except Exception:
            return 'N/A'

    def _获取WiFiIP(self, serial):
        """获取 WiFi IP 地址，多路径回退。"""
        import re as _re
        for cmd in [
            "ip addr show wlan0 2>/dev/null | grep 'inet ' | awk '{print $2}' | cut -d/ -f1",
            "ifconfig wlan0 2>/dev/null | grep -oE 'inet [0-9.]+' | awk '{print $2}'",
            "getprop dhcp.wlan0.ipaddress 2>/dev/null",
            "ip route 2>/dev/null | grep -oE 'src [0-9.]+' | awk '{print $2}' | head -n1",
        ]:
            try:
                v = self.adb.执行shell(serial, cmd, timeout=3).strip()
                if v and _re.match(r'^\d+\.\d+\.\d+\.\d+$', v):
                    return v
            except Exception:
                continue
        return 'N/WiFi未连接'

    def _获取电池信息(self, serial):
        """获取电池状态、电量、温度。"""
        try:
            raw = self.adb.执行shell(serial, 'dumpsys battery 2>/dev/null', timeout=5)
            info = {}
            for line in (raw or '').splitlines():
                line = line.strip()
                if ':' in line:
                    k, v = line.split(':', 1)
                    info[k.strip().lower()] = v.strip()
            电量 = info.get('level', '?')
            状态码 = info.get('status', '?')
            状态Map = {'2': '充电中', '3': '未充电', '4': '未接电源', '5': '充满'}
            状态 = 状态Map.get(状态码, 状态码)
            温度 = info.get('temperature', '?')
            if 温度.isdigit():
                温度 = f'{int(温度)/10:.1f}°C'
            健康码 = info.get('health', '?')
            健康Map = {'2': '良好', '3': '过热', '4': '损坏', '5': '过压', '6': '未知故障', '7': '低温'}
            健康 = 健康Map.get(健康码, 健康码)
            return f'电量{电量}% {状态} {温度} 健康:{健康}'
        except Exception:
            return 'N/A'

    def _获取存储信息(self, serial):
        """获取 /data 分区存储使用情况。"""
        try:
            raw = self.adb.执行shell(serial, 'df /data 2>/dev/null', timeout=5)
            lines = [l for l in (raw or '').splitlines() if l.strip()]
            if len(lines) >= 2:
                parts = lines[-1].split()
                if len(parts) >= 5:
                    总大小, 已用, 可用, 使用率 = parts[1], parts[2], parts[3], parts[4]
                    return f'已用{已用}/{总大小} 可用{可用} 使用率{使用率}'
        except Exception:
            pass
        return 'N/A'

    def _获取内存信息(self, serial):
        """获取内存总量和可用量。"""
        try:
            raw = self.adb.执行shell(serial, 'cat /proc/meminfo 2>/dev/null', timeout=3)
            total = avail = free = ''
            for line in (raw or '').splitlines():
                if line.startswith('MemTotal:'):
                    total = line.split()[1]
                elif line.startswith('MemAvailable:'):
                    avail = line.split()[1]
                elif line.startswith('MemFree:'):
                    free = line.split()[1]
            if total:
                total_gb = int(total) / 1024 / 1024
                if avail:
                    avail_gb = int(avail) / 1024 / 1024
                    used_gb = total_gb - avail_gb
                    pct = used_gb / total_gb * 100
                    return f'总{total_gb:.1f}GB 已用{used_gb:.1f}GB 可用{avail_gb:.1f}GB ({pct:.0f}%)'
                return f'总{total_gb:.1f}GB'
        except Exception:
            pass
        return 'N/A'

    def _格式化getprop(self, getprop_raw, serial):
        """把 getprop 输出格式化为中文分组文本（仅属性部分）。"""
        # 属性映射表：key -> (中文解释, 分组)
        分组顺序 = [
            '设备基本信息', '系统版本', '硬件信息', '系统状态',
            '网络与连接', '区域与语言', '内存与虚拟机', '构建信息', '其他属性',
        ]
        属性映射 = {
            'ro.product.model': ('设备型号', '设备基本信息'),
            'ro.product.brand': ('品牌', '设备基本信息'),
            'ro.product.manufacturer': ('制造商', '设备基本信息'),
            'ro.product.device': ('设备代号', '设备基本信息'),
            'ro.product.name': ('产品名称', '设备基本信息'),
            'ro.serialno': ('序列号', '设备基本信息'),
            'ro.boot.serialno': ('启动序列号', '设备基本信息'),
            'ro.product.marketname': ('市场名称', '设备基本信息'),
            'ro.build.version.release': ('Android版本', '系统版本'),
            'ro.build.version.sdk': ('SDK版本', '系统版本'),
            'ro.build.version.incremental': ('增量版本号', '系统版本'),
            'ro.build.id': ('构建ID', '系统版本'),
            'ro.build.display.id': ('显示版本', '系统版本'),
            'ro.build.version.security_patch': ('安全补丁级别', '系统版本'),
            'ro.build.version.codename': ('版本代号', '系统版本'),
            'ro.build.version.base_os': ('基础OS版本', '系统版本'),
            'ro.product.first_api_level': ('出厂API级别', '系统版本'),
            'ro.build.version.min_supported_target_sdk': ('最低支持SDK', '系统版本'),
            'ro.product.cpu.abi': ('主CPU架构', '硬件信息'),
            'ro.product.cpu.abilist': ('支持的CPU架构', '硬件信息'),
            'ro.product.cpu.abilist32': ('32位CPU架构', '硬件信息'),
            'ro.product.cpu.abilist64': ('64位CPU架构', '硬件信息'),
            'ro.hardware': ('硬件名称', '硬件信息'),
            'ro.hardware.chipname': ('芯片名称', '硬件信息'),
            'ro.board.platform': ('主板平台', '硬件信息'),
            'ro.boot.soc_id': ('SoC型号', '硬件信息'),
            'ro.product.board': ('主板', '硬件信息'),
            'ro.sf.lcd_density': ('屏幕密度(dpi)', '硬件信息'),
            'ro.opengles.version': ('OpenGL ES版本', '硬件信息'),
            'ro.config.low_ram': ('低内存设备', '硬件信息'),
            'ro.bootloader': ('引导程序版本', '硬件信息'),
            'ro.boot.revision': ('硬件修订版本', '硬件信息'),
            'ro.baseband': ('基带版本', '硬件信息'),
            'ro.modem': ('调制解调器版本', '硬件信息'),
            'ro.hardware.egl': ('EGL渲染器', '硬件信息'),
            'ro.hardware.vulkan': ('Vulkan版本', '硬件信息'),
            'ro.build.type': ('构建类型(user/userdebug/eng)', '系统状态'),
            'ro.build.tags': ('构建标签', '系统状态'),
            'ro.build.flavor': ('构建风格', '系统状态'),
            'ro.secure': ('安全模式(1=开启)', '系统状态'),
            'ro.adb.secure': ('ADB安全模式(1=开启)', '系统状态'),
            'ro.debuggable': ('可调试(1=开启)', '系统状态'),
            'ro.build.selinux': ('SELinux状态', '系统状态'),
            'ro.boot.verifiedbootstate': ('验证启动状态', '系统状态'),
            'ro.boot.veritymode': ('dm-verity模式', '系统状态'),
            'ro.boot.warranty_bit': ('保修位(0=未root,1=已修改)', '系统状态'),
            'ro.boot.mode': ('启动模式', '系统状态'),
            'ro.boot.hardware': ('启动硬件', '系统状态'),
            'ro.telephony.default_network': ('默认网络模式', '网络与连接'),
            'sys.usb.config': ('当前USB配置', '网络与连接'),
            'sys.usb.state': ('USB状态', '网络与连接'),
            'persist.sys.usb.config': ('持久USB配置', '网络与连接'),
            'gsm.version.baseband': ('基带版本', '网络与连接'),
            'ro.ril.wifi.chip': ('WiFi芯片', '网络与连接'),
            'ro.product.locale': ('系统区域', '区域与语言'),
            'ro.product.locale.language': ('系统语言', '区域与语言'),
            'ro.product.locale.region': ('系统地区', '区域与语言'),
            'persist.sys.timezone': ('时区', '区域与语言'),
            'persist.sys.language': ('当前语言', '区域与语言'),
            'persist.sys.country': ('当前国家', '区域与语言'),
            'dalvik.vm.heapsize': ('虚拟机堆大小', '内存与虚拟机'),
            'dalvik.vm.heapstartsize': ('堆起始大小', '内存与虚拟机'),
            'dalvik.vm.heapgrowthlimit': ('堆增长限制', '内存与虚拟机'),
            'dalvik.vm.heaptargetutilization': ('堆目标利用率', '内存与虚拟机'),
            'dalvik.vm.heapminfree': ('堆最小空闲', '内存与虚拟机'),
            'dalvik.vm.heapmaxfree': ('堆最大空闲', '内存与虚拟机'),
            'ro.build.fingerprint': ('构建指纹', '构建信息'),
            'ro.build.description': ('构建描述', '构建信息'),
            'ro.build.date': ('构建日期', '构建信息'),
            'ro.build.date.utc': ('构建日期(UTC秒)', '构建信息'),
            'ro.build.host': ('构建主机', '构建信息'),
            'ro.build.user': ('构建用户', '构建信息'),
            'ro.build.product': ('构建产品', '构建信息'),
            'ro.build.version.all_codenames': ('所有版本代号', '构建信息'),
        }

        # 解析 getprop
        props = {}
        for line in (getprop_raw or '').splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith('[') and ']:' in line:
                key = line[1:line.index(']')]
                val_part = line[line.index(']:') + 2:].strip()
                if val_part.startswith('[') and val_part.endswith(']'):
                    val_part = val_part[1:-1]
                props[key] = val_part

        分组数据 = {g: [] for g in 分组顺序}
        已映射 = set()
        for key, (中文名, 分组) in 属性映射.items():
            if key in props:
                分组数据[分组].append((中文名, props[key]))
                已映射.add(key)
        for key, val in sorted(props.items()):
            if key not in 已映射:
                分组数据['其他属性'].append((key, val))

        lines_out = [f'设备序列号: {serial}', f'属性总数: {len(props)}', '=' * 50, '']
        for 分组 in 分组顺序:
            items = 分组数据[分组]
            if not items:
                continue
            lines_out.append(f'【{分组}】')
            lines_out.append('-' * 40)
            for 中文名, val in items:
                lines_out.append(f'  {self._对齐填充(中文名, 18)} {val}')
            lines_out.append('')
        return '\n'.join(lines_out)

    def 打开修改时间对话框(self):
        """弹出修改设备系统时间对话框。"""
        serial = self._确保序列号()
        if not serial:
            return
        from dialogs.change_time_dialog import 修改时间对话框
        # 关闭旧弹窗
        if hasattr(self, '_修改时间弹窗') and self._修改时间弹窗 is not None:
            try:
                self._修改时间弹窗.close()
            except Exception:
                pass
        self._修改时间弹窗 = 修改时间对话框(
            adb=self.adb, serial=serial,
            theme_id=self._current_theme, pool=self.pool,
            状态回调=self.设置状态,
        )
        self._修改时间弹窗.show()
        self._修改时间弹窗.raise_()
        self._修改时间弹窗.activateWindow()

    def 显示logcat(self):
        serial = self._确保序列号()
        if not serial:
            return
        self.output.clear()
        self.日志('正在打开独立 logcat 窗口...')
        threading.Thread(target=lambda: self.日志(self.adb.logcat到桌面(serial)), daemon=True).start()

    def 系统root(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.root并重新挂载, serial)

    # ------------------------------------------------------------------
    # 输入文本
    # ------------------------------------------------------------------
    def 打开输入文本对话框(self):
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
        serial = self._确保序列号()
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
        card.setStyleSheet(highlight_card_style(self._current_theme))
        from PySide6.QtGui import QColor
        add_green_glow(card, accent=QColor(THEMES[self._current_theme]['accent']))

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

        def _检查adb键盘():
            try:
                ime_list = self.adb.执行shell(
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

        def _打开下载():
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
                self.日志(f'打开下载页失败: {e}')
                info_label.setText(
                    f'请手动访问: https://github.com/senzhk/ADBKeyBoard ({e})')

        btn_install.clicked.connect(_打开下载)

        # 启动时异步检测
        threading.Thread(target=_检查adb键盘, daemon=True).start()

        def _执行发送():
            text = edit.toPlainText()
            if not text:
                return
            btn_send.setEnabled(False)
            dlg.setWindowTitle('发送中…')

            # 旧剪贴板读取必须在主线程 (Qt clipboard 属 GUI 资源)
            from PySide6.QtGui import QGuiApplication
            old_text = QGuiApplication.clipboard().text()

            sender = _文本发送器(self.adb, serial, text, old_text,
                                 adbkb_installed)
            sender.progress.connect(info_label.setText)
            sender.logmsg.connect(self.日志)

            thread = QThread()
            sender.moveToThread(thread)
            thread.started.connect(sender.run)
            thread.finished.connect(thread.deleteLater)

            # 用 QObject 接收信号, 确保回调在主线程执行
            class _完成接收器(QObject):
                完成信号 = Signal(bool, str, str)
            接收器 = _完成接收器()
            def _发送完成时(ok, status_text, info_text):
                try:
                    self.设置状态(status_text, ok=ok)
                    info_label.setText(info_text)
                    dlg.setWindowTitle('输入文本 (支持中文)')
                    edit.clear()
                except Exception as e:
                    self.日志(f'输入文本完成回调异常: {e}')
                finally:
                    btn_send.setEnabled(True)
                    sender.deleteLater()
                    thread.quit()
            接收器.完成信号.connect(_发送完成时)
            sender.done.connect(接收器.完成信号)
            self._input_sender = (sender, thread, 接收器)  # 防止被提前 GC
            thread.start()


        btn_send.clicked.connect(_执行发送)
        # 回车快捷发送
        from PySide6.QtGui import QShortcut, QKeySequence
        QShortcut(QKeySequence('Ctrl+Return'), dlg, activated=_执行发送)

        main_lay = QVBoxLayout(dlg)
        main_lay.setContentsMargins(10, 10, 10, 10)
        main_lay.addWidget(card)

        dlg.show()
        self._input_text_dialog = dlg

    # 注：中文输入发送逻辑已重构为模块级 _文本发送器 worker（见文件顶部），
    # 原 _send_text_via_* 两个 主窗口 方法整体迁入，避免在按钮回调主线程
    # 内 time.sleep(1.5/0.3) + 多次同步 adb 调用导致 UI 冻结。


    # ------------------------------------------------------------------
    # 应用操作
    # ------------------------------------------------------------------
    @staticmethod
    def _规范化包名(raw):
        """把用户可能粘贴的 `pkg/Activity`、尾随 `/` 规范成纯包名。

        dumpsys meminfo / pidof / monkey 等命令只接受纯包名，带 `/` 或
        Activity 后缀会导致命令失败（表现为内存各项全部「未获取」）。"""
        if not raw:
            return raw
        s = raw.strip().rstrip('/')
        if '/' in s:
            s = s.split('/', 1)[0]
        return s.strip()

    def _包名(self):
        name = self._规范化包名(self.pkgInput.text())
        if not name:
            self.日志('请输入包名')
        return name

    def 启动应用(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self._异步运行(self.adb.启动应用, serial, pkg)

    def 停止应用(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self._异步运行(self.adb.停止应用, serial, pkg)

    def 显示内存信息(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self.output.clear()

        def _任务():
            raw = self.adb.获取内存信息(serial, pkg)
            return self._格式化内存信息(raw, pkg)

        self._异步运行(_任务)

    # ------------------------------------------------------------------
    # meminfo 结果简化（展示层）：只保留关键内存指标
    # ------------------------------------------------------------------
    @staticmethod
    def _格式化千字节(text: str) -> str:
        """把 KB 数值格式化为 KB + MB 双单位。"""
        try:
            kb = int(text.strip())
        except ValueError:
            return text.strip()
        return f'{kb} KB ({kb / 1024:.1f} MB)' if kb >= 1024 else f'{kb} KB'

    @classmethod
    def _格式化内存信息(cls, raw: str, pkg: str) -> str:
        lines = [f'包名: {pkg}']
        m = re.search(r'MEMINFO in pid (\d+)', raw)
        if m:
            lines.append(f'进程 PID: {m.group(1)}')

        # 优先用 应用性能监控 里已兼容新旧 Android 的解析器
        from monitoring.app_performance_monitor import _parse_meminfo
        parsed = _parse_meminfo(raw)
        if 'pss_mb' in parsed:
            lines.append(f'总 PSS: {cls._格式化千字节(str(int(parsed["pss_mb"] * 1024)))}')
        if 'rss_mb' in parsed:
            lines.append(f'总 RSS: {cls._格式化千字节(str(int(parsed["rss_mb"] * 1024)))}')

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
                lines.append(f'{name}: {cls._格式化千字节(str(val_kb))}')
            else:
                m = re.search(rf'{re.escape(raw_key)}:\s*(\d+)', raw)
                lines.append(f'{name}: {cls._格式化千字节(m.group(1)) if m else "未获取"}')
        return '\n'.join(lines)

    def 清除应用(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self._异步运行(self.adb.清除应用, serial, pkg)

    def 卸载应用(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self._异步运行(self.adb.卸载应用, serial, pkg)

    def 显示应用信息(self):
        serial = self._确保序列号()
        pkg = self._包名()
        if not serial or not pkg:
            return
        self._异步运行(self.adb.获取应用信息, serial, pkg)

    def 列出第三方应用(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.获取应用列表, serial, '-3')

    def 列出系统应用(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.获取应用列表, serial, '-s')

    def 列出所有应用(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.获取应用列表, serial, '')

    def 显示窗口应用(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.获取当前界面应用, serial)

    def 显示运行中应用(self):
        serial = self._确保序列号()
        if not serial:
            return
        self._异步运行(self.adb.获取运行中应用, serial)

    # ------------------------------------------------------------------
    # 输出操作
    # ------------------------------------------------------------------
    def 复制输出(self):
        clipboard = QApplication.clipboard()
        clipboard.setText(self.output.toPlainText())
        self.设置状态('已复制输出', ok=True)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    # PC 本机 IP 输入框（系统操作栏）
    # ------------------------------------------------------------------
    def _初始化电脑ip输入(self):
        """系统操作栏「PC本机IP」输入框与「tcpdump 抓包」按钮已在 ui/Super_ADB.ui
        的 sysGroup 顶部定义（pcIpLabel / pcIpInput / btnRefreshIp / btnTcpdump），
        由 setupUi 创建。这里只补设动态属性与信号连接（控件本身不再由代码 new）。"""
        self.pcIpInput.setPlaceholderText('本机IP:端口')
        self.pcIpInput.setClearButtonEnabled(True)
        self.pcIpInput.setToolTip('本机(电脑)IP:端口，设置代理时使用。默认本机IP:8888，可手动修改')
        self.pcIpInput.setText(self._组合默认ip端口())
        self.btnRefreshIp.setToolTip('重新获取本机 IP（切换网络后点击更新）')
        self.btnRefreshIp.clicked.connect(self._刷新电脑ip)
        self.btnTcpdump.setFixedWidth(120)
        self.btnTcpdump.clicked.connect(self.打开tcpdump对话框)
        self.pcIpLabel.setToolTip('本机(电脑)IP，用于给手机设置代理。格式 IP:端口，例如 192.168.1.10:8888')

    def _刷新电脑ip(self):
        """刷新 PC 本机 IP，保留用户当前输入的端口。"""
        current = self.pcIpInput.text().strip()
        port = '8888'
        if ':' in current:
            _, port = current.rsplit(':', 1)
            if not port or not port.isdigit():
                port = '8888'
        new_ip = self._获取本机ip()
        self.pcIpInput.setText(f'{new_ip}:{port}')
        self.日志(f'本机 IP 已刷新: {new_ip}:{port}')

        # 若无线调试面板正打开，同步刷新其局域网扫描页的本机网段
        if (self._wireless_debug_dialog is not None
                and self._wireless_debug_dialog.isVisible()):
            try:
                self._wireless_debug_dialog._lan_dialog.refresh_network_range()
            except Exception as e:
                self.日志(f'刷新无线调试网段失败: {e}')

    @staticmethod
    def _组合默认ip端口(port='8888'):
        return f'{主窗口._获取本机ip()}:{port}'

    @staticmethod
    def _获取本机ip():
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
        except Exception as e:
            print(f'[网络] IP 检测方法1(UDP)失败: {e!r}')

        # 方法 2：解析主机名，过滤 loopback 与链路本地地址
        try:
            hostname = socket.gethostname()
            addrs = socket.getaddrinfo(hostname, None, socket.AF_INET)
            for addr in addrs:
                ip = addr[4][0]
                if ip and not ip.startswith('127.') and not ip.startswith('169.254.'):
                    return ip
        except Exception as e:
            print(f'[网络] IP 检测方法2(getaddrinfo)失败: {e!r}')

        # 兜底：仍尝试 gethostbyname，给用户一个可编辑的值
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception:
            return '127.0.0.1'

    # ------------------------------------------------------------------
    # 窗口几何持久化
    # ------------------------------------------------------------------
    def _恢复几何(self):
        """加载窗口几何到 self._restore_blob（新格式 QByteArray）或 self._restore_rect
        （旧格式 QRect）。真正应用延迟到首次 showEvent，避免无边框窗口在 show() 时被
        WM 重置几何。

        新格式存 saveGeometry() 的 base64 字节，自动包含位置/大小/窗口状态（最大化/
        还原）与多屏坐标；旧版本 {x,y,w,h} 字典格式向后兼容。"""
        self._geometry_restored = False
        g = 加载json配置(CONFIG_NAME).get('geometry') or {}
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
        # 首次启动 / 配置缺失：优先用内嵌的默认几何 blob（源自 Super_ADB配置.json
        # 的 geometry.b64，精确包含位置/大小/最大化状态），无效时才回退旧格式矩形。
        d = DEFAULT_GEOMETRY
        blob = QByteArray.fromBase64(DEFAULT_GEOMETRY_B64.encode('ascii'))
        if not blob.isEmpty():
            self._restore_blob = blob
            self._restore_rect = None
        else:
            self._restore_rect = QRect(d['x'], d['y'], d['w'], d['h'])
            self._restore_blob = None

    def _保存几何(self):
        # 始终记录当前几何（含窗口状态）。saveGeometry 会编码最小化/最大化等状态，
        # 因此即便最小化时退出，下次也会还原到对应状态，不会丢尺寸/位置。
        blob = self.saveGeometry()
        cfg = 加载json配置(CONFIG_NAME)
        cfg['geometry'] = {'b64': bytes(blob.toBase64()).decode('ascii')}
        保存json配置(CONFIG_NAME, cfg)

    def _防抖保存几何(self):
        """移动/缩放防抖保存：停顿 300ms 后才写盘，避免拖动过程高频写入。"""
        if not hasattr(self, '_geo_timer'):
            self._geo_timer = QTimer(self)
            self._geo_timer.setSingleShot(True)
            self._geo_timer.timeout.connect(self._保存几何)
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
            self._更新桌面小猫边界()

    def changeEvent(self, ev):
        """窗口状态变化（最小化/最大化/还原）时持久化，下次启动沿用。"""
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.WindowStateChange:
            self._防抖保存几何()

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._关闭活动弹窗()
        self._防抖保存几何()
        self._更新桌面小猫边界()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._关闭活动弹窗()
        self._重定位窗口按钮()
        self._防抖保存几何()
        self._更新桌面小猫边界()

    def hideEvent(self, ev):
        """主窗口隐藏（最小化 / 入托盘）时，小猫同步隐藏。"""
        super().hideEvent(ev)
        if self._desk_cat is not None:
            self._desk_cat.hide()

    def paintEvent(self, ev):
        """主窗口 paint：圆角背景 + 4px 主题色实色边框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # ── 圆角背景 ──
        t = THEMES.get(self._current_theme, THEMES[DEFAULT_THEME])
        bg_color = QColor(t['bg_window'])
        圆角半径 = 12
        path = QPainterPath()
        path.addRoundedRect(self.rect(), 圆角半径, 圆角半径)
        painter.fillPath(path, bg_color)
        # ── 子控件正常绘制（覆盖在圆角背景之上）──
        super().paintEvent(ev)
        # ── 4px 主题色实色边框画最上层（不透明，与 PCAP/无线调试弹窗同亮度）──
        r, g, b = self._解析强调色rgb()
        pen = QPen(QColor(r, g, b), 4)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 圆角半径, 圆角半径)

    def nativeEvent(self, eventType, message):
        """无边框窗口兜底：把 ``WM_NCHITTEST`` 强制返回 ``HTCLIENT``。

        保证标题栏、空白区等不会因为命中测试被误判为窗口外区域，拖拽移动
        与按钮点击正常。resize 边缘仍由 ``_获取缩放方向`` + ``_更新光标``
        在 Qt mouse 逻辑里处理。
        """
        if eventType == b'windows_generic_MSG':
            try:
                import ctypes
                from ctypes.wintypes import HWND, UINT, WPARAM, LPARAM, DWORD, POINT
                # PySide6 6.x 未稳定导出 MSG 类型，用 ctypes 自行定义结构
                # 从 PySide6 传入的原生指针地址解析（message 即 MSG*）
                class _MSG(ctypes.Structure):
                    _fields_ = [
                        ("hwnd", HWND),
                        ("message", UINT),
                        ("wParam", WPARAM),
                        ("lParam", LPARAM),
                        ("time", DWORD),
                        ("pt", POINT),
                    ]
                msg = _MSG.from_address(int(message))
                if msg.message == 0x84:  # WM_NCHITTEST
                    return True, 1  # HTCLIENT
            except Exception:
                return super().nativeEvent(eventType, message)
        return super().nativeEvent(eventType, message)

    def _关闭活动弹窗(self):
        """主窗口移动或缩放时关闭已弹出的 QComboBox 下拉框，避免错位。"""
        popup = QApplication.activePopupWidget()
        if popup is not None and popup is not self:
            popup.close()

    def closeEvent(self, ev):
        """点 ✕ 直接关闭窗口并退出程序。"""
        self._保存几何()
        ev.accept()

    def _隐藏到托盘(self):
        self._保存几何()
        self.hide()
        self.tray_icon.showMessage(
            'Super_ADB', '已隐藏到托盘，单击托盘图标恢复，右键"退出"可彻底关闭。',
            QSystemTrayIcon.MessageIcon.Information, 3000)

    def _托盘激活时(self, reason):
        """单击托盘图标恢复窗口。"""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()
            self.raise_()
            self.activateWindow()

    def _初始化托盘(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self._创建图标())
        self.tray_icon.setToolTip('Super_ADB')
        tray_menu = QMenu()
        show_action = QAction('显示', self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)

        # 开机自动启动（仅打包后的 exe 生效；勾选写入当前用户 Run 键）
        autostart_action = QAction('开机自动启动', self)
        autostart_action.setCheckable(True)
        autostart_action.setChecked(自启动是否启用())
        autostart_action.setToolTip('勾选后开机自动在后台托盘运行（不弹主窗口）')
        def _自启动切换时(checked):
            设置自启动(checked)
            autostart_action.setChecked(自启动是否启用())
        autostart_action.triggered.connect(_自启动切换时)
        tray_menu.addAction(autostart_action)

        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self._退出应用)
        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._托盘激活时)
        self.tray_icon.show()

    def _初始化桌面小猫(self):
        """初始化桌面宠物小猫，使用打包进资源的橘白小猫图片（:/desk_cat.png）。"""
        # 图片已编译进 png_rc.py（ui/png.qrc），打包后无需外部文件
        image_path = ':/desk_cat.png'
        try:
            self._desk_cat = create_desk_cat(self, image_path=image_path, size=85)
            self._更新桌面小猫边界()
        except Exception as e:
            # 小猫加载失败不阻塞主程序启动
            print(f'[desk_cat] 初始化失败: {e}')
            self._desk_cat = None

    def _更新桌面小猫边界(self):
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

    def _退出应用(self):
        """托盘退出：先保存窗口几何，再退出程序。"""
        self._保存几何()
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # 无边框窗口：拖拽移动与边缘缩放（同 adb_Exp / jsontool 模式）
    # ------------------------------------------------------------------
    def _设置子控件追踪(self):
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
            try:
                child.installEventFilter(self)
            except Exception as e:
                # 个别子控件（如已被销毁的残留对象）安装失败不应阻塞启动
                print(f'[启动] 事件过滤器安装失败: {e!r}')

    def _是否自身子控件(self, obj):
        """判断 obj 是否仍在本窗口树内（popup 子控件会被 reparent 到独立窗口）。"""
        if not isinstance(obj, QObject):
            return False
        try:
            return obj.window() is self
        except (RuntimeError, AttributeError):
            # 对象已被销毁 / 非 QWidget（无 window()）
            return False

    @staticmethod
    def _是否可交互(widget):
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
        # 防御：布局项等非 QObject 误传时直接放行（PySide6 绑定层已知边界问题，
        # 否则 _是否自身子控件 里 obj.window() 会 AttributeError）
        if not isinstance(obj, QObject):
            return False
        # 标题栏按钮（最小化/关闭）不参与拖拽缩放，直接放行
        if obj in getattr(self, '_no_track', ()):
            return super().eventFilter(obj, event)
        # 只处理仍属于本窗口的控件；popup / 独立窗口的控件直接放行，
        # 否则 mapTo(self, ...) 可能失败并产生错误坐标，误触发缩放。
        if not self._是否自身子控件(obj):
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                parent_pos = obj.mapTo(self, event.position().toPoint())
                resize_dir = self._获取缩放方向(parent_pos)
                if resize_dir:
                    self._resizing = True
                    self._resize_dir = resize_dir
                    self._resize_origin = event.globalPosition().toPoint()
                    self._resize_geom = self.geometry()
                    return True
                # 非交互控件（空白处/标签/分组框/日志列表等）：发起窗口拖拽，
                # 让无边框窗口任意非控件区域都可拖动。交互控件（按钮/输入框/下拉/
                # 滚动条/文本框等）放行，保持自身点击行为。
                if not self._是否可交互(obj):
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
                        self.刷新设备()
        elif et == QEvent.Type.MouseButtonRelease:
            if self._resizing or self._dragging:
                self._dragging = False
                self._resizing = False
                self._resize_dir = None
                self._drag_moved = False
                self.unsetCursor()
                self._防抖保存几何()
                return True
        elif et == QEvent.Type.MouseMove:
            if self._resizing:
                self._执行缩放(event.globalPosition().toPoint())
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
                rd = self._获取缩放方向(parent_pos)
                self._更新光标(rd)
        elif et == QEvent.Type.HoverMove:
            if not self._resizing and not self._dragging:
                parent_pos = obj.mapTo(self, event.position().toPoint())
                rd = self._获取缩放方向(parent_pos)
                self._更新光标(rd)
                if rd is not None:
                    return True
        return super().eventFilter(obj, event)

    def _获取缩放方向(self, pos):
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

    def _更新光标(self, resize_dir):
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

    def _执行缩放(self, global_pos):
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


    def _重定位窗口按钮(self):
        """把关闭按钮钉在窗口右上角，在 resizeEvent 和初始化时调用。"""
        if not hasattr(self, '_btn_close'):
            return
        m = 4
        bw = self._btn_close.width()
        self._btn_close.move(self.width() - bw - m, m)
        self._btn_close.raise_()

    def mousePressEvent(self, event):
        """边缘区域进入缩放模式，其余区域进入拖拽模式。"""
        # 点击主窗口任何区域都激活到前台，避免被独立弹窗挡住
        self._激活主窗口到前台()
        if event.button() == Qt.MouseButton.LeftButton:
            resize_dir = self._获取缩放方向(event.position().toPoint())
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
            self._执行缩放(event.globalPosition().toPoint())
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
            resize_dir = self._获取缩放方向(event.position().toPoint())
            self._更新光标(resize_dir)

    def mouseReleaseEvent(self, event):
        """结束拖拽/缩放状态，重置光标。"""
        was_active = self._dragging or self._resizing
        self._dragging = False
        self._resizing = False
        self._resize_dir = None
        self._drag_moved = False
        self.unsetCursor()
        if was_active:
            self._防抖保存几何()

    def _分割条移动时(self, *_):
        """折叠/拖动左右分隔条后立即重算布局，避免右侧控件残留旧宽度被裁剪。"""
        for _w in (self.splitter_log, self.leftPanelWidget, self.toolsPanelWidget):
            _w.updateGeometry()
        if self.layout() is not None:
            self.layout().activate()
        self.splitter_main.update()

    def 带到前台(self):
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
class 单实例(QObject):
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

    def 是否主实例(self):
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
            server.newConnection.connect(self._新连接时)
            self._server = server
            self._primary = True
            return True
        # 监听失败（极端情况）退化为允许启动，避免彻底无法打开
        return True

    def _新连接时(self):
        server = self._server
        while server is not None and server.hasPendingConnections():
            sock = server.nextPendingConnection()
            sock.readAll()
            sock.disconnectFromServer()
            sock.deleteLater()
        self.activate.emit()

    def 清理(self):
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
    from PySide6.QtWidgets import QMenu, QAbstractScrollArea

    _ZH_MENU_MAP = {
        'Undo': '撤消', 'Redo': '重做',
        'Cut': '剪切', '&Cut': '剪切(&T)', 'Cu&t': '剪切(&T)',
        'Copy': '复制', '&Copy': '复制(&C)',
        'Paste': '粘贴', '&Paste': '粘贴(&P)',
        'Delete': '删除',
        'Select All': '全选', 'Select&All': '全选(&A)',
    }

    class _中文上下文菜单过滤器(QObject):
        """拦截文本控件右键事件，将标准菜单项文字替换为中文。

        关键：QTextEdit / QPlainTextEdit 等 QAbstractScrollArea 子类的实际鼠标事件
        （含右键 ContextMenu）可能由其内部 viewport()（一个普通 QWidget）接收，
        而非控件本身。因此需要把「裸 viewport」映射回其父滚动区控件再做判断，
        与 _是否可交互() 中的 viewport 认领逻辑保持一致。"""
        def eventFilter(self, obj, event):
            # 防御：PySide6 绑定层在个别场景会把非 QObject（如布局项 QWidgetItem）
            # 误传为 watched 参数，直接放行避免 super() 抛 TypeError（PYSIDE-3143 变体）
            if not isinstance(obj, QObject):
                return False
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

    _zh_filter = _中文上下文菜单过滤器(app)
    app.installEventFilter(_zh_filter)

    # ── 右键「计算哈希」模式：由注册表 command 调用（Super_ADB.exe --hash "%1"）──
    # 在单实例锁之前处理，确保即使主程序已运行，右键哈希仍能独立弹出。
    if '--hash' in sys.argv:
        _hash_paths = [a for a in sys.argv[sys.argv.index('--hash') + 1:]
                        if os.path.isfile(a)]
        if _hash_paths:
            from dialogs.hash_context_menu import 哈希上下文菜单, compute_hashes_batch, ALGO_ORDER
            from PySide6.QtCore import QSettings
            _hash_settings = QSettings('Super_ADB', 'Md5Tool')
            _hash_saved = _hash_settings.value('algos', 'MD5,SHA1,SHA256')
            _hash_algo_keys = [a for a in str(_hash_saved).split(',') if a in ALGO_ORDER]
            if not _hash_algo_keys:
                _hash_algo_keys = ['MD5', 'SHA1', 'SHA256']
            _hash_results = compute_hashes_batch(_hash_paths, algo_keys=_hash_algo_keys)
            _hash_dlg = 哈希上下文菜单(_hash_results, algo_keys=_hash_algo_keys)
            _hash_dlg.exec()
        sys.exit(0)

    # ── 单实例：已运行时激活已有窗口而非开新实例 ──
    single = 单实例('SuperADB_SingleInstance_v1')
    if not single.是否主实例():
        sys.exit(0)

    window = 主窗口()
    single.activate.connect(window.带到前台)
    # 开机自启动（--hidden）时不弹主窗口，仅托盘常驻；其余情况正常显示
    if '--hidden' in sys.argv:
        window.hide()
    else:
        window.show()
    rc = app.exec()
    single.清理()
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


def _自启动目标():
    """注册表中写入的启动命令：exe 绝对路径 + --hidden。"""
    exe = os.path.abspath(sys.executable)
    return '"%s" --hidden' % exe


def 自启动是否启用():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_REG_KEY) as k:
            winreg.QueryValueEx(k, _AUTOSTART_REG_NAME)
        return True
    except OSError:
        return False


def 设置自启动(enable):
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
                                  winreg.REG_SZ, _自启动目标())
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
