# -*- coding: utf-8 -*-
"""单独测试 PCAP 解析对话框截图"""
import os
import sys
from pathlib import Path

WIN_ROOT = Path(r'G:\Python\jcspy\Super_ADB\Super_ADB_Win')
if str(WIN_ROOT) not in sys.path:
    sys.path.insert(0, str(WIN_ROOT))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_SCALE_FACTOR', '2')
os.environ.setdefault('QT_ENABLE_HIGHDPI_SCALING', '1')
os.environ.setdefault('QT_FONT_DPI', '96')

from PySide6.QtWidgets import QApplication, QTreeWidgetItem
from PySide6.QtGui import QFont, QFontDatabase, QColor

app = QApplication.instance() or QApplication(sys.argv)

字体候选 = ['Microsoft YaHei UI', 'Microsoft YaHei', '微软雅黑', 'SimHei']
选中字体 = None
for 字体名 in 字体候选:
    if QFontDatabase.hasFamily(字体名):
        选中字体 = 字体名
        break
if 选中字体:
    font = QFont(选中字体, 9)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(font)
    print(f'字体: {选中字体}')

from 对话框.PCAP解析对话框 import Pcap解析对话框

print('正在创建 PCAP 解析对话框...')
dlg = Pcap解析对话框(pcap_path='')

# 覆盖延迟显示的拖拽遮罩方法（自做模拟，不改对话框源码）
dlg._show_drag_overlay = lambda: None
if hasattr(dlg, '_drag_overlay') and dlg._drag_overlay is not None:
    dlg._drag_overlay.hide()

# 停止可能的定时器
for _ in range(10):
    app.processEvents()

dlg.resize(1280, 800)
dlg.setWindowTitle('PCAP 解析器 — tcpdump_192.168.1.3_5555_20260830_072041.pcap')
dlg._stat_label.setText(
    '解析完成 [共 295 个流/HTTPS:192/TCP:91/HTTP:12/'
    '总包: 12535/IP:12535(100%)/非IP:0(0%)/'
    'TCP:12535/HTTP包:24/TLS包:192/用时 0.2s]'
)
dlg.btn_export.setEnabled(True)
dlg.domain_combo.addItem('全部域名')
dlg.domain_combo.addItem('app-sc.a208.ottcn.com')
dlg.domain_combo.addItem('display-sc.a208.ottcn.com')
dlg.domain_combo.setCurrentIndex(0)

# 左侧结构树
tree = dlg.structure_tree
tree.clear()

def _make_item(text, method='', size='', color=None):
    item = QTreeWidgetItem([text, method, size])
    if color:
        item.setForeground(1, QColor(color))
    return item

domain1 = _make_item('display-sc.a208.ottcn.com', '', '30')
path1 = _make_item('request', '', '6')
path2 = _make_item('sdk10', '', '6')
req1 = _make_item('sdk10?cid=6E41B129BB...', 'POST 200', '2.3 KB', '#1de9b6')
req2 = _make_item('sdk10?cid=CD41566947...', 'POST 200', '1.7 KB', '#1de9b6')
req3 = _make_item('sdk10?cid=CD41566947...', 'POST 200', '1.6 KB', '#1de9b6')
req4 = _make_item('sdk10?cid=0A92C9705F...', 'POST 200', '1.6 KB', '#1de9b6')
req5 = _make_item('sdk10?cid=5FB138566C...', 'POST 200', '2.2 KB', '#1de9b6')
req6 = _make_item('sdk10?cid=B90B176D8F...', 'POST 200', '2.2 KB', '#1de9b6')
path2.addChildren([req1, req2, req3, req4, req5, req6])
path1.addChild(path2)
domain1.addChild(path1)
tree.addTopLevelItem(domain1)
domain1.setExpanded(True)
path1.setExpanded(True)
path2.setExpanded(True)

for name, cnt in [
    ('app-sc.a208.ottcn.com', '3'),
    ('display.a208.ottcn.com', '3'),
    ('dpgwtm-cache.a208.ottcn.com', '1'),
    ('ggc.a208.ottcn.com', '1'),
    ('ggictv.a208.ottcn.com', '4'),
    ('ggv.a208.ottcn.com', '1'),
    ('ggxtv.a208.ottcn.com', '6'),
    ('gslbmgsplive.a208.ottcn.com', '1'),
    ('img.a208.ottcn.com', '2'),
    ('img.cmvideo.cn', '4'),
    ('play.a208.ottcn.com', '6'),
    ('vms-sc.a208.ottcn.com', '6'),
]:
    tree.addTopLevelItem(_make_item(name, '', cnt))

tree.setCurrentItem(req2)

# 右侧内容标签页
content_idx = dlg.tabs.indexOf(dlg.content_tab)
if content_idx >= 0:
    dlg.tabs.setCurrentIndex(content_idx)

# 请求头
req_headers = dlg.req_body_viewer._editors['headers']
req_headers.clear()
for k, v in [
    ('keep-alive', 'false'),
    ('charset', 'utf-8'),
    ('content-type', 'application/json'),
    ('x-protocol-ver', '2.1'),
    ('x-encryption', 'MIGUEncryption'),
    ('user-agent', 'ggxtv.a208.ottcn.com'),
    ('host', 'ggxtv.a208.ottcn.com'),
    ('connection', 'Keep-Alive'),
    ('accept-encoding', 'gzip'),
    ('content-length', '551'),
]:
    req_headers.addTopLevelItem(QTreeWidgetItem([k, v]))

# 响应头
resp_headers = dlg.resp_body_viewer._editors['headers']
resp_headers.clear()
for k, v in [
    ('server', 'nginx'),
    ('date', 'Sat, 29 Aug 2026 23:20:50 GMT'),
    ('content-type', 'application/json; charset=utf-8'),
    ('content-length', '403'),
    ('connection', 'keep-alive'),
    ('p3p', 'CP=CURa ADMa DEVa PSAo PSDo OUR BUS UNI PUR INT DEM STA PRE COM NAV OTC...'),
    ('set-cookie', 'REMEMBER_CODE=cb6b5126-fee4-423b-9d69-e44e784647fe;domain=ottcn.com;path=/;Max...'),
]:
    resp_headers.addTopLevelItem(QTreeWidgetItem([k, v]))
dlg.resp_body_viewer.view_tabs.setCurrentIndex(0)

# 最后确保遮罩隐藏
if hasattr(dlg, '_drag_overlay') and dlg._drag_overlay is not None:
    dlg._drag_overlay.hide()

dlg.setFont(app.font())
dlg.show()
for _ in range(15):
    app.processEvents()

size = dlg.size()
print(f'窗口尺寸: {size.width()}x{size.height()}')

from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtCore import QPoint

dpr = max(dlg.devicePixelRatioF(), 2.0)
img = QImage(int(size.width() * dpr), int(size.height() * dpr),
             QImage.Format.Format_ARGB32)
img.setDevicePixelRatio(dpr)
img.fill(QColor('#0f141a'))
painter = QPainter(img)
painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
dlg.render(painter, QPoint(0, 0))
painter.end()

margin = int(20 * dpr)
canvas_w = img.width() + margin * 2
canvas_h = img.height() + margin * 2
canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32)
canvas.setDevicePixelRatio(dpr)
canvas.fill(QColor('#0f141a'))
painter = QPainter(canvas)
painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
painter.drawImage(margin, margin, img)
painter.end()

out_path = r'G:\Python\jcspy\Super_ADB\test_pcap.png'
canvas.save(out_path, 'PNG', 100)
print(f'截图已保存: {out_path}')

dlg.close()
dlg.deleteLater()
print('完成!')
