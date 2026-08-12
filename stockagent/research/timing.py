"""ETF 择时跟踪 · 纯函数（本看板计算核心）。

本看板从「性价比评估」转定位为「ETF 择时跟踪」：只跟踪、不标买卖点、决策由人综合
多个看板做出。两件事：

  1. 净值-MA60 偏离度：当前偏离 + 历史百分位分位 + 「第几极值」（枚举自身历史 top-N
     极值点）。每个 ETF 自标，不跨 ETF 比。
  2. 份额-净值「剪刀差」分化跟踪：returns 口径、自适应窗口、双向；检不出干净分化
     则退化原始数据、不强标。

隔离说明：偏离度计算的纯函数（ma_series / deviation_series / deviation_extremes /
_merged_runs / deviation_extreme_events）从 stockagent/tracker/indicators.py **复制**
到本模块（而非 import），使 research 包对 tracker 零依赖——符合「改动不影响其他看板」
的硬约束。与 tracker 版同实现；若 tracker 侧逻辑演进，此处刻意保持独立。NAV 用累计
净值 acc_nav（复权连续），unit_nav 在拆分/分红有断崖会伪造偏离极值。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_PERIOD = 60  # 与 tracker / config research.ma_period 默认一致


# ---------------------------------------------------------------------------
# 偏离度纯函数 —— 复制自 stockagent/tracker/indicators.py（隔离，见模块 docstring）
# ---------------------------------------------------------------------------

def ma_series(close: pd.Series, period: int = MA_PERIOD) -> pd.Series:
    """Rolling SMA over the whole series (NaN for the first period-1 bars)."""
    if close is None or len(close) < period:
        return pd.Series(np.nan, index=close.index if close is not None else None)
    return close.rolling(period).mean()


def deviation_series(close: pd.Series, period: int = MA_PERIOD) -> pd.Series:
    """close/MA − 1 at each bar。NaN where MA undefined."""
    ma = ma_series(close, period)
    return close / ma - 1.0


def deviation_extremes(close: pd.Series, period: int = MA_PERIOD,
                       lookback: int | None = None) -> dict:
    """历史偏离极值 + 当前分位。

    Returns {max_dev, min_dev, cur_dev, pct, valid}:
      max_dev/min_dev — close/MA−1 的历史最大/最小
      cur_dev         — 当前 close/MA−1
      pct             — cur_dev 在历史中的分位（0=最负/超卖, 1=最正/超买）
    需 ≥20 根 dev，否则 valid=False。
    """
    dev = deviation_series(close, period).dropna()
    if lookback:
        dev = dev.iloc[-lookback:]
    if len(dev) < 20:
        return {"max_dev": np.nan, "min_dev": np.nan, "cur_dev": np.nan,
                "pct": np.nan, "valid": False}
    cur = float(dev.iloc[-1])
    return {
        "max_dev": float(dev.max()),
        "min_dev": float(dev.min()),
        "cur_dev": cur,
        "pct": float((dev < cur).sum()) / len(dev),
        "valid": True,
    }


def _merged_runs(mask: np.ndarray, merge_gap: int) -> list[tuple[int, int]]:
    """Maximal True runs in a bool mask; merge runs separated by ≤ merge_gap False bars.

    Returns (start, end_exclusive) index pairs。把抖动着多次穿越阈值的极值区合并成单个事件。"""
    runs: list[list[int]] = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    if merge_gap <= 0 or len(runs) <= 1:
        return [(int(s), int(e)) for s, e in runs]
    merged = [runs[0]]
    for s, e in runs[1:]:
        if s - merged[-1][1] <= merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(int(s), int(e)) for s, e in merged]


def deviation_extreme_events(close: pd.Series, period: int = MA_PERIOD,
                             lo_pct: float = 0.05, hi_pct: float = 0.95,
                             merge_gap: int = 5, top_n: int = 8) -> list[dict]:
    """偏离度历史极值事件（全历史口径，纯观察用）——「第几极值」。

    在全历史分位 ≤lo_pct(超卖) / ≥hi_pct(超买) 的连续区间（相邻抖动段按 merge_gap 合并）
    内取最深处一点（谷 / 峰），标注其全历史「第几」（同侧事件按深度排序，
    1 = 史上最深谷 / 最高峰，唯一不重复）。

    返回 [{date, dev, side, rank}, ...] 按日期升序; side ∈ {'low','high'}。
    全历史口径: 每个点用整段历史当尺子（不防前视; 纯观察不交易, 无所谓）。
    短序列 / 全 NaN → []。
    """
    dev = deviation_series(close, period)
    if dev.dropna().empty:
        return []
    vals = dev.to_numpy()
    idx = dev.index
    pct_full = dev.rank(pct=True)                       # 0=最负/超卖, 1=最正/超买; NaN→NaN
    mask_lo = (pct_full <= lo_pct).fillna(False).to_numpy()
    mask_hi = (pct_full >= hi_pct).fillna(False).to_numpy()

    events: list[dict] = []
    for side, mask, pick in (("low", mask_lo, np.nanargmin),
                             ("high", mask_hi, np.nanargmax)):
        if not mask.any():
            continue
        for start, end in _merged_runs(mask, merge_gap):
            seg = vals[start:end]
            if seg.size == 0 or np.all(np.isnan(seg)):
                continue
            i = start + int(pick(seg))                  # 最深处一点在全序列中的位置
            d = vals[i]
            if np.isnan(d):
                continue
            events.append({"date": idx[i], "dev": float(d), "side": side})

    # 全历史排名: 同侧事件按深度排序, 1 = 史上最极端(唯一不重复); 各取前 top_n
    for side, reverse in (("low", False), ("high", True)):
        side_evs = [e for e in events if e["side"] == side]
        side_evs.sort(key=lambda e: e["dev"], reverse=reverse)   # low 升序(最负在前) / high 降序
        for k, e in enumerate(side_evs[:top_n], start=1):
            e["rank"] = k
    out = [e for e in events if "rank" in e]
    out.sort(key=lambda e: e["date"])                   # 按日期升序（便于绘图）
    return out


# ---------------------------------------------------------------------------
# 份额-净值「剪刀差」分化检测 —— 本看板独有，无现成原语
# ---------------------------------------------------------------------------

def scissor_divergence(shares, nav_acc, window_range: tuple[int, int] = (20, 120),
                       floor: float = 0.05) -> dict:
    """检测一个窗口内 份额 与 净值 走向分化（一升一降）。

    returns-based 口径（用总收益 s1/s0-1，天然处理份额的阶跃跳跃，且消掉份额的长期增长
    偏置——否则任何成长型 ETF 都会一直读「份额高」）。

    窗口自适应：扫描 W ∈ [window_range] 天，命中 = 两端总收益异号 且 |漂移| 均 ≥ floor
    （双向：份↑净↓ / 份↓净↑），取对比度 |share_drift − nav_drift| 最大的窗口。

    Returns:
      命中 → {detected:True, start, end, window, share_drift, nav_drift, direction, contrast}
      未命中 → {detected:False}
      direction ∈ {'share_up_nav_down','share_down_nav_up'}
    """
    if shares is None or nav_acc is None:
        return {"detected": False}
    n = pd.to_numeric(pd.Series(nav_acc), errors="coerce").dropna()
    s = pd.to_numeric(pd.Series(shares), errors="coerce").dropna()
    if len(n) < 5 or len(s) < 5:
        return {"detected": False}
    # 份额变更稀疏 → 按 NAV 日线时间轴对齐 + ffill；再 dropna 取两者都有的公共日期
    df = pd.DataFrame({"n": n, "s": s.reindex(n.index, method="ffill")}).dropna()
    if len(df) < 5:
        return {"detected": False}
    n = df["n"]
    s = df["s"]
    lo_w, hi_w = window_range
    hi_w = min(hi_w, len(df))
    if hi_w < lo_w:
        return {"detected": False}

    best = None
    s_last, n_last = float(s.iloc[-1]), float(n.iloc[-1])
    for W in range(lo_w, hi_w + 1):
        s0, n0 = float(s.iloc[-W]), float(n.iloc[-W])
        if s0 <= 0 or n0 <= 0:
            continue
        sd = s_last / s0 - 1.0
        nd = n_last / n0 - 1.0
        if sd * nd < 0 and abs(sd) >= floor and abs(nd) >= floor:
            contrast = abs(sd - nd)
            if best is None or contrast > best["contrast"]:
                best = {
                    "detected": True,
                    "start": df.index[-W],
                    "end": df.index[-1],
                    "window": W,
                    "share_drift": sd,
                    "nav_drift": nd,
                    "direction": "share_up_nav_down" if sd > 0 else "share_down_nav_up",
                    "contrast": contrast,
                }
    return best or {"detected": False}


# ---------------------------------------------------------------------------
# 单 ETF 快照装配
# ---------------------------------------------------------------------------

def timing_snapshot(nav_df, shares_df, ma_period: int = MA_PERIOD,
                    scissor_window: tuple[int, int] = (20, 120),
                    scissor_floor: float = 0.05) -> dict:
    """单 ETF 择时快照：净值-MA 偏离度（分位 + 第几极值）+ 份额/净值剪刀差。纯函数。

    nav_df: Store.get_nav_series → [unit_nav, acc_nav]（优先 acc_nav，复权连续）
    shares_df: Store.get_scale_series → [shares, ...] 或 None
    data_sufficient = 偏离度可算（≈ ≥ ma_period+20 根 acc_nav）。
    """
    # 选 acc_nav（复权连续），无则退 unit_nav
    nav_series = None
    nav_col = None
    if nav_df is not None and len(nav_df):
        for col in ("acc_nav", "unit_nav"):
            if col in nav_df.columns:
                cand = pd.to_numeric(nav_df[col], errors="coerce").dropna()
                if len(cand):
                    nav_series, nav_col = cand, col
                    break

    if nav_series is None or len(nav_series) < ma_period:
        return {"nav_dev_cur": np.nan, "nav_dev_pct": np.nan, "nav_dev_max": np.nan,
                "nav_dev_min": np.nan, "nav_extreme_events": [],
                "scissor": {"detected": False}, "data_sufficient": False,
                "ma_period": ma_period, "nav_col": nav_col}

    ext = deviation_extremes(nav_series, period=ma_period)
    events = deviation_extreme_events(nav_series, period=ma_period)
    shares_series = None
    if shares_df is not None and len(shares_df) and "shares" in shares_df.columns:
        shares_series = shares_df["shares"]
    scissor = scissor_divergence(shares_series, nav_series,
                                 window_range=scissor_window, floor=scissor_floor)
    return {
        "nav_dev_cur": ext["cur_dev"],
        "nav_dev_pct": ext["pct"],
        "nav_dev_max": ext["max_dev"],
        "nav_dev_min": ext["min_dev"],
        "nav_extreme_events": events,
        "scissor": scissor,
        "data_sufficient": bool(ext["valid"]),
        "ma_period": ma_period,
        "nav_col": nav_col,
    }
