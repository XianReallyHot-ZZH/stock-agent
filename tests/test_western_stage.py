"""黄金阶段定位器单测: 阶段决策树 / 边序 / 无前视 / 词映射 / 一致性 / 驱动 / Store 往返。

classify_stage 用构造的 diag dict 直接测 (每阶段干净命中); 集成测用合成 close 序列种 Store。
风格镜像 tests/test_western_score.py。
"""
import numpy as np
import pandas as pd
import pytest

from stockagent.data.store import Store
from stockagent.tracker.indicators import deviation_extremes, deviation_series
from stockagent.western_macro import stage
from stockagent.western_macro.stage import (
    classify_stage, confidence_band, driver_confirmation, evaluate_gold_stage,
    gold_stage_snapshot, stage_agreement, stage_signal_strength, statement_to_stage,
    _rolling_dev_pct,
)


# ---- helpers ----
def _diag(above_ma, ma_trend_up, grade=0, label="中性", choppy=False, pvsma=0.0):
    return {"trend": {"above_ma": above_ma, "ma_trend_up": ma_trend_up,
                      "price_vs_ma_pct": pvsma, "valid": True},
            "deviation": {"pct": 0.5, "valid": True},
            "breakout": {"direction": "none", "grade": grade, "label": label, "valid": True},
            "cross": None, "choppy": choppy}


