"""tushare 迁移 1.11 商品日线: fut_daily 主力连续映射 + 回退链 (ADR-0002)。无网络。"""
import pandas as pd
import pytest

from stockagent.data import fetcher, manager as mgr
from stockagent.data import tushare_client as tc


def _fut_raw(ts_code: str = "RB.SHF") -> pd.DataFrame:
    return pd.DataFrame({"ts_code": [ts_code] * 2,
                         "trade_date": ["20260911", "20260910"],
                         "close": [3120.0, 3123.0]})


def test_fetch_commodity_price_tushare_maps(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _fut_raw(kw.get("ts_code", "RB.SHF"))
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_commodity_price_tushare(["螺纹钢"])
    assert list(df["date"]) == ["2026-09-10", "2026-09-11"]   # 升序
    assert abs(float(df["close"].iloc[-1]) - 3120.0) < 1e-9
    assert seen["ts_code"] == "RB.SHF"
    assert seen["start_date"] == "20200101"


def test_fetch_commodity_price_tushare_suffix_routing(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        return _fut_raw()
    monkeypatch.setattr(tc, "query", fake)
    for v, expect in [("原油", "SC.INE"), ("玻璃", "FG.ZCE"),
                      ("生猪", "LH.DCE"), ("黄金", "AU.SHF"), ("螺纹钢", "RB.SHF")]:
        fetcher.fetch_commodity_price_tushare([v])
        assert seen["ts_code"] == expect, v


def test_fetch_commodity_price_tushare_excluded_varieties(monkeypatch):
    """碳酸锂(广期)/LPG(郑商) tushare 无主力连续(2026-09-13 实测)——EXCLUDE 跳过,
    全 EXCLUDE 列表 → raise(调用方走 sina 补位)。"""
    def boom(api, **kw):
        raise AssertionError("EXCLUDE 品种不应触达 tushare")
    monkeypatch.setattr(tc, "query", boom)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_commodity_price_tushare(["碳酸锂", "LPG"])


def test_fetch_commodity_price_tushare_partial_skip(monkeypatch):
    """失败品种跳过,成功品种保留;全失败才 raise。"""
    calls = {"n": 0}

    def fake(api, **kw):
        calls["n"] += 1
        if kw["ts_code"].startswith("LC"):     # 碳酸锂(广期)模拟失败
            raise fetcher.TushareError("接口抽风")
        return _fut_raw(kw["ts_code"])
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_commodity_price_tushare(["碳酸锂", "螺纹钢"])
    assert set(df["variety"]) == {"螺纹钢"}

    def all_fail(api, **kw):
        raise fetcher.TushareError("全挂")
    monkeypatch.setattr(tc, "query", all_fail)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_commodity_price_tushare(["螺纹钢"])


def test_update_commodity_price_fallback_chain(monkeypatch, tmp_path):
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "fetch_commodity_price_tushare",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["螺纹钢"], "date": ["2026-09-11"], "close": [3120.0]}))
    # 碳酸锂(EXCLUDE)由 sina 补位
    monkeypatch.setattr(fetcher, "fetch_commodity_price",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["碳酸锂"], "date": ["2026-09-11"], "close": [71000.0]}))
    res = dm.update_commodity_price(varieties=["螺纹钢", "碳酸锂"])
    assert res["螺纹钢"] == 1 and res["碳酸锂"] == 1
    with st._conn() as c:
        srcs = dict(c.execute("SELECT variety, source FROM commodity_price").fetchall())
    assert srcs["螺纹钢"] == "tushare_fut_daily"
    assert srcs["碳酸锂"] == "akshare_futures"     # EXCLUDE 品种 sina 补位
    # tushare 整体挂 → sina 全量兜底
    monkeypatch.setattr(fetcher, "fetch_commodity_price_tushare",
                        lambda varieties, start, end: (_ for _ in ()).throw(
                            fetcher.TushareError("限频")))
    monkeypatch.setattr(fetcher, "fetch_commodity_price",
                        lambda varieties, start, end: pd.DataFrame(
                            {"variety": ["螺纹钢"], "date": ["2026-09-11"], "close": [3118.0]}))
    res = dm.update_commodity_price(varieties=["螺纹钢"])
    assert res["螺纹钢"] == 1


