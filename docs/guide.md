# Urja Niyam: Project Guide

Urja Niyam was built to help decide which areas should temporarily lose electricity when there was not enough power to supply everyone. We treated this as a planning problem: cover the shortage, respect the operating rules, and reduce the modeled impact of the cuts.

Imagine a town needed 100 units of power, but only 80 were available. Disconnecting 20 units of demand sounded simple until we considered who would lose supply. One area might contain a hospital. Another might already have been without power for an hour. A third might need a guaranteed supply period. The cheapest immediate choice could also leave us with a difficult situation later.

We built a system that considered these decisions together. It included a simulated electricity network, several scheduling algorithms, an independent schedule checker, a replanning loop, operator instructions, explanations, and a browser demo. The work combined **knowledge representation, constraint satisfaction, heuristic search, optimization, and planning under uncertainty**.

## 1. The problem we addressed

We worked at the **rotational load-shedding planning layer**. The system received an estimated shortage over the coming hours and proposed which supply groups to disconnect and restore.

A supply group was called a *feeder* in the code: a shared electricity line serving a group of consumers. Throughout this guide, “group” refers to that switchable unit. The system could disconnect a whole group, but could not select individual houses within it.

This distinction mattered. If the remaining shortage was 200 kW and the smallest suitable group used 300 kW, cutting it removed more demand than necessary. Choosing a schedule therefore involved both coverage and the cost of unnecessary cuts.

We did not build demand forecasting, emergency under-frequency protection, or a physical power-flow model. We did not model transmission topology, line voltages, or equipment protection. The shortage was an input, and the operating rules were assumptions supplied to the planner.

The intended user was a control-room operator. The prototype recommended decisions and exposed their consequences; it did not control real switches. Operator approval and command execution were represented through software callbacks and simulation.

## 2. How we turned the problem into a model

We formulated a **constraint optimization problem**. A constraint satisfaction problem defined variables, their possible values, and rules that valid assignments had to satisfy. Optimization added a preference: among acceptable schedules, which had the lowest cost?

Our basic variable was:

```text
x[group, slot] = 0 if supplied, 1 if disconnected
```

By default, a slot lasted 15 minutes and the planning horizon contained 16 slots, or four hours. A problem with 50 groups therefore contained 800 basic on/off decisions.

Each group had an estimated load for every slot, a consumer count, a priority, a type, and any promised supply requirements. Priorities were Flexible, Normal, and Critical. Types included residential-like, agricultural, industrial, and mixed demand profiles.

The model also included **history**: whether the group was currently disconnected, how long it had remained in that state, and how many switching operations it had used during the shift. Without history, a new plan could incorrectly treat a continuing outage as if it had just begun.

### The operating rules

The relief from disconnected groups had to cover the shortage with the configured safety margin. Restoration could temporarily increase demand, so the model also accounted for a restoration surge.

Minimum cut and restoration durations prevented rapid switching. Per-group and system-wide switching budgets limited operations during a shift. Promised supply requirements protected designated supply periods. Critical groups required approval before a proposed cut could become an authorized action.

Maximum consecutive downtime was enforced during strict search. It could later be relaxed through a defined process, with the excess reported.

Operator instructions forced particular groups on or off. Some conflicting operating rules could be recorded as waived at forced actions, rather than silently treated as satisfied. Required relief was not waived simply because an operator instruction made it difficult to achieve.

The switching-budget implementation also distinguished forced actions from ordinary choices. Forced actions were handled explicitly and still affected the remaining history and future switching capacity.

### The objective we minimized

We called the cost **harm**. It represented a policy preference, not money or measured human suffering.

The outage component increased with priority weight, time-of-day weight, consumer count, and uninterrupted outage length:

```text
outage cost =
    priority weight × time weight × consumers
    × (1 + growth factor × (consecutive cut slots − 1))
```

The planning objective added penalties for excess relief and switching operations.

