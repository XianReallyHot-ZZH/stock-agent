"""Rotation layer (Q6): trend gate + multi-period momentum score + top-K selection.

Pure functions over a {symbol: close_series} dict, evaluated at each series' last bar.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind


def passes_trend_gate(close: pd.Series, ma_period: int) -> bool:
    """True if last close > SMA(ma_period)."""
    ma = ind.sma(close, ma_period)
    if pd.isna(ma):
        return False
    return float(close.iloc[-1]) > ma


def score_symbol(close: pd.Series, params: dict) -> dict:
    """Compute gate + momentum score for one symbol at its last bar."""
    rot = params.get("rotation", {})
    win = rot.get("momentum", {}).get("windows", [20, 60, 120])
    wts = rot.get("momentum", {}).get("weights", [0.2, 0.3, 0.5])
    gate_ma = rot.get("trend_gate_ma", 60)
    score = ind.momentum_score(close, win, wts)
    gate = passes_trend_gate(close, gate_ma)
    return {
        "score": score,
        "above_ma": gate,
        "eligible": bool(gate and not pd.isna(score)),
        "last_close": float(close.iloc[-1]) if len(close) else None,
        "len": int(len(close)),
    }


def score_universe(close_by_symbol: dict[str, pd.Series], params: dict) -> pd.DataFrame:
    """Score every symbol. Returns DataFrame sorted by score desc.

    Columns: symbol, score, above_ma, eligible, last_close, len
    """
    rows = []
    for sym, s in close_by_symbol.items():
        info = score_symbol(s, params)
        info["symbol"] = sym
        rows.append(info)
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
    return df


def select_top_k(scored: pd.DataFrame, k: int) -> list[str]:
    """Top-K eligible symbols (delegates to signals._common; kept for API compat)."""
    from .signals._common import select_top_k as _stk
    return _stk(scored, k)


__all__ = ["passes_trend_gate", "score_symbol", "score_universe", "select_top_k"]
