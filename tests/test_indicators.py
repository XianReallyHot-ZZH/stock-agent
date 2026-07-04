import numpy as np
import pandas as pd

from stockagent.engine import indicators as ind


def s(values):
    return pd.Series(values, dtype=float)


def test_returns_basic():
    # 100 -> 110 over 1 period => 0.10
    assert round(ind.returns(s([100, 110]), 1), 6) == 0.10


def test_returns_multi_period():
    # 100 -> 121 over 2 periods => 0.21
    assert round(ind.returns(s([100, 110, 121]), 2), 6) == 0.21


def test_returns_short_history_nan():
    assert np.isnan(ind.returns(s([100, 110]), 5))


def test_sma():
    assert ind.sma(s([1, 2, 3, 4, 5]), 5) == 3.0
    assert np.isnan(ind.sma(s([1, 2, 3]), 5))


def test_drawdown_from_peak():
    # peak 120, last 90 => 90/120 - 1 = -0.25
    assert round(ind.drawdown_from_peak(s([100, 120, 90])), 6) == -0.25


def test_momentum_score_weighted():
    # construct series with known 1d/2d/3d returns
    # price path: 100,103,106,110 => r1=10/100? compute: last=110
    # r over 1 = 110/106-1 ; over 2 = 110/103-1 ; over 3 = 110/100-1
    close = s([100, 103, 106, 110])
    r1 = 110 / 106 - 1
    r2 = 110 / 103 - 1
    r3 = 110 / 100 - 1
    got = ind.momentum_score(close, [1, 2, 3], [0.2, 0.3, 0.5])
    expected = 0.2 * r1 + 0.3 * r2 + 0.5 * r3
    assert round(got, 6) == round(expected, 6)


def test_momentum_short_history_nan():
    assert np.isnan(ind.momentum_score(s([1, 2, 3]), [20, 60, 120], [0.2, 0.3, 0.5]))


def test_rsi_all_up_is_100():
    close = pd.Series(np.linspace(10, 50, 30), dtype=float)
    assert ind.rsi(close, 14) == 100.0


def test_rsi_all_down_is_0():
    close = pd.Series(np.linspace(50, 10, 30), dtype=float)
    assert ind.rsi(close, 14) == 0.0


def test_rsi_short_history_nan():
    assert np.isnan(ind.rsi(s([1, 2, 3, 4]), 14))


def test_rsi_mixed_in_range():
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)), dtype=float)
    val = ind.rsi(close, 14)
    assert 0.0 < val < 100.0
