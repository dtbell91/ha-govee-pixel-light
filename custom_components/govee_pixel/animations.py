"""Procedurally drawn 32x32 animations: weather, scrolling text and small animated scenes.

Pure Pillow with no Home Assistant imports, so `scripts/generate_gifs.py` can use it too.
Every renderer returns (frames, frame_duration_ms), frames being 32x32 RGB images.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from PIL import Image, ImageDraw

W = 32
Frames = tuple[list[Image.Image], int]

# ---------- drawing helpers ----------


def canvas(bg=(0, 0, 0)):
    im = Image.new("RGB", (W, W), tuple(bg))
    return im, ImageDraw.Draw(im)


def px(d, x, y, c):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < W and 0 <= y < W:
        d.point((x, y), fill=c)


def disc(d, cx, cy, r, c):
    d.ellipse([round(cx - r), round(cy - r), round(cx + r), round(cy + r)], fill=c)


def rect(d, x0, y0, x1, y1, c):
    d.rectangle([round(x0), round(y0), round(x1), round(y1)], fill=c)


def border(d, c):
    d.rectangle([0, 0, W - 1, W - 1], outline=c)


def dim(c, f):
    return tuple(int(v * f) for v in c)


def rainbow(i):
    r = 0.5 + 0.5 * math.sin(i)
    g = 0.5 + 0.5 * math.sin(i + 2.1)
    b = 0.5 + 0.5 * math.sin(i + 4.2)
    return (int(80 + 175 * r), int(80 + 175 * g), int(80 + 175 * b))


# ---------- 3x5 font (rows top to bottom, 15 bits) ----------

FONT = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001001001001", "8": "111101111101111",
    "9": "111101111001111",
    "A": "010101111101101", "B": "110101110101110", "C": "011100100100011",
    "D": "110101101101110", "E": "111100110100111", "F": "111100110100100",
    "G": "011100101101011", "H": "101101111101101", "I": "111010010010111",
    "J": "001001001101010", "K": "101101110101101", "L": "100100100100111",
    "M": "101111111101101", "N": "110101101101101", "O": "010101101101010",
    "P": "110101110100100", "Q": "010101101110011", "R": "110101110101101",
    "S": "011100010001110", "T": "111010010010010", "U": "101101101101111",
    "V": "101101101101010", "W": "101101111111101", "X": "101101010101101",
    "Y": "101101010010010", "Z": "111001010100111",
    " ": "000000000000000", ".": "000000000000010", ",": "000000000010100",
    "!": "010010010000010", "?": "110001010000010", ":": "000010000010000",
    "'": "010010000000000", "-": "000000111000000", "+": "000010111010000",
    "/": "001001010100100", "%": "101001010100101", "°": "010101010000000",
    "(": "001010010010001", ")": "100010010010100", "&": "010101010101011",
}


def _glyph(ch):
    return FONT.get(ch.upper(), FONT["?"])


def text(d, s, x, y, c, scale=1, gap=1):
    """Draw `s` with its top-left at (x, y); returns the x after the last glyph."""
    for i, ch in enumerate(s):
        col = c(i) if callable(c) else c
        for k, b in enumerate(_glyph(ch)):
            if b == "1":
                gx, gy = k % 3, k // 3
                rect(d, x + gx * scale, y + gy * scale,
                     x + gx * scale + scale - 1, y + gy * scale + scale - 1, col)
        x += (3 + gap) * scale
    return x


def text_w(s, scale=1, gap=1):
    return len(s) * (3 + gap) * scale - gap * scale


# ---------- weather pieces ----------

# Home Assistant weather conditions -> the icon drawn for them.
HA_CONDITIONS = {
    "sunny": "sunny", "clear-night": "night", "partlycloudy": "partly", "cloudy": "cloudy",
    "fog": "fog", "rainy": "rain", "pouring": "pouring", "lightning": "storm",
    "lightning-rainy": "storm", "snowy": "snow", "snowy-rainy": "sleet", "hail": "hail",
    "windy": "windy", "windy-variant": "windy", "exceptional": "exceptional",
}
CONDITIONS = ("sunny", "night", "partly", "cloudy", "fog", "rain", "pouring", "storm", "snow",
              "sleet", "hail", "windy", "exceptional")


def normalise_condition(cond: str) -> str:
    cond = (cond or "").strip().lower()
    if cond in CONDITIONS:
        return cond
    return HA_CONDITIONS.get(cond, "cloudy")


def temp_col(t, unit="°C"):
    c = (t - 32) * 5 / 9 if unit.upper().endswith("F") else t
    if c <= 10:
        return (90, 170, 255)
    if c <= 19:
        return (170, 255, 200)
    if c <= 27:
        return (255, 190, 60)
    return (255, 80, 60)


def cloud(d, cx, cy, u, c=(215, 225, 240), sh=(140, 150, 175)):
    for col, dy in ((sh, 1), (c, 0)):
        disc(d, cx - u, cy + dy + u * 0.3, u * 0.8, col)
        disc(d, cx + u, cy + dy + u * 0.3, u * 0.8, col)
        disc(d, cx, cy + dy - u * 0.3, u, col)
        rect(d, cx - u, cy + dy + u * 0.3, cx + u, cy + dy + u * 1.1, col)


def sun(d, cx, cy, r, phase=0, rays=8, ri=None, ro=None, col=(255, 205, 0), rcol=(255, 140, 0)):
    ri = r + 2 if ri is None else ri
    ro = r + 4 if ro is None else ro
    for k in range(rays):
        a = 2 * math.pi * k / rays + (phase / 8) * (2 * math.pi / rays)
        length = ro if k % 2 == 0 else ro - 1
        for rr in range(ri, length + 1):
            px(d, cx + math.cos(a) * rr, cy + math.sin(a) * rr, rcol)
    disc(d, cx, cy, r, col)


def moon(d, cx, cy, r, bg=(0, 0, 0)):
    disc(d, cx, cy, r, (255, 225, 110))
    disc(d, cx + r * 0.6, cy - r * 0.4, r * 0.85, bg)


def twinkle(d, t, stars, col=(255, 245, 170)):
    for i, (x, y) in enumerate(stars):
        s = (t + i * 3) % 8
        if s < 5:
            px(d, x, y, col)
        if s == 0:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                px(d, x + dx, y + dy, dim(col, 0.6))


def raindrops(d, t, x0, x1, y0, y1, col=(70, 150, 255), n=5, speed=2):
    span, h = x1 - x0, y1 - y0
    for i in range(n):
        x = x0 + (i * span) // max(1, n - 1)
        y = y0 + ((t * speed + i * 3) % (h + 1))
        px(d, x, y, col)
        px(d, x, y + 1, dim(col, 0.6))


def flakes(d, t, x0, x1, y0, y1, n=6, col=(240, 245, 255), speed=1):
    span, h = x1 - x0, y1 - y0
    for i in range(n):
        x = x0 + (i * span) // max(1, n - 1) + round(math.sin((t + i * 2) / 2))
        y = y0 + ((t * speed + i * 3) % (h + 1))
        px(d, x, y, col)


def bolt(d, cx, cy):
    d.polygon([(cx + 1, cy), (cx - 3, cy + 5), (cx, cy + 5), (cx - 2, cy + 9), (cx + 3, cy + 3),
               (cx, cy + 3)], fill=(255, 235, 60))


def wind(d, t, x0, x1, ys, col=(190, 220, 240)):
    span = x1 - x0
    for i, y in enumerate(ys):
        length = 6 + (i % 2) * 3
        x = x0 + (t * 2 + i * 7) % (span + length) - length
        for k in range(length):
            if x0 <= x + k <= x1:
                px(d, x + k, y, dim(col, 0.4 + 0.6 * k / length))


def fog(d, t, x0, x1, ys, col=(170, 175, 190)):
    for i, y in enumerate(ys):
        off = round(math.sin((t + i * 3) / 8 * 2 * math.pi) * 2)
        rect(d, x0 + off + (i % 2) * 2, y, x1 + off - (i % 2) * 2, y, col)


def warning(d, cx, cy, t):
    col = (255, 200, 40) if t % 4 < 2 else (255, 120, 40)
    d.polygon([(cx, cy - 6), (cx - 7, cy + 6), (cx + 7, cy + 6)], outline=col)
    rect(d, cx, cy - 2, cx, cy + 2, col)
    px(d, cx, cy + 4, col)


def draw_temp(d, t, y=21, unit="°C"):
    s = str(t)
    col = temp_col(t, unit)
    tw = text_w(s, 2) + 1 + 3
    x = (W - tw) // 2
    x = text(d, s, x, y, col, scale=2)
    for dx, dy in ((1, 0), (0, 1), (2, 1), (1, 2)):
        px(d, x + dx, y + dy, col)


def weather_frame(cond, temp, t, unit="°C"):
    im, d = canvas()
    cx, cy = 16, 9
    if cond == "sunny":
        sun(d, cx, cy + 1, 4, phase=t)
    elif cond == "night":
        moon(d, 16, 9, 5)
        twinkle(d, t, [(5, 3), (26, 4), (28, 13), (4, 14), (9, 8)])
    elif cond == "partly":
        sun(d, 11, 8, 3, phase=t, ri=5, ro=7)
        cloud(d, 18, 11, 5)
    elif cond == "cloudy":
        off = round(math.sin(t / 8 * 2 * math.pi) * 1.5)
        cloud(d, 12 - off, 7, 4, c=(150, 160, 180), sh=(100, 108, 130))
        cloud(d, 18 + off, 11, 5)
    elif cond == "fog":
        cloud(d, 16, 5, 4, c=(150, 160, 180), sh=(100, 108, 130))
        fog(d, t, 5, 26, (12, 14, 16, 18))
    elif cond in ("rain", "pouring"):
        cloud(d, 16, 6, 5, c=(170, 180, 200), sh=(110, 120, 145))
        raindrops(d, t, 7, 25, 13, 18, n=8 if cond == "pouring" else 5)
    elif cond == "storm":
        cloud(d, 16, 5, 5, c=(110, 115, 140), sh=(70, 75, 100))
        if t % 4 in (0, 1):
            bolt(d, 17, 9)
        raindrops(d, t, 8, 24, 13, 18, n=4)
    elif cond == "snow":
        cloud(d, 16, 6, 5, c=(190, 200, 220), sh=(130, 140, 165))
        flakes(d, t, 8, 24, 13, 19)
    elif cond == "sleet":
        cloud(d, 16, 6, 5, c=(170, 180, 200), sh=(110, 120, 145))
        raindrops(d, t, 8, 24, 13, 18, n=3)
        flakes(d, t, 10, 22, 13, 19, n=3)
    elif cond == "hail":
        cloud(d, 16, 6, 5, c=(150, 160, 180), sh=(100, 108, 130))
        flakes(d, t, 8, 24, 13, 19, n=5, col=(220, 235, 255), speed=2)
    elif cond == "windy":
        wind(d, t, 3, 28, (4, 8, 12, 16))
    else:
        warning(d, 16, 9, t)
    draw_temp(d, temp, unit=unit)
    return im


def mini_icon(d, cond, cx, cy, t):
    if cond == "sunny":
        sun(d, cx, cy, 2, phase=t, ri=3, ro=4)
    elif cond == "night":
        moon(d, cx, cy, 3)
    elif cond == "partly":
        sun(d, cx - 2, cy - 1, 2, phase=t, ri=3, ro=4)
        cloud(d, cx + 1, cy + 1, 2.4)
    elif cond in ("rain", "pouring", "sleet"):
        cloud(d, cx - 1, cy - 2, 2.1, c=(170, 180, 200), sh=(110, 120, 145))
        for i, dx in enumerate((-3, 0, 3)):
            col = (240, 245, 255) if cond == "sleet" and i == 1 else (70, 150, 255)
            px(d, cx + dx, cy + 2 + ((t + i * 2) % 4), col)
    elif cond == "storm":
        cloud(d, cx, cy - 2, 2.6, c=(110, 115, 140), sh=(70, 75, 100))
        if t % 4 in (0, 1):
            bolt(d, cx + 1, cy + 1)
    elif cond in ("snow", "hail"):
        cloud(d, cx - 1, cy - 2, 2.1, c=(190, 200, 220), sh=(130, 140, 165))
        for i, dx in enumerate((-3, 0, 3)):
            px(d, cx + dx, cy + 2 + ((t + i * 2) % 4), (240, 245, 255))
    elif cond == "fog":
        fog(d, t, cx - 4, cx + 4, (cy - 2, cy, cy + 2))
    elif cond == "windy":
        wind(d, t, cx - 5, cx + 5, (cy - 2, cy + 1, cy + 3))
    elif cond == "exceptional":
        col = (255, 200, 40) if t % 4 < 2 else (255, 120, 40)
        rect(d, cx, cy - 3, cx, cy + 1, col)
        px(d, cx, cy + 3, col)
    else:
        cloud(d, cx, cy, 2.8)


def weather(condition: str, temperature: float, unit: str = "°C") -> Frames:
    """Animated icon for the condition with the temperature underneath (8 frames)."""
    cond = normalise_condition(condition)
    temp = round(temperature)
    return [weather_frame(cond, temp, t, unit) for t in range(8)], 130


def forecast(days: list[tuple[str, float, float | None]], unit: str = "°C") -> Frames:
    """Up to three days side by side: icon, high, low. `days` = [(condition, high, low), ...]."""
    days = days[:3]
    frames = []
    for t in range(8):
        im, d = canvas()
        for x in (10, 21):
            for y in range(2, 30):
                px(d, x, y, (35, 35, 50))
        for i, (cond, hi, lo) in enumerate(days):
            cx = 5 + i * 11
            mini_icon(d, normalise_condition(cond), cx, 7, t)
            hs = str(round(hi))
            text(d, hs, cx - text_w(hs) // 2, 16, temp_col(round(hi), unit))
            if lo is not None:
                ls = str(round(lo))
                text(d, ls, cx - text_w(ls) // 2, 23, dim((120, 170, 255), 0.9))
        rect(d, 1, 31, 9, 31, (255, 255, 255))  # "today" marker
        frames.append(im)
    return frames, 140


# ---------- text ----------

def scrolling_text(message: str, color=None, background=(0, 0, 0), large: bool = True,
                   frame_ms: int = 70, max_frames: int = 100) -> Frames:
    """Text centred if it fits, otherwise scrolled right-to-left. color=None means rainbow."""
    scale = 2 if large else 1
    message = message.strip() or " "
    width = text_w(message, scale)
    y = (W - 5 * scale) // 2

    def col(t):
        return (lambda i: rainbow(t * 0.35 + i)) if color is None else tuple(color)

    if width <= W:
        x = (W - width) // 2
        n = 1 if color is not None else 16
        frames = []
        for t in range(n):
            im, d = canvas(background)
            text(d, message, x, y, col(t), scale=scale)
            frames.append(im)
        return frames, frame_ms if n > 1 else 1000
    travel = width + W
    step = max(1, math.ceil(travel / max_frames))
    frames = []
    for t in range(math.ceil(travel / step)):
        im, d = canvas(background)
        text(d, message, W - t * step, y, col(t), scale=scale)
        frames.append(im)
    return frames, frame_ms * step


def now_playing(cover: Image.Image | None, caption: str, color=(255, 255, 255),
                size: int = W) -> list[tuple[Image.Image, int]]:
    """Album art (or a note) with the caption scrolling in a dark band along the bottom."""
    base = cover.convert("RGB") if cover is not None else _note()
    band_y = size - 7
    shaded = base.copy()
    sd = ImageDraw.Draw(shaded)
    for y in range(band_y, size):
        for x in range(size):
            r, g, b = shaded.getpixel((x, y))
            sd.point((x, y), (r // 5, g // 5, b // 5))
    caption = caption.strip()
    if not caption:
        return [(base, 1000)]
    width = text_w(caption)
    frames = [(base, 2500)]  # the art on its own first
    if width <= size:
        im = shaded.copy()
        text(ImageDraw.Draw(im), caption, (size - width) // 2, band_y + 1, tuple(color))
        frames.append((im, 4000))
        return frames
    travel = width + size
    step = max(1, math.ceil(travel / 95))
    for t in range(0, travel, step):
        im = shaded.copy()
        text(ImageDraw.Draw(im), caption, size - t, band_y + 1, tuple(color))
        frames.append((im, 80 * step))
    return frames


def reminder(day: int, when: str, time_label: str, summary: str, color=(255, 200, 60)) -> Frames:
    """Calendar-page icon with the day number, when (TDAY / TMRW / FRI) and time beside it; the event name
    below, starting fully visible from the left, scrolling after a pause if it doesn't fit."""
    def top(d):
        rect(d, 1, 1, 13, 3, (220, 40, 40))  # page header
        rect(d, 1, 4, 13, 14, (235, 235, 235))  # page
        num = str(day)
        text(d, num, 1 + (13 - text_w(num)) // 2, 7, (30, 30, 30))
        text(d, when, 16, 3, tuple(color))
        if time_label:
            text(d, time_label, 16, 9, dim(tuple(color), 0.7))

    summary = summary.strip().upper() or "EVENT"
    width = text_w(summary, 2)
    y = 19
    if width <= W - 2:
        im, d = canvas()
        top(d)
        text(d, summary, (W - width) // 2, y, (255, 255, 255), scale=2)
        return [im], 1000
    frames = []
    step = 2
    for i, x in enumerate([1] * 12 + list(range(1, -(width - W + 2) - 1, -step)) + [-(width - W + 1)] * 12):
        im, d = canvas()
        top(d)
        text(d, summary, x, y, (255, 255, 255), scale=2)
        frames.append(im)
    return frames, 90


def _note() -> Image.Image:
    """Placeholder art: a music note on dark purple."""
    im, d = canvas((25, 0, 40))
    rect(d, 18, 6, 19, 19, (230, 180, 255))
    rect(d, 18, 6, 24, 7, (230, 180, 255))
    rect(d, 23, 8, 24, 9, (230, 180, 255))
    disc(d, 15, 20, 3, (230, 180, 255))
    return im


# ---------- animated scenes ----------

def scene_maths(t):
    im, d = canvas()
    border(d, dim((60, 120, 255), 0.6))
    idx = (t // 3) % 4
    col = rainbow(t * 0.5)
    cx, cy = 16, 10
    size = 5 + (1 if t % 3 == 0 else 0)
    th = 1
    if idx == 0:  # plus
        rect(d, cx - size, cy - th, cx + size, cy + th, col)
        rect(d, cx - th, cy - size, cx + th, cy + size, col)
    elif idx == 1:  # minus
        rect(d, cx - size, cy - th, cx + size, cy + th, col)
    elif idx == 2:  # times
        for k in range(-size, size + 1):
            for o in range(-th, th + 1):
                px(d, cx + k + o, cy + k, col)
                px(d, cx + k + o, cy - k, col)
    else:  # divide
        rect(d, cx - size, cy - th, cx + size, cy + th, col)
        disc(d, cx, cy - 5, 1.5, col)
        disc(d, cx, cy + 5, 1.5, col)
    text(d, "123", 5, 21, (255, 225, 90), scale=2)
    return im


def scene_reading(t):
    im, d = canvas()
    border(d, (60, 200, 110))
    page, shade = (245, 240, 220), (200, 195, 170)
    d.polygon([(5, 10), (15, 12), (15, 24), (5, 22)], fill=page)
    d.polygon([(27, 10), (17, 12), (17, 24), (27, 22)], fill=page)
    rect(d, 16, 12, 16, 24, shade)
    rect(d, 4, 23, 28, 25, (150, 90, 50))
    for k in range(4):
        y = 14 + k * 2
        d.line([(7, y), (13, y + 1)], fill=(90, 130, 200))
        d.line([(19, y + 1), (25, y)], fill=(90, 130, 200))
    for i, (x, y) in enumerate([(8, 5), (16, 4), (24, 6), (12, 7), (21, 3)]):
        if (t + i * 2) % 6 < 3:
            px(d, x, y, (255, 240, 120))
            if (t + i * 2) % 6 == 0:
                for dx, dy in ((-1, 0), (1, 0), (0, -1)):
                    px(d, x + dx, y + dy, (200, 180, 80))
    return im


def scene_art(t):
    im, d = canvas()
    border(d, (230, 70, 200))
    d.ellipse([4, 12, 24, 27], fill=(225, 185, 135))
    blobs = [(9, 17, (255, 60, 60)), (14, 15, (255, 215, 40)), (19, 17, (60, 130, 255)),
             (11, 22, (60, 210, 90)), (18, 22, (200, 80, 230))]
    for i, (x, y, c) in enumerate(blobs):
        disc(d, x, y, 2, c)
        if i == t % 5:
            d.ellipse([x - 3, y - 3, x + 3, y + 3], outline=(255, 255, 255))
    disc(d, 22, 24, 1.2, (30, 20, 10))
    d.line([(21, 11), (28, 4)], fill=(150, 90, 50), width=2)
    d.line([(20, 12), (22, 10)], fill=(190, 190, 200), width=2)
    disc(d, 19, 13, 1.5, blobs[t % 5][2])
    return im


def scene_music(t):
    im, d = canvas()
    border(d, (170, 90, 255))
    b1 = round(2 * math.sin(2 * math.pi * t / 8))
    b2 = round(2 * math.sin(2 * math.pi * t / 8 + math.pi))
    col = (90, 220, 255) if t % 4 < 2 else (255, 120, 200)
    h1, h2 = 22 + b1, 20 + b2
    disc(d, 10, h1, 2.4, col)
    disc(d, 20, h2, 2.4, col)
    rect(d, 12, h1 - 13, 12, h1, col)
    rect(d, 22, h2 - 13, 22, h2, col)
    d.line([(12, h1 - 13), (22, h2 - 13)], fill=col, width=2)
    for k in range(3):
        a = (t + k * 2) % 6
        if a < 4:
            px(d, 26 + k, 9 + k * 4 + a % 2, dim((255, 255, 255), 0.8))
    return im


def scene_sport(t):
    im, d = canvas()
    border(d, (255, 140, 30))
    h = abs(math.sin(math.pi * t / 12))
    yb = round(24 - 15 * h)
    rect(d, 3, 28, 28, 28, (60, 160, 70))
    sw = 4 + round(4 * (1 - h))
    d.ellipse([16 - sw, 27, 16 + sw, 29], fill=(30, 60, 30))
    squash = 1 if h < 0.15 else 0
    d.ellipse([11 - squash, yb - 5 + squash, 21 + squash, yb + 5], fill=(255, 130, 30))
    d.line([(11, yb), (21, yb)], fill=(60, 30, 10))
    d.line([(16, yb - 5), (16, yb + 5)], fill=(60, 30, 10))
    return im


def scene_playground(t):
    im, d = canvas()
    rect(d, 0, 29, 31, 31, (50, 170, 70))
    rect(d, 5, 3, 27, 4, (150, 95, 50))
    d.line([(5, 4), (2, 29)], fill=(150, 95, 50), width=2)
    d.line([(27, 4), (30, 29)], fill=(150, 95, 50), width=2)
    a = math.sin(2 * math.pi * t / 12) * 0.6
    sx, sy = 16 + 16 * math.sin(a), 5 + 16 * math.cos(a)
    for off in (-3, 3):
        d.line([(16 + off, 5), (sx + off, sy)], fill=(200, 200, 210))
    rect(d, sx - 4, sy, sx + 4, sy + 1, (230, 60, 70))
    return im


def scene_lunch(t):
    im, d = canvas()
    border(d, (255, 90, 70))
    disc(d, 13, 18, 7, (235, 40, 40))
    disc(d, 19, 18, 7, (235, 40, 40))
    rect(d, 13, 13, 19, 25, (235, 40, 40))
    rect(d, 16, 7, 16, 11, (120, 70, 30))
    leaf = round(math.sin(2 * math.pi * t / 6))
    d.polygon([(17, 9), (22 + leaf, 6), (22 + leaf, 9)], fill=(70, 200, 80))
    px(d, 11, 15, (255, 190, 190))
    px(d, 11, 16, (255, 190, 190))
    if t >= 6:  # bite
        disc(d, 24, 14, 4, (0, 0, 0))
        px(d, 22 + (t % 2), 21 + (t % 3), (235, 40, 40))
    elif t % 3 == 0:
        px(d, 10, 14, (255, 255, 255))
    return im


def scene_homework(t):
    im, d = canvas()
    border(d, (255, 205, 40))
    rect(d, 5, 5, 26, 28, (245, 245, 235))
    rect(d, 5, 5, 6, 28, (230, 90, 90))
    for y in (10, 14, 18, 22, 26):
        rect(d, 8, y, 24, y, (160, 190, 235))
    length = min(16, (t % 12) * 2) if t % 12 < 9 else 16
    rect(d, 8, 13, 8 + length, 13, (40, 70, 200))
    x = 8 + length
    d.line([(x, 13), (x + 6, 7)], fill=(255, 205, 40), width=2)
    px(d, x, 13, (40, 30, 20))
    d.line([(x + 6, 7), (x + 8, 5)], fill=(245, 150, 170), width=2)
    return im


def scene_bus(t):
    im, d = canvas()
    border(d, (255, 205, 40))
    bob = t % 2
    cl = (t // 2) % 20
    cloud(d, 30 - cl, 5, 2.4, c=(200, 215, 235), sh=(140, 155, 180))
    cloud(d, 52 - cl, 6, 2.4, c=(200, 215, 235), sh=(140, 155, 180))
    y0 = 11 + bob
    rect(d, 4, y0, 27, y0 + 10, (255, 205, 20))
    rect(d, 25, y0 + 4, 28, y0 + 10, (255, 205, 20))
    rect(d, 4, y0 + 7, 28, y0 + 7, (40, 40, 40))
    for x in (6, 11, 16, 21):
        rect(d, x, y0 + 2, x + 3, y0 + 4, (150, 215, 255))
    rect(d, 25, y0 + 2, 27, y0 + 5, (150, 215, 255))
    px(d, 28, y0 + 9, (255, 255, 200))
    for wx in (9, 22):
        disc(d, wx, y0 + 11, 2.4, (20, 20, 20))
        a = t * math.pi / 3
        px(d, wx + math.cos(a) * 1.5, y0 + 11 + math.sin(a) * 1.5, (200, 200, 200))
    for k in range(5):
        x = (k * 8 - t * 2) % 40 - 4
        rect(d, x, 28, x + 3, 28, (230, 230, 230))
    return im


def scene_bedtime(t):
    im, d = canvas()
    border(d, (70, 70, 170))
    disc(d, 14, 15, 8, (255, 225, 110))
    disc(d, 19, 12, 7, (0, 0, 0))
    twinkle(d, t, [(5, 5), (25, 6), (27, 20), (6, 26), (22, 27), (9, 12)])
    for k in range(2):
        r = (t + k * 6) % 12
        y = 24 - r
        if y > 5:
            text(d, "Z", 21 + k * 2 + r // 4, y, dim((200, 210, 255), 1 - r / 14))
    return im


def scene_morning(t):
    im, d = canvas()
    border(d, (255, 150, 90))
    sun(d, 16, 24 - min(t, 9), 6, phase=t, ri=8, ro=11)
    rect(d, 1, 23, 30, 30, (35, 22, 12))
    rect(d, 1, 23, 30, 23, (255, 130, 50))
    for k in range(3):
        w = 6 - k * 2 + ((t + k) % 2)
        rect(d, 16 - w, 25 + k * 2, 16 + w, 25 + k * 2, dim((255, 150, 50), 0.45 - k * 0.12))
    return im


def scene_heart(t):
    im, d = canvas()
    heart = ["0110110", "1111111", "1111111", "0111110", "0011100", "0001000"]
    s = 3 if (t // 2) % 2 == 0 else 2
    w, h = 7 * s, 6 * s
    x0, y0 = (W - w) // 2, (W - h) // 2
    for r, row in enumerate(heart):
        for c, v in enumerate(row):
            if v == "1":
                rect(d, x0 + c * s, y0 + r * s, x0 + c * s + s - 1, y0 + r * s + s - 1, (255, 60, 110))
    for i in range(6):
        if (t + i * 3) % 6 < 2:
            px(d, (i * 7 + t * 3) % 32, (i * 11 + 2) % 32, (255, 255, 200))
    return im


# name -> (frame function, frame count, frame duration ms)
SCENES: dict[str, tuple[Callable[[int], Image.Image], int, int]] = {
    "morning": (scene_morning, 14, 130),
    "bus": (scene_bus, 12, 130),
    "maths": (scene_maths, 12, 130),
    "reading": (scene_reading, 12, 130),
    "art": (scene_art, 10, 130),
    "music": (scene_music, 8, 130),
    "sport": (scene_sport, 12, 130),
    "playground": (scene_playground, 12, 130),
    "lunch": (scene_lunch, 12, 130),
    "homework": (scene_homework, 12, 130),
    "bedtime": (scene_bedtime, 12, 130),
    "heart": (scene_heart, 12, 110),
}


def scene(name: str) -> Frames:
    fn, n, ms = SCENES[name]
    return [fn(t) for t in range(n)], ms
