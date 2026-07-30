"""Cyclical-reversal screen (read-only) — 业绩最猛 × 前期跌得最多 × 财报时效。

案例:锂矿股(天齐预增 3276%~4935%、中矿资源 +1078%~1302%)= "业绩最猛 + 前期跌幅最大"的周期
反转组合,且有时效(下个业绩窗口前须兑现,预期放缓后失效)。本模块把该直觉量化成一个 0-100 综合
分,仅用于 research 只读诊断看板 + 告警(R1),**不喂交易引擎**(etf_earnings 无时点历史→回测前视偏差,
且撞"research 不喂引擎"红线)。

纯打分函数 cyclical_reversal_score(标量) + 薄装配 cyclical_reversal_snapshot(从 close/earn 取数,
注入 ETF snapshot)。回撤算法照 backtest/metrics.py:26 的 cummax。
"""
from __future__ import annotations

import math
from datetime import datetime

import pandas as pd  # noqa: F401  (类型/契约对齐 research 包;snapshot 吃 pd.Series)


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _days_between(asof: str | None, period: str | None) -> float:
    """asof(YYYY-MM-DD) − 报告期期末(YYYYMMDD) 的天数。解析失败 → NaN(届时 freshness=NaN)。

    ETF 聚合 earnings 无明确披露日,用 report_period 期末作新鲜度近似(Tier-1)。"""
    if not asof or not period:
        return float("nan")
    try:
        a = datetime.strptime(str(asof)[:10], "%Y-%m-%d")
        p = datetime.strptime(str(period)[:8], "%Y%m%d")
        return float((a - p).days)
    except Exception:  # noqa: BLE001
        return float("nan")


def cyclical_reversal_score(yoy: float, drawdown: float, days_since_report: float,
                            yoy_scale: float = 1.0, drawdown_scale: float = 0.5,
                            freshness_half_life_days: float = 120.0,
                            min_yoy: float = 0.0) -> dict:
    """周期反转综合分(只读,0-100):业绩因子 × 回撤因子 × 时效衰减。

      yoy_factor      = clamp(yoy / yoy_scale, 0, 1)        yoy<min_yoy → 0(只看正增长反转)
      drawdown_factor = clamp(|drawdown| / drawdown_scale, 0, 1)   drawdown 传负值(-0.5=回撤 50%)
      freshness       = 0.5 ** (days_since_report / half_life)     新鲜→1, 陈旧衰减
      score           = yoy_factor × drawdown_factor × freshness × 100
    任一输入 NaN → valid=False。"""
    nan = float("nan")
    if _nan(yoy) or _nan(drawdown) or _nan(days_since_report):
        return {"valid": False, "score": nan, "yoy": yoy, "drawdown": drawdown,
                "days_since_report": days_since_report, "yoy_factor": nan,
                "drawdown_factor": nan, "freshness": nan}
    yf = 0.0 if yoy < min_yoy else min(max(yoy / yoy_scale, 0.0), 1.0)
    df = min(max(abs(drawdown) / drawdown_scale, 0.0), 1.0)
    fr = (0.5 ** (days_since_report / freshness_half_life_days)
          if freshness_half_life_days > 0 else 1.0)
    return {"valid": True, "score": yf * df * fr * 100.0,
            "yoy": yoy, "drawdown": drawdown, "days_since_report": days_since_report,
            "yoy_factor": yf, "drawdown_factor": df, "freshness": fr}


def cyclical_reversal_snapshot(symbol: str, close, earn, params: dict,
                               asof: str | None = None) -> dict:
    """单只 cyclic ETF 的反转快照:etf_earnings.weighted_yoy + close 算 lookback 最大回撤 +
    report_period 算时效 → cyclical_reversal_score。返回薄 dict 注入 ETF snapshot(对外键 reversal_score)。

    earn  = store.get_etf_earnings(symbol) dict(或 None);
    close = 升序 close Series(已 end=asof 切片,与 build_snapshots 一致)。"""
    rp = (params.get("research", {}) or {}).get("cyclical_reversal", {}) or {}
    lookback = int(rp.get("drawdown_lookback_days", 250))
    # 250 日最大回撤(照 backtest/metrics.py:26 的 cummax):当前 vs 窗口内最高点
    drawdown = float("nan")
    if close is not None and len(close) >= 2:
        c = close.tail(lookback)
        if len(c) >= 2:
            drawdown = float((c / c.cummax() - 1.0).min())
    # weighted_yoy 来自 etf_earnings,单位是百分数(184.87=184.87%)→ 转 /100 喂 score(分数口径)
    yoy = (float(earn["weighted_yoy"]) / 100.0
           if (earn and not _nan(earn.get("weighted_yoy"))) else float("nan"))
    period = earn.get("report_period") if earn else None
    if asof is None:
        asof = str(close.index[-1]) if (close is not None and len(close)) else None
    days = _days_between(asof, period)
    out = cyclical_reversal_score(
        yoy, drawdown, days,
        yoy_scale=float(rp.get("yoy_scale", 1.0)),
        drawdown_scale=float(rp.get("drawdown_scale", 0.5)),
        freshness_half_life_days=float(rp.get("freshness_half_life_days", 120.0)),
        min_yoy=float(rp.get("min_yoy", 0.0)))
    out["reversal_score"] = out.pop("score")  # 对外键名(dashboard/alerts 读 reversal_score)
    out.update({"report_period": period, "symbol": symbol})
    return out
