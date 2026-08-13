"""Tests for tracker.fear_greed — pure-function unit tests on synthetic series.

Covers the ⑨ 恐惧贪婪 composite: 滚动百分位归一化(防前视)/方向反转(波动率)/
缺失成分前填且不减分母/类别等权均值/五档阈值边界。
"""
import numpy as np
import pandas as pd

from stockagent.tracker import fear_greed as fg


def _line(values, start="2015-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def _flat_then(flat_val, n_flat, finals):
    """n_flat 根恒定 + 末尾若干根突变(测成分方向:末值落历史极值)。"""
    return _line([float(flat_val)] * n_flat + [float(x) for x in finals])


# ---- rolling_percentile ----
def test_rolling_percentile_endpoints_and_min_bars():
    s = _line(np.linspace(1, 100, 200))            # 升序 → 末值=历史最大
    p = fg.rolling_percentile(s, min_bars=60)
    assert p.iloc[:59].isna().all()                # 前 59 根(<min_bars)NaN
    assert p.iloc[-1] > 0.99                       # 末值=最大 → 接近 1
    assert p.dropna().iloc[0] > 0.9                # 升序:第一个有效点即截至当时最大 → 高分位


def test_rolling_percentile_no_lookahead():
    base = _line(np.linspace(1, 50, 150))
    ext = _line(np.linspace(1, 50, 150).tolist() + [1000.0] * 20)   # 追加未来极值
    pb = fg.rolling_percentile(base, min_bars=60).dropna().to_numpy()
    pe = fg.rolling_percentile(ext, min_bars=60).dropna().to_numpy()
    assert np.allclose(pb, pe[:len(pb)])           # 已发生分位不受未来影响


def test_rolling_percentile_short_all_nan():
    p = fg.rolling_percentile(_line([1.0, 2.0, 3.0]), min_bars=60)
    assert p.isna().all()                          # 全 <min_bars → 全 NaN(不报错)


# ---- classify 五档阈值 ----
def test_classify_thresholds():
    assert fg.classify(0) == "极度恐惧"
    assert fg.classify(24.9) == "极度恐惧"
    assert fg.classify(25) == "恐惧"
    assert fg.classify(44.9) == "恐惧"
    assert fg.classify(45) == "中性"
    assert fg.classify(55) == "中性"
    assert fg.classify(55.1) == "贪婪"
    assert fg.classify(75) == "贪婪"
    assert fg.classify(75.1) == "极度贪婪"
    assert fg.classify(100) == "极度贪婪"
    assert fg.classify(float("nan")) == "—"
    assert fg.classify(None) == "—"


# ---- 单成分方向(高分=贪婪;波动率反转)----
def test_momentum_component_direction():
    assert fg.momentum_component(_flat_then(100, 180, [130])).iloc[-1] > 80   # 上突 → 贪婪
    assert fg.momentum_component(_flat_then(100, 180, [70])).iloc[-1] < 20    # 下破 → 恐惧


def test_momentum_component_short_series_empty():
    assert fg.momentum_component(_line([100.0] * 50)).empty                    # ≤MA60 → 空


def test_turnover_component_direction():
    s = _line([100e8] * 360 + [500e8])                                          # 末根天量(MA250 需 ≥250 根)
    assert fg.turnover_component(s).iloc[-1] > 80


def test_volatility_component_reversal():
    calm = _line([100.0] * 200)                                                 # 全平 → vol≈0
    assert fg.volatility_component(calm).iloc[-1] > 80                          # 低波 → 高分(贪婪)
    noisy = _line([100.0] * 180 + [100 + ((i % 2) * 40 - 20) for i in range(20)])
    assert fg.volatility_component(noisy).iloc[-1] < 30                         # 末段高波 → 反转为低分(恐惧)


def test_valuation_component_direction():
    assert fg.valuation_component(_line(np.linspace(1, 5, 200))).iloc[-1] > 80  # PB 升 → 贪婪


def test_leverage_component_direction():
    fin = _flat_then(1000.0, 180, list(np.linspace(1000, 1300, 30)))            # 末段杠杆扩张
    assert fg.leverage_component(fin).iloc[-1] > 50


# ---- 合成:等权 / 缺失不减分母 / 前填 / 裁剪 ----
def test_fear_greed_series_equal_weight_mean():
    idx = pd.date_range("2020-01-01", periods=5, freq="B")
    comps = {n: pd.Series([50, 60, 70, 80, 90], index=idx, dtype=float) for n in fg._COMPONENTS}
    s = fg.fear_greed_series(comps)
    assert len(s) == 5
    assert abs(s.iloc[0] - 50.0) < 1e-9
    assert abs(s.iloc[-1] - 90.0) < 1e-9


def test_fear_greed_series_missing_component_not_in_denominator():
    # 只给 2 个成分 → 复合 = 二者均值(不是 ÷5)
    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    comps = {"momentum": pd.Series([0.0, 0.0, 0.0], index=idx),
             "valuation": pd.Series([100.0, 100.0, 100.0], index=idx)}
    assert abs(fg.fear_greed_series(comps).iloc[0] - 50.0) < 1e-9


def test_fear_greed_series_ffill_missing_day():
    idx = pd.date_range("2020-01-01", periods=3, freq="B")
    comps = {"momentum": pd.Series([40.0, 60.0, np.nan], index=idx),
             "valuation": pd.Series([60.0, 60.0, 60.0], index=idx)}
    assert abs(fg.fear_greed_series(comps).iloc[2] - 60.0) < 1e-9   # momentum 前填 60


def test_fear_greed_series_empty_and_clip():
    assert fg.fear_greed_series({}).empty
    idx = pd.date_range("2020-01-01", periods=2, freq="B")
    s = fg.fear_greed_series({"momentum": pd.Series([200.0, -50.0], index=idx)})
    assert s.iloc[0] == 100.0 and s.iloc[1] == 0.0


def test_fear_greed_series_string_dates_latest_is_last():
    # store 返回字符串日期;成分长度不一 → 合成 iloc[-1] 必须是最新日(防 concat 未排序 bug)
    idx_a = ["2026-08-09", "2026-08-10", "2026-08-11"]
    idx_b = ["2026-08-10", "2026-08-11"]      # valuation 少一天
    comps = {"momentum": pd.Series([40.0, 40.0, 80.0], index=idx_a),
             "valuation": pd.Series([60.0, 60.0], index=idx_b)}
    s = fg.fear_greed_series(comps)
    assert str(s.index[-1]) == "2026-08-11"
    # 末日:momentum=80, valuation=60(ffill 到 08-11) → 70
    assert abs(s.iloc[-1] - 70.0) < 1e-9
