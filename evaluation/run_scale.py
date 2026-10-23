"""Scale sweep: every solver across grid sizes and shortfall tiers. OFFLINE benchmark (uses the CP-SAT oracle).

  python -m evaluation.run_scale                                   # 10, 50, 200, 500 feeders, 2 seeds, 17:00
  python -m evaluation.run_scale --sizes 10 50 --seeds 3 --hours 10 17 --ls-time 15
  python -m evaluation.run_scale --resume                          # continue an interrupted run

What is measured per instance
  setup   time to build every feeder's valid patterns (shared by branch-and-bound and local search)
  greedy  both baselines (plain, sticky); local search (ls); branch-and-bound (bnb); CP-SAT oracle (cpsat)
  Time limits apply to the SEARCH of each solver; setup is reported separately.
  Branch-and-bound runs a full search up to --bnb-full-max-n feeders; above that it only computes its root
  lower bound (one node), because there its tree search cannot finish and the bound is what is useful.
  CP-SAT runs up to --oracle-max-n feeders (it gets slow to build and to solve at 500).

Gap = how much MORE harm a plan has than a reference, in percent.
  vs best known = reference is the best plan any solver found (the true gap is at least this)
  vs bound      = reference is the best proven lower bound   (the true gap is at most this)

The system switching budget is scaled with grid size, max(base, ceil(budget_per_feeder * n)): an ASSUMPTION.
One CSV row is written as soon as an instance finishes, so a long run can be stopped and resumed.
"""
import argparse
import csv
import math
import statistics as st
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from evaluation.oracle_cpsat import solve_oracle
from loadshed import Policy, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, load_tiers, make_grid, slot_of_day
from loadshed.solvers import solve_bnb, solve_greedy, solve_min_conflicts
from loadshed.solvers.patterns import build_patterns

FIELDS = ["n", "tier", "ratio", "seed", "hour", "budget", "setup_s",
          "greedy_ok", "greedy_harm", "greedy_s", "sticky_ok", "sticky_harm", "sticky_s", "best_greedy_harm",
          "ls_status", "ls_harm", "ls_s", "ls_iters",
          "bnb_mode", "bnb_status", "bnb_harm", "bnb_bound", "bnb_nodes", "bnb_s",
          "cpsat_status", "cpsat_harm", "cpsat_bound", "cpsat_s"]


def parse():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="configs/policy.yaml")
    ap.add_argument("--tiers", default="data/processed/scenarios/tiers.json")
    ap.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200, 500])
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--hours", type=int, nargs="+", default=[17])
    ap.add_argument("--ls-time", type=float, default=30.0)
    ap.add_argument("--bnb-time", type=float, default=60.0)
    ap.add_argument("--bnb-full-max-n", type=int, default=50)
    ap.add_argument("--oracle-time", type=float, default=60.0)
    ap.add_argument("--oracle-max-n", type=int, default=200, help="0 disables the oracle")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--budget-per-feeder", type=float, default=0.6)
    ap.add_argument("--out", default="data/processed/benchmarks/scale_sweep.csv")
    ap.add_argument("--resume", action="store_true")
    return ap.parse_args()


def run_instance(a, base, tier, scen, n, seed, hour):
    budget = max(base.system_switch_budget, math.ceil(a.budget_per_feeder * n))
    pol = replace(base, system_switch_budget=budget)
    p = build_problem(make_grid(n, seed), pol, scen, slot_of_day(hour), episode_offset=8)
    row = dict(n=n, tier=tier, ratio=round(scen.peak_ratio, 4), seed=seed, hour=hour, budget=budget)

    t = perf_counter(); g = solve_greedy(p); row["greedy_s"] = round(perf_counter() - t, 3)
    gv = verify(p, g.schedule); gh = compute_metrics(p, g.schedule).total_harm
    t = perf_counter(); s = solve_greedy(p, sticky=True); row["sticky_s"] = round(perf_counter() - t, 3)
    sv = verify(p, s.schedule); sh = compute_metrics(p, s.schedule).total_harm
    ok = lambda v: v.ok and not v.soft
    row.update(greedy_ok=ok(gv), greedy_harm=round(gh, 2), sticky_ok=ok(sv), sticky_harm=round(sh, 2))
    valid = [(h, sc) for h, sc, v in ((gh, g.schedule, gv), (sh, s.schedule, sv)) if ok(v)]
    row["best_greedy_harm"] = round(min(valid, key=lambda x: x[0])[0], 2) if valid else None
    hint = min(valid, key=lambda x: x[0])[1] if valid else None

    t = perf_counter(); pats = build_patterns(p, True); row["setup_s"] = round(perf_counter() - t, 2)

    ls = solve_min_conflicts(p, time_limit_s=a.ls_time, seed=seed, patterns=pats)
    row.update(ls_status=ls.status, ls_harm=None if ls.objective is None else round(ls.objective, 2),
               ls_s=round(ls.runtime_s, 2), ls_iters=ls.iterations)

    if n <= a.bnb_full_max_n:
        b = solve_bnb(p, time_limit_s=a.bnb_time, patterns=pats)
    else:
        b = solve_bnb(p, time_limit_s=a.bnb_time, node_limit=1, patterns=pats)      # root lower bound only
    row.update(bnb_mode=b.mode, bnb_status=b.status, bnb_harm=None if b.objective is None else round(b.objective, 2),
               bnb_bound=None if b.lower_bound is None else round(b.lower_bound, 2), bnb_nodes=b.nodes,
               bnb_s=round(b.runtime_s, 2))

    if 0 < n <= a.oracle_max_n:
        t = perf_counter()
        o = solve_oracle(p, time_limit_s=a.oracle_time, workers=a.workers, hint=hint)
        row.update(cpsat_status=o.status, cpsat_harm=None if o.objective is None else round(o.objective, 2),
                   cpsat_bound=None if o.best_bound is None else round(o.best_bound, 2),
                   cpsat_s=round(perf_counter() - t, 2))
    return row


