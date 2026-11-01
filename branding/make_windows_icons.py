#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Builds Cerium's Windows icons in branding/overlay/.

Run with an unmodified Chromium source tree:

  python3 branding/make_windows_icons.py --chromium-src <chromium src>

Cerium's logo is Chromium's logo shape in other colours, so each Windows image
that shows the logo is converted pixel by pixel: every pixel that belongs to
Chromium's blue logo is replaced by the same pixel of Cerium's logo, drawn at
the same place and size; the rest (the document page, the PDF badge, the
margins) is kept. It writes, under chrome/app/theme/chromium/win/:
- chromium.ico, the app icon (taskbar, Start menu, desktop shortcut);
- chromium_doc.ico and chromium_pdf.ico, the icons of HTML and PDF files;
- tiles/Logo.png and tiles/SmallLogo.png, the Start menu tiles.
"""

import argparse
import colorsys
from pathlib import Path

from PIL import Image

_ROOT = Path(__file__).resolve().parent
_WIN = 'chrome/app/theme/chromium/win'
_LOGO = _ROOT / 'cerium' / 'logo_512.png'
_ICONS = ['chromium.ico', 'chromium_doc.ico', 'chromium_pdf.ico']
_TILES = ['tiles/Logo.png', 'tiles/SmallLogo.png']


def _is_logo_pixel(red, green, blue, alpha):
    """Whether a pixel is one of the bright blues of Chromium's logo, or a blend
    of them with white or transparency."""
    if alpha == 0:
        return False
    hue, saturation, value = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
    return saturation > 0.05 and value > 0.6 and 190 / 360 <= hue <= 240 / 360


def _is_badge_pixel(red, green, blue, alpha):
    """Whether a pixel belongs to a dark badge drawn over the logo (PDF)."""
    return alpha > 128 and max(red, green, blue) < 115


def _bbox(points):
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def convert(image, logo):
    """Returns |image| with Chromium's logo in it replaced by |logo|."""
    image = image.convert('RGBA')
    pixels = image.load()
    width, height = image.size
    is_logo = {(x, y) for y in range(height) for x in range(width)
               if _is_logo_pixel(*pixels[x, y])}
    # Ignore isolated pixels (dithering in Chromium's small frames) when
    # locating the logo.
    logo_pixels = [(x, y) for x, y in is_logo
                   if sum((x + dx, y + dy) in is_logo
                          for dx in (-1, 0, 1) for dy in (-1, 0, 1)) >= 4]
    if not logo_pixels:
        raise SystemExit('no Chromium logo found in the image')
    left, top, right, bottom = _bbox(logo_pixels)
    # The logo is a circle; a badge may cover its bottom right corner, so its
    # size comes from the larger extent, measured from the top left.
    size = max(right - left, bottom - top) + 1
    cerium = logo.resize((size, size), Image.LANCZOS).load()
    circle = [(x, y) for y in range(top, min(top + size, height))
              for x in range(left, min(left + size, width))]
    badge = [(x, y) for x, y in circle if _is_badge_pixel(*pixels[x, y])]
    badge_box = _bbox(badge) if badge else None
    result = image.copy()
    out = result.load()
    replaced = set()
    for x, y in circle:
        if (badge_box and badge_box[0] <= x <= badge_box[2] and
                badge_box[1] <= y <= badge_box[3] and not _is_logo_pixel(*pixels[x, y])):
            continue  # keep the badge and its text, which are drawn over the logo
        red, green, blue, alpha = cerium[x - left, y - top]
        if alpha == 0:
            continue  # outside the circle: keep the background
        if pixels[x, y][3] == 255 and alpha < 255:
            # On an opaque background (the document page), blend with white.
            red, green, blue = (round(c * alpha / 255 + 255 * (1 - alpha / 255))
                                for c in (red, green, blue))
            alpha = 255
        out[x, y] = (red, green, blue, alpha)
        replaced.add((x, y))
    # Any blue left outside the circle (anti-aliasing of a slightly larger
    # original) becomes Cerium's silver-gray, as light as it was.
    silver = (0x8F, 0x96, 0x9D)
    for x, y in is_logo - replaced:
        if _is_logo_pixel(*out[x, y]):
            _, saturation, _ = colorsys.rgb_to_hsv(*(c / 255 for c in out[x, y][:3]))
            strength = min(1.0, saturation / 0.6)
            out[x, y] = tuple(round(255 + (c - 255) * strength) for c in silver) + (out[x, y][3],)
    return result


def convert_ico(path, logo):
    """Converts every frame of an .ico file; returns (frames, sizes)."""
    ico = Image.open(path)
    sizes = sorted(ico.info['sizes'])
    frames = []
    for size in sizes:
        ico.size = size
        ico.load()
        frames.append(convert(ico.copy(), logo))
    return frames, sizes


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--chromium-src', type=Path, required=True,
                        help='Unmodified Chromium source tree')
    args = parser.parse_args()
    source = args.chromium_src / _WIN
    target = _ROOT / 'overlay' / _WIN
    logo = Image.open(_LOGO).convert('RGBA')

    for name in _ICONS:
        frames, sizes = convert_ico(source / name, logo)
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        largest = frames[-1]
        largest.save(target / name, format='ICO', sizes=sizes,
                     append_images=frames[:-1], bitmap_format='png')
    for name in _TILES:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        convert(Image.open(source / name), logo).save(target / name, optimize=True)
    print(f'wrote Windows icons to {target}')


if __name__ == '__main__':
    main()
