#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Builds the extensions that ship with Cerium (uBlock Origin, Dark Reader).

For every entry of extensions/extensions.json this downloads the pinned
release, checks its SHA-256, applies the listed tweaks, packs it as a CRX3
signed with a per-extension key and writes

  <output>/bundled_extensions/<name>.crx
  <output>/bundled_extensions/<extension id>.json   {external_crx, external_version}

which the browser installs on startup (see patches/cerium/add-bundled-extensions.patch),
plus their licenses and source links in <output>/bundled_extensions/licenses/.
It also writes <output>/initial_preferences so that new profiles get the
extensions pinned to the toolbar.

The extension ID is derived from the signing key, and the browser keeps an
extension's settings under its ID. Keep the keys (default: keys/, ignored by
git; in CI: repository secrets) so that IDs stay the same across releases.
Never publish them.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crx3  # pylint: disable=wrong-import-position

_ROOT = Path(__file__).resolve().parent.parent
_BUNDLE_DIR_NAME = 'bundled_extensions'


def _log(message):
    print(f'bundle_extensions: {message}', file=sys.stderr)


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _download(entry, cache_dir):
    target = cache_dir / Path(entry['url']).name
    if target.exists() and _sha256(target) == entry['sha256']:
        return target
    _log(f'downloading {entry["url"]}')
    partial = target.with_suffix(target.suffix + '.partial')
    with urllib.request.urlopen(entry['url'], timeout=120) as response, \
            open(partial, 'wb') as file:
        shutil.copyfileobj(response, file)
    actual = _sha256(partial)
    if actual != entry['sha256']:
        partial.unlink()
        raise SystemExit(f'{entry["name"]}: SHA-256 mismatch: expected {entry["sha256"]}, '
                         f'got {actual}')
    partial.replace(target)
    return target


def _extract(archive, root, destination):
    with zipfile.ZipFile(archive) as zip_file:
        for member in zip_file.infolist():
            path = Path(member.filename)
            if path.is_absolute() or '..' in path.parts:
                raise SystemExit(f'{archive}: unsafe path {member.filename}')
        zip_file.extractall(destination)
    extension_dir = destination / root if root else destination
    if not (extension_dir / 'manifest.json').is_file():
        raise SystemExit(f'{archive}: no manifest.json in "{root}"')
    return extension_dir


def _apply_tweaks(entry, extension_dir):
    for tweak in entry.get('tweaks', []):
        path = extension_dir / tweak['file']
        text = path.read_text(encoding='utf-8')
        found = text.count(tweak['find'])
        if found != tweak['count']:
            raise SystemExit(f'{entry["name"]}: expected {tweak["count"]} occurrence(s) of '
                             f'{tweak["find"]!r} in {tweak["file"]}, found {found}. '
                             'Update the tweak for this version.')
        path.write_text(text.replace(tweak['find'], tweak['replace']), encoding='utf-8')


def _bundle(entry, cache_dir, keys_dir, bundle_dir):
    archive = _download(entry, cache_dir)
    with tempfile.TemporaryDirectory() as temp:
        extension_dir = _extract(archive, entry['root'], Path(temp))
        manifest = json.loads((extension_dir / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('version') != entry['version']:
            raise SystemExit(f'{entry["name"]}: manifest version {manifest.get("version")} '
                             f'does not match {entry["version"]}')
        _apply_tweaks(entry, extension_dir)

        key_path = keys_dir / f'{entry["name"]}.pem'
        if not key_path.exists():
            _log(f'WARNING: creating a new signing key {key_path}. Keep it: a different '
                 f'key changes the extension ID and users lose their {entry["display_name"]} '
                 'settings on update.')
        key = crx3.load_or_create_key(key_path)
        crx = crx3.pack(extension_dir, key)

    extension_id = crx3.verify(crx)
    crx_name = f'{entry["name"]}.crx'
    (bundle_dir / crx_name).write_bytes(crx)
    (bundle_dir / f'{extension_id}.json').write_text(
        json.dumps({
            'external_crx': crx_name,
            'external_version': entry['version']
        }, indent=2) + '\n')
    _log(f'{entry["display_name"]} {entry["version"]} -> {extension_id}')
    return extension_id


def _write_licenses(entries, licenses_dir):
    licenses_dir.mkdir()
    notice = ['Extensions bundled with Cerium\n']
    for entry in entries:
        license_file = _ROOT / 'extensions' / 'licenses' / f'{entry["name"]}-LICENSE.txt'
        shutil.copy(license_file, licenses_dir / license_file.name)
        notice.append(f'- {entry["display_name"]} {entry["version"]}: {entry["license"]}, '
                      f'see {license_file.name}. Source code: {entry["source"]}\n')
    (licenses_dir / 'README.md').write_text(''.join(notice), encoding='utf-8')


def _write_initial_preferences(path, pinned_ids):
    # Applied to new profiles on first run.
    preferences = {'extensions': {'pinned_extensions': pinned_ids}}
    path.write_text(json.dumps(preferences, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output-dir',
                        required=True,
                        type=Path,
                        help='Browser output directory, e.g. build/src/out/Default')
    parser.add_argument('--config', type=Path, default=_ROOT / 'extensions' / 'extensions.json')
    parser.add_argument('--keys-dir', type=Path, default=_ROOT / 'keys')
    parser.add_argument('--cache-dir', type=Path, default=_ROOT / 'build' / 'download_cache')
    args = parser.parse_args()

    entries = json.loads(args.config.read_text(encoding='utf-8'))['extensions']
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    bundle_dir = args.output_dir / _BUNDLE_DIR_NAME
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)

    pinned_ids = []
    for entry in entries:
        extension_id = _bundle(entry, args.cache_dir, args.keys_dir, bundle_dir)
        if entry.get('pin'):
            pinned_ids.append(extension_id)
    _write_licenses(entries, bundle_dir / 'licenses')
    _write_initial_preferences(args.output_dir / 'initial_preferences', pinned_ids)


if __name__ == '__main__':
    main()