def load_rows(path):
    num = lambda v: None if v in ("", None) else float(v)
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append({k: (num(v) if k not in ("tier", "ls_status", "bnb_mode", "bnb_status", "cpsat_status",
                                                  "greedy_ok", "sticky_ok") else v) for k, v in r.items()})
    return rows


def summarize(rows, sizes, tiers):
    def med(v):
        v = [x for x in v if x is not None]
        return f"{st.median(v):7.1f}" if v else "    n/a"

    def secs(v):
        v = [x for x in v if x is not None]
        return f"{st.median(v):6.1f}" if v else "   n/a"

    print("\n=== TIME (median seconds) ===")
    print(f"{'n':>4} {'tier':<9} {'inst':>4} | {'setup':>6} {'greedy':>6} {'sticky':>6} | {'LS':>6} | {'B&B':>6} | {'CP-SAT':>6}")
    for n in sizes:
        for tier in tiers:
            r = [x for x in rows if int(x["n"]) == n and x["tier"] == tier]
            if r:
                print(f"{n:>4} {tier:<9} {len(r):>4} | {secs([x['setup_s'] for x in r])} {secs([x['greedy_s'] for x in r])} "
                      f"{secs([x['sticky_s'] for x in r])} | {secs([x['ls_s'] for x in r])} | "
                      f"{secs([x['bnb_s'] for x in r])} | {secs([x['cpsat_s'] for x in r])}")

    print("\n=== QUALITY: gap in %, median (vs best known plan .. vs best proven lower bound) ===")
    print("  The true gap of a solver lies between its two numbers. 'window' = how well ANYONE can bound the optimum:")
    print("  (best known - best bound) / best bound. A big window means the sizes where nobody can say how far a plan is.")
    print(f"{'n':>4} {'tier':<9} | {'best greedy':>17} | {'local search':>17} | {'B&B (full)':>17} | {'CP-SAT':>17} | {'window':>7} | proven (B&B/CP-SAT)")
    for n in sizes:
        for tier in tiers:
            r = [x for x in rows if int(x["n"]) == n and x["tier"] == tier]
            if not r:
                continue
            cols = {k: ([], []) for k in ("best_greedy", "ls", "bnb", "cpsat")}
            win = []
            for x in r:
                hs = {"best_greedy": x["best_greedy_harm"], "ls": x["ls_harm"], "bnb": x["bnb_harm"], "cpsat": x["cpsat_harm"]}
                ubs = [v for v in hs.values() if v]
                lbs = [v for v in (x["bnb_bound"], x["cpsat_bound"]) if v]
                if not ubs or not lbs:
                    continue
                ub, lb = min(ubs), max(lbs)
                win.append(100 * (ub - lb) / lb)
                for k, h in hs.items():
                    if h:
                        cols[k][0].append(100 * (h - ub) / ub)
                        cols[k][1].append(100 * (h - lb) / lb)
            fmt = lambda k: f"{med(cols[k][0])} ..{med(cols[k][1])}"
            pb = sum(1 for x in r if x["bnb_status"] == "OPTIMAL"); pc = sum(1 for x in r if x["cpsat_status"] == "OPTIMAL")
            print(f"{n:>4} {tier:<9} | {fmt('best_greedy'):>17} | {fmt('ls'):>17} | {fmt('bnb'):>17} | {fmt('cpsat'):>17} | "
                  f"{med(win)} | {pb}/{len(r)}  {pc}/{len(r)}")
    print("\n(B&B above --bnb-full-max-n feeders is a ONE-node run: its harm is the starting greedy plan, only its bound is meaningful.)")


def main():
    a = parse()
    base = Policy.from_yaml(a.policy)
    tiers = load_tiers(a.tiers)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if a.resume and out.exists():
        done = {(int(r["n"]), r["tier"], int(r["seed"]), int(r["hour"])) for r in load_rows(out)}
    new_file = not (a.resume and out.exists())
    with open(out, "w" if new_file else "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        total = len(a.sizes) * len(tiers) * a.seeds * len(a.hours)
        k = 0
        for n in a.sizes:
            for tier, scen in tiers.items():
                for seed in range(a.seeds):
                    for hour in a.hours:
                        k += 1
                        if (n, tier, seed, hour) in done:
                            continue
                        t0 = perf_counter()
                        row = run_instance(a, base, tier, scen, n, seed, hour)
                        w.writerow(row)
                        fh.flush()
                        print(f"[{k}/{total}] n={n} {tier} seed={seed} hour={hour}: setup {row['setup_s']}s, "
                              f"LS {row['ls_harm']} , total {perf_counter() - t0:.0f}s", flush=True)
    summarize(load_rows(out), a.sizes, list(tiers))
    print("\nwrote", out)


if __name__ == "__main__":
    main()
