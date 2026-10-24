"""Branch-and-bound alone vs the hybrid (local search first, then branch-and-bound from that plan).

  python -m evaluation.run_hybrid                       # 10 and 20 feeders
  python -m evaluation.run_hybrid --sizes 10 15 20 30 --seeds 3 --time 20

Per instance: B&B alone (greedy start), the hybrid, and optionally CP-SAT as the answer key (small sizes only).
'proven' = the solver finished with a proof of optimality inside its time budget.
Gap = percent more harm than the best plan found by any of the solvers on that instance.
"""
import argparse
import csv
import math
import statistics as st
from dataclasses import replace
from pathlib import Path

from evaluation.oracle_cpsat import solve_oracle
from loadshed import Policy
from loadshed.sim import build_problem, load_tiers, make_grid, slot_of_day
from loadshed.solvers import solve_bnb, solve_hybrid

ap = argparse.ArgumentParser()
ap.add_argument("--policy", default="configs/policy.yaml")
ap.add_argument("--tiers", default="data/processed/scenarios/tiers.json")
ap.add_argument("--sizes", type=int, nargs="+", default=[10, 20])
ap.add_argument("--seeds", type=int, default=2)
ap.add_argument("--hours", type=int, nargs="+", default=[17])
ap.add_argument("--time", type=float, default=15.0, help="search budget for B&B alone and for the hybrid")
ap.add_argument("--oracle-time", type=float, default=20.0, help="0 skips CP-SAT")
ap.add_argument("--workers", type=int, default=4)
ap.add_argument("--out", default="data/processed/benchmarks/hybrid_vs_bnb.csv")
a = ap.parse_args()

base = Policy.from_yaml(a.policy)
tiers = load_tiers(a.tiers)
rows = []
for n in a.sizes:
    pol = replace(base, system_switch_budget=max(base.system_switch_budget, math.ceil(0.6 * n)))
    for tier, scen in tiers.items():
        for seed in range(a.seeds):
            for hour in a.hours:
                p = build_problem(make_grid(n, seed), pol, scen, slot_of_day(hour), episode_offset=8)
                b = solve_bnb(p, time_limit_s=a.time, relax_downtime_if_infeasible=False)
                h = solve_hybrid(p, time_limit_s=a.time, seed=seed, relax_downtime_if_infeasible=False)
                o = solve_oracle(p, time_limit_s=a.oracle_time, workers=a.workers,
                                 relax_downtime_if_infeasible=False) if a.oracle_time > 0 else None
                rows.append(dict(
                    n=n, tier=tier, seed=seed, hour=hour,
                    bnb_status=b.status, bnb_harm=b.objective, bnb_bound=b.lower_bound, bnb_s=round(b.runtime_s, 2), bnb_nodes=b.nodes,
                    hy_status=h.status, hy_harm=h.objective, hy_bound=h.lower_bound, hy_s=round(h.runtime_s, 2),
                    hy_nodes=h.nodes, hy_ls_harm=h.ls_objective,
                    cpsat_status=None if o is None else o.status, cpsat_harm=None if o is None else o.objective,
                    cpsat_s=None if o is None else round(o.runtime_s, 2)))
                print(f"n={n} {tier} seed={seed}: B&B {b.status} {b.runtime_s:.1f}s | hybrid {h.status} {h.runtime_s:.1f}s", flush=True)

out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

print("\nproven = finished with a proof of optimality; time is the median over instances the solver PROVED")
print(f"{'n':>3} {'tier':<9} {'inst':>4} | {'proven B&B':>10} {'proven hybrid':>13} | {'time B&B':>8} {'time hyb':>8} | "
      f"{'gap B&B %':>9} {'gap LS-start %':>14} {'gap hybrid %':>12}")
for n in a.sizes:
    for tier in tiers:
        r = [x for x in rows if x["n"] == n and x["tier"] == tier]
        pb = [x for x in r if x["bnb_status"] == "OPTIMAL"]
        ph = [x for x in r if x["hy_status"] == "OPTIMAL"]
        gaps = {"b": [], "l": [], "h": []}
        for x in r:
            known = [v for v in (x["bnb_harm"], x["hy_harm"], x["cpsat_harm"], x["hy_ls_harm"]) if v]
            if not known:
                continue
            best = min(known)
            for k, key in (("b", "bnb_harm"), ("l", "hy_ls_harm"), ("h", "hy_harm")):
                if x[key]:
                    gaps[k].append(100 * (x[key] - best) / best)
        m = lambda v: f"{st.median(v):9.1f}" if v else "      n/a"
        t = lambda v: f"{st.median(v):8.1f}" if v else "     n/a"
        print(f"{n:>3} {tier:<9} {len(r):>4} | {len(pb):>7}/{len(r):<2} {len(ph):>10}/{len(r):<2} | "
              f"{t([x['bnb_s'] for x in pb])} {t([x['hy_s'] for x in ph])} | {m(gaps['b'])} {m(gaps['l']):>14} {m(gaps['h']):>12}")
print("\nwrote", out)
