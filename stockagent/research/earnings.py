"""ETF earnings-expectation signal — aggregate 业绩预告 over the ETF's own holdings.

Pure functions (no I/O): take a holdings DataFrame + a 业绩预告 forecast DataFrame, return an
aggregated signal dict and a 0-100 score + label. This is the forward-looking layer the
three-factor model (估值/筹码/趋势) lacks — it separates value traps (cheap + earnings falling)
from justified valuations (expensive + earnings exploding).

Phase 1: INFORMATIONAL ONLY — the score is displayed but does NOT enter the 性价比 composite.

Data shape contract:
  holdings  : DataFrame[code(str), weight(float, % of NAV)] (+ optional name/period cols)
  forecast  : DataFrame indexed by code(str), columns [yoy(float %), type(str 预告类型)]
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

# 业绩预告 type enum (11 values) → bullish / bearish buckets. '不确定' lands in neither.
BULL = {"预增", "略增", "扭亏", "续盈", "减亏"}
BEAR = {"预减", "略减", "首亏", "续亏", "增亏"}

# Score → label bands (informational; not in composite).
LABEL_HIGH = "业绩高增"
LABEL_UP = "业绩改善"
LABEL_FLAT = "业绩平稳"
LABEL_DOWN = "业绩承压"
LABEL_CRASH = "业绩恶化"
LABEL_INSUFF = "数据不足"


def _empty_signal(n_holdings: int) -> dict:
    return {
        "weighted_yoy": float("nan"), "median_yoy": float("nan"),
        "bull_ratio": float("nan"), "bear_ratio": float("nan"),
        "coverage": 0.0, "n_holdings": int(n_holdings), "n_matched": 0,
    }


def aggregate_earnings(holdings: Optional[pd.DataFrame], forecast: Optional[pd.DataFrame]) -> dict:
    """Join an ETF's holdings to the 业绩预告 forecast and aggregate.

    Returns {weighted_yoy, median_yoy, bull_ratio, bear_ratio, coverage, n_holdings, n_matched}.
      weighted_yoy = Σ(yoy·weight)/Σ(weight) over matched (yoy-usable) holdings
      median_yoy   = median yoy over matched (robust to ±200% outliers)
      bull/bear_ratio = weight of bullish/bearish types ÷ matched weight
      coverage     = matched weight ÷ total holdings weight (data-completeness)
    A holding counts as matched only if its code is in the forecast WITH a usable yoy.
    """
    if holdings is None or forecast is None or len(holdings) == 0 or len(forecast) == 0:
        return _empty_signal(0 if holdings is None else len(holdings))

    h = holdings[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return _empty_signal(len(h))

    fc = forecast[["yoy", "type"]].copy()
    fc.index = fc.index.astype(str)
    m = h.merge(fc, left_on="code", right_index=True, how="left")
    m["yoy"] = pd.to_numeric(m["yoy"], errors="coerce")
    matched = m.dropna(subset=["yoy"])
    matched_w = float(matched["weight"].sum())
    if matched_w <= 0 or len(matched) == 0:
        return _empty_signal(len(h))

    yoy = matched["yoy"].astype(float)
    bull_w = float(matched.loc[matched["type"].isin(BULL), "weight"].sum())
    bear_w = float(matched.loc[matched["type"].isin(BEAR), "weight"].sum())
    return {
        "weighted_yoy": float((yoy * matched["weight"]).sum() / matched_w),
        "median_yoy": float(yoy.median()),
        "bull_ratio": bull_w / matched_w,
        "bear_ratio": bear_w / matched_w,
        "coverage": matched_w / total_w,
        "n_holdings": int(len(h)),
        "n_matched": int(len(matched)),
    }


def _label(score: float) -> str:
    if score >= 70:
        return LABEL_HIGH
    if score >= 58:
        return LABEL_UP
    if score >= 42:
        return LABEL_FLAT
    if score >= 30:
        return LABEL_DOWN
    return LABEL_CRASH


def earnings_score(signal: Optional[dict], params: dict) -> tuple[float, str]:
    """Map an aggregate_earnings signal to a (score 0-100, label).

    score = clamp(50 + median_yoy·0.4 + (bull_ratio−bear_ratio)·30). Uses median_yoy (robust to
    extreme single-name YoY like 猪周期 −236%) and the bull−bear breadth as the stable headline.
    Returns (NaN, '数据不足') when coverage < research.earnings.min_coverage (default 0.30) or
    matched names < min_matched (default 5).
    """
    if signal is None:
        return (float("nan"), LABEL_INSUFF)
    rp = (params.get("research", {}) or {}).get("earnings", {}) or {}
    min_cov = float(rp.get("min_coverage", 0.30))
    min_n = int(rp.get("min_matched", 5))
    cov = signal.get("coverage")
    n = int(signal.get("n_matched", 0))
    if cov is None or cov < min_cov or n < min_n:
        return (float("nan"), LABEL_INSUFF)

    med = signal.get("median_yoy")
    med = 0.0 if (med is None or pd.isna(med)) else float(med)
    br = signal.get("bull_ratio")
    br = 0.0 if (br is None or pd.isna(br)) else float(br)
    be = signal.get("bear_ratio")
    be = 0.0 if (be is None or pd.isna(be)) else float(be)
    score = max(0.0, min(100.0, 50.0 + med * 0.4 + (br - be) * 30.0))
    return (score, _label(score))
