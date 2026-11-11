"""取钥兜底：动态定位并 hook AES 密钥扩展函数（Frida，只读参数，不写进程）。

为什么需要：微信 4.x 不在内存里长期保留 32 字节原始密钥（实测 raw key /
GETU32 反序形态 / OpenSSL AES_KEY 结构 / 口令串+KDF 四种扫描均无命中），
但每次页加解密都会调用 AES_set_encrypt_key 初始化上下文——hook 它即可拿到
流经参数的真实密钥。

为什么不写死 RVA：不同小版本函数地址会变。这里在**宿主侧**解析 Weixin.dll：
用 .pdata 函数表 + `66 0F 3A DF`（aeskeygenassist）指令计数，定位包含
密钥扩展循环的函数（通常 1–2 个：set_encrypt_key / set_decrypt_key），
运行时按模块基址 + RVA 挂 hook，与版本无关。

安全边界：只读函数入参（key 指针指向的 32 字节），绝不写进程内存；
密钥经页面校验后只存在于本进程内存。
"""
from __future__ import annotations

import os
import re
import struct
import time

AESKEYGEN = re.compile(rb"\x66\x0f\x3a\xdf", re.S)
MIN_MATCHES = 6


# ------------------------------------------------------------------ PE 解析

def _pe_sections(data: bytes):
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    nsec = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    opt_size = struct.unpack_from("<H", data, e_lfanew + 20)[0]
    sec_off = e_lfanew + 24 + opt_size
    sections = []
    for i in range(nsec):
        o = sec_off + i * 40
        name = data[o:o + 8].rstrip(b"\x00").decode("ascii", "ignore")
        vsize, vaddr, rawsize, raw = struct.unpack_from("<IIII", data, o + 8)
        sections.append((name, vaddr, vsize, raw, rawsize))
    return sections


def _pdata_ranges(data: bytes, sections):
    """x64 .pdata：每项 12 字节 (start_rva, end_rva, unwind)。"""
    for name, vaddr, vsize, raw, rawsize in sections:
        if name == ".pdata" and rawsize:
            blob = data[raw:raw + rawsize]
            out = []
            for i in range(0, len(blob) - 11, 12):
                s, e, _u = struct.unpack_from("<III", blob, i)
                if s == 0:
                    break
                out.append((s, e))
            return out
    return []


def locate_keyexpansion_rvas(dll_path: str) -> list[int]:
    """在 Weixin.dll 里定位含 aeskeygenassist 的函数（返回 RVA 列表）。"""
    with open(dll_path, "rb") as f:
        data = f.read()
    sections = _pe_sections(data)
    text = next(((vaddr, vsize, raw, rawsize)
                 for name, vaddr, vsize, raw, rawsize in sections
                 if name in (".text", ".code")), None)
    if not text:
        raise RuntimeError("Weixin.dll 里没有 .text 节")
    vaddr, vsize, raw, rawsize = text
    code = data[raw:raw + rawsize]

    ranges = _pdata_ranges(data, sections)
    if not ranges:
        raise RuntimeError("Weixin.dll 里没有 .pdata 函数表")

    import bisect
    starts = [r[0] for r in ranges]

    def func_of(rva: int):
        i = bisect.bisect_right(starts, rva) - 1
        if i >= 0 and ranges[i][0] <= rva < ranges[i][1]:
            return ranges[i]
        return None

    counts: dict[int, int] = {}
    for m in AESKEYGEN.finditer(code):
        rva = vaddr + m.start()
        fn = func_of(rva)
        if fn:
            counts[fn[0]] = counts.get(fn[0], 0) + 1
    hits = sorted((cnt, rva) for rva, cnt in counts.items() if cnt >= MIN_MATCHES)
    return [rva for _, rva in hits]


