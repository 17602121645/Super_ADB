#!/usr/bin/env python3
"""为 Super_ADB 生成现代化应用图标。

设计理念:
  - 深色渐变圆角方形 (现代应用图标风格)
  - 终端调试主题 >_ (主视觉元素, 青绿色带光晕)
  - Android 触角 (双天线, 暗示 Android 调试身份)
  - "ADB" 字样 (品牌标识)
  - 整体配色: #1de9b6 (项目主题色) + 深空蓝黑 (#1a1a2e → #0d1117)
"""
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

SIZE = 512
OUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '', 'Super_ADB.png'
)

# 颜色
BG_TOP = (26, 26, 46)        # #1a1a2e
BG_BOTTOM = (13, 17, 23)     # #0d1117
TEAL = (29, 233, 182)        # #1de9b6
TEAL_LIGHT = (80, 255, 210)  # 更亮的青绿
GRAY = (139, 148, 158)       # #8b949e
DARK_PANEL = (10, 14, 20)    # #0a0e14


def find_font(candidates, size):
    """在系统字体目录中按顺序查找字体。"""
    bases = [
        r'C:\Windows\Fonts',
        '/usr/share/fonts/truetype',
        '/System/Library/Fonts',
        '/Library/Fonts',
    ]
    for name in candidates:
        for base in bases:
            path = os.path.join(base, name)
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    pass
    return ImageFont.load_default()


def make_gradient(size, top, bottom):
    """生成垂直渐变图 (逐行绘制, 比逐像素快 512 倍)。"""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        d.line([(0, y), (size, y)], fill=(r, g, b, 255))
    return img


def rounded_mask(size, radius):
    """生成圆角遮罩 (L 模式)。"""
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    return mask


def glow_layer(size, text, font, pos, color, radius=10):
    """生成文字光晕层 (半透明 → 模糊 → 合成用)。"""
    layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(pos, text, font=font, fill=color)
    return layer.filter(ImageFilter.GaussianBlur(radius=radius))


def main():
    size = SIZE
    radius = int(size * 0.22)   # 22% 圆角 (iOS/macOS 应用图标风格)
    cx = size // 2

    # ---- 1. 渐变背景 ----
    grad = make_gradient(size, BG_TOP, BG_BOTTOM)
    mask = rounded_mask(size, radius)
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    draw = ImageDraw.Draw(img)

    # ---- 2. 背景装饰: 微弱终端代码行 (暗示 terminal/debug 主题) ----
    deco_color = TEAL + (18,)   # 极低透明度
    for i, (y_frac, w_frac) in enumerate([
        (0.20, 0.30), (0.24, 0.18), (0.28, 0.40),
        (0.72, 0.35), (0.76, 0.22), (0.80, 0.45),
    ]):
        y = int(size * y_frac)
        x1 = int(size * (1 - w_frac) / 2)
        x2 = x1 + int(size * w_frac)
        draw.line([(x1, y), (x2, y)], fill=deco_color, width=2)

    # ---- 3. 内边框 (青绿描边, 低透明度) ----
    bmargin = int(size * 0.014)
    draw.rounded_rectangle(
        [bmargin, bmargin, size - bmargin, size - bmargin],
        radius=radius - bmargin,
        outline=(29, 233, 182, 38),
        width=2,
    )

    # ---- 4. Android 触角 (双天线, 略微外扩) ----
    ant_w = max(8, int(size * 0.018))   # 细一些, 像天线不像管子
    ant_base_y = int(size * 0.37)
    ant_tip_y = int(size * 0.17)
    ant_off = int(size * 0.055)        # 底部距中线
    tip_off = int(size * 0.022)        # 顶端外扩 (角度更柔和)

    # 左天线
    draw.line(
        [(cx - ant_off, ant_base_y), (cx - ant_off - tip_off, ant_tip_y)],
        fill=TEAL + (240,), width=ant_w, joint='curve'
    )
    # 右天线
    draw.line(
        [(cx + ant_off, ant_base_y), (cx + ant_off + tip_off, ant_tip_y)],
        fill=TEAL + (240,), width=ant_w, joint='curve'
    )
    # 天线顶端小球 (更明显)
    for tx in (cx - ant_off - tip_off, cx + ant_off + tip_off):
        r = ant_w + 4
        # 外圈光晕
        glow_ball = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        ImageDraw.Draw(glow_ball).ellipse(
            [tx - r*2, ant_tip_y - r*2, tx + r*2, ant_tip_y + r*2],
            fill=TEAL + (80,)
        )
        glow_ball = glow_ball.filter(ImageFilter.GaussianBlur(radius=4))
        img = Image.alpha_composite(img, glow_ball)
        draw = ImageDraw.Draw(img)
        # 主体球
        draw.ellipse(
            [tx - r, ant_tip_y - r, tx + r, ant_tip_y + r],
            fill=TEAL_LIGHT
        )

    # ---- 5. Bug Droid 头部弧线 (青绿弧, 暗示机器人) ----
    head_cy = int(size * 0.38)
    head_r = int(size * 0.16)
    draw.arc(
        [cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
        start=190, end=350,
        fill=TEAL + (90,), width=4
    )

    # ---- 6. 终端提示符 >_ (主视觉) ----
    mono_font = find_font(
        ['consolab.ttf', 'consola.ttf', 'courbd.ttf', 'cour.ttf',
         'lucon.ttf', 'DejaVuSansMono-Bold.ttf', 'Menlo-Bold.ttf'],
        int(size * 0.24)
    )
    prompt = ">_"
    bbox = draw.textbbox((0, 0), prompt, font=mono_font)
    tw = bbox[2] - bbox[0]
    tx = (size - tw) // 2 - bbox[0]
    ty = int(size * 0.50)

    # 光晕层 (模糊的青绿副本, 在主文字下方)
    glow = glow_layer(size, prompt, mono_font, (tx, ty),
                      TEAL + (150,), radius=14)
    img = Image.alpha_composite(img, glow)
    # 第二层更大半径的弱光晕
    glow2 = glow_layer(size, prompt, mono_font, (tx, ty),
                       TEAL + (60,), radius=28)
    img = Image.alpha_composite(img, glow2)
    draw = ImageDraw.Draw(img)

    # 主文字
    draw.text((tx, ty), prompt, font=mono_font, fill=TEAL_LIGHT)

    # ---- 7. "ADB" 文字 (底部品牌标识) ----
    sans_font = find_font(
        ['segoeuib.ttf', 'seguisb.ttf', 'arialbd.ttf', 'arial.ttf',
         'DejaVuSans-Bold.ttf', 'Helvetica-Bold.ttf'],
        int(size * 0.062)
    )
    adb = "ADB"
    bbox = draw.textbbox((0, 0), adb, font=sans_font)
    aw = bbox[2] - bbox[0]
    ax = (size - aw) // 2 - bbox[0]
    ay = int(size * 0.79)
    draw.text((ax, ay), adb, font=sans_font, fill=GRAY)

    # ---- 8. 保存 ----
    img.save(OUT_PATH, 'PNG')
    print(f'OK -> {OUT_PATH}')


if __name__ == '__main__':
    main()
