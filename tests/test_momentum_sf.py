"""Tests for momentum_sf combo signal."""
import numpy as np
import pandas as pd

from stockagent.engine import momentum as mom
from stockagent.engine.signals import momentum_sf as msf

PARAMS = {
    "rotation": {
        "momentum": {"windows": [20, 60, 120], "weights": [0.2, 0.3, 0.5]},
        "trend_gate_ma": 60,
        "share_flow": {},
    },
}


def _make_close(n=200, drift=0.003, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(100 * np.cumprod(1 + drift + rng.normal(0, 0.01, n)), dtype=float)


def test_momentum_ranking_preserved():
    """Without share data, momentum_sf behaves like pure momentum."""
    strong = _make_close(200, drift=0.005, seed=1)
    weak = _make_close(200, drift=-0.003, seed=2)
    close_by = {"STRONG": strong, "WEAK": weak}
    scored_mom = mom.score_universe(close_by, PARAMS)
    scored_ms = msf.score_universe(close_by, PARAMS, ctx={"share": {}})
    # same ranking (no share data → no filter)
    assert scored_ms.iloc[0]["symbol"] == scored_mom.iloc[0]["symbol"]


def test_distributing_filtered_out():
    """A strong-momentum symbol with DISTRIBUTING shares → filtered to ineligible."""
    strong = _make_close(200, drift=0.005, seed=1)  # strong uptrend → momentum eligible
    shares_falling = pd.Series(np.linspace(200, 50, 200), dtype=float)  # shares dropping → DISTRIBUTING

    # pure momentum: eligible
    scored_mom = mom.score_universe({"X": strong}, PARAMS)
    assert scored_mom.iloc[0]["eligible"]

    # momentum_sf with distributing shares: filtered
    scored_ms = msf.score_universe(
        {"X": strong}, PARAMS, ctx={"share": {"X": shares_falling}}
    )
    assert not scored_ms.iloc[0]["eligible"]


def test_accumulating_not_filtered():
    """A strong-momentum symbol with ACCUMULATING shares → stays eligible."""
    strong = _make_close(200, drift=0.005, seed=1)
    shares_rising = pd.Series(np.linspace(50, 200, 200), dtype=float)

    scored = msf.score_universe(
        {"X": strong}, PARAMS, ctx={"share": {"X": shares_rising}}
    )
    assert scored.iloc[0]["eligible"]


def test_describe_shows_warning_for_distributing():
    strong = _make_close(200, drift=0.005, seed=1)
    shares_falling = pd.Series(np.linspace(200, 50, 200), dtype=float)
    desc = msf.describe_symbol(strong, PARAMS, ctx={"symbol": "X", "share": {"X": shares_falling}})
    assert "机构撤资" in desc["summary"]
    assert not desc["eligible"]
