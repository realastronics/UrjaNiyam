from dataclasses import replace
from pathlib import Path

import pytest

from loadshed import Policy, Problem, Tier, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.solvers import solve_greedy
from tests.helpers import H, policy, problem

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
HOURS = (3, 17)


def _p(n, ratio, seed, hour, pol=POL, **kw):
    return build_problem(make_grid(n, seed), pol, make_scenario("t", ratio), slot_of_day(hour),
                         episode_offset=8, **kw)


@pytest.mark.parametrize("n,ratio", [(10, 0.03), (10, 0.07), (10, 0.20), (50, 0.03), (50, 0.07)])
def test_greedy_output_passes_the_verifier(n, ratio):
    for seed in range(5):
        for hour in HOURS:
            p = _p(n, ratio, seed, hour)
            res = solve_greedy(p)
            assert verify(p, res.schedule).ok, (n, ratio, seed, hour)
            assert compute_metrics(p, res.schedule).relief_deficit_kw == 0


def test_greedy_crisis_at_50_feeders_with_scaled_budget():
    pol = replace(POL, system_switch_budget=30)
    for seed in range(5):
        for hour in HOURS:
            p = _p(50, 0.20, seed, hour, pol)
            assert verify(p, solve_greedy(p).schedule).ok


def test_no_shortfall_means_no_cuts_and_zero_harm():
    p = _p(10, 0.0, 0, 17)
    res = solve_greedy(p)
    assert all(sum(r) == 0 for r in res.schedule.cut.values())
    assert compute_metrics(p, res.schedule).total_harm == 0


def test_greedy_never_cuts_critical_without_approval():
    for seed in range(5):
        p = _p(50, 0.20, seed, 17)
        res = solve_greedy(p)
        assert "CRITICAL_UNAPPROVED" not in verify(p, res.schedule).rules("hard")
        crit = [f.id for f in p.feeders if f.tier == Tier.CRITICAL]
        assert all(sum(res.schedule.cut[c]) == 0 for c in crit)


def test_critical_is_cut_only_when_approved_and_needed():
    pol = policy(limits={"system_switch_budget": 10})
    feeders = problem(pol=pol).feeders          # F1 500 + N1 400 + N2 300 = 1200 kW non-critical, C1 300 kW
    hard_p = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H)       # needs C1 as well
    assert solve_greedy(hard_p).short_slots == tuple(range(H))                      # no approval -> cannot cover
    approved = frozenset(("C1", t) for t in range(H))
    p2 = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H, approvals=approved)
    res = solve_greedy(p2)
    assert res.short_slots == () and verify(p2, res.schedule).ok and sum(res.schedule.cut["C1"]) == H


def test_operator_overrides_are_respected():
    base = _p(10, 0.03, 0, 17)
    target = base.feeders[0].id
    keep = _p(10, 0.03, 0, 17, forced_keep=frozenset((target, t) for t in range(POL.horizon_slots)))
    cut = _p(10, 0.03, 0, 17, forced_cut=frozenset({(target, 0), (target, 1)}))
    assert all(x == 0 for x in solve_greedy(keep).schedule.cut[target])
    assert solve_greedy(cut).schedule.cut[target][:2] == (1, 1)
    assert not verify(keep, solve_greedy(keep).schedule).rules("hard") & {"FORCED_KEEP", "FORCED_CUT"}
    assert not verify(cut, solve_greedy(cut).schedule).rules("hard") & {"FORCED_KEEP", "FORCED_CUT"}


def test_result_carries_name_and_runtime():
    res = solve_greedy(_p(10, 0.03, 0, 17))
    assert res.solver == "greedy" and res.runtime_s >= 0


@pytest.mark.parametrize("n,ratio", [(10, 0.03), (10, 0.20), (50, 0.07)])
def test_sticky_greedy_passes_the_verifier_and_is_named(n, ratio):
    from loadshed.solvers import solve_greedy_sticky
    for seed in range(4):
        for hour in HOURS:
            p = _p(n, ratio, seed, hour)
            res = solve_greedy_sticky(p)
            assert res.solver == "greedy_sticky" and verify(p, res.schedule).ok


def test_sticky_keeps_a_cut_feeder_until_it_must_rotate():
    from loadshed.solvers import solve_greedy_sticky
    p = _p(10, 0.07, 1, 10)
    row = {f: r for f, r in solve_greedy_sticky(p).schedule.cut.items()}
    runs = []
    for r in row.values():
        cur = 0
        for x in r + (0,):
            if x: cur += 1
            elif cur: runs.append(cur); cur = 0
    # constant shortfall over the horizon: a cut run only ends early if the cap or the horizon forces it
    assert runs and all(r >= min(POL.max_downtime_slots, 4) for r in runs[:-1] or runs)
