"""复权核心(纯)——raw 存储 + 分红表运行时前复权 + 除权嫌疑带。

设计(计划锁定): 观察池 40 只已是 sina_stock_raw 基准,qfq 增量会混锚;除权是**确定性事件**
(stock_dividend 有精确 ex_date+cash+送转股),交易所除权参考价公式直接算因子——比统计断崖
猜测可靠(先例: research/timing.split_adjusted_shares 的「原始持久化+运行时前复权」)。

  dividend_adjusted_close  分红前复权(多次事件累乘,factor 越界跳过+flag——宁可不调不加噪)
  unexplained_cliffs       除权嫌疑带:单日跌 [lo,hi] 且 ±N 日无分红事件(分红表迟到兜底)

S1/S2 一律吃调整后序列;嫌疑行显示 ⚠️徽标、不入 stage-2、周更分红表后自愈;真 −20% 崩盘
(带外)照常触发——深跌本就是要抓的。
"""
from __future__ import annotations

import math

import pandas as pd


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def dividend_adjusted_close(close: pd.Series, dividends: pd.DataFrame | None,
                            sane_lo: float = 0.50) -> tuple[pd.Series, list[dict]]:
    """分红事件前复权(纯)。dividends indexed by ex_date(YYYY-MM-DD str)
    [cash_per_share, stock_div_10, trans_10]——store.get_stock_dividend_series 的输出契约。

    对每个 ex_date d(升序): prev = d 前最后一根 close;
      share_ratio = 1 + (送股/10 + 转增/10)          (NaN→0)
      factor      = (prev − cash) / (prev × share_ratio)   ← 交易所除权参考价公式
      d 之前全部历史 ×= factor(多次事件累乘,前复权到末段口径)
    守卫: factor∉(sane_lo, 1.0] 的事件跳过并 flagged(脏数据宁可不调不加噪);
    ex_date 早于/等于首根 K 线(取不到 prev)同样跳过。dividends=None → 原序列原样返回。
    Returns (adjusted_close, events[{date, factor, flagged}])。
    """
    if close is None or len(close) == 0:
        return close, []
    adj = close.astype(float).copy()
    if dividends is None or len(dividends) == 0:
        return adj, []
    events: list[dict] = []
    first_date = str(adj.index[0])
    for d, row in dividends.sort_index().iterrows():
        ds = str(d)
        if ds <= first_date:
            continue  # 取不到 prev(上市前/首日除权),IPO 后首个交易日已是除权后价
        prev_bars = adj[adj.index < ds]
        if len(prev_bars) == 0:
            continue
        prev = float(prev_bars.iloc[-1])
        cash = 0.0 if _nan(row.get("cash_per_share")) else float(row["cash_per_share"])
        s10 = 0.0 if _nan(row.get("stock_div_10")) else float(row.get("stock_div_10") or 0.0)
        t10 = 0.0 if _nan(row.get("trans_10")) else float(row.get("trans_10") or 0.0)
        share_ratio = 1.0 + (s10 + t10) / 10.0
        if prev <= 0 or share_ratio <= 0:
            events.append({"date": ds, "factor": None, "flagged": True})
            continue
        factor = (prev - cash) / (prev * share_ratio)
        flagged = not (sane_lo < factor <= 1.0 + 1e-9)
        events.append({"date": ds, "factor": float(factor), "flagged": flagged})
        if flagged:
            continue
        adj.loc[adj.index < ds] *= factor
    return adj, events


def dividend_adjusted_open(open_: pd.Series, close: pd.Series,
                           dividends: pd.DataFrame | None) -> pd.Series:
    """分红前复权·open 列(纯)。除权因子与 close 同式(prev close 算,dividend_adjusted_close
    的 events 输出直接复用),ex_date 之前的全部 open ×= factor——open/close 共用一套因子。
    flagged 事件同样跳过(宁可不调不加噪)。dividends=None → 原序列原样返回。
    forecast_industry 事件研究用(入场/出场都在开盘)。"""
    _, events = dividend_adjusted_close(close, dividends)
    out = open_.astype(float).copy()
    for ev in events:
        if ev["flagged"] or ev.get("factor") is None:
            continue
        out.loc[out.index < ev["date"]] *= ev["factor"]
    return out


def unexplained_cliffs(close: pd.Series, dividends: pd.DataFrame | None,
                       lo: float = 0.05, hi: float = 0.12,
                       nearby_days: int = 5) -> list[str]:
    """除权嫌疑日期(纯): **孤立**单日断崖(跌 ∈ [−hi,−lo] 且前一日未在跌) 且 ±nearby_days
    日历日内无分红事件,且该股有分红史。

    带宽校准(2026-08-16 实跑实证): 波动周里 69% 池子近 5 根有孤立 ≥1.5% 断崖——普通行情
    与小额除息同量级,幅度形状分不开;而 ≤3% 的除息对 MA60 偏离分位扰动可忽略(股价 −20%+
    在均线下方,2% 平移不改变触发)。**真正能伪造超卖极值的只有 10转N/巨息**(≥5% 断崖,
    全池仅 ~4.7%)——嫌疑带收 [5%,12%](>12% 的大坑多为真实崩盘,深跌本就是要抓的)。
    三重门: ① 幅度带 ② 孤立性(前一日跌幅 > −1%: 除息是缺口不是滑梯) ③ 分红户(表可能迟到)。
    嫌疑 ≠ 定罪——看板 ⚠️ 徽标、剔除 stage-2,周更自愈。
    """
    if close is None or len(close) < 3:
        return []
    c = close.astype(float)
    ret = c / c.shift(1) - 1.0
    ex_dates: list[str] = []
    if dividends is not None and len(dividends):
        ex_dates = [str(d) for d in dividends.index]
    if not ex_dates:
        return []  # ③ 无分红史的股票,跌多少都不是除息伪影
    suspects: list[str] = []
    prev_ret = ret.shift(1)
    for d, r in ret.dropna().items():
        ds = str(d)
        if not (-hi <= float(r) <= -lo):
            continue
        p = prev_ret.get(d)
        if p is not None and not pd.isna(p) and float(p) < -0.01:
            continue  # ② 前一日已在跌 → 滑梯不是缺口
        # 附近有分红事件 → 该跌幅大概率就是除息,不是嫌疑
        from datetime import datetime  # 局部 import:纯函数避免顶部多余依赖
        dd = datetime.strptime(ds, "%Y-%m-%d")
        near = any(abs((dd - datetime.strptime(e[:10], "%Y-%m-%d")).days) <= nearby_days
                   for e in ex_dates)
        if not near:
            suspects.append(ds)
    return suspects
