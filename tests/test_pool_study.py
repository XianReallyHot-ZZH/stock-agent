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


# ================= V8 高业绩池回放核心(collect_events / replay / window_returns / aggregate) =================
import pandas as pd  # noqa: E402

from stockagent.pool.study import (  # noqa: E402
    aggregate,
    collect_events,
    gate_at_event,
    next_trading_day_open,
    replay_membership,
    window_returns,
)

_CFG = {"floor_np_yoy": 50.0, "floor_rev_yoy": 20.0, "peg_max": 10.0}


def _fc_frame(rows):
    return pd.DataFrame(rows, columns=["yoy", "type", "announce_date"])


def _exac_frame(rows):
    return pd.DataFrame(rows, columns=["np_yoy", "rev_yoy", "announce_date"])


def _frames():
    return {
        "20251231": {
            "forecast": _fc_frame([(80.0, "预增", "2026-01-20")]),
            "express": _exac_frame([(75.0, 30.0, "2026-02-25")]),
            "actual": _exac_frame([(70.0, 28.0, "2026-03-30")]),
        },
        "20260630": {
            "forecast": _fc_frame([(90.0, "预增", "2026-07-14")]),
            "express": _exac_frame([]),
            "actual": _exac_frame([(85.0, 40.0, "2026-08-20")]),
        },
    }


def _price_df(start, vals):
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(vals))]
    return pd.DataFrame({"open": [v * 0.999 for v in vals], "high": vals,
                         "low": vals, "close": vals, "volume": 1.0, "amount": 1.0},
                        index=idx)


def test_collect_events_sorted_and_filtered():
    frames = {
        "20260630": {
            "forecast": pd.DataFrame(
                {"yoy": [90.0, 60.0], "type": ["预增", "预增"],
                 "announce_date": ["2026-07-14", ""]},          # 空 announce → 丢
                index=["600519", "873001"]),                    # 北交所 → 丢
            "express": pd.DataFrame(
                {"np_yoy": [75.0], "rev_yoy": [30.0], "announce_date": ["2026-07-20"]},
                index=["600519"]),
            "actual": pd.DataFrame(
                {"np_yoy": [70.0], "rev_yoy": [28.0], "announce_date": ["2026-08-20"]},
                index=["600519"]),
        }
    }
    evs = collect_events(frames)
    assert [e["ring"] for e in evs] == ["forecast", "express", "actual"]
    assert all(e["code"] == "600519" for e in evs)
    assert all(e["date"] <= "2026-08-20" for e in evs)
    assert [e["date"] for e in evs] == sorted(e["date"] for e in evs)


def test_gate_at_event_arms():
    ev = {"code": "600519", "date": "2026-08-20", "period": "20260630",
          "ring": "actual", "np_yoy": 85.0, "rev_yoy": 40.0, "type": None}
    price = _price_df("2026-01-02", np.linspace(100, 150, 160))
    shares = {"600519": 1e8}
    np_abs = {"600519": {"20251231": 50e8, "20250630": 20e8, "20260630": 40e8}}
    # TTM = 50+40−20 = 70 亿; PE=150×1e8/70e8≈2.14; PEG=2.14/85≈0.025 ≤10 → 过
    g = gate_at_event(ev, _CFG, lambda c: price, shares, np_abs, {}, arm="full")
    assert g["passed"] and g["valuation_pass"]
    assert abs(g["peg"] - (150 * 1e8 / 70e8) / 85) < 1e-9
    # no_valuation 臂: 直接过
    g2 = gate_at_event(ev, _CFG, lambda c: price, shares, np_abs, {}, arm="no_valuation")
    assert g2["valuation_pass"]
    # 地板不过(np_yoy 30)
    ev_low = {**ev, "np_yoy": 30.0}
    g3 = gate_at_event(ev_low, _CFG, lambda c: price, shares, np_abs, {}, arm="full")
    assert not g3["passed"]


