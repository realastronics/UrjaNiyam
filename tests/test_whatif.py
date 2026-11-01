import pytest

pytest.importorskip("ortools")

from loadshed.whatif import what_if
from tests.helpers import H, problem


def test_forcing_the_cheap_feeder_on_costs_extra_and_is_obeyed():
    a, b, extra = what_if(problem(), keep=[("F1", t) for t in range(H)], time_limit_s=12)
    assert extra is not None and extra > 0 and sum(b.schedule.cut["F1"]) == 0