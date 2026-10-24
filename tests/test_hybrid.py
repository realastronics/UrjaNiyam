"""The hybrid (local search, then branch-and-bound from its plan) must be exact where branch-and-bound is,
and never worse than the plans it starts from."""
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from evaluation.oracle_cpsat import solve_oracle
from loadshed import Policy, Problem, solve_ladder, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.solvers import solve_greedy, solve_hybrid, solve_min_conflicts
from tests.helpers import H as H8, policy as pol8, problem as prob8
from tests.test_bnb import random_tiny, same_answer

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
TIERS = {"mild": 0.0296, "moderate": 0.0684, "crisis": 0.2}


def grid_problem(n, tier, seed, hour=17):
    return build_problem(make_grid(n, seed), POL, make_scenario(tier, TIERS[tier]), slot_of_day(hour), episode_offset=8)


@pytest.mark.parametrize("seed", range(100, 125))
def test_hybrid_matches_the_oracle_on_random_tiny_problems(seed):
    p = random_tiny(seed)
    o, h = solve_oracle(p, time_limit_s=20, workers=1), solve_hybrid(p, time_limit_s=10, seed=1)
    assert same_answer(o, h), (o.status, o.mode, o.objective, h.status, h.mode, h.objective)
    if h.schedule is not None:
        assert verify(p, h.schedule).ok and h.lower_bound <= h.objective + 1e-6


@pytest.mark.parametrize("tier,seed,room", [("mild", 0, True), ("moderate", 1, True), ("crisis", 0, False)])
def test_with_the_tree_search_cut_short_the_result_is_the_local_search_plan_not_the_greedy_one(tier, seed, room):
    # `room` marks instances where the best greedy plan is known to be far (>15%) from the optimum
    p = grid_problem(10, tier, seed)
    best_greedy = min(compute_metrics(p, s.schedule).total_harm for s in (solve_greedy(p), solve_greedy(p, sticky=True))
                      if verify(p, s.schedule).ok and not verify(p, s.schedule).soft)
    h = solve_hybrid(p, time_limit_s=8, seed=0, node_limit=1)          # branch-and-bound gets a single node
    assert h.ls_objective is not None and h.ls_objective <= best_greedy + 1e-6
    assert h.objective <= h.ls_objective + 1e-6 and h.lower_bound <= h.objective + 1e-6
    assert verify(p, h.schedule).ok
    if room:                                                             # the start plan really helped
        assert h.objective < 0.95 * best_greedy


def test_never_worse_than_local_search_alone_on_a_grid():
    p = grid_problem(10, "moderate", 1)
    h = solve_hybrid(p, time_limit_s=8, seed=2)
    ls = solve_min_conflicts(p, time_limit_s=2, seed=2)
    assert h.objective <= ls.objective + 1e-6 and verify(p, h.schedule).ok


def test_infeasible_relaxed_and_forced_relaxed_cases():
    pol = pol8(limits={"system_switch_budget": 10})
    hard = Problem(policy=pol, feeders=prob8(pol=pol).feeders, shortfall_kw=(1250.0,) * H8)
    assert solve_hybrid(hard, time_limit_s=5).status == "INFEASIBLE"
    p3 = prob8(pol=pol8(limits={"max_downtime_slots": 3}))
    h = solve_hybrid(p3, time_limit_s=5)
    assert h.status == "OPTIMAL" and h.mode == "relaxed_downtime" and verify(p3, h.schedule).rules("soft") == {"MAX_DOWNTIME"}
    assert solve_hybrid(p3, time_limit_s=5, relax_downtime_if_infeasible=False).status == "INFEASIBLE"
    forced = solve_hybrid(prob8(), time_limit_s=5, force_relaxed=True)
    assert forced.mode == "relaxed_downtime" and verify(prob8(), forced.schedule).ok


def test_the_ladder_uses_the_hybrid_for_small_grids_and_local_search_for_large_ones():
    small = solve_ladder(prob8(), time_limit_s=12)
    assert small.attempts[0].solver == "hybrid" and small.status == "PLAN"
    big = solve_ladder(grid_problem(20, "mild", 0), time_limit_s=24)
    assert big.attempts[0].solver == "min_conflicts" and big.status == "PLAN"
