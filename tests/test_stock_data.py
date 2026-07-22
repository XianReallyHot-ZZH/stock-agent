"""Tests for Phase 2 个股层数据栈 (V5 tracker): _szsh_prefix bug fix (个股+ETF 双兼容) +
STOCK_VALUATION_INDICATORS 指标集(百度金矿字段验证的产物)+ stock_valuation store 往返。
No network — pure-fn + temp DB only(live shape 已在探针里验过,见 PHASE2_HANDOFF)。"""
import tempfile
from pathlib import Path

import pandas as pd

from stockagent.data import Store
from stockagent.data.fetcher import (
    STOCK_FINANCIAL_METRICS,
    STOCK_VALUATION_INDICATORS,
    _szsh_prefix,
)


def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


# ---- _szsh_prefix: 个股(Phase 2 修的主目标) ----
def test_szsh_prefix_sh_stocks():
    """上交所:主板 6xx / 科创板 68x / B股 9xx —— 旧 ETF-only 实现会把这些误判 sz(sina 直接 KeyError)。"""
    assert _szsh_prefix("600519") == "sh"  # 茅台 上证主板
    assert _szsh_prefix("601318") == "sh"  # 中国平安 上证主板
    assert _szsh_prefix("688981") == "sh"  # 中芯 科创板
    assert _szsh_prefix("900901") == "sh"  # 上证 B股


def test_szsh_prefix_sz_stocks():
    """深交所:主板 0xx(含 000/002/003) / 创业板 3xx。"""
    assert _szsh_prefix("000001") == "sz"  # 平安银行 深证主板
    assert _szsh_prefix("002415") == "sz"  # 海康威视 中小板
    assert _szsh_prefix("300750") == "sz"  # 宁德时代 创业板


def test_szsh_prefix_etf_backward_compat():
    """3 个现有 ETF 调用者(_fetch_sina/_fetch_baostock/fetch_etf_dividend)行为不能破。"""
    assert _szsh_prefix("510300") == "sh"  # 沪深300ETF
    assert _szsh_prefix("515880") == "sh"  # 煤炭ETF
    assert _szsh_prefix("512100") == "sh"  # 中证1000ETF
    assert _szsh_prefix("159915") == "sz"  # 创业板ETF
    assert _szsh_prefix("159819") == "sz"  # 人工智能ETF


def test_szsh_prefix_bj_unsupported():
    """北交所 8xx / 三板 4xx —— sina stock_zh_a_daily 不支持,按 bj 返回(fetch_stock_daily 会拒)。"""
    assert _szsh_prefix("830799") == "bj"
    assert _szsh_prefix("430047") == "bj"


# ---- STOCK_VALUATION_INDICATORS: 百度金矿字段集(探针验证产物) ----
def test_valuation_indicators_have_the_five_working():
    """实测 stock_zh_valuation_baidu 可用五指标(茅台/招行,2026-07-21 探针确认)。"""
    assert STOCK_VALUATION_INDICATORS["pe_ttm"] == "市盈率(TTM)"
    assert STOCK_VALUATION_INDICATORS["pe_static"] == "市盈率(静)"
    assert STOCK_VALUATION_INDICATORS["pb"] == "市净率"
    assert STOCK_VALUATION_INDICATORS["pcf"] == "市现率"
    assert STOCK_VALUATION_INDICATORS["market_cap"] == "总市值"


def test_valuation_indicators_exclude_broken_ps():
    """市销率(TTM) baidu 实测返回 None('NoneType' object is not subscriptable)→ 故意不入集,
    避免调用方踩坑。无股息率(另从 stock_history_dividend derive,不在本集)。"""
    keys = set(STOCK_VALUATION_INDICATORS)
    assert keys == {"pe_ttm", "pe_static", "pb", "pcf", "market_cap"}
    assert "ps" not in keys
    assert "dividend_yield" not in keys


# ---- stock_valuation store 往返(镜像 test_index_pe_roundtrip 风格) ----
def test_stock_valuation_roundtrip():
    st = _store()
    df = pd.DataFrame({"value": [18.24, 19.77]}, index=["2026-07-06", "2026-07-21"])
    assert st.upsert_stock_valuation("600519", "pe_ttm", df, source="baidu") == 2
    got = st.get_stock_valuation_series("600519", "pe_ttm")
    assert len(got) == 2
    assert float(got["value"].iloc[-1]) == 19.77
    assert st.last_stock_valuation_date("600519", "pe_ttm") == "2026-07-21"


def test_stock_valuation_multi_indicator_no_collision():
    """同 symbol+date 不同 indicator 共存(主键含 indicator),互不覆盖。"""
    st = _store()
    df_pe = pd.DataFrame({"value": [19.77]}, index=["2026-07-21"])
    df_pb = pd.DataFrame({"value": [6.04]}, index=["2026-07-21"])
    st.upsert_stock_valuation("600519", "pe_ttm", df_pe)
    st.upsert_stock_valuation("600519", "pb", df_pb)
    assert len(st.get_stock_valuation_series("600519", "pe_ttm")) == 1
    assert len(st.get_stock_valuation_series("600519", "pb")) == 1
    assert float(st.get_stock_valuation_series("600519", "pe_ttm")["value"].iloc[0]) == 19.77
    assert float(st.get_stock_valuation_series("600519", "pb")["value"].iloc[0]) == 6.04


