"""Ladder statistics: how often is each rung of the relaxation ladder needed, and how big is each relaxation?

  python -m evaluation.run_ladder_stats                       # all three families, 20 and 50 feeders
  python -m evaluation.run_ladder_stats --families ratio --sizes 20 --seeds 3 --time-limit 12
  python -m evaluation.run_ladder_stats --resume              # continue an interrupted run

Why families instead of just the three data-derived tiers: inside the observed range of shortfalls (3-20% of
demand, up to 32% at the 99.9th percentile) the ladder is expected to stay at rung 0 under our placeholder
policy. That is itself a result, but it says little about the ladder. So we also push on it:

  ratio     shortfall as a share of demand, from 3% up to 90%     (how much shortfall can the tool absorb?)
  override  a fixed 20% shortfall while the operator forces a growing share of the non-critical feeders to
            stay ON for the whole horizon                         (how do operator overrides push the ladder up?)
  tight     the ratio sweep again with a stricter policy: max downtime 6 slots, 2 switches per feeder per shift
                                                                  (how does the ladder react to stricter rules?)

Per instance we record the rung reached, whether every lower-rung "no plan" was PROVEN, and the size of each
relaxation: extra downtime slots (rung 1), Critical kW-slots that need approval (rung 2), coverage fraction and
uncovered kW (rung 3).
The system switching budget is scaled with grid size, max(base, ceil(0.6 n)): an ASSUMPTION.
"""
import argparse
import csv
import math
import random
import statistics as st
from dataclasses import replace
from time import perf_counter

from evaluation.tracking import prepare_run
from loadshed import Policy, Tier, solve_ladder
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day

FIELDS = ["family", "param", "n", "seed", "hour", "rung", "status", "proven", "harm", "total_shortfall_kw",
          "extra_downtime_slots", "critical_pairs", "critical_kw_slots", "coverage", "uncovered_kw", "ladder_s"]
DEFAULT_RATIOS = [0.03, 0.0684, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]
DEFAULT_KEEP = [0.0, 0.3, 0.5, 0.7, 0.9]


def run_instance(pol, family, param, n, seed, hour, ratio, keep_share, time_limit_s):
    p = build_problem(make_grid(n, seed), pol, make_scenario("r", ratio), slot_of_day(hour), episode_offset=8)
    if keep_share > 0:
        rng = random.Random(1000 * seed + n)
        pool = [f.id for f in p.feeders if f.tier != Tier.CRITICAL]
        chosen = rng.sample(pool, round(keep_share * len(pool)))
        p = replace(p, forced_keep=frozenset((i, t) for i in chosen for t in range(pol.horizon_slots)))
    t0 = perf_counter()
    r = solve_ladder(p, time_limit_s=time_limit_s)
    rel = {x.rule: x for x in r.relaxations}
    return dict(
        family=family, param=param, n=n, seed=seed, hour=hour, rung=r.rung, status=r.status, proven=r.proven,
        harm=None if r.objective is None else round(r.objective, 1), total_shortfall_kw=round(sum(p.shortfall_kw), 1),
        extra_downtime_slots=rel["MAX_DOWNTIME"].magnitude if "MAX_DOWNTIME" in rel else 0,
        critical_pairs=len(r.needs_approval), critical_kw_slots=round(rel["CRITICAL_CUT"].magnitude, 1) if "CRITICAL_CUT" in rel else 0,
        coverage=r.coverage_fraction, uncovered_kw=None if r.uncovered_kw is None else round(sum(r.uncovered_kw), 1),
        ladder_s=round(perf_counter() - t0, 2))


def load_rows(path):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            for k in FIELDS:
                if k not in ("family", "status", "proven"):
                    r[k] = None if r[k] in ("", None) else float(r[k])
            rows.append(r)
    return rows


