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


def _enumerate_rows(problem: Problem, f: Feeder, strict: bool, limit: int = 20000) -> list:
    """Every valid 16-slot row of this feeder, in depth-first order. strict=True also enforces max downtime."""
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
    return [r for r in rows if all(sum(1 - r[t] for t in range(w.start, w.end)) >= w.min_on_slots
                                   for w in f.promises)]


def enumerate_patterns(problem: Problem, f: Feeder, strict: bool, limit: int = 20000) -> Patterns:
    """REFERENCE implementation (slow, simple). strict=True also enforces max downtime (rung 0); False relaxes it."""
    return _evaluate(problem, f, _enumerate_rows(problem, f, strict, limit))


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
    """Drop pattern j if a cheaper-or-equal pattern i relieves at least as much in every slot and
    switches no more in every slot (so it can never hurt the cover or the system budget).
    Sorted by cost, each pattern is only compared with the still-alive, more expensive ones.
    (Missing a dominated pattern is harmless; removing a useful one would not be, and cannot happen here.)"""
    order = np.argsort(p.cost, kind="stable")
    p = p.take(order)
    n = len(p)
    alive = np.ones(n, dtype=bool)
    for i in range(n - 1):
        if not alive[i]:
            continue
        rest = np.nonzero(alive[i + 1:])[0] + i + 1
        if len(rest) == 0:
            break
        dom = ((p.net[i] >= p.net[rest] - eps).all(axis=1)
               & (p.ev[i] <= p.ev[rest]).all(axis=1) & (p.nfev[i] <= p.nfev[rest]).all(axis=1))
        alive[rest[dom]] = False
    return p.take(np.nonzero(alive)[0])


# ---------------------------------------------------------------------------------------------
# Fast path. Same patterns as enumerate_patterns + prune_dominated (tests check this), much quicker:
#   * the valid ROWS depend only on a feeder's history, overrides, promises and tier, not on its load,
#     consumers or weights, so each distinct situation is enumerated once and cached (also across loop cycles);
#   * cost and net relief are then array arithmetic per feeder;
#   * whether pattern i dominates pattern j depends only on the rows' structure (and on which slots have load),
#     so that yes/no matrix is cached too; after sorting by cost, j is dominated iff some earlier i dominates it.
# ---------------------------------------------------------------------------------------------
@dataclass
class _Struct:
    rows: np.ndarray     # (P, H) int8
    a: np.ndarray        # (P, H) cut - restoration-surge fraction: net relief = a * load
    hf: np.ndarray       # (P, H) outage-length factor 1 + beta*(r-1) where cut, else 0
    ev: np.ndarray
    nfev: np.ndarray
    n_sw: np.ndarray     # (P,) switches


_STRUCT_CACHE: dict = {}
_DOM_CACHE: dict = {}
_CACHE_MAX = 512
_MAX_DOM_CELLS = 40_000_000      # above this P*P*H, fall back to the reference pruning


def clear_pattern_cache() -> None:
    _STRUCT_CACHE.clear()
    _DOM_CACHE.clear()


def _feeder_key(problem: Problem, f: Feeder, strict: bool):
    pol, H = problem.policy, problem.policy.horizon_slots
    h = problem.hist(f.id)
    cut0 = bool(h.currently_cut)
    run_key = h.run_slots if cut0 else min(h.run_slots, pol.min_restore_slots)     # an ON run only matters up to min_restore
    sw_key = min(h.switches_this_shift, pol.feeder_switch_budget + 1)             # beyond the budget every switch is illegal
    fcut = tuple(t for t in range(H) if (f.id, t) in problem.forced_cut)
    fkeep = tuple(t for t in range(H) if (f.id, t) in problem.forced_keep)
    crit = tuple(t for t in range(H) if f.tier == Tier.CRITICAL and (f.id, t) not in problem.approvals
                 and (f.id, t) not in problem.forced_cut)
    promises = tuple((w.start, w.end, w.min_on_slots) for w in f.promises)
    pol_key = (pol.min_cut_slots, pol.min_restore_slots, pol.max_downtime_slots, pol.feeder_switch_budget,
               pol.shift_slots, problem.shift_offset, H, pol.harm_beta, pol.surge_base, pol.surge_per_slot, pol.surge_max)
    return (strict, cut0, run_key, sw_key, fcut, fkeep, crit, promises, pol_key)


