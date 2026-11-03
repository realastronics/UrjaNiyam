# Maintainability and consistency audit

Reviewed from commit `9acf55166c2963095ae2de7b2e431f1aea63b401` (3 November 2026).
Scope: Python source, evaluation scripts, tests, configuration, README and repository layout.
The literature PDFs, notebook outputs and historical benchmark conclusions were not independently validated.

## Assessment

The core has useful boundaries: model, policy, verifier, objective, solver, simulator and orchestration.
The largest files implement actual search or stateful simulation, not merely plumbing. Turning them into
50-line files would mostly hide logic or remove checks. Keep the independent verifier and the simple
pattern reference: their duplication is deliberate, and cross-checking them is valuable.

The main avoidable complexity is repeated orchestration, repeated baseline searches and experimental
reporting. No production modules were merged: fewer files would not improve these boundaries. Small
one-off experiment scripts remain candidates for a future unified experiment CLI, but that should preserve
reproducible experiment configurations rather than just move their code into one giant file.

## Changes in this branch

- Use `dataclasses.replace` for solver result updates instead of copying positional fields. Collapse the
  local-search force-relaxed return path and remove redundant best/start state.
- Consolidate ladder solver dispatch while retaining the existing B&B-to-local-search fallback.
- Use `itertools.groupby` for consecutive-state episode metrics instead of a manual run accumulator.
- Replace repeated late-binding lambdas in robustness jobs with `partial` and explicit experiment variants.
  Compared 84 deferred jobs with the original: identical keys, ordering and complete configurations.
- Explain all first-slot cuts against one baseline: **1 + k solves instead of 1 + 2k**, for k cut feeders.
  Handle missing schedules explicitly. This also prevents stochastic re-solves from silently changing
  the baseline used for each reported price.
- Remove the duplicate relaxed-mode B&B search after forced-relaxed infeasibility.
- Replace three repetitive gap-reporting sections with one strict-mode comparison table. Keep the CSV
  schema unchanged. Exclude relaxed plans/bounds from strict comparisons and retain zero objectives.
  Undefined percentage ratios are reported as unavailable. Select the oracle hint only from valid strict
  greedy candidates. Console output intentionally changes; old parsing of the printed tables must migrate.
- Make gap/hybrid modules importable without launching experiments. Load the optional oracle only when
  requested in hybrid/scale runs. Remove unnecessary oracle-based skipping from native-only test suites.
- Rename tracked `Data/` to `data/`, matching all CLI defaults and `.gitignore`. No dataset contents changed.
- Correct stale README paths/planned-work descriptions, the sensitivity experiment's 20/50-feeder label,
  the robustness experiment count, and the claim that every solver returns `SolveResult`.

## Outstanding findings, by priority

### High: system-switch verification depends on feeder order

`src/loadshed/verify.py::_check_system_switches` increments the shared count in feeder order and waives an
excess only for the particular forced feeder being processed. B&B/local search instead check the count of
non-forced switches against the budget before adding all switches in the slot. CP-SAT uses an ordered
per-feeder prefix. These are different interpretations of an override.

Reproduced with system budget 1, no shortfall, F1 forced to cut in slot 0, and F1/N1 both switching to cut:
`(F1, N1, N2, C1)` produces a hard `SYSTEM_SWITCH_BUDGET` violation on N1;
reversing feeder order produces no hard violation, only a waived violation on F1.
The physical schedule is identical. Decide whether forced operations take precedence or are accounted
last, then implement that same rule independently in all engines and add permutation-invariance tests.
This branch deliberately does not change the override policy.

### High: scenario construction loses the cost-model start time

`src/loadshed/sim/grid.py::build_problem` passes `start_slot` into loads and promises but omits it from
`Problem(...)`. `Problem.start_slot` therefore defaults to zero. Reproduced: a 17:00 request (slot 68)
produces `p.start_slot == 0` and `p.hour_of(0) == 0`. Loads reflect evening while harm weights reflect midnight.
`loop.py` passes the start slot correctly, so single-horizon benchmarks and rolling episodes disagree.
Fix the constructor argument, add a clock-hour regression and regenerate affected benchmarks; do not
silently compare their old results with new runs. Left separate from the behavior-preserving refactors.

