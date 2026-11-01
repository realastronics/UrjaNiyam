from loadshed import Policy
from loadshed.loop import run_episode
from evaluation.run_robustness import build_cfg

POL = Policy.from_yaml("configs/policy.yaml")
for seed in (7, 4):
    for tl in (12, 48, 144):
        m = run_episode(build_cfg(POL, 10, seed, 0.20, time_limit_s=tl)).metrics
        print(seed, tl, round(m.total_harm), m.n_switches, round(m.mean_solve_s, 1), flush=True)