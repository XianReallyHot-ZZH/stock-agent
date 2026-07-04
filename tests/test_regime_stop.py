import numpy as np
import pandas as pd

from stockagent.engine import regime as reg
from stockagent.engine import stop as stop_mod
from stockagent.engine.regime import RegimeFilter


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


def test_regime_filter_legacy_matches_regime_state():
    """band=0, confirm=1 → same as old regime_state."""
    close = pd.Series(np.linspace(10, 20, 130), dtype=float)
    rf = RegimeFilter(120, band_pct=0.0, confirm_days=1)
    assert rf.process(close) == reg.RISK_ON
    close_down = pd.Series(np.linspace(20, 10, 130), dtype=float)
    assert rf.process(close_down) == reg.RISK_OFF


def test_regime_filter_band_prevents_flip_near_ma():
    """With 5% band, price slightly below MA should NOT flip (stays risk_on)."""
    n = 130
    base = [100.0] * n  # flat → MA = 100
    # last 5 bars dip to 97 (within 5% band of 100)
    close = pd.Series(base[:n-5] + [99, 98, 97, 98, 97], dtype=float)
    rf_tight = RegimeFilter(120, band_pct=0.0, confirm_days=1)  # no band → flips
    rf_wide = RegimeFilter(120, band_pct=0.05, confirm_days=1)  # 5% band → stays on
    assert rf_tight.process(close) == reg.RISK_OFF  # 97 < 100, no band → off
    assert rf_wide.process(close) == reg.RISK_ON    # 97 > 95 (100×0.95) → within band → stays on


def test_regime_filter_confirm_days_requires_sustained_break():
    """confirm=3: 2 days below → no flip; 3+ days → flip."""
    n = 130
    base = [100.0] * n  # MA = 100
    # 2 days below (no band) → not enough with confirm=3
    close_2d = pd.Series(base[:n-2] + [95, 95], dtype=float)
    rf = RegimeFilter(120, band_pct=0.0, confirm_days=3)
    assert rf.process(close_2d) == reg.RISK_ON  # only 2 consecutive < 100 → no flip
    # 3 days below → flip
    close_3d = pd.Series(base[:n-3] + [95, 95, 95], dtype=float)
    assert rf.process(close_3d) == reg.RISK_OFF  # 3 consecutive → confirmed


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
