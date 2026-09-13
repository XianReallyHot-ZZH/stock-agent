"""tushare 迁移 1.10 指数成分: index_weight 国证兜底 + 权重和哨兵 + name 保留 (ADR-0002)。无网络。"""
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc
from stockagent.data.store import Store


def _iw_raw(n: int = 3) -> pd.DataFrame:
    w = 100.0 / n
    return pd.DataFrame({
        "ts_code": ["399006.SZ"] * n,
        "con_code": [f"30000{i}.SZ" for i in range(n)],
        "weight": [w] * n,
        "trade_date": ["20260831"] * n + [],
    })


def test_fetch_constituents_tushare_latest_snapshot_and_strip(monkeypatch):
    raw = pd.concat([_iw_raw(3).assign(trade_date="20260831"),
                     _iw_raw(3).assign(trade_date="20260731")], ignore_index=True)
    monkeypatch.setattr(tc, "query", lambda api, **kw: raw)
    df = fetcher.fetch_index_constituents_tushare("399006")
    assert len(df) == 3                                  # 只取最新快照
    assert set(df["code"]) == {"300000", "300001", "300002"}  # con_code 去后缀补零
    assert df["snapshot_date"].iloc[0] == "2026-08-31"
    assert (df["name"] == "").all()                      # 接口无名称列
    assert abs(df["weight"].sum() - 100.0) < 1e-9


def test_fetch_constituents_tushare_weight_sentinel(monkeypatch):
    bad = _iw_raw(3).assign(weight=[0.1, 0.1, 0.1])      # 权重和 0.3% → 疑串台
    monkeypatch.setattr(tc, "query", lambda api, **kw: bad)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_index_constituents_tushare("399006")


def test_fetch_constituents_tushare_empty(monkeypatch):
    monkeypatch.setattr(tc, "query", lambda api, **kw: pd.DataFrame())
    assert len(fetcher.fetch_index_constituents_tushare("931111")) == 0


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_upsert_constituents_preserves_name():
    """tushare 行 name='' 不得抹 csindex 存量名称。"""
    s = _store()
    s.upsert_constituents("000300", pd.DataFrame({
        "code": ["600519"], "name": ["贵州茅台"], "weight": [5.0],
        "snapshot_date": ["2026-07-31"]}))
    s.upsert_constituents("000300", pd.DataFrame({
        "code": ["600519"], "name": [""], "weight": [5.1],
        "snapshot_date": ["2026-08-31"]}))
    df = s.get_constituents("000300")
    assert abs(float(df.loc[df["code"] == "600519", "weight"].iloc[0]) - 5.1) < 1e-9  # 更新
    with s._conn() as c:                                          # name 保留(空串不抹)
        name = c.execute("SELECT name FROM index_constituents WHERE index_code='000300' "
                         "AND code='600519'").fetchone()[0]
    assert name == "贵州茅台"


def test_update_constituents_guozheng_fills_via_tushare(monkeypatch):
    """csindex 空(国证系) → tushare index_weight 兜底落库(159915/399006 首次可得)。"""
    st = _store()
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_index_constituents",
                        lambda idx, **kw: pd.DataFrame(columns=["code", "name", "weight", "snapshot_date"]))
    monkeypatch.setattr(fetcher, "fetch_index_constituents_tushare",
                        lambda idx, **kw: pd.DataFrame({
                            "code": ["300750", "300059"], "name": ["", ""],
                            "weight": [8.0, 3.0], "snapshot_date": ["2026-08-31"] * 2}))
    n = dm.update_constituents(symbols=["159915"])
    assert n == 1
    df = st.get_constituents("399006")
    assert len(df) == 2
