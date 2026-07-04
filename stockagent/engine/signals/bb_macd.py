"""Bollinger-Bands + MACD signal (V2.2).

User idea: use BB to locate price within its range + MACD to confirm momentum,
instead of BB alone (which only fades the lower band). Two entry styles:

- dip (抄底回升):  price near/below the LOWER band (%B < pctb_low) AND MACD
                  histogram turning UP (slope > 0) AND above long MA.
                  => "an uptrend name that just pulled back and is starting to recover"
- trend (顺趋势): price near/above the UPPER band (%B >= pctb_high) AND MACD
                  histogram positive (momentum sufficient) AND above long MA.
                  => "a strong name riding the band"

mode in {dip, trend, both}. Score = MACD histogram (current momentum strength),
which directly reflects "动量足够". Eligibility = mode hit AND above long MA.

Exits are ranking-only (V1): a position rotates out when its score drops out of
top-K (momentum fading => score falls) + the existing -8% stop + regime filter.
Evaluated at the last bar of the (close-sliced) series, no lookahead.
"""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from ._common import build_frame

BB_PARAMS = {
    "bb_period": 20,
    "bb_std": 2.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "pctb_low": 0.1,
    "pctb_high": 1.0,
    "long_ma": 250,
    "mode": "both",
}


def _bb_params(params: dict) -> dict:
    cfg = params.get("rotation", {}).get("bb_macd", {}) or {}
    return {**BB_PARAMS, **cfg}


def _macd_hist_slope(close: pd.Series, mf: int, ms: int, msig: int) -> float:
    """MACD hist at last bar minus hist at the prior bar."""
    need = ms + msig + 1
    if close is None or len(close) < need:
        return float("nan")
    hist_now = ind.macd(close, mf, ms, msig)[2]
    hist_prev = ind.macd(close.iloc[:-1], mf, ms, msig)[2]
    if pd.isna(hist_now) or pd.isna(hist_prev):
        return float("nan")
    return float(hist_now - hist_prev)


def score_symbol(close: pd.Series, params: dict) -> dict:
    p = _bb_params(params)
    bb_period, bb_std = int(p["bb_period"]), float(p["bb_std"])
    mf, ms, msig = int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"])
    pctb_low, pctb_high = float(p["pctb_low"]), float(p["pctb_high"])
    long_ma, mode = int(p["long_ma"]), p["mode"]

    pb = ind.pctb(close, bb_period, bb_std)
    hist = ind.macd(close, mf, ms, msig)[2]
    slope = _macd_hist_slope(close, mf, ms, msig)
    ma = ind.sma(close, long_ma)
    last = float(close.iloc[-1]) if len(close) else float("nan")
    above = (not pd.isna(ma)) and (last > ma)

    dip_hit = (not pd.isna(pb)) and (pb < pctb_low) and (not pd.isna(slope)) and (slope > 0)
    trend_hit = (not pd.isna(pb)) and (pb >= pctb_high) and (not pd.isna(hist)) and (hist > 0)
    if mode == "dip":
        mode_hit = dip_hit
    elif mode == "trend":
        mode_hit = trend_hit
    else:  # both
        mode_hit = dip_hit or trend_hit

    eligible = bool(above and mode_hit)
    score = hist if not pd.isna(hist) else float("nan")
    return {
        "score": score,
        "above_ma": above,
        "eligible": eligible,
        "last_close": last,
        "len": int(len(close)),
    }


def score_universe(close_by_symbol: dict, params: dict) -> pd.DataFrame:
    rows = []
    for sym, s in close_by_symbol.items():
        info = score_symbol(s, params)
        info["symbol"] = sym
        rows.append(info)
    return build_frame(rows)


def describe_symbol(close: pd.Series, params: dict) -> dict:
    p = _bb_params(params)
    bb_period, bb_std = int(p["bb_period"]), float(p["bb_std"])
    mf, ms, msig = int(p["macd_fast"]), int(p["macd_slow"]), int(p["macd_signal"])
    long_ma = int(p["long_ma"])

    info = score_symbol(close, params)
    pb = ind.pctb(close, bb_period, bb_std)
    hist = ind.macd(close, mf, ms, msig)[2]
    ma = ind.sma(close, long_ma)
    last = float(close.iloc[-1]) if len(close) else float("nan")
    above = (not pd.isna(ma)) and (last > ma)

    pb_str = f"{pb:.2f}" if not pd.isna(pb) else "NA"
    if not pd.isna(pb):
        band = "贴/破下轨" if pb < 0.2 else ("贴/破上轨" if pb > 0.9 else "中段")
    else:
        band = "NA"
    hist_str = f"{hist:+.3f}" if not pd.isna(hist) else "NA"
    summ = (f"%B {pb_str}({band}) | MACD柱 {hist_str} | "
            f"{'在' + str(long_ma) + '日线上' if above else '跌破' + str(long_ma) + '日线'}")
    return {"score": info["score"], "eligible": info["eligible"], "summary": summ}
