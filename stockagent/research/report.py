"""Research HTML dashboard — per-ETF 择时跟踪（份额·净值·偏离度·剪刀差）interactive report.

定位：从「性价比评估」转定位为「ETF 择时跟踪」——纯跟踪、不标买卖点、决策由人综合
多个看板做出。每个 ETF 出两类图：① 份额 vs 累计净值（剪刀差分化窗口叠加）② 净值-MA60
偏离度（历史极值「第几」标记 + 当前分位）。保留 value/growth/cyclic 三类 tab 仅作分组。

页面布局（与其他三看板对齐）：
- 深浅色主题切换（默认浅色，localStorage 记忆；Plotly 懒渲染架构下 toggle = purge + 按当前
  主题重画视口内图表）
- 排名区真 tab 分页（价值/成长/周期，记住上次选择），表头点击排序
- 逐标的明细折叠面板（details 默认收起、置顶默认展开、summary 放摘要 chips）+ 顶部快速
  跳转下拉 + 右下角回顶部

Mirrors scripts/backtest_report.py's Plotly + f-string pattern: each chart is its own
full-width responsive figure; the first carries the plotly.js CDN include. No template
engine (pure f-string HTML).
"""
from __future__ import annotations

import math
import re
from datetime import datetime
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

# 时序图 x 轴快捷窗口按钮（配合底部 rangeslider：按钮一键切档、滑块精细拖拽）。
# bgcolor 写浅色值做默认，暗色由前端 _applyPlotlyTheme relayout 覆盖。
_RANGE_BUTTONS = dict(
    buttons=[
        dict(count=1, label="1月", step="month", stepmode="backward"),
        dict(count=6, label="6月", step="month", stepmode="backward"),
        dict(count=1, label="1年", step="year", stepmode="backward"),
        dict(count=3, label="3年", step="year", stepmode="backward"),
        dict(step="all", label="全部"),
    ],
    bgcolor="white", activecolor="#fcd34d", font=dict(color="#1e293b", size=11),
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


def _x_range_default(fig: go.Figure, years: float = 3.0) -> None:
    """逐标的明细图初始视图 = 最近 N 年（默认 3 年）。此 plotly 版本 rangeselector
    无 active 属性 → 显式设 x range（按钮高亮不跟随初始 range 属版本限制；
    全历史走「全部」按钮/底部滑块）。x 末端取全部 trace 的最大日期。"""
    ends = [pd.to_datetime(tr.x[-1]) for tr in fig.data
            if tr.x is not None and len(tr.x) and tr.x[-1] is not None]
    if not ends:
        return
    x_last = max(ends)
    fig.update_xaxes(range=[x_last - pd.DateOffset(years=years), x_last])


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
    # 左轴=净值(secondary_y=False)、右轴=份额(secondary_y=True) —— 标题别装反
    fig.update_yaxes(title_text="累计净值", secondary_y=False, gridcolor=C_GRID)
    fig.update_yaxes(title_text="份额（亿份）", secondary_y=True, gridcolor=C_GRID)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d", rangeselector=_RANGE_BUTTONS,
                     rangeslider=dict(visible=True, thickness=0.02))
    _x_range_default(fig)                     # 初始视图 = 最近 3 年
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
                        vertical_spacing=0.05,
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
    # 历史低点（▼ 第k低，1=史上最深谷）—— name 供前端暗色主题 restyle 文字颜色
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
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[cur_dev], name="现在", mode="markers+text",
                                 showlegend=False,
                                 marker=dict(size=11, color="#1e293b", line=dict(color="white", width=1.5)),
                                 text=[f"现在 {cur_dev:.1%}{chip}"], textposition="top center",
                                 textfont=dict(size=10)), row=2, col=1)

    title = f"{name} — 净值MA{ma_period}偏离"
    if not _nan(cur_dev):
        pct_s = f"{cur_pct:.0%}" if not _nan(cur_pct) else "NA"
        title += f"  当前 <b>{cur_dev:+.1%}</b> · 分位 <b>{pct_s}</b>{chip}"
    fig.update_layout(**_base_layout(title, height=520))
    fig.update_yaxes(title_text="净值", row=1, col=1, gridcolor=C_GRID)
    fig.update_yaxes(title_text="偏离度", row=2, col=1, gridcolor=C_GRID, tickformat=".0%")
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d", rangeselector=_RANGE_BUTTONS, row=1, col=1)
    # 底部子图（row=2）挂 rangeslider —— 与 shares_nav_figure 一致，拖拽滑块切观察窗口；
    # shared_xaxes=True 故上下两图联动。写法参照 tracker/dashboard.py 的 2-row 子图。
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.02), row=2, col=1)
    _x_range_default(fig)                     # 初始视图 = 最近 3 年（shared_xaxes 上下联动）
    return fig


_EARN_CLASS = {
    "业绩高增": "en-hi", "业绩改善": "en-up", "业绩平稳": "en-flat",
    "业绩承压": "en-dn", "业绩恶化": "en-bad", "数据不足": "en-na",
}
_EARN_LOW_COV = 0.60


def _earnings_freshness_line(snap: dict) -> str:
    period = snap.get("earnings_period")
    plabel = ern.period_label(period)
    cov = snap.get("earnings_cov")
    cov_s = f"{cov:.0%}" if isinstance(cov, (int, float)) and not _nan(cov) else "—"
    low = isinstance(cov, (int, float)) and not _nan(cov) and cov < _EARN_LOW_COV
    warn = " · ⚠覆盖偏低" if low else ""
    cls = "warn-txt" if low else ""
    return (f"<br><span class='fresh {cls}'>{plabel} · 覆盖 {cov_s}{warn}</span>")


# 一致预期(E2) label → 色 class（复用业绩预告的 en-* 语义色）
_CONS_CLASS = {"预期高增": "en-hi", "预期改善": "en-up", "预期平稳": "en-flat",
               "预期承压": "en-dn", "预期负增": "en-bad"}


def _consensus_line(snap: dict) -> str:
    """一致预期行（E2 双 chip 的下半）: 加权预期增速 g + 覆盖 + 评级结构 + 财年·快照日
    + 4周修正（E4；冷启动期显示「累积中 N/4」诚实降级）.

    无快照/覆盖不足 → ""（cell 只剩预告层, 优雅降级）。水平值口径（非变化量, 修正另示）。
    """
    g = snap.get("consensus_g")
    if not isinstance(g, (int, float)) or _nan(g):
        return ""
    cov = snap.get("consensus_cov")
    cov_s = f"{cov:.0%}" if isinstance(cov, (int, float)) and not _nan(cov) else "—"
    lab = str(snap.get("consensus_label", ""))
    cls = _CONS_CLASS.get(lab, "")
    buy = snap.get("consensus_buy")
    buy_s = (f" · 买入{buy:.0%}" if isinstance(buy, (int, float)) and not _nan(buy) else "")
    rev = snap.get("revision_w")
    if isinstance(rev, (int, float)) and not _nan(rev):
        rcls = "en-dn" if rev < 0 else "en-up"
        buy_s += (f" · 修正<b class='{rcls}'>{rev:+.1%}</b>"
                  f"<span class='muted'>({snap.get('revision_up', 0)}↑/"
                  f"{snap.get('revision_dn', 0)}↓)</span>")
    elif snap.get("revision_status"):
        buy_s += f" · <span class='muted'>{snap['revision_status']}</span>"
    fy, sd = snap.get("consensus_fy", ""), str(snap.get("consensus_snap", ""))
    snap_s = f"·快照{sd[4:6]}/{sd[6:8]}" if len(sd) == 8 else ""
    fy_s = f" · {fy}{snap_s}" if fy else (f" · {snap_s.strip('·')}" if snap_s else "")
    return (f"<br><span class='sub2 {cls}'>预期g {g:+.1%}"
            f"<br>覆盖 {cov_s}{buy_s}{fy_s}</span>")


def _earnings_cell(snap: dict) -> str:
    """业绩预期 cell — 纯信息列（上: 业绩预告口径 + 覆盖度; 下: 一致预期增速 E2）。"""
    cons = _consensus_line(snap)
    label = snap.get("earnings_label")
    if not label:
        if cons:
            return f"<td class='c'>{cons}</td>"
        return "<td class='c'><span class='ghost'>—</span></td>"
    cls = _EARN_CLASS.get(label, "en-flat")
    yoy = snap.get("earnings_yoy")
    yoy_s = f"{yoy:+.0f}%" if isinstance(yoy, (int, float)) and not _nan(yoy) else "—"
    bull, bear = snap.get("earnings_bull"), snap.get("earnings_bear")
    bb = ""
    if isinstance(bull, (int, float)) and not _nan(bull):
        bb = (f"<br><span class='sub2'>归母YoY {yoy_s}"
              f" · 多{bull:.0%}/空{bear:.0%}</span>")
    fresh = _earnings_freshness_line(snap)
    return f"<td class='c bold {cls}'>{label}{bb}{fresh}{cons}</td>"


def _extremeness(sn: dict) -> float:
    """排序键：偏离度极值程度 = |nav_dev_pct − 0.5|（越接近 0 或 1 = 越极端）。NaN→−1。"""
    p = sn.get("nav_dev_pct")
    return abs(p - 0.5) if not _nan(p) else -1.0


def _order_detail(ranked: dict, excluded: dict, pinned, meta: dict) -> list:
    """逐标的明细顺序：置顶 ETF(pinned，按给定顺序) 最前 → 其余按偏离度极值 → excluded 随后按名。"""
    all_snaps = {**ranked, **excluded}
    pin = [s for s in (pinned or []) if s in all_snaps]
    pin_set = set(pin)
    ranked_sorted = sorted(ranked.items(), key=lambda kv: _extremeness(kv[1]), reverse=True)
    excluded_sorted = sorted(excluded.items(), key=lambda kv: meta.get(kv[0], {}).get("name", kv[0]))
    return ([(s, all_snaps[s]) for s in pin]
            + [kv for kv in ranked_sorted if kv[0] not in pin_set]
            + [kv for kv in excluded_sorted if kv[0] not in pin_set])


# 偏离度极端区阈值（与 deviation_extreme_events 的 lo_pct/hi_pct、图里 chip 同口径）
_OVERSOLD_PCT = 0.05      # 超卖：净值大幅低于均线，分位 ≤5%
_OVERBOUGHT_PCT = 0.95    # 超买：净值大幅高于均线，分位 ≥95%

# ---- 业绩预期提醒横幅（A5 下修 + 偏离×预告广度交叉 · 常驻占位） ----
_A5_DROP_PCT = 3.0        # A5: 4周加权 forward-EPS 下修超此%告警。对齐 params
                          # research.earnings.revision.alert_drop_pct 与
                          # tracker/alerts.py _A5_DROP_PCT —— 三处同步
_A5_MIN_COV = 0.40        # A5 覆盖门（对齐 params research.earnings.consensus.min_weight_cov）
_EARN_BEAR_FLOOR = ern.CROSS_BEAR_FLOOR        # 交叉门控常量归一 earnings.py（回放同源）
_EARN_BULL_LABELS = ern.CROSS_BULL_LABELS
_EARN_WINDOW_GRACE_DAYS = 14   # 披露窗扫尾天数（截止后仍算开窗，覆盖迟到披露+数据管道）


def _extreme_rank(snap: dict) -> int | None:
    """当前偏离在「同侧历史极端事件」里的排名（与图里 ▲▼ 第N 标记同源）。不在 top-N 内 → None。"""
    cur = snap.get("nav_dev_cur")
    if _nan(cur):
        return None
    side = "low" if cur < 0 else "high"
    evs = [e for e in (snap.get("nav_extreme_events") or []) if e.get("side") == side]
    if not evs:
        return None
    if side == "low":
        more = sum(1 for e in evs if e["dev"] < cur)   # 比当前更低（更极端）的事件数
    else:
        more = sum(1 for e in evs if e["dev"] > cur)
    rank = more + 1
    return rank if rank <= len(evs) else None


def _partition_extremes(snapshots: dict) -> tuple[list, list]:
    """把快照分成 (超卖, 超买) 两组，各按极端程度排序。data_sufficient=False / 无分位者排除。"""
    oversold, overbought = [], []
    for sym, snap in snapshots.items():
        if not snap.get("data_sufficient", True):
            continue
        p = snap.get("nav_dev_pct")
        if _nan(p):
            continue
        if p <= _OVERSOLD_PCT:
            oversold.append((sym, snap))
        elif p >= _OVERBOUGHT_PCT:
            overbought.append((sym, snap))
    oversold.sort(key=lambda kv: kv[1].get("nav_dev_pct"))                       # 最超卖在前
    overbought.sort(key=lambda kv: kv[1].get("nav_dev_pct"), reverse=True)       # 最超买在前
    return oversold, overbought


