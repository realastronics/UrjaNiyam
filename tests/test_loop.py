"""The rolling-horizon loop. The strongest check: what was ACTUALLY executed, cycle after cycle, must pass the
verifier as ONE plan over the whole episode (so minimum durations, switching budgets and shift resets held
across replans, not just inside each plan)."""
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from loadshed import Feeder, Policy, Problem, Tier, verify
from loadshed.loop import (ActuationFailure, EpisodeConfig, Override, ShortfallChange, approve_all, decline_all,
                           run_episode)
from loadshed.sim import make_grid, make_scenario
from loadshed.sim.grid import feeder_loads

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")


def cfg(n=6, ratio=0.0684, E=10, pol=POL, **kw):
    return EpisodeConfig(grid=make_grid(n, 0), policy=pol, scenario=make_scenario("t", ratio, episode_slots=E),
                         time_limit_s=2, **kw)


def whole_episode_check(c, res):
    """Verify the executed history as a single plan covering the whole episode."""
    E = len(c.scenario.shape)
    pol = replace(c.policy, horizon_slots=E)
    feeders = tuple(Feeder(s.id, s.tier, s.ftype, s.consumers, feeder_loads(s, pol, c.start_slot)) for s in c.grid.specs)
    fc = frozenset((o.feeder_id, g) for o in res.overrides if o.action == "cut" for g in range(o.start, min(o.end, E)))
    fk = frozenset((o.feeder_id, g) for o in res.overrides if o.action == "keep" for g in range(o.start, min(o.end, E)))
    prob = Problem(policy=pol, feeders=feeders, shortfall_kw=res.shortfall_kw, shift_offset=c.shift_offset0,
                   forced_cut=fc, forced_keep=fk, approvals=frozenset((f, g) for f, g in res.approved if g < E),
                   start_slot=c.start_slot)
    return verify(prob, res.executed_schedule())


def first_cut(res):
    for g in range(len(res.steps)):
        for i, row in res.executed.items():
            if row[g] == 1 and (g == 0 or row[g - 1] == 0):
                return g, i


def test_executed_history_passes_the_verifier_as_one_plan():
    c = cfg()
    res = run_episode(c)
    assert whole_episode_check(c, res).ok
    assert res.metrics.unserved_kw_slots == 0 and res.metrics.rung_counts == {0: 10}
    assert res.metrics.critical_cut_slots == 0 and len(res.steps) == 10


def test_switching_budget_resets_at_shift_boundaries_across_cycles():
    # 4-slot shifts, 2 switches per feeder per shift and a 3-slot downtime limit FORCE rotation every shift.
    # It is only solvable at rung 0 if the ledger's switch counters reset when a new shift starts.
    tight = replace(POL, shift_slots=4, feeder_switch_budget=2, system_switch_budget=8, max_downtime_slots=3)
    c = cfg(E=12, pol=tight)
    res = run_episode(c)
    assert res.metrics.rung_counts == {0: 12} and res.metrics.unserved_kw_slots == 0
    assert whole_episode_check(c, res).ok
    for a in (0, 4, 8):                                           # every shift really did switch something
        assert any(row[t] != (row[t - 1] if t else 0) for row in res.executed.values() for t in range(a, a + 4))


def test_same_seed_same_episode():
    a, b = run_episode(cfg(failure_prob=0.3, seed=7)), run_episode(cfg(failure_prob=0.3, seed=7))
    assert a.executed == b.executed and a.metrics.failed_commands == b.metrics.failed_commands


def test_a_failed_command_is_observed_counted_and_replanned_around():
    base = run_episode(cfg())
    g, f = first_cut(base)
    c = cfg(events=(ActuationFailure(g, f),))
    res = run_episode(c)
    assert res.metrics.failed_commands == 1 and res.executed[f][g] == 0       # the breaker did not move
    assert res.steps[g].failed == 1 and res.steps[g].unserved_kw > 0          # the shortfall was really not relieved
    assert res.steps[g + 1].unserved_kw == 0                                  # the very next plan compensates
    assert res.metrics.n_switches >= base.metrics.n_switches + 1              # the failed attempt used an operation


