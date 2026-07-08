"""业绩预期 signal: pure aggregation + score + store roundtrip."""
import math
import tempfile
from pathlib import Path

import pandas as pd

from stockagent.research import earnings as er
from stockagent.data import Store

PARAMS = {"research": {"earnings": {"min_coverage": 0.30, "min_matched": 5}}}


def _holdings(weights: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": list(weights), "weight": list(weights.values())})

def _forecast(items: dict) -> pd.DataFrame:
    # items: {code: (yoy, type)}
    codes = list(items)
    return pd.DataFrame(
        {"yoy": [items[c][0] for c in codes], "type": [items[c][1] for c in codes]},
        index=codes)


# ---------- aggregate_earnings ----------
def test_aggregate_weighted_median_buckets_coverage():
    h = _holdings({"A": 10, "B": 20, "C": 30, "D": 5})  # D unmatched
    fc = _forecast({"A": (50, "预增"), "B": (-20, "预减"), "C": (10, "略增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_holdings"] == 4 and s["n_matched"] == 3
    assert s["coverage"] == 60 / 65            # matched weight / total
    assert math.isclose(s["weighted_yoy"], (50 * 10 - 20 * 20 + 10 * 30) / 60)
    assert s["median_yoy"] == 10               # median(50,-20,10)
    assert math.isclose(s["bull_ratio"], 40 / 60)   # A+C
    assert math.isclose(s["bear_ratio"], 20 / 60)   # B


def test_aggregate_no_overlap_empty_coverage():
    h = _holdings({"X": 10, "Y": 20})
    fc = _forecast({"A": (50, "预增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 0 and s["coverage"] == 0.0
    assert math.isnan(s["weighted_yoy"])


def test_aggregate_extreme_yoy_no_crash():
    h = _holdings({"A": 50, "B": 50})
    fc = _forecast({"A": (-236, "增亏"), "B": (184, "预增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 2
    assert math.isclose(s["weighted_yoy"], (-236 + 184) / 2)
    assert s["median_yoy"] == (-236 + 184) / 2
    assert math.isclose(s["bear_ratio"], 0.5) and math.isclose(s["bull_ratio"], 0.5)


def test_aggregate_empty_inputs():
    assert er.aggregate_earnings(None, None)["n_matched"] == 0
    assert er.aggregate_earnings(pd.DataFrame(columns=["code", "weight"]),
                                 _forecast({"A": (10, "预增")}))["n_matched"] == 0


def test_aggregate_drops_nan_yoy_but_keeps_others():
    h = _holdings({"A": 10, "B": 10})
    fc = pd.DataFrame({"yoy": [10.0, None], "type": ["预增", "预减"]}, index=["A", "B"])
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 1                 # B dropped (NaN yoy)
    assert s["coverage"] == 0.5


# ---------- earnings_score ----------
def test_score_high_growth_band():
    sig = {"median_yoy": 50, "bull_ratio": 0.9, "bear_ratio": 0.05, "coverage": 0.9, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert label == er.LABEL_HIGH and score >= 70


def test_score_crash_band():
    sig = {"median_yoy": -120, "bull_ratio": 0.1, "bear_ratio": 0.9, "coverage": 0.8, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert label == er.LABEL_CRASH and score < 30


def test_score_insufficient_low_coverage():
    sig = {"median_yoy": 80, "bull_ratio": 0.9, "bear_ratio": 0.0, "coverage": 0.10, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


def test_score_insufficient_low_n():
    sig = {"median_yoy": 80, "bull_ratio": 0.9, "bear_ratio": 0.0, "coverage": 0.9, "n_matched": 2}
    score, label = er.earnings_score(sig, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


def test_score_none_signal():
    score, label = er.earnings_score(None, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


# ---------- store roundtrip ----------
def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_store_upsert_and_latest():
    st = _store()
    st.upsert_etf_earnings(
        [("159865", "20251231", -236.0, -200.0, 0.12, 0.88, 0.69, 39, 30)], source="em")
    st.upsert_etf_earnings(
        [("159865", "20250630", -50.0, -40.0, 0.20, 0.70, 0.60, 35, 25)], source="em")
    got = st.get_etf_earnings("159865")
    assert got["report_period"] == "20251231"     # latest wins
    assert got["weighted_yoy"] == -236.0
    assert got["n_matched"] == 30
    assert st.last_earnings_period() == "20251231"
    assert st.get_etf_earnings("NOPE") is None


def test_store_missing_returns_none():
    assert _store().get_etf_earnings("NOPE") is None
