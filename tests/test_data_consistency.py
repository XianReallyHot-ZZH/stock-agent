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
