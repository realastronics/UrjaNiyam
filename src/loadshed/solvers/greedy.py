"""Greedy baseline: the competitor every other solver must beat.

Slot by slot, it cuts the feeders with the lowest marginal harm per kW of relief until the
slot's requirement (S with margin, plus restoration surge) is covered.

Marginal harm = tier_weight * time_of_day * consumers * (1 + beta*(r-1))   [+ switch_mu if it must switch]
so a feeder that has been off for a while gets expensive and the cut rotates by itself.
Candidates are ranked by harm per kW that counts toward the REMAINING need (a feeder larger
than what is still needed is credited only for the remainder).

It respects the same rules the verifier checks (min cut/restore, critical approval, operator
overrides, switch budgets, promised supply). It is myopic: it never plans ahead, so it can
fail where an exact solver succeeds. That is the point of comparing against it.
"""
from __future__ import annotations

from time import perf_counter

from ..model import Problem, Schedule, Tier
from .base import SolveResult


def solve_greedy(problem: Problem, sticky: bool = False) -> SolveResult:
    """sticky=True: a feeder that is already cut stays cut (cost 0) until it hits max downtime, then rotates.
    That is plain manual rotational practice and saves switches. sticky=False rotates as soon as another
    feeder is cheaper, which can burn the switching budget."""
    t0 = perf_counter()
    pol, H = problem.policy, problem.policy.horizon_slots
    feeders = problem.feeders
    by_id = {f.id: f for f in feeders}

    state = {f.id: int(problem.hist(f.id).currently_cut) for f in feeders}
    run = {f.id: problem.hist(f.id).run_slots for f in feeders}
    f_sw = {f.id: problem.hist(f.id).switches_this_shift for f in feeders}
    sys_sw = sum(f_sw.values())
    on_so_far = {(f.id, i): 0 for f in feeders for i in range(len(f.promises))}
    rows: dict[str, list[int]] = {f.id: [] for f in feeders}
    short: list[int] = []
    relaxed: list[int] = []

    for t in range(H):
        if (problem.shift_offset + t) % pol.shift_slots == 0:
            f_sw = {fid: 0 for fid in f_sw}
            sys_sw = 0
        tod = pol.time_of_day_weights[problem.hour_of(t)]
        base_need = problem.shortfall_kw[t] * (1 + pol.safety_margin)

        # ---- 1. who is locked by a rule?
        locked_cut, locked_on, free = set(), set(), []
        for f in feeders:
            k = (f.id, t)
            if k in problem.forced_cut:
                locked_cut.add(f.id)
            elif k in problem.forced_keep:
                locked_on.add(f.id)
            elif f.tier == Tier.CRITICAL and k not in problem.approvals:
                locked_on.add(f.id)
            elif state[f.id] == 1 and run[f.id] < pol.min_cut_slots:
                locked_cut.add(f.id)
            elif state[f.id] == 0 and run[f.id] < pol.min_restore_slots:
                locked_on.add(f.id)
            else:
                free.append(f)

        def surge_of(fid: str) -> float:
            return by_id[fid].load_kw[t] * pol.surge_fraction(run[fid])

        def can_promise(f, lock: int) -> bool:
            for i, w in enumerate(f.promises):
                if w.start <= t < w.end:
                    if on_so_far[(f.id, i)] + max(0, w.end - (t + lock)) < w.min_on_slots:
                        return False
            return True

        # feeders that must come back now or break a supply promise: they get budget priority
        must_restore = {f.id for f in free if state[f.id] == 1 and not can_promise(f, 1)}

        def attempt(respect_downtime: bool):
            """Return (chosen set, covered?) for this slot."""
            chosen = set(locked_cut)
            pool: dict[str, float] = {}           # eligible feeder -> harm of cutting it this slot
            for f in free:
                if f.load_kw[t] <= 0:
                    continue
                if state[f.id] == 1:
                    if respect_downtime and run[f.id] >= pol.max_downtime_slots:
                        continue
                    if not can_promise(f, 1):
                        continue
                    r_new, sw_cost = run[f.id] + 1, 0.0
                else:
                    reserve = 2 if f.promises else 1          # start + the restore the promise will need
                    if f_sw[f.id] + reserve > pol.feeder_switch_budget or not can_promise(f, pol.min_cut_slots):
                        continue
                    r_new, sw_cost = 1, pol.switch_mu
                pool[f.id] = (pol.tier_weights[f.tier] * tod * f.consumers
                              * (1 + pol.harm_beta * (r_new - 1)) + sw_cost)
                if sticky and state[f.id] == 1:
                    pool[f.id] = 0.0                  # keep what is already cut; rotation only at the downtime cap

            def remaining() -> float:
                relief = sum(by_id[i].load_kw[t] for i in chosen)
                need = base_need + sum(surge_of(i) for i in state if state[i] == 1 and i not in chosen)
                return need - relief

            starts_used = sys_sw + sum(1 for i in locked_cut if state[i] == 0) \
                + sum(1 for i in locked_on if state[i] == 1) + len(must_restore)
            # promise feeders that are cut now will each need one restore switch later: keep it in reserve
            reserve_extra = sum(1 for f in feeders if state[f.id] == 1 and f.promises
                                and f.id not in must_restore)

            # Pick by harm per kW that actually counts toward what is STILL needed.
            # (A feeder bigger than the remaining need gets credit only for the remainder;
            #  without this the greedy wastes huge feeders on small shortfalls.)
            while (rem := remaining()) > 1e-6:
                best = None
                for fid, cost in pool.items():
                    if fid in chosen:
                        continue
                    promised = bool(by_id[fid].promises)
                    if state[fid] == 0 and starts_used + reserve_extra + (2 if promised else 1) > pol.system_switch_budget:
                        continue
                    gain = by_id[fid].load_kw[t] + (surge_of(fid) if state[fid] == 1 else 0.0)
                    key = (cost / min(gain, rem), fid)
                    if best is None or key < best[0]:
                        best = (key, fid)
                if best is None:
                    break
                fid = best[1]
                if state[fid] == 0:
                    starts_used += 1
                    reserve_extra += bool(by_id[fid].promises)
                chosen.add(fid)
            return chosen, remaining() <= 1e-6

        chosen, ok = attempt(respect_downtime=True)
        if not ok:
            chosen2, ok2 = attempt(respect_downtime=False)
            if ok2 or len(chosen2) > len(chosen):
                chosen, ok = chosen2, ok2
                relaxed.append(t)
        if not ok:
            short.append(t)

        # ---- 2. restores need switch budget too; if there is none, the feeder stays cut
        used = sys_sw + sum(1 for i in chosen if state[i] == 0)
        for i in sorted((i for i in state if state[i] == 1 and i not in chosen),
                        key=lambda i: (i not in must_restore, -by_id[i].consumers)):
            if i in problem.forced_keep:
                used += 1
            elif f_sw[i] < pol.feeder_switch_budget and used + 1 <= pol.system_switch_budget:
                used += 1
            else:
                chosen.add(i)
        used_total = sys_sw

        # ---- 3. apply the slot
        for f in feeders:
            new = 1 if f.id in chosen else 0
            if new != state[f.id]:
                f_sw[f.id] += 1
                used_total += 1
                state[f.id], run[f.id] = new, 1
            else:
                run[f.id] += 1
            rows[f.id].append(new)
            if new == 0:
                for i, w in enumerate(f.promises):
                    if w.start <= t < w.end:
                        on_so_far[(f.id, i)] += 1
        sys_sw = used_total

    sched = Schedule({fid: tuple(r) for fid, r in rows.items()})
    return SolveResult(sched, "greedy_sticky" if sticky else "greedy", perf_counter() - t0,
                       tuple(short), tuple(relaxed))
