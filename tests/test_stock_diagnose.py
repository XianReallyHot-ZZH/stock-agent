"""Tests for stock_diagnose (Phase 2 C1): 三类自动判定 + 估值分位/zone + E3 + S07 利润归因 纯函数。
No network — synthetic series only(实盘 sanity 另跑 diagnose_stock on backfilled watchlist)。"""
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from stockagent.data import Store
from stockagent.tracker import stock_diagnose as sd


# params with known thresholds (hermetic — not depending on params.yaml values)
def _params():
    return {"stock": {"classify": {
        "cyclic_vol_threshold": 0.40,
        "growth_threshold": 0.15,
        "dividend_yield_threshold": 0.03,
        "pe_low_percentile": 0.30,
    }}}


# ---- annual_only ----
def test_annual_only_keeps_year_end_periods():
    s = pd.Series([100.0, 90.0, 95.0, 80.0],
                  index=["20251231", "20250930", "20250630", "20241231"])
    ann = sd.annual_only(s)
    assert list(ann.index) == ["20241231", "20251231"]  # only 1231, sorted
    assert list(ann.values) == [80.0, 100.0]


def test_annual_only_empty_safe():
    assert len(sd.annual_only(pd.Series(dtype=float))) == 0
    assert len(sd.annual_only(None)) == 0


# ---- revenue_cagr ----
def test_revenue_cagr_three_year():
    # 100 -> 152.0875 over 3y = exactly 15% CAGR (100 * 1.15^3)
    s = pd.Series([100.0, 115.0, 132.25, 152.0875],
                  index=["20221231", "20231231", "20241231", "20251231"])
    cagr = sd.revenue_cagr(s, 3)
    assert abs(cagr - 0.15) < 1e-9


def test_revenue_cagr_nan_when_short_or_nonpositive():
    short = pd.Series([100.0, 110.0], index=["20241231", "20251231"])
    assert np.isnan(sd.revenue_cagr(short, 3))            # need 4 points for 3y
    neg = pd.Series([-100.0, 0.0, 10.0, 20.0],
                    index=["20221231", "20231231", "20241231", "20251231"])
    assert np.isnan(sd.revenue_cagr(neg, 3))              # base ≤ 0


# ---- profit_growth_volatility ----
def test_profit_volatility_cyclic_vs_stable():
    # 周期:利润大幅摆动 100→150→80→160→70
    cyclic = pd.Series([100.0, 150.0, 80.0, 160.0, 70.0],
                       index=["20211231", "20221231", "20231231", "20241231", "20251231"])
    vol, mn = sd.profit_growth_volatility(cyclic)
    assert vol > 0.40                                    # 高波动 → 周期信号
    assert mn < -0.40                                    # 经历明显下滑年
    # 稳定:茅台式 10%/年(精确 1.1x 链)
    stable = pd.Series([100.0, 110.0, 121.0, 133.1],
                       index=["20221231", "20231231", "20241231", "20251231"])
    vol2, mn2 = sd.profit_growth_volatility(stable)
    assert vol2 < 0.05                                   # 低波动
    assert abs(mn2 - 0.10) < 1e-9


def test_profit_volatility_nan_when_short():
    s = pd.Series([100.0, 110.0], index=["20241231", "20251231"])
    vol, mn = sd.profit_growth_volatility(s)
    assert np.isnan(vol) and np.isnan(mn)


# ---- stock_dividend_yield(按节奏年化,非 365 天窗口) ----
def test_stock_dividend_yield_semi_annual():
    # 半年付:最近 2 次 = 年化分红(~180 天间隔 → N=2)
    dv = pd.DataFrame({"cash_per_share": [1.0, 1.0, 1.0, 1.0]},
                      index=["2025-01-15", "2025-07-15", "2026-01-15", "2026-07-15"])
    yld = sd.stock_dividend_yield(dv, price=100.0)    # last 2 = 2.0 → 2%
    assert abs(yld - 0.02) < 1e-9


def test_stock_dividend_yield_frequency_switch_not_double_counted():
    # 年付→半年付 切换:旧整笔(2.0)+ 新两次(1.0+1.0)。旧 365 天窗口会把 3 笔全算→4.0/40=10%;
    # 按节奏(中位间隔~180天→半年付→N=2)只取最近 2 次 = 2.0 → 5%,不重复计 FY2024 整笔
    dv = pd.DataFrame({"cash_per_share": [2.0, 1.0, 1.0]},
                      index=["2025-07-11", "2026-01-16", "2026-07-10"])
    yld = sd.stock_dividend_yield(dv, price=40.0)
    assert abs(yld - 0.05) < 1e-9


