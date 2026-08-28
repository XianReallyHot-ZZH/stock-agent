"""ETF earnings-expectation signal — aggregate 业绩预告 over the ETF's own holdings.

Pure functions (no I/O): take a holdings DataFrame + a 业绩预告 forecast DataFrame, return an
aggregated signal dict and a 0-100 score + label. This is the forward-looking layer the
three-factor model (估值/筹码/趋势) lacks — it separates value traps (cheap + earnings falling)
from justified valuations (expensive + earnings exploding).

Phase 1: INFORMATIONAL ONLY — the score is displayed but does NOT enter the 性价比 composite.

Data shape contract:
  holdings  : DataFrame[code(str), weight(float, % of NAV)] (+ optional name/period cols)
  forecast  : DataFrame indexed by code(str), columns [yoy(float %), type(str 预告类型)]
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

# 业绩预告 type enum (11 values) → bullish / bearish buckets. '不确定' lands in neither.
BULL = {"预增", "略增", "扭亏", "续盈", "减亏"}
BEAR = {"预减", "略减", "首亏", "续亏", "增亏"}

# Score → label bands (informational; not in composite).
LABEL_HIGH = "业绩高增"
LABEL_UP = "业绩改善"
LABEL_FLAT = "业绩平稳"
LABEL_DOWN = "业绩承压"
LABEL_CRASH = "业绩恶化"
LABEL_INSUFF = "数据不足"

# YYYYMMDD report-period suffix → Chinese 业绩预告 window name.
_PERIOD_NAME = {"1231": "年报预告", "0331": "一季报预告",
                "0630": "中报预告", "0930": "三季报预告"}
# 去预告后缀版（三环混合头条口径——数据未必来自预告环）.
_PERIOD_SHORT = {"1231": "年报", "0331": "一季报", "0630": "中报", "0930": "三季报"}


def period_label(period: Optional[str], short: bool = False) -> str:
    """Map a YYYYMMDD report_period to a Chinese label, e.g. '20260630' → '2026中报预告'.

    Used to surface which disclosure window the earnings signal draws from (freshness). Unknown /
    malformed periods fall back to 'YYYY报告期(MMDD)' so the dashboard never shows a blank.
    short=True → 去预告后缀（'2026中报'）——三环混合头条的数据未必来自预告环，期名不带环假设.
    """
    if not period or not isinstance(period, str) or len(period) != 8 or not period.isdigit():
        return "—"
    y, tail = period[:4], period[4:]
    names = _PERIOD_SHORT if short else _PERIOD_NAME
    return f"{y}{names[tail]}" if tail in names else f"{y}报告期({tail})"


def latest_report_period(now: datetime) -> str:
    """Most recent 业绩预告 report period (YYYYMMDD) whose disclosure window is open at `now`.

    A-share 业绩预告 disclosure cutoffs: 年报 1/31, 一季报 4/15, 半年报 7/15, 三季报 10/15.
    We switch to each period as its window OPENS (not at the cutoff), so the dashboard surfaces the
    live disclosure season even before it's 100% complete — the per-ETF coverage then flags how far
    along it is. Window-open dates:
        年报 YYYY1231  → next year, from 1/1
        一季报 YYYY0331 → from 4/15
        半年报 YYYY0630 → from 7/1   (B 方案: 中报窗口即纳入, 不等 7/15 截止)
        三季报 YYYY0930 → from 10/1
    Pure (no I/O); DataManager._latest_report_period delegates here with datetime.now().
    """
    y, md = now.year, (now.month, now.day)
    if md >= (10, 1):     return f"{y}0930"   # 三季报预告窗口
    if md >= (7, 1):      return f"{y}0630"   # 半年报预告窗口(7/15 截止, 高峰即纳入)
    if md >= (4, 15):     return f"{y}0331"   # 一季报预告窗口(4/15~4/30)
    return f"{y - 1}1231"                      # 年报预告窗口(1/31 截止)


# 披露窗口时钟（预告×偏离交叉横幅的季节门控）：预告数据季度一跳，窗口关闭后广度不再
# 变化，条目钉在顶部只会壁纸化 → 只在 [开窗日, 截止日+grace] 内出交叉条目。
# 开窗日与 latest_report_period 的窗口切换日一致；grace 覆盖截止后迟到披露+数据管道。
_DISCLOSURE_WINDOWS = {          # period_tail → ((开窗月,日), (截止月,日)); 年报窗在次年1月
    "1231": ((1, 1), (1, 31)),   # 年报预告: 1/1 开窗 → 1/31 强制披露截止
    "0331": ((4, 15), (4, 30)),  # 一季报预告: 4/15 开窗(截止日) → 4/30 窗口收
    "0630": ((7, 1), (7, 15)),   # 中报预告: 7/1 开窗 → 7/15 截止
    "0930": ((10, 1), (10, 15)), # 三季报预告: 10/1 开窗 → 10/15 截止
}


def disclosure_window(now: datetime, grace_days: int = 14) -> dict:
    """业绩预告披露窗口时钟（pure, no I/O）。

    Returns {period, label, open, window_note, next_label, next_open}:
      open=True  → 正处窗口内；period/label = 该窗口，window_note = "7/1开窗·7/15截止"
      open=False → period/label = 刚关闭的窗口，next_* = 下一个开窗（"三季报预告", "10/1"）
    边界：开窗日当天即 open；截止+grace 当天仍 open（闭区间）；次日关闭。全年无缝覆盖
    （1-2月属上年年报窗的尾部/刚关闭态）。
    """
    d = now.date() if isinstance(now, datetime) else now
    y = d.year
    cands = [f"{y}{tail}" for tail in _DISCLOSURE_WINDOWS] + [f"{y - 1}1231"]
    wins = [(p, *window_dates(p, grace_days)) for p in cands]
    nxt_o = min((o for _p, o, _c in wins if o > d), default=None)
    nxt = {"next_label": "", "next_open": ""}
    if nxt_o is not None:
        nxt_p = next(p for p, o, _c in wins if o == nxt_o)
        nxt = {"next_label": period_label(nxt_p), "next_open": f"{nxt_o.month}/{nxt_o.day}"}
    for period, o, c in wins:
        if o <= d <= c:
            return {"period": period, "label": period_label(period), "open": True,
                    "window_note": _window_note(o, c, grace_days), **nxt}
    # 全窗口皆不含今天 → 关闭态：报刚过的窗口 + 下一个开窗
    closed = [w for w in wins if w[2] < d]
    if closed:
        period, o, c = max(closed, key=lambda w: w[2])   # 关闭最晚的 = 刚过的窗口
        return {"period": period, "label": period_label(period), "open": False,
                "window_note": _window_note(o, c, grace_days), **nxt}
    return {"period": "", "label": "", "open": False, "window_note": "", **nxt}


def window_dates(period: str, grace_days: int = 14) -> tuple[date, date]:
    """某报告期预告窗口的 [开窗日, 截止+grace]（pure）。年报(1231)窗在次年 1 月；
    供历史回放按窗口裁日历（台账只统计窗口内命中）。未知/畸形期 → ValueError。"""
    if not isinstance(period, str) or len(period) != 8 or not period.isdigit():
        raise ValueError(f"bad report_period {period!r}")
    y, tail = int(period[:4]), period[4:]
    if tail not in _DISCLOSURE_WINDOWS:
        raise ValueError(f"unknown period tail {tail!r}")
    (om, od), (cm, cd) = _DISCLOSURE_WINDOWS[tail]
    wy = y + 1 if tail == "1231" else y
    return date(wy, om, od), date(wy, cm, cd) + timedelta(days=grace_days)


def _window_note(o: date, c: date, grace_days: int) -> str:
    cut = c - timedelta(days=grace_days)
    return f"{o.month}/{o.day}开窗·{cut.month}/{cut.day}截止"


def _empty_signal(n_holdings: int) -> dict:
    return {
        "weighted_yoy": float("nan"), "median_yoy": float("nan"),
        "bull_ratio": float("nan"), "bear_ratio": float("nan"),
        "coverage": 0.0, "n_holdings": int(n_holdings), "n_matched": 0,
    }


def aggregate_earnings(holdings: Optional[pd.DataFrame], forecast: Optional[pd.DataFrame]) -> dict:
    """Join an ETF's holdings to the 业绩预告 forecast and aggregate.

    Returns {weighted_yoy, median_yoy, bull_ratio, bear_ratio, coverage, n_holdings, n_matched}.
      weighted_yoy = Σ(yoy·weight)/Σ(weight) over matched (yoy-usable) holdings
      median_yoy   = median yoy over matched (robust to ±200% outliers)
      bull/bear_ratio = weight of bullish/bearish types ÷ matched weight
      coverage     = matched weight ÷ total holdings weight (data-completeness)
    A holding counts as matched only if its code is in the forecast WITH a usable yoy.
    """
    if holdings is None or forecast is None or len(holdings) == 0 or len(forecast) == 0:
        return _empty_signal(0 if holdings is None else len(holdings))

    h = holdings[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return _empty_signal(len(h))

    fc = forecast[["yoy", "type"]].copy()
    fc.index = fc.index.astype(str)
    m = h.merge(fc, left_on="code", right_index=True, how="left")
    m["yoy"] = pd.to_numeric(m["yoy"], errors="coerce")
    matched = m.dropna(subset=["yoy"])
    matched_w = float(matched["weight"].sum())
    if matched_w <= 0 or len(matched) == 0:
        return _empty_signal(len(h))

    yoy = matched["yoy"].astype(float)
    bull_w = float(matched.loc[matched["type"].isin(BULL), "weight"].sum())
    bear_w = float(matched.loc[matched["type"].isin(BEAR), "weight"].sum())
    return {
        "weighted_yoy": float((yoy * matched["weight"]).sum() / matched_w),
        "median_yoy": float(yoy.median()),
        "bull_ratio": bull_w / matched_w,
        "bear_ratio": bear_w / matched_w,
        "coverage": matched_w / total_w,
        "n_holdings": int(len(h)),
        "n_matched": int(len(matched)),
    }


def _label(score: float) -> str:
    if score >= 70:
        return LABEL_HIGH
    if score >= 58:
        return LABEL_UP
    if score >= 42:
        return LABEL_FLAT
    if score >= 30:
        return LABEL_DOWN
    return LABEL_CRASH


def earnings_score(signal: Optional[dict], params: dict) -> tuple[float, str]:
    """Map an aggregate_earnings signal to a (score 0-100, label).

    score = clamp(50 + median_yoy·0.4 + (bull_ratio−bear_ratio)·30). Uses median_yoy (robust to
    extreme single-name YoY like 猪周期 −236%) and the bull−bear breadth as the stable headline.
    Returns (NaN, '数据不足') when coverage < research.earnings.min_coverage (default 0.30) or
    matched names < min_matched (default 5).
    """
    if signal is None:
        return (float("nan"), LABEL_INSUFF)
    rp = (params.get("research", {}) or {}).get("earnings", {}) or {}
    min_cov = float(rp.get("min_coverage", 0.30))
    min_n = int(rp.get("min_matched", 5))
    cov = signal.get("coverage")
    n = int(signal.get("n_matched", 0))
    if cov is None or cov < min_cov or n < min_n:
        return (float("nan"), LABEL_INSUFF)

    med = signal.get("median_yoy")
    med = 0.0 if (med is None or pd.isna(med)) else float(med)
    br = signal.get("bull_ratio")
    br = 0.0 if (br is None or pd.isna(br)) else float(br)
    be = signal.get("bear_ratio")
    be = 0.0 if (be is None or pd.isna(be)) else float(be)
    score = max(0.0, min(100.0, 50.0 + med * 0.4 + (br - be) * 30.0))
    return (score, _label(score))


# ---------------- 一致预期聚合 (E2, docs/EXECUTION_PLAN-ETF业绩预期.md §4) ----------------
# 与预告层互补: 预告=已披露的区间事实(样本偏极端, 只看广度), 一致预期=分析师前瞻(日更软信息,
# 覆盖权重中位数≈82% vs 预告期≈32%, 调研§5)。均为 INFORMATIONAL, 永不喂引擎。

LABEL_C_HIGH = "预期高增"
LABEL_C_UP = "预期改善"
LABEL_C_FLAT = "预期平稳"
LABEL_C_DOWN = "预期承压"
LABEL_C_CRASH = "预期负增"

_RATE_COLS = ("rating_buy", "rating_over", "rating_neutral", "rating_reduce", "rating_sell")


def _empty_consensus(n_all: int) -> dict:
    return {"weighted_g": float("nan"), "median_g": float("nan"), "coverage": 0.0,
            "n_names": 0, "n_all": int(n_all), "buy_ratio": float("nan"),
            "fy1_year": None, "fy2_year": None}


def aggregate_consensus(constituents: Optional[pd.DataFrame],
                        snapshot: Optional[pd.DataFrame],
                        min_reports: int = 3) -> dict:
    """Join 指数成分(官方权重) × 一致预期快照 → 聚合信号 dict (pure, no I/O).

    Data shape contract (both from the E0/E1 data layer):
      constituents : DataFrame[code(str), weight(float)]  — store.get_constituents()
      snapshot     : DataFrame indexed by code with [n_reports, rating_*, eps_fy1, eps_fy2,
                    fy1_year, fy2_year] — store.get_consensus_snapshot()[1]
    Returns {weighted_g, median_g, coverage, n_names, n_all, buy_ratio, fy1_year, fy2_year}:
      g_i = eps_fy2/eps_fy1 − 1 (财年滚动对齐由快照列保证, 年末翻滚自动跟上)
      usable = n_reports ≥ min_reports 且 eps_fy1 > 0 且两年 EPS 齐 — 研报数门挡覆盖偏差
               (调研§3.5), 负/零基数剔除 (EPS 比值在亏损基数上无意义)
      weighted_g = Σ(w·g)/Σw over usable · median_g = median(g) (抗单家极值预测, 调研§4.3)
      coverage = usable weight / total constituents weight
      buy_ratio = Σ(w·buy)/Σ(w·评级总数) over usable — 评级结构参考, 只展示不进分
    """
    if (constituents is None or snapshot is None
            or not len(constituents) or not len(snapshot)):
        return _empty_consensus(0 if constituents is None else len(constituents))

    h = constituents[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return _empty_consensus(len(h))

    m = h.merge(snapshot.reset_index(), on="code", how="left")
    usable = m[(m["n_reports"] >= min_reports) & m["eps_fy1"].notna() & (m["eps_fy1"] > 0)
               & m["eps_fy2"].notna()].copy()
    if not len(usable):
        return _empty_consensus(len(h))

    usable["g"] = usable["eps_fy2"] / usable["eps_fy1"] - 1.0
    uw = float(usable["weight"].sum())

    rc = [c for c in _RATE_COLS if c in usable.columns]
    buy_ratio = float("nan")
    if rc:
        rated = usable.copy()
        rated["_rtot"] = rated[rc].sum(axis=1)
        rated = rated[rated["_rtot"] > 0]
        if len(rated):
            rtot = float((rated["weight"] * rated["_rtot"]).sum())
            buy = float((rated["weight"] * rated["rating_buy"]).sum())
            buy_ratio = buy / rtot if rtot > 0 else float("nan")

    fy1 = usable["fy1_year"].iloc[0] if "fy1_year" in usable.columns else None
    fy2 = usable["fy2_year"].iloc[0] if "fy2_year" in usable.columns else None
    return {
        "weighted_g": float((usable["g"] * usable["weight"]).sum() / uw),
        "median_g": float(usable["g"].median()),
        "coverage": uw / total_w,
        "n_names": int(len(usable)),
        "n_all": int(len(h)),
        "buy_ratio": buy_ratio,
        "fy1_year": int(fy1) if fy1 == fy1 and fy1 is not None else None,
        "fy2_year": int(fy2) if fy2 == fy2 and fy2 is not None else None,
    }


def _clabel(score: float) -> str:
    if score >= 70:
        return LABEL_C_HIGH
    if score >= 58:
        return LABEL_C_UP
    if score >= 42:
        return LABEL_C_FLAT
    if score >= 30:
        return LABEL_C_DOWN
    return LABEL_C_CRASH


def consensus_score(signal: Optional[dict], params: dict) -> tuple[float, str]:
    """Map an aggregate_consensus signal to (score 0-100, label).

    score = clamp(50 + median_g%·0.4) — 与 earnings_score 同标度同带宽(水平值口径, 非变化量;
    修正动量是 E4 的独立信号). Gates: coverage < research.earnings.consensus.min_weight_cov
    (default 0.40, 调研§5 的 B 级线) 或可用成分 < min_names (default 5) → (NaN, '数据不足').
    """
    if signal is None:
        return (float("nan"), LABEL_INSUFF)
    cp = ((params.get("research", {}) or {}).get("earnings", {}) or {}).get("consensus", {}) or {}
    min_cov = float(cp.get("min_weight_cov", 0.40))
    min_n = int(cp.get("min_names", 5))
    cov = signal.get("coverage")
    n = int(signal.get("n_names", 0))
    if cov is None or cov < min_cov or n < min_n:
        return (float("nan"), LABEL_INSUFF)

    med = signal.get("median_g")
    med = 0.0 if (med is None or pd.isna(med)) else float(med)
    score = max(0.0, min(100.0, 50.0 + med * 100.0 * 0.4))
    return (score, _clabel(score))


# ---------------- 三环时效链 (E3, docs/EXECUTION_PLAN-ETF业绩预期.md §5) ----------------
# 同一报告期的三次逐步精化: 预告(区间·事前) → 快报(未审计近似) → 正式报(审计·滞后45天-4个月)。
# 时效与确定性互为代价(调研§1.2); 环可重叠(一个名字可能三环全有)。

_RING_NAMES = ("forecast", "express", "actual")
_RING_CN = {"forecast": "预告", "express": "快报", "actual": "正式报"}


def _empty_chain() -> dict:
    return {"rings": {}, "n": {}, "latest": {}, "days": {},
            "bull_ratio": float("nan"), "bear_ratio": float("nan"),
            "express_check": {"conservative": float("nan"), "hit": float("nan"),
                              "optimistic": float("nan")}}


def earnings_chain(constituents: Optional[pd.DataFrame],
                   forecast: Optional[pd.DataFrame],
                   express: Optional[pd.DataFrame],
                   actual: Optional[pd.DataFrame],
                   asof=None) -> dict:
    """三环时效链聚合（pure, no I/O）— ETF 成分权重口径.

    Data contracts: constituents DataFrame[code(str), weight]; forecast indexed by code
    [yoy, type, announce_date]; express/actual indexed by code [np_yoy, rev_yoy, announce_date]
    (store.get_stock_forecast_period / get_stock_express_period / get_stock_report_period).
    Returns:
      rings  {forecast, express, actual} 各环覆盖权重(占成分总权重; 可重叠)
      n      各环命中成分数
      latest 各环最新公告日('YYYY-MM-DD' 或 None) — 披露进行到哪的时钟
      days   距 asof(datetime) 天数; asof=None → None
      bull_ratio/bear_ratio 预告类型广度(环内权重口径, 复用 BULL/BEAR)
      express_check {conservative, hit, optimistic} 快报 np_yoy vs 预告 yoy 的落点
             (±10pp 带宽; 仅统计两环皆有值的名字的权重占比; 无样本 → NaN)
    """
    if constituents is None or not len(constituents):
        return _empty_chain()
    h = constituents[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return _empty_chain()

    out = _empty_chain()
    merged = {}
    for ring, df in zip(_RING_NAMES, (forecast, express, actual)):
        if df is None or not len(df):
            out["rings"][ring], out["n"][ring] = 0.0, 0
            out["latest"][ring], out["days"][ring] = None, None
            continue
        if not df.index.name:
            df = df.rename_axis("code")                    # 无名 index 归一(merge 契约)
        m = h.merge(df.reset_index(), on="code", how="inner")
        merged[ring] = m
        out["rings"][ring] = float(m["weight"].sum() / total_w)
        out["n"][ring] = int(len(m))
        dates = pd.to_datetime(m.get("announce_date"), errors="coerce").dropna()
        if len(dates):
            latest = dates.max()
            out["latest"][ring] = latest.strftime("%Y-%m-%d")
            out["days"][ring] = ((pd.Timestamp(asof) - latest).days
                                 if asof is not None else None)
        else:
            out["latest"][ring], out["days"][ring] = None, None

    # 预告广度: 环内 BULL/BEAR 类型权重占比(强制门槛使样本偏极端 → 只看广度, 调研§3.1)
    fc = merged.get("forecast")
    if fc is not None and len(fc):
        w = float(fc["weight"].sum())
        if w > 0:
            out["bull_ratio"] = float(fc.loc[fc["type"].isin(BULL), "weight"].sum() / w)
            out["bear_ratio"] = float(fc.loc[fc["type"].isin(BEAR), "weight"].sum() / w)

    # 快报落点 vs 预告(±10pp): 实际好于预告=预告保守 / 命中 / 差于预告=预告激进(落空)
    ex, pairs = merged.get("express"), None
    if fc is not None and len(fc) and ex is not None and len(ex):
        pairs = fc[["code", "weight", "yoy"]].merge(
            ex[["code", "np_yoy"]], on="code", how="inner")
        pairs = pairs[pairs["yoy"].notna() & pairs["np_yoy"].notna()]
    if pairs is not None and len(pairs):
        w = float(pairs["weight"].sum())
        if w > 0:
            d = pairs["np_yoy"] - pairs["yoy"]
            out["express_check"] = {
                "conservative": float(pairs.loc[d > 10, "weight"].sum() / w),
                "hit": float(pairs.loc[d.abs() <= 10, "weight"].sum() / w),
                "optimistic": float(pairs.loc[d < -10, "weight"].sum() / w),
            }
    return out


def best_ring_earnings(constituents: Optional[pd.DataFrame],
                       forecast: Optional[pd.DataFrame],
                       express: Optional[pd.DataFrame],
                       actual: Optional[pd.DataFrame]) -> dict:
    """三环逐名字取最精化环的头条聚合（live 口径, pure, no I/O）.

    aggregate_earnings 是纯预告口径（强制披露门槛样本偏极端，预告窗一关就冻结）；本函数把
    E3 的「三环逐步精化」延伸到头条聚合——每个成分名字取已披露的最精化环:
    正式报 np_yoy > 快报 np_yoy > 预告 yoy; 某环值 NaN = 未披露 → 落次精化环
    （store 契约: 快报/正式报行可只带 rev_yoy, np_yoy 为 NULL）。
    广度: 预告环名字按 type∈BULL/BEAR（「减亏」负 yoy 仍计多——类型桶语义，样本偏极端所以
    只看广度）; 快报/正式环名字按 YoY 符号（>0 多 / <0 空 / =0 不计）。
    单调性: 同 constituents 下 已匹配集（权重·家数）= 三环并集 ⊇ 纯预告环 → 过
    earnings_score 覆盖门者 ⊇ 纯预告口径 → 渲染端「mixed 失效回退 earnings_*」永不降级。
    Data contracts: constituents DataFrame[code(str), weight]（code 唯一）; forecast indexed
    by code [yoy, type]; express/actual indexed by code [np_yoy, ...]（get_stock_*_period）。
    Returns: aggregate_earnings 同形 dict + ring_mix{ring: 已匹配权重内占比（互斥分割）}
             + n_ring{ring: 家数}。
    """
    out = _empty_signal(0 if constituents is None else len(constituents))
    out["ring_mix"] = {r: 0.0 for r in _RING_NAMES}
    out["n_ring"] = {r: 0 for r in _RING_NAMES}
    if constituents is None or not len(constituents):
        return out

    h = constituents[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str).str.zfill(6)          # 与 earnings_chain 同防御
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return out
    m = h.set_index("code")

    # 逐环取值列 reindex 到成分 code（缺失环 = 全 NaN 列 = 该环无人披露）
    v = {}
    for ring, df, col in (("forecast", forecast, "yoy"),
                          ("express", express, "np_yoy"),
                          ("actual", actual, "np_yoy")):
        if df is None or not len(df) or col not in df.columns:
            v[ring] = pd.Series(dtype=float, index=m.index)
        else:
            s = pd.to_numeric(df[col], errors="coerce")
            s.index = s.index.astype(str).str.zfill(6)
            v[ring] = s.reindex(m.index)
    if forecast is not None and len(forecast) and "type" in forecast.columns:
        t = forecast["type"].astype(str)
        t.index = t.index.astype(str).str.zfill(6)
        m["type"] = t.reindex(m.index).fillna("")
    else:
        m["type"] = ""

    # 低→高精化依次覆写 best_ring（高环可用才覆盖）——与 best_yoy 的 combine_first 序一致
    m["best_ring"] = ""
    for ring in _RING_NAMES:                               # ("forecast", "express", "actual")
        m.loc[v[ring].notna(), "best_ring"] = ring
    m["best_yoy"] = v["actual"].combine_first(v["express"]).combine_first(v["forecast"])

    matched = m[m["best_yoy"].notna()]
    matched_w = float(matched["weight"].sum())
    if matched_w <= 0 or not len(matched):
        return out
    yoy = matched["best_yoy"].astype(float)
    is_fc = matched["best_ring"] == "forecast"
    bull_w = (float(matched.loc[is_fc & matched["type"].isin(BULL), "weight"].sum())
              + float(matched.loc[~is_fc & (yoy > 0), "weight"].sum()))
    bear_w = (float(matched.loc[is_fc & matched["type"].isin(BEAR), "weight"].sum())
              + float(matched.loc[~is_fc & (yoy < 0), "weight"].sum()))
    return {
        "weighted_yoy": float((yoy * matched["weight"]).sum() / matched_w),
        "median_yoy": float(yoy.median()),
        "bull_ratio": bull_w / matched_w,
        "bear_ratio": bear_w / matched_w,
        "coverage": matched_w / total_w,
        "n_holdings": int(len(h)),
        "n_matched": int(len(matched)),
        "ring_mix": {r: float(matched.loc[matched["best_ring"] == r, "weight"].sum()) / matched_w
                     for r in _RING_NAMES},
        "n_ring": {r: int((matched["best_ring"] == r).sum()) for r in _RING_NAMES},
    }


# ---------------- 修正动量 (E4, docs/EXECUTION_PLAN-ETF业绩预期.md §6) ----------------
# 一致预期的「变化」比「水平」更有信息量(调研§2.1.3: 变化有效水平无效)。东财免费口径无修正
# 历史 → 靠自建周度整表快照差分。冷启动 4 周(min_snapshots), 激活前看板诚实显示「累积中」。

def _empty_revision() -> dict:
    return {"weighted_rev": float("nan"), "n_up": 0, "n_dn": 0,
            "coverage": 0.0, "n_names": 0}


def revision_momentum(snap_now: Optional[pd.DataFrame],
                      snap_then: Optional[pd.DataFrame],
                      constituents: Optional[pd.DataFrame],
                      min_reports: int = 3) -> dict:
    """两份周度快照的同财年 forward-EPS 差分 → 修正动量（pure, no I/O）.

    年末翻滚防护: 两份快照的 fy1_year 不一致 → 空信号——否则会把「2026列 vs 2027列」的
    差当修正(假信号; 12月-1月切换期差分诚实不可算, 等新财年攒满窗口).

    Data contracts: snap_now/snap_then = stock_consensus 快照(indexed by code,
    [n_reports, eps_fy1, fy1_year, ...]); constituents DataFrame[code, weight].
    Returns {weighted_rev, n_up, n_dn, coverage, n_names}:
      weighted_rev = Σ(w·(eps_now/eps_then−1))/Σw over 两期皆有且达标的成分(研报数门+正基数)
      n_up/n_dn    = 上修/下修家数(±1% 带宽外才算, 微动是噪音)
      coverage     = 可差分权重 / 成分总权重
    """
    empty = _empty_revision()
    if (snap_now is None or snap_then is None
            or constituents is None or not len(constituents)
            or not len(snap_now) or not len(snap_then)):
        return empty
    try:
        fy_now = int(snap_now["fy1_year"].iloc[0])
        fy_then = int(snap_then["fy1_year"].iloc[0])
    except (KeyError, IndexError, TypeError, ValueError):
        return empty
    if fy_now != fy_then:
        return empty                                  # 财年翻滚期, 差分无意义

    h = constituents[["code", "weight"]].copy()
    h["code"] = h["code"].astype(str).str.zfill(6)
    h["weight"] = pd.to_numeric(h["weight"], errors="coerce").fillna(0.0)
    total_w = float(h["weight"].sum())
    if total_w <= 0:
        return empty

    a = snap_now[["n_reports", "eps_fy1"]].rename_axis("code").rename(columns={"eps_fy1": "eps_now"})
    b = snap_then[["eps_fy1"]].rename_axis("code").rename(columns={"eps_fy1": "eps_then"})
    m = (h.merge(a.reset_index(), on="code", how="inner")
          .merge(b.reset_index(), on="code", how="inner"))
    m = m[(m["n_reports"] >= min_reports) & m["eps_then"].notna() & (m["eps_then"] > 0)
          & m["eps_now"].notna()]
    if not len(m):
        return empty

    rev = m["eps_now"] / m["eps_then"] - 1.0
    uw = float(m["weight"].sum())
    return {
        "weighted_rev": float((rev * m["weight"]).sum() / uw),
        "n_up": int((rev > 0.01).sum()),
        "n_dn": int((rev < -0.01).sum()),
        "coverage": uw / total_w,
        "n_names": int(len(m)),
    }


# ---------------- 窗口交叉回放（历史台账 · 2026-08） ----------------
# 横幅是"当前态"，台账回答"上一窗口提醒了什么"。与 report.py 横幅同门控（常量归一本处，
# report.py 引用），逐日 point-in-time：预告按 announce_date ≤ 当日 截断（无前视）。
# 边界约定：台账只记事实（命中区间/天数/广度），不带后续涨跌——横幅不做荐股复盘。

CROSS_BEAR_FLOOR = 0.05                 # 交叉风险侧空广度地板（单家小权重预亏=噪音）
CROSS_BULL_LABELS = (LABEL_HIGH, LABEL_UP)   # 交叉机会侧预喜 label（已过 earnings_score 覆盖门）


def cross_hit_spans(nav_map: dict, fc: pd.DataFrame, cons_map: dict, days: list,
                    params: dict, bear_floor: float = CROSS_BEAR_FLOOR,
                    oversold: float = 0.05, overbought: float = 0.95,
                    ma_period: int = 60) -> dict:
    """逐日重放「偏离度极端 × 预告广度」交叉 → 每ETF命中区间（pure, no I/O）。

    Data contracts:
      nav_map  : sym → NAV Series（acc_nav 优先，调用方选好列；index 与 days 同型可比）
      fc       : 一期全市场预告帧 indexed by code [yoy, type, announce_date]
                 （store.get_stock_forecast_period 契约）
      cons_map : sym → constituents DataFrame[code, weight]（可缺/空 → 跳过该 ETF）
      days     : 升序回放日列表（窗口开窗日..截止+grace 由调用方用 window_dates 裁好）
    门控与横幅一致：risk = 分位≥overbought × 空广度≥bear_floor；opp = 分位≤oversold ×
    预喜label（label 非数据不足 = 已过覆盖门 0.30/5只）。分位=deviation_extremes 全历史口径。
    Returns {sym: {"opp": [span..], "risk": [span..]}}，span = {"first","last","n","detail"}
    （连续命中日按 days 相邻合并；detail = 末日广度描述，如 "高增·多97%" / "空8%"）。
    """
    from . import timing as _tm                    # 惰性：earnings 被 DataManager 引，避免模块级耦合
    ad = fc["announce_date"].astype(str).str[:10] if "announce_date" in fc.columns else None
    fc_cols = fc[["yoy", "type"]]
    # 命中日收集：{sym: {side: [(day, detail)..]}}
    raw: dict[str, dict[str, list]] = {}
    for D in days:
        ds = str(D)[:10]
        fc_d = fc_cols[ad.values <= ds] if ad is not None else fc_cols
        for sym, nav in nav_map.items():
            cons = cons_map.get(sym)
            if nav is None or len(nav) < ma_period + 20 or cons is None or not len(cons):
                continue
            ext = _tm.deviation_extremes(nav[nav.index <= D], ma_period)
            if not ext["valid"]:
                continue
            p = ext["pct"]
            sig = aggregate_earnings(cons, fc_d)
            _sc, label = earnings_score(sig, params)
            if label == LABEL_INSUFF:
                continue
            bear = sig["bear_ratio"]
            side = None
            detail = ""
            if p >= overbought and bear == bear and bear >= bear_floor:
                side, detail = "risk", f"空{bear:.0%}"
            elif p <= oversold and label in CROSS_BULL_LABELS:
                side, detail = "opp", f"{label}·多{sig['bull_ratio']:.0%}"
            if side:
                raw.setdefault(sym, {"opp": [], "risk": []})[side].append((ds, detail))
    # 连续日合并成区间（按 days 顺序相邻）
    order = {str(d)[:10]: i for i, d in enumerate(days)}
    out: dict[str, dict[str, list]] = {}
    for sym, sides in raw.items():
        res = {}
        for side, seq in sides.items():
            if not seq:                               # 未命中的另一侧是空表，跳过
                continue
            spans = []
            start = prev = seq[0][0]
            detail = seq[0][1]
            for d, det in seq[1:]:
                if order.get(d, -1) == order.get(prev, -2) + 1:
                    prev, detail = d, det
                else:
                    spans.append({"first": start, "last": prev,
                                  "n": order[prev] - order[start] + 1, "detail": detail})
                    start = prev = d
            spans.append({"first": start, "last": prev,
                          "n": order[prev] - order[start] + 1, "detail": detail})
            res[side] = spans
        if res:
            out[sym] = res
    return out
