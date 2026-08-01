"""个股诊断 HTML 看板渲染 — Phase 2 交付通道。

卡片式布局(诊断是「每股多指标快照」而非时间序列,卡片比折线图更贴形):顶部告警区 +
每股一卡(分类 / 估值zone / 戴维斯 / 关键指标 / 避坑 / 预告 / E3偏离)。深浅色可切(localStorage)。
纯 f-string HTML,无模板引擎,照 research/report.py 风格。双击即看、发文件即分享。
"""
from __future__ import annotations

import html
import math
from pathlib import Path

from . import indicators as ti
from . import stock_diagnose as sd
from . import stock_figures as sf


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _pct(v, signed: bool = False) -> str:
    if _nan(v):
        return "—"
    return f"{v*100:+.1f}%" if signed else f"{v*100:.0f}%"


def _pct1(v) -> str:
    """1 位小数百分比(股息率等需要细看对比的指标用)。"""
    return "—" if _nan(v) else f"{v*100:.1f}%"


def _num(v, nd: int = 2) -> str:
    return "—" if _nan(v) else f"{v:.{nd}f}"


def _yi(v) -> str:
    """财报原始元 → 亿元字符串。"""
    if _nan(v):
        return "—"
    return f"{v/1e8:.0f}亿"


def _badge(text: str, color: str) -> str:
    return f'<span class="badge" style="background:{color}">{html.escape(str(text))}</span>'


# 颜色映射(语义色,浅深色通用 via 半透明)
_C = {
    "value": "#16a34a", "growth": "#2563eb", "cyclic": "#ea580c", "none": "#94a3b8",
}
_C_ZONE = {  # 估值 zone
    "低位·便宜": "#16a34a", "高位·偏贵": "#dc2626",
    "结构分化": "#ca8a04", "中位·中性": "#94a3b8", "—": "#94a3b8",
}
_C_DAVIS = {  # 戴维斯
    "double_play": "#16a34a", "double_play_setup": "#65a30d",
    "double_play_watch": "#2563eb", "double_kill": "#dc2626",
    "double_kill_risk": "#ea580c", "neutral": "#94a3b8",
}


# 卡片指标说明(统一小字,每卡一份;<details> 默认收起,零 JS)。改指标时同步这里。
_LEGEND_HTML = """
<details class="legend"><summary>ℹ️ 指标说明</summary>
<div class="legend-body">
<div><b>主分类/次分类</b> · 三类自动判定:价值(高股息+低 PE 分位)/成长(营收·净利高增)/周期(利润波动大,结构优先)</div>
<div><b>估值 zone</b> · PE+PB 历史分位四档:双低=便宜 / 双高=偏贵 / 跨中线=分化 / 都中间=中性</div>
<div><b>戴维斯</b> · 业绩方向 × 估值方向 → 六档(双击 / 双杀 / 观察 / 预警…)</div>
<div><b>营收·净利 CAGR</b> · 最近 N 年复合年增速 =(末/首)^(1/N)−1</div>
<div><b>利润波动</b> · 年度净利同比增速的 std(高=周期信号)</div>
<div><b>股息率</b> · 近 12 月每股现金分红 ÷ 现价</div>
<div><b>PE(TTM)/PB 分位</b> · 当前值在历史中的分位(0=最便宜,1=最贵)</div>
<div><b>避坑</b> · 异常高增速(低基数幻觉)→ 用 2 年 CAGR;含公告时间差</div>
<div><b>业绩含金量</b> · (归母−扣非)/|归母|=一次性占比;归母高增但扣非掉队=一次性利润/纸面富贵(⚠ 低含金量)</div>
<div><b>预告链</b> · 业绩预告拐点(A1 增速下滑 / A2 多转空)</div>
<div><b>披露</b> · 最新财报期 + 法定截止日 + 是否已披露</div>
<div><b>E3 偏离</b> · (价−60 日线)÷60 日线 的历史分位;突破/跌破档位 gN(N 越大越确定)</div>
</div></details>"""


def _alerts_region(alerts_list: list, title: str = "📡 信号提醒", empty_msg: bool = True) -> str:
    if not alerts_list:
        return ('<div class="alerts"><h2>📡 信号提醒</h2><p class="muted">当前无触发(观察池稳定,偏离/营收/预告均在正常区)。</p></div>'
                if empty_msg else "")
    warns = [a for a in alerts_list if a["level"] == "warn"]
    infos = [a for a in alerts_list if a["level"] == "info"]
    rows = []
    for a in alerts_list:
        icon = "⚠" if a["level"] == "warn" else "💡"
        rows.append(
            f'<div class="alert {"alert-warn" if a["level"]=="warn" else "alert-info"}">'
            f'<span class="alert-icon">{icon}</span>'
            f'<span class="alert-rule">[{html.escape(a["rule"])}]</span>'
            f'<span class="alert-scope">{html.escape(str(a["scope"]))}</span>'
            f'<span class="alert-msg">{html.escape(a["msg"])}</span></div>'
        )
    return (f'<div class="alerts"><h2>{title} '
            f'<span class="count">⚠{len(warns)} 💡{len(infos)}</span></h2>'
            + "".join(rows) + "</div>")


