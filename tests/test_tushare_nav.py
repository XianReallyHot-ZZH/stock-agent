"""tushare 迁移 1.7 ETF净值腿: fund_nav 映射 + _fetch_nav 回退链 (ADR-0002)。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc


def _fund_nav_raw(desc: bool = True) -> pd.DataFrame:
    rows = [("2026-09-11", "20260911", 1.2345, 1.5678),
            ("2026-09-10", "20260910", 1.2340, 1.5670),
            ("2026-09-09", "20260909", None, 1.5660)]
    df = pd.DataFrame(rows, columns=["ann_date", "nav_date", "unit_nav", "accum_nav"])
    return df.iloc[::-1] if desc else df


def test_fetch_etf_nav_tushare_maps(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _fund_nav_raw())
    df = fetcher.fetch_etf_nav_tushare("510300")
    assert list(df.index) == ["2026-09-10", "2026-09-11"]   # 升序; unit NaN 行剔除
    assert abs(float(df.loc["2026-09-11", "unit_nav"]) - 1.2345) < 1e-9
    assert abs(float(df.loc["2026-09-11", "acc_nav"]) - 1.5678) < 1e-9


def test_fetch_etf_nav_tushare_ts_code_routing(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _fund_nav_raw()
    monkeypatch.setattr(tc, "query", fake)
    fetcher.fetch_etf_nav_tushare("510300")
    assert seen["ts_code"] == "510300.SH"
    fetcher.fetch_etf_nav_tushare("159915")
    assert seen["ts_code"] == "159915.SZ"
    fetcher.fetch_etf_nav_tushare("161226")   # 商品 LOF 白银
    assert seen["ts_code"] == "161226.SZ"
    fetcher.fetch_etf_nav_tushare("501018")   # 南方原油 LOF(沪)
    assert seen["ts_code"] == "501018.SH"


def test_fetch_etf_nav_tushare_missing_accum(monkeypatch):
    raw = _fund_nav_raw().drop(columns=["accum_nav"])
    monkeypatch.setattr(tc, "query", lambda api, **kw: raw)
    df = fetcher.fetch_etf_nav_tushare("510300")
    assert len(df) == 2 and df["acc_nav"].isna().all()   # 缺列→NaN 不炸


def test_fetch_etf_nav_tushare_empty_raises(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_etf_nav_tushare("510300")


def test_fetch_etf_nav_money_etf_always_em(monkeypatch):
    """511990 货币ETF: 两源口径不同(对账FAIL),不查 tushare 直接抛→manager 降级 em。"""
    def boom(api, **kw):
        raise AssertionError("511990 不应触达 tushare")
    monkeypatch.setattr(tc, "query", boom)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_etf_nav_tushare("511990")


def test_fetch_nav_manager_money_etf_falls_to_em(monkeypatch):
    dm = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_etf_nav",
                        lambda sym, **kw: pd.DataFrame({"unit_nav": [100.03]}))
    df, src = dm._fetch_nav("511990", "20260101", "20260911")
    assert src == "em" and abs(float(df["unit_nav"].iloc[0]) - 100.03) < 1e-9


# ---------- manager _fetch_nav 回退链 ----------

def _dm() -> mgr.DataManager:
    return mgr.DataManager()


def test_fetch_nav_tushare_primary_tag(monkeypatch):
    dm = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_etf_nav_tushare",
                        lambda sym, **kw: pd.DataFrame({"unit_nav": [1.0]}))
    df, src = dm._fetch_nav("510300", "20260101", "20260911")
    assert src == "tushare_fund_nav" and len(df) == 1


def test_fetch_nav_falls_back_to_em(monkeypatch):
    dm = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_etf_nav_tushare",
                        lambda sym, **kw: (_ for _ in ()).throw(fetcher.TushareError("限频")))
    monkeypatch.setattr(fetcher, "fetch_etf_nav",
                        lambda sym, **kw: pd.DataFrame({"unit_nav": [2.0]}))
    df, src = dm._fetch_nav("510300", "20260101", "20260911")
    assert src == "em" and len(df) == 1


def test_fetch_nav_no_token_em(monkeypatch):
    dm = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: False)

    def boom(sym, **kw):
        raise AssertionError("tushare 不应被调用(无 token)")
    monkeypatch.setattr(fetcher, "fetch_etf_nav_tushare", boom)
    monkeypatch.setattr(fetcher, "fetch_etf_nav",
                        lambda sym, **kw: pd.DataFrame({"unit_nav": [3.0]}))
    _, src = dm._fetch_nav("510300", "20260101", "20260911")
    assert src == "em"
