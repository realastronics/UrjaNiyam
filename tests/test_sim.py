import json
from pathlib import Path
import pytest

from loadshed import Policy, Schedule, Tier, verify
from loadshed.sim import (build_problem, load_tiers, make_grid, make_scenario, slot_of_day, trapezoid,)

ROOT = Path(__file__).resolve().parents[1]
POL = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
EVENING = slot_of_day(17)

# grid
def test_grid_is_deterministic_and_seed_sensitive():
    assert make_grid(50, seed=1) == make_grid(50, seed=1)
    assert make_grid(50, seed=1) != make_grid(50, seed=2)

@pytest.mark.parametrize("n", [10, 50, 200, 500])
def test_grid_sizes_and_tiers(n):
    g = make_grid(n, seed=3)
    tiers = [s.tier for s in g.specs]
    assert len(g) == n and len({s.id for s in g.specs}) == n
    assert tiers.count(Tier.CRITICAL) >= 1
    assert tiers.count(Tier.FLEXIBLE) >= 1
    assert tiers.count(Tier.NORMAL) > n / 2
    assert all(s.base_kw > 0 for s in g.specs)

def test_too_small_grid_rejected():
    with pytest.raises(ValueError):
        make_grid(2)

# ---------- scenarios
def test_trapezoid_shape():
    s = trapezoid(32, 4)
    assert len(s) == 32 and max(s) == 1.0
    assert 0 < s[0] < 1 and 0 < s[-1] < 1
    assert s[16] == 1.0 and all(0 <= x <= 1 for x in s)

def test_scenario_rejects_bad_ratio():
    with pytest.raises(ValueError):
        make_scenario("x", 1.0)

# ---------- build_problem
@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("start", [EVENING, slot_of_day(3), slot_of_day(10), slot_of_day(22)])
def test_built_problems_are_valid(seed, start):
    p = build_problem(make_grid(50, seed), POL, make_scenario("t", 0.08), start)
    assert len(p.shortfall_kw) == POL.horizon_slots
    assert max(p.shortfall_kw) > 0

@pytest.mark.parametrize("hour", [0, 3, 17, 23])
def test_build_problem_preserves_start_time(hour):
    start = slot_of_day(hour, slot_minutes=POL.slot_minutes)
    p = build_problem(make_grid(6, seed=0), POL, make_scenario("test", 0.05), start)
    assert p.start_slot == start
    for t in range(POL.horizon_slots):
        assert p.hour_of(t) == ((start + t) * POL.slot_minutes // 60) % 24

def test_shortfall_scales_with_ratio_and_ends_with_episode():
    g = make_grid(50, 0)
    lo = build_problem(g, POL, make_scenario("lo", 0.02), EVENING)
    hi = build_problem(g, POL, make_scenario("hi", 0.10), EVENING)
    assert all(h >= l for h, l in zip(hi.shortfall_kw, lo.shortfall_kw))
    assert sum(hi.shortfall_kw) > 4 * sum(lo.shortfall_kw)
    done = build_problem(
        g, POL, make_scenario("hi", 0.10, episode_slots=32), EVENING, episode_offset=40,
    )
    assert set(done.shortfall_kw) == {0.0}

def test_agricultural_feeders_off_outside_block_and_promised_inside():
    p = build_problem(make_grid(200, 0), POL, make_scenario("t", 0.05), EVENING)
    ag = [f for f in p.feeders if f.ftype.value == "agricultural"]
    assert ag and any(f.promises for f in ag)
    assert any(not f.promises and sum(f.load_kw) == 0 for f in ag)
    for f in ag:
        for w in f.promises:
            assert all(f.load_kw[t] > 0 for t in range(w.start, w.end))

def test_mild_shortfall_is_coverable_by_non_critical_load():
    p = build_problem(make_grid(50, 0), POL, make_scenario("mild", 0.05), EVENING)
    for t in range(POL.horizon_slots):
        sheddable = sum(f.load_kw[t] for f in p.feeders if f.tier != Tier.CRITICAL)
        assert sheddable >= p.shortfall_kw[t] * (1 + POL.safety_margin)

def test_verifier_rejects_the_do_nothing_schedule_on_a_real_problem():
    p = build_problem(make_grid(10, 0), POL, make_scenario("t", 0.08), EVENING)
    nothing = Schedule({f.id: (0,) * POL.horizon_slots for f in p.feeders})
    r = verify(p, nothing)
    assert not r.ok
    assert r.rules("hard") <= {"RELIEF", "PROMISED_SUPPLY"}
    assert "RELIEF" in r.rules("hard")

# ---------- tiers.json
def test_load_tiers(tmp_path):
    f = tmp_path / "tiers.json"
    thresholds = {"mild": 0.02, "moderate": 0.06, "crisis": 0.15}
    f.write_text(json.dumps({"thresholds": thresholds}), encoding="utf-8")
    t = load_tiers(f)
    assert list(t) == ["mild", "moderate", "crisis"]
    assert t["crisis"].peak_ratio == 0.15