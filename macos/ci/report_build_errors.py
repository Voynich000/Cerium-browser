#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Prints the failures in a ninja log as GitHub Actions error annotations.

A failing compile is easy to miss in hours of build output, so github_build.sh
turns each "FAILED:" block (the target and the compiler's messages, without
the long command line) into an annotation on the run's summary page:

  report_build_errors.py <ninja output>
"""

import re
import sys

_ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
_PROGRESS = re.compile(r'^\[\d+/\d+\]')
_MAX_ANNOTATIONS = 10
_MAX_LENGTH = 3500


def _annotation(message):
    escaped = message[:_MAX_LENGTH].replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::error::{escaped}')


def main():
    with open(sys.argv[1], encoding='utf-8', errors='replace') as log:
        lines = [_ANSI.sub('', line.rstrip('\n')) for line in log]

    blocks = []
    current = None
    for line in lines:
        if line.startswith('FAILED: '):
            current = [line]
            blocks.append(current)
        elif current is not None:
            if _PROGRESS.match(line) or line.startswith('ninja: '):
                current = None
            elif len(line) < 1500:  # skip the compiler command line
                current.append(line)

    for block in blocks[:_MAX_ANNOTATIONS]:
        _annotation('\n'.join(block))
    if not blocks:
        _annotation('ninja failed; its last lines:\n' + '\n'.join(lines[-40:])[-_MAX_LENGTH:])


if __name__ == '__main__':
    main()
