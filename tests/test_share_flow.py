import numpy as np
import pandas as pd

from stockagent.engine import indicators as ind
from stockagent.engine.signals import share_flow as sf

PARAMS = {"rotation": {"share_flow": {}}}


def test_share_params_default_merge():
    p = sf._share_params({"rotation": {"share_flow": {"trend_days": 40}}})
    assert p["trend_days"] == 40      # override
    assert p["long_ma"] == 120        # default kept
    assert p["min_share_change"] == 0.0


def test_share_change_basic():
    shares = pd.Series([100.0 + i for i in range(61)], dtype=float)  # 100..160
    assert round(sf._share_change(shares, 60), 6) == round(160 / 100 - 1, 6)


def test_share_change_short_history_nan():
    assert np.isnan(sf._share_change(pd.Series([1, 2, 3]), 60))


def test_score_symbol_rising_shares_eligible():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)  # uptrend => above MA
    shares = pd.Series(np.linspace(50, 80, 200), dtype=float)  # shares rising
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert info["eligible"]
    assert info["score"] > 0


def test_score_symbol_falling_shares_not_eligible():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    shares = pd.Series(np.linspace(80, 50, 200), dtype=float)  # shares falling
    info = sf.score_symbol(close, PARAMS, shares=shares)
    assert not info["eligible"]  # share_change < 0


def test_score_symbol_no_shares_not_eligible():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    info = sf.score_symbol(close, PARAMS, shares=None)
    assert not info["eligible"]
    assert np.isnan(info["score"])


def test_score_universe_uses_ctx():
    close = pd.Series(np.linspace(10, 20, 200), dtype=float)
    shares = pd.Series(np.linspace(50, 80, 200), dtype=float)
    df = sf.score_universe({"X": close}, PARAMS, ctx={"share": {"X": shares}})
    assert "eligible" in df.columns and "symbol" in df.columns
    assert bool(df.iloc[0]["eligible"])
