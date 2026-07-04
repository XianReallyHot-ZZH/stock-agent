"""Share-flow (smart-money) signal — V2.6: multi-timeframe consensus (robust).

V2.6 replaces the fragile single-ROC + daily-flow-stop with a ROBUST
multi-timeframe consensus state classifier:

- Compute share ROC over 3 windows (20/60/120 days).
- Vote: ≥2 of 3 ROCs > +threshold → ACCUMULATING (institutions net buying).
         ≥2 of 3 ROCs < -threshold → DISTRIBUTING (institutions net selling).
         Otherwise → STABLE.
- Entry: buy ACCUMULATING sectors (ranked by weighted ROC).
- Exit: when state changes to DISTRIBUTING → naturally drops out of top-K at
  weekly rotation. No daily flow-stop (eliminates churning).
- Price backstop (-20%) via check_exits: tail-risk only.

Why robust: 2-of-3 consensus + weekly-only evaluation → single noisy week on one
timeframe can't flip the state → no churning → patient institutional-trend following.
"""
from __future__ import annotations

import pandas as pd

from .. import stop as stop_mod
from ._common import build_frame

STICKY = True  # V2.8: sticky positions (hold until DISTRIBUTING, don't rotate on rank change)

SHARE_PARAMS = {
    "roc_short_days": 20,
    "roc_mid_days": 60,
    "roc_long_days": 120,
    "accum_threshold": 0.02,
    "dist_threshold": 0.02,
    "price_backstop": 0.20,
}

_ROC_WEIGHTS = (0.2, 0.3, 0.5)  # short, mid, long


def _share_params(params: dict) -> dict:
    cfg = params.get("rotation", {}).get("share_flow", {}) or {}
    return {**SHARE_PARAMS, **cfg}


def _share_change(shares: pd.Series, n: int) -> float:
    if shares is None or len(shares) < n + 1:
        return float("nan")
    a = float(shares.iloc[-1 - n])
    b = float(shares.iloc[-1])
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return float("nan")
    return b / a - 1.0


def _classify_state(rocs: list[float], accum_thr: float, dist_thr: float) -> str:
    """Multi-timeframe consensus vote → ACCUMULATING / DISTRIBUTING / STABLE."""
    accum = sum(1 for r in rocs if not pd.isna(r) and r > accum_thr)
    dist = sum(1 for r in rocs if not pd.isna(r) and r < -dist_thr)
    if accum >= 2:
        return "ACCUMULATING"
    if dist >= 2:
        return "DISTRIBUTING"
    return "STABLE"


def _weighted_score(rocs: list[float]) -> float:
    """Weighted average of ROCs (long-term bias), renormalised for NaN."""
    total_w, total = 0.0, 0.0
    for w, r in zip(_ROC_WEIGHTS, rocs):
        if not pd.isna(r):
            total_w += w
            total += w * r
    return total / total_w if total_w > 0 else float("nan")


def score_symbol(close: pd.Series, params: dict, shares: pd.Series | None = None) -> dict:
    p = _share_params(params)
    rs, rm, rl = int(p["roc_short_days"]), int(p["roc_mid_days"]), int(p["roc_long_days"])
    athr, dthr = float(p["accum_threshold"]), float(p["dist_threshold"])

    rocs = [_share_change(shares, rs), _share_change(shares, rm), _share_change(shares, rl)]
    state = _classify_state(rocs, athr, dthr)
    eligible = state == "ACCUMULATING"
    score = _weighted_score(rocs) if eligible else float("nan")
    last = float(close.iloc[-1]) if len(close) else float("nan")
    return {"score": score, "above_ma": True, "eligible": eligible, "last_close": last, "len": int(len(close))}


def score_universe(close_by_symbol: dict, params: dict, ctx: dict | None = None) -> pd.DataFrame:
    share_map = ((ctx or {}).get("share")) or {}
    rows = []
    for sym, s in close_by_symbol.items():
        info = score_symbol(s, params, shares=share_map.get(sym))
        info["symbol"] = sym
        rows.append(info)
    return build_frame(rows)


def describe_symbol(close: pd.Series, params: dict, ctx: dict | None = None) -> dict:
    p = _share_params(params)
    rs, rm, rl = int(p["roc_short_days"]), int(p["roc_mid_days"]), int(p["roc_long_days"])
    ctx = ctx or {}
    sym = ctx.get("symbol")
    shares = (ctx.get("share") or {}).get(sym) if sym is not None else None
    rocs = [_share_change(shares, rs), _share_change(shares, rm), _share_change(shares, rl)]
    state = _classify_state(rocs, float(p["accum_threshold"]), float(p["dist_threshold"]))
    info = score_symbol(close, params, shares=shares)
    roc_str = "/".join(f"{r:+.0%}" if not pd.isna(r) else "NA" for r in rocs)
    summ = f"[{state}] ROC {rs}/{rm}/{rl}d={roc_str}"
    return {"score": info["score"], "eligible": info["eligible"], "summary": summ}


def check_exits(positions: dict, ctx: dict, params: dict) -> list[str]:
    """Simplified V2.6: price backstop only (no flow-stop).

    Multi-timeframe consensus handles normal exits via weekly rotation
    (DISTRIBUTING → drops out of top-K). This is just tail-risk protection.
    """
    p = _share_params(params)
    backstop = float(p["price_backstop"])
    close_map = (ctx or {}).get("close", {})
    exits = []
    for sym, info in positions.items():
        entry = info.get("entry_date") if isinstance(info, dict) else None
        if not entry:
            continue
        close = close_map.get(sym)
        if close is not None and len(close):
            cs = close.loc[entry:] if entry in close.index else close
            if len(cs) and stop_mod.stop_triggered(cs, backstop):
                exits.append(sym)
    return exits