For example, with a Normal-group weight of 5, a time weight of 1, and 1,000 consumers, the default growth factor produced outage costs of 5,000, 7,500, 10,000, and 12,500 across four consecutive slots.

That four-slot outage accumulated 35,000 outage cost. Two separate two-slot outages accumulated 25,000 before considering extra switches, restoration surge, and other constraints. This encouraged rotation where it was feasible.

It did not guarantee fairness across households or across weeks. A group’s assigned priority and consumer count influenced its treatment, and those choices remained policy judgments.

### The main defaults

The default policy used 15-minute slots, a four-hour horizon, and eight-hour shifts. Minimum cut and restoration durations were two slots each. Maximum downtime was twelve slots, or three hours.

Priority weights were 1, 5, and 1,000 for Flexible, Normal, and Critical groups. The default safety margin was 5%. The restoration-surge model started at 5%, increased by one percentage point per outage slot, and was capped at 30%.

The default switching allowance was three operations per group and twenty system-wide per shift. Scale experiments increased the system allowance with grid size using `max(20, ceil(0.6 × number of groups))`.

These were modeling settings, not established operating standards for a real utility. They determined the problem being solved and therefore shaped the results.

## 3. Where the inputs came from

Historical state-level electricity data informed the **size of the shortage scenarios**. It did not supply real group-level switching histories or four-hour operating schedules.

The earlier data preparation used met and unmet peak-demand series from the Hunt and Bloomfield dataset described in the project’s source notes. It converted these into a shortage ratio:

```text
shortage ratio = unmet demand / (met demand + unmet demand)
```

The preparation excluded aggregate and non-state columns, handled missing values and duplicate dates, and rejected suspect extreme ratios. Corrected met-demand values were preferred where available.

Scenario percentiles were calculated from days with nonzero unmet demand. The selected levels were approximately **2.96%, 6.84%, and 20%**, labeled mild, moderate, and crisis.

These labels were experimental categories. A pooled percentile across states and years did not establish how often a particular local electricity network would experience that shortage.

The source data was daily. We therefore created the within-day shortage shape ourselves: a trapezoid that rose, held, and fell. Individual supply groups, their loads, consumer counts, histories, and agricultural supply blocks were also synthetic.

A random seed fixed the generated grid so that different algorithms could face the same instance. This supported controlled comparisons, but did not make every time-limited solver run identical.

## 4. How the architecture worked

The architecture followed a repeated sequence: represent the current situation, search for a schedule, check it, apply one simulated step, and update the history.

`model.py` defined the shared objects, including the problem, schedule, groups, and history. `policy.py` loaded and validated settings from `configs/policy.yaml`. `metrics.py` contained objective and reporting calculations.

The `sim/` package created synthetic grids and shortage scenarios. Policy settings and generator assumptions were kept distinct: not every synthetic-data choice lived in the YAML file.

The algorithms lived in `solvers/`. They proposed schedules, while `verify.py` checked schedules independently of their search logic. `ladder.py` controlled relaxation and approval steps. `loop.py` connected planning with simulated execution. `whatif.py` compared operator instructions and produced basic reasons.

The `evaluation/` directory contained experiments, the CP-SAT comparison model, plotting, and demo export. The `tests/` directory checked rules, algorithms, and interactions.

We implemented the system in **Python**, with **NumPy** for array calculations and **PyYAML** for configuration. **Pytest** supported testing, **Matplotlib** produced plots, and **Google OR-Tools CP-SAT** provided an offline comparison solver. The demo used HTML and JavaScript with exported JSON.

There was no trained neural network or language model in the scheduling path.

The diagram:

