"""预告队列研究纯核心测试(cohort.py · 2026-10 grill 定稿口径的锁死件)。"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from stockagent.pool import cohort as ch


# ---------- forecast_cutoff(截点 · Q2 定稿) ----------

def test_forecast_cutoff_four_tails():
    assert str(ch.forecast_cutoff("20251231")) == "2026-01-31"   # 年报截止在次年
    assert str(ch.forecast_cutoff("20250331")) == "2025-04-15"
    assert str(ch.forecast_cutoff("20250630")) == "2025-07-15"
    assert str(ch.forecast_cutoff("20250930")) == "2025-10-15"


def test_forecast_cutoff_bad_period():
    for bad in ("", "2025", "20251331", "2025-0630", 20250630, None):
        with pytest.raises(ValueError):
            ch.forecast_cutoff(bad)


# ---------- cohort_events(队列口径 · Q3 定稿: 含续留再过门/迟到预告不计) ----------

def _ev(code, date, period="20250630", ring="forecast", yoy=80.0, ftype="预增"):
    return {"code": code, "date": date, "period": period, "ring": ring,
            "np_yoy": yoy if ring != "forecast" else None, "rev_yoy": None,
            "type": ftype if ring == "forecast" else None, "st": False}


def test_cohort_events_filters():
    events = [
        _ev("600001", "2025-07-01"),            # 窗内预告 ✓
        _ev("600002", "2025-07-15"),            # 截点当日 ✓(闭区间)
        _ev("600003", "2025-07-16"),            # 迟到预告 ✗
        _ev("600004", "2025-07-10", ring="express"),   # 快报环 ✗
        _ev("600005", "2025-07-10", period="20250331"),  # 他期 ✗
    ]
    got = ch.cohort_events(events, "20250630", "2025-07-15")
    assert [e["code"] for e in got] == ["600001", "600002"]


def test_cohort_events_keeps_continuing_members():
    """含续留语义 = 只看「预告环过门事件」,不要求 out→in 转换(无转换状态可查)。"""
    events = [_ev("600001", "2025-07-02"), _ev("600009", "2025-07-03")]
    got = ch.cohort_events(events, "20250630", "2025-07-15")
    assert len(got) == 2


# ---------- leg_return(持有窗 · Q5/Q13: 买卖同为开盘) ----------

def _open_series(dates, prices):
    return pd.Series(prices, index=dates, dtype=float)


def test_leg_return_basic_buy_next_day_sell_at_deadline():
    # 截点 7/15(周二,交易日) → 买 7/16 开盘;截止 8/31(周日) → 前移 8/29(周五)开盘
    s = _open_series(["2025-07-15", "2025-07-16", "2025-08-28", "2025-08-29"],
                     [10.0, 10.0, 11.0, 12.0])
    leg = ch.leg_return(s, "2025-07-15", "2025-08-31")
    assert leg is not None
    assert leg["buy_date"] == "2025-07-16" and leg["sell_date"] == "2025-08-29"
    assert leg["ret"] == pytest.approx(12.0 / 10.0 - 1.0)


def test_leg_return_cutoff_non_trading_and_deadline_trading():
    # 截点 1/31(周六) → 买 2/3(周一)开盘;截止 4/30(周三,交易日) → 当日开盘卖
    s = _open_series(["2025-01-30", "2025-02-03", "2025-04-29", "2025-04-30"],
                     [8.0, 10.0, 13.0, 13.0])
    leg = ch.leg_return(s, "2025-01-31", "2025-04-30")
    assert leg["buy_date"] == "2025-02-03" and leg["sell_date"] == "2025-04-30"
    assert leg["ret"] == pytest.approx(13.0 / 10.0 - 1.0)


def test_leg_return_suspension_and_degenerate():
    # 窗内完全无数据(长期停牌) → None
    s = _open_series(["2025-07-01", "2025-09-15"], [10.0, 10.0])
    assert ch.leg_return(s, "2025-07-15", "2025-08-31") is None
    # 窗内仅一个交易日(买卖同日退化) → None
    s2 = _open_series(["2025-07-10", "2025-08-01"], [10.0, 11.0])
    assert ch.leg_return(s2, "2025-07-15", "2025-08-31") is None
    # 价格非数/≤0 → None
    s3 = _open_series(["2025-07-16", "2025-08-29"], [float("nan"), 11.0])
    assert ch.leg_return(s3, "2025-07-15", "2025-08-31") is None


def test_index_window_return_open_col():
    df = pd.DataFrame({"open": [3800.0, 3900.0, 4056.0, 4100.0],
                       "close": [3810.0, 3895.0, 4050.0, 4095.0]},
                      index=["2025-07-15", "2025-07-16", "2025-08-28", "2025-08-29"])
    leg = ch.index_window_return(df, "2025-07-15", "2025-08-31")
    assert leg is not None and leg["ret"] == pytest.approx(4100.0 / 3900.0 - 1.0)
    assert ch.index_window_return(df, "2025-07-15", "2025-08-31", col="high") is None


# ---------- adaptive_k / select_arm(精选臂 · Q11/Q12 定稿) ----------

def test_adaptive_k():
    assert ch.adaptive_k(100, k_cap=20, frac=0.25) == 20   # 25%×100=25 → 卡 cap
    assert ch.adaptive_k(60, k_cap=20, frac=0.25) == 15    # 25%×60=15
    assert ch.adaptive_k(8, k_cap=20, frac=0.25) == 2
    assert ch.adaptive_k(3, k_cap=20, frac=0.25) == 1      # floor=0 → 至少 1
    assert ch.adaptive_k(0) == 0


def _mem(code, score=None, flags=None, yellow=False, unrefined=False):
    return {"code": code, "score": score, "flags": flags or [],
            "receivable_yellow": yellow, "unrefined": unrefined}


def test_select_arm_order_and_ties():
    ms = [_mem("600003", 1.2), _mem("600001", 0.5), _mem("600002", 0.5),
          _mem("600004", None), _mem("600005", 0.9)]
    assert ch.select_arm(ms, 3) == ["600001", "600002", "600005"]  # 并列按代码,score缺排尾
    assert ch.select_arm(ms, 99) == ["600001", "600002", "600005", "600003", "600004"]
    assert ch.select_arm(ms, 0) == []


def test_select_arm_b2_exclusions():
    ms = [_mem("600001", 0.5), _mem("600002", 0.6, flags=["扭亏"]),
          _mem("600003", 0.7, yellow=True), _mem("600004", 0.8, unrefined=True),
          _mem("600005", 0.9)]
    assert ch.select_arm(ms, 3) == ["600001", "600002", "600003"]          # B1 全体排序
    assert ch.select_arm(ms, 3, exclude_flagged=True) == ["600001", "600005"]  # B2 剔后同K少中选


# ---------- arm_return / aggregate_arm(聚合 · Q14 期数等权) ----------

def test_arm_return_equal_weight():
    assert ch.arm_return([0.1, 0.2, None]) == pytest.approx(0.15)
    assert ch.arm_return([]) is None
    assert ch.arm_return([None]) is None


def test_aggregate_arm_counts_and_capital():
    rows = [
        {"period": "20240930", "ret": 0.10, "index_ret": 0.02},
        {"period": "20241231", "ret": -0.05, "index_ret": 0.01},
        {"period": "20250331", "ret": 0.04, "index_ret": 0.04},   # 平手=不算赢(严格>)
        {"period": "20250630", "ret": None, "index_ret": 0.03},   # 无效期透明剔除
    ]
    a = ch.aggregate_arm(rows)
    assert a["n_periods"] == 4 and a["n_valid"] == 3
    assert a["wins"] == 1 and a["win_rate"] == pytest.approx(1 / 3)
    assert a["mean_excess"] == pytest.approx((0.08 - 0.06 + 0.0) / 3)
    assert a["capital"] == pytest.approx(1.10 * 0.95 * 1.04 - 1.0)
    assert a["index_capital"] == pytest.approx(1.02 * 1.01 * 1.04 - 1.0)
    assert a["valid_periods"] == ["20240930", "20241231", "20250331"]
    assert math.isclose(a["median_excess"], 0.0, abs_tol=1e-12)


# ---------- attribution(归因 · Q8) ----------

def test_attribution_groups_and_missing_label():
    rows = [
        {"industry": "光伏设备", "excess": 0.05, "ret": 0.10},
        {"industry": "光伏设备", "excess": -0.02, "ret": 0.01},
        {"industry": "工业金属", "excess": 0.10, "ret": 0.12},
        {"industry": None, "excess": 0.01, "ret": 0.02},
        {"industry": "光伏设备", "excess": None, "ret": 0.03},   # 缺收益不计
    ]
    got = ch.attribution(rows, "industry")
    by = {g["label"]: g for g in got}
    assert by["光伏设备"]["n"] == 2 and by["光伏设备"]["wins"] == 1
    assert by["光伏设备"]["mean_excess"] == pytest.approx(0.015)
    assert by["工业金属"]["win_rate"] == 1.0
    assert by["缺"]["n"] == 1


# ---------- bucket_labels(市值十分位 / PEG 五分位,组内相对) ----------

def test_bucket_labels_decile_and_none():
    caps = {f"c{i}": float(i) for i in range(1, 11)}     # c1 最小 … c10 最大
    caps["cx"] = None
    lab = ch.bucket_labels(caps, n_buckets=10)
    assert lab["c1"] == 1 and lab["c10"] == 10 and lab["cx"] is None
    quint = ch.bucket_labels({"a": 1.0, "b": 2.0, "c": 3.0}, n_buckets=5)
    assert quint == {"a": 2, "b": 4, "c": 5}   # rank pct 1/3·2/3·3/3 ×5 向上取整


# ---------- 臂C·实盘镜面(估值门两轨 + Top-N · 2026-10-17 复盘后加) ----------

def test_pb_percentile_asof_point_in_time():
    """as-of 截点: 截点后才「公布」的 bvps 阶梯不得抬高/降低 as-of 分位(防前视由调用方过滤,
    本函数只消费已过滤 events);截点后价格不进样本。"""
    dates = [f"2024-01-{d:02d}" for d in range(2, 26)] + \
            [f"2024-02-{d:02d}" for d in range(1, 29)] + \
            [f"2024-03-{d:02d}" for d in range(1, 30)] + \
            [f"2024-04-{d:02d}" for d in range(1, 30)] + \
            [f"2024-05-{d:02d}" for d in range(1, 30)] + \
            [f"2024-06-{d:02d}" for d in range(1, 29)]
    close = pd.Series([10.0 + 0.05 * i for i in range(len(dates))], index=dates)
    evs = [("2024-01-15", "20231231", 5.0), ("2024-04-20", "20240331", 8.0)]
    p_asof = ch.pb_percentile_asof(close, [e for e in evs if e[0] <= "2024-03-31"],
                                   "2024-03-31", min_history=20)
    assert p_asof is not None and 0.0 <= p_asof <= 1.0
    # 全期分位应不同(4/20 的 8.0 阶梯拉低 PB)——as-of 与全期确实分离
    p_full = ch.pb_percentile_asof(close, evs, "2024-06-28", min_history=20)
    assert p_full is not None and p_full != p_asof
    # 样本不足 → 留白 None
    assert ch.pb_percentile_asof(close, evs[:1], "2024-01-20", min_history=120) is None


def _mem_c(code, track="peg", peg=None, pb_pct=None):
    return {"code": code, "track": track, "peg": peg, "pb_pct": pb_pct}


def test_rank_two_track_and_pool_mirror():
    ms = [_mem_c("100001", "peg", peg=0.4), _mem_c("100002", "peg", peg=0.9),
          _mem_c("100003", "peg", peg=1.5),                      # 估值门不过(peg>1.0)
          _mem_c("200001", "pb", pb_pct=0.10), _mem_c("200002", "pb", pb_pct=0.28),
          _mem_c("200003", "pb", pb_pct=0.50),                   # 估值门不过(>0.30)
          _mem_c("200004", "pb", pb_pct=None)]                   # 留白=不过门
    got = ch.select_pool_mirror(ms, top_n=100)
    # 两轨各剩 2 只: peg 轨 100001,100002;pb 轨 200001,200002;轨内第一名并列 rank_pct=0.5
    assert set(got) == {"100001", "100002", "200001", "200002"}
    assert got[0] in ("100001", "200001")     # 两轨轨首并列,代码序稳定
    assert ch.select_pool_mirror(ms, top_n=2) == got[:2]
    # 排序细节: 轨内名次百分位
    ranked = ch.rank_two_track([m for m in ms if m["code"] in
                                {"100001", "100002", "200001", "200002"}])
    assert ranked[0]["rank_pct"] == 0.5 and ranked[-1]["rank_pct"] == 1.0


# ---------- 商誉档/红旗(2026-10-17 tushare 补拉后入队筛选) ----------

def test_goodwill_band_boundaries():
    assert ch.goodwill_band(0.0, 100.0) == "<1%"           # 行项为空≈无商誉
    assert ch.goodwill_band(0.9, 100.0) == "<1%"           # 0.9% 边界内
    assert ch.goodwill_band(1.0, 100.0) == "1-10%"         # 1% 边界(闭)
    assert ch.goodwill_band(9.99, 100.0) == "1-10%"
    assert ch.goodwill_band(10.0, 100.0) == "10-30%"
    assert ch.goodwill_band(29.9, 100.0) == "10-30%"
    assert ch.goodwill_band(30.0, 100.0) == ch.GOODWILL_RED_BAND   # 30% 红旗线(闭)
    assert ch.goodwill_band(50.0, 100.0) == ch.GOODWILL_RED_BAND


def test_goodwill_band_unknown_cases():
    assert ch.goodwill_band(None, 100.0) == "未知"         # 不可判
    assert ch.goodwill_band(10.0, None) == "未知"
    assert ch.goodwill_band(10.0, 0.0) == "未知"           # 净资产≤0
    assert ch.goodwill_band(10.0, float("nan")) == "未知"


# ---------- wash_sale_ratio(扭亏·上年计提占亏损比 · 2026-10-18) ----------

def test_wash_sale_ratio():
    # 宜宾纸业 2024 实例形状: 亏 1.28 亿 · 计提 0.77 亿 → ~60%
    assert ch.wash_sale_ratio(-76_874_187.48, -128_427_749.27) == pytest.approx(0.5986, abs=1e-3)
    assert ch.wash_sale_ratio(-100.0, -100.0) == pytest.approx(1.0)    # 亏损全由计提构成
    assert ch.wash_sale_ratio(50.0, -100.0) == 0.0                     # 减值转回(正值)按 0
    assert ch.wash_sale_ratio(-100.0, 100.0) is None                   # 上年盈利→无扭亏基数语义
    assert ch.wash_sale_ratio(None, -100.0) is None                    # 数据缺
    assert ch.wash_sale_ratio(-100.0, None) is None
    assert ch.wash_sale_ratio(-100.0, 0.0) is None                     # 分母为 0
