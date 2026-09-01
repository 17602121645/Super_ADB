# -*- coding: utf-8 -*-
"""
桌面宠物小猫 —— DeskCatWidget
============================
在 Super_ADB 主窗口里养一只会玩耍、会躲避鼠标的小猫。

特性
----
* 作为父窗口的普通子控件浮动，透明背景，鼠标点击击穿
* 状态机：待机 / 走动 / 奔跑 / 玩耍 / 睡觉
* 鼠标进入警戒范围自动逃跑
* 气泡文字互动

图片要求
--------
推荐使用带透明背景的 PNG，例如用户放在桌面的那只橘白小猫。
"""

import math
import random

from ui.ui_styles import FONT_FAMILY
import os

from PySide6.QtCore import (
    Qt, QTimer, QPoint, QRect, QSize
)
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QCursor, QTransform, QFont
)
from PySide6.QtWidgets import (
    QWidget, QLabel
)

from ui.ui_styles import THEMES, get_current_theme_id

# 注册编译后的资源文件，确保 :/desk_cat.png 可用（打包后不依赖外部图片路径）
try:
    import png_rc  # noqa: F401
except ImportError:
    pass


class DeskCatWidget(QWidget):
    """桌面宠物小猫控件。"""

    # 状态常量
    STATE_IDLE = 'idle'
    STATE_WALK = 'walk'
    STATE_RUN = 'run'
    STATE_PLAY = 'play'
    STATE_SLEEP = 'sleep'

    # 小猫相对于主窗口的默认停靠位置（右下角留白）
    DEFAULT_OFFSET = QPoint(40, 60)

    def __init__(self, parent=None, image_path=None, size=85):
        super().__init__(parent)
        self._parent = parent
        self._cat_size = QSize(size, int(size * 1.1))  # 稍微拉高，显得可爱
        self._placed = False  # 是否已完成首次随机落位
        self._state = self.STATE_IDLE
        self._facing_right = True
        self._frame = 0
        self._paused = False
        self._hidden_by_user = False

        # 移动相关
        self._pos = QPoint(0, 0)
        self._velocity = QPoint(0, 0)
        self._target = None
        self._speed = 0
        self._bounds = QRect(0, 0, 0, 0)

        # 动画相位（呼吸、摇摆、弹跳）
        self._breath_phase = 0.0
        self._bob_phase = 0.0
        self._play_phase = 0.0

        # 鼠标躲避参数
        self._flee_radius = 75  # 进入此半径开始逃跑（调小，避免鼠标稍靠近就逃）
        self._flee_cooldown = 0  # 逃跑冷却帧数

        # 鼠标静止检测：鼠标不动时小猫不应持续逃跑，否则会一直被逼到边缘
        self._last_mouse_pos = QPoint()
        self._mouse_still_frames = 0
        self._mouse_still_threshold = 5  # 约 200ms 鼠标没动才算静止

        # 配置窗口属性
        # 作为父窗口的普通子控件浮动，不使用独立 Tool 窗口，避免与主界面控件
        # 产生事件/重绘冲突（之前独立窗口导致卡死）。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # 鼠标点击击穿：小猫只是装饰层，不拦截/消耗鼠标事件，
        # 点击会落到下方的控件上（躲避鼠标仍靠全局 QCursor 检测，不受影响）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedSize(self._cat_size)

        # 加载图片
        self._pixmap = self._load_pixmap(image_path)
        self._scaled_pixmap = self._pixmap.scaled(
            self._cat_size.width(), self._cat_size.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        # 装饰标签（展示气泡文字）
        self._bubble = QLabel(self)
        self._bubble.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._theme_id = get_current_theme_id(self)
        self._apply_bubble_style()
        self._bubble.hide()

        # 行为决策定时器
        self._think_timer = QTimer(self)
        self._think_timer.timeout.connect(self._think)
        self._think_timer.start(1800)

        # 动画帧定时器（约 25 FPS）
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(40)

        # 泡泡文字隐藏定时器
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._bubble.hide)

    # ------------------------------------------------------------------
    # 主题切换
    # ------------------------------------------------------------------
    def _apply_bubble_style(self):
        """根据当前主题设置气泡标签样式。"""
        t = THEMES.get(self._theme_id, THEMES.get('dark_cyan', {}))
        bg = t.get('bg_window', '#1e1e1e')
        text = t.get('text_primary', '#ffffff')
        # 背景用主题色 + 半透明，文字用主题文字色
        r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
        self._bubble.setStyleSheet(
            f'background:rgba({r},{g},{b},200); color:{text};'
            f' border-radius:8px; padding:4px 8px; font-size:11px;'
        )

    def apply_theme(self, theme_id):
        """运行时切换主题：更新气泡标签样式。"""
        if theme_id not in THEMES:
            theme_id = 'dark_cyan'
        self._theme_id = theme_id
        self._apply_bubble_style()
        self.update()

    # ------------------------------------------------------------------
    # 图片加载
    # ------------------------------------------------------------------
    @staticmethod
    def _load_pixmap(image_path):
        """加载小猫图片，若失败则返回一只纯色占位猫。

        优先使用 qrc 资源路径（以 ':' 开头，打包后随程序发布），
        也兼容普通文件系统路径。
        """
        if image_path:
            # 资源路径（:/xxx）无法用 os.path.isfile 判断，直接交给 QPixmap
            is_resource = image_path.startswith(':')
            if is_resource or os.path.isfile(image_path):
                pm = QPixmap(image_path)
                if not pm.isNull():
                    return pm

        # 默认占位：画一只圆滚滚的橘猫
        pm = QPixmap(120, 132)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 身体
        p.setBrush(QColor('#f5b971'))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(20, 40, 80, 80)
        # 头
        p.drawEllipse(30, 10, 60, 60)
        # 耳朵
        p.drawEllipse(28, 8, 18, 28)
        p.drawEllipse(74, 8, 18, 28)
        # 眼睛
        p.setBrush(QColor('#333'))
        p.drawEllipse(44, 32, 8, 10)
        p.drawEllipse(68, 32, 8, 10)
        # 鼻子
        p.setBrush(QColor('#ffb7b2'))
        p.drawEllipse(56, 42, 8, 6)
        # 肚皮白毛
        p.setBrush(QColor('#fff8f0'))
        p.drawEllipse(40, 80, 40, 50)
        p.end()
        return pm

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------
    def set_bounds(self, rect: QRect):
        """设置小猫的活动边界（父窗口坐标）。"""
        self._bounds = rect
        if not self._bounds.isEmpty() and not self._placed:
            # 首次设定边界时随机落位，避免小猫停在角落
            self._placed = True
            min_x = self._bounds.left() + 4
            max_x = max(min_x, self._bounds.right() - self.width() - 4)
            min_y = self._bounds.top() + 4
            max_y = max(min_y, self._bounds.bottom() - self.height() - 4)
            self.set_position(QPoint(
                random.randint(min_x, max_x),
                random.randint(min_y, max_y),
            ))
        else:
            self._clamp_position()

    def set_position(self, pos: QPoint):
        """立即把小猫放到指定位置（父窗口坐标）。"""
        self._pos = QPoint(pos)
        self._clamp_position()
        self._move_to_position()

    def say(self, text: str, ms: int = 2000):
        """头顶冒出一句话。"""
        self._bubble.setText(text)
        self._bubble.adjustSize()
        self._bubble.move(
            (self.width() - self._bubble.width()) // 2,
            -self._bubble.height() - 4
        )
        self._bubble.show()
        self._bubble_timer.start(ms)

    def pause(self):
        """暂停小猫的自动行为。"""
        self._paused = True
        self._velocity = QPoint(0, 0)
        self._target = None
        self._state = self.STATE_IDLE

    def resume(self):
        """恢复小猫的自动行为。"""
        self._paused = False

    def hide_cat(self):
        """用户主动隐藏小猫。"""
        self._hidden_by_user = True
        self.hide()

    def show_cat(self):
        """重新显示被隐藏的小猫。"""
        self._hidden_by_user = False
        self.show()
        self.raise_()

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def paintEvent(self, event):
        """绘制小猫，根据状态做动态变换。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 基础变换：呼吸缩放 + 走路摇摆 + 逃跑倾斜 + 玩耍旋转
        transform = QTransform()
        cx = self.width() / 2
        cy = self.height() * 0.85  # 以底部中心为锚点
        transform.translate(cx, cy)

        # 朝向
        sx = -1.0 if not self._facing_right else 1.0

        # 呼吸
        breath = 1.0 + math.sin(self._breath_phase) * 0.03
        transform.scale(sx * breath, breath)

        # 走路摇摆
        if self._state == self.STATE_WALK:
            rot = math.sin(self._bob_phase) * 3.0
            transform.rotate(rot)

        # 逃跑时身体前倾
        if self._state == self.STATE_RUN:
            lean = 12.0 if self._facing_right else -12.0
            transform.rotate(lean)

        # 玩耍时原地转圈 + 弹跳
        if self._state == self.STATE_PLAY:
            bounce = abs(math.sin(self._play_phase * 3)) * 10
            transform.translate(0, -bounce)
            transform.rotate(math.sin(self._play_phase) * 10)

        # 睡觉时会缩成一团（轻微缩小并下移）
        if self._state == self.STATE_SLEEP:
            transform.scale(1.05, 0.85)
            transform.translate(0, 8)

        transform.translate(-cx, -cy)
        painter.setTransform(transform)

        # 绘制阴影
        shadow_opacity = 0.25
        if self._state == self.STATE_RUN:
            shadow_opacity = 0.15
        painter.setBrush(QColor(0, 0, 0, int(255 * shadow_opacity)))
        painter.setPen(Qt.PenStyle.NoPen)
        shadow_w = self.width() * 0.55
        shadow_h = self.height() * 0.12
        painter.drawEllipse(
            int((self.width() - shadow_w) / 2),
            int(self.height() - shadow_h - 4),
            int(shadow_w),
            int(shadow_h)
        )

        # 绘制小猫
        pm = self._scaled_pixmap
        x = (self.width() - pm.width()) // 2
        y = self.height() - pm.height() - 6  # 底部对齐，留一点空间给阴影
        painter.drawPixmap(x, y, pm)

        # 睡觉画 "Zzz"
        if self._state == self.STATE_SLEEP:
            painter.resetTransform()
            painter.setPen(QColor('#fff'))
            painter.setFont(QFont(FONT_FAMILY, 10))
            painter.drawText(self.width() - 28, 24, 'Z')
            painter.setFont(QFont(FONT_FAMILY, 8))
            painter.drawText(self.width() - 18, 16, 'z')
            painter.setFont(QFont(FONT_FAMILY, 6))
            painter.drawText(self.width() - 10, 10, 'z')

        painter.end()

    # ------------------------------------------------------------------
    # 行为决策
    # ------------------------------------------------------------------
    def _mouse_is_moving(self) -> bool:
        """检测鼠标是否在移动（静止时不应持续驱赶小猫）。"""
        global_mouse = QCursor.pos()
        if global_mouse == self._last_mouse_pos:
            self._mouse_still_frames += 1
        else:
            self._mouse_still_frames = 0
            self._last_mouse_pos = QPoint(global_mouse)
        return self._mouse_still_frames < self._mouse_still_threshold

    def _think(self):
        """每隔一段时间做一次状态决策。"""
        if self._paused or not self.isVisible():
            return

        mouse_pos = self._mouse_in_parent()
        cat_center = self._center_in_parent()
        dist = self._distance(cat_center, mouse_pos)
        mouse_moving = self._mouse_is_moving()

        # 逃跑优先级最高，但鼠标静止时不逃（避免小猫被逼到墙角后一直僵住）
        if (dist < self._flee_radius and self._flee_cooldown <= 0
                and mouse_moving):
            self._flee_from(mouse_pos)
            self.say('呀！别过来~', 1000)
            return

        # 逃跑冷却递减
        if self._flee_cooldown > 0:
            self._flee_cooldown -= 1

        # 已经在移动就继续，不做新的随机决策
        if self._state in (self.STATE_WALK, self.STATE_RUN) and self._target:
            return

        # 随机选择下一个动作
        r = random.random()
        if r < 0.45:
            self._start_walk()
        elif r < 0.70:
            self._state = self.STATE_IDLE
            self._velocity = QPoint(0, 0)
            self._target = None
        elif r < 0.85:
            self._start_play()
        else:
            self._state = self.STATE_SLEEP
            self._velocity = QPoint(0, 0)
            self._target = None

    def _tick(self):
        """动画帧更新。"""
        if not self.isVisible():
            return

        self._frame += 1
        self._breath_phase += 0.08
        self._bob_phase += 0.25
        self._play_phase += 0.15

        # 持续检测鼠标，但鼠标静止/冷却中不逃跑
        mouse_pos = self._mouse_in_parent()
        cat_center = self._center_in_parent()
        dist = self._distance(cat_center, mouse_pos)
        mouse_moving = self._mouse_is_moving()
        if (dist < self._flee_radius and self._flee_cooldown <= 0
                and not self._paused and mouse_moving):
            self._flee_from(mouse_pos)

        if self._state in (self.STATE_WALK, self.STATE_RUN) and self._target:
            self._move_toward_target()

        self.update()

    # ------------------------------------------------------------------
    # 移动与动画
    # ------------------------------------------------------------------
    def _start_walk(self):
        """随机选一个目标点走过去，优先选择离鼠标较远的位置，减少被追边。"""
        if self._bounds.isEmpty():
            return
        margin = 8  # 小窗口时留更小边距，避免选不出点
        mouse = self._mouse_in_parent()
        candidates = []
        for _ in range(3):
            min_x = self._bounds.left() + margin
            max_x = max(min_x, self._bounds.right() - self.width() - margin)
            min_y = self._bounds.top() + margin
            max_y = max(min_y, self._bounds.bottom() - self.height() - margin)
            x = random.randint(min_x, max_x)
            y = random.randint(min_y, max_y)
            candidates.append(QPoint(x, y))
        # 选离鼠标最远的候选点，让小猫倾向于远离鼠标活动
        self._target = max(candidates, key=lambda p: self._distance(p, mouse))
        self._speed = random.randint(2, 4)
        self._state = self.STATE_WALK

    def _flee_from(self, mouse_pos: QPoint):
        """朝鼠标反方向逃跑。"""
        cat_center = self._center_in_parent()
        dx = cat_center.x() - mouse_pos.x()
        dy = cat_center.y() - mouse_pos.y()
        length = math.hypot(dx, dy) or 1.0

        # 逃跑距离：至少 250px
        flee_dist = max(250, int(self._flee_radius * 2.2))
        target_x = int(cat_center.x() + (dx / length) * flee_dist - self.width() / 2)
        target_y = int(cat_center.y() + (dy / length) * flee_dist - self.height() / 2)

        target = QPoint(target_x, target_y)
        self._clamp_target_to_bounds(target)  # 目标点也要落在边界内，避免卡墙
        self._target = target
        self._speed = random.randint(7, 11)
        self._state = self.STATE_RUN
        # 延长冷却：逃跑后约 1 秒内不再触发逃跑，让小猫有时间散步/玩耍
        self._flee_cooldown = 25

    def _start_play(self):
        """进入玩耍状态：原地转圈、扑腾。"""
        self._state = self.STATE_PLAY
        self._velocity = QPoint(0, 0)
        self._target = None
        self._play_phase = 0.0

    def _clamp_target_to_bounds(self, target: QPoint):
        """把逃跑目标点限制在活动边界内，避免目标落在边界外导致卡墙。"""
        if self._bounds.isEmpty():
            return
        target.setX(max(
            self._bounds.left(),
            min(target.x(), self._bounds.right() - self.width())
        ))
        target.setY(max(
            self._bounds.top(),
            min(target.y(), self._bounds.bottom() - self.height())
        ))

    def _move_toward_target(self):
        """朝当前目标点移动一步。"""
        if not self._target:
            return
        prev = QPoint(self._pos)
        dx = self._target.x() - self._pos.x()
        dy = self._target.y() - self._pos.y()
        dist = math.hypot(dx, dy)

        if dist < self._speed:
            self._pos = QPoint(self._target)
            self._target = None
            self._state = self.STATE_IDLE
            self._velocity = QPoint(0, 0)
        else:
            step_x = int(round(dx / dist * self._speed))
            step_y = int(round(dy / dist * self._speed))
            self._velocity = QPoint(step_x, step_y)
            self._pos += self._velocity

        # 根据速度方向决定朝向
        if abs(self._velocity.x()) > 0:
            self._facing_right = self._velocity.x() > 0

        self._clamp_position()
        # 撞墙检测：位置被 clamp 锁死且仍未到达目标点，说明目标在边界外，
        # 放弃该目标回到待机，避免小猫永远卡在边缘抖动。
        if self._target is not None and self._pos == prev:
            self._target = None
            self._state = self.STATE_IDLE
            self._velocity = QPoint(0, 0)
        self._move_to_position()

    def _clamp_position(self):
        """把位置限制在活动边界内。"""
        if self._bounds.isEmpty():
            return
        self._pos.setX(max(
            self._bounds.left(),
            min(self._pos.x(), self._bounds.right() - self.width())
        ))
        self._pos.setY(max(
            self._bounds.top(),
            min(self._pos.y(), self._bounds.bottom() - self.height())
        ))

    def _move_to_position(self):
        """把自身控件移动到 _pos（父窗口局部坐标），并通知父窗口重绘消除残影。"""
        old_rect = self.geometry()
        self.move(self._pos)
        new_rect = self.geometry()
        # 通知父窗口重绘旧位置和新位置，消除半透明窗口下的残影
        if self._parent is not None:
            self._parent.update(old_rect)
            self._parent.update(new_rect)

    # ------------------------------------------------------------------
    # 坐标工具
    # ------------------------------------------------------------------
    def _mouse_in_parent(self) -> QPoint:
        """鼠标在父窗口坐标系中的位置。"""
        global_mouse = QCursor.pos()
        if self._parent is None:
            return self.mapFromGlobal(global_mouse)
        return self._parent.mapFromGlobal(global_mouse)

    def _center_in_parent(self) -> QPoint:
        """小猫中心在父窗口坐标系中的位置。"""
        return QPoint(
            self._pos.x() + self.width() // 2,
            self._pos.y() + self.height() // 2
        )

    @staticmethod
    def _distance(a: QPoint, b: QPoint) -> float:
        return math.hypot(a.x() - b.x(), a.y() - b.y())

# ----------------------------------------------------------------------
# 便捷工厂函数
# ----------------------------------------------------------------------
def create_desk_cat(parent, image_path=None, size=85):
    """为父窗口创建一只桌面小猫并返回控件。

    参数
    ----
    parent : QWidget
        主窗口，小猫会跟随它移动。
    image_path : str, optional
        小猫 PNG 图片路径。缺省使用内置占位猫。
    size : int, optional
        小猫显示尺寸（像素），默认 85。
    """
    cat = DeskCatWidget(parent=parent, image_path=image_path, size=size)
    cat.show()
    cat.raise_()
    return cat
