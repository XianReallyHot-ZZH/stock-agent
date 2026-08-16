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

from datetime import datetime
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

# YYYYMMDD report-period suffix → Chinese 业绩预告 window name.
_PERIOD_NAME = {"1231": "年报预告", "0331": "一季报预告",
                "0630": "中报预告", "0930": "三季报预告"}


def period_label(period: Optional[str]) -> str:
    """Map a YYYYMMDD report_period to a Chinese label, e.g. '20260630' → '2026中报预告'.

    Used to surface which disclosure window the earnings signal draws from (freshness). Unknown /
    malformed periods fall back to 'YYYY报告期(MMDD)' so the dashboard never shows a blank.
    """
    if not period or not isinstance(period, str) or len(period) != 8 or not period.isdigit():
        return "—"
    y, tail = period[:4], period[4:]
    return f"{y}{_PERIOD_NAME[tail]}" if tail in _PERIOD_NAME else f"{y}报告期({tail})"


def latest_report_period(now: datetime) -> str:
    """Most recent 业绩预告 report period (YYYYMMDD) whose disclosure window is open at `now`.

    A-share 业绩预告 disclosure cutoffs: 年报 1/31, 一季报 4/15, 半年报 7/15, 三季报 10/15.
    We switch to each period as its window OPENS (not at the cutoff), so the dashboard surfaces the
    live disclosure season even before it's 100% complete — the per-ETF coverage then flags how far
    along it is. Window-open dates:
        年报 YYYY1231  → next year, from 1/1
        一季报 YYYY0331 → from 4/15
        半年报 YYYY0630 → from 7/1   (B 方案: 中报窗口即纳入, 不等 7/15 截止)
        三季报 YYYY0930 → from 10/1
    Pure (no I/O); DataManager._latest_report_period delegates here with datetime.now().
    """
    y, md = now.year, (now.month, now.day)
    if md >= (10, 1):     return f"{y}0930"   # 三季报预告窗口
    if md >= (7, 1):      return f"{y}0630"   # 半年报预告窗口(7/15 截止, 高峰即纳入)
    if md >= (4, 15):     return f"{y}0331"   # 一季报预告窗口(4/15~4/30)
    return f"{y - 1}1231"                      # 年报预告窗口(1/31 截止)


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


# ---------------- 一致预期聚合 (E2, docs/EXECUTION_PLAN-ETF业绩预期.md §4) ----------------
# 与预告层互补: 预告=已披露的区间事实(样本偏极端, 只看广度), 一致预期=分析师前瞻(日更软信息,
# 覆盖权重中位数≈82% vs 预告期≈32%, 调研§5)。均为 INFORMATIONAL, 永不喂引擎。

LABEL_C_HIGH = "预期高增"
LABEL_C_UP = "预期改善"
LABEL_C_FLAT = "预期平稳"
LABEL_C_DOWN = "预期承压"
LABEL_C_CRASH = "预期负增"

_RATE_COLS = ("rating_buy", "rating_over", "rating_neutral", "rating_reduce", "rating_sell")


def _empty_consensus(n_all: int) -> dict:
    return {"weighted_g": float("nan"), "median_g": float("nan"), "coverage": 0.0,
            "n_names": 0, "n_all": int(n_all), "buy_ratio": float("nan"),
            "fy1_year": None, "fy2_year": None}


