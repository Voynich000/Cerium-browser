#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Compiles Cerium's macOS app icon (branding/macos/) into an Assets.car.

macOS takes the app icon from Contents/Resources/Assets.car (the app's
CFBundleIconName is AppIcon), which only Xcode's actool can build. Chromium
checks in a compiled Assets.car with its own icon; package_dmg.sh runs this
script on the Mac that packages Cerium and puts the result into Cerium.app
before it is signed:

  compile_icon.py --output path/to/Assets.car

The actool command is the one of Chromium's tools/mac/icons/compile_car.py,
which explains each flag. It needs Xcode 26 or later. Unlike Chromium's
script, warnings don't stop it; they are printed (as GitHub Actions
annotations in CI).
"""

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SOURCES = Path(__file__).resolve().parent.parent / 'branding' / 'macos'
# actool of Xcode 26b4, the first that can compile .icon files.
_MIN_ACTOOL_VERSION = (24112,)
# Expected messages; see compile_car.py.
_IGNORED_WARNINGS = ('Ambiguous Content',)
_IGNORED_PREFIXES = ('Failed to generate flattened icon stack',)


def _report(level, message):
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::{level}::compile_icon.py: {escaped}', file=sys.stderr)
    else:
        print(f'{level}: {message}', file=sys.stderr)


def _actool_version():
    output = subprocess.check_output(['xcrun', 'actool', '--output-format=xml1', '--version'])
    version = plistlib.loads(output)['com.apple.actool.version']['bundle-version']
    return tuple(int(part) for part in version.split('.'))


def _messages(output, key):
    """Returns actool's messages under |key| as readable strings."""
    messages = []
    for item in output.get(key) or []:
        if isinstance(item, dict):
            if item.get('type') in _IGNORED_WARNINGS:
                continue
            if str(item.get('message', '')).startswith(_IGNORED_PREFIXES):
                continue
            messages.append(json.dumps(item, default=str))
        else:
            messages.append(str(item))
    return messages


def compile_icon(output_path, minimum_deployment_target, lightweight_runtime=True):
    version = _actool_version()
    xcode = subprocess.run(['xcodebuild', '-version'], capture_output=True, text=True, check=False)
    print(f'compile_icon.py: actool {".".join(map(str, version))}, {xcode.stdout.strip()}')
    if version < _MIN_ACTOOL_VERSION:
        raise SystemExit(f'actool {".".join(map(str, version))} is too old; '
                         'select Xcode 26 or later (xcode-select or DEVELOPER_DIR)')

    with tempfile.TemporaryDirectory() as work:
        work = Path(work)
        shutil.copytree(_SOURCES / 'Assets.xcassets', work / 'Assets.xcassets')
        shutil.copytree(_SOURCES / 'AppIcon.icon', work / 'AppIcon.icon')
        out_dir = work / 'out'
        out_dir.mkdir()
        command = [
            'xcrun', 'actool',
            '--output-format=xml1', '--notices', '--warnings', '--errors',
            '--platform=macosx', '--target-device=mac',
            f'--lightweight-asset-runtime-mode={"enabled" if lightweight_runtime else "disabled"}',
            '--enable-icon-stack-fallback-generation=disabled',
            '--include-all-app-icons',
            '--app-icon=AppIcon',
            f'--minimum-deployment-target={minimum_deployment_target}',
            f'--output-partial-info-plist={work / "partial.plist"}',
            f'--compile={out_dir}',
            str(work / 'Assets.xcassets'),
            str(work / 'AppIcon.icon'),
        ]
        process = subprocess.run(command, capture_output=True, check=False)
        try:
            output = plistlib.loads(process.stdout)
        except Exception:  # pylint: disable=broad-except
            output = {}

        errors = (_messages(output, 'com.apple.actool.errors') +
                  _messages(output, 'com.apple.actool.document.errors'))
        for key in ('com.apple.actool.warnings', 'com.apple.actool.document.warnings',
                    'com.apple.actool.notices', 'com.apple.actool.document.notices'):
            for message in _messages(output, key):
                _report('warning', f'actool: {message}')

        car = out_dir / 'Assets.car'
        if process.returncode != 0 or errors or not car.is_file():
            details = '\n'.join(errors) or process.stdout.decode(errors='replace')
            # A crash report starts with the reason, followed by a long backtrace.
            stderr = '\n'.join(line for line in process.stderr.decode(errors='replace').splitlines()
                               if not line.startswith('dyld['))
            _report('error', f'actool failed (exit status {process.returncode}): {details}\n'
                    f'{stderr[:3000]}')
            raise SystemExit(1)

        # Check that the catalog holds the app icon under the name Info.plist uses.
        info = subprocess.run(['xcrun', 'assetutil', '--info', str(car)],
                              capture_output=True, check=False)
        if info.returncode == 0:
            entries = [entry for entry in json.loads(info.stdout or b'[]')
                       if isinstance(entry, dict) and 'Name' in entry]
            if not any(entry['Name'] == 'AppIcon' for entry in entries):
                _report('error', 'Assets.car has no AppIcon; it has '
                        f'{sorted({str(entry["Name"]) for entry in entries})}')
                raise SystemExit(1)
            summary = sorted({f'{entry["Name"]} {entry.get("AssetType", "")} '
                              f'{entry.get("PixelWidth", "")}'.strip() for entry in entries})
            _report('notice', f'Assets.car ({len(entries)} renditions): {", ".join(summary)}')

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(car, output_path)
    print(f'compile_icon.py: wrote {output_path} (actool {".".join(map(str, version))})')


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='Assets.car to write')
    parser.add_argument('--minimum-deployment-target', default='13.0',
                        help="Chromium's mac_deployment_target (build/config/mac/mac_sdk.gni)")
    parser.add_argument('--no-lightweight-runtime', action='store_true',
                        help="use the system's asset frameworks instead of Xcode's")
    args = parser.parse_args()
    compile_icon(args.output, args.minimum_deployment_target, not args.no_lightweight_runtime)


if __name__ == '__main__':
    main()
