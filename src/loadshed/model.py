"""Core data types. No solver logic lives here."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .policy import Policy

LONG_AGO = 10**6  # "has been in this state for a very long time"


class Tier(str, Enum):
    FLEXIBLE = "F"   # shed first
    NORMAL = "N"
    CRITICAL = "C"   # shed last, only with operator approval


class FeederType(str, Enum):
    URBAN = "urban"
    RURAL = "rural"
    AGRICULTURAL = "agricultural"
    INDUSTRIAL = "industrial"
    MIXED = "mixed"   # carries a critical consumer; tag it with its highest tier


@dataclass(frozen=True)
class PromisedWindow:
    """Feeder must be ON for at least `min_on_slots` slots within plan slots [start, end).
    The caller (simulator) already subtracts any hours delivered before slot 0."""
    start: int
    end: int
    min_on_slots: int


@dataclass(frozen=True)
class Feeder:
    id: str
    tier: Tier
    ftype: FeederType
    consumers: int
    load_kw: tuple[float, ...]            # estimated load L_f(t), one value per slot
    promises: tuple[PromisedWindow, ...] = ()


@dataclass(frozen=True)
class FeederHistory:
    """What happened to this feeder before slot 0 (the downtime ledger entry)."""
    currently_cut: bool = False
    run_slots: int = LONG_AGO             # slots spent in the current state, up to now
    switches_this_shift: int = 0          # breaker operations so far in the current shift


@dataclass(frozen=True)
class Problem:
    """One planning instance: everything the solver and the verifier are given."""
    policy: "Policy"
    feeders: tuple[Feeder, ...]
    shortfall_kw: tuple[float, ...]       # S(t), one value per slot
    history: dict[str, FeederHistory] = field(default_factory=dict)
    shift_offset: int = 0                 # slots already elapsed in the current shift
    forced_cut: frozenset[tuple[str, int]] = frozenset()    # operator: cut this feeder in this slot
    forced_keep: frozenset[tuple[str, int]] = frozenset()   # operator: keep this feeder ON
    approvals: frozenset[tuple[str, int]] = frozenset()     # operator approves a Critical cut
    start_slot: int = 0                   # slot-of-day at plan slot 0 (used for time-of-day harm)

    def __post_init__(self) -> None:
        H = self.policy.horizon_slots
        ids = [f.id for f in self.feeders]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate feeder ids")
        if len(self.shortfall_kw) != H:
            raise ValueError(f"shortfall_kw needs {H} values")
        if any(s < 0 for s in self.shortfall_kw):
            raise ValueError("shortfall must be >= 0")
        if not 0 <= self.shift_offset < self.policy.shift_slots:
            raise ValueError("shift_offset must be in [0, shift_slots)")
        for f in self.feeders:
            if len(f.load_kw) != H or any(x < 0 for x in f.load_kw):
                raise ValueError(f"{f.id}: load_kw needs {H} non-negative values")
            if f.consumers < 0:
                raise ValueError(f"{f.id}: consumers must be >= 0")
            for w in f.promises:
                if not (0 <= w.start < w.end <= H) or w.min_on_slots > w.end - w.start:
                    raise ValueError(f"{f.id}: bad promised window {w}")
        known = set(ids)
        for name in ("forced_cut", "forced_keep", "approvals"):
            for fid, t in getattr(self, name):
                if fid not in known or not 0 <= t < H:
                    raise ValueError(f"{name}: bad entry {(fid, t)}")
        if self.forced_cut & self.forced_keep:
            raise ValueError("a feeder-slot cannot be both forced cut and forced keep")
        for fid in self.history:
            if fid not in known:
                raise ValueError(f"history for unknown feeder {fid}")

    def hour_of(self, t: int) -> int:
        """Clock hour (0-23) of plan slot t."""
        return ((self.start_slot + t) * self.policy.slot_minutes // 60) % 24

    def hist(self, feeder_id: str) -> FeederHistory:
        return self.history.get(feeder_id, FeederHistory())


@dataclass(frozen=True)
class Schedule:
    """cut[feeder_id][t] = 1 if the feeder is cut in slot t, else 0."""
    cut: dict[str, tuple[int, ...]]

    def committed(self, k: int) -> dict[str, tuple[int, ...]]:
        """The part we actually act on (first k slots); the rest is tentative."""
        return {fid: row[:k] for fid, row in self.cut.items()}
