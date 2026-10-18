from __future__ import annotations

from dataclasses import dataclass

from ..model import Schedule


@dataclass(frozen=True)
class SolveResult:
    """What every solver returns. The solver's own claims are NOT trusted: run verify()."""
    schedule: Schedule
    solver: str
    runtime_s: float
    short_slots: tuple[int, ...] = ()     # slots where the solver itself could not cover the relief
    relaxed_slots: tuple[int, ...] = ()   # slots where it let a feeder exceed max downtime
