#!/usr/bin/env python3
"""Render the integration's built-in 32x32 animations to GIF files (needs only Pillow).

Handy for pre-building GIFs into /config/www/ for `govee_pixel.show_image`, or for previewing
designs without a light. The same drawing code backs the show_weather / show_text /
show_animation actions.

  python3 scripts/generate_gifs.py all                     # every design + preview_sheet.png
  python3 scripts/generate_gifs.py weather rainy 17
  python3 scripts/generate_gifs.py weather sunny 81 --unit °F
  python3 scripts/generate_gifs.py forecast sunny:24:13 partlycloudy:21:12 rainy:16:9
  python3 scripts/generate_gifs.py text "Dinner time!" --color 255,200,0
  python3 scripts/generate_gifs.py text "Well done!" --color-effect cycle --motion bounce --sparkles
  python3 scripts/generate_gifs.py animation bedtime

Output goes to ./pixel_displays/ (change with -o).
"""
import argparse
import importlib.util
import math
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_SRC = Path(__file__).resolve().parent.parent / "custom_components" / "govee_pixel" / "animations.py"
_spec = importlib.util.spec_from_file_location("govee_pixel_animations", _SRC)
anim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(anim)


def save_gif(frames_ms, out_dir, name):
    frames, ms = frames_ms
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=ms, loop=0, disposal=2)
    print(path)
    return path


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "text"


def contact_sheet(paths, out_dir, cols=5, scale=5):
    font = ImageFont.load_default()
    tile, pad = anim.W * scale, 14
    rows = math.ceil(len(paths) / cols)
    sheet = Image.new("RGB", (cols * (tile + 8) + 8, rows * (tile + pad + 8) + 8), (28, 28, 34))
    sd = ImageDraw.Draw(sheet)
    for i, p in enumerate(paths):
        im = Image.open(p)
        im.seek(min(3, im.n_frames - 1) if not os.path.basename(p).startswith("text_") else im.n_frames // 2)
        x = 8 + (i % cols) * (tile + 8)
        y = 8 + (i // cols) * (tile + pad + 8)
        sheet.paste(im.convert("RGB").resize((tile, tile), Image.NEAREST), (x, y))
        sd.text((x, y + tile + 2), os.path.basename(p)[:-4][:26], fill=(220, 220, 230), font=font)
    out = os.path.join(out_dir, "preview_sheet.png")
    sheet.save(out)
    print(out)


def build_all(out_dir):
    made = []
    samples = {"sunny": 26, "clear-night": 15, "partlycloudy": 21, "cloudy": 18, "fog": 9, "rainy": 14,
               "pouring": 12, "lightning-rainy": 23, "snowy": 2, "snowy-rainy": 1, "hail": 6, "windy": 17,
               "exceptional": 40}
    for cond, temp in samples.items():
        made.append(save_gif(anim.weather(cond, temp), out_dir, f"weather_{slug(cond)}_{temp}.gif"))
    made.append(save_gif(anim.forecast([("sunny", 24, 13), ("partlycloudy", 21, 12), ("rainy", 16, 9)]),
                         out_dir, "weather_3day_forecast.gif"))
    for name in anim.SCENES:
        made.append(save_gif(anim.scene(name), out_dir, f"{name}.gif"))
    made.append(save_gif(anim.scrolling_text("Hello!"), out_dir, "text_hello.gif"))
    contact_sheet(made, out_dir)


def rgb(s):
    parts = [int(v) for v in s.split(",")]
    if len(parts) != 3 or not all(0 <= v <= 255 for v in parts):
        raise argparse.ArgumentTypeError("expected R,G,B with values 0-255")
    return tuple(parts)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="pixel_displays", help="output directory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("all")
    w = sub.add_parser("weather")
    w.add_argument("condition", help="HA condition (sunny, rainy, ...) or icon name")
    w.add_argument("temperature", type=float)
    w.add_argument("--unit", default="°C", choices=["°C", "°F"])
    f = sub.add_parser("forecast")
    f.add_argument("days", nargs="+", help="condition:high:low, up to three")
    f.add_argument("--unit", default="°C", choices=["°C", "°F"])
    t = sub.add_parser("text")
    t.add_argument("message")
    t.add_argument("--color", type=rgb, help="R,G,B (default: rainbow)")
    t.add_argument("--small", action="store_true")
    t.add_argument("--color-effect", choices=anim.TEXT_COLOR_EFFECTS)
    t.add_argument("--motion", choices=anim.TEXT_MOTIONS, default="none")
    t.add_argument("--sparkles", action="store_true")
    t.add_argument("--frame-ms", type=int, default=50, help="ms per pixel of scrolling (default 50)")
    t.add_argument("--max-frames", type=int, default=600, help="100 for Govee's own firmware, 600 for pixeld")
    a = sub.add_parser("animation")
    a.add_argument("name", choices=list(anim.SCENES))
    args = ap.parse_args()

    if args.cmd == "all":
        build_all(args.out)
    elif args.cmd == "weather":
        temp = round(args.temperature)
        save_gif(anim.weather(args.condition, temp, args.unit), args.out,
                 f"weather_{slug(args.condition)}_{temp}.gif")
    elif args.cmd == "forecast":
        days = []
        for spec in args.days[:3]:
            cond, hi, *lo = spec.split(":")
            days.append((cond, float(hi), float(lo[0]) if lo else None))
        save_gif(anim.forecast(days, args.unit), args.out, "weather_3day_forecast.gif")
    elif args.cmd == "text":
        save_gif(anim.scrolling_text(args.message, args.color, large=not args.small, frame_ms=args.frame_ms,
                                     max_frames=args.max_frames, color_effect=args.color_effect,
                                     motion=args.motion, sparkles=args.sparkles), args.out,
                 f"text_{slug(args.message)[:40]}.gif")
    else:
        save_gif(anim.scene(args.name), args.out, f"{args.name}.gif")


if __name__ == "__main__":
    main()
