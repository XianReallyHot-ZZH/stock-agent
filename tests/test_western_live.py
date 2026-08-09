"""宏观 call 实时确认单测: 方向/到期/区间/点位/无数据 + 总览聚合。风格镜像 test_western_score.py。"""
import pandas as pd

from stockagent.data.store import Store
from stockagent.western_macro.live import (
    DIVERGING, MANUAL, NO_SERIES, ON_TRACK, RES_HIT, RES_EDGE, RES_MISS,
    STALLED, ALT_BRANCH, live_call_confirmation, live_confirmation_overview,
)


def _dates(n, start="2025-01-01"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _seed(store, closes, asset=("fut", "GC"), start="2025-01-01"):
    source, symbol = asset
    dates = _dates(len(closes), start)
    df = pd.DataFrame([{"source": source, "symbol": symbol, "date": d, "close": float(c)}
                       for d, c in zip(dates, closes)])
    store.upsert_western_macro(df, source_tag="seed")
    return dates


# ---- 方向 (未到期) ----
def test_direction_on_track(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])      # 100→129
    claim = {"uid": "d1", "episode_date": dates[5], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": "2099-12-31", "statement": "x"}
    r = live_call_confirmation(claim, st, asof=dates[-1])
    assert r["status"] == ON_TRACK
    assert r["progress"] > 0


def test_direction_diverging(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [130 - i for i in range(30)])      # 130→101 下行
    claim = {"uid": "d2", "episode_date": dates[5], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": "2099-12-31"}
    r = live_call_confirmation(claim, st, asof=dates[-1])
    assert r["status"] == DIVERGING


def test_direction_stalled(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100] * 30)                         # 平
    claim = {"uid": "d3", "episode_date": dates[5], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": "2099-12-31"}
    r = live_call_confirmation(claim, st, asof=dates[-1])
    assert r["status"] == STALLED


def test_direction_down_on_track(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [130 - i for i in range(30)])       # 下行
    claim = {"uid": "d4", "episode_date": dates[5], "asset": "黄金", "claim_type": "direction",
             "direction": "down", "horizon": "2099-12-31"}
    assert live_call_confirmation(claim, st, asof=dates[-1])["status"] == ON_TRACK


# ---- 到期 (复用结算) ----
def _sett(uid, hit, edge):
    return {"claim_uid": uid, "actual_direction": "up", "actual_value": 120.0,
            "hit": hit, "baseline_hit": 0, "edge": edge, "method": "auto",
            "settled_at": "2025-02-01", "note": "x"}


def test_resolved_hit(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])
    claim = {"uid": "r1", "episode_date": dates[3], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": dates[20]}
    assert live_call_confirmation(claim, st, _sett("r1", 1, 0), asof=dates[-1])["status"] == RES_HIT


def test_resolved_edge(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])
    claim = {"uid": "r2", "episode_date": dates[3], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": dates[20]}
    assert live_call_confirmation(claim, st, _sett("r2", 1, 1), asof=dates[-1])["status"] == RES_EDGE


def test_resolved_miss(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])
    claim = {"uid": "r3", "episode_date": dates[3], "asset": "黄金", "claim_type": "direction",
             "direction": "up", "horizon": dates[20]}
    assert live_call_confirmation(claim, st, _sett("r3", 0, 0), asof=dates[-1])["status"] == RES_MISS


def test_resolved_manual_when_no_settlement(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])
    claim = {"uid": "r4", "episode_date": dates[3], "asset": "黄金", "claim_type": "level",
             "direction": "up", "horizon": dates[20]}    # level 到期需人工
    assert live_call_confirmation(claim, st, None, asof=dates[-1])["status"] == MANUAL


# ---- 情景备选 ----
def test_scenario_alt_branch_skipped(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i for i in range(30)])
    claim = {"uid": "s1", "episode_date": dates[3], "asset": "黄金", "claim_type": "scenario",
             "direction": "up", "horizon": "2099-12-31", "is_primary": 0}
    assert live_call_confirmation(claim, st, asof=dates[-1])["status"] == ALT_BRANCH


# ---- 区间 ----
def test_range_inside_on_track(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100] * 30)
    claim = {"uid": "rg1", "episode_date": dates[3], "asset": "黄金", "claim_type": "range",
             "direction": "flat", "range_low": 90, "range_high": 110, "horizon": "2099-12-31"}
    assert live_call_confirmation(claim, st, asof=dates[-1])["status"] == ON_TRACK


def test_range_outside_diverging(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i * 2 for i in range(30)])   # 涨到 158 → 出区间
    claim = {"uid": "rg2", "episode_date": dates[3], "asset": "黄金", "claim_type": "range",
             "direction": "flat", "range_low": 90, "range_high": 110, "horizon": "2099-12-31"}
    assert live_call_confirmation(claim, st, asof=dates[-1])["status"] == DIVERGING


# ---- 点位 ----
def test_level_up_reached(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i * 2 for i in range(30)])   # 100→158 触及 150
    claim = {"uid": "L1", "episode_date": dates[3], "asset": "黄金", "claim_type": "level",
             "direction": "up", "level_value": 150, "horizon": "2099-12-31"}
    r = live_call_confirmation(claim, st, asof=dates[-1])
    assert r["status"] == ON_TRACK and r["progress"] == 1.0


def test_level_up_moving_toward(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i * 0.5 for i in range(30)])  # 100→114.5, 朝 150 走未触
    claim = {"uid": "L2", "episode_date": dates[3], "asset": "黄金", "claim_type": "level",
             "direction": "up", "level_value": 150, "horizon": "2099-12-31"}
    r = live_call_confirmation(claim, st, asof=dates[-1])
    assert r["status"] == ON_TRACK and 0 < r["progress"] < 1


# ---- 无数据 ----
def test_no_series(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    claim = {"uid": "n1", "episode_date": "2025-01-05", "asset": "半导体", "claim_type": "direction",
             "direction": "up", "horizon": "2099-12-31"}   # 半导体 manual-only, 无 series
    assert live_call_confirmation(claim, st)["status"] == NO_SERIES


# ---- 总览聚合 ----
def test_overview_aggregation(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    dates = _seed(st, [100 + i * 0.5 for i in range(30)])  # 上行
    claims = [
        {"uid": "o1", "episode_date": dates[3], "asset": "黄金", "claim_type": "direction",
         "direction": "up", "horizon": "2099-12-31", "state": "draft", "statement": "a"},
        {"uid": "o2", "episode_date": dates[4], "asset": "黄金", "claim_type": "direction",
         "direction": "down", "horizon": "2099-12-31", "state": "draft", "statement": "b"},
    ]
    st.upsert_wm_claims(claims)
    ov = live_confirmation_overview(st, asof=dates[-1])
    assert ov["open_total"] == 2
    assert ov["counts"][ON_TRACK] >= 1          # up 在上行 → 兑现中
    assert ov["counts"][DIVERGING] >= 1         # down 在上行 → 背离
    g = ov["by_asset"]["黄金"]
    assert g["open"] == 2 and g["net_direction"] == "分歧"
    assert g["avg_ret"] is not None
