"""tushare 迁移 1.8/1.9 指数日线+分红: 映射与回退链 (ADR-0002)。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc


# ---------- 1.8 fetch_index_daily_tushare ----------

def _idx_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000300.SH"] * 2,
        "trade_date": ["20260911", "20260910"],
        "open": [4560.1, 4567.9], "high": [4590.0, 4580.7],
        "low": [4550.2, 4545.4], "close": [4580.3, 4575.0],
        "vol": [1.7e8, 1.8e8], "amount": [4.9e8, 5.3e8]})


def test_fetch_index_daily_tushare_maps(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _idx_raw())
    df = fetcher.fetch_index_daily_tushare("000300")
    assert list(df.index) == ["2026-09-10", "2026-09-11"]   # 升序
    assert abs(float(df.loc["2026-09-11", "close"]) - 4580.3) < 1e-9
    assert "amount" not in df.columns                        # 表无此列,弃


def test_fetch_index_daily_tushare_ts_code_routing(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _idx_raw()
    monkeypatch.setattr(tc, "query", fake)
    fetcher.fetch_index_daily_tushare("399006")
    assert seen["ts_code"] == "399006.SZ"
    fetcher.fetch_index_daily_tushare("000001")
    assert seen["ts_code"] == "000001.SH"


# ---------- 1.9 fetch_stock_dividend_tushare ----------

def _div_raw() -> pd.DataFrame:
    return pd.DataFrame([
        {"ts_code": "600519.SH", "end_date": "20011231", "ann_date": "20020626",
         "div_proc": "实施", "cash_div_tax": 0.6, "stk_div": 0.1,
         "ex_date": "20020725"},
        {"ts_code": "600519.SH", "end_date": "20020630", "ann_date": "20020814",
         "div_proc": "股东大会通过", "cash_div_tax": None, "stk_div": 0.0,
         "ex_date": None},
    ])


def test_fetch_stock_dividend_tushare_maps(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _div_raw())
    df = fetcher.fetch_stock_dividend_tushare("600519")
    assert list(df.index) == ["2002-07-25"]                  # 只留 实施 且 ex_date 有值
    row = df.loc["2002-07-25"]
    assert abs(float(row["cash_per_share"]) - 0.6) < 1e-12   # cash_div_tax=每股税前(对账实证),不除10
    assert abs(float(row["stock_div_10"]) - 1.0) < 1e-12     # 每股送转0.1→每10股1.0
    assert float(row["trans_10"]) == 0.0                     # 送转合一
    assert row["announce_date"] == "2002-06-26"


def test_fetch_stock_dividend_tushare_empty(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    df = fetcher.fetch_stock_dividend_tushare("600519")
    assert len(df) == 0 and "cash_per_share" in df.columns


# ---------- manager 回退链 ----------

def _dm() -> mgr.DataManager:
    return mgr.DataManager()


def test_update_index_daily_stays_sina(monkeypatch, tmp_path):
    """1.8 判死保留 sina: 即使有 token 也不走 tushare(volume 单位沼泽,对账判决)。"""
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def boom(sym):
        raise AssertionError("tushare 不应被调用(1.8 判死保留 sina)")
    monkeypatch.setattr(fetcher, "fetch_index_daily_tushare", boom)
    monkeypatch.setattr(fetcher, "fetch_index_daily",
                        lambda sym: _idx_raw().set_index(
                            pd.Index(["2026-09-10", "2026-09-11"]))[[
                                "open", "high", "low", "close"]].assign(volume=[1.8e8, 1.7e8]))
    res = dm.update_index_daily(symbols=["000300"])
    assert res["000300"] == 2


def test_update_stock_dividend_fallback(monkeypatch, tmp_path):
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_stock_dividend_tushare",
                        lambda sym: (_ for _ in ()).throw(fetcher.TushareError("限频")))
    monkeypatch.setattr(fetcher, "fetch_stock_dividend",
                        lambda sym: pd.DataFrame(
                            {"announce_date": ["2026-06-01"], "cash_per_share": [0.5],
                             "stock_div_10": [0.0], "trans_10": [0.0]},
                            index=["2026-06-24"]))
    res = dm.update_stock_dividend(symbols=["600519"])
    assert res["600519"] == 1
    df = st.get_stock_dividend_series("600519")
    assert len(df) == 1 and abs(float(df["cash_per_share"].iloc[0]) - 0.5) < 1e-9
