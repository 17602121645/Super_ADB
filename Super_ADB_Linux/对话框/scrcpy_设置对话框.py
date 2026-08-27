# -*- coding: utf-8 -*-
"""scrcpy 投屏参数设置对话框。

让投屏的分辨率 / 码率 / 帧率 / 编码 / 渲染驱动 / 编码器 / 连接模式 / 回退开关
在界面上可调，设置持久化到 QSettings(org='Super_ADB', app='Super_ADB')，键前缀 'scrcpy/'。

2026-08-28 新增 3 项（解决 x86_64/模拟器白屏问题）：
  1. 编码器（video_encoder）：自动 / 硬编码默认 / Google 软编码 / 自定义
  2. 连接模式（tunnel_mode）：自动(reverse优先) / reverse(官方默认) / forward
  3. 硬编码器失败自动回退软编码器（fallback_sw_encoder）：默认开
"""

import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QFormLayout, QDialogButtonBox, QSizePolicy, QLineEdit, QWidget,
)
from 项目UI.界面样式 import THEMES, get_stylesheet, get_current_theme_id
from 项目UI.弹窗样式 import add_green_glow, highlight_card_style, _create_popup_card

ORG = 'Super_ADB'
APP = 'Super_ADB'

# ── 选项定义：(显示文本, 实际值) ──
RESOLUTION_OPTIONS = [
    ('原画(不推荐, 可能卡死)', 0),
    ('640 (最低延迟)', 640), ('800', 800),
    ('1024 (默认/推荐)', 1024), ('1280', 1280),
    ('1600', 1600), ('1920', 1920),
]
BITRATE_OPTIONS = [
    ('4 Mbps (WiFi弱)', '4M'), ('8 Mbps (默认)', '8M'),
    ('16 Mbps (清晰)', '16M'), ('24 Mbps (USB)', '24M'),
    ('32 Mbps (极高)', '32M'),
]
FPS_OPTIONS = [('24', '24'), ('30 (默认)', '30'), ('60', '60')]
CODEC_OPTIONS = [
    ('H264 (默认/最稳)', 'h264'),
    ('H265 (更清晰，需设备支持)', 'h265'),
    ('AV1 (最省流量，新设备才支持)', 'av1'),
]
RENDER_OPTIONS_WIN = [
    ('自动', ''), ('Direct3D', 'direct3d'),
    ('OpenGL', 'opengl'), ('Software (兜底)', 'software'),
]
RENDER_OPTIONS_OTHER = [
    ('自动', ''), ('OpenGL', 'opengl'), ('Software (兜底)', 'software'),
]

# 2026-08-28 新增：编码器 & 连接模式
ENCODER_OPTIONS = [
    # value = (模式, 自定义字符串)，模式: 'auto'/'hard'/'soft'/'custom'
    ('自动选择（推荐：硬编码优先，失败自动回退软编码）', ('auto', '')),
    ('硬编码器（设备默认，性能最好）', ('hard', '')),
    ('Google 软编码器 c2.android.avc.encoder（兼容性最强）', ('soft', '')),
    ('自定义编码器名（高级）', ('custom', '')),
]
TUNNEL_OPTIONS = [
    # None 表示自动；True = reverse；False = forward
    ('自动（WiFi优先reverse / USB优先forward）', None),
    ('Reverse 官方模式（Server主动连PC，官方 scrcpy 默认）', True),
    ('Forward 兼容模式（PC主动连LocalAbstract隧道）', False),
]

# 首次使用的默认值
DEFAULTS = {
    'resolution': 1024,
    'bitrate': '8M',
    'fps': '30',
    'codec': 'h264',
    'render': 'direct3d' if sys.platform == 'win32' else '',
    'turn_off_screen': False,
    # 2026-08-28 新增默认值
    'encoder_mode': 'auto',       # auto / hard / soft / custom
    'encoder_custom': '',          # custom 模式下的编码器名
    'tunnel_mode': None,           # None=True自动 / True=reverse / False=forward
    'fallback_sw_encoder': True,   # 硬编码器 Aborted 时是否自动回退软编码器
}


