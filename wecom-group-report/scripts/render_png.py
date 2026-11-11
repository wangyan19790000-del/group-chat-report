#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 3a · Render report_spec.json into a 1600px-wide PNG poster.

    python render_png.py --run-dir D:/reports/ppt
    python render_png.py --run-dir D:/reports/ppt --font C:/Windows/Fonts/msyh.ttc

Output: <run-dir>/report.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, SCALE = 1600, 2
INK, MUTED, BLUE, BG, LINE = "#18324b", "#52677e", "#245ac4", "#f2f5fa", "#dce5ef"
HEAD, HEAD_ACCENT = "#152f4b", "#91b9ff"
TONES = {
    "warn": {"bg": "#fff7e9", "line": "#ecd9b7", "label": "#8a6a3a", "tag": "#b4691a"},
    "ok":   {"bg": "#e8f1ee", "line": "#d5e5df", "label": "#3f6b5d", "tag": "#2f7a5f"},
    "info": {"bg": "#f4f7fd", "line": "#dbe5f5", "label": "#4d6485", "tag": "#245ac4"},
}
PIE = ["#245ac4", "#4d7fe0", "#7ea3ec", "#a9c1f2", "#c9d7f7", "#e0e8fa", "#93a7c2"]


class Canvas:
    def __init__(self, regular: Path, bold: Path):
        self.fonts, self.cmds = {}, []
        self.measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        self.regular, self.bold = regular, bold

    def font(self, size, heavy=False):
        key = (size, heavy)
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(str(self.bold if heavy else self.regular), size * SCALE)
        return self.fonts[key]

    def text(self, x, y, value, size=30, color=INK, heavy=False):
        self.cmds.append(("text", x, y, str(value), size, color, heavy))

    def box(self, coords, fill="white", outline=None, radius=20, width=2):
        self.cmds.append(("box", coords, fill, outline, radius, width))

    def line(self, coords, color=LINE, width=2):
        self.cmds.append(("line", coords, color, width))

    def pie(self, box, start, end, color):
        self.cmds.append(("pie", box, start, end, color))

    def width(self, value, size, heavy=False):
        return self.measure.textlength(str(value), font=self.font(size, heavy)) / SCALE

    def wrap(self, value, width, size=30, heavy=False):
        lines = []
        for part in str(value).split("\n"):
            line = ""
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/／+–@-]*|.", part):
                pieces = [token] if self.width(token, size, heavy) <= width else list(token)
                for ch in pieces:
                    if line and self.width(line + ch, size, heavy) > width:
                        lines.append(line)
                        line = ch
                    else:
                        line += ch
            if line:
                lines.append(line)
        return lines

    def para(self, x, y, value, width, size=30, color=INK, heavy=False, lh=1.65):
        value = re.sub(r"<[^>]+>", "", str(value))  # PNG 不渲染强调标签，直接剥离
        for ln in self.wrap(value, width, size, heavy):
            self.text(x, y, ln, size, color, heavy)
            y += math.ceil(size * lh)
        return y

    def fit(self, value, width, size=26):
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if self.width(value, size) <= width:
            return value
        while value and self.width(value + "…", size) > width:
            value = value[:-1]
        return value + "…"

    def render(self, height, path):
        img = Image.new("RGB", (W * SCALE, int(height) * SCALE), BG)
        d = ImageDraw.Draw(img)
        for c in self.cmds:
            if c[0] == "text":
                _, x, y, v, size, color, heavy = c
                d.text((x * SCALE, y * SCALE), v, font=self.font(size, heavy), fill=color)
            elif c[0] == "box":
                _, coords, fill, outline, radius, width = c
                d.rounded_rectangle(tuple(round(v * SCALE) for v in coords), radius=radius * SCALE,
                                    fill=fill, outline=outline, width=width * SCALE)
            elif c[0] == "line":
                _, coords, color, width = c
                d.line(tuple(round(v * SCALE) for v in coords), fill=color, width=width * SCALE)
            elif c[0] == "pie":
                _, box, start, end, color = c
                d.pieslice(tuple(round(v * SCALE) for v in box), start, end, fill=color)
        img.resize((W, int(height)), Image.Resampling.LANCZOS).save(path, "PNG", optimize=True)


