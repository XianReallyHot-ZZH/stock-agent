"""候选池表 (V8): stock_spot(含市值/估值列) / stock_balance / pool_membership upsert 幂等
+ getter + spot 失败显式返回 0(不再静默降级 consensus 名称兜底)。无网络·合成帧。"""
import tempfile
from pathlib import Path

import pandas as pd

from stockagent.data.store import Store


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def _spot_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {"name": ["贵州茅台", "ST某某", "中国平安"], "close": [1500.0, 2.1, 48.0],
         "mktcap": [1.88e12, 4.2e9, 8.7e11], "float_mktcap": [1.88e12, 4.2e9, 8.7e11],
         "pe_dyn": [25.0, 40.0, 9.0], "pb": [8.1, 0.9, 1.1]},
        index=["600519", "000999", "000001"],
    )


# ---------- stock_spot(V8 扩列) ----------
def test_spot_upsert_same_day_overwrites():
    s = _store()
    assert s.upsert_stock_spot(_spot_frame(), "2026-08-16") == 3
    df2 = _spot_frame()
    df2.loc["000999", "name"] = "*ST某某"
    df2.loc["600519", "mktcap"] = 1.9e12
    assert s.upsert_stock_spot(df2, "2026-08-16") == 3
    latest = s.latest_stock_spot()
    assert len(latest) == 3
    assert latest.loc["000999", "name"] == "*ST某某"
    assert abs(float(latest.loc["600519", "mktcap"]) - 1.9e12) < 1.0


def test_spot_latest_takes_newest_date_and_carries_v8_cols():
    s = _store()
    s.upsert_stock_spot(_spot_frame(), "2026-08-15")
    s.upsert_stock_spot(_spot_frame(), "2026-08-16")
    s.upsert_stock_spot(_spot_frame(), "2026-08-10")   # 乱序不回退 latest
    latest = s.latest_stock_spot()
    assert len(latest) == 3
    for col in ("mktcap", "float_mktcap", "pe_dyn", "pb"):
        assert col in latest.columns
    assert abs(float(latest.loc["600519", "close"]) - 1500.0) < 1e-9
    assert abs(float(latest.loc["000001", "pe_dyn"]) - 9.0) < 1e-9


def test_spot_old_snapshot_missing_v8_cols_degrades():
    """V8 扩列前的旧快照(无市值列):latest 读出 NaN 不炸——诚实降级契约。"""
    s = _store()
    old = pd.DataFrame({"name": ["贵州茅台"], "close": [1500.0]}, index=["600519"])
    s.upsert_stock_spot(old, "2026-08-16")
    latest = s.latest_stock_spot()
    assert pd.isna(latest.loc["600519", "mktcap"])


def test_spot_empty_inputs():
    s = _store()
    assert s.upsert_stock_spot(pd.DataFrame(), "2026-08-16") == 0
    assert len(s.latest_stock_spot()) == 0


def test_spot_prune_keeps_window():
    s = _store()
    s.upsert_stock_spot(_spot_frame(), "2020-01-01")   # 远古
    s.upsert_stock_spot(_spot_frame(), "2026-08-16")   # 近期
    n = s.prune_stock_spot(keep_days=90)
    assert n == 3  # 只删了远古那份
    assert len(s.latest_stock_spot()) == 3


# ---------- manager: spot 失败显式返回 0(V8: 不再 consensus 名称兜底) ----------
def test_spot_failure_returns_zero_and_keeps_last(monkeypatch):
    """push2 被拦 → fetcher 内重试后仍抛 → manager 显式报错返回 0,保留最后快照。"""
    from stockagent.data import fetcher
    from stockagent.data import manager as mgr

    def _boom():
        raise fetcher.FetchError("clist blocked after retries")

    monkeypatch.setattr(fetcher, "fetch_stock_spot", _boom)
    st = _store()
    st.upsert_stock_spot(_spot_frame(), "2026-08-15")
    assert mgr.DataManager(store=st).update_stock_spot() == 0
    # meta 不动(universe 退旧日期),最后快照仍在
    assert st.get_meta("last_stock_spot_update") in (None, "2026-08-15")
    assert len(st.latest_stock_spot()) == 3


def test_spot_success_writes_with_v8_cols(monkeypatch):
    from stockagent.data import fetcher
    from stockagent.data import manager as mgr

    monkeypatch.setattr(fetcher, "fetch_stock_spot", _spot_frame)
    st = _store()
    n = mgr.DataManager(store=st).update_stock_spot()
    assert n == 3
    latest = st.latest_stock_spot()
    assert abs(float(latest.loc["600519", "pb"]) - 8.1) < 1e-9


