"""The oracle is checked against brute force: on tiny problems, enumerate EVERY schedule,
keep those the verifier accepts, and take the minimum of metrics.total_harm.
The oracle must match that number exactly. This ties oracle == verifier + metrics."""
import itertools
from dataclasses import replace

import pytest

pytest.importorskip("ortools")

from evaluation.oracle_cpsat import solve_oracle
from loadshed import (Feeder, FeederHistory, FeederType, Policy, Problem, PromisedWindow,
                      Schedule, Tier, verify)
from loadshed.metrics import compute_metrics
from tests.helpers import BASE_POLICY, H as H8, policy as pol8, problem as prob8
from copy import deepcopy


def tiny_policy(**over):
    d = deepcopy(BASE_POLICY)
    d["time"]["horizon_slots"] = 5
    d["shift"]["shift_slots"] = 6
    d["limits"].update({"min_cut_slots": 2, "min_restore_slots": 2, "max_downtime_slots": 3,
                        "feeder_switch_budget": 2, "system_switch_budget": 3})
    d["surge"] = {"base": 0.10, "per_slot": 0.05, "max": 0.30}
    d["margins"]["safety_margin"] = 0.05
    d["harm"]["time_of_day_weights"] = [1.0, 2.0] + [1.0] * 22
    for sec, vals in over.items():
        d[sec].update(vals)
    return Policy.from_dict(d)


def tiny_problem(pol=None, shortfall=(150.0, 200.0, 200.0, 100.0, 150.0), **kw):
    pol = pol or tiny_policy()
    spec = [("A", Tier.FLEXIBLE, 100.0, 5), ("B", Tier.NORMAL, 220.0, 300), ("C", Tier.NORMAL, 180.0, 200)]
    feeders = tuple(Feeder(i, t, FeederType.URBAN, n, (kw_,) * 5) for i, t, kw_, n in spec)
    return Problem(policy=pol, feeders=feeders, shortfall_kw=shortfall, start_slot=2, **kw)


def brute_force(p, strict=True):
    ids = [f.id for f in p.feeders]
    best = None
    rows = list(itertools.product((0, 1), repeat=5))
    for combo in itertools.product(rows, repeat=len(ids)):
        s = Schedule(dict(zip(ids, combo)))
        v = verify(p, s)
        if not v.ok or (strict and v.soft):
            continue
        h = compute_metrics(p, s).total_harm
        if best is None or h < best:
            best = h
    return best


def check_against_brute_force(p):
    """Strict optimum if one exists; else relaxed-downtime optimum; else the oracle must say INFEASIBLE."""
    got = solve_oracle(p, time_limit_s=20, workers=1)
    strict = brute_force(p, strict=True)
    if strict is not None:
        assert got.status == "OPTIMAL" and got.mode == "strict"
        want = strict
    else:
        relaxed = brute_force(p, strict=False)
        if relaxed is None:
            assert got.status == "INFEASIBLE"
            return "infeasible"
        assert got.status == "OPTIMAL" and got.mode == "relaxed_downtime"
        want = relaxed
    assert verify(p, got.schedule).ok
    assert got.objective == pytest.approx(want, rel=1e-6)
    return got.mode


@pytest.mark.parametrize("hist", [
    {},
    {"B": FeederHistory(currently_cut=True, run_slots=1)},                       # must stay cut one more slot
    {"A": FeederHistory(currently_cut=False, run_slots=1, switches_this_shift=1)},  # cannot be cut yet
    {"C": FeederHistory(currently_cut=True, run_slots=2, switches_this_shift=1)},
])
def test_oracle_matches_brute_force(hist):
    check_against_brute_force(tiny_problem(history=hist, shift_offset=2))


def test_oracle_matches_brute_force_with_overrides_and_promise():
    feeders = (Feeder("A", Tier.FLEXIBLE, FeederType.URBAN, 5, (100.0,) * 5),
               Feeder("B", Tier.NORMAL, FeederType.AGRICULTURAL, 300, (220.0,) * 5, (PromisedWindow(0, 5, 3),)),
               Feeder("C", Tier.NORMAL, FeederType.URBAN, 200, (180.0,) * 5))
    p = Problem(policy=tiny_policy(), feeders=feeders, shortfall_kw=(150.0, 200.0, 200.0, 100.0, 150.0),
                start_slot=2, forced_keep=frozenset({("C", 1)}), forced_cut=frozenset({("A", 0)}))
    check_against_brute_force(p)


def test_oracle_detects_infeasible_without_critical_approval():
    pol = pol8(limits={"system_switch_budget": 10})
    feeders = prob8(pol=pol).feeders                  # non-critical 1200 kW, critical 300 kW
    p = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8)
    assert solve_oracle(p, time_limit_s=10).status == "INFEASIBLE"
    ok = Problem(policy=pol, feeders=feeders, shortfall_kw=(1250.0,) * H8,
                 approvals=frozenset(("C1", t) for t in range(H8)))
    r = solve_oracle(ok, time_limit_s=10)
    assert r.status == "OPTIMAL" and verify(ok, r.schedule).ok and sum(r.schedule.cut["C1"]) == H8


def test_oracle_relaxes_max_downtime_only_when_strict_is_infeasible():
    pol = pol8(limits={"max_downtime_slots": 3})
    p = prob8(pol=pol)
    r = solve_oracle(p, time_limit_s=10)
    assert r.status == "OPTIMAL" and r.mode == "relaxed_downtime"
    v = verify(p, r.schedule)
    assert v.ok and v.rules("soft") == {"MAX_DOWNTIME"}
    assert solve_oracle(p, time_limit_s=10, relax_downtime_if_infeasible=False).status == "INFEASIBLE"
    easy = solve_oracle(prob8(), time_limit_s=10)         # default 4-slot limit is feasible
    assert easy.mode == "strict" and easy.status == "OPTIMAL"


def test_hint_does_not_change_the_optimum():
    from tests.helpers import GOOD
    p = prob8()
    a = solve_oracle(p, time_limit_s=10, workers=1)
    b = solve_oracle(p, time_limit_s=10, workers=1, hint=GOOD)
    assert a.objective == pytest.approx(b.objective, rel=1e-9) and a.objective <= compute_metrics(p, GOOD).total_harm + 1e-6


@pytest.mark.parametrize("seed", range(10))
def test_oracle_matches_brute_force_on_random_tiny_problems(seed):
    import random
    rng = random.Random(seed)
    pol = tiny_policy(limits={"feeder_switch_budget": rng.choice([1, 2, 3]),
                              "system_switch_budget": rng.choice([2, 3, 4, 5]),
                              "min_cut_slots": rng.choice([1, 2]), "min_restore_slots": rng.choice([1, 2, 3]),
                              "max_downtime_slots": rng.choice([2, 3, 4])},
                      shift={"shift_slots": rng.choice([3, 4, 6])})
    hist = {}
    for fid in "ABC":
        if rng.random() < 0.6:
            hist[fid] = FeederHistory(currently_cut=rng.random() < 0.4, run_slots=rng.randint(1, 4),
                                      switches_this_shift=rng.randint(0, 2))
    shortfall = tuple(float(rng.choice([0, 80, 120, 150, 200])) for _ in range(5))
    forced_keep = frozenset({("C", rng.randint(0, 4))}) if rng.random() < 0.3 else frozenset()
    p = tiny_problem(pol=pol, shortfall=shortfall, history=hist, forced_keep=forced_keep,
                     shift_offset=rng.randint(0, pol.shift_slots - 1))
    check_against_brute_force(p)
