import numpy as np
import pandas as pd

from stockagent.engine import indicators as ind
from stockagent.engine.signals import bb_macd as bb

PARAMS = {"rotation": {"bb_macd": {}}}


def uptrend_dip(n_up=280, dip=None):
    up = [50 + i * 0.5 for i in range(n_up)]
    return pd.Series(up + (dip or []), dtype=float)


def test_bb_params_default_merge():
    p = bb._bb_params({"rotation": {"bb_macd": {"pctb_low": 0.2}}})
    assert p["bb_period"] == 20
    assert p["pctb_low"] == 0.2          # override
    assert p["long_ma"] == 250           # default kept
    assert p["mode"] == "both"


def test_score_symbol_contract():
    info = bb.score_symbol(uptrend_dip(280), PARAMS)
    for k in ("score", "eligible", "above_ma", "last_close", "len"):
        assert k in info


def test_score_matches_formula():
    close = uptrend_dip(280, [190, 175, 160, 150, 148])
    info = bb.score_symbol(close, PARAMS)
    p = bb._bb_params(PARAMS)
    pb = ind.pctb(close, p["bb_period"], p["bb_std"])
    hist = ind.macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])[2]
    slope = bb._macd_hist_slope(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    last = float(close.iloc[-1])
    above = last > ind.sma(close, p["long_ma"])
    dip_hit = (not pd.isna(pb)) and (pb < p["pctb_low"]) and (not pd.isna(slope)) and (slope > 0)
    trend_hit = (not pd.isna(pb)) and (pb >= p["pctb_high"]) and (not pd.isna(hist)) and (hist > 0)
    assert info["eligible"] == bool(above and (dip_hit or trend_hit))
    if not pd.isna(hist):
        assert abs(info["score"] - hist) < 1e-9


def test_score_universe_columns():
    df = bb.score_universe({"X": uptrend_dip(280)}, PARAMS)
    for c in ("symbol", "score", "eligible"):
        assert c in df.columns


def test_describe_symbol_has_summary():
    d = bb.describe_symbol(uptrend_dip(280), PARAMS)
    assert "summary" in d and "%B" in d["summary"] and "MACD" in d["summary"]


def test_mode_dip_excludes_pure_trend():
    # pure strong uptrend: pctb high, hist>0 -> trend hit; mode=dip must reject it
    close = pd.Series(np.linspace(50, 150, 300), dtype=float)
    p = bb._bb_params({"rotation": {"bb_macd": {"mode": "dip"}}})
    info = bb.score_symbol(close, {"rotation": {"bb_macd": p}})
    assert not info["eligible"]
