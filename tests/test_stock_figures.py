"""Tests for stock_figures — 4 个时序图 builder(Phase 2 增量)。

No network — 合成 DataFrame/Series 喂入,断言返回 go.Figure、trace 数/类型,空/不足走占位不抛。
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stockagent.tracker import stock_figures as sf


# ---------- 价格 + 偏离 ----------
def _price_df(n=120):
    idx = pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    close = pd.Series(np.arange(n, dtype=float) + 100.0, index=idx)
    return pd.DataFrame({"close": close})


def test_price_deviation_valid():
    fig = sf.price_deviation_figure("600519", "茅台", _price_df(), period=60)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 3                         # 收盘 / MA / 偏离(+ 现在 marker)
    assert all(isinstance(t, go.Scatter) for t in fig.data)


def test_price_deviation_empty():
    fig = sf.price_deviation_figure("600519", "茅台", pd.DataFrame(), period=60)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0                         # 占位,不抛


def test_price_deviation_short():
    fig = sf.price_deviation_figure("600519", "茅台", _price_df(n=30), period=60)
    assert len(fig.data) == 0                         # 不足 60 根 → 占位


def test_price_deviation_none():
    fig = sf.price_deviation_figure("600519", "茅台", None, period=60)
    assert len(fig.data) == 0


# ---------- 估值分位带 ----------
def _val_df():
    idx = ["2024-01-15", "2024-06-15", "2025-01-15", "2025-06-15", "2026-01-15"]
    return pd.DataFrame({"value": [30.0, 25.0, 20.0, 18.0, 19.8]}, index=idx)


def test_valuation_valid_pe():
    fig = sf.valuation_figure("600519", "茅台", _val_df(), metric="pe_ttm")
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert len(fig.layout.shapes) >= 2                # hrect(便宜/贵) + hline(当前)
    assert "PE(TTM)" in fig.layout.title.text


def test_valuation_pb_label():
    fig = sf.valuation_figure("600036", "招行", _val_df(), metric="pb")
    assert "PB" in fig.layout.title.text


def test_valuation_empty():
    fig = sf.valuation_figure("600519", "茅台", pd.DataFrame(), metric="pe_ttm")
    assert len(fig.data) == 0


def test_valuation_single_point():
    fig = sf.valuation_figure("600519", "茅台",
                              pd.DataFrame({"value": [19.8]}, index=["2026-01-15"]),
                              metric="pe_ttm")
    assert len(fig.data) == 0                         # 不足 2 点 → 占位


# ---------- 业绩年报柱 + 同比 ----------
def _fin_panel():
    # 4 年报(1231) + 1 季报(0630),验证 annual 过滤
    idx = ["20201231", "20211231", "20220630", "20221231", "20231231"]
    return pd.DataFrame({
        "revenue": [1.0e10, 1.1e10, 6.0e9, 1.2e10, 1.3e10],
        "net_profit": [5.0e9, 5.5e9, 3.0e9, 6.0e9, 6.5e9],
    }, index=idx)


def test_earnings_valid():
    fig = sf.earnings_figure("600519", "茅台", _fin_panel())
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 4                         # 营收 bar / 净利 bar / 营收同比 / 净利同比
    assert isinstance(fig.data[0], go.Bar) and isinstance(fig.data[1], go.Bar)
    assert isinstance(fig.data[2], go.Scatter) and isinstance(fig.data[3], go.Scatter)
    names = [t.name for t in fig.data]
    assert "营收同比" in names and "净利同比" in names


def test_earnings_empty():
    fig = sf.earnings_figure("600519", "茅台", pd.DataFrame())
    assert len(fig.data) == 0


def test_earnings_one_annual():
    panel = pd.DataFrame({"revenue": [1.0e10], "net_profit": [5.0e9]}, index=["20231231"])
    fig = sf.earnings_figure("600519", "茅台", panel)
    assert len(fig.data) == 0                         # 年报不足 2 期 → 占位


# ---------- 分红 ----------
def _div_df():
    idx = ["2022-06-30", "2023-06-30", "2024-06-28", "2025-06-30"]
    return pd.DataFrame({"cash_per_share": [21.0, 22.0, 23.0, 24.0]}, index=idx)


def test_dividend_valid():
    fig = sf.dividend_figure("600519", "茅台", _div_df())
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1
    assert isinstance(fig.data[0], go.Bar)


def test_dividend_empty():
    fig = sf.dividend_figure("688981", "中芯", pd.DataFrame())
    assert len(fig.data) == 0                         # 科创板无分红 → 占位,不抛


# ---------- S07 利润归因(多年三段堆叠柱) ----------
def _attr_rows():
    return [
        {"year": "2021", "earnings": 0.333, "valuation": -0.133, "dividend": 0.03, "total": 0.23},
        {"year": "2022", "earnings": 0.20, "valuation": -0.25, "dividend": 0.027, "total": -0.023},
        {"year": "2023", "earnings": 0.15, "valuation": 0.40, "dividend": 0.03, "total": 0.58},
    ]


def test_attribution_figure_valid():
    fig = sf.attribution_figure("600519", "茅台", _attr_rows())
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 3                         # 业绩 / 估值 / 分红
    assert all(isinstance(t, go.Bar) for t in fig.data)
    assert [t.name for t in fig.data] == ["业绩", "估值", "分红"]


def test_attribution_figure_empty():
    fig = sf.attribution_figure("600519", "茅台", [])
    assert len(fig.data) == 0                         # 无有效区间 → 占位
