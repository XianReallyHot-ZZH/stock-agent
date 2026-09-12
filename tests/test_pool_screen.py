"""screen.build_high_earnings_snapshot 全链路集成(合成 SQLite store,无网络): spot 宇宙→
三环地板→精筛(PB 分位/PEG/红旗)→Top-N 装配。验证的是**接线**,门语义由 gates/valuation/risk
各测试钉死。"""
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from stockagent.data.store import Store
from stockagent.pool.screen import build_high_earnings_snapshot

_ASOF = "2026-08-25"   # 冻结日历: period=20260630(截止 8/31 未过),断言不随墙钟翻转


def _bdate(n: int, end: str) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]


def _seed(store: Store):
    end = _ASOF
    # ---- spot(V8 扩列) ----
    spot = pd.DataFrame({
        "name": ["贵州茅台", "赣锋锂业", "宁德时代", "中国银行"],
        "close": [1500.0, 8.0, 200.0, 5.0],
        "mktcap": [1.88e12, 3.2e10, 8.0e11, 1.4e12],
        "float_mktcap": [1.88e12, 3.0e10, 7.0e11, 1.0e12],
        "pe_dyn": [25.0, 15.0, 60.0, 6.0],
        "pb": [8.1, 1.2, 4.5, 0.5],
    }, index=["600519", "002460", "300750", "601988"])
    store.upsert_stock_spot(spot, date=end)
    # ---- 行业 ----
    ind = pd.DataFrame({
        "industry": ["白酒Ⅱ", "能源金属", "半导体", "银行"],
        "code": ["600519", "002460", "300750", "601988"],
        "name": ["贵州茅台", "赣锋锂业", "宁德时代", "中国银行"]})
    store.upsert_industry_members(ind, snapshot_date=end)
    # ---- 价格: 002460 长阴跌(600 日 14→8,PB 分位低);300750/茅台 平稳 ----
    for code, vals in (("002460", np.linspace(14.0, 8.0, 600)),
                       ("300750", np.linspace(180.0, 200.0, 400)),
                       ("600519", np.linspace(1400.0, 1500.0, 400)),
                       ("601988", np.linspace(4.6, 5.0, 400))):
        dates = _bdate(len(vals), end)
        df = pd.DataFrame({"open": vals, "high": vals, "low": vals, "close": vals,
                           "volume": 1.0, "amount": 1.0}, index=dates)
        store.upsert_prices(code, df, source="sina_stock_raw")
    # ---- 当期(20260630)三环 ----
    # 600519: 预告 预增 60% 落地 7-15(单腿过地板;后续 PEG 因无 TTM 数据诚实缺省)
    store.upsert_stock_forecast([("600519", "20260630", 60.0, "预增", "2026-07-15")],
                                source="test")
    # 601988: 快报 90/50 落地 7-20(过地板,但资产负债双高 → 红旗硬剔)
    store.upsert_stock_express([("601988", "20260630", "2026-07-20", 90.0, 50.0)],
                               source="test")
    # 002460: 正式报 60/30 落地 8-20(无 sina → 归母回退+未精筛;cyclic → PB 轨)
    #   bvps 阶梯: 20250930(ann 2025-10-28)起——PB 分位需 ≥120 观测点
    store.upsert_stock_report_actual([
        ("002460", "20260630", "2026-08-20", 60.0, 30.0, None, 6.0, None, None),
        ("002460", "20251231", "2026-03-20", 50.0, 25.0, None, 5.0, None, None),
        ("002460", "20250930", "2025-10-28", 45.0, 22.0, None, 5.5, None, None),
    ], source="test")
    # 300750: 正式报 80/40 落地 8-24 + TTM 原料(20250630/20251231/20260630)
    store.upsert_stock_report_actual([
        ("300750", "20260630", "2026-08-24", 80.0, 40.0, 4.5, 50.0, 70e8, 500e8),
        ("300750", "20251231", "2026-03-25", 60.0, 30.0, 3.8, 45.0, 100e8, 420e8),
        ("300750", "20250630", "2025-08-20", 55.0, 28.0, 3.5, 42.0, 60e8, 380e8),
    ], source="test")
    # ---- 300750 sina 精筛(扣非 75%) ----
    fins = pd.DataFrame({
        "report_period": ["20250630", "20260630"] * 2,
        "metric": ["np_deducted"] * 2 + ["goodwill"] * 2,
        "value": [60e8, 105e8, 1e8, 1e8],
    })
    store.upsert_stock_financials("300750", fins, source="sina")
    # ---- 资产负债: 601988 双高(现金 20% 总资产 ∧ 负债率 50%) ----
    store.upsert_stock_balance([
        ("601988", "20260630", "2026-08-28", 4e11, 5e10, 0.0, 2e12, 1e12, 1e12, 0.50),
        ("002460", "20260630", "2026-08-21", 2e8, 3e8, 1e8, 5e9, 2e9, 3e9, 0.40),
    ], source="test")


def _store_seeded() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    st = Store(Path(f.name))
    _seed(st)
    return st


