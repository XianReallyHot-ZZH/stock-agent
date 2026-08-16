"""策略1 偏离超卖复合分(纯)——自身历史 MA60 偏离百分位触发 + 基本面护栏 + 企稳加分。

个股 ≠ ETF: 个股偏离极值经常是基本面恶化的合理定价(暴雷/阴跌常驻极值榜),故触发器之外
必须叠护栏(预亏族预告/预期大幅下修/变脸向下→拦截)与企稳加分(positioning_score 防飞刀
教训的继承)。百分位是**自身历史口径**(截面只做展示排序,异方差教训)。

  oversold_score      复合分(加法合成: depth_weight×深度 + stabilize_weight×企稳)
  guardrails          四道闸(非ST 在 universe 已滤,此处三闸+冗余复核)
  oversold_run_start  当前超卖段的起点(point-in-time expanding 分位,「新」徽标用)
"""
from __future__ import annotations

import math

import pandas as pd

from ..research.timing import deviation_series

# 预亏族预告类型(护栏拦截;东财 stock_yjyg_em 的 type 词表子集)
LOSS_FORECAST_TYPES = ("首亏", "续亏", "增亏")


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def guardrails(forecast_type: str | None, revision_pct: float | None,
               facechange_direction: str | None, cfg: dict | None = None) -> dict:
    """策略1 基本面护栏(纯)。三道闸(非 ST 由 universe 名称过滤先行):

      ① 预告类型 ∉ 预亏族(首亏/续亏/增亏)  — 美团型连亏恶化的硬排除
      ② 一致预期 4 周修正 ≥ −guard_revision_drop_pct(%) — 预期大幅下修 = 分析师用脚投票
      ③ 变脸方向 ≠ 'down'(facechange 检测)   — 腾讯型拐点/小米型跳档的软排除
    数据缺(None)→ 该闸放行但记 flag(缺数据 ≠ 已知风险;诚实显示「缺」)。
    Returns {pass_, flags:[...]}。
    """
    cfg = cfg or {}
    loss_types = tuple(cfg.get("guard_forecast_loss_types", LOSS_FORECAST_TYPES))
    drop_pct = float(cfg.get("guard_revision_drop_pct", 3.0))
    flags: list[str] = []
    ok = True
    if forecast_type is None:
        flags.append("预告缺")
    elif forecast_type in loss_types:
        ok = False
        flags.append(f"预亏族({forecast_type})")
    if revision_pct is None:
        flags.append("修正缺")
    elif float(revision_pct) * 100.0 < -drop_pct:
        ok = False
        flags.append(f"预期下修{float(revision_pct) * 100:.1f}%")
    if facechange_direction is None:
        flags.append("变脸缺")
    elif facechange_direction == "down":
        ok = False
        flags.append("业绩变脸向下")
    return {"pass_": ok, "flags": flags}


def oversold_score(dev: dict, stabilize: float, guards: dict,
                   cfg: dict | None = None) -> dict:
    """策略1 复合分(纯,0-100 加法合成)。dev = research.timing.deviation_extremes 输出。

      trigger = dev.valid and dev.pct ≤ trigger_pct and dev.cur_dev < 0
      —— 自身历史百分位(非横截面)且**真的在 MA60 下方**:超稳态上行股的 dev 恒定,
         分位≈0 但人在线上方,不该叫超卖(全等值并列时 (dev<cur).sum()=0 的并列退化防御)。
      depth   = clamp((trigger_pct − pct)/trigger_pct, 0, 1)   分位越深越高
      score   = 100 × (depth_weight×depth + stabilize_weight×stabilize)
    护栏未过 → score=None(行仍显示,排除出候选/排序);未触发 → score=None + trigger=False。
    """
    cfg = cfg or {}
    trigger_pct = float(cfg.get("trigger_pct", 0.05))
    dw = float(cfg.get("depth_weight", 0.6))
    sw = float(cfg.get("stabilize_weight", 0.4))
    trigger = (bool(dev.get("valid")) and not _nan(dev.get("pct"))
               and float(dev["pct"]) <= trigger_pct
               and not _nan(dev.get("cur_dev")) and float(dev["cur_dev"]) < 0.0)
    depth = 0.0
    score = None
    if trigger:
        depth = min(max((trigger_pct - float(dev["pct"])) / trigger_pct, 0.0), 1.0)
        if guards.get("pass_", True):
            score = 100.0 * (dw * depth + sw * float(stabilize))
    return {"trigger": trigger, "score": score, "dev_pct": dev.get("pct"),
            "cur_dev": dev.get("cur_dev"), "depth": depth, "stabilize": float(stabilize),
            "guard_pass": guards.get("pass_", True), "guard_flags": guards.get("flags", [])}


def oversold_run_start(close: pd.Series, period: int = 60, lo_pct: float = 0.05) -> str | None:
    """当前超卖段(point-in-time expanding 分位 ≤lo_pct 的最近连续段)的起始日(纯)。

    「新」徽标口径: expanding 分位防前视(只用当日之前的历史当尺子);段起点近(≤N 交易日)
    = 新触发,段起点久 = 老超卖(阴跌磨底)。None = 当前不在超卖段。
    """
    dev = deviation_series(close, period).dropna()
    if len(dev) < 21:  # 与 deviation_extremes 的 ≥20 门一致(留 1 根做 expanding 分母)
        return None
    pct = dev.expanding().apply(lambda x: (x[:-1] < x[-1]).sum() / max(len(x) - 1, 1),
                                raw=True)
    mask = (pct <= lo_pct).to_numpy()
    if not mask.any() or not mask[-1]:
        return None  # 当前不在超卖段
    # 找包含最后一根的 True 段起点
    start = len(mask) - 1
    while start > 0 and mask[start - 1]:
        start -= 1
    return str(dev.index[start])
