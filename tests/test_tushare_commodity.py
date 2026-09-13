"""tushare 迁移 1.11 商品日线: fut_daily 主力连续映射 + 回退链 (ADR-0002)。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc


def _fut_raw(ts_code: str = "RB.SHF") -> pd.DataFrame:
    return pd.DataFrame({"ts_code": [ts_code] * 2,
                         "trade_date": ["20260911", "20260910"],
                         "close": [3120.0, 3123.0]})


def test_fetch_commodity_price_tushare_maps(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _fut_raw(kw.get("ts_code", "RB.SHF"))
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_commodity_price_tushare(["螺纹钢"])
    assert list(df["date"]) == ["2026-09-10", "2026-09-11"]   # 升序
    assert abs(float(df["close"].iloc[-1]) - 3120.0) < 1e-9
    assert seen["ts_code"] == "RB.SHF"
    assert seen["start_date"] == "20200101"


def test_fetch_commodity_price_tushare_suffix_routing(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _fut_raw()
    monkeypatch.setattr(tc, "query", fake)
    for v, expect in [("原油", "SC.INE"), ("玻璃", "FG.ZCE"),
                      ("生猪", "LH.DCE"), ("黄金", "AU.SHF"), ("螺纹钢", "RB.SHF")]:
        fetcher.fetch_commodity_price_tushare([v])
        assert seen["ts_code"] == expect, v


def test_fetch_commodity_price_tushare_excluded_varieties(monkeypatch):
    """碳酸锂(广期)/LPG(郑商) tushare 无主力连续(2026-09-13 实测)——EXCLUDE 跳过,
    全 EXCLUDE 列表 → raise(调用方走 sina 补位)。"""
    def boom(api, **kw):
        raise AssertionError("EXCLUDE 品种不应触达 tushare")
    monkeypatch.setattr(tc, "query", boom)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_commodity_price_tushare(["碳酸锂", "LPG"])


def test_fetch_commodity_price_tushare_partial_skip(monkeypatch):
    """失败品种跳过,成功品种保留;全失败才 raise。"""
    calls = {"n": 0}

    def fake(api, **kw):
        calls["n"] += 1
        if kw["ts_code"].startswith("LC"):     # 碳酸锂(广期)模拟失败
            raise fetcher.TushareError("接口抽风")
        return _fut_raw(kw["ts_code"])
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_commodity_price_tushare(["碳酸锂", "螺纹钢"])
    assert set(df["variety"]) == {"螺纹钢"}

    def all_fail(api, **kw):
        raise fetcher.TushareError("全挂")
    monkeypatch.setattr(tc, "query", all_fail)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_commodity_price_tushare(["螺纹钢"])


def test_update_commodity_price_fallback_chain(monkeypatch, tmp_path):
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_commodity_price_tushare",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["螺纹钢"], "date": ["2026-09-11"], "close": [3120.0]}))
    # 碳酸锂(EXCLUDE)由 sina 补位
    monkeypatch.setattr(fetcher, "fetch_commodity_price",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["碳酸锂"], "date": ["2026-09-11"], "close": [71000.0]}))
    res = dm.update_commodity_price(varieties=["螺纹钢", "碳酸锂"])
    assert res["螺纹钢"] == 1 and res["碳酸锂"] == 1
    with st._conn() as c:
        srcs = dict(c.execute("SELECT variety, source FROM commodity_price").fetchall())
    assert srcs["螺纹钢"] == "tushare_fut_daily"
    assert srcs["碳酸锂"] == "akshare_futures"     # EXCLUDE 品种 sina 补位
    # tushare 整体挂 → sina 全量兜底
    monkeypatch.setattr(fetcher, "fetch_commodity_price_tushare",
                        lambda varieties, start, end: (_ for _ in ()).throw(
                            fetcher.TushareError("限频")))
    monkeypatch.setattr(fetcher, "fetch_commodity_price",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["螺纹钢"], "date": ["2026-09-11"], "close": [3118.0]}))
    res = dm.update_commodity_price(varieties=["螺纹钢"])
    assert res["螺纹钢"] == 1
