# -*- coding: utf-8 -*-
"""
ADB Shell 整合工具 —— 主入口
==============================
整合常用 ADB 快捷命令、文件管理器、日志查看器于一体。
UI 布局由 Super_ADB.ui 定义，通过 Ui_MainWindow 驱动。

技术栈：PySide6 + .ui 布局 + QSplitter 分屏。
# -*- coding: UTF-8 -*-
@author:JCS
@time:2022/11/26
"""

import re
import socket
import sys
import threading
import time

# 确保直接运行时也能找到同目录模块
_here = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    from PySide6.QtCore import (Qt, QThreadPool, QRunnable, Signal, QObject,
                                QMetaObject, Q_ARG, QTimer, QEvent, QRect)
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont, QAction
    from PySide6.QtWidgets import (
        QApplication, QWidget, QPushButton, QTextEdit,
        QMessageBox, QStatusBar, QSystemTrayIcon, QMenu, QLayout,
    )
except ImportError as e:
    print(f'错误: 未找到 PySide6 ({e})')
    print('请使用已安装 PySide6 的 Python 运行本工具，例如：')
    print('  D:/Python/Python314/python.exe Super_ADB_Main.py')
    sys.exit(1)

from Super_ADB import Ui_MainWindow
from adb_utils import AdbDeviceOps, format_device_label, load_json_config, save_json_config
from 界面样式 import STYLE_SHEET
from file_manager_page import FileManagerPage
from log_viewer_page import LogViewerPage
from device_perf_monitor import DevicePerfMonitor
from monkey_runner_window import MonkeyRunnerWindow

CONFIG_NAME = 'adb_shell_config.json'
# 首次启动 / 配置缺失或损坏时的默认窗口几何
DEFAULT_GEOMETRY = {'x': 71, 'y': 126, 'w': 1400, 'h': 780}


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