def _extreme_banner(snapshots: dict, meta: dict) -> str:
    """顶部横幅：当前处于偏离度极端区(≤5% / ≥95%)的 ETF。超卖绿/超买红（A 股：红=涨/超买、绿=跌/超卖）。
    纯观察（非买卖建议）；每条 ETF 名可点跳转到该 ETF 明细（折叠面板自动展开）。"""
    oversold, overbought = _partition_extremes(snapshots)
    none_s = '<span class="muted">无</span>'

    def _items(rows, side):
        parts = []
        for sym, snap in rows:
            nm = meta.get(sym, {}).get("name", sym)
            cur, p = snap.get("nav_dev_cur"), snap.get("nav_dev_pct")
            rank = _extreme_rank(snap)
            rk = f" · 第{rank}{'低' if side == 'low' else '高'}" if rank else ""
            pos = "c-pos" if (not _nan(cur) and cur > 0) else "c-neg"
            parts.append(
                f'<span class="xb-item"><a href="#{sym}" class="xb-link">{nm}</a> '
                f'<b class="{pos}">{cur:+.1%}</b> <span class="xb-sub">分位 {p:.0%}{rk}</span></span>')
        return "".join(parts)

    if not oversold and not overbought:
        return ('<div class="extreme-banner"><b>🎯 偏离度极端区</b> '
                '<span class="muted">当前无 ETF 处于净值-MA60 偏离度的历史极端分位（≤5% / ≥95%）</span></div>')
    ov_items = _items(oversold, "low") or none_s
    ob_items = _items(overbought, "high") or none_s
    return (
        '<div class="extreme-banner">'
        '<div class="extreme-title">🎯 偏离度极端区 · 净值-MA60 偏离进入自身历史 5%/95% 极端分位 '
        '<span class="xb-note">观察 · 非买卖建议</span></div>'
        '<div class="extreme-grid">'
        f'<div class="extreme-col oversold"><div class="extreme-head">超卖区（分位≤5%，{len(oversold)}）</div>{ov_items}</div>'
        f'<div class="extreme-col overbought"><div class="extreme-head">超买区（分位≥95%，{len(overbought)}）</div>{ob_items}</div>'
        '</div></div>'
    )


_STYLE_CN = {"value": "价值", "growth": "成长", "cyclic": "周期"}
_STYLE_CLS = {"value": "st-value", "growth": "st-growth", "cyclic": "st-cyclic"}


def _num_attr(v, nd=4) -> str:
    """数值 → 排序用 data-* 属性字符串（NaN/None → 空 = 排序时沉底）。"""
    return "" if _nan(v) else f"{v:.{nd}f}"


# 四象限定义：偏离度极端区(≤5% 超卖 / ≥95% 超买) × 筹码方向(增/减；持平不入格)
_QUADRANTS = [
    ("opp", "🟢 机会提醒", "超卖+筹码增", "深跌·有承接", "quad-opp"),
    ("watch", "🔵 关注提醒", "超买+筹码增", "高位·仍增仓", "quad-watch"),
    ("severe", "🔴 严重警告", "超卖+筹码减", "深跌·无承接", "quad-severe"),
    ("risk", "🟠 风险提示", "超买+筹码减", "高位·兑现中", "quad-risk"),
]
_QUAD_KEY = {"opp": "机会", "watch": "关注", "severe": "严重警告", "risk": "风险提示"}
_QUAD_INFO = {k: (t, c) for k, t, c, _s, _cl in _QUADRANTS}   # key → (标题, 条件)


def _quad_of(snap: dict) -> str | None:
    """单 ETF 的象限 key（opp/watch/severe/risk）；非极端区/筹码持平/数据不足 → None。"""
    if not snap.get("data_sufficient", True):
        return None
    p = snap.get("nav_dev_pct")
    if _nan(p):
        return None
    st = (snap.get("chip") or {}).get("state")
    if st not in ("accumulating", "distributing"):
        return None
    if p <= _OVERSOLD_PCT:
        return "opp" if st == "accumulating" else "severe"
    if p >= _OVERBOUGHT_PCT:
        return "watch" if st == "accumulating" else "risk"
    return None


def _partition_quadrant(snapshots: dict) -> dict[str, list]:
    """四象限分组（_quad_of 判定）。各格按偏离极值程度降序。"""
    quads: dict[str, list] = {k: [] for k, *_ in _QUADRANTS}
    for sym, snap in snapshots.items():
        qk = _quad_of(snap)
        if qk:
            quads[qk].append((sym, snap))
    for q in quads.values():
        q.sort(key=lambda kv: abs(kv[1].get("nav_dev_pct") - 0.5), reverse=True)
    return quads


def _quadrant_banner(snapshots: dict, meta: dict) -> str:
    """四象限提醒横幅：偏离度极端 × 筹码方向交叉，2×2 格局。按提醒级别着色
    （绿/蓝/红/琥珀），每条可点跳转明细（折叠面板自动展开）。四格全空 → 整横幅省略。"""
    quads = _partition_quadrant(snapshots)
    if not any(quads.values()):
        return ""

    def _items(rows):
        parts = []
        for sym, snap in rows:
            nm = meta.get(sym, {}).get("name", sym)
            cur, p = snap.get("nav_dev_cur"), snap.get("nav_dev_pct")
            chip = snap.get("chip") or {}
            pos = "c-pos" if (not _nan(cur) and cur > 0) else "c-neg"
            rank = _extreme_rank(snap)
            rk = f" · 第{rank}{'低' if (not _nan(cur) and cur < 0) else '高'}" if rank else ""
            fm = chip.get("flow_main")
            fm_s = f"筹码 {fm:+.1%}" if not _nan(fm) else ""
            flows = chip.get("flows") or {}
            seq_s = (" · ".join(f"{W}日{v:+.1%}" for W, v in sorted(flows.items()))
                     if flows else "")
            seq_line = (f'<span class="xb-seq">{seq_s}</span>' if seq_s else "")
            parts.append(
                f'<span class="xb-item"><a href="#{sym}" class="xb-link">{nm}</a> '
                f'<b class="{pos}">{cur:+.1%}</b> '
                f'<span class="xb-sub">分位 {p:.0%}{rk} · {fm_s}'
                f'（{chip.get("flow_main_window") or "?"}日）</span>{seq_line}</span>')
        return parts and "".join(parts) or '<span class="xb-sub">无</span>'

    cells = "".join(
        f'<div class="quad {cls}"><div class="quad-head">{title}'
        f'<span class="quad-note">{cond} · {scene} · {len(quads[key])}</span></div>'
        f'{_items(quads[key])}</div>'
        for key, title, cond, scene, cls in _QUADRANTS)
    return (
        '<div class="extreme-banner">'
        '<div class="extreme-title">🔔 偏离度 × 筹码动向 · 四象限提醒 '
        '<span class="xb-note">仅列偏离度进入自身历史 5%/95% 极端分位的 ETF · '
        '筹码=份额申赎方向·机构行为代理（主体不可辨） · 观察·非买卖建议</span></div>'
        f'<div class="quad-grid">{cells}</div></div>'
    )


# ---- 业绩预期提醒横幅（A5 一致预期下修 + 偏离度×预告广度交叉 · 2026-08） ----
# 提醒区收「事件」不收「状态」：A5=4周快照差分跳变；交叉=偏离极端分位×预告广度。
# 一致预期 g 水平值是状态（系统性乐观·只有横截面排序有意义，排序键已覆盖）→ 不进横幅。

def _earn_alert_items(snapshots: dict, win: dict) -> dict:
    """业绩预期提醒条目分组（pure）：{"a5": [(sym,snap)..], "risk": [...], "opp": [...]}。

    a5   = E4 修正动量 4周加权下修 < -3% 且覆盖 ≥ 40%（与 tracker/alerts.py A5 同口径）
    risk = 窗口开 且 偏离超买(≥95%) × 预告空广度 ≥ 5%（价格跑前面·成分已有预亏）
    opp  = 窗口开 且 偏离超卖(≤5%) × 预告 label ∈ {业绩高增,业绩改善}（深跌·成分预喜）
    交叉条目另需 earnings_period 落在本窗口（旧窗口数据不提醒）且 label≠数据不足
    （= 已过 earnings_score 覆盖门 0.30/5只）。排序：a5 按下修最深在前，交叉按偏离极值。"""
    a5, risk, opp = [], [], []
    in_window = bool(win.get("open"))
    wperiod = str(win.get("period") or "")
    for sym, snap in snapshots.items():
        if not snap.get("data_sufficient", True):
            continue
        rev = snap.get("revision_w")
        if (isinstance(rev, (int, float)) and not _nan(rev)
                and rev * 100 < -_A5_DROP_PCT
                and (snap.get("revision_cov") or 0) >= _A5_MIN_COV):
            a5.append((sym, snap))
        if not in_window:
            continue
        if str(snap.get("earnings_period") or "") != wperiod:
            continue
        p = snap.get("nav_dev_pct")
        label = snap.get("earnings_label") or ""
        if _nan(p) or label == ern.LABEL_INSUFF:
            continue
        bear = snap.get("earnings_bear")
        if (p >= _OVERBOUGHT_PCT and isinstance(bear, (int, float))
                and not _nan(bear) and bear >= _EARN_BEAR_FLOOR):
            risk.append((sym, snap))
        elif p <= _OVERSOLD_PCT and label in _EARN_BULL_LABELS:
            opp.append((sym, snap))
    a5.sort(key=lambda kv: kv[1].get("revision_w"))
    for rows in (risk, opp):
        rows.sort(key=lambda kv: abs(kv[1].get("nav_dev_pct") - 0.5), reverse=True)
    return {"a5": a5, "risk": risk, "opp": opp}


def _earn_ledger_span(snap: dict, sym: str) -> str:
    """台账单条 span 摘要：首尾日·天数·广度（事实，无涨跌）。"""
    return (f"{snap['first']}~{snap['last']}·{snap['n']}天 {snap['detail']}")


def _earn_ledger_html(history: dict | None, meta: dict) -> str:
    """📖 历史窗口台账（折叠·默认收起）：逐窗口交叉命中区间。只记事实不带后续涨跌。"""
    wins = (history or {}).get("windows") or []
    if not wins:
        return ""
    rows = []
    for w in wins:
        opp, risk = [], []
        for sym, sides in (w.get("hits") or {}).items():
            nm = meta.get(sym, {}).get("name", sym)
            for sp in sides.get("opp") or []:
                opp.append(f'<a href="#{sym}" class="xb-link">{nm}</a> {_earn_ledger_span(sp, sym)}')
            for sp in sides.get("risk") or []:
                risk.append(f'<a href="#{sym}" class="xb-link">{nm}</a> {_earn_ledger_span(sp, sym)}')
        state_s = "进行中" if w.get("state") == "open" else "已关闭"
        def _side(entries):
            return " ｜ ".join(entries) if entries else '<span class="muted">无</span>'
        rows.append(f'<div style="margin:3px 0"><b>{w["label"]}</b>'
                    f'<span class="xb-sub">（{state_s}）</span>：'
                    f'🟢 {_side(opp)} ｜ 🟠 {_side(risk)}</div>')
    note = ('<p class="sub" style="margin:6px 0 0">逐日 point-in-time 重放（预告按公告日截断·无前视）；'
            '命中消失=价格脱离极端区（信号兑现的出口）。只记事实·不含后续涨跌——横幅不做荐股复盘。'
            '更早窗口预告未回填（`--backfill earnings --period` 可扩）；成分用当前快照（月度漂移近似）。</p>')
    return ('<details class="guide" style="margin:6px 0 0">'
            f'<summary>📖 历史窗口台账 · 交叉命中记录（{len(wins)} 窗口 · 点开）</summary>'
            f'<div class="guide-body">{"".join(rows)}{note}</div></details>')


