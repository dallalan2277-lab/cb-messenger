"""빌드용 아이콘(app.ico) 만들기 — 트레이 아이콘과 같은 달력 그림."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw


def draw(size: int = 256) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    R = lambda *v: tuple(round(x * s) for x in v)  # noqa: E731
    d.rounded_rectangle(R(6, 10, 58, 58), round(8 * s), fill=(124, 172, 248, 255))
    d.rectangle(R(6, 10, 58, 22), fill=(66, 110, 200, 255))
    for x in (18, 32, 46):
        d.rectangle(R(x - 3, 4, x + 3, 16), fill=(230, 236, 245, 255))
    for r in range(2):
        for c in range(3):
            d.rectangle(R(14 + c * 14, 28 + r * 13, 22 + c * 14, 36 + r * 13), fill=(255, 255, 255, 230))
    return img


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("app.ico"))
    draw().save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"아이콘: {out}")
