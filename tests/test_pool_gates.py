"""gates 纯函数(V8): 扣非同比 / 活跃环解析 / 三环地板判定(预告单腿·快报双轴·正式扣非)。"""
from stockagent.pool import gates as gt

CFG = {"floor_np_yoy": 50.0, "floor_rev_yoy": 20.0}


# ---------- deducted_yoy ----------
def test_deducted_yoy_same_period_comparison():
    npd = {"20250630": 80.0, "20260630": 120.0}
    assert gt.deducted_yoy(npd, "20260630") == 50.0     # 120/80−1
    # 年报期对上年年报
    npd2 = {"20241231": 50.0, "20251231": 100.0}
    assert gt.deducted_yoy(npd2, "20251231") == 100.0


def test_deducted_yoy_missing_base_returns_none():
    assert gt.deducted_yoy({"20260630": 100.0}, "20260630") is None       # 无上年同期
    assert gt.deducted_yoy({"20250630": 0.0, "20260630": 100.0}, "20260630") is None  # 基数 0
    assert gt.deducted_yoy({"20250630": -5.0, "20260630": 100.0}, "20260630") is None  # 扭亏基数
    assert gt.deducted_yoy({}, "20260630") is None


# ---------- resolve_active_ring ----------
def test_active_ring_latest_landed_by_date():
    f = {"announce_date": "2026-07-15", "yoy": 80.0, "type": "预增"}
    e = {"announce_date": "2026-08-01", "np_yoy": 75.0, "rev_yoy": 30.0}
    a = {"announce_date": "2026-08-20", "np_yoy": 70.0, "rev_yoy": 25.0}
    # 8-10: 预告(07-15)与快报(08-01)均已落地 → 最新=快报
    r = gt.resolve_active_ring(f, e, a, "2026-08-10")
    assert r["ring"] == "express" and r["announce_date"] == "2026-08-01"
    # 7-20: 只有预告落地 → 预告
    r_pre = gt.resolve_active_ring(f, e, a, "2026-07-20")
    assert r_pre["ring"] == "forecast" and r_pre["announce_date"] == "2026-07-15"
    # 8-25: 正式报最新
    r2 = gt.resolve_active_ring(f, e, a, "2026-08-25")
    assert r2["ring"] == "actual"
    # 全未落地(事件日在未来) → None
    assert gt.resolve_active_ring(f, e, a, "2026-07-01") is None
    assert gt.resolve_active_ring(None, None, None, "2026-08-25") is None


def test_active_ring_skips_future_announcements():
    f = {"announce_date": "2026-07-15", "yoy": 80.0, "type": "预增"}
    a = {"announce_date": "2026-08-30", "np_yoy": 70.0, "rev_yoy": 25.0}
    r = gt.resolve_active_ring(f, None, a, "2026-08-10")   # 正式报在未来
    assert r["ring"] == "forecast"


# ---------- ring_floor ----------
def test_floor_forecast_single_leg():
    # 预告 60% 预增 → 过(单腿:无营收轴)
    r = gt.ring_floor({"ring": "forecast", "announce_date": "2026-07-15",
                       "yoy": 60.0, "type": "预增"}, CFG)
    assert r["passed"] and r["np_yoy"] == 60.0 and r["rev_yoy"] is None
    # 40% 不够
    r2 = gt.ring_floor({"ring": "forecast", "yoy": 40.0, "type": "预增"}, CFG)
    assert not r2["passed"]
    # 预亏族直接不过
    r3 = gt.ring_floor({"ring": "forecast", "yoy": -60.0, "type": "首亏"}, CFG)
    assert not r3["passed"]


def test_floor_forecast_turnaround_passes_with_flag():
    r = gt.ring_floor({"ring": "forecast", "yoy": None, "type": "扭亏"}, CFG)
    assert r["passed"] and "扭亏" in r["flags"]


def test_floor_express_dual_axis():
    base = {"ring": "express", "np_yoy": 75.0, "rev_yoy": 30.0}
    assert gt.ring_floor(base, CFG)["passed"]
    assert not gt.ring_floor({**base, "rev_yoy": 15.0}, CFG)["passed"]   # 营收不够
    assert not gt.ring_floor({**base, "np_yoy": 45.0}, CFG)["passed"]    # 净利不够
    assert not gt.ring_floor({**base, "rev_yoy": None}, CFG)["passed"]   # 缺数据


def test_floor_actual_prefers_deducted_with_fallback_flag():
    base = {"ring": "actual", "np_yoy": 60.0, "rev_yoy": 30.0}
    # 无扣非(sina 未拉) → 归母回退 + 未精筛旗
    r = gt.ring_floor(base, CFG, deducted=None)
    assert r["passed"] and r["np_axis"] == "reported" and "未精筛" in r["flags"]
    # 扣非 55%(过) 覆盖归母
    r2 = gt.ring_floor(base, CFG, deducted=55.0)
    assert r2["passed"] and r2["np_axis"] == "deducted" and not r2["flags"]
    assert r2["np_yoy"] == 55.0
    # 扣非 30%(不过) 即使归母 60% 也不过——正式环利润轴=扣非
    r3 = gt.ring_floor(base, CFG, deducted=30.0)
    assert not r3["passed"]
    # 扣非负增长
    r4 = gt.ring_floor(base, CFG, deducted=-10.0)
    assert not r4["passed"]
