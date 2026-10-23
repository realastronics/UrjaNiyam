"""Figures for the scale sweep.   python -m evaluation.plot_scale [--csv data/processed/benchmarks/scale_sweep.csv]

  scale_time.png     median seconds vs number of feeders (log-log), per solver, plus the pattern setup
  scale_quality.png  per tier: median gap vs the best known plan, vs number of feeders, per solver
Both read the CSV written by evaluation.run_scale. Needs matplotlib (pip install -e ".[data]").
"""
import argparse
import statistics as st
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from evaluation.run_scale import load_rows

ap = argparse.ArgumentParser()
ap.add_argument("--csv", default="data/processed/benchmarks/scale_sweep.csv")
ap.add_argument("--outdir", default="data/processed/benchmarks")
a = ap.parse_args()
rows = load_rows(a.csv)
sizes = sorted({int(r["n"]) for r in rows})
tiers = [t for t in ("mild", "moderate", "crisis") if any(r["tier"] == t for r in rows)]
out = Path(a.outdir)
out.mkdir(parents=True, exist_ok=True)


def med(vals):
    vals = [v for v in vals if v is not None]
    return st.median(vals) if vals else None


# ---- time
fig, ax = plt.subplots(figsize=(7, 4.5))
for key, label in (("setup_s", "pattern setup"), ("ls_s", "local search"), ("bnb_s", "branch-and-bound"),
                   ("cpsat_s", "CP-SAT (oracle)")):
    pts = [(n, med([r[key] for r in rows if int(r["n"]) == n])) for n in sizes]
    pts = [(n, v) for n, v in pts if v]
    if pts:
        ax.plot(*zip(*pts), marker="o", label=label)
ax.set_xscale("log"); ax.set_yscale("log"); ax.set_xticks(sizes); ax.set_xticklabels(sizes); ax.minorticks_off(); ax.set_xlabel("feeders"); ax.set_ylabel("median seconds")
ax.set_title("Time vs grid size (limits cap the search solvers)"); ax.legend(); ax.grid(True, which="both", alpha=0.3)
fig.tight_layout(); fig.savefig(out / "scale_time.png", dpi=150); plt.close(fig)

# ---- quality
fig, axes = plt.subplots(1, len(tiers), figsize=(5 * len(tiers), 4.2), sharey=True, squeeze=False)
for ax, tier in zip(axes[0], tiers):
    for key, label in (("best_greedy_harm", "best greedy"), ("ls_harm", "local search"), ("cpsat_harm", "CP-SAT")):
        pts = []
        for n in sizes:
            gaps = []
            for r in (x for x in rows if int(x["n"]) == n and x["tier"] == tier):
                ubs = [v for v in (r["best_greedy_harm"], r["ls_harm"], r["cpsat_harm"]) if v]
                if r[key] and ubs:
                    gaps.append(100 * (r[key] - min(ubs)) / min(ubs))
            if gaps:
                pts.append((n, st.median(gaps)))
        if pts:
            ax.plot(*zip(*pts), marker="o", label=label)
    ax.set_xscale("log"); ax.set_xticks(sizes); ax.set_xticklabels(sizes); ax.minorticks_off(); ax.set_title(tier); ax.set_xlabel("feeders"); ax.grid(True, alpha=0.3)
axes[0][0].set_ylabel("% more harm than the best known plan (median)"); axes[0][0].legend()
fig.tight_layout(); fig.savefig(out / "scale_quality.png", dpi=150); plt.close(fig)
print("wrote", out / "scale_time.png", "and", out / "scale_quality.png")
