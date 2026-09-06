"""大宗商品看板 figure builders(第八看板)。

品种时序图复用 tracker.stock_figures.commodity_price_figure(主语序列口径见 panel.primary_series);
本模块只造比价/总览两类新图。比价图刻意保持 [比价线, MA60] 两 trace 顺序——与品种图同构,
放大模态的前端偏离度派生(逐点相除)对比价同样可用(比价 vs 其均线的伸展度)。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ..tracker import indicators as ti

_PAL = {
    "surface": "#ffffff", "ink": "#0f172a", "muted": "#898781",
    "grid": "#e2e8f0", "baseline": "#cbd5e1",
    "line": "#2a78d6", "ma": "#898781", "idx1": "#2a78d6", "idx2": "#ea580c",
    "synth": "#7c3aed", "breadth": "#16a34a", "eqret": "#c026d3",
}

_RANGE_BUTTONS = dict(
    buttons=[
        dict(count=1, label="1月", step="month", stepmode="backward"),
        dict(count=6, label="6月", step="month", stepmode="backward"),
        dict(count=1, label="1年", step="year", stepmode="backward"),
        dict(count=3, label="3年", step="year", stepmode="backward"),
        dict(label="全部", step="all"),
    ],
    bgcolor=_PAL["surface"], activecolor=_PAL["grid"],
)


def _layout(title: str, height: int = 300, showlegend: bool = True, **extra) -> dict:
    d = dict(
        title=dict(text=title, font=dict(size=13)),
        height=height, showlegend=showlegend, hovermode="x unified",
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui,-apple-system,'Segoe UI',sans-serif"),
        margin=dict(l=56, r=20, t=46, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="right", x=1),
    )
    d.update(extra)
    return d


def _style_axes(fig: go.Figure) -> None:
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"])
    fig.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["baseline"])


def ratio_figure(name: str, s: pd.Series, pct: float | None = None) -> go.Figure:
    """比价时序(金银比/螺矿比…):单线 + MA60(伸展度参照)。无四态判定(比价无该语义)。
    traces 顺序 [比价, MA60] 与品种图同构 → 放大模态可直接复用。"""
    if s is None or len(s) < 2:
        fig = go.Figure()
        fig.update_layout(**_layout(f"{name} — 比价(数据不足)", height=220, showlegend=False))
        fig.add_annotation(text="数据不足", xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(color=_PAL["muted"], size=13))
        return fig
    s = s.astype(float)
    idx = pd.to_datetime(s.index)
    ma = ti.ma_series(s, 60)
    title = f"{name}" + (f" · 当前分位{pct:.0%}" if pct is not None and pd.notna(pct) else "")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=s, name=name, line=dict(color=_PAL["line"], width=2),
                             hovertemplate="%{x|%Y-%m-%d}<br>" + name + " %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=idx, y=ma, name="MA60",
                             line=dict(color=_PAL["ma"], width=1.4, dash="dash"),
                             hovertemplate="%{x|%Y-%m-%d}<br>MA60 %{y:.2f}<extra></extra>"))
    fig.update_layout(**_layout(title, height=300))
    _style_axes(fig)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d")
    return fig


def overview_figure(idx_map: dict, synth: pd.Series, official_ok: bool) -> go.Figure:
    """总览图:官方中证商品指数(两线)+ 自算等权合成(虚线,对照);官方缺失时合成顶上(标注)。"""
    fig = go.Figure()
    plotted = False
    for nm, s in idx_map.items():
        if s is None or len(s) < 2:
            continue
        s = s.astype(float)
        color = _PAL["idx1"] if "期货指数" in nm and "价格" not in nm else _PAL["idx2"]
        fig.add_trace(go.Scatter(x=pd.to_datetime(s.index), y=s, name=nm,
                                 line=dict(color=color, width=1.8),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + nm + " %{y:.0f}<extra></extra>"))
        plotted = True
    if synth is not None and len(synth) >= 2:
        sy = synth.astype(float)
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(sy.index), y=sy, name="自算等权合成(非官方,基数100)",
            line=dict(color=_PAL["synth"], width=1.4, dash="dot"),
            hovertemplate="%{x|%Y-%m-%d}<br>自算 %{y:.0f}<extra></extra>"))
        plotted = True
    if not plotted:
        fig.update_layout(**_layout("商品总览(无数据)", height=220, showlegend=False))
        fig.add_annotation(text="官方指数与自算合成都无数据", xref="paper", yref="paper",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(color=_PAL["muted"], size=13))
        return fig
    title = "商品总览 · 中证商品指数(官方)" + ("" if official_ok else " — 官方缺,自算顶上")
    fig.update_layout(**_layout(title, height=340))
    _style_axes(fig)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d")
    return fig


def breadth_figure(b20: pd.DataFrame, b60: pd.DataFrame) -> go.Figure:
    """广度图:上涨品种占比%(20日/60日,右轴语义统一 0-100)+ 等权涨幅%(左轴,细看幅度)。"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    plotted = False
    for b, lbl in ((b20, "20日"), (b60, "60日")):
        if b is None or not len(b):
            continue
        idx = pd.to_datetime(b.index)
        fig.add_trace(go.Scatter(x=idx, y=b["up_frac"] * 100.0, name=f"{lbl}上涨占比%",
                                 line=dict(color=_PAL["breadth"], width=1.8 if lbl == "20日" else 1.2,
                                           dash=None if lbl == "20日" else "dot"),
                                 hovertemplate=f"%{{x|%Y-%m-%d}}<br>{lbl}上涨 %{{y:.0f}}%<extra></extra>"),
                      secondary_y=False)
        fig.add_trace(go.Scatter(x=idx, y=b["eq_ret"] * 100.0, name=f"{lbl}等权涨幅%",
                                 line=dict(color=_PAL["eqret"], width=1.0, dash="dash"),
                                 hovertemplate=f"%{{x|%Y-%m-%d}}<br>{lbl}等权 %{{y:+.1f}}%<extra></extra>"),
                      secondary_y=True)
        plotted = True
    if not plotted:
        fig.update_layout(**_layout("品种广度(无数据)", height=220, showlegend=False))
        fig.add_annotation(text="品种数据不足", xref="paper", yref="paper", x=0.5, y=0.5,
                           showarrow=False, font=dict(color=_PAL["muted"], size=13))
        return fig
    fig.update_layout(**_layout("品种广度 · 上涨占比%(左,0-100) + 等权涨幅%(右,自算非官方)", height=300))
    _style_axes(fig)
    fig.update_yaxes(title_text="上涨占比%", range=[0, 100], secondary_y=False)
    fig.update_yaxes(title_text="等权涨幅%", zeroline=True, secondary_y=True, gridcolor=None)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d")
    return fig