def summarize(rows):
    def med(v):
        v = [x for x in v if x is not None]
        return f"{st.median(v):7.2f}" if v else "    n/a"

    for fam, label in (("ratio", "shortfall as a share of demand"), ("override", "share of non-critical feeders forced ON (20% shortfall)"),
                       ("tight", "shortfall share, TIGHT policy (max downtime 6, 2 switches per feeder)")):
        fr = [r for r in rows if r["family"] == fam]
        if not fr:
            continue
        print(f"\n=== family '{fam}': {label} ===")
        print("rung 0 = all rules | 1 = downtime relaxed | 2 = Critical needs approval | 3 = ESCALATE")
        print(f"{'n':>4} {'param':>7} {'inst':>4} | {'rung 0':>6} {'rung 1':>6} {'rung 2':>6} {'rung 3':>6} | "
              f"{'extra downtime slots':>20} | {'Critical kW-slots':>17} | {'coverage':>8} | {'proven':>6} | {'s':>5}")
        for n in sorted({int(r["n"]) for r in fr}):
            for param in sorted({r["param"] for r in fr if int(r["n"]) == n}):
                g = [r for r in fr if int(r["n"]) == n and r["param"] == param]
                cnt = [sum(1 for r in g if int(r["rung"]) == k) for k in range(4)]
                pct = lambda c: f"{100 * c / len(g):5.0f}%"
                r1 = [r["extra_downtime_slots"] for r in g if int(r["rung"]) == 1]
                r2 = [r["critical_kw_slots"] for r in g if int(r["rung"]) == 2]
                r3 = [r["coverage"] for r in g if int(r["rung"]) == 3]
                pv = sum(1 for r in g if r["proven"] == "True")
                print(f"{n:>4} {param:>7.4g} {len(g):>4} | {pct(cnt[0]):>6} {pct(cnt[1]):>6} {pct(cnt[2]):>6} {pct(cnt[3]):>6} | "
                      f"{med(r1):>20} | {med(r2):>17} | {med(r3):>8} | {pv:>3}/{len(g):<2} | {st.mean(r['ladder_s'] for r in g):5.1f}")
        for n in sorted({int(r["n"]) for r in fr}):
            first = sorted({r["param"] for r in fr if int(r["n"]) == n and int(r["rung"]) >= 1})
            print(f"  n={n}: first parameter value where any relaxation was needed: {first[0] if first else 'none in this range'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="configs/policy.yaml")
    ap.add_argument("--families", nargs="+", default=["ratio", "override", "tight"], choices=["ratio", "override", "tight"])
    ap.add_argument("--sizes", type=int, nargs="+", default=[20, 50])
    ap.add_argument("--ratios", type=float, nargs="+", default=DEFAULT_RATIOS)
    ap.add_argument("--keep-shares", type=float, nargs="+", default=DEFAULT_KEEP)
    ap.add_argument("--override-ratio", type=float, default=0.20)
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--hours", type=int, nargs="+", default=[17])
    ap.add_argument("--time-limit", type=float, default=24.0, help="per ladder call (each solve gets a twelfth of it)")
    ap.add_argument("--budget-per-feeder", type=float, default=0.6)
    ap.add_argument("--out", default="data/processed/benchmarks/ladder_stats.csv")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    base = Policy.from_yaml(a.policy)
    out = prepare_run(a, FIELDS, {"policy": a.policy})
    done = set()
    if a.resume and out.exists():
        done = {(r["family"], r["param"], int(r["n"]), int(r["seed"]), int(r["hour"])) for r in load_rows(out)}
    new_file = not (a.resume and out.exists())
    jobs = []
    for fam in a.families:
        for n in a.sizes:
            budget = max(base.system_switch_budget, math.ceil(a.budget_per_feeder * n))
            pol = replace(base, system_switch_budget=budget)
            if fam == "tight":
                pol = replace(pol, max_downtime_slots=6, feeder_switch_budget=2)
            params = a.keep_shares if fam == "override" else a.ratios
            for param in params:
                for seed in range(a.seeds):
                    for hour in a.hours:
                        jobs.append((pol, fam, param, n, seed, hour,
                                     a.override_ratio if fam == "override" else param, param if fam == "override" else 0.0))
    with open(out, "w" if new_file else "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for k, (pol, fam, param, n, seed, hour, ratio, keep) in enumerate(jobs, 1):
            if (fam, float(param), n, seed, hour) in done:
                continue
            row = run_instance(pol, fam, param, n, seed, hour, ratio, keep, a.time_limit)
            w.writerow(row)
            fh.flush()
            print(f"[{k}/{len(jobs)}] {fam} param={param} n={n} seed={seed}: rung {row['rung']} ({row['status']}) "
                  f"{row['ladder_s']}s", flush=True)
    summarize(load_rows(out))
    print("\nwrote", out)


if __name__ == "__main__":
    main()
