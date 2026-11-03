from dataclasses import replace

import pytest

from loadshed import FeederHistory, Problem, Schedule, verify
from loadshed.solvers import solve_bnb
from tests.helpers import feeders, policy

def small_problem(**kwargs):
    pol = policy(
        time={"horizon_slots": 2},
        shift={"shift_slots": 2},
        limits={
            "min_cut_slots": 1,
            "min_restore_slots": 1,
            "max_downtime_slots": 2,
            "system_switch_budget": 1,
        },
    )
    fs = tuple(replace(f, load_kw=(100.0, 100.0)) for f in feeders()[:2])
    return Problem(
        policy=pol, feeders=fs, shortfall_kw=(0.0, 0.0), **kwargs
    )

@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("rows,forced,offset,expected", [
    (((1, 1), (1, 1)), {("F1", 0)}, 0, True),
    (((1, 1), (1, 1)), set(), 0, False),
    (((1, 1), (0, 1)), {("F1", 0)}, 0, False),
    (((1, 1), (0, 1)), {("F1", 0)}, 1, True),
])
def test_switch_order(reverse, rows, forced, offset, expected):
    p = small_problem(forced_cut=frozenset(forced), shift_offset=offset)
    if reverse:
        p = replace(p, feeders=tuple(reversed(p.feeders)))
    result = verify(p, Schedule(dict(zip(("F1", "N1"), rows))))
    assert result.ok == expected
    if not expected:
        assert result.rules() == {"SYSTEM_SWITCH_BUDGET"}
    if rows == ((1, 1), (1, 1)) and forced:
        assert result.rules("waived") == {"SYSTEM_SWITCH_BUDGET"}
        assert result.waived[0].feeder == "F1"

def test_exhausted_history():
    p = small_problem(
        shift_offset=1,
        history={"N1": FeederHistory(switches_this_shift=2)},
        forced_cut=frozenset({("F1", 0)}),
    )
    assert verify(p, Schedule({"F1": (1, 1), "N1": (0, 0)})).ok
    assert not verify(p, Schedule({"F1": (1, 1), "N1": (1, 1)})).ok

@pytest.mark.parametrize("reverse", [False, True])
def test_oracle_and_bnb_agree(reverse):
    pytest.importorskip("ortools")
    from evaluation.oracle_cpsat import solve_oracle

    p = replace(
        small_problem(forced_cut=frozenset({("F1", 0)})),
        shortfall_kw=(150.0, 150.0),
    )
    if reverse:
        p = replace(p, feeders=tuple(reversed(p.feeders)))
    exact = solve_bnb(
        p, time_limit_s=5, relax_downtime_if_infeasible=False
    )
    oracle = solve_oracle(
        p, time_limit_s=5, workers=1, relax_downtime_if_infeasible=False
    )
    assert exact.status == oracle.status == "OPTIMAL"
    assert exact.objective == pytest.approx(oracle.objective)
    assert verify(p, exact.schedule).ok
    assert verify(p, oracle.schedule).ok