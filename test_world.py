"""World layer checks, no LLM:  python test_world.py"""
import filecmp
import subprocess
import sys

import glob
import json

from world import W, World, check, normalize

# Each menu action: (level, variable, value while in force, weeks in force).
ACTIONS = {
    "discount": ("10%", "discount", "10% fashion", 1),   # category from the message
    "free_shipping": ("no minimum", "free_ship_threshold", 0, 2),
    "extra_capacity": ("+40%", "spare_capacity", 0.4, 2),
    "delivery_promise": ("on time or a $5 credit", "checkout_promise", "on time or a $5 credit", 6),   # to week 10
    "seller_check": ("on", "seller_audit", "on", 6),
    "authenticity_guarantee": ("on", "fake_claims", "double refund if proven fake", 6),
    "free_returns": ("on", "return_shipping", "free", 4),
    "faster_payouts": ("on", "payout_freq", "twice a week", 6),
    "extra_support": ("on", "complaint_sla", "24h", 4),
    "ads": ("$0.4M", "ads", "$0.4M", 2),
}


def decision(week, *actions):
    acts = [{"action": a, "level": ACTIONS[a][0]} for a in actions]
    return normalize([{"week": week, "target": "everyone", "actions": acts, "message": "10% off fashion", "channels": ["email"],
                       "why": []}])[0]


def test_each_action_moves_its_variable():
    for action, (_, var, value, weeks) in ACTIONS.items():
        w = World([decision(5, action)], [])
        seen = {}
        for week in range(1, 11):
            w.begin(week)
            seen[week] = w.s[var]
        on = [k for k, v in seen.items() if v == value]
        assert on == list(range(5, 5 + weeks)), (action, seen)
        assert seen[4] == W[var], (action, seen[4])


def test_budget_and_three_actions():
    d = [decision(4, "extra_capacity", "delivery_promise", "extra_support"), decision(6, "faster_payouts")]
    assert check(d, lambda t: True) == []
    w = World(d, [])
    spent = {}
    for week in range(1, 11):
        w.begin(week)
        spent[week] = w.s["budget_spent"]
    assert spent[3] == 0 and spent[4] == 800000 + 200000 + 250000 and spent[10] == spent[4] + 150000, spent


def test_strike_backlog_and_spare_capacity():
    def backlog(decisions):
        w = World(decisions, [], strike="on")
        rows = [{"persona": "S", "orders": 1, "returns": 0, "complained": False, "status": "buys as usual"}]
        people = [{"id": "S", "group": "shoppers", "weight": 1}]
        for week in range(1, 8):
            w.begin(week)
            w.end(week, people, rows)
        return {r["week"]: r["backlog_parcels"] for r in w.rows}
    plain, helped = backlog([]), backlog([decision(5, "extra_capacity")])
    assert plain[4] == 0 and plain[5] > 0, plain                 # backlog starts with the strike in week 5
    assert helped[5] < plain[5] and helped[7] < plain[7], (plain, helped)   # +40% in weeks 5-6, less left after


def test_team_decision_files():
    """The team's candidates name actions by the menu row; they must pass and keep their meaning."""
    files = glob.glob("/srv/lighthouse/hackathon-coz/projeler/talp-simulation-hackathon-2026/yarisma/aisle/kararlar/*.json")
    for f in files:
        if f.endswith(".taslak.json"):          # drafts, week not chosen yet
            assert check(normalize(json.load(open(f))), lambda t: True)
            continue
        d = normalize(json.load(open(f)))
        assert check(d, lambda t: True) == [], (f, check(d, lambda t: True))
    return len(files)


def test_same_inputs_same_world():
    out = []
    for n in (1, 2):
        out.append(f"/tmp/talp-aisle-env-test-{n}")
        subprocess.run([sys.executable, "sim.py", "--type", "plan", "--model", "fake", "--strike", "on", "--out", out[-1]],
                       check=True, capture_output=True)
    assert filecmp.cmp(f"{out[0]}/world_weekly.csv", f"{out[1]}/world_weekly.csv", shallow=False)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
