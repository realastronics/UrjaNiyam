"""Operator what-if: the price of an override, before it is applied."""
from dataclasses import replace

from .ladder import solve_ladder


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