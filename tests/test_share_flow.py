import numpy as np
import pandas as pd

from stockagent.engine.signals import share_flow as sf

PARAMS = {"rotation": {"share_flow": {}}}


def test_share_params_default_merge():
    p = sf._share_params({"rotation": {"share_flow": {"accum_threshold": 0.05}}})
    assert p["accum_threshold"] == 0.05
    assert p["roc_short_days"] == 20
    assert p["price_backstop"] == 0.20


def test_classify_accumulating():
    rocs = [0.05, 0.10, 0.03]  # all > 2% → 3 votes
    assert sf._classify_state(rocs, 0.02, 0.02) == "ACCUMULATING"


def test_classify_distributing():
    rocs = [-0.05, -0.10, 0.01]  # 2 negative → DISTRIBUTING
    assert sf._classify_state(rocs, 0.02, 0.02) == "DISTRIBUTING"


def test_classify_stable_mixed():
    rocs = [0.05, -0.03, 0.01]  # 1 positive, 1 negative, 1 small → STABLE
    assert sf._classify_state(rocs, 0.02, 0.02) == "STABLE"


def test_classify_stable_below_threshold():
    rocs = [0.01, 0.01, 0.01]  # all positive but < 2% → STABLE
    assert sf._classify_state(rocs, 0.02, 0.02) == "STABLE"


def test_score_accumulating_eligible():
    """Sustained share rise across timeframes → ACCUMULATING → eligible."""
    shares = pd.Series(np.linspace(50, 120, 200), dtype=float)  # steady rise
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert info["eligible"]  # multi-timeframe consensus = ACCUMULATING
    assert info["score"] > 0


def test_score_distributing_not_eligible():
    shares = pd.Series(np.linspace(120, 50, 200), dtype=float)  # steady fall
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]


def test_score_stable_not_eligible():
    """Flat shares → below threshold → STABLE → not eligible."""
    shares = pd.Series([100.0] * 200)  # completely flat
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]  # 0% change < 2% threshold


def test_check_exits_price_backstop_only():
    """V2.6: check_exits is just price backstop, no flow-stop."""
    n = 100
    close = pd.Series(list(np.linspace(10, 20, 50)) + list(np.linspace(20, 14, 50)), dtype=float)
    close.index = pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d")
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}, "share": {"X": pd.Series(np.linspace(50, 60, n))}}
    exits = sf.check_exits(positions, ctx, PARAMS)
    assert "X" in exits  # price -30% > 20% backstop


def test_check_exits_no_exit_when_price_ok():
    close = pd.Series(np.linspace(10, 15, 100), dtype=float)
    close.index = pd.date_range("2026-01-01", periods=100).strftime("%Y-%m-%d")
    positions = {"X": {"entry_date": close.index[0]}}
    ctx = {"close": {"X": close}}
    exits = sf.check_exits(positions, ctx, PARAMS)
    assert "X" not in exits  # price rising, no backstop


def test_score_universe_columns():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    shares = pd.Series(np.linspace(50, 120, 200), dtype=float)
    df = sf.score_universe({"X": close}, PARAMS, ctx={"share": {"X": shares}})
    for c in ("symbol", "score", "eligible"):
        assert c in df.columns
