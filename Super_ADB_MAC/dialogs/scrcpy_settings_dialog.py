# -*- coding: utf-8 -*-
"""scrcpy 投屏参数设置对话框（映射官方 scrcpy 4.1 命令行参数）。

设计原则：
  - 大部分参数默认值 = 官方默认（即「不传该参数，让 scrcpy 自己决定」）。
  - 低延迟优化默认：max_size=1280、max_fps=60、no_audio=True（针对高分辨率/高刷 Android 11+ 设备降低编码延迟）。
  - 下拉框首项为「默认 (官方 xxx)」，选中时不传参；选具体值才传 --flag value。
  - 复选框默认不勾选（不挂 --flag）；勾选后才传。no_audio 例外，默认勾选。
  - 文本框留空 = 不传；填写后传 --flag value。
  - 设置持久化到 QSettings(org='Super_ADB', app='Super_ADB')，键前缀 'scrcpy/'。

参数分组（5 个页签）：视频 / 音频 / 窗口 / 控制 / 设备连接。
底部另有「自定义额外参数」文本框，可直接追加任意 scrcpy 命令行参数。
"""

import sys
import shlex

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QCheckBox,
    QFormLayout, QDialogButtonBox, QSizePolicy, QLineEdit, QWidget,
    QTabWidget, QScrollArea, QPushButton,
)
from ui.ui_styles import THEMES, get_stylesheet, get_current_theme_id
from ui.dialog_styles import add_green_glow, highlight_card_style, _create_popup_card

ORG = 'Super_ADB'
APP = 'Super_ADB'

# ────────────────────────────────────────────────────────────
# 参数元数据：(key, 标签, 类型, 选项, 默认值, scrcpy参数, 帮助)
#   类型: 'combo' | 'check' | 'text'
#   combo 选项: [(显示文本, 实际值), ...]，实际值=None 表示「默认不传参」
#   check: True=传 flag, False/None=不传
#   text: 空字符串=不传，非空=传 --flag value
# ────────────────────────────────────────────────────────────

def _combo(官方默认文本, *具体值):
    """生成 combo 选项：首项「默认 (官方 xxx)」值为 None，后续为具体值。"""
    opts = [(f'默认 (官方 {官方默认文本})', None)]
    for v in 具体值:
        opts.append((str(v), str(v)))
    return opts


# ── 视频 ──
VIDEO_PARAMS = [
    ('max_size', '最大分辨率（最长边）', 'combo',
     _combo('不限制', 800, 1024, 1280, 1600, 1920, 2560),
     '1280', '--max-size', '限制视频宽高最大值，另一维度按比例自动计算。0/不限制=原画。默认1280以降低高分辨率设备编码延迟。'),
    ('max_fps', '帧率上限', 'combo',
     _combo('不限制', 24, 30, 48, 60, 90, 120, 144),
     '60', '--max-fps', '限制屏幕捕获帧率（Android 10+ 官方支持，更早版本可能也能用）。默认60以降低高刷设备编码延迟。'),
    ('video_bit_rate', '视频码率', 'combo',
     _combo('8M', '2M', '4M', '6M', '8M', '12M', '16M', '24M', '32M'),
     None, '--video-bit-rate', '视频编码码率，支持 K/M 后缀（如 16M=16Mbps）。'),
    ('video_codec', '视频编码格式', 'combo',
     _combo('h264', 'h264', 'h265', 'av1', 'vp8', 'vp9'),
     None, '--video-codec', '选择视频编码器。h264 最稳，h265 同码率更清晰，av1 最省流量（新设备）。'),
    ('video_encoder', '视频编码器（自定义）', 'text',
     [], '', '--video-encoder', '指定 MediaCodec 编码器名称（如 OMX.qcom.video.encoder.avc）。留空=自动选择。可用 scrcpy --list-encoders 查看。'),
    ('video_buffer', '视频缓冲（ms）', 'combo',
     _combo('0', 0, 50, 100, 200, 500),
     None, '--video-buffer', '显示帧前增加缓冲延迟，抗抖动但增加延迟。'),
    ('display_orientation', '显示方向', 'combo',
     _combo('0', 0, 90, 180, 270, 'flip0', 'flip90', 'flip180', 'flip270'),
     None, '--display-orientation', '初始显示方向（顺时针度数）。flip* = 先水平翻转再旋转。'),
    ('display_id', '显示 ID', 'combo',
     _combo('0', 0, 1, 2),
     None, '--display-id', '要镜像的设备显示 ID。可用 scrcpy --list-displays 查看。'),
    ('crop', '裁剪（宽:高:x:y）', 'text',
     [], '', '--crop', '在设备端裁剪屏幕，格式 width:height:x:y（设备自然方向坐标）。留空=不裁剪。'),
]