# ------------------------------------------------------------------ sections

def draw_header(cv: Canvas, spec: dict) -> int:
    m = spec["meta"]
    h = 300
    cv.box((0, 0, W, h), HEAD, radius=0, width=0)
    cv.text(90, 52, m.get("kind_label", "群聊总结"), 26, HEAD_ACCENT, True)
    title, size = m.get("title", ""), 58
    while cv.width(title, size, True) > 1420 and size > 34:
        size -= 2
    cv.text(90, 108, title, size, "white", True)
    cv.text(90, 212, m.get("subtitle", m.get("group", "")), 31, "#d9e5f5")
    cv.text(90, 252, f"{m.get('window', '')} · UTC+8", 26, "#a9c1dd")
    return h


def draw_kpis(cv: Canvas, spec: dict, y: int) -> int:
    kpis = spec.get("kpis") or []
    if not kpis:
        return y
    n = min(len(kpis), 4)
    cv.box((80, y, 1520, y + 165), "white", LINE)
    slot = 1440 // n
    for i, k in enumerate(kpis[:n]):
        x = 120 + i * slot
        value = f"{k['value']}{k.get('unit', '')}" if k.get("unit") in ("%", "点") else str(k["value"])
        cv.text(x, y + 18, value, 46, BLUE, True)
        if k.get("unit") and k.get("unit") not in ("%", "点"):
            cv.text(x + cv.width(value, 46, True) + 6, y + 42, k["unit"], 22, MUTED)
        cv.text(x, y + 96, k.get("label", ""), 24, INK, True)
        if k.get("hint"):
            cv.text(x, y + 128, cv.fit(k["hint"], slot - 40, 20), 20, MUTED)
    return y + 205


def draw_bars(cv: Canvas, s: dict, y: int) -> int:
    items = [i for i in s.get("items", []) if i.get("value") is not None]
    if not items:
        return y
    items = items[:10]
    peak = max((i["value"] for i in items), default=1) or 1
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    row_h = 58
    cv.box((80, y, 1520, y + 40 + row_h * len(items)), "white", LINE)
    by = y + 24
    for it in items:
        label = str(it["label"])
        hl = s.get("highlight") and label == s["highlight"]
        cv.text(125, by + 6, cv.fit(label, 300, 26), 26, INK if hl else MUTED, hl)
        bar_x, bar_w = 430, 700
        cv.box((bar_x, by + 10, bar_x + bar_w, by + 32), "#eef3f9", "#eef3f9", 6, 0)
        if it["value"] > 0:
            w = max(bar_w * it["value"] / peak, 5)
            cv.box((bar_x, by + 10, bar_x + w, by + 32), BLUE if not hl else "#c8811f",
                   BLUE if not hl else "#c8811f", 6, 0)
        cv.text(bar_x + bar_w + 30, by + 4, it.get("display") or it["value"], 26, INK, True)
        by += row_h
    return y + 40 + row_h * len(items) + 44


