# -*- coding: utf-8 -*-
"""
证书安装对话框
==============
点击主界面「SSL」按钮弹出：
- 拖拽 PEM / CRT / CER 证书文件到拖拽区（也可点击选择文件）
- 检查设备 /system 分区读写权限（adb root + remount + 写入验证）
- 计算证书 subject hash（参考 哈希校验对话框._Pem主题哈希器：文件内容 MD5 取前 8 位）
- 重命名为 <hash>.0 并 adb push 到 /system/etc/security/cacerts/
- chmod 777 赋予权限
- 输出框实时展示每一步执行的命令与结果

UI 与逻辑分离：本模块只依赖 adb 实例与 获取序列号 回调。
"""
import os
import shutil
import tempfile
import hashlib

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QPlainTextEdit, QFileDialog)

from 项目UI.对话框基类 import 对话框基类
from 项目UI.界面样式 import THEMES
from 项目UI.弹窗样式 import 拖拽区域


# ----------------------------------------------------------------------
# PEM 证书 subject hash（参考 哈希校验对话框._Pem主题哈希器）
# ----------------------------------------------------------------------
def 计算证书哈希(证书路径):
    """计算 PEM 证书的旧式 subject hash：文件内容 MD5 取前 8 位。

    与 Android 系统证书文件名 ``<hash>.0`` 对应。
    """
    md5 = hashlib.md5()
    with open(证书路径, 'rb') as f:
        while True:
            块 = f.read(64 * 1024)
            if not 块:
                break
            md5.update(块)
    return md5.hexdigest()[:8]


# ----------------------------------------------------------------------
# 后台执行线程（避免卡 UI）
# ----------------------------------------------------------------------
class 证书安装线程(QThread):
    """后台执行：检查权限 / 安装证书，通过信号回传日志和结果。"""

    日志 = Signal(str)
    完成 = Signal(bool, str)  # 是否成功, 结果消息

    def __init__(self, adb, 序列号, 任务类型, 证书路径=None, 父=None):
        super().__init__(父)
        self._adb = adb
        self._序列号 = 序列号
        self._任务类型 = 任务类型  # '检查权限' or '安装证书'
        self._证书路径 = 证书路径

    def run(self):
        try:
            if self._任务类型 == '检查权限':
                self._检查权限()
            elif self._任务类型 == '安装证书':
                self._安装证书()
        except Exception as e:
            self.日志.emit(f'执行异常: {e}')
            self.完成.emit(False, str(e))

    def _检查权限(self):
        self.日志.emit('>>> 正在获取 root 权限并挂载 system 为可写...')
        报告 = self._adb.root_and_remount(self._序列号)
        self.日志.emit(报告)
        可写 = '可在 /system 写入' in 报告
        if 可写:
            self.完成.emit(True, '系统分区已可写，可以安装证书')
        else:
            self.完成.emit(False, '系统分区仍为只读，无法安装证书')

    def _安装证书(self):
        if not self._证书路径 or not os.path.isfile(self._证书路径):
            self.完成.emit(False, '证书文件不存在')
            return
        # 1. 计算哈希
        self.日志.emit(f'>>> 计算证书 subject hash: {self._证书路径}')
        哈希值 = 计算证书哈希(self._证书路径)
        self.日志.emit(f'    hash = {哈希值}')
        # 2. 复制并重命名为 <hash>.0
        临时目录 = tempfile.gettempdir()
        临时路径 = os.path.join(临时目录, f'{哈希值}.0')
        shutil.copy(self._证书路径, 临时路径)
        self.日志.emit(f'    已重命名为: {哈希值}.0')
        # 3. adb push
        远程路径 = f'/system/etc/security/cacerts/{哈希值}.0'
        self.日志.emit(f'>>> adb push {临时路径} {远程路径}')
        try:
            结果 = self._adb.run_direct(self._序列号, ['push', 临时路径, 远程路径], timeout=30)
            self.日志.emit(f'    {结果.strip() or "推送成功"}')
        except Exception as e:
            self.日志.emit(f'    推送失败: {e}')
            self.完成.emit(False, f'推送失败: {e}')
            return
        # 4. chmod 777
        self.日志.emit(f'>>> adb shell chmod 777 {远程路径}')
        try:
            self._adb.run_shell(self._序列号, f'chmod 777 {远程路径}', timeout=10)
            self.日志.emit('    权限设置成功')
        except Exception as e:
            self.日志.emit(f'    权限设置失败: {e}')
            self.完成.emit(False, f'chmod 失败: {e}')
            return
        # 5. 验证
        self.日志.emit(f'>>> 验证: adb shell ls -l {远程路径}')
        try:
            验证 = self._adb.run_shell(self._序列号, f'ls -l {远程路径}', timeout=10)
            self.日志.emit(f'    {验证.strip()}')
        except Exception:
            pass
        self.完成.emit(True, f'证书安装成功: {哈希值}.0')


