"""Procedurally drawn 32x32 animations: weather, scrolling text and small animated scenes.

Pure Pillow with no Home Assistant imports, so `scripts/generate_gifs.py` can use it too.
Every renderer returns (frames, frame_duration_ms), frames being 32x32 RGB images.
"""
from __future__ import annotations

import math
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageFilter

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


def text(d, s, x, y, c, scale=1, gap=1, dy=None):
    """Draw `s` with its top-left at (x, y); returns the x after the last glyph.
    c: a colour, or a function of the character's index; dy: a function of the index giving a vertical offset."""
    for i, ch in enumerate(s):
        col = c(i) if callable(c) else c
        cy = y + (dy(i) if dy else 0)
        for k, b in enumerate(_glyph(ch)):
            if b == "1":
                gx, gy = k % 3, k // 3
                rect(d, x + gx * scale, cy + gy * scale,
                     x + gx * scale + scale - 1, cy + gy * scale + scale - 1, col)
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


def twinkle(d, t, stars, col=(255, 245, 170)):
    for i, (x, y) in enumerate(stars):
        s = (t + i * 3) % 8
        if s < 5:
            px(d, x, y, col)
        if s == 0:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                px(d, x + dx, y + dy, dim(col, 0.6))


# ---------- weather icons ----------
# Drawn per pixel with soft (anti-aliased) edges and sub-pixel motion. Every motion repeats a whole number of
# times over the loop, so the GIF loops with no jump, drawn in however many frames. `pa` is an Image.load() pixel
# access and `ph` the loop phase, 0 <= ph < 1.

WEATHER_LOOP_MS = 3600
WEATHER_FRAMES = 72  # 50 ms each: smooth, for pixeld
WEATHER_FRAMES_STOCK = 32  # keeps Govee's own firmware's downloads to ~20-35 KB
TAU = 2 * math.pi

# (light side, shadow side) of each kind of cloud
CLOUD_WHITE = ((255, 255, 255), (125, 145, 190))
CLOUD_GREY = ((205, 212, 226), (85, 92, 118))
CLOUD_FAR = ((140, 148, 168), (55, 60, 82))
CLOUD_RAIN = ((175, 186, 210), (62, 72, 102))
CLOUD_STORM = ((118, 112, 150), (32, 28, 52))
CLOUD_SNOW = ((240, 244, 255), (130, 145, 180))
CLOUD_FOG = ((150, 156, 172), (75, 80, 100))

_LOBES = ((-1.0, 0.35, 0.62), (1.05, 0.4, 0.58), (-0.25, -0.22, 0.8), (0.62, -0.02, 0.66))  # (dx, dy, r) * u
_BASE = (-1.05, 1.05, 0.45, 0.5)  # flat-bottomed capsule joining the lobes: x0, x1, y, r (* u)
_LIGHT = (-0.48, -0.72, 0.5)  # from the upper left, towards the viewer


def lerp(a, b, f):
    return tuple(round(x + (y - x) * f) for x, y in zip(a, b))


def _clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v


def blend(pa, x, y, c, a=1.0):
    """Mix colour c into pixel (x, y) with opacity a."""
    if a <= 0.004 or not (0 <= x < W and 0 <= y < W):
        return
    if a >= 1:
        pa[x, y] = tuple(c)
        return
    o = pa[x, y]
    pa[x, y] = (round(o[0] + (c[0] - o[0]) * a), round(o[1] + (c[1] - o[1]) * a), round(o[2] + (c[2] - o[2]) * a))


def glow(pa, x, y, c, a=1.0):
    """Add light c * a to pixel (x, y)."""
    if a <= 0.004 or not (0 <= x < W and 0 <= y < W):
        return
    o = pa[x, y]
    pa[x, y] = tuple(min(255, round(o[i] + c[i] * a)) for i in range(3))


def dot(pa, x, y, c, a=1.0, add=False, crisp=False):
    """A one-pixel dot at a fractional position, shared between the four pixels around it (or, crisp, in the
    nearest one: sharper for small slow things like snowflakes)."""
    if crisp:
        (glow if add else blend)(pa, round(x), round(y), c, a)
        return
    fx, fy = math.floor(x), math.floor(y)
    ax, ay = x - fx, y - fy
    put = glow if add else blend
    for dx, wx in ((0, 1 - ax), (1, ax)):
        for dy, wy in ((0, 1 - ay), (1, ay)):
            put(pa, fx + dx, fy + dy, c, a * wx * wy)