flowchart TD
    Inputs["Estimated shortage and group loads"] --> Problem["Build planning problem"]
    Policy["Policy, priorities and operating limits"] --> Problem
    History["Ledger of actual states and switches"] --> Problem
    Operator["Operator instructions and approvals"] --> Problem

    Problem --> Ladder["Relaxation ladder"]
    Ladder --> Search["Search for a schedule"]
    Search --> Verify["Independent schedule checker"]
    Verify --> Decision{"Acceptable outcome?"}

    Decision -->|"Checked plan"| Execute["Simulate the first 15-minute step"]
    Decision -->|"Approval required"| Operator
    Decision -->|"No acceptable plan found"| Escalate["Report escalation and uncovered demand"]

    Execute --> History
    Execute --> Logs["Record actions, costs and unmet demand"]
    Logs --> Demo["Browser replay and evaluation"]

    Problem -.-> WhatIf["What-if comparison"]
    WhatIf -.-> Ladder
    WhatIf --> Explain["Cost differences and basic rule reasons"]

## 5. How the scheduling algorithms worked

We implemented several methods because speed, solution quality, and proof were different goals. A fast acceptable schedule could be useful even when proving it optimal was too expensive.

### Greedy baselines

The greedy methods made locally attractive choices while moving through the slots. They considered immediate harm and useful relief, accounting for decisions already fixed by constraints.

One version preferred retaining existing cuts. This reduced some switching but could extend outages or leave less flexibility later.

The baseline comparison used the better valid schedule from the two methods. Validity mattered: a cheap schedule that broke the rules was not a successful baseline.

The main weakness was limited look-ahead. A good choice now could consume switching capacity or create a costly outage later.

### Pattern-based representation

Most rules concerned one group’s behavior over time. We used that structure to generate valid **patterns**: complete on/off rows for individual groups across the planning horizon.

Instead of searching directly over every binary decision, the algorithms selected one pattern per group. Individual duration, history, approval, and related restrictions were handled during pattern construction. The remaining coordination centered on total relief and shared switching capacity.

Patterns carried cost, net relief after restoration effects, and switching information. Caching reusable structures and using array operations reduced repeated setup work. A simpler reference builder supported checks on the optimized implementation.

This reduced the work done during search, but did not remove the combinatorial difficulty of choosing compatible patterns.

### Branch-and-bound

Our branch-and-bound solver explored a tree of partial schedules. Each branch selected a pattern for a group.

It first applied **constraint propagation**. Suppose a slot required 1,000 kW, while two groups could provide at most 900 kW together. A third group then had to provide at least 100 kW. Patterns that could not meet that necessary contribution were removed.

Such removals could trigger further removals, so propagation continued until no more changes followed. An empty domain or an impossible coverage requirement ended that branch.

The solver then calculated a **lower bound** using Lagrangian relaxation. It temporarily priced the shared relief requirements, allowing groups to be evaluated more independently. This easier problem supplied an optimistic estimate of the best cost obtainable from that branch.

If the bound could not beat the best complete schedule already found—the **incumbent**—the branch was pruned. Variable ordering used regret between attractive alternatives, and value ordering explored cheaper choices first.

A completed search could prove optimality or infeasibility for the represented problem. A time-limited search could instead return a feasible incumbent, a bound, or no found solution. These outcomes were deliberately distinguished.

### Weighted min-conflicts local search

Local search worked with a complete assignment of patterns and improved it through small changes.

Its score combined harm with penalties for unmet relief and switching-budget conflicts. It considered changes to groups that could help resolve a conflict and selected promising moves.

**Breakout weights** increased penalties when violations persisted. A **tabu mechanism** discouraged immediate cycling. Restarts perturbed the search, and penalty adjustment allowed exploration beyond the current feasible solution before repairing violations.

Temporary search states could be invalid. The schedule returned for use still had to pass verification.

Local search generally offered practical schedules on larger instances, but did not normally prove optimality or infeasibility.

### The hybrid

The hybrid ran local search first and handed its result to branch-and-bound as a warm start. A better incumbent allowed exact search to prune more branches.

This explained its purpose, but did not guarantee superiority. Dividing a fixed budget between methods could perform differently from giving either method the entire budget.

### CP-SAT as an offline comparison

We encoded a comparison model using OR-Tools CP-SAT. It provided another implementation against which our schedules could be evaluated.

