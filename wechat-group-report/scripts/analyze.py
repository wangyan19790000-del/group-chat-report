#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 2 · Turn extracted facts into a declarative report spec.

Two modes:
  dispatch  规则模式，适合派单/接单群：报价结构、主力派单方、加急单、高价值单
  general   通用模式，适合工作群/项目群/客户群：活跃度、话题、结论、待办、文件
            （语义结论来自 insights.json，由上层阅读 digest.md 后写出；缺失时退化为纯统计版）

Usage:
    python analyze.py --run-dir D:/reports/ppt --mode auto
    python analyze.py --run-dir D:/reports/dev --mode general --insights insights.json

Outputs report_spec.json + summary.md into the run dir. --mode auto picks dispatch
when >=25% of text messages carry a price tag, otherwise general.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from datetime import datetime

URGENT_RE = re.compile(r"今天|今晚|现在|马上|小时|中午|点前|立刻|尽快|急")
PRICE_PATTERNS = (r"(\d+(?:\.\d+)?)\s*[rR](?![a-zA-Z])", r"(\d+(?:\.\d+)?)\s*[元块]")
ORDER_HINT_RE = re.compile(r"美化|静态|动态|复刻|排版|P图|设计|页|稿|方案|报告|PPT")


# ------------------------------------------------------------------ utilities

def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    return re.sub(r"^@所有人\s*", "", text)


def short_name(name: str) -> str:
    n = re.sub(r"[（(].*?[)）]", "", name or "").strip()
    return n or (name or "")


def to_num(pattern: str, text: str):
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def load_run(run_dir: str) -> tuple[dict, dict]:
    payload = json.load(open(os.path.join(run_dir, "messages.json"), encoding="utf-8"))
    facts_path = os.path.join(run_dir, "facts.json")
    if os.path.exists(facts_path):
        facts = json.load(open(facts_path, encoding="utf-8"))
    else:  # tolerate older run dirs
        facts = {"group": payload["group"], "start": payload["start"],
                 "end": payload["end_exclusive"], "count": len(payload["messages"]),
                 "types": {}, "senders": [], "sender_count": 0, "hours_hist": [0] * 24,
                 "days": [], "files": [], "links": [], "keywords": []}
    return payload, facts


# ------------------------------------------------------------------ dispatch

def extract_orders(messages: list[dict]) -> list[dict]:
    orders = []
    for m in messages:
        if m["type"] != "文本":
            continue
        text = clean(m["content"])
        if not text:
            continue
        price = None
        for pat in PRICE_PATTERNS:
            price = to_num(pat, text)
            if price is not None:
                break
        pages = to_num(r"(\d+)\s*页", text)
        if price is None and pages is None and not ORDER_HINT_RE.search(text):
            continue
        orders.append({"id": m["id"], "time": m["time"], "sender": short_name(m["sender_name"]),
                       "text": text, "price": price,
                       "pages": int(pages) if pages else None,
                       "urgent": bool(URGENT_RE.search(text))})
    return orders


