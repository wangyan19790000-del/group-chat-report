"""wxSQLite3 AES-128-CBC page crypto for WeCom (企业微信) 5.x local databases.

Algorithm (matches sqlite3mc / wxSQLite3 "aes128"):
  page key = MD5(raw_key + LE32(page_no) + b"sAlT")
  page IV  = MD5(initkey_from_page_no)   (LCG, sqlite3mcGenerateInitialVector)
  page 1 keeps plaintext header fragment at bytes 16..23 for format detection.
"""
from __future__ import annotations

import hashlib
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE_SZ = 4096
SQLITE_HDR = b"SQLite format 3\x00"
SALT = b"sAlT"


def _modmult(a: int, b: int, c: int, m: int, s: int) -> int:
    q = s // a
    s = b * (s - a * q) - c * q
    if s < 0:
        s += m
    return s


def page_iv(page_no: int) -> bytes:
    z = page_no + 1
    init = bytearray(16)
    for i in range(4):
        z = _modmult(52774, 40692, 3791, 2147483399, z)
        init[i * 4:i * 4 + 4] = struct.pack("<I", z & 0xFFFFFFFF)
    return hashlib.md5(init).digest()


def page_key(raw_key: bytes, page_no: int) -> bytes:
    if len(raw_key) != 16:
        raise ValueError("raw key must be 16 bytes")
    return hashlib.md5(raw_key + struct.pack("<I", page_no) + SALT).digest()


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def decrypt_page(raw_key: bytes, page: bytes, page_no: int) -> bytes:
    if len(page) != PAGE_SZ:
        raise ValueError("page must be 4096 bytes")
    if page_no == 1 and has_header_fragment(page):
        frag = bytes(page[16:24])
        buf = bytearray(page)
        buf[16:24] = buf[8:16]
        tail = _aes_cbc_decrypt(page_key(raw_key, 1), page_iv(1), bytes(buf[16:]))
        buf[16:] = tail
        if bytes(buf[16:24]) != frag:
            raise ValueError("key validation failed")
        buf[:16] = SQLITE_HDR
        return bytes(buf)
    return _aes_cbc_decrypt(page_key(raw_key, page_no), page_iv(page_no), page)


def is_plain_sqlite(page: bytes) -> bool:
    return page[:16] == SQLITE_HDR


def has_header_fragment(page: bytes) -> bool:
    if len(page) < 24:
        return False
    h = page[16:24]
    page_size = (h[0] << 8) | h[1]
    if page_size == 1:
        page_size = 65536
    return (512 <= page_size <= 65536 and (page_size & (page_size - 1)) == 0
            and h[5] == 0x40 and h[6] == 0x20 and h[7] == 0x20)


def is_encrypted_page1(page: bytes) -> bool:
    return (not is_plain_sqlite(page)) and has_header_fragment(page)


def verify_key(raw_key: bytes, page1: bytes) -> bool:
    if len(raw_key) != 16 or len(page1) < PAGE_SZ:
        return False
    try:
        out = decrypt_page(raw_key, page1[:PAGE_SZ], 1)
    except Exception:
        return False
    return out[:16] == SQLITE_HDR and out[100] in (0x02, 0x05, 0x0A, 0x0D)


def decrypt_database(src: str, dst: str, raw_key: bytes) -> int:
    """Decrypt whole DB file, return page count."""
    size = os.path.getsize(src)
    total = (size + PAGE_SZ - 1) // PAGE_SZ
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        for n in range(1, total + 1):
            page = fin.read(PAGE_SZ)
            if not page:
                break
            if len(page) < PAGE_SZ:
                page = page + b"\x00" * (PAGE_SZ - len(page))
            fout.write(decrypt_page(raw_key, page, n))
    return total