CP-SAT used integer-scaled quantities. Rounding constraints and objective coefficients meant its numerical model was not identical to the original one. Returned schedules were checked against our model, but its optimality claim referred to its own integer formulation.

For that reason, the revised reports retained CP-SAT bounds for inspection but did not combine them with B&B bounds as if they certified exactly the same optimization problem.

## 6. Verification, relaxation, and simulated execution

The independent verifier checked schedule shape, state changes, durations, switching budgets, relief, approvals, overrides, and promised supply requirements. It reported hard violations, soft violations, and recorded waivers separately.

This separation reduced dependence on any single search algorithm. It did not prove that the model matched the real world or eliminate the possibility of a checker bug.

When strict planning did not succeed, the **relaxation ladder** tried defined alternatives. It first relaxed maximum downtime. A later stage could propose Critical cuts requiring explicit approval. If no acceptable plan was found, it escalated and reported a best-effort coverage estimate.

A higher rung did not automatically mean the lower rung was mathematically impossible. The search might simply have exhausted its budget. The ladder recorded this distinction. Its `proven` field concerned lower-rung failure claims, rather than proof that a returned plan minimized harm.

The final coverage estimate used repeated searches over a shortage-scaling factor. It was a best effort under the method and budget, not necessarily the maximum coverage any schedule could achieve.

### The rolling-horizon loop

The loop planned four hours ahead but committed only the first 15-minute action. It then updated the ledger and planned again. This was **receding-horizon planning**.

Consider a group that was supposed to be restored but whose switching command failed. The next cycle needed to know that it remained disconnected. Updating history from the intended command would have corrupted duration and switching decisions.

The simulation therefore recorded actual outcomes. Failed commands, load-estimate errors, changed shortage inputs, and operator instructions could alter what happened after planning.

A schedule that covered estimated demand could still leave a real simulated shortage because loads differed or a command failed. We measured that separately from schedule validity.

The loop’s total time limit was divided across solver attempts. Consequently, a nominal twelve-second ladder budget did not give every attempt twelve seconds. Small per-attempt budgets could affect hard cases.

### Feedback and stability

Optional meter feedback adjusted load estimates using observations available while groups were supplied, including the observation associated with a cut. It did not assume that a continuously disconnected group revealed its hidden demand.

Warm starts shifted the previous plan forward and reused it. We measured both changes in overlapping plans and surprises in the next committed action. We also counted unexpected reversals.

This distinction mattered because ordinary rotation could legitimately contain short on/off sequences. Counting every short cycle as instability would have misrepresented the system.

## 7. How explanations were produced

The **what-if** tool solved the problem before and after an instruction such as “keep this area supplied.” Where both runs supplied objective values, it reported their difference.

The first-slot explainer examined the alternative of keeping each selected cut group on. This gave a concrete comparison an operator could inspect.

We also implemented basic rule reasons: an explicit operator order, missing approval for a Critical cut, an unfinished minimum duration, or an exhausted individual switching allowance. Otherwise, the explanation stated that the optimizer selected the action.

These were useful explanations, but not complete proofs of necessity. Different search runs could find different-quality schedules. Different relaxation modes could also make a simple cost subtraction misleading.

We therefore interpreted the result as “this alternative produced this cost under the comparison,” rather than claiming that the chosen cut was the only possible or globally best decision.

The browser demo replayed computed baseline and override episodes. It showed group supply states, shortage, relief, and consequences over time. It helped explain the system visually, but did not solve new operator requests live in the browser.

## 8. How we tested the system

We checked the model at several levels rather than relying only on successful demonstrations.

Hand-calculated cases checked costs and individual rules. Small exhaustive searches provided reference answers for solver comparisons. Pattern tests compared accepted rows with verification behavior and compared the fast builder with its reference implementation.

Loop tests examined history across replanning boundaries, including durations, shift changes, overrides, approvals, and command failures. This mattered because individually valid short plans could still interact incorrectly across time.

