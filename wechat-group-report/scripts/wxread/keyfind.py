"""密钥获取：只读扫描微信进程内存，不注入、不落盘。

原理
----
SQLCipher 打开数据库时要求一个 32 字节原始密钥（raw key），该密钥必须常驻在
微信进程内存里（每次页加解密都要用）。因此不需要知道微信版本、不需要函数偏移，
只要把进程可读内存里所有「像密钥的 32 字节窗口」枚举出来，用目标库首页的固定
header 字段做校验即可（误报率 < 2^-50，见 crypto.check_key）。

同时也尝试「64 位十六进制字符串」形态（部分版本把密钥存成 hex 文本）。

安全边界：只调用 OpenProcess(PROCESS_VM_READ) + ReadProcessMemory，绝不写进程内存；
密钥只作为返回值存在于调用进程内存，不写入任何文件或日志。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import re
import subprocess
import time

import numpy as np

from .crypto import PAGE_SIZE, check_key, page1_head

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
PAGE_READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
MAX_REGION = 512 * 1024 * 1024
CHUNK = 32 * 1024 * 1024

PROC_NAMES = ("Weixin.exe", "WeChat.exe")


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD),
                ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD)]


k32.OpenProcess.restype = wt.HANDLE
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
k32.CloseHandle.argtypes = [wt.HANDLE]


def list_weixin_pids(names=PROC_NAMES) -> list[int]:
    """返回微信客户端进程 PID 列表（微信是多进程架构，逐个小进程可能持有连接）。"""
    pids: list[int] = []
    for name in names:
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
                                 capture_output=True, timeout=20).stdout.decode("gbk", "replace")
        except Exception:
            continue
        for line in out.splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == name.lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
    return pids


def _regions(handle):
    addr = 0
    mbi = _MBI()
    while addr < 0x7FFFFFFF0000:
        if k32.VirtualQueryEx(handle, ctypes.c_void_p(addr), ctypes.byref(mbi),
                              ctypes.sizeof(mbi)) == 0:
            break
        yield mbi
        if mbi.RegionSize == 0:
            break
        addr = (mbi.BaseAddress or 0) + mbi.RegionSize


def _read(handle, addr, size):
    buf = ctypes.create_string_buffer(size)
    got = ctypes.c_size_t()
    if not k32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)):
        return None
    return buf.raw[: got.value]


def _candidates(buf: bytes, min_distinct: int = 26, max_printable: int = 22,
                stride: int = 1, base: int = 0):
    """筛出「像密钥」的 32 字节窗口：无 0 字节、可打印字符少、字节足够分散。"""
    a = np.frombuffer(buf, dtype=np.uint8)
    if len(a) < 65:
        return []
    W = 32
    zero = (a == 0).astype(np.int32)
    printable = ((a >= 0x20) & (a <= 0x7E)).astype(np.int32)
    cz = np.concatenate(([0], np.cumsum(zero)))
    cp = np.concatenate(([0], np.cumsum(printable)))
    mask = ((cz[W:] - cz[:-W]) == 0) & ((cp[W:] - cp[:-W]) <= max_printable)
    if stride > 1:
        keep = np.zeros_like(mask)
        keep[(-base) % stride::stride] = True
        mask &= keep
    idx = np.nonzero(mask)[0]
    if idx.size == 0:
        return []
    out = []
    for s in range(0, idx.size, 300000):
        chunk = idx[s:s + 300000]
        win = np.sort(np.lib.stride_tricks.sliding_window_view(a, W)[chunk], axis=1)
        distinct = 1 + (np.diff(win, axis=1) != 0).sum(axis=1)
        keep = chunk[distinct >= min_distinct]
        out.extend(bytes(a[i:i + W]) for i in keep)
    return out


_HEX64 = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def _hex_candidates(buf: bytes):
    """64 位十六进制文本形态的密钥（部分版本把密钥存成 hex 字符串）。"""
    out = []
    for m in _HEX64.finditer(buf):
        try:
            out.append(bytes.fromhex(m.group(0).decode("ascii")))
        except ValueError:
            pass
    return out


def _verify(cands, probes, found: dict, log, tag: str):
    for key in cands:
        if len(found) >= len(probes):
            return
        for path, head in probes:
            if path in found:
                continue
            try:
                if check_key(key, head):
                    found[path] = key
                    log(f"[keyfind] 命中 {os.path.basename(path)}（{tag}）")
            except Exception:
                continue


def _region_chunks(handle) -> list[tuple[int, int]]:
    """枚举一个进程的可读区域，切成 32MB 块（返回 (base, size) 列表）。"""
    out = []
    for mbi in _regions(handle):
        if mbi.State != MEM_COMMIT or mbi.Protect not in PAGE_READABLE:
            continue
        if mbi.RegionSize > MAX_REGION or mbi.RegionSize < 65:
            continue
        base = mbi.BaseAddress or 0
        off = 0
        while off < mbi.RegionSize:
            blk = min(CHUNK, mbi.RegionSize - off)
            out.append((base + off, blk))
            off += blk
    return out


def _pid_memory(pids: list[int]) -> list[tuple[int, int]]:
    """返回 [(pid, 可读字节数)]，按内存从大到小排（数据库连接通常在主进程）。"""
    sizes = []
    for pid in pids:
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            continue
        total = 0
        for mbi in _regions(h):
            if mbi.State == MEM_COMMIT and mbi.Protect in PAGE_READABLE \
                    and mbi.RegionSize <= MAX_REGION:
                total += mbi.RegionSize
        k32.CloseHandle(h)
        sizes.append((pid, total))
    sizes.sort(key=lambda x: -x[1])
    return sizes


# ---------------- 并行扫描（多进程分摊「候选过滤 + AES 校验」） ----------------

_WORKER: dict = {}


def _init_worker(probes: list, pids: list):
    _WORKER["probes"] = probes
    _WORKER["handles"] = {}
    _WORKER["pids"] = set(pids)


def _worker_handle(pid: int):
    h = _WORKER["handles"].get(pid)
    if h is None:
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            raise OSError(f"OpenProcess({pid}) err={ctypes.get_last_error()}")
        _WORKER["handles"][pid] = h
    return h


def _scan_chunk(task):
    """worker：读一块内存 → 过滤候选 → 校验，返回 [(db名, key)]。"""
    pid, base, size, stride = task
    try:
        buf = _read(_worker_handle(pid), base, size)
    except OSError:
        return []
    if not buf or len(buf) < 65:
        return []
    cands = _candidates(buf, stride=stride, base=base)
    if stride == 1:
        cands = cands + _hex_candidates(buf)
    hits = []
    for key in cands:
        for name, head in _WORKER["probes"]:
            try:
                if check_key(key, head):
                    hits.append((name, key))
                    break
                # OpenSSL 在小端机器上按 GETU32 存 rd_key（每 4 字节字节序反转），
                # 内存里的“密钥”很可能是反序形态，还原后再验
                rev = b"".join(key[i:i + 4][::-1] for i in range(0, 32, 4))
                if check_key(rev, head):
                    hits.append((name, rev))
                    break
            except Exception:
                continue
    return hits


def find_keys(db_paths: list[str], pids: list[int] | None = None,
              log=lambda s: None, workers: int = 0) -> dict:
    """返回 {db_path: key}。密钥只存在于本进程内存。

    扫描策略：stride 16 → 4 → 1 逐级加密度；每级把所有微信进程的可读内存切成
    32MB 块，用进程池并行「过滤 + 校验」。某个库命中后即从目标里移除。
    """
    probes = []
    for d in db_paths:
        head = page1_head(d)
        if len(head) == PAGE_SIZE:
            probes.append((os.path.basename(d), head))
        else:
            log(f"[keyfind] 跳过（文件不可读或过小）: {d}")
    if not probes:
        return {}

    pids = list(pids or list_weixin_pids())
    if not pids:
        raise RuntimeError("未检测到微信客户端进程：请先启动并登录微信"
                           "（取密钥必须访问运行中的进程内存）")

    found: dict = {}          # db_path -> key
    by_name = {name: path for path, name in [(d, os.path.basename(d)) for d in db_paths]}

    def record(hits):
        for name, key in hits:
            path = by_name.get(name, name)
            if path not in found:
                found[path] = key
                log(f"[keyfind] 命中 {name}")

    ranked = _pid_memory(pids)
    order = [pid for pid, _ in ranked]
    log(f"[keyfind] 微信进程：{[f'pid {p} ({s/1e6:.0f}MB)' for p, s in ranked]}")

    use_pool = (os.name == "nt" and (workers or os.cpu_count() or 2) > 1)
    pool = ctx = None
    if use_pool:
        try:
            import multiprocessing as mp
            n = workers or max(2, min(10, (os.cpu_count() or 4) - 1))
            ctx = mp.get_context("spawn")
            pool = ctx.Pool(n, initializer=_init_worker, initargs=(probes, order))
            log(f"[keyfind] 并行扫描：{n} 进程")
        except Exception as e:
            log(f"[keyfind] 并行不可用（{e}），退回单进程")
            pool = None

    try:
        for stride in (16, 4, 1):
            if len(found) >= len(probes):
                break
            t0 = time.time()
            log(f"[keyfind] stride={stride} 全量扫描中…")
            cand = 0
            if pool is not None:
                tasks = [(pid, base, size, stride)
                         for pid in order
                         for base, size in _chunk_cache(pid, order, ranked)]
                done = 0
                for hits in pool.imap_unordered(_scan_chunk, tasks, chunksize=4):
                    done += 1
                    record(hits)
                    if done % 40 == 0:
                        log(f"[keyfind] stride={stride} 进度 {done}/{len(tasks)} 块，"
                            f"已找到 {len(found)}/{len(probes)}")
                    if len(found) >= len(probes):
                        break
            else:
                for pid in order:
                    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
                    if not h:
                        log(f"[keyfind] 无法打开 pid {pid}（err={ctypes.get_last_error()}）")
                        continue
                    try:
                        for base, size in _region_chunks(h):
                            hits = _scan_chunk((pid, base, size, stride))
                            record(hits)
                            if len(found) >= len(probes):
                                break
                    finally:
                        k32.CloseHandle(h)
            log(f"[keyfind] stride={stride} 完成，{time.time()-t0:.0f}s，"
                f"已找到 {len(found)}/{len(probes)}")
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
    return found


_CHUNKS: dict = {}


def _chunk_cache(pid, order, ranked):
    """主进程侧缓存每个 pid 的块列表（区域枚举一次，多轮 stride 复用）。"""
    if pid not in _CHUNKS:
        h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
        if not h:
            _CHUNKS[pid] = []
        else:
            try:
                _CHUNKS[pid] = _region_chunks(h)
            finally:
                k32.CloseHandle(h)
    return _CHUNKS[pid]
