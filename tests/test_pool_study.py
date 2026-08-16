"""event-study 纯核心: 事件定义(冷却/防前视) + 前向收益 + 臂统计 + 诚实结论模板。"""
import numpy as np
import pandas as pd

from stockagent.pool.study import (
    arm_stats,
    baseline_stats,
    conclusion_text,
    deviation_events,
    forward_returns,
)


def _series(vals, start="2024-01-02"):
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(vals))]
    return pd.Series(vals, index=idx)


def _crash(n_base=150, n_crash=40):
    base = np.linspace(10.0, 20.0, n_base) * (1 + 0.004 * np.sin(np.arange(n_base)))
    vals = np.concatenate([base, base[-1] * np.linspace(1.0, 0.7, n_crash)])
    return _series(vals)


# ---------- deviation_events ----------
def test_events_found_with_cooldown():
    evs = deviation_events(_crash(), "600519")
    assert 0 < len(evs) <= 2                 # 一段阴跌磨底只算一次(120 日冷却)
    for e in evs:
        assert set(e) >= {"code", "date", "pos"}
    # 事件日期升序
    assert [e["date"] for e in evs] == sorted(e["date"] for e in evs)


def test_events_none_on_rising_series():
    i = np.arange(150)
    s = _series(10.0 * np.exp(2e-4 * i ** 1.3))
    assert deviation_events(s, "X") == []


def test_events_min_history_gate():
    """历史 < min_above_days+21 → 无事件(分位尺子太短)。"""
    s = _series(np.concatenate([np.linspace(10, 20, 60), np.linspace(20, 12, 30)]))
    assert deviation_events(s, "X") == []


# ---------- forward_returns ----------
def test_forward_returns_exact():
    s = _series([100.0, 110.0, 105.0, 120.0, 130.0, 140.0, 150.0])
    fr = forward_returns(s, s.index[1], windows=(2, 5))
    assert abs(fr["ret_2"] - (120.0 / 110.0 - 1)) < 1e-9
    assert abs(fr["ret_5"] - (150.0 / 110.0 - 1)) < 1e-9


def test_forward_returns_partial_window_none():
    s = _series([100.0, 110.0, 105.0])
    fr = forward_returns(s, s.index[1], windows=(2,))
    assert fr["ret_2"] is None


def test_forward_returns_missing_date_rolls_forward():
    """事件日(公告日)非交易日 → 就近向后首个存在日。2024-01-05(周五)+1=周六,
    向后滚到周一 01-08(index[4], 130.0)。"""
    s = _series([100.0, 110.0, 105.0, 120.0, 130.0, 140.0, 150.0])
    missing = "2024-01-06"   # 周六,不在 bdate 索引内
    fr = forward_returns(s, missing, windows=(1,))
    assert abs(fr["ret_1"] - (140.0 / 130.0 - 1)) < 1e-9


# ---------- arm_stats / baseline ----------
def test_arm_stats_win_rate_median():
    evs = [{"ret_5": 0.1, "ret_20": 0.2}, {"ret_5": -0.05, "ret_20": 0.3},
           {"ret_5": 0.07, "ret_20": None}]
    st = arm_stats(evs, (5, 20))
    assert st[5]["n"] == 3 and abs(st[5]["win_rate"] - 2 / 3) < 1e-9
    assert st[20]["n"] == 2 and st[20]["win_rate"] == 1.0


def test_baseline_stats_sampling():
    closes = {"A": _series(np.linspace(100, 200, 100)), "B": _series(np.linspace(100, 90, 100))}
    bl = baseline_stats(closes, (5,), sample_step=10)
    assert bl[5]["n"] > 0
    assert 0.0 <= bl[5]["win_rate"] <= 1.0


# ---------- conclusion_text ----------
def test_conclusion_honest_no_edge():
    arms = {"raw": {20: {"n": 100, "win_rate": 0.52, "median_ret": 0.01}},
            "企稳": {20: {"n": 50, "win_rate": 0.53, "median_ret": 0.012}}}
    baseline = {20: {"n": 5000, "win_rate": 0.51, "median_ret": 0.008}}
    txt = conclusion_text(arms, baseline, (5, 20, 60), focus=20)
    assert "52%" in txt and "基线 51%" in txt
    assert "无 edge" in txt            # 最好臂仅 +2pp → 持平措辞


def test_conclusion_detects_separation():
    arms = {"护栏+企稳": {20: {"n": 80, "win_rate": 0.65, "median_ret": 0.05}}}
    baseline = {20: {"n": 5000, "win_rate": 0.50, "median_ret": 0.005}}
    txt = conclusion_text(arms, baseline, (20,), focus=20)
    assert "+15pp" in txt and "温和分离" in txt


def test_conclusion_detects_worse_than_baseline():
    arms = {"raw": {20: {"n": 60, "win_rate": 0.40, "median_ret": -0.02}}}
    baseline = {20: {"n": 5000, "win_rate": 0.52, "median_ret": 0.005}}
    txt = conclusion_text(arms, baseline, (20,), focus=20)
    assert "无 edge" in txt
