"""One HTML page that compares runs: sales and revenue against the plan, who stayed, delivery, policy, voices.

    python report.py                      # every runs/* with world_weekly.csv
    python report.py runs/plan_1 runs/nodecision_1 [--voices plan_1] [--out runs/report.html]

Reads only files in the run folders (no LLM). Inline SVG, no external scripts or fonts.
"""
import argparse
import csv
import html
import json
import re
from pathlib import Path

from world import POLICY, SELLER, SHOPPER

HERE = Path(__file__).resolve().parent
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
STEPS = ["#86b6ef", "#3987e5", "#256abf", "#184f95"]      # status mix: ordinal, as usual -> gone
W, H, L, R, T, B = 640, 220, 56, 16, 12, 28               # chart size and margins
esc = html.escape


def load_jsonl(path):
    p = Path(path)
    return [json.loads(s) for s in p.read_text().splitlines() if s.strip()] if p.exists() else []


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def load(folder):
    f = Path(folder)
    rows = list(csv.DictReader(open(f / "world_weekly.csv")))
    run = json.loads((f / "run.json").read_text())
    return {"name": f.name, "rows": rows, "run": run, "folder": f,
            "metrics": json.loads((f / "metrics.json").read_text()) if (f / "metrics.json").exists() else {},
            "dweeks": sorted({d["week"] for d in run.get("decisions", [])}),
            "strike": [int(r["week"]) for r in rows if r["strike_state"] == "on"]}


def money(v):
    a = abs(v)
    s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.0f}K" if a >= 1e3 else f"{a:.0f}"
    return ("-" if v < 0 else "") + "$" + s


def ticks(lo, hi, n=4):
    step = (hi - lo) / n or 1
    mag = 10 ** len(str(int(step))) / 10
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= step), default=step)
    t, out = (lo // step) * step, []
    while True:
        out.append(t)
        if t >= hi:
            return out
        t += step


def chart(title, weeks, series, fmt=money, marks=None, bars=False, note=""):
    """series: [(label, color_index or None, values)]. color None = plan/reference (muted ink).
    marks: {"decision": [weeks], "strike": [weeks]} shaded behind the lines."""
    vals = [v for _, _, vs in series for v in vs if v is not None]
    lo, hi = min(0, *vals) if vals else 0, max(vals) if vals else 1
    tk = ticks(lo, hi if hi > lo else lo + 1)
    lo, hi = tk[0], tk[-1]
    n = len(weeks)
    x = lambda i: L + (i + 0.5) * (W - L - R) / n
    y = lambda v: T + (H - T - B) * (hi - v) / (hi - lo)
    g = []
    marks = marks or {}
    bw = (W - L - R) / n
    for w in marks.get("strike", []):
        if w in weeks:
            g.append(f'<rect class="strike" x="{x(weeks.index(w)) - bw / 2:.1f}" y="{T}" width="{bw:.1f}" height="{H - T - B}"/>')
    for w in marks.get("decision", []):
        if w in weeks:
            g.append(f'<line class="dec" x1="{x(weeks.index(w)) - bw / 2:.1f}" x2="{x(weeks.index(w)) - bw / 2:.1f}" y1="{T}" y2="{H - B}"/>'
                     f'<text class="ax" x="{x(weeks.index(w)) - bw / 2 + 3:.1f}" y="{T + 10}">decision</text>')
    for t in tk:
        g.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>'
                 f'<text class="ax" x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{esc(fmt(t))}</text>')
    for i, w in enumerate(weeks):
        g.append(f'<text class="ax" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{w}</text>')
    for label, c, vs in series:
        col = "var(--muted)" if c is None else f"var(--s{c})"
        if bars:
            for i, v in enumerate(vs):
                if v:
                    top, bot = sorted((y(v), y(0)))
                    col = "var(--pos)" if v > 0 else "var(--neg)"
                    g.append(f'<rect x="{x(i) - 10:.1f}" y="{top:.1f}" width="20" height="{max(bot - top, 1):.1f}" rx="2" fill="{col}"/>')
            continue
        pts = [(x(i), y(v)) for i, v in enumerate(vs) if v is not None]
        d = " ".join(f"{'M' if k == 0 else 'L'}{a:.1f},{b:.1f}" for k, (a, b) in enumerate(pts))
        g.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        if pts:
            g.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" fill="{col}" stroke="var(--surface)" stroke-width="2"/>')
    g.append(f'<line class="base" x1="{L}" x2="{W - R}" y1="{y(max(lo, 0)):.1f}" y2="{y(max(lo, 0)):.1f}"/>')
    tip = [{"w": w, "v": [[lab, None if vs[i] is None else fmt(vs[i])] for lab, _, vs in series]} for i, w in enumerate(weeks)]
    legend = "" if len(series) < 2 else '<div class="legend">' + "".join(
        f'<span><i style="background:{"var(--muted)" if c is None else f"var(--s{c})"}"></i>{esc(lab)}</span>'
        for lab, c, _ in series) + "</div>"
    return (f'<figure><figcaption>{esc(title)}</figcaption>{legend}'
            f'<div class="plot"><svg viewBox="0 0 {W} {H}" data-x0="{L}" data-bw="{bw:.2f}" data-tip="{esc(json.dumps(tip))}">'
            f'{"".join(g)}<line class="cross" y1="{T}" y2="{H - B}"/></svg><div class="tip"></div></div>'
            f'{f"<p class=note>{note}</p>" if note else ""}</figure>')