def test_stock_dividend_yield_no_data():
    assert np.isnan(sd.stock_dividend_yield(None, 100.0))
    assert np.isnan(sd.stock_dividend_yield(pd.DataFrame(columns=["cash_per_share"]), 100.0))
    assert np.isnan(sd.stock_dividend_yield(None, np.nan))


# ---- valuation_percentile ----
def test_valuation_percentile_extremes():
    s = pd.Series(np.arange(1.0, 21.0), index=[f"2020-01-{i:02d}" for i in range(1, 21)])
    assert sd.valuation_percentile(s) > 0.90              # last = max → 接近 1(最贵)
    s_rev = pd.Series(np.arange(20.0, 0.0, -1.0),
                      index=[f"2020-01-{i:02d}" for i in range(1, 21)])
    assert sd.valuation_percentile(s_rev) < 0.10          # last = min → 接近 0(最便宜)


def test_valuation_percentile_short_returns_nan():
    assert np.isnan(sd.valuation_percentile(pd.Series([1.0, 2.0, 3.0])))


# ---- classify_stock ----
def test_classify_growth():
    f = {"revenue_cagr": 0.30, "profit_cagr": 0.25, "profit_vol": 0.05,
         "pe_pct": 0.70, "div_yield": 0.005}
    p, sec = sd.classify_stock(f, _params())
    assert p == "growth" and sec == []


def test_classify_value():
    f = {"revenue_cagr": 0.05, "profit_cagr": 0.04, "profit_vol": 0.10,
         "pe_pct": 0.15, "div_yield": 0.045}
    p, sec = sd.classify_stock(f, _params())
    assert p == "value" and sec == []


def test_classify_cyclic_overrides_growth():
    """周期优先(结构信号):即便当前高增长,利润波动大 → 周期。"""
    f = {"revenue_cagr": 0.30, "profit_cagr": 0.40, "profit_vol": 0.60,
         "pe_pct": 0.50, "div_yield": 0.01}
    p, sec = sd.classify_stock(f, _params())
    assert p == "cyclic"
    assert "growth" in sec                                # 成长作次类


def test_classify_value_growth_tie_high_div_is_value():
    """价值成长双触发(茅台式):股息率高 → 价值为主,成长为次。"""
    f = {"revenue_cagr": 0.20, "profit_cagr": 0.18, "profit_vol": 0.08,
         "pe_pct": 0.40, "div_yield": 0.035}
    p, sec = sd.classify_stock(f, _params())
    assert p == "value"
    assert "growth" in sec


def test_classify_value_growth_tie_low_div_is_growth():
    """双触发但股息率低(靠 PE 分位低判价值)→ 成长为主,价值为次。"""
    f = {"revenue_cagr": 0.20, "profit_cagr": 0.18, "profit_vol": 0.08,
         "pe_pct": 0.20, "div_yield": 0.01}
    p, sec = sd.classify_stock(f, _params())
    assert p == "growth"
    assert "value" in sec


def test_classify_default_mature_is_value():
    """无增长、估值不便宜、波动不大 → 成熟防御,归价值。"""
    f = {"revenue_cagr": 0.05, "profit_cagr": 0.03, "profit_vol": 0.12,
         "pe_pct": 0.55, "div_yield": 0.015}
    p, sec = sd.classify_stock(f, _params())
    assert p == "value" and sec == []


def test_classify_no_data_returns_none():
    f = {"revenue_cagr": np.nan, "profit_cagr": np.nan, "profit_vol": np.nan,
         "pe_pct": np.nan, "div_yield": np.nan}
    p, sec = sd.classify_stock(f, _params())
    assert p is None and sec == []


# ---- diagnose_valuation_zone ----
def test_valuation_zone_four_quadrants():
    assert sd.diagnose_valuation_zone(0.10, 0.15)["zone"] == "低位·便宜"   # 双低
    assert sd.diagnose_valuation_zone(0.90, 0.85)["zone"] == "高位·偏贵"   # 双高
    assert sd.diagnose_valuation_zone(0.15, 0.85)["zone"] == "结构分化"     # 跨中线
    assert sd.diagnose_valuation_zone(0.40, 0.45)["zone"] == "中位·中性"   # 都中间


def test_valuation_zone_pe_only_fallback():
    z = sd.diagnose_valuation_zone(0.10, np.nan)
    assert z["zone"] == "低位·便宜" and z["valid"] is True
    assert sd.diagnose_valuation_zone(np.nan, np.nan)["valid"] is False


