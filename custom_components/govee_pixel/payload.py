"""Turn images / animated GIFs into the H8630 graffiti effect payload.

Format (reverse-engineered from the Govee Home 7.7.02 Android app):

  effect = 0x50 0x5E | bg RGB(3) | speed u8 | mode u8 | bg group | animation group | fg group
  group  = count u8, Layer * count     (mode != 0xFF => bg/fg groups are a single 0x00)
  Layer  = totalLen LE16 | type 0x00 | pngLen LE16 | png | common(8)
  common = c, d, e(100), f(1), duration_ms LE16, g, h

Mode 1 plays the animation frames (mode 0 only shows the first frame).
Pure Pillow, no Home Assistant imports, so it can be tested standalone.
"""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageSequence

MAX_FRAMES = 100
MIN_DURATION_MS = 30
MAX_DURATION_MS = 30000
DEFAULT_DURATION_MS = 100

FIT_CONTAIN = "contain"  # letterbox, keep aspect ratio
FIT_COVER = "cover"  # fill, crop overflow
FIT_STRETCH = "stretch"

RESAMPLE = {
    "nearest": Image.Resampling.NEAREST,
    "box": Image.Resampling.BOX,
    "bilinear": Image.Resampling.BILINEAR,
    "lanczos": Image.Resampling.LANCZOS,
}


@dataclass
class Frame:
    image: Image.Image  # RGB, matrix-sized
    duration: int  # ms


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _layer(png: bytes, duration: int) -> bytes:
    common = bytes([0, 0, 100, 1]) + struct.pack("<H", duration) + bytes([0, 0])
    body = b"\x00" + struct.pack("<H", len(png)) + png + common
    return struct.pack("<H", len(body)) + body


def build_effect(frames: list[Frame], background: tuple[int, int, int] = (0, 0, 0), speed: int = 100) -> bytes:
    """Encode matrix-sized frames into the effect bytes the light downloads."""
    if not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError(f"1-{MAX_FRAMES} frames supported, got {len(frames)}")
    layers = b"".join(_layer(_png(f.image), f.duration) for f in frames)
    return (b"\x50\x5e" + bytes(background) + bytes([speed, 1])
            + b"\x00" + bytes([len(frames)]) + layers + b"\x00")


def _fit(img: Image.Image, size: int, fit: str, resample: Image.Resampling,
         background: tuple[int, int, int]) -> Image.Image:
    """Flatten onto the background and scale to size x size."""
    rgba = img.convert("RGBA")
    if fit == FIT_STRETCH:
        scaled = rgba.resize((size, size), resample)
        offset = (0, 0)
    else:
        w, h = rgba.size
        scale = (min if fit == FIT_CONTAIN else max)(size / w, size / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        scaled = rgba.resize((nw, nh), resample)
        offset = ((size - nw) // 2, (size - nh) // 2)
    canvas = Image.new("RGBA", (size, size), background + (255,))
    canvas.alpha_composite(scaled, (max(0, offset[0]), max(0, offset[1])),
                           (max(0, -offset[0]), max(0, -offset[1])))
    return canvas.convert("RGB")


def frames_from_image(data: bytes, size: int = 32, fit: str = FIT_CONTAIN, resample: str = "lanczos",
                      background: tuple[int, int, int] = (0, 0, 0), duration: int | None = None,
                      max_frames: int = MAX_FRAMES) -> list[Frame]:
    """Decode a still or animated image (PNG, JPEG, GIF, WebP, APNG...) into matrix frames.

    `duration` overrides per-frame timing (and is the timing used for still images).
    Consecutive identical frames are merged; animations longer than `max_frames` are
    evenly decimated with their time folded into the kept frames.
    """
    max_frames = max(1, min(max_frames, MAX_FRAMES))
    rs = RESAMPLE[resample]
    src = Image.open(io.BytesIO(data))
    frames: list[Frame] = []
    for raw in ImageSequence.Iterator(src):
        d = duration or raw.info.get("duration") or DEFAULT_DURATION_MS
        img = _fit(raw, size, fit, rs, background)
        if frames and ImageChops.difference(frames[-1].image, img).getbbox() is None:
            frames[-1].duration += int(d)
        else:
            frames.append(Frame(img, int(d)))
    if len(frames) > max_frames:
        step = len(frames) / max_frames
        kept: list[Frame] = []
        for i in range(max_frames):
            group = frames[int(i * step):int((i + 1) * step)]
            kept.append(Frame(group[0].image, sum(f.duration for f in group)))
        frames = kept
    for f in frames:
        f.duration = max(MIN_DURATION_MS, min(MAX_DURATION_MS, f.duration))
    return frames


def preview_gif(frames: list[Frame], scale: int = 8) -> bytes:
    """Upscaled GIF of what was sent (for debugging / an image entity)."""
    big = [f.image.resize((f.image.width * scale, f.image.height * scale), Image.Resampling.NEAREST)
           for f in frames]
    buf = io.BytesIO()
    big[0].save(buf, format="GIF", save_all=True, append_images=big[1:],
                duration=[f.duration for f in frames], loop=0, disposal=1)
    return buf.getvalue()
