"""黄金阶段定位器 v2 (price + micro + drivers · Phase 3 旗舰 · ADR-0001 只读旁路).

v1 纯价格结构 (stage.classify_stage) 回测 56%<60%, 失败=分不清"牛市回调"vs"终局底"。
v2 = 价格结构(短期位置) + 微观紧缺(回调vs底判别) + 利率/美元驱动(中期方向) + 框架四层判定,
出「当前阶段 + 驱动读数(底层/中期/短期) + 置信度 + 条件化操作建议」。只读, 不喂引擎。

四层 (GOLD_FRAMEWORK.md):
  L1 底层(牛市在否): bull_intact = 央行购金>0 AND (期限溢价↑ OR 实际利率↓) → 决定"下跌=加仓"vs"转弱"
  L2 中期方向: 2s10s/实际利率/DXY 趋势
  L3 短期位置: 价格5阶段(复用 stage.classify_stage) + 投机泡沫/商业逼空/库存紧缺
  L4 操作切换: 站稳MA60 + bull_intact + 未泡沫 → 切波段→持有

复用: stage.{gold_stage_snapshot, classify_stage, stage_signal_strength, UP_STAGES, _series_truncated}
      score.series_for · store getters · tracker.diagnose
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .score import series_for
from .stage import UP_STAGES, _series_truncated, gold_stage_snapshot, stage_signal_strength

# 驱动 lookback
_TREND_N = 60          # 趋势窗口(约3月, 显示用)
_TREND_LONG = 252      # 底层结构性窗口(约1年, bull_intact 判定用)
_PCT_LOOKBACK = 104    # 仓位分位窗口(约2年)
_INV_YEARS = 252


def _trunc(s, asof):
    return _series_truncated(s, asof)


def _dir(s: pd.Series, n: int = _TREND_N) -> Optional[str]:
    """序列近 n 根方向: ↑/↓/→ (0.1% 阈值去噪)。"""
    if s is None or len(s) < n + 1:
        return None
    r = float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1.0
    if r > 0.001:
        return "↑"
    if r < -0.001:
        return "↓"
    return "→"


# ---- 单驱动 reader (每个 → {label,value,dir,flag,raw?} 或 None) ----
def drv_cb(store):
    """央行(中国)月度净购金(吨)。利好 if >0。"""
    cb = store.get_cb_gold("CN")
    if cb is None or cb.empty or len(cb) < 2:
        return None
    v = cb["value"].astype(float)
    delta_t = (float(v.iloc[-1]) - float(v.iloc[-2])) * 0.3110   # 万oz → 吨(1万oz=0.311吨)
    return {"label": "央行购金(中国)", "value": f"{delta_t:+.1f}吨/月",
            "dir": "↑" if delta_t > 0 else ("↓" if delta_t < 0 else "→"),
            "flag": "利好" if delta_t > 0 else ("逆风" if delta_t < 0 else "中性"), "raw": delta_t}


def drv_real_rate(store, asof=None):
    """实际利率10Y(TIPS)。↓=利好(黄金死敌回落), ↑=逆风。"""
    s = _trunc(series_for("实际利率10Y", store), asof)
    if s is None or len(s) < _TREND_N + 1:
        return None
    d = _dir(s)
    flag = "利好" if d == "↓" else ("逆风" if d == "↑" else "中性")
    return {"label": "实际利率10Y", "value": f"{float(s.iloc[-1]):.2f}%", "dir": d, "flag": flag}


def drv_term_premium(store, asof=None):
    """期限溢价10Y(ACM)。↑=利好(不信长债)。"""
    s = _trunc(series_for("期限溢价10Y", store), asof)
    if s is None or len(s) < _TREND_N + 1:
        return None
    d = _dir(s)
    flag = "利好" if d == "↑" else ("逆风" if d == "↓" else "中性")
    return {"label": "期限溢价10Y", "value": f"{float(s.iloc[-1]):.2f}%", "dir": d, "flag": flag}


def drv_curve(store, asof=None):
    """2s10s 利差趋势。↑(陡峭化)=利好。"""
    s = _trunc(series_for("2s10s", store), asof)
    if s is None or len(s) < _TREND_N + 1:
        return None
    d = _dir(s)
    flag = "利好" if d == "↑" else ("逆风" if d == "↓" else "中性")
    return {"label": "2s10s 利差", "value": f"{float(s.iloc[-1]):.2f}", "dir": d, "flag": flag}


def drv_dxy(snap_drivers: Optional[dict]):
    """DXY 阶段(复用 gold_stage_snapshot.drivers)。弱=利好, 强=逆风。"""
    if not snap_drivers:
        return None
    ph = snap_drivers.get("dxy_phase", "中性")
    return {"label": "美元指数 DXY", "value": ph,
            "dir": "↓" if ph == "弱" else ("↑" if ph == "强" else "→"),
            "flag": "利好" if ph == "弱" else ("逆风" if ph == "强" else "中性")}


def drv_spec(store):
    """投机净多仓位(非商业)。分位≥85%=泡沫。"""
    cf = store.get_cftc_position("GC")
    if cf is None or cf.empty:
        return None
    net = cf["net_pos"].astype(float)
    cur = float(net.iloc[-1])
    lk = net.iloc[-_PCT_LOOKBACK:] if len(net) > _PCT_LOOKBACK else net
    pct = float((lk < cur).sum()) / len(lk)
    foam = pct >= 0.85
    return {"label": "投机净多仓位", "value": f"{cur/1000:+.0f}k (分位{pct:.0%})",
            "dir": "↑" if pct > 0.6 else ("↓" if pct < 0.4 else "→"),
            "flag": "泡沫" if foam else "中性", "raw": pct}


def drv_commercial(store):
    """商业净空仓位(merchant)。净空分位≥85%(最不空/空单骤缩)=逼空前兆。"""
    cfm = store.get_cftc_position("GC_M")
    if cfm is None or cfm.empty:
        return None
    net = cfm["net_pos"].astype(float)   # 负值(净空)
    cur = float(net.iloc[-1])
    lk = net.iloc[-_PCT_LOOKBACK:] if len(net) > _PCT_LOOKBACK else net
    pct = float((lk < cur).sum()) / len(lk)   # cur 越高(越不空)→pct 越高
    squeeze = pct >= 0.85
    return {"label": "商业净空仓位", "value": f"{cur/1000:+.0f}k (分位{pct:.0%})",
            "dir": "↑" if pct > 0.6 else "→",
            "flag": "逼空" if squeeze else "中性", "raw": pct}


def drv_inventory(store):
    """COMEX 金库存近1年变化。<-10%=紧缺。"""
    ci = store.get_comex_inventory("GC")
    if ci is None or ci.empty:
        return None
    last = float(ci.iloc[-1])
    yago = float(ci.iloc[-_INV_YEARS]) if len(ci) > _INV_YEARS else float(ci.iloc[0])
    chg = (last / yago - 1.0) * 100
    tight = chg < -10
    return {"label": "COMEX 金库存", "value": f"{last:.0f}吨 ({chg:+.0f}%/yr)",
            "dir": "↓" if chg < -5 else ("↑" if chg > 5 else "→"),
            "flag": "紧缺" if tight else "中性", "raw": chg}


# ---- 主入口 ----
def _long_trend_bull(store, asof) -> tuple[bool, bool]:
    """底层(L1)结构性趋势(1年窗): (期限溢价↑, 实际利率↓)。底层看长周期,不被3月波动带偏。"""
    tp = _dir(_trunc(series_for("期限溢价10Y", store), asof), _TREND_LONG) == "↑"
    rr = _dir(_trunc(series_for("实际利率10Y", store), asof), _TREND_LONG) == "↓"
    return tp, rr


def _is_bull_intact(cb, tp_up_long, rr_dn_long) -> bool:
    """L1: 央行在买 AND (期限溢价结构↑ OR 实际利率结构↓)。"""
    cb_ok = bool(cb and cb.get("raw", 0) > 0)
    return cb_ok and (tp_up_long or rr_dn_long)


def _driver_consistency(base: str, dxy, rr, curve, cb, bull_intact: bool) -> float:
    """驱动与阶段自然方向的一致度 [0,1]。"""
    flags = [d for d in (dxy, rr, curve, cb) if d]
    if base in UP_STAGES:
        if not flags:
            return 0.5
        good = sum(1 for d in flags if d["flag"] == "利好")
        bad = sum(1 for d in flags if d["flag"] == "逆风")
        return good / (good + bad) if (good + bad) else 0.5
    # 下行/筑底阶段: dip 在牛市里=正常(高一致); 非牛市=转弱(低一致)
    return 0.65 if bull_intact else 0.35


def _action_for(base: str, bull_intact: bool, spec) -> str:
    foam = bool(spec and spec["flag"] == "泡沫")
    if base == "筑底/底部震荡":
        return "分批加仓/定投,跌是加仓机会" if bull_intact else "谨慎观望,等底层明朗"
    if base == "反弹初期":
        return ("切波段→持有思路,回调加仓不轻易卖出,不做空不空仓" if bull_intact
                else "反弹偏弱,轻仓试探")
    if base == "趋势上行":
        return ("持有;警戒线以上禁加仓" + ("；投机过热,警惕A杀" if foam else ""))
    if base == "头部区域":
        return "禁加仓,高成本者减仓,带利润防守" + ("；泡沫区" if foam else "")
    if base == "回调下跌":
        return ("牛市回调=加仓位,不空仓不做空" if bull_intact
                else "谨慎,趋势或转弱,控制仓位")
    return "—"


def gold_locate(store, asof: Optional[str] = None) -> dict:
    """黄金阶段定位 (v2)。返回 stage + 驱动三栏 + 置信度 + 操作建议 + 警示。"""
    snap = gold_stage_snapshot(store, asof)
    if not snap.get("valid"):
        return {"valid": False, "reason": snap.get("reason", "黄金数据不足")}

    base = snap["stage"]
    snap_drivers = snap.get("drivers") or {}
    cb = drv_cb(store)
    rr = drv_real_rate(store, asof)
    tp = drv_term_premium(store, asof)
    curve = drv_curve(store, asof)
    dxy = drv_dxy(snap_drivers)
    spec = drv_spec(store)
    comm = drv_commercial(store)
    inv = drv_inventory(store)

    tp_up_long, rr_dn_long = _long_trend_bull(store, asof)
    bull_intact = _is_bull_intact(cb, tp_up_long, rr_dn_long)
    foam = bool(spec and spec["flag"] == "泡沫")
    squeeze = bool(comm and comm["flag"] == "逼空")

    # 阶段精炼
    warnings: list[str] = []
    if bull_intact and base in ("回调下跌", "筑底/底部震荡"):
        stage_note = "牛市回调(加仓位)"
    elif base == "头部区域" and foam:
        stage_note = "头部/泡沫风险"
        warnings.append("投机仓位处历史极值(泡沫区),杠杆拥挤易A杀")
    else:
        stage_note = base
    if rr and rr["flag"] == "逆风":
        warnings.append(f"实际利率{rr['dir']}(黄金逆风),压制上行空间")
    if squeeze:
        warnings.append("商业净空单骤缩=逼空临界,现货紧缺或引发逼空")
    if inv and inv["flag"] == "紧缺":
        warnings.append(f"COMEX库存{inv['value'].split('(')[1].rstrip(')')}紧缺,实物支撑")

    # 置信度
    strength = stage_signal_strength(
        {"stage": base, "dev_pct": snap["evidence"].get("dev_pct_12m"),
         "fresh_cross": snap["evidence"].get("fresh_cross"),
         "breakout_grade": snap["evidence"].get("breakout_grade"),
         "noise": snap.get("noise"), "fallback": snap.get("fallback")},
        snap["evidence"].get("n_bars", 0))
    consistency = _driver_consistency(base, dxy, rr, curve, cb, bull_intact)
    confidence = strength * consistency * (1.0 if bull_intact else 0.8)
    confidence = min(max(confidence, 0.0), 1.0)

    return {
        "valid": True, "asof": snap.get("asof"),
        "stage": base, "stage_note": stage_note,
        "bull_intact": bull_intact,
        "regime": "牛市(底层在)" if bull_intact else "中性·底层存疑",
        "drivers": {
            "底层": [cb, tp, rr],
            "中期": [curve, rr, dxy],
            "短期": [spec, comm, inv],
        },
        "confidence": confidence,
        "confidence_band": ("强" if confidence >= 0.6 else "中" if confidence >= 0.35 else "弱"),
        "action": _action_for(base, bull_intact, spec),
        "warnings": warnings,
        "price": snap["evidence"],
    }
