"""Build mild / moderate / crisis shortfall ratios from the Zenodo files.

Usage (from repo root):
  pip install -e ".[data]"
  python scripts/build_scenario_tiers.py
  python scripts/build_scenario_tiers.py --year-from 2019        # recent-years version
"""
import argparse
import json
from datetime import date
from pathlib import Path

from loadshed.sim.zenodo import shortfall_ratios, tier_thresholds

ap = argparse.ArgumentParser()
ap.add_argument("--unmet", default="data/raw/zenodo_electricity_MW/peak_unmet_MW.csv")
ap.add_argument("--met", default="data/raw/zenodo_electricity_MW/corrected_peak_met_MW.csv")
ap.add_argument("--out", default="data/processed/scenarios/tiers.json")
ap.add_argument("--year-from", type=int, default=None)
ap.add_argument("--year-to", type=int, default=None)
ap.add_argument("--max-ratio", type=float, default=0.5)
ap.add_argument("--percentiles", type=float, nargs=3, default=[50, 90, 99],
                metavar=("MILD", "MODERATE", "CRISIS"))
a = ap.parse_args()

res = shortfall_ratios(a.unmet, a.met, year_from=a.year_from, year_to=a.year_to,
                       max_ratio=a.max_ratio)
pct = dict(zip(("mild", "moderate", "crisis"), a.percentiles))
thr = tier_thresholds(res.ratios, pct)

print(f"state-days with data: {res.n_state_days}")
print(f"state-days with unmet > 0: {res.n_positive}  ({100 * res.n_positive / max(1, res.n_state_days):.1f}%)")
print(f"dropped above max_ratio={a.max_ratio}: {res.n_dropped_over_cap}")
print("\nratio percentiles (non-zero days):")
for q in (10, 25, 50, 75, 90, 95, 99, 99.9):
    print(f"  P{q:<5} {res.ratios.quantile(q / 100):.4f}")
print("\nstate-days by state (top 10):")
print(res.ratios.groupby(level="State").agg(["count", "median", "max"]).sort_values("count", ascending=False).head(10).to_string())
print("\nTIERS:", {k: round(v, 4) for k, v in thr.items()})

out = Path(a.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({
    "built_on": date.today().isoformat(), "unmet_file": a.unmet, "met_file": a.met,
    "percentiles": pct, "thresholds": thr, "n_observations": int(len(res.ratios)),
    "n_dropped_over_cap": res.n_dropped_over_cap, **res.settings}, indent=2))
print("wrote", out)
