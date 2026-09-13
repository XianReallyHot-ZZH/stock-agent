"""tushare 批次2.1 两融两市化: margin 双所透视 + COALESCE 列保留 + 回退链 (ADR-0002)。无网络。"""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc
from stockagent.data.store import Store


def _margin_raw(ex: str) -> pd.DataFrame:
    if ex == "SSE":
        return pd.DataFrame({"trade_date": ["20260911", "20260910"],
                             "exchange_id": ["SSE"] * 2, "rzye": [9000.0, 8900.0],
                             "rzrqye": [9100.0, 9000.0]})
    return pd.DataFrame({"trade_date": ["20260911", "20260910"],
                         "exchange_id": ["SZSE"] * 2, "rzye": [7000.0, 6900.0],
                         "rzrqye": [7100.0, 7000.0]})


def test_fetch_margin_tushare_sums_both_exchanges(monkeypatch):
    monkeypatch.setattr(tc, "query",
                        lambda api, **kw: _margin_raw(kw["exchange_id"]))
    df = fetcher.fetch_margin_tushare(start="2026-01-01", end="2026-12-31")
    assert list(df.index) == ["2026-09-10", "2026-09-11"]
    assert abs(float(df.loc["2026-09-11", "financing_sse"]) - 9000.0) < 1e-9
    assert abs(float(df.loc["2026-09-11", "financing_cs"]) - 16000.0) < 1e-9    # 沪+深
    assert abs(float(df.loc["2026-09-11", "total_margin_cs"]) - 16200.0) < 1e-9


def test_fetch_margin_tushare_single_side_day(monkeypatch):
    """单边有数据的日期: 合计=可得侧(另一所缺日)。"""
    def fake(api, **kw):
        if kw["exchange_id"] == "SSE":
            return _margin_raw("SSE")
        return pd.DataFrame({"trade_date": ["20260910"], "exchange_id": ["SZSE"],
                             "rzye": [6900.0], "rzrqye": [7000.0]})   # 深市只有 09-10
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_margin_tushare(start="2026-01-01", end="2026-12-31")
    # 09-11 只有沪: cs = 9000; 09-10 双边: cs = 15800
    assert abs(float(df.loc["2026-09-11", "financing_cs"]) - 9000.0) < 1e-9
    assert abs(float(df.loc["2026-09-10", "financing_cs"]) - 15800.0) < 1e-9


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_upsert_margin_coalesce_preserves_cs_on_akshare_refresh():
    """akshare 沪市腿(无 cs 列)重灌不抹 tushare 写入的合计列。"""
    s = _store()
    s.upsert_market_margin(pd.DataFrame(
        {"financing_sse": [9000.0], "total_margin_sse": [9100.0],
         "financing_cs": [16000.0], "total_margin_cs": [16200.0]},
        index=["2026-09-11"]), source="tushare_margin")
    s.upsert_market_margin(pd.DataFrame(
        {"financing_sse": [9001.0], "total_margin_sse": [9101.0]},
        index=["2026-09-11"]), source="sse")   # akshare 腿无 cs
    df = s.get_market_margin_series()
    assert abs(float(df.loc["2026-09-11", "financing_sse"]) - 9001.0) < 1e-9   # 刷新
    assert abs(float(df.loc["2026-09-11", "financing_cs"]) - 16000.0) < 1e-9   # 保留


def test_update_market_margin_tushare_first_and_full(monkeypatch, tmp_path):
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    seen = {}

    def fake_ts(start, end):
        seen["start"] = start
        return pd.DataFrame({"financing_sse": [9000.0], "total_margin_sse": [9100.0],
                             "financing_cs": [16000.0], "total_margin_cs": [16200.0]},
                            index=["2026-09-11"])
    monkeypatch.setattr(fetcher, "fetch_margin_tushare", fake_ts)
    monkeypatch.setattr(fetcher, "fetch_market_margin",
                        lambda start, end: (_ for _ in ()).throw(
                            AssertionError("akshare 不应被调用")))
    assert dm.update_market_margin(full=True) == 1
    assert seen["start"] == "2010-03-01"      # full=True 全量重灌


def test_update_market_margin_fallback_akshare(monkeypatch, tmp_path):
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_margin_tushare",
                        lambda start, end: (_ for _ in ()).throw(fetcher.TushareError("限频")))
    monkeypatch.setattr(fetcher, "fetch_market_margin",
                        lambda start, end: pd.DataFrame(
                            {"financing_sse": [8900.0]}, index=["2026-09-11"]))
    assert dm.update_market_margin() == 1
    df = st.get_market_margin_series()
    assert abs(float(df.loc["2026-09-11", "financing_sse"]) - 8900.0) < 1e-9
