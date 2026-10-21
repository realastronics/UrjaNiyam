"""The from-scratch branch-and-bound is checked against the CP-SAT oracle (and brute force via test_oracle)."""
import random
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from evaluation.oracle_cpsat import solve_oracle
from loadshed import FeederHistory, Policy, Problem, verify
from loadshed.sim import build_problem, load_tiers, make_grid, make_scenario, slot_of_day
from loadshed.solvers import solve_bnb
from tests.helpers import H as H8, policy as pol8, problem as prob8
from tests.test_oracle import tiny_policy, tiny_problem

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")


def random_tiny(seed):
    rng = random.Random(seed)
    pol = tiny_policy(limits={"feeder_switch_budget": rng.choice([1, 2, 3]), "system_switch_budget": rng.choice([2, 3, 4, 5]),
                              "min_cut_slots": rng.choice([1, 2]), "min_restore_slots": rng.choice([1, 2, 3]),
                              "max_downtime_slots": rng.choice([2, 3, 4])}, shift={"shift_slots": rng.choice([3, 4, 6])})
    hist = {fid: FeederHistory(currently_cut=rng.random() < 0.4, run_slots=rng.randint(1, 4),
                               switches_this_shift=rng.randint(0, 2)) for fid in "ABC" if rng.random() < 0.6}
    sf = tuple(float(rng.choice([0, 80, 120, 150, 200])) for _ in range(5))
    fk = frozenset({("C", rng.randint(0, 4))}) if rng.random() < 0.4 else frozenset()
    fc = frozenset({("B", rng.randint(0, 4))}) if rng.random() < 0.3 else frozenset()
    return tiny_problem(pol=pol, shortfall=sf, history=hist, forced_keep=fk, forced_cut=fc,
                        shift_offset=rng.randint(0, pol.shift_slots - 1))


def same_answer(o, b):
    if o.status == "INFEASIBLE":
        return b.status == "INFEASIBLE"
    return (o.status == "OPTIMAL" and b.status == "OPTIMAL" and o.mode == b.mode
            and b.objective == pytest.approx(o.objective, rel=1e-6))


@pytest.mark.parametrize("seed", range(100, 125))
def test_bnb_matches_oracle_on_random_tiny_problems(seed):
    p = random_tiny(seed)
    o, b = solve_oracle(p, time_limit_s=20, workers=1), solve_bnb(p, time_limit_s=20)
    assert same_answer(o, b), (o.status, o.mode, o.objective, b.status, b.mode, b.objective)
    if b.schedule is not None:
        assert verify(p, b.schedule).ok


@pytest.mark.parametrize("seed", range(100, 125))
def test_root_lower_bound_never_exceeds_the_optimum(seed):
    p = random_tiny(seed)
    o = solve_oracle(p, time_limit_s=20, workers=1, relax_downtime_if_infeasible=False)
    b = solve_bnb(p, node_limit=1, relax_downtime_if_infeasible=False)
    if o.status == "OPTIMAL" and b.lower_bound is not None:
        assert b.lower_bound <= o.objective + 1e-6


@pytest.mark.parametrize("tier,seed,hour", [("mild", 0, 10), ("moderate", 1, 17), ("crisis", 1, 17)])
def test_bnb_matches_oracle_on_generated_10_feeder_grids(tier, seed, hour):
    tiers = {"mild": 0.0296, "moderate": 0.0684, "crisis": 0.2}
    p = build_problem(make_grid(10, seed), POL, make_scenario(tier, tiers[tier]), slot_of_day(hour), episode_offset=8)
    o, b = solve_oracle(p, time_limit_s=30, workers=1), solve_bnb(p, time_limit_s=30)
    assert same_answer(o, b) and verify(p, b.schedule).ok


def test_node_limit_returns_a_valid_schedule_and_a_bound():
    p = build_problem(make_grid(10, 0), POL, make_scenario("crisis", 0.2), slot_of_day(10), episode_offset=8)
    b = solve_bnb(p, node_limit=3)
    assert b.status in ("FEASIBLE", "OPTIMAL") and verify(p, b.schedule).ok
    assert b.lower_bound <= b.objective + 1e-6 and b.nodes <= 4


def test_infeasible_relaxed_and_approved_cases():
    pol = pol8(limits={"system_switch_budget": 10})
    feeders = prob8(pol=pol).feeders
    hard_p = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8)
    assert solve_bnb(hard_p).status == "INFEASIBLE"
    ok = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8, approvals=frozenset(("C1", t) for t in range(H8)))
    b, o = solve_bnb(ok), solve_oracle(ok, time_limit_s=10)
    assert same_answer(o, b) and verify(ok, b.schedule).ok
    p3 = prob8(pol=pol8(limits={"max_downtime_slots": 3}))
    b3 = solve_bnb(p3)
    assert b3.status == "OPTIMAL" and b3.mode == "relaxed_downtime" and verify(p3, b3.schedule).rules("soft") == {"MAX_DOWNTIME"}
    assert solve_bnb(p3, relax_downtime_if_infeasible=False).status == "INFEASIBLE"
