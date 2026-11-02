from dataclasses import replace

import loadshed.sim.grid as grid
from loadshed import Policy
from evaluation.run_ladder_stats import run_instance

BASE = Policy.from_yaml("configs/policy.yaml")
VARIANTS = [("baseline", {}, 0.75), ("max downtime 2h", {"max_downtime_slots": 8}, 0.75),
            ("max downtime 1h", {"max_downtime_slots": 4}, 0.75), ("feeder switches 2", {"feeder_switch_budget": 2}, 0.75),
            ("2h + 2 switches", {"max_downtime_slots": 8, "feeder_switch_budget": 2}, 0.75),
            ("heavy surge", {"surge_base": 0.3, "surge_per_slot": 0.05, "surge_max": 1.0}, 0.75)]
RATIOS = (0.2, 0.3, 0.4)

print(f"{'variant':<50}" + "".join(f"{r:>6.0%}" for r in RATIOS) + "   (% of 4 seeds at rung 0, 20 feeders)")
for name, over, share in VARIANTS:
    grid.AG_PROMISE_SHARE = share
    pol = replace(BASE, system_switch_budget=30, **over)
    cells = []
    for r in RATIOS:
        rungs = [run_instance(pol, "s", r, 50, s, 17, r, 0.0, 24)["rung"] for s in range(4)]
        cells.append(f"{100 * rungs.count(0) / 4:>5.0f}%")
    print(f"{name:<50}" + "".join(cells), flush=True)