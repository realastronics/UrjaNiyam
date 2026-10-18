import pytest

from loadshed import FeederHistory, verify
from loadshed.metrics import compute_metrics, required_relief
from tests.helpers import GOOD, policy, problem, sched


def test_hand_computed_harm():
    m = compute_metrics(problem(), GOOD)
    # N1 cut 4 slots: 5*400*(1+1.5+2+2.5)=14000 ; N2 cut 4 slots: 5*300*7=10500
    assert m.cut_harm == pytest.approx(24500)
    assert m.overshoot_kw == pytest.approx(400)            # N1 gives 400 vs 300 needed, 4 slots
    assert m.overshoot_penalty == pytest.approx(0.02 * 400)
    assert m.n_switches == 3 and m.switch_penalty == pytest.approx(3.0)
    assert m.total_harm == pytest.approx(24500 + 8 + 3)
    assert m.shed_kwh == pytest.approx((400 * 4 + 300 * 4) * 0.25)
    assert (m.cut_slots_max, m.longest_outage_slots, m.critical_cut_slots) == (4, 4, 0)
    assert m.cut_slots_mean == pytest.approx(2.0) and m.relief_deficit_kw == 0


def test_history_run_length_raises_harm():
    p = problem(history={"N1": FeederHistory(currently_cut=True, run_slots=2)})
    s = sched(N1=(1, 1, 1, 1, 0, 0, 0, 0), N2=(0, 0, 0, 0, 1, 1, 1, 1))
    m = compute_metrics(p, s)
    # N1 slots are now the 3rd..6th consecutive cut slot: 5*400*(2+2.5+3+3.5)=22000
    assert m.cut_harm == pytest.approx(22000 + 10500)
    assert m.n_switches == 2                                # no switch at slot 0, N1 restore + N2 cut


def test_time_of_day_weight_is_used():
    pol = policy(harm={"time_of_day_weights": [2.0] + [1.0] * 23})   # hour 0 (slots 0-3) costs double
    m = compute_metrics(problem(pol=pol), GOOD)
    assert m.cut_harm == pytest.approx(2 * 14000 + 10500)


def test_required_relief_includes_surge_and_matches_verifier():
    pol = policy(surge={"base": 0.5, "per_slot": 0.0, "max": 0.5})
    p = problem(pol=pol)
    need = required_relief(p, GOOD)
    assert need[4] == pytest.approx(300 + 400 * 0.5) and need[0] == 300
    r = verify(p, GOOD)
    v = [x for x in r.hard if x.rule == "RELIEF"][0]
    assert v.magnitude == pytest.approx(need[4] - 300)
    assert compute_metrics(p, GOOD).relief_deficit_kw == pytest.approx(200)