# ── 音频 ──
AUDIO_PARAMS = [
    ('no_audio', '禁用音频转发', 'check',
     [], True, '--no-audio', '勾选后不转发设备音频（官方默认启用音频）。默认勾选以降低设备端资源竞争、减少延迟。'),
    ('audio_bit_rate', '音频码率', 'combo',
     _combo('128K', '64K', '96K', '128K', '192K', '256K', '320K'),
     None, '--audio-bit-rate', '音频编码码率，支持 K/M 后缀。'),
    ('audio_codec', '音频编码格式', 'combo',
     _combo('opus', 'opus', 'aac', 'flac', 'raw'),
     None, '--audio-codec', '选择音频编码器。opus 默认，aac 兼容性好，flac 无损。'),
    ('audio_source', '音频源', 'combo',
     _combo('output', 'output', 'playback', 'mic', 'mic-unprocessed',
            'mic-camcorder', 'mic-voice-recognition', 'mic-voice-communication',
            'voice-call', 'voice-call-uplink', 'voice-call-downlink', 'voice-performance'),
     None, '--audio-source', '选择音频捕获源。output=整机输出，playback=播放捕获，mic*=麦克风，voice-call=通话。'),
    ('audio_buffer', '音频缓冲（ms）', 'combo',
     _combo('50', 0, 20, 50, 100, 200),
     None, '--audio-buffer', '音频缓冲延迟。越低延迟越小但越容易卡顿（buffer underrun）。'),
]

# ── 窗口 ──
WINDOW_PARAMS = [
    ('fullscreen', '全屏启动', 'check',
     [], False, '--fullscreen', '启动时直接进入全屏。'),
    ('always_on_top', '窗口置顶', 'check',
     [], False, '--always-on-top', 'scrcpy 窗口始终在最上层。'),
    ('window_borderless', '无边框窗口', 'check',
     [], False, '--window-borderless', '禁用窗口装饰（无边框/无标题栏）。'),
    ('render_driver', '渲染驱动', 'combo',
     _combo('自动', 'direct3d', 'opengl', 'opengles2', 'opengles', 'metal', 'software'),
     None, '--render-driver', '请求 SDL 使用指定渲染驱动（只是提示，不一定生效）。Windows 推荐 direct3d。'),
    ('render_fit', '渲染适配模式', 'combo',
     _combo('letterbox', 'letterbox', 'stretched', 'unscaled'),
     None, '--render-fit', 'letterbox=保持比例留黑边，stretched=拉伸铺满，unscaled=不缩放。'),
    ('background_color', '背景色', 'combo',
     _combo('#222', '#222', '#000', '#fff', '#808080', '#1a1a1a'),
     None, '--background-color', '窗口背景色（黑边区域颜色），格式 #RGB 或 #RRGGBB。'),
    ('window_title', '窗口标题', 'text',
     [], '', '--window-title', '自定义 scrcpy 窗口标题。留空=设备型号。'),
    ('window_pos', '窗口位置（x,y）', 'text',
     [], '', '__window_pos__', '初始窗口位置，格式 x,y。留空=自动。'),
    ('window_size', '窗口大小（宽x高）', 'text',
     [], '', '__window_size__', '初始窗口大小，格式 宽x高。留空=自动。'),
]

