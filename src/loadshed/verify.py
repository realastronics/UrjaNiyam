"""Independent verifier.

Rules of this file:
  * It imports only the data types and the policy. Never a solver.
  * Plain loops, no clever tricks, so a human can audit it line by line.
  * It rebuilds everything from the Problem (including each feeder's history)
    and the Schedule. It trusts nothing the solver says about its own output.

Max downtime is soft and counts only runs the PLAN extends (a run already over the limit in history
and restored at once is not the plan's fault).

Result has three lists:
  hard   - the plan breaks a hard rule. Do not show it to the operator as valid.
  soft   - the plan breaks a soft rule (max downtime). Reported with a magnitude.
  waived - a rule was broken at a slot the OPERATOR forced. The operator's order
           wins, so this does not block the plan, but it is logged.
Relief (the shortfall rule) can never be waived: physics does not take orders.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import FeederHistory, Problem, Schedule, Tier

TOL = 1e-6

# rule names
MALFORMED = "MALFORMED"
RELIEF = "RELIEF"
MIN_CUT = "MIN_CUT"
MIN_RESTORE = "MIN_RESTORE"
FEEDER_SWITCH_BUDGET = "FEEDER_SWITCH_BUDGET"
SYSTEM_SWITCH_BUDGET = "SYSTEM_SWITCH_BUDGET"
FORCED_CUT = "FORCED_CUT"
FORCED_KEEP = "FORCED_KEEP"
PROMISED_SUPPLY = "PROMISED_SUPPLY"
CRITICAL_UNAPPROVED = "CRITICAL_UNAPPROVED"
MAX_DOWNTIME = "MAX_DOWNTIME"   # soft


@dataclass(frozen=True)
class Violation:
    rule: str
    feeder: str | None
    slot: int | None
    magnitude: float
    detail: str


@dataclass
class VerifyResult:
    hard: list[Violation] = field(default_factory=list)
    soft: list[Violation] = field(default_factory=list)
    waived: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard

    def rules(self, kind: str = "hard") -> set[str]:
        return {v.rule for v in getattr(self, kind)}


def verify(problem: Problem, schedule: Schedule) -> VerifyResult:
    res = VerifyResult()
    if not _check_shape(problem, schedule, res):
        return res

    H = problem.policy.horizon_slots
    switch_events: list[list[str]] = [[] for _ in range(H)]    # feeders that switch in slot t
    restores: list[list[tuple[str, int]]] = [[] for _ in range(H)]  # (feeder, outage length) restored in slot t

    for f in problem.feeders:
        _check_feeder_timeline(problem, f.id, schedule.cut[f.id], res, switch_events, restores)

    _check_system_switches(problem, res, switch_events)
    _check_relief(problem, schedule, res, restores)
    _check_overrides_and_critical(problem, schedule, res)
    _check_promises(problem, schedule, res)
    return res


# ---------------------------------------------------------------- helpers
def _forced(problem: Problem, fid: str, t: int) -> bool:
    return (fid, t) in problem.forced_cut or (fid, t) in problem.forced_keep


def _flag(problem: Problem, res: VerifyResult, rule: str, fid: str, t: int,
          magnitude: float, detail: str) -> None:
    """Record a violation; if the operator forced this feeder-slot, it is waived."""
    v = Violation(rule, fid, t, magnitude, detail)
    (res.waived if _forced(problem, fid, t) else res.hard).append(v)


def _check_shape(problem: Problem, schedule: Schedule, res: VerifyResult) -> bool:
    H = problem.policy.horizon_slots
    want = {f.id for f in problem.feeders}
    have = set(schedule.cut)
    for fid in sorted(want - have):
        res.hard.append(Violation(MALFORMED, fid, None, 1, "feeder missing from schedule"))
    for fid in sorted(have - want):
        res.hard.append(Violation(MALFORMED, fid, None, 1, "unknown feeder in schedule"))
    for fid in sorted(want & have):
        row = schedule.cut[fid]
        if len(row) != H:
            res.hard.append(Violation(MALFORMED, fid, None, 1, f"needs {H} slots, got {len(row)}"))
        elif any(x not in (0, 1) for x in row):
            res.hard.append(Violation(MALFORMED, fid, None, 1, "values must be 0 or 1"))
    return not res.hard


# ---------------------------------------------------------------- per-feeder timeline
def _check_feeder_timeline(problem: Problem, fid: str, row: tuple[int, ...], res: VerifyResult,
                           switch_events: list[list[str]],
                           restores: list[list[tuple[str, int]]]) -> None:
    """Walk one feeder through time, starting from its history.

    Checks: min cut, min restore, per-feeder switch budget, max downtime (soft).
    Also records switch events and restores for the system-wide checks.
    """
    pol = problem.policy
    h: FeederHistory = problem.hist(fid)
    prev = 1 if h.currently_cut else 0
    run = h.run_slots              # slots spent in `prev` so far (includes history)
    switches = h.switches_this_shift
    plan_cut_in_run = False        # did the plan itself cut this feeder in the current run?

    for t, cur in enumerate(row):
        if (problem.shift_offset + t) % pol.shift_slots == 0:
            switches = 0           # a new shift starts at this slot

        if cur != prev:            # state change between slot t-1 and slot t
            if prev == 1:          # a cut run ends here (feeder restored)
                if run < pol.min_cut_slots:
                    _flag(problem, res, MIN_CUT, fid, t, pol.min_cut_slots - run,
                          f"restored after {run} slots, minimum cut is {pol.min_cut_slots}")
                if plan_cut_in_run:
                    _soft_downtime(res, pol.max_downtime_slots, fid, t, run)
                restores[t].append((fid, run))
            else:                  # an ON run ends here (feeder cut again)
                if run < pol.min_restore_slots:
                    _flag(problem, res, MIN_RESTORE, fid, t, pol.min_restore_slots - run,
                          f"cut again after {run} slots on, minimum restore is {pol.min_restore_slots}")
            switches += 1
            switch_events[t].append(fid)
            if switches > pol.feeder_switch_budget:
                _flag(problem, res, FEEDER_SWITCH_BUDGET, fid, t, switches - pol.feeder_switch_budget,
                      f"{switches} operations this shift, budget {pol.feeder_switch_budget}")
            prev, run = cur, 1
            plan_cut_in_run = False
        else:
            run += 1
        if cur == 1:
            plan_cut_in_run = True

    if prev == 1 and plan_cut_in_run:   # cut run still going at the end of the horizon
        _soft_downtime(res, pol.max_downtime_slots, fid, len(row) - 1, run)


def _soft_downtime(res: VerifyResult, limit: int, fid: str, t: int, run: int) -> None:
    if run > limit:
        res.soft.append(Violation(MAX_DOWNTIME, fid, t, run - limit,
                                  f"off {run} slots in a row, limit {limit}"))


# ---------------------------------------------------------------- system-wide checks
def _check_system_switches(problem: Problem, res: VerifyResult,
                           switch_events: list[list[str]]) -> None:
    pol = problem.policy
    total = sum(problem.hist(f.id).switches_this_shift for f in problem.feeders)
    for t, fids in enumerate(switch_events):
        if (problem.shift_offset + t) % pol.shift_slots == 0:
            total = 0
        for fid in fids:
            total += 1
            if total > pol.system_switch_budget:
                _flag(problem, res, SYSTEM_SWITCH_BUDGET, fid, t, total - pol.system_switch_budget,
                      f"{total} operations system-wide this shift, budget {pol.system_switch_budget}")


def _check_relief(problem: Problem, schedule: Schedule, res: VerifyResult,
                  restores: list[list[tuple[str, int]]]) -> None:
    """Relief in each slot must cover S(t) with margin, plus the surge from feeders restored in that slot."""
    pol = problem.policy
    load = {f.id: f.load_kw for f in problem.feeders}
    for t in range(pol.horizon_slots):
        relief = sum(load[f.id][t] for f in problem.feeders if schedule.cut[f.id][t] == 1)
        surge = sum(load[fid][t] * pol.surge_fraction(run) for fid, run in restores[t])
        need = problem.shortfall_kw[t] * (1 + pol.safety_margin) + surge
        if relief < need - TOL:
            res.hard.append(Violation(RELIEF, None, t, need - relief,
                                      f"relief {relief:.1f} kW, need {need:.1f} kW (incl. surge {surge:.1f})"))


def _check_overrides_and_critical(problem: Problem, schedule: Schedule, res: VerifyResult) -> None:
    for fid, t in sorted(problem.forced_cut):
        if schedule.cut[fid][t] != 1:
            res.hard.append(Violation(FORCED_CUT, fid, t, 1, "operator forced a cut, schedule keeps it on"))
    for fid, t in sorted(problem.forced_keep):
        if schedule.cut[fid][t] != 0:
            res.hard.append(Violation(FORCED_KEEP, fid, t, 1, "operator forced keep-on, schedule cuts it"))
    for f in problem.feeders:
        if f.tier != Tier.CRITICAL:
            continue
        for t, x in enumerate(schedule.cut[f.id]):
            # an operator-forced cut counts as approval: the human already decided
            if x == 1 and (f.id, t) not in problem.approvals and (f.id, t) not in problem.forced_cut:
                res.hard.append(Violation(CRITICAL_UNAPPROVED, f.id, t, 1,
                                          "critical feeder cut without operator approval"))


def _check_promises(problem: Problem, schedule: Schedule, res: VerifyResult) -> None:
    for f in problem.feeders:
        row = schedule.cut[f.id]
        for w in f.promises:
            on = sum(1 for t in range(w.start, w.end) if row[t] == 0)
            if on < w.min_on_slots:
                res.hard.append(Violation(PROMISED_SUPPLY, f.id, w.start, w.min_on_slots - on,
                                          f"on {on} slots in [{w.start},{w.end}), promised {w.min_on_slots}"))
