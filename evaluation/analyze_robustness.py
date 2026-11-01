"""Per-seed, paired view of robustness.csv. python -m evaluation.analyze_robustness"""
import statistics as st
from evaluation.run_robustness import load_rows

rows = load_rows("data/processed/benchmarks/robustness.csv")
S = lambda r: int(r["seed"])

print("== replicate check: SAME effective config (n=10, 20%, margin 5%, no failure, no feedback) ==")
reps = {}
for r in rows:
    e, p1, p2 = r["experiment"], r["param1"], r["param2"]
    same = ((e == "margin" and float(p2) == 0.05) or (e == "failure" and float(p1) == 0.0)
            or (e == "override" and p1 == "none") or (e == "feedback" and p2 == "no feedback, margin 5%"))
    if same:
        reps.setdefault(S(r), set()).add((round(r["harm"], 1), int(r["switches"])))
for s, v in sorted(reps.items()):
    print(f"seed {s}: {sorted(v)}" + ("   <-- NOT DETERMINISTIC" if len(v) > 1 else ""))

print("\n== feedback vs margin: per seed, then mean/median ==")
fb = [r for r in rows if r["experiment"] == "feedback"]
for sd in sorted({r["param1"] for r in fb}):
    print(f"load error {float(sd):.0%}")
    for label in dict.fromkeys(r["param2"] for r in fb if r["param1"] == sd):
        g = sorted((r for r in fb if r["param1"] == sd and r["param2"] == label), key=S)
        h = [r["harm"] for r in g]
        print(f"  {label:<50} mean {st.mean(h):>9.0f} median {st.median(h):>9.0f} "
              f"harm {[round(x) for x in h]} unserved {[round(r['unserved']) for r in g]}")

print("\n== failure vs 0% (paired by seed) ==")
fr = [r for r in rows if r["experiment"] == "failure"]
base = {S(r): r for r in fr if float(r["param1"]) == 0.0}
for p in sorted({float(r["param1"]) for r in fr if float(r["param1"]) > 0}):
    for r in sorted((r for r in fr if float(r["param1"]) == p), key=S):
        b = base[S(r)]
        note = "  <-- no failure occurred: solver noise" if r["failed"] == 0 and abs(r["harm"] - b["harm"]) > 1 else ""
        print(f"p={p:.2f} seed {S(r)}: failed {int(r['failed'])}, relaxed cycles {int(r['relaxed_cycles'])}, "
              f"harm {b['harm']:.0f} -> {r['harm']:.0f}{note}")