# ---- _as_of ----
def test_as_of_returns_last_on_or_before():
    df = pd.DataFrame({"close": [10.0, 11.0, 12.0]}, index=["2020-01-02", "2020-06-01", "2021-01-04"])
    assert float(sd._as_of(df, "2020-01-02")["close"]) == 10.0   # 恰好
    assert float(sd._as_of(df, "2020-05-15")["close"]) == 10.0   # 往前取
    assert float(sd._as_of(df, "2025-12-31")["close"]) == 12.0   # 最后值
    assert sd._as_of(df, "2019-01-01") is None                   # 早于全部
    assert sd._as_of(None, "2020-01-02") is None


# ---- profit_attribution (S07) ----
def test_profit_attribution_additive_decomposition():
    # p0=100 pe0=20(eps0=5) → p1=150 pe1=15(eps1=10) div=10
    a = sd.profit_attribution(p0=100.0, pe0=20.0, p1=150.0, pe1=15.0, dividends_per_share=10.0)
    assert a["valid"] is True
    assert abs(a["eps0"] - 5.0) < 1e-9 and abs(a["eps1"] - 10.0) < 1e-9
    assert abs(a["earnings_return"] - 1.0) < 1e-9       # EPS 翻倍 → +100% 业绩
    assert abs(a["price_return"] - 0.5) < 1e-9           # 100→150
    assert abs(a["valuation_return"] - (-0.5)) < 1e-9    # 残差:PE 20→15 大幅收缩
    assert abs(a["dividend_return"] - 0.1) < 1e-9         # 10/100
    assert abs(a["total_return"] - 0.6) < 1e-9            # 价格+分红
    # 三段和 = 总回报(精确对账)
    s = a["earnings_return"] + a["valuation_return"] + a["dividend_return"]
    assert abs(s - a["total_return"]) < 1e-9
    # share 之和 = 1
    sh = a["share_earnings"] + a["share_valuation"] + a["share_dividend"]
    assert abs(sh - 1.0) < 1e-9


def test_profit_attribution_zero_total_shares_nan():
    # p0=p1 同价同 PE,无分红 → total=0 → share NaN
    a = sd.profit_attribution(100.0, 20.0, 100.0, 20.0, 0.0)
    assert abs(a["total_return"]) < 1e-9
    assert np.isnan(a["share_earnings"])


def test_profit_attribution_invalid_inputs():
    assert sd.profit_attribution(0.0, 20.0, 100.0, 15.0, 0.0)["valid"] is False     # p0≤0
    assert sd.profit_attribution(100.0, 0.0, 100.0, 15.0, 0.0)["valid"] is False    # pe0≤0
    assert sd.profit_attribution(100.0, 20.0, np.nan, 15.0, 0.0)["valid"] is False  # p1 NaN


# ---- diagnose_attribution store 集成 ----
def _store():
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_diagnose_attribution_store_roundtrip():
    st = _store()
    # 价格(只给 close,其余列 upsert 默认 0)
    px = pd.DataFrame({"close": [100.0, 110.0, 120.0]},
                      index=["2020-01-02", "2020-06-01", "2021-01-04"])
    st.upsert_prices("TEST", px, source="sina_raw")
    # PE(TTM)
    pe = pd.DataFrame({"value": [20.0, 15.0]}, index=["2020-01-02", "2021-01-04"])
    st.upsert_stock_valuation("TEST", "pe_ttm", pe, source="baidu")
    # 分红:持仓期内 1 次
    dv = pd.DataFrame({"announce_date": ["2020-06-10"], "cash_per_share": [5.0],
                       "stock_div_10": [0], "trans_10": [0]}, index=["2020-06-15"])
    st.upsert_stock_dividend("TEST", dv, source="sina")

    a = sd.diagnose_attribution("TEST", st, t0="2020-01-02", t1="2021-01-04")
    assert a["valid"] is True
    assert a["t0"] == "2020-01-02" and a["t1"] == "2021-01-04"
    assert abs(a["period_dividends_per_share"] - 5.0) < 1e-9
    # eps0=100/20=5, eps1=120/15=8 → 业绩=8/5-1=0.6;价格=0.2;估值=0.2-0.6=-0.4;分红=5/100=0.05
    assert abs(a["earnings_return"] - 0.6) < 1e-9
    assert abs(a["valuation_return"] - (-0.4)) < 1e-9
    assert abs(a["dividend_return"] - 0.05) < 1e-9
    assert abs(a["total_return"] - 0.25) < 1e-9


