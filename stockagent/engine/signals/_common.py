"""Shared helpers for pluggable rotation signals.

A signal module exposes `score_universe(close_by_symbol, params) -> DataFrame`
with at least columns: symbol, score (numeric, sortable), eligible (bool).
`select_top_k` and `build_frame` here are signal-agnostic and reused by all
signals + portfolio.decide_target.
"""
from __future__ import annotations

import pandas as pd


def build_frame(rows: list[dict]) -> pd.DataFrame:
    """Build the scored DataFrame from per-symbol row dicts, sorted by score desc."""
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)
    return df


def select_top_k(scored: pd.DataFrame, k: int) -> list[str]:
    """Top-k eligible symbols by score (equal weight downstream)."""
    if len(scored) == 0:
        return []
    elig = scored[scored["eligible"]].copy() if "eligible" in scored.columns else scored.copy()
    if len(elig) == 0:
        return []
    elig = elig.sort_values("score", ascending=False, na_position="last").head(k)
    return elig["symbol"].tolist()
