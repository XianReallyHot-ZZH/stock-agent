"""tushare 迁移 1.12/1.13 应急 fallback(降级件·ADR-0002): 预告/快报 ann_date 窗扫重建 +
sina 17 项 income∪fina_indicator 重建 + manager 回退链。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc
from stockagent.data.store import Store


def _fc_raw(ann: str, end: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "600519.SH"],
        "ann_date": [ann, ann, ann],
        "end_date": [end, "20251231", end],
        "type": ["预增", "预增", "扭亏"],
        "p_change_min": [50.0, 50.0, None],
        "p_change_max": [100.0, 100.0, None],
    })


def test_fetch_earnings_forecast_tushare_window_scan(monkeypatch):
    """ann_date 窗扫→滤 end_date→幅度中值→同 code 取最晚公告。"""
    calls = []

    def fake(api, **kw):
        assert api == "forecast"
        calls.append(kw["ann_date"])
        if kw["ann_date"] in ("20260115", "20260202"):
            return _fc_raw(kw["ann_date"], "20251231")
        return pd.DataFrame()
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_earnings_forecast_tushare("20251231")
    # 窗=期末次日→+4个月,逐 bdate;至少扫了 1/15 与 2/2
    assert "20260115" in calls and "20260202" in calls
    row = df.loc["000001"]
    assert row["yoy"] == pytest.approx(75.0)                 # (50+100)/2
    assert row["type"] == "预增"
    assert row["announce_date"] == "2026-02-02"              # 最晚公告胜出(2/2 > 1/15)
    assert set(df.columns) == {"yoy", "type", "announce_date"}


def test_fetch_earnings_forecast_tushare_no_hit_raises(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_earnings_forecast_tushare("20260630")


def _ex_raw(ann: str) -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["000785.SZ"],
        "ann_date": [ann],
        "end_date": ["20251231"],
        "revenue": [1.134779e10],
        "n_income": [-9.987408e8],
        "yoy_net_profit": [7.693724e8],      # 实测=上年同期绝对值(非 %)
        "yoy_revenue": [1.0e10],
    })


def test_fetch_stock_express_tushare_yoy_selfcalc(monkeypatch):
    """express 的 yoy_* = 上年同期绝对值 → 同比自算 (本期−同期)/|同期|×100;同期 0 → NaN。"""
    monkeypatch.setattr(tc, "query", lambda api, **kw: (
        _ex_raw("20260331") if kw["ann_date"] == "20260331" else pd.DataFrame()))
    df = fetcher.fetch_stock_express_tushare("20251231")
    row = df.loc["000785"]
    assert row["np_yoy"] == pytest.approx((-9.987408e8 - 7.693724e8) / 7.693724e8 * 100)
    assert row["rev_yoy"] == pytest.approx((1.134779e10 - 1.0e10) / 1.0e10 * 100)
    assert row["announce_date"] == "2026-03-31"
    assert set(df.columns) == {"np_yoy", "rev_yoy", "announce_date"}


def test_fetch_stock_financials_tushare_14_metrics(monkeypatch):
    """income∪fina_indicator → 14/17 项长表;expense_ratio 自算;修订行取最晚 ann_date。"""
    inc = pd.DataFrame({
        "ann_date": ["20260420", "20260428"],
        "end_date": ["20260331", "20260331"],
        "total_revenue": [100.0, 110.0], "oper_cost": [60.0, 66.0],
        "sell_exp": [5.0, 5.0], "admin_exp": [3.0, 3.0], "fin_exp": [2.0, 2.0],
        "n_income": [12.0, 13.0], "n_income_attr_p": [11.0, 12.5],
    })
    fi = pd.DataFrame({
        "ann_date": ["20260428"], "end_date": ["20260331"],
        "eps": [1.0], "profit_dedt": [11.5], "roe": [10.0], "roa": [5.0],
        "grossprofit_margin": [40.0], "netprofit_margin": [11.4],
        "debt_to_assets": [50.0], "bps": [10.0], "ocfps": [1.2],
    })

    def fake(api, **kw):
        assert kw["ts_code"] == "600519.SH"
        return inc if api == "income" else fi
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_stock_financials_tushare("600519")
    got = {(r["report_period"], r["metric"]): r["value"] for r in df.to_dict("records")}
    assert got[("20260331", "revenue")] == pytest.approx(110.0)      # 修订行(晚 ann)胜出
    assert got[("20260331", "net_profit")] == pytest.approx(12.5)
    assert got[("20260331", "np_deducted")] == pytest.approx(11.5)
    assert got[("20260331", "debt_ratio")] == pytest.approx(50.0)
    assert got[("20260331", "expense_ratio")] == pytest.approx((5 + 3 + 2) / 110 * 100)
    metrics = {m for (_, m) in got}
    assert len(metrics) == 14                                       # 17−3(equity_total/goodwill/ocf)
    assert not ({"equity_total", "goodwill", "ocf"} & metrics)


def test_fetch_stock_financials_tushare_all_empty_raises(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_stock_financials_tushare("600519")


# ---------- manager 回退链 ----------

def _dm(tmp_path) -> tuple[mgr.DataManager, Store]:
    st = Store(tmp_path / "t.db")
    return mgr.DataManager(store=st), st


def test_update_stock_express_fallback_chain(monkeypatch, tmp_path):
    """东财挂 → tushare 应急成功入库(source=ts_express);东财好 → tushare 不触达。"""
    dm, st = _dm(tmp_path)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_stock_express",
                        lambda p: (_ for _ in ()).throw(fetcher.FetchError("东财被拦")))
    codes = [f"{i:06d}" for i in range(12)]                 # 过 express 地板(10 行)
    monkeypatch.setattr(fetcher, "fetch_stock_express_tushare",
                        lambda p: pd.DataFrame({"np_yoy": [10.0] * 12, "rev_yoy": [5.0] * 12,
                                                "announce_date": ["2026-03-31"] * 12},
                                               index=codes))
    out = dm.update_stock_express(periods=["20251231"])
    assert out["20251231"] == 12
    with st._conn() as c:
        src = c.execute("SELECT source FROM stock_express").fetchone()[0]
    assert src == "ts_express"
    # 东财恢复 → 不再触达 tushare
    def boom(p):
        raise AssertionError("tushare 不应被调用(东财优先)")
    monkeypatch.setattr(fetcher, "fetch_stock_express",
                        lambda p: pd.DataFrame({"np_yoy": [11.0] * 12, "rev_yoy": [6.0] * 12,
                                                "announce_date": ["2026-03-30"] * 12},
                                               index=codes))
    monkeypatch.setattr(fetcher, "fetch_stock_express_tushare", boom)
    dm.update_stock_express(periods=["20251231"])
    with st._conn() as c:
        row = c.execute("SELECT np_yoy,source FROM stock_express").fetchone()
    assert row[0] == 11.0 and row[1] == "em_yjkb"


def test_update_stock_express_both_fail_skips(monkeypatch, tmp_path):
    dm, st = _dm(tmp_path)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_stock_express",
                        lambda p: (_ for _ in ()).throw(fetcher.FetchError("拦")))
    monkeypatch.setattr(fetcher, "fetch_stock_express_tushare",
                        lambda p: (_ for _ in ()).throw(fetcher.TushareError("限频")))
    out = dm.update_stock_express(periods=["20251231"])
    assert out == {"20251231": 0}


def test_update_stock_financials_fallback(monkeypatch, tmp_path):
    dm, st = _dm(tmp_path)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(dm, "STOCK_WATCHLIST", ["600519"])
    monkeypatch.setattr(fetcher, "fetch_stock_financials",
                        lambda s: (_ for _ in ()).throw(fetcher.FetchError("sina 拦")))
    monkeypatch.setattr(fetcher, "fetch_stock_financials_tushare", lambda s: pd.DataFrame(
        [{"report_period": "20260331", "metric": "revenue", "value": 110.0}]))
    out = dm.update_stock_financials()
    assert out["600519"] == 1
    with st._conn() as c:
        src = c.execute("SELECT source FROM stock_financials LIMIT 1").fetchone()[0]
    assert src == "ts_income_fi"
    # 无 token: sina 失败直接 0(不炸)
    monkeypatch.setattr(tc, "has_token", lambda: False)
    assert dm.update_stock_financials() == {"600519": 0}


def test_update_etf_earnings_forecast_fallback(monkeypatch, tmp_path):
    """预告面板: 东财挂 → tushare 应急窗扫,stock_forecast source=ts_forecast(空 symbols 只验面板落库);
    年报期 floor=1000 地板对应急面板同样生效(薄表诚实拒写)。"""
    dm, st = _dm(tmp_path)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(dm.config, "all_symbols", lambda: [])
    monkeypatch.setattr(fetcher, "fetch_earnings_forecast",
                        lambda p: (_ for _ in ()).throw(fetcher.FetchError("东财被拦")))
    rows = pd.DataFrame({"yoy": [75.0] * 120, "type": ["预增"] * 120,
                         "announce_date": ["2026-07-15"] * 120},
                        index=[f"{i:06d}" for i in range(120)])
    monkeypatch.setattr(fetcher, "fetch_earnings_forecast_tushare", lambda p: rows)
    n = dm.update_etf_earnings(report_period="20260630")   # 中报期 floor=100 → 120 行过地板
    assert n == 0                                          # 无 ETF symbols → 0 行链聚合
    with st._conn() as c:
        cnt, src = c.execute("SELECT COUNT(*), MAX(source) FROM stock_forecast").fetchone()
    assert cnt == 120 and src == "ts_forecast"
    # 年报期(地板 1000)薄应急面板 → 拒写
    monkeypatch.setattr(fetcher, "fetch_earnings_forecast_tushare",
                        lambda p: rows.iloc[:150])
    dm.update_etf_earnings(report_period="20251231")
    with st._conn() as c:
        cnt = c.execute("SELECT COUNT(*) FROM stock_forecast WHERE report_period='20251231'").fetchone()[0]
    assert cnt == 0