def stacked(title, weeks, rows, cols, labels):
    """Stacked columns of persona counts per week, one colour step per status."""
    n, h = len(weeks), H - T - B
    tot = max(sum(int(r[c]) for c in cols) for r in rows) or 1
    x = lambda i: L + (i + 0.5) * (W - L - R) / n
    g, tip = [], []
    for i, r in enumerate(rows):
        base = H - B
        for k, c in enumerate(cols):
            v = int(r[c])
            if v:
                hh = h * v / tot
                g.append(f'<rect x="{x(i) - 12:.1f}" y="{base - hh + 1:.1f}" width="24" height="{max(hh - 2, 1):.1f}" fill="{STEPS[k]}"/>')
                base -= hh
        g.append(f'<text class="ax" x="{x(i):.1f}" y="{H - 8}" text-anchor="middle">{r["week"]}</text>')
        tip.append({"w": r["week"], "v": [[lab, str(r[c])] for lab, c in zip(labels, cols)]})
    for t in (0, tot):
        yy = H - B - h * t / tot
        g.append(f'<text class="ax" x="{L - 6}" y="{yy + 4:.1f}" text-anchor="end">{t}</text>')
    return (f'<figure><figcaption>{esc(title)}</figcaption><div class="plot"><svg viewBox="0 0 {W} {H}" data-x0="{L}" '
            f'data-bw="{(W - L - R) / n:.2f}" data-tip="{esc(json.dumps(tip))}">{"".join(g)}'
            f'<line class="cross" y1="{T}" y2="{H - B}"/></svg><div class="tip"></div></div></figure>')


def span(ws):
    return f"{ws[0]}-{ws[-1]}" if ws else "none"


def decisions_text(run):
    return "; ".join(f"w{d['week']}: " + ", ".join(f"{a['action']}@{a.get('level', '')}" for a in d["actions"])
                     for d in run.get("decisions", [])) or "none"


def section_table(runs):
    goals = ["goal_1", "goal_2", "goal_3", "goal_4"]
    units = next((r["metrics"].get("units", {}) for r in runs if r["metrics"].get("units")), {})
    head = "".join(f'<th title="{esc(units.get(g, ""))}">{g}</th>' for g in goals)
    out = ['<table><tr><th></th><th>run</th><th>model</th><th>seed</th><th>decisions</th>' + head +
           '<th>strike weeks</th><th>total sales</th><th>sales / plan</th><th>budget spent</th></tr>']
    for i, r in enumerate(runs):
        sales = sum(num(x["sales_usd"]) or 0 for x in r["rows"])
        plan = sum(num(x["plan_usd"]) or 0 for x in r["rows"])
        spent = num(r["rows"][-1]["budget_spent"]) or 0
        out.append(f'<tr><td><i class="key" style="background:var(--s{i})"></i></td><td>{esc(r["name"])}</td>'
                   f'<td>{esc(str(r["run"].get("model", "")))}</td><td>{r["run"].get("seed", "")}</td>'
                   f'<td class="dec-t">{esc(decisions_text(r["run"]))}</td>' +
                   "".join(f'<td class="n">{r["metrics"].get(g, "")}</td>' for g in goals) +
                   f'<td>{span(r["strike"])}</td><td class="n">{money(sales)}</td><td class="n">{sales / plan:.2f}x</td><td class="n">{money(spent)}</td></tr>')
    out.append("</table>")
    out.append('<p class="note">' + " · ".join(f"<b>{g}</b>: {esc(u)}" for g, u in units.items()) + "</p>")
    return "".join(out)


def strike_weeks(runs):
    return sorted({w for r in runs for w in r["strike"]})