def load_scrcpy_settings():
    """从 QSettings 读取投屏设置，缺省时回退 DEFAULTS。"""
    s = QSettings(ORG, APP)

    def _g(key, cast=None):
        v = s.value(f'scrcpy/{key}', DEFAULTS[key])
        if cast is not None and v is not None:
            try:
                v = cast(v)
            except Exception:
                v = DEFAULTS[key]
        return v

    return {
        'resolution': int(_g('resolution', int)),
        'bitrate': str(_g('bitrate', str)),
        'fps': str(_g('fps', str)),
        'codec': str(_g('codec', str)),
        'render': str(_g('render', str)),
        'turn_off_screen': bool(_g('turn_off_screen')),
        # 2026-08-28 新增
        'encoder_mode': str(_g('encoder_mode', str)),
        'encoder_custom': str(_g('encoder_custom', str)),
        # tunnel_mode: QSettings 存 'auto' / 'reverse' / 'forward' 字符串，读时转换
        **_解析tunnel(_g('tunnel_mode', str) if _g('tunnel_mode', str) != '' else None),
        'fallback_sw_encoder': bool(_g('fallback_sw_encoder')),
    }


def _解析tunnel(v):
    """把 QSettings 存储的字符串转成 {'use_reverse': True/False/None}。"""
    if v is None or v in ('auto', 'None', ''):
        return {'use_reverse': None}
    if isinstance(v, bool):
        return {'use_reverse': v}
    if str(v).lower() in ('reverse', 'true', '1'):
        return {'use_reverse': True}
    if str(v).lower() in ('forward', 'false', '0'):
        return {'use_reverse': False}
    return {'use_reverse': None}


def _编码隧道(v) -> str:
    """反向：use_reverse -> 存储字符串。"""
    if v is None:
        return 'auto'
    return 'reverse' if v else 'forward'


def build_scrcpy_args(settings):
    """根据设置字典组装 外部 scrcpy.exe 命令行参数列表（供 Adb设备操作.投屏 使用）。"""
    args = []
    res = settings.get('resolution', 0)
    if res and int(res) > 0:
        args += ['--max-size', str(int(res))]
    args += ['--video-bit-rate', str(settings.get('bitrate', '8M'))]
    args += ['--max-fps', str(settings.get('fps', '60'))]
    codec = settings.get('codec', 'h264')
    if codec:
        args += ['--video-codec', codec]
    render = settings.get('render', '')
    if render:
        args += ['--render-driver', render]
    if settings.get('turn_off_screen'):
        args += ['--turn-screen-off']
    # 编码器：仅 hard/soft/custom 模式才显式传 --video-encoder
    enc_mode = settings.get('encoder_mode', 'auto')
    if enc_mode == 'soft':
        args += ['--video-encoder', 'c2.android.avc.encoder']
    elif enc_mode == 'custom':
        enc = str(settings.get('encoder_custom', '')).strip()
        if enc:
            args += ['--video-encoder', enc]
    # 连接模式：forward 模式下显式 --force-adb-forward（官方默认reverse）
    use_reverse = settings.get('use_reverse', None)
    if use_reverse is False:
        args += ['--force-adb-forward']
    # 关闭音频（Android 9 以下电视/模拟器不支持，避免启动耗时无效尝试）
    args += ['--no-audio']
    return args


def resolve_video_encoder(settings):
    """根据设置计算传给「投屏客户端 / ScrcpySession」的 video_encoder 字符串。
    返回 None 表示「不指定，设备自动选」。"""
    mode = settings.get('encoder_mode', 'auto')
    if mode == 'soft':
        return 'c2.android.avc.encoder'
    if mode == 'custom':
        enc = str(settings.get('encoder_custom', '')).strip()
        return enc if enc else None
    # hard / auto：交由客户端自行选择（auto 模式会配合 fallback_sw_encoder 兜底）
    return None


