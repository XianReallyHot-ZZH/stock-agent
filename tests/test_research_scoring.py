"""Pure-function tests for the research scoring module.

Mirrors test_share_flow.py style: synthetic pd.Series (no DB/network). Locks the 6-phase
chip semantics (article 重远投资观 non-monotone logic) + valuation/trend/composite.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockagent.config import get_config
from stockagent.research import scoring as rs
from stockagent.research import commentary as rc

PARAMS = get_config().params
RP = PARAMS["research"]
CS = RP["chip_score"]  # the 9 phase scores

N = 200


def _lin(a, b, n=N):
    return pd.Series(np.linspace(a, b, n), dtype=float)


def _concat(*parts):
    return pd.Series(np.concatenate([np.asarray(p, dtype=float) for p in parts]))


# ---------------- valuation ----------------
def test_valuation_cheap_is_high():
    pe = _lin(30, 10)  # last is the lowest → cheapest → score ~100
    assert rs.valuation_score(pe, 250) > 95


def test_valuation_expensive_is_low():
    pe = _lin(10, 30)  # last is the highest → most expensive → score ~0
    assert rs.valuation_score(pe, 250) < 5


def test_valuation_short_series_nan():
    assert np.isnan(rs.valuation_score(pd.Series([10.0, 11.0]), 250))
    assert np.isnan(rs.valuation_score(None, 250))


def test_pe_percentile_bounds():
    pe = _lin(10, 20)
    pct = rs.pe_percentile(pe, 250)
    assert 0.0 <= pct <= 1.0
    assert pct > 0.9  # last is max


# ---------------- reduction_from_peak ----------------
def test_reduction_at_peak_zero():
    sh = _lin(100, 120)  # last = peak
    assert rs.reduction_from_peak(sh) == pytest.approx(0.0, abs=1e-9)


def test_reduction_half():
    sh = _concat(_lin(100, 100), [50] * 50)  # peak 100, last 50 → 0.5
    assert rs.reduction_from_peak(sh) == pytest.approx(0.5, abs=1e-6)


def test_reduction_short_nan():
    assert np.isnan(rs.reduction_from_peak(pd.Series([100.0])))


# ---------------- chip 6 phases (position × state) ----------------
def _chip(series):
    score, label, red = rs.chip_score(series, PARAMS)
    return score, label, red


def test_chip_low_accumulating_high_position_rising():
    # rises to a new peak at the end → reduction 0 (low) + ACCUMULATING
    score, label, _ = _chip(_lin(100, 120))
    assert label == "高位加仓"
    assert score == CS["low_accumulating"]


def test_chip_low_stable_peak_flat():
    # flat at the peak → reduction 0 (low) + STABLE
    score, label, _ = _chip(pd.Series([120.0] * N))
    assert label == "见顶预警"
    assert score == CS["low_stable"]


def test_chip_low_distributing_small_recent_drop():
    # peak 120 then a small drop to 100 → reduction ~0.17 (low) + DISTRIBUTING
    s = _concat(_lin(100, 120, 150), _lin(120, 100, 50))
    score, label, red = _chip(s)
    assert red < RP["chip"]["pos_low"]
    assert label == "高位减仓"
    assert score == CS["low_distributing"]


def test_chip_mid_distributing_article_main_thesis():
    # steady decline to 70% of peak → mid reduction + DISTRIBUTING (article 兑现中段)
    s = _lin(100, 70)
    score, label, red = _chip(s)
    assert RP["chip"]["pos_low"] < red < RP["chip"]["pos_deep"]
    assert label == "兑现中段"
    assert score == CS["mid_distributing"]  # 20 — most bearish
    assert score == min(CS.values())  # global minimum


def test_chip_mid_stable_flat_after_drop():
    # drop to 70 then flat → mid reduction + STABLE
    s = _concat(_lin(100, 70, 50), pd.Series([70.0] * 150))
    _, label, red = _chip(s)
    assert RP["chip"]["pos_low"] < red < RP["chip"]["pos_deep"]
    assert label == "观望"


def test_chip_mid_accumulating_recovering():
    # crash to 60 then recover to 70 (peak 100) → mid + ACCUMULATING
    s = _concat(_lin(100, 60, 100), _lin(60, 70, 100))
    score, label, red = _chip(s)
    assert RP["chip"]["pos_low"] < red < RP["chip"]["pos_deep"]
    assert label == "加仓"
    assert score == CS["mid_accumulating"]


def test_chip_deep_accumulating_bottom_fishing():
    # crash to 20 then strong recovery to 45 (peak 100) → deep + ACCUMULATING (most bullish)
    s = _concat(_lin(100, 20, 100), _lin(20, 45, 100))
    score, label, red = _chip(s)
    assert red >= RP["chip"]["pos_deep"]
    assert label == "低位加仓"
    assert score == CS["deep_accumulating"]  # 90
    assert score == max(CS.values())  # global maximum


def test_chip_deep_stable_confirmed_bottom():
    # crash to 40 then FLAT long enough that 120d ROC also flattens → deep + STABLE = 见底
    s = _concat(_lin(100, 40, 70), pd.Series([40.0] * 130))
    score, label, red = _chip(s)
    assert red >= RP["chip"]["pos_deep"]
    assert label == "见底"
    assert score == CS["deep_stable"]  # 85


def test_chip_deep_distributing_still_falling():
    # steady decline to 40% of peak → deep + DISTRIBUTING (下跌末段, unconfirmed bottom)
    s = _lin(100, 40)
    score, label, red = _chip(s)
    assert red >= RP["chip"]["pos_deep"]
    assert label == "下跌末段"
    assert score == CS["deep_distributing"]


def test_chip_short_series_insufficient():
    score, label, _ = _chip(pd.Series(np.linspace(100, 90, 50)))  # < 121 bars
    assert np.isnan(score)
    assert label == "数据不足"


def test_chip_monotonicity_bearish_to_bullish_ordering():
    """Article core: 兑现中段(most bearish) < 下跌末段 < 见底 < 低位加仓(most bullish)."""
    assert CS["mid_distributing"] < CS["deep_distributing"] < CS["deep_stable"] < CS["deep_accumulating"]


# ---------------- trend ----------------
def test_trend_above_ma():
    cl = _lin(10, 15)  # rising → last well above MA60
    assert rs.trend_score(cl, 60) > 50


def test_trend_below_ma():
    cl = _lin(15, 10)  # falling → last below MA60
    assert rs.trend_score(cl, 60) < 50


def test_trend_short_nan():
    assert np.isnan(rs.trend_score(pd.Series([1.0, 2.0, 3.0]), 60))


def test_trend_clamped_to_100():
    # extreme rally → clamped at 100
    cl = _concat(_lin(10, 10, 60), _lin(10, 100, 140))
    assert rs.trend_score(cl, 60) == 100.0


# ---------------- composite ----------------
def test_composite_equity_weighted():
    val, chip, trend = 100.0, 60.0, 60.0
    w = RP["weights"]
    expect = w["valuation"] * 100 + w["chip"] * 60 + w["trend"] * 60
    assert rs.composite(val, chip, trend, w, has_valuation=True) == pytest.approx(expect)


def test_composite_non_equity_equal_weight():
    assert rs.composite(np.nan, 60.0, 80.0, RP["weights"], has_valuation=False) == pytest.approx(70.0)


def test_composite_renormalizes_on_missing_factor():
    # valuation present but chip NaN → renormalize over valuation+trend only
    w = RP["weights"]
    out = rs.composite(80.0, np.nan, 60.0, w, has_valuation=True)
    expect = (w["valuation"] * 80 + w["trend"] * 60) / (w["valuation"] + w["trend"])
    assert out == pytest.approx(expect)


def test_composite_all_nan():
    assert np.isnan(rs.composite(np.nan, np.nan, np.nan, RP["weights"], has_valuation=True))


# ---------------- analyze_etf end-to-end ----------------
def test_analyze_etf_full_snapshot():
    close = _lin(10, 15)
    shares = _lin(100, 120)  # high_accum
    pe = _lin(30, 10)  # cheap
    snap = rs.analyze_etf(close, shares, pe, PARAMS, has_valuation=True)
    assert snap["chip_phase"] == "高位加仓"
    assert snap["valuation"] > 95
    assert snap["composite"] > 70
    assert snap["data_sufficient"] is True
    for k in ("valuation", "chip", "trend", "composite", "pe_percentile",
              "reduction_from_peak", "chip_phase", "data_sufficient"):
        assert k in snap


def test_data_sufficient_false_when_shares_missing():
    """No share history → chip NaN → data_sufficient False (excluded from ranking)."""
    close = _lin(10, 15)
    pe = _lin(30, 10)
    snap = rs.analyze_etf(close, None, pe, PARAMS, has_valuation=True)
    assert np.isnan(snap["chip"])
    assert snap["chip_phase"] == "数据不足"
    assert snap["data_sufficient"] is False


def test_data_sufficient_true_for_non_equity_no_pe():
    """Broad/QDII ETF with no PE (by design) but valid chip+trend → still sufficient."""
    close = _lin(10, 15)
    shares = _lin(100, 120)
    snap = rs.analyze_etf(close, shares, None, PARAMS, has_valuation=False)
    assert snap["data_sufficient"] is True  # PE not applicable, not missing


# ---------------- commentary (zero-prediction) ----------------
def _snap():
    return rs.analyze_etf(_lin(10, 15), _lin(100, 120), _lin(30, 10), PARAMS, has_valuation=True)


def test_commentary_template_no_banned_words():
    txt = rc._template(_snap(), "银行ETF")
    assert not rc.has_banned_word(txt)
    assert "规则输出" in txt  # disclaimer present (the word 预测 only appears inside it)


def test_commentary_no_llm_key_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(rc.llm_client, "llm_available", lambda: False)
    out = rc.commentary({"512800": _snap()}, {"512800": {"name": "银行ETF"}}, use_llm=True)
    assert "512800" in out
    assert not rc.has_banned_word(out["512800"])
    assert "银行ETF" in out["512800"]


def test_commentary_llm_clean_passes_through(monkeypatch):
    monkeypatch.setattr(rc.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(rc.llm_client, "chat",
                        lambda prompt, system=None: "银行估值便宜且机构加仓，性价比高。")
    out = rc.commentary({"512800": _snap()}, {"512800": {"name": "银行ETF"}}, use_llm=True)
    assert "性价比高" in out["512800"]
    assert not rc.has_banned_word(out["512800"])


def test_commentary_llm_leaks_prediction_is_guarded(monkeypatch):
    """If the model emits a banned word, the hard guard discards it → template used."""
    monkeypatch.setattr(rc.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(rc.llm_client, "chat",
                        lambda prompt, system=None, max_tokens=400: "预计银行后市看好，有望上涨。")
    out = rc.commentary({"512800": _snap()}, {"512800": {"name": "银行ETF"}}, use_llm=True)
    assert not rc.has_banned_word(out["512800"])  # guarded → no banned word survives
    assert "规则输出" in out["512800"]  # template marker


# ---------------- cross-pool summary ----------------
def _pool_snapshots():
    """Build a small fake pool with distinct phases for summary tests."""
    out = {}
    # 3 cheap+accumulating (high composite), 2 expensive+distributing (low)
    for i, (sym, nm, close, shares, pe) in enumerate([
        ("A", "便宜加仓A", _lin(10, 15), _lin(100, 120), _lin(30, 10)),   # cheap, accum
        ("B", "便宜加仓B", _lin(10, 14), _lin(100, 115), _lin(28, 9)),
        ("C", "便宜加仓C", _lin(10, 13), _lin(100, 110), _lin(25, 8)),
        ("D", "贵减仓D", _lin(15, 10), _lin(100, 70), _lin(10, 30)),      # expensive, distrib
        ("E", "贵减仓E", _lin(15, 9), _lin(100, 65), _lin(9, 28)),
    ]):
        out[sym] = rs.analyze_etf(close, shares, pe, PARAMS, has_valuation=True)
        out[sym]["name"] = nm
    return out, {s: {"name": sn["name"]} for s, sn in out.items()}


def test_pool_template_grounded_no_banned():
    snaps, meta = _pool_snapshots()
    txt = rc._pool_template(snaps, meta)
    assert not rc.has_banned_word(txt)
    assert "规则输出" in txt
    assert "5 只参与排名" in txt  # all 5 data_sufficient


def test_pool_template_counts_excluded():
    snaps, meta = _pool_snapshots()
    snaps["X"] = rs.analyze_etf(_lin(10, 15), None, _lin(30, 10), PARAMS, has_valuation=True)
    snaps["X"]["name"] = "无筹码X"
    meta["X"] = {"name": "无筹码X"}
    txt = rc._pool_template(snaps, meta)
    assert "5 只参与排名" in txt
    assert "1只数据不足未参与" in txt


def test_pool_summary_llm_passes_through(monkeypatch):
    snaps, meta = _pool_snapshots()
    monkeypatch.setattr(rc.llm_client, "llm_available", lambda: True)
    captured = {}
    def fake(prompt, system=None, max_tokens=400):
        captured["max_tokens"] = max_tokens
        return "便宜加仓品种集中在前，贵的在减。"
    monkeypatch.setattr(rc.llm_client, "chat", fake)
    txt = rc.pool_summary(snaps, meta, use_llm=True)
    assert "便宜加仓" in txt
    assert captured["max_tokens"] >= 1000  # enough headroom for Chinese


def test_pool_summary_llm_banned_falls_back(monkeypatch):
    snaps, meta = _pool_snapshots()
    monkeypatch.setattr(rc.llm_client, "llm_available", lambda: True)
    monkeypatch.setattr(rc.llm_client, "chat",
                        lambda prompt, system=None, max_tokens=400: "预计后市看好，有望上涨。")
    txt = rc.pool_summary(snaps, meta, use_llm=True)
    assert not rc.has_banned_word(txt)
    assert "规则输出" in txt  # fell back to template
