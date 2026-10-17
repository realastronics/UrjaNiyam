"""Policy = every tunable number, loaded from YAML and validated."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .model import Tier


@dataclass(frozen=True)
class Policy:
    slot_minutes: int
    horizon_slots: int
    commit_slots: int
    shift_slots: int
    tier_weights: dict[Tier, float]
    min_cut_slots: int
    min_restore_slots: int
    max_downtime_slots: int          # soft limit
    feeder_switch_budget: int
    system_switch_budget: int
    safety_margin: float
    surge_base: float
    surge_per_slot: float
    surge_max: float
    harm_beta: float
    overshoot_lambda: float
    switch_mu: float
    time_of_day_weights: tuple[float, ...]

    def surge_fraction(self, outage_slots: int) -> float:
        """Extra load, as a fraction of normal load, in the slot right after a restore."""
        return min(self.surge_max, self.surge_base + self.surge_per_slot * outage_slots)

    # ---------- loading ----------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "Policy":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh))

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Policy":
        t, s, ti, li = d["time"], d["shift"], d["tiers"], d["limits"]
        ma, su, ha = d["margins"], d["surge"], d["harm"]
        p = cls(
            slot_minutes=int(t["slot_minutes"]),
            horizon_slots=int(t["horizon_slots"]),
            commit_slots=int(t["commit_slots"]),
            shift_slots=int(s["shift_slots"]),
            tier_weights={
                Tier.FLEXIBLE: float(ti["flexible"]["weight"]),
                Tier.NORMAL: float(ti["normal"]["weight"]),
                Tier.CRITICAL: float(ti["critical"]["weight"]),
            },
            min_cut_slots=int(li["min_cut_slots"]),
            min_restore_slots=int(li["min_restore_slots"]),
            max_downtime_slots=int(li["max_downtime_slots"]),
            feeder_switch_budget=int(li["feeder_switch_budget"]),
            system_switch_budget=int(li["system_switch_budget"]),
            safety_margin=float(ma["safety_margin"]),
            surge_base=float(su["base"]),
            surge_per_slot=float(su["per_slot"]),
            surge_max=float(su["max"]),
            harm_beta=float(ha["beta"]),
            overshoot_lambda=float(ha["overshoot_lambda"]),
            switch_mu=float(ha["switch_mu"]),
            time_of_day_weights=tuple(float(x) for x in ha["time_of_day_weights"]),
        )
        p._validate()
        return p

    def _validate(self) -> None:
        def need(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(f"policy: {msg}")

        need(self.slot_minutes > 0 and self.horizon_slots > 0, "slot_minutes and horizon_slots must be > 0")
        need(1 <= self.commit_slots <= self.horizon_slots, "commit_slots must be in [1, horizon_slots]")
        need(self.shift_slots > 0, "shift_slots must be > 0")
        w = self.tier_weights
        need(0 < w[Tier.FLEXIBLE] < w[Tier.NORMAL] < w[Tier.CRITICAL],
             "weights must satisfy 0 < flexible < normal < critical (critical is shed last)")
        need(self.min_cut_slots >= 1 and self.min_restore_slots >= 1, "min cut/restore slots must be >= 1")
        need(self.max_downtime_slots >= self.min_cut_slots, "max_downtime_slots must be >= min_cut_slots")
        need(self.feeder_switch_budget >= 0 and self.system_switch_budget >= 0, "switch budgets must be >= 0")
        need(self.safety_margin >= 0, "safety_margin must be >= 0")
        need(0 <= self.surge_base <= self.surge_max and self.surge_per_slot >= 0, "bad surge settings")
        need(len(self.time_of_day_weights) == 24, "time_of_day_weights needs 24 values")
