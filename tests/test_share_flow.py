import numpy as np
import pandas as pd

from stockagent.engine.signals import share_flow as sf

PARAMS = {"rotation": {"share_flow": {}}}


def test_share_params_default_merge():
    p = sf._share_params({"rotation": {"share_flow": {"trend_days": 40}}})
    assert p["trend_days"] == 40
    assert p["flow_stop_pct"] == 0.10
    assert p["price_backstop"] == 0.20


def test_share_change_basic():
    shares = pd.Series([100.0 + i for i in range(61)], dtype=float)  # 100..160
    assert round(sf._share_change(shares, 60), 6) == round(160 / 100 - 1, 6)


def test_score_symbol_rising_shares_eligible_no_price_gate():
    """V2.4: eligible based on share inflow only, no price>MA gate."""
    close = pd.Series(np.linspace(20, 10, 200), dtype=float)  # DOWNTREND price
    shares = pd.Series(np.linspace(50, 80, 200), dtype=float)  # shares rising
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert info["eligible"]  # V2.4: eligible even though price falling!
    assert info["score"] > 0


def test_score_symbol_falling_shares_not_eligible():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    shares = pd.Series(np.linspace(80, 50, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]


def test_score_symbol_no_shares_not_eligible():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=None)
    assert not info["eligible"]


def test_check_exits_flow_stop_triggered():
    """Shares rise then drop > flow_stop_pct from peak → exit."""
    n = 100
    shares = pd.Series(list(np.linspace(50, 100, 50)) + list(np.linspace(100, 80, 50)), dtype=float)
    shares.index = pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    close = pd.Series(np.linspace(10, 15, n), dtype=float, index=shares.index)
    positions = {"X": {"entry_date": shares.index[0]}}
    ctx = {"share": {"X": shares}, "close": {"X": close}}
    # peak smoothed=100, last=80 → -20% drop > default flow_stop 10%
    exits = sf.check_exits(positions, ctx, PARAMS)
    assert "X" in exits


def test_check_exits_no_exit_when_shares_rising():
    shares = pd.Series(np.linspace(50, 100, 100), dtype=float)
    shares.index = pd.date_range("2026-01-01", periods=100).strftime("%Y-%m-%d")
    close = pd.Series(np.linspace(10, 15, 100), dtype=float, index=shares.index)
    positions = {"X": {"entry_date": shares.index[0]}}
    ctx = {"share": {"X": shares}, "close": {"X": close}}
    exits = sf.check_exits(positions, ctx, PARAMS)
    assert "X" not in exits  # shares rising, no flow-stop


def test_check_exits_price_backstop():
    """Price drops > 20% from peak → exit via backstop."""
    n = 100
    close = pd.Series(list(np.linspace(10, 20, 50)) + list(np.linspace(20, 14, 50)), dtype=float)
    close.index = pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    shares = pd.Series(np.linspace(50, 60, n), dtype=float, index=close.index)  # shares stable (no flow-stop)
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"share": {"X": shares}, "close": {"X": close}}
    # peak=20, last=14 → -30% > 20% backstop
    exits = sf.check_exits(positions, ctx, PARAMS)
    assert "X" in exits


def test_score_universe_columns():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    shares = pd.Series(np.linspace(50, 80, 200), dtype=float)
    df = sf.score_universe({"X": close}, PARAMS, ctx={"share": {"X": shares}})
    for c in ("symbol", "score", "eligible"):
        assert c in df.columns
