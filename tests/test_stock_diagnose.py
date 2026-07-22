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


# ---- stock_dividend_yield ----
def test_stock_dividend_yield_trailing_12mo():
    dv = pd.DataFrame({"cash_per_share": [3.0, 2.0, 5.0]},
                      index=["2026-01-15", "2025-12-10", "2024-06-01"])
    # 假设 "今天" = 2026-02-01(由 index.max() 推):近 365 天 = 3.0+2.0=5.0,价 100 → 5%
    yld = sd.stock_dividend_yield(dv, price=100.0)
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
