"""E0 一致预期快照: parse(财年滚动/列映射) + thin-guard + store roundtrip + manager 防护."""
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import Store
from stockagent.data import fetcher, manager as mgr
from stockagent.data.fetcher import FetchError, parse_consensus_table


def _raw() -> pd.DataFrame:
    """东财 stock_profit_forecast_em 整表的迷你仿真(列名照抄 probe P3, 2026-08-16)."""
    return pd.DataFrame({
        "代码": ["600519", "000001", "300750"],
        "名称": ["贵州茅台", "平安银行", "宁德时代"],
        "研报数": [44, 20, 40],
        "机构投资评级(近六个月)-买入": [37, 10, 33],
        "机构投资评级(近六个月)-增持": [7, 5, 7],
        "机构投资评级(近六个月)-中性": [0, 3, 0],
        "机构投资评级(近六个月)-减持": [0, 2, 0],
        "机构投资评级(近六个月)-卖出": [0, 0, 0],
        "2025预测每股收益": [68.0, 2.3, 12.0],
        "2026预测每股收益": [75.0, 2.5, 15.0],
        "2027预测每股收益": [83.0, 2.6, 18.0],
        "2028预测每股收益": [90.0, None, 20.0],
    })


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


# ---------- parse_consensus_table ----------
def test_parse_fy_rolling_alignment():
    """now=2026-08 → fy1=2026, fy2=2027(跳过已过的 2025)."""
    out = parse_consensus_table(_raw(), now=datetime(2026, 8, 16))
    assert list(out.index) == ["600519", "000001", "300750"]
    assert (out["fy1_year"] == 2026).all() and (out["fy2_year"] == 2027).all()
    assert out.loc["600519", "eps_fy1"] == 75.0 and out.loc["600519", "eps_fy2"] == 83.0
    assert out.loc["000001", "eps_fy2"] == 2.6


def test_parse_year_rollover():
    """列名翻滚后自动跟上: now=2027 → fy1=2027, fy2=2028."""
    out = parse_consensus_table(_raw(), now=datetime(2027, 1, 2))
    assert (out["fy1_year"] == 2027).all() and (out["fy2_year"] == 2028).all()
    assert out.loc["300750", "eps_fy1"] == 18.0


def test_parse_missing_year_columns():
    """未来年份列耗尽(只剩过去) → FetchError, 不产出垃圾."""
    df = _raw()[["代码", "名称", "研报数", "2025预测每股收益"]]
    with pytest.raises(FetchError):
        parse_consensus_table(df, now=datetime(2026, 8, 16))


def test_parse_rating_and_code_padding():
    out = parse_consensus_table(_raw(), now=datetime(2026, 8, 16))
    assert out.loc["600519", "rating_buy"] == 37 and out.loc["600519", "rating_sell"] == 0
    assert out.loc["000001", "n_reports"] == 20
    assert str(out.index[1]) == "000001"          # 6位 zfill 保持


def test_parse_nan_eps_survives():
    """now=2027 → fy2=2028, 000001 的 2028 列是 None → NaN 落列不崩."""
    out = parse_consensus_table(_raw(), now=datetime(2027, 1, 2))
    assert pd.isna(out.loc["000001", "eps_fy2"])
    assert out.loc["300750", "eps_fy2"] == 20.0


# ---------- fetch_consensus_snapshot thin-guard ----------
def test_fetch_thin_raises(monkeypatch):
    """整表 < min_rows 视为端点半死 → FetchError(调研 §6.2 静默写零教训)."""
    import akshare as ak
    monkeypatch.setattr(ak, "stock_profit_forecast_em", lambda **kw: _raw())
    with pytest.raises(FetchError):
        fetcher.fetch_consensus_snapshot(min_rows=1000)


def test_fetch_ok_passes_guard(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "stock_profit_forecast_em", lambda **kw: _raw())
    out = fetcher.fetch_consensus_snapshot(min_rows=3)
    assert len(out) == 3 and "eps_fy1" in out.columns


# ---------- store roundtrip ----------
def test_store_upsert_idempotent_same_day():
    st = _store()
    snap = parse_consensus_table(_raw(), now=datetime(2026, 8, 16))
    n1 = st.upsert_consensus(snap, fetch_date="20260816")
    n2 = st.upsert_consensus(snap, fetch_date="20260816")   # 同日重跑 → 覆盖不重复
    assert n1 == 3 and n2 == 3
    assert st.consensus_snapshot_dates() == ["20260816"]


def test_store_two_snapshots_pick_latest():
    st = _store()
    week1 = parse_consensus_table(_raw(), now=datetime(2026, 8, 16))
    week2 = week1.copy()
    week2.loc["600519", "eps_fy1"] = 76.5                    # 一周后上修
    st.upsert_consensus(week1, fetch_date="20260809")
    st.upsert_consensus(week2, fetch_date="20260816")
    assert st.consensus_snapshot_dates() == ["20260809", "20260816"]
    date, snap = st.get_consensus_snapshot()
    assert date == "20260816" and snap.loc["600519", "eps_fy1"] == 76.5
    date0, snap0 = st.get_consensus_snapshot(asof="20260812")
    assert date0 == "20260809" and snap0.loc["600519", "eps_fy1"] == 75.0


def test_store_empty_returns_empty():
    st = _store()
    date, snap = st.get_consensus_snapshot()
    assert date == "" and len(snap) == 0                     # E0 冷启动期(尚无快照)


# ---------- manager guard ----------
def test_manager_update_skips_on_fetch_failure(monkeypatch):
    """fetch 抛错 → 返回 0 且不写库不写 meta."""
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(mgr.fetcher, "fetch_consensus_snapshot",
                        lambda **kw: (_ for _ in ()).throw(FetchError("dead endpoint")))
    assert dm.update_consensus() == 0
    assert st.consensus_snapshot_dates() == []
    assert st.get_meta("last_consensus_update") is None


def test_manager_update_writes_snapshot(monkeypatch):
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(mgr.fetcher, "fetch_consensus_snapshot",
                        lambda **kw: parse_consensus_table(_raw(), now=datetime(2026, 8, 16)))
    n = dm.update_consensus(min_rows=3)
    assert n == 3
    assert len(st.consensus_snapshot_dates()) == 1
    assert st.get_meta("last_consensus_update") is not None
