# -*- coding: utf-8 -*-
"""
主入口 Mixin：主题系统
====================
主题切换、标题栏按钮样式、品牌标识、主题传播等方法。
通过多继承混入 主窗口，可访问 self 的所有属性和方法。
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from ui.ui_styles import (
    THEMES, DEFAULT_THEME, get_stylesheet, get_theme_ids, get_theme_name, FONT_FAMILY,
)
from tools.adb_tools import 加载json配置, 保存json配置

# 配置常量（与主入口保持一致）
CONFIG_NAME = 'config/super_adb_config.json'
THEME_CONFIG_KEY = 'theme'
主题重绘延迟毫秒 = 100  # QTimer 延迟，避开 QMenu 关闭流程吞掉重绘


class 主题系统Mixin:
    """主题系统方法集合。"""

    # ------------------------------------------------------------------
    # 无边框窗口：标题栏按钮 + 主题切换
    # ------------------------------------------------------------------
    def _解析强调色rgb(self):
        """把当前主题的 accent 字符串 'rgb(29,233,182)' 解析成 (r, g, b) 三元组。"""
        accent = THEMES.get(self._current_theme, THEMES[DEFAULT_THEME])['accent']
        s = accent
        if s.startswith('rgb(') and s.endswith(')'):
            s = s[4:-1]
        parts = [int(p.strip()) for p in s.split(',')[:3]]
        return parts[0], parts[1], parts[2]

    def _主样式表(self, theme_id):
        """拼 主窗口 专属样式表：仅主题样式，不额外加主窗口背景色。"""
        return get_stylesheet(theme_id)

    def _设置列表背景色(self, theme_id):
        """空方法（已移除列表背景色设置）。"""
        pass

    @staticmethod
    def _背景是否深色(bg_hex):
        """按背景亮度（W3C 调整后）粗判深浅：dark_* = True, light_soft = False。"""
        s = bg_hex.lstrip('#')
        if len(s) != 6:
            return True
        try:
            rr, gg, bb = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        except ValueError:
            return True
        lum = (0.299 * rr + 0.587 * gg + 0.114 * bb) / 255.0
        return lum < 0.55

    def _从配置加载主题(self):
        """启动时从 super_adb_config.json 读 theme 字段，缺省/非法回退默认。"""
        try:
            tid = 加载json配置(CONFIG_NAME).get(THEME_CONFIG_KEY)
            if isinstance(tid, str) and tid in THEMES:
                return tid
        except Exception as e:
            print(f'[主题] 读取配置失败，回退默认主题: {e!r}')
        return DEFAULT_THEME

    def _保存主题到配置(self, theme_id):
        """把当前主题 id 写入配置。"""
        try:
            cfg = 加载json配置(CONFIG_NAME)
            cfg[THEME_CONFIG_KEY] = theme_id
            保存json配置(CONFIG_NAME, cfg)
        except Exception as e:
            print(f'[主题] 保存配置失败: {e}')

    def _初始化主题菜单(self):
        """构建主题下拉菜单，7 套主题各一项，点击触发 _切换主题。"""
        self._theme_menu = QMenu(self)
        self._theme_action_map = {}  # id -> QAction
        for tid in get_theme_ids():
            act = QAction(get_theme_name(tid), self)
            act.setCheckable(True)
            act.triggered.connect(lambda _checked=False, t=tid: self._切换主题(t))
            self._theme_menu.addAction(act)
            self._theme_action_map[tid] = act
        self.btnTheme.setMenu(self._theme_menu)

    def _刷新主题菜单勾选(self):
        """把当前主题的菜单项勾上，其余取消。"""
        for tid, act in self._theme_action_map.items():
            act.setChecked(tid == self._current_theme)

    def 刷新标题栏按钮样式(self):
        """统一刷新标题栏所有按钮的局部样式（关于/环境config/主题/品牌文字）。"""
        for btn, style_fn in (
            (getattr(self, '_btn_about', None), self._关于按钮样式),
            (getattr(self, '_btn_env', None), self._环境配置按钮样式),
            (getattr(self, '_btn_theme', None), self._主题按钮样式),
            (getattr(self, 'brandText', None), self._品牌文字样式),
                    ):
            if btn is not None:
                try:
                    btn.setStyleSheet(style_fn())
                except Exception as e:
                    print(f'[主题] 标题栏按钮样式刷新失败: {e!r}')

    def _切换主题(self, theme_id):
        """切换主题：setStyleSheet + 重应用标题栏按钮局部样式 + 持久化。"""
        if theme_id not in THEMES or theme_id == self._current_theme:
            return
        # 防抖——取消上一次未执行的延迟重绘
        if getattr(self, '_主题重绘定时器', None) is not None:
            self._主题重绘定时器.stop()
        self._current_theme = theme_id
        self.setStyleSheet(self._主样式表(theme_id))
        self.刷新标题栏按钮样式()
        # 设置 fileMgr_tree 和 logViewer_textEdit 背景色
        self._设置列表背景色(theme_id)
                # 强制刷新 QTreeView 样式（切换主题后可能不立即更新）
        if hasattr(self, 'fileMgr_tree'):
            self.fileMgr_tree.style().unpolish(self.fileMgr_tree)
            self.fileMgr_tree.style().polish(self.fileMgr_tree)
            self.fileMgr_tree.update()
        # 同步更新打开的设备信息弹窗
        if hasattr(self, '_设备信息弹窗') and self._设备信息弹窗 is not None:
            try:
                self._设备信息弹窗.apply_theme(theme_id)
            except Exception:
                pass
        # 同步更新打开的修改时间弹窗
        if hasattr(self, '_修改时间弹窗') and self._修改时间弹窗 is not None:
            try:
                self._修改时间弹窗.apply_theme(theme_id)
            except Exception:
                pass
        # 同步更新打开的无线调试对话框（含子页面传播）
        if hasattr(self, '_wireless_debug_dialog') and self._wireless_debug_dialog is not None:
            try:
                self._wireless_debug_dialog.apply_theme(theme_id)
            except Exception:
                pass
        # 同步更新打开的环境配置对话框
        if hasattr(self, '_env_config_dialog') and self._env_config_dialog is not None:
            try:
                self._env_config_dialog.apply_theme(theme_id)
            except Exception:
                pass
        # 同步更新打开的关于对话框
        if hasattr(self, '_about_dialog') and self._about_dialog is not None:
            try:
                self._about_dialog.apply_theme(theme_id)
            except Exception:
                pass
        # 批量同步其他打开的弹窗（统一调用 apply_theme，无此方法则回退 setStyleSheet）
        for _ref in ('_install_dialog', '_cert_dialog', '_json_tool_dialog',
                     '_md5_dialog', '_timestamp_dialog', '_wifi_dialog',
                     '_tcpdump_dialog', '_monkey_window', '_app_monitor_window',
                     '_wifi_history_dialog', '_hash_context_dialog',
                     '_scrcpy_dialog', '_desk_cat', '_pcap_parser_dialog'):
            _dlg = getattr(self, _ref, None)
            if _dlg is not None:
                try:
                    if hasattr(_dlg, 'apply_theme'):
                        _dlg.apply_theme(theme_id)
                    else:
                        _dlg.setStyleSheet(get_stylesheet(theme_id))
                except Exception:
                    pass
        self._保存主题到配置(theme_id)
        self._刷新主题菜单勾选()
        self.update()
        self._主题重绘定时器 = QTimer(self)
        self._主题重绘定时器.setSingleShot(True)
        self._主题重绘定时器.timeout.connect(self._强制主题重绘)
        self._主题重绘定时器.start(主题重绘延迟毫秒)

    def _强制主题重绘(self):
        """主题切换后的强制全量重绘（延迟到 QMenu 完全关闭后执行）。"""
        self.刷新标题栏按钮样式()
        st = self.style()
        st.unpolish(self)
        st.polish(self)
        self.update()
        self.repaint()
        wh = self.windowHandle()
        if wh is not None:
            wh.requestUpdate()
        if sys.platform == 'win32':
            import ctypes
            hwnd = int(self.winId())
            ctypes.windll.user32.InvalidateRect(hwnd, None, True)
            ctypes.windll.user32.UpdateWindow(hwnd)
        try:
            self._传播主题到弹窗(self._current_theme)
        except Exception as e:
            print(f'[主题] 传播到弹窗失败: {e!r}')

    def _传播主题到弹窗(self, theme_id):
        """主题切换后，把新样式表刷到已打开的弹窗子窗口。"""
        from PySide6.QtWidgets import QApplication
        sheet = get_stylesheet(theme_id)
        已发现弹窗 = []
        for win in QApplication.topLevelWidgets():
            if win is self:
                continue
            if not win.isVisible():
                continue
            if callable(getattr(win, 'apply_theme', None)):
                已发现弹窗.append(win)
        print(f'[主题] 发现 {len(已发现弹窗)} 个待同步弹窗: {[type(w).__name__ for w in 已发现弹窗]}')
        for dlg in 已发现弹窗:
            try:
                dlg.apply_theme(theme_id)
            except Exception as e:
                print(f'[主题] 弹窗 {type(dlg).__name__} 样式同步失败: {e!r}')
        try:
            from ui.dialog_styles import 重建所有发光效果
            重建所有发光效果(self._解析强调色rgb())
        except Exception as e:
            print(f'[主题] 发光效果重建失败: {e!r}')

    def _关于按钮样式(self):
        """标题栏「关于」按钮样式。"""
        r, g, b = self._解析强调色rgb()
        return (f"QPushButton{{background:transparent;border:none;color:rgb({r},{g},{b});"
                f"font:700 10px '{FONT_FAMILY}';border-radius:4px;}}"
                f"QPushButton:hover{{background:rgba({r},{g},{b},35);color:#ffffff;}}"
                f"QPushButton:pressed{{background:rgba({r},{g},{b},60);color:#ffffff;}}")

    def _主题按钮样式(self):
        """标题栏「主题」按钮样式。"""
        r, g, b = self._解析强调色rgb()
        return (f"QPushButton{{background:transparent;border:none;color:rgb({r},{g},{b});"
                f"font:700 10px '{FONT_FAMILY}';border-radius:4px;}}"
                f"QPushButton:hover{{background:rgba({r},{g},{b},35);color:#ffffff;}}"
                f"QPushButton:pressed{{background:rgba({r},{g},{b},60);color:#ffffff;}}"
                f"QPushButton::menu-indicator{{image:none;width:0;height:0;}}")

    def _环境配置按钮样式(self):
        """标题栏「环境配置」按钮样式。"""
        r, g, b = self._解析强调色rgb()
        return (f"QPushButton{{background:transparent;border:none;color:rgb({r},{g},{b});"
                f"font:700 10px '{FONT_FAMILY}';border-radius:4px;}}"
                f"QPushButton:hover{{background:rgba({r},{g},{b},35);color:#ffffff;}}"
                f"QPushButton:pressed{{background:rgba({r},{g},{b},60);color:#ffffff;}}")

    def _窗口按钮样式(self, is_close=True):
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

    def _初始化品牌标签(self):
        """品牌标识由 .ui 定义，这里只设透明背景和主题色文字。"""
        self.brandIcon.setStyleSheet("QLabel{background:transparent;border:none;padding:0;margin:0;}")
        self.brandText.setStyleSheet(self._品牌文字样式())

    def _品牌文字样式(self):
        """品牌文字样式：与主题强调色一致。"""
        r, g, b = self._解析强调色rgb()
        return (f"QLabel{{color:rgb({r},{g},{b});background:transparent;border:none;"
                f"padding:0;margin:0;font:700 13px '微软雅黑','{FONT_FAMILY}';}}")
