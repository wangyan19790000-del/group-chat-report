#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Step 3b · Render report_spec.json into a self-contained HTML page.

    python render_html.py --run-dir D:/reports/ppt

Output: <run-dir>/report.html — no CDN, no external assets, works offline and on mobile.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from datetime import datetime

ESC = lambda s: html.escape(str(s or ""), quote=True)

TAG_RE = re.compile(r"</?(?!em\b)[a-zA-Z][^>]*>")


def lede_html(text: str) -> str:
    """Allow <em>…</em> emphasis from the spec, escape or drop everything else."""
    safe = ESC(TAG_RE.sub("", text))
    return safe.replace("&lt;em&gt;", "<em>").replace("&lt;/em&gt;", "</em>")


CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#F4F6FB;--card:#fff;--ink:#111827;--ink2:#374151;--muted:#8894A8;
  --line:#E7EBF3;--blue:#2B5CE6;--blue-soft:#E8EEFF;
  --amber:#E4572E;--amber-soft:#FDEDE7;--violet:#6D4AFF;--violet-soft:#EFEBFF;
  --green:#0F9D6E;--radius:18px;
  --shadow:0 1px 2px rgba(17,24,39,.04),0 12px 32px -12px rgba(17,24,39,.10);
}
html{-webkit-text-size-adjust:100%}
body{background:var(--bg);color:var(--ink);line-height:1.6;-webkit-font-smoothing:antialiased;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
.wrap{max-width:920px;margin:0 auto;padding:0 20px 80px}
.mono{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}

.hero{background:radial-gradient(120% 140% at 12% -10%,#2B3A6B 0%,#161D33 48%,#0E1324 100%);
  color:#fff;padding:46px 0 132px;position:relative;overflow:hidden}
.hero::after{content:"";position:absolute;inset:0;pointer-events:none;
  background-image:linear-gradient(rgba(255,255,255,.045) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.045) 1px,transparent 1px);
  background-size:44px 44px;mask-image:linear-gradient(180deg,rgba(0,0,0,.9),transparent 78%)}
.hero .wrap{padding-bottom:0;position:relative;z-index:1}
.eyebrow{display:flex;align-items:center;gap:9px;font-size:12.5px;letter-spacing:.14em;
  color:#9FB0D8;text-transform:uppercase;font-weight:600}
.dot{width:6px;height:6px;border-radius:50%;background:#5CE1A4;box-shadow:0 0 0 4px rgba(92,225,164,.16)}
h1{font-size:clamp(25px,4.4vw,38px);line-height:1.28;margin:15px 0 0;letter-spacing:-.02em;font-weight:700}
h1 .thin{color:#8FA3CE;font-weight:500}
.sub{margin-top:13px;color:#AEBBD8;font-size:14px}
.sub b{color:#fff;font-weight:600}
.lede{margin-top:20px;max-width:680px;font-size:15px;color:#C6D0E6;
  border-left:2px solid rgba(255,255,255,.18);padding-left:16px}
.lede em{font-style:normal;color:#fff;font-weight:600}

.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:-96px;position:relative;z-index:2}
.kpi{background:var(--card);border:1px solid rgba(255,255,255,.6);border-radius:var(--radius);
  padding:20px 18px 18px;box-shadow:var(--shadow)}
.kpi .lab{font-size:12.5px;color:var(--muted);font-weight:600}
.kpi .val{font-size:30px;font-weight:700;letter-spacing:-.03em;margin-top:8px;line-height:1.15}
.kpi .val small{font-size:14px;font-weight:600;color:var(--ink2);margin-left:2px}
.kpi .hint{margin-top:8px;font-size:12.5px;color:var(--muted)}

section{margin-top:36px}
.head{display:flex;align-items:baseline;justify-content:space-between;gap:14px;margin-bottom:15px}
.head h2{font-size:17px;font-weight:700;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
.head h2::before{content:"";width:3px;height:15px;border-radius:2px;background:var(--blue)}
.head .note{font-size:12.5px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
  box-shadow:var(--shadow);padding:22px}

.bars{display:grid;gap:14px}
.bar-row{display:grid;grid-template-columns:118px 1fr 86px;align-items:center;gap:14px}
.bar-row .lbl{font-size:13px;color:var(--ink2);font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.track{height:26px;background:#F1F4FA;border-radius:7px;overflow:hidden}
.fill{height:100%;border-radius:7px;background:linear-gradient(90deg,#5A86FF,#2B5CE6);width:0;
  transition:width .9s cubic-bezier(.22,.8,.2,1)}
.fill.hl{background:linear-gradient(90deg,#FFB199,#E4572E)}
.bar-row .num{text-align:right;font-size:13px;color:var(--ink);font-weight:600}

table{width:100%;border-collapse:collapse}
th{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  font-weight:600;text-align:left;padding:0 0 10px}
th.r,td.r{text-align:right}
td{padding:11px 0;border-top:1px solid var(--line);font-size:13.5px;vertical-align:middle}
td:first-child{font-weight:600}

.cards{display:grid;gap:12px}
.hl{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--blue);
  border-radius:12px;padding:16px 18px;box-shadow:0 1px 2px rgba(17,24,39,.03)}
.hl b{display:block;font-size:14.5px;margin-bottom:5px}
.hl p{font-size:13.5px;color:#5A677D;line-height:1.68}

.olist{display:grid;gap:9px}
.o{display:grid;grid-template-columns:74px 1fr auto;gap:12px;align-items:start;background:var(--card);
  border:1px solid var(--line);border-radius:13px;padding:13px 15px;box-shadow:0 1px 1px rgba(17,24,39,.03);
  transition:.16s}
.o:hover{border-color:#CDD8EE;box-shadow:0 8px 22px -14px rgba(17,24,39,.3)}
.o.hide{display:none}
.o .t{font-size:11.5px;color:var(--muted);padding-top:2px;white-space:nowrap}
.o .body{min-width:0}
.o .who{font-size:12.5px;color:var(--ink2);font-weight:600;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.o .txt{font-size:13.5px;margin-top:4px;line-height:1.58;overflow-wrap:anywhere}
.o .val{font-size:14px;font-weight:700;text-align:right;white-space:nowrap;color:var(--ink2)}
.tag{font-size:10.5px;font-weight:700;padding:2px 7px;border-radius:5px}
.tag.warn{background:var(--amber-soft);color:var(--amber)}
.tag.ok{background:#E7F6EF;color:var(--green)}
.tag.info{background:var(--blue-soft);color:var(--blue)}
.card.tone-warn{background:#FFFCF7;border-color:#F0E2CC}
.card.tone-ok{background:#F9FDFB;border-color:#D9EBE3}
.card.tone-info{background:#FBFCFE}
.tools{display:flex;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.search{flex:1;min-width:190px;position:relative}
.search input{width:100%;border:1px solid var(--line);background:var(--card);border-radius:10px;
  padding:9px 12px 9px 34px;font-size:13.5px;font-family:inherit;color:var(--ink);outline:none;transition:.16s}
.search input:focus{border-color:#B9C9F2;box-shadow:0 0 0 3px rgba(43,92,230,.10)}
.search svg{position:absolute;left:11px;top:50%;transform:translateY(-50%);opacity:.4}

.hours{display:flex;align-items:flex-end;gap:4px;height:132px;margin-top:4px}
.hcol{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:6px;height:100%}
.hbar{width:100%;border-radius:4px 4px 2px 2px;background:linear-gradient(180deg,#7FA0FF,#2B5CE6);
  min-height:2px;height:0;transition:height .8s cubic-bezier(.22,.8,.2,1)}
.hbar.zero{background:#EDF1F8;height:2px!important}
.hbar.peak{background:linear-gradient(180deg,#FF9E80,#E4572E)}
.hlab{font-size:9.5px;color:#B3BDCE}
.hcol.peak .hlab{color:var(--amber);font-weight:700}

.donut-wrap{display:flex;align-items:center;gap:24px}
.donut{width:128px;height:128px;border-radius:50%;position:relative;flex:0 0 128px}
.donut::after{content:"";position:absolute;inset:20px;border-radius:50%;background:var(--card)}
.donut .mid{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;z-index:1}
.donut .mid b{font-size:20px}
.donut .mid small{font-size:10.5px;color:var(--muted)}
.legend{display:grid;gap:9px;flex:1}
.lg{display:flex;align-items:center;gap:9px;font-size:13px;color:var(--ink2)}
.sw{width:9px;height:9px;border-radius:3px;flex:0 0 9px}
.lg .v{margin-left:auto;font-weight:600;color:var(--ink)}

.text-block p{font-size:13.8px;color:#5A677D;line-height:1.75;margin-bottom:10px}
.notes{background:#FBFCFE;border:1px solid var(--line);border-radius:var(--radius);padding:22px 24px;margin-top:36px}
.notes h3{font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  font-weight:700;margin-bottom:12px}
.notes ol{padding-left:18px;display:grid;gap:9px}
.notes li{font-size:13px;color:#5A677D;line-height:1.65}
footer{margin-top:32px;text-align:center;font-size:12px;color:#A9B3C6;line-height:1.9}

@media (max-width:760px){
  .wrap{padding:0 15px 56px}
  .hero{padding:32px 0 118px}
  .kpis{grid-template-columns:repeat(2,1fr);gap:11px;margin-top:-92px}
  .kpi .val{font-size:25px}
  .bar-row{grid-template-columns:92px 1fr 66px;gap:10px}
  .bar-row .lbl{font-size:12px}
  .o{grid-template-columns:1fr auto;gap:8px 10px;padding:12px 13px}
  .o .t{grid-column:1/-1;padding:0;font-size:11px}
  .hours{gap:2px;height:112px}
  .hlab{font-size:8px}
  .donut-wrap{flex-direction:column;gap:16px}
  .donut{width:104px;height:104px;flex-basis:104px}
  .legend{width:100%}
  h1{font-size:24px}
  .head{flex-direction:column;gap:4px;align-items:flex-start}
}
@media print{
  body{background:#fff}
  .hero{padding-bottom:36px}
  .kpis{margin-top:18px}
  .search{display:none}
  .card,.kpi,.o,.notes,.hl{box-shadow:none;break-inside:avoid}
  section{break-inside:avoid}
}
"""


# ------------------------------------------------------------------ components

def kpis_html(spec):
    kpis = (spec.get("kpis") or [])[:4]
    if not kpis:
        return ""
    cells = []
    for k in kpis:
        unit = k.get("unit", "")
        val = f'{ESC(k["value"])}<small>{ESC(unit)}</small>' if unit else ESC(k["value"])
        cells.append(f'<div class="kpi"><div class="lab">{ESC(k.get("label"))}</div>'
                     f'<div class="val mono">{val}</div>'
                     f'<div class="hint">{ESC(k.get("hint", ""))}</div></div>')
    return f'<div class="kpis">{"".join(cells)}</div>'


def sec_head(s):
    note = f'<div class="note">{ESC(s.get("note", ""))}</div>' if s.get("note") else ""
    return f'<div class="head"><h2>{ESC(s.get("title", ""))}</h2>{note}</div>'


def cmp_bars(s, i):
    items = s.get("items", [])
    if not items:
        return ""
    peak = max((x["value"] for x in items), default=1) or 1
    rows = []
    for it in items:
        w = it["value"] / peak * 100
        cls = " hl" if s.get("highlight") and it["label"] == s["highlight"] else ""
        rows.append(f'<div class="bar-row"><div class="lbl">{ESC(it["label"])}</div>'
                    f'<div class="track"><div class="fill{cls}" data-w="{w:.1f}"></div></div>'
                    f'<div class="num mono">{ESC(it.get("display") or it["value"])}</div></div>')
    return f'<section><div class="head"><h2>{ESC(s.get("title"))}</h2>' \
           f'<div class="note">{ESC(s.get("note", ""))}</div></div>' \
           f'<div class="card"><div class="bars">{"".join(rows)}</div></div></section>'


def cmp_table(s, i):
    cols, rows = s.get("columns", []), s.get("rows", [])
    if not cols:
        return ""
    th = "".join(f'<th class="{"r" if c.get("align") == "r" else ""}">{ESC(c["label"])}</th>' for c in cols)
    body = []
    for r in rows:
        tds = "".join(f'<td class="{"r mono" if cols[j].get("align") == "r" else ""}">{ESC(c)}</td>'
                      for j, c in enumerate(r[: len(cols)]))
        body.append(f"<tr>{tds}</tr>")
    return f'<section>{sec_head(s)}<div class="card" style="padding:18px 20px">' \
           f'<table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div></section>'


def cmp_cards(s, i):
    items = s.get("items", [])
    if not items:
        return ""
    blocks = "".join(
        f'<div class="hl"><b>{ESC(it.get("title", ""))}</b><p>{lede_html(it.get("body", ""))}</p></div>'
        for it in items)
    return f'<section>{sec_head(s)}<div class="cards">{blocks}</div></section>'


def cmp_list(s, i):
    items = s.get("items", [])
    if not items:
        return ""
    tone = s.get("tone", "info")
    rows, index = [], []
    for it in items:
        stamp = ""
        if it.get("time"):
            try:
                stamp = datetime.fromisoformat(it["time"]).strftime("%m-%d %H:%M")
            except ValueError:
                stamp = str(it["time"])[:11]
        tag = f'<span class="tag {tone}">{ESC(it["tag"])}</span>' if it.get("tag") else ""
        val = f'<div class="val">{ESC(it["value"])}</div>' if it.get("value") else "<div></div>"
        rows.append(f'<div class="o" data-search="{ESC((str(it.get("who", "")) + " " + str(it.get("text", ""))).lower())}">'
                    f'<div class="t mono">{ESC(stamp)}</div>'
                    f'<div class="body"><div class="who">{ESC(it.get("who", ""))}{tag}</div>'
                    f'<div class="txt">{ESC(it.get("text", ""))}</div></div>{val}</div>')
        index.append(str(it.get("text", "")).lower())

    search = ""
    if len(items) > 12:
        search = ('<div class="tools"><div class="search">'
                  '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#111827" '
                  'stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>'
                  f'<input type="search" data-target="lst{i}" placeholder="在 {len(items)} 条中搜索发言人 / 关键词">'
                  '</div></div>')
    return (f'<section data-lst="lst{i}">{sec_head(s)}{search}'
            f'<div class="olist" id="lst{i}">{"".join(rows)}</div>'
            f'<div class="empty" id="lst{i}-empty" style="display:none;text-align:center;'
            f'color:var(--muted);font-size:13.5px;padding:32px 0">没有匹配的条目</div></section>')


def cmp_hours(s, i):
    values = s.get("values") or []
    if len(values) != 24:
        return ""
    peak = max(values) or 1
    cols = []
    for h, v in enumerate(values):
        height = v / peak * 100
        cls = " zero" if not v else (" peak" if v == peak else "")
        label = str(h) if h % 3 == 0 else ""
        cols.append(f'<div class="hcol{" peak" if v == peak and v else ""}" title="{h:02d}:00 · {v} 条">'
                    f'<div class="hbar{cls}" data-h="{height:.1f}"></div>'
                    f'<div class="hlab">{label}</div></div>')
    return (f'<section>{sec_head(s)}<div class="card"><div class="hours">{"".join(cols)}</div>'
            f'<div style="display:flex;justify-content:space-between;margin-top:12px;font-size:11.5px;'
            f'color:#B3BDCE"><span>00:00</span><span>06:00</span><span>12:00</span>'
            f'<span>18:00</span><span>23:00</span></div></div></section>')


PIE = ["#2B5CE6", "#6D4AFF", "#0F9D6E", "#E4572E", "#C79A3A", "#93A7C2", "#C6CEDD"]


def cmp_donut(s, i):
    items = [x for x in s.get("items", []) if x.get("value")]
    if not items:
        return ""
    total = sum(x["value"] for x in items) or 1
    acc, stops = 0, []
    for idx, it in enumerate(items):
        a = acc / total * 360
        acc += it["value"]
        b = acc / total * 360
        stops.append(f"{PIE[idx % len(PIE)]} {a:.2f}deg {b:.2f}deg")
    conic = f"conic-gradient(from -90deg,{','.join(stops)})"
    legend = "".join(
        f'<div class="lg"><span class="sw" style="background:{PIE[idx % len(PIE)]}"></span>{ESC(it["label"])}'
        f'<span class="v mono">{it["value"]}<span style="color:#B3BDCE;font-weight:500"> · '
        f'{it["value"] / total * 100:.0f}%</span></span></div>'
        for idx, it in enumerate(items))
    return (f'<section>{sec_head(s)}<div class="card donut-card" style="padding:20px">'
            f'<div class="donut-wrap"><div class="donut" style="background:{conic}">'
            f'<div class="mid"><b class="mono">{len(items)}</b><small>类消息</small></div></div>'
            f'<div class="legend">{legend}</div></div></div></section>')


def cmp_text(s, i):
    paras = [p for p in s.get("paragraphs", []) if p]
    if not paras:
        return ""
    body = "".join(f"<p>{lede_html(p)}</p>" for p in paras)
    return f'<section>{sec_head(s)}<div class="card text-block">{body}</div></section>'


RENDER = {"bars": cmp_bars, "table": cmp_table, "cards": cmp_cards, "list": cmp_list,
          "hours": cmp_hours, "donut": cmp_donut, "text": cmp_text}


def render(spec: dict) -> str:
    m = spec["meta"]
    sections = "".join(RENDER[s["type"]](s, i) for i, s in enumerate(spec["sections"])
                       if s.get("type") in RENDER)
    notes = ""
    if spec.get("notes"):
        notes = ('<div class="notes"><h3>数据范围与阅读口径</h3><ol>'
                 + "".join(f"<li>{lede_html(n)}</li>" for n in spec["notes"]) + "</ol></div>")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{ESC(m.get('group'))} · {ESC(m.get('kind_label'))}</title>
<style>{CSS}</style></head>
<body>
<header class="hero"><div class="wrap">
  <div class="eyebrow"><span class="dot"></span>{ESC(m.get('kind_label', '群聊总结'))}</div>
  <h1>{ESC(m.get('title'))}</h1>
  <div class="sub"><b>{ESC(m.get('window'))}</b> · UTC+8 · 数据窗口不含终点</div>
  <p class="lede">{lede_html(m.get('lede', ''))}</p>
</div></header>
<div class="wrap">
  {kpis_html(spec)}
  {sections}
  {notes}
  <footer>{ESC(spec.get('footer', ''))}<br>生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 本地生成 · 无外部请求</footer>
</div>
<script>
(function(){{
  function animate(){{
    document.querySelectorAll('.fill').forEach(function(el){{ el.style.width = el.dataset.w + '%'; }});
    document.querySelectorAll('.hbar').forEach(function(el){{ el.style.height = el.dataset.h + '%'; }});
  }}
  if(document.readyState === 'complete' || document.readyState === 'interactive') animate();
  else document.addEventListener('DOMContentLoaded', animate);
  setTimeout(animate, 400); // 兜底：任何环境下都保证最终宽度被写入
  document.querySelectorAll('.search input').forEach(function(inp){{
    var id = inp.dataset.target;
    var box = document.getElementById(id);
    if(!box) return;
    var cards = Array.prototype.slice.call(box.children);
    var empty = document.getElementById(id + '-empty');
    inp.addEventListener('input', function(){{
      var kw = this.value.trim().toLowerCase(), shown = 0;
      cards.forEach(function(c){{
        var on = !kw || (c.dataset.search || '').indexOf(kw) > -1;
        c.classList.toggle('hide', !on);
        if(on) shown++;
      }});
      if(empty) empty.style.display = shown ? 'none' : 'block';
    }});
  }});
}})();
</script>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run = args.run_dir
    spec = json.load(open(os.path.join(run, "report_spec.json"), encoding="utf-8"))
    out = os.path.join(run, "report.html")
    open(out, "w", encoding="utf-8").write(render(spec))
    print(f"html -> {out} ({os.path.getsize(out) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
