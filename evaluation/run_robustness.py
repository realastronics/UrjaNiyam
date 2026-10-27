"""Robustness and stability experiments with the rolling-horizon loop.

  python -m evaluation.run_robustness                                  # all four experiments (about 25 min)
  python -m evaluation.run_robustness --experiments margin failure --n 10 --seeds 3
  python -m evaluation.run_robustness --resume                         # continue an interrupted run

  margin     safety margin x load-estimate error. What does insurance against bad load data cost (extra harm)
             and what does it buy (less unserved shortfall)?
  stability  warm start on/off x scenario tier, with a grid large enough for local search. When NOTHING in the world
             changed since the last cycle, how much does the plan change (plan revision), and how often does the
             committed action differ from what the previous plan predicted (commit surprise)? Unplanned reversals =
             an unpredicted switch that undoes a switch made at most 4 slots earlier.
  override   operator overrides. The same episode with and without them: what do they cost in harm?
             ('keep k' forces the k most-cut feeders to stay ON in the middle of the episode; 'cut k' forces k
             otherwise-idle feeders to be cut.)
  failure    breaker failure probability: unserved shortfall, extra switching, how the ladder copes.

Each episode is a full rolling-horizon run: replan every slot, commit one slot, simulate the actuation.
Harm is what was actually suffered (cut harm + switch cost). Unserved = shortfall not covered by REAL relief.
One CSV row per episode is written as soon as it finishes.
"""
import argparse
import csv
import statistics as st
from dataclasses import replace
from pathlib import Path
from time import perf_counter

from loadshed import Policy
from loadshed.loop import EpisodeConfig, Override, run_episode
from loadshed.sim import load_tiers, make_grid, make_scenario

FIELDS = ["experiment", "param1", "param2", "n", "seed", "harm", "unserved", "overshoot", "switches", "failed",
          "replans", "relaxed_cycles", "escalations", "quiet_cycles", "plan_revision_quiet", "surprise_quiet",
          "surprises", "unplanned_reversals", "mean_solve_s", "wall_s"]


def build_cfg(policy, n, seed, ratio, margin=None, episode_slots=16, time_limit_s=12.0, **kw):
    pol = policy if margin is None else replace(policy, safety_margin=margin)
    return EpisodeConfig(grid=make_grid(n, seed), policy=pol, scenario=make_scenario("r", ratio, episode_slots=episode_slots),
                         time_limit_s=time_limit_s, seed=seed, **kw)


def episode_row(experiment, p1, p2, n, seed, cfg):
    t0 = perf_counter()
    res = run_episode(cfg)
    m = res.metrics
    return res, dict(
        experiment=experiment, param1=p1, param2=p2, n=n, seed=seed, harm=round(m.total_harm, 1),
        unserved=round(m.unserved_kw_slots, 1), overshoot=round(m.overshoot_kw_slots, 1), switches=m.n_switches,
        failed=m.failed_commands, replans=m.replans, relaxed_cycles=sum(v for k, v in m.rung_counts.items() if k >= 1),
        escalations=m.escalations, quiet_cycles=m.quiet_cycles, plan_revision_quiet=m.plan_revision_rate_quiet,
        surprise_quiet=m.commit_surprise_rate_quiet, surprises=m.commit_surprises,
        unplanned_reversals=m.unplanned_reversals, mean_solve_s=round(m.mean_solve_s, 2), wall_s=round(perf_counter() - t0, 1))


def most_cut_feeders(res, k):
    """The k feeders that were cut for the most slots in an executed episode (ties: feeder order)."""
    ranked = sorted(res.executed.items(), key=lambda kv: -sum(kv[1]))
    return [fid for fid, row in ranked[:k] if sum(row) > 0]


def idle_feeders(res, grid, k):
    """k feeders that were never cut and are not Critical (candidates for a forced cut)."""
    from loadshed import Tier
    crit = {s.id for s in grid.specs if s.tier == Tier.CRITICAL}
    return [fid for fid, row in res.executed.items() if sum(row) == 0 and fid not in crit][:k]


