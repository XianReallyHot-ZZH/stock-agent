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


def select_top_k(scored: pd.DataFrame, k: int, held: set | None = None,
                 super_sticky: bool = False) -> list[str]:
    """Top-k eligible symbols by score.

    If held provided + super_sticky=False: keep held+eligible first, then fill
    with highest-scored non-held.

    If super_sticky=True: keep ALL held symbols (regardless of eligibility),
    then fill remaining slots with highest-scored eligible non-held.
    Only check_exits can remove a super-sticky position.
    """
    if len(scored) == 0:
        return []
    elig = scored[scored["eligible"]].copy() if "eligible" in scored.columns else scored.copy()
    if len(elig) == 0 and not (held and super_sticky):
        return []
    elig = elig.sort_values("score", ascending=False, na_position="last")

    if held and super_sticky:
        # Keep all held (even ineligible), fill rest with eligible non-held
        held_list = [s for s in scored["symbol"] if s in held][:k] if "symbol" in scored.columns else []
        non_held_elig = elig[~elig["symbol"].isin(held)] if len(elig) and "symbol" in elig.columns else pd.DataFrame()
        remaining = k - len(held_list)
        fill = list(non_held_elig["symbol"][:remaining]) if remaining > 0 and len(non_held_elig) else []
        return (held_list + fill)[:k]

    if held:
        held_elig = elig[elig["symbol"].isin(held)]
        non_held = elig[~elig["symbol"].isin(held)]
        picks = list(held_elig["symbol"][:k]) + list(non_held["symbol"][:k - len(held_elig)])
        return picks[:k]

    return elig.head(k)["symbol"].tolist()
