"""Weighted min-conflicts local search. Written from scratch (numpy for arrays only).

State    : one valid pattern per feeder (patterns.py). A pattern already obeys every single-feeder rule
           (min cut/restore, feeder switch budget, promises, overrides, critical approval, history),
           so the ONLY things that can be violated are
              (a) the 16 per-slot relief sums, and
              (b) the system-wide switching budget.
Score    : F = harm  +  sum_t W[t] * (relief shortfall in slot t)  +  Wb * (switch-budget excess)
Move     : pick a feeder, switch it to the pattern with the lowest F (min-conflicts value choice).
           While something is violated, the feeder is picked among those that can help the violated slot
           (a conflicted variable). While nothing is violated, feeders are sampled for cost reduction.
Weights  : breakout. At a local minimum with a violation, multiply the weight of the violated constraints by 1.5.
           While feasible, decay all weights, so the search can trade a little violation for lower cost
           and then repair it (strategic oscillation).
Memory   : a short tabu list stops it flipping straight back.
Start    : the better of the two greedy baselines, so the result is never worse than the baseline.
Restarts : when stuck, restart from the best plan with a random part of it shaken up.

It cannot PROVE optimality or infeasibility (it only proves infeasibility via a cheap necessary check).
Every plan it returns has passed verify().
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

import numpy as np

from ..metrics import compute_metrics
from ..model import Problem, Schedule
from ..verify import verify
from .greedy import solve_greedy
from .patterns import build_patterns

TOL = 1e-6


@dataclass(frozen=True)
class LSResult:
    schedule: Schedule | None
    status: str                 # FEASIBLE (a verified plan, no optimality proof), INFEASIBLE (proven), UNKNOWN
    mode: str                   # "strict" or "relaxed_downtime"
    objective: float | None     # total harm of the schedule (metrics.py)
    start_objective: float | None   # harm of the greedy plan it started from
    iterations: int
    restarts: int
    runtime_s: float
    setup_s: float = 0.0        # time spent building the valid patterns (not counted in time_limit_s)


def solve_min_conflicts(problem: Problem, time_limit_s: float = 10.0, seed: int = 0,
                        max_iters: int | None = None, relax_downtime_if_infeasible: bool = True,
                        sample: int = 6, force_relaxed: bool = False,
                        warm_start: Schedule | None = None, patterns: list | None = None) -> LSResult:
    """time_limit_s is the budget for the SEARCH (greedy start and local search); building the valid
    patterns is reported separately as setup_s. `patterns` = strict-mode patterns already built by
    build_patterns(problem, True), to share the setup between solvers.
    force_relaxed=True starts directly at rung 1 (max downtime relaxed); used by the ladder."""
    t0 = perf_counter()
    if force_relaxed:
        r = _search(problem, False, time_limit_s, max_iters, seed, sample, warm_start, None)
        return LSResult(r.schedule, r.status, r.mode, r.objective, r.start_objective,
                        r.iterations, r.restarts, perf_counter() - t0, r.setup_s)
    r = _search(problem, True, 0.7 * time_limit_s, max_iters, seed, sample, warm_start, patterns)
    if r.schedule is None and relax_downtime_if_infeasible:
        spent = perf_counter() - t0 - r.setup_s
        r2 = _search(problem, False, max(0.3 * time_limit_s, time_limit_s - spent), max_iters, seed, sample,
                     warm_start, None)
        r = LSResult(r2.schedule, r2.status, r2.mode, r2.objective, r2.start_objective,
                     r.iterations + r2.iterations, r.restarts + r2.restarts, 0.0, r.setup_s + r2.setup_s)
    return LSResult(r.schedule, r.status, r.mode, r.objective, r.start_objective,
                    r.iterations, r.restarts, perf_counter() - t0, r.setup_s)


def _search(problem: Problem, strict: bool, budget_s: float, max_iters: int | None,
            seed: int, sample: int, warm_start: Schedule | None = None, pats: list | None = None) -> LSResult:
    pol, H = problem.policy, problem.policy.horizon_slots
    feeders = problem.feeders
    n = len(feeders)
    mode = "strict" if strict else "relaxed_downtime"
    rng = np.random.default_rng(seed)
    need = np.array([s * (1 + pol.safety_margin) for s in problem.shortfall_kw])
    c0 = -pol.overshoot_lambda * need.sum()

    # ---------------- domains (setup, not counted in the time budget) and a cheap necessary feasibility check
    t_setup = perf_counter()
    if pats is None:
        pats = build_patterns(problem, strict)
    setup_s = perf_counter() - t_setup
    if any(len(p) == 0 for p in pats):
        return LSResult(None, "INFEASIBLE", mode, None, None, 0, 0, 0.0, setup_s)
    deadline = perf_counter() + budget_s
    maxnet = np.array([p.net.max(axis=0) for p in pats])            # (n, H)
    if (maxnet.sum(axis=0) < need - TOL).any():
        return LSResult(None, "INFEASIBLE", mode, None, None, 0, 0, 0.0, setup_s)

    # ---------------- starting point: the greedy baselines
    best_sched, best_obj, start_sched, start_h = None, np.inf, None, np.inf
    plain = None
    cands = [solve_greedy(problem, sticky=False).schedule, solve_greedy(problem, sticky=True).schedule]
    if warm_start is not None and set(warm_start.cut) == {f.id for f in feeders} \
            and all(len(r) == H for r in warm_start.cut.values()):
        cands.append(warm_start)               # e.g. last cycle's plan shifted forward by one slot
    for g in cands:
        if plain is None:
            plain = g                           # the plain greedy: the fallback start
        v = verify(problem, g)
        if v.ok and not (strict and v.soft):
            h = compute_metrics(problem, g).total_harm
            if h < best_obj:
                best_sched, best_obj = g, h
            if h < start_h:
                start_sched, start_h = g, h
    if start_sched is None:
        start_sched = plain
    start_objective = best_obj if np.isfinite(best_obj) else None

    index = [{p.rows[j].tobytes(): j for j in range(len(p))} for p in pats]

    def sel_from(sched: Schedule) -> np.ndarray:
        sel = np.zeros(n, dtype=int)
        for i, f in enumerate(feeders):
            row = np.array(sched.cut[f.id], dtype=np.int8).tobytes()
            sel[i] = index[i].get(row, int(np.argmin(pats[i].cost)))
        return sel

    # ---------------- switching-budget excess (vectorised over candidate patterns)
    base_sw = sum(problem.hist(f.id).switches_this_shift for f in feeders)
    resets = [t for t in range(H) if (problem.shift_offset + t) % pol.shift_slots == 0]
    starts = sorted(set([0] + resets))
    segs = [(a, (starts[k + 1] if k + 1 < len(starts) else H), base_sw if (a == 0 and 0 not in resets) else 0)
            for k, a in enumerate(starts)]

    def budget_excess(ev: np.ndarray, nf: np.ndarray) -> np.ndarray:
        out = 0
        for a, b, base in segs:
            e = ev[..., a:b]
            before = base + np.cumsum(e, axis=-1) - e
            over = np.maximum(0, before + nf[..., a:b] - pol.system_switch_budget)
            out = out + np.where(nf[..., a:b] >= 1, over, 0).sum(axis=-1)
        return out

    # ---------------- state
    sel = sel_from(start_sched if start_sched is not None else Schedule(
        {f.id: (0,) * H for f in feeders}))
    cur_net = np.array([pats[i].net[sel[i]] for i in range(n)])
    cur_ev = np.array([pats[i].ev[sel[i]] for i in range(n)]).astype(int)
    cur_nf = np.array([pats[i].nfev[sel[i]] for i in range(n)]).astype(int)
    cur_cost = np.array([pats[i].cost[sel[i]] for i in range(n)])
    net_tot, ev_tot, nf_tot, cost_sum = cur_net.sum(0), cur_ev.sum(0), cur_nf.sum(0), float(cur_cost.sum())

    w0 = (best_obj if np.isfinite(best_obj) else max(float(cost_sum), 1.0)) / max(float(need.sum()), 1.0)
    w0 = max(w0, 1e-3)
    W = np.full(H, 2.0 * w0)
    Wb0 = w0 * max(float(need.mean()), 1.0)
    Wb = Wb0
    best_sel = sel.copy() if best_sched is not None else None
    if best_sched is not None:
        best_sel = sel_from(best_sched)
    tabu = [np.zeros(len(p), dtype=int) for p in pats]

    def record_if_feasible(sel_now: np.ndarray) -> None:
        nonlocal best_sched, best_obj, best_sel
        sched = Schedule({feeders[i].id: tuple(int(x) for x in pats[i].rows[sel_now[i]]) for i in range(n)})
        v = verify(problem, sched)
        if v.ok and not (strict and v.soft):
            h = compute_metrics(problem, sched).total_harm
            if h < best_obj - 1e-9:
                best_sched, best_obj, best_sel = sched, h, sel_now.copy()

    it, restarts, last_improve = 0, 0, 0
    restart_after = max(3000, 40 * n)
    while perf_counter() < deadline and (max_iters is None or it < max_iters):
        it += 1
        deficit = np.maximum(0.0, need - net_tot)
        viol = float(budget_excess(ev_tot, nf_tot))
        violated = deficit.max() > TOL or viol > 0
        f_cur = cost_sum + float(W @ deficit) + Wb * viol

        if not violated:
            h_now = cost_sum + c0
            if h_now < best_obj - 1e-9:
                prev = best_obj
                record_if_feasible(sel)
                if best_obj < prev - 1e-9:
                    last_improve = it
            W = np.maximum(W * 0.97, 0.05 * w0)               # feasible: let costs fall, allow some violation

        # ---- choose which feeders to look at (conflicted variables first)
        if violated:
            if deficit.max() > TOL:
                t = int(rng.choice(H, p=deficit / deficit.sum()))
                pool = np.nonzero(maxnet[:, t] > cur_net[:, t] + TOL)[0]
            else:
                pool = np.nonzero(cur_ev.sum(axis=1) > 0)[0]
            if len(pool) == 0:
                pool = np.arange(n)
        else:
            pool = np.arange(n)
        cand = rng.choice(pool, size=min(sample, len(pool)), replace=False)

        best_move = None
        for i in cand:
            p = pats[i]
            new_net = (net_tot - cur_net[i]) + p.net
            pen = np.maximum(0.0, need - new_net) @ W
            bv = budget_excess((ev_tot - cur_ev[i]) + p.ev, (nf_tot - cur_nf[i]) + p.nfev)
            f_new = p.cost + pen + Wb * bv
            f_new[sel[i]] = np.inf                              # must be a real move
            blocked = tabu[i] > it
            if blocked.any():
                f_new = np.where(blocked & (f_new >= best_obj - c0 - 1e-9) , np.inf, f_new)
            j = int(np.argmin(f_new))
            fn = float(f_new[j])
            if best_move is None or fn - cur_cost[i] < best_move[0]:
                best_move = (fn - cur_cost[i], i, j, fn)        # compare by improvement over own current cost
        move_delta = None
        if best_move is not None:
            _, i, j, fn = best_move
            # exact change of F for this move: F_new - F_cur
            move_delta = (cost_sum - cur_cost[i] + fn) - f_cur

        if best_move is not None and move_delta is not None and move_delta < -1e-9:
            accept = True
        else:
            accept = False
            if violated:                                          # local minimum: breakout weights
                W = np.minimum(W * np.where(deficit > TOL, 1.5, 1.0), 1e12)   # geometric growth: reaches any cost scale fast
                if viol > 0:
                    Wb = min(Wb * 1.5, 1e12)
                if best_move is not None and rng.random() < 0.3:  # plateau/escape move
                    accept = True

        if accept and best_move is not None:
            _, i, j, _ = best_move
            tabu[i][sel[i]] = it + int(rng.integers(5, 15))
            net_tot = net_tot - cur_net[i] + pats[i].net[j]
            ev_tot = ev_tot - cur_ev[i] + pats[i].ev[j]
            nf_tot = nf_tot - cur_nf[i] + pats[i].nfev[j]
            cost_sum += float(pats[i].cost[j] - cur_cost[i])
            sel[i] = j
            cur_net[i], cur_ev[i], cur_nf[i] = pats[i].net[j], pats[i].ev[j].astype(int), pats[i].nfev[j].astype(int)
            cur_cost[i] = pats[i].cost[j]

        # ---- restart from the best plan with part of it shaken up
        if it - last_improve > restart_after:
            restarts += 1
            last_improve = it
            base = best_sel if best_sel is not None else sel
            sel = base.copy()
            k = max(1, int(0.1 * n))
            for i in rng.choice(n, size=k, replace=False):
                sel[i] = int(rng.integers(len(pats[i])))
            cur_net = np.array([pats[i].net[sel[i]] for i in range(n)])
            cur_ev = np.array([pats[i].ev[sel[i]] for i in range(n)]).astype(int)
            cur_nf = np.array([pats[i].nfev[sel[i]] for i in range(n)]).astype(int)
            cur_cost = np.array([pats[i].cost[sel[i]] for i in range(n)])
            net_tot, ev_tot, nf_tot, cost_sum = cur_net.sum(0), cur_ev.sum(0), cur_nf.sum(0), float(cur_cost.sum())
            W = np.full(H, 2.0 * w0)
            Wb = Wb0

    if best_sched is None:
        return LSResult(None, "UNKNOWN", mode, None, start_objective, it, restarts, 0.0, setup_s)
    return LSResult(best_sched, "FEASIBLE", mode, float(best_obj), start_objective, it, restarts, 0.0, setup_s)
