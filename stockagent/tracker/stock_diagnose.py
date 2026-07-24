"""个股层诊断 — Phase 2 C1 (V5 tracker)。

三类自动判定 + 估值分位/zone + E3 偏离极值(价格择时)。纯函数优先;顶层 diagnose_stock 读 store 装配。

复用(不重写):
  - engine.indicators.percentile_rank   → 估值分位(PE/PB 历史分位)
  - tracker.indicators.deviation_extremes / trend_state / breakout_grade / is_choppy → E3 偏离极值/趋势/突破
  - classifier.VALID_STYLES             → 三类输出形状对齐(主+次 tuple),三层分类器统一入口

数据底座(C0 + C0.5 已就绪):daily_prices(价)/ stock_valuation(PE·PB,baidu稀疏)/
stock_financials(营收·净利 17 指标,按报告期)/ stock_dividend(每股现金分红)。

三类判定规则(PRD §7.1):
  利润波动大 → 周期 / 营收·净利增速高 → 成长 / 股息率高·PE 分位低 → 价值。
周期是结构信号优先判定(避免把周期顶部的高增长误判成长)。阈值在 params.yaml stock.classify。
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from ..config import get_config
from ..engine import indicators as ind
from . import indicators as ti
from .classifier import VALID_STYLES  # {"value","growth","cyclic"} — 输出形状对齐


def _nan(x) -> bool:
    return x is None or (isinstance(x, float) and np.isnan(x))


# ---------- 报告期 helpers ----------
def annual_only(series: pd.Series) -> pd.Series:
    """从按报告期(YYYYMMDD 字符串)索引的序列里取**年报**(期末 1231)。
    年报是全年累计,可直接跨年比(CAGR/波动),绕开季报累计口径问题(单季拆分留给 S07 利润归因)。"""
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)
    idx = [str(i) for i in series.index]
    mask = [s.endswith("1231") for s in idx]
    return series[mask].sort_index()


# ---------- 特征提取(纯,吃 series) ----------
def revenue_cagr(annual_series: pd.Series, years: int) -> float:
    """年报序列最近 `years` 年 CAGR = (末/首)^(1/years) − 1。NaN if 数据不足或首末≤0。"""
    if annual_series is None:
        return np.nan
    s = pd.to_numeric(annual_series, errors="coerce").dropna()
    if len(s) < years + 1:
        return np.nan
    a = float(s.iloc[-years - 1])
    b = float(s.iloc[-1])
    if a <= 0 or b <= 0:
        return np.nan
    return (b / a) ** (1.0 / years) - 1.0


def profit_growth_volatility(annual_series: pd.Series,
                             years: int | None = None) -> tuple[float, float]:
    """年度净利 YoY 增速的 (std, min) → (波动, 最差年增速)。周期判定的结构信号。

    std 高 = 增速逐年大幅摆动(周期);std 低 = 稳定(价值/成长)。min 捕捉是否经历过明显下滑年。
    base≤0 的年份跳过(负利润→负基期,增长率无意义)。返回 (nan,nan) if 有效增速<3。"""
    if annual_series is None:
        return (np.nan, np.nan)
    s = pd.to_numeric(annual_series, errors="coerce").dropna().sort_index()
    if years:
        s = s.iloc[-(years + 1):]
    vals = s.astype(float).to_numpy()
    if len(vals) < 4:  # 至少 4 个年报 → 3 个 YoY 增速
        return (np.nan, np.nan)
    growths = []
    for i in range(1, len(vals)):
        base = vals[i - 1]
        if base > 0:
            growths.append(vals[i] / base - 1.0)
    if len(growths) < 3:
        return (np.nan, np.nan)
    g = np.asarray(growths, dtype=float)
    return (float(g.std(ddof=0)), float(g.min()))


def stock_dividend_yield(dividend_df: pd.DataFrame, price: float,
                         lookback_days: int = 365) -> float:
    """近 12 月(可配)每股现金分红之和 ÷ 当前价。dividend_df = store.get_stock_dividend_series
    (indexed by ex_date, 有 cash_per_share 列)。NaN if 无数据/无价。

    已知偏高(方向正确,绝对值偏大,C2/B1 精算时再修):
      1) 项目 data.adjust=raw,高分红股的 raw 价被历年累计分红压低(招行 raw 38 vs 实际 ~42)→ 分母偏小。
      2) 分红节奏切换年(如招行 2026 由年付转半年付),TTM 窗口会多吃 1 次 → 分子偏大。
    分类器对此鲁棒(价值也经干净的 PE 分位识别);精确股息率留给 C2 价值提醒(可用一次性 qfq 价/派息率口径重算)。"""
    if dividend_df is None or len(dividend_df) == 0 or _nan(price) or price <= 0:
        return np.nan
    if "cash_per_share" not in dividend_df.columns:
        return np.nan
    cps = pd.to_numeric(dividend_df["cash_per_share"], errors="coerce").dropna()
    if len(cps) == 0:
        return np.nan
    idx = pd.to_datetime(cps.index, errors="coerce")
    cutoff = idx.max() - pd.Timedelta(days=lookback_days)
    # 严格 `>`(非 ≥):一年分红 2-4 次的股票,365 天窗口在边界会多吃 1 次(N+1)→ 股息率虚高 ~1.6x。
    recent = cps[idx > cutoff]
    if len(recent) == 0:
        return np.nan
    return float(recent.sum()) / float(price)


def valuation_percentile(value_series, lookback_years: int | None = None) -> float:
    """当前值在历史分位(0=最低=最便宜, 1=最高=最贵)。value_series = stock_valuation 某 indicator
    的 value 列(baidu 稀疏~半月级,按点数排名,不用日频窗口)。NaN if <20 点。"""
    if value_series is None:
        return np.nan
    s = pd.to_numeric(value_series, errors="coerce").dropna()
    if len(s) < 20:
        return np.nan
    if lookback_years:
        idx = pd.to_datetime(s.index, errors="coerce")
        s = s[idx >= idx.max() - pd.Timedelta(days=365 * lookback_years)]
    if len(s) < 20:
        return np.nan
    last = float(s.iloc[-1])
    return float((s < last).sum()) / len(s)


# ---------- 三类自动判定(纯) ----------
def classify_stock(features: dict, params: dict) -> tuple[str | None, list[str]]:
    """三类判定。features = {revenue_cagr, profit_cagr, profit_vol, min_profit_growth,
    pe_pct, div_yield}(任一可 NaN)。返回 (主类型, [次类型]),对齐 VALID_STYLES;无任何可用特征 → (None, [])。

    优先级: 周期(利润波动·结构信号) > 成长/价值。价值成长双触发(茅台式)按股息率高低定主。
    阈值取 params.stock.classify(缺省回退默认)。"""
    cy = (params.get("stock", {}) or {}).get("classify", {}) or {}
    vol_t = float(cy.get("cyclic_vol_threshold", 0.40))
    grow_t = float(cy.get("growth_threshold", 0.15))
    div_t = float(cy.get("dividend_yield_threshold", 0.03))
    pe_low = float(cy.get("pe_low_percentile", 0.30))

    rev_cagr = features.get("revenue_cagr")
    prof_cagr = features.get("profit_cagr")
    pvol = features.get("profit_vol")
    pe_pct = features.get("pe_pct")
    div = features.get("div_yield")

    has_any = any(not _nan(x) for x in (rev_cagr, prof_cagr, pvol, pe_pct, div))
    if not has_any:
        return (None, [])

    is_cyclic = (not _nan(pvol)) and pvol >= vol_t
    is_growth = ((not _nan(rev_cagr)) and rev_cagr >= grow_t) \
        or ((not _nan(prof_cagr)) and prof_cagr >= grow_t)
    is_value = ((not _nan(div)) and div >= div_t) \
        or ((not _nan(pe_pct)) and pe_pct <= pe_low)

    if is_cyclic:
        primary = "cyclic"
    elif is_growth and is_value:
        primary = "value" if ((not _nan(div)) and div >= div_t) else "growth"
    elif is_growth:
        primary = "growth"
    else:  # is_value 或都没触发 → 成熟/防御,归价值
        primary = "value"

    secondary = [s for s, flag in (("growth", is_growth), ("value", is_value), ("cyclic", is_cyclic))
                 if flag and s != primary]
    return (primary, secondary)


# ---------- 估值 zone(纯,PE+PB 分位四档) ----------
def diagnose_valuation_zone(pe_pct: float, pb_pct: float,
                            lo: float = 0.20, hi: float = 0.80) -> dict:
    """PE+PB 分位四档(镜像 diagnose.diagnose_valuation 的 zone 逻辑,个股版)。
    双低→便宜 / 双高→偏贵 / 跨中线→分化 / 都中间→中性。仅有 PE 时退化为单指标。"""
    out = {"pe_pct": pe_pct, "pb_pct": pb_pct, "zone": "—", "valid": False}
    if not _nan(pe_pct) and not _nan(pb_pct):
        pe_l, pe_h = pe_pct < lo, pe_pct > hi
        pb_l, pb_h = pb_pct < lo, pb_pct > hi
        if pe_l and pb_l:
            zone = "低位·便宜"
        elif pe_h and pb_h:
            zone = "高位·偏贵"
        elif (pe_pct > 0.5) != (pb_pct > 0.5):
            zone = "结构分化"
        else:
            zone = "中位·中性"
        out["zone"], out["valid"] = zone, True
    elif not _nan(pe_pct):
        p = pe_pct
        out["zone"] = "低位·便宜" if p < lo else ("高位·偏贵" if p > hi else "中位·中性")
        out["valid"] = True
    return out


# ---------- 价格择时 E3(纯,复用 ti) ----------
def diagnose_price_timing(close: pd.Series, period: int = ti.MA_PERIOD,
                          lookback: int | None = None) -> dict:
    """个股价格择时诊断(E3 偏离极值套利 + 趋势 + 突破)。复用 tracker.indicators,
    与 diagnose.diagnose_index 同源(个股版;单独函数明示意图、解耦指数层)。"""
    return {
        "trend": ti.trend_state(close, period),
        "deviation": ti.deviation_extremes(close, period, lookback),
        "breakout": ti.breakout_grade(close, period),
        "choppy": ti.is_choppy(close, period),
    }


# ---------- 顶层装配(读 store) ----------
def diagnose_stock(symbol: str, store, config=None) -> dict:
    """读 store(price + valuation + financials + dividend)→ 算特征 → 三类判定 + 估值 zone + E3。
    返回结构化 dict(给 C1 后续看板/提醒消费)。valid=False 表示数据不足无法判定。"""
    cfg = config or get_config()
    params = cfg.params
    sp = (params.get("stock", {}) or {})
    cy = sp.get("classify", {}) or {}
    vp = sp.get("valuation", {}) or {}

    # --- 价格(daily_prices 复用) ---
    price_df = store.get_series(symbol)
    close = price_df["close"].astype(float) if len(price_df) else pd.Series(dtype=float)
    price = float(close.iloc[-1]) if len(close) else np.nan

    # --- 估值(PE/PB 分位) ---
    pe_df = store.get_stock_valuation_series(symbol, "pe_ttm")
    pb_df = store.get_stock_valuation_series(symbol, "pb")
    pe_vals = pe_df["value"] if (len(pe_df) and "value" in pe_df.columns) else None
    pb_vals = pb_df["value"] if (len(pb_df) and "value" in pb_df.columns) else None
    pe_lookback = vp.get("pe_lookback_years")
    pe_pct = valuation_percentile(pe_vals, pe_lookback)
    pb_pct = valuation_percentile(pb_vals, pe_lookback)

    # --- 财报(年报 CAGR + 利润波动) ---
    rev_annual = annual_only(store.get_stock_financials_series(symbol, "revenue"))
    np_annual = annual_only(store.get_stock_financials_series(symbol, "net_profit"))
    cagr_years = int(cy.get("growth_cagr_years", 3))
    rev_cagr = revenue_cagr(rev_annual, cagr_years)
    np_cagr = revenue_cagr(np_annual, cagr_years)
    vol_years = int(cy.get("profit_vol_years", 5))
    pvol, min_g = profit_growth_volatility(np_annual, vol_years)

    # --- 分红(股息率) ---
    dv = store.get_stock_dividend_series(symbol)
    div_yield = stock_dividend_yield(dv, price)

    features = {
        "revenue_cagr": rev_cagr, "profit_cagr": np_cagr,
        "profit_vol": pvol, "min_profit_growth": min_g,
        "pe_pct": pe_pct, "div_yield": div_yield,
    }
    primary, secondary = classify_stock(features, params)

    return {
        "symbol": symbol,
        "price_last": price,
        "date_last": str(close.index[-1]) if len(close) else None,
        "pe_ttm": float(pe_vals.iloc[-1]) if pe_vals is not None and len(pe_vals) else np.nan,
        "pb": float(pb_vals.iloc[-1]) if pb_vals is not None and len(pb_vals) else np.nan,
        "features": features,
        "classification": {"primary": primary, "secondary": secondary},
        "valuation_zone": diagnose_valuation_zone(
            pe_pct, pb_pct,
            lo=float(vp.get("zone_low_percentile", 0.20)),
            hi=float(vp.get("zone_high_percentile", 0.80))),
        "price_timing": (diagnose_price_timing(close)
                         if len(close) >= ti.MA_PERIOD else {"valid": False}),
        "valid": primary is not None,
    }


# ---------- S07 利润来源归因(业绩/估值/分红 三段拆解) ----------
def _as_of(df: pd.DataFrame, date: str):
    """df indexed by date(str) → 返回 index ≤ date 的最后一行(as-of join);无则 None。
    用于取持仓期端点的价/PE(非交易日取前一交易日;baidu PE 稀疏取最近的)。"""
    if df is None or len(df) == 0:
        return None
    sub = df[df.index <= date]
    return sub.iloc[-1] if len(sub) else None


def profit_attribution(p0: float, pe0: float, p1: float, pe1: float,
                       dividends_per_share: float) -> dict:
    """持仓期 [t0,t1] 总回报拆为 业绩/估值/分红 三段(S07 茅台拆解)。单位:占 P0 的比例(可加)。

    EPS 由 价格/PE(TTM) 反推(市场隐含 TTM EPS,与 PE 序列同源,避开财报 TTM 滚动)。
      业绩贡献 = EPS1/EPS0 − 1           (PE 恒定下的价格回报)
      价格回报 = P1/P0 − 1               (除息后,raw 价路径)
      估值贡献 = 价格回报 − 业绩贡献      (残差,含交互项;PE 扩张为正、收缩为负)
      分红贡献 = 期间每股现金分红 / P0
      总回报   = 价格回报 + 分红贡献 = 业绩 + 估值 + 分红  (additive,精确对账)
    raw 价在此成立:除息日跌幅已在价格路径里,P1+Div−P0 即真实股东回报。
    返回 share_*(各段占总回报比,仅 total>0 有意义,否则 NaN)。"""
    if _nan(p0) or _nan(pe0) or _nan(p1) or _nan(pe1) or p0 <= 0 or pe0 <= 0 or pe1 <= 0:
        return {"valid": False, "reason": "缺价或 PE(≤0)"}
    div = 0.0 if _nan(dividends_per_share) else float(dividends_per_share)
    eps0 = p0 / pe0
    eps1 = p1 / pe1
    earnings_ret = eps1 / eps0 - 1.0
    price_ret = p1 / p0 - 1.0
    valuation_ret = price_ret - earnings_ret
    dividend_ret = div / p0
    total_ret = price_ret + dividend_ret

    def _share(x):
        # share 仅 total>0 有意义(亏损期/对冲大的成分会让占比失真,如茅台估值杀导致 total<0
        # 时占比会变 -454% 这种);成分回报(业绩/估值/分红)本身才是稳健的。
        return float("nan") if total_ret <= 1e-9 else x / total_ret

    return {
        "valid": True,
        "p0": p0, "p1": p1, "pe0": pe0, "pe1": pe1,
        "eps0": eps0, "eps1": eps1, "dividends_per_share": div,
        "earnings_return": earnings_ret,       # 业绩
        "valuation_return": valuation_ret,     # 估值
        "dividend_return": dividend_ret,       # 分红
        "price_return": price_ret,
        "total_return": total_ret,
        "share_earnings": _share(earnings_ret),
        "share_valuation": _share(valuation_ret),
        "share_dividend": _share(dividend_ret),
    }


def diagnose_attribution(symbol: str, store, t0: str, t1: str, config=None) -> dict:
    """读 store(price + pe_ttm + dividend)→ 拆 [t0,t1] 持仓期回报三段。端点取 as-of(非交易日/PE 稀疏
    取最近的前值)。分红取 (t0, t1] 内每股现金之和。返回 profit_attribution + 端点日期。"""
    px = store.get_series(symbol)
    pe = store.get_stock_valuation_series(symbol, "pe_ttm")
    if len(px) == 0 or len(pe) == 0:
        return {"valid": False, "reason": "无价格或 PE 数据", "symbol": symbol}

    row0, row1 = _as_of(px, t0), _as_of(px, t1)
    pe0r, pe1r = _as_of(pe, t0), _as_of(pe, t1)
    if row0 is None or row1 is None or pe0r is None or pe1r is None:
        return {"valid": False, "reason": "端点取值失败(t0/t1 超出数据范围?)",
                "symbol": symbol, "t0": t0, "t1": t1}

    p0, p1 = float(row0["close"]), float(row1["close"])
    pe0, pe1 = float(pe0r["value"]), float(pe1r["value"])

    # 分红:ex_date 在 (t0, t1] 的每股现金之和
    dv = store.get_stock_dividend_series(symbol)
    div = 0.0
    if len(dv) and "cash_per_share" in dv.columns:
        cps = pd.to_numeric(dv["cash_per_share"], errors="coerce").dropna()
        in_period = cps[(cps.index > t0) & (cps.index <= t1)]
        div = float(in_period.sum()) if len(in_period) else 0.0

    out = profit_attribution(p0, pe0, p1, pe1, div)
    out.update({"symbol": symbol,
                "t0": str(row0.name), "t1": str(row1.name),  # 实际取到的 as-of 交易日
                "period_dividends_per_share": div})
    return out


def attribution_by_year(symbol: str, store, years: int = 6) -> list[dict]:
    """最近 `years` 个自然年的 S07 利润归因(业绩/估值/分红 三段,占年初价比例,可加)——给看板画
    多年堆叠柱用。每年一根:该年最后一个交易日 → 次年最后一个交易日的持仓回报拆解(12-31 常是
    周末,故用「每自然年最后一个交易日」作锚,不用死 1231)。复用 diagnose_attribution(端点
    as-of、分红按年汇总、profit_attribution 三段对账)。返回 [{year,earnings,valuation,dividend,
    total}],仅含有效年(端点价/PE 齐全);无价格或不足 2 个年末锚 → []。"""
    px = store.get_series(symbol)
    if len(px) == 0:
        return []
    last_by_year: dict[str, str] = {}              # 年 → 该年最后一个交易日
    for d in px.index:
        y = str(d)[:4]
        if y not in last_by_year or str(d) > last_by_year[y]:
            last_by_year[y] = str(d)
    anchors = sorted(last_by_year.values())[-(years + 1):]   # years+1 锚 → 至多 years 个区间
    if len(anchors) < 2:
        return []
    rows = []
    for t0, t1 in zip(anchors[:-1], anchors[1:]):
        a = diagnose_attribution(symbol, store, t0, t1)
        if not a.get("valid"):
            continue
        rows.append({"year": str(t1)[:4],
                     "earnings": a["earnings_return"], "valuation": a["valuation_return"],
                     "dividend": a["dividend_return"], "total": a["total_return"]})
    return rows


# ---------- S10 戴维斯双击/双杀 ----------
def davis_signal(profit_yoy_latest: float, profit_yoy_prev: float,
                 pe_change: float, pe_pct: float,
                 pe_low: float = 0.30, pe_high: float = 0.70) -> dict:
    """戴维斯双击/双杀信号(S10)。业绩方向 × 估值方向 → 6 档:

      double_play        业绩正增加速 + PE 扩张            → 强双击·进行中
      double_play_setup  低 PE + 业绩正增                  → 双击买点·机会(PE 有重估空间)
      double_play_watch  低 PE + 业绩负增(探底)            → 双击观察·待回升确认(cheap+dip≠杀估值)
      double_kill        业绩负增 + PE 收缩(PE 非低位)     → 强双杀·进行中
      double_kill_risk   高 PE + 业绩负增                  → 双杀预警·风险(PE 有杀估值空间)
      neutral            中性

    关键:双杀是「杀高估值」,故 PE 已在低位(pe_lo)时不判 kill——那是探底/双击前夜,归 watch。
    需 profit_yoy_latest(无最近增速 → 无法判方向 → valid=False)。pe_change/pe_pct 可缺(降级用
    pe_pct 低位/高位判 setup/watch/risk)。原始 Davis 双击 = 低位买进盈利回升的股票,享业绩+估值双升。"""
    if _nan(profit_yoy_latest):
        return {"type": None, "label": "数据不足(缺最近净利增速)", "valid": False}
    growing = profit_yoy_latest > 0
    accel = (not _nan(profit_yoy_prev)) and (profit_yoy_latest > profit_yoy_prev)
    pe_up = (not _nan(pe_change)) and pe_change > 0
    pe_down = (not _nan(pe_change)) and pe_change < 0
    pe_hi = (not _nan(pe_pct)) and pe_pct > pe_high
    pe_lo = (not _nan(pe_pct)) and pe_pct < pe_low

    if growing and accel and pe_up:
        t, lbl = "double_play", "戴维斯双击·业绩正增加速+估值扩张"
    elif pe_lo and growing:
        t, lbl = "double_play_setup", "双击买点·低PE+业绩正增"
    elif pe_lo and (not growing):
        t, lbl = "double_play_watch", "双击观察·低PE+业绩探底待回升"
    elif (not growing) and pe_down:
        t, lbl = "double_kill", "戴维斯双杀·业绩负增+估值收缩"
    elif pe_hi and (not growing):
        t, lbl = "double_kill_risk", "双杀预警·高PE+业绩负增"
    else:
        t, lbl = "neutral", "中性"
    return {
        "type": t, "label": lbl, "valid": True,
        "drivers": {"growing": growing, "accel": accel, "pe_change": pe_change,
                    "pe_pct": pe_pct, "pe_up": pe_up, "pe_down": pe_down,
                    "pe_high": pe_hi, "pe_low": pe_lo},
    }


def diagnose_davis(symbol: str, store, config=None) -> dict:
    """读 store(年报净利 YoY + pe_ttm 变化/分位)→ 戴维斯双击/双杀信号。
    净利 YoY 用年报(期末 1231,全年口径);PE 变化 = 今 vs pe_change_lookback_days 前;PE 分位用 baidu 全史。"""
    cfg = config or get_config()
    params = cfg.params
    sp = (params.get("stock", {}) or {})
    dp = sp.get("davis", {}) or {}
    pe_low = float(dp.get("pe_low_percentile", 0.30))
    pe_high = float(dp.get("pe_high_percentile", 0.70))
    lookback = int(dp.get("pe_change_lookback_days", 365))

    # 净利 YoY(年报)
    np_annual = annual_only(store.get_stock_financials_series(symbol, "net_profit")).dropna()
    yoy_latest = np.nan
    yoy_prev = np.nan
    if len(np_annual) >= 2:
        vals = np_annual.astype(float).sort_index()
        if float(vals.iloc[-2]) > 0:
            yoy_latest = float(vals.iloc[-1]) / float(vals.iloc[-2]) - 1.0
        if len(vals) >= 3 and float(vals.iloc[-3]) > 0:
            yoy_prev = float(vals.iloc[-2]) / float(vals.iloc[-3]) - 1.0

    # PE 变化(今 vs lookback 天前) + 分位
    pe = store.get_stock_valuation_series(symbol, "pe_ttm")
    pe_vals = pe["value"] if (len(pe) and "value" in pe.columns) else None
    pe_lookback = (sp.get("valuation", {}) or {}).get("pe_lookback_years")
    pe_pct = valuation_percentile(pe_vals, pe_lookback)
    pe_change = np.nan
    if pe_vals is not None and len(pe_vals) >= 2:
        idx = pd.to_datetime(pe_vals.index, errors="coerce")
        cutoff = idx.max() - pd.Timedelta(days=lookback)
        past = pe_vals[idx <= cutoff]
        if len(past) and float(past.iloc[-1]) > 0:
            pe_change = float(pe_vals.iloc[-1]) / float(past.iloc[-1]) - 1.0

    sig = davis_signal(yoy_latest, yoy_prev, pe_change, pe_pct, pe_low, pe_high)
    sig.update({"symbol": symbol,
                "profit_yoy_latest": yoy_latest, "profit_yoy_prev": yoy_prev,
                "pe_change": pe_change, "pe_pct": pe_pct})
    return sig


# ---------- S08 避坑(公告时间差 + 两年复合增速 + 异常高增速最小值分母) ----------
# 法定最晚披露日(保守上界,防 lookahead):年报次年 4/30、一季报当年 4/30、半年报 8/31、三季报 10/31。
_DISCLOSURE_DEADLINE = {"1231": "-04-30", "0331": "-04-30", "0630": "-08-31", "0930": "-10-31"}


def disclosure_deadline(report_period: str) -> str | None:
    """报告期(YYYYMMDD)→ 法定最晚披露日(YYYY-MM-DD)。年报(Y-12-31)→(Y+1)-04-30;
    一季报→当年 04-30;半年报→08-31;三季报→10/31。非标准期末 → None。"""
    s = str(report_period)
    if len(s) != 8 or not s.isdigit():
        return None
    y, md = s[:4], s[4:]
    suffix = _DISCLOSURE_DEADLINE.get(md)
    if suffix is None:
        return None
    year = str(int(y) + 1) if md == "1231" else y  # 年报跨年到次年
    return f"{year}{suffix}"


def is_disclosed_by(report_period: str, asof: str) -> bool:
    """该报告期数据在 asof(YYYY-MM-DD)当日是否已必然公开(按法定最晚披露日的保守上界)。
    用于回测/诊断防 lookahead:只用 is_disclosed_by 为 True 的报告期。"""
    dl = disclosure_deadline(report_period)
    return dl is not None and dl <= str(asof)


def growth_quality(base: float, latest: float, prev_base: float = np.nan,
                   abnormal_threshold: float = 1.5, low_base_frac: float = 0.5) -> dict:
    """避坑:YoY 增速可信度(S08 异常高增速·最小值分母 + 两年复合增速)。

      base       = 上年值(分母), latest = 当年值, prev_base = 前年值(算 2y CAGR)
    异常(abnormal=True)当:① YoY ≥ abnormal_threshold(如 150%)② 或 base < low_base_frac×prev_base
    (上年腰斩→低基数,分母被压低→增速虚高)。异常时可信增速 trustworthy 用 2y CAGR(平滑基数效应),
    否则 = YoY。返回 {yoy, cagr2, abnormal, reason, trustworthy}。"""
    if _nan(base) or _nan(latest) or base <= 0 or latest <= 0:
        return {"valid": False, "reason": "基数/当期 ≤0 或缺失"}
    yoy = latest / base - 1.0
    low_base = (not _nan(prev_base)) and prev_base > 0 and base < low_base_frac * prev_base
    abnormal = (yoy >= abnormal_threshold) or low_base
    cagr2 = ((latest / prev_base) ** 0.5 - 1.0) if (not _nan(prev_base) and prev_base > 0) else np.nan
    trustworthy = cagr2 if (abnormal and not _nan(cagr2)) else yoy

    reasons = []
    if yoy >= abnormal_threshold:
        reasons.append(f"YoY {yoy * 100:.0f}%≥{abnormal_threshold * 100:.0f}%")
    if low_base:
        reasons.append(f"低基数(上年 {base:.4g} < 前年 {prev_base:.4g}×{low_base_frac})")
    return {
        "valid": True, "yoy": yoy, "cagr2": cagr2, "abnormal": abnormal,
        "reason": "；".join(reasons) if reasons else "", "trustworthy": trustworthy,
        "base": base, "latest": latest, "prev_base": prev_base,
    }


def _metric_pitfall(np_annual: pd.Series, pp: dict) -> dict:
    """单指标(年报序列)的避坑诊断:最近一年 YoY + 2y CAGR + 异常标记。np_annual 已是年报、升序。"""
    s = np_annual.dropna().astype(float).sort_index() if np_annual is not None else pd.Series(dtype=float)
    if len(s) < 2:
        return {"valid": False, "reason": "年报<2 期"}
    latest = float(s.iloc[-1])
    base = float(s.iloc[-2])
    prev_base = float(s.iloc[-3]) if len(s) >= 3 else np.nan
    g = growth_quality(base, latest, prev_base,
                       abnormal_threshold=float(pp.get("abnormal_growth_threshold", 1.5)),
                       low_base_frac=float(pp.get("low_base_frac", 0.5)))
    g["latest_period"] = str(s.index[-1])
    return g


def diagnose_pitfalls(symbol: str, store, config=None, asof: str | None = None) -> dict:
    """读 store(年报 net_profit + revenue)→ S08 避坑诊断:异常高增速/低基数(用 2y CAGR 纠偏)+
    最新报告期公告时间差(G2,asof 是否已披露)。预告链(G1)待补 stock_yjyg 数据后加。
    签名 (symbol, store, config, asof) 与其他 diagnose_* 一致(config 第 3 位参)。"""
    cfg = config or get_config()
    pp = (cfg.params.get("stock", {}) or {}).get("pitfalls", {}) or {}
    asof = asof or datetime.now().strftime("%Y-%m-%d")

    np_p = _metric_pitfall(annual_only(store.get_stock_financials_series(symbol, "net_profit")), pp)
    rev_p = _metric_pitfall(annual_only(store.get_stock_financials_series(symbol, "revenue")), pp)

    # 公告时间差(G2):最新报告期的法定披露日 + 是否已过
    latest_period = np_p.get("latest_period") or rev_p.get("latest_period")
    dl = disclosure_deadline(latest_period) if latest_period else None
    disclosed = (dl is not None and dl <= asof) if dl else None

    return {
        "symbol": symbol, "asof": asof,
        "net_profit": np_p, "revenue": rev_p,
        "disclosure": {"latest_period": latest_period, "deadline": dl,
                       "disclosed_by_asof": disclosed},
        "valid": np_p.get("valid") or rev_p.get("valid"),
    }


# ---------- S08-G1 业绩预告链(A1 拐点 / A2 转空 / G1 窗口) ----------
_FORECAST_SENTIMENT = {
    "预增": "bullish", "略增": "bullish", "续盈": "bullish", "扭亏": "bullish",
    "预减": "bearish", "略减": "bearish", "首亏": "bearish",
    "续亏": "bearish", "增亏": "bearish", "减亏": "bearish",
    "不确定": "neutral",
}


def forecast_type_sentiment(type_str: str) -> str:
    """业绩预告类型 → 多头(bullish)/空头(bearish)/中性(neutral)。多=预增略增续盈扭亏;
    空=预减略减首亏续亏增亏减亏;不确定→中性。未知类型→中性。"""
    if not type_str or (isinstance(type_str, float) and np.isnan(type_str)):
        return "neutral"
    return _FORECAST_SENTIMENT.get(str(type_str).strip(), "neutral")


def diagnose_forecast_chain(symbol: str, store, config=None) -> dict:
    """业绩预告链诊断(S08-G1):读 stock_forecast 全历史 → 最新预告 + A1/A2/G1。

      A1 业绩增速下滑拐点:最新预告 yoy < 上期预告 yoy(预告增速在掉 → 抱着颗雷)
      A2 预告类型转空:  sentiment 由多(bullish)转空(bearish)= 戴维斯双杀前兆(预增→预减/首亏)
      G1 披露窗口:      最新预告的存在即代表该期披露窗口已开 + 公司提前表态(催化剂/风险事件)
    预告稀疏(稳定股常无),无历史 → valid=False。"""
    fc = store.get_stock_forecast_series(symbol)
    if fc is None or len(fc) == 0:
        return {"symbol": symbol, "valid": False, "reason": "无业绩预告历史(稳定股常无预告)"}
    fc = fc.sort_index()
    latest = fc.iloc[-1]
    latest_yoy = latest.get("yoy")
    latest_sent = forecast_type_sentiment(latest.get("type"))
    out = {
        "symbol": symbol, "valid": True, "n_periods": len(fc),
        "latest": {"period": str(fc.index[-1]), "yoy": latest_yoy,
                   "type": latest.get("type"), "sentiment": latest_sent,
                   "announce_date": latest.get("announce_date")},
        "latest_sentiment": latest_sent,
        "a1_deceleration": False, "a2_turn_bearish": False,
        "prior": None,
    }
    if len(fc) >= 2:
        prior = fc.iloc[-2]
        prior_yoy = prior.get("yoy")
        prior_sent = forecast_type_sentiment(prior.get("type"))
        out["prior"] = {"period": str(fc.index[-2]), "yoy": prior_yoy,
                        "type": prior.get("type"), "sentiment": prior_sent}
        if (not _nan(latest_yoy)) and (not _nan(prior_yoy)) and latest_yoy < prior_yoy:
            out["a1_deceleration"] = True                            # 增速下滑拐点
        if latest_sent == "bearish" and prior_sent == "bullish":
            out["a2_turn_bearish"] = True                             # 多→空·双杀前兆
    return out


# ---------- C2 个股提醒装配入口 ----------
def collect_stock_alerts(symbols, store, config=None, index_diag=None,
                         asof: str | None = None, names=None) -> list:
    """跑全套个股诊断(diagnose_stock + pitfalls + forecast_chain)→ 装配 snapshots → evaluate_stocks。
    C2 个股提醒的可调用入口(给报告脚本/CLI/微信推送)。names={symbol:显示名} 可选。
    返回 alert 列表(形状同 alerts.evaluate,可直喂 alerts.format_for_push)。"""
    from .alerts import evaluate_stocks  # lazy: alerts 不 import 本模块,避免循环

    cfg = config or get_config()
    names = names or {}
    stocks: dict = {}
    for sym in symbols:
        ds = diagnose_stock(sym, store, cfg)
        stocks[sym] = {
            "name": names.get(sym, sym),
            "price_timing": ds.get("price_timing") or {},
            "pitfalls": diagnose_pitfalls(sym, store, cfg, asof=asof),
            "forecast": diagnose_forecast_chain(sym, store, cfg),
        }
    return evaluate_stocks(stocks, index_diag=index_diag, asof=asof)


def diagnose_stock_full(symbol: str, store, config=None,
                        asof: str | None = None) -> dict:
    """打包全套个股诊断(给报告/看板用):diagnose_stock(分类+估值zone+E3+features)+
    pitfalls(S08 避坑)+ forecast_chain(预告链 A1/A2/G1)+ davis(S10 戴维斯)。一个 dict。"""
    cfg = config or get_config()
    base = diagnose_stock(symbol, store, cfg)
    base["pitfalls"] = diagnose_pitfalls(symbol, store, cfg, asof=asof)
    base["forecast"] = diagnose_forecast_chain(symbol, store, cfg)
    base["davis"] = diagnose_davis(symbol, store, cfg)
    return base
