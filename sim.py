"""Aisle, ten weeks, one run.

    python sim.py --type plan --seed 1 [--decisions decisions/plan.json] [--model claude-haiku-5-5] [--probe] [--limit 6]

Writes runs/<type>_<seed>/ (or --out) in the TALP layout: run.json, personas.jsonl, calls.jsonl, weekly.jsonl,
metrics.json, and probe.jsonl with --probe. Each week every persona reads what it can see (news, what Aisle is
doing, Aisle's messages sent to its group, its own last weeks) and the model writes what it does and its status.
"""
import argparse
import hashlib
import json
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import llm
from metrics import compute
from world import World, check, normalize

HERE = Path(__file__).resolve().parent
# G>=3: world.json plan, W4 = plan_bf_cm * plan_split.week_4 = $24M against a normal week 72M * 12 / 52 = $16.6M -> 1.44
# G>=5: world.json holiday_lift: December weeks 1.128 x a normal week, January 1.0
DECEMBER = {True: "you keep ordering through December, about 1.1 times your normal week, Christmas week too; "
                  "in early January about your normal week",
            False: "your sales run about 1.1 times a normal week through December, Christmas week too; "
                   "about a normal week in early January"}
SEASON = {True: "in each of these two weeks you usually order about 1.4 times your normal week",
          False: "in each of these two weeks your sales usually run about 1.4 times a normal week"}
CFG = json.loads((HERE / "aisle.json").read_text())
SELLER_GROUPS = ("small sellers", "large sellers")


