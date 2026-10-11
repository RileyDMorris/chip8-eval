"""Tiny dependency-free PNG writer and CHIP-8 frame-diff renderer."""

import struct
import zlib

OFF = (16, 18, 22)
ON = (230, 232, 236)
ONLY_CANDIDATE = (235, 80, 70)     # lit in the candidate's frame but not the reference's
ONLY_REFERENCE = (60, 190, 120)    # lit in the reference's frame but missing from the candidate's
BOTH_DIM = (70, 74, 82)            # lit in both (shown dimmed in the diff panel)
GAP = (40, 44, 52)
BLANK_FRAME = ["0" * 64] * 32


def write_png(path, pixels):
    """``pixels`` is a list of rows, each a list of (r, g, b) tuples."""
    height = len(pixels)
    width = len(pixels[0])
    raw = b"".join(b"\x00" + bytes(c for px in row for c in px) for row in pixels)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def valid_frame(frame):
    return (isinstance(frame, list) and len(frame) == 32
            and all(isinstance(r, str) and len(r) == 64 and set(r) <= {"0", "1"} for r in frame))


def _panel(frame, other=None, diff=False):
    rows = []
    for y in range(32):
        row = []
        for x in range(64):
            a = frame[y][x] == "1"
            if not diff:
                row.append(ON if a else OFF)
                continue
            b = other[y][x] == "1"
            row.append(BOTH_DIM if a and b else ONLY_CANDIDATE if a else ONLY_REFERENCE if b else OFF)
        rows.append(row)
    return rows


def render_diff(path, candidate, reference, scale=6, gap=4):
    """Write candidate | reference | diff side by side. Invalid candidate frames render blank."""
    candidate = candidate if valid_frame(candidate) else BLANK_FRAME
    panels = [_panel(candidate), _panel(reference), _panel(candidate, reference, diff=True)]
    width = 3 * 64 * scale + 4 * gap
    height = 32 * scale + 2 * gap
    image = [[GAP] * width for _ in range(height)]
    for p, panel in enumerate(panels):
        x_off = gap + p * (64 * scale + gap)
        for y in range(32):
            for x in range(64):
                color = panel[y][x]
                for dy in range(scale):
                    row = image[gap + y * scale + dy]
                    start = x_off + x * scale
                    row[start:start + scale] = [color] * scale
    write_png(path, image)
