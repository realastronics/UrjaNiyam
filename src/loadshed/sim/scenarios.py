"""Shortfall scenarios.

A scenario = a peak shortfall RATIO (share of demand that cannot be served)
           + a SHAPE over the episode (0..1 per slot).
S(t) = peak_ratio * shape[t] * demand(t).

The ratio comes from the Zenodo data (see zenodo.py). The shape is a documented
synthetic trapezoid, because the Zenodo data is daily and cannot give intra-day shape.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    name: str
    peak_ratio: float
    shape: tuple[float, ...]       # one value in [0, 1] per slot of the episode


def trapezoid(n_slots: int, ramp_slots: int = 4) -> tuple[float, ...]:
    """Ramp up, hold at 1.0, ramp down. Ramps shrink if the episode is short."""
    if n_slots < 1:
        raise ValueError("n_slots must be >= 1")
    ramp = max(0, min(ramp_slots, n_slots // 2))
    out = []
    for t in range(n_slots):
        up = (t + 1) / (ramp + 1) if ramp else 1.0
        down = (n_slots - t) / (ramp + 1) if ramp else 1.0
        out.append(min(1.0, up, down))
    return tuple(out)


def make_scenario(name: str, peak_ratio: float, episode_slots: int = 32,
                  ramp_slots: int = 4) -> Scenario:
    if not 0 <= peak_ratio < 1:
        raise ValueError("peak_ratio must be in [0, 1)")
    return Scenario(name, peak_ratio, trapezoid(episode_slots, ramp_slots))


def load_tiers(path: str | Path, episode_slots: int = 32,
               ramp_slots: int = 4) -> dict[str, Scenario]:
    """Read data/processed/scenarios/tiers.json (written by scripts/build_scenario_tiers.py)."""
    with open(path, "r", encoding="utf-8") as fh:
        blob = json.load(fh)
    return {name: make_scenario(name, float(r), episode_slots, ramp_slots)
            for name, r in blob["thresholds"].items()}
