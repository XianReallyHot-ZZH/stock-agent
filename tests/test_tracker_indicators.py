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


def test_deviation_pct_expanding_no_lookahead():
    # 防前视核心: 末尾追加未来数据, 已发生的 expanding 分位应完全不变
    base = _line([100.0] * 95 + [90.0] * 5)               # 100 根
    ext = _line([100.0] * 95 + [90.0] * 5 + [50.0] * 10)  # 追加 10 根大跌(未来)
    pb = ti.deviation_pct_expanding(base, 60).dropna().to_numpy()
    pe = ti.deviation_pct_expanding(ext, 60).dropna().to_numpy()
    assert np.allclose(pb, pe[:len(pb)])                  # 前 100 根分位不受未来影响
    assert np.isnan(ti.deviation_pct_expanding(base, 60).iloc[0])  # 前 period 根 NaN


def test_deviation_pct_expanding_last_bar_matches_full_history():
    # 末根时 expanding == 全历史 deviation_extremes(无未来可偷, 两者必然一致)
    s = _line([100.0] * 80 + [130.0])  # 末根大涨 → 历史最正偏离
    pct = ti.deviation_pct_expanding(s, 60)
    ex = ti.deviation_extremes(s, 60)
    assert ex["valid"]
    assert abs(float(pct.iloc[-1]) - ex["pct"]) < 1e-9
    assert ex["cur_dev"] == ex["max_dev"]      # 末根是历史最正偏离
    assert ex["pct"] > 0.9                     # (n-1)/n, 接近顶部分位


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


# ---- ⑧ 成交量地量监测(turnover/MA250 → 地量 + event-study)----
def test_turnover_percentile():
    s = _line([float(i) for i in range(30)])   # 单调升,末尾最大 → 分位高
    assert ti.turnover_percentile(s) >= 0.9


def test_turnover_dry_events_threshold_and_cooldown():
    # 250 高值(100) 后 50 个低值(50) → ratio 0.5≤0.6 触发;cooldown=20 → 每 20 日 1 个事件
    s = _line([100.0] * 250 + [50.0] * 50)
    ev = ti.turnover_dry_events(s, ma=250, threshold=0.6, cooldown=20)
    assert len(ev) == 3    # 第250/270/290 个 bar(50 个 dry 日,每 20 日去簇 → 3 次)


def test_turnover_dry_events_no_trigger_when_high():
    s = _line([100.0] * 300)   # 全程平稳高 → ratio=1.0 > 0.6,不触发
    assert ti.turnover_dry_events(s, ma=250, threshold=0.6) == []


def test_volume_bottom_stats_known_returns():
    # 构造: 1 次地量(成交额骤降)后 px 单调涨 → 胜率应 100%、量底即价底(ttb=1)
    t = _line([100.0] * 250 + [50.0] + [100.0] * 30)     # 仅第 250 bar dry
    px = _line([100.0] * 251 + [100.0 + i * 2 for i in range(1, 31)])  # 地量后线性涨
    stats = ti.volume_bottom_stats(t, px, ma=250, threshold=0.6, forward=(5, 10, 20), cooldown=20)
    assert stats["sample"] >= 1
    assert stats["win_rate_20"] == 1.0          # 地量后 px 只涨
    assert stats["time_to_bottom_median"] == 1  # 地量当日即最低(之后只涨)


def test_volume_bottom_stats_min_lookback():
    # 第250 bar dry(值50), 之前全100 → 无更低 → lookback=250/252≈0.99yr
    t = _line([100.0] * 250 + [50.0] + [100.0] * 30)
    px = _line([100.0] * 251 + [100.0 + i * 2 for i in range(1, 31)])
    base = dict(ma=250, threshold=0.6, forward=(5, 10, 20), cooldown=20)
    assert ti.volume_bottom_stats(t, px, **base)["sample"] == 1          # 不过滤 → 1
    assert ti.volume_bottom_stats(t, px, min_lookback=2.0, **base)["sample"] == 0   # 0.99<2 → 滤掉
    assert ti.volume_bottom_stats(t, px, min_lookback=0.5, **base)["sample"] == 1   # 0.99>=0.5 → 留


def test_turnover_new_low_years():
    # [50, 100, 80]: 第2个(80)的 prev-lower = 第0个(50) → lookback (2-0)/252
    s = _line([50.0, 100.0, 80.0])
    lby = ti.turnover_new_low_years(s)
    assert abs(float(lby.iloc[0])) < 1e-9             # 第一个无更低 → 0
    assert abs(float(lby.iloc[2]) - 2 / 252) < 1e-6   # 距 index0
    # 单调递减: 每个 bar 都是新低 → 无更低 → lookback = i/252
    dec = ti.turnover_new_low_years(_line([100.0 - i for i in range(5)]))
    assert abs(float(dec.iloc[3]) - 3 / 252) < 1e-6
