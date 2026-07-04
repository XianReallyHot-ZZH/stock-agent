"""Mean-reversion / reversal signal (V2.1): buy oversold pullbacks in an uptrend.

The A-share-opposite of momentum: instead of chasing the strongest, buy sectors
that are in a LONG-TERM uptrend (above long MA) but have pulled back / are
oversold (RSI < threshold). Score = how oversold (threshold - RSI). Avoids
falling knives via the long-MA gate.

Same no-lookahead contract as momentum: evaluated at the last bar of the given
(close-sliced) series.
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ._common import build_frame

REVERSION_PARAMS = {
    "rsi_period": 14,
    "oversold_threshold": 40,
    "long_ma": 250,
}


def _rev_params(params: dict) -> dict:
    cfg = params.get("rotation", {}).get("reversion", {}) or {}
    return {**REVERSION_PARAMS, **cfg}


def score_symbol(close: pd.Series, params: dict) -> dict:
    p = _rev_params(params)
    rsi_period = int(p["rsi_period"])
    oversold = float(p["oversold_threshold"])
    long_ma = int(p["long_ma"])

    r = ind.rsi(close, rsi_period)
    ma = ind.sma(close, long_ma)
    last = float(close.iloc[-1]) if len(close) else float("nan")

    in_uptrend = (not pd.isna(ma)) and (last > ma)
    oversold_ok = (not pd.isna(r)) and (r < oversold)
    eligible = bool(in_uptrend and oversold_ok)
    score = (oversold - r) if (not pd.isna(r)) else float("nan")

    return {
        "score": score,
        "above_ma": in_uptrend,   # re-used as a generic "trend gate" flag
        "eligible": eligible,
        "last_close": last,
        "len": int(len(close)),
    }


def score_universe(close_by_symbol: dict, params: dict, ctx: dict | None = None) -> pd.DataFrame:
    rows = []
    for sym, s in close_by_symbol.items():
        info = score_symbol(s, params)
        info["symbol"] = sym
        rows.append(info)
    return build_frame(rows)


def describe_symbol(close: pd.Series, params: dict, ctx: dict | None = None) -> dict:
    """Human-facing indicator snapshot for the report (reversion-specific)."""
    p = _rev_params(params)
    rsi_p, oversold, long_ma = int(p["rsi_period"]), float(p["oversold_threshold"]), int(p["long_ma"])
    r = ind.rsi(close, rsi_p)
    ma = ind.sma(close, long_ma)
    last = float(close.iloc[-1]) if len(close) else float("nan")
    above = (not pd.isna(ma)) and (last > ma)
    eligible = bool(above and (not pd.isna(r)) and r < oversold)
    score = (oversold - r) if not pd.isna(r) else float("nan")
    rsi_str = f"{r:.0f}" if not pd.isna(r) else "NA"
    is_over = (not pd.isna(r)) and r < oversold
    summ = (f"RSI {rsi_str} ({'超跌' if is_over else '未超跌'}, 门槛{int(oversold)}) | "
            f"{'在' + str(long_ma) + '日线上' if above else '跌破' + str(long_ma) + '日线'}")
    return {"score": score, "eligible": eligible, "summary": summ}
