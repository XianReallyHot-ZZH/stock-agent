"""Pure indicator functions on a close-price Series (indexed by date, ascending).

All functions evaluate at the LAST bar of the given series. Callers are responsible
for slicing the series to the decision date so there is no lookahead.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def returns(close: pd.Series, n: int) -> float:
    """Total return over the last `n` periods (close[-1]/close[-1-n] - 1). NaN if short."""
    if close is None or len(close) < n + 1:
        return np.nan
    a = float(close.iloc[-1 - n])
    b = float(close.iloc[-1])
    if a <= 0 or np.isnan(a) or np.isnan(b):
        return np.nan
    return b / a - 1.0


def sma(close: pd.Series, n: int) -> float:
    """Simple moving average of the last `n` bars. NaN if series too short."""
    if close is None or len(close) < n:
        return np.nan
    return float(close.iloc[-n:].mean())


def prev_peak(close: pd.Series) -> float:
    """Running max up to and including the last bar."""
    if close is None or len(close) == 0:
        return np.nan
    return float(close.iloc[-1] if len(close) == 1 else close.max())


def drawdown_from_peak(close: pd.Series, window: int | None = None) -> float:
    """Drawdown of last close from the max over `window` bars (None = whole series)."""
    if close is None or len(close) == 0:
        return np.nan
    s = close.iloc[-window:] if window else close
    peak = float(s.max())
    last = float(close.iloc[-1])
    if peak <= 0:
        return np.nan
    return last / peak - 1.0


def momentum_score(close: pd.Series, windows: list[int], weights: list[float]) -> float:
    """Weighted sum of multi-period returns."""
    if len(close) == 0:
        return np.nan
    # need enough history for the longest window
    need = max(windows) + 1
    if len(close) < need:
        return np.nan
    total, wsum = 0.0, 0.0
    for w, n in zip(weights, windows):
        r = returns(close, n)
        if not np.isnan(r):
            total += w * r
            wsum += w
    if wsum == 0:
        return np.nan
    return total / wsum  # renormalize in case some windows were unavailable
