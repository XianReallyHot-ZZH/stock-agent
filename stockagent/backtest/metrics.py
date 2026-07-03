"""Performance metrics on an equity curve (pure functions)."""
from __future__ import annotations

import numpy as np
import pandas as pd

PERIODS_PER_YEAR = 252  # A-share trading days


def total_return(equity: pd.Series) -> float:
    if equity is None or len(equity) < 2:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def annualized_return(equity: pd.Series, periods: int = PERIODS_PER_YEAR) -> float:
    if equity is None or len(equity) < 2:
        return 0.0
    n = len(equity) - 1
    total = equity.iloc[-1] / equity.iloc[0]
    if total <= 0:
        return -0.999
    return float(total ** (periods / n) - 1.0)


def max_drawdown(equity: pd.Series) -> tuple[float, float]:
    """Return (max_drawdown_fraction, peak_to_trough_ratio_in_days-ish). Negative mdd."""
    if equity is None or len(equity) < 2:
        return 0.0, 0.0
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    mdd = float(dd.min())
    # recovery/length (number of days in worst drawdown window)
    trough_idx = int(dd.values.argmin())
    peak_idx = int(equity.iloc[:trough_idx + 1].values.argmax())
    return mdd, float(trough_idx - peak_idx)


def sharpe(equity: pd.Series, rf_annual: float = 0.0, periods: int = PERIODS_PER_YEAR) -> float:
    if equity is None or len(equity) < 3:
        return 0.0
    rets = equity.pct_change().dropna()
    if rets.std() == 0:
        return 0.0
    rf_per = rf_annual / periods
    excess = rets - rf_per
    return float(np.sqrt(periods) * excess.mean() / rets.std())


def calmar(equity: pd.Series, periods: int = PERIODS_PER_YEAR) -> float:
    mdd, _ = max_drawdown(equity)
    ann = annualized_return(equity, periods)
    if mdd == 0:
        return 0.0
    return float(ann / abs(mdd))


def summarize(equity: pd.Series, name: str = "strategy") -> dict:
    mdd, _ = max_drawdown(equity)
    return {
        "name": name,
        "total_return": round(total_return(equity), 4),
        "annualized": round(annualized_return(equity), 4),
        "max_drawdown": round(mdd, 4),
        "calmar": round(calmar(equity), 2),
        "sharpe": round(sharpe(equity), 2),
        "n_days": int(len(equity)),
    }
