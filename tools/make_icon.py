"""Gera multidesk/assets/icon.png e packaging/windows/multidesk.ico (sem dependências)."""

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BG = (37, 99, 235, 255)       # azul
SCREEN = (255, 255, 255, 255)
SCREEN2 = (191, 219, 254, 255)
DARK = (15, 23, 42, 255)


def render(n):
    px = [[(0, 0, 0, 0)] * n for _ in range(n)]

    def rect(x0, y0, x1, y1, color, radius=0.0):
        r = radius * n
        for y in range(int(y0 * n), int(y1 * n)):
            for x in range(int(x0 * n), int(x1 * n)):
                cx = min(max(x + 0.5, x0 * n + r), x1 * n - r)
                cy = min(max(y + 0.5, y0 * n + r), y1 * n - r)
                if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r + 0.25:
                    px[y][x] = color

    rect(0.0, 0.0, 1.0, 1.0, BG, 0.2)
    # duas telas
    rect(0.10, 0.26, 0.48, 0.60, SCREEN, 0.03)
    rect(0.52, 0.26, 0.90, 0.60, SCREEN2, 0.03)
    rect(0.25, 0.60, 0.33, 0.68, SCREEN)
    rect(0.67, 0.60, 0.75, 0.68, SCREEN2)
    rect(0.19, 0.68, 0.39, 0.72, SCREEN, 0.01)
    rect(0.61, 0.68, 0.81, 0.72, SCREEN2, 0.01)
    # seta passando de uma tela para a outra
    rect(0.30, 0.405, 0.62, 0.455, DARK)
    for i in range(int(0.12 * n)):
        y = 0.43 * n
        h = 0.12 * n - i
        x = int(0.60 * n) + i
        for yy in range(int(y - h / 2), int(y + h / 2) + 1):
            if 0 <= yy < n and 0 <= x < n:
                px[yy][x] = DARK
    return px


def png(px):
    n = len(px)
    raw = b"".join(b"\x00" + bytes(c for p in row for c in p) for row in px)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images):
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return header + entries + blobs


def main():
    assets = ROOT / "multidesk" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "icon.png").write_bytes(png(render(256)))
    win = ROOT / "packaging" / "windows"
    win.mkdir(parents=True, exist_ok=True)
    (win / "multidesk.ico").write_bytes(ico([(s, png(render(s))) for s in (16, 24, 32, 48, 64, 256)]))
    print("ícones gerados")


if __name__ == "__main__":
    main()
