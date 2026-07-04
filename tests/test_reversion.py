import numpy as np
import pandas as pd

from stockagent.engine import indicators as ind
from stockagent.engine.signals import reversion as rev

PARAMS = {"rotation": {"reversion": {"rsi_period": 14, "oversold_threshold": 40, "long_ma": 200}}}


def uptrend_dip(n_up=220, dip=None):
    up = [50 + i * 0.7 for i in range(n_up)]
    return pd.Series(up + (dip or []), dtype=float)


def test_reversion_defaults_merged():
    p = rev._rev_params({"rotation": {"reversion": {"oversold_threshold": 35}}})
    assert p["rsi_period"] == 14          # default kept
    assert p["oversold_threshold"] == 35  # override applied
    assert p["long_ma"] == 250            # default kept


def test_score_symbol_formula_matches_indicators():
    dip = [203.3, 195, 188, 190, 185, 180, 182, 178, 175, 176, 172, 170, 168, 165]
    close = uptrend_dip(220, dip)
    info = rev.score_symbol(close, PARAMS)
    r = ind.rsi(close, 14)
    ma = ind.sma(close, 200)
    expected_eligible = (float(close.iloc[-1]) > ma) and (r < 40)
    assert info["eligible"] == expected_eligible
    assert abs(info["score"] - (40 - r)) < 1e-9


def test_pure_uptrend_not_eligible_high_rsi():
    # monotonically rising -> RSI ~ 100 (not oversold) -> ineligible
    close = pd.Series(np.linspace(50, 200, 250), dtype=float)
    info = rev.score_symbol(close, PARAMS)
    assert not info["eligible"]


def test_pure_downtrend_not_eligible_below_ma():
    # monotonically falling -> below long MA -> ineligible (even though RSI low)
    close = pd.Series(np.linspace(200, 50, 250), dtype=float)
    info = rev.score_symbol(close, PARAMS)
    assert not info["eligible"]


def test_deeper_dip_scores_higher():
    base = uptrend_dip(220)
    mild = pd.concat([base, pd.Series([203.3, 200, 198, 199, 197], dtype=float)], ignore_index=True)
    deep = pd.concat([base, pd.Series([203.3, 180, 165, 158, 150], dtype=float)], ignore_index=True)
    sm = rev.score_symbol(mild, PARAMS)
    sd = rev.score_symbol(deep, PARAMS)
    assert sd["score"] > sm["score"]  # deeper pullback => more oversold => higher score


def test_score_universe_has_required_columns():
    close = uptrend_dip(220, [203.3, 195, 185, 175, 165])
    df = rev.score_universe({"X": close}, PARAMS)
    for col in ("symbol", "score", "eligible"):
        assert col in df.columns
