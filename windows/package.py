#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright (c) 2018 The ungoogled-chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""
Cerium packaging script for Microsoft Windows

Cerium's copy of ungoogled-chromium-windows's package.py. It writes
build/cerium-<version>-<revision>-<cpu>-windows-setup.exe (Chromium's
mini_installer) and build/cerium-<version>-<revision>-<cpu>-windows.zip (a
portable copy). Both contain the bundled extensions: the installer through
chrome.release (windows/patches/cerium-windows/add-bundled-extensions-windows.patch),
the .zip from here.
"""

import sys
if sys.version_info.major < 3:
    raise RuntimeError('Python 3 is required for this script.')

import argparse
import os
import platform
from pathlib import Path
import shutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'ungoogled-chromium' / 'utils'))
import filescfg
from _common import ENCODING, get_chromium_version
sys.path.pop(0)

def _get_release_revision():
    revision_path = Path(__file__).resolve().parent.parent / 'ungoogled-chromium' / 'revision.txt'
    return revision_path.read_text(encoding=ENCODING).strip()

_cached_target_cpu = None

def _get_target_cpu(build_outputs):
    global _cached_target_cpu
    if not _cached_target_cpu:
        with open(build_outputs / 'args.gn', 'r') as f:
            args_gn_text = f.read()
            for cpu in ('x64', 'x86', 'arm64'):
                if f'target_cpu="{cpu}"' in args_gn_text:
                    _cached_target_cpu = cpu
                    break
    assert _cached_target_cpu
    return _cached_target_cpu

def main():
    """Entrypoint"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cpu-arch',
        metavar='ARCH',
        default=platform.architecture()[0],
        choices=('64bit', '32bit', 'arm'),
        help=('Filter build outputs by a target CPU. '
              'This is the same as the "arch" key in FILES.cfg. '
              'Default (from platform.architecture()): %(default)s'))
    args = parser.parse_args()

    build_outputs = Path('build/src/out/Default')

    name = 'cerium-{}-{}-{}-windows'.format(
        get_chromium_version(), _get_release_revision(), _get_target_cpu(build_outputs))
    shutil.copyfile('build/src/out/Default/mini_installer.exe', f'build/{name}-setup.exe')

    timestamp = None
    try:
        with open('build/src/build/util/LASTCHANGE.committime', 'r') as ct:
            timestamp = int(ct.read())
    except FileNotFoundError:
        pass

    output = Path(f'build/{name}.zip')

    excluded_files = set([
        Path('mini_installer.exe'),
        Path('mini_installer_exe_version.rc'),
        Path('setup.exe'),
        Path('chrome.packed.7z'),
    ])
    files_generator = filescfg.filescfg_generator(
        Path('build/src/chrome/tools/build/win/FILES.cfg'),
        build_outputs, args.cpu_arch, excluded_files)
    # Cerium: the bundled extensions and the initial preferences that pin
    # them, next to chrome.exe and chrome.dll.
    bundled = (build_outputs / 'bundled_extensions', build_outputs / 'initial_preferences')
    for path in bundled:
        if not path.exists():
            raise SystemExit(f'{path} is missing; run utils/bundle_extensions.py first')
    filescfg.create_archive(
        files_generator, bundled, build_outputs, output, timestamp)

if __name__ == '__main__':
    main()
