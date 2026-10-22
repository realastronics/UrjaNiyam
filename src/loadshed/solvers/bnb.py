"""Branch-and-bound with bounds propagation. Written from scratch (numpy for arrays only).

Variables  : one per feeder. Its DOMAIN is the set of valid 16-slot patterns (see patterns.py).
Constraints: for every slot t   sum over feeders of net[t] >= need[t]      (relief, surge included)
             plus the system-wide switching budget.
Objective  : sum of pattern costs (+ a constant). Same objective as metrics.py.

Search
  1. PROPAGATE (bounds propagation on the relief sums). For slot t, a feeder can contribute at most
     max over its alive patterns of net[t]. If the other feeders' maxima plus a pattern's net[t] fall
     short of need[t], that pattern can never be part of a solution: delete it from the domain.
     Repeat until nothing changes. Also checks the switching budget against the switches that are unavoidable.
  2. BOUND. A Lagrangian lower bound on the cost of the best completion. Multipliers pi >= 0 for the 16 relief
     constraints are found once at the root (subgradient method); any pi >= 0 gives a valid bound, and the
     bound at a node is  pi.need + const + sum over feeders of (min over alive patterns of cost - pi.clip(net, 0, need)).
     (Capping net at need is what stops a huge feeder from being 'cut fractionally' to cover a small shortfall.)
     Prune the node if the bound cannot beat the incumbent.
  3. BRANCH on the feeder whose best and second-best choices differ most (largest regret), trying its
     patterns cheapest-first.
  Incumbent: the better of the two greedy baselines (if valid), improved by search.

Every schedule it returns has passed verify(). If the max-downtime rule makes the problem infeasible,
it retries with that rule relaxed (rung 1 of the ladder), exactly like the oracle.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from time import perf_counter

import numpy as np

from ..metrics import compute_metrics
from ..model import Problem, Schedule
from ..verify import verify
from .greedy import solve_greedy
from .patterns import enumerate_patterns, prune_dominated

TOL = 1e-6


@dataclass(frozen=True)
class BBResult:
    schedule: Schedule | None
    status: str                 # OPTIMAL (proven), FEASIBLE (stopped by a limit), INFEASIBLE (proven), UNKNOWN
    mode: str                   # "strict" or "relaxed_downtime"
    objective: float | None     # total harm of the schedule (metrics.py)
    lower_bound: float | None   # proven lower bound on the optimum
    nodes: int
    runtime_s: float


def solve_bnb(problem: Problem, time_limit_s: float = 30.0, node_limit: int | None = None,
              relax_downtime_if_infeasible: bool = True, lagrange_iters: int = 100,
              force_relaxed: bool = False) -> BBResult:
    """force_relaxed=True starts directly at rung 1 (max downtime relaxed); used by the ladder."""
    t0 = perf_counter()
    deadline = t0 + time_limit_s
    r = _run(problem, not force_relaxed, deadline, node_limit, lagrange_iters)
    if r.status == "INFEASIBLE" and relax_downtime_if_infeasible:
        r2 = _run(problem, False, deadline, node_limit, lagrange_iters)
        r = BBResult(r2.schedule, r2.status, r2.mode, r2.objective, r2.lower_bound, r.nodes + r2.nodes, 0.0)
    return BBResult(r.schedule, r.status, r.mode, r.objective, r.lower_bound, r.nodes, perf_counter() - t0)


def _run(problem: Problem, strict: bool, deadline: float, node_limit: int | None, lagr_iters: int) -> BBResult:
    pol, H = problem.policy, problem.policy.horizon_slots
    feeders = problem.feeders
    n = len(feeders)
    mode = "strict" if strict else "relaxed_downtime"
    need = np.array([s * (1 + pol.safety_margin) for s in problem.shortfall_kw])
    c0 = -pol.overshoot_lambda * need.sum()

    # ---------------- domains
    pats = []
    for f in feeders:
        p = prune_dominated(enumerate_patterns(problem, f, strict))
        if len(p) == 0:
            return BBResult(None, "INFEASIBLE", mode, None, None, 0, 0.0)
        pats.append(p)
    alive0 = [np.ones(len(p), dtype=bool) for p in pats]
    # For the BOUND only: relief above need[t] cannot help cover slot t, so credit at most need[t].
    # (The implied constraint  sum_f clip(net_f[t], 0, need[t]) >= need[t]  holds for every valid plan.)
    # Negative net (a restoration surge) is clipped to 0 so the implied constraint stays valid.
    netc = [np.clip(p.net, 0.0, need) for p in pats]

    # ---------------- incumbent from the greedy baselines
    ub, best = np.inf, None
    for sticky in (False, True):
        g = solve_greedy(problem, sticky=sticky).schedule
        v = verify(problem, g)
        if v.ok and not (strict and v.soft):
            h = compute_metrics(problem, g).total_harm
            if h < ub:
                ub, best = h, g

    base_sw = sum(problem.hist(f.id).switches_this_shift for f in feeders)

    def budget_ok(evsum, nfsum) -> bool:
        before = base_sw
        for t in range(H):
            if (problem.shift_offset + t) % pol.shift_slots == 0:
                before = 0
            if nfsum[t] >= 1 and before + nfsum[t] > pol.system_switch_budget:
                return False
            before += evsum[t]
        return True

    # ---------------- bounds propagation
    def propagate(alive) -> bool:
        maxnet = np.empty((n, H))
        minev = np.empty((n, H))
        minnf = np.empty((n, H))
        for i in range(n):
            a = alive[i]
            maxnet[i] = pats[i].net[a].max(axis=0)
            minev[i] = pats[i].ev[a].min(axis=0)
            minnf[i] = pats[i].nfev[a].min(axis=0)
        total = maxnet.sum(axis=0)
        while True:
            if (total < need - TOL).any():
                return False
            changed = False
            for i in range(n):
                thr = need - (total - maxnet[i])
                keep = alive[i] & (pats[i].net >= thr - TOL).all(axis=1)
                cnt = int(np.count_nonzero(keep))
                if cnt == 0:
                    return False
                if cnt != int(np.count_nonzero(alive[i])):
                    alive[i] = keep
                    changed = True
                    new = pats[i].net[keep].max(axis=0)
                    total += new - maxnet[i]
                    maxnet[i] = new
                    minev[i] = pats[i].ev[keep].min(axis=0)
                    minnf[i] = pats[i].nfev[keep].min(axis=0)
            if not changed:
                break
        return budget_ok(minev.sum(axis=0), minnf.sum(axis=0))

    # ---------------- root: propagate, then Lagrangian multipliers
    root = [a.copy() for a in alive0]
    if not propagate(root):
        return BBResult(None, "INFEASIBLE", mode, None, None, 0, 0.0)

    pi = np.zeros(H)
    best_L, best_pi = -np.inf, pi.copy()
    theta, stall = 2.0, 0
    for _ in range(lagr_iters):
        L, sub = pi @ need + c0, need.copy()
        for i in range(n):
            rc = np.where(root[i], pats[i].cost - netc[i] @ pi, np.inf)
            j = int(np.argmin(rc))
            L += rc[j]
            sub -= netc[i][j]
        if L > best_L + 1e-9:
            best_L, best_pi, stall = L, pi.copy(), 0
        else:
            stall += 1
            if stall >= 10:
                theta, stall = theta / 2, 0
        g2 = float(sub @ sub)
        if g2 < 1e-12 or theta < 1e-4:
            break
        target = ub if np.isfinite(ub) else best_L + abs(best_L) * 0.2 + 1.0
        pi = np.maximum(0.0, pi + theta * max(target - L, 1e-6) / g2 * sub)
    pi = best_pi
    rc_all = [p.cost - netc[i] @ pi for i, p in enumerate(pats)]
    const = float(pi @ need + c0)

    def bound(alive) -> float:
        return const + sum(float(rc_all[i][alive[i]].min()) for i in range(n))

    root_lb = bound(root)

    # ---------------- search
    nodes = 0
    stopped = False

    def try_complete(sel) -> None:
        nonlocal ub, best
        net_tot = sum(pats[i].net[sel[i]] for i in range(n))
        if (net_tot < need - TOL).any():
            return
        total = sum(pats[i].cost[sel[i]] for i in range(n)) + c0
        if total >= ub - 1e-9:
            return
        ev = sum(pats[i].ev[sel[i]].astype(int) for i in range(n))
        nf = sum(pats[i].nfev[sel[i]].astype(int) for i in range(n))
        if not budget_ok(ev, nf):
            return
        sched = Schedule({feeders[i].id: tuple(int(x) for x in pats[i].rows[sel[i]]) for i in range(n)})
        v = verify(problem, sched)
        if not v.ok or (strict and v.soft):
            return
        h = compute_metrics(problem, sched).total_harm
        if h < ub:
            ub, best = h, sched

    def dfs(alive) -> None:
        nonlocal nodes, stopped
        if stopped:
            return
        nodes += 1
        if (node_limit is not None and nodes > node_limit) or ((nodes & 7) == 0 and perf_counter() > deadline):
            stopped = True
            return
        if not propagate(alive):
            return
        if bound(alive) >= ub - 1e-7 * max(1.0, abs(ub)):
            return
        multi = [i for i in range(n) if np.count_nonzero(alive[i]) > 1]
        if not multi:
            try_complete([int(np.nonzero(a)[0][0]) for a in alive])
            return
        # primal dive: everyone takes their cheapest reduced-cost pattern; keep it if it happens to be valid
        try_complete([int(np.argmin(np.where(alive[i], rc_all[i], np.inf))) for i in range(n)])
        # branch on the largest regret (best vs second best reduced cost)
        bi, breg = multi[0], -1.0
        for i in multi:
            r = np.sort(rc_all[i][alive[i]])
            reg = float(r[1] - r[0])
            if reg > breg + 1e-12:
                bi, breg = i, reg
        idx = np.nonzero(alive[bi])[0]
        for j in idx[np.argsort(rc_all[bi][idx], kind="stable")]:
            child = [a.copy() for a in alive]
            child[bi] = np.zeros_like(alive[bi])
            child[bi][j] = True
            dfs(child)
            if stopped:
                return

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 4 * n + 1000))
    try:
        dfs(root)
    finally:
        sys.setrecursionlimit(old)

    if best is None:
        return BBResult(None, "UNKNOWN" if stopped else "INFEASIBLE", mode, None, root_lb if stopped else None, nodes, 0.0)
    obj = compute_metrics(problem, best).total_harm
    if stopped:
        return BBResult(best, "FEASIBLE", mode, obj, root_lb, nodes, 0.0)
    return BBResult(best, "OPTIMAL", mode, obj, obj, nodes, 0.0)