Benchmark tests checked mode separation and resume tracking. Per-seed comparisons helped expose misleading aggregate claims and variation from time-limited search.

Earlier supplied full-suite output recorded **372 passing tests**. A later focused benchmark check recorded **23 passing tests**. These were separate checkpoints; they were not added together or presented as a verified final full-suite count.

The audit also corrected behavior around time-of-day handling and switching-budget accounting. Simplification was kept separate from correctness: fewer lines were useful only when the behavior remained understandable and supported by checks.

## 9. What the results showed

The latest reviewed scale dataset contained **360 cases** across 10, 50, and 200 groups, three shortage levels, ten seeds, and four start times.

Two cases were identified as interrupted by laptop sleep. Excluding them left **358 cases for interpretation**. Their values were not replaced with expected numbers. The exclusions also needed to be reflected in any regenerated summaries and plots.

At **10 groups**, B&B proved optimality in **103 of 120 cases**. CP-SAT reported integer-model optimality in **116 of 120**. Small instances therefore often allowed strong checks, but not every run completed with proof.

At **50 groups**, local search frequently matched the best schedule found among the compared methods. Proof remained much harder within the configured limits. A zero median gap against the best found schedule did not mean every result was optimal; it described agreement with that particular reference.

At **200 groups**, local search returned strict feasible schedules in all **120 cases** and matched the best recorded cost among the methods run there. CP-SAT was disabled, and B&B was limited to one node. The short B&B runtime at that size therefore did not demonstrate fast full optimization.

Local search had a nominal 30-second budget, with about 21 seconds allocated to strict search. B&B and CP-SAT received 60-second limits where fuller searches were enabled. These settings defined the comparison; they did not establish an equal-runtime contest or universal algorithm ranking.

The **ladder dataset** contained **2,160 distinct cases** across shortage, override, and tighter-policy experiments. Removing the identified sleep-affected case left **2,159 cases: 1,826 plans, 71 approval requests, and 262 escalations**. Plans included both strict and downtime-relaxed outcomes.

Those counts demonstrated that the response paths were exercised. They were not a real-world success rate, and an escalation did not necessarily prove unavoidable failure. A possible second sleep could not be identified confidently from the supplied measurements alone.

The supplied **robustness terminal log** contained **430 episode results**, including **80 with positive unmet demand before exclusions**. This reinforced the need to examine coverage alongside harm. An episode could appear inexpensive partly because demand remained uncovered.

The complete current robustness CSV had not been reviewed, and several episodes were interrupted. We therefore did not retain the old guide’s precise percentages for feedback benefits, override costs, or stability improvements as verified final conclusions.

Likewise, older 500-group, hybrid, forecast, and sensitivity figures were not merged into the newer results as though they came from one consistent experiment. Their experiment code remained part of the implementation, but their historical numbers required their own settings and evidence.

| Method | 10 groups | 50 groups | 200 groups | What the result established |
|---|---|---|---|---|
| **Best valid greedy baseline** | Generally higher harm than the stronger search methods | Often left substantial room for improvement | Higher harm than local search in the reported comparisons | A fast reference for measuring the benefit of looking ahead |
| **Branch-and-bound** | Proved optimality in **103/120 cases** | Full search was attempted, but no optimality proofs were recorded within the budget | Returned feasible schedules in **120/120 cases**, with a one-node search limit | Useful for small-instance proofs and lower bounds; the large-instance run was deliberately limited |
| **Local search** | Matched the best recorded harm in **115/120 cases** | Frequently matched the best recorded schedule; optimality remained unproven | Returned strict feasible schedules and matched the best recorded harm in **120/120 cases** | Produced strong schedules across the tested sizes without generally certifying optimality |
| **Hybrid** | Implemented and tested separately | Not included in this sweep | Not included in this sweep | Combined local search with exact search; this dataset did not establish its comparative advantage |
| **CP-SAT** | Reported integer-model optimality in **116/120 cases** | Found feasible schedules on most cases, but no optimality proofs were recorded within the budget | Not run | Provided an independent comparison, with optimality claims limited to its integer model |