### High: the scale report still combines different feasible sets

`evaluation/run_scale.py` permits solver downtime relaxation, records only B&B's mode, and combines LS,
B&B and CP-SAT harms/bounds in `summarize`. A relaxed solution cannot establish a strict upper bound.
The gap report is repaired in this branch; the scale CSV needs new `ls_mode`/`cpsat_mode` fields and a
migration or regeneration of old files before equivalent repair is trustworthy. In `run_hybrid.py`,
zero objectives are also dropped by truthiness checks in its report; use explicit None checks and define
zero-denominator presentation there too.

### Medium: ladder prose overstates what a timeout proves

`ladder.py::_finish` says no plan satisfies all rules / coverage requires Critical feeders even when
lower-rung attempts are `NOT_FOUND`, rather than `PROVEN`. `_escalate_messages` similarly says the rules
conflict even when the zero-shortfall search merely found nothing. The attempt metadata is more cautious
than the user-facing prose. Report "no plan found within the search budget" unless infeasibility was proven.
"Best coverage reachable" is also too strong when bisection uses heuristic failures to lower its upper bound.

### Medium: runtime and reproducibility contracts are inconsistent

- Local search reserves 30% of its limit even when fallback relaxation is disabled; hybrid compensates by
  dividing its strict LS allocation by 0.7. B&B's limit excludes setup; ladder applies a minimum 0.5 seconds
  per attempt, may invoke fallback searches, and has a separate 1-second zero-plan attempt. Its supplied
  time limit is therefore not a wall-clock deadline.
- Time-limited searches can stop at different iterations despite identical seeds. Paired benchmark
  differences must not automatically be attributed to feedback/failure policy. Use an iteration/node
  budget for reproducibility studies or measure repeated-run variability.
- `--resume` identifies rows by scenario keys but does not fingerprint policy, solver budgets, episode
  length, input tiers or code version. Changing settings can silently mix incompatible runs. Store and
  validate experiment metadata before resuming.

### Medium: explanations and advertised capabilities need clearer limits

`what_if` compares objectives across whatever rung each call returns. A price is not a strict optimal
marginal cost when modes differ or either search is heuristic; show status/mode/optimality alongside it.
`reasons` is a rule heuristic, not a proof of causality, and its switching-budget reason does not account
for a shift reset at slot zero. The repository description advertises prediction and fairness, whereas
current code consumes a supplied shortfall and optimizes harm without a fairness constraint. README now
states the implemented scope. Forecast-error experiments are not a forecasting model.

### Lower priority

- Several scratch evaluation scripts execute on import; gap/hybrid are fixed, but forecast, sensitivity,
  budget/determinism checks, plotting and robustness analysis should eventually get explicit entrypoints.
- Direct `Policy(...)` and `dataclasses.replace` bypass `_validate`; Problem/history validation also does
  not reject all non-finite values or invalid history counts. Define supported input guarantees before
  consolidating validation.
- Most default paths are relative to the working directory; run commands from the repository root.
- No CI workflow is tracked. Add core tests plus an optional oracle job before relying on a passing local
  suite as a continuous guarantee.

## Validation

- Original full suite: **372 passed** (214.63 seconds).
- Refactored core full suite: **372 passed** (205.68 seconds), including CP-SAT comparisons.
- Final targeted checks after report/entrypoint edits: **10 passed**, including four new regressions.
- 84 deferred robustness jobs matched the original configurations and ordering exactly.
- Gap and hybrid CLI smoke runs completed across all three tiers.
- Hybrid and scale CLI smoke runs completed with imports of OR-Tools explicitly blocked and the oracle disabled.
- Compilation and `git diff --check` passed.

These timings are validation durations, not a performance benchmark. Full multi-seed benchmark regeneration
was not performed; stored CSVs remain historical results. New regressions cover baseline reuse, absent
schedules, forced-relaxed retry suppression, and strict-only/zero-valued comparison inputs.