class Scrcpy设置对话框(QDialog):
    """投屏参数设置对话框，确定后写入 QSettings。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('scrcpy 投屏设置')
        self.setMinimumWidth(440)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id) + "QDialog { background-color: transparent; }")
        self.card, _ = _create_popup_card(self, self._theme_id)
        self._build_ui()
        self._load()

    def apply_theme(self, theme_id):
        if theme_id not in THEMES:
            theme_id = 'dark_teal'
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id) + "QDialog { background-color: transparent; }")
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        if hasattr(self, '_hint_label') and self._hint_label is not None:
            self._hint_label.setStyleSheet(f"color: {THEMES[theme_id]['text_disabled']}; font-size: 9pt;")
        self.update()

    # ── UI 构建 ──
    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setSpacing(10)

        def _行(标签文本, 控件容器):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl = QLabel(标签文本)
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            lbl.setMinimumWidth(140)
            row.addWidget(lbl)
            if isinstance(控件容器, QWidget):
                row.addWidget(控件容器, 1)
            else:
                row.addWidget(控件容器)
            lay.addLayout(row)

        # ── 基础参数 ──
        self.cb_res = self._mk_combo(RESOLUTION_OPTIONS)
        _行('分辨率（最长边）', self.cb_res)

        self.cb_br = self._mk_combo(BITRATE_OPTIONS)
        _行('码率', self.cb_br)

        self.cb_fps = self._mk_combo(FPS_OPTIONS)
        _行('帧率上限', self.cb_fps)

        self.cb_codec = self._mk_combo(CODEC_OPTIONS)
        _行('视频编码格式', self.cb_codec)

        self.cb_render = self._mk_combo(
            RENDER_OPTIONS_WIN if sys.platform == 'win32' else RENDER_OPTIONS_OTHER)
        _行('渲染驱动', self.cb_render)

        # ── 2026-08-28 新增：解决白屏相关参数 ──
        self.cb_encoder = self._mk_combo(ENCODER_OPTIONS)
        self.cb_encoder.currentIndexChanged.connect(self._on_encoder_changed)
        # 自定义编码器输入框（仅当选择"自定义"时可用）
        self.edit_encoder_custom = QLineEdit()
        self.edit_encoder_custom.setPlaceholderText(
            '例如 OMX.qcom.video.encoder.avc 或 c2.android.avc.encoder')
        wrap_enc = QWidget()
        hb = QHBoxLayout(wrap_enc)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(6)
        hb.addWidget(self.cb_encoder, 2)
        hb.addWidget(self.edit_encoder_custom, 3)
        _行('视频编码器', wrap_enc)

        self.cb_tunnel = self._mk_combo(TUNNEL_OPTIONS)
        _行('连接模式', self.cb_tunnel)

        self.chk_fallback = QCheckBox(
            '硬编码器启动失败时，自动回退 Google 软编码器重试（推荐开启，可解决大多数"白屏/Aborted"）')
        lay.addWidget(self.chk_fallback)

        self.chk_off = QCheckBox('投屏时关闭设备屏幕（仅部分设备生效）')
        lay.addWidget(self.chk_off)

        # 通用下拉框样式
        for _cb in (self.cb_res, self.cb_br, self.cb_fps, self.cb_codec,
                    self.cb_render, self.cb_encoder, self.cb_tunnel):
            _cb.setEditable(True)
            _cb.lineEdit().setReadOnly(True)
            _cb.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            _cb.setMinimumWidth(180)
            _cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._hint_label = QLabel(
            '■ 白屏/Aborted 排查指引：\n'
            '  1) 若默认配置白屏，优先把「连接模式」切到 Reverse 官方模式再试；\n'
            '  2) 仍不行，把「视频编码器」切到「Google 软编码器」（兼容性最好）；\n'
            '  3) Android 模拟器/x86_64 设备通常需要软编码器才能正常出帧。\n'
            '■ 性能调优：分辨率越低、码率适中，延迟越低；2K 以上设备强烈建议限制为 1024。\n'
            '■ 受 DRM/HDCP 保护的视频内容（Netflix/银行/支付）会黑屏，属硬件限制。'
        )
        self._hint_label.setWordWrap(True)
        self._hint_label.setStyleSheet(
            f"color: {THEMES[self._theme_id]['text_disabled']}; font-size: 9pt;")
        lay.addWidget(self._hint_label)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _mk_combo(self, options):
        cb = QComboBox()
        for label, val in options:
            cb.addItem(label, val)
        return cb

    def _on_encoder_changed(self, _idx):
        data = self.cb_encoder.currentData()
        is_custom = (isinstance(data, tuple) and data[0] == 'custom')
        self.edit_encoder_custom.setEnabled(is_custom)
        if not is_custom:
            self.edit_encoder_custom.clear()

    # ── 读取 / 保存 ──
    def _load(self):
        s = load_scrcpy_settings()
        self._select(self.cb_res, s['resolution'])
        self._select(self.cb_br, s['bitrate'])
        self._select(self.cb_fps, s['fps'])
        self._select(self.cb_codec, s['codec'])
        self._select(self.cb_render, s['render'])
        self.chk_off.setChecked(s['turn_off_screen'])
        # 2026-08-28 新增
        em = s.get('encoder_mode', 'auto')
        ec = s.get('encoder_custom', '')
        if em == 'custom':
            self._select(self.cb_encoder, ('custom', ''))
            self.edit_encoder_custom.setText(ec)
            self.edit_encoder_custom.setEnabled(True)
        else:
            self._select(self.cb_encoder, (em, ''))
            self.edit_encoder_custom.setEnabled(False)
        self._select(self.cb_tunnel, s.get('use_reverse'))
        self.chk_fallback.setChecked(bool(s.get('fallback_sw_encoder', True)))

    @staticmethod
    def _select(combo, value):
        """按 data 匹配下拉项；对 tuple 比较首字段（模式），对其余直接比较。"""
        count = combo.count()
        for i in range(count):
            item = combo.itemData(i)
            if item is None and value is None:
                combo.setCurrentIndex(i)
                return
            if isinstance(item, tuple) and isinstance(value, tuple):
                # 编码器：只比较 mode 字段，忽略 custom 内容
                if item[0] == value[0]:
                    combo.setCurrentIndex(i)
                    return
            if isinstance(item, tuple) and not isinstance(value, tuple):
                if item[0] == value:
                    combo.setCurrentIndex(i)
                    return
            if item == value:
                combo.setCurrentIndex(i)
                return
        if count > 0:
            combo.setCurrentIndex(0)

    def accept(self):
        s = QSettings(ORG, APP)
        s.setValue('scrcpy/resolution', self.cb_res.currentData())
        s.setValue('scrcpy/bitrate', self.cb_br.currentData())
        s.setValue('scrcpy/fps', self.cb_fps.currentData())
        s.setValue('scrcpy/codec', self.cb_codec.currentData())
        s.setValue('scrcpy/render', self.cb_render.currentData())
        s.setValue('scrcpy/turn_off_screen', self.chk_off.isChecked())
        # 2026-08-28 新增
        enc_data = self.cb_encoder.currentData()
        enc_mode = enc_data[0] if isinstance(enc_data, tuple) else 'auto'
        s.setValue('scrcpy/encoder_mode', enc_mode)
        s.setValue('scrcpy/encoder_custom',
                   self.edit_encoder_custom.text().strip() if enc_mode == 'custom' else '')
        s.setValue('scrcpy/tunnel_mode',
                   _编码隧道(self.cb_tunnel.currentData()))
        s.setValue('scrcpy/fallback_sw_encoder', self.chk_fallback.isChecked())
        s.sync()
        super().accept()
