"""候选池新表 (V7): stock_spot / industry_member upsert 幂等 + getter + prune。无网络·合成帧。"""
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
        {"name": ["贵州茅台", "ST某某", "中国平安"], "close": [1500.0, 2.1, 48.0]},
        index=["600519", "000999", "000001"],
    )


# ---------- stock_spot ----------
def test_spot_upsert_same_day_overwrites():
    s = _store()
    assert s.upsert_stock_spot(_spot_frame(), "2026-08-16") == 3
    # 同日重跑覆盖(幂等):改名后重跑只保留新名
    df2 = _spot_frame()
    df2.loc["000999", "name"] = "*ST某某"
    assert s.upsert_stock_spot(df2, "2026-08-16") == 3
    latest = s.latest_stock_spot()
    assert len(latest) == 3
    assert latest.loc["000999", "name"] == "*ST某某"


def test_spot_latest_takes_newest_date():
    s = _store()
    s.upsert_stock_spot(_spot_frame(), "2026-08-15")
    s.upsert_stock_spot(_spot_frame(), "2026-08-16")
    # 再写一份更早的(乱序)不回退 latest
    s.upsert_stock_spot(_spot_frame(), "2026-08-10")
    latest = s.latest_stock_spot()
    assert len(latest) == 3
    assert float(latest.loc["600519", "close"]) == 1500.0


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


# ---------- manager: spot 的 consensus 名称兜底 ----------
def test_spot_fallback_to_consensus_names(monkeypatch):
    """push2/clist 被拦 → consensus 整表名称列兜底(monkeypatch 两个 fetcher,无网络)。"""
    from stockagent.data import fetcher
    from stockagent.data import manager as mgr

    def _boom():
        raise fetcher.FetchError("clist blocked")

    def _fallback():
        return pd.DataFrame({"name": ["贵州茅台", "ST某某"], "close": [float("nan")] * 2},
                            index=["600519", "000999"])

    monkeypatch.setattr(fetcher, "fetch_stock_spot", _boom)
    monkeypatch.setattr(fetcher, "fetch_stock_spot_from_consensus", _fallback)
    st = _store()
    dm = mgr.DataManager(store=st)
    assert dm.update_stock_spot() == 2
    latest = st.latest_stock_spot()
    assert latest.loc["600519", "name"] == "贵州茅台"
    assert st.get_meta("last_stock_spot_update")  # meta 已置(universe 可推导)


def test_spot_both_sources_fail_returns_zero(monkeypatch):
    from stockagent.data import fetcher
    from stockagent.data import manager as mgr

    def _boom():
        raise fetcher.FetchError("down")

    monkeypatch.setattr(fetcher, "fetch_stock_spot", _boom)
    monkeypatch.setattr(fetcher, "fetch_stock_spot_from_consensus", _boom)
    st = _store()
    assert mgr.DataManager(store=st).update_stock_spot() == 0
    assert st.get_meta("last_stock_spot_update") is None


# ---------- industry_member ----------
def _ind_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "industry": ["酿酒行业", "酿酒行业", "银行"],
        "code": ["600519", "000596", "600036"],
        "name": ["贵州茅台", "古井贡酒", "招商银行"],
    })


def test_industry_full_snapshot_replaces_old():
    s = _store()
    assert s.upsert_industry_members(_ind_frame(), "2026-08-01") == 3
    # 新快照整帧替换:旧快照日行被清,防板块更名/调出成分的幽灵残留
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


def test_industry_same_day_rerun_idempotent():
    s = _store()
    s.upsert_industry_members(_ind_frame(), "2026-08-01")
    s.upsert_industry_members(_ind_frame(), "2026-08-01")
    assert len(s.industry_map()) == 3


def test_industry_boards_and_empty():
    s = _store()
    assert s.industry_boards() == []
    assert s.last_industry_snapshot() is None
    assert len(s.industry_map()) == 0
    s.upsert_industry_members(_ind_frame(), "2026-08-01")
    assert s.industry_boards() == ["酿酒行业", "银行"]  # 升序(SQLite 字节序:酿 U+917F < 银 U+94F6)


def test_industry_map_picks_most_specific_level():
    """申万三级口径下一股挂多层级(电子515 + 半导体185):取成分数最小的最深层级,
    与行写入顺序无关(两帧插入顺序对调结果一致)。"""
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
