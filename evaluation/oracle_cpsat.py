"""CP-SAT ORACLE. Offline benchmarking only. Never imported from src/loadshed.

Its job: give the true optimum so we can say "solver X is within Y% of optimal",
and tell us which instances are genuinely infeasible.

It encodes EXACTLY the rules of verify() and the objective of metrics.compute_metrics():
  * relief >= S*(1+margin) + restoration surge in every slot
  * min cut / min restore durations (history included), waived at operator-forced slots
  * per-feeder and system switch budgets with shift resets (history included)
  * operator overrides fixed, Critical cuts only if approved or forced
  * promised supply windows
  * max downtime is HARD in "strict" mode; if that is infeasible we retry with it relaxed
    (this is rung 1 of the relaxation ladder) and report mode="relaxed_downtime"
Numbers are scaled to integers; loads are rounded DOWN and requirements UP, so every
oracle answer is also accepted by the verifier. tests/test_oracle.py checks it against
brute force over every schedule of small random problems.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from time import perf_counter

from ortools.sat.python import cp_model

from loadshed.metrics import compute_metrics
from loadshed.model import Problem, Schedule, Tier

OBJ_SCALE = 10_000      # harm units -> integers
KW_SCALE = 100          # kW -> integers (0.01 kW)
MAX_HISTORY_RUN = 10_000


@dataclass(frozen=True)
class OracleResult:
    schedule: Schedule | None
    status: str                 # OPTIMAL, FEASIBLE (not proven), INFEASIBLE, UNKNOWN
    mode: str                   # "strict" or "relaxed_downtime"
    objective: float | None     # total harm of the returned schedule (recomputed by metrics.py)
    best_bound: float | None    # solver's proven lower bound on harm
    gap: float | None           # (objective - bound) / objective
    runtime_s: float


def solve_oracle(problem: Problem, time_limit_s: float = 30.0, workers: int = 8,
                 hint: Schedule | None = None, relax_downtime_if_infeasible: bool = True) -> OracleResult:
    t0 = perf_counter()
    res = _solve_once(problem, True, time_limit_s, workers, hint)
    if res.status == "INFEASIBLE" and relax_downtime_if_infeasible:
        res = _solve_once(problem, False, time_limit_s, workers, hint)
    return OracleResult(res.schedule, res.status, res.mode, res.objective, res.best_bound,
                        res.gap, perf_counter() - t0)


def _solve_once(problem: Problem, strict: bool, time_limit_s: float, workers: int,
                hint: Schedule | None) -> OracleResult:
    pol, H = problem.policy, problem.policy.horizon_slots
    m = cp_model.CpModel()
    forced = problem.forced_cut | problem.forced_keep
    surge_on = pol.surge_base > 0 or pol.surge_per_slot > 0

    x: dict[tuple[str, int], cp_model.IntVar] = {}
    run: dict[tuple[str, int], cp_model.IntVar] = {}
    cs: dict[tuple[str, int], cp_model.IntVar] = {}
    rs: dict[tuple[str, int], cp_model.IntVar] = {}
    sw: dict[tuple[str, int], cp_model.IntVar] = {}
    sg: dict[tuple[str, int], cp_model.IntVar | int] = {}
    obj_terms = []

    for f in problem.feeders:
        h = problem.hist(f.id)
        cut0 = 1 if h.currently_cut else 0
        run_hist = h.run_slots if cut0 else 0
        on_hist = 0 if cut0 else h.run_slots
        if run_hist > MAX_HISTORY_RUN:
            raise ValueError(f"{f.id}: history cut run too long for the oracle")
        w = pol.tier_weights[f.tier]

        for t in range(H):
            k = (f.id, t)
            xt = m.new_bool_var(f"x_{f.id}_{t}")
            x[k] = xt
            prev_x = cut0 if t == 0 else x[(f.id, t - 1)]
            prev_run = run_hist if t == 0 else run[(f.id, t - 1)]

            # run length of the current cut run (counts history). 0 when not cut.
            rt = m.new_int_var(0, run_hist + t + 1, f"r_{f.id}_{t}")
            run[k] = rt
            m.add(rt == prev_run + 1).only_enforce_if(xt)
            m.add(rt == 0).only_enforce_if(xt.negated())

            # start / restore indicators
            c, r_ = m.new_bool_var(f"cs_{f.id}_{t}"), m.new_bool_var(f"rs_{f.id}_{t}")
            cs[k], rs[k] = c, r_
            if isinstance(prev_x, int):
                if prev_x == 0:
                    m.add(c == xt); m.add(r_ == 0)
                else:
                    m.add(c == 0); m.add(r_ + xt == 1)
            else:
                m.add_bool_and([xt, prev_x.negated()]).only_enforce_if(c)
                m.add_bool_or([xt.negated(), prev_x]).only_enforce_if(c.negated())
                m.add_bool_and([prev_x, xt.negated()]).only_enforce_if(r_)
                m.add_bool_or([prev_x.negated(), xt]).only_enforce_if(r_.negated())
            s = m.new_bool_var(f"sw_{f.id}_{t}")
            sw[k] = s
            m.add(s == c + r_)

            # operator overrides and critical approval
            if k in problem.forced_cut:
                m.add(xt == 1)
            elif k in problem.forced_keep:
                m.add(xt == 0)
            elif f.tier == Tier.CRITICAL and k not in problem.approvals:
                m.add(xt == 0)

            if k not in forced:
                # minimum cut: a restore needs the cut run so far to be long enough
                if isinstance(prev_run, int):
                    if prev_run < pol.min_cut_slots:
                        m.add(r_ == 0)
                else:
                    m.add(prev_run >= pol.min_cut_slots).only_enforce_if(r_)
                # minimum restore: a new cut at slot t needs the ON run just before t to be long enough.
                # Slots t-min_restore..t-1 inside the plan must be ON; any part before slot 0 must come from history.
                need_hist = pol.min_restore_slots - t
                if need_hist > 0 and on_hist < need_hist:
                    m.add(c == 0)
                else:
                    for u in range(max(0, t - pol.min_restore_slots), t):
                        m.add(x[(f.id, u)] == 0).only_enforce_if(c)
            if strict:
                m.add(rt <= pol.max_downtime_slots)

            # harm: w*tod*n*(1 + beta*(r-1)) when cut = w*tod*n*((1-beta) + beta*r), and r=0 when not cut
            base = w * pol.time_of_day_weights[problem.hour_of(t)] * f.consumers
            a = round(OBJ_SCALE * base * (1 - pol.harm_beta))
            b = round(OBJ_SCALE * base * pol.harm_beta)
            obj_terms.append(a * xt)
            obj_terms.append(b * rt)
            obj_terms.append(round(OBJ_SCALE * pol.switch_mu) * s)

            # restoration surge in this slot
            load_i = math.ceil(f.load_kw[t] * KW_SCALE - 1e-9)
            if surge_on and load_i > 0:
                if isinstance(prev_run, int):
                    q_val = math.ceil(f.load_kw[t] * pol.surge_fraction(prev_run) * KW_SCALE - 1e-9)
                    sg[k] = q_val * r_
                else:
                    ub_prev = run_hist + (t - 1) + 1
                    table = [math.ceil(f.load_kw[t] * pol.surge_fraction(v) * KW_SCALE - 1e-9)
                             for v in range(ub_prev + 1)]
                    q = m.new_int_var(0, max(table), f"q_{f.id}_{t}")
                    m.add_element(prev_run, table, q)
                    g = m.new_int_var(0, max(table), f"sg_{f.id}_{t}")
                    m.add(g == q).only_enforce_if(r_)
                    m.add(g == 0).only_enforce_if(r_.negated())
                    sg[k] = g
            else:
                sg[k] = 0

        # promised supply windows
        for win in f.promises:
            m.add(sum(1 - x[(f.id, t)] for t in range(win.start, win.end)) >= win.min_on_slots)

    # per-feeder switching budget (exact, incl. waiver at forced slots) with shift resets
    start_idx = {f.id: 0 for f in problem.feeders}
    base_cnt = {f.id: problem.hist(f.id).switches_this_shift for f in problem.feeders}
    sys_start, sys_base = 0, sum(base_cnt.values())
    slot_sw = []
    for t in range(H):
        if (problem.shift_offset + t) % pol.shift_slots == 0:
            start_idx = {fid: t for fid in start_idx}
            base_cnt = {fid: 0 for fid in base_cnt}
            sys_start, sys_base = t, 0
        for f in problem.feeders:
            k = (f.id, t)
            if k in forced:
                continue
            cum = base_cnt[f.id] + sum(sw[(f.id, u)] for u in range(start_idx[f.id], t + 1))
            m.add(cum <= pol.feeder_switch_budget).only_enforce_if(sw[k])
        # system budget, exact: events in slot t are counted in feeder order, like the verifier does
        # Ordinary switches must fit; forced switches count in later slots.
        before = sys_base + sum(slot_sw[u] for u in range(sys_start, t))
        ordinary = [
            sw[(f.id, t)] for f in problem.feeders if (f.id, t) not in forced
        ]
        for event in ordinary:
            m.add(
                before + sum(ordinary) <= pol.system_switch_budget
            ).only_enforce_if(event)
        slot_sw.append(sum(sw[(f.id, t)] for f in problem.feeders))

    # relief in every slot, with overshoot slack
    lam = round(OBJ_SCALE * pol.overshoot_lambda / KW_SCALE)
    for t in range(H):
        need = math.ceil(problem.shortfall_kw[t] * (1 + pol.safety_margin) * KW_SCALE - 1e-9)
        relief = sum(math.floor(f.load_kw[t] * KW_SCALE + 1e-9) * x[(f.id, t)] for f in problem.feeders)
        surge = sum(sg[(f.id, t)] for f in problem.feeders)
        ov = m.new_int_var(0, 10**9, f"ov_{t}")
        m.add(relief - surge - need == ov)
        obj_terms.append(lam * ov)

    m.minimize(sum(obj_terms))
    if hint is not None:
        for f in problem.feeders:
            for t in range(H):
                m.add_hint(x[(f.id, t)], int(hint.cut[f.id][t]))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 1
    status = solver.solve(m)
    name = solver.status_name(status)
    mode = "strict" if strict else "relaxed_downtime"
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return OracleResult(None, "INFEASIBLE" if name == "INFEASIBLE" else "UNKNOWN", mode,
                            None, None, None, 0.0)
    sched = Schedule({f.id: tuple(int(solver.value(x[(f.id, t)])) for t in range(H))
                      for f in problem.feeders})
    obj = compute_metrics(problem, sched).total_harm
    bound = solver.best_objective_bound / OBJ_SCALE
    gap = max(0.0, (obj - bound) / obj) if obj > 0 else 0.0
    return OracleResult(sched, name, mode, obj, bound, gap, 0.0)
