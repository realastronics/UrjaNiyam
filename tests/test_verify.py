import pytest

from loadshed import FeederHistory, PromisedWindow, verify
from tests.helpers import GOOD, H, Z, policy, problem, sched


def hard(p, s):
    return verify(p, s).rules("hard")


# ---------- the happy path
def test_valid_schedule_passes():
    r = verify(problem(), GOOD)
    assert r.ok and not r.hard and not r.soft and not r.waived


# ---------- relief
def test_relief_shortfall_is_flagged_per_slot():
    r = verify(problem(), sched(N1=(1, 1, 1, 1, 0, 0, 0, 0)))      # nothing covers slots 4-7
    slots = {v.slot for v in r.hard if v.rule == "RELIEF"}
    assert slots == {4, 5, 6, 7}


def test_safety_margin_is_applied():
    r = verify(problem(pol=policy(margins={"safety_margin": 0.10})), GOOD)   # N2: 300 < 330
    assert "RELIEF" in r.rules("hard")


def test_restoration_surge_raises_the_requirement():
    p = problem(pol=policy(surge={"base": 0.5, "per_slot": 0.0, "max": 0.5}))
    r = verify(p, GOOD)           # N1 restored in slot 4: need 300 + 400*0.5 = 500, have 300
    v = [x for x in r.hard if x.rule == "RELIEF"]
    assert [x.slot for x in v] == [4] and v[0].magnitude == pytest.approx(200.0)


# ---------- critical tier
def test_critical_cut_needs_approval():
    s = sched(N1=(1, 1, 1, 1, 0, 0, 0, 0), C1=(0, 0, 0, 0, 1, 1, 1, 1))
    assert "CRITICAL_UNAPPROVED" in hard(problem(), s)
    approved = frozenset(("C1", t) for t in range(4, 8))
    assert verify(problem(approvals=approved), s).ok


def test_operator_forced_cut_counts_as_approval():
    s = sched(N1=(1, 1, 1, 1, 0, 0, 0, 0), C1=(0, 0, 0, 0, 1, 1, 1, 1))
    forced = frozenset(("C1", t) for t in range(4, 8))
    assert verify(problem(forced_cut=forced), s).ok


# ---------- minimum durations (in-plan and from history)
def test_min_cut_in_plan():
    assert "MIN_CUT" in hard(problem(), sched(N1=(1, 0, 0, 0, 0, 0, 0, 0)))


def test_min_restore_in_plan():
    assert "MIN_RESTORE" in hard(problem(), sched(N1=(1, 1, 0, 1, 1, 0, 0, 0)))


def test_min_cut_uses_history():
    p = problem(history={"N1": FeederHistory(currently_cut=True, run_slots=1)})
    assert "MIN_CUT" in hard(p, sched())              # restored at slot 0 after only 1 slot cut


def test_min_restore_uses_history():
    p = problem(history={"N1": FeederHistory(currently_cut=False, run_slots=1)})
    assert "MIN_RESTORE" in hard(p, sched(N1=(1, 1, 1, 1, 0, 0, 0, 0)))


def test_history_cut_that_is_long_enough_may_restore():
    p = problem(history={"N1": FeederHistory(currently_cut=True, run_slots=2)})
    assert "MIN_CUT" not in hard(p, sched())


# ---------- switching budgets
def test_feeder_budget_counts_history():
    p = problem(shift_offset=2, history={"N1": FeederHistory(switches_this_shift=2)})
    assert "FEEDER_SWITCH_BUDGET" in hard(p, GOOD)


def test_feeder_budget_resets_at_shift_boundary():
    s = sched(N1=(0, 0, 1, 1, 1, 1, 0, 0))            # first switch at t=2
    hist = {"N1": FeederHistory(switches_this_shift=2)}
    # shift boundary at t=2 (offset 6): counter resets, two switches fit
    assert "FEEDER_SWITCH_BUDGET" not in hard(problem(shift_offset=6, history=hist), s)
    # boundary at t=6 (offset 2): the t=2 switch is the 3rd of the shift
    assert "FEEDER_SWITCH_BUDGET" in hard(problem(shift_offset=2, history=hist), s)


def test_system_budget_counts_history_of_all_feeders():
    p = problem(shift_offset=2, history={"F1": FeederHistory(switches_this_shift=1)})
    # GOOD has 3 switches (N1 cut, N1 restore, N2 cut) + 1 from history = 4 > 3
    assert "SYSTEM_SWITCH_BUDGET" in hard(p, GOOD)
    assert verify(problem(shift_offset=2), GOOD).ok    # without that history it fits exactly


# ---------- operator overrides
def test_forced_cut_must_be_respected():
    assert "FORCED_CUT" in hard(problem(forced_cut=frozenset({("F1", 0)})), GOOD)


def test_forced_keep_must_be_respected():
    assert "FORCED_KEEP" in hard(problem(forced_keep=frozenset({("N1", 0)})), GOOD)


def test_override_waives_duration_rule_but_not_relief():
    s = sched(N1=(1, 0, 0, 0, 0, 0, 0, 0))             # restored after 1 slot: breaks MIN_CUT at t=1
    r = verify(problem(forced_keep=frozenset({("N1", 1)})), s)
    assert "MIN_CUT" in r.rules("waived") and "MIN_CUT" not in r.rules("hard")
    assert "RELIEF" in r.rules("hard")                  # physics is never waived


# ---------- promised supply
def test_promised_supply_hours():
    p = problem(promises={"N2": [PromisedWindow(0, 8, 5)]})
    assert "PROMISED_SUPPLY" in hard(p, GOOD)           # N2 is on only 4 of 8 slots
    assert "PROMISED_SUPPLY" not in hard(problem(promises={"N2": [PromisedWindow(0, 8, 4)]}), GOOD)


# ---------- soft rule
def test_max_downtime_is_soft_with_magnitude():
    r = verify(problem(), sched(N1=(1, 1, 1, 1, 1, 0, 0, 0)))    # 5 slots, limit 4
    assert [(v.rule, v.magnitude) for v in r.soft] == [("MAX_DOWNTIME", 1)]
    assert "MAX_DOWNTIME" not in r.rules("hard")


def test_downtime_run_that_started_in_history_counts():
    p = problem(history={"N1": FeederHistory(currently_cut=True, run_slots=4)})
    r = verify(p, sched(N1=(1, 1, 0, 0, 0, 0, 0, 0)))             # 4 + 2 = 6 slots
    assert any(v.rule == "MAX_DOWNTIME" and v.magnitude == 2 for v in r.soft)


# ---------- malformed input
@pytest.mark.parametrize("bad", [
    {"N1": (1, 1)},                                  # wrong length
    {"N1": (2, 0, 0, 0, 0, 0, 0, 0)},                # not 0/1
])
def test_malformed_rows(bad):
    assert verify(problem(), sched(**bad)).rules("hard") == {"MALFORMED"}


def test_missing_and_unknown_feeders():
    s = sched()
    cut = dict(s.cut)
    del cut["C1"]
    cut["ZZ"] = Z
    r = verify(problem(), type(s)(cut))
    assert r.rules("hard") == {"MALFORMED"} and len(r.hard) == 2
