#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Checks net::UnsubstituteHost() against ungoogled-chromium's domain rules.

The Google services switch relies on net/base/domain_unsubstitution.cc
(added by patches/cerium/add-google-services-switch.patch) exactly reversing
ungoogled-chromium/domain_regex.list. Run this after updating the
ungoogled-chromium submodule.

The script takes the C++ file out of the patch, compiles it with a small
driver, and feeds it hostnames run through the forward rules: synthetic ones
for every rule and, with --source-tree, every hostname found in the files
listed in domain_substitution.list (takes a few minutes).

Needs a C++20 compiler (c++, g++ or clang++; or set CXX).
"""

import argparse
import itertools
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PATCH = _ROOT / 'patches' / 'cerium' / 'add-google-services-switch.patch'
_UNGOOGLED = _ROOT / 'ungoogled-chromium'
_HOST = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?'
                   r'(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+')

# Stand-ins for the two Chromium headers domain_unsubstitution.cc uses.
_STUBS = {
    'net/base/net_export.h': '#pragma once\n#define NET_EXPORT\n',
    'base/strings/string_util.h': '''#pragma once
#include <string>
#include <string_view>
namespace base {
inline std::string ToLowerASCII(std::string_view s) {
  std::string r(s);
  for (char& c : r) if (c >= 'A' && c <= 'Z') c += 'a' - 'A';
  return r;
}
}  // namespace base
''',
}

_DRIVER = r'''
#include <iostream>
#include <string>
#include "net/base/domain_unsubstitution.h"
// Reads "substituted<TAB>original" lines; "-" means "not substituted".
int main() {
  std::string line;
  int cases = 0, failures = 0;
  while (std::getline(std::cin, line)) {
    const auto tab = line.find('\t');
    const std::string input = line.substr(0, tab), want = line.substr(tab + 1);
    const auto result = net::UnsubstituteHost(input);
    const std::string got = result ? *result : "-";
    ++cases;
    if (got != want && ++failures <= 50) {
      std::cout << "MISMATCH " << input << ": got " << got << ", want " << want << "\n";
    }
  }
  std::cout << cases << " cases, " << failures << " mismatches\n";
  return failures ? 1 : 0;
}
'''


def _new_files_from_patch(patch_path):
    """Returns {path: content} for the files a patch creates."""
    files, current = {}, None
    lines = patch_path.read_text().splitlines()
    for index, line in enumerate(lines):
        if line.startswith('--- /dev/null') and index + 1 < len(lines):
            current = lines[index + 1][len('+++ b/'):].split('\t')[0]
            files[current] = []
        elif line.startswith('--- '):
            current = None
        elif current and line.startswith('+') and not line.startswith('+++ '):
            files[current].append(line[1:])
    return {path: '\n'.join(content) + '\n' for path, content in files.items()}


def _load_rules():
    rules = []
    for line in (_UNGOOGLED / 'domain_regex.list').read_text().splitlines():
        if line.strip():
            pattern, replacement = line.split('#', 1)
            rules.append((re.compile(pattern), replacement))
    return rules


def _substitute(rules, text):
    for pattern, replacement in rules:
        text = pattern.sub(replacement, text)
    return text


def _cases(rules, source_tree):
    cases = {}
    originals = [
        'fonts.googleapis.com', 'google.com', 'googleapis.com', 'googleusercontent.com',
        'google-analytics.com', 'gstatic.com', 'chrome.com', 'chromium.org', 'mozilla.org',
        'facebook.com', 'appspot.com', 'youtube.com', 'youtube-nocookie.com', 'ytimg.com',
        'gmail.com', 'doubleclick.net', 'doubleclick.com', 'googlezip.net', 'beacons.gvt1.com',
        'beacons3.gvt2.com', 'ggpht.com', 'microsoft.com', '1e100.net', 'android.com', 'goo.gl',
        'goo.gle', 'privacysandbox.com', 'google-google.com', 'google.com.google.com'
    ]
    for prefix, original in itertools.product(['', 'www.', 'clients2.', 'a.b-c.', 'x'],
                                              originals):
        host = prefix + original
        substituted = _substitute(rules, host)
        if not substituted.endswith('.qjz9zk'):
            raise SystemExit(f'forward rules no longer substitute {host}')
        cases[substituted.lower()] = host
        cases[substituted.upper()] = host
    for host in ['qjz9zk', '.qjz9zk', 'example.qjz9zk', 'example.com', 'www.9oo91e.qjz9zk.au']:
        cases.setdefault(host, '-')

    if source_tree:
        files = (_UNGOOGLED / 'domain_substitution.list').read_text().split()
        for name in files:
            try:
                text = (source_tree / name).read_text(errors='ignore')
            except OSError:
                continue
            for host in set(_HOST.findall(text)):
                substituted = _substitute(rules, host)
                if substituted != host and substituted.lower().endswith('.qjz9zk'):
                    cases[substituted.lower()] = host.lower()
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-tree',
                        type=Path,
                        help='Unmodified Chromium source tree to take hostnames from')
    args = parser.parse_args()

    compiler = os.environ.get('CXX') or next(
        (c for c in ('c++', 'g++', 'clang++') if shutil.which(c)), None)
    if not compiler:
        raise SystemExit('No C++ compiler found; set CXX.')

    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        sources = _new_files_from_patch(_PATCH)
        for path, content in {**sources, **_STUBS, 'driver.cc': _DRIVER}.items():
            (temp / path).parent.mkdir(parents=True, exist_ok=True)
            (temp / path).write_text(content)
        binary = temp / 'driver'
        subprocess.run([
            compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror', '-O1', f'-I{temp}', '-o',
            str(binary),
            str(temp / 'driver.cc'),
            str(temp / 'net/base/domain_unsubstitution.cc')
        ],
                       check=True)
        cases = _cases(_load_rules(), args.source_tree)
        stdin = ''.join(f'{s}\t{o}\n' for s, o in sorted(cases.items()))
        result = subprocess.run([str(binary)], input=stdin, text=True, check=False)
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
