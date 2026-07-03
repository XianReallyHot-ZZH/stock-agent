"""Per-position trailing stop (Q8): exit when price falls `pct` from peak since entry."""
from __future__ import annotations

import pandas as pd


def peak_since_entry(close_since_entry: pd.Series) -> float:
    if close_since_entry is None or len(close_since_entry) == 0:
        return float("nan")
    return float(close_since_entry.max())


def stop_triggered(close_since_entry: pd.Series, pct: float) -> bool:
    """True if last close has drawdown >= pct from the peak since entry."""
    if close_since_entry is None or len(close_since_entry) == 0:
        return False
    peak = float(close_since_entry.max())
    last = float(close_since_entry.iloc[-1])
    if peak <= 0:
        return False
    return (last / peak - 1.0) <= -pct