def aggregate_consensus(constituents: Optional[pd.DataFrame],
                        snapshot: Optional[pd.DataFrame],
                        min_reports: int = 3) -> dict:
    """Join 指数成分(官方权重) × 一致预期快照 → 聚合信号 dict (pure, no I/O).

    Data shape contract (both from the E0/E1 data layer):
      constituents : DataFrame[code(str), weight(float)]  — store.get_constituents()
      snapshot     : DataFrame indexed by code with [n_reports, rating_*, eps_fy1, eps_fy2,
                    fy1_year, fy2_year] — store.get_consensus_snapshot()[1]
    Returns {weighted_g, median_g, coverage, n_names, n_all, buy_ratio, fy1_year, fy2_year}:
      g_i = eps_fy2/eps_fy1 − 1 (财年滚动对齐由快照列保证, 年末翻滚自动跟上)
      usable = n_reports ≥ min_reports 且 eps_fy1 > 0 且两年 EPS 齐 — 研报数门挡覆盖偏差
               (调研§3.5), 负/零基数剔除 (EPS 比值在亏损基数上无意义)
      weighted_g = Σ(w·g)/Σw over usable · median_g = median(g) (抗单家极值预测, 调研§4.3)
      coverage = usable weight / total constituents weight
      buy_ratio = Σ(w·buy)/Σ(w·评级总数) over usable — 评级结构参考, 只展示不进分
    """
    if (constituents is None or snapshot is None
            or not len(constituents) or not len(snapshot)):
        return _empty_consensus(0 if constituents is None else len(constituents))

    h = constituents[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return _empty_consensus(len(h))

    m = h.merge(snapshot.reset_index(), on="code", how="left")
    usable = m[(m["n_reports"] >= min_reports) & m["eps_fy1"].notna() & (m["eps_fy1"] > 0)
               & m["eps_fy2"].notna()].copy()
    if not len(usable):
        return _empty_consensus(len(h))

    usable["g"] = usable["eps_fy2"] / usable["eps_fy1"] - 1.0
    uw = float(usable["weight"].sum())

    rc = [c for c in _RATE_COLS if c in usable.columns]
    buy_ratio = float("nan")
    if rc:
        rated = usable.copy()
        rated["_rtot"] = rated[rc].sum(axis=1)
        rated = rated[rated["_rtot"] > 0]
        if len(rated):
            rtot = float((rated["weight"] * rated["_rtot"]).sum())
            buy = float((rated["weight"] * rated["rating_buy"]).sum())
            buy_ratio = buy / rtot if rtot > 0 else float("nan")

    fy1 = usable["fy1_year"].iloc[0] if "fy1_year" in usable.columns else None
    fy2 = usable["fy2_year"].iloc[0] if "fy2_year" in usable.columns else None
    return {
        "weighted_g": float((usable["g"] * usable["weight"]).sum() / uw),
        "median_g": float(usable["g"].median()),
        "coverage": uw / total_w,
        "n_names": int(len(usable)),
        "n_all": int(len(h)),
        "buy_ratio": buy_ratio,
        "fy1_year": int(fy1) if fy1 == fy1 and fy1 is not None else None,
        "fy2_year": int(fy2) if fy2 == fy2 and fy2 is not None else None,
    }


def _clabel(score: float) -> str:
    if score >= 70:
        return LABEL_C_HIGH
    if score >= 58:
        return LABEL_C_UP
    if score >= 42:
        return LABEL_C_FLAT
    if score >= 30:
        return LABEL_C_DOWN
    return LABEL_C_CRASH


def consensus_score(signal: Optional[dict], params: dict) -> tuple[float, str]:
    """Map an aggregate_consensus signal to (score 0-100, label).

    score = clamp(50 + median_g%·0.4) — 与 earnings_score 同标度同带宽(水平值口径, 非变化量;
    修正动量是 E4 的独立信号). Gates: coverage < research.earnings.consensus.min_weight_cov
    (default 0.40, 调研§5 的 B 级线) 或可用成分 < min_names (default 5) → (NaN, '数据不足').
    """
    if signal is None:
        return (float("nan"), LABEL_INSUFF)
    cp = ((params.get("research", {}) or {}).get("earnings", {}) or {}).get("consensus", {}) or {}
    min_cov = float(cp.get("min_weight_cov", 0.40))
    min_n = int(cp.get("min_names", 5))
    cov = signal.get("coverage")
    n = int(signal.get("n_names", 0))
    if cov is None or cov < min_cov or n < min_n:
        return (float("nan"), LABEL_INSUFF)

    med = signal.get("median_g")
    med = 0.0 if (med is None or pd.isna(med)) else float(med)
    score = max(0.0, min(100.0, 50.0 + med * 100.0 * 0.4))
    return (score, _clabel(score))