def test_gate_at_event_risk_arm_dual_high():
    ev = {"code": "601988", "date": "2026-08-20", "period": "20260630",
          "ring": "express", "np_yoy": 90.0, "rev_yoy": 50.0, "type": None}
    bal = {("20260630", "601988"): {"cash": 20.0, "total_assets": 100.0, "debt_ratio": 0.5}}
    price = _price_df("2026-01-02", np.linspace(5, 6, 160))
    g = gate_at_event(ev, {**_CFG, "risk": {"dual_high_cash_pct": 0.15,
                                            "dual_high_debt_ratio": 0.40}},
                      lambda c: price, {"601988": 1e9}, {}, bal, arm="full")
    assert g["risk_red"]
    g2 = gate_at_event(ev, {**_CFG, "risk": {"dual_high_cash_pct": 0.15,
                                             "dual_high_debt_ratio": 0.40}},
                       lambda c: price, {"601988": 1e9}, {}, bal, arm="no_risk")
    assert not g2["risk_red"]


def test_replay_membership_enter_exit():
    """环事件流: 预告过门→入;正式报砸(增速滑落)→出。"""
    frames = {"20260630": {
        "forecast": pd.DataFrame({"yoy": [90.0], "type": ["预增"],
                                  "announce_date": ["2026-07-14"]}, index=["600519"]),
        "express": pd.DataFrame(),
        "actual": pd.DataFrame({"np_yoy": [10.0], "rev_yoy": [5.0],
                                "announce_date": ["2026-08-20"]}, index=["600519"]),
    }}
    evs = collect_events(frames)
    price = _price_df("2026-01-02", np.linspace(100, 150, 160))
    ivs = replay_membership(evs, {**_CFG, "peg_max": 10.0}, lambda c: price,
                            {"600519": 1e8}, {"600519": {"20251231": 50e8,
                                                         "20250630": 20e8,
                                                         "20260630": 40e8}}, {}, arm="full")
    assert len(ivs) == 1
    assert ivs[0]["entry_date"] == "2026-07-14" and ivs[0]["exit_date"] == "2026-08-20"


def test_next_trading_day_open():
    df = _price_df("2026-01-02", [100.0, 110.0, 120.0])
    d, o = next_trading_day_open(df, "2026-01-02")   # 01-02 周五 → 下个交易日 01-05
    assert d == "2026-01-05" and abs(o - 110.0 * 0.999) < 1e-9
    assert next_trading_day_open(df, "2026-01-06") is None   # 真数据末端(01-06 是最后一行)


def test_window_returns_t_plus_one_legs():
    """入/出=事件次日开盘;持有到窗口末=最后 close。"""
    df = _price_df("2026-01-02", [100.0, 110.0, 120.0, 130.0, 140.0])
    iv = [{"code": "X", "period": "p", "entry_date": "2026-01-02",
           "exit_date": "2026-01-05"}]     # 入=01-05 开盘(110×0.999),出=01-06 开盘(120×0.999)
    r = window_returns(iv, {"X": df}, {"idx": pd.Series([1.0, 1.1], index=["2026-01-02",
                                                                           "2026-01-06"])},
                       "2026-01-02", "2026-01-06")
    assert abs(r["pool"] - (120.0 / 110.0 - 1)) < 1e-9
    assert abs(r["indices"]["idx"] - 0.1) < 1e-9


def test_aggregate_win_rates():
    wins = [
        {"pool": 0.10, "indices": {"a": 0.05, "b": 0.08}},     # 全胜
        {"pool": 0.02, "indices": {"a": 0.05, "b": 0.01}},     # 输 a
        {"pool": None, "indices": {"a": 0.05}},                # 无池收益 → 不计
    ]
    agg = aggregate(wins)
    assert agg["n_windows"] == 2
    assert abs(agg["win_rate_all"] - 0.5) < 1e-9
    assert abs(agg["per_index"]["a"]["beat_rate"] - 0.5) < 1e-9
    assert abs(agg["per_index"]["b"]["beat_rate"] - 1.0) < 1e-9
    assert abs(agg["median_pool"] - 0.06) < 1e-9
