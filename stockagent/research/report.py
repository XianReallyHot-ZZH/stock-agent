"""Research HTML dashboard — per-ETF 择时跟踪（份额·净值·偏离度·剪刀差）interactive report.

定位：从「性价比评估」转定位为「ETF 择时跟踪」——纯跟踪、不标买卖点、决策由人综合
多个看板做出。每个 ETF 出两类图：① 份额 vs 累计净值（剪刀差分化窗口叠加）② 净值-MA60
偏离度（历史极值「第几」标记 + 当前分位）。保留 value/growth/cyclic 三类 tab 仅作分组。

Mirrors scripts/backtest_report.py's Plotly + f-string pattern: each chart is its own
full-width responsive figure; the first carries the plotly.js CDN include. No template
engine (pure f-string HTML).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import earnings as ern
from . import timing as tm

CHART_HEIGHT = 460
_TEMPLATE = "plotly_white"
C_SHARES = "#2563eb"
C_NAV = "#f59e0b"
C_GRID = "#e2e8f0"
C_DEV_POS = "#ea580c"   # 正偏离（线上方）
C_DEV_NEG = "#2563eb"   # 负偏离（线下方）

# 时序图 x 轴快捷窗口按钮（配合底部 rangeslider：按钮一键切档、滑块精细拖拽）
_RANGE_BUTTONS = dict(
    buttons=[
        dict(count=1, label="1月", step="month", stepmode="backward"),
        dict(count=6, label="6月", step="month", stepmode="backward"),
        dict(count=1, label="1年", step="year", stepmode="backward"),
        dict(count=3, label="3年", step="year", stepmode="backward"),
        dict(step="all", label="全部"),
    ],
    bgcolor="#f1f5f9", activecolor="#fcd34d", font=dict(color="#1e293b", size=11),
)


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _fmt(v, pct=False, nd=0) -> str:
    if _nan(v):
        return "NA"
    if pct:
        return f"{v * 100:.{nd}f}%"
    return f"{v:.{nd}f}"


def _base_layout(title: str, height: int = CHART_HEIGHT) -> dict:
    return dict(
        title=dict(text=title, font=dict(size=14)),
        height=height, template=_TEMPLATE, hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=54, r=54, t=50, b=28),
    )


def _nav_series(nav_df):
    """Pick acc_nav (split+dividend-adjusted, continuous) else unit_nav. Returns (Series, label)."""
    if nav_df is None or not len(nav_df):
        return None, None
    nav_col = ("acc_nav" if "acc_nav" in nav_df.columns
               and pd.to_numeric(nav_df["acc_nav"], errors="coerce").notna().any()
               else "unit_nav")
    s = pd.to_numeric(nav_df[nav_col], errors="coerce").dropna()
    label = "累计净值(复权·连续)" if nav_col == "acc_nav" else "单位净值"
    return s, label


def shares_nav_figure(name: str, shares_df, nav_df, ma_period: int = 60,
                      current_shares: float | None = None, scissor: dict | None = None) -> go.Figure:
    """Dual-axis: shares (亿份, right) vs NAV (left, acc_nav preferred) + NAV MA 趋势参考线
    + 份额峰值 ★。若 scissor 命中，置灰分化窗口并注释「剪刀差：份x% / 净y%（W日）」。

    acc_nav 复权连续（拆分/分红已平滑）；unit_nav 在公司行为上有断崖。current_shares：当
    无份额历史时（部分 ETF 不在 fund_etf_scale_sse），画当前水平作虚线参考。"""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if shares_df is not None and len(shares_df):
        s = shares_df["shares"].astype(float) / 1e8 if "shares" in shares_df else None
        if s is not None:
            fig.add_trace(go.Scatter(
                x=shares_df.index, y=s, name="份额(亿份)",
                line=dict(color=C_SHARES, width=1.4),
                hovertemplate="%{x|%Y-%m-%d}<br>份额: %{y:.2f}亿<extra></extra>"),
                secondary_y=True)
            peak_idx = s.idxmax()
            fig.add_trace(go.Scatter(
                x=[peak_idx], y=[float(s.loc[peak_idx])], name="份额峰值",
                mode="markers", marker=dict(color=C_SHARES, size=12, symbol="star",
                                            line=dict(color="white", width=1)),
                hovertemplate=f"峰值 %{{x|%Y-%m-%d}}<extra></extra>"),
                secondary_y=True)
    elif current_shares and current_shares > 0:
        nav_idx = nav_df.index if nav_df is not None and len(nav_df) else [None]
        fig.add_trace(go.Scatter(
            x=nav_idx, y=[current_shares / 1e8] * (len(nav_idx) if nav_df is not None and len(nav_df) else 1),
            name="当前份额(历史不可用)", line=dict(color=C_SHARES, width=1, dash="dash"),
            opacity=0.6, hovertemplate="当前份额: %{y:.2f}亿<extra></extra>"),
            secondary_y=True)
    nav_y, nav_label = _nav_series(nav_df)
    if nav_y is not None and len(nav_y):
        fig.add_trace(go.Scatter(
            x=nav_y.index, y=nav_y.values, name=nav_label,
            line=dict(color=C_NAV, width=1.4),
            hovertemplate="%{x|%Y-%m-%d}<br>" + nav_label + ": %{y:.4f}<extra></extra>"),
            secondary_y=False)
        ma = nav_y.rolling(ma_period, min_periods=ma_period // 2).mean()
        fig.add_trace(go.Scatter(
            x=nav_y.index, y=ma.values, name=f"净值MA{ma_period}(趋势参考)",
            line=dict(color="#a8a29e", width=1.2, dash="dash"), opacity=0.8,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>MA{ma_period}: %{{y:.4f}}<extra></extra>"),
            secondary_y=False)
    # 剪刀差分化窗口（命中时置灰 + 注释；不命中则保持原始双线图，用户自己看）
    if scissor and scissor.get("detected"):
        x0 = pd.to_datetime(scissor["start"])
        x1 = pd.to_datetime(scissor["end"])
        sd, nd = scissor["share_drift"], scissor["nav_drift"]
        arrow = "份↑净↓" if scissor["direction"] == "share_up_nav_down" else "份↓净↑"
        fig.add_vrect(x0=x0, x1=x1, fillcolor=C_NAV, opacity=0.10, layer="below", line_width=0)
        fig.add_annotation(x=x1, y=1.0, yref="paper", xref="x", showarrow=False,
                           text=f"剪刀差 {arrow}<br>份{sd:+.0%} / 净{nd:+.0%}（{scissor['window']}日）",
                           bgcolor="#fffbeb", font=dict(size=10, color="#92400e"),
                           bordercolor=C_NAV, borderwidth=1)
    fig.update_layout(**_base_layout(f"{name} — 份额 vs 累计净值（复权，拆分/分红已平滑）"))
    fig.update_yaxes(title_text="累计净值", secondary_y=False, gridcolor=C_GRID)
    fig.update_yaxes(title_text="份额（亿份）", secondary_y=False, gridcolor=C_GRID)
    fig.update_xaxes(type="date", rangeselector=_RANGE_BUTTONS,
                     rangeslider=dict(visible=True, thickness=0.04))
    return fig


def nav_deviation_figure(name: str, nav_df, snap: dict, ma_period: int = 60) -> go.Figure:
    """净值 vs MA60（上）+ 偏离度副图（下）：正/负偏离柱 + 历史极值虚线 + top-N 极值 ▲/▼
    「第几」标记（来自 timing.deviation_extreme_events）+ 当前点分位 chip。"""
    fig = go.Figure()
    nav_y, nav_label = _nav_series(nav_df)
    if nav_y is None or len(nav_y) < ma_period:
        fig.update_layout(**_base_layout(f"{name} — 净值MA偏离（历史不足）", height=320))
        return fig
    ma = tm.ma_series(nav_y, ma_period)
    dev = tm.deviation_series(nav_y, ma_period)
    idx = pd.to_datetime(nav_y.index)
    dc = dev.dropna()
    mx = float(dc.max()) if len(dc) else float("nan")
    mn = float(dc.min()) if len(dc) else float("nan")
    events = snap.get("nav_extreme_events") or []
    lows = [e for e in events if e["side"] == "low"]
    highs = [e for e in events if e["side"] == "high"]
    cur_dev = snap.get("nav_dev_cur")
    cur_pct = snap.get("nav_dev_pct")

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                        vertical_spacing=0.12,
                        subplot_titles=(f"{nav_label} vs MA{ma_period}", "偏离度 (净值−均线)÷均线"))
    fig.add_trace(go.Scatter(x=idx, y=nav_y.values, name=nav_label,
                             line=dict(color=C_NAV, width=1.6)), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma.values, name=f"MA{ma_period}",
                             line=dict(color="#a8a29e", width=1.2, dash="dash")), row=1, col=1)
    # 偏离柱按正负着色（不赋予买卖含义，仅区分方向）
    pos = dev.where(dev >= 0, np.nan)
    neg = dev.where(dev < 0, np.nan)
    fig.add_trace(go.Bar(x=idx, y=pos.values, name="正偏离", marker_color=C_DEV_POS,
                         hovertemplate="%{x|%Y-%m-%d}<br>偏离 %{y:.1%}<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Bar(x=idx, y=neg.values, name="负偏离", marker_color=C_DEV_NEG,
                         hovertemplate="%{x|%Y-%m-%d}<br>偏离 %{y:.1%}<extra></extra>"), row=2, col=1)
    if not np.isnan(mx):
        fig.add_hline(y=mx, row=2, col=1, line=dict(color=C_DEV_POS, width=1, dash="dot"),
                      annotation_text=f"正极值 +{mx:.0%}", annotation_position="top left")
    if not np.isnan(mn):
        fig.add_hline(y=mn, row=2, col=1, line=dict(color=C_DEV_NEG, width=1, dash="dot"),
                      annotation_text=f"负极值 {mn:.0%}", annotation_position="bottom left")
    # 历史低点（▼ 第k低，1=史上最深谷）
    if lows:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([e["date"] for e in lows]), y=[e["dev"] for e in lows],
            name="历史低点", mode="markers+text", showlegend=False,
            marker=dict(symbol="triangle-down", size=11, color=C_DEV_NEG, line=dict(color="white", width=0.5)),
            text=[f"第{e['rank']}低" for e in lows], textposition="bottom center", textfont=dict(size=9),
            cliponaxis=False,
            hovertemplate="<b>%{x|%Y-%m-%d}</b> 超卖极值<br>偏离 %{y:.1%}<extra></extra>"), row=2, col=1)
    # 历史高点（▲ 第k高，1=史上最高峰）
    if highs:
        fig.add_trace(go.Scatter(
            x=pd.to_datetime([e["date"] for e in highs]), y=[e["dev"] for e in highs],
            name="历史高点", mode="markers+text", showlegend=False,
            marker=dict(symbol="triangle-up", size=11, color=C_DEV_POS, line=dict(color="white", width=0.5)),
            text=[f"第{e['rank']}高" for e in highs], textposition="top center", textfont=dict(size=9),
            cliponaxis=False,
            hovertemplate="<b>%{x|%Y-%m-%d}</b> 超买极值<br>偏离 %{y:.1%}<extra></extra>"), row=2, col=1)
    chip = ""
    if not _nan(cur_pct):
        if cur_pct <= 0.05:
            chip = f" · 超卖区({cur_pct:.0%})"
        elif cur_pct >= 0.95:
            chip = f" · 超买区({cur_pct:.0%})"
    if not _nan(cur_dev):
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur_dev], mode="markers+text", showlegend=False,
                                 marker=dict(size=11, color="#1e293b", line=dict(color="white", width=1)),
                                 text=[f"现在 {cur_dev:.1%}{chip}"], textposition="top center",
                                 textfont=dict(size=10)), row=2, col=1)

    title = f"{name} — 净值MA{ma_period}偏离"
    if not _nan(cur_dev):
        pct_s = f"{cur_pct:.0%}" if not _nan(cur_pct) else "NA"
        title += f"  当前 <b>{cur_dev:+.1%}</b> · 分位 <b>{pct_s}</b>{chip}"
    fig.update_layout(**_base_layout(title, height=520))
    fig.update_yaxes(title_text="净值", row=1, col=1, gridcolor=C_GRID)
    fig.update_yaxes(title_text="偏离度", row=2, col=1, gridcolor=C_GRID, tickformat=".0%")
    fig.update_xaxes(type="date", rangeselector=_RANGE_BUTTONS, row=1, col=1)
    # 底部子图（row=2）挂 rangeslider —— 与 shares_nav_figure 一致，拖拽滑块切观察窗口；
    # shared_xaxes=True 故上下两图联动。写法参照 tracker/dashboard.py 的 2-row 子图。
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.04), row=2, col=1)
    return fig


_EARN_COLOR = {
    "业绩高增": "#16a34a", "业绩改善": "#65a30d", "业绩平稳": "#64748b",
    "业绩承压": "#ea580c", "业绩恶化": "#dc2626", "数据不足": "#94a3b8",
}
_EARN_LOW_COV = 0.60


def _earnings_freshness_line(snap: dict) -> str:
    period = snap.get("earnings_period")
    plabel = ern.period_label(period)
    cov = snap.get("earnings_cov")
    cov_s = f"{cov:.0%}" if isinstance(cov, (int, float)) and not _nan(cov) else "—"
    low = isinstance(cov, (int, float)) and not _nan(cov) and cov < _EARN_LOW_COV
    color = "#b45309" if low else "#94a3b8"
    warn = " · ⚠覆盖偏低" if low else ""
    return f"<br><span style='font-size:10px;color:{color}'>{plabel} · 覆盖 {cov_s}{warn}</span>"


def _earnings_cell(snap: dict) -> str:
    """业绩预期 cell — 纯信息列（最新一期业绩预告口径 + 覆盖度）。"""
    label = snap.get("earnings_label")
    if not label:
        return "<td style='text-align:center;color:#cbd5e1'>—</td>"
    color = _EARN_COLOR.get(label, "#64748b")
    yoy = snap.get("earnings_yoy")
    yoy_s = f"{yoy:+.0f}%" if isinstance(yoy, (int, float)) and not _nan(yoy) else "—"
    bull, bear = snap.get("earnings_bull"), snap.get("earnings_bear")
    bb = ""
    if isinstance(bull, (int, float)) and not _nan(bull):
        bb = (f"<br><span style='font-size:11px;color:#64748b'>归母YoY {yoy_s}"
              f" · 多{bull:.0%}/空{bear:.0%}</span>")
    fresh = _earnings_freshness_line(snap)
    return f"<td style='text-align:center;font-weight:bold;color:{color}'>{label}{bb}{fresh}</td>"


def _extremeness(sn: dict) -> float:
    """排序键：偏离度极值程度 = |nav_dev_pct − 0.5|（越接近 0 或 1 = 越极端）。NaN→−1。"""
    p = sn.get("nav_dev_pct")
    return abs(p - 0.5) if not _nan(p) else -1.0


def _ranking_rows(snapshots: dict, meta: dict, style_filter: str | None = None) -> str:
    rows = sorted(snapshots.items(), key=lambda kv: _extremeness(kv[1]), reverse=True)
    out = ""
    for sym, snap in rows:
        if style_filter and snap.get("style", "growth") != style_filter:
            continue
        nm = meta.get(sym, {}).get("name", sym)
        aum = snap.get("aum_yi")
        aum_html = (f"<br><span style='color:#94a3b8;font-size:10px'>规模 {aum:.0f}亿</span>"
                    if not _nan(aum) else "")
        style = snap.get("style", "growth")
        style_cn = {"value": "价值", "growth": "成长", "cyclic": "周期"}.get(style, style)
        style_color = {"value": "#16a34a", "growth": "#2563eb", "cyclic": "#ea580c"}.get(style, "#64748b")

        # 净值MA偏离 cell：当前偏离%（着色）+ 分位 + 极值区 chip
        cur = snap.get("nav_dev_cur")
        pct = snap.get("nav_dev_pct")
        dev_color = C_DEV_POS if (not _nan(cur) and cur > 0) else (C_DEV_NEG if not _nan(cur) else "#94a3b8")
        chip = ""
        if not _nan(pct):
            if pct <= 0.05:
                chip = " ·超卖区"
            elif pct >= 0.95:
                chip = " ·超买区"
        cur_s = f"{cur:+.1%}" if not _nan(cur) else "NA"
        pct_s = f"分位 {pct:.0%}" if not _nan(pct) else ""
        dev_cell = (f"<td style='text-align:center;font-weight:bold;color:{dev_color}'>{cur_s}"
                    f"<br><span style='font-size:11px;color:#64748b'>{pct_s}<b>{chip}</b></span></td>")

        # 份额/净值剪刀差 cell
        sc = snap.get("scissor") or {}
        if sc.get("detected"):
            sd, nd = sc["share_drift"], sc["nav_drift"]
            arrow = "份↑净↓" if sc["direction"] == "share_up_nav_down" else "份↓净↑"
            sc_cell = (f"<td style='text-align:center;color:#b45309;font-weight:600'>{arrow}"
                       f"<br><span style='font-size:11px;color:#64748b'>份{sd:+.0%}/净{nd:+.0%}<br>{sc['window']}日</span></td>")
        else:
            sc_cell = "<td style='text-align:center;color:#cbd5e1'>—</td>"

        to = snap.get("turnover_5d_yi")
        to_html = f"{to:.1f}亿" if not _nan(to) else "—"
        out += (
            f"<tr><td><b><a href='#{sym}' style='color:#1e293b;text-decoration:none'>{nm}</a></b>"
            f"<br><span style='color:#64748b;font-size:11px'>{sym}</span>{aum_html}</td>"
            f"<td style='text-align:center;color:{style_color};font-weight:600'>{style_cn}</td>"
            f"{dev_cell}{sc_cell}"
            f"{_earnings_cell(snap)}"
            f"<td style='text-align:center;color:#475569'>{to_html}</td></tr>"
        )
    return out


def _etf_figs(sym: str, snap: dict, meta: dict, series_map: dict, ma_period: int) -> list:
    """每 ETF 明细图组：份额 vs 净值（剪刀差叠加）+ 净值-MA 偏离度（极值标记 + 分位）。"""
    nm = meta.get(sym, {}).get("name", sym)
    aum = snap.get("aum_yi")
    label = f"{nm}({sym})" + (f" · 规模{aum:.0f}亿" if not _nan(aum) else "")
    sm = series_map.get(sym, {})
    return [
        shares_nav_figure(label, sm.get("shares"), sm.get("nav"),
                          ma_period=ma_period, current_shares=sm.get("current_shares"),
                          scissor=snap.get("scissor")),
        nav_deviation_figure(label, sm.get("nav"), snap, ma_period=ma_period),
    ]


# 懒渲染交互：IntersectionObserver 滚入 newPlot、滚出 purge（bound 内存）。
# 依赖运行时 Plotly 与 CHARTS（由 render 嵌入）。data-sym/data-idx 定位 CHARTS[sym][i]。
_LAZY_CHART_JS = r"""
(function(){
  function render(el){
    if(el.dataset.rendered === '1') return;
    var sym = el.getAttribute('data-sym'), i = +el.getAttribute('data-idx');
    var list = CHARTS[sym];
    if(!list || !list[i]) return;
    el.dataset.rendered = '1';
    Plotly.newPlot(el, list[i], {responsive:true, displaylogo:false});
  }
  function purge(el){
    if(el.dataset.rendered !== '1') return;
    try { Plotly.purge(el); } catch(e) {}
    delete el.dataset.rendered;
  }
  var nodes = document.querySelectorAll('.lazy-chart');
  if(!('IntersectionObserver' in window)){ nodes.forEach(render); return; }   // 老浏览器兜底
  var io = new IntersectionObserver(function(entries){
    entries.forEach(function(e){ if(e.isIntersecting) render(e.target); else purge(e.target); });
  }, {rootMargin: '300px 0px'});
  nodes.forEach(function(el){ io.observe(el); });
})();
"""


def render(snapshots: dict, series_map: dict, meta: dict, as_of: str,
           signal_note: str = "", ma_period: int = 60, pool_summary: str = "") -> str:
    """Build the full HTML. series_map[symbol] = {close, shares, nav}."""
    # data_sufficient ETFs 参与排名；不足者（NAV 历史不够算偏离度）保留明细图、不进排名。
    ranked = {s: sn for s, sn in snapshots.items() if sn.get("data_sufficient", True)}
    excluded = {s: sn for s, sn in snapshots.items() if not sn.get("data_sufficient", True)}

    # 逐标的明细：按偏离度极值排（ranked 在前，excluded 随后按名）
    ordered = (list(sorted(ranked.items(), key=lambda kv: _extremeness(kv[1]), reverse=True))
               + list(sorted(excluded.items(), key=lambda kv: meta.get(kv[0], {}).get("name", kv[0]))))
    # 懒渲染：图数据 to_json 嵌入 CHARTS dict，页内只放占位 div；IntersectionObserver
    # 滚入视口才 Plotly.newPlot、离开 purge 释放——把同时在画的图从 66 张压到 ~3-5 张，
    # 消除整页卡顿（参照 stock_report.py 懒渲染模式，适配为内联滚动版）。
    import re
    from plotly.offline import get_plotlyjs
    charts_json: dict[str, list[str]] = {}
    chart_blocks = []
    for sym, snap in ordered:
        nm = meta.get(sym, {}).get("name", sym)
        figs = _etf_figs(sym, snap, meta, series_map, ma_period)
        charts_json[sym] = [f.to_json() for f in figs]
        block = f'<div id="{sym}" class="etf-detail"><h4>📌 {nm}（{sym}）</h4>'
        for i, fig in enumerate(figs):
            h = int(fig.layout.height or 460)   # 占位高度匹配图高，避免渲染后跳屏/留白
            block += (f'<div class="chart-block">'
                      f'<div class="lazy-chart" data-sym="{sym}" data-idx="{i}" '
                      f'style="min-height:{h}px"></div></div>')
        chart_blocks.append(block + "</div>")
    charts_html = "\n".join(chart_blocks)
    _esc = lambda s: re.sub(r"</script", r"<\\/script", s, flags=re.I)
    entries = _esc(",\n".join(f'"{sym}":[{",".join(charts_json[sym])}]' for sym in charts_json))
    charts_script = ("<script>\n" + _esc(get_plotlyjs()) + "\nvar CHARTS={" + entries + "};\n"
                     + _LAZY_CHART_JS + "\n</script>")

    ranking_value = _ranking_rows(ranked, meta, style_filter="value")
    ranking_growth = _ranking_rows(ranked, meta, style_filter="growth")
    ranking_cyclic = _ranking_rows(ranked, meta, style_filter="cyclic")
    _rank_header = (f'<tr><th style="text-align:left">ETF</th><th>类型</th>'
                    f'<th>净值MA{ma_period}偏离<sup style="font-size:9px">分位</sup></th>'
                    f'<th>份额/净值剪刀差</th>'
                    f'<th>业绩预期<sup style="font-size:9px">信息</sup></th>'
                    f'<th>成交<sub style="font-size:9px">5日</sub></th></tr>')
    n_ranked, n_excluded = len(ranked), len(excluded)
    if excluded:
        excl_names = "、".join(f"{meta.get(s, {}).get('name', s)}({s})" for s in excluded)
        excluded_note = (f'<p class="sub">⚠️ {n_excluded} 只 NAV 历史不足未参与排名：{excl_names}'
                         f'（明细图见下方）</p>')
    else:
        excluded_note = ""

    summary_html = (f'<h3>🔍 全池格局</h3><div class="summary-box">{pool_summary}</div>'
                    if pool_summary else "")

    return f"""<html><head><meta charset="utf-8"><title>ETF 行业研究 · {as_of}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f8fafc; color: #1e293b; }}
