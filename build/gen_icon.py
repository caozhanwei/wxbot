# -*- coding: utf-8 -*-
"""生成应用图标 build/app.ico：微信绿渐变圆角方底 + 白色双聊天气泡 + AI 四角火花。
运行：python build/gen_icon.py（需 Pillow，仅开发机用，产物已入库 CI 无需重生成）"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

S = 2048  # 超采样画布，缩到各尺寸保证小图标边缘干净
GREEN_TL = (46, 214, 109)   # 左上亮绿
GREEN_BR = (5, 158, 79)     # 右下深绿（微信 #07c160 系）
INK = (6, 173, 86)          # 气泡内图形绿


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def diag_gradient(size, c1, c2):
    """对角线渐变：小图放大即可，避免逐像素循环。"""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            px[x, y] = lerp(c1, c2, (x + y) / 126)
    return small.resize((size, size), Image.BILINEAR)


def sparkle_points(cx, cy, r, pinch=0.32):
    """四角火花（AI sparkle）：东西南北四尖 + 45° 收腰多边形。"""
    pts = []
    for i in range(8):
        ang = math.pi / 4 * i
        rad = r if i % 2 == 0 else r * pinch
        pts.append((cx + rad * math.sin(ang), cy - rad * math.cos(ang)))
    return pts


def build_base(s):
    """s×s 画布：渐变圆角方底。"""
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = diag_gradient(s, GREEN_TL, GREEN_BR).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=int(s * 0.225), fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def _small_bubble_shapes(d, k):
    d.rounded_rectangle([int(1120 * k), int(1080 * k), int(1900 * k), int(1660 * k)],
                        radius=int(240 * k), fill=(255, 255, 255, 255))
    d.polygon([(int(1680 * k), int(1600 * k)), (int(1850 * k), int(1900 * k)),
               (int(1900 * k), int(1530 * k))], fill=(255, 255, 255, 255))


def draw_bubbles(img):
    d = ImageDraw.Draw(img)
    s = img.size[0]
    k = s / 2048.0

    # 大气泡（左上）：圆角矩形 + 左下小尾巴
    d.rounded_rectangle([int(150 * k), int(160 * k), int(1330 * k), int(1220 * k)],
                        radius=int(270 * k), fill=(255, 255, 255, 255))
    d.polygon([(int(360 * k), int(1180 * k)), (int(210 * k), int(1520 * k)),
               (int(760 * k), int(1210 * k))], fill=(255, 255, 255, 255))

    # 火花（大气泡内，圆角矩形几何中心）
    d.polygon(sparkle_points(int(740 * k), int(680 * k), int(390 * k)), fill=INK + (255,))

    # 小气泡投影：同形状偏移模糊，避免与大气泡黏连
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _small_bubble_shapes(ImageDraw.Draw(sh), k)
    solid = Image.new("RGBA", img.size, (3, 92, 48, 255))
    sh = Image.composite(solid, sh, sh.getchannel("A"))
    sh.putalpha(sh.getchannel("A").point(lambda a: int(a * 0.35)))
    sh = sh.filter(ImageFilter.GaussianBlur(int(36 * k)))
    img.alpha_composite(sh, (int(20 * k), int(28 * k)))

    # 小气泡（右下，压在大气泡一角）
    _small_bubble_shapes(d, k)

    # 小气泡内三个点（正在输入的感觉）
    for cx in (1355, 1515, 1675):
        d.ellipse([int((cx - 62) * k), int(1300 * k), int((cx + 62) * k), int(1424 * k)],
                  fill=INK + (255,))


def main():
    base = build_base(S)
    draw_bubbles(base)

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48),
             (32, 32), (24, 24), (16, 16)]
    frames = [base.resize(sz, Image.LANCZOS) for sz in sizes]
    # Pillow ico 保存：以最大帧为底、sizes 参数生成全部层级
    frames[0].save(os.path.join(HERE, "app.ico"), format="ICO",
                   append_images=frames[1:])
    base.resize((512, 512), Image.LANCZOS).save(
        os.path.join(HERE, "appicon_preview.png"))
    print("OK build/app.ico + appicon_preview.png")


if __name__ == "__main__":
    main()
