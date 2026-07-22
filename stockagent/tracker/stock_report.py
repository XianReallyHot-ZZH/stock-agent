"""个股诊断 HTML 看板渲染 — Phase 2 交付通道。

卡片式布局(诊断是「每股多指标快照」而非时间序列,卡片比折线图更贴形):顶部告警区 +
每股一卡(分类 / 估值zone / 戴维斯 / 关键指标 / 避坑 / 预告 / E3偏离)。深浅色可切(localStorage)。
纯 f-string HTML,无模板引擎,照 research/report.py 风格。双击即看、发文件即分享。
"""
from __future__ import annotations

import html
import math
from pathlib import Path


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _pct(v, signed: bool = False) -> str:
    if _nan(v):
        return "—"
    return f"{v*100:+.1f}%" if signed else f"{v*100:.0f}%"


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


def _alerts_region(alerts_list: list) -> str:
    if not alerts_list:
        return '<div class="alerts"><h2>📡 信号提醒</h2><p class="muted">当前无触发(观察池稳定,偏离/营收/预告均在正常区)。</p></div>'
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
    return (f'<div class="alerts"><h2>📡 信号提醒 '
            f'<span class="count">⚠{len(warns)} 💡{len(infos)}</span></h2>'
            + "".join(rows) + "</div>")


def _card(sym: str, d: dict, name: str) -> str:
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
    pit = d.get("pitfalls") or {}
    np_ = pit.get("net_profit") or {}
    rev = pit.get("revenue") or {}
    disc = pit.get("disclosure") or {}
    fc = d.get("forecast") or {}
    pt = d.get("price_timing") or {}
    dev = pt.get("deviation") or {}
    bo = pt.get("breakout") or {}

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

    return f"""
    <div class="card">
      <div class="card-head">
        <span class="stock-name">{html.escape(name)}</span>
        <span class="stock-code">{sym}</span>
        <span class="stock-price">¥{_num(d.get("price_last"))} <span class="muted">{d.get("date_last","")}</span></span>
      </div>
      <div class="card-row">{cls_badge} {zone_badge} {dv_badge}</div>
      <table class="metrics">
        <tr><td>营收CAGR</td><td>{_pct(f.get('revenue_cagr'), True)}</td>
            <td>净利CAGR</td><td>{_pct(f.get('profit_cagr'), True)}</td></tr>
        <tr><td>利润波动</td><td>{_pct(f.get('profit_vol'))}</td>
            <td>股息率</td><td>{_pct(f.get('div_yield'))}</td></tr>
        <tr><td>PE(TTM)</td><td>{_num(d.get('pe_ttm'),1)} <span class="muted">(分位{_pct(vz.get('pe_pct'))})</span></td>
            <td>PB</td><td>{_num(d.get('pb'))} <span class="muted">(分位{_pct(vz.get('pb_pct'))})</span></td></tr>
        <tr><td>戴维斯</td><td colspan="3"><span class="muted">净利YoY {_pct(dv.get('profit_yoy_latest'),True)} · PE变化 {_pct(dv.get('pe_change'),True)}</span></td></tr>
        <tr><td>避坑</td><td colspan="3">{pit_txt} · 营收 {_pct(rev.get('yoy'),True)}</td></tr>
        <tr><td>预告链</td><td colspan="3">{fc_txt}</td></tr>
        <tr><td>披露</td><td colspan="3">{disc_txt}</td></tr>
        <tr><td>E3偏离</td><td colspan="3">{_pct(dev.get('pct'))} <span class="muted">({html.escape(bo.get('label','—'))} grade{bo.get('grade','—')})</span></td></tr>
      </table>
    </div>"""


_CSS = """
:root { --bg:#f8fafc; --card:#fff; --text:#0f172a; --muted:#64748b; --border:#e2e8f0; --shadow:0 1px 3px rgba(0,0,0,.08); }
body.dark { --bg:#0f172a; --card:#1e293b; --text:#e2e8f0; --muted:#94a3b8; --border:#334155; --shadow:0 1px 3px rgba(0,0,0,.3); }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; margin:0; padding:20px; font-size:14px; }
h1 { font-size:20px; margin:0 0 4px; }
h2 { font-size:16px; margin:0 0 10px; }
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
"""


def render(stock_diagnoses: dict, alerts_list: list, as_of: str,
           names: dict | None = None, title: str = "个股诊断看板") -> str:
    """渲染个股诊断 HTML。stock_diagnoses = {symbol: diagnose_stock_full 输出}。
    names = {symbol: 显示名}(可选)。alerts_list = collect_stock_alerts 输出。"""
    names = names or {}
    cards = "\n".join(_card(sym, d, names.get(sym, sym)) for sym, d in stock_diagnoses.items())
    n = len(stock_diagnoses)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body>
<div class="header">
  <button class="toggle" onclick="document.body.classList.toggle('dark');
    localStorage.setItem('stock-dark',document.body.classList.contains('dark'))">🌙 深浅色</button>
  <h1>{html.escape(title)}</h1>
  <p class="muted">as_of {html.escape(as_of)} · {n} 只个股 · 数据底座 C0/C0.5/C0.6(price/估值/财报/分红/预告)</p>
</div>
{_alerts_region(alerts_list)}
<h2>个股诊断卡片</h2>
<div class="grid">
{cards}
</div>
<script>if(localStorage.getItem('stock-dark')==='true')document.body.classList.add('dark');</script>
</body></html>"""


def write_html(html_str: str, output: str | Path) -> Path:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_str, encoding="utf-8")
    return out
