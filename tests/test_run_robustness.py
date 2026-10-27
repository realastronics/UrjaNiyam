"""Smoke tests so the robustness script keeps working (the real experiments take minutes)."""
from pathlib import Path

import pytest

pytest.importorskip("ortools")

from evaluation.run_robustness import (FIELDS, build_cfg, episode_row, idle_feeders, load_rows, most_cut_feeders,
                                       summarize)
from loadshed import Policy, Tier

POL = Policy.from_yaml(Path(__file__).resolve().parents[1] / "configs" / "policy.yaml")


def test_one_episode_row_has_every_field_and_sane_values():
    cfg = build_cfg(POL, 6, 0, 0.2, episode_slots=6, time_limit_s=2)
    res, row = episode_row("margin", 0.1, 0.05, 6, 0, cfg)
    assert set(FIELDS) <= set(row) and row["replans"] == 6 and row["harm"] >= 0 and row["unserved"] >= 0
    assert 0 <= row["wall_s"]


def test_most_cut_and_idle_feeder_helpers():
    cfg = build_cfg(POL, 6, 0, 0.2, episode_slots=6, time_limit_s=2)
    res, _ = episode_row("override", "none", 0, 6, 0, cfg)
    cut = most_cut_feeders(res, 3)
    assert all(sum(res.executed[f]) > 0 for f in cut)
    assert [sum(res.executed[f]) for f in cut] == sorted((sum(res.executed[f]) for f in cut), reverse=True)
    crit = {s.id for s in cfg.grid.specs if s.tier == Tier.CRITICAL}
    idle = idle_feeders(res, cfg.grid, 10)
    assert all(sum(res.executed[f]) == 0 and f not in crit for f in idle)


def test_summary_handles_every_experiment(tmp_path, capsys):
    import csv

    def row(**kw):
        base = {k: 1.0 for k in FIELDS}
        base.update(experiment="margin", param1="0.2", param2="0.05", n=10, seed=0, harm=100.0, unserved=0.0,
                    plan_revision_quiet=0.01, surprise_quiet=0.0)
        base.update(kw)
        return base

    rows = [row(), row(param2="0.3", harm=150.0, seed=1),
            row(experiment="stability", param1="mild", param2="True"), row(experiment="stability", param1="mild", param2="False"),
            row(experiment="override", param1="none", param2="0"), row(experiment="override", param1="keep", param2="1", harm=300.0),
            row(experiment="failure", param1="0.0", param2="0"), row(experiment="failure", param1="0.2", param2="0", failed=2.0)]
    path = tmp_path / "rob.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    summarize(load_rows(path))
    out = capsys.readouterr().out
    for name in ("margin:", "stability:", "override:", "failure:"):
        assert name in out
    assert "3.0x" in out                                   # the override doubled-and-more: 300 / 100
