"""E3 三环时效链: earnings_chain(环覆盖/广度/落点/时效) + store period getters + manager 管线."""
import math
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

from stockagent.data import Store
from stockagent.data import fetcher, manager as mgr
from stockagent.research import earnings as er


def _hold(weights: dict) -> pd.DataFrame:
    return pd.DataFrame({"code": list(weights), "weight": list(weights.values())})


def _fc(items: dict) -> pd.DataFrame:
    """items: {code: (yoy, type, announce_date)} → 预告帧契约."""
    codes = list(items)
    return pd.DataFrame({
        "yoy": [items[c][0] for c in codes],
        "type": [items[c][1] for c in codes],
        "announce_date": [items[c][2] for c in codes],
    }, index=codes)


def _perf(items: dict) -> pd.DataFrame:
    """items: {code: (np_yoy, rev_yoy, announce_date)} → 快报/正式报帧契约."""
    codes = list(items)
    return pd.DataFrame({
        "np_yoy": [items[c][0] for c in codes],
        "rev_yoy": [items[c][1] for c in codes],
        "announce_date": [items[c][2] for c in codes],
    }, index=codes)


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


# ---------- earnings_chain 纯函数 ----------
def test_chain_ring_weights_overlap_and_breadth():
    h = _hold({"600001": 40, "600002": 30, "600003": 30})
    fc = _fc({"600001": (50, "预增", "2026-07-10"), "600003": (-30, "预减", "2026-07-12")})
    ex = _perf({"600001": (55, 40, "2026-07-28")})            # 600001 快报落点 = 55 vs 预告50
    ac = _perf({"600002": (10, 8, "2026-08-14")})             # 600002 直接出正式报
    asof = datetime(2026, 8, 16)
    ch = er.earnings_chain(h, fc, ex, ac, asof=asof)
    assert math.isclose(ch["rings"]["forecast"], 0.7)          # 600001+600003
    assert math.isclose(ch["rings"]["express"], 0.4)           # 只有 600001
    assert math.isclose(ch["rings"]["actual"], 0.3)             # 只有 600002
    assert ch["n"] == {"forecast": 2, "express": 1, "actual": 1}
    assert math.isclose(ch["bull_ratio"], 40 / 70) and math.isclose(ch["bear_ratio"], 30 / 70)
    assert ch["latest"]["forecast"] == "2026-07-12" and ch["days"]["forecast"] == 35
    assert ch["latest"]["actual"] == "2026-08-14" and ch["days"]["actual"] == 2
    # 落点: 600001 np_yoy 55 vs 预告 50 → diff=+5 命中(±10pp 内)
    assert math.isclose(ch["express_check"]["hit"], 1.0)


def test_chain_express_check_bands():
    """diff>+10 保守 / |diff|<=10 命中 / diff<-10 落空."""
    h = _hold({"600001": 50, "600002": 30, "600003": 20})
    fc = _fc({"600001": (20, "预增", "2026-07-01"), "600002": (30, "预增", "2026-07-01"),
              "600003": (40, "预增", "2026-07-01")})
    ex = _perf({"600001": (45, 40, "2026-07-20"),              # +25 → 保守
                "600002": (35, 30, "2026-07-20"),              # +5 → 命中
                "600003": (20, 15, "2026-07-20")})             # −20 → 落空
    ch = er.earnings_chain(h, fc, ex, None)
    assert math.isclose(ch["express_check"]["conservative"], 0.5)
    assert math.isclose(ch["express_check"]["hit"], 0.3)
    assert math.isclose(ch["express_check"]["optimistic"], 0.2)


def test_chain_empty_and_missing_rings():
    ch = er.earnings_chain(None, None, None, None)
    assert ch["rings"] == {} and math.isnan(ch["bull_ratio"])
    h = _hold({"600001": 10})
    ch2 = er.earnings_chain(h, None, None, None)
    assert ch2["rings"]["forecast"] == 0.0 and ch2["n"]["express"] == 0
    assert math.isnan(ch2["express_check"]["hit"])             # 无两环交集 → NaN


def test_chain_asof_none_days_none():
    h = _hold({"600001": 10})
    fc = _fc({"600001": (50, "预增", "2026-07-10")})
    ch = er.earnings_chain(h, fc, None, None, asof=None)
    assert ch["latest"]["forecast"] == "2026-07-10" and ch["days"]["forecast"] is None


