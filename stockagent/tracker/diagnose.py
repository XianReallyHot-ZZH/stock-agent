"""指数择时层诊断 — 组装 B1 指标 + B0 数据成结构化诊断(给 dashboard/告警消费)。

分层:
  diagnose_index(close, ...)  — 纯:单指数 60日线择时诊断(trend/deviation/breakout/choppy)
  diagnose_valuation(store)   — 估值开关(④):沪深300 PE 分位 + 全市场 PB 分位 → 敏感度建议
  diagnose_style(store)       — 蓝筹 vs 成长 → 仓位倾向(S13)
  diagnose_layer(store)       — 顶层:遍历5宽基 + 估值 + 风格,返回完整指数择时诊断
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ti

# 5 broad indices (same set as DataManager.BROAD_INDICES; duplicated here so the diagnose
# layer has a stable iteration order independent of the manager).
BROAD_INDICES = [("000016", "上证50"), ("000300", "沪深300"), ("000905", "中证500"),
                 ("399006", "创业板指"), ("000688", "科创50")]
VALUATION_INDEX = "沪深300"            # ④估值开关以沪深300(大盘benchmark)为主
PE_PCT_LOW, PE_PCT_HIGH = 0.20, 0.80   # 估值低/高位分位阈值(④ 敏感度建议)


def diagnose_index(close: pd.Series, period: int = ti.MA_PERIOD,
                   lookback: int | None = None) -> dict:
    """单指数 60日线择时诊断(纯)。组装 trend / deviation / breakout / choppy。"""
    return {
        "trend": ti.trend_state(close, period),
        "deviation": ti.deviation_extremes(close, period, lookback),
        "breakout": ti.breakout_grade(close, period),
        "choppy": ti.is_choppy(close, period),
    }


def diagnose_valuation(store, pe_lookback_years: int = 10) -> dict:
    """估值开关(④):沪深300 **同口径** PE+PB 分位 → 敏感度建议;全市场 PB 作广度补充。

    S13:大盘估值低位(3000点下)趋势信号可更激进,高位宜保守。
    zone 用沪深300 同口径 PE+PB 判断(PE=PB/ROE,故 PE 高/PB 低 = ROE 偏弱 = 结构分化):
      双低 → 低位·可激进 / 双高 → 高位·宜保守 / 一高一低 → 结构分化·ROE偏弱。
    全市场 PB 仅作广度展示(大盘蓝筹 vs 全市场的差异)。"""
    pe = store.get_index_pe_series(VALUATION_INDEX)
    pb_same = store.get_index_pb_series(VALUATION_INDEX)   # 沪深300 PB(同口径,用于 zone)
    pb_mkt = store.get_market_pb_series()                  # 全市场 PB(广度)
    out = {"pe_index": VALUATION_INDEX,
           "pe_ttm": np.nan, "pe_pct": np.nan,             # 沪深300 PE
           "pb": np.nan, "pb_pct": np.nan,                 # 沪深300 PB(同口径)
           "pb_market": np.nan, "pb_market_pct": np.nan,   # 全市场 PB(广度)
           "zone": "—", "valid": False}

    def _pct(series, col, lookback):
        s = series.iloc[-252 * lookback:] if lookback else series
        last = float(s[col].iloc[-1])
        return last, float((s[col] < last).sum()) / len(s)

    if len(pe) >= 20:
        out["pe_ttm"], out["pe_pct"] = _pct(pe, "pe_ttm", pe_lookback_years)
    if len(pb_same) >= 20:
        out["pb"], out["pb_pct"] = _pct(pb_same, "pb", pe_lookback_years)
    if len(pb_mkt) >= 20:
        last_mkt = float(pb_mkt["pb"].iloc[-1])
        out["pb_market"] = last_mkt
        if "pct_all" in pb_mkt.columns and not np.isnan(pb_mkt["pct_all"].iloc[-1]):
            out["pb_market_pct"] = float(pb_mkt["pct_all"].iloc[-1])
        else:
            out["pb_market_pct"] = float((pb_mkt["pb"] < last_mkt).sum()) / len(pb_mkt)

    # zone: 沪深300 同口径 PE+PB(四档)
    if not np.isnan(out["pe_pct"]) and not np.isnan(out["pb_pct"]):
        pe_p, pb_p = out["pe_pct"], out["pb_pct"]
        pe_lo, pe_hi = pe_p < PE_PCT_LOW, pe_p > PE_PCT_HIGH
        pb_lo, pb_hi = pb_p < PE_PCT_LOW, pb_p > PE_PCT_HIGH
        if pe_lo and pb_lo:
            out["zone"] = "低位·可激进"
        elif pe_hi and pb_hi:
            out["zone"] = "高位·宜保守"
        elif (pe_p > 0.50) != (pb_p > 0.50):    # 跨中线:一偏贵一偏便宜 → 分化
            out["zone"] = "结构分化·宜观望"
        else:                                    # 都在中间同侧 → 中性
            out["zone"] = "中位·中性"
        out["valid"] = True
    elif not np.isnan(out["pe_pct"]):   # fallback: 只有 PE(沪深300 PB 缺失时)
        p = out["pe_pct"]
        out["zone"] = ("低位·可激进" if p < PE_PCT_LOW
                       else "高位·宜保守" if p > PE_PCT_HIGH
                       else "中位·中性")
        out["valid"] = True
    return out


def diagnose_style(store, period: int = ti.MA_PERIOD) -> dict:
    """蓝筹(上证50) vs 成长(创业板指) 仓位倾向(S13)。"""
    blue = store.get_index_daily_series("000016")
    growth = store.get_index_daily_series("399006")
    if len(blue) < period or len(growth) < period:
        return {"blue_up": None, "growth_up": None, "lean": None, "valid": False}
    return ti.style_allocation(blue["close"], growth["close"], period)


def diagnose_layer(store, period: int = ti.MA_PERIOD,
                   lookback: int | None = None) -> dict:
    """顶层:整个指数择时层诊断(给 dashboard)。

    返回 {indices: {symbol: {name, close_last, date_last, diagnosis, valid}},
          valuation, style, period}."""
    indices = {}
    for sym, nm in BROAD_INDICES:
        df = store.get_index_daily_series(sym)
        if len(df) < period:
            indices[sym] = {"name": nm, "valid": False, "reason": "数据不足"}
            continue
        close = df["close"]
        indices[sym] = {
            "name": nm,
            "close_last": float(close.iloc[-1]),
            "date_last": str(close.index[-1]),
            "diagnosis": diagnose_index(close, period, lookback),
            "valid": True,
        }
    return {
        "indices": indices,
        "valuation": diagnose_valuation(store),
        "style": diagnose_style(store, period),
        "period": period,
    }