h2 {{ border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
h3 {{ color: #334155; margin-top: 28px; }}
p.sub {{ color: #64748b; font-size: 13px; margin-top: -8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; background: white; }}
th {{ background: #f1f5f9; padding: 10px; text-align: center; border-bottom: 2px solid #cbd5e1; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
td:first-child {{ text-align: left; }}
tr:hover {{ background: #f8fafc; }}
.chart-block {{ width: 100%; margin: 0 0 4px 0; }}
.chart-block > div {{ width: 100% !important; max-width: 100% !important; }}
.lazy-chart {{ width: 100%; }}
.flag {{ background: #fef3c7; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #92400e; }}
.summary-box {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 12px 16px; border-radius: 6px;
                font-size: 14px; line-height: 1.7; color: #1e3a8a; margin: 14px 0; }}
html {{ scroll-behavior: smooth; }}
.etf-detail {{ margin: 12px 0 4px; padding: 8px 0 0; border-top: 2px solid #cbd5e1; }}
.etf-detail:target {{ background: #eff6ff; transition: background 0.6s; }}
.etf-detail h4 {{ margin: 0 0 10px; color: #334155; }}
</style></head><body>
<h2>🏭 ETF 行业研究 · 择时跟踪看板</h2>
<p class="sub">数据截至 {as_of} 收盘 · 纯跟踪视图（只跟踪、不标买卖点、不含涨跌预测；决策请综合多个看板）· {signal_note}</p>
<div class="flag">读图：本看板跟踪两件事——① <b>净值-MA{ma_period}偏离度</b>：净值相对自身均线的偏离 + 历史百分位分位（0=最负/超卖…1=最正/超买），副图标历史极值「第几低/高」（1=史上最极端，纯观察）。
② <b>份额-净值剪刀差</b>：份额与净值走向分化（一升一降）时置灰标注漂移幅度与窗口天数；检不出干净分化则只画原始双线。两者均为跟踪/观察信号，不构成买卖建议。</div>
{summary_html}
<h3>📊 择时跟踪排名 · 三类分页（{n_ranked} 只参与{n_excluded and f"，{n_excluded} 只 NAV 历史不足未参与" or ""}）</h3>
{excluded_note}
<h3 style="color:#16a34a">💰 价值型</h3>
<table><thead>{_rank_header}</thead><tbody>{ranking_value}</tbody></table>
<h3 style="color:#2563eb">🚀 成长型</h3>
<table><thead>{_rank_header}</thead><tbody>{ranking_growth}</tbody></table>
<h3 style="color:#ea580c">🔄 周期型</h3>
<table><thead>{_rank_header}</thead><tbody>{ranking_cyclic}</tbody></table>
<h3>📈 逐标的明细（份额·净值·偏离度·剪刀差）</h3>
{charts_html}
{charts_script}
</body></html>"""


def write_html(html: str, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