# ── 控制 ──
CONTROL_PARAMS = [
    ('no_control', '只读模式（禁用控制）', 'check',
     [], False, '--no-control', '勾选后只镜像不控制（无法用鼠标键盘操作设备）。'),
    ('keyboard', '键盘模式', 'combo',
     _combo('sdk', 'sdk', 'disabled', 'uhid', 'aoa'),
     None, '--keyboard', 'sdk=Android API 注入，uhid=模拟物理 HID 键盘，aoa=AOAv2 协议（仅 USB），disabled=禁用。'),
    ('mouse', '鼠标模式', 'combo',
     _combo('sdk', 'sdk', 'disabled', 'uhid', 'aoa'),
     None, '--mouse', 'sdk=Android API 注入，uhid=模拟物理 HID 鼠标，aoa=AOAv2（仅 USB），disabled=禁用。'),
    ('gamepad', '手柄模式', 'combo',
     _combo('disabled', 'disabled', 'uhid', 'aoa'),
     None, '--gamepad', 'disabled=不转发手柄，uhid=模拟 HID 手柄，aoa=AOAv2（仅 USB）。'),
    ('shortcut_mod', '快捷键修饰键', 'combo',
     _combo('lalt,lsuper', 'lalt,lsuper', 'lctrl', 'rctrl', 'lalt', 'ralt', 'lsuper', 'rsuper'),
     None, '--shortcut-mod', 'scrcpy 快捷键的修饰键。默认左 Alt 或左 Super。多个用逗号分隔（如 lctrl,lalt）。'),
    ('no_clipboard_autosync', '禁用剪贴板自动同步', 'check',
     [], False, '--no-clipboard-autosync', '勾选后不同步电脑/设备剪贴板（官方默认自动同步）。'),
    ('show_touches', '显示触摸点', 'check',
     [], False, '--show-touches', '启动时开启设备「显示触摸」（退出时恢复）。仅显示物理触摸，不显示 scrcpy 鼠标点击。'),
    ('stay_awake', '保持设备亮屏', 'check',
     [], False, '--stay-awake', '投屏时保持设备屏幕常亮（需插电）。'),
]

# ── 设备连接 ──
DEVICE_PARAMS = [
    ('turn_screen_off', '投屏时关闭设备屏幕', 'check',
     [], False, '--turn-screen-off', '启动后立即关闭设备屏幕（仍可镜像）。部分设备生效。'),
    ('power_off_on_close', '关闭时关闭设备屏幕', 'check',
     [], False, '--power-off-on-close', '退出 scrcpy 时关闭设备屏幕。'),
    ('disable_screensaver', '禁用电脑屏保', 'check',
     [], False, '--disable-screensaver', 'scrcpy 运行期间禁用电脑屏幕保护。'),
    ('force_adb_forward', '强制 forward 连接', 'check',
     [], False, '--force-adb-forward', '不尝试 adb reverse，强制用 forward 建立隧道。reverse 失败的设备/网络可勾选。'),
    ('kill_adb_on_close', '关闭时杀 adb 进程', 'check',
     [], False, '--kill-adb-on-close', 'scrcpy 退出时终止 adb server。注意：自研模式依赖 adb server，不建议勾选。'),
    ('print_fps', '显示 FPS 计数', 'check',
     [], False, '--print-fps', '在控制台输出实时帧率（也可在 scrcpy 窗口用 MOD+i 切换）。'),
    ('verbosity', '日志级别', 'combo',
     _combo('info', 'verbose', 'debug', 'info', 'warn', 'error'),
     None, '--verbosity', 'scrcpy 日志输出级别。info 默认，debug/verbose 更详细，warn/error 更安静。'),
]

# 所有参数（按页签分组，用于 UI 构建）
TAB_GROUPS = [
    ('视频', VIDEO_PARAMS),
    ('音频', AUDIO_PARAMS),
    ('窗口', WINDOW_PARAMS),
    ('控制', CONTROL_PARAMS),
    ('设备连接', DEVICE_PARAMS),
]

ALL_PARAMS = VIDEO_PARAMS + AUDIO_PARAMS + WINDOW_PARAMS + CONTROL_PARAMS + DEVICE_PARAMS

# 默认值：大部分 = 官方默认（combo=None, check=False, text=''）；
# 低延迟优化项：max_size='1280', max_fps='60', no_audio=True
DEFAULTS = {p[0]: p[4] for p in ALL_PARAMS}
DEFAULTS['extra_args'] = ''  # 自定义额外参数


# ────────────────────────────────────────────────────────────
# 读取 / 保存
# ────────────────────────────────────────────────────────────

