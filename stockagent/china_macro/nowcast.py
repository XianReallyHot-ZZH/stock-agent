"""v2 纯函数:社融可观测成分(地方债 nowcast) + 会议→M2 转向历史回放。

纪律:**观测(nowcast)非预测**——社融分子端只有政府债(地方债明细)/企业债可提前观测,
信贷(最大头)黑箱诚实留白;会议→M2 回放是历史统计对照,非因果、n 小。
"""
from __future__ import annotations

import pandas as pd

# 锚点月 → 人读标签(典型时点;月度序列无需精确日期)
ANCHOR_LABELS = {3: "3 月(两会)", 4: "4 月末(政治局·经济)",
                 7: "7 月末(政治局·经济)", 12: "12 月(政治局+中央经济工作会议)"}


def lgb_monthly_series(detail: pd.DataFrame, amt_col: str = "actual_amt") -> pd.Series:
    """逐券发行明细(地方债/国债同构) → 月度实际发行合计(亿元),index 'YYYY-MM' 升序。
    issue_date/金额缺失跳过。口径注记:cninfo 逐券(含再融资/跨市场),绝对量级未与
    官方月报交叉校验——用于月度节奏与月内累计的**相对观察**。"""
    if detail is None or len(detail) == 0 or amt_col not in detail.columns:
        return pd.Series(dtype=float)
    if "issue_date" not in detail.columns:
        return pd.Series(dtype=float)
    s = pd.to_numeric(detail[amt_col], errors="coerce")
    ym = detail["issue_date"].astype(str).str[:7]
    keep = ym.str.fullmatch(r"\d{4}-\d{2}", na=False)   # issue_date 缺失(None/NaN)→桶 'None'/'nan',剔除
    s, ym = s[keep], ym[keep]
    s.index = ym
    return s.dropna().groupby(level=0).sum().sort_index()


def bond_monthly_stack(lgb_detail: pd.DataFrame, tsy_detail: pd.DataFrame) -> dict:
    """政府债月度发行(亿) = 国债 + 地方债 堆叠原料。Returns {'tsy','lgb','total'}
    (total=两者对齐求和,单侧缺月按 0)。"""
    t = lgb_monthly_series(tsy_detail)
    l = lgb_monthly_series(lgb_detail)
    total = t.add(l, fill_value=0.0).sort_index()
    return {"tsy": t, "lgb": l, "total": total}


def mtd_progress(monthly: pd.Series, ym: str, lookback: int = 12) -> dict:
    """本月至今(进行时) vs 近 lookback 个完整月均值(纯)。Returns
    {cur, base, ratio, last_full}: cur=本月累计(亿);base=完整月均值;ratio=cur/base;
    last_full=最近完整月值。样本不足→NaN。"""
    empty = {"cur": float("nan"), "base": float("nan"),
             "ratio": float("nan"), "last_full": float("nan")}
    if monthly is None or len(monthly) == 0:
        return empty
    cur = float(monthly.get(ym, float("nan")))
    full = monthly[monthly.index < ym].astype(float)
    if len(full) == 0:
        return {**empty, "cur": cur}
    last_full = float(full.iloc[-1])
    base = float(full.tail(lookback).mean())
    ratio = cur / base if base and cur == cur else float("nan")
    return {"cur": cur, "base": base, "ratio": ratio, "last_full": last_full}


def meeting_anchor_stats(yoy: pd.Series, anchors: tuple = (3, 4, 7, 12),
                         ahead: int = 3) -> dict:
    """锚点月会后 ahead 个月 M2 同比变化的历史统计(纯,月度锚点=月即可,无需精确会议日)。
    yoy=月度同比(index 'YYYY-MM-01');对每年每锚点算 delta = yoy[锚月+ahead] − yoy[锚月]。
    Returns {anchor: {label, n, up_prob, median_delta, latest_ym, latest_delta}}——
    latest=最近一个**前后值齐**的锚点(还没走完 ahead 的最新锚不进统计,防半程假读)。"""
    vals = pd.to_numeric(yoy, errors="coerce").dropna().sort_index()
    m = {str(i)[:7]: float(v) for i, v in vals.items()}
    yms = sorted(m)
    if not yms:
        return {}

    def _shift(ym: str, k: int) -> str | None:
        y, mm = int(ym[:4]), int(ym[5:7])
        t = y * 12 + (mm - 1) + k
        return f"{t // 12:04d}-{t % 12 + 1:02d}"

    out: dict = {}
    for a in anchors:
        deltas: list[tuple[str, float]] = []
        for ym in yms:
            if int(ym[5:7]) != a:
                continue
            nxt = _shift(ym, ahead)
            if nxt in m:
                deltas.append((ym, m[nxt] - m[ym]))
        if not deltas:
            continue
        ds = [d for _, d in deltas]
        out[a] = {
            "label": ANCHOR_LABELS.get(a, f"{a} 月"),
            "n": len(ds),
            "up_prob": sum(1 for d in ds if d > 0) / len(ds),
            "median_delta": float(pd.Series(ds).median()),
            "latest_ym": deltas[-1][0],
            "latest_delta": deltas[-1][1],
        }
    return out