# ----------------------------------------------------------------------
# 主窗口（多重继承 Ui_MainWindow）
# ----------------------------------------------------------------------
class MainWindow(QWidget, Ui_MainWindow):
    def __init__(self):
        super().__init__()
        self.setupUi(self)
        # ── 无边框窗口 ──────────────────────────────────────────
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setMouseTracking(True)
        self.setMinimumSize(800, 500)
        # 窗口标题由 .ui 文件 (Super_ADB.ui) 的 windowTitle 定义，
        # 这里不再硬覆盖，保持 UI 与逻辑分离。
        # 页面容器不再用工具栏最小宽度顶住 splitter，
        # 修复左侧折叠/窗口变窄后右侧内容溢出被裁剪、需手动拉窗口才恢复的问题
        for _lay in (self.leftPanel.layout(),
                     self.layoutWidget.layout(),
                     self.layoutWidget1.layout()):
            if _lay is not None:
                _lay.setSizeConstraint(QLayout.SetNoConstraint)
        self._restore_geometry()
        self.splitter.setSizes([600, 1200])
        self.splitter_2.splitterMoved.connect(self._on_splitter_moved)
        # 压小设备下拉框最小宽度，让右栏可以缩得更窄而不裁剪控件
        self.deviceCombo.setMinimumWidth(160)
        self.fileMgr_deviceCombo.setMinimumWidth(160)
        self.logViewer_deviceCombo.setMinimumWidth(160)
        self.setWindowIcon(self._create_icon())

        self.adb = AdbDeviceOps(log_callback=self.log)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(6)
        self._live_workers = []
        self._dpm_window = None
        self._monkey_window = None
        # 无边框窗口交互状态（拖拽移动 / 边缘缩放）
        self._dragging = False
        self._resizing = False
        self._resize_dir = None
        self._margin = 8                     # 窗口四边 8px 内为缩放热区

        self._wire_signals()
        self._add_status_bar()
        self._init_pages()
        self.setStyleSheet(STYLE_SHEET)
        # 无边框窗口标题栏按钮：最小化 / 关闭（必须在 _setup_child_tracking 之前创建）
        self._no_track = set()
        self._btn_min = QPushButton('–', self)
        self._btn_min.setObjectName('winBtnMin')
        self._btn_min.setFixedSize(34, 26)
        self._btn_min.setToolTip('最小化')
        self._btn_min.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_min.setStyleSheet(self._win_btn_style(False))
        self._btn_min.clicked.connect(self.showMinimized)
        self._no_track.add(self._btn_min)

        self._btn_close = QPushButton('✕', self)
        self._btn_close.setObjectName('winBtnClose')
        self._btn_close.setFixedSize(34, 26)
        self._btn_close.setToolTip('关闭')
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.setStyleSheet(self._win_btn_style(True))
        self._btn_close.clicked.connect(self.close)
        self._no_track.add(self._btn_close)

        self._reposition_win_buttons()
        self._setup_child_tracking()          # 必须在 UI 全部构建后：为子控件安装事件过滤器
        self._init_tray()

        if not self.adb.check_adb():
            QMessageBox.warning(self, '缺少 adb', '未检测到 adb 命令，请检查环境变量后重启本程序。')
            self.status_bar.showMessage('adb 不可用', 0)
        else:
            self.refresh_devices()

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
        self.btnDpm.clicked.connect(self.open_perf_monitor)
        self.btnSystemRoot.clicked.connect(self.system_root)
        # 应用操作
        self.btnStartApp.clicked.connect(self.start_app)
        self.btnStopApp.clicked.connect(self.stop_app)
        self.btnMeminfo.clicked.connect(self.show_meminfo)
        self.btnClearApp.clicked.connect(self.clear_app)
        self.btnUninstall.clicked.connect(self.uninstall_app)
        self.btnAppInfo.clicked.connect(self.show_app_info)
        self.btnApps3.clicked.connect(self.list_apps_3)
        self.btnAppsS.clicked.connect(self.list_apps_s)
        self.btnAppsAll.clicked.connect(self.list_apps_all)
        self.btnWindowApp.clicked.connect(self.show_window_app)
        self.btnRunningApps.clicked.connect(self.show_running_apps)
        self.btnRunningApps_2.clicked.connect(self.open_monkey_runner)
        # 输出
        self.btnClear.clicked.connect(self.output.clear)
        self.btnCopy.clicked.connect(self.copy_output)

    def _add_status_bar(self):
        """.ui 不包含 QStatusBar，手动添加到底部。"""
        self.status_bar = QStatusBar()
        self.status_bar.showMessage('就绪')
        self.verticalLayout_4.addWidget(self.status_bar)

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
            tag_input=self.logViewer_tagInput,
            pid_input=self.logViewer_pidInput,
            msg_input=self.logViewer_msgInput,
            regex_chk=self.logViewer_regexChk,
            btn_reset=self.logViewer_btnReset,
            text_edit=self.logViewer_textEdit,
            follow_chk=self.logViewer_followChk,
            count_label=self.logViewer_countLabel,
            btn_load_file=self.btnLf,
        )

    # ------------------------------------------------------------------
    # 图标
    # ------------------------------------------------------------------
    def _create_icon(self):
        pm = QPixmap(64, 64)
        pm.fill(QColor(29, 233, 182))
        p = QPainter(pm)
        p.setPen(QColor(27, 27, 27))
        f = QFont('微软雅黑', 18, QFont.Bold)
        p.setFont(f)
        p.drawText(pm.rect(), Qt.AlignCenter, 'ADB')
        p.end()
        return QIcon(pm)

    # ------------------------------------------------------------------
    # 线程安全输出
    # ------------------------------------------------------------------
    def log(self, text: str):
        now = time.strftime('%Y-%m-%d %H:%M:%S')
        QMetaObject.invokeMethod(
            self.output, 'append',
            Qt.QueuedConnection,
            Q_ARG(str, f'[{now}]\n{text.strip()}\n'),
        )

    def set_status(self, text: str, ok: bool = None):
        prefix = '' if ok is None else ('● ' if ok else '✕ ')
        QMetaObject.invokeMethod(
            self.status_bar, 'showMessage',
            Qt.QueuedConnection,
            Q_ARG(str, prefix + text),
        )

    def _run_async(self, func, *args, **kwargs):
        """将函数放入线程池后台执行，结果通过 log / set_status 展示。"""
        self.output.clear()
        worker = CmdWorker(func, *args, **kwargs)
        worker.signals.result.connect(lambda r: self.log(str(r)))
        worker.signals.error.connect(lambda e: self.log(f'错误: {e}'))
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
        self.deviceCombo.clear()
        online = [d for d in devices if d.get('state') == 'device']
        for d in online:
            self.deviceCombo.addItem(format_device_label(d), d.get('serial'))
        self.set_status(f'已连接 {len(online)} 台设备', ok=len(online) > 0)

    def connect_device(self):
        ip = self.ipInput.text().strip()
        if not ip:
            self.log('请输入设备 IP')
            return
        self._run_async(self.adb.connect, ip)

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
        host = self._get_local_ip()
        self._run_async(self.adb.set_proxy, serial, f'{host}:8888')

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
        self._monkey_window = MonkeyRunnerWindow(
            serial, default_pkg=default_pkg, parent=self)
        self._monkey_window.show()

    # ------------------------------------------------------------------
    # 应用操作
    # ------------------------------------------------------------------
    def _package_name(self):
        name = self.pkgInput.text().strip()
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

        m = re.search(r'TOTAL PSS:\s*(\d+)', raw)
        if m:
            lines.append(f'总 PSS: {cls._fmt_kb(m.group(1))}')
        m = re.search(r'TOTAL RSS:\s*(\d+)', raw)
        if m:
            lines.append(f'总 RSS: {cls._fmt_kb(m.group(1))}')

        items = [
            ('Java Heap', 'Java 堆'),
            ('Native Heap', 'Native 堆'),
            ('Code', '代码'),
            ('Stack', '栈'),
            ('Graphics', '图形'),
            ('Private Other', '私有其他'),
            ('System', '系统占用'),
        ]
        lines.append('-' * 32)
        for key, name in items:
            m = re.search(rf'{re.escape(key)}:\s*(\d+)', raw)
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
    @staticmethod
    def _get_local_ip():
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception:
            return '127.0.0.1'

    # ------------------------------------------------------------------
    # 窗口几何持久化
    # ------------------------------------------------------------------
    def _restore_geometry(self):
        """启动时从 adb_shell_config.json 恢复窗口坐标大小，缺失/非法则回退默认值。"""
        g = load_json_config(CONFIG_NAME).get('geometry') or {}
        try:
            x, y, w, h = int(g['x']), int(g['y']), int(g['w']), int(g['h'])
            if w >= 800 and h >= 500:
                self.setGeometry(x, y, w, h)
                return
        except (KeyError, TypeError, ValueError):
            pass
        self.setGeometry(DEFAULT_GEOMETRY['x'], DEFAULT_GEOMETRY['y'],
                         DEFAULT_GEOMETRY['w'], DEFAULT_GEOMETRY['h'])

    def _save_geometry(self):
        g = self.geometry()
        cfg = load_json_config(CONFIG_NAME)
        cfg['geometry'] = {'x': g.x(), 'y': g.y(), 'w': g.width(), 'h': g.height()}
        save_json_config(CONFIG_NAME, cfg)

    def _save_geometry_debounced(self):
        """移动/缩放防抖保存：停顿 300ms 后才写盘，避免拖动过程高频写入。"""
        if not hasattr(self, '_geo_timer'):
            self._geo_timer = QTimer(self)
            self._geo_timer.setSingleShot(True)
            self._geo_timer.timeout.connect(self._save_geometry)
        self._geo_timer.start(300)

    def moveEvent(self, ev):
        super().moveEvent(ev)
        self._save_geometry_debounced()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._reposition_win_buttons()
        self._save_geometry_debounced()

    def closeEvent(self, ev):
        """点 ✕ 不退出，改为隐藏到托盘；真正退出走托盘菜单"退出"。"""
        ev.ignore()
        self._hide_to_tray()

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
        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _quit_app(self):
        """托盘退出：先保存窗口几何，再退出程序。"""
        self._save_geometry()
        QApplication.instance().quit()

    # ------------------------------------------------------------------
    # 无边框窗口：拖拽移动与边缘缩放（同 adb_Exp / jsontool 模式）
    # ------------------------------------------------------------------
    def _setup_child_tracking(self):
        """为所有子控件启用鼠标追踪并安装事件过滤器，
        使父窗口能统一处理子控件区域内的拖拽和缩放事件。"""
        for child in self.findChildren(QWidget):
            child.setMouseTracking(True)
            child.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截子控件的鼠标事件，实现子控件区域内的窗口缩放和拖拽。"""
        # 标题栏按钮（最小化/关闭）不参与拖拽缩放，直接放行
        if obj in getattr(self, '_no_track', ()):
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
        elif et == QEvent.Type.MouseButtonRelease:
            if self._resizing or self._dragging:
                self._dragging = False
                self._resizing = False
                self._resize_dir = None
                self.unsetCursor()
                self._save_geometry_debounced()
                return True
        elif et == QEvent.Type.MouseMove:
            if self._resizing:
                self._do_resize(event.globalPosition().toPoint())
                return True
            elif self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
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
        elif top:
            return Qt.Edge.TopEdge
        elif bottom:
            return Qt.Edge.BottomEdge
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
        """根据鼠标全局位移量执行窗口缩放，保证不小于最小尺寸。"""
        delta = global_pos - self._resize_origin
        geom = QRect(self._resize_geom)
        min_w, min_h = self.minimumWidth(), self.minimumHeight()
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
    # 无边框窗口：标题栏按钮（最小化 / 关闭）
    # ------------------------------------------------------------------
    def _win_btn_style(self, is_close):
        """生成标题栏按钮的局部样式表。关闭按钮 hover 为红色（Windows 风格）。"""
        common = ("QPushButton{background:transparent;border:none;color:#cccccc;"
                  "font:16px 'Segoe UI','微软雅黑';border-radius:4px;}")
        if is_close:
            return (common +
                    "QPushButton:hover{background:#e81123;color:#ffffff;}"
                    "QPushButton:pressed{background:#b0091a;color:#ffffff;}")
        return (common +
                "QPushButton:hover{background:rgba(255,255,255,30);color:#ffffff;}"
                "QPushButton:pressed{background:rgba(255,255,255,55);color:#ffffff;}")

    def _reposition_win_buttons(self):
        """把最小化/关闭按钮钉在窗口右上角，在 resizeEvent 和初始化时调用。"""
        if not hasattr(self, '_btn_close'):
            return
        m = 4
        bw = self._btn_close.width()
        self._btn_close.move(self.width() - bw - m, m)
        self._btn_min.move(self.width() - bw * 2 - m - 2, m)
        self._btn_close.raise_()
        self._btn_min.raise_()

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
                event.accept()

    def mouseMoveEvent(self, event):
        """缩放模式下缩放窗口，拖拽模式下移动窗口，空闲时更新光标。"""
        if self._resizing:
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
        elif self._dragging and event.buttons() == Qt.MouseButton.LeftButton:
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


# ----------------------------------------------------------------------
# 入口
# ----------------------------------------------------------------------
def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setQuitOnLastWindowClosed(False)   # 关窗口留托盘，退出走托盘菜单
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