# ----------------------------------------------------------------------
# 主对话框
# ----------------------------------------------------------------------
class 证书安装对话框(对话框基类):
    """证书安装弹窗：拖拽证书 → 检查权限 → 计算哈希 → 推送 → chmod。"""

    def __init__(self, adb, 获取序列号, parent=None):
        # 业务属性必须在 super().__init__ 之前设置
        self._adb = adb
        self._获取序列号 = 获取序列号
        self._证书路径 = None
        self._系统可写 = False
        self._工作线程 = None

        # 标题栏显示当前设备
        序列号 = 获取序列号() if callable(获取序列号) else None
        标题 = f'证书安装 — 设备: {序列号}' if 序列号 else '证书安装 — 未连接设备'

        super().__init__(parent, 标题=标题, 最小尺寸=(620, 480), 发光=True)

        根布局 = QVBoxLayout(self)
        根布局.setContentsMargins(16, 16, 16, 16)
        根布局.setSpacing(10)

        # 提示标签
        提示 = QLabel('拖拽 PEM / CRT / CER 证书文件到下方区域，或点击选择文件')
        提示.setStyleSheet(f'color: {THEMES[self._主题id]["text_primary"]};')
        根布局.addWidget(提示)

        # 拖拽区
        self.拖拽区 = 拖拽区域(
            self,
            text='拖拽证书文件到此处\n（.pem / .crt / .cer）',
            file_filter='证书文件 (*.pem *.crt *.cer);;所有文件 (*.*)',
            file_mode='single',
            theme_id=self._主题id,
        )
        self.拖拽区.paths_dropped.connect(self._处理拖入文件)
        根布局.addWidget(self.拖拽区)

        # 已选证书显示
        self.证书标签 = QLabel('未选择证书')
        self.证书标签.setStyleSheet(f'color: {THEMES[self._主题id]["accent"]}; font-weight: bold;')
        根布局.addWidget(self.证书标签)

        # 按钮栏（仅保留清空输出；权限检查+安装由拖入证书后自动串联执行）
        按钮栏 = QHBoxLayout()
        按钮栏.addStretch()
        self.清空按钮 = QPushButton('清空输出')
        self.清空按钮.clicked.connect(self._清空输出)
        按钮栏.addWidget(self.清空按钮)
        根布局.addLayout(按钮栏)

        # 输出框
        输出标签 = QLabel('执行日志：')
        输出标签.setStyleSheet(f'color: {THEMES[self._主题id]["text_primary"]};')
        根布局.addWidget(输出标签)

        self.输出框 = QPlainTextEdit()
        self.输出框.setReadOnly(True)
        self.输出框.setStyleSheet(self._输出框样式())
        根布局.addWidget(self.输出框, 1)

        self.setAcceptDrops(True)

    def _输出框样式(self):
        t = THEMES[self._主题id]
        return (
            f'QPlainTextEdit {{ background: {t["bg_input"]}; '
            f'color: {t["text_primary"]}; '
            f'border: 1px solid {t["accent"]}; border-radius: 6px; '
            f'font-family: Consolas, "微软雅黑"; font-size: 9pt; }}'
        )

    def _追加日志(self, 文本):
        self.输出框.appendPlainText(文本)
        self.输出框.verticalScrollBar().setValue(
            self.输出框.verticalScrollBar().maximum()
        )

    def _处理拖入文件(self, 路径列表):
        for 路径 in 路径列表:
            if os.path.isfile(路径):
                self._证书路径 = 路径
                文件名 = os.path.basename(路径)
                self.证书标签.setText(f'已选证书: {文件名}')
                self._追加日志(f'已选择证书: {路径}')
                # 预计算哈希
                try:
                    哈希值 = 计算证书哈希(路径)
                    self._追加日志(f'证书 subject hash: {哈希值}（将重命名为 {哈希值}.0）')
                except Exception as e:
                    self._追加日志(f'哈希计算失败: {e}')
                # 自动检查系统读写权限，通过则自动安装
                self._自动检查并安装()
                break

    def _自动检查并安装(self):
        """拖入证书后自动执行：检查系统读写权限 → 通过则自动安装，失败则输出提示。"""
        序列号 = self._获取序列号()
        if not 序列号:
            self._追加日志('✗ 未连接 Android 设备，请先连接设备')
            return
        if self._工作线程 is not None and self._工作线程.isRunning():
            self._追加日志('⚠ 已有任务在执行，请等待完成')
            return
        self._追加日志(f'设备: {序列号}')
        self._追加日志('>>> 正在检测系统读写权限...')
        self._工作线程 = 证书安装线程(self._adb, 序列号, '检查权限', 父=self)
        self._工作线程.日志.connect(self._追加日志)
        self._工作线程.完成.connect(self._权限检查完成)
        self._工作线程.start()

    def _权限检查完成(self, 成功, 消息):
        self._系统可写 = 成功
        if 成功:
            self._追加日志(f'✓ {消息}')
            self._追加日志('>>> 系统可写，开始安装证书...')
            self._安装证书()
        else:
            self._追加日志(f'✗ {消息}')
            self._追加日志('  提示：真机需 userdebug 固件并开启 root；模拟器请用 -writable-system 参数重启。')

    def _安装证书(self):
        if not self._证书路径:
            self._追加日志('✗ 未选择证书')
            return
        序列号 = self._获取序列号()
        if not 序列号:
            self._追加日志('✗ 未连接设备')
            return
        self._工作线程 = 证书安装线程(
            self._adb, 序列号, '安装证书', 证书路径=self._证书路径, 父=self
        )
        self._工作线程.日志.connect(self._追加日志)
        self._工作线程.完成.connect(self._安装完成)
        self._工作线程.start()

    def _安装完成(self, 成功, 消息):
        if 成功:
            self._追加日志(f'✓ {消息}')
        else:
            self._追加日志(f'✗ {消息}')

    def _清空输出(self):
        self.输出框.clear()

    def apply_theme(self, theme_id):
        """主题切换时刷新样式。"""
        super().apply_theme(theme_id)
        if theme_id not in THEMES:
            return
        self.拖拽区.apply_theme(theme_id)
        强调色 = THEMES[theme_id]['accent']
        self.证书标签.setStyleSheet(f'color: {强调色}; font-weight: bold;')
        self.输出框.setStyleSheet(self._输出框样式())


# ----------------------------------------------------------------------
# 独立运行测试入口（直接 python 证书安装对话框.py 可预览 UI）
# ----------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    # 把 项目UI 目录加入路径，解决 界面样式/弹窗样式/png_rc 的导入
    _ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '项目UI')
    if os.path.isdir(_ui_dir):
        sys.path.insert(0, os.path.abspath(_ui_dir))
    from PySide6.QtWidgets import QApplication

    class _模拟adb:
        """单独运行时用的 mock adb，避免依赖真实设备。"""
        def root_and_remount(self, serial):
            return '① adb root：成功\n② adb remount：成功\n⑤ 验证：可在 /system 写入 ✓'
        def run_direct(self, serial, args, timeout=30):
            return f'模拟执行: adb {" ".join(args)}'
        def run_shell(self, serial, command, timeout=30):
            return f'模拟 shell: {command}'

    app = QApplication(sys.argv)
    dlg = 证书安装对话框(_模拟adb(), lambda: 'emulator-5554（模拟）')
    dlg.show()
    sys.exit(app.exec())