def spec_dispatch(payload, facts, args) -> dict:
    msgs = payload["messages"]
    orders = extract_orders(msgs)
    prices = [o["price"] for o in orders if o["price"]]
    by_sender = {}
    for o in orders:
        e = by_sender.setdefault(o["sender"], {"n": 0, "sum": 0.0, "priced": 0})
        e["n"] += 1
        if o["price"]:
            e["sum"] += o["price"]
            e["priced"] += 1
    top = sorted(by_sender.items(), key=lambda kv: (-kv[1]["n"], -kv[1]["sum"]))[:6]
    buckets = [(0, 20, "<20 元"), (20, 40, "20–40 元"), (40, 60, "40–60 元"),
               (60, 100, "60–100 元"), (100, 200, "100–200 元"), (200, 10 ** 9, "200 元以上")]
    bucket_items = [{"label": lb, "value": sum(1 for p in prices if lo <= p < hi),
                     "display": f"{sum(1 for p in prices if lo <= p < hi)} 单"}
                    for lo, hi, lb in buckets]
    median = statistics.median(prices) if prices else 0
    avg = sum(prices) / len(prices) if prices else 0
    urgent = [o for o in orders if o["urgent"]]
    high = sorted([o for o in orders if (o["price"] or 0) >= args.price_threshold],
                  key=lambda o: -(o["price"] or 0))
    days = [{"date": d["date"], "messages": d["messages"],
             "value": sum(1 for o in orders if o["time"].startswith(d["date"]))}
            for d in facts["days"]]

    start = datetime.fromisoformat(payload["start"])
    end = datetime.fromisoformat(payload["end_exclusive"])
    hours = round((end - start).total_seconds() / 3600)
    urgent_pct = round(len(urgent) / len(orders) * 100) if orders else 0
    peak = max(range(24), key=lambda h: facts["hours_hist"][h]) if facts["hours_hist"] else 0

    spec = {
        "version": 1,
        "meta": {
            "group": payload["group"],
            "start": payload["start"],
            "end": payload["end_exclusive"],
            "window": f"{start:%m.%d %H:%M} — {end:%m.%d %H:%M}",
            "mode": "dispatch",
            "kind_label": f"派单日报 / {end:%Y.%m.%d}",
            "title": f"这 {hours} 小时，群里派了 {len(orders)} 单",
            "subtitle": payload["group"],
            "lede": (f"{facts['count']} 条消息、{facts['sender_count']} 位老师，能数出 "
                     f"<em>{len(orders)} 条文字派单</em>；报价中位 <em>{fmt(median)} 元</em>，"
                     f"<em>{urgent_pct}%</em> 标了今天/今晚/几小时内。"),
        },
        "kpis": [
            {"label": "群消息", "value": facts["count"], "unit": "条", "hint": f"{facts['sender_count']} 位发言人"},
            {"label": "文字派单", "value": len(orders), "unit": "单", "hint": f"{len(prices)} 单带报价"},
            {"label": "报价中位数", "value": fmt(median), "unit": "元",
             "hint": f"均价 {fmt(round(avg, 1))} 元 · 最高 {fmt(max(prices) if prices else 0)} 元"},
            {"label": "加急单占比", "value": urgent_pct, "unit": "%",
             "hint": f"{len(urgent)} 单 · 今天/今晚/数小时内"},
        ],
        "sections": [
            {"type": "bars", "title": "报价结构", "note": f"按正文标价归档 · 共 {len(prices)} 单",
             "items": bucket_items, "highlight": "20–40 元"},
            {"type": "table", "title": "主力派单方", "note": f"Top {len(top)}",
             "columns": [{"label": "老师", "align": "l"}, {"label": "派单", "align": "r"},
                         {"label": "合计", "align": "r"}, {"label": "均价", "align": "r"}],
             "rows": [[short_name(n), f"{v['n']} 单", f"{fmt(round(v['sum'], 1))} 元",
                       f"{fmt(round(v['sum'] / v['priced'], 1))} 元" if v["priced"] else "—"]
                      for n, v in top],
             "bar_column": 1, "row_values": [v["n"] for _, v in top]},
            {"type": "hours", "title": "派单时段分布", "note": f"峰值 {peak:02d}:00",
             "values": facts["hours_hist"]},
            {"type": "table", "title": "每日节奏", "note": "按消息时间归档",
             "columns": [{"label": "日期", "align": "l"}, {"label": "消息", "align": "r"},
                         {"label": "派单", "align": "r"}],
             "rows": [[d["date"][5:], f"{d['messages']} 条", f"{d['value']} 单"] for d in days]},
            {"type": "list", "title": f"加急单 · 共 {len(urgent)} 条", "note": "今天 / 今晚 / 数小时内",
             "tone": "warn", "limit": 12,
             "items": [{"time": o["time"], "who": o["sender"], "text": o["text"],
                        "tag": "加急", "value": f"{fmt(o['price'])} 元" if o["price"] else "未标价"}
                       for o in urgent]},
            {"type": "list", "title": f"高价值单 · 报价 ≥ {args.price_threshold:g} 元（共 {len(high)} 条）",
             "note": "按报价降序", "tone": "ok", "limit": 12,
             "items": [{"time": o["time"], "who": o["sender"], "text": o["text"],
                        "value": f"{fmt(o['price'])} 元"} for o in high]},
        ],
        "notes": [
            f"精确窗口 {payload['start']} 至 {payload['end_exclusive']}（不含终点），共 {facts['count']} 条消息。",
            f"派单按正文含报价/页数/美化等关键词的文本消息统计，含转单与改价后再次发布，不等于独立需求数。",
            (f"图片消息 {facts['types'].get('图片', 0)} 条未做文字识别，只统计文字派单，"
             f"{len(orders)} 单是可识别下限。"),
            "报价均为群内文本标注，未核实是否成交，也未区分已接/未接。",
            "仅读取本机已同步的群聊天记录，数据库自检通过不代表手机端消息已全部同步。",
            "报告由本地脚本生成，数据不出本机。",
        ],
        "footer": f"{payload['group']} · {end:%Y.%m.%d}",
    }
    return spec


# ------------------------------------------------------------------ general

