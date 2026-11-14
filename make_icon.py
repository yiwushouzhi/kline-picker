"""生成应用图标 app.ico（纯标准库，无需 Pillow）。

深色圆角底 + 红绿K线 + 金色均线。
"""
from __future__ import annotations

import os
import struct
import zlib

W = H = 256
RADIUS = 46
BG_TOP = (28, 33, 44)
BG_BOT = (13, 15, 20)
UP = (239, 83, 80)
DOWN = (38, 166, 154)
GOLD = (255, 213, 79)
BORDER = (58, 66, 82)

px = bytearray(W * H * 4)


def put(x: int, y: int, color: tuple[int, int, int], a: int = 255) -> None:
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 4
        px[i:i + 4] = bytes((color[0], color[1], color[2], a))


def rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    rgba = bytes((color[0], color[1], color[2], 255))
    for y in range(max(0, y0), min(H, y1)):
        i = (y * W + max(0, x0)) * 4
        n = max(0, min(W, x1) - max(0, x0))
        px[i:i + n * 4] = rgba * n


def line(x0: int, y0: int, x1: int, y1: int, color, width: int = 4) -> None:
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx - dy
    half = width // 2
    while True:
        rect(x0 - half, y0 - half, x0 - half + width, y0 - half + width, color)
        if x0 == x1 and y0 == y1:
            break
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x0 += sx
        if e2 < dx:
            err += dx
            y0 += sy


def rounded_mask(x: int, y: int) -> bool:
    cx = min(max(x, RADIUS), W - 1 - RADIUS)
    cy = min(max(y, RADIUS), H - 1 - RADIUS)
    return (x - cx) ** 2 + (y - cy) ** 2 <= RADIUS ** 2


def build() -> None:
    for y in range(H):
        t = y / (H - 1)
        col = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        for x in range(W):
            if not rounded_mask(x, y):
                continue
            edge = not (rounded_mask(x - 2, y) and rounded_mask(x + 2, y)
                        and rounded_mask(x, y - 2) and rounded_mask(x, y + 2))
            put(x, y, BORDER if edge else col)

    bars = [
        (48, 96, 150, 74, 196, True),
        (88, 100, 160, 86, 186, False),
        (128, 78, 138, 58, 158, False),
        (168, 92, 150, 70, 176, True),
        (208, 110, 168, 92, 190, True),
    ]
    for x, bt, bb, wt, wb, up in bars:
        color = UP if up else DOWN
        rect(x + 9, wt, x + 11, wb, color)          # 上下影线
        if up:
            rect(x, bt, x + 20, bb, color)          # 阳线：实心 + 挖空成空心
            if bb - bt > 14:
                rect(x + 4, bt + 5, x + 16, bb - 5, BG_BOT)
        else:
            rect(x, bt, x + 20, bb, color)          # 阴线：实心

    line(46, 178, 96, 150, GOLD, 5)
    line(96, 150, 146, 164, GOLD, 5)
    line(146, 164, 196, 122, GOLD, 5)
    line(196, 122, 224, 132, GOLD, 5)


def png_bytes() -> bytes:
    raw = bytearray()
    for y in range(H):
        raw.append(0)
        raw += px[y * W * 4:(y + 1) * W * 4]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def main() -> None:
    build()
    png = png_bytes()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
    try:                      # 有 Pillow 时生成多尺寸标准 ICO
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(png))
        im.save(out, format="ICO",
                sizes=[(16, 16), (32, 32), (48, 48),
                       (64, 64), (128, 128), (256, 256)])
    except Exception:         # 回退：单尺寸 PNG 型 ICO
        header = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
        with open(out, "wb") as f:
            f.write(header + entry + png)
    print("已生成", out, f"{os.path.getsize(out) / 1024:.1f} KB")


if __name__ == "__main__":
    main()
