# -*- coding: UTF-8 -*-
"""
自动生成软件界面截图（Qt offscreen 模式）+ 自动填充模拟数据
运行：python gen_ui_screenshots.py
输出：docs/screenshots/ 目录
"""
import os
import sys
import time
import random

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
# 指定系统字体目录，确保 offscreen 模式下能找到中文字体
if sys.platform == 'win32':
    os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
elif sys.platform == 'darwin':
    os.environ.setdefault('QT_QPA_FONTDIR', '/System/Library/Fonts')
else:
    os.environ.setdefault('QT_QPA_FONTDIR', '/usr/share/fonts')

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from PySide6.QtWidgets import (QApplication, QWidget, QTextEdit, QPlainTextEdit, QListWidget,
                                QTableWidget, QTableWidgetItem, QLabel, QComboBox,
                                QLineEdit, QProgressBar, QTreeWidget, QTreeWidgetItem,
                                QTabWidget, QListWidgetItem, QSpinBox, QTextBrowser,
                                QListView, QTableView, QTreeView, QCheckBox, QRadioButton)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QStandardItemModel, QStandardItem, QFont

app = QApplication.instance() or QApplication(sys.argv)

# 设置全局中文字体，解决 offscreen 模式下中文显示为方框的问题
if sys.platform == 'win32':
    _font_family = 'Microsoft YaHei'
elif sys.platform == 'darwin':
    _font_family = 'PingFang SC'
else:
    _font_family = 'Noto Sans CJK SC'
_app_font = QFont(_font_family, 9)
app.setFont(_app_font)
# 同时设置字体数据库，确保 Qt 能找到
from PySide6.QtGui import QFontDatabase
QFontDatabase.addApplicationFont(os.path.join(os.environ.get('QT_QPA_FONTDIR', '.'), 'msyh.ttc')) if sys.platform == 'win32' else None

_output_dir = os.path.join(os.path.dirname(_project_root), 'docs', 'screenshots')
os.makedirs(_output_dir, exist_ok=True)