def _build_struct(problem: Problem, f: Feeder, strict: bool) -> _Struct:
    pol, H = problem.policy, problem.policy.horizon_slots
    h = problem.hist(f.id)
    rows = _enumerate_rows(problem, f, strict)
    P = len(rows)
    a, hf = np.zeros((P, H)), np.zeros((P, H))
    ev, nfev = np.zeros((P, H), dtype=np.int8), np.zeros((P, H), dtype=np.int8)
    forced = {t for t in range(H) if (f.id, t) in problem.forced_cut or (f.id, t) in problem.forced_keep}
    for i, r in enumerate(rows):
        prev, run = (1 if h.currently_cut else 0), h.run_slots
        for t in range(H):
            cur = r[t]
            if cur != prev:
                ev[i, t] = 1
                nfev[i, t] = 0 if t in forced else 1
                if prev == 1:
                    a[i, t] -= pol.surge_fraction(run)          # surge when restored
                prev, run = cur, 1
            else:
                run += 1
            if cur == 1:
                a[i, t] += 1.0
                hf[i, t] = 1 + pol.harm_beta * (run - 1)
    return _Struct(np.array(rows, dtype=np.int8).reshape(P, H), a, hf, ev, nfev, ev.sum(axis=1))


def _dominance(key, support: tuple, st: _Struct):
    """D[i, j] = pattern i dominates pattern j: at least as much net relief in every slot that has load,
    and no more switches in any slot."""
    dk = (key, support)
    D = _DOM_CACHE.get(dk)
    if D is None:
        cols = [t for t, on in enumerate(support) if on]
        a = st.a[:, cols]
        D = ((a[:, None, :] >= a[None, :, :] - 1e-12).all(axis=2)
             & (st.ev[:, None, :] <= st.ev[None, :, :]).all(axis=2)
             & (st.nfev[:, None, :] <= st.nfev[None, :, :]).all(axis=2))
        if len(_DOM_CACHE) >= _CACHE_MAX // 4:
            _DOM_CACHE.clear()
        _DOM_CACHE[dk] = D
    return D


def _fast_feeder(problem: Problem, f: Feeder, strict: bool, tod: np.ndarray) -> Patterns:
    pol, H = problem.policy, problem.policy.horizon_slots
    key = _feeder_key(problem, f, strict)
    st = _STRUCT_CACHE.get(key)
    if st is None:
        st = _build_struct(problem, f, strict)
        if len(_STRUCT_CACHE) >= _CACHE_MAX:
            _STRUCT_CACHE.clear()
        _STRUCT_CACHE[key] = st
    P = len(st.rows)
    if P == 0:
        return Patterns(st.rows, np.zeros(0), np.zeros((0, H)), st.ev, st.nfev)
    L = np.asarray(f.load_kw, dtype=float)
    net = st.a * L
    cost = (st.hf @ tod) * (pol.tier_weights[f.tier] * f.consumers) + pol.switch_mu * st.n_sw \
        + pol.overshoot_lambda * net.sum(axis=1)
    order = np.argsort(cost, kind="stable")
    if P * P * max(1, int((L > 0).sum())) > _MAX_DOM_CELLS:           # huge pattern sets: use the reference pruning
        return prune_dominated(Patterns(st.rows, cost, net, st.ev, st.nfev))
    D = _dominance(key, tuple((L > 0).tolist()), st)
    dominated = np.triu(D[np.ix_(order, order)], 1).any(axis=0)
    keep = order[~dominated]
    return Patterns(st.rows[keep], cost[keep], net[keep], st.ev[keep], st.nfev[keep])


def build_patterns(problem: Problem, strict: bool, fast: bool = True) -> list[Patterns]:
    """Valid, non-dominated patterns for every feeder (in feeder order). A feeder with no valid pattern
    shows up as an empty Patterns; callers treat that as 'infeasible'. Build once, share between solvers.
    fast=False uses the simple reference implementation (used by the tests to check the fast one)."""
    if not fast:
        return [prune_dominated(enumerate_patterns(problem, f, strict)) for f in problem.feeders]
    pol, H = problem.policy, problem.policy.horizon_slots
    tod = np.array([pol.time_of_day_weights[problem.hour_of(t)] for t in range(H)])
    return [_fast_feeder(problem, f, strict, tod) for f in problem.feeders]
