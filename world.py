"""The world layer: company variables (world.json) linked week by week. No LLM, no randomness.

check(decisions)  every rule a locked decision must meet (menu, levels, 1-3 actions, budget, message, target)
World.begin(w)    applies the decisions in force, moves the strike and the budget, returns the sentences
                  a persona reads about Aisle's terms and parcel speed
World.end(w)      reads the personas' answers: sales, parcels, Rapid Parcel's backlog, one CSV row
"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
W = {k: v["value"] for k, v in json.loads((HERE / "world.json").read_text()).items() if not k.startswith("_")}
CFG = json.loads((HERE / "aisle.json").read_text())
SHOPPER = ("buys as usual", "buys less", "paused", "moved to a rival")
SELLER = ("active", "reduced", "paused", "left")
CATEGORIES = tuple(W["category_mix"])
POLICY = ("checkout_promise", "free_ship_threshold", "spare_capacity", "payout_freq", "complaint_sla", "discount",
          "return_shipping", "fake_claims", "seller_audit", "ads")
COLUMNS = ["week", "budget_spent", "budget_left", "sales_usd", "plan_usd", "sales_vs_plan", "orders",
           *[f"shoppers_{s.replace(' ', '_')}" for s in SHOPPER], *[f"sellers_{s}" for s in SELLER],
           "strike_state", "rapid_days", "backlog_parcels", *POLICY, "returns", "complaints"]


def level(s):
    return str(s).replace("–", "-").replace("—", "-").strip('"')


def cost(a):
    return W["action_cost"][a["action"]][level(a["level"])]


def plan_usd(week):
    """The plan's sales for one week: a normal November week, the Black Friday / Cyber Monday split, December lift."""
    normal = W["plan_sales_month"] * 12 / 52
    if week in (4, 5):
        return W["plan_bf_cm"] * W["plan_split"][f"week_{week}"]
    if week in (6, 7, 8):
        return normal * W["holiday_lift"]["december"]
    if week in (9, 10):
        return normal * W["holiday_lift"]["january"]
    return normal


def check(decisions, reaches):
    """Errors that would make a board (or the TALP checks) reject the decisions; [] when fine."""
    err = []
    if len(decisions) > W["decisions_max"]:
        err.append(f"{len(decisions)} decisions, at most {W['decisions_max']}")
    lo, hi = W["actions_per_decision"]
    for i, d in enumerate(decisions, 1):
        acts = d.get("actions") or []
        if not 1 <= d.get("week", 0) <= 10:
            err.append(f"decision {i}: week {d.get('week')} is not 1-10")
        if not lo <= len(acts) <= hi:
            err.append(f"decision {i}: {len(acts)} actions, must be {lo}-{hi}")
        if len({a.get('action') for a in acts}) < len(acts):
            err.append(f"decision {i}: the same action twice")
        for a in acts:
            levels = W["action_cost"].get(a.get("action"))
            if levels is None:
                err.append(f"decision {i}: '{a.get('action')}' is not on the menu ({', '.join(W['action_cost'])})")
            elif level(a.get("level")) not in levels:
                err.append(f"decision {i}: {a['action']} level '{a.get('level')}' not in {list(levels)}")
            if a.get("action") == "discount" and a.get("category") not in CATEGORIES:
                err.append(f"decision {i}: discount needs \"category\": one of {CATEGORIES}")
        if not 0 < len(d.get("message", "")) <= W["message_max_chars"]:
            err.append(f"decision {i}: message must be 1-{W['message_max_chars']} characters")
        if not d.get("channels") or set(d["channels"]) - set(W["channels"]):
            err.append(f"decision {i}: channels must be from {W['channels']}")
        if not reaches(d.get("target", "")):
            err.append(f"decision {i}: target '{d.get('target')}' reaches nobody")
    if not err:
        total = sum(cost(a) for d in decisions for a in d["actions"])
        if total > W["budget_total"]:
            err.append(f"both decisions cost ${total:,}, budget ${W['budget_total']:,}")
    return err


