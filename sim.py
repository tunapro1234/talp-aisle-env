"""Aisle, ten weeks, one run.

    python sim.py --type plan --seed 1 [--decisions decisions/plan.json] [--model claude-haiku-5-5] [--probe] [--limit 6]

Writes runs/<type>_<seed>/ (or --out) in the TALP layout: run.json, personas.jsonl, calls.jsonl, weekly.jsonl,
metrics.json, and probe.jsonl with --probe. Each week every persona reads what it can see (news, what Aisle is
doing, Aisle's messages sent to its group, its own last weeks) and the model writes what it does and its status.
"""
import argparse
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import llm
from metrics import compute

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "aisle.json").read_text())
SELLER_GROUPS = ("small sellers", "large sellers")


def load_jsonl(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def targets(target, group):
    """Does a decision's target reach this group? 'everyone', a group name, or 'sellers'."""
    t = target.lower()
    if t == "everyone" or group in t:
        return True
    return "sellers" in t and group in SELLER_GROUPS and not any(g.split()[0] in t for g in SELLER_GROUPS)


def persona_block(p):
    if p["group"] == "shoppers":
        head = f"You are {p['name']}, a shopper on Aisle."
    else:
        head = f"You run a shop that sells on Aisle ({p['group'][:-1]})."
    traits = "\n".join(f"- {k}: {v}" for k, v in p["traits"].items())
    return f"{head}\n{traits}\n{p['story']}"


def world_lines(week, decisions, news, group):
    """What a persona of this group can see in this week."""
    seen = [f"- {CFG['known_crisis']['text']}"] if week >= CFG["known_crisis"]["from_week"] else []
    seen += [f"- Week {a['week']}: {a['title']}. {a['text']}" for a in news if a["week"] <= week]
    doing, told = [], []
    for d in decisions:
        if d["week"] > week:
            continue
        for a in d["actions"]:
            spec = CFG["actions"][a["action"]]
            if week < d["week"] + spec["weeks"]:
                doing.append("- " + spec["text"].format(level=a["level"]))
        if targets(d["target"], group):
            told.append(f"- Week {d['week']}, by {' and '.join(d['channels'])}: \"{d['message']}\"")
    doing = doing or [f"- Business as usual. {' '.join(CFG['business_as_usual'])}"]
    return seen, doing, told


def week_prompt(p, week, history, decisions, news):
    seen, doing, told = world_lines(week, decisions, news, p["group"])
    options = CFG["statuses"][p["group"]]
    past = "\n".join(f"- Week {h['week']}: {h['status']}. {h.get('note', '')}" for h in history[-3:]) or "- (first week)"
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
Your last weeks:
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


def checkin_prompt(p, when, history, decisions, news):
    qs = "\n".join(f"{k}: {v.format(company=CFG['name'])}" for k, v in CFG["checkin"].items())
    if when == "start":
        ctx = "It is November 1, before the ten weeks start."
    else:
        _, _, told = world_lines(10, decisions, news, p["group"])
        weeks = "\n".join(f"- Week {h['week']}: {h['status']}. {h.get('note', '')}" for h in history)
        ctx = f"It is January 11; the ten weeks are over. Your weeks:\n{weeks}\nMessages Aisle sent you:\n" \
              f"{chr(10).join(told) or '- none'}"
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
        self.decisions = [] if a.type == "nodecision" else json.loads(Path(a.decisions).read_text())
        news = json.loads((HERE / "announcements.json").read_text())
        self.news = [n for n in news if not (a.type == "plan_nosurprises" and n.get("surprise", True))]
        self.calls, self.weekly, self.probe = [], [], []
        self.history = {p["id"]: [] for p in self.personas}
        for d in self.decisions:
            if not any(targets(d["target"], g) for g in CFG["statuses"]):
                raise SystemExit(f"target '{d['target']}' reaches nobody: use everyone, shoppers, sellers, small/large sellers")

    def call(self, pid, week, prompt, attempt, max_tokens=400):
        cid = f"{pid}-w{week}-{attempt}"
        text = llm.ask(prompt, self.a.seed, self.a.model, max_tokens, tag=f"{self.out.name}:{cid}")
        self.calls.append({"id": cid, "persona": pid, "week": week, "prompt": prompt, "response": text,
                           "model": self.a.model})
        return cid, text

    def persona_week(self, p, week):
        prompt = week_prompt(p, week, self.history[p["id"]], self.decisions, self.news)
        for attempt in range(3):
            cid, text = self.call(p["id"], week, prompt, attempt)
            row = parse_week(text, p["group"])
            if row:
                return {"persona": p["id"], "week": week, **row, "call": cid}
            prompt += f"\n\nYour last line must be exactly: Status: <one of {' | '.join(CFG['statuses'][p['group']])}>"
        raise SystemExit(f"{p['id']} week {week}: no valid status after 3 tries; last answer:\n{text}")

    def persona_checkin(self, p, when):
        week = 0 if when == "start" else 11
        prompt = checkin_prompt(p, when, self.history[p["id"]], self.decisions, self.news)
        for attempt in range(3):
            cid, text = self.call(p["id"], week, prompt, attempt, max_tokens=200)
            m = re.search(r"\{.*\}", text, re.S)
            try:
                s = json.loads(m.group()) if m else {}
                scores = {q: int(s[q]) for q in CFG["checkin"] if 1 <= int(s[q]) <= 5}
            except (ValueError, KeyError, TypeError):
                scores = {}
            if len(scores) == len(CFG["checkin"]):
                return [{"persona": p["id"], "when": when, "question": q, "score": v, "call": cid} for q, v in scores.items()]
        raise SystemExit(f"{p['id']} {when} check-in: no eight scores after 3 tries")

    def each(self, fn, *args):
        with ThreadPoolExecutor(self.a.workers) as ex:
            return list(ex.map(lambda p: fn(p, *args), self.personas))

    def persona_run(self, p):
        """One persona's whole run. Personas do not see each other, so they run side by side."""
        rows = self.persona_checkin(p, "start") if self.a.probe else []
        for week in range(1, 11):
            self.history[p["id"]].append(self.persona_week(p, week))
        if self.a.probe:
            rows += self.persona_checkin(p, "end")
        print(f"{p['id']} done: {self.history[p['id']][-1]['status']}", flush=True)
        return rows

    def go(self):
        if self.out.exists():
            shutil.rmtree(self.out)
        self.out.mkdir(parents=True)
        self.probe = sum(self.each(self.persona_run), [])
        self.weekly = sorted((r for h in self.history.values() for r in h), key=lambda r: (r["week"], r["persona"]))
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
        (self.out / "metrics.json").write_text(json.dumps(m, indent=2) + "\n")
        run = {"company": CFG["company"], "type": self.a.type, "seed": self.a.seed, "model": self.a.model,
               "decisions": self.decisions, "announcements": [n.get("id", n["title"]) for n in self.news]}
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
    Run(ap.parse_args()).go()


if __name__ == "__main__":
    main()
