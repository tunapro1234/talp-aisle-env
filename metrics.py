"""Persona behaviour -> the board's goals. Every persona counts for `weight` real people (or sellers).

goal_1  the board's goal 1: Aisle's sales in weeks 4-10 as % of the plan for those weeks (world.json plan). Sales are the
        shoppers' weighted orders, scaled so weeks 1-3 match the plan, as in world_weekly.csv.
goal_2  sellers active or reduced in week 10, % of all sellers (everyone was active on November 2).
goal_3  orders sent back / orders placed, weeks 4-10, %. goal_3b: complaints per persona-week, weeks 4-10 as % of weeks 1-3
        (a 0.05 per persona-week prior, so a quiet weeks 1-3 does not divide by zero).
goal_4  shoppers' trust (1a and 1b), end minus start, 1-5 scale; only with check-in answers.
"""

from world import plan_usd


def _wsum(rows, w, key):
    return sum(w[r["persona"]] * (r[key] if not isinstance(r[key], bool) else int(r[key])) for r in rows)


def compute(personas, weekly, probe):
    w = {p["id"]: p["weight"] for p in personas}
    group = {p["id"]: p["group"] for p in personas}
    shop = [r for r in weekly if group[r["persona"]] == "shoppers"]
    early, late = [r for r in shop if r["week"] <= 3], [r for r in shop if r["week"] >= 4]
    sellers10 = [r for r in weekly if group[r["persona"]] != "shoppers" and r["week"] == 10]
    m = {}
    if early and late:
        plan = lambda weeks: sum(plan_usd(k) for k in weeks)
        m["goal_1"] = round(100 * _wsum(late, w, "orders") / max(_wsum(early, w, "orders"), 1e-9)
                            * plan(range(1, 4)) / plan(range(4, 11)), 1)
        m["goal_3"] = round(100 * _wsum(late, w, "returns") / max(_wsum(late, w, "orders"), 1e-9), 1)
    if sellers10:
        ok = sum(w[r["persona"]] for r in sellers10 if r["status"] in ("active", "reduced"))
        m["goal_2"] = round(100 * ok / sum(w[r["persona"]] for r in sellers10), 1)
    all_early = [r for r in weekly if r["week"] <= 3]
    all_late = [r for r in weekly if r["week"] >= 4]
    if all_early and all_late:
        rate = lambda rows: (sum(r["complained"] for r in rows) + 0.05 * len(rows)) / len(rows)   # 0.05/persona-week prior
        m["goal_3b"] = round(100 * rate(all_late) / rate(all_early), 1)
    trust = {}
    for a in probe:
        if group[a["persona"]] == "shoppers" and a["question"] in ("1a", "1b"):
            trust.setdefault(a["when"], {}).setdefault(a["persona"], []).append(a["score"])
    both = sorted(set(trust.get("start", {})) & set(trust.get("end", {})))   # a missing check-in drops that persona
    if both:
        mean = lambda when: sum(sum(trust[when][k]) for k in both) / sum(len(trust[when][k]) for k in both)
        m["goal_4"] = round(mean("end") - mean("start"), 2)
    m["units"] = {
        "goal_1": "sales in weeks 4-10 as % of the plan for weeks 4-10 (sales scaled so weeks 1-3 match the plan)",
        "goal_2": "% of sellers (weighted) active or reduced in week 10",
        "goal_3": "% of orders sent back, weeks 4-10 (weighted)",
        "goal_3b": "complaints per persona-week, weeks 4-10 as % of weeks 1-3",
        "goal_4": "shoppers' trust (1a, 1b) end minus start, 1-5 scale",
    }
    return m
