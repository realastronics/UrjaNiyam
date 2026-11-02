from dataclasses import replace

import loadshed.sim.grid as grid
from loadshed import Policy
from evaluation.run_ladder_stats import run_instance

BASE = Policy.from_yaml("configs/policy.yaml")
VARIANTS = [("baseline", {}, 0.75),
            ("surge cap 50%", {"surge_max": 0.5}, 0.75), ("surge cap 100%", {"surge_max": 1.0}, 0.75),
            ("max downtime 2h", {"max_downtime_slots": 8}, 0.75), ("max downtime 6h", {"max_downtime_slots": 24}, 0.75),
            ("feeder switches 2", {"feeder_switch_budget": 2}, 0.75), ("feeder switches 6", {"feeder_switch_budget": 6}, 0.75),
            ("farm promise 60%", {}, 0.60), ("farm promise 100%", {}, 1.0)]
RATIOS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)

print(f"{'variant':<20}" + "".join(f"{r:>6.0%}" for r in RATIOS) + "   (% of 4 seeds at rung 0, 20 feeders)")
for name, over, share in VARIANTS:
    grid.AG_PROMISE_SHARE = share
    pol = replace(BASE, **over)
    cells = []
    for r in RATIOS:
        rungs = [run_instance(pol, "s", r, 20, s, 17, r, 0.0, 24)["rung"] for s in range(4)]
        cells.append(f"{100 * rungs.count(0) / 4:>5.0f}%")
    print(f"{name:<20}" + "".join(cells), flush=True)