"""Recover wxSQLite3 raw keys from WXWork.exe process memory (read-only).

Two strategies, both verified against real DB page 1 before being accepted:
  A. hex literal scan  -> key embedded as x'<32 hex>' (SQL PRAGMA key string)
  B. cipher-struct scan -> heap object layout: flags, 16-byte key at +8,
     page-size pointer chain at +0x30 / +4 / +0x24

The key never leaves this process: callers get it in memory only.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import re
import struct
import subprocess
from typing import Optional

from .crypto import PAGE_SZ, is_encrypted_page1, verify_key

k32 = ctypes.windll.kernel32
MEM_COMMIT = 0x1000
READABLE = {0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80}
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PAGE_SIZES = {512, 1024, 2048, 4096, 8192, 16384, 32768, 65536}

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wt.HANDLE
_k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_k32.ReadProcessMemory.restype = wt.BOOL
_k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
_k32.VirtualQueryEx.restype = ctypes.c_size_t


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_uint64), ("AllocationBase", ctypes.c_uint64),
        ("AllocationProtect", wt.DWORD), ("_pad1", wt.DWORD),
        ("RegionSize", ctypes.c_uint64), ("State", wt.DWORD),
        ("Protect", wt.DWORD), ("Type", wt.DWORD), ("_pad2", wt.DWORD),
    ]


def _pids() -> list[tuple[int, int]]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WXWork.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.strip().splitlines():
        parts = line.strip('"').split('","')
        if len(parts) >= 5:
            mem = parts[4].replace(",", "").replace(" K", "").strip()
            rows.append((int(parts[1]), int(mem or 0)))
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows


def _regions(h) -> list[tuple[int, int]]:
    regs, addr, mbi = [], 0, MBI()
    while addr < 0x7FFFFFFFFFFF:
        if not _k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        if mbi.State == MEM_COMMIT and mbi.Protect in READABLE and 0 < mbi.RegionSize < 512 * 1024 * 1024:
            regs.append((int(mbi.BaseAddress), int(mbi.RegionSize)))
        nxt = int(mbi.BaseAddress) + int(mbi.RegionSize)
        if nxt <= addr:
            break
        addr = nxt
    return regs


def _read(h, addr: int, size: int) -> Optional[bytes]:
    buf = ctypes.create_string_buffer(size)
    n = ctypes.c_size_t(0)
    if _k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        return buf.raw[:n.value]
    return None


def _u32(h, addr: int) -> Optional[int]:
    b = _read(h, addr, 4)
    return struct.unpack("<I", b)[0] if b and len(b) == 4 else None


HEX_RE = re.compile(b"x'([0-9a-fA-F]{32})'")


def _try_key(cand: bytes, pages: dict[str, bytes], found: dict[str, bytes]) -> bool:
    for salt, page1 in pages.items():
        if salt in found:
            continue
        if verify_key(cand, page1):
            found[salt] = cand
            return True
    return False


def extract_keys(parent_dbs: dict[str, str], verbose: bool = True) -> dict[str, bytes]:
    """parent_dbs: {salt_hex: path_to_db}; returns {salt_hex: raw_key}."""
    pages = {}
    for salt, path in parent_dbs.items():
        with open(path, "rb") as f:
            page1 = f.read(PAGE_SZ)
        if is_encrypted_page1(page1):
            pages[salt] = page1
    if not pages:
        raise RuntimeError("没有找到加密数据库，检查 db 目录")
    found: dict[str, bytes] = {}

    for pid, mem_kb in _pids():
        h = _k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        try:
            regs = _regions(h)
            if verbose:
                print(f"  scan pid={pid} mem={mem_kb//1024}MB regions={len(regs)} waiting={len(pages)-len(found)}")
            # A. hex literal
            for base, size in regs:
                data = _read(h, base, size)
                if not data:
                    continue
                for m in HEX_RE.finditer(data):
                    if _try_key(bytes.fromhex(m.group(1).decode()), pages, found):
                        nxt = m.end()
                        data = data[:nxt]  # keep scanning rest of buffer
                        if len(found) == len(pages):
                            break
                        data = data + _read(h, base + nxt, size - nxt) if nxt < size else data
                if len(found) == len(pages):
                    break
            # B. cipher struct
            if len(found) < len(pages):
                for base, size in regs:
                    data = _read(h, base, size)
                    if not data:
                        continue
                    max_off = len(data) - 0x40
                    pos = 0
                    while pos < max_off:
                        i1 = data.find(b"\x01\x00\x00\x00", pos)
                        i2 = data.find(b"\x02\x00\x00\x00", pos)
                        if i1 < 0 and i2 < 0:
                            break
                        off = min(x for x in (i1, i2) if x >= 0)
                        pos = off + 4
                        if off % 4 or off >= max_off:
                            continue
                        f4 = struct.unpack_from("<I", data, off + 4)[0]
                        if f4 not in (1, 2, 4096, 8192, 16384):
                            continue
                        cand = bytes(data[off + 8:off + 24])
                        if cand == b"\x00" * 16 or len(set(cand)) < 6:
                            continue
                        # page-size pointer chain
                        ps_holder = _u32(h, base + off + 0x30)
                        if not ps_holder:
                            continue
                        ps_obj = _u32(h, ps_holder + 4)
                        if not ps_obj:
                            continue
                        if _u32(h, ps_obj + 0x24) not in PAGE_SIZES:
                            continue
                        _try_key(cand, pages, found)
                        if len(found) == len(pages):
                            break
                    if len(found) == len(pages):
                        break
        finally:
            k32.CloseHandle(h)
        if len(found) == len(pages):
            break
    if not found:
        raise RuntimeError("未能从 WXWork.exe 内存中提取到密钥（确认企微已登录并保持运行）")
    return found
