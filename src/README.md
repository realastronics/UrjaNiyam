# Urja Niyam: load-shedding decision support

Recommends which feeders to cut, when, and for how long, after the shortfall S(t) is known.
The dispatcher always has the final word.

## Layout
```
configs/policy.yaml     every tunable number (tiers, limits, switching budgets, margins)
src/loadshed/model.py   data types: Feeder, FeederHistory, Problem, Schedule
src/loadshed/policy.py  loads and validates the YAML
src/loadshed/verify.py  independent verifier (no solver code, plain loops)
tests/                  one test per hard rule + history/shift/override cases
docs/                   data-sources.md, field-constraints.md (to be written)
```
Planned next: `src/loadshed/sim/`, `src/loadshed/solvers/`, `eval/` (CP-SAT oracle lives here, offline only).

## Run
```
pip install -e ".[dev]"
pytest
```
`ortools` is installed only with `pip install -e ".[oracle]"` and is never imported from `src/loadshed`.

## Conventions
- Slot = 15 min. Horizon = 16 slots. Only the first slot is committed, then we replan.
- `x[f][t] = 1` means feeder f is CUT in slot t.
- `FeederHistory` is the ledger: current state, slots in that state, switches this shift.
- A rule broken at a slot the operator forced goes to `waived`, not `hard`. Relief is never waived.
