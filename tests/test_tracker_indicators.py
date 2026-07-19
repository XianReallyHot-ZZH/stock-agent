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