def pairs(runs):
    """plan-type run and nodecision run with the same seed and model."""
    base = {(r["run"].get("seed"), r["run"].get("model")): r for r in runs if r["run"].get("type") == "nodecision"}
    return [(r, base[k]) for r in runs if r["run"].get("type", "").startswith("plan")
            for k in [(r["run"].get("seed"), r["run"].get("model"))] if k in base]


def section_sales(runs, weeks):
    marks = {"decision": sorted({w for r in runs for w in r["dweeks"]}), "strike": strike_weeks(runs)}
    col = lambda r, c: [num(x[c]) for x in r["rows"] if int(x["week"]) in weeks]
    ser = [(r["name"], i, col(r, "sales_usd")) for i, r in enumerate(runs)] + [("plan", None, col(runs[0], "plan_usd"))]
    out = [chart("Sales per week (USD) against the plan", weeks, ser, marks=marks,
                 note="Shaded weeks: Rapid Parcel strike on in at least one run (see the strike column in table 1). "
                      "Vertical rule: a decision week in any run.")]
    cum = lambda vs: [sum(v or 0 for v in vs[:k + 1]) for k in range(len(vs))]
    ser = [(lab, c, cum(vs)) for lab, c, vs in ser]
    out.append(chart("Cumulative revenue (USD)", weeks, ser, marks=marks))
    for p, nd in pairs(runs):
        d = [(a or 0) - (b or 0) for a, b in zip(col(p, "sales_usd"), col(nd, "sales_usd"))]
        out.append(chart(f"{p['name']} minus {nd['name']}: sales difference per week (same seed, same model)", weeks,
                         [(f"{p['name']} - {nd['name']}", 0, d)], bars=True, marks={"decision": p["dweeks"], "strike": p["strike"]},
                         note=f"Weeks 1-10 total: <b>{money(sum(d))}</b>. Before the first decision the two runs share "
                              f"the same prompts and cached answers, so any difference there is noise, not the decision."))
    return "".join(out)


def section_mix(runs):
    sh = [f"shoppers_{s.replace(' ', '_')}" for s in SHOPPER]
    se = [f"sellers_{s}" for s in SELLER]
    leg = lambda labels: '<div class="legend">' + "".join(
        f'<span><i style="background:{STEPS[k]}"></i>{esc(lab)}</span>' for k, lab in enumerate(labels)) + "</div>"
    out = []
    for cols, labels, who in ((sh, SHOPPER, "Shoppers"), (se, SELLER, "Sellers")):
        out.append(f"<h3>{who} (personas, unweighted)</h3>{leg(labels)}<div class=grid>")
        for r in runs:
            rows = [x for x in r["rows"] if int(x["week"]) >= 1]
            out.append(stacked(f"{r['name']}", [int(x["week"]) for x in rows], rows, cols, labels))
        out.append("</div>")
    return "".join(out)


def section_delivery(runs, weeks):
    marks = {"strike": strike_weeks(runs)}
    col = lambda r, c: [num(x[c]) for x in r["rows"] if int(x["week"]) in weeks]
    return ('<div class="grid">' +
            chart("Rapid Parcel delivery days", weeks, [(r["name"], i, col(r, "rapid_days")) for i, r in enumerate(runs)],
                  fmt=lambda v: f"{v:g} d", marks=marks) +
            chart("Backlog parcels (Rapid Parcel)", weeks, [(r["name"], i, col(r, "backlog_parcels")) for i, r in enumerate(runs)],
                  fmt=lambda v: f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}", marks=marks) +
            chart("Budget spent so far (USD)", weeks, [(r["name"], i, col(r, "budget_spent")) for i, r in enumerate(runs)],
                  marks=marks) + "</div>")


def section_policy(runs):
    out = []
    for r in runs:
        base = r["rows"][0]
        changed = [p for p in POLICY if any(x[p] != base[p] for x in r["rows"])]
        if not changed:
            out.append(f'<p class="note"><b>{esc(r["name"])}</b>: no policy variable changed.</p>')
            continue
        wk = [x for x in r["rows"] if int(x["week"]) >= 1]
        t = [f'<table class="pol"><caption>{esc(r["name"])}</caption><tr><th>variable</th>' +
             "".join(f"<th>{x['week']}</th>" for x in wk) + "</tr>"]
        for p in changed:
            t.append(f"<tr><td>{p}<br><small>default {esc(base[p])}</small></td>" + "".join(
                f'<td class="{"on" if x[p] != base[p] else ""}" title="week {x["week"]}: {esc(x[p])}">'
                f'{esc(x[p]) if x[p] != base[p] else ""}</td>' for x in wk) + "</tr>")
        out.append("".join(t) + "</table>")
    return "".join(out)