class World:
    def __init__(self, decisions, news, strike=None):
        self.decisions, self.news, self.strike = decisions, news, strike
        self.s = {"strike_state": W["strike_state"], "backlog": 0.0, "budget_spent": 0, "rapid_days": ""}
        self.normal_parcels = W["plan_sales_month"] * 12 / 52 / W["avg_order"]
        self.rapid_share = W["carrier_share"]["Rapid Parcel"] / 100
        self.parcel_scale = None
        self.rows = []
        self.policy(0)

    def policy(self, week):
        """Decision variables in force this week: the default, changed by each action for its duration."""
        s = self.s
        s.update({k: W[k] for k in POLICY})
        for d in self.decisions:
            for a in d["actions"]:
                if not d["week"] <= week < d["week"] + CFG["actions"][a["action"]]["weeks"]:
                    continue
                act, lv = a["action"], level(a["level"])
                if act == "extra_capacity":
                    s["spare_capacity"] = int(lv.strip("+%")) / 100
                elif act == "delivery_promise":
                    s["checkout_promise"] = lv
                elif act == "free_shipping":
                    s["free_ship_threshold"] = 0 if lv == "no minimum" else int(lv.strip("$"))
                elif act == "faster_payouts":
                    s["payout_freq"] = "twice a week"
                elif act == "extra_support":
                    s["complaint_sla"] = "24h"
                elif act == "discount":
                    s["discount"] = f"{lv} {a['category']}"
                elif act == "free_returns":
                    s["return_shipping"] = "free"
                elif act == "authenticity_guarantee":
                    s["fake_claims"] = "double refund if proven fake"
                elif act == "seller_check":
                    s["seller_audit"] = "on"
                elif act == "ads":
                    s["ads"] = lv

    def begin(self, week):
        """This week's state; returns {"terms": ..., "parcels": ...}, the sentences every persona reads."""
        s = self.s
        for n in self.news:
            if n["week"] == week and "strike_state" in n.get("set", {}):
                s["strike_state"] = n["set"]["strike_state"]
        if self.strike and week >= 5:
            s["strike_state"] = "on" if self.strike == "on" else "none"
        self.policy(week)
        s["budget_spent"] += sum(cost(a) for d in self.decisions if d["week"] == week for a in d["actions"])
        on = s["strike_state"] == "on"
        s["rapid_cap"] = self.normal_parcels * self.rapid_share * W["carrier_headroom"] * (W["strike_capacity"] if on else 1)
        mid = sum(W["normal_days"]) / 2
        s["rapid_days"] = round(mid + (W["strike_extra_days"] if on else 0) + 7 * s["backlog"] / s["rapid_cap"], 1)
        free = "on every order" if s["free_ship_threshold"] == 0 else f"over ${s['free_ship_threshold']}"
        terms = (f"Aisle's terms this week: checkout says delivery in {s['checkout_promise']}; free shipping {free}; "
                 f"return shipping {s['return_shipping']}; complaints answered within {s['complaint_sla']}; "
                 f"fake-product claims {s['fake_claims']}; sellers paid {s['payout_freq']}.")
        strike = " (its drivers are on strike)" if on else ""
        parcels = (f"Parcels this week: Rapid Parcel{strike}, which carries 45% of Aisle's orders, takes about "
                   f"{s['rapid_days']:g} days; Meridian and Dash about {mid:g} days.")
        return {"terms": terms, "parcels": parcels}

    def end(self, week, personas, rows):
        s, p = self.s, {x["id"]: x for x in personas}
        shop = [r for r in rows if p[r["persona"]]["group"] == "shoppers"]
        orders = sum(r["orders"] * p[r["persona"]]["weight"] for r in shop)
        if self.parcel_scale is None:      # ours: week 1's parcels equal the plan's; the ratio holds for the run
            self.parcel_scale = self.normal_parcels / max(orders, 1)
        load = orders * self.parcel_scale * self.rapid_share + s["backlog"]
        spare = self.normal_parcels * (1 - self.rapid_share) * s["spare_capacity"]   # Meridian + Dash extra, takes Rapid's overflow
        s["backlog"] = max(0.0, load - s["rapid_cap"] - spare)
        self.row(week, rows, personas, sales=orders * W["avg_order"], orders=orders,
                 returns=sum(r["returns"] * p[r["persona"]]["weight"] for r in shop),
                 complaints=sum(p[r["persona"]]["weight"] for r in rows if r["complained"]))

    def row(self, week, rows, personas, sales=0, orders=0, returns=0, complaints=0):
        s, g = self.s, {x["id"]: x["group"] for x in personas}
        n = lambda shopper, st: sum(1 for r in rows if (g[r["persona"]] == "shoppers") == shopper and r["status"] == st)
        plan = plan_usd(week) if week else 0
        self.rows.append({
            "week": week, "budget_spent": s["budget_spent"], "budget_left": W["budget_total"] - s["budget_spent"],
            "sales_usd": round(sales), "plan_usd": round(plan), "sales_vs_plan": round(sales / plan, 3) if plan else "",
            "orders": round(orders), **{f"shoppers_{x.replace(' ', '_')}": n(True, x) for x in SHOPPER},
            **{f"sellers_{x}": n(False, x) for x in SELLER}, "strike_state": s["strike_state"],
            "rapid_days": s["rapid_days"], "backlog_parcels": round(s["backlog"]), **{k: s[k] for k in POLICY},
            "returns": round(returns), "complaints": round(complaints)})

    def write(self, path):
        with open(path, "w", newline="") as f:
            out = csv.DictWriter(f, COLUMNS)
            out.writeheader()
            out.writerows(self.rows)
