#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 1 · 抽取个人微信群聊记录（只读，明文临时库自动清理）。

输出到 run-dir：
    messages.json   完整消息（机器读）
    messages.txt    一行一条（人读 / grep）
    facts.json      确定性统计（条数、发言人、时段、文件、链接、关键词）
    digest.md       紧凑转录稿（供上层归纳结论，带消息编号）

用法：
    python extract.py --list --hours 48
    python extract.py --group "产品研发群" --hours 24
    python extract.py --group "家" --hours 48 --out D:/reports/jia
    python extract.py --group "家" --hours 24 --account wxid_xxxxxxxxxxxx

安全边界：密钥只在内存、原始库只读、临时明文库退出即删。
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
from collections import Counter, OrderedDict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wxread import source                                   # noqa: E402
from wxread.crypto import decrypt_db                        # noqa: E402
from wxread.keyfind import find_keys, list_weixin_pids      # noqa: E402
from wxread.parser import KIND_LABEL, normalize             # noqa: E402

TZ = datetime.now().astimezone().tzinfo
TMP_DIRS: list[str] = []

STOP = set("""老师 收到 好的 谢谢 可以 我们 这个 就是 什么 需要 已经 现在 今天 明天 大家 一个
没有 还是 然后 因为 所以 如果 但是 那个 这样 那样 时候 自己 一下 麻烦 辛苦 请问 有人
不是 应该 可能 直接 以及 还有 或者 不过 而且 感觉 觉得 知道 看看 这里 那里 多少 怎么
怎么样 的话 一次 两个 三个 之后 之前 目前 具体 相关 方面 问题 情况 内容 需求 时间 版本
同学 老师好 图片 文件 表情 链接 引用 消息 抱歉 感谢 嗯嗯 收到啦 哈哈哈 不是 就是 真的
""".split())

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")
URL_RE = re.compile(r"https?://[^\s，。；）)]+")


# ------------------------------------------------------------------ 清理

def new_tmp_dir() -> str:
    d = tempfile.mkdtemp(prefix="wxchat_plain_")
    TMP_DIRS.append(d)
    return d


def cleanup(*_):
    for d in TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    TMP_DIRS.clear()


def install_signal_cleanup():
    """被 SIGTERM/SIGINT 终止时也清理临时明文库（atexit 不保证触发）。"""
    def handler(signum, _frame):
        cleanup()
        raise SystemExit(128 + signum)
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is not None:
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass


# ------------------------------------------------------------------ 取钥 + 解密

def plain_databases(account_dir: str, tmpdir: str,
                    log=lambda s: print(s, flush=True),
                    capture_seconds: float = 300) -> dict[str, str]:
    """取钥并解密核心库，返回 {库名: 明文路径}。密钥只在本进程内存。

    两级策略：① Frida hook AES 密钥扩展函数（快，配合在微信里点开群/通讯录，
    秒级命中）；② 只读内存扫描（无 frida 时的兜底，较慢）。
    """
    from wxread.keyfind import find_keys

    try:
        from wxread.hookfind import capture_keys_via_hook
        hooking = True
    except ImportError:
        hooking = False
        log("[extract] 未安装 frida，退化为内存扫描（pip install frida 可显著提速）")

    dbs = source.db_files(account_dir)
    if "message_0.db" not in dbs:
        raise RuntimeError(f"{account_dir} 下没有 message/message_0.db（账号目录不完整或未登录过）")

    targets = {name: path for name, path in dbs.items()}
    pids = list_weixin_pids()
    if not pids:
        raise RuntimeError("未检测到微信客户端进程：请先启动并登录微信")

    deadline = time.time() + capture_seconds
    keys: dict[str, bytes] = {}
    attempt = 0
    while time.time() < deadline:
        todo = [p for p in targets.values() if p not in keys]
        if not todo:
            break
        attempt += 1
        if hooking:
            log(f"[extract] 第 {attempt} 轮 hook 捕获（请在微信里点开目标群、滚动聊天记录；"
                f"缺 contact 时点开「通讯录」）…")
            got = capture_keys_via_hook(pids, todo,
                                        seconds=min(75, max(20, deadline - time.time())),
                                        log=log)
        else:
            log(f"[extract] 第 {attempt} 轮内存扫描（1–4 分钟，微信保持活跃）…")
            got = find_keys(todo, pids=pids, log=log)
        for path, key in got.items():
            keys[os.path.basename(path)] = key
        if {"message_0.db", "contact.db"} <= set(keys):
            break
        if time.time() + 15 >= deadline:
            break

    if not keys:
        raise RuntimeError(
            "没有取到任何密钥。请确认：① 微信客户端已登录并保持运行；"
            "② 以管理员身份运行本命令；③ 捕获期间在微信里点开群聊/滚动聊天记录。")
    if "message_0.db" not in keys:
        raise RuntimeError("未捕获 message_0.db 密钥：请在捕获期间打开目标群、"
                           "滚动聊天记录后重试。")

    plain: dict[str, str] = {}
    for name, enc in targets.items():
        if name not in keys:
            log(f"[extract] 警告：未取得 {name} 密钥，相关功能降级")
            continue
        dst = os.path.join(tmpdir, "plain_" + name)
        info = decrypt_db(enc, dst, keys[name])
        conn = sqlite3.connect(dst)
        try:
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
        if check != "ok":
            raise RuntimeError(f"{name} 解密后校验失败：{check}")
        log(f"[extract] 解密 {name}: pages={info['pages']} wal={info['wal_frames']} quick_check=ok")
        plain[name] = dst
    keys.clear()
    return plain


