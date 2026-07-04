"""Per-position trailing stop (Q8): exit when price falls `pct` from peak since entry.

V2.5: added vol-adaptive (ATR-proxy) stop — exit when drawdown > mult × avg daily vol.
"""
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


def stop_triggered_vol(close_since_entry: pd.Series, period: int, mult: float) -> bool:
    """Vol-adaptive stop: exit if drawdown from peak > mult × recent avg daily volatility.

    High-vol ETFs get wider stops (avg_vol ~3% × mult 3 = 9%); low-vol get tighter
    (~1% × 3 = 3%). No high/low needed — uses close-to-close abs returns as ATR proxy.
    """
    if close_since_entry is None or len(close_since_entry) < period + 1:
        return False
    peak = float(close_since_entry.max())
    last = float(close_since_entry.iloc[-1])
    if peak <= 0:
        return False
    rets = close_since_entry.pct_change().dropna()
    if len(rets) < 1:
        return False
    p = min(period, len(rets))
    avg_vol = float(rets.iloc[-p:].abs().mean())
    if avg_vol <= 0:
        return False
    drawdown = last / peak - 1.0
    return drawdown <= -(mult * avg_vol)
