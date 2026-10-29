#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Prints the ninja targets that compile the files Cerium's patches change.

github_build.sh builds them before the rest of Chromium, so that a mistake in
Cerium's code stops the first build job within the hour instead of hours into
the build:

  cerium_first_targets.py <chromium src>

C++ and Objective-C++ files map to their object files (from ninja's
compilation database); TypeScript files, and the string files that
utils/rebrand.py rewrites, to the GN targets that compile them.
"""

import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# macos/, where CI copies the ci/ scripts; or macos/ci/ itself.
_MACOS = _HERE if (_HERE / 'patches').is_dir() else _HERE.parent
_PATCH_DIRS = [_MACOS / 'patches' / 'cerium', _MACOS / 'patches' / 'cerium-macos']
# String files that utils/rebrand.py rewrites (with their translations).
_REBRANDED = {'chrome/app/chromium_strings.grd', 'components/components_chromium_strings.grd'}


def _patched_files():
    files = set()
    for patch_dir in _PATCH_DIRS:
        for patch in patch_dir.glob('*.patch'):
            for line in patch.read_text(encoding='utf-8').splitlines():
                if line.startswith('+++ b/'):
                    files.add(line[len('+++ b/'):].strip())
    return files


def _object_files(out_dir, sources):
    """Returns the default toolchain's object files for |sources|."""
    objects = set()
    # Some compile commands contain bytes that are not UTF-8; only the "file"
    # and "output" lines matter.
    process = subprocess.Popen(['ninja', '-C', str(out_dir), '-t', 'compdb'],
                               stdout=subprocess.PIPE, encoding='utf-8', errors='replace')
    source = None
    # One "key": "value" pair per line; "file" comes before "output".
    for line in process.stdout:
        line = line.strip().rstrip(',')
        if line.startswith('"file": '):
            source = line[len('"file": '):].strip('"')
        elif line.startswith('"output": '):
            output = line[len('"output": '):].strip('"')
            # Other toolchains' outputs are in subdirectories like clang_x64/.
            if source and output.startswith('obj/'):
                relative = Path(source).as_posix()
                while relative.startswith('../'):
                    relative = relative[3:]
                if relative in sources:
                    objects.add(output)
            source = None
    process.wait()
    return objects


def _gn_targets(src_dir, out_dir, files):
    if not files:
        return set()
    result = subprocess.run([str(out_dir / 'gn'), 'refs', str(out_dir)] +
                            ['//' + name for name in sorted(files)],
                            cwd=src_dir, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f'cerium_first_targets.py: gn refs failed: {result.stderr}', file=sys.stderr)
        return set()
    return {line.strip()[2:] for line in result.stdout.splitlines() if line.startswith('//')}


def main():
    src_dir = Path(sys.argv[1]).resolve()
    out_dir = src_dir / 'out' / 'Default'
    files = _patched_files()
    sources = {name for name in files if name.endswith(('.cc', '.mm', '.c'))}
    others = {name for name in files if name.endswith('.ts')} | _REBRANDED
    targets = _object_files(out_dir, sources) | _gn_targets(src_dir, out_dir, others)
    print(' '.join(sorted(targets)))
    print(f'cerium_first_targets.py: {len(targets)} targets for {len(sources)} C++ files, '
          f'{len(others)} TypeScript and string files', file=sys.stderr)


if __name__ == '__main__':
    main()
