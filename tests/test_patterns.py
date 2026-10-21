"""Pattern enumeration must equal 'every row the verifier accepts for this one feeder'."""
import itertools
import random
from dataclasses import replace

import numpy as np
import pytest

from loadshed import Feeder, FeederHistory, FeederType, Problem, PromisedWindow, Schedule, Tier, verify
from loadshed.metrics import compute_metrics
from loadshed.solvers.patterns import enumerate_patterns, prune_dominated
from tests.test_oracle import tiny_policy

H = 5


def one_feeder_problem(seed):
    rng = random.Random(seed)
    pol = tiny_policy(limits={"feeder_switch_budget": rng.choice([1, 2, 3]), "min_cut_slots": rng.choice([1, 2]),
                              "min_restore_slots": rng.choice([1, 2, 3]), "max_downtime_slots": rng.choice([2, 3, 4])},
                      shift={"shift_slots": rng.choice([3, 4, 6])})
    pol = replace(pol, system_switch_budget=1000)                       # isolate the single-feeder rules
    tier = rng.choice([Tier.NORMAL, Tier.NORMAL, Tier.CRITICAL])
    promises = (PromisedWindow(0, H, rng.choice([2, 3])),) if rng.random() < 0.3 else ()
    f = Feeder("A", tier, FeederType.URBAN, 100, tuple(float(rng.choice([50, 120, 200])) for _ in range(H)), promises)
    hist = {"A": FeederHistory(currently_cut=rng.random() < 0.4, run_slots=rng.randint(1, 4),
                               switches_this_shift=rng.randint(0, 2))} if rng.random() < 0.7 else {}
    t = rng.randint(0, H - 1)
    fc = frozenset({("A", t)}) if rng.random() < 0.2 else frozenset()
    fk = frozenset({("A", (t + 1) % H)}) if rng.random() < 0.2 and not fc else frozenset()
    ap = frozenset(("A", u) for u in range(H)) if tier == Tier.CRITICAL and rng.random() < 0.5 else frozenset()
    return Problem(policy=pol, feeders=(f,), shortfall_kw=(0.0,) * H, history=hist, forced_cut=fc, forced_keep=fk,
                   approvals=ap, shift_offset=rng.randint(0, pol.shift_slots - 1), start_slot=2)


@pytest.mark.parametrize("strict", [True, False])
@pytest.mark.parametrize("seed", range(25))
def test_patterns_equal_rows_the_verifier_accepts(seed, strict):
    p = one_feeder_problem(seed)
    want = set()
    for row in itertools.product((0, 1), repeat=H):
        v = verify(p, Schedule({"A": row}))
        if not (v.rules("hard") - {"RELIEF"}) and not (strict and v.soft):
            want.add(row)
    got = enumerate_patterns(p, p.feeders[0], strict)
    assert {tuple(int(x) for x in r) for r in got.rows} == want


@pytest.mark.parametrize("seed", range(25))
def test_pattern_cost_and_net_match_metrics(seed):
    p = one_feeder_problem(seed)
    pat = enumerate_patterns(p, p.feeders[0], False)
    c0 = -p.policy.overshoot_lambda * sum(s * (1 + p.policy.safety_margin) for s in p.shortfall_kw)
    for i in range(len(pat)):
        if (pat.net[i] < 0).any():
            continue                      # metrics clips negative overshoot; linear cost is only exact when net >= 0
        s = Schedule({"A": tuple(int(x) for x in pat.rows[i])})
        assert compute_metrics(p, s).total_harm == pytest.approx(pat.cost[i] + c0, abs=1e-6)


@pytest.mark.parametrize("seed", range(10))
def test_dominance_never_removes_the_cheapest_cover(seed):
    p = one_feeder_problem(seed)
    pat = enumerate_patterns(p, p.feeders[0], False)
    kept = prune_dominated(pat)
    if len(pat) == 0:                     # a feeder can have no valid pattern at all (infeasible instance)
        assert len(kept) == 0
        return
    assert 0 < len(kept) <= len(pat)
    rng = random.Random(seed)
    for _ in range(20):                    # for any per-slot requirement, the best cost is unchanged
        need = np.array([rng.choice([0, 50, 100, 200]) for _ in range(H)], float)
        def best(P):
            ok = (P.net >= need - 1e-9).all(axis=1)
            return P.cost[ok].min() if ok.any() else np.inf
        assert best(kept) == pytest.approx(best(pat))
