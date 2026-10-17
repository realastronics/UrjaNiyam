from pathlib import Path

import pytest

from loadshed import Policy, Problem, Tier
from tests.helpers import BASE_POLICY, H, feeders, policy

ROOT = Path(__file__).resolve().parents[1]


def test_real_policy_file_loads_with_agreed_defaults():
    p = Policy.from_yaml(ROOT / "configs" / "policy.yaml")
    assert (p.slot_minutes, p.horizon_slots, p.commit_slots) == (15, 16, 1)
    assert p.min_cut_slots == p.min_restore_slots == 2 and p.max_downtime_slots == 12
    assert p.feeder_switch_budget == 3 and p.system_switch_budget == 20
    assert p.tier_weights[Tier.CRITICAL] > p.tier_weights[Tier.NORMAL] > p.tier_weights[Tier.FLEXIBLE]


def test_commit_cannot_exceed_horizon():
    with pytest.raises(ValueError):
        policy(time={"commit_slots": 99})


def test_critical_must_be_the_heaviest_tier():
    with pytest.raises(ValueError):
        policy(tiers={"critical": {"weight": 2.0}})      # lower than normal (5.0)


def test_surge_fraction_is_capped():
    p = policy(surge={"base": 0.05, "per_slot": 0.1, "max": 0.3})
    assert p.surge_fraction(1) == pytest.approx(0.15) and p.surge_fraction(50) == 0.3


def test_problem_rejects_bad_inputs():
    with pytest.raises(ValueError):
        Problem(policy(), feeders(), (300.0,) * (H - 1))                       # wrong S length
    with pytest.raises(ValueError):
        Problem(policy(), feeders(), (300.0,) * H, forced_cut=frozenset({("N1", 0)}),
                forced_keep=frozenset({("N1", 0)}))                            # both forced
    with pytest.raises(ValueError):
        Problem(policy(), feeders(), (300.0,) * H, approvals=frozenset({("NOPE", 0)}))
