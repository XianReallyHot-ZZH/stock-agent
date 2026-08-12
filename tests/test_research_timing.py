"""Tests for stockagent.research.timing (ETF 择时跟踪纯函数).

Covers: copied deviation primitives on synthetic series; scissor_divergence across
divergent / convergent / flat / lumpy-shares / sub-floor scenarios; timing_snapshot
data_sufficient gating.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stockagent.research import timing as tm


def _idx(n: int = 250) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D")


# ---------------- deviation primitives (copied) ----------------

def test_ma_series_short_and_known():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    ma = tm.ma_series(s, period=2)
    assert np.isnan(ma.iloc[0])
    assert ma.iloc[1] == 1.5 and ma.iloc[-1] == 4.5
    # too short → all-NaN with same index
    short = tm.ma_series(pd.Series([1.0, 2.0]), period=60)
    assert short.isna().all() and len(short) == 2


def test_deviation_series_sign():
    s = pd.Series(np.linspace(1.0, 1.2, 200), index=_idx(200))  # rising → dev≥0 at tail
    dev = tm.deviation_series(s, period=60)
    assert dev.iloc[:59].isna().all()
    assert dev.iloc[-1] > 0


def test_deviation_extremes_valid_and_pct_bounds():
    # oscillation around 100 → deviation swings both ways
    s = pd.Series(100 + 30 * np.sin(np.linspace(0, 4 * np.pi, 200)), index=_idx(200))
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"] is True
    assert ext["max_dev"] > 0 > ext["min_dev"]
    assert 0.0 <= ext["pct"] <= 1.0


def test_deviation_extremes_short_series_invalid():
    s = pd.Series(np.linspace(1, 2, 50), index=_idx(50))  # < ma_period
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"] is False
    assert np.isnan(ext["pct"])


def test_deviation_extremes_tail_spike_is_high_percentile():
    # flat then ramp up → last bar is among the highest deviations
    close = np.r_[np.full(180, 100.0), np.linspace(100, 135, 40)]
    s = pd.Series(close, index=_idx(220))
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"]
    assert ext["pct"] > 0.90  # near the top of its own history


def test_deviation_extreme_events_have_ranked_extremes():
    s = pd.Series(100 + 30 * np.sin(np.linspace(0, 4 * np.pi, 200)), index=_idx(200))
    evs = tm.deviation_extreme_events(s, period=60)
    assert len(evs) > 0
    sides = {e["side"] for e in evs}
    assert "low" in sides and "high" in sides
    # each side's ranks are 1..k contiguous, starting at 1 (1 = most extreme)
    for side in ("low", "high"):
        ranks = sorted(e["rank"] for e in evs if e["side"] == side)
        assert ranks[0] == 1
        assert ranks == list(range(1, len(ranks) + 1))


# ---------------- scissor_divergence ----------------

def test_scissor_divergent_share_up_nav_down():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 0.8, 250), index=idx)          # -20%
    shares = pd.Series(np.linspace(1e8, 1.3e8, 250), index=idx)     # +30%
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_up_nav_down"
    assert r["share_drift"] > 0 and r["nav_drift"] < 0
    assert r["window"] >= 20


def test_scissor_divergent_share_down_nav_up():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 1.25, 250), index=idx)         # +25%
    shares = pd.Series(np.linspace(1.3e8, 1e8, 250), index=idx)     # -23%
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_down_nav_up"


def test_scissor_convergent_not_detected():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 1.2, 250), index=idx)          # both up → same sign
    shares = pd.Series(np.linspace(1e8, 1.3e8, 250), index=idx)
    assert tm.scissor_divergence(shares, nav)["detected"] is False


def test_scissor_flat_not_detected():
    idx = _idx(250)
    nav = pd.Series(np.full(250, 1.0), index=idx)
    shares = pd.Series(np.full(250, 1e8), index=idx)
    assert tm.scissor_divergence(shares, nav)["detected"] is False


def test_scissor_lumpy_shares_handled():
    # shares flat then a block step jump (creation/redemption is lumpy, not daily-smooth)
    idx = _idx(250)
    sh = np.full(250, 1e8)
    sh[150:] = 1.25e8                                             # +25% step
    nav = pd.Series(np.linspace(1.0, 0.8, 250), index=idx)        # -20%
    shares = pd.Series(sh, index=idx)
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_up_nav_down"


def test_scissor_sub_floor_not_detected():
    # divergence concentrated in the last 60 days: ±4% (between a 2% and 5% floor)
    idx = _idx(250)
    nav = pd.Series(np.r_[np.full(190, 1.0), np.linspace(1.0, 0.96, 60)], index=idx)      # -4%
    shares = pd.Series(np.r_[np.full(190, 1e8), np.linspace(1e8, 1.04e8, 60)], index=idx)  # +4%
    assert tm.scissor_divergence(shares, nav, floor=0.05)["detected"] is False  # 4% < 5%
    assert tm.scissor_divergence(shares, nav, floor=0.02)["detected"] is True   # 4% ≥ 2%


def test_scissor_none_inputs():
    assert tm.scissor_divergence(None, None)["detected"] is False
    assert tm.scissor_divergence(None, pd.Series([1.0]))["detected"] is False


# ---------------- timing_snapshot ----------------

def test_timing_snapshot_short_history_insufficient():
    nav_df = pd.DataFrame({"unit_nav": np.linspace(1, 1.1, 30), "acc_nav": np.linspace(1, 1.1, 30)},
                          index=_idx(30))
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["data_sufficient"] is False
    assert np.isnan(snap["nav_dev_pct"])
    assert snap["scissor"]["detected"] is False


def test_timing_snapshot_sufficient_has_fields():
    idx = _idx(200)
    nav_df = pd.DataFrame({"acc_nav": np.linspace(1.0, 1.2, 200)}, index=idx)
    shares_df = pd.DataFrame({"shares": np.linspace(1e8, 1.3e8, 200)}, index=idx)
    snap = tm.timing_snapshot(nav_df, shares_df, ma_period=60)
    assert snap["data_sufficient"] is True
    assert 0.0 <= snap["nav_dev_pct"] <= 1.0
    assert isinstance(snap["nav_extreme_events"], list)
    assert snap["nav_col"] == "acc_nav"
    # shares + nav same direction here (both up) → no divergence
    assert snap["scissor"]["detected"] is False


def test_timing_snapshot_prefers_acc_nav():
    idx = _idx(200)
    # acc_nav continuous; unit_nav has an artificial cliff → must pick acc_nav
    unit = np.linspace(1.0, 1.2, 200)
    unit[120:] = unit[120:] * 0.5  # cliff
    nav_df = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.2, 200)}, index=idx)
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["nav_col"] == "acc_nav"
