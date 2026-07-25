"""指数择时层交互式 HTML 看板(plotly,离线自包含)— 七件套(V4 tracker)。

① 偏离极值曲线(close+MA60 主图 / 偏离度副图+历史极值线+当前点)
② 6宽基趋势状态表(60日线上下/均线趋势/突破跌破档位/震荡市)
③ 估值开关 stat tile(沪深300 PE 分位 + 全市场 PB 分位 + zone)
④ 蓝筹 vs 成长 仓位倾向 lean 指标卡
⑤ 当前有效突破/跌破信号列表
⑥ 市场温度·大小盘温差(全市场 PB 分位 vs 沪深300 PB 分位)
⑦ 相对周期律·沪深成长温差(创业板 vs 上证 点差:5年包络位置 → 极点/中枢 + 历史稀有度 + 漂移)

配色遵循 dataviz skill 中性参考调色板:文字用 ink token 不穿 series 色;状态用 status
chip(icon+label,不单靠色);A股语义下正偏离(超买)暖红、负偏离(超卖)冷蓝。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import diagnose as dz
from . import indicators as ti

# ---- palette (dataviz reference, light mode) ----
_PAL = {
    "surface": "#fcfcfb", "plane": "#f9f9f7", "ink": "#0b0b0b",
    "ink_sec": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7",
    "series_1": "#2a78d6",    # blue — close line / 蓝筹
    "series_2": "#008300",    # green — 成长
    "pos_extreme": "#d03b3b",  # red — 正极值(超买/涨)
    "neg_extreme": "#1c5cab",  # blue-dark — 负极值(超卖/跌)
    "good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b",
}


def _chip(label: str, color: str) -> str:
    """status chip — icon+label paired, never color-alone."""
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:8px;'
            f'background:{color}22;color:{color};font-size:12px;font-weight:600;'
            f'border:1px solid {color}55">{label}</span>')


# ---- ① 偏离极值曲线 ----
def _deviation_figure(sym: str, name: str, df: pd.DataFrame, period: int = ti.MA_PERIOD) -> go.Figure:
    close = df["close"]
    ma = ti.ma_series(close, period)
    dev = ti.deviation_series(close, period)
    idx = pd.to_datetime(close.index)   # 转 date 类型 → hover 显示完整日期
    dc = dev.dropna()
    mx = float(dc.max()) if len(dc) else float("nan")
    mn = float(dc.min()) if len(dc) else float("nan")
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.10,
        subplot_titles=(f"{name}({sym}) 收盘价 vs {period}日线", "偏离度 (价格−均线)÷均线"))
    fig.add_trace(go.Scatter(x=idx, y=close, name="收盘",
                             line=dict(color=_PAL["series_1"], width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=ma, name=f"MA{period}",
                             line=dict(color=_PAL["muted"], width=1.5, dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=idx, y=dev, name="偏离度",
                             line=dict(color=_PAL["ink_sec"], width=1.5)), row=2, col=1)
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
    fig.update_layout(
        height=540, margin=dict(l=50, r=20, t=50, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=False)
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"],
                     type="date", hoverformat="%Y-%m-%d")
    fig.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["baseline"])
    fig.update_xaxes(
        rangeslider_visible=True,
        rangeselector=dict(buttons=[
            dict(count=1, label="1月", step="month", stepmode="backward"),
            dict(count=6, label="6月", step="month", stepmode="backward"),
            dict(count=1, label="1年", step="year", stepmode="backward"),
            dict(count=3, label="3年", step="year", stepmode="backward"),
            dict(label="全部", step="all"),
        ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]),
        row=2, col=1)
    return fig


# ---- ② 趋势状态表 ----
def _trend_table_html(diag: dict) -> str:
    rows = []
    for sym, info in diag["indices"].items():
        if not info.get("valid"):
            rows.append(f"<tr><td>{info['name']}</td><td colspan='5' style='color:{_PAL['muted']}'>"
                        f"{info.get('reason','—')}</td></tr>")
            continue
        dg = info["diagnosis"]; t = dg["trend"]; bo = dg["breakout"]; dev = dg["deviation"]
        above = "线上▲" if t["above_ma"] else "线下▼"
        above_c = _PAL["good"] if t["above_ma"] else _PAL["critical"]
        mtrend = "↑" if t["ma_trend_up"] else ("↓" if t["ma_trend_up"] is False else "—")
        # 突破/跌破 只在「近期真穿越」时标;仅在线上/下但无穿越 → 中性(不再误称突破)
        fresh = ti.fresh_cross_direction(dg.get("cross"))
        if fresh == "up":
            bo_label, bo_c = f"突破 g{bo['grade']}", _PAL["good"]
        elif fresh == "down":
            bo_label, bo_c = f"跌破 g{bo['grade']}", _PAL["critical"]
        else:
            bo_label, bo_c = "中性", _PAL["muted"]
        choppy = f'<span style="color:{_PAL["warning"]}">⚠震荡</span>' if dg["choppy"] else "趋势"
        dev_txt = (f"{dev['cur_dev']:+.1%} / {dev['pct']:.0%}位"
                   if not pd.isna(dev["pct"]) and not pd.isna(dev["cur_dev"]) else "—")
        rows.append(
            f"<tr><td>{info['name']}</td>"
            f"<td>{_chip(above, above_c)}</td><td style='text-align:center'>{mtrend}</td>"
            f"<td>{_chip(bo_label, bo_c)}</td><td>{choppy}</td><td style='font-variant-numeric:tabular-nums'>{dev_txt}</td></tr>")
    return ("<table class='stat'><thead><tr>"
            "<th>指数</th><th>60日线</th><th>均线趋势</th><th>突破跌破</th><th>市态</th><th>偏离度</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            "<div class='hint' style='margin-top:8px'>列定义:60日线=价 vs 均线(线上/下) · "
            "均线趋势=均线升降 · 突破跌破=近5日真穿越才标(无穿越=中性;gN=偏离≥2%/3% 强度) · "
            "市态=趋势/震荡(震荡时信号谨慎) · 偏离度=(价格−均线)÷均线%(绝对值 / 历史分位)</div>")


# ---- ③ 估值开关 stat tile ----
def _meter(label: str, pct: float, sub: str = "") -> str:
    p = 0.0 if pd.isna(pct) else max(0.0, min(1.0, float(pct)))
    zc = _PAL["good"] if p < 0.2 else _PAL["critical"] if p > 0.8 else _PAL["ink_sec"]
    val_txt = f"{p:.0%}" if not pd.isna(pct) else "—"
    return (f"<div class='tile'><div class='tile-label'>{label}</div>"
            f"<div class='tile-value' style='color:{zc}'>{val_txt}</div>"
            f"<div class='meter'><div class='meter-fill' style='width:{p*100:.0f}%;background:{zc}'></div></div>"
            f"<div class='tile-sub'>{sub}</div></div>")


def _valuation_tile_html(val: dict) -> str:
    zone = val.get("zone", "—")
    if "低位" in zone:
        zc = _PAL["good"]
    elif "高位" in zone:
        zc = _PAL["critical"]
    elif "分化" in zone:
        zc = _PAL["warning"]    # 结构分化 = 需警惕(ROE 偏弱)
    else:
        zc = _PAL["ink_sec"]
    pe_sub = f"沪深300 PE-TTM {val['pe_ttm']:.1f}" if not pd.isna(val.get("pe_ttm")) else "—"
    pb_sub = f"沪深300 PB {val['pb']:.2f}" if not pd.isna(val.get("pb")) else "—"
    tiles = (
        f"{_meter('沪深300 PE 分位(同口径)', val['pe_pct'], pe_sub)}"
        f"{_meter('沪深300 PB 分位(同口径)', val['pb_pct'], pb_sub)}")
    pe_txt = f"{val['pe_pct']:.0%}" if not pd.isna(val.get("pe_pct")) else "—"
    pb_txt = f"{val['pb_pct']:.0%}" if not pd.isna(val.get("pb_pct")) else "—"
    if "分化" in zone:
        hint = (f"PE=PB/ROE → 一高一低 = ROE 偏弱。建议观望:资产便宜但盈利下滑,不急于抄底;"
                f"盈利企稳→转积极,继续下滑→转保守(沪深300 PE {pe_txt} / PB {pb_txt})")
    elif "低位" in zone or "高位" in zone:
        side = "低" if "低位" in zone else "高"
        hint = f"沪深300 PE+PB 同口径双{side}(PE {pe_txt} / PB {pb_txt})"
    else:  # 中位·中性
        hint = f"沪深300 PE+PB 都在历史中间区(PE {pe_txt} / PB {pb_txt})"
    return (f"<div class='tiles-row'>{tiles}</div>"
            f"<div style='margin-top:10px'>{_chip('估值开关: ' + zone, zc)} "
            f"<span class='hint'>{hint}</span></div>"
            f"<div class='hint' style='margin-top:6px'>指标:PE(TTM)=市值÷净利润 · PB=市值÷净资产 · "
            f"ROE=净利润÷净资产 · 故 PE=PB÷ROE</div>")


# ---- ⑥ 市场温度·大小盘温差 ----
def _market_temp_html(mt: dict) -> str:
    if not mt.get("valid"):
        return "<p class='hint'>市场温度数据不足</p>"
    diff = mt["diff"]
    regime = mt["regime"]
    rc = (_PAL["critical"] if regime == "小盘偏贵"
          else _PAL["good"] if regime == "小盘偏便宜" else _PAL["ink_sec"])
    mkt_sub = f"全市场中位数 PB {mt['market_pb']:.2f}"
    hs_sub = f"沪深300 PB {mt['hs300_pb']:.2f}"
    return (f"<div class='tiles-row'>"
            f"{_meter('全A 股 PB 分位', mt['market_pct'], mkt_sub)}"
            f"{_meter('沪深300 PB 分位', mt['hs300_pct'], hs_sub)}"
            f"<div class='tile'><div class='tile-label'>大小盘温差</div>"
            f"<div class='tile-value' style='color:{rc}'>{diff:+.0%}</div>"
            f"<div class='tile-sub'>{regime}</div></div></div>"
            f"<div class='hint' style='margin-top:8px'>温差 = 全市场PB分位 − 沪深300PB分位;"
            f"&gt;0 小盘偏贵(中小盘热),&lt;0 小盘偏便宜(小盘机会),≈0 同步</div>")


# ---- ④ 蓝筹 vs 成长 ----
def _style_card_html(style: dict) -> str:
    lean = style.get("lean")
    if not lean:
        return "<p class='hint'>风格数据不足</p>"
    lean_cn = {"growth": "偏成长(弹性好)", "blue_chip": "偏蓝筹(防御)"}.get(lean, lean)
    lean_c = _PAL["series_2"] if lean == "growth" else _PAL["series_1"]
    b = "▲ 上行" if style["blue_up"] else "▼ 下行"
    g = "▲ 上行" if style["growth_up"] else "▼ 下行"
    return (f"<div class='tiles-row' style='gap:18px'>"
            f"<div class='tile'><div class='tile-label'>蓝筹(上证50)</div>"
            f"<div class='tile-value' style='color:{_PAL['series_1']}'>{b}</div></div>"
            f"<div class='tile'><div class='tile-label'>成长(创业板指)</div>"
            f"<div class='tile-value' style='color:{_PAL['series_2']}'>{g}</div></div>"
            f"<div class='tile'><div class='tile-label'>仓位倾向</div>"
            f"<div class='tile-value' style='color:{lean_c};font-size:18px'>{lean_cn}</div></div></div>"
            f"<div class='hint' style='margin-top:8px'>注:上行 = 站上 60 日线 且 均线趋势向上;"
            f"下行 = 两者未同时满足。都上行→偏成长,都下行→偏蓝筹,相反→偏向上的</div>")


# ---- ⑤ 信号列表 ----
def _signals_html(diag: dict) -> str:
    sigs = []
    for sym, info in diag["indices"].items():
        if not info.get("valid"):
            continue
        dg = info["diagnosis"]; bo = dg["breakout"]
        fresh = ti.fresh_cross_direction(dg.get("cross"))   # 真穿越闸门(非「在线上」)
        if fresh in ("up", "down") and bo["grade"] >= 2:
            ago = (dg.get("cross") or {}).get("bars_ago", "?")
            dir_cn = "有效突破▲" if fresh == "up" else "有效跌破▼"
            c = _PAL["good"] if fresh == "up" else _PAL["critical"]
            warn = " <span class='hint'>(震荡市,信号谨慎)</span>" if dg["choppy"] else ""
            sigs.append(f"<li>{info['name']}({sym}): {_chip(dir_cn + ' g' + str(bo['grade']), c)}"
                        f" <span class='hint'>{ago}日前穿越</span>{warn}</li>")
    if not sigs:
        return "<p class='hint'>当前无有效突破/跌破信号(近5日真穿越 + 偏离≥2%)</p>"
    return "<ul class='sig-list'>" + "".join(sigs) + "</ul>"


# ---- ⑦ 相对周期律(创业板 vs 上证 点差:包络位置 → 极点/中枢)----
def _relative_cycle_figure(rc: dict, spread: pd.Series) -> go.Figure:
    """点差(上证−创业板,点)长历史 + 振幅通道线性趋势线(上/下/中) + 当前点。

    上/下沿 = 5年滚动 max/min 的 OLS 线性拟合(直线,看漂移方向,精度让位于趋势);
    中线 = (上沿+下沿)/2。两直线收敛/发散 = 振幅带收窄/展宽。当前精确包络见 tile。"""
    idx = pd.to_datetime(spread.index)
    win = 252 * rc.get("envelope_years", 5)
    yrs = rc.get("envelope_years", 5)
    upper = ti.linear_fit_line(spread.rolling(win).max())    # 上沿趋势(直线)
    lower = ti.linear_fit_line(spread.rolling(win).min())    # 下沿趋势(直线)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=idx, y=spread, name="点差(上证−创业板)",
                             line=dict(color=_PAL["series_1"], width=2)))
    if len(upper):
        fig.add_trace(go.Scatter(x=pd.to_datetime(upper.index), y=upper.to_numpy(),
                                 name=f"上沿·趋势线({yrs}年滚动最高·线性)",
                                 line=dict(color=_PAL["pos_extreme"], width=1.5)))
    if len(lower):
        fig.add_trace(go.Scatter(x=pd.to_datetime(lower.index), y=lower.to_numpy(),
                                 name=f"下沿·趋势线({yrs}年滚动最低·线性)",
                                 line=dict(color=_PAL["neg_extreme"], width=1.5)))
    if len(upper) and len(lower):
        mid = ((upper + lower) / 2.0).dropna()
        if len(mid):
            fig.add_trace(go.Scatter(x=pd.to_datetime(mid.index), y=mid.to_numpy(),
                                     name="中线·趋势(上沿+下沿)/2",
                                     line=dict(color=_PAL["muted"], width=1.5, dash="dash")))
    now = rc.get("spread_now")
    if not pd.isna(now) and len(idx):
        fig.add_trace(go.Scatter(x=[idx[-1]], y=[now], mode="markers+text",
                                 marker=dict(size=10, color=_PAL["ink"]),
                                 text=[f"现在 {now:+.0f}"], textposition="top center",
                                 showlegend=False))
    fig.update_layout(
        height=460, margin=dict(l=50, r=20, t=30, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=True)
    fig.update_yaxes(gridcolor=_PAL["grid"], zeroline=True, zerolinewidth=1,
                     zerolinecolor=_PAL["baseline"])
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"], type="date",
                     hoverformat="%Y-%m-%d", rangeslider_visible=True,
                     rangeselector=dict(buttons=[
                         dict(count=1, label="1年", step="year", stepmode="backward"),
                         dict(count=3, label="3年", step="year", stepmode="backward"),
                         dict(count=5, label="5年", step="year", stepmode="backward"),
                         dict(label="全部", step="all"),
                     ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))
    return fig


def _relative_cycle_html(rc: dict, fig_html: str = "") -> str:
    if not rc.get("valid"):
        return "<p class='hint'>相对周期律数据不足(需 上证综指+创业板指 各 ≥2 年)</p>"
    zone = rc["zone"]
    zc = (_PAL["neg_extreme"] if zone == "下沿极点"
          else _PAL["pos_extreme"] if zone == "上沿极点" else _PAL["ink_sec"])
    now = rc["spread_now"]
    tiles = (
        f"<div class='tile'><div class='tile-label'>当前点差(上证−创业板)</div>"
        f"<div class='tile-value' style='color:{_PAL['ink']}'>{now:+.0f}</div>"
        f"<div class='tile-sub'>5年包络 {rc['min_spread']:+.0f} ~ {rc['max_spread']:+.0f}</div></div>"
        f"{_meter('周期位置(包络·headline)', rc['env_pos'], '作者口径 · ' + zone)}"
        f"{_meter('历史稀有度(5yr秩分位)', rc['rank_pct'], 'house口径 · 分布偏态时会与包络位置背离')}"
        f"<div class='tile'><div class='tile-label'>结构漂移</div>"
        f"<div class='tile-value' style='color:{_PAL['ink_sec']}'>{rc['drift_pts_per_yr']:+.0f} 点/年</div>"
        f"<div class='tile-sub'>10yr OLS · &lt;0 = 创业板结构性跑赢</div></div>")
    run_txt = f"{rc['run_dir'] or '—'} {rc['run']}日" if rc.get("run_dir") else "—"
    b_tiles = (
        f"<div class='tile'><div class='tile-label'>短期动量(20日)</div>"
        f"<div class='tile-value'>{rc['mom_now']:+.0f} 点</div>"
        f"<div class='tile-sub'>点差近20日净变动</div></div>"
        f"<div class='tile'><div class='tile-label'>连续相对强弱</div>"
        f"<div class='tile-value'>{run_txt}</div>"
        f"<div class='tile-sub'>spread 同向连续天数</div></div>")
    hint = ("相对周期律:点差 = 上证综指 − 创业板指(点)。<b>headline=当前点差在5年包络[下沿,上沿]内的位置</b>"
            "(作者口径:极点才有回归方向,中枢无edge = 仅相对回归风险解除,非绝对涨跌)。"
            "历史稀有度=同窗口秩分位(house口径:分布偏态时与包络位置背离,两者并存看)。"
            "结构漂移 &lt;0 = 创业板长期跑赢(约 −40~−50 点/年)。上沿→回归利创业板,下沿→回归利上证。")
    out = (f"<div class='tiles-row'>{tiles}</div>"
           f"<div style='margin-top:10px'>{_chip('周期: ' + zone, zc)}</div>"
           f"<div class='tiles-row' style='margin-top:12px'>{b_tiles}</div>"
           f"<div class='hint' style='margin-top:8px'>{hint}</div>")
    if fig_html:
        out += (f"<div class='hint' style='margin-top:10px'>通道线 = 5年滚动上下沿的线性趋势拟合"
                f"(看漂移方向 & 振幅收窄/展宽,非精确边缘);当前精确包络见上方 tile。</div>"
                f"<div style='margin-top:4px'>{fig_html}</div>")
    return out


def _cycle_stage_chip(diag: dict) -> str:
    """D: 顶部一行「周期定位」= ③估值zone + ①任一宽基偏离极值 → 定性合成。"""
    val_zone = (diag.get("valuation") or {}).get("zone", "—")
    extremes = []
    for info in (diag.get("indices") or {}).values():
        if not info.get("valid"):
            continue
        pct = ((info.get("diagnosis") or {}).get("deviation") or {}).get("pct")
        if pct is not None and not pd.isna(pct) and (pct <= 0.05 or pct >= 0.95):
            extremes.append((info["name"], pct))
    if extremes:
        ext_txt = "、".join(f"{n}偏离{p:.0%}" for n, p in extremes[:2])
    else:
        ext_txt = "宽基偏离均中性"
    return (f"<div class='hint' style='margin-bottom:12px'>📍 周期定位:估值『{val_zone}』· {ext_txt}"
            f" → 估值+趋势极值合成读大盘阶段(定性参考,非交易信号)</div>")


_CSS = """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9;--hover:#f4f3ef}
[data-theme="dark"]{--surface:#1a1a19;--plane:#0d0d0d;--ink:#ffffff;--ink-sec:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--hover:#262624}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px;max-width:1200px;margin:0 auto}
.topbar{display:flex;justify-content:space-between;align-items:center;margin:0 0 4px}
h1{font-size:22px;margin:0}
h2{font-size:16px;margin:0 0 10px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:16px;margin-bottom:16px}
section h2{margin-top:0}
table.stat{border-collapse:collapse;width:100%;font-size:13px}
table.stat th,table.stat td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:left}
table.stat th{color:var(--muted);font-weight:600}
table.stat tbody tr:hover{background:var(--hover)}
.tiles-row{display:flex;gap:16px;flex-wrap:wrap}
.tile{flex:1;min-width:160px;background:var(--plane);border:1px solid var(--grid);border-radius:8px;padding:12px}
.tile-label{color:var(--muted);font-size:12px;margin-bottom:4px}
.tile-value{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.tile-sub{color:var(--ink-sec);font-size:11px;margin-top:6px}
.meter{height:6px;background:var(--grid);border-radius:3px;margin-top:8px;overflow:hidden}
.meter-fill{height:100%;border-radius:3px}
.hint{color:var(--muted);font-size:12px}
.sig-list{margin:0;padding-left:20px;font-size:14px;line-height:2}
#theme-btn{background:var(--surface);border:1px solid var(--grid);color:var(--ink-sec);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:15px;line-height:1}
#theme-btn:hover{background:var(--hover)}
"""


_JS = """
function _isDark(){return document.documentElement.getAttribute('data-theme')==='dark';}
function _applyPlotly(dark){if(!window.Plotly)return;var u={'paper_bgcolor':dark?'#1a1a19':'#fcfcfb','plot_bgcolor':dark?'#1a1a19':'#fcfcfb','font.color':dark?'#ffffff':'#0b0b0b'};['xaxis','xaxis2','yaxis','yaxis2'].forEach(function(a){u[a+'.gridcolor']=dark?'#2c2c2a':'#e1e0d9';u[a+'.zerolinecolor']=dark?'#383835':'#c3c2b7';});document.querySelectorAll('.plotly-graph-div').forEach(function(gd){try{Plotly.relayout(gd,u);}catch(e){}});}
function toggleTheme(){var cur=document.documentElement.getAttribute('data-theme');var isDark=(cur==='dark');var next=isDark?'light':'dark';document.documentElement.setAttribute('data-theme',next);var b=document.getElementById('theme-btn');if(b)b.textContent=next==='dark'?'☀️':'🌙';_applyPlotly(next==='dark');}
window.addEventListener('DOMContentLoaded',function(){var b=document.getElementById('theme-btn');if(b)b.textContent=_isDark()?'☀️':'🌙';_applyPlotly(_isDark());});
"""


def render_index_timing(store, output_path, period: int = ti.MA_PERIOD,
                        lookback: int | None = None, title: str = "指数择时层看板") -> str:
    """组装六件套 + ⑦相对周期律,写出离线自包含 HTML。返回输出路径。"""
    diag = dz.diagnose_layer(store, period=period, lookback=lookback)
    # ⑦ 相对周期律: 点差图(若有效,它最先出现 → 承载 plotly.js 首加载;① 首图改 False 避免重复加载 ~3MB)
    rc = diag.get("relative_cycle") or {}
    rc_fig_html = ""
    if rc.get("valid"):
        try:
            _sp = ti.relative_spread_series(
                store.get_index_daily_series(rc["benchmark"])["close"],
                store.get_index_daily_series(rc["growth"])["close"])
            if len(_sp):
                rc_fig_html = _relative_cycle_figure(rc, _sp).to_html(
                    full_html=False, include_plotlyjs=True)
        except Exception:  # noqa: BLE001
            rc_fig_html = ""
    figs_html, first = [], (rc_fig_html == "")   # ⑦ 已加载 plotly.js → ① 首图不再重复
    for sym, nm in dz.BROAD_INDICES:
        df = store.get_index_daily_series(sym)
        if len(df) < period:
            continue
        fig = _deviation_figure(sym, nm, df, period)
        figs_html.append(fig.to_html(full_html=False, include_plotlyjs=first))
        first = False
    last_date = next((i.get("date_last") for i in diag["indices"].values() if i.get("valid")), "—")
    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><style>{_CSS}</style></head><body>"
        f"<div class='topbar'><h1>{title}</h1>"
        f"<button id='theme-btn' onclick='toggleTheme()'>🌙</button></div>"
        f"<div class='meta'>数据截至 {last_date} · {period}日线 · 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>"
        f"{_cycle_stage_chip(diag)}"
        f"<h2>③ 估值开关</h2><section>{_valuation_tile_html(diag['valuation'])}</section>"
        f"<h2>⑥ 市场温度·大小盘温差</h2><section>{_market_temp_html(diag['market_temp'])}</section>"
        f"<h2>⑦ 相对周期律·沪深成长温差</h2><section>{_relative_cycle_html(rc, rc_fig_html)}</section>"
        f"<h2>④ 蓝筹 vs 成长 仓位倾向</h2><section>{_style_card_html(diag['style'])}</section>"
        f"<h2>② 趋势状态</h2><section>{_trend_table_html(diag)}</section>"
        f"<h2>⑤ 有效突破/跌破信号</h2><section>{_signals_html(diag)}"
        f"<div class='hint' style='margin-top:8px'>有效突破/跌破 = 近 5 日内真正穿越 60 日线 + 偏离≥2%"
        f"(S13「收盘价穿越 60 日线 ±2% 以上」);gN=强度(2=±2%、3=±3%)。仅在线上/下但无近期穿越者不计;"
        f"震荡市标注信号谨慎。</div></section>"
        f"<h2>① 偏离极值曲线</h2><section>"
        f"<div class='hint' style='margin-bottom:10px'>主图:收盘价 vs 60日线;副图:偏离度(价格−均线)÷均线,"
        f"虚线=历史极值(红=正极值/超买,蓝=负极值/超卖),黑点=当前。"
        f"S13:偏离接近历史极值(黑点贴近虚线)时有技术拉回力量,可套利。</div>"
        + "".join(figs_html) + "</section>"
        f"<script>{_JS}</script></body></html>"
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