# ---------- 批次2.3 fut_wsr 库存扩腿 + CZCE 总计口径修正 (2026-09-13) ----------

def _czce_fg_df():
    """复刻 2026-09-02 FG 表:仓库+小计成对、总计行、续表丢名行。旧版全列求和=3804(3×)。"""
    return pd.DataFrame({
        "仓库编号": ["0901", "小计", "0923", "小计", "0925", "小计", None, None, "总计"],
        "仓库简称": ["沙河安全", None, "武汉创景（厂库）", None, "弘润建材", None, None, None, None],
        "提货地点": [None] * 9,
        "仓单数量": [0, 0, 500, 500, 768, 768, 130, 138, 1268],
        "当日增减": [0] * 9,
        "升贴水": [0, 0, 0, 0, 0, 0, 0, 0, None],
    })


def test_fetch_czce_receipts_uses_total_row(monkeypatch):
    """口径修正:聚合=「总计」行(官方),不再全列求和(3× 高估)。"""
    monkeypatch.setattr(fetcher.ak, "futures_warehouse_receipt_czce",
                        lambda date: {"FG": _czce_fg_df(), "WH": pd.DataFrame()})
    df = fetcher.fetch_czce_receipts("20260902")
    assert df.to_dict("records") == [{"variety": "FG", "date": "2026-09-02",
                                      "volume": 1268.0}]
    # 单日源失败 → 空表不抛(周采样重跑自愈)
    def boom(date):
        raise RuntimeError("czce 拦")
    monkeypatch.setattr(fetcher.ak, "futures_warehouse_receipt_czce", boom)
    assert fetcher.fetch_czce_receipts("20260902").empty


def test_fetch_czce_receipts_fallback_numeric_rows(monkeypatch):
    """无总计行:退仓库行求和(编号为数字者,剔除小计/总计/续表丢名行)。"""
    raw = _czce_fg_df()
    raw = raw[raw["仓库编号"] != "总计"]
    monkeypatch.setattr(fetcher.ak, "futures_warehouse_receipt_czce",
                        lambda date: {"FG": raw})
    df = fetcher.fetch_czce_receipts("20260902")
    assert float(df["volume"].iloc[0]) == 1268.0   # 500+768,续表 130/138 丢名行不入


def _wsr_raw() -> pd.DataFrame:
    """复刻单日页:CU 双段同名仓库(完税+保税)+ CZCE 品种(FG,应被滤除)。"""
    return pd.DataFrame({
        "trade_date": ["20260902"] * 6,
        "symbol": ["CU", "CU", "CU", "CU", "SC", "FG"],
        "fut_name": ["铜"] * 6,
        "warehouse": ["中储无锡", "中储无锡", "世天威外港", "世天威外港", "中石化儲運", "弘润建材"],
        "pre_vol": [None] * 6,
        "vol": [3924.0, 3924.0, 599.0, 7771.0, 2961000.0, 768.0],
        "vol_chg": [0] * 6,
        "unit": ["手"] * 5 + ["张"],
    })


def test_fetch_inventory_wsr_tushare_aggregates(monkeypatch):
    """仓库和聚合(CU 双段同名各计一次=完税+保税)、CZCE 品种滤除、失败静默空表。"""
    monkeypatch.setattr(tc, "query_paged", lambda api, **kw: _wsr_raw())
    df = fetcher.fetch_inventory_wsr_tushare("20260902")
    got = {r["variety"]: r["volume"] for r in df.to_dict("records")}
    assert got["CU"] == 3924.0 + 599.0 + 7771.0   # 完全重复行合并;同名双行(完税+保税)异值都算
    assert got["SC"] == 2961000.0
    assert "FG" not in got                                  # CZCE 不吃 tushare 混列
    assert set(df.columns) == {"variety", "date", "volume"}

    def boom(api, **kw):
        raise fetcher.TushareError("限频")
    monkeypatch.setattr(tc, "query_paged", boom)
    assert fetcher.fetch_inventory_wsr_tushare("20260902").empty


