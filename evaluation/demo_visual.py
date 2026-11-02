from dataclasses import replace

import matplotlib.pyplot as plt

from evaluation.run_robustness import build_cfg, most_cut_feeders
from loadshed import Policy
from loadshed.loop import Override, run_episode

cfg = build_cfg(Policy.from_yaml("configs/policy.yaml"), 10, 1, 0.20)
base = run_episode(cfg)
f = most_cut_feeders(base, 1)[0]
over = run_episode(replace(cfg, events=(Override(4, 12, f, "keep"),)))
tier = {s.id: s.tier.value for s in cfg.grid.specs}
fig, ax = plt.subplots(1, 2, figsize=(12, 4), sharey=True)
for a, res, title in ((ax[0], base, "The tool's plan"), (ax[1], over, f"Operator keeps {f} on (slots 4-11)")):
    ids = list(res.executed)
    a.imshow([res.executed[i] for i in ids], cmap="Reds", aspect="auto", vmin=0, vmax=1)
    a.set_yticks(range(len(ids)), [f"{i} ({tier[i]})" for i in ids])
    a.set_title(f"{title}\nharm {res.metrics.total_harm:,.0f}, unserved {res.metrics.unserved_kw_slots:.0f} kW-slots")
    a.set_xlabel("15-minute slot (red = feeder cut)")
plt.tight_layout()
plt.savefig("demo.png", dpi=150)
plt.show()