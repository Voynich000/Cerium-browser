#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Builds Cerium's macOS icon files.

Run after `node branding/render.js` (which renders the app icon bitmaps),
with an unmodified Chromium source tree for the files that are recoloured:

  python3 branding/make_macos_icons.py --chromium-src <chromium src>

It writes:
- branding/macos/: Chromium's chrome/app/theme/chromium/mac/AppIcon.icon and
  Assets.xcassets with Cerium's colours: the macOS 26 icon layers (SVG, the
  same colour mapping as branding/cerium/logo.svg) and the flat bitmaps,
  recoloured pixel by pixel; macos/compile_icon.py compiles them into the
  Assets.car that macOS reads, which only Xcode's actool can do;
- branding/overlay/chrome/app/theme/chromium/mac/app.icns, built from the
  rendered AppIcon.appiconset bitmaps.
"""

import argparse
import shutil
from pathlib import Path

import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parent
_MAC = 'chrome/app/theme/chromium/mac'

# Chromium's logo colours and their Cerium replacements.
COLORS = {
    '#1967D2': '#8F969D',  # top segment: silver-gray
    '#669DF6': '#B8BEC4',  # lower-left segment: light silver-gray
    '#AECBFA': '#F7E7A6',  # right segment: pale yellow
    '#1A73E8': '#F28A2E',  # centre: orange
}


def _rgb(color):
    return np.array([int(color[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def recolor_svg(text):
    for old, new in COLORS.items():
        text = text.replace(old, new).replace(old.lower(), new)
    return text


def recolor_flat_png(image):
    """Recolours a flat rendering of the logo.

    Every pixel of such an image is one of the logo colours, possibly blended
    with white (anti-aliasing against the white ring) and with transparency.
    Each pixel is matched to the logo colour whose blend with white explains
    it best, and the same blend is applied to the replacement colour. The
    centre colour only occurs inside the white ring, so outside it a blend of
    two segment colours is never mistaken for the centre.
    """
    rgba = np.asarray(image.convert('RGBA'), dtype=float)
    rgb, alpha = rgba[..., :3], rgba[..., 3:]
    white = np.full(3, 255.0)
    height, width = rgb.shape[:2]
    y, x = np.mgrid[0:height, 0:width]
    # Distance from the centre, as a fraction of the logo's radius.
    radius = np.hypot(x - (width - 1) / 2, y - (height - 1) / 2) / (min(width, height) / 2)
    best_error = np.full(rgb.shape[:2], np.inf)
    result = rgb.copy()
    for old, new in COLORS.items():
        base, target = _rgb(old), _rgb(new)
        direction = base - white
        # Blend factor t of the logo colour against white, clipped to [0, 1].
        t = np.clip(((rgb - white) @ direction) / (direction @ direction), 0, 1)[..., None]
        error = np.linalg.norm(rgb - (white + t * direction), axis=-1)
        if old == '#1A73E8':
            # The centre disc has a radius of 52/128 of the logo's.
            error = np.where(radius > 0.45, np.inf, error)
        better = error < best_error
        best_error = np.where(better, error, best_error)
        result = np.where(better[..., None], white + t * (target - white), result)
    # Pure white and near-white pixels stay as they are.
    near_white = np.linalg.norm(rgb - white, axis=-1) < 6
    result = np.where(near_white[..., None], rgb, result)
    out = np.concatenate([np.clip(result, 0, 255), alpha], axis=-1).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--chromium-src', type=Path, required=True,
                        help='Unmodified Chromium source tree')
    args = parser.parse_args()
    source = args.chromium_src / _MAC
    target = _ROOT / 'macos'

    for name in ['AppIcon.icon/icon.json', 'Assets.xcassets/Contents.json',
                 'Assets.xcassets/AppIcon.appiconset/Contents.json']:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target / name)

    (target / 'AppIcon.icon' / 'Assets').mkdir(parents=True, exist_ok=True)
    for svg in sorted((source / 'AppIcon.icon' / 'Assets').glob('*.svg')):
        (target / 'AppIcon.icon' / 'Assets' / svg.name).write_text(recolor_svg(svg.read_text()))

    for name in ['AppIcon.icon/Assets/ring.png',
                 'Assets.xcassets/Icon.iconset/icon_256x256.png',
                 'Assets.xcassets/Icon.iconset/icon_256x256@2x.png']:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        recolor_flat_png(Image.open(source / name)).save(target / name, optimize=True)

    appiconset = target / 'Assets.xcassets' / 'AppIcon.appiconset'
    largest = Image.open(appiconset / 'appicon_1024.png').convert('RGBA')
    overlay = _ROOT / 'overlay' / _MAC
    overlay.mkdir(parents=True, exist_ok=True)
    largest.save(overlay / 'app.icns',
                 append_images=[
                     Image.open(appiconset / f'appicon_{size}.png').convert('RGBA')
                     for size in (16, 32, 64, 128, 256, 512)
                 ])
    print(f'wrote macOS icons to {target} and {overlay}')


if __name__ == '__main__':
    main()
