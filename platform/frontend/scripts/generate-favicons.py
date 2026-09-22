#!/usr/bin/env python3
"""从平台标记的几何参数生成 favicon 位图。

用法（在 platform/frontend 下执行）：

    python scripts/generate-favicons.py

为什么要脚本：标记几何写死在 `src/assets/logo.tsx` 与 `public/images/favicon*.svg` 里，
图形一改位图就得跟着重生成，靠手工画必然与 SVG 不一致；本机没有 SVG→PNG 工具
（无 sharp / resvg），所以用 Pillow 按同一份几何参数绘制。

几何（24×24 视口，与 logo.tsx 一致：描边 2、圆角端点）：
    外框 rounded_rectangle(x=3, y=4, w=18, h=16, r=3)
    内芯 rounded_rectangle(x=9, y=9, w=6, h=6, r=1.5)
    引线 (12,6)-(12,9)、(12,15)-(12,18)、(6,12)-(9,12)、(15,12)-(18,12)

引线的圆角端点是必需的：外框那端与外框描边带之间留有 1 单位的缝，
圆角半径正好把它补上（SVG 侧同理）。

抗锯齿：Pillow 自身不做抗锯齿，先按 SUPERSAMPLE 倍绘制再 LANCZOS 缩小。

输出（透明背景）：
    public/images/favicon.png        深色描边，浅色浏览器主题
    public/images/favicon_light.png  浅色描边，深色浏览器主题
"""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 48
VIEWBOX = 24
SUPERSAMPLE = 4
DARK = (15, 23, 42, 255)  # #0f172a
LIGHT = (248, 250, 252, 255)  # #f8fafc
OUT_DIR = Path(__file__).resolve().parent.parent / 'public' / 'images'

# 视口单位 → 位图像素：先放大 SUPERSAMPLE 倍绘制，最后 LANCZOS 缩回 SIZE
SCALE = SIZE * SUPERSAMPLE // VIEWBOX


def render_mark(color: tuple[int, int, int, int]) -> Image.Image:
    scale = SCALE
    # Pillow 的绘制接口只接受整数坐标，视口坐标乘完倍率后取整
    def u(value: float) -> int:
        return round(value * scale)

    # 线条宽度按同样的倍数放大，否则缩小回目标尺寸后只剩一半粗
    width = u(2)
    img = Image.new('RGBA', (SIZE * SUPERSAMPLE, SIZE * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def rounded_rect(x: float, y: float, w: float, h: float, r: float) -> None:
        # Pillow 的描边是从给定 bbox 往内画的，而 SVG 的描边以路径为中心向两侧各铺一半：
        # 这里先把 bbox 与圆角按半个描边宽度外扩，画出来的边界才与 SVG 一致
        half = 1
        draw.rounded_rectangle(
            (u(x - half), u(y - half), u(x + w + half), u(y + h + half)),
            radius=u(r + half),
            outline=color,
            width=width,
        )

    def capped_line(x1: float, y1: float, x2: float, y2: float) -> None:
        draw.line((u(x1), u(y1), u(x2), u(y2)), fill=color, width=width)
        # Pillow 的直线没有圆角端点，两端各补一个等径圆点（等价 stroke-linecap: round）
        r = width / 2
        for cx, cy in ((x1, y1), (x2, y2)):
            draw.ellipse(
                (u(cx) - r, u(cy) - r, u(cx) + r, u(cy) + r),
                fill=color,
            )

    rounded_rect(3, 4, 18, 16, 3)
    rounded_rect(9, 9, 6, 6, 1.5)
    capped_line(12, 6, 12, 9)
    capped_line(12, 15, 12, 18)
    capped_line(6, 12, 9, 12)
    capped_line(15, 12, 18, 12)

    return img.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    for name, color in (('favicon.png', DARK), ('favicon_light.png', LIGHT)):
        path = OUT_DIR / name
        render_mark(color).save(path)
        print(f'wrote {path}')


if __name__ == '__main__':
    main()
