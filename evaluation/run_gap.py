"""Greedy vs CP-SAT oracle on generated grids. Offline benchmark.

  python -m evaluation.run_gap                      # 10 and 50 feeders, tiers from data/processed/scenarios/tiers.json
  python -m evaluation.run_gap --sizes 10 50 200 --seeds 5 --time-limit 30

The system switching budget is scaled with grid size: max(base, ceil(budget_per_feeder * n)).
That scaling is an ASSUMPTION (a fixed number would be absurdly tight at 200+ feeders).
"""
import argparse
import csv
import math
import statistics as st
from dataclasses import replace
from pathlib import Path

from evaluation.oracle_cpsat import solve_oracle
from evaluation.run_scale import comparable
from loadshed import Policy, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, load_tiers, make_grid, slot_of_day
from loadshed.solvers import solve_bnb, solve_greedy, solve_greedy_sticky, solve_min_conflicts

ap = argparse.ArgumentParser()
ap.add_argument("--policy", default="configs/policy.yaml")
ap.add_argument("--tiers", default="data/processed/scenarios/tiers.json")
ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50])
ap.add_argument("--seeds", type=int, default=5)
ap.add_argument("--hours", type=int, nargs="+", default=[3, 10, 17, 20])
ap.add_argument("--time-limit", type=float, default=20.0)
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--bnb-time", type=float, default=20.0, help="time limit for our branch-and-bound; 0 = skip it")
ap.add_argument("--ls-time", type=float, default=10.0, help="time limit for our local search; 0 = skip it")
ap.add_argument("--budget-per-feeder", type=float, default=0.6)
ap.add_argument("--out", default="data/processed/benchmarks/gap_greedy_vs_oracle.csv")
a = ap.parse_args()

base = Policy.from_yaml(a.policy)
tiers = load_tiers(a.tiers)
rows = []
for n in a.sizes:
    pol = replace(base, system_switch_budget=max(base.system_switch_budget, math.ceil(a.budget_per_feeder * n)))
    for tier, scen in tiers.items():
        for seed in range(a.seeds):
            for hour in a.hours:
                p = build_problem(make_grid(n, seed), pol, scen, slot_of_day(hour), episode_offset=8)
                g = solve_greedy(p)
                gv = verify(p, g.schedule)
                gm = compute_metrics(p, g.schedule)
                s_ = solve_greedy_sticky(p)
                sv = verify(p, s_.schedule)
                sm = compute_metrics(p, s_.schedule)
                best_h = min([m.total_harm for m, v in ((gm, gv), (sm, sv)) if v.ok and not v.soft], default=None)
                best_sched = (s_.schedule if (sv.ok and not sv.soft and sm.total_harm <= gm.total_harm) else g.schedule)
                o = solve_oracle(p, time_limit_s=a.time_limit, workers=a.workers,
                                 hint=best_sched if (gv.ok or sv.ok) else None)
                b = solve_bnb(p, time_limit_s=a.bnb_time) if a.bnb_time > 0 else None
                ls = solve_min_conflicts(p, time_limit_s=a.ls_time, seed=seed) if a.ls_time > 0 else None
                rows.append(dict(
                    n=n, tier=tier, ratio=round(scen.peak_ratio, 4), seed=seed, hour=hour,
                    greedy_ok=gv.ok, greedy_soft=len(gv.soft), greedy_harm=gm.total_harm,
                    greedy_s=round(g.runtime_s, 4),
                    sticky_ok=sv.ok, sticky_soft=len(sv.soft), sticky_harm=sm.total_harm,
                    best_harm=best_h,
                    oracle_status=o.status, oracle_mode=o.mode,
                    oracle_harm=o.objective,
                    oracle_bound=o.best_bound,
                    oracle_proven_gap=None if o.gap is None else round(o.gap, 5),
                    oracle_s=round(o.runtime_s, 2),
                    bnb_status=None if b is None else b.status, bnb_mode=None if b is None else b.mode,
                    bnb_harm=None if b is None or b.objective is None else b.objective,
                    bnb_bound=None if b is None or b.lower_bound is None else b.lower_bound,
                    bnb_nodes=None if b is None else b.nodes, bnb_s=None if b is None else round(b.runtime_s, 2),
                    ls_status=None if ls is None else ls.status, ls_mode=None if ls is None else ls.mode,
                    ls_harm=None if ls is None or ls.objective is None else ls.objective,
                    ls_iters=None if ls is None else ls.iterations, ls_s=None if ls is None else round(ls.runtime_s, 2)))
    print("done n =", n, flush=True)

out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

def references(row, mode):
    mapped = dict(row, best_greedy_harm=row["best_harm"])
    for suffix in ("mode", "status", "harm", "bound"):
        mapped["cpsat_" + suffix] = row["oracle_" + suffix]
    harms, bound = comparable(mapped, mode)
    return list(harms.values()), bound

def gap_stats(rows_, key):
    g = [100 * (x[key] - x["oracle_harm"]) / x["oracle_harm"] for x in rows_
         if x[key] is not None and x["oracle_harm"] and x["oracle_harm"] > 0]
    return f"{st.median(g):6.1f}/{max(g):7.1f}" if g else "    n/a"


