"""The relaxation ladder. The rungs are cumulative and always tried in this order:

  rung 0  all rules hold                                         -> PLAN           (executable)
  rung 1  relax MAXIMUM DOWNTIME (soft rule), report by how much -> PLAN           (executable)
  rung 2  cut CRITICAL feeders, but only as a PROPOSAL           -> NEEDS_APPROVAL (not executable)
          listing exactly which (feeder, slot) pairs the operator must approve
  rung 3  ESCALATE: the shortfall cannot be covered within the rules. Nothing is cut automatically
          beyond what is allowed; we report the best coverage reachable and the uncovered kW per slot
          -> ESCALATE

The tool NEVER cuts a Critical feeder without approval, and it never hides a relaxation: every rung used
is listed with a magnitude. Each "no plan at this rung" is labelled PROVEN (an exact search or a necessary
check proved it) or NOT FOUND (a heuristic ran out of time), so the operator knows how far to trust it.

Operator flow:  result = solve_ladder(problem)
                if result.status == "NEEDS_APPROVAL":  show result.needs_approval; the operator decides
                    approved -> problem2 = with_approvals(problem, pairs); result2 = solve_ladder(problem2)
                    declined -> solve_ladder(problem, allow_critical_proposals=False)   (goes to ESCALATE)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

from .metrics import compute_metrics, required_relief
from .model import Problem, Schedule, Tier
from .solvers.bnb import solve_bnb
from .solvers.hybrid import solve_hybrid
from .solvers.minconflicts import solve_min_conflicts
from .solvers.patterns import enumerate_patterns
from .verify import verify

PLAN, NEEDS_APPROVAL, ESCALATE = "PLAN", "NEEDS_APPROVAL", "ESCALATE"
PROVEN, NOT_FOUND, FOUND = "proven impossible", "not found in time", "plan found"


@dataclass(frozen=True)
class Relaxation:
    rung: int
    rule: str            # MAX_DOWNTIME or CRITICAL_CUT
    magnitude: float     # slots over the limit / kW-slots of Critical load proposed to cut
    detail: str


@dataclass(frozen=True)
class Attempt:
    rung: int
    label: str
    solver: str
    outcome: str         # FOUND, PROVEN, NOT_FOUND
    seconds: float


@dataclass(frozen=True)
class LadderResult:
    rung: int
    status: str                                        # PLAN, NEEDS_APPROVAL, ESCALATE
    schedule: Schedule | None
    objective: float | None
    relaxations: tuple[Relaxation, ...]
    needs_approval: tuple[tuple[str, int], ...]        # (feeder, slot) pairs the operator must approve
    coverage_fraction: float | None                    # rung 3 only: share of the shortfall we can cover
    uncovered_kw: tuple[float, ...] | None             # rung 3 only: required relief we cannot provide, per slot
    attempts: tuple[Attempt, ...]
    messages: tuple[str, ...]

    @property
    def executable(self) -> bool:
        return self.status == PLAN

    @property
    def proven(self) -> bool:
        """True if every 'no plan at a lower rung' claim was proven, not just a heuristic giving up."""
        return all(a.outcome != NOT_FOUND for a in self.attempts)


def with_approvals(problem: Problem, pairs) -> Problem:
    """A copy of the problem in which the operator has approved these Critical (feeder, slot) cuts."""
    return replace(problem, approvals=problem.approvals | frozenset(pairs))


# ---------------------------------------------------------------- one attempt
def _attempt(problem: Problem, relaxed: bool, seconds: float, solver: str, exact_max: int, seed: int,
             warm_start: Schedule | None = None):
    """Returns (schedule | None, objective | None, outcome, solver_name, elapsed).

    solver="auto": the hybrid (local search, then exact search started from its plan) up to `exact_max` feeders,
    local search above that. solver="hybrid" / "bnb" / "ls" force one choice."""
    t0 = perf_counter()
    n = len(problem.feeders)
    if solver == "hybrid" or (solver == "auto" and n <= exact_max):
        r = solve_hybrid(problem, time_limit_s=seconds, seed=seed, warm_start=warm_start,
                         relax_downtime_if_infeasible=False, force_relaxed=relaxed)
        if r.schedule is not None:
            return r.schedule, r.objective, FOUND, "hybrid", perf_counter() - t0
        return (None, None, PROVEN if r.status == "INFEASIBLE" else NOT_FOUND, "hybrid", perf_counter() - t0)
    if solver == "bnb":
        r = solve_bnb(problem, time_limit_s=seconds, relax_downtime_if_infeasible=False, force_relaxed=relaxed,
                      warm_start=warm_start)
        if r.schedule is not None:
            return r.schedule, r.objective, FOUND, "bnb", perf_counter() - t0
        if r.status == "INFEASIBLE":
            return None, None, PROVEN, "bnb", perf_counter() - t0
    r2 = solve_min_conflicts(problem, time_limit_s=seconds, seed=seed, relax_downtime_if_infeasible=False,
                             force_relaxed=relaxed, warm_start=warm_start)
    if r2.schedule is not None:
        return r2.schedule, r2.objective, FOUND, "min_conflicts", perf_counter() - t0
    return (None, None, PROVEN if r2.status == "INFEASIBLE" else NOT_FOUND, "min_conflicts",
            perf_counter() - t0)


# ---------------------------------------------------------------- the ladder
def solve_ladder(problem: Problem, *, allow_critical_proposals: bool = True, time_limit_s: float = 60.0,
                 solver: str = "auto", exact_max_feeders: int = 12, seed: int = 0,
                 warm_start: Schedule | None = None) -> LadderResult:
    per = max(0.5, time_limit_s / 12)
    attempts: list[Attempt] = []

    def run(prob: Problem, relaxed: bool, rung: int, label: str):
        sched, obj, outcome, name, el = _attempt(prob, relaxed, per, solver, exact_max_feeders, seed, warm_start)
        attempts.append(Attempt(rung, label, name, outcome, round(el, 3)))
        return sched, obj

    # ---- rung 0
    sched, obj = run(problem, False, 0, "all rules")
    if sched is not None:
        return _finish(PLAN, 0, problem, sched, obj, attempts, (), ())

    # ---- rung 1
    sched, obj = run(problem, True, 1, "max downtime relaxed")
    if sched is not None:
        return _finish(PLAN, 1, problem, sched, obj, attempts, _downtime_relaxations(problem, sched), ())

    # ---- rung 2: a PROPOSAL that needs the operator's approval
    has_critical = any(f.tier == Tier.CRITICAL for f in problem.feeders)
    if allow_critical_proposals and has_critical:
        H = problem.policy.horizon_slots
        every = frozenset((f.id, t) for f in problem.feeders if f.tier == Tier.CRITICAL for t in range(H))
        p2 = replace(problem, approvals=problem.approvals | every)
        sched, obj = run(p2, True, 2, "critical cuts proposed + max downtime relaxed")
        if sched is not None:
            pending = tuple(sorted((f.id, t) for f in problem.feeders if f.tier == Tier.CRITICAL
                                   for t in range(H) if sched.cut[f.id][t] == 1
                                   and (f.id, t) not in problem.approvals and (f.id, t) not in problem.forced_cut))
            rel = _downtime_relaxations(p2, sched) + _critical_relaxations(problem, pending)
            return _finish(NEEDS_APPROVAL, 2, p2, sched, obj, attempts, rel, pending)

    # ---- rung 3: ESCALATE with the best coverage reachable by allowed actions only
    return _escalate(problem, attempts, per, solver, exact_max_feeders, seed, allow_critical_proposals and has_critical)


# ---------------------------------------------------------------- rung 3
def _escalate(problem: Problem, attempts: list[Attempt], per: float, solver: str, exact_max: int, seed: int,
              critical_was_tried: bool) -> LadderResult:
    pol, H = problem.policy, problem.policy.horizon_slots
    need = [s * (1 + pol.safety_margin) for s in problem.shortfall_kw]

    # optimistic coverage from the per-slot best case of every feeder (an upper bound on what is possible)
    sums = [0.0] * H
    for f in problem.feeders:
        pat = enumerate_patterns(problem, f, strict=False)
        if len(pat) == 0:
            sums = [0.0] * H
            break
        mx = pat.net.max(axis=0)
        sums = [sums[t] + float(mx[t]) for t in range(H)]
    ratios = [sums[t] / need[t] for t in range(H) if need[t] > 1e-9]
    a_hi = max(0.0, min([1.0] + ratios))

    def scaled(a: float) -> Problem:
        return replace(problem, shortfall_kw=tuple(a * s for s in problem.shortfall_kw))

    def try_cover(a: float):
        sched, _, outcome, name, el = _attempt(scaled(a), True, per, solver, exact_max, seed)
        attempts.append(Attempt(3, f"best effort: cover {a:.0%} of the shortfall", name, outcome, round(el, 3)))
        return sched

    best_a, best_sched = 0.0, None
    s_hi = try_cover(a_hi) if a_hi > 0 else None
    if s_hi is not None:
        best_a, best_sched = a_hi, s_hi
    elif a_hi > 0:
        lo, hi = 0.0, a_hi
        for _ in range(7):
            mid = (lo + hi) / 2
            s = try_cover(mid)
            if s is not None:
                lo, best_a, best_sched = mid, mid, s
            else:
                hi = mid
    if best_sched is None:
        best_sched = _zero_plan(problem)
        if best_sched is not None:
            best_a = 0.0

    uncovered = None
    if best_sched is not None:
        req = required_relief(problem, best_sched)
        relief = [sum(f.load_kw[t] for f in problem.feeders if best_sched.cut[f.id][t] == 1) for t in range(H)]
        uncovered = tuple(round(max(0.0, req[t] - relief[t]), 1) for t in range(H))
    msgs = _escalate_messages(problem, best_a, uncovered, critical_was_tried)
    return LadderResult(3, ESCALATE, best_sched, None, (), (), best_a if best_sched is not None else None,
                        uncovered, tuple(attempts), tuple(msgs))


def _zero_plan(problem: Problem) -> Schedule | None:
    """A plan for 'no shortfall': only what the rules and the operator force. None if even that is impossible."""
    r = _attempt(replace(problem, shortfall_kw=(0.0,) * problem.policy.horizon_slots), True, 1.0, "auto", 12, 0)
    return r[0]


# ---------------------------------------------------------------- reporting
def _downtime_relaxations(problem: Problem, sched: Schedule) -> tuple[Relaxation, ...]:
    soft = verify(problem, sched).soft
    if not soft:
        return ()
    total = sum(v.magnitude for v in soft)
    detail = "; ".join(f"{v.feeder} off {int(v.magnitude) + problem.policy.max_downtime_slots} slots in a row "
                       f"(limit {problem.policy.max_downtime_slots}, +{int(v.magnitude)})" for v in soft)
    return (Relaxation(1, "MAX_DOWNTIME", float(total), detail),)


def _critical_relaxations(problem: Problem, pending) -> tuple[Relaxation, ...]:
    if not pending:
        return ()
    load = {f.id: f.load_kw for f in problem.feeders}
    kw = sum(load[fid][t] for fid, t in pending)
    feeders = sorted({fid for fid, _ in pending})
    return (Relaxation(2, "CRITICAL_CUT", float(kw),
                       f"approval needed to cut Critical feeder(s) {', '.join(feeders)} "
                       f"in {len(pending)} feeder-slot(s)"),)


def _finish(status: str, rung: int, problem: Problem, sched: Schedule, obj: float | None,
            attempts: list[Attempt], relaxations, pending) -> LadderResult:
    v = verify(problem, sched)
    assert v.ok, "the ladder must never return a plan the verifier rejects"
    msgs = []
    if rung == 0:
        msgs.append("RUNG 0: all rules hold. The plan is ready for the operator.")
    if rung >= 1:
        msgs.append("RUNG 1: no plan satisfies every rule, so MAXIMUM DOWNTIME was relaxed.")
        for r in relaxations:
            if r.rule == "MAX_DOWNTIME":
                msgs.append(f"   {r.detail}")
    if rung == 2:
        msgs.append("RUNG 2: the shortfall cannot be covered without Critical feeders. THIS PLAN IS A PROPOSAL AND "
                    "IS NOT EXECUTABLE until the operator approves each Critical cut listed below.")
        for fid, t in pending:
            msgs.append(f"   approve: cut {fid} in slot {t}")
        msgs.append("   If the operator declines, re-run with allow_critical_proposals=False (goes to ESCALATE).")
    if obj is None:
        obj = compute_metrics(problem, sched).total_harm
    return LadderResult(rung, status, sched, float(obj), tuple(relaxations), tuple(pending), None, None,
                        tuple(attempts), tuple(msgs))


def _escalate_messages(problem: Problem, coverage: float, uncovered, critical_was_tried: bool) -> list[str]:
    msgs = ["ESCALATE: the shortfall cannot be covered within the rules and the actions currently allowed."]
    if critical_was_tried:
        msgs.append("   Even proposing every Critical feeder for approval did not give a feasible plan.")
    else:
        msgs.append("   Critical feeders were not offered for cutting.")
    if uncovered is None:
        msgs.append("   The rules conflict even with no shortfall. A human must decide.")
        return msgs
    msgs.append(f"   Best effort covers about {coverage:.0%} of the shortfall without cutting any Critical feeder.")
    worst = max(range(len(uncovered)), key=lambda t: uncovered[t])
    msgs.append(f"   Largest uncovered relief: {uncovered[worst]:.0f} kW in slot {worst}. "
                f"Total uncovered: {sum(uncovered):.0f} kW-slots.")
    msgs.append("   Nothing beyond the best-effort partial plan is done automatically. The human decides what to do about the rest.")
    return msgs
