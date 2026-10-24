"""Paired runs: for every seed, nodecision first and then plan, with the same seed and the same cache.

    python pairs.py --seeds 1-6 [--decisions decisions/plan.json] [--probe] [--strike on] [--model claude-haiku-5-5]
                    [--out runs/pairs] [--parallel 6] [--workers 24]

Weeks before a decision has any effect give the plan run the very same prompts as the nodecision run, so they read
the same cached answers and the two runs differ only from the decision's week on. The plan - nodecision difference
per seed then carries far less noise than two independent runs (6 seeds, goal_1: +-2.6 paired against +-11-14
unpaired). Seeds run side by side; the two runs of a seed run in order. Writes <out>/nodecision_<seed>/,
<out>/plan_<seed>/ and <out>/pairs.json (each seed's goals and the paired differences).
"""
import argparse
import json
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOALS = ("goal_1", "goal_2", "goal_3", "goal_3b", "goal_4")


def seeds(text):
    out = []
    for part in text.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def pair(a, seed):
    common = ["--seed", str(seed), "--model", a.model, "--bind", a.bind, "--workers", str(a.workers)] + (["--probe"] if a.probe else []) + \
             (["--strike", a.strike] if a.strike else [])
    got = {}
    for kind in ("nodecision", "plan"):
        out = Path(a.out) / f"{kind}_{seed}"
        extra = ["--decisions", a.decisions] if kind == "plan" else []
        with open(f"{out}.log", "w") as log:
            rc = subprocess.run([sys.executable, str(HERE / "sim.py"), "--type", kind, *common, *extra, "--out", str(out)],
                                stdout=log, stderr=subprocess.STDOUT).returncode
        got[kind] = json.loads((out / "metrics.json").read_text()) if rc == 0 else None
        print(f"seed {seed} {kind}: " + (json.dumps({g: got[kind].get(g) for g in GOALS}) if got[kind] else f"failed, see {out}.log"),
              flush=True)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="1-6", help="e.g. 1-6 or 1,3,5")
    ap.add_argument("--decisions", default=str(HERE / "decisions" / "plan.json"))
    ap.add_argument("--model", default="claude-haiku-5-5")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--strike", choices=("on", "off"))
    ap.add_argument("--bind", choices=("on", "off"), default="on")
    ap.add_argument("--out", default=str(HERE / "runs" / "pairs"))
    ap.add_argument("--parallel", type=int, default=6, help="seeds at once")
    ap.add_argument("--workers", type=int, default=24, help="threads per run (sim.py --workers)")
    a = ap.parse_args()
    Path(a.out).mkdir(parents=True, exist_ok=True)
    ss = seeds(a.seeds)
    with ThreadPoolExecutor(a.parallel) as ex:
        runs = dict(zip(ss, ex.map(lambda s: pair(a, s), ss)))
    diff = {}
    for g in GOALS:
        d = [r["plan"][g] - r["nodecision"][g] for r in runs.values()
             if r["plan"] and r["nodecision"] and r["plan"].get(g) is not None and r["nodecision"].get(g) is not None]
        if d:
            diff[g] = {"mean": round(statistics.mean(d), 2), "sd": round(statistics.stdev(d), 2) if len(d) > 1 else None,
                       "min": round(min(d), 2), "max": round(max(d), 2), "n": len(d)}
            print(f"plan - nodecision {g}: {diff[g]['mean']:+} (sd {diff[g]['sd']}, {diff[g]['min']:+} .. {diff[g]['max']:+}, n {len(d)})")
    (Path(a.out) / "pairs.json").write_text(json.dumps({"args": vars(a), "seeds": runs, "plan_minus_nodecision": diff},
                                                       indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