def test_operator_overrides_are_obeyed_and_the_plan_stays_valid():
    base = run_episode(cfg())
    _, f = first_cut(base)
    other = next(s.id for s in cfg().grid.specs if s.id != f and s.tier == Tier.NORMAL)
    c = cfg(events=(Override(2, 8, f, "keep"), Override(3, 6, other, "cut")))
    res = run_episode(c)
    assert all(x == 0 for x in res.executed[f][2:8]) and all(x == 1 for x in res.executed[other][3:6])
    assert whole_episode_check(c, res).ok and res.metrics.unserved_kw_slots == 0


def test_a_shortfall_change_is_picked_up_from_the_slot_it_happens():
    base = run_episode(cfg(ratio=0.05))
    c = cfg(ratio=0.05, events=(ShortfallChange(5, 0.15),))
    res = run_episode(c)
    assert res.shortfall_kw[:5] == base.shortfall_kw[:5] and res.shortfall_kw[5] > 2 * base.shortfall_kw[5]
    assert res.steps[5].unserved_kw == 0 and res.steps[5].net_relief_kw >= res.shortfall_kw[5]
    assert whole_episode_check(c, res).ok


def critical_only_config(approve):
    c0 = cfg(E=8)
    crit = {s.id for s in c0.grid.specs if s.tier == Tier.CRITICAL}
    pol = c0.policy
    est = {s.id: feeder_loads(s, replace(pol, horizon_slots=16), c0.start_slot) for s in c0.grid.specs}
    ratio = 0.5 * min(sum(est[i][g] for i in crit) / sum(e[g] for e in est.values()) for g in range(8))
    keep = tuple(Override(0, 8, s.id, "keep") for s in c0.grid.specs if s.id not in crit)
    return cfg(E=8, ratio=ratio, events=keep, operator=approve_all if approve else decline_all)


def test_critical_cuts_happen_only_after_the_operator_approves():
    yes_cfg = critical_only_config(True)
    yes = run_episode(yes_cfg)
    assert yes.metrics.critical_cut_slots > 0 and yes.metrics.approvals_granted > 0
    assert yes.metrics.unserved_kw_slots == 0 and whole_episode_check(yes_cfg, yes).ok
    no = run_episode(critical_only_config(False))
    assert no.metrics.critical_cut_slots == 0 and no.metrics.escalations > 0
    assert no.metrics.approvals_granted == 0 and no.metrics.unserved_kw_slots > 0


def test_a_bigger_safety_margin_buys_protection_against_load_error_at_a_price():
    thin = run_episode(cfg(ratio=0.25, load_error=(0.2, 0.0), seed=2, pol=replace(POL, safety_margin=0.05)))
    fat = run_episode(cfg(ratio=0.25, load_error=(0.2, 0.0), seed=2, pol=replace(POL, safety_margin=0.40)))
    assert thin.metrics.unserved_kw_slots > 0 and fat.metrics.unserved_kw_slots == 0
    assert fat.metrics.total_harm > 1.5 * thin.metrics.total_harm
    # learning each feeder's real load from its meter removes the shortfall the thin margin leaves, without a fat margin
    fb = run_episode(cfg(ratio=0.25, load_error=(0.2, 0.0), seed=2, pol=replace(POL, safety_margin=0.05), load_feedback=(0.7, 0.0)))
    assert fb.metrics.unserved_kw_slots < thin.metrics.unserved_kw_slots

def test_warm_start_keeps_plans_valid_and_metrics_are_well_formed():
    c = cfg(ratio=0.2, E=8)
    for ws in (True, False):
        res = run_episode(replace(c, warm_start=ws))
        m = res.metrics
        assert whole_episode_check(replace(c, warm_start=ws), res).ok
        assert m.replans == 8 and m.total_harm == pytest.approx(m.cut_harm + m.switch_cost)
        assert m.plan_revision_rate_quiet is None or 0.0 <= m.plan_revision_rate_quiet <= 1.0
        n = len(c.grid.specs)
        quiet = [x for x in res.steps if x.quiet and x.plan_revision is not None]
        assert m.commit_surprises == sum(x.surprises for x in res.steps) and m.unplanned_reversals >= 0
        if quiet:
            assert m.commit_surprise_rate_quiet == pytest.approx(sum(x.surprises for x in quiet) / (len(quiet) * n))
            assert 0.0 <= m.commit_surprise_rate_quiet <= 1.0
