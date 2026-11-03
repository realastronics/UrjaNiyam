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
  vs bound      = reference is the B&B lower bound for the same constraint mode
  CP-SAT bounds belong to its integer approximation and are recorded, not pooled with B&B.

The system switching budget is scaled with grid size, max(base, ceil(budget_per_feeder * n)): an ASSUMPTION.
One CSV row is written as soon as an instance finishes, so a long run can be stopped and resumed.
"""
import argparse
import csv
import math
import statistics as st
from dataclasses import replace
from time import perf_counter

from evaluation.tracking import prepare_run
from loadshed import Policy, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, load_tiers, make_grid, slot_of_day
from loadshed.solvers import solve_bnb, solve_greedy, solve_min_conflicts
from loadshed.solvers.patterns import build_patterns

FIELDS = ["n", "tier", "ratio", "seed", "hour", "budget", "setup_s",
          "greedy_ok", "greedy_harm", "greedy_s", "sticky_ok", "sticky_harm", "sticky_s", "best_greedy_harm",
          "ls_mode", "ls_status", "ls_harm", "ls_s", "ls_iters",
          "bnb_mode", "bnb_status", "bnb_harm", "bnb_bound", "bnb_nodes", "bnb_s",
          "cpsat_mode", "cpsat_status", "cpsat_harm", "cpsat_bound", "cpsat_s"]


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
    row = dict.fromkeys(FIELDS)
    row.update(n=n, tier=tier, ratio=scen.peak_ratio, seed=seed, hour=hour, budget=budget)

    t = perf_counter(); g = solve_greedy(p); row["greedy_s"] = round(perf_counter() - t, 3)
    gv = verify(p, g.schedule); gh = compute_metrics(p, g.schedule).total_harm
    t = perf_counter(); s = solve_greedy(p, sticky=True); row["sticky_s"] = round(perf_counter() - t, 3)
    sv = verify(p, s.schedule); sh = compute_metrics(p, s.schedule).total_harm
    ok = lambda v: v.ok and not v.soft
    row.update(greedy_ok=ok(gv), greedy_harm=gh, sticky_ok=ok(sv), sticky_harm=sh)
    valid = [(h, sc) for h, sc, v in ((gh, g.schedule, gv), (sh, s.schedule, sv)) if ok(v)]
    row["best_greedy_harm"] = min(valid, key=lambda x: x[0])[0] if valid else None
    hint = min(valid, key=lambda x: x[0])[1] if valid else None

    t = perf_counter(); pats = build_patterns(p, True); row["setup_s"] = round(perf_counter() - t, 2)

    ls = solve_min_conflicts(p, time_limit_s=a.ls_time, seed=seed, patterns=pats)
    row.update(ls_mode=ls.mode, ls_status=ls.status, ls_harm=ls.objective,
               ls_s=round(ls.runtime_s, 2), ls_iters=ls.iterations)

    if n <= a.bnb_full_max_n:
        b = solve_bnb(p, time_limit_s=a.bnb_time, patterns=pats)
    else:
        b = solve_bnb(p, time_limit_s=a.bnb_time, node_limit=1, patterns=pats)      # root lower bound only
    row.update(bnb_mode=b.mode, bnb_status=b.status, bnb_harm=b.objective,
               bnb_bound=b.lower_bound, bnb_nodes=b.nodes,
               bnb_s=round(b.runtime_s, 2))

    if 0 < n <= a.oracle_max_n:
        from evaluation.oracle_cpsat import solve_oracle
        t = perf_counter()
        o = solve_oracle(p, time_limit_s=a.oracle_time, workers=a.workers, hint=hint)
        row.update(cpsat_mode=o.mode, cpsat_status=o.status, cpsat_harm=o.objective,
                   cpsat_bound=o.best_bound,
                   cpsat_s=round(perf_counter() - t, 2))
    return row


def load_rows(path):
    num = lambda v: None if v in ("", None) else float(v)
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            rows.append({k: (num(v) if k not in ("tier", "ls_mode", "ls_status", "bnb_mode", "bnb_status", "cpsat_mode", "cpsat_status",
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

    for mode in ("strict", "relaxed_downtime"):
        print(f"\n=== QUALITY: {mode} only (median gap %) ===")
        print("vs best found / vs B&B bound; each pair uses the same instances. Zero references give n/a.")
        print(f"{'n':>4} {'tier':<9} | {'best greedy':>17} | {'local search':>17} | {'B&B':>17} | {'CP-SAT':>17} | {'window':>7} | optimal status B&B/CP-SAT")
        for n in sizes:
            for tier in tiers:
                group = [r for r in rows if int(r["n"]) == n and r["tier"] == tier]
                cols = {k: ([], []) for k in ("best_greedy", "ls", "bnb", "cpsat")}
                windows, invalid = [], 0
                for row in group:
                    harms, bound = comparable(row, mode)
                    if not harms or bound is None:
                        continue
                    best = min(harms.values())
                    if bound > best + 1e-6 * max(1, abs(best)):
                        invalid += 1
                        continue
                    if best <= 0 or bound <= 0:
                        continue
                    windows.append(100 * (best - bound) / bound)
                    for name, harm in harms.items():
                        cols[name][0].append(100 * (harm - best) / best)
                        cols[name][1].append(100 * (harm - bound) / bound)
                fmt = lambda k: f"{med(cols[k][0])} /{med(cols[k][1])}"
                proven = [sum(r.get(k + "_mode") == mode and r[k + "_status"] == "OPTIMAL"
                              for r in group) for k in ("bnb", "cpsat")]
                if group:
                    print(f"{n:>4} {tier:<9} | {fmt('best_greedy')} | {fmt('ls')} | {fmt('bnb')} | {fmt('cpsat')} | {med(windows)} | {proven[0]}/{proven[1]}")
                    print("  paired samples:", {k: len(v[0]) for k, v in cols.items()},
                          "inconsistent bound rows:", invalid)
    print("\nB&B above --bnb-full-max-n is limited to one node; inspect bnb_nodes/status in the CSV.")


def comparable(row, mode):
    """Keep objectives and bounds from the same constraint mode; zero is a value."""
    harms = {}
    if mode == "strict" and row.get("best_greedy_harm") is not None:
        harms["best_greedy"] = row["best_greedy_harm"]
    for name in ("ls", "bnb", "cpsat"):
        if (row.get(name + "_mode") == mode and
                row.get(name + "_status") in ("OPTIMAL", "FEASIBLE") and
                row.get(name + "_harm") is not None):
            harms[name] = row[name + "_harm"]
    bounds = [row[name + "_bound"] for name in ("bnb",)
              if row.get(name + "_mode") == mode and
              row.get(name + "_status") in ("OPTIMAL", "FEASIBLE", "UNKNOWN") and
              row.get(name + "_bound") is not None]
    return harms, max(bounds) if bounds else None


def main():
    a = parse()
    base = Policy.from_yaml(a.policy)
    tiers = load_tiers(a.tiers)
    out = prepare_run(a, FIELDS, {"policy": a.policy, "tiers": a.tiers})
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
