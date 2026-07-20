"""Research scoring — three-factor 性价比 (valuation + chip + trend). Pure functions.

Evaluated at the last bar of each input series (no lookahead). Reuses engine primitives:
  - indicators.percentile_rank / sma        (price-style stats on PE / close)
  - share_flow._classify_state / _share_change  (robust 2-of-3 institutional-direction consensus)

The chip factor is NON-monotone by design (重远投资观): sustained distribution mid-cycle =
bearish (no 性价比), but deep-drawdown + flat = bottoming = bullish. Encoded as a 6-phase
lookup (position × state) — thresholds/scores in params.yaml research.chip_score.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..engine import indicators as ind
from ..engine.signals import share_flow as sf

# 6-phase (position × institutional-state) → human label for the dashboard
_PHASE_LABEL = {
    "deep_accumulating": "低位加仓",   # deep drawdown + 聪明钱进场 → strongest bull
    "deep_stable": "见底",            # deep drawdown + 卖盘枯竭 → bull
    "deep_distributing": "下跌末段",  # deep drawdown + still dumping → unconfirmed
    "mid_accumulating": "加仓",
    "mid_stable": "观望",
    "mid_distributing": "兑现中段",   # article main thesis: 机构兑现·无性价比 → most bearish
    "low_accumulating": "高位加仓",   # near peak + still buying → risky
    "low_stable": "见顶预警",         # near peak + stalled → top warning
    "low_distributing": "高位减仓",
}


def _clamp(x, lo: float = 0.0, hi: float = 100.0) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return np.nan
    return float(max(lo, min(hi, x)))


def _pe_lookback_days(rp: dict) -> int:
    return int(rp.get("pe_lookback_years", 5)) * 250


# ---------- valuation ----------
def pe_percentile(pe_series: pd.Series, lookback: int) -> float:
    """Where the last PE sits in its history. 0=cheapest, 1=most expensive. NaN if short.

    Requires at least 20 points — a percentile over <20 readings is noise."""
    if pe_series is None:
        return np.nan
    s = pe_series.dropna()
    if len(s) < 20:
        return np.nan
    return ind.percentile_rank(s, lookback)


def valuation_score(pe_series: pd.Series, lookback: int) -> float:
    """PE historical percentile → cheaper = higher score (0-100). NaN if no PE."""
    pct = pe_percentile(pe_series, lookback)
    if np.isnan(pct):
        return np.nan
    return 100.0 * (1.0 - pct)


def value_dividend_yield(dividend_df, close, lookback_days: int = 365) -> float:
    """近 12 月股息率 = 最近 lookback_days 内单次分红之和 ÷ 当前价格(Phase 1-A 价值型)。

    dividend_df: etf_dividend store 的 cumulative_dividend 序列(indexed by date)。
    单次分红 = 累计差分(剔除 ≤0 的噪音)。NaN if 数据不足(覆盖稀疏属正常,不报错)。"""
    if dividend_df is None or len(dividend_df) < 2 or close is None or len(close) == 0:
        return np.nan
    cum = pd.to_numeric(dividend_df["cumulative_dividend"], errors="coerce").dropna()
    if len(cum) < 2:
        return np.nan
    per_event = cum.diff().dropna()
    per_event = per_event[per_event > 0]
    if len(per_event) == 0:
        return np.nan
    idx = pd.to_datetime(per_event.index)
    cutoff = idx.max() - pd.Timedelta(days=lookback_days)
    recent = per_event[idx >= cutoff]
    last_price = float(close.iloc[-1])
    if last_price <= 0:
        return np.nan
    return float(recent.sum()) / last_price


# ---------- chip ----------
def reduction_from_peak(shares: pd.Series) -> float:
    """1 - now/peak over the series. 0 = at historical peak (高仓位), 1 = fully drawn down (深底部)."""
    if shares is None or len(shares) < 2:
        return np.nan
    s = shares.dropna()
    if len(s) < 2:
        return np.nan
    peak = float(s.max())
    last = float(s.iloc[-1])
    if peak <= 0:
        return np.nan
    return 1.0 - last / peak


def _position_bucket(reduction: float, pos_deep: float, pos_low: float) -> str:
    if reduction >= pos_deep:
        return "deep"
    if reduction <= pos_low:
        return "low"
    return "mid"


def chip_score(shares: pd.Series, params: dict) -> tuple[float, str, float]:
    """Institutional chip factor. Returns (score 0-100, phase label, reduction_from_peak).

    direction = share_flow 2-of-3 consensus (ACCUMULATING/STABLE/DISTRIBUTING)
    position = reduction_from_peak (each ETF's own share peak)
    → 6-phase lookup in params.research.chip_score (captures article's non-monotone logic).
    """
    need = 121  # longest ROC window is 120; need 120+1 bars
    if shares is None or len(shares.dropna()) < need:
        return (np.nan, "数据不足", np.nan)

    sp = sf._share_params(params)  # {roc_short/mid/long, accum/dist_threshold, ...}
    rocs = [
        sf._share_change(shares, int(sp["roc_short_days"])),
        sf._share_change(shares, int(sp["roc_mid_days"])),
        sf._share_change(shares, int(sp["roc_long_days"])),
    ]
    if any(np.isnan(r) for r in rocs):
        return (np.nan, "数据不足", np.nan)
    state = sf._classify_state(
        rocs, float(sp["accum_threshold"]), float(sp["dist_threshold"]))
    state_key = {"ACCUMULATING": "accumulating", "STABLE": "stable",
                 "DISTRIBUTING": "distributing"}[state]

    rp = params.get("research", {})
    red = reduction_from_peak(shares)
    if np.isnan(red):
        return (np.nan, "数据不足", np.nan)
    bucket = _position_bucket(red, float(rp["chip"]["pos_deep"]), float(rp["chip"]["pos_low"]))
    key = f"{bucket}_{state_key}"
    score = float(rp["chip_score"][key])
    return (score, _PHASE_LABEL[key], red)


# ---------- trend ----------
def trend_score(close: pd.Series, ma_period: int) -> float:
    """close vs MA → 0-100 (above MA = higher). Reuses ind.sma."""
    if close is None or len(close.dropna()) < ma_period:
        return np.nan
    ma = ind.sma(close, ma_period)
    if np.isnan(ma) or ma <= 0:
        return np.nan
    return _clamp(50.0 + (float(close.iloc[-1]) / ma - 1.0) * 300.0)


# ---------- composite ----------
def composite(val: float, chip: float, trend: float, weights: dict,
              has_valuation: bool) -> float:
    """Weighted 性价比 (0-100). Missing factors are dropped + weights renormalized.
    Non-equity (no PE) → chip + trend equal-weight."""
    if has_valuation and not np.isnan(val):
        parts = [(val, weights["valuation"]), (chip, weights["chip"]), (trend, weights["trend"])]
    else:
        parts = [(chip, 0.5), (trend, 0.5)]
    avail = [(v, w) for v, w in parts if not np.isnan(v)]
    if not avail:
        return np.nan
    tw = sum(w for _, w in avail)
    return sum(v * w for v, w in avail) / tw


def analyze_etf(
    close: pd.Series,
    shares: pd.Series | None,
    pe_series: pd.Series | None,
    params: dict,
    has_valuation: bool = True,
    style: str = "growth",
    dividend_df: pd.DataFrame | None = None,
) -> dict:
    """One-ETF snapshot. Pure: takes series, returns the factor dict the report consumes.

    style (Phase 1-A 三类分类):
      - growth/value(has_valuation=True): valuation = 100*(1 - PE分位)(PE分位低 = 便宜 = 高分)
      - cyclic: 命门是 PB,板块 PB 无数据源 → build_snapshots 设 has_valuation=False,
        不用 PE 反向(误差大,宁缺毋滥),valuation 留空,只用通用底盘(chip + trend)。
    dividend_df 给 value 算股息率(附加显示,不进 composite)。默认 style='growth' = 现有行为。"""
    rp = params.get("research", {})
    lookback = _pe_lookback_days(rp)
    pe_pct = pe_percentile(pe_series, lookback) if (has_valuation and pe_series is not None) else np.nan

    # value/growth: PE 分位低=便宜=高分。cyclic 由调用方设 has_valuation=False,不进此分支。
    val = 100.0 * (1.0 - pe_pct) if (has_valuation and not np.isnan(pe_pct)) else np.nan

    chip, label, red = chip_score(shares, params)
    trend = trend_score(close, int(rp["ma_period"]))
    comp = composite(val, chip, trend, rp["weights"], has_valuation and not np.isnan(val))

    # value 型股息率(附加显示;数据稀疏故不进 composite,免扰乱无数据的标的)
    div_yield = (value_dividend_yield(dividend_df, close)
                 if style == "value" and dividend_df is not None else np.nan)

    def _ok(x):
        return not (x is None or (isinstance(x, float) and np.isnan(x)))

    # data_sufficient: every APPLICABLE factor is computable. Excludes ETFs whose data
    # source is incomplete (e.g. chip "数据不足" with no share history → 515880). An ETF
    # with no PE BY DESIGN (broad/QDII/commodity: has_valuation=False) is still sufficient —
    # valuation isn't applicable, not missing.
    data_sufficient = _ok(chip) and _ok(trend) and (_ok(val) if has_valuation else True)

    return {
        "valuation": val,
        "pe_percentile": pe_pct,
        "chip": chip,
        "chip_phase": label,
        "reduction_from_peak": red,
        "trend": trend,
        "composite": comp,
        "data_sufficient": data_sufficient,
        "style": style,
        "dividend_yield": div_yield,
    }
