"""黄金阶段定位器 v2 单测: 驱动 reader + bull_intact + 阶段精炼 + 操作 + 集成。"""
import pandas as pd

from stockagent.data.store import Store
from stockagent.western_macro import gold_locator as gl


def _dates(n, start="2024-01-01"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _seed_w(store, source, symbol, closes, start="2024-01-01"):
    dates = _dates(len(closes), start)
    df = pd.DataFrame([{"source": source, "symbol": symbol, "date": d, "close": float(c)}
                       for d, c in zip(dates, closes)])
    store.upsert_western_macro(df, source_tag="seed")


def _seed_micro(store, cb_buying=True, spec_net=200000, comm_net=-220000, inv_tonnes=838.0):
    # cb_gold 月频(实物万oz)
    months = [f"2024-{m:02d}-01" for m in range(1, 13)] + [f"2025-{m:02d}-01" for m in range(1, 13)] \
        + [f"2026-{m:02d}-01" for m in range(1, 8)]
    vals = [7000 + (i if cb_buying else -i) * 8 for i in range(len(months))]   # 买/卖
    store.upsert_cb_gold([{"country": "CN", "date": d, "value": v} for d, v in zip(months, vals)])
    # cftc 投机(GC) + 商业(GC_M), 周频(用 bdate)
    dts = _dates(120)
    store.upsert_cftc_position([{"symbol": "GC", "date": d, "long_pos": spec_net + 1000, "short_pos": 1000,
                                 "net_pos": spec_net} for d in dts])
    store.upsert_cftc_position([{"symbol": "GC_M", "date": d, "long_pos": 1000, "short_pos": abs(comm_net) + 1000,
                                 "net_pos": comm_net} for d in dts])
    # comex 库存(日)
    dts2 = _dates(260)
    store.upsert_comex_inventory([{"symbol": "GC", "date": d, "tonnes": inv_tonnes, "ounces": inv_tonnes * 32150}
                                  for d in dts2])


def _full_seed(store, gold_closes, cb_buying=True, tp_rising=True):
    _seed_w(store, "fut", "GC", gold_closes)
    tp = [0.30 + 0.002 * i for i in range(300)] if tp_rising else [0.90 - 0.002 * i for i in range(300)]
    _seed_w(store, "nyfed_acm", "ACMTP10", tp)                 # 期限溢价
    _seed_w(store, "fred", "DFII10", [2.0 + 0.001 * i for i in range(300)])   # 实际利率(缓升)
    _seed_w(store, "ust", "US2S10S", [0.10 + 0.001 * i for i in range(300)])  # 2s10s
    _seed_w(store, "dxy", "DXY", [100 - 0.01 * i for i in range(300)])        # DXY 缓降
    _seed_micro(store, cb_buying=cb_buying)


# ---- 纯函数 ----
def test_dir_basic():
    s = pd.Series([1.0 + 0.01 * i for i in range(70)])
    assert gl._dir(s, 60) == "↑"
    s2 = pd.Series([2.0 - 0.01 * i for i in range(70)])
    assert gl._dir(s2, 60) == "↓"


def test_is_bull_intact():
    cb = {"raw": 2.0}
    assert gl._is_bull_intact(cb, True, False) is True     # 央行买 + 期限溢价↑
    assert gl._is_bull_intact(cb, False, True) is True     # 央行买 + 实际利率↓
    assert gl._is_bull_intact({"raw": -1.0}, True, True) is False   # 央行停买
    assert gl._is_bull_intact(cb, False, False) is False   # 驱动都不利


# ---- driver readers ----
def test_drv_cb(tmp_path):
    st = Store(tmp_path / "t.sqlite"); _seed_micro(st, cb_buying=True)
    d = gl.drv_cb(st)
    assert d and d["flag"] == "利好" and d["raw"] > 0
    # 换算系数钉死: 种子月增 8 万oz → 8*0.3110 吨(曾写成 0.0311 差10倍)
    assert abs(d["raw"] - 8 * 0.3110) < 1e-9
    assert d["value"] == "+2.5吨/月"


def test_drv_spec_and_commercial(tmp_path):
    st = Store(tmp_path / "t.sqlite"); _seed_micro(st)
    sp = gl.drv_spec(st); assert sp and sp["raw"] >= 0
    cm = gl.drv_commercial(st); assert cm and cm["raw"] >= 0


def test_drv_inventory_tight(tmp_path):
    st = Store(tmp_path / "t.sqlite"); _seed_micro(st, inv_tonnes=838.0)
    # 近1年库存需下降才判紧缺 — 这里稳定,只验不崩
    d = gl.drv_inventory(st)
    assert d and "COMEX" in d["label"]


# ---- 集成: 牛市回调 vs 非牛市 ----
def test_gold_locate_bull_pullback(tmp_path):
    """价格筑底(单边跌) + 央行买 + 期限溢价结构↑ → 牛市回调(加仓位)。"""
    st = Store(tmp_path / "t.sqlite")
    _full_seed(st, [200 - 0.5 * i for i in range(200)], cb_buying=True, tp_rising=True)
    r = gl.gold_locate(st)
    assert r["valid"]
    assert r["bull_intact"] is True
    assert r["stage_note"] == "牛市回调(加仓位)"      # 关键修复: v1 会判 ambiguous 筑底
    assert "加仓" in r["action"]
    assert r["regime"].startswith("牛市")


def test_gold_locate_non_bull(tmp_path):
    """央行停买 → bull_intact False → stage_note=base(无牛市回调精炼), 操作偏谨慎。"""
    st = Store(tmp_path / "t.sqlite")
    _full_seed(st, [200 - 0.5 * i for i in range(200)], cb_buying=False, tp_rising=True)
    r = gl.gold_locate(st)
    assert r["valid"]
    assert r["bull_intact"] is False
    assert r["stage_note"] == r["stage"]               # 不精炼
    assert "谨慎" in r["action"] or "观望" in r["action"]


def test_gold_locate_confidence_in_range(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _full_seed(st, [100 + 0.3 * i for i in range(200)], cb_buying=True)
    r = gl.gold_locate(st)
    assert 0.0 <= r["confidence"] <= 1.0
    assert r["confidence_band"] in ("强", "中", "弱")
    # 三栏驱动齐全
    for layer in ("底层", "中期", "短期"):
        assert layer in r["drivers"] and len(r["drivers"][layer]) == 3


def test_gold_locate_invalid_when_no_gold(tmp_path):
    st = Store(tmp_path / "t.sqlite")     # 空
    r = gl.gold_locate(st)
    assert r["valid"] is False
