"""All valid 16-slot on/off PATTERNS of one feeder.

Why patterns: the only things that couple feeders are (a) the per-slot relief sums and (b) the
system-wide switching budget. Everything else (min cut / min restore, per-feeder switch budget with
shift resets, max downtime, promised supply, operator forced slots, critical approval, history)
depends on ONE feeder only. So we enumerate each feeder's valid rows once, and the search then
picks one row per feeder.

Each pattern carries, as plain numbers:
  cost = harm + switch_mu * switches + overshoot_lambda * sum_t net[t]
  net[t] = relief[t] - restoration_surge[t]        (what the pattern contributes to the relief sum)
  ev[t]  = 1 if the feeder switches in slot t;  nfev[t] = same but 0 at operator-forced slots
Total objective = sum of pattern costs  -  overshoot_lambda * sum_t need[t]   (a constant).

Enumeration uses the same rules as verify(); tests compare it against verify() on every possible row.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..model import Feeder, Problem, Tier


@dataclass
class Patterns:
    rows: np.ndarray    # (P, H) int8, 1 = cut
    cost: np.ndarray    # (P,)
    net: np.ndarray     # (P, H)
    ev: np.ndarray      # (P, H) int8
    nfev: np.ndarray    # (P, H) int8

    def __len__(self) -> int:
        return int(self.rows.shape[0])

    def take(self, idx: np.ndarray) -> "Patterns":
        return Patterns(self.rows[idx], self.cost[idx], self.net[idx], self.ev[idx], self.nfev[idx])


class TooManyPatterns(RuntimeError):
    pass


def enumerate_patterns(problem: Problem, f: Feeder, strict: bool, limit: int = 20000) -> Patterns:
    """strict=True also enforces max downtime (rung 0); strict=False relaxes it (rung 1)."""
    pol, H = problem.policy, problem.policy.horizon_slots
    h = problem.hist(f.id)
    prev0 = 1 if h.currently_cut else 0
    fcut = [(f.id, t) in problem.forced_cut for t in range(H)]
    fkeep = [(f.id, t) in problem.forced_keep for t in range(H)]
    forced = [a or b for a, b in zip(fcut, fkeep)]
    crit_blocked = [f.tier == Tier.CRITICAL and (f.id, t) not in problem.approvals and not fcut[t]
                    for t in range(H)]
    rows: list[tuple[int, ...]] = []
    row = [0] * H

    def rec(t: int, prev: int, run: int, sw: int) -> None:
        if t == H:
            rows.append(tuple(row))
            if len(rows) > limit:
                raise TooManyPatterns(f"{f.id}: more than {limit} patterns")
            return
        if (problem.shift_offset + t) % pol.shift_slots == 0:
            sw = 0
        for cur in (0, 1):
            if (fcut[t] and cur == 0) or (fkeep[t] and cur == 1) or (cur == 1 and crit_blocked[t]):
                continue
            nsw, nrun = sw, run + 1
            if cur != prev:
                if not forced[t]:
                    if prev == 1 and run < pol.min_cut_slots:
                        continue
                    if prev == 0 and run < pol.min_restore_slots:
                        continue
                nsw = sw + 1
                if nsw > pol.feeder_switch_budget and not forced[t]:
                    continue
                nrun = 1
            if cur == 1 and strict and nrun > pol.max_downtime_slots:
                continue
            row[t] = cur
            rec(t + 1, cur, nrun, nsw)

    rec(0, prev0, h.run_slots, h.switches_this_shift)
    rows = [r for r in rows if all(sum(1 - r[t] for t in range(w.start, w.end)) >= w.min_on_slots
                                   for w in f.promises)]
    return _evaluate(problem, f, rows)


def _evaluate(problem: Problem, f: Feeder, rows: list[tuple[int, ...]]) -> Patterns:
    pol, H = problem.policy, problem.policy.horizon_slots
    h = problem.hist(f.id)
    w = pol.tier_weights[f.tier]
    P = len(rows)
    cost, net = np.zeros(P), np.zeros((P, H))
    ev, nfev = np.zeros((P, H), dtype=np.int8), np.zeros((P, H), dtype=np.int8)
    tod = [pol.time_of_day_weights[problem.hour_of(t)] for t in range(H)]
    for i, r in enumerate(rows):
        prev, run, harm = (1 if h.currently_cut else 0), h.run_slots, 0.0
        for t in range(H):
            cur = r[t]
            if cur != prev:
                ev[i, t] = 1
                nfev[i, t] = 0 if ((f.id, t) in problem.forced_cut or (f.id, t) in problem.forced_keep) else 1
                if prev == 1:
                    net[i, t] -= f.load_kw[t] * pol.surge_fraction(run)     # surge when restored
                prev, run = cur, 1
            else:
                run += 1
            if cur == 1:
                net[i, t] += f.load_kw[t]
                harm += w * tod[t] * f.consumers * (1 + pol.harm_beta * (run - 1))
        cost[i] = harm + pol.switch_mu * ev[i].sum() + pol.overshoot_lambda * net[i].sum()
    return Patterns(np.array(rows, dtype=np.int8).reshape(P, H), cost, net, ev, nfev)


def prune_dominated(p: Patterns, eps: float = 1e-9) -> Patterns:
    """Drop pattern j if another pattern i is no more costly, relieves at least as much in every slot,
    and switches no more in every slot (so it can never hurt the cover or the system budget)."""
    order = np.argsort(p.cost, kind="stable")
    p = p.take(order)
    n = len(p)
    keep = np.ones(n, dtype=bool)
    for i in range(n):
        if not keep[i]:
            continue
        dom = ((p.cost[i] <= p.cost + eps) & (p.net[i] >= p.net - eps).all(axis=1)
               & (p.ev[i] <= p.ev).all(axis=1) & (p.nfev[i] <= p.nfev).all(axis=1))
        dom[i] = False
        # if j also dominates i and j came first, j stays (it will have removed i already if it can)
        back = ((p.cost <= p.cost[i] + eps) & (p.net >= p.net[i] - eps).all(axis=1)
                & (p.ev <= p.ev[i]).all(axis=1) & (p.nfev <= p.nfev[i]).all(axis=1))
        back[i:] = False
        dom &= ~back
        keep &= ~dom
    return p.take(np.nonzero(keep)[0])
