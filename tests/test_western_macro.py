"""Western-macro: ICE DXY reconstruction (pure function) + store round-trip.

Network-free: validates the reconstruction formula (constant + signs) against a hand-computed
reference snapshot, and the western_macro_series store path. Live accuracy vs the real ICE DXY
is a separate check (run on a machine where push2his.eastmoney.com forex is reachable).
"""
import pandas as pd
import pytest

from stockagent.data.fetcher import reconstruct_dxy, reconstruct_dxy_series
from stockagent.data.store import Store

# Late-2024-style snapshot (USDJPY~150, EURUSD~1.10) — real ICE DXY was ~103-106 then.
_SNAPSHOT = {"EURUSD": 1.10, "USDJPY": 150.0, "GBPUSD": 1.27,
             "USDCAD": 1.36, "USDSEK": 10.5, "USDCHF": 0.88}


def test_reconstruct_dxy_known_snapshot():
    # Hand-computed via the ICE formula with USD-base pairs (JPY/CAD/SEK/CHF) positive,
    # USD-quote pairs (EUR/GBP) negative: ≈ 103.0
    dxy = reconstruct_dxy(_SNAPSHOT)
    assert abs(dxy - 103.0) < 0.5


def test_reconstruct_dxy_signs():
    # USD-base pair up → DXY up; USD-quote pair up (= USD down) → DXY down.
    dxy0 = reconstruct_dxy(_SNAPSHOT)
    assert reconstruct_dxy({**_SNAPSHOT, "USDJPY": 160.0}) > dxy0   # USD up vs JPY
    assert reconstruct_dxy({**_SNAPSHOT, "USDCAD": 1.45}) > dxy0    # USD up vs CAD
    assert reconstruct_dxy({**_SNAPSHOT, "EURUSD": 1.15}) < dxy0    # EUR up (= USD down)
    assert reconstruct_dxy({**_SNAPSHOT, "GBPUSD": 1.33}) < dxy0    # GBP up (= USD down)


def test_reconstruct_dxy_missing_leg_raises():
    with pytest.raises(Exception):
        reconstruct_dxy({"EURUSD": 1.10, "USDJPY": 150.0})  # 2 of 6 legs


def test_reconstruct_dxy_series_inner_join():
    idx = ["2024-01-02", "2024-01-03", "2024-01-04"]
    legs = {
        "EURUSD": pd.Series([1.10, 1.11, 1.09], index=idx),
        "USDJPY": pd.Series([150.0, 151.0, 149.0], index=idx),
        "GBPUSD": pd.Series([1.27, 1.27, 1.26], index=idx),
        "USDCAD": pd.Series([1.36, 1.36, 1.35], index=idx),
        "USDSEK": pd.Series([10.5, 10.5, 10.4], index=idx),
        "USDCHF": pd.Series([0.88, 0.88, 0.89], index=idx),
    }
    s = reconstruct_dxy_series(legs)
    assert len(s) == 3
    assert list(s.index) == idx
    assert all(95.0 < v < 110.0 for v in s.values)


def test_reconstruct_dxy_series_skips_dates_missing_a_leg():
    # SEK missing on day 2 → that day dropped, others kept.
    legs = {
        "EURUSD": pd.Series([1.10, 1.11, 1.09], index=["d1", "d2", "d3"]),
        "USDJPY": pd.Series([150.0, 151.0, 149.0], index=["d1", "d2", "d3"]),
        "GBPUSD": pd.Series([1.27, 1.27, 1.26], index=["d1", "d2", "d3"]),
        "USDCAD": pd.Series([1.36, 1.36, 1.35], index=["d1", "d2", "d3"]),
        "USDSEK": pd.Series([10.5, None, 10.4], index=["d1", "d2", "d3"]),
        "USDCHF": pd.Series([0.88, 0.88, 0.89], index=["d1", "d2", "d3"]),
    }
    s = reconstruct_dxy_series(legs)
    assert list(s.index) == ["d1", "d3"]


def test_reconstruct_dxy_series_missing_whole_leg_empty():
    legs = {
        "EURUSD": pd.Series([1.10], index=["d1"]),
        "USDJPY": pd.Series([150.0], index=["d1"]),
        # GBPUSD/CAD/SEK/CHF entirely absent → cannot reconstruct
    }
    assert len(reconstruct_dxy_series(legs)) == 0


def test_store_western_roundtrip(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    df = pd.DataFrame([
        {"source": "ust", "symbol": "US10Y", "date": "2024-01-02", "close": 4.0},
        {"source": "dxy", "symbol": "DXY", "date": "2024-01-02", "close": 103.0},
    ])
    assert st.upsert_western_macro(df, source_tag="test") == 2
    # idempotent re-upsert
    assert st.upsert_western_macro(df, source_tag="test") == 2
    got = st.get_western_series("ust", "US10Y")
    assert len(got) == 1
    assert abs(got["close"].iloc[0] - 4.0) < 1e-9
    assert st.last_western_date("dxy", "DXY") == "2024-01-02"
    assert set(st.western_symbols()) == {"US10Y", "DXY"}
