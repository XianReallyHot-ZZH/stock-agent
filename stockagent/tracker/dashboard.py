"""指数择时层交互式 HTML 看板(plotly,离线自包含)— 五件套(V4 tracker)。

① 偏离极值曲线(close+MA60 主图 / 偏离度副图+历史极值线+当前点)
② 5宽基趋势状态表(60日线上下/均线趋势/突破跌破档位/震荡市)
③ 估值开关 stat tile(沪深300 PE 分位 + 全市场 PB 分位 + zone)
④ 蓝筹 vs 成长 仓位倾向 lean 指标卡
⑤ 当前有效突破/跌破信号列表

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
    dc = dev.dropna()
    mx = float(dc.max()) if len(dc) else float("nan")
    mn = float(dc.min()) if len(dc) else float("nan")
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4], vertical_spacing=0.10,
        subplot_titles=(f"{name}({sym}) 收盘价 vs {period}日线", "偏离度 close/MA − 1"))
    fig.add_trace(go.Scatter(x=close.index, y=close, name="收盘",
                             line=dict(color=_PAL["series_1"], width=2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=ma.index, y=ma, name=f"MA{period}",
                             line=dict(color=_PAL["muted"], width=1.5, dash="dash")), row=1, col=1)
    fig.add_trace(go.Scatter(x=dev.index, y=dev, name="偏离度",
                             line=dict(color=_PAL["ink_sec"], width=1.5)), row=2, col=1)
    if not pd.isna(mx):
        fig.add_hline(y=mx, row=2, col=1, line=dict(color=_PAL["pos_extreme"], width=1, dash="dot"),
                      annotation_text=f"正极值 +{mx:.0%}", annotation_position="top right")
    if not pd.isna(mn):
        fig.add_hline(y=mn, row=2, col=1, line=dict(color=_PAL["neg_extreme"], width=1, dash="dot"),
                      annotation_text=f"负极值 {mn:.0%}", annotation_position="bottom right")
    if not pd.isna(dev.iloc[-1]):
        fig.add_trace(go.Scatter(x=[dev.index[-1]], y=[dev.iloc[-1]], mode="markers+text",
                                 marker=dict(size=10, color=_PAL["ink"]),
                                 text=[f"现在 {dev.iloc[-1]:.1%}"], textposition="top center",
                                 showlegend=False), row=2, col=1)
    fig.update_layout(
        height=460, margin=dict(l=50, r=20, t=50, b=30),
        paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
        font=dict(color=_PAL["ink"], family="system-ui, sans-serif"), showlegend=False)
    fig.update_xaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"])
    fig.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["baseline"])
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
        bo_dir = bo["direction"]
        bo_label = {"up": "突破", "down": "跌破", "none": "中性"}[bo_dir] + f" g{bo['grade']}"
        bo_c = _PAL["good"] if bo_dir == "up" else _PAL["critical"] if bo_dir == "down" else _PAL["muted"]
        choppy = f'<span style="color:{_PAL["warning"]}">⚠震荡</span>' if dg["choppy"] else "趋势"
        dev_txt = f"{dev['pct']:.0%}位" if not pd.isna(dev["pct"]) else "—"
        rows.append(
            f"<tr><td>{info['name']}</td>"
            f"<td>{_chip(above, above_c)}</td><td style='text-align:center'>{mtrend}</td>"
            f"<td>{_chip(bo_label, bo_c)}</td><td>{choppy}</td><td style='font-variant-numeric:tabular-nums'>{dev_txt}</td></tr>")
    return ("<table class='stat'><thead><tr>"
            "<th>指数</th><th>60日线</th><th>均线趋势</th><th>突破跌破</th><th>市态</th><th>偏离分位</th>"
            "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


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
        if bo["direction"] in ("up", "down") and bo["grade"] >= 2:
            dir_cn = "有效突破▲" if bo["direction"] == "up" else "有效跌破▼"
            c = _PAL["good"] if bo["direction"] == "up" else _PAL["critical"]
            warn = " <span class='hint'>(震荡市,信号谨慎)</span>" if dg["choppy"] else ""
            sigs.append(f"<li>{info['name']}({sym}): {_chip(dir_cn + ' g' + str(bo['grade']), c)}{warn}</li>")
    if not sigs:
        return "<p class='hint'>当前无有效突破/跌破信号(grade ≥ 2)</p>"
    return "<ul class='sig-list'>" + "".join(sigs) + "</ul>"


_CSS = """
:root{--surface:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);font-family:system-ui,-apple-system,'Segoe UI',sans-serif;padding:24px;max-width:1200px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:0 0 10px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
section{background:var(--surface);border:1px solid var(--grid);border-radius:10px;padding:16px;margin-bottom:16px}
section h2{margin-top:0}
table.stat{border-collapse:collapse;width:100%;font-size:13px}
table.stat th,table.stat td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:left}
table.stat th{color:var(--muted);font-weight:600}
table.stat tbody tr:hover{background:#f4f3ef}
.tiles-row{display:flex;gap:16px;flex-wrap:wrap}
.tile{flex:1;min-width:160px;background:var(--plane);border:1px solid var(--grid);border-radius:8px;padding:12px}
.tile-label{color:var(--muted);font-size:12px;margin-bottom:4px}
.tile-value{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.2}
.tile-sub{color:var(--ink-sec);font-size:11px;margin-top:6px}
.meter{height:6px;background:var(--grid);border-radius:3px;margin-top:8px;overflow:hidden}
.meter-fill{height:100%;border-radius:3px}
.hint{color:var(--muted);font-size:12px}
.sig-list{margin:0;padding-left:20px;font-size:14px;line-height:2}
"""


def render_index_timing(store, output_path, period: int = ti.MA_PERIOD,
                        lookback: int | None = None, title: str = "指数择时层看板") -> str:
    """组装五件套,写出离线自包含 HTML。返回输出路径。"""
    diag = dz.diagnose_layer(store, period=period, lookback=lookback)
    figs_html, first = [], True
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
        f"<h1>{title}</h1>"
        f"<div class='meta'>数据截至 {last_date} · {period}日线 · 生成于 {datetime.now():%Y-%m-%d %H:%M}</div>"
        f"<h2>③ 估值开关</h2><section>{_valuation_tile_html(diag['valuation'])}</section>"
        f"<h2>④ 蓝筹 vs 成长 仓位倾向</h2><section>{_style_card_html(diag['style'])}</section>"
        f"<h2>② 趋势状态</h2><section>{_trend_table_html(diag)}</section>"
        f"<h2>⑤ 有效突破/跌破信号</h2><section>{_signals_html(diag)}</section>"
        f"<h2>① 偏离极值曲线</h2><section>" + "".join(figs_html) + "</section>"
        f"</body></html>"
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return str(out)
