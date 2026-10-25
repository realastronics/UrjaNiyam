"""The fast pattern builder must give EXACTLY the patterns of the simple reference builder (same rows, same costs,
same relief, same switch events), for every feeder, in every situation."""
import random
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from loadshed import FeederHistory, Policy, Tier
from loadshed.sim import build_problem, make_grid, make_scenario, slot_of_day
from loadshed.solvers.patterns import build_patterns, clear_pattern_cache
from tests.test_patterns import one_feeder_problem

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")


@pytest.fixture(autouse=True)
def fresh_cache():
    clear_pattern_cache()
    yield
    clear_pattern_cache()


def same(a, b):
    ka = {r.tobytes(): i for i, r in enumerate(a.rows)}
    kb = {r.tobytes(): i for i, r in enumerate(b.rows)}
    if set(ka) != set(kb):
        return False
    ia, ib = [ka[k] for k in kb], [kb[k] for k in kb]
    return all(np.allclose(getattr(a, f)[ia], getattr(b, f)[ib], rtol=1e-9, atol=1e-7)
               for f in ("cost", "net", "ev", "nfev"))


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("seed", range(25))
def test_single_feeder_problems_with_histories_overrides_and_promises(seed, strict):
    p = one_feeder_problem(seed)
    fast, ref = build_patterns(p, strict), build_patterns(p, strict, fast=False)
    assert same(fast[0], ref[0])


def messy_problem(n, seed):
    """A grid problem with random histories, operator overrides and approvals."""
    rng = random.Random(seed)
    base = build_problem(make_grid(n, seed), POL, make_scenario("t", 0.07), slot_of_day(rng.choice([3, 10, 17])),
                         episode_offset=8)
    H, ids = POL.horizon_slots, [f.id for f in base.feeders]
    hist = {i: FeederHistory(rng.random() < 0.3, rng.randint(1, 15), rng.randint(0, 4)) for i in ids if rng.random() < 0.6}
    fk = frozenset((rng.choice(ids), rng.randrange(H)) for _ in range(6))
    fc = frozenset((rng.choice(ids), rng.randrange(H)) for _ in range(4)) - fk
    crit = [f.id for f in base.feeders if f.tier == Tier.CRITICAL]
    ap = frozenset((c, t) for c in crit for t in range(H) if rng.random() < 0.3)
    return replace(base, history=hist, forced_keep=fk, forced_cut=fc, approvals=ap, shift_offset=rng.randrange(POL.shift_slots))


@pytest.mark.parametrize("seed", range(4))
def test_grids_with_random_histories_overrides_and_approvals(seed):
    p = messy_problem(30, seed)
    for strict in (True, False):
        fast, ref = build_patterns(p, strict), build_patterns(p, strict, fast=False)
        assert all(same(a, b) for a, b in zip(fast, ref))


def test_a_warm_cache_gives_the_same_answer_as_a_cold_one():
    p = messy_problem(30, 7)
    cold = build_patterns(p, True)
    warm = build_patterns(p, True)                         # now served from the cache
    assert all(same(a, b) for a, b in zip(cold, warm))
    other_loads = build_patterns(messy_problem(30, 8), True)   # a different grid reuses structure, not numbers
    ref = build_patterns(messy_problem(30, 8), True, fast=False)
    assert all(same(a, b) for a, b in zip(other_loads, ref))


def test_kept_patterns_are_non_dominated_and_pruning_removes_some():
    from loadshed.solvers.patterns import enumerate_patterns
    p = messy_problem(20, 3)
    fast = build_patterns(p, True)
    removed = 0
    for f, x in zip(p.feeders, fast):
        removed += len(enumerate_patterns(p, f, True)) - len(x)          # a feeder may legitimately have no pattern at all
        for i in range(len(x)):                                           # sorted by cost: only LATER patterns can be dominated
            dom = ((x.cost[i] <= x.cost + 1e-9) & (x.net[i] >= x.net - 1e-9).all(axis=1)
                   & (x.ev[i] <= x.ev).all(axis=1) & (x.nfev[i] <= x.nfev).all(axis=1))
            assert not (dom & (np.arange(len(x)) > i)).any()
    assert removed > 0


def test_it_is_much_faster_than_the_reference():
    p = build_problem(make_grid(60, 0), replace(POL, system_switch_budget=36), make_scenario("t", 0.07),
                      slot_of_day(17), episode_offset=8)
    t = time.perf_counter(); build_patterns(p, True, fast=False); ref_s = time.perf_counter() - t
    clear_pattern_cache()
    t = time.perf_counter(); build_patterns(p, True); fast_s = time.perf_counter() - t
    assert fast_s * 4 < ref_s, (fast_s, ref_s)
