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


def select_top_k(scored: pd.DataFrame, k: int, held: set | None = None) -> list[str]:
    """Top-k eligible symbols by score. If held provided, sticky: keep held+eligible first,
    then fill remaining slots with highest-scored non-held."""
    if len(scored) == 0:
        return []
    elig = scored[scored["eligible"]].copy() if "eligible" in scored.columns else scored.copy()
    if len(elig) == 0:
        return []
    elig = elig.sort_values("score", ascending=False, na_position="last")
    if held:
        held_elig = elig[elig["symbol"].isin(held)]
        non_held = elig[~elig["symbol"].isin(held)]
        picks = list(held_elig["symbol"][:k]) + list(non_held["symbol"][:k - len(held_elig)])
        return picks[:k]
    return elig.head(k)["symbol"].tolist()
