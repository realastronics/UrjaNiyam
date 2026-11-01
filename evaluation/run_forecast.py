import statistics as st

from loadshed import Policy
from loadshed.loop import run_episode
from evaluation.run_robustness import build_cfg

POL = Policy.from_yaml("configs/policy.yaml")
print("forecast error | margin | mean harm | mean unserved | episodes with unserved")
for err in (0.0, 0.2, 0.4):
    for mg in (0.05, 0.15):
        ms = [run_episode(build_cfg(POL, 10, s, 0.20, mg, shortfall_error=err)).metrics for s in range(10)]
        print(f"{err:>14.0%} | {mg:>5.0%} | {st.mean(m.total_harm for m in ms):9.0f} | "
              f"{st.mean(m.unserved_kw_slots for m in ms):13.0f} | {sum(m.unserved_kw_slots > 0 for m in ms)}/10", flush=True)