def test_update_commodity_inventory_dual_legs(monkeypatch, tmp_path):
    """双腿编排:CZCE(akshare 总计)+ WSR(fut_wsr)各自周采样入库,source 分记。"""
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    # WSR 腿已灌到 2026-01-07(两品种)→ 增量从 01-14;czce 空库 → 从起点 01-07
    st.upsert_commodity_inventory([("CU", "2026-01-07", 1.0), ("LC", "2026-01-07", 1.0)],
                                  source="ts_wsr")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "WSR_INVENTORY_SYMBOLS", ["CU", "LC"])
    monkeypatch.setattr(fetcher, "fetch_czce_receipts", lambda d8: pd.DataFrame(
        [{"variety": "FG", "date": f"{d8[:4]}-{d8[4:6]}-{d8[6:]}", "volume": 1268.0}]))
    monkeypatch.setattr(fetcher, "fetch_inventory_wsr_tushare", lambda iso: pd.DataFrame(
        [{"variety": "CU", "date": iso, "volume": 38567.0},
         {"variety": "LC", "date": iso, "volume": 45389.0}]))
    n = dm.update_commodity_inventory(start="2026-01-01", end="2026-01-21")
    assert n == 7        # czce 3 周(01-07/14/21) + wsr 2 周(01-14/21)×2 品种
    with st._conn() as c:
        rows = dict(((v, d), s) for v, d, s in
                    c.execute("SELECT variety,date,source FROM commodity_inventory").fetchall())
    assert rows[("FG", "2026-01-07")] == "czce"
    assert rows[("CU", "2026-01-14")] == "ts_wsr"
    assert rows[("LC", "2026-01-21")] == "ts_wsr"
    # 幂等增量:再跑同窗口零新增(各腿从末日续)
    assert dm.update_commodity_inventory(start="2026-01-01", end="2026-01-21") == 0


def test_update_commodity_inventory_wsr_new_symbol_backfills(monkeypatch, tmp_path):
    """WSR 腿增量取各品种最旧末日:某新品种缺史 → 从 wsr 起点全量补(不为已有品种跳过)。"""
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    # 只有 CU 有历史(末日 2026-06),其余 WSR 品种无 → b_start=wsr_start → 2019 起点扫
    st.upsert_commodity_inventory([("CU", "2026-06-10", 38000.0)], source="ts_wsr")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)
    monkeypatch.setattr(fetcher, "WSR_INVENTORY_SYMBOLS", ["CU", "LC"])
    monkeypatch.setattr(fetcher, "fetch_czce_receipts",
                        lambda d8: pd.DataFrame(columns=["variety", "date", "volume"]))
    calls = {"wsr": []}
    monkeypatch.setattr(fetcher, "fetch_inventory_wsr_tushare",
                        lambda iso: (calls["wsr"].append(iso) or pd.DataFrame(
                            [{"variety": "CU", "date": iso, "volume": 1.0}])))
    n = dm.update_commodity_inventory(start="2026-01-01", end="2019-02-06")   # end 早于 CU 末日
    assert len(calls["wsr"]) == 5       # 2019-01-02 的次一个周三 01-09 起,至 02-06 共 5 个
    assert n == 5


def test_update_commodity_inventory_no_token_skips_wsr(monkeypatch, tmp_path):
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: False)
    monkeypatch.setattr(fetcher, "fetch_czce_receipts", lambda d8: pd.DataFrame(
        [{"variety": "SA", "date": "2026-01-07", "volume": 6454.0}]))
    n = dm.update_commodity_inventory(start="2026-01-01", end="2026-01-21")
    assert n == 3
    with st._conn() as c:
        srcs = {v for v, in c.execute("SELECT DISTINCT variety FROM commodity_inventory").fetchall()}
    assert srcs == {"SA"}