def jobs(a, pol):
    """Yield (experiment, param1, param2, n, seed, config_factory). Overrides need a baseline, so they run lazily."""
    for exp in a.experiments:
        if exp == "margin":
            for sd in a.load_errors:
                for margin in a.margins:
                    for seed in range(a.seeds):
                        yield exp, sd, margin, a.n, seed, lambda sd=sd, margin=margin, seed=seed: build_cfg(
                            pol, a.n, seed, a.ratio, margin, a.episode_slots, a.time_limit, load_error=(sd, 0.0))
        elif exp == "stability":
            tiers = load_tiers(a.tiers)
            for tier, scen in tiers.items():
                for warm in (True, False):
                    for seed in range(a.seeds):
                        yield exp, tier, warm, a.stability_n, seed, lambda scen=scen, warm=warm, seed=seed: build_cfg(
                            pol, a.stability_n, seed, scen.peak_ratio, None, a.episode_slots, a.time_limit, warm_start=warm)
        elif exp == "failure":
            for pf in a.failure_probs:
                for seed in range(a.seeds):
                    yield exp, pf, 0, a.n, seed, lambda pf=pf, seed=seed: build_cfg(
                        pol, a.n, seed, a.ratio, None, a.episode_slots, a.time_limit, failure_prob=pf)
        elif exp == "override":
            for seed in range(a.seeds):
                yield exp, "none", 0, a.n, seed, lambda seed=seed: build_cfg(pol, a.n, seed, a.ratio, None, a.episode_slots, a.time_limit)
                for mode, k in [("keep", 1), ("keep", 2), ("keep", 3), ("cut", 1), ("cut", 2)]:
                    yield exp, mode, k, a.n, seed, "OVERRIDE"


def load_rows(path):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            for k in FIELDS:
                if k not in ("experiment", "param1", "param2"):
                    r[k] = None if r[k] in ("", None) else float(r[k])
            rows.append(r)
    return rows


