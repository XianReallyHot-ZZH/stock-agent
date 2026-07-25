"""Pure indicator functions for the 指数择时层 (V4 tracker).

Implements the 60-day moving-line method from 课程 Session 12-13:
  - ma_series / deviation_series   — 60日线 + 价格/均线偏离(S13 偏离极值套利的底座)
  - trend_state                    — 60日线趋势状态(上/下、均线趋势、左/右侧)
  - deviation_extremes             — 偏离历史极值 + 当前分位(S13 "接近极值才有最高确定性")
  - breakout_grade                 — 有效突破/跌破 6档梯度(S13 确定性由低到高)
  - is_choppy                      — 震荡市识别(反复横穿 → 趋势信号是噪音,关闭)
  - style_allocation               — 蓝筹 vs 成长 趋势对比 → 仓位倾向(S13)
  - relative_spread_series / cycle_extremes / classify_cycle
      — 相对周期律(创业板 vs 上证 点差,线性回归去漂移 → 残差极点/中枢)
  - relative_momentum / consecutive_run
      — 风格轮动持续性(短期相对动量 + 连续跑盈天数)

All scalars evaluate at the LAST bar (no lookahead). *_series return full lines.
Reuses engine.indicators idiom (NaN on short series, float close.iloc[-1]).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MA_PERIOD = 60  # S12-13: 60日均线(中线趋势;短线可用20)

CYCLE_PCT_LOW, CYCLE_PCT_HIGH = 0.20, 0.80  # ⑦周期分位极点阈值(镜像 PE_PCT_LOW/HIGH)


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


def last_ma_cross(close: pd.Series, period: int = MA_PERIOD) -> dict | None:
    """最近一次 close 真正穿越 MA(period) 的事件 —— 区别于 breakout_grade(后者把「在线上」
    也叫突破)。返回 {direction:'up'/'down', date, bars_ago} 或 None(数据不足/从未穿越)。
      'up'  = 从下方穿到上方(向上突破)
      'down'= 从上方穿到下方(向下跌破)
    严格变号判定(sign −1↔+1 才算,触线/贴线 sign=0 不算)。bars_ago = 距最后 bar 的交易日数。"""
    ma = ma_series(close, period)
    if ma is None or len(ma) < 2:
        return None
    diff = (close - ma).dropna()
    if len(diff) < 2:
        return None
    flips = np.sign(diff).diff().abs() == 2          # 严格穿越(−1↔+1)
    if not flips.any():
        return None
    idx = diff.index[flips.values][-1]
    pos = diff.index.get_loc(idx)
    return {"direction": "up" if diff.iloc[pos] > 0 else "down",
            "date": str(idx), "bars_ago": int(len(diff) - 1 - pos)}


def fresh_cross_direction(cross: dict | None, fresh: int = 5) -> str | None:
    """最近 `fresh`(默认 5) 个交易日内真正穿越 MA 的方向('up'/'down'),否则 None。
    用于把「有效突破/跌破」锚定到真实穿越事件(而非 breakout_grade 那种「在线上=突破」)。
    配合 breakout_grade 的 grade≥2(偏离≥2%)即 S13「收盘价穿越 60日线 ±2% 以上」的本意。"""
    if not cross:
        return None
    ago = cross.get("bars_ago")
    if ago is None or ago > fresh:
        return None
    return cross.get("direction")


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


# ---- ⑦ 相对周期律(创业板 vs 上证 点差:去漂移 → 极点/中枢) ----
def _ols_fit(s: pd.Series) -> tuple[float, pd.Series] | None:
    """OLS 线性拟合(deg=1)。返回 (每 bar 斜率, 拟合线) 或 None(<20 点)。
    漂移线 = 长期中枢(含结构性漂移);残差 = spread − 漂移线 = 稳态周期。"""
    s = s.dropna()
    if len(s) < 20:
        return None
    x = np.arange(len(s), dtype=float)
    slope, intercept = np.polyfit(x, s.to_numpy(dtype=float), 1)
    fitted = pd.Series(slope * x + intercept, index=s.index)
    return float(slope), fitted


def relative_spread_series(benchmark_close: pd.Series,
                           growth_close: pd.Series) -> pd.Series:
    """基准 − 成长(原始点数;⑦ 默认 上证综指 − 创业板指)。
    两序列按日期对齐(union index)后 dropna → 仅保留共同交易日,再相减。
    返回点差 Series(name='spread');无重叠 → 空 Series。"""
    bm = pd.Series(benchmark_close, dtype=float)
    gr = pd.Series(growth_close, dtype=float)
    df = pd.DataFrame({"benchmark": bm, "growth": gr}).dropna()
    if not len(df):
        return pd.Series([], dtype=float, name="spread")
    return (df["benchmark"] - df["growth"]).rename("spread")


def linear_fit_line(s: pd.Series, lookback: int | None = None) -> pd.Series:
    """OLS 线性拟合线(deg=1)——把折线(如滚动包络上下沿)简化为一条直线看趋势。
    lookback>0 时只在末尾 lookback bar 上拟合。返回拟合线 Series(对齐拟合样本);<20 点 → 空 Series。"""
    s = pd.Series(s, dtype=float)
    if lookback:
        s = s.iloc[-lookback:]
    fit = _ols_fit(s)
    return pd.Series([], dtype=float) if fit is None else fit[1]


def cycle_extremes(spread: pd.Series, envelope: int = 252 * 5,
                   drift_lookback: int = 252 * 10) -> dict:
    """相对周期律诊断(作者口径: raw spread 在滚动包络内的位置)。

    作者方法 = 当前点差在「上一周期+本周期」振幅区间 [min, max] 里的位置:
      env_pos        = (cur − min)/(max − min) ∈ [0,1]   ← headline(作者全部三次调用都由此复现)
      rank_pct       = 同窗口内 raw spread 的秩分位       ← 副指标「历史稀有度」(house 口径)
      drift_pts_per_yr = 长窗口 OLS 斜率×252             ← 结构漂移率(info; <0 = 成长结构性跑赢)
    envelope 默认 5 年(实测 2-5yr 给出同一约 -250..1350 带,稳定)。"""
    s = pd.Series(spread, dtype=float).dropna()
    if len(s) < 252 * 2:
        return {"min_spread": np.nan, "max_spread": np.nan, "envelope_mid": np.nan,
                "spread_now": np.nan, "env_pos": np.nan, "rank_pct": np.nan,
                "drift_pts_per_yr": np.nan, "valid": False}
    env = s.iloc[-envelope:] if envelope and len(s) >= envelope else s
    lo, hi = float(env.min()), float(env.max())
    cur = float(s.iloc[-1])
    span = hi - lo
    env_pos = (cur - lo) / span if span > 0 else 0.5
    rank_pct = float((env < cur).sum()) / len(env)
    drift = np.nan
    fit = _ols_fit(s.iloc[-drift_lookback:]) if drift_lookback else _ols_fit(s)
    if fit is not None:
        drift = fit[0] * 252.0
    return {
        "min_spread": lo, "max_spread": hi, "envelope_mid": (lo + hi) / 2.0,
        "spread_now": cur, "env_pos": float(env_pos), "rank_pct": rank_pct,
        "drift_pts_per_yr": float(drift), "valid": True,
    }


def classify_cycle(pos: float, low: float = CYCLE_PCT_LOW,
                   high: float = CYCLE_PCT_HIGH) -> str:
    """周期位置(0..1,默认喂 env_pos)→ 区间标签。
      pos≤low  → 下沿极点(回归方向:上证相对跑盈)
      pos≥high → 上沿极点(回归方向:创业板相对跑盈)
      中间     → 中枢·无edge(仅相对回归风险解除,非绝对方向)"""
    if pd.isna(pos):
        return "—"
    if pos <= low:
        return "下沿极点"
    if pos >= high:
        return "上沿极点"
    return "中枢·无edge"


# ---- ⑦·B 风格轮动持续性(折进同一节)----
def relative_momentum(spread: pd.Series, short_window: int = 20) -> dict:
    """点差短期动量:近 short_window 日的净变动(点) + 历史日均绝对变动(量级参考)。"""
    s = pd.Series(spread, dtype=float).dropna()
    if len(s) <= short_window:
        return {"mom_now": np.nan, "mom_avg": np.nan, "valid": False}
    return {
        "mom_now": float(s.iloc[-1] - s.iloc[-1 - short_window]),
        "mom_avg": float(s.diff().abs().mean()),
        "valid": True,
    }


def consecutive_run(spread: pd.Series, window: int = 60) -> dict:
    """连续相对跑盈天数:从末尾往前数 spread.diff() 同号的连续 bar 数。
      spread↑ = 基准(上证)当日跑赢 → direction='上证跑盈';spread↓ → '创业板跑盈'。
    run 计数 cap 在 window;首日无 diff 时 run=0。"""
    s = pd.Series(spread, dtype=float).dropna()
    if len(s) < 2:
        return {"direction": None, "run": 0, "valid": False}
    diffs = s.diff().dropna().iloc[-window:]
    if not len(diffs):
        return {"direction": None, "run": 0, "valid": True}
    last_sign = float(np.sign(diffs.iloc[-1]))
    if last_sign == 0:
        return {"direction": None, "run": 0, "valid": True}
    run = 0
    for v in reversed(diffs.to_numpy()):
        if float(np.sign(v)) == last_sign:
            run += 1
        else:
            break
    return {"direction": "上证跑盈" if last_sign > 0 else "创业板跑盈",
            "run": run, "valid": True}