def load_scrcpy_settings():
    """从 QSettings 读取投屏设置，缺省时回退 DEFAULTS（官方默认）。"""
    s = QSettings(ORG, APP)
    out = {}
    for key, default in DEFAULTS.items():
        v = s.value(f'scrcpy/{key}', default)
        # combo 的 None 在 QSettings 里可能存成空字符串/无效值，统一回退 None
        if default is None and (v is None or v == '' or str(v).lower() == 'none'):
            out[key] = None
        elif isinstance(default, bool):
            if isinstance(v, str):
                out[key] = v.lower() in ('true', '1', 'yes')
            else:
                out[key] = bool(v)
        else:
            out[key] = v if v is not None else default
    return out


def build_scrcpy_args(settings):
    """根据设置字典组装 scrcpy 命令行参数列表。

    规则：
      - combo 值为 None / text 为空 → 不传该参数（用官方默认）
      - check 为 True → 传 --flag（无值）
      - combo/text 有值 → 传 --flag value
      - window_pos / window_size / extra_args 为特殊参数，单独解析
    """
    args = []
    s = settings or {}
    for key, _label, ptype, _opts, _default, flag, _help in ALL_PARAMS:
        val = s.get(key)
        if ptype == 'check':
            if val:
                args.append(flag)
        elif ptype == 'combo':
            if val is not None and str(val).strip() != '':
                args += [flag, str(val)]
        elif ptype == 'text':
            if flag.startswith('__'):
                continue  # 特殊参数在下面处理
            if val is not None and str(val).strip() != '':
                args += [flag, str(val).strip()]

    # 特殊参数：窗口位置 x,y
    wp = s.get('window_pos', '')
    if wp and ',' in str(wp):
        parts = [x.strip() for x in str(wp).split(',', 1)]
        if parts[0]:
            args += ['--window-x', parts[0]]
        if len(parts) > 1 and parts[1]:
            args += ['--window-y', parts[1]]

    # 特殊参数：窗口大小 宽x高
    ws = s.get('window_size', '')
    if ws and 'x' in str(ws).lower():
        parts = [x.strip() for x in str(ws).lower().split('x', 1)]
        if parts[0]:
            args += ['--window-width', parts[0]]
        if len(parts) > 1 and parts[1]:
            args += ['--window-height', parts[1]]

    # 自定义额外参数：空格分隔，直接追加
    extra = s.get('extra_args', '')
    if extra and str(extra).strip():
        try:
            args += shlex.split(str(extra).strip())
        except ValueError:
            # 引号不配对时退化为简单空格分割
            args += str(extra).strip().split()
    return args


# ────────────────────────────────────────────────────────────
# 对话框 UI
# ────────────────────────────────────────────────────────────