print("\nGap = how much MORE harm than the oracle's best solution, in %  (median/max).")
print("Where the oracle could not prove optimality (status FEASIBLE) its best solution is only an upper bound on the optimum,")
print("CP-SAT optimality refers to its integer approximation; the report compares actual schedule harm.")
print(f"{'n':>4} {'tier':<9} {'inst':>4} | proven/found/relax/infeas | greedy ok | sticky ok | greedy gap  | sticky gap  | best-of-2 gap | oracle s")
for n in a.sizes:
    for tier in tiers:
        r = [x for x in rows if x["n"] == n and x["tier"] == tier]
        proven = [x for x in r if x["oracle_status"] == "OPTIMAL" and x["oracle_mode"] == "strict"]
        opt = [x for x in r if x["oracle_status"] in ("OPTIMAL", "FEASIBLE") and x["oracle_mode"] == "strict"]
        rel = [x for x in r if x["oracle_status"] in ("OPTIMAL", "FEASIBLE") and x["oracle_mode"] != "strict"]
        inf = [x for x in r if x["oracle_status"] == "INFEASIBLE"]
        so = lambda k: [dict(x, **{k + "_h": x[k + "_harm"] if (x[k + "_ok"] and x[k + "_soft"] == 0) else None}) for x in opt]
        gg = [dict(x, gh=x["greedy_harm"] if x["greedy_ok"] and x["greedy_soft"] == 0 else None) for x in opt]
        ss = [dict(x, sh=x["sticky_harm"] if x["sticky_ok"] and x["sticky_soft"] == 0 else None) for x in opt]
        print(f"{n:>4} {tier:<9} {len(r):>4} | {len(proven):>3}/{len(opt):>3}/{len(rel):>3}/{len(inf):>3} | "
              f"{sum(x['greedy_ok'] for x in r):>4}/{len(r):<4} | {sum(x['sticky_ok'] for x in r):>4}/{len(r):<4} | "
              f"{gap_stats(gg, 'gh')} | {gap_stats(ss, 'sh')} | {gap_stats(opt, 'best_harm'):>13} | {st.mean(x['oracle_s'] for x in r):7.2f}")

if a.bnb_time > 0:
    def med(v):
        return f"{st.median(v):6.1f}" if v else "   n/a"
    print("\nStrict mode only: branch-and-bound (ours) and the interval for the best greedy baseline (best-of-2):")
    print("  vs best found = harm above the best solution anyone found (a LOWER estimate of the true gap)")
    print("  vs best bound = harm above the same-mode B&B lower bound   (an UPPER estimate of the true gap)")
    print(f"{'n':>4} {'tier':<9} | bnb proven | bnb vs oracle %  (median/max) | best-of-2 gap, median %: vs best found .. vs best bound")
    for n in a.sizes:
        for tier in tiers:
            r = [x for x in rows if x["n"] == n and x["tier"] == tier and x["oracle_mode"] == "strict" and x["bnb_mode"] == "strict"]
            allr = [x for x in rows if x["n"] == n and x["tier"] == tier]
            pr = sum(1 for x in allr if x["bnb_status"] == "OPTIMAL" and x["bnb_mode"] == "strict")
            d = [100 * (x["bnb_harm"] - x["oracle_harm"]) / x["oracle_harm"] for x in r
                 if x["bnb_harm"] is not None and x["oracle_harm"]]
            lo, hi = [], []
            for x in allr:
                ubs, bound = references(x, "strict")
                if (x["best_harm"] is not None and ubs and min(ubs) > 0
                        and bound is not None and 0 < bound <= min(ubs)):
                    lo.append(100 * (x["best_harm"] - min(ubs)) / min(ubs))
                    hi.append(100 * (x["best_harm"] - bound) / bound)
            dtxt = f"{med(d)} / {max(d):6.1f}" if d else "      n/a"
            print(f"{n:>4} {tier:<9} | {pr:>4}/{len(allr):<5} | {dtxt:<28} | {med(lo)} .. {med(hi)}")

if a.ls_time > 0:
    def med2(v):
        return f"{st.median(v):6.1f}" if v else "   n/a"
    print("\nStrict-mode local search (ours, weighted min-conflicts). Positive 'vs best found' = local search used MORE harm than the best solution any solver found.")
    print(f"{'n':>4} {'tier':<9} | LS vs best found %: median / max | LS gain over best greedy %: median | LS gap interval, median %: vs best found .. vs best bound | LS s")
    for n in a.sizes:
        for tier in tiers:
            allr = [x for x in rows if x["n"] == n and x["tier"] == tier and x["ls_harm"] is not None and x["ls_mode"] == "strict"
                    and x["ls_status"] in ("OPTIMAL", "FEASIBLE")]
            if not allr:
                continue
            d, gain, lo, hi = [], [], [], []
            for x in allr:
                ubs, bound = references(x, "strict")
                if ubs and min(ubs) > 0:
                    d.append(100 * (x["ls_harm"] - min(ubs)) / min(ubs))
                if x["best_harm"]:
                    gain.append(100 * (x["best_harm"] - x["ls_harm"]) / x["best_harm"])
                if ubs and min(ubs) > 0 and bound is not None and 0 < bound <= min(ubs):
                    lo.append(100 * (x["ls_harm"] - min(ubs)) / min(ubs))
                    hi.append(100 * (x["ls_harm"] - bound) / bound)
            dtxt = f"{med2(d)} / {max(d):6.1f}" if d else "      n/a"
            print(f"{n:>4} {tier:<9} | {dtxt:<31} | {med2(gain):>30} | {med2(lo)} .. {med2(hi):<31} | {st.mean(x['ls_s'] for x in allr):5.1f}")
print("\nwrote", out)
