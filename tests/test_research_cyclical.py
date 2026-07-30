"""Tests for research.cyclical — 周期反转筛子(只读,业绩×回撤×时效)。纯函数 + snapshot 装配。"""
import numpy as np
import pandas as pd

from stockagent.research import cyclical as cyc


# ---- cyclical_reversal_score(纯函数)----
def test_reversal_high_yoy_deep_drawdown_fresh():
    # yoy=150%(cap→1.0) × 回撤60%(cap→1.0) × 20天新鲜(0.5^(20/120)≈0.89)
    s = cyc.cyclical_reversal_score(yoy=1.5, drawdown=-0.6, days_since_report=20)
    assert s["valid"] is True
    assert s["yoy_factor"] == 1.0 and s["drawdown_factor"] == 1.0
    assert 80 < s["score"] < 90                       # ≈89


def test_reversal_stale_report_decays():
    fresh = cyc.cyclical_reversal_score(1.5, -0.6, 20)["score"]
    stale = cyc.cyclical_reversal_score(1.5, -0.6, 240)["score"]   # 0.5^(240/120)=0.25
    assert stale < 30 and stale < fresh               # 时效衰减显著


def test_reversal_negative_yoy_zero_factor():
    # 负增长不是"反转" → yoy_factor=0 → score=0(仍 valid,只是不得分)
    s = cyc.cyclical_reversal_score(yoy=-0.5, drawdown=-0.6, days_since_report=20)
    assert s["valid"] is True
    assert s["yoy_factor"] == 0.0
    assert s["score"] == 0.0


def test_reversal_drawdown_capped():
    # 回撤 90% → drawdown_factor cap 在 1.0(不超额奖励更深的跌)
    s = cyc.cyclical_reversal_score(yoy=0.5, drawdown=-0.9, days_since_report=10)
    assert s["drawdown_factor"] == 1.0


def test_reversal_invalid_inputs():
    assert cyc.cyclical_reversal_score(np.nan, -0.5, 20)["valid"] is False
    assert cyc.cyclical_reversal_score(0.5, np.nan, 20)["valid"] is False
    assert cyc.cyclical_reversal_score(0.5, -0.5, np.nan)["valid"] is False


# ---- cyclical_reversal_snapshot(装配)----
def test_reversal_snapshot_assembles():
    # close: 10→12→8,回撤 = 8/12-1 ≈ -33%;earn: yoy 80%(百分数口径),报告期 20260630;asof 2026-07-30(30天)
    close = pd.Series([10.0, 12.0, 8.0], index=["2026-07-28", "2026-07-29", "2026-07-30"])
    earn = {"weighted_yoy": 80.0, "report_period": "20260630"}   # 百分数(80=80%)
    snap = cyc.cyclical_reversal_snapshot("512400", close, earn,
                                          {"research": {"cyclical_reversal": {}}}, asof="2026-07-30")
    assert snap["valid"] is True
    assert snap["report_period"] == "20260630"
    assert snap["symbol"] == "512400"
    assert abs(snap["drawdown"] - (8 / 12 - 1)) < 1e-9
    assert 0 < snap["reversal_score"] < 100


def test_reversal_snapshot_missing_earnings():
    # 无业绩数据 → yoy NaN → valid False,不崩
    close = pd.Series([10.0, 8.0], index=["2026-07-29", "2026-07-30"])
    snap = cyc.cyclical_reversal_snapshot("512400", close, None,
                                          {"research": {}}, asof="2026-07-30")
    assert snap["valid"] is False
    assert snap["report_period"] is None
