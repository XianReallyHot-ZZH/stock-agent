"""西方宏观预测台账看板 (ADR-0001 · read-only 旁路 · Q10)。

Renders data/western_macro.html from the claim ledger + settlements + rules + driver map + coverage.
Pure static HTML (CSS light/dark toggle, SVG driver map) — no Plotly for v1, no LLM, no 微信.
Clone the tracker dashboard's shape but lighter. The fence (ADR-0001) holds: this is diagnostic
for the human, never feeds the rotation engine.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import drivers

import re

import pandas as pd
import plotly.graph_objects as go

_LIGHT = {
    "bg": "#f7f7f5", "surface": "#ffffff", "ink": "#1a1a1a", "ink2": "#555",
    "accent": "#0b6bcb", "edge": "#1a8a3a", "hit": "#b8860b", "miss": "#b03a2e",
    "open": "#7a7a7a", "border": "#e2e2dd",
}
_DARK = {
    "bg": "#0d0d0c", "surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7",
    "accent": "#5aa9ff", "edge": "#3ec96f", "hit": "#e0b021", "miss": "#ff6b6b",
    "open": "#8a8a85", "border": "#2c2c2a",
}
_GROUP_ORDER = ["debt", "rates", "fx", "metal", "energy", "equity", "flow"]
_TYPE_CN = {"direction": "方向", "range": "区间", "level": "点位", "timing": "时点", "scenario": "情景"}
_DIR_CN = {"up": "涨", "down": "跌", "flat": "震荡"}
ASSET_YLABEL = {
    "美元指数": "DXY 美元指数水平",
    "黄金": "COMEX黄金 期货价(USD)",
    "白银": "COMEX白银 期货价(USD)",
    "美债2Y": "2年期美债 收益率(%)",
    "美债10Y": "10年期美债 收益率(%)",
    "美债30Y": "30年期美债 收益率(%)",
    "2s10s": "10年减2年 利差(%)",
    "标普500": "标普500 指数",
    "纳斯达克": "纳斯达克 指数",
    "道琼斯": "道琼斯 指数",
    "原油": "WTI原油 期货价(USD)",
    "铜": "沪铜主连 期货价(元/吨)",
    "有色": "沪铜主连(有色代理,元/吨)",
    "A股": "上证综指 指数(000001)",
}


def _status_cell(s: Optional[dict]) -> tuple[str, str]:
    if not s:
        return ("open", "未到期")
    if s["edge"]:
        return ("edge", "本事★")
    return ("hit", "命中") if s["hit"] else ("miss", "未中")


def _state_badge(state: str) -> str:
    cls = {"confirmed": "edge", "vetoed": "miss", "draft": "open"}.get(state, "open")
    txt = {"confirmed": "已确认", "vetoed": "已否决", "draft": "草稿"}.get(state, state)
    return f'<span class="badge {cls}">{txt}</span>'


def _driver_svg(colors: dict) -> str:
    """Lay out the canonical driver-map as an SVG (nodes by group-column, causal edges as lines)."""
    pos: dict[str, tuple[int, int]] = {}
    by_group: dict[str, list] = {}
    for n in drivers.NODES:
        by_group.setdefault(n["group"], []).append(n)
    x = 30
    maxh = 0
    for i, g in enumerate(_GROUP_ORDER):
        members = by_group.get(g, [])
        if not members:
            continue
        for j, n in enumerate(members):
            pos[n["id"]] = (x, 50 + j * 62)
            maxh = max(maxh, 50 + j * 62)
        x += 168
    width, height = x, maxh + 70
    parts = [f'<svg viewBox="0 0 {width} {height}" class="driver" xmlns="http://www.w3.org/2000/svg">']
    # edges first (under nodes)
    for a, b in drivers.EDGES:
        if a in pos and b in pos:
            ax, ay = pos[a]; bx, by = pos[b]
            parts.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{colors["border"]}" stroke-width="1.2"/>')
    # nodes
    for n in drivers.NODES:
        if n["id"] not in pos:
            continue
        x, y = pos[n["id"]]
        fill = colors["surface"] if not n.get("asset") else colors["accent"]
        stroke = colors["accent"] if n.get("asset") else colors["border"]
        txt = "#fff" if n.get("asset") else colors["ink"]
        parts.append(f'<rect x="{x-46}" y="{y-15}" width="92" height="30" rx="6" '
                     f'fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>')
        parts.append(f'<text x="{x}" y="{y+4}" text-anchor="middle" font-size="11" '
                     f'fill="{txt}">{n["label"]}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _asset_fig(asset: str, store, claims: list, sett: dict, c: dict):
    """One Plotly figure: the asset's close series + claim markers (color = verdict) + range bands.
    Lets the human VERIFY settlements against the actual data (not trust a black-box verdict)."""
    from .score import series_for, _value_at_or_before, _value_at_or_after
    s = series_for(asset, store)
    if s is None or len(s) == 0:
        return None
    s = s.sort_index()
    end = s.index[-1]
    start = s.index[max(0, len(s) - 500)]  # ~2y context
    eps = sorted(x for x in (cl.get("episode_date") for cl in claims) if x)
    if eps and eps[0] < start:
        start = eps[0]
    win = s[(s.index >= start) & (s.index <= end)]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pd.to_datetime(list(win.index)), y=[float(v) for v in win.values],
                             mode="lines", name=asset, line=dict(color=c["accent"], width=1.4),
                             hovertemplate="%{x|%Y-%m-%d}  %{y:.2f}<extra></extra>"))
    xs, ys, mcs, txs = [], [], [], []          # 兑现日终点(大实心彩色点)
    exs, eys, ecs = [], [], []                 # 发布日起点(小空心点)
    for cl in claims:
        h = (cl.get("horizon") or "")[:10]
        ep = (cl.get("episode_date") or "")[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}", h) or not re.match(r"\d{4}-\d{2}-\d{2}", ep):
            continue  # event/非日期 horizon → 不画(避免错位)
        yv = _value_at_or_before(s, h)          # 兑现日值
        yve = _value_at_or_after(s, ep)         # 发布日值
        if yv is None or yve is None:
            continue
        st = sett.get(cl["uid"])
        col = c["open"] if not st else (c["edge"] if st["edge"] else c["hit"] if st["hit"] else c["miss"])
        vtxt = ("本事★" if st and st["edge"] else "命中" if st and st["hit"]
                else "未中" if st else "未到期")
        xs.append(pd.to_datetime(h)); ys.append(float(yv)); mcs.append(col)
        exs.append(pd.to_datetime(ep)); eys.append(float(yve)); ecs.append(col)
        _t = _TYPE_CN.get(cl["claim_type"], cl["claim_type"])
        _d = _DIR_CN.get(cl["direction"] or "", "")
        _ad = _DIR_CN.get(st["actual_direction"], "—") if st else "—"
        txs.append(f"发布 {ep} → 兑现 {h}<br>{_t} 预测{_d} · {cl['statement'][:34]}<br>"
                   f"{yve:.2f} → {yv:.2f}  实际{_ad} → {vtxt}")
        # 高亮[发布日,兑现日]窗口:全高竖向色带 + 起止虚线段(=被评分的那段走势)
        fig.add_shape(type="rect", xref="x", yref="paper", x0=ep, x1=h, y0=0, y1=1,
                      fillcolor=col, opacity=0.08, line_width=0, layer="below")
        fig.add_shape(type="line", x0=ep, y0=float(yve), x1=h, y1=float(yv),
                      line=dict(color=col, width=1.6, dash="dot"))
    if xs:
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="markers", name="兑现日",
                                 marker=dict(size=11, color=mcs, line=dict(width=1.5, color="#ffffff")),
                                 text=txs, hoverinfo="text", hoverlabel=dict(bgcolor=c["surface"])))
        fig.add_trace(go.Scatter(x=exs, y=eys, mode="markers", name="发布日",
                                 marker=dict(size=10, color=ecs, symbol="circle-open", line_width=1.5),
                                 hoverinfo="skip"))
    for cl in claims:  # range → 阴影带; level → 目标水平虚线
        x0, x1 = cl.get("episode_date"), (cl.get("horizon") or "")[:10]
        if not re.match(r"\d{4}-\d{2}-\d{2}", x1):
            continue
        if cl["claim_type"] == "range":
            lo, hi = cl.get("range_low"), cl.get("range_high")
            if lo is None or hi is None:
                continue
            fig.add_shape(type="rect", x0=x0 or start, x1=x1, y0=lo, y1=hi,
                          fillcolor=c["edge"], opacity=0.10, line_width=0, layer="below")
        elif cl["claim_type"] == "level" and cl.get("level_value") is not None:
            lv = float(cl["level_value"])
            fig.add_shape(type="line", x0=x0 or start, x1=x1, y0=lv, y1=lv,
                          line=dict(color=c["miss"], width=1.2, dash="dash"))
    ylabel = ASSET_YLABEL.get(asset, asset)
    fig.update_layout(margin=dict(l=64, r=16, t=8, b=28), height=260, showlegend=False,
                      paper_bgcolor=c["surface"], plot_bgcolor=c["bg"],
                      font=dict(size=11, color=c["ink"]),
                      xaxis=dict(type="date", tickformat="%Y-%m", gridcolor=c["border"]),
                      yaxis=dict(title=dict(text=ylabel, font=dict(size=10)), gridcolor=c["border"]))
    return fig


def _charts_html(store, claims, sett, c: dict) -> str:
    """One chart per asset that has claims. plotly.js embedded once (first fig)."""
    parts, first = [], True
    for i, asset in enumerate(sorted({cl["asset"] for cl in claims})):
        ac = [cl for cl in claims if cl["asset"] == asset]
        fig = _asset_fig(asset, store, ac, sett, c)
        if fig is None:
            continue
        parts.append(f'<div class="chart"><div class="chart-t">{asset} '
                     f'<span class="muted">({len(ac)} 条断言 · 点颜色=结果)</span></div>')
        parts.append(fig.to_html(full_html=False, include_plotlyjs=first, div_id=f"chart_{i}"))
        parts.append("</div>")
        first = False
    if not parts:
        return '<div class="muted">无可画时序的标的(无数据)。</div>'
    return "\n".join(parts)


_STAGE_COLOR = {  # 阶段 → theme key
    "筑底/底部震荡": "edge", "反弹初期": "edge", "趋势上行": "accent",
    "头部区域": "hit", "回调下跌": "miss",
}


def _gold_stage_block(store, c: dict) -> str:
    """黄金阶段定位块: chips(阶段/信心/偏离/MA60/驱动) + Plotly(close+MA60+12月偏离分位+当前阶段点)。

    MVP 只读快照(不在 render 里跑回测); 一致性由 wm_gold_stage_eval.py 单独出。
    无黄金数据 → 降级提示。"""
    from .stage import (GOLD_DEV_HIGH, GOLD_DEV_LOW, GOLD_DISCOUNT, gold_stage_snapshot,
                        _rolling_dev_pct)
    from .score import series_for
    from stockagent.tracker.indicators import deviation_series, ma_series

    snap = gold_stage_snapshot(store)
    if not snap.get("valid"):
        return ('<div class="chart"><div class="chart-t">🥇 黄金阶段定位</div>'
                '<div class="muted">黄金数据不足,无法定位阶段。</div></div>')

    ev = snap["evidence"]
    st = snap["stage"]
    stage_col = c.get(_STAGE_COLOR.get(st, "accent"), c["accent"])
    st_label = st + ("（震荡·信号衰减）" if snap.get("noise") else "")

    def chip(label, val, sub="", color=None):
        vs = f' style="color:{color}"' if color else ""
        return (f'<div class="card"><div class="card-v"{vs}>{val}</div><div class="card-l">{label}</div>'
                f'{f"<div class=card-s>{sub}</div>" if sub else ""}</div>')

    pvsma = ev.get("price_vs_ma_pct")
    above = "上" if ev.get("above_ma") else "下"
    pvsma_txt = f"{above} {pvsma:+.1%}" if (pvsma is not None and pvsma == pvsma) else "—"
    drv = snap.get("drivers") or {}
    conf_txt = f"{snap['confidence']*100:.0f}%·{snap['confidence_band']}"
    strength_txt = f"强度{snap['strength']:.2f}×折价{GOLD_DISCOUNT:.1f}"
    dev_txt = f"{ev['dev_pct_12m']:.2f}"
    ma_side = "均线↑" if ev.get("ma_trend_up") else "均线↓"
    drv_txt = f"DXY {drv.get('dxy_phase', '—')} · 曲线 {drv.get('curve_phase', '—')}"
    chips = (
        '<div class="cards">'
        + chip("当前阶段", st_label, "黄金·规则复现JZ阶段语言", stage_col)
        + chip("信心", conf_txt, strength_txt)
        + chip("偏离分位(近12月)", dev_txt, "0=超卖 / 1=超买")
        + chip("MA60 侧", pvsma_txt, ma_side)
        + chip("驱动", drv_txt, drv.get("note", ""))
        + '</div>')

    # 图: 近2年 close + MA60 + 12月偏离分位(y2) + 当前阶段点 + DEV 带
    gold = series_for("黄金", store).sort_index()
    n = min(500, len(gold))
    g = gold.iloc[-n:]
    ma60 = ma_series(gold, 60).iloc[-n:]
    devp = _rolling_dev_pct(deviation_series(gold, 60), 252).iloc[-n:]
    dt = pd.to_datetime(list(g.index))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in g.values], mode="lines", name="黄金",
                             line=dict(color=c["accent"], width=1.4),
                             hovertemplate="%{x|%Y-%m-%d}  %{y:.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in ma60.values], mode="lines", name="MA60",
                             line=dict(color=c["ink2"], width=1.0), hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) if v == v else None for v in devp.values],
                             mode="lines", name="12月偏离分位", yaxis="y2",
                             line=dict(color=c["open"], width=1.0, dash="dot"),
                             fill="tozeroy", hovertemplate="偏离分位 %{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[dt[-1]], y=[float(g.iloc[-1])], mode="markers", name=st,
                             marker=dict(size=14, color=stage_col, symbol="diamond",
                                         line=dict(width=1.5, color="#ffffff")), hoverinfo="skip"))
    for y0, y1, col in [(0.0, GOLD_DEV_LOW, c["edge"]), (GOLD_DEV_HIGH, 1.0, c["miss"])]:
        fig.add_shape(type="rect", xref="paper", yref="y2", x0=0, x1=1, y0=y0, y1=y1,
                      fillcolor=col, opacity=0.10, line_width=0, layer="below")
    fig.add_annotation(x=dt[-1], y=float(g.iloc[-1]), text=st_label, showarrow=False,
                       xanchor="left", xshift=8, font=dict(color=stage_col, size=11))
    fig.update_layout(margin=dict(l=64, r=44, t=8, b=28), height=300, showlegend=False,
                      paper_bgcolor=c["surface"], plot_bgcolor=c["bg"],
                      font=dict(size=11, color=c["ink"]),
                      xaxis=dict(type="date", tickformat="%Y-%m", gridcolor=c["border"]),
                      yaxis=dict(title=dict(text="COMEX黄金(USD)", font=dict(size=10)),
                                 gridcolor=c["border"]),
                      yaxis2=dict(overlaying="y", side="right", range=[0, 1], showgrid=False,
                                  title=dict(text="偏离分位", font=dict(size=9))))
    fig_html = fig.to_html(full_html=False, include_plotlyjs=False, div_id="stage_gold")
    return (f'<div class="chart"><div class="chart-t">🥇 黄金阶段定位 '
            f'<span class="muted">(MA60 + 近12月偏离分位 · ◆当前阶段点 · 绿带=超卖区/红带=超买区)</span></div>'
            f'{chips}{fig_html}</div>')


def _live_confirm_block(store, c: dict) -> str:
    """宏观 call 实时确认块: 未到期 claim 当前是否被数据兑现 (兑现中/背离/停滞) + 按标的聚合 + 背离清单。

    利率(2Y/10Y/2s10s) 等 JZ 用*方向语言*的资产, 在此用方向兑现来量 (而非阶段); 用上真 edge。"""
    from .live import (ALT_BRANCH, DIVERGING, MANUAL, NO_SERIES, ON_TRACK, RES_EDGE,
                       RES_HIT, RES_MISS, STALLED, live_confirmation_overview)
    ov = live_confirmation_overview(store)
    counts = ov["counts"]
    open_total = ov["open_total"]

    def chip(label, val, sub="", color=None):
        vs = f' style="color:{color}"' if color else ""
        return (f'<div class="card"><div class="card-v"{vs}>{val}</div><div class="card-l">{label}</div>'
                f'{f"<div class=card-s>{sub}</div>" if sub else ""}</div>')

    cards = (
        '<div class="cards">'
        + chip("未到期 call", open_total, f"共 {ov['total']} 条断言")
        + chip("兑现中", counts.get(ON_TRACK, 0), "数据正向预测走", c["edge"])
        + chip("背离", counts.get(DIVERGING, 0), "数据反向走", c["miss"])
        + chip("停滞", counts.get(STALLED, 0), "基本没动", c["ink2"])
        + '</div>')

    # 按标的聚合
    by_asset = ov["by_asset"]

    def _fmt_ret(d):
        return f"{d['avg_ret']*100:+.1f}%" if d['avg_ret'] is not None else "—"

    if by_asset:
        asset_rows = "".join(
            f"<tr><td>{a}</td><td>{d['open']}</td><td>{d[ON_TRACK]}</td><td>{d[DIVERGING]}</td>"
            f"<td>{d[STALLED]}</td><td>{d['net_direction'] or '—'}</td><td>{_fmt_ret(d)}</td></tr>"
            for a, d in sorted(by_asset.items(), key=lambda kv: -kv[1]["open"]))
        asset_tbl = (
            '<table style="margin-top:8px"><tr><th>标的</th><th>未到期</th><th>兑现中</th>'
            '<th>背离</th><th>停滞</th><th>JZ净方向</th><th>平均走势(自发布)</th></tr>'
            + asset_rows + '</table>')
    else:
        asset_tbl = '<div class="muted">无未到期 call。</div>'

    # 背离清单 (最可操作: JZ 说 X, 数据说 otherwise)
    diverging = sorted([r for r in ov["rows"] if r["status"] == DIVERGING],
                       key=lambda x: x["episode_date"], reverse=True)[:10]
    if diverging:
        div_rows = "".join(
            f"<tr><td>{r['episode_date']}</td><td>{r['asset']}</td><td>{r['direction']}</td>"
            f"<td class='muted'>{r['note']}</td><td class='stmt'>{r['statement'][:48]}</td></tr>"
            for r in diverging)
        div_tbl = (
            '<div class="chart-t" style="margin-top:10px">⚠ 当前背离的 call (JZ 预测 vs 数据反向 · 前 10)</div>'
            '<table><tr><th>日期</th><th>标的</th><th>预测</th><th>走势</th><th>断言</th></tr>'
            + div_rows + '</table>')
    else:
        div_tbl = '<div class="muted" style="margin-top:10px">无当前背离的 call。</div>'

    return (f'<div class="chart"><div class="chart-t">📡 宏观 call 实时确认 '
            f'<span class="muted">(未到期断言 · 数据当前是否兑现 · 利率用方向兑现量 edge)</span></div>'
            f'{cards}{asset_tbl}{div_tbl}</div>')


def render_western_macro(store, docs_dir: Path, episodes_json: Path,
                         out_path: Path, asof: str = "") -> Path:
    claims = store.get_wm_claims()
    rules = store.get_wm_rules()
    sett = {s["claim_uid"]: s for s in store.get_wm_settlements()}
    settled = [c for c in claims if c["uid"] in sett]

    # coverage
    from .extract import discover_transcripts
    n_transcripts = len(discover_transcripts(docs_dir))
    extracted_dates = {c["episode_date"] for c in claims}
    n_extracted = len(extracted_dates)
    n_anthology = 0
    if episodes_json.exists():
        try:
            n_anthology = len(json.loads(episodes_json.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

    # track record
    n_hit = sum(1 for c in settled if sett[c["uid"]]["hit"])
    n_edge = sum(1 for c in settled if sett[c["uid"]]["edge"])
    hit_rate = (n_hit / len(settled) * 100) if settled else 0
    edge_rate = (n_edge / len(settled) * 100) if settled else 0
    by_asset: dict[str, dict] = {}
    by_node: dict[str, dict] = {}
    for c in settled:
        s = sett[c["uid"]]
        a = by_asset.setdefault(c["asset"], {"n": 0, "hit": 0, "edge": 0})
        a["n"] += 1; a["hit"] += s["hit"]; a["edge"] += s["edge"]
        for nd in (c.get("basis_nodes") or "").split(","):
            if nd:
                b = by_node.setdefault(nd, {"n": 0, "edge": 0})
                b["n"] += 1; b["edge"] += s["edge"]

    def card(label, val, sub=""):
        return (f'<div class="card"><div class="card-v">{val}</div>'
                f'<div class="card-l">{label}</div>{f"<div class=card-s>{sub}</div>" if sub else ""}</div>')

    def status_badge(s):
        cls, txt = _status_cell(s)
        return f'<span class="badge {cls}">{txt}</span>'

    L, D = _LIGHT, _DARK
    rows_html = []
    for c in sorted(claims, key=lambda x: (x["uid"] not in sett, x["episode_date"]), reverse=False):
        s = sett.get(c["uid"])
        actual = s["actual_direction"] if s else ""
        note = (s.get("note") or "") if s else ""
        cn_type = _TYPE_CN.get(c["claim_type"], c["claim_type"])
        cn_dir = _DIR_CN.get(c["direction"] or "", "—")
        cn_actual = _DIR_CN.get(actual, "—")
        cn_nodes = " ".join(drivers.node_label(n) for n in (c.get("basis_nodes") or "").split(",") if n)
        if c["claim_type"] == "scenario" and c.get("is_primary") == 0:
            verdict = '<span class="badge open">备选</span>'  # 对冲分支,不计分(只主推情景算)
        else:
            verdict = status_badge(s)
        rows_html.append(
            f"<tr><td>{c['episode_date']}</td><td>{c['asset']}</td>"
            f"<td>{cn_type}</td><td>{cn_dir}</td>"
            f"<td>{c['horizon']}</td><td>{cn_actual}</td><td>{verdict}</td>"
            f"<td>{_state_badge(c['state'])}</td>"
            f"<td class='stmt'>{c['statement']}</td>"
            f"<td class='muted'>{cn_nodes}</td><td class='muted'>{note}</td></tr>")

    asset_rows = "".join(
        f"<tr><td>{a}</td><td>{d['n']}</td><td>{d['hit']}</td><td>{d['edge']}</td></tr>"
        for a, d in sorted(by_asset.items())) or '<tr><td colspan=4 class="muted">无</td></tr>'
    node_rows = "".join(
        f"<tr><td>{drivers.node_label(nd)}</td><td>{d['n']}</td><td>{d['edge']}</td></tr>"
        for nd, d in sorted(by_node.items(), key=lambda x: -x[1]["edge"])) \
        or '<tr><td colspan=3 class="muted">无</td></tr>'
    rules_html = "".join(
        f"<tr><td>{r['episode_date']}</td><td>{r['rule_type']}</td><td class='stmt'>{r['statement']}</td></tr>"
        for r in rules) or '<tr><td colspan=3 class="muted">无</td></tr>'
    provisional = (n_anthology and n_transcripts < n_anthology)
    charts_html = _charts_html(store, claims, sett, D)
    gold_stage_html = _gold_stage_block(store, D)
    live_html = _live_confirm_block(store, D)
    state_counts = {"draft": 0, "confirmed": 0, "vetoed": 0}
    for c in claims:
        state_counts[c["state"]] = state_counts.get(c["state"], 0) + 1

    html = f"""<!doctype html><html lang="zh" data-theme="dark"><head><meta charset="utf-8">
