"""品种面板数据组装(第八看板 · 纯读 store,渲染无关)。

主语规则(2026-09 用户拍板):有国际通用基准的品种以国际价为主语(国际价格波动一般传导至国内,
研究看传导方向),国内价作对照(=大A投资指导口径);无基准品种(黑色/建材/化肥/新能源金属/生猪)
标「国内定价」。判定四态用 tracker.stock_figures.judge_commodity(统一口径,与个股看板/leading 同源)。
"""
from __future__ import annotations

import datetime as _dt

import pandas as pd

from ..config import get_config
from ..data import fetcher
from ..tracker import stock_figures as sf
from ..tracker.stock_figures import judge_commodity


def series_stats(s: pd.Series) -> dict:
    """单序列面板统计(纯):现价/日期/同比/近20日/近60日/偏离分位/60日新高新低标记。空/不足 → {}。"""
    if s is None or len(s) < 2:
        return {}
    s = s.astype(float).dropna()
    if len(s) < 2:
        return {}

    def _chg(k: int) -> float:
        n = min(k, len(s) - 1)
        return float(s.iloc[-1]) / float(s.iloc[-1 - n]) - 1.0

    yoy, m60, m20 = _chg(252), _chg(60), _chg(20)
    dst = sf.commodity_dev_stats(s) or {}
    win60 = s.iloc[-min(60, len(s)):]
    mark = "🔺" if float(win60.iloc[-1]) >= float(win60.max()) \
        else ("🔻" if float(win60.iloc[-1]) <= float(win60.min()) else "")
    return {"cur": float(s.iloc[-1]), "date": str(s.index[-1]),
            "yoy": yoy, "m20": m20, "m60": m60,
            "pct": dst.get("pct"), "mark": mark,
            # 60日偏离度本体 + 历史极值排名(第几高/第几低,与🚦慢腿/放大视图标注同源;
            # dst={} 时三者为 None → 面板该两列诚实显示 —)
            "dev": dst.get("cur"),
            "rank_high": dst.get("rank_high"), "rank_low": dst.get("rank_low")}


def intl_series(store, symbol: str):
    """外盘基准 close 序列(western_macro_series source='fut')。无数据 → None。"""
    if not hasattr(store, "get_western_series"):
        return None
    try:
        df = store.get_western_series("fut", symbol)
    except Exception:  # noqa: BLE001 — 表缺失/空,静默降级
        return None
    if df is None or len(df) == 0 or "close" not in df.columns:
        return None
    s = df["close"].astype(float).dropna()
    return s if len(s) >= 2 else None


def spot_map(store, valid_days: int = 2) -> tuple[dict, str]:
    """夜盘快照 {variety: price}(超过 valid_days 天视为失效) + 快照日。无 → ({}, '')。"""
    if not hasattr(store, "get_commodity_spot"):
        return {}, ""
    try:
        sdf = store.get_commodity_spot()
    except Exception:  # noqa: BLE001 — 快照表缺失/空,静默降级
        return {}, ""
    if not len(sdf):
        return {}, ""
    spot_date = str(sdf["date"].max())
    if (_dt.date.today() - _dt.date.fromisoformat(spot_date)).days > valid_days:
        return {}, spot_date
    return dict(zip(sdf["variety"], sdf["price"].astype(float))), spot_date


def panel_rows(store, config=None) -> list[dict]:
    """17 品种面板行(展示序=fetcher.COMMODITY_CODES)。每行:
    primary(主语统计:intl 有则 intl,否则 dom)/dom(国内对照)/judge(主语口径四态)/
    overnight(国内夜盘快照 vs 国内日收盘)/spread60(国际−国内 近60日涨幅差,pp 语义)。"""
    cfg = config or get_config()
    cp = (cfg.params.get("commodity") or {}).get("panel") or {}
    valid_days = int(cp.get("overnight_spot_days", 2))
    smap, spot_date = spot_map(store, valid_days)

    rows: list[dict] = []
    for v in fetcher.COMMODITY_CODES:
        dom = series_stats(store.get_commodity_series(v))
        bench = fetcher.COMMODITY_BENCHMARKS.get(v)
        intl = series_stats(intl_series(store, bench[0])) if bench else {}
        primary = intl or dom
        if not primary:
            continue                      # 两边都无数据,跳过(诚实缺省)
        overnight = None
        if v in smap and dom.get("cur"):
            overnight = smap[v] / dom["cur"] - 1.0
        spread60 = None
        if intl and dom:
            spread60 = intl["m60"] - dom["m60"]
        rows.append({
            "variety": v, "has_bench": bool(bench),
            "intl_name": bench[1] if bench else "", "intl_unit": bench[2] if bench else "",
            "intl": intl, "dom": dom, "primary": primary,
            "judge": judge_commodity(primary["yoy"], primary["m60"]),
            "overnight": overnight, "spot_date": spot_date, "spread60": spread60,
        })
    return rows


def primary_series(store, variety: str):
    """某品种的主语序列(intl 有则外盘,否则国内)——时序图 section 用,与面板口径一致。"""
    bench = fetcher.COMMODITY_BENCHMARKS.get(variety)
    if bench:
        s = intl_series(store, bench[0])
        if s is not None:
            return s, f"{variety}·{bench[1]}", bench[2]
    return store.get_commodity_series(variety), variety, ""


def breadth_inputs(store) -> dict:
    """广度计算输入 {variety: 国内序列}——广度看 A股可交易口径,一律国内价。"""
    return {v: store.get_commodity_series(v) for v in fetcher.COMMODITY_CODES}
