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