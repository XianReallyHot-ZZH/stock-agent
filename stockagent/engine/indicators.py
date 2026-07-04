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


def rsi(close: pd.Series, period: int) -> float:
    """Wilders RSI at the last bar, in [0, 100]. NaN if series too short.

    Standard Wilders smoothing: seed avg gain/loss with the simple mean of the
    first `period` changes, then exponentially smooth with alpha = 1/period.
    """
    if close is None or len(close) < period + 1:
        return np.nan
    s = close.astype(float).reset_index(drop=True)
    delta = s.diff().dropna()  # length len(close)-1
    if len(delta) < period:
        return np.nan
    gain = delta.clip(lower=0.0).to_numpy()
    loss = (-delta.clip(upper=0.0)).to_numpy()
    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + float(gain[i])) / period
        avg_loss = (avg_loss * (period - 1) + float(loss[i])) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - 100.0 / (1.0 + rs))


def bollinger_bands(close: pd.Series, period: int, n_std: float) -> tuple[float, float, float]:
    """Bollinger Bands at the last bar -> (upper, mid, lower). NaN if too short.

    mid = SMA(period); bands = mid ± n_std * population_std(period).
    """
    if close is None or len(close) < period:
        return (np.nan, np.nan, np.nan)
    window = close.iloc[-period:].astype(float)
    mid = float(window.mean())
    sd = float(window.std(ddof=0))
    return (mid + n_std * sd, mid, mid - n_std * sd)


def pctb(close: pd.Series, period: int, n_std: float) -> float:
    """Bollinger %B at the last bar: (close-lower)/(upper-lower).

    <0 = below lower band, >1 = above upper band, 0.5 = at midline.
    Returns 0.5 when bands are flat (zero width); NaN if too short.
    """
    upper, mid, lower = bollinger_bands(close, period, n_std)
    if np.isnan(upper):
        return np.nan
    if upper == lower:
        return 0.5
    last = float(close.iloc[-1])
    return (last - lower) / (upper - lower)


def macd(close: pd.Series, fast: int, slow: int, signal: int) -> tuple[float, float, float]:
    """MACD at the last bar -> (macd_line, signal_line, histogram).

    EMA-based (adjust=False, span smoothing). hist = macd_line - signal_line.
    NaN tuple if series shorter than slow+signal.
    """
    if close is None or len(close) < slow + signal:
        return (np.nan, np.nan, np.nan)
    c = close.astype(float) if not isinstance(close, pd.Series) else close.astype(float)
    ema_fast = c.ewm(span=fast, adjust=False).mean()
    ema_slow = c.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    ml = float(macd_line.iloc[-1])
    sl = float(signal_line.iloc[-1])
    return (ml, sl, ml - sl)