def puff(pa, cx, cy, u, pal=CLOUD_WHITE, flash=0.0, alpha=1.0):
    """A shaded, soft-edged cumulus about 3.3u wide and 2u high centred on (cx, cy).
    flash (0-1) lights it from inside, for lightning."""
    light, shadow = pal
    lobes = [(cx + dx * u, cy + dy * u, r * u) for dx, dy, r in _LOBES]
    bx0, bx1, by, br = cx + _BASE[0] * u, cx + _BASE[1] * u, cy + _BASE[2] * u, _BASE[3] * u
    bottom, top = cy + 0.85 * u, cy - 1.02 * u
    for y in range(math.floor(top), math.ceil(bottom) + 1):
        for x in range(math.floor(cx - 1.7 * u), math.ceil(cx + 1.7 * u) + 1):
            sd, best, n = 9e9, -9e9, (0.0, 0.0, 1.0)
            for lx, ly, r in lobes + [(_clamp(x, bx0, bx1), by, br)]:
                dx, dy = x - lx, y - ly
                d = math.hypot(dx, dy)
                sd = min(sd, d - r)
                if d < r + 0.7:
                    q = min(d / r, 1.0)
                    z = math.sqrt(1 - q * q) * r + ly * -0.05  # surface height; higher lobes sit a little behind
                    if z > best:
                        best = z
                        n = (dx / max(d, r), dy / max(d, r), math.sqrt(1 - q * q))
            sd = max(sd, y - bottom)
            cov = _clamp(0.5 - sd)
            if cov <= 0:
                continue
            lam = _clamp(n[0] * _LIGHT[0] + n[1] * _LIGHT[1] + n[2] * _LIGHT[2])
            low = _clamp((y - top) / (bottom - top))
            col = lerp(shadow, light, _clamp(0.15 + 0.95 * lam - 0.35 * low))
            if flash:
                col = lerp(col, (235, 230, 255), flash * (0.35 + 0.5 * low))
            blend(pa, x, y, col, cov * alpha)


def sun_glow(pa, cx, cy, r, ph, rays=8, ray_in=1.6, ray_out=(4.6, 3.2), halo=2.4):
    """A gradient sun with a breathing halo and long and short rays turning two ray-spacings per loop."""
    rot = 2 * TAU / rays * ph
    pulse = 0.5 + 0.5 * math.sin(TAU * ph * 2)
    reach = r + max(ray_out) + 1.5
    for y in range(math.floor(cy - reach), math.ceil(cy + reach) + 1):
        for x in range(math.floor(cx - reach), math.ceil(cx + reach) + 1):
            dx, dy = x - cx, y - cy
            d = math.hypot(dx, dy)
            out = d - r
            if out > 0:
                glow(pa, x, y, (255, 110, 0), 0.32 * (0.75 + 0.25 * pulse) * math.exp(-out / halo))
                a = (math.atan2(dy, dx) - rot) * rays / TAU
                k = round(a)
                across = abs(a - k) * TAU / rays * d  # distance from the ray's centre line, in pixels
                length = r + ray_out[k % 2] + 0.5 * pulse
                along = _clamp(out - ray_in + 0.5) * _clamp(length - d + 0.5)
                ray = _clamp(1.0 - across / 0.75) * along * (1 - 0.45 * (d - r) / (length - r))
                blend(pa, x, y, (255, 175, 30), ray)
            cov = _clamp(r + 0.5 - d)
            if cov:
                f = _clamp(math.hypot(dx + r * 0.35, dy + r * 0.35) / (r * 1.25))
                blend(pa, x, y, lerp((255, 250, 190), (255, 160, 10), f), cov)


def moon_glow(pa, cx, cy, r, ph):
    """A crescent with a soft glow."""
    ox, oy, rc = cx + r * 0.62, cy - r * 0.42, r * 0.86
    pulse = 0.5 + 0.5 * math.sin(TAU * ph)
    for y in range(math.floor(cy - r - 3), math.ceil(cy + r + 3) + 1):
        for x in range(math.floor(cx - r - 3), math.ceil(cx + r + 3) + 1):
            d = math.hypot(x - cx, y - cy)
            sd = max(d - r, rc - math.hypot(x - ox, y - oy))
            if sd > 0:
                glow(pa, x, y, (90, 80, 30), (0.22 + 0.08 * pulse) * math.exp(-sd / 1.6))
                continue
            cov = _clamp(0.5 - sd)
            shade = _clamp(((x - cx + r) * 0.4 + (y - cy + r) * 0.6) / (2 * r))
            col = lerp((255, 245, 200), (225, 185, 90), shade)
            blend(pa, x, y, col, cov)


def stars(pa, ph, spots):
    """spots: (x, y, twinkles per loop, phase offset). Each brightens and fades; at its peak it flares."""
    for x, y, c, o in spots:
        s = math.sin(TAU * (ph * c + o))
        b = 0.25 + 0.75 * max(0.0, s) ** 2
        blend(pa, x, y, (255, 245, 200), b)
        if b > 0.85:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                blend(pa, x + dx, y + dy, (170, 180, 255), (b - 0.85) * 3)