def find_weixin_dll(pid_hint_pids: list[int] | None = None) -> str:
    """从安装目录猜 Weixin.dll 路径（C:/Program Files/Tencent/Weixin/<ver>/Weixin.dll）。"""
    cands = []
    for drive in ("C:", "D:", "E:"):
        base = os.path.join(drive, os.sep, "Program Files", "Tencent", "Weixin")
        if not os.path.isdir(base):
            continue
        for ver in os.listdir(base):
            p = os.path.join(base, ver, "Weixin.dll")
            if os.path.isfile(p):
                cands.append(p)
    if not cands:
        raise FileNotFoundError("未找到 Weixin.dll（请确认微信安装于 Program Files/Tencent/Weixin）")
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


# ------------------------------------------------------------------ Frida hook

_HOOK_JS = r"""
var base = null;
Process.enumerateModules().forEach(function (m) {
    if (!base && m.name.toLowerCase() === 'weixin.dll') base = m.base;
});
if (!base) { send({t:'err', msg:'Weixin.dll not found'}); }
else {
  send({t:'info', msg:'base=' + base});
  __RVAS__.forEach(function (rva) {
    try {
      Interceptor.attach(base.add(rva), {
          onEnter: function (args) {
              try { send({t:'k'}, args[0].readByteArray(32)); } catch (e) {}
          }
      });
    } catch (e) { send({t:'err', msg:'attach ' + rva + ': ' + e}); }
  });
}
"""


def capture_keys_via_hook(pids: list[int], db_paths: list[str],
                          seconds: float = 120, log=lambda s: None) -> dict:
    """hook 所有候选微信进程，返回 {db_path: key}。密钥只在本进程内存。"""
    from .crypto import PAGE_SIZE, check_key, page1_head

    import frida                                   # 局部导入：未装 frida 时其余功能可用
    probes = []
    for d in db_paths:
        head = page1_head(d)
        if len(head) == PAGE_SIZE:
            probes.append((os.path.basename(d), head))
    if not probes:
        return {}

    dll = find_weixin_dll()
    rvas = locate_keyexpansion_rvas(dll)
    if not rvas:
        raise RuntimeError("未在 Weixin.dll 中定位到 AES 密钥扩展函数（微信大版本可能改动了实现）")
    log(f"[hook] Weixin.dll={dll}")
    log(f"[hook] 候选密钥扩展函数 {len(rvas)} 个（RVA {['0x%x' % r for r in rvas]}）")

    found: dict = {}
    seen: set = set()
    sessions = []

    def make_on_msg(who):
        def on_msg(m, data):
            if m.get("type") == "send" and data:
                k = bytes(data)
                if len(k) != 32:
                    return
                for name, head in probes:
                    if name in seen:
                        continue
                    try:
                        if check_key(k, head):
                            seen.add(name)
                            found[by_name[name]] = k
                            log(f"[hook] 命中 {name}（pid {who}）")
                    except Exception:
                        pass
            elif m.get("type") == "error":
                log(f"[hook] pid {who} 脚本错误: {m.get('description')}")
            elif m.get("type") == "send" and not data:
                p = m.get("payload", {})
                if p.get("t") == "err":
                    log(f"[hook] pid {who}: {p.get('msg')}")
        return on_msg

    by_name = {os.path.basename(d): d for d in db_paths}
    attached = 0
    for pid in pids:
        try:
            sess = frida.attach(pid)
            script = sess.create_script(_HOOK_JS.replace("__RVAS__", repr([int(r) for r in rvas])))
            script.on("message", make_on_msg(pid))
            script.load()
            sessions.append((sess, script))
            attached += 1
        except Exception as e:
            log(f"[hook] 无法附加 pid {pid}: {e}")
    if not attached:
        raise RuntimeError("Frida 未能附加任何微信进程（是否被杀软拦截？）")

    t0 = time.time()
    try:
        while time.time() - t0 < seconds and len(found) < len(probes):
            time.sleep(0.3)
    finally:
        for sess, script in sessions:
            try:
                script.unload()
            except Exception:
                pass
            try:
                sess.detach()
            except Exception:
                pass
    return found
