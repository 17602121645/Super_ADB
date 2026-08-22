# -*- coding: utf-8 -*-
"""scrcpy 投屏参数设置对话框。

让投屏的分辨率 / 码率 / 帧率 / 编码 / 渲染驱动在界面上可调，
设置持久化到 QSettings(org='Super_ADB', app='Super_ADB')，键前缀 'scrcpy/'。
"""

import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox, QCheckBox,
    QFormLayout, QDialogButtonBox,
)
from 项目UI.界面样式 import THEMES, get_stylesheet, get_current_theme_id

ORG = 'Super_ADB'
APP = 'Super_ADB'

# 选项定义：(显示文本, 实际值)
RESOLUTION_OPTIONS = [
    ('原画', 0), ('1024', 1024), ('1280', 1280),
    ('1600', 1600), ('1920', 1920),
]
BITRATE_OPTIONS = [
    ('8 Mbps', '8M'), ('16 Mbps', '16M'),
    ('24 Mbps', '24M'), ('32 Mbps', '32M'),
]
FPS_OPTIONS = [('30', '30'), ('60', '60')]
CODEC_OPTIONS = [
    ('H264（默认/最稳）', 'h264'),
    ('H265（更清晰，需设备与显卡支持）', 'h265'),
]
RENDER_OPTIONS_WIN = [
    ('自动', ''), ('Direct3D', 'direct3d'),
    ('OpenGL', 'opengl'), ('Software', 'software'),
]
RENDER_OPTIONS_OTHER = [
    ('自动', ''), ('OpenGL', 'opengl'), ('Software', 'software'),
]

# 首次使用的默认值
DEFAULTS = {
    'resolution': 1280,
    'bitrate': '16M',
    'fps': '60',
    'codec': 'h264',
    'render': 'direct3d' if sys.platform == 'win32' else '',
    'turn_off_screen': False,
}


def load_scrcpy_settings():
    """从 QSettings 读取投屏设置，缺省时回退 DEFAULTS。"""
    s = QSettings(ORG, APP)
    return {
        'resolution': int(s.value('scrcpy/resolution', DEFAULTS['resolution'])),
        'bitrate': str(s.value('scrcpy/bitrate', DEFAULTS['bitrate'])),
        'fps': str(s.value('scrcpy/fps', DEFAULTS['fps'])),
        'codec': str(s.value('scrcpy/codec', DEFAULTS['codec'])),
        'render': str(s.value('scrcpy/render', DEFAULTS['render'])),
        'turn_off_screen': bool(s.value('scrcpy/turn_off_screen',
                                        DEFAULTS['turn_off_screen'])),
    }


def build_scrcpy_args(settings):
    """根据设置字典组装 scrcpy 命令行参数列表。

    返回的列表直接作为 extra_args 传给 Adb设备操作.scrcpy()，
    完全覆盖内建默认参数。
    """
    args = []
    res = settings.get('resolution', 0)
    if res and int(res) > 0:
        args += ['--max-size', str(int(res))]
    args += ['--video-bit-rate', str(settings.get('bitrate', '16M'))]
    args += ['--max-fps', str(settings.get('fps', '60'))]
    codec = settings.get('codec', 'h264')
    if codec:
        args += ['--video-codec', codec]
    render = settings.get('render', '')
    if render:
        args += ['--render-driver', render]
    if settings.get('turn_off_screen'):
        args += ['--turn-screen-off']
    # 电视 Android 9 不支持音频转发，默认关掉以加快启动、避免无效尝试
    args += ['--no-audio']
    return args


class Scrcpy设置对话框(QDialog):
    """投屏参数设置对话框，确定后写入 QSettings。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('scrcpy 投屏设置')
        self.setMinimumWidth(380)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id) + "QDialog { background-color: transparent; }")
        self._build_ui()
        self._load()

    def apply_theme(self, theme_id):
        """运行时切换主题。"""
        if theme_id not in THEMES:
            theme_id = 'dark_teal'
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id) + "QDialog { background-color: transparent; }")
        if hasattr(self, '_hint_label') and self._hint_label is not None:
            self._hint_label.setStyleSheet(f"color: {THEMES[theme_id]['text_disabled']}; font-size: 9pt;")
        self.update()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        def _行(标签文本, 控件):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl = QLabel(标签文本)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addWidget(控件)
            lay.addLayout(row)

        self.cb_res = QComboBox()
        for label, val in RESOLUTION_OPTIONS:
            self.cb_res.addItem(label, val)
        _行('分辨率（最长边）', self.cb_res)

        self.cb_br = QComboBox()
        for label, val in BITRATE_OPTIONS:
            self.cb_br.addItem(label, val)
        _行('码率', self.cb_br)

        self.cb_fps = QComboBox()
        for label, val in FPS_OPTIONS:
            self.cb_fps.addItem(label, val)
        _行('帧率上限', self.cb_fps)

        self.cb_codec = QComboBox()
        for label, val in CODEC_OPTIONS:
            self.cb_codec.addItem(label, val)
        _行('视频编码', self.cb_codec)

        self.cb_render = QComboBox()
        render_opts = RENDER_OPTIONS_WIN if sys.platform == 'win32' else RENDER_OPTIONS_OTHER
        for label, val in render_opts:
            self.cb_render.addItem(label, val)
        _行('渲染驱动', self.cb_render)

        self.chk_off = QCheckBox('投屏时关闭手机屏幕')
        lay.addWidget(self.chk_off)

        # 下拉框文字靠左（显示区 + 弹出列表）
        for _cb in (self.cb_res, self.cb_br, self.cb_fps, self.cb_codec, self.cb_render):
            _cb.setEditable(True)
            _cb.lineEdit().setReadOnly(True)
            _cb.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            _cb.view().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            _cb.setMaximumWidth(160)
            _cb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._hint_label = QLabel(
            '提示：分辨率越低延迟越低；码率越高越清晰（但更吃带宽）。\n'
            '受 DRM/HDCP 保护的视频内容仍会黑屏，属硬件限制。'
        )
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(f"color: {THEMES[self._theme_id]['text_disabled']}; font-size: 9pt;")
        lay.addWidget(self._hint_label)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load(self):
        s = load_scrcpy_settings()
        self._select(self.cb_res, s['resolution'])
        self._select(self.cb_br, s['bitrate'])
        self._select(self.cb_fps, s['fps'])
        self._select(self.cb_codec, s['codec'])
        self._select(self.cb_render, s['render'])
        self.chk_off.setChecked(s['turn_off_screen'])

    @staticmethod
    def _select(combo, value):
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def accept(self):
        s = QSettings(ORG, APP)
        s.setValue('scrcpy/resolution', self.cb_res.currentData())
        s.setValue('scrcpy/bitrate', self.cb_br.currentData())
        s.setValue('scrcpy/fps', self.cb_fps.currentData())
        s.setValue('scrcpy/codec', self.cb_codec.currentData())
        s.setValue('scrcpy/render', self.cb_render.currentData())
        s.setValue('scrcpy/turn_off_screen', self.chk_off.isChecked())
        s.sync()
        super().accept()