def _earnings_alert_banner(snapshots: dict, meta: dict, as_of: str,
                           history: dict | None = None) -> str:
    """📈 业绩预期提醒横幅（常驻占位）：A5 一致预期下修 + 偏离度×预告广度交叉。

    两行各有占位态——A5 行：冷启动「累积中N/4」/ 激活后无命中「无下修告警」；
    交叉行：披露窗口门控（窗口外只报下窗口时点 + 上窗口命中摘要；预告数据季度一跳，
    非披露季钉在顶部=壁纸化）。history（scripts 组装的窗口回放）→ 底部折叠台账。
    只用现成字段/入参，不做新数据计算；纯观察·非买卖建议。"""
    try:
        now = datetime.strptime(str(as_of)[:10], "%Y-%m-%d")
    except ValueError:
        now = datetime.now()
    win = ern.disclosure_window(now, grace_days=_EARN_WINDOW_GRACE_DAYS)
    items = _earn_alert_items(snapshots, win)

    def _a5_entry(sym: str, snap: dict) -> str:
        nm = meta.get(sym, {}).get("name", sym)
        cov = snap.get("revision_cov") or 0
        return (f'<span class="xb-item"><a href="#{sym}" class="xb-link">{nm}</a> '
                f'<b class="c-neg">下修 {snap["revision_w"]:+.1%}</b> '
                f'<span class="xb-sub">下调{snap.get("revision_dn", 0)}家/'
                f'上调{snap.get("revision_up", 0)}家 · 覆盖{cov:.0%}'
                f' · 4周（{snap.get("revision_span", "")}）</span></span>')

    def _x_entry(sym: str, snap: dict, side: str) -> str:
        nm = meta.get(sym, {}).get("name", sym)
        cur, p = snap.get("nav_dev_cur"), snap.get("nav_dev_pct")
        pos = "c-pos" if (not _nan(cur) and cur > 0) else "c-neg"
        if side == "risk":
            x = f'空广度 {snap["earnings_bear"]:.0%}'
            if not _nan(snap.get("earnings_yoy")):
                x += f'（归母YoY {snap["earnings_yoy"]:+.0%}）'
        else:
            x = (f'{snap["earnings_label"]} · 多{snap.get("earnings_bull", 0):.0%}/'
                 f'空{snap.get("earnings_bear", 0):.0%}')
        return (f'<span class="xb-item"><a href="#{sym}" class="xb-link">{nm}</a> '
                f'<b class="{pos}">{cur:+.1%}</b> '
                f'<span class="xb-sub">分位 {p:.0%} × {x}</span></span>')

    # --- A5 行（常驻）：条目 / 冷启动 / 激活无命中 / 无数据 ---
    if items["a5"]:
        a5_html = "".join(_a5_entry(s, sn) for s, sn in items["a5"])
    else:
        cold = next((sn.get("revision_status") for sn in snapshots.values()
                     if sn.get("revision_status")), None)
        m = re.search(r"\((\d+)/(\d+)\)", cold or "")
        if m:
            left = max(int(m.group(2)) - int(m.group(1)), 0)
            a5_html = (f'<span class="xb-sub muted">修正动量冷启动 · 快照累积中 '
                       f'{m.group(1)}/{m.group(2)}（约还需 {left} 周，每周 --backfill '
                       f'consensus 攒一份后自动激活）</span>')
        elif any(isinstance(sn.get("revision_w"), (int, float))
                 and not _nan(sn.get("revision_w")) for sn in snapshots.values()):
            a5_html = (f'<span class="xb-sub muted">已激活 · 当前无下修告警'
                       f'（阈值 4周加权 &lt; -{_A5_DROP_PCT:.0f}% 且覆盖 ≥ '
                       f'{_A5_MIN_COV:.0%}；下调信息量大于上调，只看下修侧）</span>')
        else:
            a5_html = ('<span class="xb-sub muted">无修正动量数据'
                       '（需 --backfill consensus 周度快照）</span>')

    # --- 交叉行（窗口门控）：条目 / 窗口内无命中 / 窗口已关闭 ---
    if win["open"]:
        x_head = (f'偏离 × 预告广度交叉（{win["label"]} · {win["window_note"]}）')
        if items["risk"] or items["opp"]:
            rk = ("".join(_x_entry(s, sn, "risk") for s, sn in items["risk"])
                  or '<span class="xb-sub muted">无</span>')
            op = ("".join(_x_entry(s, sn, "opp") for s, sn in items["opp"])
                  or '<span class="xb-sub muted">无</span>')
            x_html = (f'<div style="margin:2px 0"><span class="xb-sub">🟠 超买×预亏：'
                      f'</span>{rk}</div>'
                      f'<div style="margin:2px 0"><span class="xb-sub">🟢 超卖×预喜：'
                      f'</span>{op}</div>')
        else:
            x_html = ('<span class="xb-sub muted">本窗口暂无交叉命中'
                      '（超买分位≥95%×空广度≥5% / 超卖分位≤5%×预喜label 均无）</span>')
    else:
        x_head = '偏离 × 预告广度交叉'
        # 上窗口摘要（台账最新关闭窗）：🟢/🟠 命中只数 + top3 天数——占位行也承载信息
        prev_s = ""
        prevs = [w for w in ((history or {}).get("windows") or [])
                 if w.get("state") == "closed" and w.get("hits")]
        if prevs:
            w0 = prevs[0]
            opp_n = sum(1 for s in w0["hits"].values() if s.get("opp"))
            rk_n = sum(1 for s in w0["hits"].values() if s.get("risk"))
            tops = sorted(((sym, sp) for sym, s in w0["hits"].items()
                           for sp in (s.get("opp") or [])), key=lambda t: -t[1]["n"])[:3]
            tops_s = ("·" + "·".join(
                f"{meta.get(sym, {}).get('name', sym)}{sp['n']}天" for sym, sp in tops)
                      if tops else "")
            prev_s = (f' · 上窗口({w0["label"]})命中：🟢{opp_n}只{tops_s} · 🟠{rk_n}只')
        x_html = (f'<span class="xb-sub muted">{win["label"]}窗口已关闭'
                  f'（{win["window_note"]}）{prev_s} · 下窗口：{win["next_label"]} '
                  f'{win["next_open"]} 开窗 —— 窗口外不出条目'
                  '（预告数据季度一跳·非披露季钉顶部=壁纸化）</span>')

    return (
        '<div class="extreme-banner">'
        '<div class="extreme-title">📈 业绩预期提醒 · A5 一致预期下修 + 偏离度 × 预告广度交叉 '
        '<span class="xb-note">事件进横幅·状态留表格（预期g水平值不进） · 观察 · 非买卖建议 · '
        '信息层不喂引擎</span></div>'
        f'<div class="xb-list"><b>A5 一致预期下修</b>（E4 修正动量·4周快照差分）：{a5_html}</div>'
        f'<div class="xb-list"><b>{x_head}</b>：{x_html}</div>'
        + _earn_ledger_html(history, meta)
        + '</div>'
    )


# ---------------------------------------------------------------------------
# 板块资金流向 section（份额视角 · research/flow.py 算好的 payload 驱动，本模块不算数）
# ---------------------------------------------------------------------------

# 行业组色环（25 组行业级分组；浅色底可辨；深色主题下线条色不变，仅纸底/轴色切换）
_FLOW_COLORS = ["#2563eb", "#ea580c", "#16a34a", "#9333ea", "#0891b2", "#dc2626", "#d97706",
                "#4f46e5", "#65a30d", "#db2777", "#0d9488", "#7c3aed", "#ca8a04", "#059669",
                "#b91c1c", "#1d4ed8", "#f472b6", "#14b8a6", "#8b5cf6", "#a16207", "#4d7c0f",
                "#9f1239", "#0f766e", "#6d28d9", "#be123c"]

# 25 条线的 unified 悬浮框（全组横截面对比）：模板只给数值（unified 自动带
# 顶部日期 + 每行彩色组名——模板里再写日期/组名会每行重复、框高翻倍被裁），
# 字号压 9 → 单行×25 ≈ 410px；图高 600（有效绘图区 ≈450px）留足余量
_FLOW_HEIGHT_LINES = 600


def flow_lines_figure(flow: dict) -> go.Figure:
    """组级 W 日滚动净流入时序（亿元 = Δ份额×当日净值）。

    updatemenus 一组 6 态按钮 = 窗口(5/20/60日) × 单位(绝对亿元/占组规模%) 的完整
    状态切换——plotly 按钮无状态，每个按钮必须携带全部目标 y 数组与轴/图标题，
    做不了两个独立维度各自切。% 模式防小组（红利 vs 医药）被压扁；流 signed 不用
    对数轴；零轴常显。窗口数据来自 payload 的 rolls（无则退化单窗口×2态）。"""
    aum = flow.get("aum") or {}
    default_w = flow.get("window", 20)
    rolls = flow.get("rolls") or {default_w: flow["group_roll"]}
    windows = sorted(rolls)
    roll0 = rolls[windows[0]]
    x = pd.to_datetime(roll0.index)
    fig = go.Figure()
    for i, g in enumerate(roll0.columns):
        fig.add_trace(go.Scatter(
            x=x, y=roll0[g].astype(float), name=str(g),
            line=dict(color=_FLOW_COLORS[i % len(_FLOW_COLORS)], width=1.4),
            hovertemplate="%{fullData.name} %{y:+.1f}<extra></extra>"))

    # 预生成全部 (窗口, 单位) 组合：按钮 label / 目标 y 数组 / 轴标题 / 图标题。
    # % 态分母 = 逐日组规模（aum_series，时间变化）——单组曲线形状与亿态也不同；
    # 无 aum_series（旧 payload）退化用最新规模常数分母
    aum_s = flow.get("aum_series")
    has_aum_series = aum_s is not None and len(aum_s)
    combos = []
    for w in windows:
        roll = rolls[w]
        abs_ys = [roll[g].astype(float).tolist() for g in roll.columns]
        if has_aum_series:
            al = aum_s.reindex(roll.index)
            pct_df = (roll / al * 100.0).where(al > 0)
            pct_ys = [pct_df[g].tolist() for g in roll.columns]
        else:
            pct_ys = [[(v / aum[g] * 100.0) if (aum.get(g) or 0) > 0 else None
                       for v in roll[g].astype(float)] for g in roll.columns]
        combos.append((f"{w}日", abs_ys, "净流入(亿)", "",
                       f"组级净流入 · {w}日滚动（亿元 = Δ份额×当日净值 · 份额=净申赎）"))
        combos.append((f"{w}日%", pct_ys, "净流入(% 当日组规模)", "%",
                       f"组级净流入强度 · {w}日（占当日组规模% · 逐日分母）"))
    active = 2 * windows.index(default_w)      # 默认态 = 主窗口 × 绝对亿元

    fig.update_layout(**_base_layout(
        f"组级净流入 · {default_w}日滚动（亿元 = Δ份额×当日净值 · 份额=净申赎）",
        _FLOW_HEIGHT_LINES))
    # unified 悬浮 = 同一时点全组横截面对比；模板=「组名 数值」同行一行高
    # （此 plotly 版本 unified 行只带色点不自动带组名文本；日期只在框顶——
    # 模板里写日期会每行重复、行高翻倍被裁，框顶格式由 xaxis.hoverformat 控）
    fig.update_layout(hoverlabel=dict(font=dict(size=9)))
    # 图内 legend 移除：tile 的组 chips 即图例（带色点+点击开关+双击独显，见 _PAGE_JS）
    # —— 省掉 25 项换行图例的 2-3 行空间，绘图区更高、悬浮框也更从容
    fig.update_layout(showlegend=False, margin=dict(t=64, l=54, r=54, b=28))
    # 默认视图 1 年（此 plotly 版本 rangeselector 无 active 属性 → 显式设 x 初始
    # range；按钮高亮不跟随初始 range 属版本限制，全历史走底部滑块/「全部」按钮）
    x0 = x[-1] - pd.DateOffset(years=1)
    fig.update_xaxes(type="date", hoverformat="%Y-%m-%d",
                     rangeselector=_RANGE_BUTTONS,
                     rangeslider=dict(visible=True, thickness=0.02),
                     range=[x0, x[-1]])
    fig.update_yaxes(title_text="净流入(亿)", zeroline=True, zerolinewidth=1, gridcolor=C_GRID)
    # 切换按钮放 rangeselector 右侧、避开换行 legend；active=默认态（主窗口·亿元）
    fig.update_layout(updatemenus=[dict(
        type="buttons", direction="right", x=0.36, xanchor="left", y=1.24, yanchor="bottom",
        pad=dict(t=0, b=0), active=active, buttons=[
            dict(label=lbl, method="update",
                 args=[{"y": ys}, {"yaxis.title.text": ytitle, "yaxis.ticksuffix": tsuf,
                                   "title.text": ftitle}])
            for lbl, ys, ytitle, tsuf, ftitle in combos
        ])])
    return fig


