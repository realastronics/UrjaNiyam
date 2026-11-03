"""Synthetic grid: feeders with tiers, types, sizes and daily load shapes.

EVERYTHING numeric in this file is a documented PLACEHOLDER (shares, sizes, profiles),
to be calibrated later from tariff orders / discom documents (see docs/field-constraints.md).
Same seed -> same grid.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..model import Feeder, FeederHistory, FeederType, Problem, PromisedWindow, Tier
from ..policy import Policy
from .scenarios import Scenario

# ---- placeholder assumptions --------------------------------------------------------
TIER_SHARE = {Tier.CRITICAL: 0.05, Tier.FLEXIBLE: 0.15}      # the rest is NORMAL

TYPE_MIX = {                                                  # feeder type given tier
    Tier.CRITICAL: ((FeederType.MIXED, 0.7), (FeederType.RURAL, 0.3)),     # hospital area / water pumping
    Tier.FLEXIBLE: ((FeederType.INDUSTRIAL, 1.0),),
    Tier.NORMAL:   ((FeederType.URBAN, 0.45), (FeederType.RURAL, 0.35), (FeederType.AGRICULTURAL, 0.20)),
}

# consumers (low, high) and reference kW per consumer
SIZE = {
    FeederType.URBAN:        (800, 3000, 1.0),
    FeederType.RURAL:        (300, 1500, 0.7),
    FeederType.AGRICULTURAL: (100, 600, 4.0),
    FeederType.INDUSTRIAL:   (5, 60, 90.0),
    FeederType.MIXED:        (1000, 4000, 1.0),
}

# load multiplier by hour of day (0..23). Reference kW x multiplier = load in that hour.
_URBAN = [0.45] * 6 + [0.60, 0.65, 0.65] + [0.65] * 8 + [0.85] + [1.0] * 4 + [0.8, 0.6]
_RURAL = [0.30] * 6 + [0.45] * 3 + [0.40] * 8 + [0.70] + [1.0] * 4 + [0.7, 0.45]
_INDUS = [0.55] * 6 + [0.80] * 2 + [0.95] * 10 + [0.85] * 4 + [0.70, 0.60]
PROFILE = {FeederType.URBAN: _URBAN, FeederType.MIXED: _URBAN, FeederType.RURAL: _RURAL,
           FeederType.INDUSTRIAL: _INDUS}
assert all(len(p) == 24 for p in PROFILE.values())

# agricultural supply blocks (start hour, end hour), wrapping past midnight allowed
AG_BLOCKS = ((6, 14), (14, 22), (22, 6))
AG_PROMISE_SHARE = 0.75          # at least 75% of the block slots inside the horizon must be ON
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FeederSpec:
    id: str
    tier: Tier
    ftype: FeederType
    consumers: int
    base_kw: float
    ag_block: tuple[int, int] | None = None


@dataclass(frozen=True)
class Grid:
    specs: tuple[FeederSpec, ...]
    seed: int

    def __len__(self) -> int:
        return len(self.specs)


def slot_of_day(hour: int, minute: int = 0, slot_minutes: int = 15) -> int:
    return (hour * 60 + minute) // slot_minutes


def _in_block(hour: int, block: tuple[int, int]) -> bool:
    s, e = block
    return s <= hour < e if s < e else (hour >= s or hour < e)


def _tier_counts(n: int) -> dict[Tier, int]:
    if n < 3:
        raise ValueError("need at least 3 feeders (one per tier)")
    crit = max(1, round(n * TIER_SHARE[Tier.CRITICAL]))
    flex = max(1, round(n * TIER_SHARE[Tier.FLEXIBLE]))
    return {Tier.CRITICAL: crit, Tier.FLEXIBLE: flex, Tier.NORMAL: n - crit - flex}


def make_grid(n_feeders: int, seed: int = 0) -> Grid:
    rng = random.Random(seed)
    specs: list[FeederSpec] = []
    for tier, count in _tier_counts(n_feeders).items():
        types, weights = zip(*TYPE_MIX[tier])
        for _ in range(count):
            ft = rng.choices(types, weights)[0]
            lo, hi, kwpc = SIZE[ft]
            consumers = rng.randint(lo, hi)
            base = round(consumers * kwpc * rng.lognormvariate(0.0, 0.15), 1)
            block = rng.choice(AG_BLOCKS) if ft == FeederType.AGRICULTURAL else None
            specs.append(FeederSpec("", tier, ft, consumers, base, block))
    rng.shuffle(specs)                                   # no tier ordering bias
    width = len(str(n_feeders - 1))
    specs = [FeederSpec(f"f{i:0{width}d}", s.tier, s.ftype, s.consumers, s.base_kw, s.ag_block)
             for i, s in enumerate(specs)]
    return Grid(tuple(specs), seed)


def _hour(policy: Policy, start_slot: int, t: int) -> int:
    return ((start_slot + t) * policy.slot_minutes // 60) % 24


def feeder_loads(spec: FeederSpec, policy: Policy, start_slot: int) -> tuple[float, ...]:
    """Estimated load L_f(t) for each slot of the horizon (kW)."""
    out = []
    for t in range(policy.horizon_slots):
        h = _hour(policy, start_slot, t)
        if spec.ftype == FeederType.AGRICULTURAL:
            out.append(spec.base_kw if _in_block(h, spec.ag_block) else 0.0)
        else:
            out.append(round(spec.base_kw * PROFILE[spec.ftype][h], 1))
    return tuple(out)


def _promises(spec: FeederSpec, policy: Policy, start_slot: int) -> tuple[PromisedWindow, ...]:
    if spec.ag_block is None:
        return ()
    inside = [t for t in range(policy.horizon_slots)
              if _in_block(_hour(policy, start_slot, t), spec.ag_block)]
    if not inside:
        return ()
    start, end = inside[0], inside[-1] + 1
    return (PromisedWindow(start, end, math.ceil(AG_PROMISE_SHARE * (end - start))),)


def build_problem(grid: Grid, policy: Policy, scenario: Scenario, start_slot: int, *,
                  episode_offset: int = 0,
                  history: dict[str, FeederHistory] | None = None,
                  shift_offset: int = 0,
                  forced_cut: frozenset = frozenset(),
                  forced_keep: frozenset = frozenset(),
                  approvals: frozenset = frozenset()) -> Problem:
    """One planning instance for the horizon starting at `start_slot` of the day.

    S(t) = scenario.peak_ratio * scenario.shape[episode_offset + t] * total_demand(t).
    Past the end of the episode the shortfall is 0.
    """
    H = policy.horizon_slots
    feeders = tuple(
        Feeder(s.id, s.tier, s.ftype, s.consumers, feeder_loads(s, policy, start_slot),
               _promises(s, policy, start_slot)) for s in grid.specs)
    demand = [sum(f.load_kw[t] for f in feeders) for t in range(H)]
    shape = [scenario.shape[episode_offset + t] if episode_offset + t < len(scenario.shape) else 0.0
             for t in range(H)]
    shortfall = tuple(round(scenario.peak_ratio * shape[t] * demand[t], 1) for t in range(H))
    return Problem(policy=policy, feeders=feeders, shortfall_kw=shortfall,
                   history=history or {}, shift_offset=shift_offset,
                   forced_cut=forced_cut, forced_keep=forced_keep, approvals=approvals, start_slot=start_slot)
