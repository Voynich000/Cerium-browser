#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Renames the browser from "Chromium" to "Cerium": UI strings and logos.

Chromium keeps every string that names the product in "branded" resource
files (chrome/app/chromium_strings.grd with settings_chromium_strings.grdp,
and components/components_chromium_strings.grd), translated into each locale
in .xtb files. This script replaces the product name in those messages.

Translations are matched to messages by a fingerprint of the English text,
so changing the English text would orphan every translation. The script
therefore uses grit, Chromium's resource tool from the source tree, to
compute each changed message's old and new ID, and moves the translations
over to the new IDs (renaming the product in them too).

"The Chromium Authors" (copyright attribution), "Chromium developers" and
ChromiumOS names are left alone.

It also copies the Cerium logos from branding/overlay/ over Chromium's
(see branding/render.js). Every target must already exist with the same
pixel size, so a Chromium update that moves or resizes a logo stops the
build instead of leaving a Chromium logo behind.

Run on a source tree after patching and domain substitution:
  rebrand.py <chromium src>
It is idempotent: a second run finds nothing to rename.
"""

import argparse
import re
import shutil
import struct
import sys
from pathlib import Path

_OVERLAY = Path(__file__).resolve().parent.parent / 'branding' / 'overlay'

OLD_NAME = 'Chromium'
NEW_NAME = 'Cerium'

# "Chromium" as a word of its own: not part of e.g. "ChromiumOS", but still
# matched next to non-Latin text such as "Chromiumを". Attribution and
# developer-facing mentions keep the upstream name.
_NAME = re.compile(r'(?<![A-Za-z])Chromium(?![A-Za-z])(?! Authors| developers)')

# (grd file, directory of its .xtb files, .xtb file name prefix)
_RESOURCES = [
    ('chrome/app/chromium_strings.grd', 'chrome/app/resources', 'chromium_strings_'),
    ('components/components_chromium_strings.grd', 'components/strings',
     'components_chromium_strings_'),
]
# grd and grdp files whose <message> texts are renamed.
_MESSAGE_FILES = [
    'chrome/app/chromium_strings.grd',
    'chrome/app/settings_chromium_strings.grdp',
    'components/components_chromium_strings.grd',
]

_MESSAGE = re.compile(r'(<message\b[^>]*>)(.*?)(</message>)', re.DOTALL)
_TRANSLATION = re.compile(r'<translation id="(\d+)">(.*?)</translation>', re.DOTALL)
_TRANSLATION_IDS = re.compile(r'<translation id="(\d+)">')


def _rename(text):
    return _NAME.sub(NEW_NAME, text)


def _message_ids(src, grd):
    """Returns the IDs of all messages in |grd| (all platforms), in order."""
    from grit import grd_reader  # pylint: disable=import-outside-toplevel
    from grit.node import message  # pylint: disable=import-outside-toplevel
    root = grd_reader.Parse(str(src / grd), debug=False, skip_validation_checks=True)
    return [
        node.GetCliques()[0].GetId() for node in root.Preorder()
        if isinstance(node, message.MessageNode)
    ]


def _rename_messages(path):
    """Renames the product in the <message> bodies of a grd/grdp file."""
    text = path.read_text(encoding='utf-8')
    renamed = _MESSAGE.sub(lambda m: m.group(1) + _rename(m.group(2)) + m.group(3), text)
    if renamed != text:
        path.write_text(renamed, encoding='utf-8')


def _rewrite_translations(xtb, id_map):
    """Moves translations to their messages' new IDs. Returns the count."""
    text = xtb.read_text(encoding='utf-8')
    existing = set(_TRANSLATION_IDS.findall(text))
    moved = 0

    def replace(match):
        nonlocal moved
        old_id, translation = match.groups()
        new_id = id_map.get(old_id)
        if new_id is None or new_id in existing:
            return match.group(0)
        moved += 1
        return f'<translation id="{new_id}">{_rename(translation)}</translation>'

    rewritten = _TRANSLATION.sub(replace, text)
    if rewritten != text:
        xtb.write_text(rewritten, encoding='utf-8')
    return moved


def _translated_count(xtb_dir, prefix, ids):
    """Returns {locale file: number of messages in |ids| it translates}."""
    wanted = set(ids)
    return {
        xtb.name: len(wanted & set(_TRANSLATION_IDS.findall(xtb.read_text(encoding='utf-8'))))
        for xtb in sorted(xtb_dir.glob(prefix + '*.xtb'))
    }


def _rename_branding(src):
    branding = src / 'chrome' / 'app' / 'theme' / 'chromium' / 'BRANDING'
    lines = []
    for line in branding.read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if key.startswith('PRODUCT_'):
            value = _rename(value)
        lines.append(f'{key}={value}' if _ else line)
    branding.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def _png_size(path):
    header = path.read_bytes()[:24]
    if header[:8] != b'\x89PNG\r\n\x1a\n':
        raise SystemExit(f'{path}: not a PNG file')
    return struct.unpack('>II', header[16:24])


def _apply_logos(src):
    """Copies branding/overlay/ over the Chromium logos. Returns the count."""
    copied = 0
    for logo in sorted(p for p in _OVERLAY.rglob('*') if p.is_file()):
        relative = logo.relative_to(_OVERLAY)
        target = src / relative
        if not target.is_file():
            raise SystemExit(f'rebrand: {relative} is not in the source tree any more')
        if logo.suffix == '.png' and _png_size(logo) != _png_size(target):
            raise SystemExit(f'rebrand: {relative} is {_png_size(target)} in the source '
                             f'tree but {_png_size(logo)} in branding/overlay')
        shutil.copyfile(logo, target)
        copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('src', type=Path, help='Chromium source tree')
    args = parser.parse_args()
    src = args.src.resolve()
    sys.path.insert(0, str(src / 'tools' / 'grit'))

    old_ids = {grd: _message_ids(src, grd) for grd, _, _ in _RESOURCES}
    before = {
        grd: _translated_count(src / xtb_dir, prefix, old_ids[grd])
        for grd, xtb_dir, prefix in _RESOURCES
    }

    for name in _MESSAGE_FILES:
        _rename_messages(src / name)
    _rename_branding(src)

    for grd, xtb_dir, prefix in _RESOURCES:
        new_ids = _message_ids(src, grd)
        if len(new_ids) != len(old_ids[grd]):
            raise SystemExit(f'{grd}: message count changed while renaming')
        id_map = {old: new for old, new in zip(old_ids[grd], new_ids) if old != new}
        moved = sum(
            _rewrite_translations(xtb, id_map)
            for xtb in sorted((src / xtb_dir).glob(prefix + '*.xtb')))
        after = _translated_count(src / xtb_dir, prefix, new_ids)
        lost = {locale: before[grd][locale] - count
                for locale, count in after.items() if count < before[grd][locale]}
        if lost:
            raise SystemExit(f'{grd}: translations lost while renaming: {lost}')
        print(f'rebrand: {grd}: renamed {len(id_map)} messages, '
              f'moved {moved} translations in {len(after)} locales')

    print(f'rebrand: replaced {_apply_logos(src)} logo files')


if __name__ == '__main__':
    main()
