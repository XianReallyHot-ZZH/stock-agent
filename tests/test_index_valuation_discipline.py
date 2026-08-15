"""Tests for 指数择时 ③ 估值纪律层(框架第二层:中位线高抛低吸)。

覆盖 dashboard.py 新增/改动:
- _discipline_label: zone → (纪律标签, 配色) 确定性映射
- _position_bar: 0-100% 估值位置条(刻度/三段着色/当前标记/纪律标签)
- _valuation_figure: 分位线统一近 10 年口径 + 50% 中位线 + 当前点分位取自 val
- _valuation_price_figure: 沪深300 收盘 + 顶/底/中位趋势线

No network — 合成 Series/DataFrame 喂入,断言映射/HTML/figure trace。
"""
import re

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stockagent.tracker import dashboard as d


# ---------- A1 纪律标签映射 ----------
def test_discipline_label_mapping():
    assert d._discipline_label("低位·可激进") == ("只买不卖", d._PAL["good"])
    assert d._discipline_label("高位·宜保守") == ("只卖不买", d._PAL["critical"])
    assert d._discipline_label("结构分化·宜观望") == ("中性·观望", d._PAL["warning"])
    assert d._discipline_label("中位·中性") == ("中性", d._PAL["ink_sec"])


def test_discipline_label_fallback():
    assert d._discipline_label("—") == ("—", d._PAL["muted"])
    assert d._discipline_label("") == ("—", d._PAL["muted"])
    assert d._discipline_label("乱七八糟") == ("—", d._PAL["muted"])


# ---------- A2 估值位置条 ----------
def test_position_bar_extents():
    assert "◀只买" in d._position_bar(0.0, "只买不卖")
    assert "只卖▶" in d._position_bar(0.0, "只买不卖")
    assert "只买不卖" in d._position_bar(0.0, "只买不卖")
    assert "left:0%" in d._position_bar(0.0, "只买不卖")
    assert "left:50%" in d._position_bar(0.5, "中性")
    assert "left:100%" in d._position_bar(1.0, "只卖不买")


def test_position_bar_nan():
    html = d._position_bar(float("nan"), "")
    assert "—" in html                      # NaN → 显示占位
    assert "left:0%" in html                # NaN 截断到 0,不抛


# ---------- A3 _valuation_figure: 10 年口径 + 50% 中位线 ----------
def _pe_df_split_history():
    """老段(>10年)高估值 + 近 10 年低估值 → 10年口径与全历史口径显著不同。"""
    dates = pd.date_range("2025-12-31", periods=6000, freq="B")   # ~24 年
    old = pd.Series(np.linspace(50.0, 55.0, 3000), index=dates[:3000])
    recent = pd.Series(np.linspace(10.0, 15.0, 3000), index=dates[3000:])
    pe = pd.concat([old, recent])
    df = pd.DataFrame({"pe_ttm": pe.values}, index=pe.index)
    df.index.name = "date"
    return df, pe


def test_valuation_figure_10y_midline_not_full_history():
    pe_df, pe = _pe_df_split_history()
    s10 = pe.iloc[-252 * 10:]
    mid10 = float(s10.quantile(0.50))
    mid_full = float(pe.quantile(0.50))
    assert mid10 != mid_full                 # 前提: 两口径确有差异(否则测不出对齐)

    fig = d._valuation_figure({"pe_pct": 0.33, "valid": True}, pe_df, pd.DataFrame())
    assert isinstance(fig, go.Figure)

    # 50% 中位 注解存在,且其值 == 近 10 年口径(非全历史)
    ann_txts = [a.text for a in fig.layout.annotations if a.text]
    mid_ann = [t for t in ann_txts if "50% 中位" in t]
    assert mid_ann, "缺 50% 中位线注解"
    val_in_ann = float(re.search(r"中位 ([\d.]+)", mid_ann[0]).group(1))
    assert abs(val_in_ann - mid10) < 0.05    # 1 位小数格式化误差
    assert abs(val_in_ann - mid_full) > 0.5  # 明显区别于全历史


def test_valuation_figure_current_pct_uses_val():
    pe_df, _ = _pe_df_split_history()
    fig = d._valuation_figure({"pe_pct": 0.33, "valid": True}, pe_df, pd.DataFrame())
    txts = []
    for tr in fig.data:
        t = tr.text
        if isinstance(t, (list, tuple)):
            txts.extend(str(x) for x in t)
        elif t:
            txts.append(str(t))
    # 当前点文字应含 val['pe_pct']=0.33 → 33%(而非全历史重算值)
    assert any("33%" in t for t in txts), f"当前点未用 val 的 10 年分位: {txts}"


# ---------- A4 _valuation_price_figure: 收盘 + 顶/底/中位趋势线 ----------
def _daily_df(n=252 * 8):
    idx = pd.date_range("2025-12-31", periods=n, freq="B")
    t = np.arange(n, dtype=float)
    close = pd.Series(3000.0 + t * 0.5, index=idx)              # 缓慢上升
    for b in range(200, n, 400):                                # 每~400bar 一个尖锐V型低点(仿熊市底,可被 swing 检测)
        close.iloc[b] -= 600.0
    return pd.DataFrame({"close": close.to_numpy()}, index=idx)