def summarize(rows):
    def med(v, f="{:8.1f}"):
        v = [x for x in v if x is not None]
        return f.format(st.median(v)) if v else "     n/a"

    def mean(v, f="{:8.1f}"):
        v = [x for x in v if x is not None]
        return f.format(st.mean(v)) if v else "     n/a"

    pct = lambda v: med([None if x is None else 100 * x for x in v], "{:6.1f}%")
    ex = lambda name: [r for r in rows if r["experiment"] == name]

    if ex("margin"):
        print("\n=== margin: safety margin vs load-estimate error (harm actually suffered, and shortfall left unserved) ===")
        print(f"{'load error':>10} {'margin':>7} {'eps':>3} | {'median harm':>11} {'harm vs 5%':>10} | {'mean unserved kW-slots':>22} | {'episodes with any unserved':>26}")
        for sd in sorted({r["param1"] for r in ex("margin")}):
            ref = [r["harm"] for r in ex("margin") if r["param1"] == sd and abs(float(r["param2"]) - 0.05) < 1e-9]
            base = st.median(ref) if ref else None
            for mg in sorted({float(r["param2"]) for r in ex("margin") if r["param1"] == sd}):
                g = [r for r in ex("margin") if r["param1"] == sd and abs(float(r["param2"]) - mg) < 1e-9]
                rel = "     n/a" if not base else f"{100 * (st.median([r['harm'] for r in g]) - base) / base:+9.0f}%"
                print(f"{float(sd):>10.0%} {mg:>7.0%} {len(g):>3} | {med([r['harm'] for r in g], '{:11.0f}')} {rel:>10} | "
                      f"{mean([r['unserved'] for r in g], '{:22.0f}')} | {sum(1 for r in g if r['unserved'] > 0):>20}/{len(g)}")

    if ex("stability"):
        print("\n=== stability: warm start on/off, nothing-changed ('quiet') cycles only ===")
        print(f"{'tier':>9} {'warm':>5} {'eps':>3} | {'plan revision':>13} {'commit surprise':>15} | {'unplanned reversals':>19} | {'switches':>8} | {'harm':>8}")
        for tier in ("mild", "moderate", "crisis"):
            for warm in ("True", "False"):
                g = [r for r in ex("stability") if r["param1"] == tier and r["param2"] == warm]
                if g:
                    print(f"{tier:>9} {warm:>5} {len(g):>3} | {pct([r['plan_revision_quiet'] for r in g]):>13} "
                          f"{pct([r['surprise_quiet'] for r in g]):>15} | {mean([r['unplanned_reversals'] for r in g], '{:19.1f}')} | "
                          f"{mean([r['switches'] for r in g], '{:8.1f}')} | {med([r['harm'] for r in g], '{:8.0f}')}")

    if ex("override"):
        print("\n=== override: the same episode with and without operator overrides ===")
        print("(k is capped by how many feeders the baseline actually cut or left idle; harm ratio = harm with / harm without)")
        print(f"{'override':>10} {'eps':>3} | {'median extra harm':>17} {'median harm ratio':>17} | {'mean unserved kW-slots':>22}")
        base = {int(r["seed"]): r for r in ex("override") if r["param1"] == "none"}
        for mode, k in [("keep", 1), ("keep", 2), ("keep", 3), ("cut", 1), ("cut", 2)]:
            g = [r for r in ex("override") if r["param1"] == mode and int(float(r["param2"])) == k and int(r["seed"]) in base]
            if g:
                extra = [r["harm"] - base[int(r["seed"])]["harm"] for r in g]
                ratio = [r["harm"] / base[int(r["seed"])]["harm"] for r in g if base[int(r["seed"])]["harm"] > 0]
                print(f"{mode + ' ' + str(k):>10} {len(g):>3} | {med(extra, '{:17.0f}')} {med(ratio, '{:16.1f}x')} | {mean([r['unserved'] for r in g], '{:22.0f}')}")

    if ex("failure"):
        print("\n=== failure: breaker failure probability ===")
        print(f"{'fail prob':>9} {'eps':>3} | {'failed cmds':>11} {'extra switches':>14} | {'median harm':>11} {'mean unserved kW-slots':>23} | {'relaxed cycles':>14} {'escalations':>11}")
        sw0 = st.mean([r["switches"] for r in ex("failure") if float(r["param1"]) == 0.0] or [0])
        for pf in sorted({float(r["param1"]) for r in ex("failure")}):
            g = [r for r in ex("failure") if float(r["param1"]) == pf]
            print(f"{pf:>9.0%} {len(g):>3} | {mean([r['failed'] for r in g], '{:11.1f}')} {st.mean([r['switches'] for r in g]) - sw0:>14.1f} | "
                  f"{med([r['harm'] for r in g], '{:11.0f}')} {mean([r['unserved'] for r in g], '{:23.0f}')} | "
                  f"{mean([r['relaxed_cycles'] for r in g], '{:14.1f}')} {mean([r['escalations'] for r in g], '{:11.1f}')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", default="configs/policy.yaml")
    ap.add_argument("--tiers", default="data/processed/scenarios/tiers.json")
    ap.add_argument("--experiments", nargs="+", default=["margin", "stability", "override", "failure"],
                    choices=["margin", "stability", "override", "failure"])
    ap.add_argument("--n", type=int, default=10, help="grid size for margin, override and failure (10 = exact solver)")
    ap.add_argument("--stability-n", type=int, default=20, help="grid size for stability (above 12 = local search)")
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--ratio", type=float, default=0.20)
    ap.add_argument("--margins", type=float, nargs="+", default=[0.0, 0.05, 0.15, 0.30, 0.50])
    ap.add_argument("--load-errors", type=float, nargs="+", default=[0.1, 0.2, 0.3])
    ap.add_argument("--failure-probs", type=float, nargs="+", default=[0.0, 0.05, 0.10, 0.20, 0.30])
    ap.add_argument("--episode-slots", type=int, default=16)
    ap.add_argument("--time-limit", type=float, default=12.0, help="per replan (each solve gets a twelfth of it)")
    ap.add_argument("--out", default="data/processed/benchmarks/robustness.csv")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    pol = Policy.from_yaml(a.policy)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if a.resume and out.exists():
        done = {(r["experiment"], str(r["param1"]), str(r["param2"]), int(r["n"]), int(r["seed"])) for r in load_rows(out)}
    new_file = not (a.resume and out.exists())
    baselines = {}
    with open(out, "w" if new_file else "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        for exp, p1, p2, n, seed, make in jobs(a, pol):
            if (exp, str(p1), str(p2), n, seed) in done:
                continue
            if make == "OVERRIDE":
                if seed not in baselines:                      # e.g. after --resume: rebuild the baseline (not written again)
                    bcfg = build_cfg(pol, n, seed, a.ratio, None, a.episode_slots, a.time_limit)
                    baselines[seed] = (run_episode(bcfg), bcfg)
                base_res, base_cfg = baselines[seed]
                if p1 == "keep":
                    ids = most_cut_feeders(base_res, p2)
                    ev = tuple(Override(4, 12, i, "keep") for i in ids)
                else:
                    ids = idle_feeders(base_res, base_cfg.grid, p2)
                    ev = tuple(Override(4, 8, i, "cut") for i in ids)
                cfg = replace(base_cfg, events=ev)
            else:
                cfg = make()
            res, row = episode_row(exp, p1, p2, n, seed, cfg)
            if exp == "override" and p1 == "none":
                baselines[seed] = (res, cfg)
            w.writerow(row)
            fh.flush()
            print(f"{exp} {p1} {p2} n={n} seed={seed}: harm {row['harm']}, unserved {row['unserved']}, {row['wall_s']}s", flush=True)
    summarize(load_rows(out))
    print("\nwrote", out)


if __name__ == "__main__":
    main()
