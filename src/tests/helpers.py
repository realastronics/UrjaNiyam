"""Small hand-made grid used by every test. 8 slots, 4 feeders, S = 300 kW."""
from copy import deepcopy

from loadshed import (Feeder, FeederType, Policy, Problem, Schedule, Tier)

BASE_POLICY = {
    "time": {"slot_minutes": 15, "horizon_slots": 8, "commit_slots": 1},
    "shift": {"shift_slots": 8},
    "tiers": {"flexible": {"weight": 1.0}, "normal": {"weight": 5.0}, "critical": {"weight": 1000.0}},
    "limits": {"min_cut_slots": 2, "min_restore_slots": 2, "max_downtime_slots": 4,
               "feeder_switch_budget": 2, "system_switch_budget": 3},
    "margins": {"safety_margin": 0.0},
    "surge": {"base": 0.0, "per_slot": 0.0, "max": 0.0},
    "harm": {"beta": 0.5, "overshoot_lambda": 0.02, "switch_mu": 1.0, "time_of_day_weights": [1.0] * 24},
}
H = 8


def policy(**section_overrides) -> Policy:
    d = deepcopy(BASE_POLICY)
    for section, vals in section_overrides.items():
        d[section].update(vals)
    return Policy.from_dict(d)


def feeders(promises=None):
    promises = promises or {}
    spec = [("F1", Tier.FLEXIBLE, FeederType.INDUSTRIAL, 5, 500.0),
            ("N1", Tier.NORMAL, FeederType.URBAN, 400, 400.0),
            ("N2", Tier.NORMAL, FeederType.RURAL, 300, 300.0),
            ("C1", Tier.CRITICAL, FeederType.MIXED, 50, 300.0)]
    return tuple(Feeder(i, t, ft, n, (kw,) * H, tuple(promises.get(i, ()))) for i, t, ft, n, kw in spec)


def problem(pol=None, promises=None, **kw) -> Problem:
    return Problem(policy=pol or policy(), feeders=feeders(promises),
                   shortfall_kw=(300.0,) * H, **kw)


Z = (0,) * H


def sched(**rows) -> Schedule:
    """sched(N1=(1,1,1,1,0,0,0,0)) -> all other feeders never cut."""
    cut = {"F1": Z, "N1": Z, "N2": Z, "C1": Z}
    cut.update(rows)
    return Schedule(cut)


# Valid baseline: N1 covers slots 0-3 (400 kW), N2 covers slots 4-7 (300 kW).
GOOD = sched(N1=(1, 1, 1, 1, 0, 0, 0, 0), N2=(0, 0, 0, 0, 1, 1, 1, 1))
