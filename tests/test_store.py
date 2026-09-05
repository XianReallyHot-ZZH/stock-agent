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


def test_scale_roundtrip():
    st = _store()
    rows = [
        ("510300", "2026-07-02", 1.99e10, 0.001),
        ("510300", "2026-07-03", 1.71e10, -0.002),
        ("512480", "2026-07-03", 5.0e8, None),
    ]
    n = st.upsert_scale(rows, source="sse")
    assert n == 3
    s = st.get_scale_series("510300")
    assert len(s) == 2
    assert st.last_scale_date("510300") == "2026-07-03"
    # idempotent update
    st.upsert_scale([("510300", "2026-07-03", 1.80e10, None)], source="sse")
    s2 = st.get_scale_series("510300")
    assert len(s2) == 2  # no dup
    assert float(s2["shares"].iloc[-1]) == 1.80e10  # updated


def test_commodity_spot_roundtrip():
    """夜盘快照表(2026-09 提速改版):每品种留最新一条(UPSERT by variety),读回 DataFrame。"""
    st = _store()
    n = st.upsert_commodity_spot([("焦煤", "2026-09-05", 1680.5, "230000")], source="spot")
    assert n == 1
    # 同品种再拉 → 覆盖不重复
    st.upsert_commodity_spot([("焦煤", "2026-09-08", 1702.0, "230000"),
                              ("生猪", "2026-09-08", 11700.0, "")], source="spot")
    got = st.get_commodity_spot()
    jm = got[got["variety"] == "焦煤"]
    assert len(jm) == 1 and float(jm["price"].iloc[0]) == 1702.0
    assert jm["date"].iloc[0] == "2026-09-08" and jm["quote_time"].iloc[0] == "230000"
    assert set(got["variety"]) == {"焦煤", "生猪"}