# ------------------------------------------------------------------ 群定位

def resolve_group(plain: dict, keyword: str, names_map=None):
    """返回 (chat|None, groups|list, ambiguous|list)。"""
    contact = None
    if "contact.db" in plain:
        contact = sqlite3.connect(plain["contact.db"])
        contact.row_factory = sqlite3.Row
    groups = source.list_groups(contact)
    if not groups and "session.db" in plain:
        sess = sqlite3.connect(plain["session.db"])
        groups = source.list_groups_from_session(sess)
        sess.close()
    if not keyword:
        return None, groups, []

    exact = [g for g in groups if keyword == g["name"] or keyword == g["username"]]
    if len(exact) == 1:
        return exact[0], groups, []
    if not exact:
        sub = [g for g in groups if keyword in g["name"]]
        if len(sub) == 1:
            return sub[0], groups, []
        if sub:
            return None, groups, sub
        return None, groups, []
    return None, groups, exact


# ------------------------------------------------------------------ 消息解析

def parse_messages(plain: dict, chat_id: str, db_tag: str = "m0", names_map=None):
    conn = sqlite3.connect(plain["message_0.db"])
    table = source.msg_table_for(conn, chat_id)
    if not table:
        conn.close()
        raise LookupError(
            f"message_0.db 里没有该群的消息表（Msg_{__import__('hashlib').md5(chat_id.encode()).hexdigest()}）"
            "，说明这个群的记录没有同步到本机")

    cols = source.columns(conn, table)
    for need in ("local_id", "local_type", "create_time", "real_sender_id", "message_content"):
        if need not in cols:
            conn.close()
            raise RuntimeError(f"表 {table} 缺少字段 {need}；实际字段：{cols}")
    order = "sort_seq" if "sort_seq" in cols else "local_id"

    contact = sqlite3.connect(plain["contact.db"]) if "contact.db" in plain else None
    id2name = source.sender_map_msgdb(conn) or {}
    disp = source.display_names(contact)
    for k, v in (names_map or {}).items():       # 外部昵称字典只补空缺
        disp.setdefault(k, v)
    is_group = chat_id.endswith("@chatroom")
    if is_group and contact is not None:
        disp.update({k: v for k, v in source.group_members(contact, chat_id).items()})

    msgs = []
    q = (f'SELECT local_id, local_type, create_time, real_sender_id, message_content, '
         f'compress_content, {order} FROM "{table}" ORDER BY {order} ASC')
    for local_id, ltype, ctime, sender_id, content, compressed, seq in conn.execute(q):
        msgs.append(normalize(db_tag, local_id, ltype, ctime, sender_id, content,
                              compressed, id2name, disp, seq, group=is_group))
    conn.close()
    if contact is not None:
        contact.close()
    return msgs