def test_diagnose_attribution_as_of_non_trade_day():
    """端点不是交易日 → 取前一交易日(baidu PE 稀疏同理取最近前值)。"""
    st = _store()
    px = pd.DataFrame({"close": [100.0, 120.0]}, index=["2020-01-02", "2021-01-04"])
    st.upsert_prices("TEST", px)
    pe = pd.DataFrame({"value": [20.0, 15.0]}, index=["2020-01-02", "2021-01-04"])
    st.upsert_stock_valuation("TEST", "pe_ttm", pe)
    # t0/t1 取周末(2020-01-04 周六 → 取 2020-01-02;2021-01-10 周日 → 取 2021-01-04)
    a = sd.diagnose_attribution("TEST", st, t0="2020-01-04", t1="2021-01-10")
    assert a["valid"] is True
    assert a["t0"] == "2020-01-02" and a["t1"] == "2021-01-04"


# ---- attribution_by_year(多年归因,给看板堆叠柱) ----
def test_attribution_by_year_multi_year_additive():
    st = _store()
    # 4 个年末交易日 → 3 个自然年区间;每段 close/PE 已给,每年一次分红
    px = pd.DataFrame({"close": [100.0, 120.0, 110.0, 140.0]},
                      index=["2020-12-31", "2021-12-31", "2022-12-30", "2023-12-29"])
    st.upsert_prices("TEST", px, source="sina_raw")
    pe = pd.DataFrame({"value": [20.0, 18.0, 22.0, 19.0]},
                      index=["2020-12-31", "2021-12-31", "2022-12-30", "2023-12-29"])
    st.upsert_stock_valuation("TEST", "pe_ttm", pe, source="baidu")
    dv = pd.DataFrame({"announce_date": ["2021-06-01", "2022-06-01", "2023-06-01"],
                       "cash_per_share": [3.0, 3.0, 3.0], "stock_div_10": [0, 0, 0],
                       "trans_10": [0, 0, 0]},
                      index=["2021-06-15", "2022-06-15", "2023-06-15"])
    st.upsert_stock_dividend("TEST", dv, source="sina")

    rows = sd.attribution_by_year("TEST", st, years=6)
    assert [r["year"] for r in rows] == ["2021", "2022", "2023"]
    for r in rows:                                    # 三段可加 = 总回报(对账)
        assert abs(r["earnings"] + r["valuation"] + r["dividend"] - r["total"]) < 1e-9
    # 2021:p0=100 pe0=20(eps5) → p1=120 pe1=18(eps6.667);业绩=eps1/eps0-1=0.3333,分红=3/100=0.03
    r21 = rows[0]
    assert abs(r21["earnings"] - ((120 / 18) / (100 / 20) - 1)) < 1e-9
    assert abs(r21["dividend"] - 0.03) < 1e-9

    # years 截断:years=1 → 只取最近 1 个区间
    rows1 = sd.attribution_by_year("TEST", st, years=1)
    assert len(rows1) == 1 and rows1[0]["year"] == "2023"


def test_attribution_by_year_empty():
    st = _store()
    assert sd.attribution_by_year("NOPE", st) == []


# ---- davis_signal (S10) ----
def test_davis_double_play():
    # 业绩正增加速(0.30>0.10) + PE 扩张 → 强双击
    s = sd.davis_signal(0.30, 0.10, pe_change=0.20, pe_pct=0.50)
    assert s["type"] == "double_play" and s["valid"]


def test_davis_double_play_setup():
    # 低 PE + 业绩正增(但 PE 未扩张)→ 买点
    s = sd.davis_signal(0.10, 0.05, pe_change=-0.05, pe_pct=0.20)
    assert s["type"] == "double_play_setup"


def test_davis_double_play_watch_low_pe_dip_not_kill():
    # 低 PE + 业绩负增(探底)→ 观察,不是双杀(杀不动已在地板的估值)
    s = sd.davis_signal(-0.05, 0.10, pe_change=-0.02, pe_pct=0.10)
    assert s["type"] == "double_play_watch"


def test_davis_double_kill():
    # 业绩负增 + PE 收缩 → 强双杀
    s = sd.davis_signal(-0.20, 0.10, pe_change=-0.15, pe_pct=0.50)
    assert s["type"] == "double_kill"


def test_davis_double_kill_risk():
    # 高 PE + 业绩负增(PE 未收缩)→ 预警
    s = sd.davis_signal(-0.10, -0.05, pe_change=0.05, pe_pct=0.85)
    assert s["type"] == "double_kill_risk"


def test_davis_neutral_growth_decelerating():
    # 仍正增但减速 + PE 中位 → 中性(非双击非双杀)
    s = sd.davis_signal(0.10, 0.25, pe_change=0.0, pe_pct=0.50)
    assert s["type"] == "neutral"


def test_davis_no_recent_growth_invalid():
    # 缺最近增速 → 无法判方向
    s = sd.davis_signal(np.nan, 0.10, pe_change=0.20, pe_pct=0.50)
    assert s["valid"] is False and s["type"] is None


