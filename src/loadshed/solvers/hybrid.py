"""Hybrid solver: local search first, then branch-and-bound started from the local-search plan.

Why: branch-and-bound prunes a subtree only if its bound cannot beat the best plan already known (the
incumbent). Its default starting plan is the better greedy plan, which can be 50-150% above the optimum, so
it prunes little and, in tight (crisis) cases, may not improve on it at all within the time limit. Local search
reaches near-optimal plans quickly. Handing that plan to branch-and-bound as its incumbent gives the exact
search strong pruning from the first node, and the result is never worse than either solver alone.

Flow (per mode): build the valid patterns once -> local search for `ls_share` of the budget ->
branch-and-bound for the rest, with the local-search plan as its warm start. If the strict problem is proven
infeasible and relaxing is allowed, repeat with maximum downtime relaxed (rung 1).
Every plan returned has passed verify().
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

from ..model import Problem, Schedule
from .bnb import solve_bnb
from .minconflicts import solve_min_conflicts
from .patterns import build_patterns


@dataclass(frozen=True)
class HybridResult:
    schedule: Schedule | None
    status: str                  # OPTIMAL (proven), FEASIBLE, INFEASIBLE (proven), UNKNOWN
    mode: str                    # "strict" or "relaxed_downtime"
    objective: float | None
    lower_bound: float | None    # proven lower bound on the optimum (from branch-and-bound)
    nodes: int
    ls_objective: float | None   # what local search alone reached (the starting plan handed to branch-and-bound)
    runtime_s: float
    setup_s: float


def solve_hybrid(problem: Problem, time_limit_s: float = 30.0, ls_share: float = 0.3, seed: int = 0,
                 warm_start: Schedule | None = None, relax_downtime_if_infeasible: bool = True,
                 force_relaxed: bool = False, node_limit: int | None = None) -> HybridResult:
    """time_limit_s is the SEARCH budget per mode (local search + branch-and-bound); pattern setup is separate."""
    t0 = perf_counter()
    r = _phase(problem, not force_relaxed, time_limit_s, ls_share, seed, warm_start, node_limit,)
    if (r.status == "INFEASIBLE" and relax_downtime_if_infeasible and not force_relaxed):
        r2 = _phase(problem, False, time_limit_s, ls_share, seed, warm_start, node_limit,)
        r = replace(r2, nodes=r.nodes + r2.nodes, setup_s=r.setup_s + r2.setup_s,)
    return replace(r, runtime_s=perf_counter() - t0)


def _phase(problem: Problem, strict: bool, budget: float, ls_share: float, seed: int,
           warm_start: Schedule | None, node_limit: int | None) -> HybridResult:
    mode = "strict" if strict else "relaxed_downtime"
    t = perf_counter()
    pats = build_patterns(problem, strict)
    setup = perf_counter() - t
    if any(len(p) == 0 for p in pats):
        return HybridResult(None, "INFEASIBLE", mode, None, None, 0, None, 0.0, setup)

    # 1. local search: at most ls_share of the budget, and it stops early once it has stopped improving
    #    (its strict phase uses 70% of the limit it is given, so scale to get ls_share of the budget)
    ls_budget = budget * ls_share
    ls = solve_min_conflicts(problem, time_limit_s=ls_budget / 0.7 if strict else ls_budget, seed=seed,
                             relax_downtime_if_infeasible=False, force_relaxed=not strict,
                             warm_start=warm_start, patterns=pats,
                             patience=400 + 20 * len(problem.feeders))      # converged -> hand over early
    if ls.status == "INFEASIBLE":                     # proven by the necessary check
        return HybridResult(None, "INFEASIBLE", mode, None, None, 0, None, 0.0, setup)

    # 2. branch-and-bound, starting from the local-search plan (or the caller's warm start if that is better)
    start = ls.schedule if ls.schedule is not None else warm_start
    bb = solve_bnb(problem, time_limit_s=budget - ls_budget, node_limit=node_limit,
                   relax_downtime_if_infeasible=False, force_relaxed=not strict, warm_start=start, patterns=pats)
    if bb.schedule is None and ls.schedule is not None:      # cannot happen while the start plan verifies; kept safe
        return HybridResult(ls.schedule, "FEASIBLE", mode, ls.objective, bb.lower_bound, bb.nodes, ls.objective, 0.0, setup)
    return HybridResult(bb.schedule, bb.status, bb.mode, bb.objective, bb.lower_bound, bb.nodes, ls.objective, 0.0, setup)