class Scrcpy设置对话框(QDialog):
    """scrcpy 投屏参数设置对话框（官方参数映射，5 页签 + 自定义额外参数）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('scrcpy 投屏设置（官方参数）')
        self.setMinimumWidth(520)
        self.setMinimumHeight(560)
        self._theme_id = get_current_theme_id(self)
        self.setStyleSheet(get_stylesheet(self._theme_id) + "QDialog { background-color: transparent; }")
        self.card, _ = _create_popup_card(self, self._theme_id)
        self._widgets = {}  # key -> 控件
        self._build_ui()
        self._load()

    def apply_theme(self, theme_id):
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self._theme_id = theme_id
        self.setStyleSheet(get_stylesheet(theme_id) + "QDialog { background-color: transparent; }")
        self.card.setStyleSheet(highlight_card_style(theme_id))
        add_green_glow(self.card, accent=QColor(THEMES[theme_id]['accent']))
        self.update()

    def _build_ui(self):
        lay = QVBoxLayout(self.card)
        lay.setSpacing(8)

        # 页签
        tabs = QTabWidget()
        for tab_name, params in TAB_GROUPS:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            container = QWidget()
            form = QFormLayout(container)
            form.setSpacing(8)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            for p in params:
                self._add_form_row(form, p)
            scroll.setWidget(container)
            tabs.addTab(scroll, tab_name)
        lay.addWidget(tabs, 1)

        # 自定义额外参数
        extra_row = QHBoxLayout()
        extra_lbl = QLabel('自定义额外参数')
        extra_lbl.setMinimumWidth(140)
        self.edit_extra = QLineEdit()
        self.edit_extra.setPlaceholderText('直接追加 scrcpy 参数，空格分隔，如 --time-limit 300 --record /sdcard/rec.mp4')
        extra_row.addWidget(extra_lbl)
        extra_row.addWidget(self.edit_extra, 1)
        lay.addLayout(extra_row)

        # 提示
        self._hint = QLabel(
            '■ 默认采用低延迟配置：最大分辨率1280、帧率上限60、禁用音频转发。\n'
            '■ 下拉选「默认 (官方 xxx)」即不传该参数，让 scrcpy 用官方默认。\n'
            '■ 仅在需要覆盖默认时才选择具体值；需要音频时取消勾选「禁用音频转发」。\n'
            '■ 「自定义额外参数」可填任何本对话框未列出的 scrcpy 参数。\n'
            '■ 受 DRM/HDCP 保护的内容（Netflix/银行/支付）会黑屏，属硬件限制。'
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(f"color: {THEMES[self._theme_id]['text_disabled']}; font-size: 9pt;")
        lay.addWidget(self._hint)

        # 按钮：恢复默认 + OK + Cancel
        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton('恢复默认')
        self.btn_reset.clicked.connect(self._reset_defaults)
        btn_row.addWidget(self.btn_reset)
        btn_row.addStretch(1)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btn_row.addWidget(btns)
        lay.addLayout(btn_row)

    def _add_form_row(self, form, param):
        key, label, ptype, options, _default, _flag, help_text = param
        if ptype == 'combo':
            cb = QComboBox()
            cb.setEditable(True)
            cb.lineEdit().setReadOnly(True)
            cb.lineEdit().setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            cb.setMinimumWidth(200)
            cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            for opt_label, opt_val in options:
                cb.addItem(opt_label, opt_val)
            cb.setToolTip(help_text)
            self._widgets[key] = cb
            form.addRow(label, cb)
        elif ptype == 'check':
            chk = QCheckBox(label)
            chk.setToolTip(help_text)
            self._widgets[key] = chk
            form.addRow('', chk)  # 复选框自带文字，label 留空
        elif ptype == 'text':
            edit = QLineEdit()
            edit.setPlaceholderText('留空=官方默认')
            edit.setToolTip(help_text)
            self._widgets[key] = edit
            form.addRow(label, edit)

    def _load(self):
        s = load_scrcpy_settings()
        for key, widget in self._widgets.items():
            val = s.get(key)
            if isinstance(widget, QComboBox):
                self._select_combo(widget, val)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(val) if val else '')
        self.edit_extra.setText(str(s.get('extra_args', '')) or '')

    @staticmethod
    def _select_combo(cb, value):
        """按 data 匹配下拉项；匹配不到且非空则设为可编辑文本（自定义值）。"""
        count = cb.count()
        for i in range(count):
            item = cb.itemData(i)
            if item is None and value is None:
                cb.setCurrentIndex(i)
                return
            if item == value:
                cb.setCurrentIndex(i)
                return
        # 未匹配：如果是具体值，用可编辑模式显示
        if value is not None and str(value).strip():
            cb.setCurrentText(str(value))
        else:
            cb.setCurrentIndex(0)

    def _reset_defaults(self):
        """恢复所有设置为官方默认（清空 QSettings 对应键）。"""
        s = QSettings(ORG, APP)
        for key in DEFAULTS:
            s.remove(f'scrcpy/{key}')
        s.sync()
        self._load()

    def accept(self):
        s = QSettings(ORG, APP)
        for key, widget in self._widgets.items():
            if isinstance(widget, QComboBox):
                data = widget.currentData()
                # 可编辑下拉框：如果用户输入了自定义文本且不在选项中，currentData 可能为 None
                if data is None and widget.currentText() and not widget.currentText().startswith('默认'):
                    s.setValue(f'scrcpy/{key}', widget.currentText())
                else:
                    s.setValue(f'scrcpy/{key}', data if data is not None else '')
            elif isinstance(widget, QCheckBox):
                s.setValue(f'scrcpy/{key}', widget.isChecked())
            elif isinstance(widget, QLineEdit):
                s.setValue(f'scrcpy/{key}', widget.text().strip())
        s.setValue('scrcpy/extra_args', self.edit_extra.text().strip())
        s.sync()
        super().accept()
