import numpy as np
import pandas as pd
import pytest

from stockagent.engine import momentum as mom


def make_close(n=200, start=10.0, drift=0.001, seed=0):
    rng = np.random.default_rng(seed)
    rets = drift + rng.normal(0, 0.01, n)
    px = start * np.cumprod(1 + rets)
    return pd.Series(px, dtype=float)


PARAMS = {
    "rotation": {
        "momentum": {"windows": [20, 60, 120], "weights": [0.2, 0.3, 0.5]},
        "trend_gate_ma": 60,
    }
}


def test_passes_trend_gate_when_above():
    # monotonically rising => close above its MA
    close = pd.Series(np.linspace(10, 20, 100), dtype=float)
    assert mom.passes_trend_gate(close, 60)


def test_fails_trend_gate_when_below():
    close = pd.Series(np.linspace(20, 10, 100), dtype=float)  # falling
    assert not mom.passes_trend_gate(close, 60)


def test_score_universe_ranks_stronger_higher():
    strong = make_close(n=200, drift=0.003, seed=1)   # uptrend
    weak = make_close(n=200, drift=-0.002, seed=2)    # downtrend
    df = mom.score_universe({"STRONG": strong, "WEAK": weak}, PARAMS)
    assert df.iloc[0]["symbol"] == "STRONG"
    assert df.loc[df.symbol == "STRONG", "score"].iloc[0] > df.loc[df.symbol == "WEAK", "score"].iloc[0]


def test_select_top_k_only_eligible():
    strong = make_close(n=200, drift=0.003, seed=1)
    weak = make_close(n=200, drift=-0.003, seed=2)  # below MA -> ineligible
    df = mom.score_universe({"STRONG": strong, "WEAK": weak}, PARAMS)
    picked = mom.select_top_k(df, k=1)
    assert picked == ["STRONG"]


def test_select_top_k_empty_when_all_ineligible():
    a = make_close(n=200, drift=-0.003, seed=3)
    b = make_close(n=200, drift=-0.004, seed=4)
    df = mom.score_universe({"A": a, "B": b}, PARAMS)
    assert mom.select_top_k(df, k=3) == []