def _commodity_region(store) -> str:
    """🧲 商品 A 类面板:5 商品 现价/同比/近60日/判定(向上·背离·向下)+ 板块指引。

    商品价领先周期股财报 1-4 月;判定看同比(趋势)+ 近60日(边际):向上=埋伏方向,背离/向下=避。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    varieties = ["碳酸锂", "铜", "螺纹钢", "黄金", "原油"]
    td = "padding:6px;border-bottom:1px solid var(--border)"
    th = "padding:8px;border-bottom:2px solid var(--border)"
    rows, summary = [], {"向上": [], "背离": [], "向下": [], "震荡": []}
    for v in varieties:
        s = store.get_commodity_series(v)
        if s is None or len(s) < 2:
            continue
        s = s.astype(float)
        n252 = min(252, len(s) - 1)
        n60 = min(60, len(s) - 1)
        yoy = float(s.iloc[-1]) / float(s.iloc[-1 - n252]) - 1.0
        rec = float(s.iloc[-1]) / float(s.iloc[-1 - n60]) - 1.0
        if yoy > 0.10 and rec > -0.05:
            judge, color = "向上", "#16a34a"
        elif yoy > 0.10:
            judge, color = "背离", "#d97706"
        elif yoy > -0.10:
            judge, color = "震荡", "#64748b"
        else:
            judge, color = "向下", "#dc2626"
        summary[judge].append(v)
        rows.append((v, float(s.iloc[-1]), yoy, rec, judge, color))
    if not rows:
        return ""
    body = "".join(
        f"<tr><td style='{td}'>{v}</td><td style='{td};text-align:center'>{val:.0f}</td>"
        f"<td style='{td};text-align:center'>{_pct(yoy, True)}</td>"
        f"<td style='{td};text-align:center'>{_pct(rec, True)}</td>"
        f"<td style='{td};text-align:center;color:{color};font-weight:600'>{judge}</td></tr>"
        for (v, val, yoy, rec, judge, color) in rows)
    head = (f"<tr><th style='{th};text-align:left'>商品(板块)</th><th style='{th}'>现价</th>"
            f"<th style='{th}'>同比</th><th style='{th}'>近60日</th><th style='{th}'>判定</th></tr>")
    guide = (f"向上(埋伏方向):{','.join(summary['向上']) or '—'} | "
             f"背离(避):{','.join(summary['背离']) or '—'} | "
             f"向下(避):{','.join(summary['向下']) or '—'}")
    return ('<div class="alerts"><h2>🧲 商品 A 类面板 '
            '<span class="count">上游价 → 周期股业绩领先信号</span></h2>'
            f'<p class="muted">商品价领先财报 1-4 月;判定看同比(趋势)+近60日(边际)。{guide}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead>{head}</thead><tbody>{body}</tbody></table></div>')


def _commodity_charts(store) -> str:
    """📈 商品价时序图面板(5 商品折线,inline 渲染于周期 tab)。复用 stock_figures.commodity_price_figure;
    DOMContentLoaded 时 newPlot(Plotly 已由个股图表模态加载)。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    from . import stock_figures as sf
    import re
    varieties = ["碳酸锂", "铜", "螺纹钢", "黄金", "原油"]
    figs = [(v, sf.commodity_price_figure(v, store.get_commodity_series(v))) for v in varieties]
    if not figs:
        return ""
    divs = "".join(f'<div id="comm-chart-{i}" class="comm-chart"></div>' for i in range(len(figs)))
    arr = ",".join(re.sub(r"</script", r"<\\/script", f.to_json(), flags=re.I) for _, f in figs)
    js = ("var COMM=[" + arr + "];\n"
          "document.addEventListener('DOMContentLoaded',function(){\n"
          "  if(!window.Plotly)return;\n"
          "  COMM.forEach(function(fig,i){var gd=document.getElementById('comm-chart-'+i);"
          "    if(gd)Plotly.newPlot(gd,fig,{responsive:true,displaylogo:false});});\n"
          "  setTimeout(function(){if(window._applyPlotly)_applyPlotly(_isDark());},60);\n"
          "});")
    return ('<div class="alerts"><h2>📈 商品价时序(A 类领先信号)</h2>'
            '<p class="muted">商品价领先周期股财报 1-4 月;折线=价,虚线=1 年前水平(同比可视化)。结合下方个股 📊 判断。</p>'
            f'<div class="comm-grid">{divs}</div></div>\n<script>{js}</script>')


