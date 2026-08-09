"""宏观框架看板单测: asset_analysis 三家族 + 渲染冒烟。风格镜像 test_western_score.py。"""
import pandas as pd

from stockagent.data.store import Store
from stockagent.western_macro.framework import asset_analysis, render_macro_framework


def _dates(n, start="2024-01-01"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def _seed(store, closes, source, symbol, start="2024-01-01"):
    dates = _dates(len(closes), start)
    df = pd.DataFrame([{"source": source, "symbol": symbol, "date": d, "close": float(c)}
                       for d, c in zip(dates, closes)])
    store.upsert_western_macro(df, source_tag="seed")


# ---- price 家族 (5阶段树) ----
def test_price_family_bottoming(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [200 - 0.5 * i for i in range(200)], "fut", "GC")   # 单边下跌 → 末段最超卖
    an = asset_analysis("黄金", st)
    assert an["valid"] and an["family"] == "price"
    assert an["label"] == "筑底/底部震荡"
    assert 0.0 <= an["dev_pct"] <= 1.0
    assert an["ret_20"] is not None and an["ret_60"] is not None   # 200 bars → ret_250 合理为 None


def test_price_family_trend_up(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [100 + 0.3 * i for i in range(200)], "usidx", ".INX")  # 稳步上行
    an = asset_analysis("标普500", st)
    assert an["valid"] and an["above_ma"] is True


# ---- yield 家族 (水平分位 + 趋势) ----
def test_yield_family_high(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [4.0 + 0.01 * i for i in range(200)], "ust", "US2Y")  # 上行到区间顶
    an = asset_analysis("美债2Y", st)
    assert an["valid"] and an["family"] == "yield"
    assert "高位" in an["label"] and "上行" in an["label"]


def test_yield_family_low(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [5.0 - 0.01 * i for i in range(200)], "ust", "US10Y")  # 下行到区间底
    an = asset_analysis("美债10Y", st)
    assert an["valid"] and "低位" in an["label"]


# ---- curve 家族 (倒挂/正常 + 陡峭/趋平) ----
def test_curve_family_inverted(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [-0.3 - 0.001 * i for i in range(200)], "ust", "US2S10S")  # 负且下行
    an = asset_analysis("2s10s", st)
    assert an["valid"] and an["family"] == "curve"
    assert "倒挂" in an["label"]


def test_curve_family_normal_steepening(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [0.1 + 0.001 * i for i in range(200)], "ust", "US2S10S")  # 正且上行
    an = asset_analysis("2s10s", st)
    assert an["valid"]
    assert "正常" in an["label"] and "陡峭化" in an["label"]


# ---- 无数据 ----
def test_no_series_invalid(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    an = asset_analysis("半导体", st)   # manual-only, 无 series
    assert an["valid"] is False


def test_insufficient_bars(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [100 + i for i in range(50)], "fut", "GC")   # < MIN_BARS(80)
    an = asset_analysis("黄金", st)
    assert an["valid"] is False


# ---- 渲染冒烟 ----
def test_render_smoke(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    _seed(st, [200 - 0.5 * i for i in range(200)], "fut", "GC")             # 黄金
    _seed(st, [4.0 + 0.01 * i for i in range(200)], "ust", "US2Y")          # 2Y
    _seed(st, [0.1 + 0.001 * i for i in range(200)], "ust", "US2S10S")      # 2s10s
    _seed(st, [100 - 0.1 * i for i in range(200)], "dxy", "DXY")            # DXY
    out = tmp_path / "macro_framework.html"
    p = render_macro_framework(st, out, asof="2026-08-09")
    assert p == out and out.exists()
    html = out.read_text(encoding="utf-8")
    assert "宏观框架看板" in html
    assert "总览" in html
    assert "因果框架" in html
    assert "筑底/底部震荡" in html       # 黄金阶段标签进了总览
    assert "陡峭化" in html             # 2s10s 标签