def load_jsonl(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def targets(target, group):
    """Does a decision's target reach this group? 'everyone', a group name, or 'sellers'."""
    t = target.lower()
    if t == "everyone" or group in t:
        return True
    return "sellers" in t and group in SELLER_GROUPS and not any(g.split()[0] in t for g in SELLER_GROUPS)


def hears(item, p):
    """Does an announcement reach this persona? Its "audience": missing or 'everyone', a group name or 'sellers'
    (as a decision's target), a list of persona ids, or {"trait": key, "contains": text or [texts, any one]}."""
    aud = item.get("audience", "everyone")
    if isinstance(aud, list):
        return p["id"] in aud
    if isinstance(aud, dict):
        have = str(p["traits"].get(aud["trait"], "")).lower()
        words = aud["contains"] if isinstance(aud["contains"], list) else [aud["contains"]]
        return any(w.lower() in have for w in words)
    return targets(aud, p["group"])


STAY = {"shoppers": "buys as usual", "small sellers": "active", "large sellers": "active"}


def word_of_mouth(p, rows, seed):
    """--wom on: one line on the persona's own group last week, from last week's rows, and at most two notes
    of others in the group (order fixed by seed, week and persona). No LLM."""
    others = [r for r in rows if r["group"] == p["group"] and r["persona"] != p["id"]]
    if not others:
        return []
    counts = {}
    for r in others:
        if r["status"] != STAY[p["group"]]:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
    moves = ", ".join(f"{n} {st}" for st, n in sorted(counts.items(), key=lambda x: CFG["statuses"][p["group"]].index(x[0])))
    lines = [f"- Among other {p['group']} you know, last week: "
             + (f"{moves} (of {len(others)})." if moves else f"all {len(others)} carried on as usual.")]
    key = lambda r: hashlib.sha256(f"{seed}|{r['week']}|{p['id']}|{r['persona']}".encode()).hexdigest()
    notes = [r["note"] for r in sorted(others, key=key) if r.get("note")][:2]
    return lines + [f"- One of them wrote: \"{n[:120]}\"" for n in notes]


def persona_block(p):
    if p["group"] == "shoppers":
        head = f"You are {p['name']}, a shopper on Aisle."
    else:
        head = f"You run a shop that sells on Aisle ({p['group'][:-1]})."
    traits = "\n".join(f"- {k}: {v}" for k, v in p["traits"].items())
    return f"{head}\n{traits}\n{p['story']}"


G = 0   # talp-persona-new Gemini iterations (cumulative): 1 fact line, 2 returns cap, 3 season line, 4 message window


def world_lines(week, decisions, news, group, window=False):
    """What a persona of this group can see in this week."""
    struck = any("strike_state" in a.get("set", {}) for a in news if a["week"] <= week)   # the warning gives way to the news
    seen = [f"- {CFG['known_crisis']['text']}"] if week >= CFG["known_crisis"]["from_week"] and not struck else []
    seen += [f"- Week {a['week']}: {a['title']}. {a['text']}" for a in news if a["week"] <= week]
    doing, told = [], []
    for d in decisions:
        if d["week"] > week:
            continue
        for a in d["actions"]:
            spec = CFG["actions"][a["action"]]
            if week < d["week"] + spec["weeks"]:
                doing.append("- " + spec["text"].format(level=a["level"], category=a.get("category", "")))
        if targets(d["target"], group) and not (window and week > d["week"] + 1):
            told.append(f"- Week {d['week']}, by {' and '.join(d['channels'])}: \"{d['message']}\"")
    doing = doing or ["- Business as usual, nothing special."]
    return seen, doing, told


def fact(p, h):
    """G>=1: one sentence the env computes from last week's row, in place of the persona's own notes."""
    if p["group"] == "shoppers":
        took = f", your parcels took about {h['days']:g} days" if h["orders"] else ""   # no order, no parcel to wait for
        return f"- Last week you placed {h['orders']} orders, returned {h['returns']}{took}; your status was {h['status']}."
    return f"- Last week your sales on Aisle were ${h['sales_usd']:,}; your status was {h['status']}."


def week_prompt(p, week, history, decisions, news, delivery, wom=None):
    seen, doing, told = world_lines(week, decisions, [a for a in news if hears(a, p)], p["group"], window=G >= 4)
    carrier = p["traits"].get("ships mostly with")
    seen += [f"- {delivery['terms']}", f"- {'You ship with ' + carrier + '. ' if carrier else ''}{delivery['parcels']}"]
    options = CFG["statuses"][p["group"]]
    past = "\n".join(f"- Week {h['week']}: {h['status']}. {h.get('note', '')}" for h in history[-3:]) or "- (first week)"
    if G >= 1:
        past = fact(p, history[-1]) if history else "- (first week)"
    if p["group"] == "shoppers":
        numbers = "Orders: <orders you place on Aisle this week, a whole number>\nReturned: <orders you send back this week>"
    else:
        numbers = "Sales: <your sales on Aisle this week in dollars, a whole number>"
    return f"""{persona_block(p)}

About Aisle: {CFG['about']}

It is week {week} of 10 ({CFG['weeks'][str(week)]}).
What you have heard:
{chr(10).join(seen) or '- nothing unusual'}
What Aisle is doing:
{chr(10).join(doing)}
Messages Aisle sent you:
{chr(10).join(told) or '- none'}
{"What people around you say:" + chr(10) + chr(10).join(wom) + chr(10) if wom else ""}Your last weeks:
{past}

Decide what you do this week, as yourself. Write two or three sentences in your own voice, then end with exactly these lines:
{numbers}
Complained: yes or no
Note: <one line you want to remember next week>
Status: one of {' | '.join(options)}"""


def field(text, name):
    m = re.findall(rf"^\s*\**{name}\**\s*:\s*(.+?)\s*$", text, re.M | re.I)
    return m[-1] if m else None


def number(text, name):
    v = field(text, name)
    m = re.search(r"\d[\d,]*", v or "")
    return int(m.group().replace(",", "")) if m else 0


def parse_week(text, group):
    status = (field(text, "Status") or "").strip(" .*\"'").lower()
    if status not in CFG["statuses"][group]:
        return None
    row = {"status": status, "complained": (field(text, "Complained") or "").lower().startswith("y"),
           "note": (field(text, "Note") or "")[:200]}
    if group == "shoppers":
        row["orders"], row["returns"] = number(text, "Orders"), number(text, "Returned")
    else:
        row["sales_usd"] = number(text, "Sales")
    return row


def bind_orders(row, history):
    """Orders follow the status the persona chose (the model often says 'buys less' and still orders as much as ever):
    paused or moved to a rival -> 0 orders; buys less -> over its 'buys less' weeks at most half of its normal week
    (the mean of its own earlier 'buys as usual' weeks, else of all its earlier weeks; week 1 has no normal, no cap).
    The half is counted over the weeks together, so a one-order-a-month shopper still orders now and then.
    What the model said is kept in orders_said when it was cut. Returns stay: they can be of earlier weeks' orders."""
    said = row["orders"]
    if row["status"] in ("paused", "moved to a rival"):
        row["orders"] = 0
    elif row["status"] == "buys less" and history:
        usual = [h["orders"] for h in history if h["status"] == "buys as usual"] or [h["orders"] for h in history]
        less = [h for h in history if h["status"] == "buys less"]
        allowed = int(0.5 * sum(usual) / len(usual) * (len(less) + 1)) - sum(h["orders"] for h in less)
        row["orders"] = max(0, min(said, allowed))
    if row["orders"] != said:
        row["orders_said"] = said


def checkin_prompt(p, when, history, decisions, news):
    qs = "\n".join(f"{k}: {v.format(company=CFG['name'])}" for k, v in CFG["checkin"].items())
    if when == "start":
        ctx = "It is November 1, before the ten weeks start."
    else:
        heard = [a for a in news if hears(a, p)]
        _, _, told = world_lines(10, decisions, heard, p["group"])   # every message, no window: the survey looks back
        events = [f"- Week {a['week']}: {a['title']}" for a in heard]
        did = [f"- Weeks {d['week']}-{min(10, d['week'] + CFG['actions'][x['action']]['weeks'] - 1)}: "
               + CFG["actions"][x["action"]]["text"].format(level=x["level"], category=x.get("category", ""))
               for d in decisions for x in d["actions"]]
        weeks = "\n".join(f"- Week {h['week']}: {h['status']}. {h.get('note', '')}" for h in history)
        if G >= 1:
            weeks = "\n".join(f"- Week {h['week']}: {h['status']}; " + (f"{h['orders']} orders, {h['returns']} returned."
                               if "orders" in h else f"sales ${h['sales_usd']:,}.") for h in history)
        ctx = (f"It is January 11; the ten weeks are over. What happened:\n{chr(10).join(events) or '- nothing unusual'}\n"
               f"What Aisle did:\n{chr(10).join(did) or '- business as usual'}\nYour weeks:\n{weeks}\n"
               f"Messages Aisle sent you:\n{chr(10).join(told) or '- none'}")
    return f"""{persona_block(p)}

About Aisle: {CFG['about']}

{ctx}

Answer each question with a whole number from 1 to 5, as yourself.
{qs}
Answer with JSON only, for example {{"1a": 3, "1b": 4, ...}} with all eight keys."""


class Run:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out or HERE / "runs" / f"{a.type}_{a.seed}")
        self.personas = load_jsonl(HERE / "personas.jsonl")[: a.limit or None]
        if G >= 3:
            for p in self.personas:
                p["traits"]["Black Friday week and Cyber Monday week"] = SEASON[p["group"] == "shoppers"]
        if G >= 5:
            for p in self.personas:
                p["traits"]["December weeks and early January"] = DECEMBER[p["group"] == "shoppers"]
        self.locked = [] if a.type == "nodecision" else json.loads(Path(a.decisions).read_text())   # run.json keeps these
        self.decisions = normalize(self.locked)
        news = json.loads((HERE / "announcements.json").read_text())
        self.all_news = news
        self.news = [n for n in news if not (a.type == "plan_nosurprises" and n.get("surprise", True))]
        errors = check(self.decisions, lambda t: any(targets(t, g) for g in CFG["statuses"]))
        if errors:
            raise SystemExit("decisions rejected:\n  " + "\n  ".join(errors))
        self.world = World(self.decisions, self.news, a.strike)
        self.calls, self.weekly, self.probe = [], [], []
        self.probe_missing, self.lock = {}, threading.Lock()   # check-ins that gave no eight scores: {when: [ids]}
        self.history = {p["id"]: [] for p in self.personas}

    def call(self, pid, week, prompt, attempt, max_tokens=400):
        cid = f"{pid}-w{week}-{attempt}"
        text = llm.ask(prompt, self.a.seed, self.a.model, max_tokens, tag=f"{self.out.name}:{cid}")
        self.calls.append({"id": cid, "persona": pid, "week": week, "prompt": prompt, "response": text,
                           "model": self.a.model})
        return cid, text

    def persona_week(self, p, week, delivery, last):
        wom = word_of_mouth(p, last, self.a.seed) if self.a.wom == "on" else None
        prompt = week_prompt(p, week, self.history[p["id"]], self.decisions, self.news, delivery, wom)
        for attempt in range(3):
            cid, text = self.call(p["id"], week, prompt, attempt)
            row = parse_week(text, p["group"])
            if row:
                if p["group"] == "shoppers" and self.a.bind == "on":
                    bind_orders(row, self.history[p["id"]])
                if p["group"] == "shoppers":
                    row["days"] = delivery["days"]
                    h = self.history[p["id"]]
                    cap = row["orders"] + (h[-1]["orders"] if h else 0)
                    if G >= 2 and row["returns"] > cap:
                        row["returns_said"], row["returns"] = row["returns"], cap
                return {"persona": p["id"], "week": week, **row, "call": cid}
            prompt += f"\n\nYour last line must be exactly: Status: <one of {' | '.join(CFG['statuses'][p['group']])}>"
        raise SystemExit(f"{p['id']} week {week}: no valid status after 3 tries; last answer:\n{text}")

    def persona_checkin(self, p, when):
        """Eight scores, or [] and the persona listed in probe_missing. The model sometimes reasons in prose first and
        runs out of tokens before the JSON; a retry with the same prompt and budget would only read the same cached
        answer, so each retry asks for JSON only and gets 100 more tokens (a new cache key)."""
        week = 0 if when == "start" else 11
        prompt = checkin_prompt(p, when, self.history[p["id"]], self.decisions, self.news)
        for attempt in range(6):
            if attempt == 1:
                prompt += "\n\nReply with the JSON object only, nothing before or after it."
            cid, text = self.call(p["id"], week, prompt, attempt, max_tokens=200 + 100 * attempt)
            m = re.search(r"\{.*\}", text, re.S)
            try:
                s = json.loads(m.group()) if m else {}
                scores = {q: int(s[q]) for q in CFG["checkin"] if 1 <= int(s[q]) <= 5}
            except (ValueError, KeyError, TypeError):
                scores = {}
            if len(scores) == len(CFG["checkin"]):
                return [{"persona": p["id"], "when": when, "question": q, "score": v, "call": cid} for q, v in scores.items()]
        print(f"{p['id']} {when} check-in: no eight scores after 6 tries; recorded as missing", flush=True)
        with self.lock:
            self.probe_missing.setdefault(when, []).append(p["id"])
        return []

    def each(self, fn, *args):
        with ThreadPoolExecutor(self.a.workers) as ex:
            return list(ex.map(lambda p: fn(p, *args), self.personas))

    def go(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        if self.a.probe:
            self.probe += sum(self.each(self.persona_checkin, "start"), [])
        self.world.row(0, [], self.personas)
        for week in range(1, 11):                 # the world links personas, so weeks run in order
            delivery = self.world.begin(week)
            group = {p["id"]: p["group"] for p in self.personas}
            last = [{**r, "group": group[r["persona"]]} for r in self.weekly if r["week"] == week - 1]
            rows = self.each(self.persona_week, week, delivery, last)
            for r in rows:
                self.history[r["persona"]].append(r)
            self.weekly += rows
            self.world.end(week, self.personas, rows)
            print(f"week {week}: {self.world.rows[-1]}", flush=True)
        if self.a.probe:
            self.probe += sum(self.each(self.persona_checkin, "end"), [])
        self.save()

    def save(self):
        def jl(name, rows):
            (self.out / name).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        jl("personas.jsonl", self.personas)
        jl("calls.jsonl", sorted(self.calls, key=lambda c: (c["week"], c["persona"], c["id"])))
        jl("weekly.jsonl", self.weekly)
        if self.a.probe:
            jl("probe.jsonl", self.probe)
        m = compute(self.personas, self.weekly, self.probe)
        if self.a.probe:
            m["probe_missing"] = sum(len(ids) for ids in self.probe_missing.values())
        (self.out / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
        run = {"company": CFG["company"], "type": self.a.type, "seed": self.a.seed, "model": self.a.model,
               "decisions": self.locked, "probe": self.a.probe, "wom": self.a.wom, "g": G, "probe_eksik": {w: sorted(ids) for w, ids in self.probe_missing.items()},
               "announcements": {"file": "announcements.json", "n": len(self.all_news), "ids": [n.get("id") for n in self.all_news],
                                 "sha256": sha(HERE / "announcements.json"), "used": [n.get("id") for n in self.news]},
               "decisions_file": None if self.a.type == "nodecision" else
               {"file": Path(self.a.decisions).name, "sha256": sha(self.a.decisions)}}
        self.world.write(self.out / "world_weekly.csv")
        (self.out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False) + "\n")   # last: marks a finished run
        st = llm.STATS
        print(f"model calls {st['calls']} (cached {len(self.calls) - st['calls']}), errors {st['errors']} (429: {st['429']}), "
              f"${st['usd']:.4f}, {st['seconds'] / max(st['calls'], 1):.2f} s per call")
        print(f"{self.out}: {len(self.calls)} calls, metrics {json.dumps({k: v for k, v in m.items() if k != 'units'})}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=("plan", "nodecision", "plan_nosurprises"), required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--decisions", default=str(HERE / "decisions" / "plan.json"))
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--probe", action="store_true", help="ask the eight check-in questions before week 1 and after week 10")
    ap.add_argument("--limit", type=int, help="only the first N personas (quick test)")
    ap.add_argument("--workers", type=int, default=36)
    ap.add_argument("--out")
    ap.add_argument("--bind", choices=("on", "off"), default="on", help="orders follow the status (bind_orders); off: as the model said")
    ap.add_argument("--wom", choices=("on", "off"), default="off",
                    help="word of mouth: from week 2 each persona hears how its group did last week, and two of their notes")
    ap.add_argument("--g", type=int, default=5, help="talp-persona-new Gemini iteration 0-5 (cumulative; 5 = hand-in setting, 0 = off)")
    ap.add_argument("--strike", choices=("on", "off"), help="test: Rapid Parcel strike on / off from week 5 (default: announcements decide)")
    a = ap.parse_args()
    global G
    G = a.g
    Run(a).go()


if __name__ == "__main__":
    main()