def test_full_pipeline_assembles():
    snap = build_high_earnings_snapshot(_store_seeded(), asof=_ASOF, persist=False)
    assert snap["period"] == "20260630"
    assert snap["universe_stats"]["n"] == 4
    # 过地板 4 只: 茅台(预告)/中行(快报)/赣锋(正式)/宁德(正式)
    assert snap["n_floor_pass"] == 4
    assert snap["clock"]["ring_mix"] == {"forecast": 1, "express": 1, "actual": 2}
    # 池 = 赣锋(PB 轨)+宁德(PEG 轨);茅台估值缺(TTM 无原料)、中行红旗双高硬剔
    pool = {r["code"]: r for r in snap["rows"]}
    assert set(pool) == {"002460", "300750"}
    assert pool["002460"]["track"] == "pb"
    assert pool["002460"]["score"] is not None and pool["002460"]["score"] <= 0.30
    assert "未精筛" in pool["002460"]["yellow"]        # sina 腿缺 → 归母回退+旗
    assert pool["300750"]["track"] == "peg"
    assert pool["300750"]["np_axis"] == "deducted"      # sina 精筛到扣非 75%
    assert pool["300750"]["score"] is not None and pool["300750"]["score"] <= 1.0
    # 估值: PE=8e11/1.1e10≈72.7, PEG=72.7/75≈0.97
    assert abs(pool["300750"]["pe_ttm"] - 800e9 / 110e8) < 0.5
    # 排名: rank 连续
    assert sorted(r["rank"] for r in snap["rows"]) == [1, 2]
    # 数据缺口与红旗透明呈现(茅台/赣锋/中行都无 sina → 精筛腿待拉)
    assert any("红旗剔除" in k for k in snap["gaps"])
    assert set(snap["sina_needed"]) == {"600519", "002460", "601988"}
    # 构成/涌现: 2 只中 1 只商品关联周期 = 恰 50% ≥ 阈值 → 边界触发涌现簇
    assert snap["composition"]["n_total"] == 2
    assert snap["emergent"] is not None and abs(snap["emergent"]["share"] - 0.5) < 1e-9
    assert snap["emergent"]["industries"] == ["能源金属"]
    # 市值中位: 002460(3.2e10) 与 300750(8e11) 排队取中 = 4.16e11
    assert abs(snap["mktcap_median"] - (3.2e10 + 8e11) / 2) < 1e6
    # diff 结构在(无历史档案 → 空列表)
    assert snap["diff"] == {"entered": [], "exited": []}
    assert snap["history"] == []


def test_membership_persist_and_diff_next_day():
    st = _store_seeded()
    # 第一天: 活装配(asof=None)落档案——用 monkeypatch 冻结 datetime? 简化: 直接手工插档案
    store_first = st
    store_first.insert_pool_membership("2026-08-24", [
        ("002460", "20260630", "actual", "2026-08-20", 1, 0.2),
        ("600519", "20260630", "forecast", "2026-07-15", 2, 0.5)])
    snap = build_high_earnings_snapshot(store_first, asof=_ASOF, persist=False)
    # vs 8-24 档案: 新进 300750, 淘汰 600519(估值缺出局——「自然被淘汰」)
    assert {d["code"] for d in snap["diff"]["entered"]} == {"300750"}
    assert {d["code"] for d in snap["diff"]["exited"]} == {"600519"}
    assert [h["asof"] for h in snap["history"]] == ["2026-08-24"]


def test_empty_store_degrades_cleanly():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    snap = build_high_earnings_snapshot(Store(Path(f.name)))
    assert snap["universe_stats"]["n"] == 0
    assert snap["rows"] == [] and snap["n_floor_pass"] == 0
    assert snap["composition"]["n_total"] == 0


def test_mktcap_filter_default_off_in_cfg_note():
    """市值过滤默认关(cfg_note 透传,消融臂裁决后再决定常开)。"""
    snap = build_high_earnings_snapshot(_store_seeded(), asof=_ASOF, persist=False)
    assert snap["cfg_note"]["mktcap_filter_on"] is False


def test_history_forward_settlement():
    """⑤节后视 30 日结算: 满窗快照自动算池等权 vs 沪深300 同窗;未满窗 → None 留白。"""
    st = _store_seeded()
    # 沪深300 指数日历(400 日 4000→4400,均匀 +10%/全程)
    dates = _bdate(400, _ASOF)
    idx_df = pd.DataFrame({"open": np.linspace(4000, 4400, 400),
                           "high": np.linspace(4000, 4400, 400),
                           "low": np.linspace(4000, 4400, 400),
                           "close": np.linspace(4000, 4400, 400),
                           "volume": 1.0}, index=dates)
    st.upsert_index_daily("000300", idx_df, source="test")
    # 两个月前的旧快照(成员=002460/300750,价格腿已覆盖其后 30+ 交易日)
    st.insert_pool_membership("2026-06-15", [
        ("002460", "20260630", "actual", "2026-08-20", 1, 0.2),
        ("300750", "20260630", "actual", "2026-08-24", 2, 0.97)])
    snap = build_high_earnings_snapshot(st, asof=_ASOF, persist=False)
    hist = {h["asof"]: h for h in snap["history"]}
    old = hist["2026-06-15"]
    # 满窗: 三列都算出,excess=ret-bench
    assert old["ret30"] is not None and old["bench30"] is not None
    assert abs(old["excess30"] - (old["ret30"] - old["bench30"])) < 1e-9
    # bench30 独立复核: 快照日后首个交易日起 30 交易日的指数闭区间收益
    bdates = [d for d in dates if d > "2026-06-15"]
    d0, d1 = bdates[0], bdates[29]
    closes = idx_df["close"]
    expect_b = float(closes[d1]) / float(closes[d0]) - 1.0
    assert abs(old["bench30"] - expect_b) < 1e-9
    # 基准上涨 → bench30 > 0
    assert old["bench30"] > 0