## 10. What the results meant—and what they did not

The experiments supported a practical division of roles. Greedy methods supplied quick baselines. Branch-and-bound supplied exact search and useful bounds. Local search supplied strong feasible schedules at larger tested sizes. The hybrid connected the latter two approaches, while CP-SAT supplied an external implementation for comparison.

**Best found** and **optimal** remained different. A schedule matching the best recorded answer could still be improved by an undiscovered schedule. A valid lower bound helped quantify that uncertainty when it referred to the same model and constraint mode.

Planning objective and experienced episode harm were also different. The planning objective included overshoot penalties, while episode reporting measured the harm actually accumulated from cuts and switching. Unserved shortage was reported separately. Those values should not be compared as interchangeable measures.

Unserved shortage reported in kW-slots represented power deficit accumulated across slots. With 15-minute slots, multiplying by 0.25 converted that quantity to kWh.

The project did not establish fairness over long periods, real-world operating safety, or readiness for deployment. Synthetic loads and assumed rules determined what the planner considered acceptable. Field validation would have been needed to establish whether those assumptions represented an actual utility.

## 11. Reproducibility and running the project

We added metadata checks to the resumable scale, ladder, and robustness runners. Their `.meta.json` files recorded settings, input and source hashes, and selected environment details. Incompatible resumes were rejected.

This prevented accidental mixing of different experiments, but did not make clock-limited search deterministic. Sleep, machine load, and available processing time could still affect results. Robustness override resumes also reconstructed their baseline, which limited exact replay.

From the repository root, installation and testing used:

```bash
python -m pip install -e ".[dev,data,oracle]"
python -m pytest -q
```

The demo was regenerated with:

```bash
python -m evaluation.export_demo
python -c "import json; from pathlib import Path; p=Path('data/processed'); d=json.loads((p/'demo.json').read_text(encoding='utf-8')); (p/'demo_data.js').write_text('window.DEMO='+json.dumps(d, allow_nan=False)+';', encoding='utf-8')"
```

Opening `data/processed/demo.html` displayed the replay.

The reviewed scale experiment used:

```bash
python -m evaluation.run_scale --sizes 10 50 200 --seeds 10 --hours 3 10 17 20 --ls-time 30 --bnb-time 60 --bnb-full-max-n 50 --oracle-time 60 --oracle-max-n 50 --workers 1 --tiers data/processed/scenarios/tiers.json --out data/processed/benchmarks/scale_sweep.csv
```

The ladder and robustness experiments used:

```bash
python -m evaluation.run_ladder_stats --families ratio override tight --sizes 20 50 --seeds 10 --hours 3 10 17 20 --time-limit 24 --out data/processed/benchmarks/ladder_stats.csv

python -m evaluation.run_robustness --experiments margin stability override failure feedback --n 10 --stability-n 20 --seeds 10 --episode-slots 16 --time-limit 12 --tiers data/processed/scenarios/tiers.json --out data/processed/benchmarks/robustness.csv
```

These were long experiments. Existing tracked runs required the same settings with `--resume`; new experiments required new output paths. The original CSVs and metadata belonged together. Analysis exclusions were documented separately rather than disguised as measured replacements.

## 12. What we built

We completed a prototype that connected explicit operating rules to search, checking, simulated execution, and explanation. Someone could inspect the policy, understand the objective, compare scheduling methods, see which constraints were relaxed, and replay the resulting actions.

Our contribution was the implementation and integration of these parts for the scheduling problem, together with their evaluation. We did not claim to invent branch-and-bound, local search, or constraint programming.

The project showed how an AI planning system could expose its assumptions and decisions instead of returning an unexplained timetable. Its strongest claim was a working, inspectable prototype with evidence on simulated cases. Its limits remained visible: the model simplified electricity operations, some results remained uncertain, and deployment on a real network had not been established.