def flow_events_strip_figure(flow: dict) -> go.Figure:
    """申赎异动时间条带（近1月）：x=日期，申购/赎回分上下双轨，点大小∝金额，
    同日多事件在轨内堆叠。悬停=全信息；点击点跳该 ETF（_PAGE_JS plotly_click）。
    事件的时间聚集（如某周连环申购潮）在此一目了然——文字台账看不到这层。"""
    events = flow.get("events") or []
    last = pd.to_datetime(flow.get("last_date") or events[0]["date"])
    x0 = last - pd.Timedelta(days=32)
    mx = max([abs(e["flow_yi"]) for e in events] + [1.0])

    def lane(evs, base):
        xs, ys, sizes, cds = [], [], [], []
        per_day: dict[str, int] = {}
        for e in evs:
            k = per_day.get(e["date"], 0)
            per_day[e["date"]] = k + 1
            xs.append(e["date"])
            ys.append(base * (1.0 + 0.28 * k))          # 同日堆叠
            sizes.append(max(abs(e["flow_yi"]), 0.3))
            cds.append([e.get("name", e["symbol"]), e["symbol"],
                        e["flow_yi"], e["pct"], e["pctile"]])
        return xs, ys, sizes, cds

    fig = go.Figure()
    for evs, base, name, color in (([e for e in events if e["side"] == "in"], 1, "净申购", "#dc2626"),
                                   ([e for e in events if e["side"] == "out"], -1, "净赎回", "#16a34a")):
        xs, ys, sizes, cds = lane(evs, base)
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers", name=name,
            marker=dict(color=color, size=sizes, sizemode="area",
                        sizeref=mx / (22 ** 2), sizemin=6,
                        line=dict(color="white", width=1)),
            customdata=cds,
            hovertemplate=("%{customdata[0]}<br>%{x|%Y-%m-%d} · %{customdata[2]:+.1f}亿"
                           "<br>日增减 %{customdata[3]:+.1%} · 方向内分位 %{customdata[4]:.1%}"
                           "<extra></extra>")))
    fig.update_layout(height=230, template=_TEMPLATE, hovermode="closest",
                      showlegend=True, legend=dict(orientation="h", yanchor="bottom",
                                                   y=1.02, xanchor="right", x=1,
                                                   font=dict(size=10)),
                      margin=dict(l=10, r=10, t=26, b=10))
    fig.update_xaxes(type="date", range=[x0, last + pd.Timedelta(days=1)],
                     tickformat="%m-%d", nticks=8, gridcolor=C_GRID,
                     rangeselector=None, rangeslider=dict(visible=False))
    fig.update_yaxes(visible=False, zeroline=True, zerolinewidth=1,
                     zerolinecolor=C_GRID)               # 零线=时间轴基线
    return fig


def _flow_events_banner(flow: dict, meta: dict, top_n: int = 8) -> str:
    """📡 申赎异动横幅：最近大额申赎事件台账（|日增减%|≥自身历史分位 且 ≥金额
    地板），点条目跳该 ETF 日度净申赎图。近1月扫描、最新在前（新事件顶旧事件），
    最新交易日事件加「最新」徽标。无命中也常驻（安静占位）。纯观察·非买卖建议。"""
    events = flow.get("events") or []
    last_date = flow.get("last_date")
    if not events:
        body = ('<span class="xb-sub muted">近一个月无大额申赎命中 —— 安静窗口属正常。'
                '判定：日增减% ≥ 自身<b>方向</b>历史99%分位（申购日比申购日·赎回日比赎回日）<b>且</b> |净申赎额| ≥ 1亿'
                '（申购日比申购日·赎回日比赎回日·某方向样本<30日退双向；阈值可调 params.research.flow.alert）</span>')
    else:
        shown = events[:top_n]
        items = []
        for ev in shown:
            nm = meta.get(ev["symbol"], {}).get("name", ev["symbol"])
            cls = "flow-pos" if ev["flow_yi"] > 0 else "flow-neg"
            arrow = "净申购" if ev["flow_yi"] > 0 else "净赎回"
            badge = ('<b class="flow-ev-today">最新</b> ' if ev["date"] == last_date else "")
            items.append(
                f'<span class="xb-item"><a href="#{ev["symbol"]}" class="xb-link">{nm}({ev["symbol"]})</a> '
                f'{badge}<b class="{cls}">{arrow} {ev["flow_yi"]:+.1f}亿</b> '
                f'<span class="xb-sub">{ev["date"]} · 日增减 {ev["pct"]:+.1%} · '
                f'{("申购向" if ev["side"] == "in" else "赎回向") if ev.get("pctile_kind") == "side" else "双向"}'
                f'分位 {ev["pctile"]:.1%}</span></span>')
        more = (f'<span class="xb-sub">…另有 {len(events) - top_n} 条</span>'
                if len(events) > top_n else "")
        body = "".join(items) + more
    return (
        '<div class="extreme-banner">'
        '<div class="extreme-title">📡 申赎异动 · 最近大额申赎事件（近1月） '
        '<span class="xb-note">时间条带：上轨=净申购(红)/下轨=净赎回(绿)·点大小∝金额·点悬停看详情·**点击点跳该ETF** · '
        '日增减%≥自身方向历史99%分位 且 ≥1亿 · 流入≠看好（A股常见逆势申购）· 纯观察·非买卖建议</span></div>'
        + ('<div class="chart-block"><div class="lazy-chart" data-sym="__flow" data-idx="1" '
           'style="min-height:230px"></div></div>' if events else "")
        + f'<div class="xb-list">{body}</div></div>')


def _flow_section(flow: dict) -> str:
    """板块资金流向 section HTML：增量vs存量 tile + 两个 lazy-chart 占位（CHARTS['__flow']）。"""
    st = flow.get("state") or {}
    W = flow.get("window", 20)
    label, key = st.get("label", "数据不足"), st.get("label_key", "insufficient")
    net, gross = st.get("pool_net_yi"), st.get("pool_gross_yi")
    inten, br = st.get("intensity"), st.get("breadth")
    lbl_cls = {"broad_in": "flow-lbl-in", "focused_in": "flow-lbl-in",
               "rotation": "flow-lbl-rot", "net_out": "flow-lbl-out"}.get(key, "flow-lbl-quiet")
    net_cls = "flow-pos" if (not _nan(net) and net > 0) else "flow-neg"
    net_s = f"{net:+.0f}亿" if not _nan(net) else "NA"
    gross_s = f"{gross:.0f}亿" if not _nan(gross) else "NA"
    inten_s = f"{inten:.2f}" if not _nan(inten) else "NA"
    br_s = f"{br:.0%}" if not _nan(br) else "NA"
    members = flow.get("members") or {}

    def _members_title(g: str) -> str:
        ms = members.get(g) or []
        return ("成员：" + "、".join(f"{nm}({s})" for s, nm in ms)) if ms else ""

    groups_order = flow.get("groups") or []

    def _group_color(g: str) -> str:
        return _FLOW_COLORS[groups_order.index(g) % len(_FLOW_COLORS)] if g in groups_order else "#94a3b8"

    chips = "".join(
        f'<span class="flow-chip {"flow-pos" if (not _nan(gf.get("flow_yi")) and gf["flow_yi"] > 0) else "flow-neg"} on" '
        f'data-flow-group="{gf["group"]}" title="{_members_title(gf["group"])}" '
        f'onclick="flowChipClick(\'{gf["group"]}\')" '
        f'ondblclick="flowChipSolo(\'{gf["group"]}\')">'
        f'<i class="flow-dot" style="background:{_group_color(gf["group"])}"></i>'
        f'{gf["group"]} {gf["flow_yi"]:+.1f}亿</span>'
        for gf in (st.get("group_flows") or []) if not _nan(gf.get("flow_yi")))
    chips += ('<span class="flow-chip flow-chip-all" onclick="flowChipAll()" '
              'title="恢复全部组显示">↺ 全部</span>')
    groups_lines = "".join(
        f'<div><b>{g}</b><span class="muted">（{len(ms)}只）</span>：{"、".join(nm for _s, nm in ms)}</div>'
        for g, ms in members.items() if ms)
    groups_block = (f'<details class="flow-groups"><summary>🏷️ {len(members)} 行业组构成'
                    f'（点击展开 · 悬停上方组 chip 可看成员代码）</summary>'
                    f'<div class="flow-groups-body">{groups_lines}</div></details>'
                    if groups_lines else "")
    excl = flow.get("excluded") or []
    excl_s = (f' · ⚠️ 未计入{len(excl)}只无份额历史：'
              + "、".join(f"{nm}({s})" for s, nm in excl[:6])
              + ("等" if len(excl) > 6 else "")) if excl else ""
    return (
        '<h3>💰 板块资金流向（份额视角）</h3>'
        '<p class="sub">ETF份额=净申赎（配置盘的脚印·比主力资金流干净）· 金额=Δ份额×当日净值 · '
        '本节颜色：<b class="flow-pos">红=净流入</b>/<b class="flow-neg">绿=净流出</b>'
        '（A股惯例·与排名表筹码列相反）· 组=下方「行业组构成」'
        f'{excl_s}</p>'
        '<div class="summary-box flow-tile">'
        f'<div class="flow-lbl {lbl_cls}">{label}</div>'
        f'<div class="flow-stats">近{W}日全池净流入 <b class="{net_cls}">{net_s}</b>'
        f' · 毛额(Σ|组净流入|) {gross_s} · 轮动强度(净/毛) {inten_s} · 流入广度 {br_s}'
        '<span class="sub2">（强度 0=纯对冲轮动 1=全同向 · 温度计非开关·标签只是辅助）</span></div>'
        f'<div class="flow-chips">{chips}</div>'
        '</div>'
        f'{groups_block}'
        '<div class="chart-block"><div class="lazy-chart" data-sym="__flow" data-idx="0" '
        f'style="min-height:{_FLOW_HEIGHT_LINES}px"></div></div>'
        '<p class="sub">⌨ 图上按钮：<b>N日</b> = 过去 N 个交易日的净流入<b>合计</b>'
        '（5日≈周内爆发·最灵敏 ｜ 20日≈月度节奏·与上方 tile 同口径 ｜ 60日≈季度趋势·最钝）；'
        '带 <b>%</b> = 占<b>当日自身规模</b>的百分比（<b>逐日分母</b>：单组时曲线形状也与亿态不同，'
        '规模小的时期同额流入占比更大；大小组可横向比），不带 = 绝对亿元。'
        '切换只换窗口/口径不改数据；滚动值对拐点的反应约滞后半个窗口。<br>'
        '🎛 组筛选：<b>单击</b>上方组 chip = 该组线开/关（灰=已隐藏）· <b>双击</b> = 仅看该组'
        '（再双击复位）· <b>↺ 全部</b> = 恢复显示。chip 色点=线的颜色。</p>')