# ---- diagnose_davis store 集成 ----
def test_diagnose_davis_store_integration():
    st = _store()
    # 净利年报:100→120(+20%)→130(+8.3%,减速但正增)
    fin = pd.DataFrame([
        {"report_period": "20231231", "metric": "net_profit", "value": 100.0},
        {"report_period": "20241231", "metric": "net_profit", "value": 120.0},
        {"report_period": "20251231", "metric": "net_profit", "value": 130.0},
    ])
    st.upsert_stock_financials("TEST", fin)
    # PE:1 年前 25 → 今 30(扩张);历史分位构造为低位
    pe_idx = [f"2024-{m:02d}-15" for m in range(1, 13)] + [f"2025-{m:02d}-15" for m in range(1, 13)]
    pe_vals = [20.0] * 12 + [30.0] * 12  # 12 个 20(低)+ 12 个 30(今=高位)
    st.upsert_stock_valuation("TEST", "pe_ttm", pd.DataFrame({"value": pe_vals}, index=pe_idx))

    d = sd.diagnose_davis("TEST", st)
    assert d["valid"] is True
    # 最新 YoY = 130/120-1 ≈ +8.3%(正增);prev = 120/100-1 = +20%(减速→accel False)
    assert abs(d["profit_yoy_latest"] - 0.0833) < 1e-3
    assert abs(d["profit_yoy_prev"] - 0.20) < 1e-3
    # PE 今 30 vs ~1 年前 20 → 扩张(+50%);分位:12 个 20 < 30 → 12/24=0.5(非低位)
    assert d["pe_change"] > 0.4
    # 正增但减速 + PE 非低位 → 中性
    assert d["type"] == "neutral"


# ---- disclosure_deadline / is_disclosed_by (G2 公告时间差) ----
def test_disclosure_deadline_by_report_type():
    assert sd.disclosure_deadline("20251231") == "2026-04-30"   # 年报→次年 4/30
    assert sd.disclosure_deadline("20250331") == "2025-04-30"   # 一季报→当年 4/30
    assert sd.disclosure_deadline("20250630") == "2025-08-31"   # 半年报→8/31
    assert sd.disclosure_deadline("20250930") == "2025-10-31"   # 三季报→10/31
    assert sd.disclosure_deadline("20251232") is None           # 非法期末
    assert sd.disclosure_deadline("bad") is None


def test_is_disclosed_by_boundary():
    # 2024 年报 deadline = 2025-04-30
    assert sd.is_disclosed_by("20241231", "2025-05-01") is True
    assert sd.is_disclosed_by("20241231", "2025-04-30") is True    # 当天(已过法定日)
    assert sd.is_disclosed_by("20241231", "2025-04-29") is False   # 前一天(未披露完)


# ---- growth_quality (异常高增速 + 低基数 + 2y CAGR) ----
def test_growth_quality_normal():
    g = sd.growth_quality(base=90.0, latest=120.0, prev_base=100.0)
    assert g["valid"] is True
    assert abs(g["yoy"] - (120 / 90 - 1)) < 1e-9
    assert g["abnormal"] is False                     # 33% < 150%, 非低基数
    assert abs(g["trustworthy"] - g["yoy"]) < 1e-9    # 正常 → 用 YoY


def test_growth_quality_low_base_spike_is_mirage():
    # 上年腰斩(100→10)后反弹(10→100):YoY +900% 是基数幻觉,真实 2y CAGR = 0
    g = sd.growth_quality(base=10.0, latest=100.0, prev_base=100.0)
    assert g["abnormal"] is True
    assert g["yoy"] >= 1.5 and abs(g["cagr2"]) < 1e-9  # 2y CAGR=0(没真涨)
    assert abs(g["trustworthy"] - 0.0) < 1e-9          # 可信增速用 2y CAGR(0),非 +900%


def test_growth_quality_threshold_only():
    # 增速 ≥150% 但非低基数 → 仍异常,用 2y CAGR
    g = sd.growth_quality(base=100.0, latest=260.0, prev_base=200.0)
    assert g["abnormal"] is True                       # YoY 160%≥150%
    assert "低基数" not in (g["reason"] or "")
    assert abs(g["trustworthy"] - g["cagr2"]) < 1e-9


def test_growth_quality_invalid():
    assert sd.growth_quality(0.0, 100.0)["valid"] is False
    assert sd.growth_quality(-5.0, 100.0)["valid"] is False
    assert sd.growth_quality(np.nan, 100.0)["valid"] is False


# ---- earnings_quality (业绩含金量/一次性利润 Tier-1) ----
def test_earnings_quality_non_recurring_high():
    # 归母100(上年80) vs 扣非50(上年80):一次性占比50%≥30% → low_quality
    q = sd.earnings_quality(np_latest=100.0, np_base=80.0, ded_latest=50.0, ded_base=80.0)
    assert q["valid"] is True
    assert abs(q["non_recurring_frac"] - 0.5) < 1e-9
    assert q["low_quality"] is True


