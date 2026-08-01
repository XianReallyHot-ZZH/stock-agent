"""个股时序图 figure builders — Phase 2 增量(给 stock_diagnose.html 卡片快照补时序)。

4 张图,把 diagnose_stock_full 压成标量的背后序列重新展开:
  - price_deviation_figure  价格+MA60+偏离极值(镜像 tracker/dashboard._deviation_figure,复用 ti 原语)
  - valuation_figure        PE/PB 历史+便宜/贵区间+当前位(参照 research/report.pe_figure)
  - earnings_figure         营收/净利年报柱(亿)+ 净利同比线(年报累计口径,单季留 S07)
  - dividend_figure         每股现金分红(按除权日)

均为纯函数,返回 plotly go.Figure;空/不足数据走占位(不抛),浅色态布局色,深色态由
stock_report 前端 applyPlotly 重涂。复用底层 tracker.indicators(ma_series/deviation_series),
不跨模块 import dashboard 的私有名。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import indicators as ti

# ---- palette (light mode; 深色由前端 applyPlotly 重涂) ----
_PAL = {
    "surface": "#ffffff", "ink": "#0f172a", "ink_sec": "#52514e", "muted": "#898781",
    "grid": "#e2e8f0", "baseline": "#cbd5e1",
    "close": "#2a78d6", "rev": "#2563eb", "profit": "#008300", "yoy": "#ea580c", "rev_yoy": "#c026d3",
    "val": "#7c3aed", "val_now": "#dc2626",
    "pos_extreme": "#d03b3b", "neg_extreme": "#1c5cab", "div": "#16a34a",
}

_METRIC_LABEL = {"pe_ttm": "PE(TTM)", "pb": "PB"}


def _layout(title: str, height: int = 320, showlegend: bool = True, **extra) -> dict:
    d = dict(
        title=dict(text=title, font=dict(size=13)),
        height=height, showlegend=showlegend, hovermode="x unified",
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui,-apple-system,'Segoe UI',sans-serif"),
        margin=dict(l=52, r=20, t=46, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
    )
    d.update(extra)
    return d


def _placeholder(name: str, label: str, reason: str, height: int = 220) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**_layout(f"{name} — {label}（{reason}）", height=height, showlegend=False))
    fig.add_annotation(text=reason, xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False, font=dict(color=_PAL["muted"], size=13))
    return fig


def _style_axes(fig: go.Figure) -> None:
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"])
    fig.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["baseline"])


# ---- ① 价格 + MA60 + 偏离极值 ----
def price_deviation_figure(sym: str, name: str, price_df: pd.DataFrame,
                           period: int = ti.MA_PERIOD) -> go.Figure:
    """双行子图(0.6/0.4):上=收盘价+MA{period},下=偏离度+历史极值线+当前点。带范围按钮。"""
    if price_df is None or len(price_df) == 0 or "close" not in price_df.columns:
        return _placeholder(name, "价格+偏离", "无日线数据")
    close = price_df["close"].astype(float)
    if len(close) < period:
        return _placeholder(name, "价格+偏离", f"日线不足 {period} 根")

    ma = ti.ma_series(close, period)
    dev = ti.deviation_series(close, period)
    idx = pd.to_datetime(close.index)
    dc = dev.dropna()
    mx = float(dc.max()) if len(dc) else float("nan")
    mn = float(dc.min()) if len(dc) else float("nan")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.12,
        subplot_titles=(f"{name}({sym}) 收盘价 vs MA{period}", "偏离度 (价格−均线)÷均线"))
    fig.add_trace(go.Scatter(x=idx, y=close, name="收盘",
                             line=dict(color=_PAL["close"], width=2),
                             hovertemplate="%{x|%Y-%m-%d}<br>收盘 %{y:.2f}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma, name=f"MA{period}",
                             line=dict(color=_PAL["muted"], width=1.4, dash="dash"),
                             hovertemplate=f"%{{x|%Y-%m-%d}}<br>MA{period} %{{y:.2f}}<extra></extra>"),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=dev, name="偏离度",
                             line=dict(color=_PAL["ink_sec"], width=1.4),
                             hovertemplate="%{x|%Y-%m-%d}<br>偏离 %{y:.1%}<extra></extra>"),
                  row=2, col=1)
    if not pd.isna(mx):
        fig.add_hline(y=mx, row=2, col=1, line=dict(color=_PAL["pos_extreme"], width=1, dash="dot"),
                      annotation_text=f"正极值 +{mx:.0%}", annotation_position="top right")
    if not pd.isna(mn):
        fig.add_hline(y=mn, row=2, col=1, line=dict(color=_PAL["neg_extreme"], width=1, dash="dot"),
                      annotation_text=f"负极值 {mn:.0%}", annotation_position="bottom right")
    if not pd.isna(dev.iloc[-1]):
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[dev.iloc[-1]], mode="markers+text",
                                 marker=dict(size=10, color=_PAL["ink"]),
                                 text=[f"现在 {dev.iloc[-1]:.1%}"], textposition="top center",
                                 showlegend=False), row=2, col=1)
    fig.update_layout(**_layout(f"{name}({sym}) — 价格 vs MA{period} + 偏离极值", height=480))
    _style_axes(fig)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d")
    fig.update_xaxes(
        rangeslider_visible=True, row=2, col=1,
        rangeselector=dict(buttons=[
            dict(count=1, label="1月", step="month", stepmode="backward"),
            dict(count=6, label="6月", step="month", stepmode="backward"),
            dict(count=1, label="1年", step="year", stepmode="backward"),
            dict(count=3, label="3年", step="year", stepmode="backward"),
            dict(label="全部", step="all"),
        ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


# ---- ② 估值历史 + 分位带 ----
def valuation_figure(sym: str, name: str, val_df: pd.DataFrame,
                     metric: str = "pe_ttm") -> go.Figure:
    """PE/PB 历史折线 + min~max 便宜/贵区间 + 当前位虚线。标题带当前历史分位。"""
    label = _METRIC_LABEL.get(metric, metric)
    if val_df is None or len(val_df) == 0 or "value" not in val_df.columns:
        return _placeholder(name, f"{label}历史", "无估值数据")
    s = pd.to_numeric(val_df["value"], errors="coerce").dropna()
    if len(s) < 2:
        return _placeholder(name, f"{label}历史", "估值点不足")

    last = float(s.iloc[-1])
    pct = float((s < last).sum()) / len(s)
    lo, hi = float(s.min()), float(s.max())
    idx = pd.to_datetime(s.index)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=idx, y=s, name=label, line=dict(color=_PAL["val"], width=1.6),
        hovertemplate=f"%{{x|%Y-%m-%d}}<br>{label} %{{y:.2f}}<extra></extra>"))
    fig.add_hrect(y0=lo, y1=hi, line_width=0, fillcolor=_PAL["val"], opacity=0.07,
                  annotation_text=f"历史 {lo:.2f}~{hi:.2f}", annotation_position="top left")
    fig.add_hline(y=last, line=dict(color=_PAL["val_now"], width=1.5, dash="dash"),
                  annotation_text=f"当前 {last:.2f}（{pct:.0%}分位）", annotation_position="top right")
    fig.update_layout(**_layout(
        f"{name}({sym}) — {label}历史 + 当前位（{pct:.0%}分位）", height=320))
    _style_axes(fig)
    fig.update_yaxes(title_text=label)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d")
    return fig


# ---- ③ 业绩年报柱 + 同比线 ----
def earnings_figure(sym: str, name: str, fin_panel: pd.DataFrame) -> go.Figure:
    """营收/净利年报柱(亿元)+ 营收&净利同比线(次轴%)。年报口径(累计可跨年比);单季留 S07。"""
    if fin_panel is None or len(fin_panel) == 0:
        return _placeholder(name, "业绩(年报)", "无财报数据")
    annual_mask = pd.Index([str(i) for i in fin_panel.index]).str.endswith("1231")
    panel = fin_panel.loc[annual_mask].sort_index()
    if len(panel) < 2:
        return _placeholder(name, "业绩(年报)", "年报不足 2 期")

    years = [str(p)[:4] for p in panel.index]
    rev = pd.to_numeric(panel["revenue"], errors="coerce").values / 1e8 \
        if "revenue" in panel.columns else None
    np_ = pd.to_numeric(panel["net_profit"], errors="coerce").values / 1e8 \
        if "net_profit" in panel.columns else None
    if rev is None and np_ is None:
        return _placeholder(name, "业绩(年报)", "无营收/净利指标")

    # 同比(前值>0 才有意义,负基期置空)
    rev_yoy = np_yoy = None
    if rev is not None:
        rev_s = pd.Series(rev, index=years)
        rev_yoy = rev_s.pct_change().where(rev_s.shift() > 0)
    if np_ is not None:
        np_s = pd.Series(np_, index=years)
        np_yoy = np_s.pct_change().where(np_s.shift() > 0)

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if rev is not None:
        fig.add_trace(go.Bar(x=years, y=rev, name="营收(亿)", marker_color=_PAL["rev"],
                             hovertemplate="%{x}<br>营收 %{y:.0f}亿<extra></extra>"),
                      secondary_y=False)
    if np_ is not None:
        fig.add_trace(go.Bar(x=years, y=np_, name="净利(亿)", marker_color=_PAL["profit"],
                             hovertemplate="%{x}<br>净利 %{y:.0f}亿<extra></extra>"),
                      secondary_y=False)
    if rev_yoy is not None:
        fig.add_trace(go.Scatter(x=years, y=rev_yoy, name="营收同比", mode="lines+markers",
                                 line=dict(color=_PAL["rev_yoy"], width=1.6),
                                 hovertemplate="%{x}<br>营收同比 %{y:.1%}<extra></extra>"),
                      secondary_y=True)
    if np_yoy is not None:
        fig.add_trace(go.Scatter(x=years, y=np_yoy, name="净利同比", mode="lines+markers",
                                 line=dict(color=_PAL["yoy"], width=1.6),
                                 hovertemplate="%{x}<br>净利同比 %{y:.1%}<extra></extra>"),
                      secondary_y=True)
    fig.update_layout(**_layout(f"{name}({sym}) — 营收/净利(年报·亿) + 同比", height=360,
                                barmode="group"))
    _style_axes(fig)
    fig.update_yaxes(title_text="亿元", secondary_y=False)
    fig.update_yaxes(title_text="同比", secondary_y=True, hoverformat=".0%",
                     gridcolor=_PAL["grid"])
    return fig


# ---- ④ 分红时序 ----
def dividend_figure(sym: str, name: str, div_df: pd.DataFrame) -> go.Figure:
    """每股现金分红(cash_per_share)按除权日柱。无分红(如科创板)走占位。"""
    if div_df is None or len(div_df) == 0 or "cash_per_share" not in div_df.columns:
        return _placeholder(name, "每股现金分红", "无分红历史")
    cps = pd.to_numeric(div_df["cash_per_share"], errors="coerce").dropna()
    if len(cps) == 0:
        return _placeholder(name, "每股现金分红", "无现金分红记录")
    idx = pd.to_datetime(cps.index)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=idx, y=cps.values, name="每股派息(元)", marker_color=_PAL["div"],
        text=[f"{v:.2f}" for v in cps.values], textposition="outside",
        hovertemplate="%{x|%Y-%m-%d}<br>每股派息 %{y:.3f}元<extra></extra>"))
    fig.update_layout(**_layout(f"{name}({sym}) — 每股现金分红(按除权日)", height=260, showlegend=False))
    _style_axes(fig)
    fig.update_yaxes(title_text="元/股")
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d", dtick="M12",
                     tickformat="%Y")
    return fig


# ---- ⑤ S07 利润归因(多年三段堆叠柱) ----
def attribution_figure(sym: str, name: str, rows: list[dict]) -> go.Figure:
    """S07 利润归因:每年一根 业绩/估值/分红 堆叠柱(占年初价%,可加,barmode=relative 支持负值)。
    rows = attribution_by_year 输出。看回报是业绩驱动(可持续)还是估值驱动(周期/脆弱)。
    用 *_return 稳健成分(share_* 在 total≤0 时为 NaN);不给总回报线(深色下深色线不可见)。"""
    if not rows:
        return _placeholder(name, "利润归因", "无有效年度区间")
    years = [r["year"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=years, y=[r["earnings"] for r in rows], name="业绩",
                         marker_color="#16a34a",
                         hovertemplate="%{x} 业绩 %{y:+.1%}<extra></extra>"))
    fig.add_trace(go.Bar(x=years, y=[r["valuation"] for r in rows], name="估值",
                         marker_color="#f59e0b",
                         hovertemplate="%{x} 估值 %{y:+.1%}<extra></extra>"))
    fig.add_trace(go.Bar(x=years, y=[r["dividend"] for r in rows], name="分红",
                         marker_color="#2563eb",
                         hovertemplate="%{x} 分红 %{y:+.1%}<extra></extra>"))
    fig.update_layout(**_layout(
        f"{name}({sym}) — 利润归因(业绩/估值/分红,占年初价)", height=340, barmode="relative"))
    _style_axes(fig)
    fig.update_yaxes(title_text="回报(占年初价)", hoverformat=".0%", gridcolor=_PAL["grid"])
    fig.add_hline(y=0, line=dict(color=_PAL["baseline"], width=1))
    return fig


# ---- ⑦ 商品价(A 类领先信号;周期股上游,领先财报 1-4 月)----
def commodity_price_figure(variety: str, series: pd.Series) -> go.Figure:
    """商品价时序图(周期股业绩的因果领先指标)。单线 + "1 年前"参考虚线(让同比可视化),
    标题带判定(向上🟢/背离🟡/震荡⚪/向下🔴)。空/不足 → 占位。"""
    if series is None or len(series) < 2:
        return _placeholder(variety, "商品价", "无商品价数据")
    s = series.astype(float)
    idx = pd.to_datetime(s.index)
    n252 = min(252, len(s) - 1)
    n60 = min(60, len(s) - 1)
    yoy = float(s.iloc[-1]) / float(s.iloc[-1 - n252]) - 1.0 if n252 >= 1 else float("nan")
    rec = float(s.iloc[-1]) / float(s.iloc[-1 - n60]) - 1.0 if n60 >= 1 else float("nan")
    if not pd.isna(yoy) and yoy > 0.10 and not pd.isna(rec) and rec > -0.05:
        judge = "向上🟢"
    elif not pd.isna(yoy) and yoy > 0.10:
        judge = "背离🟡"
    elif not pd.isna(yoy) and yoy > -0.10:
        judge = "震荡⚪"
    else:
        judge = "向下🔴"
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=s, name=variety, line=dict(color=_PAL["close"], width=2),
                             hovertemplate="%{x|%Y-%m-%d}<br>" + variety + " %{y:.0f}<extra></extra>"))
    ma60 = ti.ma_series(s, 60)   # 与个股 E3/企稳同口径(MA60),读图直觉一致
    fig.add_trace(go.Scatter(x=idx, y=ma60, name="MA60",
                             line=dict(color=_PAL["muted"], width=1.4, dash="dash"),
                             hovertemplate="%{x|%Y-%m-%d}<br>MA60 %{y:.0f}<extra></extra>"))
    if n252 >= 1 and not pd.isna(s.iloc[-1 - n252]):
        base = float(s.iloc[-1 - n252])
        fig.add_hline(y=base, line=dict(color=_PAL["muted"], width=1, dash="dot"),
                      annotation_text=f"1年前 {base:.0f}", annotation_position="top left")
    _yy = f"{yoy:+.0%}" if not pd.isna(yoy) else "—"
    _rr = f"{rec:+.0%}" if not pd.isna(rec) else "—"
    fig.update_layout(**_layout(f"{variety}价 · {judge}(同比{_yy}/近60日{_rr})", height=300, showlegend=True))
    _style_axes(fig)
    return fig
