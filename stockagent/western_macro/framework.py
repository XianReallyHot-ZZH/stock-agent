"""宏观框架看板 (Macro Framework Dashboard · Phase 3 北向目标 · ADR-0001 只读旁路).

把博主的**思维框架**(因果链 利率→曲线→美元→金属→能源→权益)做成一个**纯数据跟踪+分析**看板:
每个宏观资产给「当前在哪」的数据分析(趋势/位置/结构), 沿因果链铺开。**不含任何 claim/台账/命中率**
—— 北向目标「把框架编成规则 → 阶段定位器」的落地, track record 只当过滤断语, 不再纠结准不准。

只读诊断, 永不喂 A股轮动引擎 (ADR-0001)。
政策节点(债务压力/化债工具/实际利率/期限溢价)无数据序列 → 仅作框架图的链路上下文, 不做面板。

复用 (不重写):
  - score.series_for · tracker.diagnose.diagnose_index · tracker.indicators.{ma_series,deviation_series}
  - stage.{classify_stage, gold_stage_snapshot, _rolling_dev_pct, 常量} —— price 家族直接复用黄金阶段树
  - dashboard.{_driver_svg, _LIGHT, _DARK, _css, _gold_stage_block} —— 框架图 + 主题 + 黄金阶段块
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stockagent.tracker.diagnose import diagnose_index
from stockagent.tracker.indicators import deviation_series, fresh_cross_direction, ma_series
from .score import series_for
from .stage import (GOLD_DEV_HIGH, GOLD_DEV_LOW, GOLD_DEV_LOOKBACK, GOLD_FRESH,
                    GOLD_MA_PERIOD, MIN_BARS, _rolling_dev_pct, classify_stage)

MA_PERIOD = GOLD_MA_PERIOD
DEV_LOOKBACK = GOLD_DEV_LOOKBACK
DEV_LOW = GOLD_DEV_LOW
DEV_HIGH = GOLD_DEV_HIGH

# 资产 → 分析家族 (price=5阶段树 / yield=水平分位+trend / curve=倒挂/陡峭)
ASSET_FAMILIES: dict[str, str] = {
    "美债2Y": "yield", "美债10Y": "yield", "美债30Y": "yield",
    "2s10s": "curve",
    "美元指数": "price", "黄金": "price", "白银": "price", "铜": "price",
    "原油": "price", "标普500": "price", "纳斯达克": "price", "道琼斯": "price", "A股": "price",
}

# 因果链节点 (顺序 = 框架传导方向)
CHAIN_NODES: list[tuple[str, list[str]]] = [
    ("利率 · 美债收益率", ["美债2Y", "美债10Y", "美债30Y"]),
    ("曲线 · 2s10s", ["2s10s"]),
    ("美元 · DXY", ["美元指数"]),
    ("金属", ["黄金", "白银", "铜"]),
    ("能源", ["原油"]),
    ("权益", ["标普500", "纳斯达克", "道琼斯", "A股"]),
]


def _rolling_pct(s: pd.Series, lookback: int = DEV_LOOKBACK, min_bars: int = 20) -> pd.Series:
    """trailing-lookback 水平分位 (防前视): 每 bar 用截至当时的末 lookback 个原始值算 (w<cur).sum()/len。
    与 _rolling_dev_pct 同构, 但作用在原始值(水平)而非偏离。0=区间最低, 1=区间最高。"""
    vals = s.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    recent: list[float] = []
    for i in range(len(vals)):
        v = vals[i]
        if not np.isnan(v):
            recent.append(float(v))
            if len(recent) > lookback:
                recent = recent[-lookback:]
        if len(recent) < min_bars:
            continue
        cur = recent[-1]
        arr = np.asarray(recent)
        out[i] = float((arr < cur).sum()) / len(arr)
    return pd.Series(out, index=s.index)


def _yield_label(lvl_pct: float, ma_trend_up) -> str:
    pos = "高位" if (lvl_pct == lvl_pct and lvl_pct >= DEV_HIGH) else (
        "低位" if (lvl_pct == lvl_pct and lvl_pct <= DEV_LOW) else "中位")
    trend = "上行" if ma_trend_up is True else ("下行" if ma_trend_up is False else "—")
    return f"{pos}·{trend}"


def _curve_label(spread: float, ma_trend_up) -> str:
    shape = "倒挂" if spread < 0 else "正常"
    trend = "陡峭化" if ma_trend_up is True else ("趋平" if ma_trend_up is False else "—")
    return f"{shape}·{trend}"


def asset_analysis(asset: str, store, asof: Optional[str] = None) -> dict:
    """单个资产的统一指标盘 + 家族标签 (只读, 不喂引擎)。"""
    s = series_for(asset, store)
    if s is None or len(s) == 0:
        return {"valid": False, "asset": asset, "family": ASSET_FAMILIES.get(asset, "price"),
                "reason": "无数据"}
    s = s.sort_index().astype(float)
    if asof:
        s = s[s.index <= asof]
    if len(s) < MIN_BARS:
        return {"valid": False, "asset": asset, "family": ASSET_FAMILIES.get(asset, "price"),
                "reason": "数据不足", "n_bars": len(s)}

    family = ASSET_FAMILIES.get(asset, "price")
    diag = diagnose_index(s, MA_PERIOD)
    dev_pct = float(_rolling_dev_pct(deviation_series(s, MA_PERIOD), DEV_LOOKBACK).iloc[-1])
    lvl_pct = float(_rolling_pct(s, DEV_LOOKBACK).iloc[-1])
    fresh = fresh_cross_direction(diag.get("cross"), GOLD_FRESH)
    tr = diag.get("trend") or {}
    bo = diag.get("breakout") or {}
    cur = float(s.iloc[-1])

    def _ret(n):
        return float(cur / float(s.iloc[-1 - n]) - 1.0) if len(s) > n else None

    if family == "price":
        info = classify_stage(diag, dev_pct, fresh, bool(diag.get("choppy")), len(s))
        label = info["stage"]
    elif family == "yield":
        label = _yield_label(lvl_pct, tr.get("ma_trend_up"))
    else:  # curve
        label = _curve_label(cur, tr.get("ma_trend_up"))

    return {
        "valid": True, "asset": asset, "family": family,
        "current": cur, "date": str(s.index[-1]), "n_bars": len(s),
        "above_ma": tr.get("above_ma") is True, "ma_trend_up": tr.get("ma_trend_up") is True,
        "price_vs_ma_pct": tr.get("price_vs_ma_pct"),
        "dev_pct": dev_pct, "lvl_pct": lvl_pct,
        "breakout_grade": bo.get("grade"), "breakout_label": bo.get("label"),
        "fresh_cross": fresh, "choppy": bool(diag.get("choppy")),
        "ret_20": _ret(20), "ret_60": _ret(60), "ret_250": _ret(250),
        "label": label,
    }


# ---- 渲染 ----
def _label_color(label: str, c: dict) -> str:
    if any(k in label for k in ("筑底", "反弹", "低位", "正常")):
        return c["edge"]
    if "趋势" in label:
        return c["accent"]
    if any(k in label for k in ("头部", "高位", "倒挂")):
        return c["hit"]
    if any(k in label for k in ("回调", "趋平")):
        return c["miss"]
    return c["ink2"]


def _asset_chart(asset: str, store, an: dict, c: dict):
    """单资产时序图: close + MA60 + 右轴近12月偏离分位(超卖/超买带) + 当前点(色=标签)。"""
    s = series_for(asset, store)
    if s is None or len(s) == 0:
        return None
    s = s.sort_index().astype(float)
    n = min(500, len(s))
    g = s.iloc[-n:]
    ma60 = ma_series(s, MA_PERIOD).iloc[-n:]
    devp = _rolling_dev_pct(deviation_series(s, MA_PERIOD), DEV_LOOKBACK).iloc[-n:]
    dt = pd.to_datetime(list(g.index))
    label_col = _label_color(an.get("label", ""), c)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in g.values], mode="lines", name=asset,
                             line=dict(color=c["accent"], width=1.4),
                             hovertemplate="%{x|%Y-%m-%d}  %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in ma60.values], mode="lines", name="MA60",
                             line=dict(color=c["ink2"], width=1.0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) if v == v else None for v in devp.values],
                             mode="lines", name="偏离分位", yaxis="y2",
                             line=dict(color=c["open"], width=1.0, dash="dot"),
                             fill="tozeroy", hovertemplate="偏离分位 %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[dt[-1]], y=[float(g.iloc[-1])], mode="markers",
                             marker=dict(size=12, color=label_col, symbol="diamond",
                                         line=dict(width=1.5, color="#ffffff")), hoverinfo="skip"))
    for y0, y1, col in [(0.0, DEV_LOW, c["edge"]), (DEV_HIGH, 1.0, c["miss"])]:
        fig.add_shape(type="rect", xref="paper", yref="y2", x0=0, x1=1, y0=y0, y1=y1,
                      fillcolor=col, opacity=0.10, line_width=0, layer="below")
    from .dashboard import ASSET_YLABEL
    ylabel = ASSET_YLABEL.get(asset, asset)   # 左轴: 资产单位(价/收益率/指数...)
    fig.update_layout(margin=dict(l=64, r=50, t=8, b=28), height=240, showlegend=False,
                      paper_bgcolor=c["surface"], plot_bgcolor=c["bg"],
                      font=dict(size=11, color=c["ink"]),
                      xaxis=dict(type="date", tickformat="%Y-%m", gridcolor=c["border"]),
                      yaxis=dict(title=dict(text=ylabel, font=dict(size=10)), gridcolor=c["border"]),
                      yaxis2=dict(overlaying="y", side="right", range=[0, 1], showgrid=False,
                                  title=dict(text="偏离分位", font=dict(size=9))))
    return fig


def _fmt_pct(x, sign=False):
    if x is None or x != x:
        return "—"
    return f"{x*100:+.1f}%" if sign else f"{x*100:.0f}%"


def _overview_table(an_by_asset: dict, c: dict) -> str:
    rows = []
    for _, assets in CHAIN_NODES:
        for a in assets:
            an = an_by_asset.get(a)
            if not an or not an.get("valid"):
                rows.append(f'<tr><td>{a}</td><td colspan="7" class="muted">{(an or {}).get("reason","无数据")}</td></tr>')
                continue
            vsma = an["price_vs_ma_pct"]
            vsma_txt = f"{'上' if an['above_ma'] else '下'} {_fmt_pct(vsma, sign=True)}" if (vsma is not None and vsma == vsma) else "—"
            col = _label_color(an["label"], c)
            rows.append(
                f"<tr><td><b>{a}</b> <span class='muted' style='font-size:11px'>{an['family']}</span></td>"
                f"<td>{an['current']:.2f}</td><td class='muted'>{vsma_txt}</td>"
                f"<td>{_fmt_pct(an['dev_pct'])}</td><td>{_fmt_pct(an['lvl_pct'])}</td>"
                f"<td class='muted'>{_fmt_pct(an['ret_60'], sign=True)}</td>"
                f"<td class='muted'>{_fmt_pct(an['ret_250'], sign=True)}</td>"
                f"<td><span class='badge' style='background:{col};color:#fff'>{an['label']}</span></td></tr>")
    return ("<table><tr><th>标的</th><th>现价</th><th>MA60侧</th><th>偏离分位</th>"
            "<th>水平分位</th><th>近60日</th><th>近250日</th><th>状态</th></tr>"
            + "".join(rows) + "</table>")


def _gold_micro_block(store, c: dict) -> str:
    """🔬 黄金微观紧缺(L2/L3/L4 实证): COMEX库存 / CFTC非商业(投机)净仓位+分位 / 央行购金节奏。
    JZ 框架的微观证据层: 库存↓=紧缺, 投机净多单极值=泡沫预警, 央行购金=底的锚。"""
    ci = store.get_comex_inventory("GC")
    cf = store.get_cftc_position("GC")
    cb = store.get_cb_gold("CN")
    if ci.empty and cf.empty and cb.empty:
        return ('<div class="chart"><div class="chart-t">🔬 黄金微观紧缺</div>'
                '<div class="muted">无微观数据。先跑 <code>python scripts/backfill_gold_micro.py</code>。</div></div>')

    def chip(label, val, sub="", color=None):
        vs = f' style="color:{color}"' if color else ""
        return (f'<div class="card"><div class="card-v"{vs}>{val}</div><div class="card-l">{label}</div>'
                f'{f"<div class=card-s>{sub}</div>" if sub else ""}</div>')

    # chips
    chips = ['<div class="cards">']
    if not ci.empty:
        last = float(ci.iloc[-1])
        yago = float(ci.iloc[-252]) if len(ci) > 252 else float(ci.iloc[0])
        chg = (last / yago - 1) * 100
        col = c["miss"] if chg < -5 else (c["edge"] if chg > 5 else c["ink2"])
        chips.append(chip("COMEX金库存", f"{last:.0f}吨", f"近1年 {chg:+.0f}%", col))
    if not cf.empty:
        net = cf["net_pos"].astype(float)
        cur = float(net.iloc[-1])
        look = net.iloc[-104:] if len(net) > 104 else net   # 近2年
        pct = float((look < cur).sum()) / len(look) if len(look) else 0.5
        col = c["hit"] if pct >= 0.85 else (c["edge"] if pct <= 0.15 else c["ink2"])
        chips.append(chip("投机净仓位", f"{cur/1000:+.0f}k", f"近2年分位 {pct:.0%}（≥85%泡沫）", col))
    if not cb.empty:
        moms = cb["mom"].astype(float).iloc[-12:]
        avg = float(moms.mean()) if len(moms) else 0.0
        chips.append(chip("央行(中国)购金", f"+{avg:.2f}%/月", f"近12月均值 @ {cb.index[-1]}", c["accent"]))
    chips.append('</div>')
    parts = [f'<div class="chart"><div class="chart-t">🔬 黄金微观紧缺 '
             f'<span class="muted">(L2库存/L3紧缺/L4投机泡沫 · JZ 框架微观证据 · GOFO/全球ETF无源待补)</span></div>',
             "".join(chips)]

    # 图: COMEX库存 (近2y)
    if not ci.empty:
        s = ci.iloc[-500:]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pd.to_datetime(list(s.index)), y=[float(v) for v in s.values],
                                 mode="lines", name="COMEX金库存", line=dict(color=c["accent"], width=1.4),
                                 fill="tozeroy", hovertemplate="%{x|%Y-%m-%d}  %{y:.0f}吨<extra></extra>"))
        fig.update_layout(margin=dict(l=56, r=16, t=6, b=24), height=180, showlegend=False,
                          paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                          xaxis=dict(type="date", tickformat="%Y-%m", gridcolor=c["border"]),
                          yaxis=dict(title="吨", gridcolor=c["border"]))
        parts.append(f'<div class="chart-t" style="margin-top:8px">COMEX 黄金库存(吨 · ↓=紧缺/逼空压力)</div>' + fig.to_html(False, False, "micro_comex"))

    # 图: CFTC 投机净仓位 + 泡沫分位带 (近3y)
    if not cf.empty:
        net = cf["net_pos"].astype(float).iloc[-156:]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pd.to_datetime(list(net.index)), y=[float(v) for v in net.values],
                                 mode="lines", name="投机净仓位", line=dict(color=c["hit"], width=1.4),
                                 hovertemplate="%{x|%Y-%m-%d}  净 %{y:,.0f}<extra></extra>"))
        hi = float(cf["net_pos"].astype(float).iloc[-156:].quantile(0.85)) if len(cf) > 10 else 0
        fig.add_hline(y=hi, line_dash="dot", line_color=c["miss"], annotation_text="85%分位(泡沫区)", annotation_font_size=9)
        fig.update_layout(margin=dict(l=56, r=16, t=6, b=24), height=180, showlegend=False,
                          paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                          xaxis=dict(type="date", tickformat="%Y-%m", gridcolor=c["border"]),
                          yaxis=dict(title="净仓位(手)", gridcolor=c["border"]))
        parts.append('<div class="chart-t" style="margin-top:8px">CFTC 非商业(投机)净仓位(↑极值=泡沫预警)</div>' + fig.to_html(False, False, "micro_cftc"))

    # 图: 央行购金 (中国, 月频, 末段2y)
    if not cb.empty:
        sub = cb.iloc[-24:]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=pd.to_datetime(list(sub.index)), y=sub["value"].astype(float).values,
                             name="黄金储备", marker_color=c["accent"], yaxis="y",
                             hovertemplate="%{x|%Y-%m}  %{y:.0f}<extra></extra>"))
        parts.append('<div class="chart-t" style="margin-top:8px">中国央行黄金储备(月 · 持续增持=底的锚)</div>' + fig.to_html(False, False, "micro_cb"))
    parts.append('</div>')
    return "".join(parts)


def render_macro_framework(store, out_path: Path, asof: str = "") -> Path:
    """渲染宏观框架看板 → data/macro_framework.html (纯数据·只读·不喂引擎)。"""
    from .dashboard import _DARK, _LIGHT, _css, _driver_svg

    D, L = _DARK, _LIGHT
    all_assets = [a for _, assets in CHAIN_NODES for a in assets]
    an_by_asset = {a: asset_analysis(a, store) for a in all_assets}

    # 分节点图 (统一普通数据跟踪展示; 黄金定位器由 ledger 看板单独做)
    first = [True]  # plotly.js 只嵌一次
    node_html: list[str] = []
    for node, assets in CHAIN_NODES:
        parts = [f'<h2>🔗 {node}</h2>']
        for a in assets:
            an = an_by_asset.get(a) or {}
            fig = _asset_chart(a, store, an, L)
            if fig is None:
                parts.append(f'<div class="chart"><div class="chart-t">{a}</div><div class="muted">无数据</div></div>')
                continue
            sub = (f"{an.get('label','—')} · MA60{'↑' if an.get('ma_trend_up') else '↓'} "
                   f"· 偏离{_fmt_pct(an.get('dev_pct'))} · {_fmt_pct(an.get('ret_60'), sign=True)}/60日")
            div_id = f"fw_{a.replace(' ','_')}"
            parts.append(f'<div class="chart"><div class="chart-t">{a} <span class="muted">({sub})</span></div>'
                         + fig.to_html(full_html=False, include_plotlyjs=first[0], div_id=div_id)
                         + '</div>')
            first[0] = False
        node_html.append("\n".join(parts))

    n_valid = sum(1 for an in an_by_asset.values() if an.get("valid"))
    gold_micro_html = _gold_micro_block(store, D)
    html = f"""<!doctype html><html lang="zh" data-theme="light"><head><meta charset="utf-8">