def note_of(text):
    m = re.findall(r"^\s*\**Note\**:\s*(.+)$", text or "", re.M)
    return m[-1].strip() if m else ""


def section_voices(r):
    f = r["folder"]
    calls = {c["id"]: c for c in load_jsonl(f / "calls.jsonl")}
    people = {p["id"]: p for p in load_jsonl(f / "personas.jsonl")}
    weekly = load_jsonl(f / "weekly.jsonl")
    if not weekly:
        return '<p class="note">No weekly.jsonl in this run.</p>'
    weeks = sorted({x["week"] for x in weekly})
    ids = list(people) or sorted({x["persona"] for x in weekly})
    cell = {(x["persona"], x["week"]): x for x in weekly}
    level = {s: k for k, s in enumerate(SHOPPER)} | {s: k for k, s in enumerate(SELLER)}
    news = {}
    for a in json.loads((HERE / "announcements.json").read_text()):
        news.setdefault(a["week"], []).append("News: " + a["title"])
    for d in r["run"].get("decisions", []):
        news.setdefault(d["week"], []).append("Aisle: " + ", ".join(f"{a['action']} {a.get('level', '')}" for a in d["actions"])
                                              + f" (to {d['target']})")
    for w in r["strike"][:1]:
        news.setdefault(w, []).append("Strike on")
    grid = ['<div class="scroll"><table class="voices"><tr><th>persona</th>' + "".join(f"<th>{w}</th>" for w in weeks) + "</tr>"]
    for pid in ids:
        p = people.get(pid, {})
        grid.append(f'<tr><td>{esc(pid)} <small>{esc(p.get("name", ""))}</small></td>' + "".join(
            (f'<td class="st" style="background:{STEPS[level.get(c["status"], 0)]};color:{"#0b0b0b" if level.get(c["status"], 0) == 0 else "#fff"}" '
             f'title="{esc(c["status"])}">{esc(c["status"][:1].upper() + "".join(w[0] for w in c["status"].split()[1:]))}</td>')
            if (c := cell.get((pid, w))) else "<td></td>" for w in weeks) + "</tr>")
    grid.append("</table></div>")
    det = []
    for w in weeks:
        rows = []
        for pid in ids:
            c = cell.get((pid, w))
            if not c:
                continue
            p = people.get(pid, {})
            note = note_of(calls.get(c.get("call"), {}).get("response")) or c.get("note", "")
            rows.append(f'<tr><td>{esc(pid)}</td><td>{esc(p.get("name", ""))}</td><td>{esc(p.get("group", ""))}</td>'
                        f'<td>{esc(c["status"])}</td><td class="n">{c.get("orders", "")}</td>'
                        f'<td>{"yes" if c.get("complained") else ""}</td><td>{esc(note)}</td></tr>')
        ev = " · ".join(news.get(w, []))
        det.append(f'<details><summary>Week {w}{" - " + esc(ev) if ev else ""}</summary><div class="scroll"><table>'
                   '<tr><th>id</th><th>name</th><th>group</th><th>status</th><th>orders</th><th>complained</th><th>note</th></tr>'
                   + "".join(rows) + "</table></div></details>")
    return (f'<p class="note">Run <b>{esc(r["name"])}</b>. Cell letters: status initials '
            '(B = buys as usual, Bl = buys less, P = paused, Mtar = moved to a rival; A / R / P / L for sellers); '
            'darker = further from business as usual.</p>' + "".join(grid) + "".join(det))