def _ambush_region(diagnoses: dict, names: dict, as_of: str,
                   score_min: float = 30.0) -> str:
    """🎯 提前埋伏候选排名(只读,基本面领先):深跌×业绩拐头×含金量×未兑现 综合分降序。无候选 → 空串。

    入场=领先基本面(深跌+最新已报期业绩拐头,早于预告/上涨);预告/正报=兑现出场窗口。
    分来自 positioning_from_diag(吃 diagnose_stock_full)。≥60 绿/≥40 黄高亮。不含涨跌预测。"""
    from .stock_diagnose import positioning_from_diag
    rows = []
    for sym, d in diagnoses.items():
        p = positioning_from_diag(d)
        sc = p.get("score")
        if _nan(sc) or sc < score_min:
            continue
        nm = names.get(sym, sym)
        dd, et, rr = p.get("drawdown"), p.get("earnings_turn"), p.get("recent_return")
        dd_s = f"{abs(dd):.0%}" if not _nan(dd) else "—"
        et_s = {1.0: "扭亏/回升", 0.5: "持平", 0.0: "仍亏"}.get(et, "—")
        rr_s = f"{rr:+.0%}" if not _nan(rr) else "—"
        ex_s = p.get("exit_date") or "—"
        flags = "".join(f" <span style='font-size:10px;color:#b45309'>{f}</span>"
                        for f in (p.get("flags") or []))
        hot = "background:#dcfce7" if sc >= 60 else ("background:#fef9c3" if sc >= 40 else "")
        td = "padding:6px;border-bottom:1px solid var(--border)"
        rows.append((sc,
                     f"<tr><td style='{td}'><b>{html.escape(nm)}</b>"
                     f"<br><span class='muted'>{sym}</span></td>"
                     f"<td style='{td};text-align:center'>{dd_s}</td>"
                     f"<td style='{td};text-align:center'>{et_s}{flags}</td>"
                     f"<td style='{td};text-align:center'>{rr_s}</td>"
                     f"<td style='{td};text-align:center'>{ex_s}</td>"
                     f"<td style='{td};text-align:center;font-size:16px;font-weight:bold;{hot}'>{sc:.0f}</td></tr>"))
    if not rows:
        return ""
    rows.sort(key=lambda x: x[0], reverse=True)
    th = "padding:8px;border-bottom:2px solid var(--border)"
    head = (f"<tr><th style='{th};text-align:left'>股票</th><th style='{th}'>深跌</th>"
            f"<th style='{th}'>业绩拐头</th><th style='{th}'>近60日涨</th>"
            f"<th style='{th}'>兑现窗口</th><th style='{th}'>埋伏分</th></tr>")
    return ('<div class="alerts"><h2>🎯 提前埋伏候选 <span class="count">深跌×业绩拐头×含金量×未兑现</span></h2>'
            '<p class="muted">入场=领先基本面(深跌+最新已报期业绩拐头,4月年报即显,早于预告/上涨);'
            '预告/正报=兑现出场窗口。近60日已大涨=已兑现→压低。不含涨跌预测,非荐股。</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px"><thead>{head}</thead>'
            f'<tbody>{"".join(r for _, r in rows)}</tbody></table></div>')