# ---------- stock_balance(V8 风险筛腿) ----------
def _bal_rows():
    return [("600519", "20260630", "2026-08-20", 5e10, 1e9, 2e10, 2.4e12, 5e11, 1.9e12, 0.21),
            ("000001", "20260630", "2026-08-21", 4.5e11, 3e11, 0.0, 1.1e13, 1.0e13, 9e11, 0.91)]


def test_balance_upsert_idempotent_and_getter():
    s = _store()
    assert s.upsert_stock_balance(_bal_rows(), source="test") == 2
    assert s.upsert_stock_balance(_bal_rows(), source="test") == 2  # 幂等
    df = s.get_stock_balance_period("20260630")
    assert len(df) == 2
    assert abs(df.loc["600519", "cash"] - 5e10) < 1.0
    assert abs(df.loc["000001", "debt_ratio"] - 0.91) < 1e-9
    assert s.stock_balance_periods(min_rows=2) == ["20260630"]
    assert len(s.get_stock_balance_period("20250630")) == 0


def test_balance_empty_rows():
    s = _store()
    assert s.upsert_stock_balance([], source="test") == 0


# ---------- report_actual V8 扩列 ----------
def test_report_actual_extended_columns_roundtrip():
    s = _store()
    rows = [("600519", "20260630", "2026-08-20", 18.0, 15.0, 23.5, 120.0, 9.0e10, 8.0e11)]
    assert s.upsert_stock_report_actual(rows, source="test") == 1
    full = s.get_stock_report_period_full("20260630")
    assert abs(float(full.loc["600519", "bvps"]) - 120.0) < 1e-9
    assert abs(float(full.loc["600519", "np_abs"]) - 9.0e10) < 1.0
    # 旧 5 元组(无扩列)写入 → 扩列 NULL,不炸
    s.upsert_stock_report_actual([("000001", "20250630", "2025-08-20", 5.0, 3.0)], source="t")
    full2 = s.get_stock_report_period_full("20250630")
    assert pd.isna(full2.loc["000001", "bvps"])
    per_row = s.stock_report_rows_for("600519")
    assert list(per_row.index) == ["20260630"]


# ---------- pool_membership(V8 池留档) ----------
def test_membership_insert_diff_and_history():
    s = _store()
    s.insert_pool_membership("2026-08-01", [
        ("600519", "20260630", "forecast", "2026-07-10", 1, 0.5),
        ("000001", "20260630", "actual", "2026-08-20", 2, 0.7)])
    s.insert_pool_membership("2026-09-01", [
        ("600519", "20260930", "forecast", "2026-08-25", 1, 0.4),
        ("300750", "20260930", "forecast", "2026-08-26", 2, 0.6)])
    hist = s.latest_pool_snapshots(5)
    assert [h["asof"] for h in hist] == ["2026-09-01", "2026-08-01"]
    cur = s.pool_membership_asof("2026-09-01")
    assert set(cur.index) == {"600519", "300750"}
    prev = s.pool_membership_asof("2026-08-01")
    assert set(prev.index) == {"600519", "000001"}
    # 同日重渲染先清后插(幂等)
    s.insert_pool_membership("2026-09-01", [("600519", "20260930", "forecast", "2026-08-25", 1, 0.4)])
    assert len(s.pool_membership_asof("2026-09-01")) == 1


# ---------- industry_member(沿 V7,回归保护) ----------
def _ind_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "industry": ["酿酒行业", "酿酒行业", "银行"],
        "code": ["600519", "000596", "600036"],
        "name": ["贵州茅台", "古井贡酒", "招商银行"],
    })


def test_industry_full_snapshot_replaces_old():
    s = _store()
    assert s.upsert_industry_members(_ind_frame(), "2026-08-01") == 3
    df2 = pd.DataFrame({
        "industry": ["白酒Ⅱ", "银行"],
        "code": ["600519", "600036"],
        "name": ["贵州茅台", "招商银行"],
    })
    assert s.upsert_industry_members(df2, "2026-09-01") == 2
    m = s.industry_map()
    assert len(m) == 2
    assert m.loc["600519", "industry"] == "白酒Ⅱ"
    assert "000596" not in m.index
    assert s.last_industry_snapshot() == "2026-09-01"


def test_industry_map_picks_most_specific_level():
    stock = pd.DataFrame({
        "industry": ["电子", "半导体", "电子"],   # 电子 2 行(大板) vs 半导体 1 行(叶子)
        "code": ["002049", "002049", "600519"],
        "name": ["紫光国微", "紫光国微", "贵州茅台"],
    })
    s1, s2 = _store(), _store()
    s1.upsert_industry_members(stock, "2026-08-17")
    s2.upsert_industry_members(stock.iloc[::-1], "2026-08-17")  # 逆序插入
    for s in (s1, s2):
        m = s.industry_map()
        assert m.loc["002049", "industry"] == "半导体"
        assert m.loc["600519", "industry"] == "电子"
