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
    "comm": "#d97706",
    "qline_year": "#94a3b8",   # 季度分界线·年份线(1月)略加重,深浅两态下均可见
}

_METRIC_LABEL = {"pe_ttm": "PE(TTM)", "pb": "PB"}

# 时序图 x 轴快捷窗口按钮(配合底部 rangeslider:按钮一键切档、滑块精细拖拽;价格偏离图/商品叠加图共用)
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
        rangeselector=_RANGE_BUTTONS)
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


def judge_commodity(yoy, recent) -> str:
    """商品四态判定(统一口径;2026-09 拆大宗商品看板时把三处历史实现收口于此:
    stock_report 面板 / 本模块商品图 / leading.commodity_alignment 健康度)。
    口径:同比>+10% 且 近60日>-5% → 向上;同比>+10% 但近期回落 → 背离;
    同比±10% 内 → 震荡;同比<-10% → 向下。NaN 同比 → 向下(与面板/图历史行为一致;
    leading 侧在入口已拦 NaN)。阈值改动只改这里,parity 测试锁行为。"""
    if yoy is None or pd.isna(yoy):
        return "向下"
    ok_recent = recent is not None and not pd.isna(recent)
    if yoy > 0.10 and ok_recent and recent > -0.05:
        return "向上"
    if yoy > 0.10:
        return "背离"
    if yoy > -0.10:
        return "震荡"
    return "向下"


_JUDGE_EMOJI = {"向上": "向上🟢", "背离": "背离🟡", "震荡": "震荡⚪", "向下": "向下🔴"}


def _commodity_judge(s: pd.Series) -> tuple[str, float, float]:
    """商品判定(向上🟢/背离🟡/震荡⚪/向下🔴) + 同比 + 近60日(judge_commodity 的序列包装)。"""
    n252 = min(252, len(s) - 1)
    n60 = min(60, len(s) - 1)
    yoy = float(s.iloc[-1]) / float(s.iloc[-1 - n252]) - 1.0 if n252 >= 1 else float("nan")
    rec = float(s.iloc[-1]) / float(s.iloc[-1 - n60]) - 1.0 if n60 >= 1 else float("nan")
    return _JUDGE_EMOJI[judge_commodity(yoy, rec)], yoy, rec


def commodity_radar(stats_rows: list, th: float = 0.95, mo_th: float = 0.10):
    """🚦 双段异动雷达纯分类器(2026-09 从 stock_report._commodity_extreme_banner 收口,两看板共用):
    in  = [(variety, commodity_dev_stats dict)];
    out = (fast, slow, n_ok),fast=(variety, cur偏离, tags, up) / slow=(variety, cur偏离, tags)。
    快腿(⚠异动提醒)=20日动量≥±mo_th 或 60日新高/新低;慢腿(⛔极端警戒)=偏离分位 ≥th 超买 / ≤1-th 超卖。
    同品种可双段同时出现(既在动又在伸展=两句话都成立);只分类不渲染,chips 归各看板。"""
    fast, slow, n_ok = [], [], 0
    for v, st in stats_rows:
        if not st or pd.isna(st.get("pct")):
            continue
        n_ok += 1
        m20 = st.get("momentum20")
        stags, ftags = [], []
        if st["pct"] >= th:
            stags.append(f"分位 {st['pct']:.0%} · 超买")
        elif st["pct"] <= 1.0 - th:
            stags.append(f"分位 {st['pct']:.0%} · 超卖")
        if not pd.isna(m20) and abs(float(m20)) >= mo_th:
            ftags.append(f"20日{float(m20):+.0%} · 动量")
        if st.get("new_high60"):
            ftags.append("60日新高")
        if st.get("new_low60"):
            ftags.append("60日新低")
        if ftags:
            up = bool(st.get("new_high60")) or (not pd.isna(m20) and float(m20) >= 0)
            fast.append((v, st["cur"], ftags, up))
        if stags:
            slow.append((v, st["cur"], stags))
    return fast, slow, n_ok


def fig_json_readable(fig) -> str:
    """fig → JSON 字符串,数值数组强制展开为普通 JSON 数组(2026-09 从 stock_report 收口,两看板共用)。

    plotly 的 to_json() 会把数值列打包成 base64 二进制块({dtype:'f8',bdata:...})——浏览器端
    Plotly 认得、能直接 newPlot,但 JS 读不到逐点数值,放大视图的偏离度派生(价格/MA60 逐点
    相除)会拿到全 undefined → 整行不渲染。日期列 to_json() 本就输出 ISO 字符串(正确),
    故只对 bdata 块解码(NaN→null),其余原样保留。"""
    import base64
    import json

    import numpy as np

    d = json.loads(fig.to_json())
    for tr in d.get("data") or []:
        for key in ("x", "y"):
            v = tr.get(key)
            if not (isinstance(v, dict) and "bdata" in v):
                continue
            try:
                buf = base64.b64decode(v["bdata"])
                arr = np.frombuffer(buf, dtype=np.dtype("<" + str(v.get("dtype", "f8"))))
                tr[key] = [None if isinstance(z, float) and np.isnan(z) else z
                           for z in arr.tolist()]
            except Exception:  # noqa: BLE001
                pass                                       # 解不开保留原块(Plotly 自己能画)
    return json.dumps(d, ensure_ascii=False)