def test_earnings_quality_deviation_only():
    # 归母90→180(+100%) 扣非160→170(+6%):占比小(5.6%<30%)但增速背离≈94pp≥30% → low_quality
    q = sd.earnings_quality(np_latest=180.0, np_base=90.0, ded_latest=170.0, ded_base=160.0)
    assert q["valid"] is True
    assert q["non_recurring_frac"] < 0.30                 # 不是占比路径触发
    assert abs(q["np_yoy"] - 1.0) < 1e-9
    assert q["deviation"] >= 0.30
    assert q["low_quality"] is True


def test_earnings_quality_clean():
    # 归母与扣非同节奏:占比5%、增速背离≈0 → 干净
    q = sd.earnings_quality(np_latest=100.0, np_base=80.0, ded_latest=95.0, ded_base=76.0)
    assert q["valid"] is True
    assert q["low_quality"] is False
    assert q["reason"] == ""


def test_earnings_quality_invalid_and_base_edge():
    # 归母≤0 / 缺扣非 → invalid
    assert sd.earnings_quality(-50.0, 100.0, 40.0, 80.0)["valid"] is False
    assert sd.earnings_quality(100.0, 80.0, np.nan, 80.0)["valid"] is False
    # 归母>0 但上年基数≤0 → valid 仍 True,只是 np_yoy 算不出(NaN),不崩
    q = sd.earnings_quality(np_latest=100.0, np_base=-10.0, ded_latest=90.0, ded_base=80.0)
    assert q["valid"] is True
    assert np.isnan(q["np_yoy"])


# ---- positioning_score (提前埋伏分,纯函数)----
def test_positioning_strong():
    # 预告转多 + 干净 + 低 PE + 黄金窗 → 高分
    s = sd.positioning_score(leading=1.0, quality_ok=True, pe_pct=0.1, days_to_deadline=60)
    assert s["valid"] is True
    assert abs(s["score"] - 90.0) < 1e-9          # 1×1×0.9×1.0×100
    assert s["timing_factor"] == 1.0


def test_positioning_quality_penalty():
    # Q1 触发(一次性)→ 含金量重罚 0.2
    s = sd.positioning_score(1.0, False, 0.1, 60)
    assert abs(s["quality_factor"] - 0.2) < 1e-9
    assert abs(s["score"] - 18.0) < 1e-9          # 1×0.2×0.9×1×100


def test_positioning_leading_zero_kills():
    # 预告恶化/无信号 → leading 0 → 0 分(乘法合成)
    assert sd.positioning_score(0.0, True, 0.1, 60)["score"] == 0.0


def test_positioning_disclosed_window_passed():
    # 已披露(days<0)→ timing 0.3,埋伏窗已过
    s = sd.positioning_score(1.0, True, 0.1, -5)
    assert abs(s["timing_factor"] - 0.3) < 1e-9
    assert abs(s["score"] - 27.0) < 1e-9          # 1×1×0.9×0.3×100


def test_positioning_no_deadline_and_far_decay():
    assert sd.positioning_score(1.0, True, 0.1, np.nan)["timing_factor"] == 0.5   # 无 deadline→0.5
    assert sd.positioning_score(1.0, True, 0.1, 400)["timing_factor"] == 0.3      # 远期地板 0.3


def test_positioning_pe_nan_neutral():
    assert abs(sd.positioning_score(1.0, True, np.nan, 60)["valuation_factor"] - 0.5) < 1e-9


# ---- positioning_from_diag(装配,吃 diagnose_stock_full dict)----
def test_positioning_from_diag_forecast_bullish():
    d = {
        "symbol": "002460",
        "forecast": {"valid": True, "latest_sentiment": "bullish", "a1_deceleration": False,
                     "latest": {"yoy": 800.0, "type": "扭亏"}, "prior": {"sentiment": "bearish"}},
        "earnings_quality": {"low_quality": False},
        "davis": {"profit_yoy_latest": -0.5},
        "valuation_zone": {"pe_pct": 0.2},
        "pitfalls": {"disclosure": {"deadline": "2026-08-31"}, "net_profit": {"abnormal": False}},
    }
    p = sd.positioning_from_diag(d, asof="2026-07-30")
    assert p["signal_label"] == "预告转多"
    assert "新转多" in p["flags"]                       # prior bearish → 新转多
    assert p["leading_factor"] == 1.0
    # deadline 2026-08-31 − 2026-07-30 = 32 天(golden→timing 1.0);pe 0.2→vf 0.8
    assert abs(p["score"] - 80.0) < 1e-9               # 1×1×0.8×1×100


