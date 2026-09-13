"""tushare 批次2.2 两市成交额官方口径: daily_info 映射 + 回退链 (ADR-0002)。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc


def _di_raw() -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": "20260911", "ts_name": "上海市场", "amount": 5500.0},
        {"trade_date": "20260911", "ts_name": "深圳市场", "amount": 10145.78},
        {"trade_date": "20260911", "ts_name": "科创板", "amount": 2619.74},   # 分块行,应被滤掉
        {"trade_date": "20260910", "ts_name": "上海市场", "amount": 5400.0},
        {"trade_date": "20260910", "ts_name": "深圳市场", "amount": 8685.68},
    ])


def test_fetch_turnover_tushare_filters_and_units(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _di_raw())
    df = fetcher.fetch_market_turnover_tushare(start="2026-09-01", end="2026-09-30")
    assert list(df.index) == ["2026-09-10", "2026-09-11"]
    assert abs(float(df.loc["2026-09-11", "sse"]) - 5.5e11) < 1.0     # 亿→元
    assert abs(float(df.loc["2026-09-11", "sz"]) - 1.014578e12) < 1e3
    assert abs(float(df.loc["2026-09-11", "total"]) - 1.564578e12) < 1e3


def test_fetch_turnover_tushare_empty_raises(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame(
        {"trade_date": [], "ts_name": [], "amount": []}))
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_market_turnover_tushare()


def test_update_turnover_stays_baostock(monkeypatch, tmp_path):
    """批次2.2 判死: 即使有 token 也不切 daily_info(A股口径 vs baostock 全证券口径,
    中位差 86%,⑧地量实证校准在旧口径)。"""
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def boom(start=None, end=None):
        raise AssertionError("daily_info 不应被调用(2.2 判死保留 baostock)")
    monkeypatch.setattr(fetcher, "fetch_market_turnover_tushare", boom)
    monkeypatch.setattr(fetcher, "fetch_market_turnover",
                        lambda start=None, end=None: pd.DataFrame(
                            {"sse": [5.0e11], "sz": [1.0e12], "total": [1.5e12]},
                            index=["2026-09-11"]))
    assert dm.update_market_turnover() == 1
