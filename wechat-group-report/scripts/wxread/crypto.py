"""SQLCipher 4 页级解密（纯 Python，无外部 sqlcipher 依赖）。

已实测参数（Windows 微信 4.x）：
    page_size = 4096, reserve = 80
    每页 = [密文(4016)] [IV(16)] [HMAC-SHA512(64)]
    首页前 16 字节是 salt（明文，不参与本套解密）
    enc_key = 32 字节原始密钥（SQLCipher raw-key 模式）

解密后重组为标准 SQLite 文件：salt 位置写回 b"SQLite format 3\\x00"，
每页尾部补 80 字节 0（该区域是 reserve，SQLite 读取时按 header 的 reserved 跳过）。

WAL：-wal 文件里是同样布局的页，帧头 24 字节；盐值轮换后的旧帧失效，需跳过。
"""
from __future__ import annotations

import hashlib
import os
import struct

from Crypto.Cipher import AES

PAGE_SIZE = 4096
RESERVE = 80
SALT_LEN = 16
IV_OFF = PAGE_SIZE - RESERVE          # 4016
MAGIC = b"SQLite format 3\x00"


def page1_head(path: str) -> bytes:
    """读取加密库第 1 页（4096 字节），用于密钥校验。"""
    try:
        with open(path, "rb") as f:
            return f.read(PAGE_SIZE)
    except OSError:
        return b""


def is_encrypted_page1(page1: bytes) -> bool:
    """已加密的 SQLCipher 库首页不以 SQLite 明文魔数开头。"""
    return bool(page1) and len(page1) >= 32 and not page1.startswith(MAGIC)


def decrypt_page(key: bytes, page: bytes, pgno: int) -> bytes:
    if len(page) != PAGE_SIZE:
        page = page.ljust(PAGE_SIZE, b"\x00")
    iv = page[IV_OFF:IV_OFF + 16]
    if pgno == 1:
        ct = page[SALT_LEN:IV_OFF]
        return MAGIC + AES.new(key, AES.MODE_CBC, iv).decrypt(ct) + b"\x00" * RESERVE
    ct = page[:IV_OFF]
    return AES.new(key, AES.MODE_CBC, iv).decrypt(ct) + b"\x00" * RESERVE


def check_key(key: bytes, page1: bytes) -> bool:
    """用首页首块校验密钥。

    首页前 16 字节是 salt，因此首块解密得到的是 SQLite header 的 16..32 字节
    （页大小/读写版本/保留区/载荷分数）。校验这些固定字段，误报率 < 2^-50。
    """
    if len(key) != 32 or len(page1) < PAGE_SIZE:
        return False
    try:
        iv = page1[IV_OFF:IV_OFF + 16]
        b = AES.new(key, AES.MODE_CBC, iv).decrypt(page1[SALT_LEN:SALT_LEN + 16])
    except ValueError:
        return False
    return (b[0:2] == b"\x10\x00" and b[2] in (1, 2) and b[3] in (1, 2)
            and b[4] <= 100 and b[5] == 64 and b[6] == 32 and b[7] == 32)


def parse_wal(path: str):
    """解析 SQLCipher WAL，返回 ([(pgno, page_bytes)], wal_salt)。"""
    if not os.path.exists(path):
        return [], None
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 32:
        return [], None
    magic = struct.unpack(">I", data[0:4])[0]
    if magic not in (0x377F0682, 0x377F0683):
        return [], None
    wal_salt = data[16:24]
    frames = []
    off = 32
    while off + 24 + PAGE_SIZE <= len(data):
        hdr = data[off:off + 24]
        pgno = struct.unpack(">I", hdr[0:4])[0]
        salt = hdr[8:16]
        if pgno == 0 or salt != wal_salt:
            break
        frames.append((pgno, data[off + 24: off + 24 + PAGE_SIZE]))
        off += 24 + PAGE_SIZE
    return frames, wal_salt


def decrypt_db(src: str, dst: str, key: bytes, use_wal: bool = True) -> dict:
    """解密单个库到 dst（临时明文，调用方负责清理）。"""
    with open(src, "rb") as f:
        raw = f.read()
    n_pages = (len(raw) + PAGE_SIZE - 1) // PAGE_SIZE
    pages = {}
    for i in range(n_pages):
        pgno = i + 1
        page = raw[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
        pages[pgno] = decrypt_page(key, page, pgno)

    wal_applied = 0
    if use_wal:
        frames, _ = parse_wal(src + "-wal")
        for pgno, page in frames:
            pages[pgno] = decrypt_page(key, page, pgno)
            wal_applied += 1

    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    maxpg = max(pages) if pages else 0
    with open(dst, "wb") as f:
        for pgno in range(1, maxpg + 1):
            f.write(pages.get(pgno, b"\x00" * PAGE_SIZE))
    with open(dst, "r+b") as f:      # WAL 可能带来新页，修正 header 里的页数
        f.seek(28)
        f.write(struct.pack(">I", maxpg))
    return {"pages": n_pages, "wal_frames": wal_applied, "out_pages": maxpg}


def selftest() -> bool:
    """离线自测：按真实页布局加密 30 页随机数据 → 解密 → 逐字节一致。"""
    import random

    rnd = random.Random(20261031)
    salt = bytes(rnd.getrandbits(8) for _ in range(16))
    key = hashlib.sha256(b"selftest").digest()
    pages = []
    for i in range(30):
        body = bytearray(rnd.getrandbits(8) for _ in range(PAGE_SIZE))
        if i == 0:
            body[0:16] = MAGIC
            body[16:18] = b"\x10\x00"
            body[18] = 2
            body[19] = 2
            body[20] = RESERVE
            body[21] = 64
            body[22] = 32
            body[23] = 32
            body[28:32] = (30).to_bytes(4, "big")
        body[PAGE_SIZE - RESERVE:] = b"\x00" * RESERVE
        pages.append(bytes(body))

    enc = []
    for i, p in enumerate(pages):
        pgno = i + 1
        iv = os.urandom(16)
        body = p[SALT_LEN:PAGE_SIZE - RESERVE] if pgno == 1 else p[:PAGE_SIZE - RESERVE]
        ct = AES.new(key, AES.MODE_CBC, iv).encrypt(body)
        e = (p[:SALT_LEN] + ct + iv) if pgno == 1 else (ct + iv)
        enc.append(e.ljust(PAGE_SIZE, b"\x00"))

    assert check_key(key, enc[0]), "check_key 误判为错误密钥"
    assert not check_key(bytes(32), enc[0]), "check_key 误报"
    for i, e in enumerate(enc):
        if decrypt_page(key, e, i + 1) != pages[i]:
            return False
    return True


if __name__ == "__main__":
    print("selftest:", selftest())
