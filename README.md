# UrjaNiyam

> Deciding what to cut, fairly, when the grid can't meet demand.

UrjaNiyam is a decision-support system for power-grid load shedding. Given a predicted shortfall and a grid's feeders (loads, priority classes, shedding history), it computes which feeders to cut, in what order, and for how long — treating the decision as a Constraint Satisfaction Problem, so hospitals and water systems are never cut, no single area is unfairly targeted, and disruption is minimized. An LLM layer explains the resulting schedule in plain language; it never makes the decision itself.

### Overview 
AI based decision-support system (CSP solver + simulation + LLM explanation layer) |

## Features

### Core Scheduler
- Formal CSP model: feeder × time-slot variables, on/off domain
- Two solvers implemented from scratch — backtracking + arc-consistency (AC-3), and min-conflicts local search — benchmarked against each other
- A* single-slot baseline for an informed-vs-uninformed search comparison (explicitly scoped as non-comparable to the full CSP — see `docs/CSP-spec.md`)
- Constraint-relaxation cascade for infeasible shortfalls — relaxes fairness bound → max-downtime → priority class, in that order, **never** the protected-feeder rule — with every relaxation logged
- Rolling-horizon replanning loop — re-solves on a fixed cadence or shortfall-delta threshold, using the live downtime ledger as an input

### Simulation, Evaluation & Explanation
- Synthetic-but-grounded grid topology and shortfall scenario generators (mild / moderate / crisis)
- Fairness metric (Gini coefficient / max-to-mean ratio) computed automatically every replan cycle
- Replanning-stability (plan-thrashing) and relaxation frequency/severity tracked as first-class metrics
- Read-only, evidence-grounded LLM explanation layer — answers operator queries strictly from the solver's constraint trace, never influences the decision *(planned — Phase 9)*

---

## Tech Stack

```
Language        Python 3.11+
Solvers         Backtracking + AC-3, Min-Conflicts local search, A* baseline — all from scratch
Data            pandas, numpy
Simulation      Custom grid topology + shortfall scenario generators
Visualization   matplotlib
Explanation     Anthropic API (planned, read-only against constraint trace)
Data sources    Grid-India / POSOCO, Kaggle demand-shortage compilation, hourly load dataset, data.gov.in
```

---

## Project Structure

```
UrjaNiyam/
│
├── csp/                    # Constraint compiler + backtracking/AC-3 and min-conflicts solvers
├── search/                 # A* single-slot baseline
├── kr/                     # Rule base — protected-load rules, relaxation-order policy
├── simulation/             # Grid topology generator, shortfall scenario generator
├── evaluation/             # Fairness metric, thrashing test, relaxation frequency/severity
├── explain/                # LLM explanation layer (planned — reads constraint trace only)
├── data/
│   ├── data-sources.md     # Confirmed sources, fields, access + license notes
│   ├── raw/
│   └── processed/
├── docs/
│   ├── CSP-spec.md         # Canonical formal problem definition
│   └── related-work.md     # Industry + academic CSP-scheduling literature
└── notebooks/               # EDA and exploratory analysis
```

### Key models (shared across all components)

| File | Purpose |
|---|---|
| `Feeder.py` | Feeder load, priority class, downtime ledger |
| `Schedule.py` | Computed cut schedule — feeder, time-slot, duration |
| `ConstraintTrace.py` | Solver's constraint-satisfaction trace + relaxation log |
| `ShortfallScenario.py` | Shortfall magnitude and severity tier over a time horizon |

> **Rule:** `kr/` and the model classes are shared contracts. No one edits them without a group discussion first — this is the CSP spec everyone else builds against.

---

## Getting Started

### Prerequisites

- Python 3.11+
- pip / virtualenv
- Jupyter (for EDA and notebooks)

Data sources and access notes for all datasets used in simulation live in `data/data-sources.md`.

---

## Branch Strategy

```
main                              ← stable, reviewed code only
 └── develop                      ← integration branch — everyone merges here first
      ├── feature/csp-solver/Parth
      ├── feature/simulation-data/Mitali
      └── feature/evaluation-explain/Mehak
```

## Team

| Name | Role |
|---|---|
| Parth | CSP solvers, KR&R rule base, architecture (Team Lead) |
| Mitali | Simulation, data pipeline & EDA, evaluation |
| Mehak | Literature review, LLM explanation layer, project coordination |

---

## Roadmap

- [x] Phase 0 — Problem scoping, formal proposal, and system architecture
- [ ] Phase 1 — Problem & data foundation *(current)*
- [ ] Phase 2 — Grid simulation & scenario generation
- [ ] Phase 3 — CSP solver development (backtracking + AC-3, min-conflicts, greedy baseline)
- [ ] Phase 4 — Rolling-horizon replanning & constraint-relaxation cascade
- [ ] Phase 5 — Evaluation, fairness & explainability (A*, scalability, thrashing, LLM layer)
- [ ] Phase 6 — Final analysis, report writing & demo preparation

---

*Built as part of an Artificial Intelligence course capstone project. Designed to be transparent, auditable, and demonstrably fairer than ad hoc manual load-shedding scheduling.*
