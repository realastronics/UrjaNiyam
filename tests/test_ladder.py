"""One scenario per rung. Every 'executable' plan must pass the verifier; a proposal must NOT."""
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from loadshed import Policy, Problem, Tier, solve_ladder, verify, with_approvals
from loadshed.ladder import ESCALATE, NEEDS_APPROVAL, PLAN
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.solvers import solve_bnb
from tests.helpers import H as H8, policy as pol8, problem as prob8

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
POL10 = pol8(limits={"system_switch_budget": 10})


def helpers_problem(shortfall, **kw):
    return Problem(policy=POL10, feeders=prob8(pol=POL10).feeders, shortfall_kw=(float(shortfall),) * H8, **kw)


def test_rung_0_all_rules_hold():
    p = prob8()
    r = solve_ladder(p, time_limit_s=12)
    assert (r.rung, r.status, r.executable) == (0, PLAN, True)
    assert r.relaxations == () and r.needs_approval == () and verify(p, r.schedule).ok
    assert r.objective == pytest.approx(solve_bnb(p, relax_downtime_if_infeasible=False).objective)
    assert r.proven and "RUNG 0" in r.messages[0]


def test_rung_1_relaxes_max_downtime_and_reports_the_size():
    p = prob8(pol=pol8(limits={"max_downtime_slots": 3}))
    r = solve_ladder(p, time_limit_s=12)
    assert (r.rung, r.status, r.executable) == (1, PLAN, True)
    soft = verify(p, r.schedule).soft
    assert soft and r.relaxations[0].rule == "MAX_DOWNTIME"
    assert r.relaxations[0].magnitude == pytest.approx(sum(v.magnitude for v in soft))
    assert any("limit 3" in m for m in r.messages) and r.proven        # strict was PROVEN impossible


def test_rung_2_is_a_proposal_that_needs_approval_and_is_not_executable():
    p = helpers_problem(1250)                    # 1200 kW non-critical is not enough: C1 (300 kW) is needed
    r = solve_ladder(p, time_limit_s=12)
    assert (r.rung, r.status, r.executable) == (2, NEEDS_APPROVAL, False)
    assert r.needs_approval and all(fid == "C1" for fid, _ in r.needs_approval)
    assert "CRITICAL_UNAPPROVED" in verify(p, r.schedule).rules("hard")      # truly not executable as it stands
    assert r.relaxations[-1].rule == "CRITICAL_CUT" and r.relaxations[-1].magnitude == pytest.approx(300 * len(r.needs_approval))
    assert any("NOT EXECUTABLE" in m for m in r.messages)


def test_operator_approves_then_the_plan_is_executable_and_costs_the_same():
    p = helpers_problem(1250)
    proposal = solve_ladder(p, time_limit_s=12)
    approved = with_approvals(p, proposal.needs_approval)
    r2 = solve_ladder(approved, time_limit_s=12)
    assert r2.status == PLAN and r2.executable and verify(approved, r2.schedule).ok
    assert {(f, t) for f in r2.schedule.cut for t in range(H8) if f == "C1" and r2.schedule.cut[f][t]} \
        <= set(proposal.needs_approval)
    assert r2.objective == pytest.approx(proposal.objective, rel=1e-6)


def test_operator_declines_critical_so_we_escalate_with_the_best_coverage():
    p = helpers_problem(1250)
    r = solve_ladder(p, allow_critical_proposals=False, time_limit_s=24)
    assert (r.rung, r.status, r.executable) == (3, ESCALATE, False)
    assert sum(r.schedule.cut["C1"]) == 0                                  # never cut Critical without approval
    assert verify(p, r.schedule).rules("hard") <= {"RELIEF"}              # the partial plan breaks ONLY the relief rule
    assert r.coverage_fraction == pytest.approx(0.96, abs=0.02)            # 1200 of 1250 kW
    assert all(abs(u - 50) <= 15 for u in r.uncovered_kw)
    assert "ESCALATE" in r.messages[0] and "not offered" in r.messages[1]


def test_escalate_when_even_every_critical_feeder_is_not_enough():
    p = helpers_problem(1600)                    # all four feeders together carry only 1500 kW
    r = solve_ladder(p, time_limit_s=24)
    assert r.status == ESCALATE and sum(r.schedule.cut["C1"]) == 0
    assert r.coverage_fraction == pytest.approx(0.75, abs=0.03)             # 1200 / 1600
    assert "Even proposing every Critical" in r.messages[1]
    assert any(a.rung == 2 and a.outcome == "proven impossible" for a in r.attempts)


def only_critical_can_be_cut(ratio):
    base = build_problem(make_grid(20, 0), POL, make_scenario("t", ratio), slot_of_day(17), episode_offset=8)
    keep_all = frozenset((f.id, t) for f in base.feeders if f.tier != Tier.CRITICAL for t in range(POL.horizon_slots))
    return replace(base, forced_keep=keep_all)             # the operator has forced every non-critical feeder to stay on


def test_on_a_larger_grid_critical_is_proposed_never_cut_automatically():
    p = only_critical_can_be_cut(0.015)                    # the single Critical feeder (544-778 kW) can cover 1.5%
    r = solve_ladder(p, time_limit_s=24)
    assert r.status == NEEDS_APPROVAL and not r.executable and r.needs_approval
    crit = {f.id for f in p.feeders if f.tier == Tier.CRITICAL}
    assert {fid for fid, _ in r.needs_approval} <= crit
    d = solve_ladder(p, allow_critical_proposals=False, time_limit_s=24)
    assert d.status == ESCALATE and d.coverage_fraction == 0.0 and sum(map(sum, d.schedule.cut.values())) == 0
    assert sum(d.uncovered_kw) > 0


def test_critical_feeder_too_small_escalates_and_every_rung_was_proven_impossible():
    p = only_critical_can_be_cut(0.0296)                   # needs 785-892 kW; the Critical feeder has 544-778 kW
    r = solve_ladder(p, time_limit_s=24)
    assert r.status == ESCALATE and r.proven
    assert [(a.rung, a.outcome) for a in r.attempts[:3]] == [(0, "proven impossible"), (1, "proven impossible"),
                                                             (2, "proven impossible")]


def test_a_normal_grid_scenario_is_rung_0_and_verified():
    p = build_problem(make_grid(20, 1), POL, make_scenario("moderate", 0.0684), slot_of_day(17), episode_offset=8)
    r = solve_ladder(p, time_limit_s=24)
    assert r.status == PLAN and r.executable and verify(p, r.schedule).ok