<title>西方宏观预测台账 · {asof}</title>
<style>
:root{{{_css(D)}}}[data-theme="light"]{{{_css(L)}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,"Microsoft YaHei",sans-serif}}
.wrap{{max-width:1280px;margin:0 auto;padding:20px}}
h1{{font-size:20px;margin:0 0 4px}}h2{{font-size:15px;margin:24px 0 8px;color:var(--ink2)}}
.muted{{color:var(--ink2)}}.stmt{{max-width:380px}}
.banner{{padding:10px 14px;border-radius:8px;margin:10px 0;font-size:13px}}
.banner.warn{{background:color-mix(in srgb,var(--miss) 14%,var(--surface));border:1px solid var(--miss)}}
.banner.info{{background:var(--surface);border:1px solid var(--border)}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px;min-width:120px}}
.card-v{{font-size:26px;font-weight:700;color:var(--accent)}}.card-l{{font-size:12px;color:var(--ink2)}}.card-s{{font-size:11px;color:var(--ink2);margin-top:2px}}
table{{border-collapse:collapse;width:100%;background:var(--surface);border:1px solid var(--border);border-radius:8px;overflow:hidden}}
th,td{{padding:7px 10px;text-align:left;border-bottom:1px solid var(--border);font-size:13px;vertical-align:top}}
th{{background:color-mix(in srgb,var(--accent) 10%,var(--surface));color:var(--ink2);font-weight:600;font-size:12px}}
tr:hover{{background:color-mix(in srgb,var(--accent) 6%,transparent)}}
.badge{{padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}}
.badge.edge{{background:var(--edge);color:#fff}}.badge.hit{{background:var(--hit);color:#fff}}
.badge.miss{{background:var(--miss);color:#fff}}.badge.open{{background:var(--border);color:var(--ink2)}}
.driver{{width:100%;max-width:1180px;height:auto;margin:8px 0}}
.chart{{margin:14px 0;background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px}}
.chart-t{{font-size:13px;color:var(--ink2);margin-bottom:4px;font-weight:600}}
.toggle{{position:fixed;top:14px;right:18px;cursor:pointer;background:var(--surface);border:1px solid var(--border);
color:var(--ink);border-radius:8px;padding:6px 12px;font-size:12px}}
.foot{{margin-top:24px;color:var(--ink2);font-size:11px;border-top:1px solid var(--border);padding-top:10px}}
</style></head><body>
<button class="toggle" onclick="toggle()">🌓 浅/深</button>
<div class="wrap">
<h1>🪙 西方宏观预测台账 <span class="muted" style="font-size:13px">— JZ《西方经济》可证伪断言追踪 · 只读诊断旁路(ADR-0001)</span></h1>
<div class="muted" style="font-size:12px">截至 {asof} · 度量一个自由裁量宏观预测者,绝不自动执行 · 数据来自 AkShare(DXY 6腿重算)</div>
<div class="banner info">📊 覆盖:已抽取 <b>{n_extracted}</b> 期 / 已转写 <b>{n_transcripts}</b> 期{f" / 合集 {n_anthology} 期" if n_anthology else ""}。
{f"⚠ 语料仍在回填(转写中),track record 为部分样本、非定论。" if provisional else ""}</div>
<div class="banner info">📝 审核:草稿 <b>{state_counts['draft']}</b> / 已确认 <b>{state_counts['confirmed']}</b> / 已否决 <b>{state_counts['vetoed']}</b> —— 用 <code>python scripts/wm_confirm.py</code> 审核(已否决的从评分剔除;可执行层建议先确认)。</div>
{'<div class="banner warn">⚠ 美元指数(DXY)数据缺:外汇端点 push2his 在本环境被拦 → 美元/黄金相关 claim 暂未自动结算。本机重跑 backfill_western_macro.py 即可补。</div>' if not store.get_western_series('dxy','DXY').size else ''}

<h2>📡 宏观 call 实时确认(未到期断言 · 数据当前是否兑现 · 只读诊断·不喂引擎 ADR-0001)</h2>
<div class="banner info">JZ 在<b>利率</b>(2Y/10Y/2s10s)上用方向语言(edge 所在), 本视图用「数据当前是否兑现其方向」来量。到期断言沿用结算(本事/命中/未中); 未到期按自发布以来的走势判 兑现中/背离/停滞。本块价值有限,置顶备忘,随新转写稿迭代。</div>
{live_html}

<h2>🎯 业绩追踪</h2>
<div class="cards">
{card("已结算", len(settled), f"共 {len(claims)} 条断言")}
{card("方向命中", f"{n_hit}/{len(settled)}", f"{hit_rate:.0f}%")}
{card("本事★(赢过朴素基准)", f"{n_edge}/{len(settled)}", f"{edge_rate:.0f}% · 真本事")}
{card("操作规则", len(rules), "隔离·不计命中率")}
</div>
<details><summary style="cursor:pointer;color:var(--ink2)">按标的 / 按驱动节点拆分</summary>
<div style="display:flex;gap:24px;flex-wrap:wrap">
<div><table style="margin-top:8px"><tr><th>标的</th><th>结算</th><th>命中</th><th>本事</th></tr>{asset_rows}</table></div>
<div><table style="margin-top:8px"><tr><th>驱动节点</th><th>结算</th><th>本事</th></tr>{node_rows}</table></div>
</div></details>

<h2>📈 标的时序图(验证用 · 实心点=兑现日 / 空心点=发布日 / 竖色带=评判窗口 / 虚线=这段走势 · 🟢本事 / 🟡命中 / 🔴未中 / ⚪未到期)</h2>
{charts_html}

<h2>🥇 黄金阶段定位器(Phase 3 MVP · 规则复现 JZ 阶段语言 · 只读诊断·不喂引擎 ADR-0001)</h2>
<div class="banner info">规则由黄金自身结构(MA60 + 近12月偏离分位)定阶段, DXY/曲线作确认驱动。置信度按 JZ 黄金择时 32% 命中弱项 ×0.60 折价。回测一致性由 <code>python scripts/wm_gold_stage_eval.py</code> 单独出(MVP 不在此跑回测)。</div>
{gold_stage_html}

<h2>📋 台账(全部断言 · 未到期=兑现日未到 / 点位·时点=人工结算)</h2>
<table><tr><th>日期</th><th>标的</th><th>类型</th><th>预测</th><th>兑现</th><th>实际</th><th>结果</th><th>状态</th><th>断言</th><th>驱动</th><th>价格</th></tr>
{"".join(rows_html)}</table>

<h2>🗺️ 因果框架(驱动图 · 断言挂其节点)</h2>
{_driver_svg(D if True else L)}
<div class="muted" style="font-size:11px">蓝=可结算标的节点;箭头=因果传导。框架小而稳,可演化。</div>

<h2>📜 操作规则(隔离 · 非预测)</h2>
<table><tr><th>日期</th><th>类型</th><th>规则</th></tr>{rules_html}</table>

<div class="foot">
⚠ 本看板是把一位 B 站 UP 主的观点抽成可证伪断言并打分的<b>诊断工具</b>,不构成投资建议。
本事★=方向命中且赢过朴素基准(该资产自身漂移);只有本事★才算真本事,计入命中率。
大模型抽取为草稿,需人工确认后才计入命中率;兑现日由大模型标注+年份自纠,仍以人工复核为准。
语料回填中 → 当前为部分样本。
</div>
</div>
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


def _css(c: dict) -> str:
    return "".join(f"--{k}:{v};" for k, v in c.items())
