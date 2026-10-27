#!/usr/bin/env python3
# Copyright 2026 The Cerium Browser Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Minimal CRX3 packer (the format Chromium installs extensions from).

A CRX3 file is
  "Cr24" | uint32le 3 | uint32le header_size | CrxFileHeader | zip archive
where CrxFileHeader is the protobuf from components/crx_file/crx3.proto:

  message CrxFileHeader {
    repeated AsymmetricKeyProof sha256_with_rsa = 2;
    bytes signed_header_data = 10000;  // serialized SignedData
  }
  message AsymmetricKeyProof { bytes public_key = 1; bytes signature = 2; }
  message SignedData { bytes crx_id = 1; }

The signature is RSASSA-PKCS1-v1_5/SHA-256 over
  "CRX3 SignedData\\x00" | uint32le len(signed_header_data)
  | signed_header_data | zip archive
and crx_id is the first 16 bytes of SHA-256(public key in DER SPKI form).
The extension ID is crx_id in hex, with the digits 0-f mapped to a-p.
"""

import hashlib
import io
import struct
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

_MAGIC = b'Cr24'
_VERSION = 3
_SIGNATURE_CONTEXT = b'CRX3 SignedData\x00'
# Fixed timestamp so the same inputs always give the same archive.
_ZIP_DATE_TIME = (2000, 1, 1, 0, 0, 0)


def _varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _bytes_field(field_number, payload):
    """Encodes a length-delimited protobuf field."""
    return _varint(field_number << 3 | 2) + _varint(len(payload)) + payload


def load_or_create_key(path):
    """Returns the RSA private key in |path|, creating it if missing."""
    path = Path(path)
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()))
    path.chmod(0o600)
    return key


def public_key_der(key):
    return key.public_key().public_bytes(serialization.Encoding.DER,
                                         serialization.PublicFormat.SubjectPublicKeyInfo)


def crx_id(key):
    return hashlib.sha256(public_key_der(key)).digest()[:16]


def extension_id(key):
    return ''.join(chr(ord('a') + int(c, 16)) for c in crx_id(key).hex())


def zip_directory(directory):
    """Returns a deterministic zip archive of |directory|'s contents."""
    directory = Path(directory)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in directory.rglob('*') if p.is_file()):
            info = zipfile.ZipInfo(path.relative_to(directory).as_posix(), _ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return buffer.getvalue()


def pack(directory, key):
    """Packs the unpacked extension in |directory| into CRX3 bytes."""
    archive = zip_directory(directory)
    signed_header_data = _bytes_field(1, crx_id(key))
    signature = key.sign(
        _SIGNATURE_CONTEXT + struct.pack('<I', len(signed_header_data)) + signed_header_data +
        archive, padding.PKCS1v15(), hashes.SHA256())
    proof = _bytes_field(1, public_key_der(key)) + _bytes_field(2, signature)
    header = _bytes_field(2, proof) + _bytes_field(10000, signed_header_data)
    return _MAGIC + struct.pack('<II', _VERSION, len(header)) + header + archive


def _read_varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _parse_fields(data):
    """Yields (field_number, payload) for the length-delimited fields of |data|."""
    pos = 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        if tag & 7 != 2:
            raise ValueError('unexpected wire type')
        length, pos = _read_varint(data, pos)
        yield tag >> 3, data[pos:pos + length]
        pos += length


def verify(crx):
    """Checks a CRX3 the way Chromium does and returns its extension ID."""
    magic, version, header_size = crx[:4], *struct.unpack('<II', crx[4:12])
    if magic != _MAGIC or version != _VERSION:
        raise ValueError('not a CRX3 file')
    header, archive = crx[12:12 + header_size], crx[12 + header_size:]
    proofs, signed_header_data = [], None
    for number, payload in _parse_fields(header):
        if number == 2:
            proofs.append(dict(_parse_fields(payload)))
        elif number == 10000:
            signed_header_data = payload
    expected_id = dict(_parse_fields(signed_header_data))[1]
    message = (_SIGNATURE_CONTEXT + struct.pack('<I', len(signed_header_data)) +
               signed_header_data + archive)
    developer_key_found = False
    for proof in proofs:
        public_key = serialization.load_der_public_key(proof[1])
        public_key.verify(proof[2], message, padding.PKCS1v15(), hashes.SHA256())
        if hashlib.sha256(proof[1]).digest()[:16] == expected_id:
            developer_key_found = True
    if not developer_key_found:
        raise ValueError('no proof from the key matching the CRX ID')
    zipfile.ZipFile(io.BytesIO(archive)).testzip()
    return ''.join(chr(ord('a') + int(c, 16)) for c in expected_id.hex())
