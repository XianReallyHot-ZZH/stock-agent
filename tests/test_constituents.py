"""E1 成分股底座: fetcher 解析/空态 + store roundtrip + manager 哨兵/空防护 + update_etf_earnings 断点修复."""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data import fetcher, manager as mgr


def _csindex_df(name: str = "中证银行指数", n: int = 3) -> pd.DataFrame:
    """index_stock_cons_weight_csindex 返回格式的迷你仿真(列名照抄 probe P6)."""
    return pd.DataFrame({
        "日期": ["2026-07-31"] * n,
        "指数代码": ["399986"] * n,
        "指数名称": [name] * n,
        "成分券代码": ["600036", "601398", "1"],
        "成分券名称": ["招商银行", "工商银行", "平安银行"],
        "权重": [15.0, 12.0, 10.0],
    })


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


# ---------- fetcher.parse + empty ----------
def test_fetch_constituents_parses_and_pads(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "index_stock_cons_weight_csindex", lambda **kw: _csindex_df())
    out = fetcher.fetch_index_constituents("399986")
    assert list(out.columns) == ["code", "name", "weight", "snapshot_date"]
    assert out.iloc[2]["code"] == "000001"          # 1 → zfill 000001
    assert out.attrs["index_name"] == "中证银行指数"
    assert out.iloc[0]["weight"] == 15.0 and out.iloc[0]["snapshot_date"] == "2026-07-31"


def test_fetch_constituents_unknown_index_empty(monkeypatch):
    import akshare as ak
    monkeypatch.setattr(ak, "index_stock_cons_weight_csindex", lambda **kw: pd.DataFrame())
    out = fetcher.fetch_index_constituents("399006")   # 国证系 → csindex 不认
    assert len(out) == 0


def test_fetch_constituents_network_error_swallowed(monkeypatch):
    import akshare as ak
    def boom(**kw):
        raise RuntimeError("proxy dead")
    monkeypatch.setattr(ak, "index_stock_cons_weight_csindex", boom)
    assert len(fetcher.fetch_index_constituents("399986")) == 0   # 端点错误 → 空 df, 由 manager 拦


# ---------- store roundtrip ----------
def test_store_constituents_upsert_idempotent():
    st = _store()
    fake = pd.DataFrame({
        "code": ["600036", "601398"],
        "name": ["招商银行", "工商银行"],
        "weight": [15.0, 12.0],
        "snapshot_date": ["2026-07-31", "2026-07-31"],
    })
    assert st.upsert_constituents("399986", fake) == 2
    assert st.upsert_constituents("399986", fake) == 2   # 重跑覆盖不重复
    got = st.get_constituents("399986")
    assert len(got) == 2 and list(got.columns) == ["code", "weight"]
    assert got.iloc[0]["code"] == "600036" and got.iloc[0]["weight"] == 15.0
    assert st.last_constituent_snapshot("399986") == "2026-07-31"
    assert len(st.get_constituents("NOPE")) == 0


# ---------- manager: sentinel + empty guard ----------
def _parsed(idx_name: str, n: int = 3) -> pd.DataFrame:
    """fetch_index_constituents 的解析后输出格式(manager 的消费契约)."""
    df = pd.DataFrame({
        "code": [f"{600036 + i}" for i in range(n)],
        "name": [f"股{i}" for i in range(n)],
        "weight": [10.0] * n,
        "snapshot_date": ["2026-07-31"] * n,
    })
    df.attrs["index_name"] = idx_name
    return df


def test_manager_sentinel_mismatch_no_write(monkeypatch):
    """返回指数名不含 expect → 拒收不写库(调研期 930999=SHS大湾区 错配的防线)."""
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(mgr.fetcher, "fetch_index_constituents",
                        lambda idx: _parsed("SHS大湾区指数"))
    assert dm.update_constituents(symbols=["512800"]) == 0    # expect=银行 不匹配
    assert len(st.get_constituents("399986")) == 0


def test_manager_sentinel_match_writes(monkeypatch):
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(mgr.fetcher, "fetch_index_constituents",
                        lambda idx: _parsed("中证银行指数"))
    assert dm.update_constituents(symbols=["512800"]) == 1
    assert len(st.get_constituents("399986")) == 3


def test_manager_empty_constituents_no_write(monkeypatch):
    """csindex 空 且 tushare 兜底不可用 → 不写库。(2026-09-13 迁移1.10 起 csindex 空会先
    试 index_weight——token 经 stockagent.config 导入泄漏进 pytest 进程,须显式关掉。)"""
    from stockagent.data import tushare_client as tc
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: False)
    monkeypatch.setattr(mgr.fetcher, "fetch_index_constituents",
                        lambda idx: pd.DataFrame(columns=["code", "name", "weight", "snapshot_date"]))
    assert dm.update_constituents(symbols=["512800"]) == 0
    assert len(st.get_constituents("399986")) == 0


# ---------- update_etf_earnings 断点修复 ----------
def _fake_forecast(n: int = 150) -> pd.DataFrame:
    codes = [f"{600000 + i:06d}" for i in range(n)]
    return pd.DataFrame(
        {"yoy": [20.0] * n, "type": ["预增"] * n}, index=codes)


def test_update_etf_earnings_skips_when_no_holdings(monkeypatch):
    """无成分 → 不写零行(2026-08 断点教训的回归测试; top-10 兜底已删 2026-09-13, 缺口=诚实跳过)."""
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(mgr.fetcher, "fetch_earnings_forecast", lambda period, **kw: _fake_forecast())
    n = dm.update_etf_earnings(symbols=["512800"], report_period="20260630")
    assert n == 0
    assert st.get_etf_earnings("512800") is None          # 零行都没写


def test_update_etf_earnings_uses_constituents_route(monkeypatch):
    """B 路线: 有成分 → 聚合出 coverage>0 的有效行."""
    st = _store()
    dm = mgr.DataManager(store=st)
    fake = pd.DataFrame({
        "code": ["600000", "600001", "600002", "600003"],
        "name": ["A", "B", "C", "D"],
        "weight": [30.0, 30.0, 20.0, 20.0],
        "snapshot_date": ["2026-07-31"] * 4,
    })
    st.upsert_constituents("399986", fake)                # 600000..3 都在 fake_forecast 里
    monkeypatch.setattr(mgr.fetcher, "fetch_earnings_forecast", lambda period, **kw: _fake_forecast())
    n = dm.update_etf_earnings(symbols=["512800"], report_period="20260630")
    assert n == 1
    got = st.get_etf_earnings("512800")
    assert got["coverage"] == 1.0 and got["n_holdings"] == 4 and got["n_matched"] == 4
    assert got["weighted_yoy"] == 20.0


def test_pool_yaml_has_index_codes():
    """码表落地: 31 只有 index_code(2026-09-13 迁移1.10: 159915 补 399006 国证系,经
    tushare index_weight 兜底), 哨兵关键词齐(调研§5)."""
    meta = get_config().symbol_meta()
    with_code = [s for s, m in meta.items() if m.get("index_code")]
    assert len(with_code) == 31                           # 29 + 159326(931994) + 159915(399006)
    assert meta["512800"]["index_expect"] == "银行"
    assert meta["159326"]["index_code"] == "931994"
    assert meta["159915"]["index_code"] == "399006"       # 国证系,tushare 兜底(无名称哨兵)
    for s in ("513060", "513180", "513050", "159941", "512480"):
        assert not meta.get(s, {}).get("index_code")      # QDII×4 + CES → 降级
