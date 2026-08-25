"""指数择时层诊断 — 组装 B1 指标 + B0 数据成结构化诊断(给 dashboard/告警消费)。

分层:
  diagnose_index(close, ...)  — 纯:单指数 60日线择时诊断(trend/deviation/breakout/choppy)
  diagnose_valuation(store)   — 估值开关(④):沪深300 PE 分位 + 全市场 PB 分位 → 敏感度建议
  diagnose_style(store)       — 蓝筹 vs 成长 → 仓位倾向(S13)
  diagnose_relative_cycle(store) — ⑦相对周期律: 创业板 vs 上证 点差在包络内的位置 → 极点/中枢
  diagnose_fear_greed(store)  — ⑨恐惧贪婪指数: 5成分(动量/流动性/波动/估值/杠杆) → 0-100 复合(只读温度计)
  diagnose_money_conditions(store) — ⑪货币条件: M2/M1同比+剪刀差+社融脉冲+episode状态机(只读温度计)
  diagnose_layer(store)       — 顶层:遍历6宽基 + 估值 + 风格 + 相对周期 + 恐贪 + 货币,返回完整指数择时诊断
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import indicators as ti

# 7 broad indices (same set as DataManager.BROAD_INDICES; duplicated here so the diagnose
# layer has a stable iteration order independent of the manager). 顺序=看板展示序
# (①偏离极值曲线/②趋势表/⑤信号区共用): 上证综指(总览)→沪深300→创业板→科创50→上证50→中证500→中证1000。
# 000001(上证综指) is the benchmark for ⑦相对周期律 (创业板 vs 上证 点差周期)。
BROAD_INDICES = [("000001", "上证综指"), ("000300", "沪深300"), ("399006", "创业板指"),
                 ("000688", "科创50"), ("000016", "上证50"), ("000905", "中证500"),
                 ("000852", "中证1000")]
VALUATION_INDEX = "沪深300"            # ④估值开关以沪深300(大盘benchmark)为主
PE_PCT_LOW, PE_PCT_HIGH = 0.20, 0.80   # 估值低/高位分位阈值(④ 敏感度建议)


def diagnose_index(close: pd.Series, period: int = ti.MA_PERIOD,
                   lookback: int | None = None) -> dict:
    """单指数 60日线择时诊断(纯)。组装 trend / deviation / breakout / cross / choppy。
    cross = 真正的穿越事件(突破/跌破应基于此,非「在线上」;breakout_grade 仍给位置强度)。"""
    return {
        "trend": ti.trend_state(close, period),
        "deviation": ti.deviation_extremes(close, period, lookback),
        "breakout": ti.breakout_grade(close, period),
        "cross": ti.last_ma_cross(close, period),
        "choppy": ti.is_choppy(close, period),
    }


def diagnose_valuation(store, pe_lookback_years: int = 10) -> dict:
    """估值开关(④):沪深300 **同口径** PE+PB 分位 → 敏感度建议(聚焦,不含全市场)。

    S13:大盘估值低位(3000点下)趋势信号可更激进,高位宜保守。
    zone 用沪深300 同口径 PE+PB 判断(PE=PB/ROE,故 PE 高/PB 低 = ROE 偏弱 = 结构分化):
      双低 → 低位·可激进 / 双高 → 高位·宜保守 / 跨中线 → 结构分化·宜观望 / 都中间 → 中位·中性。
    全市场 PB 的「大小盘温差」见 diagnose_market_temp(独立组件)。"""
    pe = store.get_index_pe_series(VALUATION_INDEX)
    pb_same = store.get_index_pb_series(VALUATION_INDEX)
    out = {"pe_index": VALUATION_INDEX,
           "pe_ttm": np.nan, "pe_pct": np.nan,
           "pb": np.nan, "pb_pct": np.nan,
           "zone": "—", "valid": False}

    def _pct(series, col, lookback):
        s = series.iloc[-252 * lookback:] if lookback else series
        last = float(s[col].iloc[-1])
        return last, float((s[col] < last).sum()) / len(s)

    if len(pe) >= 20:
        out["pe_ttm"], out["pe_pct"] = _pct(pe, "pe_ttm", pe_lookback_years)
    if len(pb_same) >= 20:
        out["pb"], out["pb_pct"] = _pct(pb_same, "pb", pe_lookback_years)

    # zone: 沪深300 同口径 PE+PB(四档)
    if not np.isnan(out["pe_pct"]) and not np.isnan(out["pb_pct"]):
        pe_p, pb_p = out["pe_pct"], out["pb_pct"]
        pe_lo, pe_hi = pe_p < PE_PCT_LOW, pe_p > PE_PCT_HIGH
        pb_lo, pb_hi = pb_p < PE_PCT_LOW, pb_p > PE_PCT_HIGH
        if pe_lo and pb_lo:
            out["zone"] = "低位·可激进"
        elif pe_hi and pb_hi:
            out["zone"] = "高位·宜保守"
        elif (pe_p > 0.50) != (pb_p > 0.50):    # 跨中线:一偏贵一偏便宜 → 分化
            out["zone"] = "结构分化·宜观望"
        else:                                    # 都在中间同侧 → 中性
            out["zone"] = "中位·中性"
        out["valid"] = True
    elif not np.isnan(out["pe_pct"]):   # fallback: 只有 PE(沪深300 PB 缺失时)
        p = out["pe_pct"]
        out["zone"] = ("低位·可激进" if p < PE_PCT_LOW
                       else "高位·宜保守" if p > PE_PCT_HIGH
                       else "中位·中性")
        out["valid"] = True
    return out


def diagnose_market_temp(store, lookback_years: int = 10) -> dict:
    """市场温度·大小盘温差(独立组件):全A PB 分位 vs 沪深300 PB 分位。

    温差 = 全市场PB分位 − 沪深300PB分位:
      >0  → 全市场(含小盘)比大盘蓝筹贵 → 小盘偏贵(中小盘行情偏热)
      <0  → 全市场比大盘便宜 → 小盘偏便宜(潜在小盘机会)
      ≈0  → 大小盘估值同步。
    沪深300 只看 300 只大盘;全市场中位数覆盖 5000+ 全市场(含中小盘)→ 差值即大小盘温差。"""
    pb_mkt = store.get_market_pb_series()
    pb_hs = store.get_index_pb_series(VALUATION_INDEX)
    out = {"market_pb": np.nan, "market_pct": np.nan,
           "hs300_pb": np.nan, "hs300_pct": np.nan,
           "diff": np.nan, "regime": "—", "valid": False}

    def _pct(series, col, lookback):
        s = series.iloc[-252 * lookback:] if lookback else series
        last = float(s[col].iloc[-1])
        return last, float((s[col] < last).sum()) / len(s)

    if len(pb_mkt) >= 20:
        last = float(pb_mkt["pb"].iloc[-1])
        out["market_pb"] = last
        if "pct_all" in pb_mkt.columns and not np.isnan(pb_mkt["pct_all"].iloc[-1]):
            out["market_pct"] = float(pb_mkt["pct_all"].iloc[-1])
        else:
            _, out["market_pct"] = _pct(pb_mkt, "pb", lookback_years)
    if len(pb_hs) >= 20:
        out["hs300_pb"], out["hs300_pct"] = _pct(pb_hs, "pb", lookback_years)

    if not np.isnan(out["market_pct"]) and not np.isnan(out["hs300_pct"]):
        diff = out["market_pct"] - out["hs300_pct"]
        out["diff"] = diff
        if abs(diff) < 0.10:
            out["regime"] = "大小盘同步"
        elif diff > 0:
            out["regime"] = "小盘偏贵"
        else:
            out["regime"] = "小盘偏便宜"
        out["valid"] = True
    return out


def diagnose_style(store, period: int = ti.MA_PERIOD) -> dict:
    """蓝筹(上证50) vs 成长(创业板指) 仓位倾向(S13)。"""
    blue = store.get_index_daily_series("000016")
    growth = store.get_index_daily_series("399006")
    if len(blue) < period or len(growth) < period:
        return {"blue_up": None, "growth_up": None, "lean": None, "valid": False}
    return ti.style_allocation(blue["close"], growth["close"], period)


def diagnose_relative_cycle(store, growth: str = "399006", benchmark: str = "000001",
                            envelope_years: int = 5, drift_years: int = 10,
                            short_momentum_window: int = 20) -> dict:
    """⑦ 相对周期律(只读诊断旁路): 创业板 vs 上证 点差在滚动包络内的位置。

    作者框架: spread = 上证 − 创业板(点) 在「上一周期+本周期」振幅区间 [min_spread,
    max_spread] 内的位置 env_pos ∈ [0,1] → 极点(有回归方向)/中枢(无edge,仅风险解除)。
      env_pos        — headline(作者口径,三次调用皆由此复现)
      rank_pct       — 同窗口 raw spread 秩分位 = 历史稀有度(house 口径,副指标)
      drift_pts_per_yr — 长窗口 OLS 结构漂移率(info;<0 = 成长结构性跑赢,约 −40~−50/年)
    B 风格轮动持续性(mom_now 短期动量 + 连续跑盈 run)折进同一节。pair 默认评论员原文口径
    (399006 创业板指, 000001 上证综指)。硬编码常量(贴合 house style,不读 params.yaml)。"""
    bm = store.get_index_daily_series(benchmark)
    gr = store.get_index_daily_series(growth)
    out = {"benchmark": benchmark, "growth": growth,
           "min_spread": np.nan, "max_spread": np.nan, "envelope_mid": np.nan,
           "spread_now": np.nan, "env_pos": np.nan, "zone": "—",
           "rank_pct": np.nan, "drift_pts_per_yr": np.nan,
           "chan_lo": np.nan, "chan_hi": np.nan, "chan_mid": np.nan,
           "chan_pos": np.nan, "chan_zone": "—",
           "mom_now": np.nan, "run_dir": None, "run": 0,
           "envelope_years": envelope_years, "valid": False}
    if len(bm) < 252 * 2 or len(gr) < 252 * 2:
        return out
    spread = ti.relative_spread_series(bm["close"], gr["close"])
    if len(spread) < 252 * 2:
        return out
    ce = ti.cycle_extremes(spread, envelope=252 * envelope_years,
                           drift_lookback=252 * drift_years)
    mom = ti.relative_momentum(spread, short_momentum_window)
    run = ti.consecutive_run(spread)
    ch = ti.cycle_trend_channel(spread, envelope=252 * envelope_years)   # 图上趋势通道(与看板同源)
    out.update({
        "min_spread": ce["min_spread"], "max_spread": ce["max_spread"],
        "envelope_mid": ce["envelope_mid"], "spread_now": ce["spread_now"],
        "env_pos": ce["env_pos"], "zone": ti.classify_cycle(ce["env_pos"]),
        "rank_pct": ce["rank_pct"], "drift_pts_per_yr": ce["drift_pts_per_yr"],
        "chan_lo": ch["lower_now"], "chan_hi": ch["upper_now"], "chan_mid": ch["mid_now"],
        "chan_pos": ch["chan_pos"], "chan_zone": ti.classify_cycle(ch["chan_pos"]),
        "mom_now": mom["mom_now"], "run_dir": run["direction"], "run": run["run"],
        "valid": ce["valid"],
    })
    return out


def _binom_p_one_sided(w: int, n: int) -> float:
    """单边二项检验 p = P(X ≥ w | n, p=0.5)。w = 胜次, n = 样本。用 math.comb 精确计算。"""
    if n <= 0 or w <= 0:
        return float("nan") if n <= 0 else 1.0
    w = min(w, n)
    return sum(math.comb(n, i) for i in range(w, n + 1)) / (2 ** n)


def diagnose_turnover(store, index_sym: str = "000001", ma: int = ti.TURNOVER_MA,
                     threshold: float = ti.TURNOVER_DRY_THRESHOLD) -> dict:
    """⑧ 成交量地量监测(只读诊断旁路): 两市成交额 / MA250 → 地量 flag + event-study 经验值。

    地量 = 成交额/MA250 ≤ threshold(regime 自适应,消除名义额长期上行)。event-study 给
    '量底→价底时效'(中位~1月,扎实)与'各 horizon 胜率'(成熟市场均~50%无 edge)作经验参考。
    极端子集(近>0.5年最低)60日胜率~71%但样本薄(p≈0.09未显著)→ 暗示非定律。index_sym 默认上证综指。"""
    mt = store.get_market_turnover_series()
    px_df = store.get_index_daily_series(index_sym)
    out = {"index": index_sym, "turnover_now": np.nan, "turnover_yi": np.nan,
           "ratio_now": np.nan, "is_dry": False, "pct_3yr": np.nan, "pct_5yr": np.nan,
           "days_since_dry": None, "sample": 0,
           "time_to_bottom_median": np.nan, "time_to_bottom_max": np.nan,
           "win_rate_20": np.nan, "win_rate_60": np.nan, "median_ret_60": np.nan,
           "extreme_sample": 0, "extreme_win_rate_60": np.nan,
           "extreme_median_ret_60": np.nan, "extreme_pvalue": np.nan,
           "valid": False}
    if len(mt) < ma + 61 or len(px_df) < 252:
        return out
    tseries = mt["total"]
    pos = tseries[tseries > 0]
    if len(pos) < ma:
        return out
    t_now = float(pos.iloc[-1])
    mean_ma = float(tseries.iloc[-ma:].mean())
    ratio_now = t_now / mean_ma if mean_ma > 0 else float("nan")
    events = ti.turnover_dry_events(tseries, ma, threshold, start=ti.TURNOVER_MATURE_START)
    days_since = len(pos[pos.index > events[-1]]) if events else None
    stats = ti.volume_bottom_stats(tseries, px_df["close"], ma, threshold,
                                   start=ti.TURNOVER_MATURE_START)
    stats_x = ti.volume_bottom_stats(tseries, px_df["close"], ma, threshold,
                                     start=ti.TURNOVER_MATURE_START,
                                     min_lookback=ti.TURNOVER_EXTREME_LOOKBACK)
    ex_n = stats_x["sample"]
    ex_wins = int(round(stats_x["win_rate_60"] * ex_n)) if ex_n and not np.isnan(stats_x["win_rate_60"]) else 0
    return {
        "index": index_sym, "turnover_now": t_now, "turnover_yi": t_now / 1e8,
        "ratio_now": ratio_now, "is_dry": (not np.isnan(ratio_now)) and ratio_now <= threshold,
        "pct_3yr": ti.turnover_percentile(tseries, 252 * 3),
        "pct_5yr": ti.turnover_percentile(tseries, 252 * 5),
        "days_since_dry": days_since,
        "sample": stats["sample"],
        "time_to_bottom_median": stats["time_to_bottom_median"],
        "time_to_bottom_max": stats["time_to_bottom_max"],
        "win_rate_20": stats.get("win_rate_20", np.nan),
        "win_rate_60": stats.get("win_rate_60", np.nan),
        "median_ret_60": stats.get("median_ret_60", np.nan),
        "extreme_sample": ex_n,
        "extreme_win_rate_60": stats_x["win_rate_60"],
        "extreme_median_ret_60": stats_x.get("median_ret_60", np.nan),
        "extreme_pvalue": _binom_p_one_sided(ex_wins, ex_n) if ex_n else float("nan"),
        "valid": True,
    }


def diagnose_fear_greed(store, index_sym: str = "000001") -> dict:
    """⑨ 恐惧贪婪指数(只读诊断旁路):5 成分 → 0-100 复合 + 五档标签 + 各成分末值 + 复合历史。

    温度计不是开关(同 ⑧ 实证无择时 edge);永不喂引擎。index_sym 默认上证综指(动量/波动成分基准,
    与 ⑦ 相对周期律同)。估值成分用全市场 PB 中位(market_pb.pb,与 ⑥ 市场温度同口径);杠杆成分仅沪市
    (深市总量历史 akshare 不可得,见 fetcher.fetch_market_margin)。
    返回 {score, label, date, score_series, components, valid}。"""
    from . import fear_greed as fg
    close_df = store.get_index_daily_series(index_sym)
    close = close_df["close"] if len(close_df) else pd.Series(dtype=float)
    turn_df = store.get_market_turnover_series()
    turnover = turn_df["total"] if len(turn_df) else pd.Series(dtype=float)
    pb_df = store.get_market_pb_series()
    pb = pb_df["pb"] if len(pb_df) and "pb" in pb_df.columns else pd.Series(dtype=float)
    marg_df = store.get_market_margin_series()
    financing = marg_df["financing_sse"] if len(marg_df) else pd.Series(dtype=float)

    comp_series = {
        "momentum": fg.momentum_component(close),
        "turnover": fg.turnover_component(turnover),
        "volatility": fg.volatility_component(close),
        "valuation": fg.valuation_component(pb),
        "leverage": fg.leverage_component(financing),
    }
    score_series = fg.fear_greed_series(comp_series)
    last = (float(score_series.iloc[-1]) if len(score_series)
            and not np.isnan(score_series.iloc[-1]) else float("nan"))
    last_date = str(score_series.index[-1]) if len(score_series) else None
    comps = {k: (float(v.iloc[-1]) if len(v) and not np.isnan(v.iloc[-1]) else float("nan"))
             for k, v in comp_series.items()}
    return {
        "index": index_sym,
        "score": last,
        "label": fg.classify(last),
        "date": last_date,
        "score_series": score_series,     # 完整历史(看板时序图)
        "components": comps,
        "valid": not np.isnan(last),
    }


def diagnose_money_conditions(store) -> dict:
    """⑪ 货币条件(只读诊断旁路): M2/M1 同比 + M1−M2 剪刀差 + 社融脉冲 + episode 状态机。
    「M2 定大盘」主流叙事的观测层落地——温度计非开关,永不喂引擎;实证结论由
    validate_m2_timing 写 meta、dashboard 活注入读图说明。"""
    from . import money_conditions as mcm
    df = store.get_china_money_series()
    if len(df) < mcm.MIN_MONTHS:
        return {"valid": False, "months": len(df)}
    m2 = pd.to_numeric(df["m2_yoy"], errors="coerce")
    m1 = pd.to_numeric(df["m1_yoy"], errors="coerce")
    tsf = store.get_china_tsf_series()
    pulse = (mcm.tsf_pulse_series(tsf["tsf_inc"], df["m2_amt"])
             if len(tsf) and "tsf_inc" in tsf.columns else pd.Series(dtype=float))
    state = mcm.episode_state(m2)

    def _last(s: pd.Series) -> float:
        return float(s.iloc[-1]) if len(s) and not pd.isna(s.iloc[-1]) else float("nan")

    sc = mcm.scissor_series(m1, m2)
    return {
        "valid": True,
        "months": len(df),
        "month_last": str(df.index[-1]),
        "m2_yoy": _last(m2),
        "m1_yoy": _last(m1),
        "m2_series": m2.dropna(),
        "m1_series": m1.dropna(),
        "scissor_series": sc,
        "pulse_series": pulse,
        "events": mcm.m2_episode_events(m2),
        "state": state,
        "state_label": mcm.state_label(state),
        "tsf_last": str(tsf.index[-1]) if len(tsf) else None,
    }


def diagnose_layer(store, period: int = ti.MA_PERIOD,
                   lookback: int | None = None) -> dict:
    """顶层:整个指数择时层诊断(给 dashboard)。

    返回 {indices: {symbol: {name, close_last, date_last, diagnosis, valid}},
          valuation, market_temp, style, relative_cycle, turnover, fear_greed, money, period}."""
    indices = {}
    for sym, nm in BROAD_INDICES:
        df = store.get_index_daily_series(sym)
        if len(df) < period:
            indices[sym] = {"name": nm, "valid": False, "reason": "数据不足"}
            continue
        close = df["close"]
        indices[sym] = {
            "name": nm,
            "close_last": float(close.iloc[-1]),
            "date_last": str(close.index[-1]),
            "diagnosis": diagnose_index(close, period, lookback),
            "valid": True,
        }
    return {
        "indices": indices,
        "valuation": diagnose_valuation(store),
        "market_temp": diagnose_market_temp(store),
        "style": diagnose_style(store, period),
        "relative_cycle": diagnose_relative_cycle(store),
        "turnover": diagnose_turnover(store),
        "fear_greed": diagnose_fear_greed(store),
        "money": diagnose_money_conditions(store),
        "period": period,
    }
