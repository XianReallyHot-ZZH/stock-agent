"""A 类领先信号(非价格 · 预判下期业绩)——把"提前埋伏"从企稳后右侧推进到底前左侧。

回测证实:任何基于"价格止跌"的过滤在底的那一刻都失效(底时价格仍跌)。真正能提前的是**非价格**的
领先指标。本模块按板块路由:
  - 强形式·周期(日频):上游商品价(碳酸锂/铜/螺纹钢/黄金/原油)→ 预判周期股下期业绩。
  - 弱形式·通用(季频):基金持仓变动(增仓/减仓)→ 聪明钱动向。
  - 保险代理:大盘季度涨幅 → 近似险企投资收益方向(akshare 无持仓浮盈源,粗代理)。

日频聪明钱(每股北上 stock_hsgt_individual_em 停滞在 2024、主力资金 push2his 被拦)取不到,故弱形式
退到季频基金持仓。leading_signal 喂回 positioning_score 的 earnings_outlook=max(业绩拐头, 领先信号)。
"""
from __future__ import annotations

import math

import pandas as pd

from ..config import get_config


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def commodity_score(series: pd.Series, yoy_window: int = 252) -> dict:
    """商品价 → 领先分(纯,0-1):同比涨幅映射,上涨=利好生产商业绩。

      yoy = 末值 / window 日前 − 1;score = clamp(0.5 + yoy, 0, 1)(±50% 涨跌→0/1)。
    数据不足 → valid=False。"""
    if series is None or len(series) < 2:
        return {"valid": False}
    s = series.astype(float).dropna()
    n = min(yoy_window, len(s) - 1)
    if n < 1:
        return {"valid": False}
    yoy = float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1.0   # 末值 vs n+1 期前
    score = min(max(0.5 + yoy, 0.0), 1.0)
    return {"valid": True, "score": score, "yoy": yoy}


def commodity_signal(store, variety: str, asof: str | None = None,
                     config=None) -> dict:
    """读 commodity_price 序列(point-in-time:end=asof)→ commodity_score + 近期 + 背离标记 + 标签。"""
    cfg = config or get_config()
    lp = (cfg.params.get("stock", {}) or {}).get("leading", {}) or {}
    yw = int(lp.get("commodity_yoy_window", 252))
    rw = int(lp.get("commodity_recent_window", 60))
    s = store.get_commodity_series(variety, end=asof)
    out = commodity_score(s, yoy_window=yw)
    if not out.get("valid"):
        return out
    ss = s.astype(float)
    n = min(rw, len(ss) - 1)
    recent = float(ss.iloc[-1]) / float(ss.iloc[-1 - n]) - 1.0 if n >= 1 else float("nan")
    out["recent"] = recent
    out["variety"] = variety
    # 背离:同比涨(>10%)但近期回落(<−5%)→ 前瞻恶化(M1 预警用)
    out["divergent"] = bool(out["yoy"] > 0.10 and (not _nan(recent)) and recent < -0.05)
    out["down"] = bool(out["yoy"] < 0)   # 同比转负 → 周期确认向下(M2 卖出用)
    out["label"] = f"{variety}同比{out['yoy']:+.0%}"
    return out


def leading_signal(symbol: str, store, config=None, asof: str | None = None) -> dict:
    """A 类领先信号(板块路由):周期股→商品价;弱形式(基金持仓)/保险代理待补(TODO)。

    返回 {valid, score(0-1), components, label}。score=max(各适用组件);无适用/全缺 → valid=False。"""
    cfg = config or get_config()
    sp = cfg.params.get("stock", {}) or {}
    variety = (sp.get("commodity_map", {}) or {}).get(symbol)

    scores, components, labels = [], {}, []
    if variety:
        cs = commodity_signal(store, variety, asof=asof, config=cfg)
        if cs.get("valid"):
            components["commodity"] = cs
            scores.append(cs["score"])
            labels.append(cs["label"])
    # TODO(P1弱形式): fund_hold_score(季频基金持仓增减仓)
    # TODO(P3保险):   insurance_proxy_score(沪深300 季度涨幅,近似险企投资收益)

    return {
        "valid": bool(scores),
        "score": max(scores) if scores else 0.0,
        "components": components,
        "label": " ".join(labels),
    }
