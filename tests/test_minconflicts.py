"""Local search is checked against the oracle (never below the optimum, usually at it), the verifier
(never invalid), the greedy baselines (never worse) and the branch-and-bound lower bound."""
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from evaluation.oracle_cpsat import solve_oracle
from loadshed import Policy, Problem, verify
from loadshed.metrics import compute_metrics
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.solvers import solve_bnb, solve_greedy, solve_greedy_sticky, solve_min_conflicts
from tests.helpers import H as H8, policy as pol8, problem as prob8
from tests.test_bnb import random_tiny

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
TIERS = {"mild": 0.0296, "moderate": 0.0684, "crisis": 0.2}


def grid_problem(n, tier, seed, hour, **kw):
    return build_problem(make_grid(n, seed), POL, make_scenario(tier, TIERS[tier]), slot_of_day(hour),
                         episode_offset=8, **kw)


def test_on_random_tiny_problems_it_is_valid_never_below_optimum_and_usually_optimal():
    at_opt, feasible = 0, 0
    for seed in range(100, 125):
        p = random_tiny(seed)
        o = solve_oracle(p, time_limit_s=20, workers=1)
        r = solve_min_conflicts(p, time_limit_s=10, seed=1, max_iters=1500)
        if o.status == "INFEASIBLE":
            assert r.schedule is None and r.status in ("INFEASIBLE", "UNKNOWN")
            continue
        feasible += 1
        assert r.schedule is not None and verify(p, r.schedule).ok
        assert r.objective >= o.objective - 1e-6                      # nothing can beat the proven optimum
        at_opt += abs(r.objective - o.objective) <= 1e-6 * max(1.0, o.objective)
        assert r.mode == o.mode or abs(r.objective - o.objective) > 1e-6   # same ladder rung when optimal
    assert at_opt >= feasible - 2


@pytest.mark.parametrize("n,tier", [(10, "mild"), (10, "crisis"), (50, "moderate")])
def test_never_worse_than_the_best_valid_greedy_and_always_valid(n, tier):
    for seed in range(3):
        p = grid_problem(n, tier, seed, 17)
        best = min(compute_metrics(p, s.schedule).total_harm for s in (solve_greedy(p), solve_greedy_sticky(p))
                   if verify(p, s.schedule).ok and not verify(p, s.schedule).soft)
        r = solve_min_conflicts(p, time_limit_s=20, seed=seed, max_iters=300)
        assert verify(p, r.schedule).ok and r.objective <= best + 1e-6
        assert r.start_objective == pytest.approx(best)


@pytest.mark.parametrize("tier,seed", [("mild", 0), ("moderate", 1), ("crisis", 1)])
def test_objective_respects_the_branch_and_bound_lower_bound(tier, seed):
    p = grid_problem(10, tier, seed, 17)
    lb = solve_bnb(p, node_limit=1).lower_bound
    r = solve_min_conflicts(p, time_limit_s=20, seed=0, max_iters=800)
    assert r.objective >= lb - 1e-6


def test_improves_on_the_greedy_when_there_is_room():
    p = grid_problem(50, "moderate", 0, 17)
    r = solve_min_conflicts(p, time_limit_s=30, seed=0, max_iters=600)
    assert r.objective < 0.9 * r.start_objective and verify(p, r.schedule).ok


def test_same_seed_and_iterations_give_the_same_plan():
    p = grid_problem(10, "moderate", 0, 17)
    a = solve_min_conflicts(p, time_limit_s=30, seed=5, max_iters=400)
    b = solve_min_conflicts(p, time_limit_s=30, seed=5, max_iters=400)
    assert a.schedule == b.schedule and a.objective == b.objective


def test_operator_overrides_are_respected():
    base = grid_problem(10, "moderate", 0, 17)
    target = base.feeders[0].id
    keep = grid_problem(10, "moderate", 0, 17, forced_keep=frozenset((target, t) for t in range(POL.horizon_slots)))
    cut = grid_problem(10, "moderate", 0, 17, forced_cut=frozenset({(target, 2), (target, 3)}))
    rk = solve_min_conflicts(keep, time_limit_s=20, seed=0, max_iters=300)
    rc = solve_min_conflicts(cut, time_limit_s=20, seed=0, max_iters=300)
    assert verify(keep, rk.schedule).ok and all(x == 0 for x in rk.schedule.cut[target])
    assert verify(cut, rc.schedule).ok and rc.schedule.cut[target][2:4] == (1, 1)


def test_infeasible_without_approval_is_proven_and_with_approval_it_solves():
    pol = pol8(limits={"system_switch_budget": 10})
    feeders = prob8(pol=pol).feeders
    hard = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8)
    r = solve_min_conflicts(hard, time_limit_s=10, max_iters=200)
    assert r.status == "INFEASIBLE" and r.schedule is None
    ok = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8,
                 approvals=frozenset(("C1", t) for t in range(H8)))
    r2 = solve_min_conflicts(ok, time_limit_s=10, max_iters=500)
    assert r2.schedule is not None and verify(ok, r2.schedule).ok


def test_relaxes_max_downtime_only_when_strict_has_no_plan():
    p = prob8(pol=pol8(limits={"max_downtime_slots": 3}))
    r = solve_min_conflicts(p, time_limit_s=10, seed=0, max_iters=800)
    assert r.mode == "relaxed_downtime" and verify(p, r.schedule).rules("soft") == {"MAX_DOWNTIME"}
    easy = solve_min_conflicts(prob8(), time_limit_s=10, seed=0, max_iters=800)
    assert easy.mode == "strict" and verify(prob8(), easy.schedule).ok
