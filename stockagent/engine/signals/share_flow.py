"""Share-flow (smart-money) signal (V2.3). Rank sectors by ETF share trend.

ETF shares change ONLY via creation/redemption (institutional / AP), NOT via
retail secondary trading. So a sustained share INCREASE = institutions
subscribing (net inflow / 加仓); DECREASE = institutions redeeming (distribution /
减仓). Uses a monthly trend (trend_days) to suppress short-term arbitrage /
market-making noise — the article-style "long-term share trend = 机构动向".

score = share change rate over trend_days. eligible = share_change > min AND
price > long MA. Needs ctx["share"][symbol] (a shares Series, from the etf_scale
store). evaluated at last bar; no lookahead.
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ._common import build_frame

SHARE_PARAMS = {"trend_days": 60, "long_ma": 120, "min_share_change": 0.0}


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
    p = _share_params(params)
    ch = _share_change(shares, int(p["trend_days"]))
    ma = ind.sma(close, int(p["long_ma"]))
    last = float(close.iloc[-1]) if len(close) else float("nan")
    above = (not pd.isna(ma)) and (last > ma)
    eligible = bool(above and not pd.isna(ch) and ch > float(p["min_share_change"]))
    return {"score": ch, "above_ma": above, "eligible": eligible, "last_close": last, "len": int(len(close))}


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
    trend_days, long_ma = int(p["trend_days"]), int(p["long_ma"])
    ctx = ctx or {}
    sym = ctx.get("symbol")
    shares = (ctx.get("share") or {}).get(sym) if sym is not None else None
    ch = _share_change(shares, trend_days)
    ma = ind.sma(close, long_ma)
    last = float(close.iloc[-1]) if len(close) else float("nan")
    above = (not pd.isna(ma)) and (last > ma)
    info = score_symbol(close, params, shares=shares)
    ch_str = f"{ch:+.1%}" if not pd.isna(ch) else "NA"
    direction = "机构加仓" if (not pd.isna(ch) and ch > 0) else ("机构减仓" if not pd.isna(ch) else "无份额数据")
    summ = (f"份额{trend_days}日 {ch_str}({direction}) | "
            f"{'在' + str(long_ma) + '日线上' if above else '跌破' + str(long_ma) + '日线'}")
    return {"score": info["score"], "eligible": info["eligible"], "summary": summ}