# ---- ⑦ 商品价(A 类领先信号;周期股上游,领先财报 1-4 月)----
def commodity_price_figure(variety: str, series: pd.Series) -> go.Figure:
    """商品价时序图(周期股业绩的因果领先指标)。单线 + "1 年前"参考虚线(让同比可视化),
    标题带判定(向上🟢/背离🟡/震荡⚪/向下🔴)。空/不足 → 占位。"""
    if series is None or len(series) < 2:
        return _placeholder(variety, "商品价", "无商品价数据")
    s = series.astype(float)
    idx = pd.to_datetime(s.index)
    judge, yoy, rec = _commodity_judge(s)
    n252 = min(252, len(s) - 1)   # "1 年前"参考线(同比可视化)
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


def commodity_dev_stats(series: pd.Series, period: int = 60, top_k: int = 10) -> dict:
    """商品偏离度统计(放大视图下行标注用,纯):当前偏离 + 历史极值 Top-K + 当前第几高/第几低。

    MA 口径与 commodity_price_figure 的 MA60 同源(ti.ma_series)——图与标注必为同一条线。
    highs/lows = 历史 Top-K 偏离点 [{r:排名(1=最极端), v:偏离值, d:发生日}](放大视图在
    发生位置打点标「第几高/第几低」);并列值按时间先后稳定排序。偏离度序列本身由前端
    从已下发的价格/MA60 两条 trace 逐点相除派生(零重复传参),本函数只下发小标量数组。
    偏离值 <20 期(与 deviation_extremes 同门槛)或非有限值 → {}。"""
    if series is None or len(series) <= period:
        return {}
    dev = ti.deviation_series(series.astype(float), period).dropna()
    if len(dev) < 20:
        return {}
    cur = float(dev.iloc[-1])
    mx, mn = float(dev.max()), float(dev.min())
    if not all(pd.notna(v) and abs(v) != float("inf") for v in (cur, mx, mn)):
        return {}
    desc = dev.sort_values(ascending=False, kind="stable")
    asc = dev.sort_values(ascending=True, kind="stable")
    highs = [{"r": i + 1, "v": float(v), "d": str(d)} for i, (d, v) in enumerate(desc.iloc[:top_k].items())]
    lows = [{"r": i + 1, "v": float(v), "d": str(d)} for i, (d, v) in enumerate(asc.iloc[:top_k].items())]
    # 快腿(2026-09 提速改版):20日动量 + 60日新高/新低(价格口径,非偏离度)
    m20_n = min(20, len(series) - 1)
    momentum20 = float(series.iloc[-1]) / float(series.iloc[-1 - m20_n]) - 1.0
    m20 = momentum20 if pd.notna(momentum20) and abs(momentum20) != float("inf") else None
    win60 = series.iloc[-min(60, len(series)):]
    new_high60 = bool(float(win60.iloc[-1]) >= float(win60.max()))
    new_low60 = bool(float(win60.iloc[-1]) <= float(win60.min()))
    return {"cur": cur, "max": mx, "min": mn,
            "highs": highs, "lows": lows,
            "pct": float((dev < cur).sum()) / len(dev),  # 当前偏离的历史分位(0=最偏低,1=最偏高;与 deviation_extremes 同口径)
            "momentum20": m20,                     # 近20日价格动量(快腿横幅用;异常值→None)
            "new_high60": new_high60,              # 现价创 60 日新高(突破腿)
            "new_low60": new_low60,                # 现价创 60 日新低(突破腿)
            "rank_high": int((dev > cur).sum()) + 1,   # 第几高(1=历史最偏高)
            "rank_low": int((dev < cur).sum()) + 1,    # 第几低(1=历史最偏低)
            "n": int(len(dev))}