def spec_general(payload, facts, args, insights: dict | None) -> dict:
    ins = insights or {}
    msgs = payload["messages"]
    start = datetime.fromisoformat(payload["start"])
    end = datetime.fromisoformat(payload["end_exclusive"])
    hours = round((end - start).total_seconds() / 3600)
    days = max(len(facts["days"]), 1)
    peak = max(range(24), key=lambda h: facts["hours_hist"][h]) if facts["hours_hist"] else 0
    text_n = facts["types"].get("文本", 0)

    kpis = ins.get("kpis") or [
        {"label": "群消息", "value": facts["count"], "unit": "条", "hint": f"{facts['sender_count']} 人参与"},
        {"label": "发言人数", "value": facts["sender_count"], "unit": "人", "hint": f"日均 {round(facts['count'] / days)} 条"},
        {"label": "文件 / 链接", "value": len(facts["files"]) + len(facts["links"]), "unit": "个",
         "hint": f"文件 {len(facts['files'])} · 链接 {len(facts['links'])}"},
        {"label": "最活跃时段", "value": f"{peak:02d}", "unit": "点",
         "hint": f"该小时 {facts['hours_hist'][peak]} 条"},
    ]

    sections = []
    if ins.get("highlights"):
        sections.append({"type": "cards", "title": ins.get("highlights_title", "关键结论"),
                         "note": ins.get("highlights_note", ""),
                         "items": ins["highlights"]})
    if ins.get("topics"):
        sections.append({"type": "bars", "title": ins.get("topics_title", "话题分布"),
                         "note": ins.get("topics_note", "按讨论条数"),
                         "items": [{"label": t["label"], "value": t["value"],
                                    "display": f"{t['value']} 条"} for t in ins["topics"]]})
    if ins.get("todos"):
        sections.append({"type": "list", "title": ins.get("todos_title", "待办与跟进"),
                         "note": ins.get("todos_note", "从对话中提取，未独立核实"),
                         "tone": "warn",
                         "items": [{"time": t.get("time", ""), "who": t.get("who", ""),
                                    "text": t["text"], "tag": t.get("tag", ""),
                                    "value": t.get("due", "")} for t in ins["todos"]]})
    if ins.get("risks"):
        sections.append({"type": "text", "title": ins.get("risks_title", "风险与待确认"),
                         "paragraphs": ins["risks"]})

    active = [{"label": short_name(n), "value": c, "display": f"{c} 条"} for n, c in facts["senders"][:8]]
    if active:
        sections.append({"type": "bars", "title": "活跃成员", "note": f"Top {len(active)}",
                         "items": active})
    sections.append({"type": "hours", "title": "发言时段分布", "note": f"峰值 {peak:02d}:00",
                     "values": facts["hours_hist"]})
    sections.append({"type": "donut", "title": "消息构成", "note": f"{facts['count']} 条",
                     "items": [{"label": k, "value": v} for k, v in sorted(
                         facts["types"].items(), key=lambda kv: -kv[1])]})
    sections.append({"type": "table", "title": "每日节奏", "note": "按消息时间归档",
                     "columns": [{"label": "日期", "align": "l"}, {"label": "消息", "align": "r"},
                                 {"label": "发言人数", "align": "r"},
                                 {"label": "主要发言人", "align": "l"}],
                     "rows": [[d["date"][5:], f"{d['messages']} 条", f"{d.get('senders', len(d.get('top_senders', [])))} 人",
                               "、".join(short_name(n) for n, _ in d.get("top_senders", [])[:2]) or "—"]
                              for d in facts["days"]]})

    attach = [{"time": f["time"], "who": short_name(f["sender"]), "text": f["name"] or "（文件）",
               "tag": "文件", "value": ""} for f in facts["files"][:10]]
    attach += [{"time": l["time"], "who": short_name(l["sender"]), "text": l["url"],
                "tag": "链接", "value": ""} for l in facts["links"][:10]]
    attach.sort(key=lambda x: x["time"])
    if attach:
        sections.append({"type": "list", "title": "文件与链接", "note": f"共 {len(facts['files'])} 文件 · {len(facts['links'])} 链接",
                         "tone": "info", "limit": 14, "items": attach})

    if ins.get("quotes"):
        sections.append({"type": "list", "title": "原话摘录", "note": "不做改写，保留上下文",
                         "tone": "info", "limit": 10,
                         "items": [{"time": q.get("time", ""), "who": q.get("who", ""),
                                    "text": q["text"], "tag": q.get("tag", ""), "value": ""}
                                   for q in ins["quotes"]]})

    notes = ins.get("notes") or [
        f"精确窗口 {payload['start']} 至 {payload['end_exclusive']}（不含终点），共 {facts['count']} 条消息"
        f"（文本 {text_n}、图片 {facts['types'].get('图片', 0)}、文件 {facts['types'].get('文件', 0)}）。",
        "结论与待办由 AI 从对话中归纳，可能遗漏或误读，重要事项请回看原文。",
        "仅读取本机已同步的群聊天记录，不代表手机端消息已全量同步。",
        "报告由本地脚本生成，数据不出本机。",
    ]

    lede = ins.get("lede") or (
        f"{facts['hours']:g} 小时里 <em>{facts['count']} 条消息</em>、<em>{facts['sender_count']} 人</em>发言，"
        f"高峰在 <em>{peak:02d} 点</em>，文件往来 <em>{len(facts['files'])}</em> 个。")
    return {
        "version": 1,
        "meta": {
            "group": payload["group"],
            "start": payload["start"],
            "end": payload["end_exclusive"],
            "window": f"{start:%m.%d %H:%M} — {end:%m.%d %H:%M}",
            "mode": "general",
            "kind_label": ins.get("kind_label", f"群聊总结 / {end:%Y.%m.%d}"),
            "title": ins.get("title") or f"{facts['hours']:g} 小时，{facts['count']} 条消息说了什么",
            "subtitle": payload["group"],
            "lede": lede,
        },
        "kpis": kpis,
        "sections": sections,
        "notes": notes,
        "footer": f"{payload['group']} · {end:%Y.%m.%d}",
    }


