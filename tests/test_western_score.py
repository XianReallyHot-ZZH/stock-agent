"""Scoring unit tests: direction/baseline/edge logic + settle round-trip on seeded data."""
import pandas as pd
import pytest

from stockagent.data.store import Store
from stockagent.western_macro import score


def test_direction():
    assert score._direction(100, 105) == "up"
    assert score._direction(100, 95) == "down"
    assert score._direction(100, 100.5) == "flat"  # <1%


def test_parse_date():
    assert score._parse_date("2026-12-31") == "2026-12-31"
    assert score._parse_date("2026年9月美联储会议") is None
    assert score._parse_date(None) is None


def _seed_gold(store, dates, closes):
    df = pd.DataFrame([{"source": "fut", "symbol": "GC", "date": d, "close": c}
                       for d, c in zip(dates, closes)])
    store.upsert_western_macro(df, source_tag="seed")


def test_settle_direction_hit_with_edge(tmp_path):
    # Gold claimed UP over a window where it actually rose, but the PRIOR trend was DOWN → edge.
    st = Store(tmp_path / "t.sqlite")
    dates = [f"2025-01-{d:02d}" for d in range(1, 31)]
    closes = [100 - i * 0.5 for i in range(15)] + [92.5 + i * 1.0 for i in range(15)]  # down then up
    _seed_gold(st, dates, closes)
    claim = {"uid": "c1", "episode_date": "2025-01-16", "asset": "黄金",
             "claim_type": "direction", "direction": "up", "horizon": "2025-01-30",
             "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res is not None
    assert res["actual_direction"] == "up"
    assert res["hit"] == 1
    assert res["baseline_hit"] == 0  # prior window was down
    assert res["edge"] == 1


def test_settle_direction_hit_no_edge_when_riding_trend(tmp_path):
    # Gold claimed UP, rose, AND prior trend was also up → hit but baseline also hit → NO edge.
    st = Store(tmp_path / "t.sqlite")
    dates = [f"2025-01-{d:02d}" for d in range(1, 31)]
    closes = [90 + i * 0.5 for i in range(30)]  # steady up throughout
    _seed_gold(st, dates, closes)
    claim = {"uid": "c2", "episode_date": "2025-01-16", "asset": "黄金",
             "claim_type": "direction", "direction": "up", "horizon": "2025-01-30",
             "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res["hit"] == 1 and res["baseline_hit"] == 1 and res["edge"] == 0


def test_settle_range_inside_band_hits(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = [f"2025-06-{d:02d}" for d in range(1, 29)]
    closes = [78] * 28  # flat inside 70-90
    _seed_gold(st, dates, closes)
    claim = {"uid": "c3", "episode_date": "2025-06-01", "asset": "黄金",
             "claim_type": "range", "direction": "flat", "range_low": 70, "range_high": 90,
             "horizon": "2025-06-28", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-07-01")
    assert res["hit"] == 1 and res["edge"] == 1  # range correct + non-trend baseline


def test_settle_future_horizon_returns_none(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, ["2025-01-01", "2025-01-02"], [100, 101])
    claim = {"uid": "c4", "episode_date": "2025-01-01", "asset": "黄金",
             "claim_type": "direction", "direction": "up", "horizon": "2099-12-31",
             "is_primary": 1}
    assert score.settle_claim(claim, st, asof="2025-02-01") is None


def test_settle_scenario_alt_branch_skipped(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, ["2025-01-01", "2025-01-30"], [100, 110])
    claim = {"uid": "c5", "episode_date": "2025-01-01", "asset": "黄金",
             "claim_type": "scenario", "direction": "down", "horizon": "2025-01-30",
             "is_primary": 0}  # alt branch → must be skipped
    assert score.settle_claim(claim, st, asof="2025-02-01") is None


def test_settle_level_up_reached(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, [f"2025-01-{d:02d}" for d in range(1, 31)], [100 + i * 2 for i in range(30)])  # 100→158
    claim = {"uid": "L1", "episode_date": "2025-01-01", "asset": "黄金", "claim_type": "level",
             "direction": "up", "level_value": 150, "horizon": "2025-01-30", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res and res["hit"] == 1 and res["edge"] == 1   # 摸到 150


def test_settle_level_up_not_reached(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, [f"2025-01-{d:02d}" for d in range(1, 31)], [100 + i * 0.5 for i in range(30)])  # 100→114
    claim = {"uid": "L2", "episode_date": "2025-01-01", "asset": "黄金", "claim_type": "level",
             "direction": "up", "level_value": 150, "horizon": "2025-01-30", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res and res["hit"] == 0                         # 最高 114 < 150


def test_settle_level_support_held(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, [f"2025-01-{d:02d}" for d in range(1, 31)], [100] * 30)   # 平在 100,守住 90
    claim = {"uid": "L3", "episode_date": "2025-01-01", "asset": "黄金", "claim_type": "level",
             "direction": "flat", "level_value": 90, "horizon": "2025-01-30", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res and res["hit"] == 1                         # 最低 100 >= 90


def test_settle_level_down_touched_but_actual_up_miss(tmp_path):
    # "跌到105":中途插针摸到102,但收在130(整体涨)→ 方向不符 → 未中(防假命中)
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, ["2025-01-01", "2025-01-15", "2025-01-30"], [110, 102, 130])
    claim = {"uid": "L4", "episode_date": "2025-01-01", "asset": "黄金", "claim_type": "level",
             "direction": "down", "level_value": 105, "horizon": "2025-01-30", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-02-01")
    assert res and res["hit"] == 0                         # 摸到105但整体涨 → 未中


def test_settle_direction_path_correction(tmp_path):
    # "回调":100→92→101。端点+1%(震荡),但中途跌8%→路径感知下方向"跌"应命中(端点法会误判未中)
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, ["2024-07-12", "2024-08-01", "2024-08-05", "2024-08-31"], [100, 99, 92, 101])
    claim = {"uid": "P1", "episode_date": "2024-07-12", "asset": "黄金", "claim_type": "direction",
             "direction": "down", "horizon": "2024-08-31", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2024-09-01")
    assert res and res["hit"] == 1 and res["actual_direction"] == "down"


def test_settle_negative_series_spread(tmp_path):
    # 2s10s 利差(可负):从 -0.27(倒挂)升到 +0.10(陡峭化)→ 方向"涨"应命中(旧 start<=0 守卫会拒)
    st = Store(tmp_path / "t.sqlite")
    df = pd.DataFrame([{"source": "ust", "symbol": "US2S10S", "date": d, "close": v}
                       for d, v in [("2024-07-12", -0.27), ("2024-09-30", -0.10), ("2024-12-31", 0.10)]])
    st.upsert_western_macro(df, source_tag="seed")
    claim = {"uid": "N1", "episode_date": "2024-07-12", "asset": "2s10s", "claim_type": "direction",
             "direction": "up", "horizon": "2024-12-31", "is_primary": 1}
    res = score.settle_claim(claim, st, asof="2025-01-01")
    assert res and res["hit"] == 1 and res["actual_direction"] == "up"
