"""黄金阶段定位器 (Gold Stage Locator · Phase 3 MVP · ADR-0001 只读旁路).

把 JZ 通篇的黄金「阶段语言」(筑底/底部震荡 → 反弹初期 → 趋势上行 → 头部区域 → 回调下跌)
编成**规则分类器**: 由黄金自身价格结构 (相对 MA60 + 近 12 月偏离分位) 决定当前阶段,
美元(DXY)/曲线(2s10s) 作*确认*驱动 (不作阶段决定者), 出「阶段 + 驱动信号 + 置信度」。
再用 wm_claims 里 JZ 自述的阶段词做**无前视回测**, 比对「规则阶段 vs 他说的阶段」,
≥60% 一致 → 路通, 扩到美元/曲线/原油; <60% → 降级为辅助参考。

围栏不变: 只读诊断, **永不喂 A股轮动引擎** (ADR-0001)。
黄金是 JZ 择时弱项 (32% 命中) → 置信度按 ×0.60 折价, 不照搬择时。

复用 (不重写):
  - stockagent.tracker.diagnose.diagnose_index  — 一站式 {trend/deviation/breakout/cross/choppy}
  - stockagent.tracker.indicators.{deviation_series, ma_series, fresh_cross_direction}
  - stockagent.western_macro.score.series_for   — close pd.Series (黄金/美元指数/2s10s)
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from stockagent.tracker import indicators as ti
from stockagent.tracker.diagnose import diagnose_index
from stockagent.tracker.indicators import fresh_cross_direction, ma_series, deviation_series
from .score import series_for

log = logging.getLogger(__name__)

# ---- 常量 (金特有; 不复用 A股 0.20/0.80, 金尾部肥、极端聚边) ----
GOLD_MA_PERIOD = 60          # MA60 (tracker 默认; 中线趋势)
GOLD_DEV_LOOKBACK = 252      # 近 12 月 (252 交易日) 偏离分位窗口
GOLD_DEV_LOW = 0.15          # 区间底部 (超卖)
GOLD_DEV_HIGH = 0.85         # 区间顶部 (超买)
GOLD_DEV_MID = 0.50
GOLD_FRESH = 5               # 「fresh 穿越」= 5 个交易日内
GOLD_DISCOUNT = 0.60         # JZ 黄金择时 32% 命中弱项折价 → 置信封顶 0.60
MIN_BARS = 80                # < 80 根 → 数据不足 (MA60 + 缓冲)

STAGES = ["筑底/底部震荡", "反弹初期", "趋势上行", "头部区域", "回调下跌"]
STAGE_INDEX = {s: i for i, s in enumerate(STAGES)}   # 循环邻接: 回调(4)→筑底(0) 回绕
UP_STAGES = {"反弹初期", "趋势上行", "头部区域"}

# JZ 实际语料采样 → 5 阶段词族 (去掉可被否定/单字歧义词, 如 bare「底」「站稳」)
STAGE_WORDS: dict[str, list[str]] = {
    "筑底/底部震荡": ["筑底", "底部震荡", "接近底部", "探底", "磨底", "低位震荡",
                    "未站稳", "没有站稳", "未能站稳"],
    "反弹初期": ["反弹初期", "开始反弹", "触底反弹", "止跌回升", "企稳回升", "转强",
                "初步企稳", "反弹"],
    "趋势上行": ["趋势上行", "主升", "上行趋势", "单边上行", "加速上涨", "持续上涨",
                "走强", "牛市", "上涨"],
    "头部区域": ["头部", "见顶", "阶段性顶部", "阶段涨幅", "涨幅满足", "高位震荡",
                "顶背离", "风险提示", "超买", "冲高回落", "冲高"],
    "回调下跌": ["回调", "回落", "主跌", "下探", "走弱", "下跌趋势", "调整", "下跌"],
}


def _rolling_dev_pct(dev: pd.Series, lookback: int = GOLD_DEV_LOOKBACK,
                     min_bars: int = 20) -> pd.Series:
    """逐 bar 的 trailing-lookback 偏离分位 (防前视); 与 deviation_extremes(lookback=) 末值完全一致。

    每个 bar 只用「截至当时」的非 NaN 偏离值, 取末 lookback 个, 算 (w<cur).sum()/len。
    0=最负/超卖, 1=最正/超买; 前 period 根 / 样本<min_bars 为 NaN。
    这正是 deviation_extremes(close,60,lookback)["pct"] 在每个历史 bar 的点在时口径。"""
    vals = dev.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    recent: list[float] = []          # 截至当前的非 NaN 偏离 (有界到 lookback)
    for i in range(len(vals)):
        v = vals[i]
        if not np.isnan(v):
            recent.append(float(v))
            if len(recent) > lookback:
                recent = recent[-lookback:]
        if len(recent) < min_bars:
            continue
        cur = recent[-1]
        arr = np.asarray(recent)
        out[i] = float((arr < cur).sum()) / len(arr)
    return pd.Series(out, index=dev.index)


def classify_stage(diag: dict, dev_pct: float, fresh_cross: Optional[str],
                   choppy: bool, n_bars: int) -> dict:
    """阶段决策树 (first-match-wins) → info dict (含 'stage'/'fallback'/'noise' + 解析字段)。

    主信号 = 黄金自身结构: above_ma × ma_trend_up × dev_pct(近12月分位) × fresh_cross。
    边序要点: fresh 下穿压过一切 (→回调); fresh 上穿但 MA 仍平 = 反弹非趋势 (rule 要 ma_trend_up)。
    choppy 不改阶段, 只置 noise=True (在 strength 里压低)。
    """
    tr = diag.get("trend") or {}
    above_ma = tr.get("above_ma") is True
    ma_trend_up = tr.get("ma_trend_up") is True
    price_vs_ma_pct = tr.get("price_vs_ma_pct")
    bo = diag.get("breakout") or {}
    bo_grade = bo.get("grade") or 0
    if dev_pct != dev_pct:  # NaN → 中性, 让树仍能跑
        dev_pct = GOLD_DEV_MID

    fallback = False
    if n_bars < MIN_BARS:
        stage = "数据不足"
    elif fresh_cross == "down":                       # 2a: fresh 跌破优先
        stage = "回调下跌"
    elif above_ma and dev_pct >= GOLD_DEV_HIGH:       # 1: 头部 (上MA + 区间顶)
        stage = "头部区域"
    elif (not above_ma) and (not ma_trend_up) and dev_pct > GOLD_DEV_LOW:   # 2b: 下行中
        stage = "回调下跌"
    elif above_ma and ma_trend_up and GOLD_DEV_LOW < dev_pct < GOLD_DEV_HIGH:  # 3: 趋势
        stage = "趋势上行"
    elif fresh_cross == "up":                         # 4a: fresh 上穿
        stage = "反弹初期"
    elif above_ma and (not ma_trend_up) and dev_pct <= GOLD_DEV_MID:          # 4b: 上MA但MA平
        stage = "反弹初期"
    elif (not above_ma) and dev_pct <= GOLD_DEV_LOW:  # 5: 下MA + 区间底
        stage = "筑底/底部震荡"
    elif (not above_ma) and dev_pct < GOLD_DEV_MID:   # 5b: 下MA + 下半区
        stage = "筑底/底部震荡"
    else:                                             # 6: 中位僵局
        fallback = True
        stage = "趋势上行" if ma_trend_up else ("头部区域" if above_ma else "筑底/底部震荡")

    return {
        "stage": stage,
        "above_ma": above_ma,
        "ma_trend_up": ma_trend_up,
        "price_vs_ma_pct": price_vs_ma_pct,
        "dev_pct": dev_pct,
        "fresh_cross": fresh_cross,
        "breakout_grade": bo_grade,
        "breakout_label": bo.get("label", "中性"),
        "choppy": bool(choppy),
        "fallback": fallback,
        "noise": bool(choppy),
        "valid": stage != "数据不足",
    }


def stage_signal_strength(info: dict, n_bars: int) -> float:
    """信号强度 [0,1]: 极值/穿越/突破加分, choppy/fallback/数据不足 压低。"""
    stage = info.get("stage")
    if stage == "数据不足" or n_bars < MIN_BARS:
        return 0.0
    dev_pct = info.get("dev_pct", GOLD_DEV_MID)
    if dev_pct != dev_pct:
        dev_pct = GOLD_DEV_MID
    s = 0.50
    s += 0.30 * (abs(dev_pct - 0.50) / 0.50)          # 极值加分 (满 +0.30)
    if info.get("fresh_cross") in ("up", "down"):
        s += 0.15                                      # 穿越确认
    if (info.get("breakout_grade") or 0) >= 3:
        s += 0.10                                      # ≥3% 偏离
    s = min(max(s, 0.0), 1.0)
    if info.get("noise"):                              # 震荡 → 信号衰减
        s = min(s, 0.30)
    if info.get("fallback"):                           # 中位僵局 → 低确信
        s = min(s, 0.35)
    return float(s)


def confidence_band(conf: float) -> str:
    if conf != conf or conf < 0.25:
        return "弱"
    if conf < 0.40:
        return "中"
    return "强"


def statement_to_stage(statement: str, direction: Optional[str] = None) -> Optional[str]:
    """JZ 自述语句 → 阶段 (或 None 剔除歧义)。

    路径 vs 阶段消歧: 同时含回调族+反弹族且 direction=up → 反弹是阶段、回调是路径。
    direction 兜底: 多族并列 → 取与 direction 一致者; 仍并列或无 direction → None (不强行分桶)。
    """
    s = (statement or "").strip()
    if not s:
        return None
    cands = [st for st, words in STAGE_WORDS.items() if any(w in s for w in words)]
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    # 多族: 路径 vs 阶段
    if "回调下跌" in cands and "反弹初期" in cands and direction == "up":
        return "反弹初期"
    # direction 兜底
    if direction == "up":
        consistent = [c for c in cands if c in ("反弹初期", "趋势上行", "头部区域")]
    elif direction == "down":
        consistent = [c for c in cands if c == "回调下跌"]
    elif direction == "flat":
        consistent = [c for c in cands if c in ("筑底/底部震荡", "头部区域")]
    else:
        consistent = []
    if len(consistent) == 1:
        return consistent[0]
    return None  # 歧义 → 剔除 (不入分母)


def stage_agreement(rule_stage: str, jz_stage: str) -> float:
    """规则阶段 vs JZ 阶段 一致性: 完全一致 1.0 / 循环邻接 0.5 / 否则 0.0。

    循环邻接 = 5 阶段环上相邻 (回调↔筑底 回绕也算), 奖励「差一档」。
    """
    if rule_stage not in STAGE_INDEX or jz_stage not in STAGE_INDEX:
        return 0.0
    if rule_stage == jz_stage:
        return 1.0
    n = len(STAGES)
    d = abs(STAGE_INDEX[rule_stage] - STAGE_INDEX[jz_stage])
    return 0.5 if min(d, n - d) == 1 else 0.0


def _trend_booleans(diag: dict) -> tuple[bool, bool, bool]:
    """(above_ma, ma_trend_up, valid) — None 安全。"""
    tr = diag.get("trend") or {}
    return (tr.get("above_ma") is True, tr.get("ma_trend_up") is True, bool(tr.get("valid")))


def driver_confirmation(diag_dxy: Optional[dict], diag_curve: Optional[dict]) -> dict:
    """DXY + 2s10s → 驱动确认分 (modifier, 非阶段决定者)。

    DXY 强 (上MA+上行) → 黄金利空 −0.5; DXY 弱 → 利好 +0.5。
    2s10s 陡峭化 (利差上行, reflation) → 黄金利好 +0.5; 趋平/倒挂 → −0.5。
    confirm_score = (dxy + curve)/2。
    """
    # DXY
    if diag_dxy:
        dxy_above, dxy_up, dxy_valid = _trend_booleans(diag_dxy)
    else:
        dxy_above = dxy_up = dxy_valid = False
    if dxy_valid and dxy_above and dxy_up:
        dxy_phase, dxy_contrib = "强", -0.5
    elif dxy_valid and (not dxy_above) and (not dxy_up):
        dxy_phase, dxy_contrib = "弱", 0.5
    else:
        dxy_phase, dxy_contrib = "中性", 0.0

    # 2s10s 曲线 (趋势方向: 上行=陡峭化)
    if diag_curve:
        _c_above, c_up, c_valid = _trend_booleans(diag_curve)
    else:
        c_up = c_valid = False
    if c_valid and c_up:
        curve_phase, curve_contrib = "陡峭化", 0.5
    elif c_valid and not c_up:
        curve_phase, curve_contrib = "趋平/倒挂", -0.5
    else:
        curve_phase, curve_contrib = "中性", 0.0

    confirm_score = (dxy_contrib + curve_contrib) / 2.0
    return {
        "dxy_phase": dxy_phase, "dxy_contrib": dxy_contrib,
        "curve_phase": curve_phase, "curve_contrib": curve_contrib,
        "confirm_score": confirm_score,
        "note": f"DXY{dxy_phase}·曲线{curve_phase}",
    }


def _series_truncated(s: Optional[pd.Series], asof: Optional[str]) -> Optional[pd.Series]:
    if s is None or len(s) == 0:
        return None
    if asof:
        s = s[s.index <= asof]
    return s if len(s) else None


def gold_stage_snapshot(store, asof: Optional[str] = None) -> dict:
    """黄金当前阶段快照 (截断到 asof; 默认末值)。只读, 不喂引擎。"""
    gold = _series_truncated(series_for("黄金", store), asof)
    if gold is None or len(gold) < MIN_BARS:
        return {"valid": False, "asof": asof, "reason": "黄金数据不足", "stage": None,
                "n_bars": 0 if gold is None else len(gold)}

    diag = diagnose_index(gold, GOLD_MA_PERIOD, GOLD_DEV_LOOKBACK)
    dev_pct_series = _rolling_dev_pct(deviation_series(gold, GOLD_MA_PERIOD), GOLD_DEV_LOOKBACK)
    dev_pct = float(dev_pct_series.iloc[-1])
    fresh = fresh_cross_direction(diag.get("cross"), GOLD_FRESH)
    info = classify_stage(diag, dev_pct, fresh, bool(diag.get("choppy")), len(gold))
    strength = stage_signal_strength(info, len(gold))

    # 驱动 (截断到同一 asof)
    dxy = _series_truncated(series_for("美元指数", store), asof)
    curve = _series_truncated(series_for("2s10s", store), asof)
    diag_dxy = diagnose_index(dxy, GOLD_MA_PERIOD) if (dxy is not None and len(dxy) >= GOLD_MA_PERIOD) else None
    diag_curve = diagnose_index(curve, GOLD_MA_PERIOD) if (curve is not None and len(curve) >= GOLD_MA_PERIOD) else None
    drivers = driver_confirmation(diag_dxy, diag_curve)

    # 置信度 = strength × discount, 再按驱动确认微调 (封顶 discount)
    confidence = strength * GOLD_DISCOUNT
    cscore = drivers["confirm_score"]
    stage = info["stage"]
    if stage in UP_STAGES:
        if cscore > 0.01:
            confidence *= 1.05
        elif cscore < -0.01:
            confidence *= 0.80
    elif stage == "回调下跌":
        if cscore < -0.01:
            confidence *= 1.05
        elif cscore > 0.01:
            confidence *= 0.80
    confidence = float(min(confidence, GOLD_DISCOUNT))

    ma60 = ma_series(gold, GOLD_MA_PERIOD)
    tr = diag.get("trend") or {}
    return {
        "valid": True,
        "asof": asof or str(gold.index[-1]),
        "stage": stage,
        "confidence": confidence,
        "confidence_band": confidence_band(confidence),
        "strength": strength,
        "gold_discount": GOLD_DISCOUNT,
        "noise": info["noise"],
        "fallback": info["fallback"],
        "evidence": {
            "above_ma": info["above_ma"],
            "ma_trend_up": info["ma_trend_up"],
            "price_vs_ma_pct": tr.get("price_vs_ma_pct"),
            "dev_pct_12m": dev_pct,
            "fresh_cross": fresh,
            "breakout_grade": info["breakout_grade"],
            "breakout_label": info["breakout_label"],
            "choppy": info["choppy"],
            "close_last": float(gold.iloc[-1]),
            "ma60_last": float(ma60.iloc[-1]) if (len(ma60) and not np.isnan(ma60.iloc[-1])) else None,
            "date_last": str(gold.index[-1]),
            "n_bars": len(gold),
        },
        "drivers": drivers,
        "reason": "",
    }


def _dev_pct_at(dev_pct_series: pd.Series, ep: str) -> float:
    """dev_pct_series 中 ≤ ep 的末值 (episode 落非交易日取前一根)。NaN if 无。"""
    sub = dev_pct_series[dev_pct_series.index <= ep]
    if len(sub) == 0:
        return float("nan")
    return float(sub.iloc[-1])


def evaluate_gold_stage(store, asof: Optional[str] = None,
                        min_classified: int = 15, threshold: float = 0.60,
                        claim_states: tuple = ("draft", "confirmed")) -> dict:
    """回测: 对每条黄金 claim 做*无前视*规则分类, 比对 JZ 自述阶段 → 一致性 + 混淆矩阵 + 闸门。

    无前视: 每 ep 用 gold[gold.index<=ep] 算 diag + fresh_cross + _rolling_dev_pct(full).loc[ep]。
    闸门 = per-episode soft_agreement ≥ threshold 且 n_classified ≥ min_classified。
    """
    gold = series_for("黄金", store)
    out: dict = {
        "valid": False, "n_claims": 0, "n_classified": 0, "n_dropped": 0,
        "hard_agreement": float("nan"), "soft_agreement": float("nan"),
        "per_episode_soft_agreement": float("nan"), "n_episodes": 0,
        "confusion": {}, "per_claim": [], "per_episode": [],
        "threshold": threshold, "min_classified": min_classified,
        "min_classified_met": False, "gate_pass": False, "gate_inconclusive": True,
    }
    if gold is None or len(gold) < MIN_BARS:
        out["reason"] = "黄金数据不足"
        return out
    gold = gold.sort_index()
    if asof:
        gold_eval = gold[gold.index <= asof]
    else:
        gold_eval = gold
    dev_pct_series = _rolling_dev_pct(deviation_series(gold, GOLD_MA_PERIOD), GOLD_DEV_LOOKBACK)

    claims = [c for c in store.get_wm_claims(asset="黄金") if c.get("state") in claim_states]
    out["n_claims"] = len(claims)
    per_claim = []
    for c in claims:
        ep = (c.get("episode_date") or "")[:10]
        jz_stage = statement_to_stage(c.get("statement"), c.get("direction"))
        entry = {"uid": c.get("uid"), "episode_date": ep, "asset": "黄金",
                 "statement": c.get("statement") or "", "direction": c.get("direction"),
                 "jz_stage": jz_stage, "rule_stage": None, "agree": None}
        if jz_stage is None:
            entry["dropped"] = True
            entry["dropped_reason"] = "no/ambiguous stage word"
            per_claim.append(entry)
            continue
        s_trunc = gold_eval[gold_eval.index <= ep] if ep else gold_eval.iloc[0:0]
        if len(s_trunc) < MIN_BARS:
            entry["dropped"] = True
            entry["dropped_reason"] = "insufficient history"
            per_claim.append(entry)
            continue
        diag = diagnose_index(s_trunc, GOLD_MA_PERIOD, GOLD_DEV_LOOKBACK)
        fresh = fresh_cross_direction(diag.get("cross"), GOLD_FRESH)
        dev_pct = _dev_pct_at(dev_pct_series, ep)
        info = classify_stage(diag, dev_pct, fresh, bool(diag.get("choppy")), len(s_trunc))
        rule_stage = info["stage"]
        if rule_stage == "数据不足":
            entry["dropped"] = True
            entry["dropped_reason"] = "rule data insufficient"
            per_claim.append(entry)
            continue
        entry["rule_stage"] = rule_stage
        entry["agree"] = stage_agreement(rule_stage, jz_stage)
        entry["dropped"] = False
        per_claim.append(entry)

    classified = [e for e in per_claim if not e.get("dropped")]
    dropped = [e for e in per_claim if e.get("dropped")]
    out["n_classified"] = len(classified)
    out["n_dropped"] = len(dropped)
    out["per_claim"] = per_claim

    if classified:
        agrees = [e["agree"] for e in classified]
        out["hard_agreement"] = float(sum(1 for a in agrees if a >= 1.0) / len(agrees))
        out["soft_agreement"] = float(sum(agrees) / len(agrees))

    # 混淆矩阵 {jz_stage: {rule_stage: count}} (per-claim)
    confusion: dict[str, dict[str, int]] = {}
    for e in classified:
        confusion.setdefault(e["jz_stage"], {})
        confusion[e["jz_stage"]][e["rule_stage"]] = confusion[e["jz_stage"]].get(e["rule_stage"], 0) + 1
    out["confusion"] = confusion

    # per-episode collapse (modal stage; tie → first stated)
    by_ep: dict[str, list] = {}
    for e in classified:
        by_ep.setdefault(e["episode_date"], []).append(e)
    ep_rows = []
    ep_soft = []
    for ep, lst in sorted(by_ep.items()):
        rule_modes = _modal([e["rule_stage"] for e in lst])
        jz_modes = _modal([e["jz_stage"] for e in lst])
        a = stage_agreement(rule_modes, jz_modes)
        ep_soft.append(a)
        ep_rows.append({"episode_date": ep, "rule_stage": rule_modes,
                        "jz_stage": jz_modes, "agree": a, "n": len(lst)})
    out["per_episode"] = ep_rows
    out["n_episodes"] = len(ep_rows)
    if ep_soft:
        out["per_episode_soft_agreement"] = float(sum(ep_soft) / len(ep_soft))

    out["valid"] = True
    out["min_classified_met"] = out["n_classified"] >= min_classified
    out["gate_inconclusive"] = not out["min_classified_met"]
    out["gate_pass"] = (out["min_classified_met"]
                        and out["per_episode_soft_agreement"] >= threshold
                        and not np.isnan(out["per_episode_soft_agreement"]))
    return out


def _modal(stages: list[str]) -> str:
    """众数; 并列取首个出现 (保序)。"""
    if not stages:
        return ""
    counts: dict[str, int] = {}
    for s in stages:
        counts[s] = counts.get(s, 0) + 1
    return max(counts, key=lambda s: (counts[s], -stages.index(s)))
