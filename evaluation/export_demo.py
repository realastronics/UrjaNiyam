"""Export real rolling-horizon episodes for the offline presentation.

Run from the repository root: python -m evaluation.export_demo
The browser replays these cases; it does not run an optimizer.
"""
import argparse
import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from loadshed import Policy, Tier
from loadshed.loop import EpisodeConfig, Override, run_episode
from loadshed.sim import load_tiers, make_grid
from loadshed.sim.grid import feeder_loads

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / ("Data" if (ROOT / "Data").exists() else "data") / "processed"
NAMES = {"urban": "Residential quarter", "rural": "Village neighbourhood",
         "agricultural": "Farm belt", "industrial": "Industrial park", "mixed": "Mixed-use district"}


def city_record(grid, policy, start, slots):
    """Keep each city's metadata with its own seed, not the last exported grid."""
    horizon = replace(policy, horizon_slots=slots + policy.horizon_slots)
    return {"seed": grid.seed, "districts": [dict(
        id=s.id, name=f"{NAMES.get(s.ftype.value, 'District')} {i + 1}",
        tier=s.tier.value, kind=s.ftype.value, connections=s.consumers,
        load_kw=list(feeder_loads(s, horizon, start)[:slots]))
        for i, s in enumerate(grid.specs)]}


def episode_record(cfg, result, variant, forced=None):
    return dict(seed=cfg.seed, tier=cfg.scenario.name, ratio=cfg.scenario.peak_ratio,
                variant=variant, forced=forced, overrides=[asdict(x) for x in cfg.events],
                rows=result.executed, steps=[asdict(s) for s in result.steps],
                metrics=asdict(result.metrics), approved=sorted(result.approved))


def build_export(policy, scenarios, seeds=(1, 4), n=10, time_limit=6.0):
    slots = len(next(iter(scenarios.values())).shape)
    if policy.commit_slots != 1 or any(len(s.shape) != slots for s in scenarios.values()):
        raise ValueError("Demo requires one-slot commits and equally long scenarios")
    out = dict(schema=2, generated_at=datetime.now(timezone.utc).isoformat(),
               slot_minutes=policy.slot_minutes, start_slot=17 * 60 // policy.slot_minutes,
               slots=slots, horizon_slots=policy.horizon_slots,
               safety_margin=policy.safety_margin, cities=[], episodes=[],
               notes="Synthetic districts and demand. Recorded solver decisions; no live grid connection. Critical approvals declined.")
    for seed in seeds:
        grid = make_grid(n, seed)
        out["cities"].append(city_record(grid, policy, out["start_slot"], slots))
        for scenario in scenarios.values():
            cfg = EpisodeConfig(grid, policy, scenario, start_slot=out["start_slot"],
                                seed=seed, time_limit_s=time_limit)
            base = run_episode(cfg)
            variants = [("plan", None, cfg, base)]
            eligible = [s.id for s in grid.specs if s.tier != Tier.CRITICAL]
            keep = max(eligible, key=lambda fid: sum(base.executed[fid]))
            if sum(base.executed[keep]):
                c = replace(cfg, events=(Override(slots // 4, 3 * slots // 4, keep, "keep"),))
                variants.append(("keep", keep, c, None))
            idle = next((fid for fid in eligible if not any(base.executed[fid])), None)
            if idle:
                c = replace(cfg, events=(Override(slots // 4, slots // 2, idle, "cut"),))
                variants.append(("cut", idle, c, None))
            for label, fid, config, result in variants:
                result = result if result is not None else run_episode(config)
                out["episodes"].append(episode_record(config, result, label, fid))
                print(f"City {seed}, {scenario.name}, {label}: harm {result.metrics.total_harm:,.0f}", flush=True)
    return out


def write_export(payload, output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    # Finish both serializations before replacing either destination.
    for path, content in ((output, encoded), (output.with_name("demo_data.js"), "window.DEMO=" + encoded + ";\n")):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / "configs/policy.yaml")
    parser.add_argument("--tiers", type=Path, default=DATA / "scenarios/tiers.json")
    parser.add_argument("--out", type=Path, default=DATA / "demo.json")
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--time-limit", type=float, default=6.0)
    args = parser.parse_args()
    if args.time_limit <= 0 or len(set(args.seeds)) != len(args.seeds):
        parser.error("Use a positive time limit and distinct seeds")
    payload = build_export(Policy.from_yaml(args.policy), load_tiers(args.tiers, episode_slots=16),
                           args.seeds, time_limit=args.time_limit)
    paths = [args.policy, args.tiers, *sorted((ROOT / "src/loadshed").rglob("*.py")), Path(__file__)]
    payload["provenance"] = {"time_limit_s": args.time_limit, "sha256": {
        str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths}}
    write_export(payload, args.out)
    print(f"Wrote {args.out} and demo_data.js")


if __name__ == "__main__":
    main()