<title>宏观框架看板 · {asof}</title>
<style>
:root{{{_css(L)}}}[data-theme="dark"]{{{_css(D)}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,"Microsoft YaHei",sans-serif}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:24px 0 8px;color:var(--ink2)}}
.muted{{color:var(--ink2)}}
.banner{{padding:10px 14px;border-radius:8px;margin:10px 0;font-size:13px}}
.banner.info{{background:var(--surface);border:1px solid var(--border)}}
table{{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:13px;white-space:nowrap}}
th{{background:color-mix(in srgb,var(--accent) 10%,var(--surface));color:var(--ink2);font-weight:600;font-size:12px}}
tr:hover{{background:color-mix(in srgb,var(--accent) 6%,transparent)}}
.badge{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.driver{{width:100%;max-width:1180px;height:auto;margin:8px 0}}
.chart{{margin:14px 0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px}}
.chart-t{{font-size:13px;color:var(--ink2);margin-bottom:4px;font-weight:600}}
.toggle{{position:fixed;top:14px;right:18px;cursor:pointer;background:var(--surface);border:1px solid var(--border);
color:var(--ink);border-radius:8px;padding:6px 12px;font-size:12px}}
.foot{{margin-top:24px;color:var(--ink2);font-size:11px;border-top:1px solid var(--border);padding-top:10px}}
</style></head><body>
<button class="toggle" onclick="toggle()">🌓 浅/深</button>
<div class="wrap">
<h1>🌐 宏观框架看板 <span class="muted" style="font-size:13px">— JZ 思维框架的数据跟踪与结构分析 · 因果链 利率→曲线→美元→金属→能源→权益</span></h1>
<div class="muted" style="font-size:12px">截至 {asof} · 纯数据跟踪与分析, 不含预测台账/命中率 · 只读诊断·不喂引擎 (ADR-0001) · {n_valid}/{len(all_assets)} 标的已覆盖</div>
<div class="banner info">📍 <b>怎么读</b>: 总览表看全局状态(色=阶段/位置); 因果框架图看传导链; 分节点图看每个资产的趋势+偏离。price 标的=5阶段(筑底/反弹/趋势/头部/回调), 利率=水平分位(高/中/低)+趋势, 曲线=倒挂/正常+陡峭/趋平。偏离分位 0=区间最超卖, 1=最超买。</div>

<h2>🗺️ 因果框架(博主思维链 · 蓝点=有数据标的 · 政策节点为链路上下文)</h2>
{_driver_svg(L)}

<h2>📊 总览(全资产 · 一屏看清当前在哪)</h2>
{_overview_table(an_by_asset, L)}

{''.join(node_html)}

<h2>🔬 黄金微观紧缺(L2/L3/L4 实证 · JZ 框架微观证据)</h2>
{gold_micro_html}

<div class="foot">
⚠ 纯数据诊断看板, 不构成投资建议。阶段/位置标签由价格结构规则(MA60 + 近12月偏离/水平分位)给出, 非预测。
政策节点(债务压力/化债工具/实际利率/期限溢价)无公开数据序列, 仅在框架图作链路上下文, 不做面板。
数据来自 AkShare(DXY 6腿重算 / 铜用沪铜CU0)。
</div></div>
<script>
const PAL={{dark:{{paper:"#1a1a19",plot:"#0d0d0c",ink:"#ffffff",grid:"#2c2c2a"}},
           light:{{paper:"#ffffff",plot:"#f7f7f5",ink:"#1a1a1a",grid:"#e2e2dd"}}}};
function applyPlotly(t){{const p=PAL[t];document.querySelectorAll('.plotly-graph-div').forEach(function(gd){{
 Plotly.relayout(gd,{{"paper_bgcolor":p.paper,"plot_bgcolor":p.plot,"font.color":p.ink,"xaxis.gridcolor":p.grid,"yaxis.gridcolor":p.grid}});}});}}
function toggle(){{var h=document.documentElement;var t=h.dataset.theme==='dark'?'light':'dark';h.dataset.theme=t;applyPlotly(t);}}
</script></body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
