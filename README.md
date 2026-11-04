# Urja Niyam

Urja Niyam helps plan power cuts when there is not enough electricity for everyone. It suggests which areas to temporarily disconnect, when to do it, and when to restore their supply. A human operator makes the final decision.

Imagine a town needs 100 units of power, but only 80 are available. Some demand must be disconnected. Cutting the same neighbourhood all evening would be unfair, and cutting a hospital could be dangerous. This project tries to find a plan that covers the shortage while following rules about important services, outage length, and how often an area can be switched off and on.

It works with groups of electricity users connected through a shared supply line, called a *feeder* in the code. It cannot choose individual houses within that group. This also means a cut may remove more demand than the exact shortage.

## What it does

The system compares possible schedules using a cost that represents the modeled impact of power cuts. A lower cost is better under the chosen rules; it is not a direct measurement of real human suffering.

By default, it plans four hours ahead in 15-minute steps. It applies the first step in the simulation, updates what has happened, and plans again. An operator can also give instructions, such as keeping a particular area supplied.

If the usual rules cannot produce a plan, the system tries defined relaxations. Cutting protected services requires approval. If it still cannot produce an acceptable plan, it reports that the situation needs further action.

## Where it works

This is a **planning and decision-support prototype**. It starts with an estimated electricity shortage. It does not predict the whole electricity market, control real switches, or simulate how power flows through physical wires.

The experiments use simulated areas and demand. Tests and benchmarks help check the software and compare its methods. They do not establish that it is ready to operate a real electricity network.

## How the code fits together

`configs/policy.yaml` contains the rules and limits. `src/loadshed/sim/` creates simulated situations, and `src/loadshed/solvers/` searches for schedules. `verify.py` checks schedules separately from the search code.

`ladder.py` handles relaxations and approval requests. `loop.py` moves the simulation forward and replans. `evaluation/` contains experiments and the demo exporter, while `tests/` checks expected behaviour.

## Run it

Use Python 3.11 or newer. From the project folder, install the dependencies and run the tests:

```bash
python -m pip install -e ".[dev,data,oracle]"
python -m pytest -q
```

Generate the demo data:

```bash
python -m evaluation.export_demo
python -c "import json; from pathlib import Path; p=Path('data/processed'); d=json.loads((p/'demo.json').read_text(encoding='utf-8')); (p/'demo_data.js').write_text('window.DEMO='+json.dumps(d, allow_nan=False)+';', encoding='utf-8')"
```

Open `data/processed/demo.html` in your browser. Choose a scenario and press Play. The page replays a computed schedule; it does not run a live electricity network.

For a small benchmark using the included scenario file:

```bash
python -m evaluation.run_scale --sizes 10 --seeds 1 --hours 17 --ls-time 5 --bnb-time 5 --oracle-time 5 --tiers data/processed/scenarios/tiers.json --out data/processed/benchmarks/quick_check.csv
```

This compares search methods under short time limits. It is a quick check, not a complete performance study. Keep the output CSV with its `.meta.json` file. To continue the same experiment, repeat the same command with `--resume`.