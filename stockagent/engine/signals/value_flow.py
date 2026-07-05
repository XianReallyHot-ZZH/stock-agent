"""Value-flow signal: buy low + institutions accumulating + patient hold.

Entry: ACCUMULATING (institutions buying) AND price percentile < threshold (historically cheap)
       AND price > MA(250) (long-term trend intact).
Hold: adaptive trailing stop — distance depends on institutional state:
       ACCUMULATING → wide (3×ATR), STABLE → moderate (2×ATR), DISTRIBUTING → tight (1×ATR).
       This captures the "最后疯狂" (institutions leave but price still rises → trailing follows up,
       only exits when price actually reverses).
Exit: trailing triggered OR catastrophic stop OR (min hold days elapsed).
No time cap: profitable positions held indefinitely.

This is NOT a rotation signal (no weekly re-ranking). It's buy-and-hold with
institutional-aware adaptive trailing. STICKY=True (held positions get priority).
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .. import stop as stop_mod
from . import share_flow as sf
from ._common import build_frame

STICKY = True

VALUE_PARAMS = {
    "percentile_window": 500,      # ~2 years lookback for "historical low"
    "entry_percentile": 0.30,      # buy when below 30th percentile
    "trend_gate_ma": 250,          # must be above MA(250) to enter
    "accum_trail_mult": 3.0,      # trailing multiplier when ACCUMULATING
    "stable_trail_mult": 2.0,     # when STABLE
    "dist_trail_mult": 1.0,       # when DISTRIBUTING (tight)
    "catastrophic_stop": 0.25,    # -25% from entry
    "min_hold_days": 60,          # minimum holding period
}


def _vf_params(params: dict) -> dict:
    cfg = params.get("rotation", {}).get("value_flow", {}) or {}
    return {**VALUE_PARAMS, **cfg}


def _share_state(shares: pd.Series, sp: dict) -> str:
    """Classify share state using share_flow's multi-timeframe consensus."""
    rs, rm, rl = int(sp["roc_short_days"]), int(sp["roc_mid_days"]), int(sp["roc_long_days"])
    rocs = [sf._share_change(shares, rs), sf._share_change(shares, rm), sf._share_change(shares, rl)]
    return sf._classify_state(rocs, float(sp["accum_threshold"]), float(sp["dist_threshold"]))


def score_symbol(close: pd.Series, params: dict, shares: pd.Series | None = None) -> dict:
    p = _vf_params(params)
    pct = ind.percentile_rank(close, int(p["percentile_window"]))
    ma250 = ind.sma(close, int(p["trend_gate_ma"]))
    last = float(close.iloc[-1]) if len(close) else float("nan")

    # share state
    state = "STABLE"
    if shares is not None and len(shares) > 0:
        sp = sf._share_params(params)
        state = _share_state(shares, sp)

    above_trend = (not pd.isna(ma250)) and (last > ma250)
    cheap = (not pd.isna(pct)) and (pct < float(p["entry_percentile"]))
    eligible = bool(state == "ACCUMULATING" and cheap and above_trend)

    # score: deeper discount = higher score (buy cheapest first)
    score = (float(p["entry_percentile"]) - pct) / float(p["entry_percentile"]) if eligible else float("nan")

    return {
        "score": score, "above_ma": above_trend, "eligible": eligible,
        "last_close": last, "len": int(len(close)),
    }


def score_universe(close_by_symbol: dict, params: dict, ctx: dict | None = None) -> pd.DataFrame:
    share_map = ((ctx or {}).get("share")) or {}
    rows = []
    for sym, s in close_by_symbol.items():
        info = score_symbol(s, params, shares=share_map.get(sym))
        info["symbol"] = sym
        rows.append(info)
    return build_frame(rows)


def describe_symbol(close: pd.Series, params: dict, ctx: dict | None = None) -> dict:
    p = _vf_params(params)
    ctx = ctx or {}
    sym = ctx.get("symbol")
    shares = (ctx.get("share") or {}).get(sym) if sym else None
    info = score_symbol(close, params, shares=shares)

    pct = ind.percentile_rank(close, int(p["percentile_window"]))
    pct_str = f"{pct:.0%}" if not pd.isna(pct) else "NA"
    state = _share_state(shares, sf._share_params(params)) if shares is not None and len(shares) else "STABLE"
    summ = f"[{state}] 分位{pct_str} | {'在250日线上' if info['above_ma'] else '破250日线'}"
    return {"score": info["score"], "eligible": info["eligible"], "summary": summ}


def check_exits(positions: dict, ctx: dict, params: dict) -> list[str]:
    """Adaptive trailing stop: distance depends on institutional state.

    ACCUMULATING → wide trailing (3×ATR): trust institutions, ride pullbacks.
    DISTRIBUTING → tight trailing (1×ATR): be ready to run, but still let 最后疯狂 run.
    Exit only when price actually reverses from peak (not when DISTRIBUTING starts).
    """
    p = _vf_params(params)
    sp = sf._share_params(params)
    share_map = (ctx or {}).get("share", {})
    close_map = (ctx or {}).get("close", {})
    exits = []

    for sym, info in positions.items():
        entry = info.get("entry_date") if isinstance(info, dict) else None
        if not entry:
            continue

        close = close_map.get(sym)
        if close is None or len(close) == 0:
            continue
        cs = close.loc[entry:] if entry in close.index else close
        if len(cs) < 2:
            continue

        # days held
        cs.index = pd.to_datetime(cs.index)
        days_held = (cs.index[-1] - pd.to_datetime(entry)).days
        if days_held < int(p["min_hold_days"]):
            continue  # too early, be patient

        peak = float(cs.max())
        last = float(cs.iloc[-1])
        entry_px = float(cs.iloc[0])

        # catastrophic stop
        if entry_px > 0 and (last / entry_px - 1.0) <= -float(p["catastrophic_stop"]):
            exits.append(sym)
            continue

        # adaptive trailing: distance depends on share state
        shares = share_map.get(sym)
        state = _share_state(shares, sp) if shares is not None and len(shares) else "STABLE"

        if state == "ACCUMULATING":
            trail_mult = float(p["accum_trail_mult"])
        elif state == "DISTRIBUTING":
            trail_mult = float(p["dist_trail_mult"])
        else:
            trail_mult = float(p["stable_trail_mult"])

        atr = ind.mean_abs_return(cs, 14)
        if pd.isna(atr) or atr <= 0 or peak <= 0:
            continue

        # trailing line = peak − mult × ATR × peak (in price terms)
        trailing_line = peak * (1.0 - trail_mult * atr)
        if last <= trailing_line:
            exits.append(sym)

    return exits