# ---------- fetch 解析 ----------
def test_fetch_express_parses(monkeypatch):
    import akshare as ak
    fake = pd.DataFrame({
        "股票代码": ["600000", "1"],
        "净利润-同比增长": [5.0, None],
        "营业收入-同比增长": [3.0, None],
        "公告日期": ["2026-07-28 00:00:00", "2026-07-29"],
    })
    monkeypatch.setattr(ak, "stock_yjkb_em", lambda **kw: fake)
    out = fetcher.fetch_stock_express("20260630")
    assert list(out.columns) == ["np_yoy", "rev_yoy", "announce_date"]
    assert out.index[1] == "000001"                            # zfill
    assert out.iloc[0]["announce_date"] == "2026-07-28"        # 截 10 字符


def test_fetch_report_actual_parses(monkeypatch):
    import akshare as ak
    fake = pd.DataFrame({
        "股票代码": ["600000"],
        "净利润-同比增长": [12.0],
        "营业总收入-同比增长": [8.0],
        "最新公告日期": ["2026-08-15"],
    })
    monkeypatch.setattr(ak, "stock_yjbb_em", lambda **kw: fake)
    out = fetcher.fetch_stock_report_actual("20260630")
    assert out.loc["600000", "np_yoy"] == 12.0


# ---------- store roundtrip ----------
def test_store_perf_roundtrip_and_period_getters():
    st = _store()
    rows = [("600000", "20260630", "2026-07-28", 5.0, 3.0),
            ("600001", "20260630", "2026-07-29", None, 2.0)]
    assert st.upsert_stock_express(rows, source="em") == 2
    assert st.upsert_stock_express(rows, source="em") == 2      # 幂等
    got = st.get_stock_express_period("20260630")
    assert len(got) == 2 and got.loc["600000", "np_yoy"] == 5.0
    assert pd.isna(got.loc["600001", "np_yoy"]) and list(got.columns)[:3] == ["np_yoy", "rev_yoy", "announce_date"]
    assert len(st.get_stock_express_period("20251231")) == 0
    st.upsert_stock_report_actual([("600000", "20260630", "2026-08-15", 6.0, 4.0)])
    assert st.get_stock_report_period("20260630").loc["600000", "np_yoy"] == 6.0


def test_store_forecast_period_getter():
    st = _store()
    st.upsert_stock_forecast([("600000", "20260630", 50.0, "预增", "2026-07-10")])
    got = st.get_stock_forecast_period("20260630")
    assert got.loc["600000", "type"] == "预增" and got.loc["600000", "announce_date"] == "2026-07-10"


# ---------- manager 管线 ----------
def test_manager_express_floor_guard(monkeypatch):
    """期行数 < floor(10) → 跳过不写库(快报中期稀疏 vs 端点半死的界线)."""
    st = _store()
    dm = mgr.DataManager(store=st)
    tiny = _perf({f"60000{i}": (1.0, 1.0, "2026-07-20") for i in range(5)})
    monkeypatch.setattr(mgr.fetcher, "fetch_stock_express", lambda p: tiny)
    r = dm.update_stock_express(periods=["20260630"])
    assert r["20260630"] == 0 and len(st.get_stock_express_period("20260630")) == 0


def test_manager_report_actual_writes(monkeypatch):
    st = _store()
    dm = mgr.DataManager(store=st)
    big = _perf({f"{600000 + i:06d}": (1.0, 1.0, "2026-08-15") for i in range(600)})
    monkeypatch.setattr(mgr.fetcher, "fetch_stock_report_actual", lambda p: big)
    r = dm.update_stock_report_actual(periods=["20260630"])
    assert r["20260630"] == 600 and len(st.get_stock_report_period("20260630")) == 600


def test_update_etf_earnings_persists_whole_market_forecast(monkeypatch):
    """E3 联动: update_etf_earnings 顺手把全市场预告面板落库(成分级链的底座)."""
    st = _store()
    dm = mgr.DataManager(store=st)
    fc = pd.DataFrame({
        "yoy": [20.0] * 150, "type": ["预增"] * 150,
        "announce_date": ["2026-07-15"] * 150,
    }, index=[f"{600000 + i:06d}" for i in range(150)])
    monkeypatch.setattr(mgr.fetcher, "fetch_earnings_forecast", lambda period, **kw: fc)
    cons = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(4)],
                         "name": ["A", "B", "C", "D"], "weight": [25.0] * 4,
                         "snapshot_date": ["2026-07-31"] * 4})
    st.upsert_constituents("399986", cons)
    n = dm.update_etf_earnings(symbols=["512800"], report_period="20260630")
    assert n == 1
    got = st.get_stock_forecast_period("20260630")
    assert len(got) == 150                                       # 全市场(不止成分 4 只)
    assert got.loc["600000", "announce_date"] == "2026-07-15"