def test_positioning_from_diag_davis_fallback():
    # 无预告但年报业绩正增 → leading 0.5 退路
    d = {"forecast": {"valid": False}, "earnings_quality": {"low_quality": False},
         "davis": {"profit_yoy_latest": 0.3}, "valuation_zone": {"pe_pct": 0.4},
         "pitfalls": {"disclosure": {"deadline": "2026-08-31"}}}
    p = sd.positioning_from_diag(d, asof="2026-07-30")
    assert p["signal_label"] == "业绩正增"
    assert p["leading_factor"] == 0.5


def test_positioning_from_diag_no_signal():
    d = {"forecast": {"valid": False}, "earnings_quality": {"low_quality": False},
         "davis": {"profit_yoy_latest": float("nan")}, "valuation_zone": {}}
    p = sd.positioning_from_diag(d, asof="2026-07-30")
    assert p["leading_factor"] == 0.0 and p["score"] == 0.0


def test_positioning_from_diag_catalyst_period_wins():
    # 时效优先用预告期(20260630→2026-08-31)而非已报期截止(2026-04-30 已过)
    d = {"forecast": {"valid": True, "latest_sentiment": "bullish", "a1_deceleration": False,
                      "latest": {"period": "20260630", "type": "预增"}},
         "earnings_quality": {"low_quality": False}, "davis": {"profit_yoy_latest": 0.0},
         "valuation_zone": {"pe_pct": 0.2}, "pitfalls": {"disclosure": {"deadline": "2026-04-30"}}}
    p = sd.positioning_from_diag(d, asof="2026-07-30")
    assert p["deadline"] == "2026-08-31"            # 预告期截止覆盖已报期
    assert p["timing_factor"] == 1.0                # 32 天 = 黄金窗
    assert abs(p["score"] - 80.0) < 1e-9


# ---- diagnose_earnings_quality store 集成 ----
def test_diagnose_earnings_quality_store_integration():
    st = _store()
    # 归母 100→150(+50%) 扣非 95→110(+16%):占比小(27%<30%)但增速背离≈34pp≥30% → low_quality
    fin = pd.DataFrame([
        {"report_period": "20241231", "metric": "net_profit", "value": 100.0},
        {"report_period": "20251231", "metric": "net_profit", "value": 150.0},
        {"report_period": "20241231", "metric": "np_deducted", "value": 95.0},
        {"report_period": "20251231", "metric": "np_deducted", "value": 110.0},
    ])
    st.upsert_stock_financials("TEST", fin)
    q = sd.diagnose_earnings_quality("TEST", st)
    assert q["valid"] is True
    assert q["latest_period"] == "20251231"
    assert q["low_quality"] is True
    assert q["np_yoy"] > q["ded_yoy"]


def test_diagnose_earnings_quality_misaligned_periods():
    # 扣非只有 1 个年报 → 交集 <2 → invalid
    st = _store()
    fin = pd.DataFrame([
        {"report_period": "20241231", "metric": "net_profit", "value": 100.0},
        {"report_period": "20251231", "metric": "net_profit", "value": 130.0},
        {"report_period": "20251231", "metric": "np_deducted", "value": 96.0},
    ])
    st.upsert_stock_financials("TEST", fin)
    assert sd.diagnose_earnings_quality("TEST", st)["valid"] is False


def test_diagnose_davis_quality_warning_flag():
    # 归母高增(+150%)但扣非掉队(+1%)→ davis quality_warning=True,且六档 type 仍正常输出
    st = _store()
    fin = pd.DataFrame([
        {"report_period": "20241231", "metric": "net_profit", "value": 100.0},
        {"report_period": "20251231", "metric": "net_profit", "value": 250.0},
        {"report_period": "20241231", "metric": "np_deducted", "value": 95.0},
        {"report_period": "20251231", "metric": "np_deducted", "value": 96.0},
    ])
    st.upsert_stock_financials("TEST", fin)
    pe_idx = [f"2024-{m:02d}-15" for m in range(1, 13)] + [f"2025-{m:02d}-15" for m in range(1, 13)]
    pe_vals = [30.0] * 12 + [40.0] * 12
    st.upsert_stock_valuation("TEST", "pe_ttm", pd.DataFrame({"value": pe_vals}, index=pe_idx))
    d = sd.diagnose_davis("TEST", st)
    assert d["valid"] is True
    assert d["quality_warning"] is True
    assert d["earnings_quality"]["low_quality"] is True
    assert d["type"] in {"double_play", "double_play_setup", "double_play_watch",
                         "double_kill", "double_kill_risk", "neutral"}