# ---------- 批次2.4 fut_mapping 展期口径 (2026-09-13) ----------

def test_fetch_fut_mapping_tushare_maps(monkeypatch):
    seen = {}

    def fake(api, **kw):
        seen.update(kw)
        assert api == "fut_mapping"
        return pd.DataFrame({"ts_code": ["RB.SHF"] * 2,
                             "trade_date": ["20260911", "20260910"],
                             "mapping_ts_code": ["RB2701.SHF", "RB2610.SHF"]})
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_fut_mapping_tushare("RB", "2026-01-01", "2026-09-11")
    assert seen["ts_code"] == "RB.SHF" and seen["start_date"] == "20260101"
    assert list(df["date"]) == ["2026-09-10", "2026-09-11"]      # 升序
    assert list(df["mapping_code"]) == ["RB2610.SHF", "RB2701.SHF"]
    # 无后缀映射的代码(防御) → 空表不抛
    assert fetcher.fetch_fut_mapping_tushare("XX").empty


def test_fetch_fut_contract_daily_tushare(monkeypatch):
    def fake(api, **kw):
        assert kw["ts_code"] == "RB2701.SHF"
        return pd.DataFrame({"ts_code": ["RB2701.SHF"],
                             "trade_date": ["20260911"], "close": [3120.0]})
    monkeypatch.setattr(tc, "query", fake)
    df = fetcher.fetch_fut_contract_daily_tushare("RB2701.SHF")
    assert df.to_dict("records") == [{"ts_code": "RB2701.SHF",
                                      "date": "2026-09-11", "close": 3120.0}]

    def empty(api, **kw):
        return pd.DataFrame()
    monkeypatch.setattr(tc, "query", empty)
    with pytest.raises(fetcher.FetchError):
        fetcher.fetch_fut_contract_daily_tushare("RB2701.SHF")


def test_update_fut_rollover_incremental(monkeypatch, tmp_path):
    """映射整段重拉幂等;逐合约只拉未入库的(增量);失败合约跳过不炸。"""
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    st.upsert_fut_contract_daily([("RB2610.SHF", "2026-08-01", 3100.0)])
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: True)

    def fake_map(code, start, end):
        return pd.DataFrame({"ts_code": [f"RB.SHF"] * 2,
                             "date": ["2026-09-10", "2026-09-11"],
                             "mapping_code": ["RB2610.SHF", "RB2701.SHF"]})
    monkeypatch.setattr(fetcher, "fetch_fut_mapping_tushare", fake_map)
    pulled = []

    def fake_contract(ts):
        pulled.append(ts)
        return pd.DataFrame({"ts_code": [ts], "date": ["2026-09-11"], "close": [3120.0]})
    monkeypatch.setattr(fetcher, "fetch_fut_contract_daily_tushare", fake_contract)
    out = dm.update_fut_rollover()
    assert out["mapping"] > 0 and out["contracts"] >= 1 and out["failed"] == 0
    assert pulled == ["RB2701.SHF"]                       # RB2610 已入库不重拉
    assert list(st.get_fut_mapping("RB.SHF").iloc[-2:]) == ["RB2610.SHF", "RB2701.SHF"]
    assert float(st.get_fut_contract_close("RB2701.SHF").iloc[-1]) == 3120.0
    assert st.last_fut_mapping_date() == "2026-09-11"


def test_update_fut_rollover_no_token(monkeypatch, tmp_path):
    from stockagent.data.store import Store
    st = Store(tmp_path / "t.db")
    dm = mgr.DataManager(store=st)
    monkeypatch.setattr(tc, "has_token", lambda: False)
    assert dm.update_fut_rollover() == {"mapping": 0, "contracts": 0, "failed": 0}
