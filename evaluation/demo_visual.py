"""Create a presentation figure from demo.json, without running the solver again."""
import argparse
import json
from pathlib import Path

from evaluation.export_demo import DATA


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA / "demo.json")
    parser.add_argument("--out", type=Path, default=DATA / "demo_summary.png")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--tier", default="crisis")
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    if data.get("schema") != 2:
        parser.error("Regenerate the demo with evaluation.export_demo first")
    episodes = [e for e in data["episodes"] if e["seed"] == args.seed and e["tier"] == args.tier]
    if not episodes:
        parser.error("No matching city/scenario in this export")
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap

    city = next(c for c in data["cities"] if c["seed"] == args.seed)
    fig, axes = plt.subplots(2, len(episodes), figsize=(6 * len(episodes), 8), squeeze=False,
                             gridspec_kw={"height_ratios": [2, 1]}, layout="constrained")
    for col, e in enumerate(episodes):
        ax, chart = axes[:, col]
        ax.imshow([e["rows"][d["id"]] for d in city["districts"]], aspect="auto",
                  cmap=ListedColormap(["#efc776", "#233144"]), vmin=0, vmax=1)
        ax.set_yticks(range(len(city["districts"])), [d["name"] for d in city["districts"]])
        ax.set_xlabel(f"Recorded time steps ({data['slot_minutes']} minutes each)")
        title = "Model plan" if e["variant"] == "plan" else f"Operator order: {e['variant']} {e['forced']}"
        m = e["metrics"]
        ax.set_title(f"{title}\nImpact score {m['total_harm']:,.0f} | {m['n_switches']} switches")
        chart.plot([s["shortfall_kw"] / 1000 for s in e["steps"]], color="#c23e48", label="Shortage")
        chart.plot([s["net_relief_kw"] / 1000 for s in e["steps"]], color="#198676", label="Net reduction")
        chart.set_ylabel("MW")
        chart.set_xlabel("Recorded time step")
        chart.legend(frameon=False)
        chart.grid(alpha=.15)
    fig.suptitle(f"Urja Niyam · city {args.seed} · {args.tier}\nGold = supplied; dark = cut. Synthetic simulation, not live operations.")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, dpi=160)
    plt.close(fig)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
