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
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
                               QPlainTextEdit, QFileDialog, QWidget)

from ui.dialog_base import 对话框基类
from ui.ui_styles import THEMES
from ui.dialog_styles import 拖拽区域, add_green_glow, highlight_card_style, _create_popup_card


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
        报告 = self._adb.root并重新挂载(self._序列号)
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
        # 3. 检查 /system 分区可用空间和可写性
        远程路径 = f'/system/etc/security/cacerts/{哈希值}.0'
        try:
            df_out = self._adb.执行shell(self._序列号, 'df -k /system', timeout=10)
            self.日志.emit(f'    /system 空间: {df_out.strip()}')
            # 解析可用空间 (df -k 输出: Filesystem 1K-blocks Used Available Use% Mounted on)
            lines = [l for l in df_out.strip().splitlines() if l.strip()]
            if len(lines) >= 2:
                parts = lines[-1].split()
                if len(parts) >= 4:
                    可用KB = int(parts[3]) if parts[3].isdigit() else 0
                    证书大小KB = max(1, (os.path.getsize(临时路径) + 1023) // 1024)
                    if 可用KB < 证书大小KB:
                        self.日志.emit(f'    ✗ /system 可用空间不足: {可用KB}KB < 证书大小 {证书大小KB}KB')
                        self.完成.emit(False, f'/system 可用空间不足 ({可用KB}KB)，证书需要 {证书大小KB}KB。请清理 /system 分区或使用 Magisk 模块方式安装。')
                        return
        except Exception as e:
            self.日志.emit(f'    空间检查跳过: {e}')

        # 4. 确保证书目录存在（个别设备缺 cacerts 目录会导致推送静默失败）
        self.日志.emit('>>> mkdir -p /system/etc/security/cacerts')
        try:
            mkdir_out = (self._adb.执行shell(
                self._序列号,
                'mkdir -p /system/etc/security/cacerts 2>&1 && echo MKDIR_OK',
                timeout=10) or '').strip()
            if 'MKDIR_OK' not in mkdir_out:
                self.日志.emit(f'    ✗ 创建证书目录失败: {mkdir_out or "无输出"}')
                self.完成.emit(False, '创建 /system/etc/security/cacerts 失败，请检查 root 权限')
                return
            self.日志.emit('    目录就绪')
        except Exception as e:
            self.日志.emit(f'    创建目录异常: {e}')
            self.完成.emit(False, f'创建证书目录失败: {e}')
            return

        # 5. adb push
        self.日志.emit(f'>>> adb push {临时路径} {远程路径}')
        try:
            结果 = self._adb.直接执行(self._序列号, ['push', 临时路径, 远程路径], timeout=30)
            self.日志.emit(f'    {结果.strip() or "推送成功"}')
        except Exception as e:
            错误信息 = str(e)
            self.日志.emit(f'    ✗ 推送失败: {错误信息}')
            # 详细诊断: 逐步排查失败原因
            self.日志.emit('    --- 诊断信息 ---')
            # 诊断1: 检查 /system 是否真的可写
            try:
                验证 = self._adb.执行shell(
                    self._序列号,
                    'touch /system/.super_adb_write_test && rm /system/.super_adb_write_test && echo WRITABLE',
                    timeout=5)
                if 'WRITABLE' not in (验证 or ''):
                    self.日志.emit('    ✗ 诊断: /system 实际为只读，remount 可能未生效')
                    错误信息 += ' | 原因: /system 为只读分区（remount 可能未生效）'
                else:
                    self.日志.emit('    ✓ 诊断: /system 分区可写')
            except Exception as de:
                self.日志.emit(f'    ✗ 诊断: /system 写入验证异常: {de}')
                错误信息 += f' | 原因: /system 写入验证异常({de})'
            # 诊断2: 检查远程目标目录是否存在
            try:
                目录检查 = self._adb.执行shell(
                    self._序列号,
                    f'ls -ld /system/etc/security/cacerts 2>&1',
                    timeout=5)
                self.日志.emit(f'    诊断: 目标目录状态: {目录检查.strip() or "无输出"}')
                if 'No such file' in (目录检查 or ''):
                    错误信息 += ' | 原因: 目标目录 /system/etc/security/cacerts 不存在'
            except Exception as de:
                self.日志.emit(f'    诊断: 目录检查异常: {de}')
            # 诊断3: 检查本地文件大小是否正确
            try:
                本地大小 = os.path.getsize(临时路径)
                self.日志.emit(f'    诊断: 本地证书大小: {本地大小}B')
            except Exception:
                self.日志.emit('    诊断: 无法获取本地文件大小')
            # 诊断4: 检查 /system 分区剩余空间
            try:
                空间 = self._adb.执行shell(
                    self._序列号, 'df -k /system 2>&1', timeout=5)
                self.日志.emit(f'    诊断: /system 分区空间: {(空间 or "").strip()}')
            except Exception:
                pass
            # 给出建议
            if '字节数不一致' in 错误信息 or '0B' in 错误信息:
                错误信息 += ' | 建议: 设备权限不足或 /system 分区已满，请确认 root 权限和分区空间'
            elif '只读' in 错误信息:
                错误信息 += ' | 建议: 请执行 adb root && adb remount 重新获取写权限'
            self.日志.emit(f'    --- 诊断结束 ---')
            self.完成.emit(False, f'推送失败: {错误信息}')
            return
        # 6. chmod 777（用标记验证，设备端 chmod 失败不抛异常）
        self.日志.emit(f'>>> adb shell chmod 777 {远程路径}')
        try:
            chmod_out = (self._adb.执行shell(
                self._序列号,
                f'chmod 777 {远程路径} 2>&1 && echo CHMOD_OK',
                timeout=10) or '').strip()
            if 'CHMOD_OK' not in chmod_out:
                self.日志.emit(f'    权限设置失败: {chmod_out or "无输出"}')
                self.完成.emit(False, f'chmod 失败: {chmod_out or 远程路径}')
                return
            self.日志.emit('    权限设置成功')
        except Exception as e:
            self.日志.emit(f'    权限设置失败: {e}')
            self.完成.emit(False, f'chmod 失败: {e}')
            return
        # 7. 验证（硬性关卡：设备上文件不存在即判失败，不再误报成功）
        self.日志.emit(f'>>> 验证: adb shell ls -l {远程路径}')
        try:
            验证 = (self._adb.执行shell(
                self._序列号, f'ls -l {远程路径}', timeout=10) or '').strip()
        except Exception as e:
            验证 = f'验证异常: {e}'
        self.日志.emit(f'    {验证 or "无输出"}')
        if not 验证 or 'No such file' in 验证 or '验证异常' in 验证:
            self.完成.emit(False,
                           f'安装验证失败: 设备上不存在 {远程路径}'
                           f'（{验证 or "无输出"}）')
            return
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

        super().__init__(parent, 标题=标题, 最小尺寸=(620, 480), 发光=False)

        # 内层亮边卡片（与 TCPDump/PCAP 弹窗同款 4px 主题色边框）
        self.card, _ = _create_popup_card(self, self._主题id)

        根布局 = QVBoxLayout(self.card)
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
            f'font-family: ui-monospace, "Cascadia Code", Consolas, "Courier New", monospace; font-size: 9pt; }}'
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
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        self.拖拽区.apply_theme(theme_id)
        强调色 = THEMES[theme_id]['accent']
        self.证书标签.setStyleSheet(f'color: {强调色}; font-weight: bold;')
        self.输出框.setStyleSheet(self._输出框样式())


# ----------------------------------------------------------------------
# 独立运行测试入口（直接 python cert_install_dialog.py 可预览 UI）
# ----------------------------------------------------------------------
if __name__ == '__main__':
    import sys
    # 把 项目UI 目录加入路径，解决 ui_styles/dialog_styles/png_rc 的导入
    _ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'ui')
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
