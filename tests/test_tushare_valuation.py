"""tushare 迁移 1.1 个股估值腿: fetch_valuation_tushare 映射/单位/降级链 (ADR-0002)。
无网络——tushare_client.query 与 manager 依赖全部 monkeypatch。"""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc
from stockagent.data.store import Store


def _ts_raw(desc: bool = True) -> pd.DataFrame:
    """模拟 daily_basic 返回(含 NaN 值 + 万单位市值 + 可乱序)。"""
    rows = [
        ("20260911", 25.0, 20.0, 8.1, 12.0, 2.5, 0.4, 2.5e6, 2.4e6),
        ("20260910", 24.5, float("nan"), 8.0, 11.8, 2.5, 0.4, 2.49e6, 2.39e6),
        ("20260909", 24.0, 19.5, 7.9, 11.5, 2.4, 0.3, 2.48e6, 2.38e6),
    ]
    cols = ["trade_date", "pe_ttm", "pe", "pb", "ps_ttm", "dv_ratio",
            "turnover_rate", "total_mv", "circ_mv"]
    df = pd.DataFrame(rows, columns=cols)
    return df.iloc[::-1] if desc else df


# ---------- fetch_valuation_tushare ----------

def test_fetch_valuation_maps_units_and_sorts(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: _ts_raw())
    frames = fetcher.fetch_valuation_tushare("600519")
    assert set(frames) == set(fetcher.TS_VALUATION_MAP)  # 8 指标,无 pcf
    pe_ttm = frames["pe_ttm"]["value"]
    assert list(pe_ttm.index) == ["2026-09-09", "2026-09-10", "2026-09-11"]  # 升序
    # 万→亿: total_mv 2.5e6 万元 = 250 亿(对齐 baidu 历史存量单位)
    assert abs(float(frames["market_cap"]["value"].iloc[-1]) - 250.0) < 1e-6
    assert abs(float(frames["circ_mv"]["value"].iloc[-1]) - 240.0) < 1e-6
    # NaN 只影响所在指标: pe_static 少一行, pe_ttm 全 3 行
    assert len(frames["pe_static"]) == 2
    assert len(frames["pe_ttm"]) == 3


def test_fetch_valuation_ts_code_routing(monkeypatch):
    seen = {}

    def fake_query(api, **kw):
        seen.update(kw)
        return _ts_raw()

    monkeypatch.setattr(tc, "query", fake_query)
    fetcher.fetch_valuation_tushare("600519")
    assert seen["ts_code"] == "600519.SH"
    fetcher.fetch_valuation_tushare("300750")
    assert seen["ts_code"] == "300750.SZ"
    fetcher.fetch_valuation_tushare("830001")   # 北交所: baidu/sina 都不支持,tushare 首次可得
    assert seen["ts_code"] == "830001.BJ"
    fetcher.fetch_valuation_tushare("920001")
    assert seen["ts_code"] == "920001.SH"


def test_fetch_valuation_empty_raises(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_valuation_tushare("600519")


# ---------- manager 回退链 ----------

def _dm() -> tuple[mgr.DataManager, Store]:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    st = Store(Path(f.name))
    return mgr.DataManager(store=st), st


def _baidu_frame() -> pd.DataFrame:
    return pd.DataFrame({"value": [24.0, 25.0]},
                        index=["2026-09-09", "2026-09-11"])


def test_manager_tushare_first_no_baidu_call(monkeypatch):
    """回退链: tushare 成功时不再碰 baidu(数据源优先级原则)。"""
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_valuation_tushare",
                        lambda code: {"pe_ttm": _baidu_frame()})

    def _boom(*a, **kw):
        raise AssertionError("baidu 不应被调用(tushare 优先)")
    monkeypatch.setattr(fetcher, "fetch_stock_valuation", _boom)
    res = dm.update_stock_valuation(symbols=["600519"])
    assert res["600519"]["pe_ttm"] == 2
    df = st.get_stock_valuation_series("600519", "pe_ttm")
    assert len(df) == 2


def test_manager_falls_back_to_baidu_on_tushare_failure(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def _boom(code):
        raise fetcher.TushareError("daily_basic failed")
    monkeypatch.setattr(fetcher, "fetch_valuation_tushare", _boom)
    monkeypatch.setattr(fetcher, "fetch_stock_valuation",
                        lambda sym, ind, **kw: _baidu_frame())
    res = dm.update_stock_valuation(symbols=["600519"])
    assert res["600519"]["pe_ttm"] == 2
    df = st.get_stock_valuation_series("600519", "pe_ttm")
    assert len(df) == 2  # baidu 落库


def test_manager_no_token_uses_baidu(monkeypatch):
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: False)
    monkeypatch.setattr(fetcher, "fetch_stock_valuation",
                        lambda sym, ind, **kw: _baidu_frame())
    res = dm.update_stock_valuation(symbols=["600519"],
                                    indicators=["pe_ttm", "pb"])
    assert set(res["600519"]) == {"pe_ttm", "pb"}


def test_manager_explicit_indicator_not_in_tushare_fills_baidu(monkeypatch):
    """显式要 pcf(tushare 无此指标)→ baidu 补位。"""
    dm, st = _dm()
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_valuation_tushare",
                        lambda code: {"pe_ttm": _baidu_frame()})
    monkeypatch.setattr(fetcher, "fetch_stock_valuation",
                        lambda sym, ind, **kw: _baidu_frame())
    res = dm.update_stock_valuation(symbols=["600519"], indicators=["pcf"])
    assert res["600519"]["pcf"] == 2
    assert st.get_stock_valuation_series("600519", "pcf") is not None