def shooting_star(pa, ph, start, end, at=0.15, length=0.18):
    """Streaks from start to end over `length` of the loop, starting at `at`."""
    s = (ph - at) / length
    if not 0 <= s <= 1:
        return
    hx, hy = start[0] + (end[0] - start[0]) * s, start[1] + (end[1] - start[1]) * s
    ux, uy = end[0] - start[0], end[1] - start[1]
    n = math.hypot(ux, uy)
    fade = math.sin(math.pi * s)
    for k in range(9):
        f = k / 2
        dot(pa, hx - ux / n * f, hy - uy / n * f, (255, 255, 230) if k < 2 else (150, 170, 255),
            fade * (1 - k / 9), add=True)


def rain(pa, ph, x0, x1, y0, y1, n=6, speeds=(5, 6, 7), length=3.0, slant=0.3, splash=True, seed=1,
         head=(200, 232, 255), tail=(40, 110, 240)):
    """Slanted streaks falling from y0 to y1 (start them inside the cloud, drawn afterwards, to hide where they
    appear). Faster drops are nearer and brighter; each lands with a little splash."""
    fall = y1 - y0 + length
    for i in range(n):
        h = _mix(seed, i)
        c = speeds[(h >> 8) % len(speeds)]
        near = (c - min(speeds)) / max(1, max(speeds) - min(speeds))
        bright = 0.7 + 0.3 * near
        x = x0 + (i + 0.5) * (x1 - x0) / n + ((h & 0xFF) / 255 - 0.5) * 1.5
        y = y0 + ((ph * c + ((h >> 16) & 0xFF) / 256) % 1) * fall
        for row in range(math.floor(y - length), math.ceil(y) + 1):
            if row > y1:
                continue
            cov = _clamp(min(row + 0.5, y) - max(row - 0.5, y - length))
            g = _clamp(1 - (y - row) / (length + 0.5))
            blend(pa, round(x + slant * (row - y0)), row, lerp(tail, head, g), cov * (0.45 + 0.55 * g) * bright)
        if splash and y > y1:
            s = (y - y1) / length
            sx = x + slant * (y1 - y0)
            for side in (-1, 1):
                dot(pa, sx + side * (1 + 1.5 * s), y1 - 1.5 * math.sin(math.pi * s), head, (1 - s) * bright, crisp=True)


def snow(pa, ph, x0, x1, y0, y1, n=6, speeds=(1, 2), seed=2, col=(245, 248, 255)):
    """Flakes drifting down and swaying; the nearer, faster ones are bigger."""
    fall = y1 - y0 + 2
    for i in range(n):
        h = _mix(seed, i)
        c = speeds[(h >> 8) % len(speeds)]
        x = x0 + (i + 0.5) * (x1 - x0) / n + ((h & 0xFF) / 255 - 0.5)
        x += 1.1 * math.sin(TAU * (ph * (c + 1) + ((h >> 4) & 0xFF) / 256))
        y = y0 - 1 + ((ph * c + ((h >> 16) & 0xFF) / 256) % 1) * fall
        a = _clamp(y1 + 1 - y) * _clamp((y - y0 + 2) / 3)
        big = c == max(speeds)
        dot(pa, x, y, col, a, crisp=True)
        if big:
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                dot(pa, x + dx, y + dy, (150, 185, 255), 0.5 * a, crisp=True)


def hail(pa, ph, x0, x1, y0, y1, n=5, speeds=(6, 7), seed=3):
    """Icy pellets that fall fast and bounce off the ground."""
    drop = 0.7  # share of each cycle spent falling; the rest bouncing
    for i in range(n):
        h = _mix(seed, i)
        c = speeds[(h >> 8) % len(speeds)]
        x = x0 + (i + 0.5) * (x1 - x0) / n + ((h & 0xFF) / 255 - 0.5)
        p = (ph * c + ((h >> 16) & 0xFF) / 256) % 1
        if p < drop:
            y = y0 + (p / drop) ** 1.3 * (y1 - y0)
            dot(pa, x, y - 1, (90, 140, 220), 0.5, crisp=True)
            a = 1.0
        else:
            s = (p - drop) / (1 - drop)
            x += (1 if i % 2 else -1) * 2 * s
            y = y1 - 3 * math.sin(math.pi * s) * (1 - 0.3 * s)
            a = 1 - s * s
        dot(pa, x, y, (235, 248, 255), a, crisp=True)
        dot(pa, x + 1, y, (150, 190, 240), 0.7 * a, crisp=True)
        dot(pa, x, y + 1, (150, 190, 240), 0.7 * a, crisp=True)
        dot(pa, x + 1, y + 1, (90, 130, 200), 0.6 * a, crisp=True)


