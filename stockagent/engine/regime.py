"""Regime / market-timing layer (Q7): risk_on vs risk_off based on benchmark vs MA."""
from __future__ import annotations

import pandas as pd

from . import indicators as ind

RISK_ON = "risk_on"
RISK_OFF = "risk_off"


def regime_state(bench_close: pd.Series, ma_period: int) -> str:
    """risk_on if last close > SMA(ma_period); risk_off if below.

    If the benchmark series is too short to compute the MA, we cannot time the
    market -> default to risk_on (do not park in cash on insufficient info).
    """
    ma = ind.sma(bench_close, ma_period)
    if pd.isna(ma) or len(bench_close) == 0:
        return RISK_ON
    return RISK_ON if float(bench_close.iloc[-1]) > ma else RISK_OFF