CSS = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;
--base:#c3c2b7;--pos:#2a78d6;--neg:#e34948;--strike:rgba(227,73,72,.08);--line:rgba(11,11,11,.10);%L}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;--base:#383835;--pos:#3987e5;--neg:#e66767;--strike:rgba(230,103,103,.12);
--line:rgba(255,255,255,.10);%D}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--grid:#2c2c2a;
--base:#383835;--pos:#3987e5;--neg:#e66767;--strike:rgba(230,103,103,.12);--line:rgba(255,255,255,.10);%D}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:22px;margin:8px 0}h2{font-size:17px;margin:28px 0 8px}
h3{font-size:14px;margin:16px 0 4px;color:var(--ink2)}.note{color:var(--ink2);font-size:13px}
.warn{border:1px solid var(--line);border-left:4px solid #fab219;background:var(--surface);padding:8px 12px;border-radius:6px}
figure{margin:12px 0;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:10px}
figcaption{font-weight:600;margin-bottom:4px}.plot{position:relative}svg{width:100%;height:auto;display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:0 12px}
.ax{fill:var(--muted);font-size:11px;font-variant-numeric:tabular-nums}line.grid{stroke:var(--grid);stroke-width:1}
line.base{stroke:var(--base);stroke-width:1}rect.strike{fill:var(--strike)}line.dec{stroke:var(--ink2);stroke-width:1}
line.cross{stroke:var(--ink2);stroke-width:1;visibility:hidden}
.tip{position:absolute;pointer-events:none;display:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;
padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);white-space:nowrap;z-index:2}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink2);margin:2px 0 6px}
.legend i,.key{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
table{border-collapse:collapse;font-size:13px;background:var(--surface);width:100%}th,td{border-bottom:1px solid var(--grid);
padding:5px 7px;text-align:left;vertical-align:top}th{color:var(--ink2);font-weight:600}.n{text-align:right;font-variant-numeric:tabular-nums}
.scroll,section>table{display:block;overflow-x:auto}.dec-t{min-width:180px}small{color:var(--muted)}
table.pol{margin:8px 0;width:auto}table.pol caption{text-align:left;font-weight:600;padding:4px 0}
td.on{background:var(--pos);color:#fff;font-size:11px}table.voices td.st{text-align:center;font-size:11px;min-width:30px}
details{background:var(--surface);border:1px solid var(--line);border-radius:6px;margin:6px 0;padding:4px 8px}
summary{cursor:pointer;font-weight:600}
"""

JS = """
document.querySelectorAll('svg[data-tip]').forEach(function(s){
 var d=JSON.parse(s.dataset.tip),x0=+s.dataset.x0,bw=+s.dataset.bw,c=s.querySelector('.cross'),t=s.parentNode.querySelector('.tip');
 function show(e){var b=s.getBoundingClientRect(),k=s.viewBox.baseVal.width/b.width,i=Math.floor(((e.clientX-b.left)*k-x0)/bw);
  if(i<0||i>=d.length){hide();return}var cx=x0+(i+.5)*bw;c.setAttribute('x1',cx);c.setAttribute('x2',cx);c.style.visibility='visible';
  t.innerHTML='<b>Week '+d[i].w+'</b>'+d[i].v.map(function(p){return '<br>'+p[0]+': '+(p[1]===null?'-':p[1])}).join('');
  t.style.display='block';var px=cx/k;t.style.left=Math.min(px+12,b.width-t.offsetWidth)+'px';t.style.top='8px'}
 function hide(){c.style.visibility='hidden';t.style.display='none'}
 s.addEventListener('mousemove',show);s.addEventListener('mouseleave',hide);
});
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--voices", help="run name for section 6 (default: first plan run)")
    ap.add_argument("--out", default=str(HERE / "runs" / "report.html"))
    a = ap.parse_args()
    folders = a.runs or sorted(str(p.parent) for p in (HERE / "runs").glob("*/world_weekly.csv"))
    runs = [load(f) for f in folders][:8]          # eight colour slots; more runs -> pass them explicitly
    if not runs:
        raise SystemExit("no run folder with world_weekly.csv")
    weeks = [int(x["week"]) for x in runs[0]["rows"] if int(x["week"]) >= 1]
    v = next((r for r in runs if r["name"] == a.voices), None) or next(
        (r for r in runs if r["run"].get("type", "").startswith("plan")), runs[0])
    var = lambda pal: "".join(f"--s{i}:{c};" for i, c in enumerate(pal))
    css = CSS.replace("%L", var(COLORS)).replace("%D", var(DARK))
    body = f"""<main><h1>Aisle runs</h1>
<p class="note">{len(runs)} runs: {", ".join(esc(r["name"]) for r in runs)}. Hover a chart for each week's values.</p>
<p class="warn"><b>Read differences, not levels.</b> sales_vs_plan is inflated for now: personas over-order (week 1 runs
about 6x the plan) and a scale fix is pending, so absolute sales are not trustworthy yet. What matters is how runs differ
from one another, week by week.</p>
<section><h2>1. Runs side by side</h2>{section_table(runs)}</section>
<section><h2>2. Sales and revenue</h2>{section_sales(runs, weeks)}</section>
<section><h2>3. Who keeps buying, who keeps selling</h2>{section_mix(runs)}</section>
<section><h2>4. Delivery and budget</h2>{section_delivery(runs, weeks)}</section>
<section><h2>5. Policy timeline</h2><p class="note">Filled cell: the variable differs from its default that week.</p>{section_policy(runs)}</section>
<section><h2>6. What personas said, week by week</h2>{section_voices(v)}</section></main>"""
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Aisle run report</title><style>{css}</style></head><body>{body}<script>{JS}</script></body></html>")
    Path(a.out).write_text(page)
    print(a.out)


if __name__ == "__main__":
    main()
