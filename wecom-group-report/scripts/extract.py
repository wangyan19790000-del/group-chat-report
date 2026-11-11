#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 1 · Extract a WeCom group's messages for a time window.

Reads the local (encrypted) WeCom databases in read-only mode, decrypts them into
a temp dir that is wiped on exit, and exports:
    <run-dir>/messages.json   full payload (machine)
    <run-dir>/messages.txt    one line per message (human / grep)
    <run-dir>/facts.json      deterministic statistics (counts, senders, hours)
    <run-dir>/digest.md       compact reading material for the summarising step

Usage:
    python extract.py --group "产品研发群" --hours 24
    python extract.py --group "示例项目群" --hours 48 --out D:/reports/ppt
    python extract.py --list --hours 48          # only list recent groups, no export
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, OrderedDict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from wxwork_reader.db import TZ, PlainDBs, find_db_dir, export  # noqa: E402

STOP = set("""老师 收到 好的 谢谢 可以 我们 这个 就是 什么 需要 已经 现在 今天 明天 大家 一个 没有
还是 然后 因为 所以 如果 但是 这个 那个 这样 那样 时候 自己 一下 麻烦 辛苦 请问 有人 不是
已经 应该 可能 直接 以及 还有 或者 不过 而且 感觉 觉得 知道 看看 这里 那里 多少 怎么 怎么样
的话 一次 两个 三个 一下 之后 之前 目前 具体 相关 方面 问题 情况 内容 需求 时间 版本 同学 老师好
@所有人 图片 文件 表情 链接 引用 消息 抱歉 感谢 好的呀 嗯嗯 收到啦 哈哈哈""".split())

CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,6}")
URL_RE = re.compile(r"https?://[^\s，。；）)]+")


# ------------------------------------------------------------------ statistics

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
        d = days.setdefault(dt.strftime("%Y-%m-%d"), {"messages": 0, "senders": Counter(), "types": Counter()})
        d["messages"] += 1
        d["senders"][m["sender_name"]] += 1
        d["types"][m["type"]] += 1

    day_rows = [{
        "date": d,
        "messages": v["messages"],
        "senders": len(v["senders"]),
        "top_senders": [[n, c] for n, c in v["senders"].most_common(3)],
        "types": dict(v["types"]),
    } for d, v in days.items()]

    files, links = [], []
    for m in msgs:
        if m["type"] == "文件":
            files.append({"id": m["id"], "time": m["time"], "sender": m["sender_name"],
                          "name": (m["content"] or "").strip()[:80]})
        for u in URL_RE.findall(m["content"] or ""):
            links.append({"id": m["id"], "time": m["time"], "sender": m["sender_name"], "url": u})

    # light keyword hint: CJK n-grams that recur across several messages / senders
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
    keywords = [
        {"word": g, "count": c, "senders": len(gram_senders[g])}
        for g, c in grams.most_common(400)
        if c >= 3 and len(gram_senders[g]) >= 2 and not any(g in o["word"] for o in [])
    ]
    # drop substrings of a longer equally-frequent gram
    keep = []
    for k in keywords:
        if any(k["word"] != o["word"] and k["word"] in o["word"] and o["count"] >= k["count"] * 0.8
               for o in keywords):
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
                     "len": len(m["content"] or ""), "text": (m["content"] or "")[:160]} for m in longest],
        "empty_text": sum(1 for m in msgs if m["type"] == "文本" and not (m["content"] or "").strip()),
    }


def build_digest(payload: dict, facts: dict, max_chars: int = 24000) -> str:
    """Compact, ID-tagged transcript used by the summarising step."""
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
        body = body[:220]
        line = f"[{m['id']}] {dt:%H:%M} {m['sender_name']}: {body}"
        budget -= len(line) + 1
        if budget < 0:
            out.append(f"…（共 {facts['count']} 条，后文略）")
            break
        out.append(line)
    return "\n".join(out)


def list_groups(db_dir: str, hours: int, limit: int = 20) -> list[dict]:
    dbs = PlainDBs(db_dir).open()
    try:
        import sqlite3
        con = sqlite3.connect(dbs.path("session.db"))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "select id,name,roomname_remark,last_message_time from conversation_table "
            "order by last_message_time desc limit ?", (limit,)).fetchall()
        cutoff = datetime.now(TZ).timestamp() - hours * 3600
        out = []
        for r in rows:
            if not r["last_message_time"] or r["last_message_time"] < cutoff:
                continue
            name = r["roomname_remark"] or r["name"] or ""
            if r["id"].startswith("R:"):
                out.append({"id": r["id"], "name": name,
                            "last": datetime.fromtimestamp(r["last_message_time"], TZ).isoformat()})
        con.close()
        return out
    finally:
        dbs.cleanup()


def main() -> int:
    ap = argparse.ArgumentParser(description="企业微信群聊记录抽取（只读，明文临时文件自动清理）")
    ap.add_argument("--group", help="群名称（支持部分匹配，如 产品研发）")
    ap.add_argument("--hours", type=float, default=24, help="统计窗口小时数，默认 24")
    ap.add_argument("--end", help="窗口终点 ISO8601，默认当前时间")
    ap.add_argument("--db-dir", help="企业微信 Data 目录，默认自动探测")
    ap.add_argument("--out", help="输出目录，默认 ./reports/<时间戳>-<群ID>")
    ap.add_argument("--list", action="store_true", help="只列出最近活跃的群，不导出")
    args = ap.parse_args()

    db_dir = find_db_dir(args.db_dir)
    print(f"数据目录：{db_dir}")

    if args.list:
        for g in list_groups(db_dir, args.hours):
            print(f"  {g['name']}  ({g['id']})  最后消息 {g['last'][:16].replace('T', ' ')}")
        return 0

    if not args.group:
        ap.error("--group 必填（或用 --list 先看群名）")

    # export() re-opens the DBs; reuse its pipeline for consistency
    from wxwork_reader.db import export
    out_dir = args.out or os.path.join(
        os.getcwd(), "reports", f"{datetime.now(TZ):%Y%m%d-%H%M%S}")
    end = datetime.fromisoformat(args.end) if args.end else None
    res = export(args.group, hours=int(round(args.hours)), db_dir=db_dir, end=end, out_dir=out_dir)

    payload = json.load(open(os.path.join(out_dir, "messages.json"), encoding="utf-8"))
    facts = build_facts(payload)
    with open(os.path.join(out_dir, "facts.json"), "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "digest.md"), "w", encoding="utf-8") as f:
        f.write(build_digest(payload, facts))

    print(f"群：{res['group']}（{payload.get('conversation_id', '')}）")
    print(f"窗口：{res['start']} → {res['end']}")
    print(f"消息：{res['count']} 条 / {facts['sender_count']} 人 / 类型 {facts['types']}")
    print(f"输出：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
