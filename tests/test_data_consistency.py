"""Basis-consistency guard: incremental price updates must not mix 复权 bases.

Regression for the 2026-07-07 bug where a few ETFs got an `eastmoney_hfq` point on the
latest day mixed into a `sina_raw` history, inflating trend scores (食品饮料 0.396 -> 1.502
overnight -> 性价比 77, a fake #2). The fix: derive the fetch basis from existing history
and reject cross-family contamination.
"""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import Store
from stockagent.data.fetcher import (
    _adapter_plan,
    _eastmoney_adjust,
    _fetch_baostock,
    _fetch_eastmoney,
    _fetch_sina,
    is_basis_consistent,
    price_basis_family,
)


# ---------- price_basis_family ----------
@pytest.mark.parametrize("tag,expected", [
    ("sina_raw", "raw"),
    ("eastmoney_raw", "raw"),
    ("baostock_raw", "raw"),
    ("split_adj", "raw"),   # fix_splits.py output stays on raw scale
    ("eastmoney_hfq", "hfq"),
    ("baostock_hfq", "hfq"),
    ("eastmoney_qfq", "qfq"),
    ("", "unknown"),
    (None, "unknown"),
    ("something_else", "unknown"),
])
def test_price_basis_family(tag, expected):
    assert price_basis_family(tag) == expected


# ---------- is_basis_consistent ----------
def test_consistent_same_family():
    assert is_basis_consistent("sina_raw", "sina_raw") is True
    assert is_basis_consistent("baostock_raw", "sina_raw") is True  # both raw = compatible


def test_inconsistent_cross_family():
    # the actual bug: hfq point into raw history
    assert is_basis_consistent("eastmoney_hfq", "sina_raw") is False
    assert is_basis_consistent("sina_raw", "eastmoney_hfq") is False


def test_consistent_when_no_history():
    assert is_basis_consistent("eastmoney_hfq", None) is True
    assert is_basis_consistent("eastmoney_hfq", "") is True  # fresh symbol


# ---------- _eastmoney_adjust (raw token -> akshare '') ----------
@pytest.mark.parametrize("adj,expected", [
    ("raw", ""),   # our 不复权 token -> akshare ''
    ("", ""),
    (None, ""),
    ("hfq", "hfq"),
    ("qfq", "qfq"),
])
def test_eastmoney_adjust_translation(adj, expected):
    assert _eastmoney_adjust(adj) == expected


# ---------- _adapter_plan ----------
def test_adapter_plan_no_family_uses_default_order():
    plan = _adapter_plan("hfq", None)
    assert [fn for fn, _ in plan] == [_fetch_eastmoney, _fetch_sina, _fetch_baostock]
    assert all(a == "hfq" for _, a in plan)


def test_adapter_plan_raw_keeps_sina_and_uses_unfu_adjust():
    plan = dict((fn, adj) for fn, adj in _adapter_plan("hfq", "raw"))
    assert _fetch_sina in plan          # sina CAN emit raw (always does)
    assert _fetch_eastmoney in plan
    assert _fetch_baostock in plan
    assert plan[_fetch_eastmoney] == ""     # akshare fund_etf_hist_em: '' = 不复权
    assert plan[_fetch_baostock] == "raw"   # baostock adjustflag '3'


def test_adapter_plan_hfq_drops_sina():
    plan = _adapter_plan("hfq", "hfq")
    fns = [fn for fn, _ in plan]
    assert _fetch_sina not in fns        # sina CANNOT emit hfq — would return raw
    assert _fetch_eastmoney in fns and _fetch_baostock in fns
    assert all(a == "hfq" for _, a in plan)


def test_adapter_plan_qfq_drops_sina():
    plan = _adapter_plan("hfq", "qfq")
    assert _fetch_sina not in [fn for fn, _ in plan]
    assert all(a == "qfq" for _, a in plan)


# ---------- Store.dominant_price_source ----------
def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def _row(date: str) -> pd.DataFrame:
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0, "amount": 0.0},
        index=[date],
    )


def test_dominant_source_returns_mode():
    st = _store()
    st.upsert_prices("X", _row("2026-07-01"), source="sina_raw")
    st.upsert_prices("X", _row("2026-07-02"), source="sina_raw")
    st.upsert_prices("X", _row("2026-07-03"), source="eastmoney_hfq")  # minority
    assert st.dominant_price_source("X") == "sina_raw"


def test_dominant_source_empty_returns_none():
    st = _store()
    assert st.dominant_price_source("NOPE") is None


# ---------- DataManager.update_symbol end-to-end (the actual bug scenario) ----------
def test_update_symbol_rejects_hfq_into_raw_history(monkeypatch):
    """Regression: sina fails on the latest day, eastmoney wins with hfq — must NOT upsert."""
    from stockagent.data import manager as mgr

    st = _store()
    st.upsert_prices("X", _row("2026-07-03"), source="sina_raw")  # raw history
    dm = mgr.DataManager(store=st)

    hfq_df = pd.DataFrame(
        {"open": 1.5, "high": 1.5, "low": 1.5, "close": 1.5, "volume": 0.0, "amount": 0.0},
        index=["2026-07-06"],
    )
    monkeypatch.setattr(mgr.fetcher, "fetch_etf_daily",
                        lambda *a, **k: (hfq_df, "eastmoney_hfq"))
    assert dm.update_symbol("X") == 0                 # skipped
    assert st.dominant_price_source("X") == "sina_raw"  # basis unchanged
    assert st.last_date("X") == "2026-07-03"           # no hfq point written


