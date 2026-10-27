#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for crx3.py."""

import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import crx3  # pylint: disable=wrong-import-position


class Crx3Test(unittest.TestCase):

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.dir = Path(self._temp.name)
        self.extension = self.dir / 'extension'
        (self.extension / 'js').mkdir(parents=True)
        (self.extension / 'manifest.json').write_text(
            json.dumps({
                'manifest_version': 3,
                'name': 'Test',
                'version': '1.0'
            }))
        (self.extension / 'js' / 'background.js').write_text('// test\n')
        self.key = crx3.load_or_create_key(self.dir / 'key.pem')

    def tearDown(self):
        self._temp.cleanup()

    def test_pack_and_verify(self):
        crx = crx3.pack(self.extension, self.key)
        self.assertEqual(crx[:4], b'Cr24')
        self.assertEqual(crx3.verify(crx), crx3.extension_id(self.key))
        archive = zipfile.ZipFile(io.BytesIO(crx[12 + int.from_bytes(crx[8:12], 'little'):]))
        self.assertEqual(sorted(archive.namelist()), ['js/background.js', 'manifest.json'])

    def test_extension_id_format(self):
        extension_id = crx3.extension_id(self.key)
        self.assertEqual(len(extension_id), 32)
        self.assertTrue(set(extension_id) <= set('abcdefghijklmnop'))

    def test_key_is_reused(self):
        reloaded = crx3.load_or_create_key(self.dir / 'key.pem')
        self.assertEqual(crx3.extension_id(reloaded), crx3.extension_id(self.key))

    def test_packing_is_deterministic(self):
        first = crx3.zip_directory(self.extension)
        (self.extension / 'js' / 'background.js').touch()
        self.assertEqual(first, crx3.zip_directory(self.extension))

    def test_tampered_archive_is_rejected(self):
        crx = bytearray(crx3.pack(self.extension, self.key))
        crx[-30] ^= 0xFF
        with self.assertRaises(Exception):
            crx3.verify(bytes(crx))


if __name__ == '__main__':
    unittest.main()
