import json
from dataclasses import replace

from evaluation.run_robustness import build_cfg, idle_feeders, most_cut_feeders
from loadshed import Policy
from loadshed.loop import Override, run_episode
from loadshed.sim import build_problem
from loadshed.whatif import explain_cuts, reasons

POL = Policy.from_yaml("configs/policy.yaml")
out = {"episodes": []}
for seed in (1, 4):
    for tier, ratio in (("mild", 0.0296), ("moderate", 0.0684), ("crisis", 0.20)):
        cfg = build_cfg(POL, 10, seed, ratio)
        base = run_episode(cfg)
        keep, cut = most_cut_feeders(base, 1), idle_feeders(base, cfg.grid, 1)
        variants = [("plan", None, cfg)]
        if keep:
            variants.append(("keep", keep[0], replace(cfg, events=(Override(4, 12, keep[0], "keep"),))))
        if cut:
            variants.append(("cut", cut[0], replace(cfg, events=(Override(4, 8, cut[0], "cut"),))))
        p = build_problem(cfg.grid, POL, cfg.scenario, cfg.start_slot)
        plan, price = explain_cuts(p)
        why = {f: w for f, w in reasons(p, plan).items() if w != "chosen by the optimizer"}
        for name, fid, c in variants:
            res = base if name == "plan" else run_episode(c)
            m = res.metrics
            out["episodes"].append(dict(
                tier=tier, ratio=ratio, seed=seed, variant=name, forced=fid, harm=m.total_harm,
                unserved=m.unserved_kw_slots, switches=m.n_switches, rows=res.executed,
                steps=[(s.shortfall_kw, s.net_relief_kw, s.unserved_kw, s.rung) for s in res.steps],
                price=price if name == "plan" else None, why=why if name == "plan" else None))
            print(tier, seed, name, round(m.total_harm), flush=True)
out["feeders"] = {s.id: [s.tier.value, s.consumers] for s in cfg.grid.specs}
json.dump(out, open("data/processed/demo.json", "w"))