def bolt(pa, pts, a):
    """A forked lightning bolt along the points: a white core with a violet glow."""
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        steps = max(1, round(math.hypot(xb - xa, yb - ya) * 3))
        for k in range(steps + 1):
            x, y = xa + (xb - xa) * k / steps, ya + (yb - ya) * k / steps
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                glow(pa, round(x + dx), round(y + dy), (60, 45, 120), a * 0.5)
            blend(pa, round(x), round(y), (255, 250, 210), a)


def gusts(pa, ph, rows, x0=0, x1=W - 1, col=(200, 230, 255)):
    """Wavy streaks of wind blowing right, hooking up at the front. rows: (y, length, laps per loop, offset)."""
    period = x1 - x0 + 16
    for y0, length, laps, o in rows:
        hx = x0 - 4 + ((ph * laps + o) % 1) * period
        wave = lambda x, y0=y0, laps=laps: y0 + 0.5 * math.sin(TAU * (x / 16 - ph * laps))
        edge = lambda x: _clamp((x - x0 + 1) / 3) * _clamp((x1 + 1 - x) / 3)
        for x in range(math.floor(hx - length), math.ceil(hx) + 1):
            f = hx - x
            a = _clamp(1 - f / length) * _clamp(1 - (x - hx)) * edge(x)
            y = wave(x)
            fy = math.floor(y)
            blend(pa, x, fy, col, a * (1 - (y - fy)))
            blend(pa, x, fy + 1, col, a * (y - fy))
        y = wave(hx)
        for dx, dy, a in ((1, -1, 0.85), (1.5, -2, 0.7), (1, -3, 0.5), (0, -3, 0.3)):  # the curl
            dot(pa, hx + dx, y + dy, col, a * edge(hx + dx))


def leaf(pa, ph, x0, x1, y):
    """An autumn leaf tumbling across once per loop."""
    x = x0 - 3 + ph * (x1 - x0 + 6)
    yy = y + 3 * math.sin(TAU * ph * 2)
    ang = TAU * ph * 3
    for k, col in ((-1, (240, 160, 40)), (0, (220, 110, 30)), (1, (170, 70, 20))):
        dot(pa, x + math.cos(ang) * k * 0.9, yy + math.sin(ang) * k * 0.9, col)


def fog_bands(pa, ph, x0, x1, rows, col=(190, 195, 210)):
    """Wisps of mist drifting in alternate directions, with gaps that move along them. rows: (y, thickness)."""
    for i, (yc, th) in enumerate(rows):
        shift = (1 if i % 2 else -1) * 16 * ph
        o = i * 1.7
        for y in range(math.floor(yc - th - 1), math.ceil(yc + th + 1) + 1):
            v = math.exp(-((y - yc) / th) ** 2)
            for x in range(x0, x1 + 1):
                u = (x + shift) / 16
                tex = _clamp(0.45 + 0.55 * math.sin(TAU * u + o) + 0.3 * math.sin(TAU * 2 * u + 2 * o))
                edge = _clamp((x - x0 + 1) / 4) * _clamp((x1 + 1 - x) / 4)
                blend(pa, x, y, col, v * tex * edge * (0.55 + 0.1 * i))


def warning(pa, d, cx, ph):
    """A pulsing warning sign."""
    pulse = 0.5 + 0.5 * math.sin(TAU * ph * 2)
    col = lerp((255, 140, 20), (255, 215, 60), pulse)
    d.polygon([(cx - 1, 0), (cx + 1, 0), (cx + 10, 18), (cx - 10, 18)], outline=dim(col, 0.2 + 0.25 * pulse))
    d.polygon([(cx - 1, 2), (cx, 2), (cx + 8, 17), (cx - 9, 17)], fill=col)
    rect(d, cx - 1, 7, cx, 12, (40, 10, 0))
    rect(d, cx - 1, 14, cx, 15, (40, 10, 0))


# lightning: 48ths of the loop -> flash strength
_FLASHES = {30: 1.0, 31: 0.7, 32: 0.2, 34: 0.9, 35: 0.45, 36: 0.15}


def _flash(ph):
    return _FLASHES.get(int(ph * 48 + 1e-6), 0.0)
_BOLT = [(17, 9), (14.5, 12.5), (17.5, 13.5), (14, 18), (15, 19)]