def test_stock_valuation_upsert_is_idempotent():
    st = _store()
    df = pd.DataFrame({"value": [19.77]}, index=["2026-07-21"])
    st.upsert_stock_valuation("600519", "pe_ttm", df)
    df2 = pd.DataFrame({"value": [20.10]}, index=["2026-07-21"])  # 同日,更新
    st.upsert_stock_valuation("600519", "pe_ttm", df2)
    got = st.get_stock_valuation_series("600519", "pe_ttm")
    assert len(got) == 1                       # 无重复
    assert float(got["value"].iloc[0]) == 20.10  # 值已更新


def test_stock_valuation_date_filter_and_last_date():
    st = _store()
    df = pd.DataFrame({"value": [5.4, 5.57, 6.04]},
                      index=["2026-06-01", "2026-07-06", "2026-07-21"])
    st.upsert_stock_valuation("600519", "pb", df)
    got = st.get_stock_valuation_series("600519", "pb", start="2026-07-01")
    assert len(got) == 2
    assert st.last_stock_valuation_date("600519", "pb") == "2026-07-21"
    assert st.last_stock_valuation_date("600519") == "2026-07-21"  # 跨 indicator 取最新


# ---- STOCK_FINANCIAL_METRICS: 财报 17 指标映射(跨股稳定,2026-07-22 探针) ----
def test_financial_metrics_cover_c1_needs():
    """C1 要的底层绝对值 + 关键比率都在常用指标 17 项里(指标名跨股稳定,实测茅台=招行)。"""
    keys = set(STOCK_FINANCIAL_METRICS)
    assert len(keys) == 17
    for must in ["revenue", "net_profit", "np_deducted", "eps", "bvps", "ocf",
                 "roe", "gross_margin"]:
        assert must in keys, f"C1 缺关键指标 {must}"
    assert STOCK_FINANCIAL_METRICS["revenue"] == "营业总收入"
    assert STOCK_FINANCIAL_METRICS["net_profit"] == "归母净利润"
    assert STOCK_FINANCIAL_METRICS["np_deducted"] == "扣非净利润"


# ---- stock_financials store(长表 → 序列/面板往返) ----
def test_stock_financials_roundtrip_and_series():
    st = _store()
    long = pd.DataFrame([
        {"report_period": "20251231", "metric": "revenue", "value": 172054171890.91},
        {"report_period": "20251231", "metric": "net_profit", "value": 82320067101.68},
        {"report_period": "20241231", "metric": "revenue", "value": 170000000000.0},
    ])
    assert st.upsert_stock_financials("600519", long, source="sina") == 3
    rev = st.get_stock_financials_series("600519", "revenue")
    assert len(rev) == 2
    assert float(rev.iloc[-1]) == 172054171890.91  # 2025 年报
    assert st.last_stock_financials_period("600519") == "20251231"


def test_stock_financials_panel_pivot():
    """宽表面板:index=report_period, columns=metric(C1 多指标诊断便利读)。"""
    st = _store()
    long = pd.DataFrame([
        {"report_period": "20251231", "metric": "revenue", "value": 100.0},
        {"report_period": "20251231", "metric": "eps", "value": 65.66},
        {"report_period": "20241231", "metric": "revenue", "value": 90.0},
    ])
    st.upsert_stock_financials("600519", long)
    pnl = st.get_stock_financials_panel("600519", ["revenue", "eps"])
    assert list(pnl.index) == ["20241231", "20251231"]
    assert set(pnl.columns) == {"revenue", "eps"}
    assert float(pnl.loc["20251231", "eps"]) == 65.66
    assert pd.isna(pnl.loc["20241231", "eps"])  # 该期无 eps → NaN


def test_stock_financials_idempotent():
    st = _store()
    long = pd.DataFrame([{"report_period": "20251231", "metric": "eps", "value": 65.66}])
    st.upsert_stock_financials("600519", long)
    long2 = pd.DataFrame([{"report_period": "20251231", "metric": "eps", "value": 66.0}])
    st.upsert_stock_financials("600519", long2)  # 同期同指标 → 更新
    s = st.get_stock_financials_series("600519", "eps")
    assert len(s) == 1
    assert float(s.iloc[-1]) == 66.0


# ---- stock_dividend store ----
def test_stock_dividend_roundtrip():
    st = _store()
    dv = pd.DataFrame({
        "announce_date": ["2025-12-11", "2025-06-20"],
        "cash_per_share": [23.957, 27.673],
        "stock_div_10": [0, 0],
        "trans_10": [0, 0],
    }, index=["2025-12-19", "2025-06-26"])
    assert st.upsert_stock_dividend("600519", dv, source="sina") == 2
    got = st.get_stock_dividend_series("600519")
    assert len(got) == 2
    assert float(got["cash_per_share"].iloc[-1]) == 23.957  # by ex_date 升序,最新=12-19
    assert st.last_stock_dividend_date("600519") == "2025-12-19"


def test_stock_dividend_empty_ok():
    """部分股票无分红记录 → 空 df,upsert 不报错、返回 0。"""
    st = _store()
    assert st.upsert_stock_dividend("300750", pd.DataFrame(
        columns=["announce_date", "cash_per_share", "stock_div_10", "trans_10"])) == 0
    assert st.last_stock_dividend_date("300750") is None
