"""Rolling-horizon replanning loop (a simulation of one shedding episode).

Every cycle:
  1. build a Problem for the next 16 slots from the CURRENT world: the history ledger (what the feeders
     actually did), the shortfall the operator currently states, estimated loads, operator overrides and approvals;
  2. solve it through the relaxation ladder (warm-started from last cycle's plan);
  3. if the ladder asks for approval of Critical cuts, ask the operator (a callback);
  4. COMMIT only the first slot, send the commands, and see what ACTUALLY happens
     (a breaker may fail to trip; real loads differ from the estimates);
  5. update the ledger from what happened, not from what was planned; advance one slot; repeat.

Why this matters: the fairness-free rules that depend on time (minimum cut/restore durations, switching
budgets, maximum downtime) only mean something if the ledger is carried from cycle to cycle.

Optional meter feedback (load_feedback): after each slot every feeder's measured load is compared with the estimate and a
per-feeder multiplier is smoothed toward the ratio, so persistent estimate errors are learned instead of insured against.

Assumptions (placeholders, see docs): a failed command still consumes one switching operation; agricultural
promised-hours windows are recomputed from the current horizon each cycle; load error is Gaussian
(a per-feeder bias plus per-slot noise); a shortfall change is known to the planner only from the slot it happens.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Callable
from itertools import groupby
from .ladder import ESCALATE, NEEDS_APPROVAL, LadderResult, solve_ladder, with_approvals
from .model import LONG_AGO, Feeder, FeederHistory, Problem, Schedule, Tier
from .policy import Policy
from .sim.grid import Grid, _promises, feeder_loads
from .sim.scenarios import Scenario


# ---------------------------------------------------------------- scripted events
@dataclass(frozen=True)
class ShortfallChange:
    slot: int
    peak_ratio: float            # the operator now states a different shortfall ratio from this slot on


@dataclass(frozen=True)
class Override:
    start: int                   # first episode slot (inclusive)
    end: int                     # last episode slot (exclusive)
    feeder_id: str
    action: str                  # "cut" or "keep"

@dataclass(frozen=True)
class ActuationFailure:
    slot: int
    feeder_id: str               # the command to this feeder at this slot fails (the breaker does not move)

def approve_all(result: LadderResult, problem: Problem, k: int):
    return frozenset(result.needs_approval)

def decline_all(result: LadderResult, problem: Problem, k: int):
    return None

@dataclass(frozen=True)
class EpisodeConfig:
    grid: Grid
    policy: Policy
    scenario: Scenario                                   # peak ratio + shape; len(shape) = episode length in slots
    start_slot: int = 68                                 # slot of day at episode slot 0 (68 = 17:00)
    shift_offset0: int = 0
    events: tuple = ()
    failure_prob: float = 0.0                            # random chance that any switching command fails
    load_error: tuple[float, float] = (0.0, 0.0)         # (sd of per-feeder bias, sd of per-slot noise), as fractions
    time_limit_s: float = 6.0                            # per replan
    solver: str = "auto"
    exact_max_feeders: int = 12
    seed: int = 0
    warm_start: bool = True
    operator: Callable = decline_all
    load_feedback: tuple[float, float] | None = None   # (smoothing alpha, meter noise sd as a fraction); None = no meter feedback
    shortfall_error: float = 0.0                       # sd of the planner's error on FUTURE shortfall slots (fraction)

@dataclass(frozen=True)
class StepLog:
    slot: int
    rung: int
    status: str
    quiet: bool                      # nothing in the world changed since the previous cycle
    plan_revision: float | None      # share of overlapping (feeder, slot) entries the new plan changed
    switches: int
    failed: int
    shortfall_kw: float
    net_relief_kw: float
    unserved_kw: float
    solve_s: float
    approvals_requested: int
    approvals_granted: int
    surprises: int = 0               # feeders whose committed action differs from what the previous cycle's plan predicted

@dataclass(frozen=True)
class EpisodeMetrics:
    total_harm: float            # harm actually suffered: cut harm + switch cost (no overshoot term)
    cut_harm: float
    switch_cost: float
    unserved_kw_slots: float     # shortfall NOT covered by real relief, summed over slots
    overshoot_kw_slots: float    # real relief beyond the shortfall
    shed_kwh: float
    n_switches: int
    failed_commands: int
    replans: int
    rung_counts: dict
    escalations: int
    approvals_requested: int
    approvals_granted: int
    quiet_cycles: int
    plan_revision_rate_quiet: float | None
    short_cycles: int            # on-off-on or off-on-off with the middle state <= 4 slots (ALSO counts legitimate rotation)
    commit_surprises: int        # committed actions the previous plan had not predicted, summed over cycles
    commit_surprise_rate_quiet: float | None   # share of feeders surprised per QUIET cycle (nothing changed in the world)
    unplanned_reversals: int     # an unpredicted switch that undoes a switch made at most 4 slots earlier
    cut_slots_mean: float
    cut_slots_max: int
    longest_outage_slots: int
    critical_cut_slots: int
    mean_solve_s: float
    max_solve_s: float


@dataclass(frozen=True)
class EpisodeResult:
    executed: dict                           # feeder id -> tuple of actual states (1 = cut) over the episode
    steps: tuple
    metrics: EpisodeMetrics
    shortfall_kw: tuple                      # the real shortfall per slot
    approved: frozenset                      # (feeder, absolute slot) Critical cuts the operator approved
    overrides: tuple = field(default=())

    def executed_schedule(self) -> Schedule:
        return Schedule({fid: tuple(r) for fid, r in self.executed.items()})


# ---------------------------------------------------------------- the loop
def run_episode(cfg: EpisodeConfig) -> EpisodeResult:
    pol, grid = cfg.policy, cfg.grid
    H, c = pol.horizon_slots, pol.commit_slots
    E = len(cfg.scenario.shape)
    specs = grid.specs
    ids = [s.id for s in specs]
    spd = 24 * 60 // pol.slot_minutes                                  # slots per day
    rng = random.Random(cfg.seed)

    long_pol = replace(pol, horizon_slots=E + H)
    est = {s.id: feeder_loads(s, long_pol, cfg.start_slot) for s in specs}          # estimated loads, E+H slots
    demand = [sum(est[i][g] for i in ids) for g in range(E + H)]
    bias = {i: (rng.gauss(0.0, cfg.load_error[0]) if cfg.load_error[0] > 0 else 0.0) for i in ids}
    corr = {i: 1.0 for i in ids}                         # meter feedback: learned multiplier on each feeder's load estimate

    changes = sorted((e for e in cfg.events if isinstance(e, ShortfallChange)), key=lambda e: e.slot)
    overrides = tuple(e for e in cfg.events if isinstance(e, Override))
    scripted_fail = {(e.slot, e.feeder_id) for e in cfg.events if isinstance(e, ActuationFailure)}
    event_slots = ({e.slot for e in changes} | {e.start for e in overrides} | {e.end for e in overrides}
                   | {s for s, _ in scripted_fail})

    def ratio_at(g: int) -> float:
        r = cfg.scenario.peak_ratio
        for e in changes:
            if e.slot <= g:
                r = e.peak_ratio
        return r

    def shortfall(g: int, ratio: float) -> float:
        shape = cfg.scenario.shape[g] if g < E else 0.0
        return round(ratio * shape * demand[g], 1)

    # ---- the ledger: what the feeders ACTUALLY did
    cut = {i: 0 for i in ids}
    run = {i: LONG_AGO for i in ids}
    sw = {i: 0 for i in ids}
    executed = {i: [] for i in ids}
    approved: set = set()
    steps: list[StepLog] = []
    shortfalls: list[float] = []
    prev_plan = None
    prev_k = None
    prev_failed = 0
    cut_harm = switch_cost = unserved = overshoot = shed = 0.0
    n_sw = failed_total = escalations = req_total = grant_total = 0
    rung_counts: dict = {}
    last_sw_slot = {i: None for i in ids}
    last_sw_dir = {i: None for i in ids}
    unplanned_rev = 0
    k = 0
    while k < E:
        # ---------- 1. the problem for this cycle
        feeders = tuple(
            Feeder(s.id, s.tier, s.ftype, s.consumers, tuple(x * corr[s.id] for x in est[s.id][k:k + H]),
                   _promises(s, pol, cfg.start_slot + k)) for s in specs)
        eb = max(-0.9, rng.gauss(0.0, cfg.shortfall_error)) if cfg.shortfall_error > 0 else 0.0
        S = tuple(round(shortfall(g, ratio_at(k)) * (1 + eb), 1) for g in range(k, k + H))
        forced_cut, forced_keep = set(), set()
        for o in overrides:
            for g in range(max(o.start, k), min(o.end, k + H)):
                (forced_cut if o.action == "cut" else forced_keep).add((o.feeder_id, g - k))
        problem = Problem(
            policy=pol, feeders=feeders, shortfall_kw=S,
            history={i: FeederHistory(bool(cut[i]), run[i], sw[i]) for i in ids},
            shift_offset=(cfg.shift_offset0 + k) % pol.shift_slots,
            forced_cut=frozenset(forced_cut), forced_keep=frozenset(forced_keep),
            approvals=frozenset((f, g - k) for f, g in approved if k <= g < k + H),
            start_slot=cfg.start_slot + k)

        warm = None
        if cfg.warm_start and prev_plan is not None:
            shift = k - prev_k
            warm = Schedule({i: tuple(prev_plan[i][shift:]) + (prev_plan[i][-1],) * shift for i in ids})

        # ---------- 2. solve through the ladder; 3. ask the operator about Critical proposals
        t0 = perf_counter()
        kw = dict(time_limit_s=cfg.time_limit_s, solver=cfg.solver, exact_max_feeders=cfg.exact_max_feeders,
                  seed=cfg.seed + k, warm_start=warm)
        res = solve_ladder(problem, **kw)
        requested = granted = 0
        if res.status == NEEDS_APPROVAL:
            requested = len(res.needs_approval)
            ok = cfg.operator(res, problem, k)
            if ok:
                granted = len(ok)
                approved |= {(f, k + t) for f, t in ok}
                problem = with_approvals(problem, ok)
                res = solve_ladder(problem, **kw)
            if res.status == NEEDS_APPROVAL or not ok:           # declined, or only part of what was needed
                res = solve_ladder(problem, allow_critical_proposals=False, **kw)
        solve_s = perf_counter() - t0
        rung_counts[res.rung] = rung_counts.get(res.rung, 0) + 1
        req_total += requested
        grant_total += granted
        if res.status == ESCALATE:
            escalations += 1

        # ---------- plan stability: how much of the overlap with last cycle's plan did we change?
        plan = {i: tuple(res.schedule.cut[i]) for i in ids} if res.schedule is not None else None
        revision = None
        if plan is not None and prev_plan is not None:
            shift = k - prev_k
            cells = changed = 0
            for i in ids:
                for t in range(H - shift):
                    cells += 1
                    changed += prev_plan[i][t + shift] != plan[i][t]
            revision = changed / cells if cells else None
        surprises = 0
        if plan is not None and prev_plan is not None and k - prev_k < H:
            off = k - prev_k
            surprises = sum(1 for i in ids if prev_plan[i][off] != plan[i][0])
        quiet = (prev_k is not None and not any(prev_k < e <= k for e in event_slots) and prev_failed == 0
                 and (cfg.shift_offset0 + k) % pol.shift_slots != 0 and requested == 0)

        # ---------- 4. commit the first `c` slots (stop early if something goes wrong)
        failed_this_cycle = 0
        done = 0
        for j in range(min(c, E - k)):
            g = k + j
            if (cfg.shift_offset0 + g) % pol.shift_slots == 0:
                sw = {i: 0 for i in ids}                           # a new shift: switching counters reset
            want = {i: (plan[i][j] if plan is not None else cut[i]) for i in ids}
            switches = failed = 0
            relief = surge = cut_h = 0.0
            was_cut = dict(cut)
            before_run = dict(run)
            for i in ids:
                if want[i] != cut[i]:
                    sw[i] += 1                                      # an attempt always costs one operation
                    switches += 1
                    if (g, i) in scripted_fail or (cfg.failure_prob > 0 and rng.random() < cfg.failure_prob):
                        failed += 1
                        run[i] += 1                                  # the breaker did not move: same state continues
                    else:
                        cut[i], run[i] = want[i], 1
                else:
                    run[i] += 1
                executed[i].append(cut[i])
            for i in ids:                                            # unplanned reversals: a surprise that undoes a recent switch
                if cut[i] != was_cut[i]:
                    predicted = (j == 0 and prev_plan is not None and k - prev_k < H and prev_plan[i][k - prev_k] == cut[i])
                    if (last_sw_slot[i] is not None and g - last_sw_slot[i] <= 4 and last_sw_dir[i] != cut[i]
                            and not predicted):
                        unplanned_rev += 1
                    last_sw_slot[i], last_sw_dir[i] = g, cut[i]
            tod = pol.time_of_day_weights[((cfg.start_slot + g) * pol.slot_minutes // 60) % 24]
            for s in specs:
                i = s.id
                true = est[i][g] * (1.0 + bias[i] + (rng.gauss(0.0, cfg.load_error[1]) if cfg.load_error[1] > 0 else 0.0))
                if cfg.load_feedback is not None and est[i][g] > 0 and not was_cut[i]:          # the meter reading corrects the next plans
                    alpha, msd = cfg.load_feedback
                    meas = true * (1.0 + (rng.gauss(0.0, msd) if msd > 0 else 0.0))
                    corr[i] = min(3.0, max(0.3, (1 - alpha) * corr[i] + alpha * meas / est[i][g]))
                if cut[i]:
                    relief += true
                    cut_h += pol.tier_weights[s.tier] * tod * s.consumers * (1 + pol.harm_beta * (run[i] - 1))
                if was_cut[i] and not cut[i]:                        # restored this slot: surge
                    surge += true * pol.surge_fraction(before_run[i])
            S_real = shortfall(g, ratio_at(g))
            net = relief - surge
            shortfalls.append(S_real)
            unserved += max(0.0, S_real - net)
            overshoot += max(0.0, net - S_real)
            shed += relief * pol.slot_minutes / 60
            cut_harm += cut_h
            switch_cost += pol.switch_mu * switches
            n_sw += switches
            failed_total += failed
            failed_this_cycle += failed
            steps.append(StepLog(g, res.rung, res.status, quiet and j == 0, revision if j == 0 else None,
                                 switches, failed, S_real, round(net, 1), round(max(0.0, S_real - net), 1),
                                 solve_s if j == 0 else 0.0, requested if j == 0 else 0, granted if j == 0 else 0,
                                 surprises if j == 0 else 0))
            done += 1
            if failed or (g + 1) in event_slots:             # something changed: replan now, not at the next cadence point
                break
        if plan is not None:
            prev_plan, prev_k, prev_failed = plan, k, failed_this_cycle
        else:
            prev_plan, prev_k, prev_failed = None, None, 0
        k += max(1, done)

    # ---------- summary
    rows = {i: tuple(r) for i, r in executed.items()}
    cut_counts = [sum(r) for r in rows.values()]
    longest = short = 0
    for row in rows.values():
        runs = [(value, sum(1 for _ in group))
            for value, group in groupby(row)]
        longest = max(longest,max((length for value, length in runs if value == 1), default=0,),)
        short += sum(length <= 4 for _, length in runs[1:-1])
    crit = {s.id for s in specs if s.tier == Tier.CRITICAL}
    quiet_steps = [s for s in steps if s.quiet and s.plan_revision is not None]
    solves = [s.solve_s for s in steps if s.solve_s > 0]
    metrics = EpisodeMetrics(
        total_harm=cut_harm + switch_cost, cut_harm=cut_harm, switch_cost=switch_cost,
        unserved_kw_slots=unserved, overshoot_kw_slots=overshoot, shed_kwh=shed, n_switches=n_sw,
        failed_commands=failed_total, replans=sum(rung_counts.values()), rung_counts=dict(sorted(rung_counts.items())),
        escalations=escalations, approvals_requested=req_total, approvals_granted=grant_total,
        quiet_cycles=len(quiet_steps),
        plan_revision_rate_quiet=(sum(s.plan_revision for s in quiet_steps) / len(quiet_steps)) if quiet_steps else None,
        short_cycles=short, commit_surprises=sum(x.surprises for x in steps),
        commit_surprise_rate_quiet=(sum(x.surprises for x in quiet_steps) / (len(quiet_steps) * len(ids))) if quiet_steps else None,
        unplanned_reversals=unplanned_rev, cut_slots_mean=sum(cut_counts) / max(1, len(cut_counts)),
        cut_slots_max=max(cut_counts, default=0), longest_outage_slots=longest,
        critical_cut_slots=sum(sum(rows[i]) for i in crit),
        mean_solve_s=sum(solves) / len(solves) if solves else 0.0, max_solve_s=max(solves, default=0.0))
    return EpisodeResult(rows, tuple(steps), metrics, tuple(shortfalls), frozenset(approved), overrides)