def _ranking_rows(snapshots: dict, meta: dict, style_filter: str | None = None) -> str:
    rows = sorted(snapshots.items(), key=lambda kv: _extremeness(kv[1]), reverse=True)
    out = ""
    for sym, snap in rows:
        if style_filter and snap.get("style", "growth") != style_filter:
            continue
        nm = meta.get(sym, {}).get("name", sym)
        aum = snap.get("aum_yi")
        aum_html = (f"<br><span class='sub2 faint'>规模 {aum:.0f}亿</span>"
                    if not _nan(aum) else "")
        style = snap.get("style", "growth")
        style_cn = _STYLE_CN.get(style, style)
        style_cls = _STYLE_CLS.get(style, "")
        # 行业组（板块资金流向的聚合口径）副行——与流向 section 互相参照
        group = meta.get(sym, {}).get("group") or ""
        group_html = f"<br><span class='sub2 muted'>{group}</span>" if group else ""

        # 净值MA偏离 cell：当前偏离%（着色）+ 分位 + 极值区 chip
        cur = snap.get("nav_dev_cur")
        pct = snap.get("nav_dev_pct")
        dev_color = "c-pos" if (not _nan(cur) and cur > 0) else ("c-neg" if not _nan(cur) else "faint")
        chip = ""
        if not _nan(pct):
            if pct <= 0.05:
                chip = " ·超卖区"
            elif pct >= 0.95:
                chip = " ·超买区"
        cur_s = f"{cur:+.1%}" if not _nan(cur) else "NA"
        pct_s = f"分位 {pct:.0%}" if not _nan(pct) else ""
        dev_cell = (f"<td class='c bold {dev_color}'>{cur_s}"
                    f"<br><span class='sub2 muted'>{pct_s}<b>{chip}</b></span></td>")

        # 份额/净值剪刀差 cell
        sc = snap.get("scissor") or {}
        if sc.get("detected"):
            sd, nd = sc["share_drift"], sc["nav_drift"]
            arrow = "份↑净↓" if sc["direction"] == "share_up_nav_down" else "份↓净↑"
            sc_cell = (f"<td class='c sc-txt bold'>{arrow}"
                       f"<br><span class='sub2 muted'>份{sd:+.0%}/净{nd:+.0%}<br>{sc['window']}日</span></td>")
        else:
            sc_cell = "<td class='c'><span class='ghost'>—</span></td>"

        to = snap.get("turnover_5d_yi")
        to_html = f"{to:.1f}亿" if not _nan(to) else "—"
        # 筹码方向 cell（份额申赎 · 机构行为代理）：主窗口值 + 方向 + 多窗口带标签直接展示
        chip = snap.get("chip") or {}
        if chip.get("data_sufficient") and not _nan(chip.get("flow_main")):
            st_cn = {"accumulating": "增", "distributing": "减", "flat": "平"}.get(chip["state"], "平")
            st_cls = {"accumulating": "chip-up", "distributing": "chip-dn"}.get(chip["state"], "")
            flows = chip.get("flows") or {}
            seq = " · ".join(f"{W}日{flows[W]:+.1%}" for W in sorted(flows))
            chip_cell = (f"<td class='c bold'>{chip['flow_main']:+.1%}"
                         f"<br><span class='sub2 muted'>近{chip['flow_main_window']}日 "
                         f"<b class='{st_cls}'>{st_cn}</b>"
                         f"<br><span class='chip-seq'>{seq}</span></span></td>")
        else:
            chip_cell = "<td class='c'><span class='ghost'>—</span></td>"

        # 最新日净申赎 cell（当日脉搏 · 与筹码列的平滑中期票互补）：
        # 主值=金额亿（Δ份额×当日unit_nav·拆分已调整），副行=日增减% + 自身全历史
        # 带符号分位；≥95% 红=罕见大额净申购 / ≤5% 绿=罕见大额净赎回（A股资金流
        # 语义·与筹码列相反，读图说明④注明）。历史不足250日 → 诚实显示 —。
        dfl = snap.get("daily_flow") or {}
        fy = dfl.get("flow_yi")
        if dfl.get("data_sufficient") and not _nan(dfl.get("pct")):
            fy_html = f"{fy:+.2f}亿" if not _nan(fy) else "—"
            pt = dfl["pctile"]
            # 分位标签：按方向各自统计（申购向/赎回向·方向内强度温度计），某方向
            # 样本太少退双向（绝对值·与📡横幅同式）——两个口径回答不同问题，
            # 不同名不同义，防「对不上」的困惑（2026-08 两轮口径修正后定稿）
            if dfl.get("pctile_kind") == "side":
                lbl = "申购向分位" if dfl["pct"] >= 0 else "赎回向分位"
            else:
                lbl = "双向分位"
            hot = ("flow-hot" if dfl["pct"] > 0 else "flow-cold") if pt >= 0.95 else ""
            pt_html = f"{lbl}<b class='{hot}'>{_fmt_pctile(pt)}</b>" if hot else f"{lbl}{_fmt_pctile(pt)}"
            n_side = dfl.get("n_side")
            flow_cell = (f"<td class='c bold' title=\"{dfl.get('date') or ''} · 最新日净申赎"
                         f" · {lbl[:-2]}样本{n_side}日\">"
                         f"{fy_html}"
                         f"<br><span class='sub2 muted'>{dfl['pct']:+.1f}% · {pt_html}</span></td>")
        else:
            flow_cell = "<td class='c'><span class='ghost'>—</span></td>"
        out += (
            f"<tr data-name=\"{nm}\" data-style=\"{style}\" "
            f"data-dev=\"{_num_attr(cur)}\" data-pct=\"{_num_attr(pct)}\" "
            f"data-aum=\"{_num_attr(aum, 1)}\" data-turnover=\"{_num_attr(to, 1)}\" "
            f"data-chip=\"{_num_attr((snap.get('chip') or {}).get('flow_main'))}\" "
            f"data-flow=\"{_num_attr(fy)}\" "
            f"data-earn=\"{_num_attr(snap.get('consensus_g'))}\">"
            f"<td><b><a href='#{sym}' class='etf-link'>{nm}</a></b>"
            f"<br><span class='sub2 muted'>{sym}</span>{aum_html}</td>"
            f"<td class='c bold {style_cls}'>{style_cn}{group_html}</td>"
            f"{dev_cell}{sc_cell}{chip_cell}{flow_cell}"
            f"{_earnings_cell(snap)}"
            f"<td class='c text2'>{to_html}</td></tr>"
        )
    return out


_RING_CN = {"forecast": "预告", "express": "快报", "actual": "正式报"}


def _chain_block(snap: dict) -> str:
    """业绩预期链状态条 (E3): 预告→快报→正式报 各环覆盖权重+最新披露日+时效
    + 预告广度 + 快报落点。纯 HTML(无图), 明细面板 summary 之后。空链 → ''。"""
    ch = snap.get("chain") or {}
    rings = ch.get("rings") or {}
    if not rings:
        return ""
    plabel = ern.period_label(snap.get("chain_period"))

    def _cell(ring: str) -> str:
        w = rings.get(ring, 0.0)
        cn = _RING_CN[ring]
        if not w:
            return (f"<div class='chain-cell ghost'><b>{cn} —</b>"
                    f"<br><span class='sub2'>未披露</span></div>")
        n = (ch.get("n") or {}).get(ring, 0)
        d = (ch.get("days") or {}).get(ring)
        fresh = "" if (d is None or d > 45) else " fresh-ring"
        d_s = f"{d}天前" if d is not None else ""
        return (f"<div class='chain-cell{fresh}'><b>{cn} {w:.0%}</b>"
                f"<br><span class='sub2'>{n}家 · {d_s}</span></div>")

    bull, bear = ch.get("bull_ratio"), ch.get("bear_ratio")
    bb = (f"多{bull:.0%}/空{bear:.0%}" if isinstance(bull, (int, float)) and not _nan(bull) else "—")
    ec = ch.get("express_check") or {}
    def _pct(x):
        return f"{x:.0%}" if isinstance(x, (int, float)) and not _nan(x) else "—"
    ec_s = (f"落点 保守{_pct(ec.get('conservative'))}/命中{_pct(ec.get('hit'))}"
            f"/落空{_pct(ec.get('optimistic'))}")
    return (f"<div class='chain-strip'><span class='chain-title'>⛓ 业绩预期链 · {plabel}</span>"
            f"<div class='chain-cells'>{_cell('forecast')}{_cell('express')}{_cell('actual')}"
            f"<div class='chain-cell'><b>广度</b><br><span class='sub2'>{bb}</span></div>"
            f"<div class='chain-cell'><b>快报vs预告</b><br><span class='sub2'>{ec_s}</span></div>"
            f"</div></div>")


def _detail_chips(snap: dict) -> str:
    """折叠面板 summary 上的摘要 chips：类型 / 偏离+分位+极端区 / 剪刀差 / 业绩 / 规模。
    收起状态即可横向扫全池，不用展开。"""
    chips = []
    style = snap.get("style", "growth")
    chips.append(f'<span class="chip {_STYLE_CLS.get(style, "")}">{_STYLE_CN.get(style, style)}</span>')
    cur, pct = snap.get("nav_dev_cur"), snap.get("nav_dev_pct")
    if not _nan(cur):
        pos = "dev-pos" if cur > 0 else "dev-neg"
        txt = f"偏离 {cur:+.1%}"
        if not _nan(pct):
            txt += f" · 分位 {pct:.0%}"
            if pct <= 0.05:
                txt += " · 超卖区"
            elif pct >= 0.95:
                txt += " · 超买区"
        chips.append(f'<span class="chip {pos}">{txt}</span>')
    sc = snap.get("scissor") or {}
    if sc.get("detected"):
        sd, nd = sc["share_drift"], sc["nav_drift"]
        arrow = "✂ 份↑净↓" if sc["direction"] == "share_up_nav_down" else "✂ 份↓净↑"
        chips.append(f'<span class="chip sc-txt">{arrow} 份{sd:+.0%}/净{nd:+.0%}·{sc["window"]}日</span>')
    qk = _quad_of(snap)
    if qk:
        title, cond = _QUAD_INFO[qk]
        chips.append(f'<span class="chip quad-{qk}">{title}·{cond}</span>')
    label = snap.get("earnings_label")
    if label:
        chips.append(f'<span class="chip {_EARN_CLASS.get(label, "en-flat")}">{label}</span>')
    g = snap.get("consensus_g")
    if isinstance(g, (int, float)) and not _nan(g):
        clab = str(snap.get("consensus_label", ""))
        chips.append(f'<span class="chip {_CONS_CLASS.get(clab, "en-flat")}">'
                     f'预期g {g:+.0%}·{clab or "—"}·覆盖{snap.get("consensus_cov", 0):.0%}</span>')
    aum = snap.get("aum_yi")
    if not _nan(aum):
        chips.append(f'<span class="chip">规模 {aum:.0f}亿</span>')
    return "".join(chips)


def flow_daily_figure(label: str, shares_df, nav_df):
    """日度净申赎（事件级）：柱 = 净申赎额（亿元 = Δ份额 × 当日单位净值·拆分已调整，
    红=净申购/绿=净赎回），线 = 份额日增减%（右轴，Δ份额/前日份额——分母随时间
    变化，与柱不同形：柱看这笔申赎的金额大小，线看它相对基金当时体量的大小）。

    份额必须用拆分前复权口径（timing.split_adjusted_shares）——原始份额跨拆分日
    会画出一根假 +100%/数百亿的巨柱。无份额历史 → None（明细图组自动缩短）。"""
    adj, _events = tm.split_adjusted_shares(shares_df, nav_df)
    if adj is None or len(adj.dropna()) < 3:
        return None
    nav = pd.to_numeric(nav_df["unit_nav"], errors="coerce").dropna()
    nav_al = nav.reindex(adj.index)
    flow_yi = (adj.diff() * nav_al) / 1e8
    pct = adj.pct_change() * 100.0
    bar_colors = [("#dc2626" if (v == v and v >= 0) else "#16a34a") for v in flow_yi.values]
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(
        x=adj.index, y=flow_yi, name="净申赎额(亿)",
        marker_color=bar_colors,
        hovertemplate="%{x|%Y-%m-%d}<br>净申赎: %{y:+.2f}亿<extra></extra>"), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=adj.index, y=pct, name="份额日增减%",
        line=dict(color="#2563eb", width=1.2),
        hovertemplate="%{x|%Y-%m-%d}<br>日增减: %{y:+.2f}%<extra></extra>"), secondary_y=True)
    fig.update_layout(**_base_layout(f"{label} · 日度净申赎（柱:亿元=Δ份额×当日净值 · 线:份额日增减% · 拆分已调整）",
                                     CHART_HEIGHT))
    fig.update_xaxes(type="date", rangeselector=_RANGE_BUTTONS,
                     rangeslider=dict(visible=True, thickness=0.02))
    fig.update_yaxes(title_text="净申赎额(亿)", zeroline=True, zerolinewidth=1,
                     gridcolor=C_GRID, secondary_y=False)
    fig.update_yaxes(title_text="日增减%", zeroline=False, gridcolor="rgba(0,0,0,0)",
                     secondary_y=True)
    _x_range_default(fig)                     # 初始视图 = 最近 3 年
    return fig


def _fmt_pctile(pt: float) -> str:
    """分位显示：着色区(≥95%·与📡横幅交叉对照的行)与低端(≤0.5%)用一位小数——
    与横幅 :.1% 逐字一致且 :.0% 会把 99.3%/99.6% 圆成「99%/100%」抹掉差异；
    其余整数保持列宽（2026-08 两次修正：圆整假象 + 列/横幅口径分裂）。"""
    return f"{pt:.1%}" if (pt >= 0.95 or pt <= 0.005) else f"{pt:.0%}"


def _etf_figs(sym: str, snap: dict, meta: dict, series_map: dict, ma_period: int) -> list:
    """每 ETF 明细图组：份额 vs 净值（剪刀差叠加）+ 净值-MA 偏离度（极值标记 + 分位）
    + 日度净申赎（事件级：柱=亿元·线=%）。"""
    nm = meta.get(sym, {}).get("name", sym)
    aum = snap.get("aum_yi")
    label = f"{nm}({sym})" + (f" · 规模{aum:.0f}亿" if not _nan(aum) else "")
    sm = series_map.get(sym, {})
    figs = [
        shares_nav_figure(label, sm.get("shares"), sm.get("nav"),
                          ma_period=ma_period, current_shares=sm.get("current_shares"),
                          scissor=snap.get("scissor")),
        nav_deviation_figure(label, sm.get("nav"), snap, ma_period=ma_period),
    ]
    fd = flow_daily_figure(label, sm.get("shares"), sm.get("nav"))
    if fd is not None:
        figs.append(fd)
    return figs


# ---------------- 页面级 CSS / JS（浅色默认 + body.dark 覆盖，与其他三看板同模式） ----------------

