"""Regime / market-timing layer: risk_on vs risk_off based on benchmark vs MA.

V2.7: RegimeFilter with band (deadband) + confirmation days for stability.
When band_pct=0 and confirm_days=1, behaves identically to the old regime_state.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind

RISK_ON = "risk_on"
RISK_OFF = "risk_off"


def regime_state(bench_close: pd.Series, ma_period: int) -> str:
    """Legacy stateless regime check (kept for backward compat / tests)."""
    ma = ind.sma(bench_close, ma_period)
    if pd.isna(ma) or len(bench_close) == 0:
        return RISK_ON
    return RISK_ON if float(bench_close.iloc[-1]) > ma else RISK_OFF


class RegimeFilter:
    """Stateful regime filter with band (deadband) + confirmation days.

    - Band: price must move beyond MA ± band_pct to trigger a flip. Within the
      band → hold current regime (thermostat hysteresis).
    - Confirmation: price must stay beyond the band for `confirm_days` consecutive
      bars to confirm the flip. Filters single-day false breaks.

    Defaults (band_pct=0, confirm_days=1) reproduce the legacy regime_state.
    """

    def __init__(self, ma_period: int, band_pct: float = 0.0, confirm_days: int = 1):
        self.ma_period = ma_period
        self.band_pct = band_pct
        self.confirm_days = max(1, confirm_days)

    def process(self, close: pd.Series) -> str:
        """Replay the close series bar-by-bar; return the final regime state."""
        labels = self.process_series(close)
        return labels.iloc[-1] if len(labels) else RISK_ON

    def process_series(self, close: pd.Series) -> pd.Series:
        """Replay and return per-bar regime labels (for backtest precompute)."""
        n = len(close)
        mp = self.ma_period
        if n < mp:
            return pd.Series([RISK_ON] * n, index=close.index)

        labels = []
        state = RISK_ON
        consec = 0  # consecutive bars beyond band in the "flip" direction

        for i in range(n):
            if i < mp:
                labels.append(state)
                continue
            ma = float(close.iloc[i - mp: i].mean())
            last = float(close.iloc[i])
            upper = ma * (1 + self.band_pct)
            lower = ma * (1 - self.band_pct)

            if state == RISK_ON:
                if last < lower:
                    consec += 1
                    if consec >= self.confirm_days:
                        state = RISK_OFF
                else:
                    consec = 0
            else:  # RISK_OFF
                if last > upper:
                    consec += 1
                    if consec >= self.confirm_days:
                        state = RISK_ON
                else:
                    consec = 0
            labels.append(state)

        return pd.Series(labels, index=close.index)
