"""Tests for broad-index data layer (V4 tracker): index_daily / index_pe / market_pb
store roundtrips + _index_prefix pure function. No network — DB + pure-fn only."""
import tempfile
from pathlib import Path

import pandas as pd

from stockagent.data import Store
from stockagent.data.fetcher import _index_prefix


def _store():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_index_prefix():
    # 上证/中证系列 (000xxx) -> sh; 深证 (399xxx) -> sz
    assert _index_prefix("000016") == "sh"  # 上证50
    assert _index_prefix("000300") == "sh"  # 沪深300
    assert _index_prefix("000905") == "sh"  # 中证500
    assert _index_prefix("000688") == "sh"  # 科创50
    assert _index_prefix("399006") == "sz"  # 创业板指


def test_index_daily_roundtrip():
    st = _store()
    df = pd.DataFrame(
        {"open": [3800.0], "high": [3850.0], "low": [3780.0], "close": [3820.0],
         "volume": [1.2e9]},
        index=["2026-07-17"],
    )
    assert st.upsert_index_daily("000300", df, source="sina_raw") == 1
    got = st.get_index_daily_series("000300")
    assert len(got) == 1
    assert float(got["close"].iloc[0]) == 3820.0
    assert st.last_index_daily_date("000300") == "2026-07-17"


def test_index_daily_upsert_is_idempotent():
    st = _store()
    df = pd.DataFrame({"close": [3820.0]}, index=["2026-07-17"])
    st.upsert_index_daily("000300", df)
    df2 = pd.DataFrame({"close": [3900.0]}, index=["2026-07-17"])  # same date, update
    st.upsert_index_daily("000300", df2)
    got = st.get_index_daily_series("000300")
    assert len(got) == 1                       # no duplicate
    assert float(got["close"].iloc[0]) == 3900.0  # value updated


def test_index_pe_roundtrip():
    st = _store()
    df = pd.DataFrame(
        {"pe_ttm": [13.30, 13.45], "pe_median": [18.0, 18.2]},
        index=["2026-07-16", "2026-07-17"],
    )
    assert st.upsert_index_pe("沪深300", df, source="lg") == 2
    got = st.get_index_pe_series("沪深300")
    assert len(got) == 2
    assert float(got["pe_ttm"].iloc[-1]) == 13.45
    assert st.last_index_pe_date("沪深300") == "2026-07-17"


def test_market_pb_roundtrip():
    st = _store()
    df = pd.DataFrame(
        {"pb": [1.45, 1.46], "pb_median": [2.1, 2.12],
         "pct_all": [0.12, 0.13], "pct_10y": [0.15, 0.16]},
        index=["2026-07-16", "2026-07-17"],
    )
    assert st.upsert_market_pb(df, source="lg") == 2
    got = st.get_market_pb_series()
    assert len(got) == 2
    assert float(got["pb"].iloc[-1]) == 1.46
    assert float(got["pct_10y"].iloc[-1]) == 0.16


def test_market_pb_date_filter():
    st = _store()
    df = pd.DataFrame(
        {"pb": [1.4, 1.5, 1.6]},
        index=["2026-06-01", "2026-07-01", "2026-07-17"],
    )
    st.upsert_market_pb(df)
    got = st.get_market_pb_series(start="2026-07-01")
    assert len(got) == 2                       # only the two July rows
    assert st.last_market_pb_date() == "2026-07-17"