def test_update_symbol_accepts_same_family(monkeypatch):
    from stockagent.data import manager as mgr

    st = _store()
    st.upsert_prices("X", _row("2026-07-03"), source="sina_raw")
    dm = mgr.DataManager(store=st)

    raw_df = pd.DataFrame(
        {"open": 0.4, "high": 0.4, "low": 0.4, "close": 0.4, "volume": 0.0, "amount": 0.0},
        index=["2026-07-06"],
    )
    monkeypatch.setattr(mgr.fetcher, "fetch_etf_daily",
                        lambda *a, **k: (raw_df, "sina_raw"))
    assert dm.update_symbol("X") == 1                 # accepted
    assert st.last_date("X") == "2026-07-06"


def test_update_symbol_accepts_raw_into_split_adj_history(monkeypatch):
    """Regression: ETFs whose history was tagged split_adj by fix_splits.py must still
    accept raw incremental updates — otherwise they freeze at their last fix_splits date
    and the documented update_all -> fix_splits workflow can never land new raw points.
    """
    from stockagent.data import manager as mgr

    st = _store()
    st.upsert_prices("X", _row("2026-07-03"), source="split_adj")  # split-adjusted history
    dm = mgr.DataManager(store=st)

    raw_df = pd.DataFrame(
        {"open": 0.4, "high": 0.4, "low": 0.4, "close": 0.4, "volume": 0.0, "amount": 0.0},
        index=["2026-07-17"],
    )
    monkeypatch.setattr(mgr.fetcher, "fetch_etf_daily",
                        lambda *a, **k: (raw_df, "sina_raw"))
    assert dm.update_symbol("X") == 1                 # accepted (was skipped before fix)
    assert st.last_date("X") == "2026-07-17"


# ---------- commodity_map ↔ watchlist ↔ COMMODITY_CODES consistency ----------
def test_commodity_map_varieties_all_backfilled():
    """commodity_map 指向的品种必须都在 fetcher.COMMODITY_CODES —— 否则该股 A 类商品信号静默失效
    (variety 拼错/漏加进 COMMODITY_CODES 都会被这条抓住)。"""
    from stockagent.config import get_config
    from stockagent.data import fetcher
    cm = (get_config().params.get("stock", {}) or {}).get("commodity_map", {}) or {}
    bad = {s: v for s, v in cm.items() if v not in fetcher.COMMODITY_CODES}
    assert not bad, f"commodity_map 指向未回填品种: {bad}"


def test_watchlist_stocks_all_have_names():
    """STOCK_WATCHLIST 每只都有 STOCK_NAMES 条目(看板不露裸代码)。"""
    from stockagent.data.manager import DataManager
    missing = [s for s in DataManager.STOCK_WATCHLIST if s not in DataManager.STOCK_NAMES]
    assert not missing, f"缺名字: {missing}"


def test_new_commodity_stocks_wired():
    """8 只新品种周期股已写进 watchlist + commodity_map(回归守卫:防误删/拼错)。"""
    from stockagent.config import get_config
    from stockagent.data.manager import DataManager
    cm = (get_config().params.get("stock", {}) or {}).get("commodity_map", {}) or {}
    expected = {"601600": "铝", "000060": "锌", "601969": "铁矿石", "000983": "焦煤",
                "000603": "白银", "601636": "玻璃", "000683": "纯碱", "002714": "生猪"}
    for code, var in expected.items():
        assert code in DataManager.STOCK_WATCHLIST, f"{code} 不在观察池"
        assert cm.get(code) == var, f"{code} 映射应为 {var}, 实际 {cm.get(code)}"


def test_each_commodity_has_multiple_watchlist_stocks():
    """被观察池覆盖的商品至少 2 只映射股(单只=个股信号单一不稳);2026-09 扩容品种
    (LPG/尿素/豆粕/玉米)为**纯观测行**——不进 commodity_map(豆粕/玉米对养殖是反向暴露,
    grilling Q3 决策),映射覆盖要求只约束原 13 种。"""
    from collections import Counter
    from stockagent.config import get_config
    from stockagent.data import fetcher
    from stockagent.data.manager import DataManager
    cm = (get_config().params.get("stock", {}) or {}).get("commodity_map", {}) or {}
    watch = set(DataManager.STOCK_WATCHLIST)
    counts = Counter(v for s, v in cm.items() if s in watch)
    mapped_required = set(fetcher.COMMODITY_CODES) - {"LPG", "尿素", "豆粕", "玉米"}
    assert set(counts) == mapped_required, \
        f"未覆盖商品: {mapped_required - set(counts)} | 意外映射: {set(counts) - mapped_required}"
    single = [v for v, n in counts.items() if n < 2]
    assert not single, f"商品仅 1 只观察池股票: {single}"
