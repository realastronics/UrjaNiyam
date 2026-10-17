import pytest

pd = pytest.importorskip("pandas")

from loadshed.sim.zenodo import read_wide, shortfall_ratios, tier_thresholds


def _write(tmp_path):
    dates = ["2013-01-01", "2013-01-02", "2013-01-02", "2014-01-01", "2020-01-01"]   # one duplicate date
    unmet = pd.DataFrame({"Date": dates, "Occurrence": [0, 0, 1, 0, 0],
                          "A": [0, 10, 99, 20, 30], "B": [None, 0, 0, 5, 1000], "Total": [1, 1, 1, 1, 1]})
    met = pd.DataFrame({"Date": dates, "Occurrence": [0, 0, 1, 0, 0],
                        "A": [90, 90, 90, 80, 70], "B": [50, 50, 50, 45, 50], "Total": [9, 9, 9, 9, 9]})
    u, m = tmp_path / "unmet.csv", tmp_path / "met.csv"
    unmet.to_csv(u, index=False)
    met.to_csv(m, index=False)
    return u, m


def test_read_wide_drops_duplicates_and_occurrence(tmp_path):
    u, _ = _write(tmp_path)
    df = read_wide(u)
    assert len(df) == 4 and "Occurrence" not in df.columns and df.loc["2013-01-02", "A"] == 10


def test_ratios_exclude_non_states_zeros_and_outliers(tmp_path):
    u, m = _write(tmp_path)
    r = shortfall_ratios(u, m)
    vals = {k: round(v, 4) for k, v in r.ratios.items()}
    assert set(r.ratios.index.get_level_values("State")) == {"A", "B"}          # Total excluded
    assert len(r.ratios) == 4                      # A:10,20,30  B:5   (B:1000 -> ratio ~0.95 dropped)
    assert r.n_dropped_over_cap == 1 and r.n_positive == 5
    assert round(10 / 100, 4) in vals.values()


def test_year_filter(tmp_path):
    u, m = _write(tmp_path)
    assert len(shortfall_ratios(u, m, year_from=2014).ratios) == 3             # A:20, B:5, A:30 (B 2020 is over the cap)


def test_no_nan_in_ratios_and_thresholds_increase(tmp_path):
    u, m = _write(tmp_path)
    r = shortfall_ratios(u, m)
    assert not r.ratios.isna().any()
    t = tier_thresholds(r.ratios, {"mild": 50, "moderate": 90, "crisis": 99})
    assert t["mild"] <= t["moderate"] <= t["crisis"] and all(v == v for v in t.values())
    with pytest.raises(ValueError):
        tier_thresholds(r.ratios, {"a": 90, "b": 50})
