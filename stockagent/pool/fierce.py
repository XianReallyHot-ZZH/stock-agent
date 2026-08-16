"""策略2 「业绩预期猛 × 深跌」(纯)——完全独立于 P1 positioning_score(不复用其乘法结构)。

陈老师方法论: 下跌≠便宜。埋伏候选必须双腿交叉——深跌(250日回撤≥40%,同 P1 的 drawdown
定义原语) × 业绩预期向上。分类型「猛」:
  周期 = 商品健康×股价落后(leading.commodity_alignment 语义) + 一致预期 g 确认
         (纯用研报 g 筛周期股 = 买在预期顶;商品价是领先指标、研报滞后)
  成长 = 三腿均值(前瞻 g 池内分位 / 4周上修 / 已报增速加速),≥2 腿才 valid
  价值 = v1 无「猛」概念(业绩弹性非其主要矛盾)
窗口: A(预告/快报落地→正式报截止,全类型) + B(季中预估,仅周期——商品可观测;
见 calendar.py 状态机)。
"""
from __future__ import annotations

import math

import pandas as pd

from ..tracker.leading import commodity_alignment
from ..tracker.stock_diagnose import price_reversal


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def deep_drawdown(close: pd.Series, min_dd: float = 0.40, lookback: int = 250,
                  recent: int = 60) -> dict:
    """深跌判定(纯): price_reversal 原语(250日 cummax 回撤 + 近60日涨幅)。
    deep = drawdown ≤ −min_dd(负值口径,同 P1/回测 metrics)。数据不足 → deep=False。"""
    pr = price_reversal(close, lookback=lookback, recent=recent)
    dd = pr["drawdown"]
    return {"drawdown": dd, "recent_return": pr["recent_return"],
            "deep": (not _nan(dd)) and float(dd) <= -abs(min_dd)}


def cyclic_fierce(com: dict, stock_recent_return, consensus_g, cfg: dict | None = None) -> dict:
    """周期猛分(纯): 商品 alignment(health×lag_factor,错杀度) + 一致预期 g 确认。

    com = leading.commodity_signal 输出(装配层读 store 后传入,函数保持纯)。
    confirmed = consensus_g ≥ min_consensus_g(默认 0.10;纯商品信号无研报确认 → 降半档显示)。
    score = alignment.score(0-1);valid = alignment 有效且 score ≥ min_fierce(默认 0.05,
    商品向下=双杀不是错杀,零分不入)。
    """
    cfg = cfg or {}
    lag_scale = float(cfg.get("lag_scale", 0.20))
    min_g = float(cfg.get("min_consensus_g", 0.10))
    min_fierce = float(cfg.get("min_fierce", 0.05))
    al = commodity_alignment(com, stock_recent_return, lag_scale=lag_scale)
    confirmed = (not _nan(consensus_g)) and float(consensus_g) >= min_g
    score = float(al.get("score", 0.0)) if al.get("valid") else 0.0
    return {
        "valid": bool(al.get("valid")) and score >= min_fierce,
        "fierce": score,
        "health": al.get("health"),
        "lag_factor": al.get("lag_factor"),
        "confirmed": bool(confirmed),
        "consensus_g": None if _nan(consensus_g) else float(consensus_g),
    }


def growth_fierce(g_rank_pct=None, revision_up=None, accel_pp=None,
                  cfg: dict | None = None) -> dict:
    """成长猛分(纯): 三腿均值,可用腿 ≥2 才 valid。

      rank_leg  = clamp(g_rank_pct/rank_gate, 0, 1)   前瞻 g(eps_fy2/fy1−1)池内分位
      rev_leg   = 1.0/0.0                              4 周一致预期上修(死区外)
      accel_leg = clamp(accel_pp/accel_min_pp, −1, 1)  已报增速加速(同尾二阶差,可为负)
    """
    cfg = cfg or {}
    rank_gate = float(cfg.get("rank_gate", 0.80))
    accel_min = float(cfg.get("accel_min_pp", 5.0))
    legs: dict[str, float] = {}
    if not _nan(g_rank_pct):
        legs["rank"] = min(max(float(g_rank_pct) / rank_gate, 0.0), 1.0)
    if revision_up is not None:
        legs["revision"] = 1.0 if bool(revision_up) else 0.0
    if not _nan(accel_pp):
        legs["accel"] = min(max(float(accel_pp) / accel_min, -1.0), 1.0)
    valid = len(legs) >= 2
    fierce = sum(legs.values()) / len(legs) if legs else 0.0
    return {"valid": valid, "fierce": fierce, "legs": legs,
            "g_rank_pct": None if _nan(g_rank_pct) else float(g_rank_pct),
            "accel_pp": None if _nan(accel_pp) else float(accel_pp)}


def accel_from_actuals(actuals: pd.DataFrame) -> float | None:
    """已报增速加速(纯): 最新期 np_yoy − 上年同期 np_yoy(pp)。

    累计口径对策——只比同尾期(Q1 vs 上年Q1、H1 vs 上年H1…),不同长度报告期不硬比;
    同尾缺 → None(诚实缺腿,growth_fierce 两腿仍可成立)。
    actuals indexed by report_period(YYYYMMDD str) [np_yoy]。
    """
    if actuals is None or len(actuals) == 0 or "np_yoy" not in actuals.columns:
        return None
    df = actuals[["np_yoy"]].dropna()
    if len(df) == 0:
        return None
    latest = str(df.index.max())
    tail = latest[4:]
    prior = f"{int(latest[:4]) - 1}{tail}"
    if prior not in df.index or latest not in df.index:
        return None
    return float(df.loc[latest, "np_yoy"]) - float(df.loc[prior, "np_yoy"])


def eligible_s2(deep: bool, win_state: str, fierce: dict, stock_type: str | None,
                cfg: dict | None = None) -> bool:
    """策略2 入表资格(纯): 深跌 ∧ 窗口有效 ∧ 猛分 valid ∧ 类型有猛分概念。

    窗口有效态 = windowA(预告/快报已落地) 或 windowB(仅 cyclic——商品可观测才可季中预估;
    陈老师「能分析出反转才提前下注」)。windowB 对非周期不开。
    """
    fierce_ok = bool(fierce and fierce.get("valid"))
    type_ok = stock_type in ("cyclic", "growth")
    win_ok = win_state == "windowA" or (win_state == "windowB" and stock_type == "cyclic")
    return bool(deep) and win_ok and fierce_ok and type_ok