# ------------------------------------------------------------------ md + cli

def spec_to_md(spec: dict) -> str:
    m = spec["meta"]
    out = [f"# {m['group']}｜{m['kind_label']}", "", f"{m['window']} · {m.get('hours', '')}".strip(), ""]
    if spec.get("kpis"):
        out.append(" · ".join(f"{k['label']} {k['value']}{k.get('unit', '')}" for k in spec["kpis"]))
        out.append("")
    out += [re.sub(r"</?em>", "", m.get("lede", "")), ""]
    for s in spec["sections"]:
        t = s.get("title", "")
        out.append(f"## {t}")
        if s["type"] == "table":
            cols = [c["label"] for c in s["columns"]]
            out += ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            out += ["| " + " | ".join(str(c) for c in r) + " |" for r in s["rows"]]
        elif s["type"] in ("bars", "donut"):
            out += [f"- {i['label']}：{i['value']}" for i in s["items"]]
        elif s["type"] == "list":
            out += [f"- {re.sub(r'T.*$', '', i.get('time', ''))} {i.get('who', '')}：{i['text']}"
                    + (f"（{i['value']}）" if i.get("value") else "") for i in s["items"][: s.get("limit", 999)]]
        elif s["type"] == "cards":
            out += [f"- **{i.get('title', '')}** {i.get('body', '')}" for i in s["items"]]
        elif s["type"] == "text":
            out += [p for p in s["paragraphs"]]
        out.append("")
    if spec.get("notes"):
        out += ["## 口径", ""] + [f"- {n}" for n in spec["notes"]]
    return "\n".join(out) + "\n"


def looks_like_dispatch(payload) -> bool:
    text = [m for m in payload["messages"] if m["type"] == "文本" and (m["content"] or "").strip()]
    if not text:
        return False
    priced = 0
    for m in text:
        t = clean(m["content"])
        if any(to_num(p, t) is not None for p in PRICE_PATTERNS[:1]) or re.search(r"\d+\s*页", t):
            priced += 1
    return priced / len(text) >= 0.25


def main() -> int:
    ap = argparse.ArgumentParser(description="生成报告规格 report_spec.json")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--mode", choices=["auto", "dispatch", "general"], default="auto")
    ap.add_argument("--insights", help="general 模式的语义结论 JSON（由上层阅读 digest.md 后写出）")
    ap.add_argument("--price-threshold", type=float, default=90, help="dispatch 高价值单阈值，默认 90")
    args = ap.parse_args()

    payload, facts = load_run(args.run_dir)
    mode = args.mode
    if mode == "auto":
        mode = "dispatch" if looks_like_dispatch(payload) else "general"
        print(f"自动判定模式：{mode}")

    insights = None
    if args.insights:
        insights = json.load(open(args.insights, encoding="utf-8"))
    spec = spec_dispatch(payload, facts, args) if mode == "dispatch" else spec_general(payload, facts, args, insights)

    spec["meta"]["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    spec["meta"]["message_count"] = facts["count"]
    spec["meta"]["sender_count"] = facts["sender_count"]

    out = os.path.join(args.run_dir, "report_spec.json")
    json.dump(spec, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    open(os.path.join(args.run_dir, "summary.md"), "w", encoding="utf-8").write(spec_to_md(spec))
    print(f"spec -> {out}（{mode}，{len(spec['sections'])} 个板块）")
    return 0


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