def _dates(n, start="2024-01-01"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _series(closes, start="2024-01-01"):
    return pd.Series(closes, index=_dates(len(closes), start), dtype=float)


def _seed_gold(store, closes, start="2024-01-01"):
    dates = _dates(len(closes), start)
    df = pd.DataFrame([{"source": "fut", "symbol": "GC", "date": d, "close": float(c)}
                       for d, c in zip(dates, closes)])
    store.upsert_western_macro(df, source_tag="seed")


# ============ A. 阶段分类 (每阶段) ============
def test_stage_bottoming():
    info = classify_stage(_diag(False, False), 0.10, None, False, 200)
    assert info["stage"] == "筑底/底部震荡"


def test_stage_early_rebound_fresh_upcross():
    info = classify_stage(_diag(True, False), 0.40, "up", False, 200)
    assert info["stage"] == "反弹初期"


def test_stage_early_rebound_above_flat_ma():
    info = classify_stage(_diag(True, False), 0.40, None, False, 200)
    assert info["stage"] == "反弹初期"


def test_stage_trend_up():
    info = classify_stage(_diag(True, True), 0.40, None, False, 200)
    assert info["stage"] == "趋势上行"


def test_stage_top_zone():
    info = classify_stage(_diag(True, True), 0.90, None, False, 200)
    assert info["stage"] == "头部区域"


def test_stage_pullback_fresh_downcross():
    info = classify_stage(_diag(True, True), 0.50, "down", False, 200)
    assert info["stage"] == "回调下跌"


def test_stage_pullback_ongoing():
    info = classify_stage(_diag(False, False), 0.30, None, False, 200)
    assert info["stage"] == "回调下跌"


def test_stage_data_insufficient():
    info = classify_stage(_diag(True, True), 0.5, None, False, 50)
    assert info["stage"] == "数据不足"
    assert info["valid"] is False
    assert stage_signal_strength(info, 50) == 0.0


def test_stage_choppy_discounts_strength_not_label():
    info = classify_stage(_diag(False, False, choppy=True), 0.10, None, True, 200)
    assert info["stage"] == "筑底/底部震荡"      # 标签不变
    assert info["noise"] is True
    assert stage_signal_strength(info, 200) <= 0.30


# ============ B. 边序 ============
def test_fresh_downcross_beats_topzone():
    # 上MA + 区间顶, 但 fresh 下穿 → 回调 (priority)
    info = classify_stage(_diag(True, True), 0.90, "down", False, 200)
    assert info["stage"] == "回调下跌"


def test_fresh_upcross_is_rebound_not_trend():
    # fresh 上穿 + MA 仍平 → 反弹 (rule3 要 ma_trend_up)
    info = classify_stage(_diag(True, False), 0.40, "up", False, 200)
    assert info["stage"] == "反弹初期"
    # 对照: 无 fresh, MA 上行 → 趋势
    info2 = classify_stage(_diag(True, True), 0.40, None, False, 200)
    assert info2["stage"] == "趋势上行"


def test_strength_components():
    info = classify_stage(_diag(True, True, grade=3), 0.90, "up", False, 200)
    assert info["stage"] == "头部区域"
    assert stage_signal_strength(info, 200) > 0.90   # 极值+穿越+grade3


# ============ C. 无前视 ============
def test_rolling_dev_pct_matches_truncated_extremes():
    closes = [200 - 0.5 * i for i in range(300)]
    s = _series(closes)
    dev = deviation_series(s, 60)
    rdp = _rolling_dev_pct(dev, 252)
    ep = s.index[200]
    ex = deviation_extremes(s[s.index <= ep], 60, lookback=252)
    assert ex["valid"]
    assert abs(rdp.loc[ep] - ex["pct"]) < 1e-9


def test_snapshot_respects_asof(tmp_path):
    closes = [200 - 0.5 * i for i in range(200)]
    st_full = Store(tmp_path / "full.sqlite")
    _seed_gold(st_full, closes)
    dates = _dates(200)
    mid = dates[149]
    snap_full = gold_stage_snapshot(st_full, asof=mid)
    # 另一库只存到 mid (前 150 根) → 末值快照应一致
    st_trunc = Store(tmp_path / "trunc.sqlite")
    _seed_gold(st_trunc, closes[:150])
    snap_trunc = gold_stage_snapshot(st_trunc, asof=None)
    assert snap_full["valid"] and snap_trunc["valid"]
    assert snap_full["stage"] == snap_trunc["stage"]
    assert snap_full["evidence"]["n_bars"] == 150


# ============ D. 词→阶段映射 ============
def test_statement_bottoming():
    assert statement_to_stage("黄金接近底部,仍未站稳4250") == "筑底/底部震荡"


def test_statement_top_zone():
    assert statement_to_stage("黄金阶段涨幅已满足,提示风险") == "头部区域"


def test_statement_pullback():
    assert statement_to_stage("黄金短期有回调") == "回调下跌"


def test_statement_rebound_after_pullback():
    # 回调是路径, 反弹是阶段 (direction up)
    assert statement_to_stage("回调后黄金将反弹", direction="up") == "反弹初期"


def test_statement_no_stage_words_returns_none():
    assert statement_to_stage("黄金9月突破4800") is None


def test_statement_conflict_no_direction_returns_none():
    # 反弹 + 回调, 无 direction → 歧义剔除
    assert statement_to_stage("黄金反弹后仍有回调") is None


def test_statement_empty_returns_none():
    assert statement_to_stage("") is None
    assert statement_to_stage(None) is None


# ============ E. 一致性度量 ============
def test_agreement_exact():
    assert stage_agreement("筑底/底部震荡", "筑底/底部震荡") == 1.0


def test_agreement_adjacent_cyclic_half():
    assert stage_agreement("筑底/底部震荡", "反弹初期") == 0.5
    assert stage_agreement("回调下跌", "筑底/底部震荡") == 0.5   # 回绕
    assert stage_agreement("头部区域", "回调下跌") == 0.5


def test_agreement_far_zero():
    assert stage_agreement("筑底/底部震荡", "头部区域") == 0.0
    assert stage_agreement("反弹初期", "头部区域") == 0.0


def test_confidence_band():
    assert confidence_band(0.50) == "强"
    assert confidence_band(0.30) == "中"
    assert confidence_band(0.10) == "弱"


# ============ F. 驱动确认 ============
def test_dxy_down_gold_supportive():
    res = driver_confirmation(_diag(False, False), None)   # DXY 弱, 曲线缺
    assert res["dxy_contrib"] == 0.5
    assert res["confirm_score"] > 0


def test_curve_steepening_supportive():
    res = driver_confirmation(None, _diag(True, True))     # 曲线陡峭化
    assert res["curve_contrib"] == 0.5
    assert res["confirm_score"] > 0


def test_mixed_drivers_score_near_zero():
    res = driver_confirmation(_diag(True, True), _diag(True, True))  # DXY强(-0.5)+曲线陡(+0.5)
    assert abs(res["confirm_score"]) < 1e-9


# ============ G. 集成 (Store 往返) ============
def test_snapshot_seeded_bottoming(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed_gold(st, [200 - 0.5 * i for i in range(200)])     # 单边下跌 → 末段最超卖 → 筑底
    snap = gold_stage_snapshot(st)
    assert snap["valid"] is True
    assert snap["stage"] == "筑底/底部震荡"
    assert snap["evidence"]["n_bars"] == 200
    assert snap["confidence"] <= stage.GOLD_DISCOUNT         # 折价封顶
    assert snap["drivers"]["confirm_score"] == 0.0           # DXY/曲线无数据 → 中性


def test_snapshot_invalid_when_no_gold(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    snap = gold_stage_snapshot(st)
    assert snap["valid"] is False
    assert "reason" in snap


def test_evaluate_gold_stage_seeded(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    closes = [200 - 0.5 * i for i in range(200)]
    _seed_gold(st, closes)
    dates = _dates(200)
    eps = [dates[150], dates[160], dates[170]]
    claims = [
        {"uid": "g1", "episode_date": eps[0], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金接近底部,仍在筑底", "direction": "down", "state": "draft"},
        {"uid": "g2", "episode_date": eps[1], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金探底过程中", "direction": "down", "state": "draft"},
        {"uid": "g3", "episode_date": eps[2], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金磨底,接近底部", "direction": "down", "state": "draft"},
    ]
    st.upsert_wm_claims(claims)
    res = evaluate_gold_stage(st, min_classified=3)
    assert res["valid"]
    assert res["n_classified"] == 3
    assert res["n_dropped"] == 0
    assert abs(res["soft_agreement"] - 1.0) < 1e-9
    assert res["gate_pass"] is True
    assert res["confusion"]["筑底/底部震荡"]["筑底/底部震荡"] == 3


def test_evaluate_gold_stage_drops_ambiguous(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    closes = [200 - 0.5 * i for i in range(200)]
    _seed_gold(st, closes)
    dates = _dates(200)
    claims = [
        {"uid": "a1", "episode_date": dates[150], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金接近底部", "direction": "down", "state": "draft"},
        {"uid": "a2", "episode_date": dates[160], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金筑底中", "direction": "down", "state": "draft"},
        {"uid": "a3", "episode_date": dates[170], "asset": "黄金", "claim_type": "direction",
         "statement": "黄金反弹后仍有回调", "state": "draft"},   # 无 direction → 歧义
    ]
    st.upsert_wm_claims(claims)
    res = evaluate_gold_stage(st, min_classified=2)
    assert res["n_classified"] == 2
    assert res["n_dropped"] == 1