_PAGE_CSS = """
:root { --bg:#f8fafc; --card:#ffffff; --text:#1e293b; --head:#334155; --text2:#475569;
        --muted:#64748b; --faint:#94a3b8; --ghost:#cbd5e1; --border:#e2e8f0; --border2:#cbd5e1;
        --thbg:#f1f5f9; --hover:#f1f5f9; --chipbg:#f1f5f9; --targetbg:#eff6ff;
        --shadow:0 1px 3px rgba(0,0,0,.08); --warn:#b45309;
        --sumbg:#eff6ff; --sumline:#2563eb; --sumtext:#1e3a8a;
        --ovbg:#dcfce7; --ovline:#16a34a; --obbg:#fee2e2; --obline:#dc2626;
        --wtbg:#eff6ff; --wtline:#2563eb; --rskbg:#fef3c7; --rskline:#d97706; }
body.dark { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --head:#cbd5e1; --text2:#cbd5e1;
        --muted:#94a3b8; --faint:#94a3b8; --ghost:#475569; --border:#334155; --border2:#475569;
        --thbg:#283548; --hover:#26334a; --chipbg:#334155; --targetbg:#1e3a5f;
        --shadow:0 1px 3px rgba(0,0,0,.3); --warn:#fbbf24;
        --sumbg:#16233f; --sumline:#3b82f6; --sumtext:#bfdbfe;
        --ovbg:rgba(34,197,94,.13); --ovline:#4ade80; --obbg:rgba(220,38,38,.15); --obline:#f87171;
        --wtbg:#16233f; --wtline:#3b82f6; --rskbg:#3a2c0a; --rskline:#fbbf24; }
* { box-sizing:border-box; }
body { font-family:'Microsoft YaHei',sans-serif; margin:0; padding:20px; background:var(--bg);
       color:var(--text); font-size:14px; }
h2 { border-bottom:2px solid var(--border); padding-bottom:8px; margin:0 0 4px; font-size:20px; }
h3 { color:var(--head); margin-top:28px; }
p.sub { color:var(--muted); font-size:13px; margin-top:2px; }
.page-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }
#theme-btn { background:var(--card); border:1px solid var(--border); color:var(--text);
             padding:4px 10px; border-radius:6px; cursor:pointer; font-size:15px; line-height:1.2; }
#theme-btn:hover { background:var(--hover); }
.muted { color:var(--muted); } .faint { color:var(--faint); } .ghost { color:var(--ghost); }
.text2 { color:var(--text2); } .bold { font-weight:bold; } .c { text-align:center; }
.sub2 { font-size:11px; }
.fresh { font-size:10px; color:var(--faint); } .fresh.warn-txt { color:var(--warn); }
/* 语义色（深浅色各一套） */
.c-pos { color:#ea580c; } body.dark .c-pos { color:#fb923c; }
.c-neg { color:#2563eb; } body.dark .c-neg { color:#60a5fa; }
/* 日申赎列极端分位（A股资金流语义：红=巨额净申购/绿=巨额净赎回·与筹码列相反） */
.flow-hot { color:#dc2626; } body.dark .flow-hot { color:#f87171; }
.flow-cold { color:#16a34a; } body.dark .flow-cold { color:#4ade80; }
.st-value { color:#16a34a; } body.dark .st-value { color:#4ade80; }
.st-growth { color:#2563eb; } body.dark .st-growth { color:#60a5fa; }
.st-cyclic { color:#ea580c; } body.dark .st-cyclic { color:#fb923c; }
.sc-txt { color:#b45309; } body.dark .sc-txt { color:#fbbf24; }
.en-hi { color:#16a34a; } body.dark .en-hi { color:#4ade80; }
.en-up { color:#65a30d; } body.dark .en-up { color:#a3e635; }
.en-flat { color:var(--text2); }
.en-dn { color:#ea580c; } body.dark .en-dn { color:#fb923c; }
.en-bad { color:#dc2626; } body.dark .en-bad { color:#f87171; }
.en-na { color:var(--faint); }
/* 板块资金流向 section（红=流入/绿=流出·A股惯例·本节局部语义，与筹码列相反） */
.flow-pos { color:#dc2626; } body.dark .flow-pos { color:#f87171; }
.flow-neg { color:#16a34a; } body.dark .flow-neg { color:#4ade80; }
.flow-tile { display:flex; flex-direction:column; gap:6px; }
.flow-lbl { font-size:20px; font-weight:700; }
.flow-lbl-in { color:#dc2626; } body.dark .flow-lbl-in { color:#f87171; }
.flow-lbl-rot { color:#7c3aed; } body.dark .flow-lbl-rot { color:#c4b5fd; }
.flow-lbl-out { color:#16a34a; } body.dark .flow-lbl-out { color:#4ade80; }
.flow-lbl-quiet { color:var(--muted); }
.flow-stats { font-size:13px; color:var(--text2); }
.flow-chips { display:flex; flex-wrap:wrap; gap:6px; }
.flow-chip { background:var(--chipbg); border-radius:10px; padding:2px 8px; font-size:12px;
             cursor:pointer; user-select:none; }   /* 点击=线开关·双击=独显·title=成员列表 */
.flow-ev-today { font-size:10px; background:#fcd34d; color:#78350f; border-radius:4px;
                 padding:0 4px; margin-left:2px; }   /* 申赎异动「最新」徽标 */
.flow-chip:not(.on) { opacity:.38; filter:grayscale(.8); }   /* 关闭态：变灰去色 */
.flow-dot { display:inline-block; width:8px; height:8px; border-radius:50%;
            margin-right:4px; vertical-align:baseline; }
.flow-groups { margin-top:2px; }
.flow-groups summary { cursor:pointer; color:var(--muted); font-size:12px; }
.flow-groups-body { display:grid; grid-template-columns:repeat(3, minmax(0,1fr));
                    gap:2px 18px; font-size:12px; color:var(--text2); padding:6px 0 2px; }
/* 排名表 */
table { border-collapse:collapse; width:100%; margin:12px 0; font-size:13px; background:var(--card); }
th { background:var(--thbg); padding:10px; text-align:center; border-bottom:2px solid var(--border2); }
td { padding:8px 10px; border-bottom:1px solid var(--border); vertical-align:top; }
td:first-child { text-align:left; }
tr:hover { background:var(--hover); }
.etf-link, .xb-link { color:var(--text); text-decoration:none; }
.etf-link:hover, .xb-link:hover { text-decoration:underline; }
table.sortable th[data-key] { cursor:pointer; user-select:none; }
table.sortable th[data-key]::after { content:" ⇅"; font-size:9px; color:var(--faint); }
table.sortable th[data-key].asc::after { content:" ▲"; }
table.sortable th[data-key].desc::after { content:" ▼"; }
/* 排名 tab（价值/成长/周期 真分页，参照 stock_report.py） */
.rank-tabs { display:flex; gap:4px; flex-wrap:wrap; margin:14px 0 0; border-bottom:2px solid var(--border); }
.rank-tabs .tab { background:transparent; border:none; border-bottom:3px solid transparent;
                  padding:8px 14px; cursor:pointer; font-size:14px; font-weight:600; color:var(--muted); }
.rank-tabs .tab:hover { color:var(--text); }
.rank-tabs .tab.active { color:var(--text); border-bottom-color:#2563eb; }
.rank-panel { display:none; padding-top:0; }
.rank-panel.active { display:block; }
.count { color:var(--muted); font-weight:normal; font-size:12px; }
/* 偏离度极端区横幅 */
.extreme-banner { background:var(--card); border:1px solid var(--border); border-radius:8px;
                  padding:10px 14px; margin:12px 0; }
.extreme-title { font-size:14px; font-weight:600; color:var(--head); margin-bottom:8px; }
.xb-note { color:var(--faint); font-size:11px; font-weight:400; }
.extreme-grid { display:flex; gap:12px; flex-wrap:wrap; }
.extreme-col { flex:1; min-width:260px; padding:8px 12px; border-radius:6px; }
.extreme-col.oversold { background:var(--ovbg); border-left:4px solid var(--ovline); }
.extreme-col.overbought { background:var(--obbg); border-left:4px solid var(--obline); }
.extreme-head { font-weight:600; color:var(--text); margin-bottom:6px; }
.xb-item { display:inline-block; margin:3px 12px 3px 0; font-size:13px; white-space:nowrap; }
.xb-sub { color:var(--muted); font-size:12px; }
.xb-seq { display:block; white-space:normal; font-size:11px; color:var(--faint);
          font-variant-numeric:tabular-nums; margin-top:1px; }
/* 四象限提醒（偏离度极端 × 筹码方向）：按提醒级别着色 */
.quad-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.quad { border-radius:6px; padding:8px 12px; border-left:4px solid; }
.quad-head { font-weight:600; color:var(--text); margin-bottom:6px; font-size:13px; }
.quad-note { color:var(--muted); font-weight:400; font-size:11px; margin-left:6px; }
.quad-opp { background:var(--ovbg); border-color:var(--ovline); }
.quad-watch { background:var(--wtbg); border-color:var(--wtline); }
.quad-severe { background:var(--obbg); border-color:var(--obline); }
.quad-risk { background:var(--rskbg); border-color:var(--rskline); }
.chip.quad-opp { color:#16a34a; } body.dark .chip.quad-opp { color:#4ade80; }
.chip.quad-watch { color:#2563eb; } body.dark .chip.quad-watch { color:#60a5fa; }
.chip.quad-severe { color:#dc2626; } body.dark .chip.quad-severe { color:#f87171; }
.chip.quad-risk { color:#b45309; } body.dark .chip.quad-risk { color:#fbbf24; }
.chip-up { color:#16a34a; } body.dark .chip-up { color:#4ade80; }
.chip-dn { color:#dc2626; } body.dark .chip-dn { color:#f87171; }
.chip-seq { font-size:10px; color:var(--faint); font-variant-numeric:tabular-nums; white-space:normal; }
/* 业绩预期链 (E3 明细块) */
.chain-strip { margin: 10px 14px 4px; padding: 8px 10px; border: 1px solid var(--border);
  border-radius: 8px; background: var(--bg2, rgba(127,127,127,.06)); }
.chain-title { font-size: 12px; color: var(--text2); font-weight: 600; }
.chain-cells { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
.chain-cell { flex: 1 1 90px; min-width: 90px; text-align: center; padding: 6px 4px;
  border-radius: 6px; background: rgba(127,127,127,.08); }
.chain-cell b { font-size: 13px; }
.chain-cell.fresh-ring { outline: 1px solid #65a30d55; }
body.dark .chain-cell.fresh-ring { outline-color: #a3e63555; }
/* 全池格局 / 读图说明 */
.summary-box { background:var(--sumbg); border-left:4px solid var(--sumline); padding:12px 16px;
               border-radius:6px; font-size:14px; line-height:1.7; color:var(--sumtext); margin:14px 0; }
details.guide { margin:10px 0; }
details.guide > summary { cursor:pointer; display:inline-block; color:var(--muted); font-size:12px;
                          list-style:none; }
details.guide > summary::-webkit-details-marker { display:none; }
details.guide > summary::before { content:"▸ "; }
details.guide[open] > summary::before { content:"▾ "; }
details.guide > summary:hover { color:var(--text); }
.guide-body { margin-top:6px; padding:8px 12px; background:var(--card); border:1px solid var(--border);
              border-radius:6px; font-size:12px; line-height:1.8; color:var(--text2); }
.guide-body b { color:var(--text); }
/* 逐标的明细：折叠面板 + 快速跳转 */
.detail-head { display:flex; align-items:center; justify-content:space-between; gap:12px;
               flex-wrap:wrap; margin-top:28px; }
.detail-head h3 { margin-top:0; }
.jump-select { background:var(--card); color:var(--text); border:1px solid var(--border);
               border-radius:6px; padding:6px 10px; font-size:13px; cursor:pointer; max-width:280px; }
details.etf-detail { margin:0; padding:4px 0 0; border-top:2px solid var(--border2); }
details.etf-detail:target { background:var(--targetbg); }
details.etf-detail > summary { cursor:pointer; list-style:none; display:flex; align-items:baseline;
                               gap:10px; flex-wrap:wrap; padding:8px 4px; }
details.etf-detail > summary::-webkit-details-marker { display:none; }
details.etf-detail > summary::before { content:"▸"; color:var(--faint); font-size:12px; }
details.etf-detail[open] > summary::before { content:"▾"; }
details.etf-detail[open] > summary { border-bottom:1px dashed var(--border); }
.etf-title { font-weight:600; color:var(--head); font-size:14px; }
.etf-code { color:var(--muted); font-size:12px; }
.etf-chips { display:flex; gap:6px; flex-wrap:wrap; }
.chip { font-size:11px; padding:1px 8px; border-radius:9px; background:var(--chipbg);
        color:var(--text2); border:1px solid var(--border); white-space:nowrap; }
.chip.dev-pos { color:#ea580c; } body.dark .chip.dev-pos { color:#fb923c; }
.chip.dev-neg { color:#2563eb; } body.dark .chip.dev-neg { color:#60a5fa; }
/* 图表容器 */
.chart-block { width:100%; margin:0 0 2px 0; }
details.etf-detail > .chart-block:last-child { margin-bottom:6px; }
.chart-block > div { width:100% !important; max-width:100% !important; }
.lazy-chart { width:100%; position:relative; }
/* 未渲染占位提示（入队等停稳/分帧调度时给视觉反馈；purge 后自动复现） */
.lazy-chart:not([data-rendered])::after {
  content: "⏳ 图表渲染中…"; position:absolute; top:40%; left:50%;
  transform:translate(-50%,-50%); color:var(--faint); font-size:13px;
}
html { scroll-behavior:smooth; }
/* 回顶部 */
#back-top { position:fixed; right:18px; bottom:18px; z-index:50; display:none; align-items:center;
            justify-content:center; width:42px; height:42px; border-radius:50%;
            border:1px solid var(--border); background:var(--card); color:var(--text);
            cursor:pointer; font-size:18px; box-shadow:var(--shadow); }
#back-top:hover { background:var(--hover); }
"""