# ---- diagnose_pitfalls store 集成 ----
def test_diagnose_pitfalls_detects_low_base_spike():
    st = _store()
    # 净利:2022=100 → 2023=10(腰斩) → 2024=100(反弹)→ YoY +900% 是幻觉,2y CAGR=0
    fin = pd.DataFrame([
        {"report_period": "20221231", "metric": "net_profit", "value": 100.0},
        {"report_period": "20231231", "metric": "net_profit", "value": 10.0},
        {"report_period": "20241231", "metric": "net_profit", "value": 100.0},
    ])
    st.upsert_stock_financials("TEST", fin)

    p = sd.diagnose_pitfalls("TEST", st, asof="2025-05-01")
    assert p["valid"] is True
    np_p = p["net_profit"]
    assert np_p["abnormal"] is True
    assert np_p["yoy"] >= 9.0                            # +900%(100/10−1=9.0 恰)
    assert abs(np_p["trustworthy"]) < 1e-9              # 可信=2y CAGR≈0
    # 公告时间差:2024 年报 deadline 2025-04-30,asof 2025-05-01 → 已披露
    assert p["disclosure"]["latest_period"] == "20241231"
    assert p["disclosure"]["deadline"] == "2025-04-30"
    assert p["disclosure"]["disclosed_by_asof"] is True


def test_diagnose_pitfalls_not_yet_disclosed():
    st = _store()
    fin = pd.DataFrame([{"report_period": "20251231", "metric": "net_profit", "value": 100.0},
                        {"report_period": "20241231", "metric": "net_profit", "value": 90.0}])
    st.upsert_stock_financials("TEST", fin)
    # asof 2026-02-01:2025 年报 deadline 2026-04-30 → 尚未披露
    p = sd.diagnose_pitfalls("TEST", st, asof="2026-02-01")
    assert p["disclosure"]["disclosed_by_asof"] is False


# ---- stock_forecast store (S08-G1 业绩预告链) ----
def test_stock_forecast_roundtrip():
    st = _store()
    rows = [
        ("600519", "20241231", 80.0, "预增", "2025-01-20"),
        ("600519", "20251231", -20.0, "预减", "2026-01-15"),
    ]
    assert st.upsert_stock_forecast(rows, source="em") == 2
    fc = st.get_stock_forecast_series("600519")
    assert len(fc) == 2
    assert list(fc.index) == ["20241231", "20251231"]
    assert st.last_stock_forecast_period("600519") == "20251231"


def test_stock_forecast_empty_ok():
    st = _store()
    assert st.upsert_stock_forecast([], source="em") == 0
    assert st.last_stock_forecast_period("NOPE") is None


# ---- forecast_type_sentiment ----
def test_forecast_type_sentiment():
    assert sd.forecast_type_sentiment("预增") == "bullish"
    assert sd.forecast_type_sentiment("扭亏") == "bullish"
    assert sd.forecast_type_sentiment("预减") == "bearish"
    assert sd.forecast_type_sentiment("首亏") == "bearish"
    assert sd.forecast_type_sentiment("不确定") == "neutral"
    assert sd.forecast_type_sentiment("未知类型") == "neutral"
    assert sd.forecast_type_sentiment(None) == "neutral"


# ---- diagnose_forecast_chain (A1 拐点 / A2 转空) ----
def test_diagnose_forecast_chain_a1_a2_double_warning():
    st = _store()
    rows = [
        ("TEST", "20241231", 80.0, "预增", "2025-01-20"),    # 上期:多 +80%
        ("TEST", "20251231", -20.0, "预减", "2026-01-15"),   # 本期:空 -20%
    ]
    st.upsert_stock_forecast(rows)
    d = sd.diagnose_forecast_chain("TEST", st)
    assert d["valid"] is True
    assert d["latest"]["sentiment"] == "bearish"
    assert d["a1_deceleration"] is True        # -20 < +80 增速下滑拐点
    assert d["a2_turn_bearish"] is True        # 多→空·戴维斯双杀前兆


def test_diagnose_forecast_chain_accelerating_no_warning():
    st = _store()
    rows = [
        ("TEST", "20241231", 20.0, "略增", "2025-01-20"),
        ("TEST", "20251231", 50.0, "预增", "2026-01-15"),    # 增速上行 + 仍多
    ]
    st.upsert_stock_forecast(rows)
    d = sd.diagnose_forecast_chain("TEST", st)
    assert d["a1_deceleration"] is False
    assert d["a2_turn_bearish"] is False       # 都 bullish,不触发


def test_diagnose_forecast_chain_no_data():
    st = _store()
    d = sd.diagnose_forecast_chain("NOPE", st)
    assert d["valid"] is False
