"""Pure indicator functions for the 指数择时层 (V4 tracker).

Implements the 60-day moving-line method from 课程 Session 12-13:
  - ma_series / deviation_series   — 60日线 + 价格/均线偏离(S13 偏离极值套利的底座)
  - trend_state                    — 60日线趋势状态(上/下、均线趋势、左/右侧)
  - deviation_extremes             — 偏离历史极值 + 当前分位(S13 "接近极值才有最高确定性")
  - breakout_grade                 — 有效突破/跌破 6档梯度(S13 确定性由低到高)
  - is_choppy                      — 震荡市识别(反复横穿 → 趋势信号是噪音,关闭)
  - style_allocation               — 蓝筹 vs 成长 趋势对比 → 仓位倾向(S13)

All scalars evaluate at the LAST bar (no lookahead). *_series return full lines.
Reuses engine.indicators idiom (NaN on short series, float close.iloc[-1]).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_PERIOD = 60  # S12-13: 60日均线(中线趋势;短线可用20)


def ma_series(close: pd.Series, period: int = MA_PERIOD) -> pd.Series:
    """Rolling SMA over the whole series (NaN for the first period-1 bars).

    Unlike engine.indicators.sma (last-bar scalar), this returns the full MA line —
    needed to compute per-bar deviation and its historical extremes."""
    if close is None or len(close) < period:
        return pd.Series(np.nan, index=close.index if close is not None else None)
    return close.rolling(period).mean()


def deviation_series(close: pd.Series, period: int = MA_PERIOD) -> pd.Series:
    """close/MA − 1 at each bar (S13 偏离度). NaN where MA undefined."""
    ma = ma_series(close, period)
    return close / ma - 1.0


def trend_state(close: pd.Series, period: int = MA_PERIOD) -> dict:
    """60-day trend snapshot at the last bar (S12-13).

    Returns {above_ma, ma_trend_up, price_vs_ma_pct, valid}:
      above_ma      — last close >= last MA (右侧=True / 左侧=False)
      ma_trend_up   — today's MA >= yesterday's MA (均线趋势方向)
      price_vs_ma_pct — close/MA − 1
    """
    ma = ma_series(close, period)
    if len(ma) < 1 or np.isnan(ma.iloc[-1]):
        return {"above_ma": None, "ma_trend_up": None, "price_vs_ma_pct": np.nan, "valid": False}
    last_ma = float(ma.iloc[-1])
    last_close = float(close.iloc[-1])
    ma_trend_up = None
    if len(ma) >= 2 and not np.isnan(ma.iloc[-2]):
        ma_trend_up = last_ma >= float(ma.iloc[-2])
    return {
        "above_ma": last_close >= last_ma,
        "ma_trend_up": ma_trend_up,
        "price_vs_ma_pct": last_close / last_ma - 1.0 if last_ma > 0 else np.nan,
        "valid": True,
    }


def deviation_extremes(close: pd.Series, period: int = MA_PERIOD,
                       lookback: int | None = None) -> dict:
    """Historical deviation extremes + current percentile (S13 极值套利).

    Returns {max_dev, min_dev, cur_dev, pct, valid}:
      max_dev/min_dev — historical max/min of close/MA−1
      cur_dev         — current close/MA−1
      pct             — percentile of cur_dev in history (0=最负/超卖, 1=最正/超买)
    S13: 只有 pct 接近 0 或 1(非常接近历史极值)才有最高确定性做套利。"""
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


def breakout_grade(close: pd.Series, period: int = MA_PERIOD,
                   thresholds: tuple[float, float] = (0.02, 0.03)) -> dict:
    """60-day breakout/breakdown strength at the last bar (S13 确定性梯度).

    Returns {direction, grade, label, price_vs_ma_pct, ma_trend_up, valid}:
      direction ∈ {'up','down','none'}; grade = strength (1=收盘穿越, 2=±2%, 3=±3%),
      +1 if MA trend confirms direction. Thresholds default to S13 的 2%/3% 「有效突破/跌破」。
    """
    ma = ma_series(close, period)
    if len(ma) < 1 or np.isnan(ma.iloc[-1]):
        return {"direction": "none", "grade": 0, "label": "数据不足", "valid": False}
    last_ma = float(ma.iloc[-1])
    if last_ma <= 0:
        return {"direction": "none", "grade": 0, "label": "数据不足", "valid": False}
    pct = float(close.iloc[-1]) / last_ma - 1.0
    t1, t2 = thresholds
    ma_up = (len(ma) >= 2 and not np.isnan(ma.iloc[-2]) and last_ma >= float(ma.iloc[-2]))

    direction, grade = "none", 0
    if pct >= t2:
        direction, grade = "up", 3
    elif pct >= t1:
        direction, grade = "up", 2
    elif pct > 0:
        direction, grade = "up", 1
    elif pct <= -t2:
        direction, grade = "down", 3
    elif pct <= -t1:
        direction, grade = "down", 2
    elif pct < 0:
        direction, grade = "down", 1
    # MA trend confirms the breakout/breakdown direction → +1 strength
    if direction != "none" and ma_up == (direction == "up"):
        grade += 1
    label = {"up": "突破", "down": "跌破", "none": "中性"}[direction]
    return {"direction": direction, "grade": grade, "label": label,
            "price_vs_ma_pct": pct, "ma_trend_up": ma_up, "valid": True}


def is_choppy(close: pd.Series, period: int = MA_PERIOD, window: int = 60,
              cross_threshold: int = 6) -> bool:
    """震荡市 flag (S13): price has crossed the MA ≥ cross_threshold times in the last
    `window` bars → trend signal is noise, the 60-day method should be disabled. False
    when insufficient data (don't block)."""
    ma = ma_series(close, period)
    if ma is None or len(ma.dropna()) < window:
        return False
    diff = (close - ma).iloc[-window:].dropna()
    if len(diff) < 2:
        return False
    signs = np.sign(diff)
    crosses = int((signs.diff().abs() == 2).sum())  # strict sign flips (excludes touching 0)
    return crosses >= cross_threshold


def style_allocation(blue_chip: pd.Series, growth: pd.Series,
                     period: int = MA_PERIOD) -> dict:
    """蓝筹(上证50) vs 成长(创业板/中证500) trend comparison → position lean (S13).

    "Up" = above MA AND MA rising. Returns {blue_up, growth_up, lean, valid} where
    lean ∈ {'growth','blue_chip'}: 都上→偏成长(弹性好), 都下→偏蓝筹(防御), 相反→偏向上的。"""
    bt = trend_state(blue_chip, period)
    gt = trend_state(growth, period)
    if not bt["valid"] or not gt["valid"]:
        return {"blue_up": None, "growth_up": None, "lean": None, "valid": False}
    b_up = bool(bt["above_ma"]) and bool(bt["ma_trend_up"])
    g_up = bool(gt["above_ma"]) and bool(gt["ma_trend_up"])
    if b_up and g_up:
        lean = "growth"
    elif not b_up and not g_up:
        lean = "blue_chip"
    elif b_up:
        lean = "blue_chip"
    else:
        lean = "growth"
    return {"blue_up": b_up, "growth_up": g_up, "lean": lean, "valid": True}