def draw_temp(d, t, y=21, unit="°C", backing=None, pa=None):
    """The temperature, light at the top of the digits shading darker to the bottom.
    backing, for weather drawn behind the digits: "outline" (a dark edge round them) or "plate" (also dims the
    band they sit on); pa is the image's pixel access, needed for a backing."""
    s = str(t)
    col = temp_col(t, unit)
    x = (W - (text_w(s, 2) + 1 + 3)) // 2
    mask = Image.new("L", (W, W))
    md = ImageDraw.Draw(mask)
    for i, ch in enumerate(s):
        for k, b in enumerate(_glyph(ch)):
            if b == "1":
                gx, gy = x + i * 8 + (k % 3) * 2, y + (k // 3) * 2
                md.rectangle([gx, gy, gx + 1, gy + 1], fill=255)
    deg = x + len(s) * 8
    for dx, dy in ((1, 0), (0, 1), (2, 1), (1, 2)):
        md.point((deg + dx, y + dy), fill=255)
    if backing:
        ma = mask.load()
        edge = mask.filter(ImageFilter.MaxFilter(3)).load()
        for yy in range(y - 2, y + 12):
            for xx in range(W):
                if not 0 <= yy < W or ma[xx, yy]:
                    continue
                if edge[xx, yy]:
                    blend(pa, xx, yy, (0, 0, 0), 0.9)
                elif backing == "plate":
                    blend(pa, xx, yy, (0, 0, 0), 0.45 if y - 1 <= yy <= y + 10 else 0.2)
    for row in range(5):
        c = lerp(lerp(col, (255, 255, 255), 0.3), dim(col, 0.8), row / 4)
        for i, ch in enumerate(s):
            for k, b in enumerate(_glyph(ch)):
                if b == "1" and k // 3 == row:
                    gx = x + i * 8 + (k % 3) * 2
                    rect(d, gx, y + row * 2, gx + 1, y + row * 2 + 1, c)
    for dx, dy in ((1, 0), (0, 1), (2, 1), (1, 2)):
        px(d, deg + dx, y + dy, col)


def weather_icon(pa, d, cond, ph, full=False):
    """The weather in the 32x20 area above the temperature, or with full, falling (blowing, drifting) all the
    way down the card behind it."""
    s = math.sin(TAU * ph)
    g = W - 1 if full else 19  # where rain lands
    if cond == "sunny":
        sun_glow(pa, 16, 9.5, 4.5, ph)
    elif cond == "night":
        spots = [(4, 3, 1, 0.0), (26, 3, 2, 0.3), (29, 12, 1, 0.6), (3, 14, 2, 0.8), (8, 8, 1, 0.45), (23, 16, 1, 0.15)]
        if full:
            spots += [(1, 22, 1, 0.7), (30, 25, 2, 0.05), (2, 30, 1, 0.35), (29, 31, 1, 0.9)]
        stars(pa, ph, spots)
        shooting_star(pa, ph, (27, 1), (19, 6), at=0.55)
        moon_glow(pa, 15, 10, 6, ph)
    elif cond == "partly":
        sun_glow(pa, 11, 7.5, 3.6, ph, ray_out=(3.6, 2.6), halo=2.0)
        puff(pa, 19.5 + 1.2 * s, 12.5, 4.6, CLOUD_WHITE)
    elif cond == "cloudy":
        puff(pa, 25 - 1.0 * math.cos(TAU * ph), 4.5, 2.4, CLOUD_FAR)
        puff(pa, 11 + 1.5 * s, 7.5, 3.8, CLOUD_GREY)
        puff(pa, 19 - 1.2 * s, 12, 4.8, CLOUD_WHITE)
    elif cond == "fog":
        puff(pa, 16 + 0.8 * s, 6, 4.2, CLOUD_FOG)
        rows = [(11, 0.8), (13.5, 0.8), (16, 0.8), (18.5, 0.8)]
        if full:
            rows += [(21.5, 0.9), (24.5, 0.9), (27.5, 0.9), (30.5, 0.9)]
        fog_bands(pa, ph, 0 if full else 2, W - 1 if full else 29, rows)
    elif cond in ("rain", "pouring"):
        heavy = cond == "pouring"
        if full:
            rain(pa, ph, 2 if heavy else 3, 26, -4, g, n=18 if heavy else 12, speeds=(3, 4) if heavy else (2, 3),
                 length=5.0 if heavy else 3.5, slant=0.25 if heavy else 0.15)
        else:
            rain(pa, ph, 8 if heavy else 9, 21, 9, g, n=10 if heavy else 6, speeds=(7, 8, 9) if heavy else (5, 6, 7),
                 length=4.0 if heavy else 3.0, slant=0.4 if heavy else 0.25)
        puff(pa, 16 + 0.7 * s, 6.5, 5, CLOUD_STORM if heavy else CLOUD_RAIN)
    elif cond == "storm":
        f = _flash(ph)
        if f:
            for y in range(W if full else 20):
                for x in range(W):
                    blend(pa, x, y, (45, 40, 90), f * 0.45 * (1 - y / (40 if full else 24)))
        if full:
            rain(pa, ph, 3, 25, -4, g, n=13, speeds=(2, 3, 4), length=3.5, slant=0.2, seed=4)
        else:
            rain(pa, ph, 9, 21, 9, g, n=6, speeds=(6, 7, 8), length=3.0, slant=0.35, seed=4)
        if f > 0.3:
            bolt(pa, _BOLT, f)
        puff(pa, 16 + 0.6 * s, 6, 5.2, CLOUD_STORM, flash=f)
    elif cond == "snow":
        if full:
            snow(pa, ph, 1, 30, 10, g, n=14, speeds=(1,))
        else:
            snow(pa, ph, 9, 23, 10, g, n=7)
        puff(pa, 16 + 0.7 * s, 6.5, 5, CLOUD_SNOW)
    elif cond == "sleet":
        if full:
            rain(pa, ph, 3, 25, -4, g, n=7, speeds=(2, 3), seed=5)
            snow(pa, ph, 2, 29, 10, g, n=7, speeds=(1,), seed=6)
        else:
            rain(pa, ph, 9, 21, 9, g, n=4, seed=5)
            snow(pa, ph, 10, 22, 10, g, n=4, seed=6)
        puff(pa, 16 + 0.7 * s, 6.5, 5, CLOUD_RAIN)
    elif cond == "hail":
        if full:
            hail(pa, ph, 2, 29, -2, g - 1, n=9, speeds=(2, 3))
        else:
            hail(pa, ph, 9, 23, 10, g)
        puff(pa, 16 + 0.7 * s, 6.5, 5, CLOUD_GREY)
    elif cond == "windy":
        rows = [(4, 9, 1, 0.0), (9, 12, 2, 0.4), (14, 8, 1, 0.55), (18, 10, 2, 0.9)]
        if full:
            rows += [(23, 11, 1, 0.2), (28, 9, 2, 0.7)]
        gusts(pa, ph, rows)
        leaf(pa, ph, 0, W - 1, 11)
    else:
        warning(pa, d, 16, ph)


def weather_frame(cond, temp, ph, unit="°C", full=False, backing="outline"):
    im, d = canvas()
    weather_icon(im.load(), d, cond, ph, full)
    draw_temp(d, temp, unit=unit, backing=backing if full else None, pa=im.load())
    return im


def mini_icon(pa, d, cond, cx, cy, ph):
    """A ~10x10 icon for the forecast."""
    s = math.sin(TAU * ph)
    if cond == "sunny":
        sun_glow(pa, cx, cy, 2.2, ph, ray_in=1.2, ray_out=(2.6, 1.8), halo=1.4)
    elif cond == "night":
        moon_glow(pa, cx, cy, 3.2, ph)
    elif cond == "partly":
        sun_glow(pa, cx - 1.8, cy - 1.5, 1.8, ph, ray_in=1.0, ray_out=(2.2, 1.5), halo=1.2)
        puff(pa, cx + 1 + 0.4 * s, cy + 1.5, 2.2)
    elif cond in ("rain", "pouring", "sleet", "storm"):
        f = _flash(ph) if cond == "storm" else 0.0
        rain(pa, ph, cx - 3.5, cx + 3.5, cy - 1, cy + 5, n=4 if cond == "pouring" else 3, length=2.0,
             slant=0.2, splash=False, seed=7)
        if cond == "sleet":
            snow(pa, ph, cx - 2, cx + 2, cy, cy + 5, n=2, seed=8)
        if f > 0.3:
            bolt(pa, [(cx + 0.5, cy), (cx - 1, cy + 2), (cx + 1, cy + 3), (cx - 0.5, cy + 5)], f)
        puff(pa, cx, cy - 1.5, 2.3, CLOUD_STORM if cond in ("storm", "pouring") else CLOUD_RAIN, flash=f)
    elif cond == "snow":
        snow(pa, ph, cx - 3.5, cx + 3.5, cy, cy + 5, n=3, speeds=(1,), seed=9)
        puff(pa, cx, cy - 1.5, 2.3, CLOUD_SNOW)
    elif cond == "hail":
        hail(pa, ph, cx - 3.5, cx + 3.5, cy, cy + 5, n=3)
        puff(pa, cx, cy - 1.5, 2.3, CLOUD_GREY)
    elif cond == "fog":
        fog_bands(pa, ph, cx - 4, cx + 4, [(cy - 2, 0.8), (cy + 0.5, 0.8), (cy + 3, 0.8)])
    elif cond == "windy":
        gusts(pa, ph, [(cy - 2, 5, 1, 0.0), (cy + 1, 6, 2, 0.5), (cy + 4, 4, 1, 0.3)], cx - 4, cx + 4)
    elif cond == "exceptional":
        col = lerp((255, 140, 20), (255, 215, 60), 0.5 + 0.5 * math.sin(TAU * ph * 2))
        rect(d, cx, cy - 3, cx, cy + 1, col)
        px(d, cx, cy + 3, col)
    else:
        puff(pa, cx, cy, 2.6, CLOUD_GREY)


def weather(condition: str, temperature: float, unit: str = "°C", frames: int = WEATHER_FRAMES) -> Frames:
    """Animated icon for the condition with the temperature underneath, looping seamlessly."""
    cond = normalise_condition(condition)
    temp = round(temperature)
    return [weather_frame(cond, temp, t / frames, unit) for t in range(frames)], round(WEATHER_LOOP_MS / frames)


def forecast(days: list[tuple[str, float, float | None]], unit: str = "°C", frames: int = WEATHER_FRAMES) -> Frames:
    """Up to three days side by side: icon, high, low. `days` = [(condition, high, low), ...]."""
    days = days[:3]
    n, frames = frames, []
    for t in range(n):
        im, d = canvas()
        pa = im.load()
        for i, (cond, hi, lo) in enumerate(days):
            cx = 5 + i * 11
            mini_icon(pa, d, normalise_condition(cond), cx, 7, t / n)
        # dividers drawn after the icons so a wide icon doesn't run into its neighbour
        for x in (10, 21):
            for y in range(31):
                pa[x, y] = (0, 0, 0)
            for y in range(2, 30):
                px(d, x, y, (35, 35, 50))
        for i, (cond, hi, lo) in enumerate(days):
            cx = 5 + i * 11
            hs = str(round(hi))
            text(d, hs, cx - text_w(hs) // 2, 16, temp_col(round(hi), unit))
            if lo is not None:
                ls = str(round(lo))
                text(d, ls, cx - text_w(ls) // 2, 23, dim((120, 170, 255), 0.9))
        rect(d, 1, 31, 9, 31, (255, 255, 255))  # "today" marker
        frames.append(im)
    return frames, round(WEATHER_LOOP_MS / n)


# ---------- text ----------

TEXT_COLOR_EFFECTS = ["none", "rainbow", "cycle"]
TEXT_MOTIONS = ["none", "bounce", "wave"]
TEXT_LOOP_MS = 2000  # one cycle of the text effects; a message that fits loops over exactly this
MIN_FRAME_MS = 30  # shortest frame the lights take (payload.MIN_DURATION_MS)
SPARKLE_COLORS = ((255, 215, 60), (255, 110, 190), (120, 220, 255), (255, 255, 255))
SPARKLE_LIFE_MS = 1000
SPARKLE_SIZES = (-1, 0, 1, 2, 1, 0, -1, -1)  # over one life: off, dot, small, big, small, dot, off


def _mix(a, b):
    """A small deterministic hash of two ints (the same frames for the same message, so nothing re-uploads)."""
    h = (a * 73856093) ^ (b * 19349663) ^ 0x5BD1E995
    h = (h ^ (h >> 13)) * 0x5BD1E995 & 0xFFFFFFFF
    return h ^ (h >> 15)


def sparkle(d, x, y, size, c):
    """A twinkle: a dot (size 0), a plus with a white centre (1), or a bigger plus (2)."""
    if size <= 0:
        px(d, x, y, dim(c, 0.7))
        return
    px(d, x, y, (255, 255, 255))
    for r in range(1, size + 1):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
            px(d, x + dx, y + dy, c if r == 1 else dim(c, 0.5))


def _sparkles(d, t_ms, bands, n=6, loop_ms=None):
    """n sparkles twinkling at places that change every life, in the given row bands (y0, y1).
    With loop_ms, the sparkles repeat after that long so the animation loops seamlessly."""
    rows = [y for y0, y1 in bands for y in range(y0, y1 + 1)]
    if not rows:
        return
    for k in range(n):
        t = t_ms + k * SPARKLE_LIFE_MS // n
        if loop_ms:
            t %= loop_ms
        life, into = divmod(t, SPARKLE_LIFE_MS)
        size = SPARKLE_SIZES[into * len(SPARKLE_SIZES) // SPARKLE_LIFE_MS]
        if size < 0:
            continue
        h = _mix(k, life)
        sparkle(d, 1 + h % (W - 2), rows[(h >> 8) % len(rows)], size, SPARKLE_COLORS[(h >> 16) % len(SPARKLE_COLORS)])


def scrolling_text(message: str, color=None, background=(0, 0, 0), large: bool = True,
                   frame_ms: int = 50, max_frames: int = 100, color_effect: str | None = None,
                   motion: str = "none", sparkles: bool = False) -> Frames:
    """Text centred if it fits, otherwise scrolled right-to-left one pixel every frame_ms (in bigger steps
    when that would take more than max_frames frames).

    color_effect: "none" (color), "rainbow" (each letter a different colour, flowing along the message) or
    "cycle" (the whole message changing colour); by default a rainbow without a color, else none.
    motion: "bounce" hops the line up and down as it goes, "wave" ripples the letters.
    sparkles: stars twinkle above and below the text."""
    scale = 2 if large else 1
    message = message.strip() or " "
    width = text_w(message, scale)
    y = (W - 5 * scale) // 2
    effect = color_effect or ("rainbow" if color is None else "none")
    color = tuple(color) if color is not None else (255, 255, 255)
    amp = 3 if large else 2
    bands = [(1, y - amp - 2), (y + 5 * scale + amp + 1, W - 2)]

    if width <= W:
        x0, step = (W - width) // 2, 0
        if effect == "none" and motion == "none" and not sparkles:
            im, d = canvas(background)
            text(d, message, x0, y, color, scale=scale)
            return [im], 1000
        n = max(8, min(max_frames, round(TEXT_LOOP_MS / max(frame_ms, MIN_FRAME_MS))))
        ms, loop_ms = TEXT_LOOP_MS / n, TEXT_LOOP_MS
    else:
        x0, travel = W, width + W
        step = max(1, math.ceil(travel / max_frames), math.ceil(MIN_FRAME_MS / frame_ms))
        n, ms, loop_ms = math.ceil(travel / step), step * frame_ms, None

    frames = []
    for k in range(n):
        t_ms = round(k * ms)
        phase = 2 * math.pi * t_ms / TEXT_LOOP_MS
        if effect == "rainbow":
            col = (lambda i, p=phase: rainbow(p + i))
        elif effect == "cycle":
            col = rainbow(phase)
        else:
            col = color
        if motion == "bounce":
            dy = (lambda i, h=-round(amp * abs(math.sin(phase))): h)
        elif motion == "wave":
            dy = (lambda i, p=phase: round((amp - 1) * math.sin(2 * p - i * 0.9)))
        else:
            dy = None
        im, d = canvas(background)
        if sparkles:
            _sparkles(d, t_ms, bands, loop_ms=loop_ms)
        text(d, message, x0 - k * step, y, col, scale=scale, dy=dy)
        frames.append(im)
    return frames, round(ms)


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


def _wrap(words: list[str], width: int) -> list[str]:
    lines, cur = [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if text_w(cand) <= width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def reminder(day: int, when: str, time_label: str, summary: str, color=(255, 200, 60), size: str = "auto") -> Frames:
    """Calendar-page icon with the day number, when (TDAY / TMRW / FRI) and time beside it; the event name
    below. size "auto": small text word-wrapped onto up to 3 lines, a line too wide for the panel sliding back
    and forth in place; falls back to "large" (one line of big text, scrolling) if it needs more lines."""
    summary = summary.strip().upper() or "EVENT"
    lines = _wrap(summary.split(), W) if size != "large" else []
    if not lines or len(lines) > 3:
        return _reminder_large(day, when, time_label, summary, color)

    def page(d):
        rect(d, 1, 1, 11, 2, (220, 40, 40))  # page header
        rect(d, 1, 3, 11, 10, (235, 235, 235))  # page
        num = str(day)
        text(d, num, 1 + (11 - text_w(num)) // 2, 5, (30, 30, 30))
        text(d, when, 14, 1, tuple(color))
        if time_label:
            text(d, time_label, 14, 7, dim(tuple(color), 0.7))

    y0 = 14 if len(lines) == 3 else 16 if len(lines) == 2 else 19
    overflow = [max(0, text_w(ln) - W) for ln in lines]
    travel = max(overflow)
    if travel == 0:
        im, d = canvas()
        page(d)
        for i, ln in enumerate(lines):
            text(d, ln, (W - text_w(ln)) // 2, y0 + i * 6, (255, 255, 255))
        return [im], 1000
    # Wide line(s): hold at the start, slide to show the end, hold, slide back (loops seamlessly).
    offsets = [0] * 10 + list(range(1, travel + 1)) + [travel] * 10 + list(range(travel - 1, 0, -1))
    frames = []
    for off in offsets:
        im, d = canvas()
        page(d)
        for i, ln in enumerate(lines):
            x = (W - text_w(ln)) // 2 if overflow[i] == 0 else -min(off, overflow[i])
            text(d, ln, x, y0 + i * 6, (255, 255, 255))
        frames.append(im)
    return frames, 150


def _reminder_large(day: int, when: str, time_label: str, summary: str, color) -> Frames:
    """One line of big text under the page, scrolling if it doesn't fit."""
    def top(d):
        rect(d, 1, 1, 13, 3, (220, 40, 40))  # page header
        rect(d, 1, 4, 13, 14, (235, 235, 235))  # page
        num = str(day)
        text(d, num, 1 + (13 - text_w(num)) // 2, 7, (30, 30, 30))
        text(d, when, 16, 3, tuple(color))
        if time_label:
            text(d, time_label, 16, 9, dim(tuple(color), 0.7))

    width = text_w(summary, 2)
    y = 19
    if width <= W - 2:
        im, d = canvas()
        top(d)
        text(d, summary, (W - width) // 2, y, (255, 255, 255), scale=2)
        return [im], 1000
    frames = []
    travel = width - W + 2
    step = max(2, math.ceil(travel / 70))  # stay under the 100-frame item limit
    for x in [1] * 12 + list(range(1, -travel - 1, -step)) + [-(width - W + 1)] * 12:
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