def filter_window(msgs, start: int, end: int):
    """[start, end) 语义，按业务键去重。"""
    seen, out = set(), []
    for m in sorted(msgs, key=lambda x: (x.ts, x.seq)):
        if not (start <= m.ts < end):
            continue
        key = (m.ts, m.sender_id, m.kind, (m.text or "")[:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return out


def to_records(msgs) -> list[dict]:
    records = []
    for i, m in enumerate(msgs, 1):
        label = KIND_LABEL.get(m.kind, "其他")
        content = m.text or ""
        if m.kind == "file" and m.extra.get("file_name"):
            content = m.extra["file_name"]
        if m.quote and m.quote.get("text"):
            content = f"{content}（引用 {m.quote.get('sender', '')}：{m.quote['text'][:60]}）"
        records.append({
            "id": i,
            "time": datetime.fromtimestamp(m.ts, TZ).isoformat(),
            "sender_username": m.sender_id,
            "sender_name": m.sender_name or m.sender_id,
            "type": label,
            "content": content,
            "raw_type": m.raw_type,
            "extra": m.extra,
        })
    return records


# ------------------------------------------------------------------ 统计 / 摘要

def build_facts(payload: dict) -> dict:
    msgs = payload["messages"]
    start = datetime.fromisoformat(payload["start"])
    end = datetime.fromisoformat(payload["end_exclusive"])
    types = Counter(m["type"] for m in msgs)
    senders = Counter(m["sender_name"] for m in msgs)

    hours = [0] * 24
    days = OrderedDict()
    for m in msgs:
        dt = datetime.fromisoformat(m["time"])
        hours[dt.hour] += 1
        d = days.setdefault(dt.strftime("%Y-%m-%d"),
                            {"messages": 0, "senders": Counter(), "types": Counter()})
        d["messages"] += 1
        d["senders"][m["sender_name"]] += 1
        d["types"][m["type"]] += 1

    day_rows = [{"date": d, "messages": v["messages"], "senders": len(v["senders"]),
                 "top_senders": [[n, c] for n, c in v["senders"].most_common(3)],
                 "types": dict(v["types"])} for d, v in days.items()]

    files, links = [], []
    for m in msgs:
        if m["type"] == "文件":
            files.append({"id": m["id"], "time": m["time"], "sender": m["sender_name"],
                          "name": (m["content"] or "").strip()[:80]})
        for u in URL_RE.findall(m["content"] or ""):
            links.append({"id": m["id"], "time": m["time"], "sender": m["sender_name"], "url": u})

    # 轻量关键词：跨多人重复出现的中文 2~4 字片段
    grams, gram_senders = Counter(), {}
    for m in msgs:
        if m["type"] != "文本":
            continue
        text = re.sub(r"\s+", "", m["content"] or "")
        seen = set()
        for run in CJK_RE.findall(text):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g in STOP or g in seen:
                        continue
                    seen.add(g)
                    grams[g] += 1
                    gram_senders.setdefault(g, set()).add(m["sender_name"])
    keywords = [{"word": g, "count": c, "senders": len(gram_senders[g])}
                for g, c in grams.most_common(400) if c >= 3 and len(gram_senders[g]) >= 2]
    keep = []
    for k in keywords:
        if any(k["word"] != o["word"] and k["word"] in o["word"]
               and o["count"] >= k["count"] * 0.8 for o in keywords):
            continue
        keep.append(k)
        if len(keep) >= 24:
            break

    longest = sorted([m for m in msgs if m["type"] == "文本"],
                     key=lambda m: -(len(m["content"] or "")))[:8]

    return {
        "group": payload["group"],
        "conversation_id": payload.get("conversation_id", ""),
        "start": payload["start"],
        "end": payload["end_exclusive"],
        "hours": round((end - start).total_seconds() / 3600, 1),
        "count": len(msgs),
        "types": dict(types),
        "senders": [[n, c] for n, c in senders.most_common()],
        "sender_count": len(senders),
        "hours_hist": hours,
        "days": day_rows,
        "files": files,
        "links": links,
        "keywords": keep,
        "longest": [{"id": m["id"], "time": m["time"], "sender": m["sender_name"],
                     "len": len(m["content"] or ""), "text": (m["content"] or "")[:160]}
                    for m in longest],
        "empty_text": sum(1 for m in msgs if m["type"] == "文本" and not (m["content"] or "").strip()),
    }


def build_digest(payload: dict, facts: dict, max_chars: int = 24000) -> str:
    """紧凑、带编号的转录稿，供上层写总结时引用。"""
    msgs = payload["messages"]
    out = [
        f"# {facts['group']}",
        f"窗口：{facts['start']} → {facts['end']}（{facts['hours']:g} 小时，不含终点）",
        f"消息：{facts['count']} 条 / {facts['sender_count']} 人；类型 {facts['types']}",
        "格式：[编号] HH:MM 发言人: 正文",
        "",
    ]
    budget = max_chars - sum(len(x) + 1 for x in out)
    cur_day = None
    for m in msgs:
        dt = datetime.fromisoformat(m["time"])
        if dt.strftime("%Y-%m-%d") != cur_day:
            cur_day = dt.strftime("%Y-%m-%d")
            out.append(f"\n## {cur_day}")
        body = re.sub(r"\s+", " ", m["content"] or "").strip()
        if m["type"] != "文本":
            body = f"[{m['type']}] {body}"
        line = f"[{m['id']}] {dt:%H:%M} {m['sender_name']}: {body[:220]}"
        budget -= len(line) + 1
        if budget < 0:
            out.append(f"…（共 {facts['count']} 条，后文略）")
            break
        out.append(line)
    return "\n".join(out)


# ------------------------------------------------------------------ 列表模式

def list_recent_groups(plain: dict, hours: float, limit: int = 30) -> list[dict]:
    contact = sqlite3.connect(plain["contact.db"]) if "contact.db" in plain else None
    names = {g["username"]: g["name"] for g in source.list_groups(contact)}
    if contact:
        contact.close()

    last: dict[str, float] = {}
    if "session.db" in plain:
        conn = sqlite3.connect(plain["session.db"])
        if source.table_exists(conn, "SessionTable"):
            cols = source.columns(conn, "SessionTable")
            ts_col = next((c for c in ("last_timestamp", "last_msg_time", "last_time",
                                       "update_time", "timestamp") if c in cols), None)
            if ts_col and "username" in cols:
                for un, ts in conn.execute(
                        f'SELECT username, "{ts_col}" FROM SessionTable'):
                    if un and "@chatroom" in un and ts:
                        last[un] = float(ts) / (1000 if float(ts) > 10 ** 12 else 1)
        conn.close()

    if not names:                       # 通讯录不可用时用会话表兜底
        for un in last:
            names[un] = un

    cutoff = time.time() - hours * 3600
    rows = [{"id": un, "name": nm, "last": last.get(un, 0)} for un, nm in names.items()]
    rows = [r for r in rows if not last or r["last"] >= cutoff]
    rows.sort(key=lambda r: (-r["last"], r["name"]))
    return rows[:limit]


# ------------------------------------------------------------------ main

def parse_end(value: str) -> int:
    if not value:
        return int(time.time())
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(value, fmt).timestamp())
        except ValueError:
            continue
    raise SystemExit(f"无法解析 --end：{value}（示例 2026-10-31T09:00:00）")


def main() -> int:
    ap = argparse.ArgumentParser(description="个人微信群聊记录抽取（只读，明文临时库自动清理）")
    ap.add_argument("--group", help="群名称（完整或部分匹配）或群 username（xxx@chatroom）")
    ap.add_argument("--hours", type=float, default=24, help="统计窗口小时数，默认 24")
    ap.add_argument("--end", help="窗口终点 ISO8601，默认当前时间")
    ap.add_argument("--account", default="", help="多账号时指定 wxid")
    ap.add_argument("--data-root", default="", help="xwechat_files 目录，默认自动探测")
    ap.add_argument("--out", help="输出目录，默认 ./reports/<时间戳>-<群名>")
    ap.add_argument("--list", action="store_true", help="只列出最近活跃的群，不导出")
    ap.add_argument("--capture-seconds", type=float, default=300,
                    help="等待取到密钥的最长秒数，默认 300")
    ap.add_argument("--names", help="昵称字典 JSON：{\"names\": {\"wxid_x\": \"昵称\"}}（只补空缺）")
    args = ap.parse_args()

    root = source.find_data_root(args.data_root)
    account = source.pick_account(root, args.account)
    print(f"数据目录：{root}", flush=True)
    print(f"账号目录：{account['dir']}（wxid={account['wxid']}）", flush=True)

    tmp = new_tmp_dir()
    plain = plain_databases(account["dir"], tmp, capture_seconds=args.capture_seconds)

    if args.list:
        rows = list_recent_groups(plain, args.hours)
        print(f"\n最近 {args.hours:g} 小时活跃群（{len(rows)} 个）：")
        for r in rows:
            when = datetime.fromtimestamp(r["last"], TZ).strftime("%m-%d %H:%M") if r["last"] else "—"
            print(f"  {r['name']}  [{r['id']}]  最后消息 {when}")
        return 0

    if not args.group:
        ap.error("--group 必填（可先用 --list 查看群名）")

    chat, groups, ambiguous = resolve_group(plain, args.group)
    if ambiguous:
        print("存在同名/相似群，请用完整群名或 username 指定：")
        for g in ambiguous:
            print(f"  {g['name']}  [{g['username']}]")
        return 2
    if chat is None:
        print(f"没有匹配到群「{args.group}」。可用群聊（共 {len(groups)} 个，前 40 个）：")
        for g in groups[:40]:
            print(f"  {g['name']}  [{g['username']}]")
        if "contact.db" not in plain:
            print("\n提示：通讯录库未解密，群名可能不完整——可改用群 username 指定，"
                  "或重试并在扫描期间点开微信「通讯录」。")
        return 2

    end = parse_end(args.end)
    start = end - int(args.hours * 3600)
    print(f"目标群：{chat['name']} [{chat['username']}]", flush=True)
    print(f"时间范围：[{datetime.fromtimestamp(start, TZ):%Y-%m-%d %H:%M}, "
          f"{datetime.fromtimestamp(end, TZ):%Y-%m-%d %H:%M}) 含起点不含终点")

    names_map = {}
    if args.names:
        with open(args.names, encoding="utf-8") as f:
            names_map = {k: v for k, v in (json.load(f).get("names") or {}).items() if k and v}
        print(f"昵称字典：{args.names}（{len(names_map)} 条，仅补空缺）")

    try:
        msgs = parse_messages(plain, chat["username"], names_map=names_map)
    except LookupError as e:
        print(f"错误：{e}")
        return 3
    window = filter_window(msgs, start, end)
    print(f"本群本地全量 {len(msgs)} 条；窗口内（去重后）{len(window)} 条")

    out_dir = args.out or os.path.join(
        os.getcwd(), "reports", f"{datetime.now(TZ):%Y%m%d-%H%M%S}-"
                                f"{re.sub(r'[^\w\u4e00-\u9fff-]', '', chat['name'])[:20]}")
    os.makedirs(out_dir, exist_ok=True)

    payload = {
        "group": chat["name"],
        "conversation_id": chat["username"],
        "start": datetime.fromtimestamp(start, TZ).replace(microsecond=0).isoformat(),
        "end_exclusive": datetime.fromtimestamp(end, TZ).replace(microsecond=0).isoformat(),
        "message_count": len(window),
        "duplicates_removed": len(msgs) - len(window) if len(window) < len(msgs) else 0,
        "source": {"kind": "personal-wechat", "account_wxid": account["wxid"],
                   "data_root": root},
        "messages": to_records(window),
    }
    with open(os.path.join(out_dir, "messages.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "messages.txt"), "w", encoding="utf-8") as f:
        f.write(f"# {chat['name']}  {payload['start']} — {payload['end_exclusive']}\n")
        f.write(f"# {len(window)} 条消息\n\n")
        for m in payload["messages"]:
            stamp = datetime.fromisoformat(m["time"]).strftime("%m-%d %H:%M:%S")
            body = m["content"] or f"[{m['type']}]"
            f.write(f"[{m['id']:03d}] {stamp} {m['sender_name']}: {body}\n")

    facts = build_facts(payload)
    with open(os.path.join(out_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "digest.md"), "w", encoding="utf-8") as f:
        f.write(build_digest(payload, facts))

    print(f"消息：{len(window)} 条 / {facts['sender_count']} 人 / 类型 {facts['types']}")
    print(f"输出：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    install_signal_cleanup()
    try:
        code = main()
    finally:
        cleanup()
    raise SystemExit(code)
