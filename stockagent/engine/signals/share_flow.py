"""Share-flow (smart-money) signal — V2.4: pure flow (no price gate, flow-based exit).

V2.4 changes from V2.3:
- ENTRY: dropped the price>long_ma gate. 机构 accumulates BEFORE price confirms;
  requiring price>MA filtered out the best early entries. Now: eligible = share
  net inflow only (trust the flow).
- EXIT: signal-specific check_exits — flow-stop (smoothed shares drop > pct from
  peak = 机构 turning to redemption) + wide price backstop (-20%) for tail risk.
  Other signals keep the default price -8% stop (no check_exits → fallback).

score = share change rate over trend_days. eligible = share_change > min.
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .. import stop as stop_mod
from ._common import build_frame

SHARE_PARAMS = {
    "trend_days": 60,
    "min_share_change": 0.0,
    "flow_stop_pct": 0.10,
    "price_backstop": 0.20,
    "smooth_window": 20,
}


def _share_params(params: dict) -> dict:
    cfg = params.get("rotation", {}).get("share_flow", {}) or {}
    return {**SHARE_PARAMS, **cfg}


def _share_change(shares: pd.Series, trend_days: int) -> float:
    if shares is None or len(shares) < trend_days + 1:
        return float("nan")
    a = float(shares.iloc[-1 - trend_days])
    b = float(shares.iloc[-1])
    if pd.isna(a) or pd.isna(b) or a <= 0:
        return float("nan")
    return b / a - 1.0


def score_symbol(close: pd.Series, params: dict, shares: pd.Series | None = None) -> dict:
    """V2.4: eligible = share net inflow only (no price gate)."""
    p = _share_params(params)
    ch = _share_change(shares, int(p["trend_days"]))
    last = float(close.iloc[-1]) if len(close) else float("nan")
    eligible = bool(not pd.isna(ch) and ch > float(p["min_share_change"]))
    return {"score": ch, "above_ma": True, "eligible": eligible, "last_close": last, "len": int(len(close))}


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
    trend_days = int(p["trend_days"])
    ctx = ctx or {}
    sym = ctx.get("symbol")
    shares = (ctx.get("share") or {}).get(sym) if sym is not None else None
    ch = _share_change(shares, trend_days)
    info = score_symbol(close, params, shares=shares)
    ch_str = f"{ch:+.1%}" if not pd.isna(ch) else "NA"
    direction = "机构加仓" if (not pd.isna(ch) and ch > 0) else ("机构减仓" if not pd.isna(ch) else "无份额数据")
    summ = f"份额{trend_days}日 {ch_str}({direction})"
    return {"score": info["score"], "eligible": info["eligible"], "summary": summ}


def check_exits(positions: dict, ctx: dict, params: dict) -> list[str]:
    """V2.4 signal-specific exit: flow-stop + price backstop.

    flow-stop: smoothed shares (rolling mean) drop > flow_stop_pct from peak
    since entry → 机构 turning to redemption.
    price backstop: close drops > price_backstop from peak → tail-risk exit.

    Returns list of symbols to exit.
    """
    p = _share_params(params)
    flow_pct = float(p["flow_stop_pct"])
    price_backstop = float(p["price_backstop"])
    smooth_win = int(p["smooth_window"])
    share_map = (ctx or {}).get("share", {})
    close_map = (ctx or {}).get("close", {})
    exits = []
    for sym, info in positions.items():
        entry = info.get("entry_date") if isinstance(info, dict) else None
        if not entry:
            continue
        # 1) flow-stop: smoothed shares from peak since entry
        shares = share_map.get(sym)
        if shares is not None and len(shares):
            sh_since = shares.loc[entry:] if entry in shares.index else shares
            if len(sh_since) >= smooth_win:
                smoothed = sh_since.rolling(smooth_win, min_periods=max(3, smooth_win // 4)).mean().ffill()
                peak = float(smoothed.max())
                last = float(smoothed.iloc[-1])
                if peak > 0 and (last / peak - 1.0) <= -flow_pct:
                    exits.append(sym)
                    continue
        # 2) price backstop (tail risk)
        close = close_map.get(sym)
        if close is not None and len(close):
            cs = close.loc[entry:] if entry in close.index else close
            if len(cs) and stop_mod.stop_triggered(cs, price_backstop):
                exits.append(sym)
    return exits