def _add_quarter_lines(fig: go.Figure, lo: pd.Timestamp, hi: pd.Timestamp) -> None:
    """季度分界竖线(每年 1/4/7/10 月首日,置于曲线下层):按季度读曲线段;
    1 月线=年份线、色略加重,形成 年|季|季|季 层级。淡灰点线不随主题重涂
    (同极值线先例),深浅两态下均可见;不打标签(全量视图 ~20 条会挤,悬停自带年月日)。"""
    for year in range(lo.year, hi.year + 1):
        for month in (1, 4, 7, 10):
            q = pd.Timestamp(year=year, month=month, day=1)
            if not (lo < q <= hi):
                continue
            fig.add_vline(x=q, layer="below",
                          line=dict(color=_PAL["qline_year"] if month == 1 else _PAL["baseline"],
                                    width=1, dash="dot"))


# ---- ⑦b 股价 vs 上游商品价(双 Y 轴叠加;周期股模态首图)----
def commodity_stock_overlay_figure(sym: str, name: str, variety: str,
                                   stock_df: pd.DataFrame, comm_series: pd.Series) -> go.Figure:
    """股价 + 上游商品价 双 Y 轴叠加(绝对价位,直观比对商品→股价的传导/背离)。
    左轴=股价(元),右轴=商品价;两条线同图,hovermode=x unified 同日双值。
    双轴默认按各自数据 min-max 自适应(不人工设范围,避免操纵相关性)。
    带 rangeslider(横轴窗口拖拽)+ 1月/6月/1年/3年/全部 快捷按钮,切窗口后双轴自适应重定标;
    带季度分界竖线(1/4/7/10 月首日,年份线略加重),方便按季度读曲线段。
    数据缺一股:只有商品→退独立商品图;都缺→占位。"""
    has_stock = stock_df is not None and len(stock_df) > 0 and "close" in stock_df.columns
    has_comm = comm_series is not None and len(comm_series) >= 2
    if not has_stock and not has_comm:
        return _placeholder(name, "股价vs商品价", "无数据")
    if has_comm and not has_stock:
        return commodity_price_figure(variety, comm_series)   # 兜底:仅商品 → 退独立商品图

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    close = stock_df["close"].astype(float)
    six = pd.to_datetime(close.index)
    fig.add_trace(go.Scatter(x=six, y=close, name=f"{name}股价(左轴)",
                             line=dict(color=_PAL["close"], width=2),
                             hovertemplate="%{x|%Y-%m-%d}<br>股价 %{y:.2f}元<extra></extra>"),
                  secondary_y=False)
    sma60 = ti.ma_series(close, 60)   # 股价 MA60(左轴),同口径平滑,读趋势(与商品 MA60 对称)
    fig.add_trace(go.Scatter(x=six, y=sma60, name="股价 MA60",
                             line=dict(color=_PAL["muted"], width=1.4, dash="dash"),
                             hovertemplate="%{x|%Y-%m-%d}<br>股价 MA60 %{y:.2f}<extra></extra>"),
                  secondary_y=False)
    title_core = f"{name}({sym}) 股价 vs {variety}价"
    if has_comm:
        cs = comm_series.astype(float)
        cix = pd.to_datetime(cs.index)
        judge, yoy, _ = _commodity_judge(cs)
        _yy = f"{yoy:+.0%}" if not pd.isna(yoy) else "—"
        title_core = f"{title_core} · {variety}{judge}(同比{_yy})"
        fig.add_trace(go.Scatter(x=cix, y=cs, name=f"{variety}价(右轴)",
                                 line=dict(color=_PAL["comm"], width=2),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + variety + " %{y:.0f}<extra></extra>"),
                      secondary_y=True)
        cma60 = ti.ma_series(cs, 60)   # 商品 MA60(右轴),与单图同口径平滑,读趋势(合图时别再丢)
        fig.add_trace(go.Scatter(x=cix, y=cma60, name=f"{variety} MA60",
                                 line=dict(color=_PAL["muted"], width=1.4, dash="dash"),
                                 hovertemplate="%{x|%Y-%m-%d}<br>" + variety + " MA60 %{y:.0f}<extra></extra>"),
                      secondary_y=True)
    idx_lo, idx_hi = six[0], six[-1]          # 季度线取股价/商品两序列的并集范围
    if has_comm:
        idx_lo, idx_hi = min(idx_lo, cix[0]), max(idx_hi, cix[-1])
    _add_quarter_lines(fig, idx_lo, idx_hi)
    fig.update_layout(**_layout(f"{title_core} · 双轴(左股价/右商品)", height=360, showlegend=True))
    _style_axes(fig)
    fig.update_yaxes(title_text="股价(元)", secondary_y=False)
    fig.update_yaxes(title_text=f"{variety}价", secondary_y=True, gridcolor=None)  # 右轴不画第二层网格
    # 横轴窗口拖拽 + 快捷按钮(显式 date 轴,step=month/year 的按钮才有意义)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d",
                     rangeselector=_RANGE_BUTTONS, rangeslider=dict(visible=True))
    return fig
