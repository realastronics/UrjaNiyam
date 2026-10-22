# Urja Niyam: load-shedding decision support

Recommends which feeders to cut, when, and for how long, after the shortfall S(t) is known.
The dispatcher always has the final word.

## Layout
```
configs/policy.yaml     every tunable number (tiers, limits, switching budgets, margins)
src/loadshed/model.py   data types: Feeder, FeederHistory, Problem, Schedule
src/loadshed/policy.py  loads and validates the YAML
src/loadshed/metrics.py the objective (harm) every solver and the oracle share
src/loadshed/solvers/   greedy.py (plain + sticky baselines), patterns.py (valid 16-slot patterns per feeder),
                        bnb.py (branch-and-bound: bounds propagation + Lagrangian lower bound, from scratch),
                        minconflicts.py (weighted min-conflicts local search, from scratch)
src/loadshed/verify.py  independent verifier (no solver code, plain loops)
src/loadshed/sim/       synthetic grid (grid.py), shortfall scenarios (scenarios.py), Zenodo ratios (zenodo.py)
scripts/                build_scenario_tiers.py -> data/processed/scenarios/tiers.json
evaluation/             oracle_cpsat.py (CP-SAT answer key, OFFLINE ONLY), run_gap.py (greedy vs oracle benchmark)
tests/                  one test per hard rule + history/shift/override cases, grid and data tests
docs/                   data-sources.md, field-constraints.md (to be written)
```
Planned next: relaxation ladder with operator-approval flow, rolling-horizon loop, scale sweep to 500 feeders, `eval/` (CP-SAT oracle lives here, offline only).

## Run
```
pip install -e ".[dev,data,oracle]"
pytest
python scripts/build_scenario_tiers.py      # needs the Zenodo CSVs in data/raw/
python -m evaluation.run_gap --seeds 2 --hours 10 17   # baselines + our solvers vs CP-SAT oracle (add --sizes 10 50; --ls-time, --bnb-time)
```
`ortools` is installed only with `pip install -e ".[oracle]"` and is never imported from `src/loadshed`.

## Conventions
- Slot = 15 min. Horizon = 16 slots. Only the first slot is committed, then we replan.
- `x[f][t] = 1` means feeder f is CUT in slot t.
- `FeederHistory` is the ledger: current state, slots in that state, switches this shift.
- A rule broken at a slot the operator forced goes to `waived`, not `hard`. Relief is never waived.
