"""screen.build_pool_snapshot 全链路集成(合成 SQLite store,无网络): universe→价格复权→
三环→窗口→S1/S2/修正/变脸/PEAD 六表装配。验证的是**接线**,打分语义由 P2 各测试钉死。"""
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from stockagent.data.store import Store
from stockagent.pool.screen import build_pool_snapshot


def _now() -> datetime:
    return datetime.now()


def _bdate(n: int, end=None) -> list[str]:
    end = end or _now().date()
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(end=end, periods=n)]


def _seed(store: Store):
    now = _now()
    # ---- consensus: 4 份周快照(002460 eps 1.0→1.1;600519 稳定 68) ----
    cols = ["n_reports", "rating_buy", "rating_over", "rating_neutral",
            "rating_reduce", "rating_sell", "eps_fy1", "eps_fy2", "fy1_year", "fy2_year"]
    for w, eps in [(28, 1.0), (21, 1.02), (14, 1.05), (7, 1.1)]:
        d = (now - timedelta(days=w)).strftime("%Y%m%d")
        df = pd.DataFrame(
            {c: [44.0, 10.0, 0.0, 0.0, 0.0, 1.1, 1.2, 2026, 2027, 68.0, 75.0, 2026, 2027][:2]
             for c in cols}, index=["002460", "600519"])
        df.loc["002460", "eps_fy1"] = eps
        df.loc["600519", "eps_fy1"] = 68.0
        store.upsert_consensus(df, fetch_date=d)
    # ---- spot ----
    spot = pd.DataFrame({"name": ["赣锋锂业", "贵州茅台"], "close": [12.0, 1500.0]},
                        index=["002460", "600519"])
    store.upsert_stock_spot(spot, date=now.strftime("%Y-%m-%d"))
    # ---- industry ----
    ind = pd.DataFrame({"industry": ["能源金属", "白酒Ⅱ"],
                        "code": ["002460", "600519"], "name": ["赣锋锂业", "贵州茅台"]})
    store.upsert_industry_members(ind, snapshot_date=now.strftime("%Y-%m-%d"))
    # ---- prices: 002460 涨后深跌(−52%,触发 S1+S2 深跌);600519 稳步上行 ----
    dates = _bdate(320)
    rise = np.linspace(10.0, 25.0, 200)
    crash = np.linspace(25.0, 12.0, 120)
    for code, vals in (("002460", np.concatenate([rise, crash])),
                       ("600519", np.linspace(1400, 1600, 320))):
        df = pd.DataFrame({"open": vals, "high": vals, "low": vals, "close": vals,
                           "volume": 1.0, "amount": 1.0}, index=dates)
        store.upsert_prices(code, df, source="sina_stock_raw")
    # ---- commodity: 碳酸锂 大涨后走平(yoy>10%,recent>−5% → health 1.0) ----
    cvals = np.concatenate([np.linspace(50.0, 110.0, 260), np.linspace(110.0, 114.0, 60)])
    store.upsert_commodity_price(
        [("碳酸锂", d, float(v)) for d, v in zip(dates[-320:], cvals)], source="test")
    # ---- 当期三环: 002460 预告(预增 60%) + 正式报 8 期(增速改善→变脸向上+加速腿) ----
    # 预告面板须为「全市场级」(forecast_period_counts 的 n≥100 门,滤观察池稀疏期)——
    # 补 100 只 dummy 让 20260630 期成期,002460 混在其中
    fc_rows = [(f"9{i:05d}", "20260630", 10.0, "预增", now.strftime("%Y-%m-%d"))
               for i in range(100)]
    fc_rows.append(("002460", "20260630", 60.0, "预增", now.strftime("%Y-%m-%d")))
    store.upsert_stock_forecast(fc_rows, source="test")
    rows = []
    for i, (p, v) in enumerate([("20240630", 5.0), ("20241231", 8.0), ("20250331", 4.0),
                                ("20250630", 20.0), ("20250930", 18.0), ("20251231", 25.0),
                                ("20260331", 12.0)]):
        rows.append(("002460", p, (now - timedelta(days=400 - i * 30)).strftime("%Y-%m-%d"),
                     v, v * 0.8))
    store.upsert_stock_report_actual(rows, source="test")


def _store_seeded() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    st = Store(Path(f.name))
    _seed(st)
    return st


def test_full_pipeline_assembles():
    snap = build_pool_snapshot(_store_seeded())
    # universe: 2 只全通过
    assert snap["universe_stats"]["n"] == 2
    assert snap["universe_stats"]["n_cyclic"] == 1 and snap["universe_stats"]["n_typed"] == 2
    assert snap["period"] == "20260630"     # 8 月中 → 中报周期
    # S1: 002460 触发(深跌偏离极值),护栏全过(预增/上修/变脸up) → 有分;600519 不触发
    s1 = {r["code"]: r for r in snap["s1_rows"]}
    assert "002460" in s1 and "600519" not in s1
    assert s1["002460"]["score"] is not None
    assert s1["002460"]["guard_pass"] is True
    # S2: 002460 = 深跌 ∧ 窗口A(预告落地) ∧ 周期猛(health 1.0 × lag) → 入表
    s2 = {r["code"]: r for r in snap["s2_rows"]}
    assert "002460" in s2
    assert s2["002460"]["chip"] == "窗口A·预告落地"
    assert s2["002460"]["fierce"] > 0.05
    # 修正动量: 4 快照激活,002460 +10% 上修
    rev = {r["code"]: r for r in snap["revision"]["rows"]}
    assert snap["revision"]["cold_start"] is None
    assert "002460" in rev and rev["002460"]["up"] is True
    # 变脸: 002460 同尾 20250630→20260630 尚无 20260630 正式报 → 由已报序列判(无 down)
    codes_down = {r["code"] for r in snap["face_rows_down"]}
    assert "002460" not in codes_down
    # PEAD: 预告 60% vs leg A(上年同期 20%) → surprise +40pp
    pead = {r["code"]: r for r in snap["pead_rows"]}
    assert "002460" in pead and abs(pead["002460"]["surprise_pp"] - 40.0) < 1e-6
    assert pead["002460"]["leg"] == "A"
    # 价格新鲜度/快照数
    assert snap["price_freshness"]["n"] == 2
    assert snap["n_snapshots"] == 4


def test_empty_store_degrades_cleanly():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    snap = build_pool_snapshot(Store(Path(f.name)))
    assert snap["universe_stats"]["n"] == 0
    assert snap["s1_rows"] == [] and snap["s2_rows"] == []
    assert snap["revision"]["cold_start"]["have"] == 0