def draw_table(cv: Canvas, s: dict, y: int) -> int:
    cols, rows = s.get("columns", []), s.get("rows", [])
    if not cols:
        return y
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    # column widths by content weight
    weights = []
    for i, c in enumerate(cols):
        longest = max([cv.width(c["label"], 24)] + [cv.width(r[i], 26) for r in rows if i < len(r)])
        weights.append(max(longest, 60))
    total = sum(weights) or 1
    avail = 1395
    xs, acc = [], 125
    for wt in weights:
        xs.append(acc)
        acc += avail * wt / total
    row_h = 56
    cv.box((80, y, 1520, y + 52 + row_h * len(rows)), "white", LINE)
    for i, c in enumerate(cols):
        label = c["label"]
        x = xs[i] if c.get("align") != "r" else xs[i] + avail * weights[i] / total - cv.width(label, 24) - 10
        cv.text(x, y + 14, label, 24, MUTED)
    ry = y + 52
    for r_i, row in enumerate(rows):
        for i, cell in enumerate(row[: len(cols)]):
            size = 26
            if cols[i].get("align") == "r":
                w = cv.width(cell, size)
                x = xs[i] + avail * weights[i] / total - w - 10
            else:
                x = xs[i]
            span = avail * weights[i] / total - (30 if i < len(cols) - 1 else 40)
            cv.text(x, ry + 10, cv.fit(cell, span, size), size, INK, i == 0)
        if r_i < len(rows) - 1:
            cv.line((100, ry + row_h - 3, 1500, ry + row_h - 3), "#eef3f9", 1)
        ry += row_h
    return y + 52 + row_h * len(rows) + 44


def draw_cards(cv: Canvas, s: dict, y: int) -> int:
    items = s.get("items", [])
    if not items:
        return y
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    for it in items[:8]:
        body = re.sub(r"<[^>]+>", "", it.get("body", ""))
        lines = cv.wrap(body, 1300, 26)
        h = 52 + 24 + len(lines) * 43 + 8
        cv.box((80, y, 1520, y + h), "white", LINE)
        cv.box((80, y + 14, 85, y + h - 14), BLUE, BLUE, 2, 0)
        cv.text(120, y + 20, cv.fit(it.get("title", ""), 1300, 30), 30, INK, True)
        ty = y + 48
        for ln in lines:
            cv.text(120, ty, ln, 26, MUTED)
            ty += 43
        y += h + 16
    return y + 26


def draw_list(cv: Canvas, s: dict, y: int) -> int:
    items = s.get("items", [])
    if not items:
        return y
    tone = TONES.get(s.get("tone", "info"), TONES["info"])
    limit = s.get("limit") or 12
    shown = items[:limit]
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    row_h = 66
    cv.box((80, y, 1520, y + 54 + row_h * len(shown)), tone["bg"], tone["line"])
    cv.text(125, y + 15, "时间", 24, tone["label"])
    cv.text(265, y + 15, "发言人", 24, tone["label"])
    cv.text(520, y + 15, "内容", 24, tone["label"])
    cv.text(1370, y + 15, "备注", 24, tone["label"])
    ry = y + 54
    for it in shown:
        stamp = ""
        if it.get("time"):
            try:
                stamp = datetime.fromisoformat(it["time"]).strftime("%m-%d %H:%M")
            except ValueError:
                stamp = str(it["time"])[:11]
        cv.text(125, ry + 12, stamp, 23, INK)
        cv.text(265, ry + 12, cv.fit(it.get("who", ""), 240, 23), 23, INK, True)
        cv.text(520, ry + 12, cv.fit(it.get("text", ""), 820, 24), 24, MUTED)
        if it.get("value"):
            cv.text(1370, ry + 12, cv.fit(it["value"], 130, 23), 23,
                    tone["tag"] if s.get("tone") == "warn" else INK, True)
        ry += row_h
    y += 54 + row_h * len(shown)
    if len(items) > len(shown):
        cv.text(95, y + 14, f"另有 {len(items) - len(shown)} 条未展开，完整记录见 messages.txt", 23, MUTED)
        y += 46
    return y + 40


