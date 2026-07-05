"""Tests for value_flow signal."""
import numpy as np
import pandas as pd

from stockagent.engine import indicators as ind
from stockagent.engine.signals import value_flow as vf

PARAMS = {"rotation": {"value_flow": {"entry_percentile": 0.50, "min_hold_days": 0,
    "stabilize_lookback": 50, "stabilize_recent": 20}, "share_flow": {}}}


def test_percentile_rank_basic():
    s = pd.Series(np.linspace(1, 100, 100), dtype=float)
    assert ind.percentile_rank(s, 100) == 0.99  # last value is highest → ~99th percentile
    s2 = pd.Series(list(np.linspace(1, 99, 99)) + [1.0], dtype=float)
    assert ind.percentile_rank(s2, 100) == 0.0  # last value is lowest → 0th


def test_vf_params_default_merge():
    p = vf._vf_params({"rotation": {"value_flow": {"entry_percentile": 0.20}}})
    assert p["entry_percentile"] == 0.20
    assert p["accum_trail_mult"] == 3.0
    assert p["percentile_window"] == 500


def test_score_low_price_accumulating_eligible():
    """Price at historical low + recovering + ACCUMULATING → eligible."""
    n = 600
    close = pd.Series(
        list(np.linspace(10, 20, 300)) +      # run up
        list(np.linspace(20, 8, 250)) +        # crash
        list(np.linspace(8, 9.5, 50)),         # recover from bottom
        dtype=float)
    shares = pd.Series(np.linspace(50, 200, n), dtype=float)  # shares rising → ACCUMULATING
    info = vf.score_symbol(close, PARAMS, shares=shares)
    assert info["eligible"]
    assert info["score"] > 0


def test_score_high_price_not_eligible():
    """Price at historical high → not cheap → not eligible."""
    close = pd.Series(np.linspace(10, 20, 600), dtype=float)  # always rising, now at top
    shares = pd.Series(np.linspace(50, 200, 600), dtype=float)
    info = vf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]  # percentile > 0.30


def test_score_distributing_not_eligible():
    """Price low but DISTRIBUTING → not eligible (institutions leaving)."""
    n = 600
    close = pd.Series(list(np.linspace(10, 20, 300)) + list(np.linspace(20, 10.5, 300)), dtype=float)
    shares = pd.Series(np.linspace(200, 50, n), dtype=float)  # shares falling → DISTRIBUTING
    info = vf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]


def test_check_exits_entry_stop_triggered():
    """Price drops >12% from ENTRY → exit regardless of share state."""
    n = 100
    # price rises then drops below entry by 15%
    close = pd.Series(list(np.linspace(10, 12, 50)) + list(np.linspace(12, 8.5, 50)), dtype=float)
    close.index = pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    shares = pd.Series(np.linspace(50, 200, n), dtype=float, index=close.index)  # ACCUMULATING
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": shares}}
    # entry=10, last=8.5 → -15% < -12% entry_stop → exit even though ACCUMULATING
    params = {"rotation": {"value_flow": {"min_hold_days": 0}, "share_flow": {}}}
    exits = vf.check_exits(positions, ctx, params)
    assert "X" in exits  # entry stop triggered


def test_check_exits_trailing_protects_profits():
    """When in profit, trailing stop from peak protects gains."""
    n = 100
    close = pd.Series(list(np.linspace(10, 15, 70)) + list(np.linspace(15, 11, 30)), dtype=float)
    close.index = pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    shares = pd.Series(np.linspace(200, 50, n), dtype=float, index=close.index)  # DISTRIBUTING
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": shares}}
    # entry=10, peak=15, last=11 → still +10% profit
    # DISTRIBUTING trailing: 1×ATR from peak → should trigger (tight)
    params = {"rotation": {"value_flow": {"min_hold_days": 0}, "share_flow": {}}}
    exits = vf.check_exits(positions, ctx, params)
    assert "X" in exits  # trailing triggered to protect profit


def test_check_exits_no_exit_underwater_but_above_entry_stop():
    """Price slightly below entry but above -12% → no exit (patience)."""
    n = 100
    close = pd.Series(list(np.linspace(10, 10.5, 50)) + list(np.linspace(10.5, 9.5, 50)), dtype=float)
    close.index = pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d")
    shares = pd.Series(np.linspace(50, 200, n), dtype=float, index=close.index)
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": shares}}
    # entry=10, last=9.5 → -5% > -12% → hold (within tolerance, no trailing since underwater)
    params = {"rotation": {"value_flow": {"min_hold_days": 0}, "share_flow": {}}}
    exits = vf.check_exits(positions, ctx, params)
    assert "X" not in exits


def test_check_exits_no_exit_when_rising():
    """Price still rising → no exit even after min_hold."""
    close = pd.Series(np.linspace(10, 20, 100), dtype=float)
    close.index = pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d")
    shares = pd.Series(np.linspace(50, 200, 100), dtype=float, index=close.index)
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": shares}}
    params = {"rotation": {"value_flow": {"min_hold_days": 0}, "share_flow": {}}}
    exits = vf.check_exits(positions, ctx, params)
    assert "X" not in exits  # price rising, no exit


def test_check_exits_min_hold_blocks_early_exit():
    """Before min_hold_days → no exit even if trailing triggered."""
    close = pd.Series([10, 9, 8, 7], dtype=float)  # drops immediately
    close.index = pd.date_range("2024-01-01", periods=4).strftime("%Y-%m-%d")
    shares = pd.Series([100, 90, 80, 70], dtype=float, index=close.index)
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": shares}}
    params_60 = {"rotation": {"value_flow": {"min_hold_days": 60}, "share_flow": {}}}
    exits = vf.check_exits(positions, ctx, params_60)  # min_hold=60, only 3 days
    assert "X" not in exits  # too early


def test_sticky_flag():
    assert vf.STICKY is True
