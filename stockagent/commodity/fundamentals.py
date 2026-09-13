"""🔬 基差/期限结构/库存 纯函数(二期剩余 · event-study 礼遇层与看板观察层共享)。

数据:commodity_basis(100ppi 生意社,2019 起日频) + commodity_inventory(CZCE 总计口径周采样
2021 起 + 批次2.3 fut_wsr 仓库和周采样 2019 起) + fut_mapping/fut_contract_daily(批次2.4
展期口径,roll_adjusted_series 消费)。
口径纪律:分位一律 expanding(point-in-time,无前视,同 validate_deviation_extreme 礼遇);
温度计非开关——实证结论注入看板,不出现买卖措辞。
端点真相(DCE 仓单无源·CZCE 品种不吃 fut_wsr 混列·99qh 死·em 仅72天)见 fetcher 注。
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


def roll_adjusted_series(dom_close: pd.Series, mapping: pd.Series,
                         contract_close: dict) -> tuple[pd.Series, int]:
    """展期调整连续指数(批次2.4·精确展期口径)。

    主力拼接序列在换月日的收益 = 新约(t)/旧约(t−1),含换月跳空(contango 展期成本一次性兑现),
    污染 event-study 前向收益。修正法(收益率口径拼接):换月日 t 的持仓真实收益 =
    **旧合约** close(t)/close(t−1) − 1(旧约 t 日仍在交易;收盘后切新约,次日起恢复主连自身收益);
    逐日收益 cumprod 还原为指数(基=主连首个有效收盘;指数水平无绝对意义,只供跨换月 close-to-close)。

    dom_close=主连收盘(Series,date 升序);mapping=换月映射(date→当日主力实际合约);
    contract_close={合约代码: close Series}。旧约在 t/t−1 收盘缺 → 该换月日回退主连原生收益
    (含跳空,诚实计数)。Returns (指数 Series 与 dom_close 同 index, 回退换月日数)。"""
    px = pd.to_numeric(dom_close, errors="coerce").dropna().astype(float)
    if not len(px) or mapping is None or not len(mapping):
        return px, 0
    mp = mapping.reindex(px.index)          # 对齐主连交易日(fut_mapping 逐交易日,缺口→NaN)
    ret = px / px.shift(1) - 1.0
    n_fallback = 0
    prev = None
    idx = list(px.index)
    for i in range(1, len(idx)):
        cur_code = mp.iloc[i]
        if pd.notna(cur_code) and prev is not None and pd.notna(prev) and cur_code != prev:
            old = contract_close.get(prev)
            t, t1 = idx[i], idx[i - 1]
            if old is not None and t in old.index and t1 in old.index \
                    and float(old[t1]) > 0:
                ret.iloc[i] = float(old[t]) / float(old[t1]) - 1.0
            else:
                n_fallback += 1             # 旧约收盘缺 → 回退含跳空的原生收益
        if pd.notna(mp.iloc[i]):
            prev = mp.iloc[i]
    adj = (1.0 + ret.fillna(0.0)).cumprod() * px.iloc[0]
    adj.iloc[0] = px.iloc[0]
    return adj, n_fallback


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
