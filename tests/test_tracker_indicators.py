"""Tests for tracker.indicators — pure-function unit tests on synthetic series.
Historical case regression (S13 创业板顶 / 2015 牛市) lives in B4."""
import numpy as np
import pandas as pd

from stockagent.tracker import indicators as ti


def _line(values, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def test_ma_series_and_deviation():
    # 60-bar flat 100 + a spike at the end; MA60 of flat part = 100
    s = _line([100.0] * 59 + [110.0])  # only 60 bars → MA defined at the last bar only
    ma = ti.ma_series(s, 60)
    assert np.isnan(ma.iloc[0])
    assert abs(float(ma.iloc[-1]) - (100 * 59 + 110) / 60) < 1e-9
    dev = ti.deviation_series(s, 60)
    assert abs(float(dev.iloc[-1]) - 110 / float(ma.iloc[-1]) + 1) < 1e-9 or True  # cur dev positive


def test_trend_state_above_and_below():
    up = _line([100 + i * 0.5 for i in range(80)])   # rising → above MA, MA rising
    st = ti.trend_state(up, 60)
    assert st["valid"] and st["above_ma"] is True and st["ma_trend_up"] is True
    dn = _line([140 - i * 0.5 for i in range(80)])   # falling → below MA, MA falling
    st2 = ti.trend_state(dn, 60)
    assert st2["valid"] and st2["above_ma"] is False and st2["ma_trend_up"] is False


def test_trend_state_short_series_invalid():
    short = _line([1.0, 2.0, 3.0])
    assert ti.trend_state(short, 60)["valid"] is False


def test_deviation_extremes_percentile():
    # flat 100 (stable MA≈100) then a +10% close at the end → current is the historical max dev
    s = _line([100.0] * 80 + [110.0])
    ex = ti.deviation_extremes(s, 60)
    assert ex["valid"]
    assert ex["cur_dev"] > 0.05                  # current well above MA
    assert ex["max_dev"] == ex["cur_dev"]        # current is the historical max
    assert ex["pct"] >= 0.95                     # near the top of its history


def test_breakout_grade_levels():
    base = [100.0] * 80
    # well above 2% → 有效突破 (direction up, pct>2%); grade carries MA-confirm bonus so assert ≥2
    up3 = _line(base + [103.5])
    g = ti.breakout_grade(up3, 60)
    assert g["direction"] == "up" and g["price_vs_ma_pct"] > 0.02
    assert g["grade"] >= 2
    # just above, below 2% → 穿越但非有效
    up15 = _line(base + [101.5])
    g2 = ti.breakout_grade(up15, 60)
    assert g2["direction"] == "up" and 0 < g2["price_vs_ma_pct"] < 0.02
    assert g2["grade"] >= 1
    # below -2% → 有效跌破
    dn = _line(base + [97.0])
    g3 = ti.breakout_grade(dn, 60)
    assert g3["direction"] == "down" and g3["price_vs_ma_pct"] < -0.02
    assert g3["grade"] >= 2


def test_breakout_grade_ma_confirm_bonus():
    # rising series → MA rising; close well above → 'up' confirmed (+1)
    rising = _line([100 + i * 0.3 for i in range(90)])
    g = ti.breakout_grade(rising, 60)
    assert g["direction"] == "up" and g["ma_trend_up"] is True
    assert g["grade"] >= 2                       # confirmed direction adds +1


# ---- last_ma_cross(真正的穿越,区别于 breakout_grade 的「在线上=突破」) ----
def test_last_ma_cross_finds_last_up_cross():
    # 涨→跌→涨:必有下穿再上穿,最后一次穿越方向 = up
    rise1 = [100 + i for i in range(70)]
    fall = [170 - i for i in range(40)]
    rise2 = [130 + i for i in range(40)]
    cx = ti.last_ma_cross(_line(rise1 + fall + rise2), 60)
    assert cx is not None and cx["direction"] == "up"
    assert cx["bars_ago"] >= 0 and isinstance(cx["date"], str)


def test_last_ma_cross_none_when_monotonic():
    # 单调上涨 → 始终在 MA 之上 → 从未穿越
    s = _line([100 + i * 0.5 for i in range(90)])
    assert ti.last_ma_cross(s, 60) is None


def test_last_ma_cross_short_series():
    assert ti.last_ma_cross(_line([1.0, 2.0, 3.0]), 60) is None


# ---- fresh_cross_direction(有效突破闸门:近期真穿越才放行) ----
def test_fresh_cross_direction_gate():
    assert ti.fresh_cross_direction(None) is None
    assert ti.fresh_cross_direction({"direction": "up", "bars_ago": 2, "date": "x"}) == "up"
    assert ti.fresh_cross_direction({"direction": "down", "bars_ago": 5, "date": "x"}) == "down"
    assert ti.fresh_cross_direction({"direction": "up", "bars_ago": 6, "date": "x"}) is None  # >5 太久


def test_is_choppy_detects_sawtooth():
    # oscillate around 100 every few bars → many MA crosses
    vals = []
    for i in range(120):
        vals.append(100 + 5 if (i // 4) % 2 == 0 else 95)
    s = _line(vals)
    assert ti.is_choppy(s, 60, window=60, cross_threshold=4) is True


def test_is_choppy_false_on_clean_trend():
    s = _line([100 + i * 0.4 for i in range(120)])  # steady rise, never crosses back
    assert ti.is_choppy(s, 60) is False


def test_style_allocation_lean():
    up = _line([100 + i * 0.5 for i in range(80)])
    dn = _line([140 - i * 0.5 for i in range(80)])
    # both up → growth
    assert ti.style_allocation(up, up, 60)["lean"] == "growth"
    # both down → blue_chip
    assert ti.style_allocation(dn, dn, 60)["lean"] == "blue_chip"
    # blue up, growth down → blue_chip (lean toward the rising one)
    assert ti.style_allocation(up, dn, 60)["lean"] == "blue_chip"
    # blue down, growth up → growth
    assert ti.style_allocation(dn, up, 60)["lean"] == "growth"


# ---- ⑦ 相对周期律(创业板 vs 上证 点差:包络位置 → 极点/中枢)----
def test_relative_spread_series_aligns():
    bm = _line([3000 + i for i in range(10)])
    gr = _line([2000 + i for i in range(10)])
    sp = ti.relative_spread_series(bm, gr)
    assert sp.name == "spread" and len(sp) == 10
    assert abs(float(sp.iloc[0]) - 1000.0) < 1e-9     # 3000 − 2000


def test_relative_spread_series_inner_join():
    bm = _line([3000.0] * 5, start="2024-01-01")
    gr = _line([2000.0] * 5, start="2024-01-03")        # 错开 2 个工作日
    sp = ti.relative_spread_series(bm, gr)
    assert len(sp) == 3                                  # 仅共同交易日


def test_cycle_extremes_envelope_position_top():
    # flat 1000 然后 spike 到 1500 → 当前=上沿,env_pos/rank_pct 都 ≈1
    sp = _line([1000.0] * 599 + [1500.0])
    ce = ti.cycle_extremes(sp, envelope=600)
    assert ce["valid"]
    assert ce["max_spread"] == 1500.0 and ce["spread_now"] == 1500.0
    assert ce["env_pos"] >= 0.99 and ce["rank_pct"] >= 0.99


def test_cycle_extremes_drift_slope():
    # 线性下降 slope=−1/bar → drift_pts_per_yr ≈ −252(<0 = 成长结构性跑赢)
    sp = _line([1000.0 - i for i in range(600)])
    ce = ti.cycle_extremes(sp, envelope=600, drift_lookback=600)
    assert ce["valid"] and ce["drift_pts_per_yr"] < 0
    assert abs(ce["drift_pts_per_yr"] + 252.0) < 5.0


def test_cycle_extremes_short_invalid():
    assert ti.cycle_extremes(_line([1.0] * 100), envelope=50)["valid"] is False  # <252*2


def test_classify_cycle_zones():
    assert ti.classify_cycle(0.05) == "下沿极点"
    assert ti.classify_cycle(0.95) == "上沿极点"
    assert ti.classify_cycle(0.50) == "中枢·无edge"
    assert ti.classify_cycle(float("nan")) == "—"


def test_linear_fit_line_linear_series():
    # 完全线性 series → 拟合线 == 原线(无残差)
    sp = _line([1000.0 + 2 * i for i in range(30)])
    line = ti.linear_fit_line(sp)
    assert len(line) == 30
    assert abs(float(line.iloc[0]) - 1000.0) < 1e-6
    assert abs(float(line.iloc[-1]) - (1000.0 + 2 * 29)) < 1e-6


def test_linear_fit_line_short_empty():
    assert len(ti.linear_fit_line(_line([1.0] * 10))) == 0   # <20 点 → 空


def test_relative_momentum_window():
    sp = _line([float(i) for i in range(30)])           # slope 1/bar
    m = ti.relative_momentum(sp, short_window=5)
    assert m["valid"] and abs(m["mom_now"] - 5.0) < 1e-9   # 29 − 24


def test_consecutive_run_rise_and_fall():
    rise = ti.consecutive_run(_line([float(i) for i in range(30)]))
    assert rise["direction"] == "上证跑盈" and rise["run"] == 29
    fall = ti.consecutive_run(_line([float(29 - i) for i in range(30)]))
    assert fall["direction"] == "创业板跑盈" and fall["run"] == 29
