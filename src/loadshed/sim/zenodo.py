"""Turn the Zenodo state demand files into shortfall-ratio statistics.

ratio = unmet / (met + unmet), on days where unmet > 0.
This needs pandas (pip install -e ".[data]"). The model and verifier do not.

Choices (all written into tiers.json so they can be cited):
  * corrected peak-met file is used for 'met'; raw peak-unmet for 'unmet' (no corrected version exists)
  * non-state columns (Total, AMNSIL, DVC) are excluded by default
  * duplicate dates: keep the first row
  * ratios above max_ratio are dropped as suspect outliers and counted
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

NON_STATE = ("Total", "AMNSIL", "Damodar Valley Corporation")


def read_wide(path: str | Path) -> pd.DataFrame:
    """Read a Zenodo wide file: one row per date, one column per state."""
    df = pd.read_csv(path)
    date_col = next((c for c in df.columns if str(c).strip().lower() in ("date", "datetime")),
                    df.columns[0])
    df = df.rename(columns={date_col: "Date"})
    df = df.drop(columns=[c for c in df.columns if str(c).strip().lower() == "occurrence"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).drop_duplicates("Date", keep="first")
    df = df.set_index("Date").sort_index()
    return df.apply(pd.to_numeric, errors="coerce")


@dataclass
class RatioResult:
    ratios: pd.Series                       # index: (Date, State); value: unmet/(met+unmet)
    n_state_days: int = 0                   # state-days with both values present
    n_positive: int = 0                     # of those, unmet > 0
    n_dropped_over_cap: int = 0
    settings: dict = field(default_factory=dict)


def shortfall_ratios(unmet_csv: str | Path, met_csv: str | Path, *,
                     exclude: tuple[str, ...] = NON_STATE,
                     year_from: int | None = None, year_to: int | None = None,
                     states: list[str] | None = None,
                     max_ratio: float = 0.5) -> RatioResult:
    unmet, met = read_wide(unmet_csv), read_wide(met_csv)
    cols = [c for c in unmet.columns if c in met.columns and c not in exclude]
    if states is not None:
        cols = [c for c in cols if c in states]
    idx = unmet.index.intersection(met.index)
    if year_from is not None:
        idx = idx[idx.year >= year_from]
    if year_to is not None:
        idx = idx[idx.year <= year_to]
    u, m = unmet.loc[idx, cols], met.loc[idx, cols]

    both = u.notna() & m.notna() & ((u + m) > 0)
    pos = both & (u > 0)
    ratio = (u / (u + m)).where(pos)
    long = ratio.stack().dropna()           # only unmet>0 state-days remain (explicit: pandas versions differ)
    long.index.names = ["Date", "State"]
    over = long > max_ratio
    return RatioResult(
        ratios=long[~over], n_state_days=int(both.to_numpy().sum()),
        n_positive=int(pos.to_numpy().sum()), n_dropped_over_cap=int(over.sum()),
        settings={"year_from": year_from, "year_to": year_to, "max_ratio": max_ratio,
                  "excluded_columns": list(exclude), "n_state_columns": len(cols)})


def tier_thresholds(ratios: pd.Series, percentiles: dict[str, float]) -> dict[str, float]:
    """e.g. {"mild": 50, "moderate": 90, "crisis": 99} -> ratio at each percentile."""
    if len(ratios) == 0:
        raise ValueError("no positive-unmet observations to take percentiles from")
    out = {name: float(np.percentile(ratios.to_numpy(), q)) for name, q in percentiles.items()}
    vals = list(out.values())
    if vals != sorted(vals):
        raise ValueError("percentiles must be increasing")
    return out
