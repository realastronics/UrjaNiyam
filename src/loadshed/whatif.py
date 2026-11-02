"""Operator what-if: the price of an override, before it is applied."""
from dataclasses import replace

from .ladder import solve_ladder
from .model import Tier

def what_if(problem, keep=(), cut=(), time_limit_s=24.0):
    """keep / cut: iterables of (feeder_id, slot). Returns (plan without, plan with, extra harm or None)."""
    a = solve_ladder(problem, time_limit_s=time_limit_s)
    b = solve_ladder(replace(problem, forced_keep=problem.forced_keep | frozenset(keep),
                             forced_cut=problem.forced_cut | frozenset(cut)), time_limit_s=time_limit_s)
    extra = None if a.objective is None or b.objective is None else b.objective - a.objective
    return a, b, extra

def explain_cuts(problem, time_limit_s=24.0):
    """For every feeder cut in the first slot: the extra harm if it were kept on instead (None = no plan without it)."""
    plan = solve_ladder(problem, time_limit_s=time_limit_s)
    now = [f for f, row in plan.schedule.cut.items() if row[0] == 1]
    return plan, {f: what_if(problem, keep=[(f, 0)], time_limit_s=time_limit_s)[2] for f in now}

def reasons(problem, plan):
    """Why each feeder is on or cut in the first slot: a rule if a rule decided it, else 'optimizer'."""
    pol, out = problem.policy, {}
    for f in problem.feeders:
        h, cut = problem.hist(f.id), plan.schedule.cut[f.id][0]
        if (f.id, 0) in problem.forced_cut: out[f.id] = "operator forced a cut"
        elif (f.id, 0) in problem.forced_keep: out[f.id] = "operator forced it to stay on"
        elif f.tier == Tier.CRITICAL and not cut and (f.id, 0) not in problem.approvals: out[f.id] = "Critical feeder, no approval to cut"
        elif h.currently_cut and h.run_slots < pol.min_cut_slots: out[f.id] = "must stay cut: minimum cut time"
        elif not h.currently_cut and h.run_slots < pol.min_restore_slots: out[f.id] = "must stay on: minimum restore time"
        elif h.switches_this_shift >= pol.feeder_switch_budget: out[f.id] = "cannot change: no switches left this shift"
        else: out[f.id] = "chosen by the optimizer"
    return out