def fill_mock_data(widget):
    """递归遍历所有子控件，根据类型填充模拟数据"""
    # 方式1: findChildren
    try:
        all_widgets = widget.findChildren(QWidget)
    except Exception:
        all_widgets = []

    # 方式2: 递归遍历 children() 作为补充
    if not all_widgets:
        def collect(w, result):
            for child in w.children():
                if isinstance(child, QWidget):
                    result.append(child)
                collect(child, result)
        collected = []
        collect(widget, collected)
        all_widgets = collected

    filled = 0
    for child in all_widgets:
        try:
            obj_name = child.objectName().lower() if child.objectName() else ''

            # QTextEdit / QPlainTextEdit：填充文本
            if isinstance(child, (QTextEdit, QPlainTextEdit)):
                if child.isReadOnly() or 'log' in obj_name or '日志' in obj_name or 'output' in obj_name:
                    mock_log = '\n'.join([
                        '[10:23:45] I/ActivityManager: Start proc com.example.app for activity',
                        '[10:23:46] D/NetworkUtils: HTTP GET https://api.example.com/data -> 200 (123ms)',
                        '[10:23:47] W/BatteryService: Battery level 85%, temperature 32.5°C',
                        '[10:23:48] I/WifiService: Connected to SSID "HomeWiFi", RSSI -45dBm',
                        '[10:23:49] D/Graphics: Frame rendered in 8.2ms, FPS 60',
                        '[10:23:50] E/AndroidRuntime: FATAL EXCEPTION: main (mock)',
                        '[10:23:51] I/System: GC freed 12345 objects, 2.3MB freed',
                    ])
                    if isinstance(child, QPlainTextEdit):
                        child.setPlainText(mock_log)
                    else:
                        child.setText(mock_log)
                    filled += 1
                else:
                    json_text = '{\n  "name": "Super_ADB",\n  "version": "v2026.08.30",\n  "features": ["设备连接", "文件管理", "日志抓取"],\n  "config": {\n    "theme": "dark_cyan",\n    "adb_mode": "self_built"\n  }\n}'
                    if isinstance(child, QPlainTextEdit):
                        child.setPlainText(json_text)
                    else:
                        child.setText(json_text)
                    filled += 1

            # QListWidget：填充列表项
            elif isinstance(child, QListWidget):
                if child.count() == 0:
                    items = [
                        '192.168.1.100:5555  (荣耀 ELZ-AN20)',
                        '192.168.1.101:5555  (小米 13 Pro)',
                        'APHTVB1908002153  (USB - 华为 Mate60)',
                        '192.168.1.102:5555  (OPPO Find X7)',
                        'emulator-5554  (Android 模拟器)',
                    ]
                    for item in items:
                        child.addItem(QListWidgetItem(item))
                    filled += 1

            # QTableWidget：填充表格数据
            elif isinstance(child, QTableWidget):
                if child.rowCount() == 0 and child.columnCount() > 0:
                    child.setRowCount(5)
                    headers = ['应用名', '包名', '版本', '大小', '状态']
                    data = [
                        ['微信', 'com.tencent.mm', '8.0.43', '256MB', '运行中'],
                        ['支付宝', 'com.eg.android.AlipayGphone', '10.5.20', '128MB', '已停止'],
                        ['抖音', 'com.ss.android.ugc.aweme', '28.5.0', '320MB', '运行中'],
                        ['淘宝', 'com.taobao.taobao', '10.28.10', '180MB', '已停止'],
                        ['Super_ADB', 'com.jcs.super_adb', '1.0.0', '15MB', '运行中'],
                    ]
                    for col in range(min(child.columnCount(), len(headers))):
                        child.setHorizontalHeaderItem(col, QTableWidgetItem(headers[col]))
                    for row in range(5):
                        for col in range(min(child.columnCount(), len(data[row]))):
                            child.setItem(row, col, QTableWidgetItem(data[row][col]))
                    child.resizeColumnsToContents()
                    filled += 1

            # QTreeWidget：填充树节点
            elif isinstance(child, QTreeWidget):
                if child.topLevelItemCount() == 0:
                    root = QTreeWidgetItem(['/sdcard'])
                    for d in ['DCIM', 'Download', 'Android', 'Pictures', 'Movies']:
                        child_dir = QTreeWidgetItem([d])
                        child_dir.addChild(QTreeWidgetItem(['文件1.jpg']))
                        child_dir.addChild(QTreeWidgetItem(['文件2.png']))
                        root.addChild(child_dir)
                    child.addTopLevelItem(root)
                    child.expandAll()
                    filled += 1

            # QComboBox：填充下拉选项
            elif isinstance(child, QComboBox):
                if child.count() == 0 and not child.isEditable():
                    child.addItems(['全部', '仅运行中', '已停止', '系统应用', '第三方应用'])
                    child.setCurrentIndex(0)
                    filled += 1

            # QLineEdit：填充文本
            elif isinstance(child, QLineEdit):
                if child.text() == '' and not child.isReadOnly():
                    if 'filter' in obj_name or '搜索' in obj_name or 'search' in obj_name:
                        child.setText('com.example')
                    elif 'ip' in obj_name or '地址' in obj_name:
                        child.setText('192.168.1.100:5555')
                    else:
                        child.setText('模拟数据')
                    filled += 1

            # QLabel：填充状态文本
            elif isinstance(child, QLabel):
                if child.text() == '':
                    if 'status' in obj_name or '状态' in obj_name:
                        child.setText('● 已连接 · 延迟 23ms')
                        filled += 1
                    elif 'stat' in obj_name or '统计' in obj_name:
                        child.setText('已抓 12.5 KB · 156 包 · 00:02:34')
                        filled += 1

            # QProgressBar：填充进度
            elif isinstance(child, QProgressBar):
                if child.value() == 0:
                    child.setValue(random.randint(30, 80))
                    filled += 1

            # QSpinBox：设置随机值
            elif isinstance(child, QSpinBox):
                if child.value() == 0:
                    child.setValue(random.randint(10, 100))
                    filled += 1

            # QTextBrowser：填充文本
            elif isinstance(child, QTextBrowser):
                if child.toPlainText() == '':
                    child.setPlainText('Super_ADB 功能说明\n\n'
                        '1. 设备连接：支持 USB / 无线调试 / 局域网扫描\n'
                        '2. 文件管理：上传下载 / 权限修改 / 递归搜索\n'
                        '3. 日志抓取：多标签 logcat / 关键字过滤\n'
                        '4. 性能监控：设备级 + 应用级 / 内存泄漏检测\n'
                        '5. Monkey压测：命令模板 / 事件统计 / 崩溃报告')
                    filled += 1

            # QListView：设置 model 填充数据
            elif isinstance(child, QListView):
                if child.model() is None or child.model().rowCount() == 0:
                    model = QStandardItemModel()
                    for item in ['设备1: 192.168.1.100', '设备2: APHTVB1908002153',
                                 '设备3: 192.168.1.101', '设备4: emulator-5554']:
                        model.appendRow(QStandardItem(item))
                    child.setModel(model)
                    filled += 1

            # QTableView：设置 model 填充数据
            elif isinstance(child, QTableView):
                if child.model() is None or child.model().rowCount() == 0:
                    model = QStandardItemModel(4, 3)
                    model.setHorizontalHeaderLabels(['应用名', '包名', '状态'])
                    data = [['微信', 'com.tencent.mm', '运行中'],
                            ['支付宝', 'com.eg.android.AlipayGphone', '已停止'],
                            ['抖音', 'com.ss.android.ugc.aweme', '运行中']]
                    for r, row in enumerate(data):
                        for c, val in enumerate(row):
                            model.setItem(r, c, QStandardItem(val))
                    child.setModel(model)
                    filled += 1

            # QCheckBox：勾选
            elif isinstance(child, QCheckBox):
                if not child.isChecked() and random.random() > 0.5:
                    child.setChecked(True)
                    filled += 1

            # QRadioButton：选中第一个
            elif isinstance(child, QRadioButton):
                if not child.isChecked():
                    child.setChecked(True)
                    filled += 1

        except Exception:
            continue

    # 强制刷新
    try:
        widget.update()
        app.processEvents()
    except Exception:
        pass
    return filled


