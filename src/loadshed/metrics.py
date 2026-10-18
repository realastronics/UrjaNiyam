"""Objective and reporting numbers. Every solver and the CP-SAT oracle must use THIS objective.

harm = sum over cut (feeder, slot):  tier_weight * time_of_day_weight * consumers * (1 + beta*(r-1))
     + overshoot_lambda * sum over slots of (relief - required)
     + switch_mu * number of breaker operations
r = number of consecutive slots the feeder has been cut, counting history and this slot.

No fairness claim: cut-slot spread is reported as plain numbers only.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Problem, Schedule, Tier


@dataclass(frozen=True)
class Metrics:
    total_harm: float
    cut_harm: float
    overshoot_penalty: float
    switch_penalty: float
    shed_kwh: float
    overshoot_kw: float            # sum over slots of relief beyond the requirement
    relief_deficit_kw: float       # sum over slots of requirement not met (0 for a valid plan)
    n_switches: int
    cut_slots_mean: float          # mean cut slots per feeder (all feeders)
    cut_slots_max: int
    longest_outage_slots: int
    critical_cut_slots: int


def required_relief(problem: Problem, schedule: Schedule) -> list[float]:
    """S(t)*(1+margin) + restoration surge, per slot. Same definition the verifier enforces."""
    pol = problem.policy
    need = [problem.shortfall_kw[t] * (1 + pol.safety_margin) for t in range(pol.horizon_slots)]
    for f in problem.feeders:
        h = problem.hist(f.id)
        prev, run = (1 if h.currently_cut else 0), h.run_slots
        for t, cur in enumerate(schedule.cut[f.id]):
            if cur != prev:
                if prev == 1:
                    need[t] += f.load_kw[t] * pol.surge_fraction(run)
                prev, run = cur, 1
            else:
                run += 1
    return need


def compute_metrics(problem: Problem, schedule: Schedule) -> Metrics:
    pol = problem.policy
    H = pol.horizon_slots
    cut_harm = 0.0
    n_switches = 0
    cut_counts, longest, crit_slots = [], 0, 0

    for f in problem.feeders:
        h = problem.hist(f.id)
        w = pol.tier_weights[f.tier]
        prev, run = (1 if h.currently_cut else 0), h.run_slots
        n_cut = 0
        for t, cur in enumerate(schedule.cut[f.id]):
            if cur != prev:
                n_switches += 1
                prev, run = cur, 1
            else:
                run += 1
            if cur == 1:
                n_cut += 1
                cut_harm += w * pol.time_of_day_weights[problem.hour_of(t)] * f.consumers * (1 + pol.harm_beta * (run - 1))
                longest = max(longest, run)
                crit_slots += f.tier == Tier.CRITICAL
        cut_counts.append(n_cut)

    need = required_relief(problem, schedule)
    relief = [sum(f.load_kw[t] for f in problem.feeders if schedule.cut[f.id][t] == 1) for t in range(H)]
    over = sum(max(0.0, relief[t] - need[t]) for t in range(H))
    deficit = sum(max(0.0, need[t] - relief[t]) for t in range(H))
    slot_h = pol.slot_minutes / 60
    over_pen, sw_pen = pol.overshoot_lambda * over, pol.switch_mu * n_switches
    return Metrics(
        total_harm=cut_harm + over_pen + sw_pen, cut_harm=cut_harm, overshoot_penalty=over_pen,
        switch_penalty=sw_pen, shed_kwh=sum(relief) * slot_h, overshoot_kw=over,
        relief_deficit_kw=deficit, n_switches=n_switches,
        cut_slots_mean=sum(cut_counts) / max(1, len(cut_counts)), cut_slots_max=max(cut_counts, default=0),
        longest_outage_slots=longest, critical_cut_slots=int(crit_slots))