def draw_hours(cv: Canvas, s: dict, y: int) -> int:
    values = s.get("values") or []
    if len(values) != 24:
        return y
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    h, top = 230, y + 20
    cv.box((80, y, 1520, y + h + 80), "white", LINE)
    peak = max(values) or 1
    slot = 1390 / 24
    bw = slot - 12
    for i, v in enumerate(values):
        bh = max(h * v / peak, 3)
        x = 105 + i * slot
        color = "#c8811f" if v == peak and v else BLUE
        cv.box((x, top + h - bh, x + bw, top + h), color, color, 4, 0)
        if i % 3 == 0:
            cv.text(x, top + h + 14, f"{i}", 20, MUTED)
        if v == peak and v:
            cv.text(x, top + h - bh - 34, str(v), 20, "#c8811f", True)
    return y + h + 80 + 40


def draw_donut(cv: Canvas, s: dict, y: int) -> int:
    items = [i for i in s.get("items", []) if i.get("value")]
    if not items:
        return y
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    if s.get("note"):
        cv.text(95 + cv.width(s["title"], 38, True) + 18, y + 12, s["note"], 22, MUTED)
    y += 78
    h = 300
    cv.box((80, y, 1520, y + h), "white", LINE)
    total = sum(i["value"] for i in items) or 1
    box = (180, y + 40, 420, y + 280)
    angle = -90
    for idx, it in enumerate(items[:7]):
        sweep = it["value"] / total * 360
        cv.pie(box, angle, angle + sweep, PIE[idx % len(PIE)])
        angle += sweep
    cv.box((240, y + 100, 360, y + 220), "white", "white", 60, 0)
    cv.text(275, y + 130, str(len(items)), 34, INK, True)
    cv.text(268, y + 176, "类消息", 20, MUTED)
    ly = y + 52
    for idx, it in enumerate(items[:7]):
        cv.box((520, ly + 6, 534, ly + 20), PIE[idx % len(PIE)], PIE[idx % len(PIE)], 3, 0)
        cv.text(552, ly, cv.fit(it["label"], 420, 26), 26, INK)
        pct = it["value"] / total * 100
        cv.text(1080, ly, f"{it['value']}", 26, INK, True)
        cv.text(1190, ly, f"{pct:.0f}%", 26, MUTED)
        ly += 34
    return y + h + 44


def draw_text(cv: Canvas, s: dict, y: int) -> int:
    paras = [p for p in s.get("paragraphs", []) if p]
    if not paras:
        return y
    cv.text(95, y, s.get("title", ""), 38, INK, True)
    y += 70
    for p in paras:
        y = cv.para(95, y, p, 1400, 26, MUTED) + 16
    return y + 28


DRAWERS = {"bars": draw_bars, "table": draw_table, "cards": draw_cards,
           "list": draw_list, "hours": draw_hours, "donut": draw_donut, "text": draw_text}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--font")
    ap.add_argument("--bold-font")
    args = ap.parse_args()
    run = Path(args.run_dir).resolve()
    spec = json.loads((run / "report_spec.json").read_text(encoding="utf-8"))

    system = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    regular = Path(args.font) if args.font else system / "msyh.ttc"
    bold = Path(args.bold_font) if args.bold_font else system / "msyhbd.ttc"
    if not regular.exists():
        raise SystemExit(f"找不到字体 {regular}，请用 --font 指定中文字体")
    if not bold.exists():
        bold = regular

    cv = Canvas(regular, bold)
    y = draw_header(cv, spec)
    y = draw_kpis(cv, spec, y + 40)
    y = cv.para(95, y, spec["meta"].get("lede", ""), 1400, 32, INK, True) + 40

    for s in spec.get("sections", []):
        fn = DRAWERS.get(s.get("type"))
        if fn:
            y = fn(cv, s, y)

    if spec.get("notes"):
        cv.text(95, y, "数据范围与阅读口径", 31, INK, True)
        y += 60
        for n in spec["notes"]:
            y = cv.para(95, y, "· " + n, 1400, 24, MUTED) + 14
        y += 10
    cv.text(95, y, spec.get("footer", ""), 23, MUTED)
    y += 70

    out = run / "report.png"
    cv.render(y, str(out))
    print(json.dumps({"png": str(out), "w": W, "h": int(y)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
