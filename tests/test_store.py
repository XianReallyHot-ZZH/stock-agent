import tempfile
from pathlib import Path

import pandas as pd

from stockagent.data import Store


def _store():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_fund_flow_roundtrip():
    st = _store()
    df = pd.DataFrame(
        {"net_inflow": [1e8, -2.0e8], "rank": [3, 5]},
        index=["2026-07-02", "2026-07-03"],
    )
    n = st.upsert_fund_flow("半导体", df, source="em")
    assert n == 2
    got = st.get_fund_flow("半导体")
    assert len(got) == 2
    assert list(got["net_inflow"]) == [1e8, -2.0e8]
    assert st.last_fund_flow_date("半导体") == "2026-07-03"
    assert "半导体" in st.fund_flow_sectors()


def test_fund_flow_upsert_is_idempotent():
    st = _store()
    df = pd.DataFrame({"net_inflow": [1e8]}, index=["2026-07-02"])
    st.upsert_fund_flow("银行", df)
    df2 = pd.DataFrame({"net_inflow": [5e8]}, index=["2026-07-02"])  # same date, update
    st.upsert_fund_flow("银行", df2)
    got = st.get_fund_flow("银行")
    assert len(got) == 1                 # no duplicate
    assert float(got["net_inflow"].iloc[0]) == 5e8  # value updated


def test_prices_roundtrip():
    st = _store()
    df = pd.DataFrame(
        {"open": [10.0], "high": [11.0], "low": [9.5], "close": [10.5],
         "volume": [1000], "amount": [10500]},
        index=["2026-07-02"],
    )
    assert st.upsert_prices("512480", df, source="test") == 1
    got = st.get_series("512480")
    assert len(got) == 1 and float(got["close"].iloc[0]) == 10.5
    assert st.last_date("512480") == "2026-07-02"
