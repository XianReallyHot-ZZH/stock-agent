"""🔬 基差/期限结构/库存 纯函数(二期剩余 · event-study 礼遇层与看板观察层共享)。

数据:commodity_basis(100ppi 生意社,2019 起日频) + commodity_inventory(CZCE 周采样,2021 起)。
口径纪律:分位一律 expanding(point-in-time,无前视,同 validate_deviation_extreme 礼遇);
温度计非开关——实证结论注入看板,不出现买卖措辞。
端点真相(SHFE/DCE 仓单死·GFEX 解析坏·99qh 死·em 仅72天)见 fetcher 注,库存覆盖=郑商所四品种。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def month_gap(near_yymm, dom_yymm) -> float:
    """YYMM 间隔月数(跨年展开):2701−2609=4、2611−2609=2。非法/非递增 → NaN。"""

    def _m(x):
        try:
            v = int(x)
        except (TypeError, ValueError):
            return None
        return (v // 100) * 12 + (v % 100)

    a, b = _m(near_yymm), _m(dom_yymm)
    if a is None or b is None or b <= a:
        return float("nan")
    return float(b - a)


def term_slope_annualized(basis_df: pd.DataFrame) -> pd.Series:
    """基差面板 → 近月-主力价差的年化斜率:(主力/近月 − 1) × 12/月差。正=远月升水(contango),
    负=贴水结构(backwardation,常解读为现货紧缺)。near/dom 价或月差非法 → NaN。"""
    if basis_df is None or len(basis_df) == 0:
        return pd.Series(dtype=float)
    out = []
    for near_p, dom_p, near_m, dom_m in zip(basis_df["near_price"], basis_df["dom_price"],
                                            basis_df["near_month"], basis_df["dom_month"]):
        gap = month_gap(near_m, dom_m)
        if (not np.isfinite(gap) or gap <= 0 or not np.isfinite(near_p) or not np.isfinite(dom_p)
                or near_p <= 0):
            out.append(float("nan"))
            continue
        out.append((float(dom_p) / float(near_p) - 1.0) * 12.0 / gap)
    return pd.Series(out, index=basis_df.index, dtype=float)


def expanding_pct(s: pd.Series, min_history: int = 250) -> pd.Series:
    """逐点历史分位(point-in-time):t 点分位 = (≤t 的历史中 < x_t 的比例,含当日)。
    前 min_history 个有效点置 NaN(早期分位不可靠,同 deviation_extremes 门槛)。
    O(n²)——event-study 逐事件用;看板只要当前分位请用 current_pct。"""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    vals = pd.to_numeric(s, errors="coerce").astype(float).values
    out = np.full(len(vals), np.nan)
    for i in range(len(vals)):
        if np.isnan(vals[i]):
            continue
        hist = vals[: i + 1]
        hist = hist[~np.isnan(hist)]
        if len(hist) < min_history:
            continue
        out[i] = float((hist < vals[i]).sum()) / len(hist)
    return pd.Series(out, index=s.index, dtype=float)


def current_pct(s: pd.Series, min_history: int = 250) -> float:
    """末值的全史分位(expanding_pct 的 O(n) 快路径,看板渲染用;与末点同口径:严格小于的比例)。"""
    if s is None or len(s) == 0:
        return float("nan")
    vals = pd.to_numeric(s, errors="coerce").astype(float).dropna().values
    if len(vals) < min_history or np.isnan(vals[-1]):
        return float("nan")
    return float((vals < vals[-1]).sum()) / len(vals)


def forward_return(close: pd.Series, h: int) -> pd.Series:
    """t 日 → t+h 日收益(index 对齐事件日 t;尾部 h 个 NaN)。close 需已排序升序。"""
    if close is None or len(close) == 0:
        return pd.Series(dtype=float)
    return close.shift(-h).astype(float) / close.astype(float) - 1.0


def cooldown_mask(flags: pd.Series, cooldown: int) -> pd.Series:
    """布尔事件序列 → 冷却后的序列(事件日起 cooldown 个观测内后续事件抹掉,同品种防重复计数)。"""
    out = flags.copy().astype(bool)
    last = -10 ** 9
    for i, v in enumerate(flags.values):
        if not v:
            continue
        if i - last < cooldown:
            out.iloc[i] = False
        else:
            last = i
    return out
