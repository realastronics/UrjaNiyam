import pytest

pytest.importorskip("ortools")

from loadshed.whatif import explain_cuts, what_if
from tests.helpers import H, problem


def test_forcing_the_cheap_feeder_on_costs_extra_and_is_obeyed():
    a, b, extra = what_if(problem(), keep=[("F1", t) for t in range(H)], time_limit_s=12)
    assert extra is not None and extra > 0 and sum(b.schedule.cut["F1"]) == 0

def test_explain_cuts_prices_every_first_slot_cut():
    plan, why = explain_cuts(problem(), time_limit_s=12)
    assert set(why) == {f for f, row in plan.schedule.cut.items() if row[0]}
    assert all(v is None or v > -1e-6 for v in why.values())