def test_valuation_price_figure_traces():
    df = _daily_df()
    fig = d._valuation_price_figure(df, years=5)
    assert isinstance(fig, go.Figure)
    names = [tr.name or "" for tr in fig.data]
    assert any("收盘" in n for n in names)
    assert any("上沿" in n for n in names)      # 阻力(直线)
    assert any("下沿" in n for n in names)      # 支撑(直线)
    assert any("中位" in n for n in names)      # (顶+底)/2
    # 直线趋势: 至少覆盖一年,且不出现整段 OLS 左端外推到远低于数据(宽松阈值,防 667<818 复发)。
    close = pd.to_numeric(df["close"]).dropna()
    lower_tr = next(t for t in fig.data if t.name and "下沿" in t.name)
    assert len(lower_tr.x) >= 252
    assert min(lower_tr.y) >= close.min() - 500


def test_valuation_price_figure_fit_start_param():
    # fit_start 控制拟合起点(默认 2009);改 2015 → 名称含「自2015」
    fig = d._valuation_price_figure(_daily_df(), years=5, fit_start="2015-01-01")
    names = [tr.name or "" for tr in fig.data]
    assert any("自2015" in n for n in names)


def test_valuation_price_figure_channel_percentile_traces():
    df = _daily_df()
    fig = d._valuation_price_figure(df, years=5)
    names = [tr.name or "" for tr in fig.data]
    assert any("通道20%分位" in n for n in names)        # 近支撑
    assert any("通道80%分位" in n for n in names)        # 近阻力

    def last_y(kw):
        tr = next(t for t in fig.data if t.name and kw in t.name)
        return float(tr.y[-1])

    # 通道内顺序: 下沿 < 20% < 中位 < 80% < 上沿(末点)
    assert last_y("下沿") < last_y("通道20%") < last_y("中位") < last_y("通道80%") < last_y("上沿")



# ---------- _pivot_line: 过 pivot 点(bar 位置空间 OLS)的直线 ----------
def test_pivot_line_through_collinear():
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)   # 完全线性
    mask = pd.Series(False, index=idx)
    for b in (50, 150, 250):
        mask.iloc[b] = True
    line = d._pivot_line(close, mask, "2020-01-01")
    assert len(line) == n
    for b in (50, 150, 250):
        assert abs(line.iloc[b] - close.iloc[b]) < 1e-6         # 直线穿过这些 pivot
    assert line.iloc[0] < line.iloc[-1]                         # 上升


def test_pivot_line_too_few_pivots():
    idx = pd.date_range("2020-01-01", periods=50, freq="B").strftime("%Y-%m-%d")
    close = pd.Series(np.arange(50, dtype=float), index=idx)
    mask = pd.Series(False, index=idx)
    mask.iloc[10] = True                                        # 仅 1 个 pivot
    assert len(d._pivot_line(close, mask, "2020-01-01")) == 0


def test_pivot_line_shift_sigma_lowers():
    n = 300
    idx = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
    close = pd.Series(100.0 + np.arange(n) + np.sin(np.arange(n) / 5) * 20, index=idx)
    mask = pd.Series(False, index=idx)
    for b in (50, 150, 250):
        mask.iloc[b] = True
    base = d._pivot_line(close, mask, "2020-01-01", shift_sigma=0.0)
    shifted = d._pivot_line(close, mask, "2020-01-01", shift_sigma=1.0)
    assert len(base) == len(shifted)
    assert (shifted < base).all()                              # 下移后全程更低
    assert (base - shifted).mean() > 0                         # 有实际位移(低点残差σ>0)




def test_valuation_price_figure_years_param():
    fig = d._valuation_price_figure(_daily_df(), years=3)
    names = [tr.name or "" for tr in fig.data]
    assert any("3年" in n for n in names)       # years 参数生效


def test_valuation_price_figure_empty():
    fig = d._valuation_price_figure(pd.DataFrame({"close": []}), years=5)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0                   # 不足不抛


# ---------- ⑩ 关键位监测 ----------
def _kl_snap():
    lv = {"kind": "low", "level": 3766.0, "touch": "2026-07-17", "confirm": "2026-08-05",
          "outcome": "hold", "state": "已守住", "dist": 0.0428, "in_zone": False,
          "pending": False, "break_date": None, "platform_start": "", "platform_end": "",
          "breakout": "", "vol_ratio": float("nan"), "high_vol_break": False}
    return {"date": "2026-08-14", "close": 3927.0, "n_events": 59,
            "levels": [lv], "next_below": lv, "next_above": None}


def test_key_levels_html_renders():
    html = d._key_levels_html(_kl_snap(), "")
    assert "下方第一支撑" in html and "3766" in html            # 第一档 tile
    assert "上方第一压力" in html and "—" in html                # 无压力 → 占位
    assert "已守住" in html and "回测" not in html               # 状态表
    assert "三幕剧本" in html and "永不喂交易引擎" in html        # 剧本提示 + 只读声明


def test_key_levels_html_insufficient():
    assert "数据不足" in d._key_levels_html(None)
    assert "数据不足" in d._key_levels_html({"levels": []})


def test_key_levels_figure_traces():
    idx = pd.date_range("2025-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    close = pd.Series(np.linspace(3400.0, 3900.0, 300), index=idx)
    fig = d._key_levels_figure(close, _kl_snap()["levels"])
    assert isinstance(fig, go.Figure)
    names = [t.name or "" for t in fig.data]
    assert any("上证综指" in n for n in names)                   # 价格线
    assert any("前低 3766" in n for n in names)                  # 位横线(trace 化,可点图例)