def grab(widget, filename, w=1200, h=800, fill_data=True):
    try:
        widget.resize(w, h)
        # 强制设置不透明背景（主窗口是无边框透明背景，offscreen 下会截成透明）
        widget.setAutoFillBackground(True)
        palette = widget.palette()
        palette.setColor(widget.backgroundRole(), QColor(30, 32, 40))  # 深色背景
        widget.setPalette(palette)
        widget.setStyleSheet(widget.styleSheet() + " QWidget { background-color: #1e2028; }")
        widget.show()
        # 先等待控件完全渲染
        for _ in range(10):
            app.processEvents()
            time.sleep(0.05)
        widget.repaint()
        app.processEvents()

        if fill_data:
            filled = fill_mock_data(widget)
            print(f'  📝 填充了 {filled} 个控件')
            # 填充后强制每个子控件刷新
            for child in widget.findChildren(QWidget):
                try:
                    child.update()
                except Exception:
                    pass
            widget.repaint()
            # 等待填充后的数据渲染完成
            for _ in range(15):
                app.processEvents()
                time.sleep(0.05)
            widget.repaint()
            app.processEvents()
            time.sleep(0.2)  # 额外等待，确保渲染完成

        pixmap = widget.grab()
        # 如果 pixmap 有透明通道，用深色背景填充
        if pixmap.hasAlphaChannel():
            from PySide6.QtGui import QPainter
            new_pixmap = pixmap.copy()
            painter = QPainter(new_pixmap)
            painter.fillRect(new_pixmap.rect(), QColor(30, 32, 40))
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = new_pixmap
        filepath = os.path.join(_output_dir, filename)
        pixmap.save(filepath, 'PNG')
        print(f'  ✅ {filename} ({pixmap.width()}x{pixmap.height()})')
        widget.close()
        return True
    except Exception as e:
        print(f'  ❌ {filename}: {e}')
        return False


