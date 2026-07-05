"""Research HTML dashboard — per-ETF 份额+净值+估值+性价比 interactive report.

Mirrors scripts/backtest_report.py's Plotly + f-string pattern: each chart is its own
full-width responsive figure; the first carries the plotly.js CDN include. No template
engine (pure f-string HTML).
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

CHART_HEIGHT = 520
_TEMPLATE = "plotly_white"
C_SHARES = "#2563eb"
C_NAV = "#f59e0b"
C_PE = "#7c3aed"
C_PE_NOW = "#dc2626"
C_FACTOR = "#0ea5e9"
C_GRID = "#e2e8f0"


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _composite_color(c: float) -> str:
    if _nan(c):
        return "#94a3b8"
    if c >= 75:
        return "#16a34a"  # 优 - green
    if c >= 55:
        return "#65a30d"  # 偏多
    if c >= 45:
        return "#ca8a04"  # 中性
    if c >= 25:
        return "#ea580c"  # 偏空
    return "#dc2626"      # 差 - red


def _rating(c: float) -> str:
    if _nan(c):
        return "NA"
    if c >= 75:
        return "优"
    if c >= 55:
        return "偏多"
    if c >= 45:
        return "中性"
    if c >= 25:
        return "偏空"
    return "差"


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
        margin=dict(l=60, r=60, t=60, b=40),
    )


def shares_nav_figure(name: str, shares_df, nav_df, ma_period: int = 60,
                      current_shares: float | None = None) -> go.Figure:
    """Dual-axis: shares (亿份, right) vs NAV (left) + NAV MA (趋势参考线). Share-peak marker.

    The MA uses the same window as the trend factor (research.ma_period, default 60). Note the
    trend factor computes MA on trading PRICE; here we MA the plotted NAV — visually near-
    identical since ETF price ≈ NAV. Labeled '趋势参考线' to reflect that.
    current_shares: when no share HISTORY is available (some ETFs absent from fund_etf_scale_sse),
    draw the current level as a dashed reference line so the shares axis isn't empty."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    peak_date = None
    if shares_df is not None and len(shares_df):
        s = shares_df["shares"].astype(float) / 1e8 if "shares" in shares_df else None
        if s is not None:
            fig.add_trace(go.Scatter(
                x=shares_df.index, y=s, name="份额(亿份)",
                line=dict(color=C_SHARES, width=1.4),
                hovertemplate="%{x|%Y-%m-%d}<br>份额: %{y:.2f}亿<extra></extra>"),
                secondary_y=True)
            peak_idx = s.idxmax()
            peak_date = peak_idx
            fig.add_trace(go.Scatter(
                x=[peak_idx], y=[float(s.loc[peak_idx])], name="份额峰值",
                mode="markers", marker=dict(color=C_SHARES, size=12, symbol="star",
                                            line=dict(color="white", width=1)),
                hovertemplate=f"峰值 %{{x|%Y-%m-%d}}<extra></extra>"),
                secondary_y=True)
    elif current_shares and current_shares > 0:
        # no history — draw current level as a dashed reference on the shares axis
        fig.add_trace(go.Scatter(
            x=nav_df.index if nav_df is not None and len(nav_df) else [None],
            y=[current_shares / 1e8] * (len(nav_df) if nav_df is not None and len(nav_df) else 1),
            name="当前份额(历史不可用)", line=dict(color=C_SHARES, width=1, dash="dash"),
            opacity=0.6, hovertemplate="当前份额: %{y:.2f}亿<extra></extra>"),
            secondary_y=True)
    if nav_df is not None and len(nav_df):
        # 累计净值(acc_nav) is split+dividend-adjusted → continuous across 拆分/分红.
        # unit_nav shows raw cliffs on corporate actions (e.g. 512800 2025-07-04 split),
        # so plot acc_nav for an honest trend. Fall back to unit_nav if acc_nav absent.
        nav_col = ("acc_nav" if "acc_nav" in nav_df.columns
                   and pd.to_numeric(nav_df["acc_nav"], errors="coerce").notna().any()
                   else "unit_nav")
        nav_label = "累计净值(复权·连续)" if nav_col == "acc_nav" else "单位净值"
        nav_y = pd.to_numeric(nav_df[nav_col], errors="coerce")
        fig.add_trace(go.Scatter(
            x=nav_df.index, y=nav_y, name=nav_label,
            line=dict(color=C_NAV, width=1.4),
            hovertemplate="%{x|%Y-%m-%d}<br>" + nav_label + ": %{y:.4f}<extra></extra>"),
            secondary_y=False)
        # 趋势参考线: NAV MA (same window as trend_score's MA60; price≈NAV so visually equivalent)
        ma = nav_y.rolling(ma_period, min_periods=ma_period // 2).mean()
        fig.add_trace(go.Scatter(
            x=nav_df.index, y=ma, name=f"净值MA{ma_period}(趋势参考)",
            line=dict(color="#a8a29e", width=1.2, dash="dash"), opacity=0.8,
            hovertemplate=f"%{{x|%Y-%m-%d}}<br>MA{ma_period}: %{{y:.4f}}<extra></extra>"),
            secondary_y=False)
    fig.update_layout(**_base_layout(f"{name} — 份额 vs 累计净值（复权，拆分/分红已平滑）"))
    fig.update_yaxes(title_text="累计净值", secondary_y=False, gridcolor=C_GRID)
    fig.update_yaxes(title_text="份额（亿份）", secondary_y=False, gridcolor=C_GRID)
    fig.update_xaxes(type="date", rangeslider=dict(visible=True, thickness=0.04))
    return fig


def pe_figure(name: str, pe_df) -> go.Figure:
    """Industry PE history with current value marked. Shades the lookback cheap/expensive band."""
    fig = go.Figure()
    if pe_df is None or not len(pe_df) or "pe" not in pe_df.columns:
        fig.update_layout(**_base_layout(f"{name} — 行业PE历史（无数据）"))
        return fig
    pe = pe_df["pe"].astype(float).dropna()
    fig.add_trace(go.Scatter(
        x=pe.index, y=pe, name="行业静态PE", line=dict(color=C_PE, width=1.4),
        hovertemplate="%{x|%Y-%m-%d}<br>PE: %{y:.2f}<extra></extra>"))
    if len(pe):
        lo, hi = float(pe.min()), float(pe.max())
        last = float(pe.iloc[-1])
        fig.add_hrect(y0=lo, y1=hi, line_width=0, fillcolor=C_PE, opacity=0.06,
                      annotation_text=f"{lo:.1f}~{hi:.1f}", annotation_position="top left")
        fig.add_hline(y=last, line=dict(color=C_PE_NOW, width=1.5, dash="dash"),
                      annotation_text=f"当前 {last:.1f}", annotation_position="top right")
    fig.update_layout(**_base_layout(f"{name} — 行业PE历史 + 当前位置"))
    fig.update_yaxes(title_text="静态市盈率（加权）", gridcolor=C_GRID)
    fig.update_xaxes(type="date")
    return fig


def factor_figure(name: str, snap: dict) -> go.Figure:
    """Three-factor horizontal bar + composite marker. Shows the chip phase."""
    fig = go.Figure()
    labels = ["估值", "筹码", "趋势"]
    vals = [snap.get("valuation"), snap.get("chip"), snap.get("trend")]
    vals = [0 if _nan(v) else float(v) for v in vals]
    colors = ["#7c3aed", "#2563eb", "#0ea5e9"]
    fig.add_trace(go.Bar(x=vals, y=labels, orientation="h", marker_color=colors,
                         text=[f"{v:.0f}" for v in vals], textposition="outside",
                         showlegend=False, hovertemplate="%{y}: %{x:.0f}<extra></extra>"))
    comp = snap.get("composite")
    phase = snap.get("chip_phase", "")
    red = snap.get("reduction_from_peak")
    title = (f"{name} — 三因子（{phase}·距峰值{_fmt(red, pct=True)}）"
             f"  综合性价比 <b style='color:{_composite_color(comp)}'>{_fmt(comp)} {_rating(comp)}</b>")
    fig.update_layout(**_base_layout(title, height=300))
    fig.update_xaxes(range=[0, 100], gridcolor=C_GRID)
    fig.update_yaxes(autorange="reversed")
    return fig


def _ranking_rows(snapshots: dict, meta: dict, commentaries: dict) -> str:
    rows = sorted(snapshots.items(), key=lambda kv: (kv[1].get("composite") if not _nan(kv[1].get("composite")) else -1), reverse=True)
    out = ""
    for sym, snap in rows:
        nm = meta.get(sym, {}).get("name", sym)
        comp = snap.get("composite")
        c = _composite_color(comp)
        out += (
            f"<tr><td><b>{nm}</b><br><span style='color:#64748b;font-size:11px'>{sym}</span></td>"
            f"<td style='text-align:center;font-size:18px;color:{c};font-weight:bold'>{_fmt(comp)}<br>"
            f"<span style='font-size:11px'>{_rating(comp)}</span></td>"
            f"<td style='text-align:center'>{_fmt(snap.get('valuation'))}<br>"
            f"<span style='font-size:11px;color:#64748b'>PE分位 {_fmt(snap.get('pe_percentile'), pct=True)}</span></td>"
            f"<td style='text-align:center'>{_fmt(snap.get('chip'))}<br>"
            f"<span style='font-size:11px;color:#64748b'>{snap.get('chip_phase','')}</span></td>"
            f"<td style='text-align:center'>{_fmt(snap.get('trend'))}</td>"
            f"<td style='font-size:12px'>{commentaries.get(sym,'')}</td></tr>"
        )
    return out


def _etf_figs(sym: str, snap: dict, meta: dict, series_map: dict, ma_period: int) -> list:
    """Build the 3 figures (shares+NAV, PE, factor) for one ETF."""
    nm = meta.get(sym, {}).get("name", sym)
    label = f"{nm}({sym})"
    sm = series_map.get(sym, {})
    return [
        shares_nav_figure(label, sm.get("shares"), sm.get("nav"),
                          ma_period=ma_period, current_shares=sm.get("current_shares")),
        pe_figure(label, sm.get("pe")),
        factor_figure(label, snap),
    ]


def render(snapshots: dict, series_map: dict, meta: dict, commentaries: dict,
           as_of: str, signal_note: str = "", ma_period: int = 60,
           pool_summary: str = "") -> str:
    """Build the full HTML. series_map[symbol] = {close, shares, nav, pe}."""
    # Split: data_sufficient ETFs are ranked; insufficient ones (e.g. no share history) are
    # kept for their detail charts but excluded from the ranking (shown in a note).
    def _comp(sn):
        c = sn.get("composite")
        return c if not _nan(c) else -1

    ranked = {s: sn for s, sn in snapshots.items() if sn.get("data_sufficient", True)}
    excluded = {s: sn for s, sn in snapshots.items() if not sn.get("data_sufficient", True)}

    # per-ETF detail figures: ranked (by composite) first, then excluded (by name)
    chart_figs = []
    for sym, snap in sorted(ranked.items(), key=lambda kv: _comp(kv[1]), reverse=True):
        chart_figs.extend(_etf_figs(sym, snap, meta, series_map, ma_period))
    for sym, snap in sorted(excluded.items(), key=lambda kv: meta.get(kv[0], {}).get("name", kv[0])):
        chart_figs.extend(_etf_figs(sym, snap, meta, series_map, ma_period))

    chart_blocks = []
    for i, f in enumerate(chart_figs):
        div = f.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False),
                        config={"responsive": True})
        chart_blocks.append(f'<div class="chart-block">{div}</div>')
    charts_html = "\n".join(chart_blocks)

    ranking = _ranking_rows(ranked, meta, commentaries)
    n_ranked, n_excluded = len(ranked), len(excluded)
    if excluded:
        excl_names = "、".join(f"{meta.get(s, {}).get('name', s)}({s})" for s in excluded)
        excluded_note = (
            f'<p class="sub">⚠️ {n_excluded} 只数据不足未参与排名：{excl_names}'
            f'（明细图见下方，份额历史不可用故筹码因子缺失）</p>'
        )
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
.chart-block {{ width: 100%; margin: 0 0 16px 0; }}
.chart-block > div {{ width: 100% !important; max-width: 100% !important; }}
.flag {{ background: #fef3c7; padding: 8px 12px; border-radius: 6px; font-size: 12px; color: #92400e; }}
.summary-box {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 12px 16px; border-radius: 6px;
                font-size: 14px; line-height: 1.7; color: #1e3a8a; margin: 14px 0; }}
</style></head><body>
<h2>🏭 ETF 行业研究 · 性价比看板</h2>
<p class="sub">数据截至 {as_of} 收盘 · 纯研究视图（不构成买卖建议，不含涨跌预测）· {signal_note}</p>
<div class="flag">读图：份额变动=机构真实意图（散户买卖只转手不改份额）；性价比=估值PE分位(0.40)+筹码动向(0.30)+价格趋势(0.30)。
筹码相位含文章「末期见底」非单调逻辑：兑现中段最空，深回撤+卖盘枯竭(见底)最看多。</div>
{summary_html}
<h3>📊 性价比排名（{n_ranked} 只参与{n_excluded and f"，{n_excluded} 只数据不足未参与" or ""}）</h3>
{excluded_note}
<table><thead><tr><th style="text-align:left">ETF</th><th>综合性价比</th><th>估值(PE分位)</th><th>筹码(相位)</th><th>趋势</th><th style="text-align:left">解读</th></tr></thead>
<tbody>{ranking}</tbody></table>
<h3>📈 逐标的明细（份额·净值·估值·三因子）</h3>
{charts_html}
</body></html>"""


def write_html(html: str, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
