"""valuation 纯函数(V8): TTM 自算 / 股本反推 / PE_ttm / PEG(封顶) / PB 阶梯分位。"""
import numpy as np
import pandas as pd

from stockagent.pool import valuation as vl


def _series(vals, start="2024-01-02"):
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(vals))]
    return pd.Series(vals, index=idx)


# ---------- ttm_net_profit ----------
def test_ttm_quarter_and_annual():
    np_abs = {"20251231": 100.0, "20260630": 70.0, "20250630": 60.0, "20251231_x": 0}
    # 中报: TTM = 100 + 70 − 60 = 110
    assert vl.ttm_net_profit(np_abs, "20260630") == 110.0
    # 年报: TTM = 年报本身
    assert vl.ttm_net_profit(np_abs, "20251231") == 100.0
    # 缺上年同期 → None(诚实缺省)
    assert vl.ttm_net_profit({"20251231": 100.0, "20260630": 70.0}, "20260630") is None


# ---------- implied_shares / pe_ttm / peg ----------
def test_implied_shares_guards():
    assert abs(vl.implied_shares(1.0e10, 50.0) - 2.0e8) < 1e-6
    assert vl.implied_shares(None, 50.0) is None
    assert vl.implied_shares(1.0e10, 0.0) is None


def test_pe_ttm_and_lossy_none():
    # 50 元 × 2 亿股 / TTM 10 亿 = 10×
    assert abs(vl.pe_ttm(50.0, 2.0e8, 1.0e9) - 10.0) < 1e-9
    assert vl.pe_ttm(50.0, None, 1.0e9) is None        # 股本缺
    assert vl.pe_ttm(50.0, 2.0e8, None) is None        # TTM 缺
    assert vl.pe_ttm(50.0, 2.0e8, -1.0e8) is None      # TTM 亏损 → PE 无意义


def test_peg_and_growth_cap():
    assert abs(vl.peg(20.0, 40.0) - 0.5) < 1e-9
    assert vl.peg(20.0, None) is None
    assert vl.peg(20.0, -5.0) is None                  # 负增长
    # 巨大增长封顶: PE20 / min(1000, 300) = 0.0667(防 PEG 假性登顶)
    assert abs(vl.peg(20.0, 1000.0) - 20.0 / 300.0) < 1e-9


# ---------- pb_series / pb_percentile ----------
def test_pb_series_step_function_point_in_time():
    price = _series(np.full(700, 10.0) + np.linspace(0, 5, 700))  # 2024-01→2026-09,覆盖事件日
    events = [("2025-06-02", "20250331", 5.0), ("2026-06-01", "20260331", 10.0)]
    pb = vl.pb_series(price, events)
    assert len(pb) > 0
    pidx = price.index.astype(str)
    # 第一段(2025-06-02 后): 价/5 → ~2x
    m1 = (pidx >= "2025-06-02") & (pidx < "2026-06-01")
    first_seg = pb[(pb.index.astype(str) >= "2025-06-02") & (pb.index.astype(str) < "2026-06-01")]
    assert np.allclose(first_seg.values, price[m1]["close" if hasattr(price, "columns") else True] / 5.0
                       if hasattr(price, "columns") else price[m1] / 5.0)
    # 第二段: 价/10 → ~1x
    m2 = pidx >= "2026-06-01"
    second_seg = pb[pb.index.astype(str) >= "2026-06-01"]
    assert np.allclose(second_seg.values, price[m2]["close"] / 10.0
                       if hasattr(price, "columns") else price[m2] / 10.0)
    # 首个公告日之前无 PB(不回填 hindsight)
    assert pb[pb.index.astype(str) < "2025-06-02"].empty


def test_pb_series_empty_inputs():
    assert len(vl.pb_series(pd.Series(dtype=float), [("2025-01-01", "20241231", 5.0)])) == 0
    assert len(vl.pb_series(_series([1.0]), [])) == 0
    assert len(vl.pb_series(_series([1.0]), [("2025-01-01", "20241231", None)])) == 0


def test_pb_percentile_expanding_and_min_history():
    # 260 点: 前 259 是 1.0×(分位低),最后 1 点 5.0(新高 → 分位 ~1.0)
    s = pd.Series([1.0] * 259 + [5.0])
    pct = vl.pb_percentile(s, min_history=120)
    assert pct == 1.0
    # 样本不足 → None(启动期留白)
    assert vl.pb_percentile(pd.Series([1.0] * 50), min_history=120) is None
    # 中位水平 → ~0.5
    s2 = pd.Series(sorted(np.linspace(1.0, 3.0, 200)) + [2.0])
    assert abs(vl.pb_percentile(s2, min_history=120) - 0.5) < 0.05
