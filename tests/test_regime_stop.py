import numpy as np
import pandas as pd

from stockagent.engine import regime as reg
from stockagent.engine import stop as stop_mod


def s(v):
    return pd.Series(v, dtype=float)


def test_regime_risk_on_when_above_ma():
    close = pd.Series(np.linspace(10, 20, 130), dtype=float)  # rising
    assert reg.regime_state(close, 120) == reg.RISK_ON


def test_regime_risk_off_when_below_ma():
    close = pd.Series(np.linspace(20, 10, 130), dtype=float)  # falling
    assert reg.regime_state(close, 120) == reg.RISK_OFF


def test_regime_short_history_defaults_on():
    assert reg.regime_state(s([1, 2, 3]), 120) == reg.RISK_ON


def test_stop_not_triggered_small_pullback():
    # peak 100, last 95 -> -5% < 8% threshold
    assert not stop_mod.stop_triggered(s([90, 100, 95]), 0.08)


def test_stop_triggered_large_pullback():
    # peak 100, last 91 -> -9% >= 8%
    assert stop_mod.stop_triggered(s([90, 100, 91]), 0.08)


def test_stop_peak_since_entry():
    assert stop_mod.peak_since_entry(s([95, 100, 97, 91])) == 100.0


def test_stop_vol_mult_scales_threshold():
    """Higher mult → wider stop → harder to trigger for same drawdown."""
    close = pd.Series(list(np.linspace(100, 110, 20)) + [99.0], dtype=float)  # peak 110, last 99, dd=-10%
    assert stop_mod.stop_triggered_vol(close, 14, 2.0)     # narrow: triggered
    assert not stop_mod.stop_triggered_vol(close, 14, 20.0)  # very wide: not triggered


def test_stop_vol_short_history_not_triggered():
    assert not stop_mod.stop_triggered_vol(s([1, 2, 3]), 14, 3.0)
