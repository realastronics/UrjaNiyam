"""Smoke tests so the ladder-statistics script keeps working: it must reach the rung we know a scenario needs."""
import csv
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from evaluation.run_ladder_stats import FIELDS, load_rows, run_instance, summarize
from loadshed import Policy

POL = Policy.from_yaml(Path(__file__).resolve().parents[1] / "configs" / "policy.yaml")


def test_an_easy_scenario_stays_at_rung_0():
    r = run_instance(POL, "ratio", 0.05, 10, 0, 17, 0.05, 0.0, 12)
    assert (r["rung"], r["status"]) == (0, "PLAN") and r["extra_downtime_slots"] == 0 and r["harm"] > 0


def test_forcing_every_non_critical_feeder_on_escalates_with_zero_coverage():
    r = run_instance(POL, "override", 1.0, 10, 0, 17, 0.20, 1.0, 12)
    assert (r["rung"], r["status"]) == (3, "ESCALATE") and r["coverage"] == 0.0 and r["uncovered_kw"] > 0


def test_summary_prints_for_every_family(tmp_path, capsys):
    rows = [run_instance(POL, "ratio", 0.05, 10, 0, 17, 0.05, 0.0, 12),
            run_instance(POL, "override", 1.0, 10, 0, 17, 0.20, 1.0, 12),
            run_instance(POL, "tight", 0.05, 10, 0, 17, 0.05, 0.0, 12)]
    path = tmp_path / "ls.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    summarize(load_rows(path))
    out = capsys.readouterr().out
    assert "family 'ratio'" in out and "family 'override'" in out and "family 'tight'" in out