# 页面交互：主题(懒渲染 purge+rebuild) / 懒渲染 / 排名 tab / 表头排序 / 明细跳转+锚点展开 / 回顶部。
# 依赖运行时 Plotly 与 CHARTS（由 render 嵌入）。data-sym/data-idx 定位 CHARTS[sym][i]。
_PAGE_JS = r"""
// ---------- 深浅色主题（默认浅色；暗色偏好存 localStorage） ----------
function _isDark(){ return document.body.classList.contains('dark'); }
function _syncThemeBtn(){ var b=document.getElementById('theme-btn'); if(b)b.textContent=_isDark()?'☀️':'🌙'; }
function _applyPlotlyTheme(gd, dark){
  var u = {paper_bgcolor:dark?'#1e293b':'#ffffff', plot_bgcolor:dark?'#1e293b':'#ffffff',
           'font.color':dark?'#e2e8f0':'#1e293b'};
  try {
    Object.keys(gd._fullLayout).forEach(function(k){
      if(!/^[xy]axis\d*$/.test(k)) return;
      u[k+'.gridcolor'] = dark?'#334155':'#e2e8f0';
      u[k+'.zerolinecolor'] = dark?'#475569':'#cbd5e1';
      var ax = gd._fullLayout[k];
      if(ax && ax.rangeselector){
        u[k+'.rangeselector.bgcolor'] = dark?'#334155':'#ffffff';
        u[k+'.rangeselector.activecolor'] = dark?'#3b4a63':'#fcd34d';
        u[k+'.rangeselector.font.color'] = dark?'#e2e8f0':'#1e293b';
      }
    });
  } catch(e) {}
  try { Plotly.relayout(gd, u); } catch(e) {}
  // 图内文字标签（现在 / 历史低高点「第N」）随主题换色，否则暗色底上看不见
  try {
    var tc = dark?'#f1f5f9':'#1e293b';
    (gd.data||[]).forEach(function(tr, i){
      if(!tr || !tr.name) return;
      if(tr.name === '现在'){ Plotly.restyle(gd, {'marker.color':tc, 'textfont.color':tc}, i); }
      else if(tr.name.indexOf('历史') === 0){ Plotly.restyle(gd, {'textfont.color':tc}, i); }
    });
  } catch(e) {}
}
function toggleTheme(){
  document.body.classList.toggle('dark');
  var dark = _isDark();
  try { localStorage.setItem('research-dark', String(dark)); } catch(e) {}
  _syncThemeBtn();
  _rerenderCharts();
}
(function(){ try { if(localStorage.getItem('research-dark')==='true') document.body.classList.add('dark'); } catch(e) {} _syncThemeBtn(); })();

// ---------- 懒渲染 v2：滚动中只入队、停稳后分帧渲染；render/purge 滞回双窗口 ----------
// 根因：IO 回调在滚动帧里同步 newPlot，快速滚动一次连发 3-4 张（每张全历史数据 + rangeslider
// 数百 ms）阻塞主线程 → 顿卡。改为：进入窗口只入队，滚动停稳 ~130ms 后逐张渲染（每帧一张），
// 渲染永不与滚动争帧；渲染窗口 800px / 释放窗口 3000px 滞回，消除边界 purge↔render 抖动。
function _renderChart(el){
  if(el.dataset.rendered === '1') return;
  var sym = el.getAttribute('data-sym'), i = +el.getAttribute('data-idx');
  var list = CHARTS[sym]; if(!list || !list[i]) return;
  el.dataset.rendered = '1';
  // 把占位 min-height 提升为显式 height：打破 responsive 模式下 Plotly 注入的
  // .plot-container height:100% × 父级 auto 高度的循环百分比环（异步 relayout 时序
  // 下 Chrome 会把盒子卡在错误高度——svg 460 而盒 765，多出的空洞=图间大间隔）
  if(el.style.minHeight) el.style.height = el.style.minHeight;
  Plotly.newPlot(el, list[i], {responsive:true, displaylogo:false}).then(function(){
    if(el.dataset.rendered === '1') _applyPlotlyTheme(el, _isDark());
    if(sym === '__flow' && i === 0) _applyFlowFilter();   // 组chips过滤态在(重)渲染后恢复
    if(sym === '__flow' && i === 1){                      // 异动条带：点击点跳该ETF明细
      el.removeAllListeners && el.removeAllListeners('plotly_click');
      el.on('plotly_click', function(data){
        try { var cd = data.points[0].customdata;
              if(cd && cd[1]) jumpToEtf(String(cd[1])); } catch(e) {}
      });
    }
  }).catch(function(){});
}
// —— 板块资金流向：组 chips = 线图图例开关（图内 legend 已移除，chips 即图例）——
// 单击 chip=该组线开关 · 双击=仅看该组(已独显则复位) · ↺全部=全开
function _flowLinesEl(){
  return document.querySelector('.lazy-chart[data-sym="__flow"][data-idx="0"]');
}
function _applyFlowFilter(){
  var el = _flowLinesEl();
  if(!el || el.dataset.rendered !== '1' || !el.data) return;
  var vis = el.data.map(function(tr){
    var c = document.querySelector('.flow-chip[data-flow-group="' + tr.name + '"]');
    return (!c || c.classList.contains('on')) ? true : 'legendonly';
  });
  try { Plotly.restyle(el, {'visible': vis}); } catch(e) {}
}
function flowChipClick(name){
  var c = document.querySelector('.flow-chip[data-flow-group="' + name + '"]');
  if(c) c.classList.toggle('on');
  _applyFlowFilter();
}
function flowChipSolo(name){
  var chips = [].slice.call(document.querySelectorAll('.flow-chip[data-flow-group]'));
  var nc = document.querySelector('.flow-chip[data-flow-group="' + name + '"]');
  var isSolo = nc && nc.classList.contains('on') &&
               chips.filter(function(c){ return c.classList.contains('on'); }).length === 1;
  chips.forEach(function(c){ c.classList.toggle('on', isSolo ? true : c === nc); });
  _applyFlowFilter();
}
function flowChipAll(){
  [].slice.call(document.querySelectorAll('.flow-chip[data-flow-group]')).forEach(function(c){
    c.classList.add('on');
  });
  _applyFlowFilter();
}
function _purgeChart(el){
  if(el.dataset.rendered !== '1') return;
  try { Plotly.purge(el); } catch(e) {}
  delete el.dataset.rendered;
  el.style.height = '';   // 还原为 min-height 占位
}
var _queue = [], _queued = [], _pumping = false, _paused = false, _scrollIdle = null;
function _enqueue(el){
  if(el.dataset.rendered === '1' || _queued.indexOf(el) >= 0) return;
  _queued.push(el); _queue.push(el);
}
function _dequeue(el){
  var i = _queued.indexOf(el); if(i >= 0) _queued.splice(i, 1);
  var j = _queue.indexOf(el); if(j >= 0) _queue.splice(j, 1);
}
function _pump(){
  if(_pumping || _paused || !_queue.length) return;
  _pumping = true;
  requestAnimationFrame(function step(){
    if(_paused){ _pumping = false; return; }      // 滚动打断，idle 后自动重启
    var el = _queue.shift();
    if(el !== undefined){
      var k = _queued.indexOf(el); if(k >= 0) _queued.splice(k, 1);
      try {
        if(el.dataset.rendered !== '1' && el.isConnected) _renderChart(el);
      } catch(e) {                                 // 单图异常不得杀死泵链
        try { delete el.dataset.rendered; el.style.height = ''; } catch(e2) {}
      }
    }
    if(_queue.length){ requestAnimationFrame(step); }
    else { _pumping = false; }
  });
}
// 滚动进行中暂缓渲染，停稳后再清队列（占位 div 有 min-height，不跳屏）
window.addEventListener('scroll', function(){
  _paused = true;
  if(_scrollIdle) clearTimeout(_scrollIdle);
  _scrollIdle = setTimeout(function(){ _paused = false; _pump(); }, 130);
  var b = document.getElementById('back-top');
  if(b) b.style.display = (window.scrollY > 600) ? 'flex' : 'none';
}, {passive:true});
// 主题切换：已渲染的全部 purge 后重新入队分帧重画（保证主题在渲染时一次到位）
function _rerenderCharts(){
  document.querySelectorAll('.lazy-chart').forEach(function(el){
    if(el.dataset.rendered === '1'){ _purgeChart(el); _enqueue(el); }
  });
  _paused = false; if(_scrollIdle) clearTimeout(_scrollIdle); _pump();
}
(function(){
  var nodes = document.querySelectorAll('.lazy-chart');
  if(!('IntersectionObserver' in window)){ nodes.forEach(_renderChart); return; }
  var renderIO = new IntersectionObserver(function(entries){
    entries.forEach(function(e){ if(e.isIntersecting) _enqueue(e.target); });
    _pump();
  }, {rootMargin: '800px 0px'});
  var purgeIO = new IntersectionObserver(function(entries){
    entries.forEach(function(e){ if(!e.isIntersecting){ _dequeue(e.target); _purgeChart(e.target); } });
  }, {rootMargin: '3000px 0px'});
  nodes.forEach(function(el){ renderIO.observe(el); purgeIO.observe(el); });
})();

// ---------- 排名 tab（价值/成长/周期，记住上次选择） ----------
function switchRankTab(name){
  document.querySelectorAll('.rank-tabs .tab').forEach(function(b){ b.classList.toggle('active', b.dataset.tab===name); });
  document.querySelectorAll('.rank-panel').forEach(function(p){ p.classList.toggle('active', p.id==='tab-'+name); });
  try { localStorage.setItem('research-rank-tab', name); } catch(e) {}
}
(function(){
  var t = null; try { t = localStorage.getItem('research-rank-tab'); } catch(e) {}
  if(t && document.getElementById('tab-'+t)) switchRankTab(t);
})();

// ---------- 表头点击排序（缺失值恒沉底；文本列中文 locale） ----------
document.addEventListener('click', function(e){
  var th = e.target && e.target.closest ? e.target.closest('th[data-key]') : null;
  if(!th) return;
  var table = th.closest('table'), tbody = table.querySelector('tbody');
  if(!tbody) return;
  var key = th.dataset.key, isText = th.dataset.type === 'text';
  var dir = th.getAttribute('data-dir') === 'asc' ? 'desc' : 'asc';
  table.querySelectorAll('th[data-key]').forEach(function(h){ h.classList.remove('asc','desc'); h.removeAttribute('data-dir'); });
  th.setAttribute('data-dir', dir); th.classList.add(dir);
  var rows = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
  rows.sort(function(a, b){
    var va = a.dataset[key] || '', vb = b.dataset[key] || '';
    if(isText){ var c = va.localeCompare(vb, 'zh'); return dir==='asc' ? c : -c; }
    var na = va==='' ? NaN : parseFloat(va), nb = vb==='' ? NaN : parseFloat(vb);
    if(isNaN(na) && isNaN(nb)) return 0;
    if(isNaN(na)) return 1; if(isNaN(nb)) return -1;   // 缺失恒沉底
    return dir==='asc' ? na-nb : nb-na;
  });
  rows.forEach(function(r){ tbody.appendChild(r); });
});

// ---------- 明细：下拉快速跳转（自动展开）+ 锚点跳转自动展开 ----------
function jumpToEtf(sym){
  if(!sym) return;
  var el = document.getElementById(sym); if(!el) return;
  if(el.tagName === 'DETAILS') el.open = true;
  el.scrollIntoView({behavior:'smooth', block:'start'});
  if(history.replaceState) history.replaceState(null, '', '#'+sym);
}
function _openTarget(){
  var id = decodeURIComponent(location.hash.slice(1)); if(!id) return;
  var el = document.getElementById(id);
  if(el && el.tagName === 'DETAILS') el.open = true;
}
window.addEventListener('hashchange', _openTarget);
window.addEventListener('load', _openTarget);

"""