def main():
    print('=' * 50)
    print('Super_ADB 界面截图自动生成（含模拟数据）')
    print('=' * 50)
    print(f'输出目录: {_output_dir}\n')

    success = 0
    total = 0
    _idx = 0
    def _next():
        nonlocal _idx
        _idx += 1
        return _idx

    # 1. 主窗口
    total += 1
    print(f'[{_next()}/20] 主窗口...')
    try:
        from app.main import 主窗口
        w = 主窗口()
        for combo in [getattr(w, 'fileMgr_deviceCombo', None), getattr(w, 'logViewer_deviceCombo', None)]:
            if combo:
                combo.addItems(['192.168.1.100:5555 (荣耀 ELZ-AN20)', 'APHTVB1908002153 (USB)', '192.168.1.101:5555 (小米13)'])
                combo.setCurrentIndex(0)
        if grab(w, '主界面.png', 1400, 900):
            success += 1
    except Exception as e:
        print(f'  ❌ 主窗口: {e}')

    # 2. 无线调试
    total += 1
    print(f'[{_next()}/20] 无线调试...')
    try:
        from dialogs.wireless_debug_dialog import 无线调试对话框
        dlg = 无线调试对话框()
        if grab(dlg, '无线调试.png', 900, 650):
            success += 1
    except Exception as e:
        print(f'  ❌ 无线调试: {e}')

    # 3. WiFi 配对
    total += 1
    print(f'[{_next()}/20] WiFi配对...')
    try:
        from dialogs.wifi_pair_dialog import WiFi配对对话框
        dlg = WiFi配对对话框()
        if grab(dlg, 'WiFi配对.png', 500, 450):
            success += 1
    except Exception as e:
        print(f'  ❌ WiFi配对: {e}')

    # 4. WiFi 历史
    total += 1
    print(f'[{_next()}/20] WiFi历史...')
    try:
        from dialogs.wifi_history_dialog import WifiHistoryDialog
        dlg = WifiHistoryDialog()
        if grab(dlg, 'WiFi历史.png', 600, 500):
            success += 1
    except Exception as e:
        print(f'  ❌ WiFi历史: {e}')

    # 5. 局域网扫描
    total += 1
    print(f'[{_next()}/20] 局域网扫描...')
    try:
        from dialogs.lan_scan_dialog import 局域网扫描对话框
        dlg = 局域网扫描对话框()
        if grab(dlg, '局域网扫描.png', 700, 550):
            success += 1
    except Exception as e:
        print(f'  ❌ 局域网扫描: {e}')

    # 6. IP扫描
    total += 1
    print(f'[{_next()}/20] IP扫描...')
    try:
        from dialogs.ip_scan_dialog import IP扫描对话框
        dlg = IP扫描对话框()
        if grab(dlg, 'IP扫描.png', 700, 550):
            success += 1
    except Exception as e:
        print(f'  ❌ IP扫描: {e}')

    # 7. ADB 终端
    total += 1
    print(f'[{_next()}/20] ADB终端...')
    try:
        from dialogs.adb_terminal_dialog import ADB终端对话框
        # 用 SimpleNamespace 模拟主窗口（提供 adb 属性）
        from types import SimpleNamespace
        _mock_main = SimpleNamespace(adb=None)
        dlg = ADB终端对话框(_mock_main)
        if grab(dlg, 'ADB终端.png', 900, 600):
            success += 1
    except Exception as e:
        print(f'  ❌ ADB终端: {e}')

    # 7. Monkey 压测
    total += 1
    print(f'[{_next()}/20] Monkey压测...')
    try:
        from dialogs.monkey_stress_window import Monkey压测窗口
        dlg = Monkey压测窗口('test_serial')
        if grab(dlg, 'Monkey压测.png', 1000, 700):
            success += 1
    except Exception as e:
        print(f'  ❌ Monkey压测: {e}')

    # 8. 安装解包
    total += 1
    print(f'[{_next()}/20] 安装解包...')
    try:
        from dialogs.install_unpack_dialog import 安装解包对话框
        dlg = 安装解包对话框(None, lambda: 'test_serial')
        if grab(dlg, '安装解包.png', 900, 650):
            success += 1
    except Exception as e:
        print(f'  ❌ 安装解包: {e}')

    # 9. 网络抓包
    total += 1
    print(f'[{_next()}/20] 网络抓包...')
    try:
        from dialogs.tcpdump_dialog import Tcpdump对话框
        dlg = Tcpdump对话框('test_serial')
        if grab(dlg, '网络抓包.png', 900, 650):
            success += 1
    except Exception as e:
        print(f'  ❌ 网络抓包: {e}')

    # 10. PCAP 解析
    total += 1
    print(f'[{_next()}/20] PCAP解析...')
    try:
        from dialogs.pcap_parse_dialog import Pcap解析对话框
        dlg = Pcap解析对话框()
        if grab(dlg, 'PCAP解析.png', 1100, 750):
            success += 1
    except Exception as e:
        print(f'  ❌ PCAP解析: {e}')

    # 11. 投屏设置
    total += 1
    print(f'[{_next()}/20] 投屏设置...')
    try:
        from dialogs.scrcpy_settings_dialog import Scrcpy设置对话框
        dlg = Scrcpy设置对话框()
        if grab(dlg, '投屏.png', 700, 550):
            success += 1
    except Exception as e:
        print(f'  ❌ 投屏设置: {e}')

    # 13. JSON 工具
    total += 1
    print(f'[{_next()}/20] JSON工具...')
    try:
        from dialogs.json_tool_dialog import Json工具对话框
        dlg = Json工具对话框()
        if grab(dlg, 'JSON工具.png', 1000, 700):
            success += 1
    except Exception as e:
        print(f'  ❌ JSON工具: {e}')

    # 13. 哈希校验
    total += 1
    print(f'[{_next()}/20] 哈希校验...')
    try:
        from dialogs.hash_check_dialog import 哈希校验对话框
        dlg = 哈希校验对话框()
        if grab(dlg, '哈希校验.png', 800, 600):
            success += 1
    except Exception as e:
        print(f'  ❌ 哈希校验: {e}')

    # 14. 时间戳转换
    total += 1
    print(f'[{_next()}/20] 时间戳转换...')
    try:
        from dialogs.timestamp_dialog import 时间戳对话框
        dlg = 时间戳对话框()
        if grab(dlg, '时间戳转换.png', 600, 450):
            success += 1
    except Exception as e:
        print(f'  ❌ 时间戳转换: {e}')

    # 15. 修改时间
    total += 1
    print(f'[{_next()}/20] 修改时间...')
    try:
        from dialogs.change_time_dialog import 修改时间对话框
        from unittest.mock import MagicMock
        dlg = 修改时间对话框(MagicMock(), 'test_serial', 'dark_cyan', pool=MagicMock())
        if grab(dlg, '修改时间.png', 500, 400):
            success += 1
    except Exception as e:
        print(f'  ❌ 修改时间: {e}')

    # 16. 证书安装
    total += 1
    print(f'[{_next()}/20] 证书安装...')
    try:
        from dialogs.cert_install_dialog import 证书安装对话框
        dlg = 证书安装对话框(None, lambda: 'test_serial')
        if grab(dlg, '证书安装.png', 600, 500):
            success += 1
    except Exception as e:
        print(f'  ❌ 证书安装: {e}')

    # 17. 环境配置
    total += 1
    print(f'[{_next()}/20] 环境配置...')
    try:
        from dialogs.env_config_dialog import 环境配置对话框
        dlg = 环境配置对话框()
        if grab(dlg, '环境配置.png', 800, 600):
            success += 1
    except Exception as e:
        print(f'  ❌ 环境配置: {e}')

    # 18. 设备信息
    total += 1
    print(f'[{_next()}/20] 设备信息...')
    try:
        from dialogs.device_info_dialog import 设备信息对话框
        dlg = 设备信息对话框(None, 'test_serial', 'dark_cyan')
        if grab(dlg, '设备信息.png', 700, 550):
            success += 1
    except Exception as e:
        print(f'  ❌ 设备信息: {e}')

    # 19. 关于
    total += 1
    print(f'[{_next()}/20] 关于...')
    try:
        from dialogs.about_dialog import 关于对话框
        dlg = 关于对话框()
        if grab(dlg, '关于.png', 500, 650, fill_data=False):
            success += 1
    except Exception as e:
        print(f'  ❌ 关于: {e}')

    print(f'\n{"=" * 50}')
    print(f'完成: {success}/{total} 个截图（含模拟数据）')
    print(f'输出目录: {_output_dir}')
    print('=' * 50)


if __name__ == '__main__':
    main()
