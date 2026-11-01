from loadshed import Policy
from loadshed.loop import run_episode
from evaluation.run_robustness import build_cfg

POL = Policy.from_yaml("configs/policy.yaml")
for rep in range(6):
    m = run_episode(build_cfg(POL, 10, 0, 0.20)).metrics
    print(rep, round(m.total_harm, 1), m.n_switches, round(m.unserved_kw_slots, 1))