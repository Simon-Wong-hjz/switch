#!/usr/bin/env python3
"""生成“安装为应用”用的图标 web/icon-192.png、web/icon-512.png。只用标准库；改了标志颜色或形状后重跑即可。

    python3 tools/make_icons.py
"""
import struct
import zlib
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / 'web'
BG = (0x0E, 0x15, 0x22)                                  # 暗色主题 --bg
# 与页面左上角的标志一致：22×22 画布上的三根竖条（--accent / --r / --q）
BARS = [((1, 3, 5, 16), (0x7A, 0xA2, 0xFF)), ((8.5, 3, 5, 11), (0x4C, 0xC9, 0xF0)), ((16, 3, 5, 7), (0xFF, 0xAB, 0x40))]
PLATE, RADIUS, LOGO, SAMPLES = 0.82, 0.22, 0.5, 4        # 底板占比（留出系统图标边距）、圆角、标志占底板比例、每轴抽样数


def inside_rounded(x, y, left, top, w, h, r):
    if not (left <= x <= left + w and top <= y <= top + h):
        return False
    dx = max(left + r - x, 0, x - (left + w - r))
    dy = max(top + r - y, 0, y - (top + h - r))
    return dx * dx + dy * dy <= r * r


def render(n):
    plate = n * PLATE
    p0 = (n - plate) / 2
    scale = plate * LOGO / 20                             # 标志内容区宽 20（x 从 1 到 21）
    lx, ly = p0 + (plate - 20 * scale) / 2 - scale, p0 + (plate - 16 * scale) / 2 - 3 * scale
    bars = [((lx + x * scale, ly + y * scale, w * scale, h * scale), c) for (x, y, w, h), c in BARS]
    rows = []
    for py in range(n):
        row = bytearray([0])
        for px in range(n):
            acc, alpha = [0, 0, 0], 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x, y = px + (sx + 0.5) / SAMPLES, py + (sy + 0.5) / SAMPLES
                    if not inside_rounded(x, y, p0, p0, plate, plate, plate * RADIUS):
                        continue
                    color = next((c for (bx, by, bw, bh), c in bars if inside_rounded(x, y, bx, by, bw, bh, 1.5 * scale)), BG)
                    acc = [a + c for a, c in zip(acc, color)]
                    alpha += 1
            row += bytes([round(a / alpha) for a in acc] + [round(255 * alpha / SAMPLES ** 2)]) if alpha else bytes(4)
        rows.append(bytes(row))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', n, n, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    for size in (192, 512):
        (WEB / f'icon-{size}.png').write_bytes(render(size))
        print(f'web/icon-{size}.png')
