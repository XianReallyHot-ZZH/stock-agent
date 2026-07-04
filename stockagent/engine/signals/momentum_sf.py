"""Combo signal: momentum ranking + share_flow distribution filter.

Logic (the article's insight):
1. Rank all ETFs by momentum (price strength) — catches trending sectors.
2. For each candidate, check institutional share state:
   - DISTRIBUTING (institutions redeeming) → marked ineligible (排雷).
   - ACCUMULATING / STABLE / unknown → stays eligible.
3. select_top_k picks top-K eligible = "strong momentum AND institutions not leaving".

This filters out "最后疯狂" sectors where price is still rising but institutions
are already distributing — the exact trap that momentum alone falls into.

Uses existing momentum params (rotation.momentum) + share_flow params
(rotation.share_flow). No new params of its own.
"""
from __future__ import annotations

import pandas as pd

from .. import momentum as mom
from . import share_flow as sf
from ._common import build_frame


def score_universe(close_by_symbol: dict, params: dict, ctx: dict | None = None) -> pd.DataFrame:
    """Momentum ranking with share_flow DISTRIBUTING filter."""
    # 1. standard momentum scoring
    scored = mom.score_universe(close_by_symbol, params, ctx=ctx)

    # 2. share_flow filter: mark DISTRIBUTING as ineligible
    share_map = ((ctx or {}).get("share")) or {}
    sp = sf._share_params(params)
    rs, rm, rl = int(sp["roc_short_days"]), int(sp["roc_mid_days"]), int(sp["roc_long_days"])
    athr, dthr = float(sp["accum_threshold"]), float(sp["dist_threshold"])

    for idx, row in scored.iterrows():
        sym = row["symbol"]
        shares = share_map.get(sym)
        if shares is None or len(shares) < rl + 1:
            continue  # no data → don't filter (keep eligible if momentum says so)
        rocs = [sf._share_change(shares, rs), sf._share_change(shares, rm), sf._share_change(shares, rl)]
        state = sf._classify_state(rocs, athr, dthr)
        if state == "DISTRIBUTING":
            scored.at[idx, "eligible"] = False
            scored.at[idx, "score"] = float("nan")  # exclude from ranking

    # re-sort (NaN scores go last)
    return scored.sort_values("score", ascending=False, na_position="last").reset_index(drop=True)


def describe_symbol(close: pd.Series, params: dict, ctx: dict | None = None) -> dict:
    """Momentum describe + share_flow state annotation."""
    desc = mom.describe_symbol(close, params, ctx=ctx)
    ctx = ctx or {}
    sym = ctx.get("symbol")
    share_map = (ctx.get("share") or {}).get(sym) if sym else None

    sp = sf._share_params(params)
    rs, rm, rl = int(sp["roc_short_days"]), int(sp["roc_mid_days"]), int(sp["roc_long_days"])
    if share_map is not None and len(share_map) >= rl + 1:
        rocs = [sf._share_change(share_map, rs), sf._share_change(share_map, rm), sf._share_change(share_map, rl)]
        state = sf._classify_state(rocs, float(sp["accum_threshold"]), float(sp["dist_threshold"]))
        if state == "DISTRIBUTING":
            desc["summary"] = f"[⚠机构撤资] {desc['summary']}"
            desc["eligible"] = False
    return desc
