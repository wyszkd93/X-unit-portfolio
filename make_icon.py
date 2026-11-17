# -*- coding: utf-8 -*-
"""
바로가기용 아이콘(icon.ico) 만들기:  python make_icon.py [미리보기.png]
브라우저 탭 아이콘(web/index.html 의 favicon)과 같은 디자인을 외부 라이브러리 없이 그린다.
  - 파란 둥근 사각형 + 흰 봉투 + 오른쪽 아래 돋보기
  - 16~24px 같은 작은 크기는 돋보기를 빼고 선을 굵게 해서 뭉개지지 않게 한다.
"""
import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
BLUE = (15, 108, 189)
WHITE = (255, 255, 255)
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


# ---- 거리 함수 (64x64 좌표계) ----
def sd_round_box(x, y, cx, cy, hw, hh, r):
    qx, qy = abs(x - cx) - hw + r, abs(y - cy) - hh + r
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - r


def sd_segment(x, y, ax, ay, bx, by):
    px, py, dx, dy = x - ax, y - ay, bx - ax, by - ay
    h = max(0.0, min(1.0, (px * dx + py * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - dx * h, py - dy * h)


def shapes(small):
    w = 6.0 if small else 4.5          # 봉투 선 굵기
    env = (32, 32, 20, 14, 4) if not small else (32, 32.5, 21, 15, 4)
    flap_y0, flap_y1 = (env[1] - env[3] + 3), (env[1] + 2)
    L = [
        (BLUE, lambda x, y: sd_round_box(x, y, 32, 32, 32, 32, 14)),
        (WHITE, lambda x, y: abs(sd_round_box(x, y, *env)) - w / 2),
        (WHITE, lambda x, y: min(sd_segment(x, y, env[0] - env[2] + 2, flap_y0, 32, flap_y1),
                                 sd_segment(x, y, 32, flap_y1, env[0] + env[2] - 2, flap_y0)) - w / 2),
    ]
    if not small:
        L += [
            (WHITE, lambda x, y: math.hypot(x - 48, y - 46) - 9),
            (BLUE, lambda x, y: abs(math.hypot(x - 47, y - 45) - 4.2) - 1.3),
            (BLUE, lambda x, y: sd_segment(x, y, 50.2, 48.2, 53, 51) - 1.3),
        ]
    return L


def render(size):
    ss = 4 if size >= 128 else 6
    layers = shapes(size <= 24)
    scale = 64.0 / size
    px = bytearray()
    for j in range(size):
        px.append(0)  # PNG 필터 바이트
        for i in range(size):
            r = g = b = a = 0.0
            for (col, f) in layers:
                cov = 0
                for sj in range(ss):
                    y = (j + (sj + 0.5) / ss) * scale
                    for si in range(ss):
                        if f((i + (si + 0.5) / ss) * scale, y) <= 0:
                            cov += 1
                if cov:
                    c = cov / (ss * ss)
                    r = col[0] * c + r * (1 - c)
                    g = col[1] * c + g * (1 - c)
                    b = col[2] * c + b * (1 - c)
                    a = c + a * (1 - c)
            if a > 0:
                px += bytes((round(r / a), round(g / a), round(b / a), round(a * 255)))
            else:
                px += b"\0\0\0\0"
    return png(size, bytes(px))


def png(size, raw):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    imgs = [(s, render(s)) for s in SIZES]
    head = struct.pack("<HHH", 0, 1, len(imgs))
    off = 6 + 16 * len(imgs)
    dirs, data = b"", b""
    for s, p in imgs:
        dirs += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(p), off + len(data))
        data += p
    out = os.path.join(HERE, "icon.ico")
    with open(out, "wb") as f:
        f.write(head + dirs + data)
    print(out, len(head + dirs + data), "bytes")
    if len(sys.argv) > 1:  # 미리보기: 모든 크기를 한 장에
        with open(sys.argv[1], "wb") as f:
            f.write(dict(imgs)[256])
        for s, p in imgs:
            with open(sys.argv[1].replace(".png", f"_{s}.png"), "wb") as f:
                f.write(p)


if __name__ == "__main__":
    main()