def _card(sym: str, d: dict, name: str, with_charts: bool = False,
          with_ai: bool = False) -> str:
    cls = d.get("classification") or {}
    primary = cls.get("primary") or "未分类"
    secondary = "+".join(cls.get("secondary") or [])
    cls_badge = _badge(primary, _C.get(primary, _C["none"])) + (
        f" {_badge(secondary, '#475569')}" if secondary else "")
    sec_short = f'<span class="sec">{html.escape(secondary)}</span>' if secondary else ""

    vz = d.get("valuation_zone") or {}
    zone = vz.get("zone", "—")
    zone_badge = _badge(zone, _C_ZONE.get(zone, _C_ZONE["—"]))

    dv = d.get("davis") or {}
    dv_type = dv.get("type", "neutral")
    dv_badge = _badge(dv.get("label", "中性"), _C_DAVIS.get(dv_type, _C_DAVIS["neutral"]))

    f = d.get("features") or {}
    _cy, _vy = f.get("cagr_years"), f.get("vol_years")
    _rev_lbl = f"营收CAGR({_cy}y)" if _cy else "营收CAGR"
    _np_lbl = f"净利CAGR({_cy}y)" if _cy else "净利CAGR"
    _vol_lbl = f"利润波动({_vy}y)" if _vy else "利润波动"
    pit = d.get("pitfalls") or {}
    np_ = pit.get("net_profit") or {}
    rev = pit.get("revenue") or {}
    disc = pit.get("disclosure") or {}
    fc = d.get("forecast") or {}
    pt = d.get("price_timing") or {}
    dev = pt.get("deviation") or {}
    cross = pt.get("cross") or {}

    # 预告
    if fc.get("valid"):
        L = fc.get("latest") or {}
        fc_txt = f'{html.escape(str(L.get("type","?")))} yoy={_pct(L.get("yoy"), True)} <span class="muted">({L.get("announce_date","?")})</span>'
    else:
        fc_txt = '<span class="muted">无预告历史</span>'

    # 避坑(净利)
    if np_.get("abnormal"):
        pit_txt = f'<span class="warn-txt">⚠ 异常 {_pct(np_.get("yoy"),True)} → 可信 {_pct(np_.get("trustworthy"),True)}</span>'
    else:
        pit_txt = f'净利 {_pct(np_.get("yoy"),True)}'

    disc_txt = (f'{disc.get("latest_period","?")[:4]}报 截止{disc.get("deadline","?")}'
                f' <span class="muted">({"已披露" if disc.get("disclosed_by_asof") else "未披露"})</span>')

    # 业绩含金量(一次性利润/纸面富贵):归母 vs 扣非 背离
    eq = d.get("earnings_quality") or {}
    if eq.get("valid"):
        _eq_core = (f"一次性占比{_pct(eq.get('non_recurring_frac'))} · "
                    f"归母{_pct(eq.get('np_yoy'), True)} · 扣非{_pct(eq.get('ded_yoy'), True)}")
        eq_txt = (f'<span class="warn-txt">⚠ 业绩含金量低 {_eq_core}</span>'
                  if eq.get("low_quality") else _eq_core)
    else:
        eq_txt = '<span class="muted">—</span>'

    chart_link = ('<button type="button" class="chart-link" title="查看时序图" '
                  'onclick="openChart(\'' + sym + '\')">📊</button>'
                  if with_charts else "")
    ai_link = ('<button type="button" class="chart-link" title="AI 评估" '
               'onclick="openAiEval(\'' + sym + '\')">🤖</button>'
               if with_ai else "")

    # E3:诚实展示 —— 当前相对 60 日线的位置(线上/线下+偏离)+ 最近一次真正穿越(≤5 日=新突破/跌破,
    # 否则只标上穿/下穿日期)。不再把「在线上」误称「突破」。
    _pos = dev.get("cur_dev")
    _pos_txt = (f"{'线上' if _pos >= 0 else '线下'}{_pos:+.1%}" if not _nan(_pos) else "—")
    _cx, _cxd, _cxa = cross.get("direction"), cross.get("date"), cross.get("bars_ago")
    if _cx and _cxd:
        _mmdd = str(_cxd)[5:10] if len(str(_cxd)) >= 10 else str(_cxd)
        if isinstance(_cxa, int) and _cxa <= 5:
            _cross_txt = f'<span class="muted">· {"突破" if _cx == "up" else "跌破"} {_mmdd}(新)</span>'
        else:
            _cross_txt = f'<span class="muted">· {"上穿" if _cx == "up" else "下穿"} {_mmdd}</span>'
    else:
        _cross_txt = ""

    return f"""
    <div class="card">
      <div class="card-head">
        <span class="stock-name">{html.escape(name)}</span>
        <span class="stock-code">{sym}</span>
        <span class="stock-price">¥{_num(d.get("price_last"))} <span class="muted">{d.get("date_last","")}</span></span>
        {chart_link}{ai_link}
      </div>
      <div class="card-row">{cls_badge} {zone_badge} {dv_badge}</div>
      <table class="metrics">
        <tr><td>{_rev_lbl}</td><td>{_pct(f.get('revenue_cagr'), True)}</td>
            <td>{_np_lbl}</td><td>{_pct(f.get('profit_cagr'), True)}</td></tr>
        <tr><td>{_vol_lbl}</td><td>{_pct(f.get('profit_vol'))}</td>
            <td>股息率</td><td>{_pct1(f.get('div_yield'))}</td></tr>
        <tr><td>PE(TTM)</td><td>{_num(d.get('pe_ttm'),1)} <span class="muted">(分位{_pct(vz.get('pe_pct'))})</span></td>
            <td>PB</td><td>{_num(d.get('pb'))} <span class="muted">(分位{_pct(vz.get('pb_pct'))})</span></td></tr>
        <tr><td>戴维斯</td><td colspan="3"><span class="muted">净利YoY {_pct(dv.get('profit_yoy_latest'),True)} · PE变化 {_pct(dv.get('pe_change'),True)}</span></td></tr>
        <tr><td>避坑</td><td colspan="3">{pit_txt} · 营收 {_pct(rev.get('yoy'),True)}</td></tr>
        <tr><td>业绩含金量</td><td colspan="3">{eq_txt}</td></tr>
        <tr><td>预告链</td><td colspan="3">{fc_txt}</td></tr>
        <tr><td>披露</td><td colspan="3">{disc_txt}</td></tr>
        <tr><td>E3偏离</td><td colspan="3">{_pct(dev.get('pct'))}位 · <b>{_pos_txt}</b> {_cross_txt}</td></tr>
      </table>
    </div>"""


_CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --border:#e2e8f0; --shadow:0 1px 3px rgba(0,0,0,.08); }
body.dark { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --border:#334155; --shadow:0 1px 3px rgba(0,0,0,.3); }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; margin:0; padding:20px; font-size:14px; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:16px; margin:0 0 10px; }
.cards-head { display:flex; align-items:baseline; flex-wrap:wrap; gap:12px; margin-bottom:10px; }
.cards-head h2 { margin:0; }
.muted { color:var(--muted); font-size:12px; }
.header { margin-bottom:16px; }
.toggle { float:right; margin-top:4px; cursor:pointer; background:var(--card); border:1px solid var(--border); color:var(--text); padding:4px 10px; border-radius:6px; }
.alerts { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px 16px; margin-bottom:18px; box-shadow:var(--shadow); }
.count { color:var(--muted); font-weight:normal; font-size:13px; }
.alert { display:flex; gap:8px; align-items:flex-start; padding:6px 0; border-top:1px solid var(--border); }
.alert:first-of-type { border-top:none; }
.alert-icon { flex:0 0 18px; }
.alert-warn .alert-icon { color:#dc2626; } .alert-info .alert-icon { color:#2563eb; }
.alert-rule { flex:0 0 56px; font-weight:600; color:var(--muted); }
.alert-scope { flex:0 0 90px; font-weight:600; }
.alert-msg { flex:1; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(380px,1fr)); gap:14px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px 16px; box-shadow:var(--shadow); }
.card-head { display:flex; align-items:baseline; gap:8px; margin-bottom:8px; border-bottom:1px solid var(--border); padding-bottom:6px; }
.stock-name { font-weight:700; font-size:15px; }
.stock-code { color:var(--muted); font-size:12px; }
.stock-price { margin-left:auto; font-weight:600; }
.card-row { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:10px; }
.badge { color:#fff; font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; }
.metrics { width:100%; border-collapse:collapse; }
.metrics td { padding:3px 6px; border-top:1px solid var(--border); }
.metrics td:nth-child(odd) { color:var(--muted); width:18%; }
.metrics td:nth-child(even) { font-weight:600; }
.warn-txt { color:#dc2626; font-weight:600; }
.legend { margin:0 0 8px; }
.legend > summary { cursor:pointer; display:inline-block; color:var(--muted); font-size:12px; list-style:none; }
.legend > summary::-webkit-details-marker { display:none; }
.legend > summary::before { content:"▸ "; }
.legend[open] > summary::before { content:"▾ "; }
.legend > summary:hover { color:var(--text); }
.legend-body { margin-top:6px; padding:8px 10px; background:var(--bg); border:1px solid var(--border); border-radius:6px; font-size:11px; line-height:1.75; color:var(--muted); }
.legend-body b { color:var(--text); font-weight:600; }
.chart-link { margin-left:8px; background:transparent; border:none; color:var(--text); font-size:15px; cursor:pointer; padding:0 2px; line-height:1; opacity:.7; }
.chart-link:hover { opacity:1; }
.modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.55); display:flex; align-items:flex-start; justify-content:center; padding:28px 14px; z-index:1000; overflow:auto; }
.modal-overlay[hidden] { display:none; }
.modal-box { background:var(--card); border:1px solid var(--border); border-radius:10px; width:100%; max-width:2200px; box-shadow:0 16px 50px rgba(0,0,0,.45); }
.modal-head { display:flex; justify-content:space-between; align-items:center; padding:10px 16px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--card); border-radius:10px 10px 0 0; z-index:1; }
.modal-head #modal-title { font-weight:700; font-size:16px; }
.modal-close { background:transparent; border:none; color:var(--muted); font-size:20px; cursor:pointer; padding:2px 10px; border-radius:6px; line-height:1; }
.modal-close:hover { background:var(--border); color:var(--text); }
.modal-body { padding:10px 14px 18px; }
.modal-chart { margin-bottom:6px; }
.ai-box { max-width:820px; }                       /* AI 评估弹窗比图表窄,文本更易读 */
.ai-body { white-space:pre-wrap; word-wrap:break-word; line-height:1.75; font-size:13.5px; padding:4px 2px; }
/* tab 切换(按类型分页:周期/价值/成长) */
.tabs { display:flex; gap:4px; flex-wrap:wrap; margin:14px 0 0; border-bottom:2px solid var(--border); }
.tab { background:transparent; border:none; border-bottom:3px solid transparent; padding:8px 14px; cursor:pointer; font-size:14px; font-weight:600; color:var(--muted); }
.tab:hover { color:var(--text); }
.tab.active { color:var(--text); border-bottom-color:#2563eb; }
.tab-panel { display:none; padding-top:10px; }
.tab-panel.active { display:block; }
.comm-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(440px,1fr)); gap:10px; margin-top:8px; }
.comm-chart { min-height:300px; }
"""


_THEME_JS = """
function _isDark(){return document.body.classList.contains('dark');}
function _applyPlotly(dark){
  if(!window.Plotly)return;
  var u={'paper_bgcolor':dark?'#1e293b':'#ffffff','plot_bgcolor':dark?'#1e293b':'#ffffff','font.color':dark?'#e2e8f0':'#0f172a'};
  ['xaxis','xaxis2','xaxis3','yaxis','yaxis2','yaxis3','yaxis4'].forEach(function(a){
    u[a+'.gridcolor']=dark?'#334155':'#e2e8f0';
    u[a+'.zerolinecolor']=dark?'#475569':'#cbd5e1';
  });
  document.querySelectorAll('.plotly-graph-div').forEach(function(gd){try{Plotly.relayout(gd,u);}catch(e){}});
}
function _syncBtn(){var b=document.getElementById('theme-btn');if(b)b.textContent=_isDark()?'☀️ 浅色':'🌙 深浅色';}
function toggleTheme(){
  document.body.classList.toggle('dark');
  var dark=_isDark();
  localStorage.setItem('stock-dark',dark);
  _syncBtn();
  _applyPlotly(dark);
}
(function(){
  if(localStorage.getItem('stock-dark')==='true')document.body.classList.add('dark');
  _syncBtn();
  function init(){_applyPlotly(_isDark());}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
function switchTab(name){
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active', b.dataset.tab===name));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.toggle('active', p.id==='tab-'+name));
}
"""


_CHART_MODAL_HTML = """
<div id="chart-modal" class="modal-overlay" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <span id="modal-title">—</span>
      <button type="button" class="modal-close" onclick="closeChart()" title="关闭 (Esc)">✕</button>
    </div>
    <div class="modal-body">
      __SLOTS__
    </div>
  </div>
</div>"""


# 模态交互逻辑(依赖运行时的 Plotly / CHARTS / _NAMES / _applyPlotly / _isDark,
# 后两者来自 _THEME_JS)。openChart 只 newPlot 当前这一只的 4 张,closeChart purge 释放。
_CHART_MODAL_JS = """
function openChart(sym){
  var list = CHARTS[sym];
  if(!list) return;
  document.getElementById('modal-title').textContent = (_NAMES[sym] || sym);
  var ov = document.getElementById('chart-modal');
  ov.hidden = false;                 // 先显示(容器拿到真实宽度)再 newPlot,图才能横向占满
  document.body.style.overflow = 'hidden';
  var allSlots = document.querySelectorAll('.modal-chart');
  allSlots.forEach(function(gd){ try{ Plotly.purge(gd); }catch(e){} gd.style.display=''; });
  Promise.all(list.map(function(fig, i){
    return Plotly.newPlot(document.getElementById('m-chart-' + i), fig, {responsive:true, displaylogo:false});
  })).then(function(){ _applyPlotly(_isDark()); });
  for(var i=list.length; i<allSlots.length; i++){ allSlots[i].style.display='none'; }
}
function closeChart(){
  var ov = document.getElementById('chart-modal');
  ov.hidden = true;
  document.body.style.overflow = '';
  document.querySelectorAll('.modal-chart').forEach(function(gd){
    try{ Plotly.purge(gd); }catch(e){}
  });
}
document.addEventListener('keydown', function(e){
  var ov = document.getElementById('chart-modal');
  if(ov && !ov.hidden && (e.key==='Escape' || e.keyCode===27)) closeChart();
});
document.addEventListener('DOMContentLoaded', function(){
  var ov = document.getElementById('chart-modal');
  if(ov) ov.addEventListener('click', function(e){ if(e.target===ov) closeChart(); });
});
"""


_AI_EVAL_MODAL_HTML = """
<div id="ai-modal" class="modal-overlay" hidden>
  <div class="modal-box ai-box">
    <div class="modal-head">
      <span id="ai-modal-title">—</span>
      <button type="button" class="modal-close" onclick="closeAiEval()" title="关闭 (Esc)">✕</button>
    </div>
    <div class="modal-body">
      <div id="ai-body" class="ai-body"></div>
    </div>
  </div>
</div>"""


# AI 评估弹窗交互(依赖运行时 AI_EVALS / _NAMES)。openAiEval 用 textContent 渲染(天然防 XSS,
# 无需 markdown);标题/遮罩/Esc 关闭镜像 _CHART_MODAL_JS。独立 id(ai-modal-title/ai-body)避免与
# 图表弹窗的 modal-title 冲突(getElementById 只返第一个匹配)。
_AI_EVAL_MODAL_JS = r"""
async function openAiEval(sym){
  var ov=document.getElementById('ai-modal'),
      title=document.getElementById('ai-modal-title'),
      body=document.getElementById('ai-body');
  var nm=(typeof _NAMES!=='undefined' && _NAMES[sym]) ? _NAMES[sym] : sym;
  title.textContent = nm + ' · AI 评估';
  ov.hidden = false;
  document.body.style.overflow = 'hidden';
  body.textContent = '⏳ 生成中…(实时调大模型,约 10-30 秒,请勿关闭)';
  try{
    var r = await fetch('http://127.0.0.1:8765/ai-eval?sym=' + sym);
    var data = await r.json();
    body.textContent = data.ok ? data.text
      : '⚠ 生成失败: ' + (data.error||'未知') + '\n(检查 ai_eval_server 是否运行、LLM key 是否配置)';
  }catch(e){
    body.textContent = '⚠ AI 评估服务未启动或不可达。\n请先在新终端运行: python scripts/ai_eval_server.py';
  }
}
function closeAiEval(){
  var ov = document.getElementById('ai-modal');
  if(!ov) return;
  ov.hidden = true;
  document.body.style.overflow = '';
}
document.addEventListener('keydown', function(e){
  var ov = document.getElementById('ai-modal');
  if(ov && !ov.hidden && (e.key==='Escape' || e.keyCode===27)) closeAiEval();
});
document.addEventListener('DOMContentLoaded', function(){
  var ov = document.getElementById('ai-modal');
  if(ov) ov.addEventListener('click', function(e){ if(e.target===ov) closeAiEval(); });
});
"""


def _chart_assets(stock_diagnoses: dict, names: dict, store, period: int) -> str:
    """点卡片 📊 弹模态窗看该股 4 张时序图(价格+偏离 / 估值分位 / 业绩年报 / 分红)。
    store=None → 空串(向后兼容)。图表以 JSON 嵌入 CHARTS dict,点开时才 Plotly.newPlot
    懒渲染——页面只承载紧凑 JSON,股票再多也只画当前这一只(可扩展,避免一次性渲染几百张)。"""
    if store is None:
        return ""
    import re
    from plotly.offline import get_plotlyjs

    from ..config import get_config
    commodity_map = (get_config().params.get("stock", {}) or {}).get("commodity_map", {}) or {}
    charts: dict[str, list[str]] = {}
    max_n = 0
    for sym, d in stock_diagnoses.items():
        name = names.get(sym, sym)
        built = [
            sf.price_deviation_figure(sym, name, store.get_series(sym), period),
            # PE 和 PB 都画(价值/银行看 PB 更准,成长/周期看 PE;两个口径都给,任由判断)
            sf.valuation_figure(sym, name, store.get_stock_valuation_series(sym, "pe_ttm"), "pe_ttm"),
            sf.valuation_figure(sym, name, store.get_stock_valuation_series(sym, "pb"), "pb"),
            sf.earnings_figure(sym, name,
                               store.get_stock_financials_panel(sym, ["revenue", "net_profit"])),
            # S07 利润归因:每年 业绩/估值/分红 三段(多年滚动,看回报驱动力)
            sf.attribution_figure(sym, name, sd.attribution_by_year(sym, store, 6)),
            sf.dividend_figure(sym, name, store.get_stock_dividend_series(sym)),
        ]
        # 周期股:映射商品价时序放首位(A 类领先信号,打开 📊 先看上游商品价)
        variety = commodity_map.get(sym)
        comm = sf.commodity_price_figure(variety, store.get_commodity_series(variety)) if variety else None
        if comm is not None:
            built = [comm] + built
        max_n = max(max_n, len(built))
        charts[sym] = [f.to_json() for f in built]

    slots = "\n".join(f'      <div id="m-chart-{i}" class="modal-chart"></div>' for i in range(max_n))
    modal_html = _CHART_MODAL_HTML.replace("__SLOTS__", slots)
    # 每个 to_json() 是合法 JSON → 直接作 JS 对象字面量;纯拼接(不用 %/format,plotly.js 含大量 % {})
    entries = ",\n".join(f'"{sym}":[{",".join(charts[sym])}]' for sym in charts)
    names_js = ", ".join(f'"{sym}":"{html.escape(names.get(sym, sym))}"' for sym in charts)
    plotly_js = re.sub(r"</script", r"<\\/script", get_plotlyjs(), flags=re.I)
    entries = re.sub(r"</script", r"<\\/script", entries, flags=re.I)   # 防御 figure JSON
    body = (plotly_js + "\nvar CHARTS={" + entries + "};\nvar _NAMES={" + names_js + "};\n"
            + _CHART_MODAL_JS)
    return modal_html + "\n<script>\n" + body + "</script>"


def _ai_eval_assets(names: dict) -> str:
    """点卡片 🤖 弹模态窗 → 前端 fetch 本地 ai_eval_server 实时生成评估(纯服务,无预计算嵌入)。

    本函数只注入 modal HTML + openAiEval/closeAiEval JS + _NAMES(标题显示用)。评估文本在
    用户点 🤖 时由前端 fetch http://127.0.0.1:8765/ai-eval 实时获取(服务持 key 调 LLM);
    服务未启动时前端显示"请先运行 ai_eval_server.py"提示。_NAMES 自带注入以防 store=None
    (无图表)时 _chart_assets 未注入 _NAMES。
    """
    names_js = ", ".join(f'"{sym}":"{html.escape(names.get(sym, sym))}"' for sym in names)
    body = "var _NAMES={" + names_js + "};\n" + _AI_EVAL_MODAL_JS
    return _AI_EVAL_MODAL_HTML + "\n<script>\n" + body + "</script>"


def _tab_of(sym: str, d: dict, commodity_map: dict) -> str:
    """个股 → tab 归属:商品跟踪股(commodity_map)或自动分类 cyclic → 周期;其余按分类(未分类→成长)。"""
    if sym in commodity_map or (d.get("classification") or {}).get("primary") == "cyclic":
        return "cyclic"
    prim = (d.get("classification") or {}).get("primary")
    return prim if prim in ("value", "growth") else "growth"


def render(stock_diagnoses: dict, alerts_list: list, as_of: str,
           names: dict | None = None, title: str = "个股诊断看板",
           store=None, period: int | None = None) -> str:
    """渲染个股诊断 HTML。stock_diagnoses = {symbol: diagnose_stock_full 输出}。
    names = {symbol: 显示名}(可选)。alerts_list = collect_stock_alerts 输出。
    store 非空时追加「个股时序图」区(价格/估值/业绩/分红);period 默认 MA_PERIOD。
    🤖 AI 评估按钮始终渲染(点击时 fetch 本地 ai_eval_server 实时生成,无需预计算数据)。"""
    names = names or {}
    prd = period or ti.MA_PERIOD
    with_charts = store is not None
    from ..config import get_config
    commodity_map = (get_config().params.get("stock", {}) or {}).get("commodity_map", {}) or {}
    tabs = {"cyclic": {}, "value": {}, "growth": {}}
    for sym, d in stock_diagnoses.items():
        tabs[_tab_of(sym, d, commodity_map)][sym] = d

    def _cards(group):
        return "\n".join(_card(sym, d, names.get(sym, sym), with_charts=with_charts, with_ai=True)
                         for sym, d in group.items())
    cards = {t: _cards(g) for t, g in tabs.items()}
    n = len(stock_diagnoses)
    chart_section = _chart_assets(stock_diagnoses, names, store, prd)
    ai_section = _ai_eval_assets(names)
    ambush_section = _ambush_region(tabs["cyclic"], names, as_of)      # 仅周期股埋伏
    commodity_section = _commodity_region(store) if store else ""
    commodity_charts = _commodity_charts(store)
    # 信号提醒按 tab 拆:个股级(scope=个股名)→ 各 tab 顶部;市场级(大盘/指数)→ tab 栏上方全局条
    name2tab = {}
    for _t, _group in tabs.items():
        for _sym in _group:
            name2tab[names.get(_sym, _sym)] = _t
            name2tab[_sym] = _t
    market_alerts = [a for a in alerts_list if a.get("scope") not in name2tab]
    tab_alerts = {t: [a for a in alerts_list if name2tab.get(a.get("scope")) == t] for t in tabs}
    alerts_top = (_alerts_region(market_alerts, title="🌐 市场级提醒", empty_msg=False)
                  if alerts_list else _alerts_region([], empty_msg=True))
    tab_meta = (("cyclic", "🔄 周期"), ("value", "💰 价值"), ("growth", "🚀 成长"))
    tab_bar = "".join(
        f'<button class="tab{" active" if t == "cyclic" else ""}" data-tab="{t}" '
        f"onclick=\"switchTab('{t}')\">{label}({len(tabs[t])})</button>"
        for t, label in tab_meta)
    panels = []
    for t, label in tab_meta:
        head = f'<div class="cards-head"><h2>{label}股({len(tabs[t])})</h2></div>'
        grid = f'<div class="grid">\n{cards[t]}\n</div>'
        lead = _alerts_region(tab_alerts[t], empty_msg=False)   # 该 tab 个股级提醒
        inner = f"{lead}\n{commodity_section}\n{commodity_charts}\n{ambush_section}\n{head}\n{grid}" if t == "cyclic" else f"{lead}\n{head}\n{grid}"
        panels.append(f'<div id="tab-{t}" class="tab-panel{" active" if t == "cyclic" else ""}">\n{inner}\n</div>')
    panels_html = "\n".join(panels)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body>
<div class="header">
  <button id="theme-btn" class="toggle" onclick="toggleTheme()">🌙 深浅色</button>
  <h1>{html.escape(title)}</h1>
  <p class="muted">as_of {html.escape(as_of)} · {n} 只个股 · 周期/价值/成长 三 tab</p>
</div>
{alerts_top}
{_LEGEND_HTML}
<div class="tabs">
{tab_bar}
</div>
{panels_html}
{chart_section}
{ai_section}
<script>{_THEME_JS}</script>
</body></html>"""


def write_html(html_str: str, output: str | Path) -> Path:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_str, encoding="utf-8")
    return out