def render(snapshots: dict, series_map: dict, meta: dict, as_of: str,
           signal_note: str = "", ma_period: int = 60, pool_summary: str = "",
           pinned: list[str] | None = None, flow: dict | None = None,
           earn_history: dict | None = None) -> str:
    """Build the full HTML. series_map[symbol] = {close, shares, nav}。

    flow: research/flow.py 的 payload（{window, state, group_roll, rolls, aum, aum_series,
    groups, excluded, as_of}），由 scripts/research_report.py 组装；None/数据不足 →
    section 整体省略（优雅降级）。
    earn_history: 业绩预期提醒的窗口回放台账（scripts build_earn_history 组装），
    None/空 → 横幅不带台账（优雅降级）。"""
    # data_sufficient ETFs 参与排名；不足者（NAV 历史不够算偏离度）保留明细图、不进排名。
    ranked = {s: sn for s, sn in snapshots.items() if sn.get("data_sufficient", True)}
    excluded = {s: sn for s, sn in snapshots.items() if not sn.get("data_sufficient", True)}

    # 逐标的明细：置顶 ETF 最前，其余按偏离度极值排，excluded 随后按名
    ordered = _order_detail(ranked, excluded, pinned, meta)
    pin_set = {s for s in (pinned or []) if s in snapshots}
    # 懒渲染：图数据 to_json 嵌入 CHARTS dict，页内只放占位 div；IntersectionObserver
    # 滚入视口才 Plotly.newPlot、离开 purge 释放——把同时在画的图从 66 张压到 ~3-5 张。
    # 明细改折叠面板（details 默认收起、置顶展开）：收起=零渲染，展开即触发 IO 渲染。
    from plotly.offline import get_plotlyjs
    charts_json: dict[str, list[str]] = {}
    chart_blocks = []
    jump_options = ['<option value="">跳转到 ETF…</option>']
    for sym, snap in ordered:
        nm = meta.get(sym, {}).get("name", sym)
        figs = _etf_figs(sym, snap, meta, series_map, ma_period)
        charts_json[sym] = [f.to_json() for f in figs]
        star = "⭐ " if sym in pin_set else ""
        open_attr = " open" if sym in pin_set else ""
        block = (f'<details id="{sym}" class="etf-detail"{open_attr}>'
                 f'<summary><span class="etf-title">{star}{nm}</span>'
                 f'<span class="etf-code">{sym}</span>'
                 f'<span class="etf-chips">{_detail_chips(snap)}</span></summary>')
        block += _chain_block(snap)
        for i, fig in enumerate(figs):
            h = int(fig.layout.height or 460)   # 占位高度匹配图高，避免渲染后跳屏/留白
            block += (f'<div class="chart-block">'
                      f'<div class="lazy-chart" data-sym="{sym}" data-idx="{i}" '
                      f'style="min-height:{h}px"></div></div>')
        chart_blocks.append(block + "</details>")
        jump_options.append(f'<option value="{sym}">{nm}（{sym}）</option>')
    charts_html = "\n".join(chart_blocks)
    # 板块资金流向：图表挂 CHARTS['__flow'] 伪 key（JS 纯 dict 查找，与 ETF 图同一懒渲染管线）
    flow_html = ""
    flow_events_html = ""
    if flow and (flow.get("state") or {}).get("label_key") not in (None, "insufficient"):
        flow_figs_json = [flow_lines_figure(flow).to_json()]
        if flow.get("events"):      # 申赎异动时间条带 = __flow 图组第 2 张（idx1）
            flow_figs_json.append(flow_events_strip_figure(flow).to_json())
        charts_json["__flow"] = flow_figs_json
        flow_html = _flow_section(flow)
        flow_events_html = _flow_events_banner(flow, meta)
    _esc = lambda s: re.sub(r"</script", r"<\\/script", s, flags=re.I)
    entries = _esc(",\n".join(f'"{sym}":[{",".join(charts_json[sym])}]' for sym in charts_json))
    charts_script = ("<script>\n" + _esc(get_plotlyjs()) + "\nvar CHARTS={" + entries + "};\n"
                     + _PAGE_JS + "\n</script>")

    # 排名区：价值/成长/周期 真 tab 分页（记住上次选择），表头点击排序
    ranking_value = _ranking_rows(ranked, meta, style_filter="value")
    ranking_growth = _ranking_rows(ranked, meta, style_filter="growth")
    ranking_cyclic = _ranking_rows(ranked, meta, style_filter="cyclic")
    cnt = {"value": 0, "growth": 0, "cyclic": 0}
    for sn in ranked.values():
        st = sn.get("style", "growth")
        if st in cnt:
            cnt[st] += 1
    _rank_header = ('<tr><th style="text-align:left" data-key="name" data-type="text">ETF</th>'
                    '<th data-key="style" data-type="text">类型</th>'
                    f'<th data-key="dev" data-type="num">净值MA{ma_period}偏离<sup style="font-size:9px">分位</sup></th>'
                    '<th>份额/净值剪刀差</th>'
                    '<th data-key="chip" data-type="num">筹码<sub style="font-size:9px">20日</sub></th>'
                    '<th data-key="flow" data-type="num">日申赎<sub style="font-size:9px">当日脉搏</sub></th>'
                    '<th data-key="earn" data-type="num">业绩预期<sup style="font-size:9px">信息·预期g</sup></th>'
                    '<th data-key="turnover" data-type="num">成交<sub style="font-size:9px">5日</sub></th></tr>')
    tabs_html = (
        '<div class="rank-tabs">'
        f'<button class="tab active" data-tab="value" onclick="switchRankTab(\'value\')">💰 价值型 <span class="count">{cnt["value"]}</span></button>'
        f'<button class="tab" data-tab="growth" onclick="switchRankTab(\'growth\')">🚀 成长型 <span class="count">{cnt["growth"]}</span></button>'
        f'<button class="tab" data-tab="cyclic" onclick="switchRankTab(\'cyclic\')">🔄 周期型 <span class="count">{cnt["cyclic"]}</span></button>'
        '</div>')
    panels_html = (
        f'<div id="tab-value" class="rank-panel active"><table class="sortable"><thead>{_rank_header}</thead><tbody>{ranking_value}</tbody></table></div>'
        f'<div id="tab-growth" class="rank-panel"><table class="sortable"><thead>{_rank_header}</thead><tbody>{ranking_growth}</tbody></table></div>'
        f'<div id="tab-cyclic" class="rank-panel"><table class="sortable"><thead>{_rank_header}</thead><tbody>{ranking_cyclic}</tbody></table></div>')
    n_ranked, n_excluded = len(ranked), len(excluded)
    if excluded:
        excl_names = "、".join(f"{meta.get(s, {}).get('name', s)}({s})" for s in excluded)
        excluded_note = (f'<p class="sub">⚠️ {n_excluded} 只 NAV 历史不足未参与排名：{excl_names}'
                         f'（明细图见下方）</p>')
    else:
        excluded_note = ""

    summary_html = (f'<h3>🔍 全池格局</h3><div class="summary-box">{pool_summary}</div>'
                    if pool_summary else "")

    extreme_banner_html = _extreme_banner(snapshots, meta)
    quadrant_banner_html = _quadrant_banner(snapshots, meta)
    earnings_alert_html = _earnings_alert_banner(snapshots, meta, as_of, history=earn_history)
    flow_guide = ("" if not flow_html else
                  "④ <b>板块资金流向（份额视角）</b>：ETF份额=净申赎（配置盘的脚印，比主力资金流干净——"
                  "真实现金进出·非逐笔推断）；份额流入≠看好（A股常有越跌越买的逆势申购，须与偏离度交叉看）；"
                  "「板块间流向」是推断非直接观测（申赎是独立净额·资金来源无标签，存量约束下的此消彼长"
                  "=跷跷板最强证据）；本池是精选池非全市场，流出可能去了池外主题ETF（代表性偏差）；"
                  "拆分/折算已做前复权（份额×净值反向断崖检测），不计入流入。<br>"
                  + ("⑤ <b>申赎异动台账</b>：近1月扫描 日增减% 进入自身<b>方向</b>历史99%分位（申购日比申购日·赎回日比赎回日·2026-08 与排名表「日申赎」列统一口径·同日数字互证） 且 ≥1亿 的单日大额"
                     "申赎事件，最新8条滚动展示（新事件顶旧事件·供回检）；全历史分位·纯观察不防前视；"
                     "流入≠看好。点击条目跳该ETF的日度净申赎图看事件细节。<br>" if flow_events_html else ""))
    earn_guide = ("⑥ <b>业绩预期列（信息层）</b>：上=最新披露窗口的<b>业绩预告</b>聚合（多/空=预喜/预亏类型"
                  "的权重占比·广度口径——强制披露门槛使样本天然偏极端，只看广度不看水平，覆盖=披露进度"
                  "与门槛筛过的混合）；下=<b>一致预期</b> g=Σ(官方权重×成分股 EPS 次年/当年−1)（东财研报"
                  "摘录口径·研报数≥3·财年滚动对齐·<b>水平值非变化量</b>——4周修正另示于同行：自建周度快照"
                  "差分·同财年对齐防年末翻滚·冷启动4周显示「累积中」；覆盖权重门"
                  "40%）。两行互补：预告=已披露的区间事实，预期=分析师前瞻（日更·软信息·系统性乐观需"
                  "横向比较）。观察坐标·不喂引擎。<br>"
                  "④' <b>📈 业绩预期提醒横幅</b>（提醒区只收事件不收状态）：A5=一致预期4周加权下修"
                  "&lt;-3%且覆盖≥40%（变化量才有信息·Womack 1996 下调>上调）；交叉=偏离极端分位×预告"
                  "广度（🟠超买×空≥5% 价格跑前面成分已有预亏 / 🟢超卖×预喜label 深跌基本面预喜），"
                  "只在披露窗口内出条目（开窗~截止+14天·非披露季只报下窗口时点——预告季度一跳防壁纸化）。"
                  "预期g水平值是状态（横截面排序才有意义·排序键已覆盖）→ 不进横幅。<br>")

    return f"""<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ETF 行业研究 · {as_of}</title>
<style>{_PAGE_CSS}</style></head><body>
<div class="page-head"><h2>🏭 ETF 行业研究 · 择时跟踪看板</h2>
<button id="theme-btn" onclick="toggleTheme()" title="深浅色切换">🌙</button></div>
<p class="sub">数据截至 {as_of} 收盘 · 纯跟踪视图（只跟踪、不标买卖点、不含涨跌预测；决策请综合多个看板）· {signal_note}</p>
<details class="guide"><summary>📖 读图说明 · 偏离度 / 剪刀差（点击展开）</summary>
<div class="guide-body">本看板跟踪三件事——① <b>净值-MA{ma_period}偏离度</b>：净值相对自身均线的偏离 + 历史百分位分位（0=最负/超卖…1=最正/超买），副图标历史极值「第几低/高」（1=史上最极端，纯观察）。
② <b>份额-净值剪刀差</b>：份额与净值走向分化（一升一降）时置灰标注漂移幅度与窗口天数；检不出干净分化则只画原始双线。
③ <b>偏离度×筹码 四象限提醒</b>：偏离度进入自身历史 5%/95% 极端分位 × 筹码方向（份额申赎 5/10/20/30/60 日近端等差加权投票·±1% 死区·阈值2，机构行为代理·主体不可辨）交叉——超卖+筹码增=🟢机会（深跌有承接）/超买+筹码减=🟠风险（高位兑现）/超卖+筹码减=🔴严重警告（深跌无承接）/超买+筹码增=🔵关注（惯性未死）。筹码多窗口值（5/10/20/30/60 日净变化率）在排名表第二行与四象限条目下直接展示，一眼看申赎节奏。<b>排名表「日申赎」列=当日脉搏</b>：最新交易日净申赎额（亿元=Δ份额×当日单位净值·拆分已调整）+ 份额日增减% + <b>方向内</b>自身全历史分位——申购日在全部申购日里排名（申购向分位）/赎回日在全部赎回日里排名（赎回向分位）：红绿着色表达方向、分位也按方向总体算（绝对值口径会把某方向史上空前的极端被另一方向更肥的尾巴压低分位）。某方向样本&lt;30日退「双向分位」（绝对值·横幅同规则降级）。分位≥95% 着色：正=红（该方向罕见大额）/负=绿——A股资金流语义·与筹码列颜色相反。<b>与「📡申赎异动」横幅同式同源：本列=最新日全量展示，横幅=近月≥99%分位+1亿地板过滤的子集，同日数字互证。</b>单日噪音大：当日脉搏看此列、中期节奏看筹码多窗口；历史不足250日诚实显示—。观察坐标·非买卖建议。<br>
{flow_guide}{earn_guide}表格点击表头可排序；逐标的明细默认折叠，点击行展开，或用右侧下拉快速跳转。</div></details>
{summary_html}
{extreme_banner_html}
{quadrant_banner_html}
{earnings_alert_html}
{flow_events_html}
{flow_html}
<h3>📊 择时跟踪排名 · 三类分页（{n_ranked} 只参与{n_excluded and f"，{n_excluded} 只 NAV 历史不足未参与" or ""}）</h3>
{excluded_note}
{tabs_html}
{panels_html}
<div class="detail-head"><h3>📈 逐标的明细（份额·净值·偏离度·剪刀差 · 点击展开）</h3>
<select id="etf-jump" class="jump-select" onchange="jumpToEtf(this.value)">{''.join(jump_options)}</select></div>
{charts_html}
{charts_script}
<button id="back-top" title="回顶部" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">↑</button>
</body></html>"""


def write_html(html: str, out_path: str | Path) -> Path:
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
