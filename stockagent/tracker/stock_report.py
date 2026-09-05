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


def pct_color(pct) -> str:
    """偏离分位着色(阈值与 🚦 横幅一致:超买≥95% 红 / 超卖≤5% 绿,常态灰)。"""
    if _nan(pct):
        return "var(--muted)"
    p = float(pct)
    if p >= 0.95:
        return "#b91c1c"
    if p <= 0.05:
        return "#15803d"
    return "var(--muted)"


def _pct_exact(pct) -> str:
    """偏离分位真实值直显(0.9496 → '94.96%',不四舍五入成 95%——显示与着色/横幅阈值不打架)。"""
    return f"{int(pct * 10000) / 100:g}%"


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
    """🧲 商品 A 类面板:全品种(17) 现价/同比/近20日/近60日/隔夜/判定 + 板块指引。

    商品价领先周期股财报 1-4 月;判定看同比(趋势)+ 近60日(边际):向上=埋伏方向,背离/向下=避。
    近20日=快腿动量(2026-09 提速改版);隔夜=夜盘快照 vs 最近日收盘(盘前可见,无快照显示 —)。
    品种清单源自 fetcher.COMMODITY_CODES(单一数据源),无数据的品种自动跳过。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    import datetime as _dt
    from ..data import fetcher
    from . import stock_figures as sf
    varieties = list(fetcher.COMMODITY_CODES.keys())
    # 夜盘快照(可选腿):{variety: price};快照超过 2 天视为失效显示 —
    spot_map: dict = {}
    spot_date = ""
    if hasattr(store, "get_commodity_spot"):
        try:
            sdf = store.get_commodity_spot()
            if len(sdf):
                spot_date = str(sdf["date"].max())
                if (_dt.date.today() - _dt.date.fromisoformat(spot_date)).days <= 2:
                    spot_map = dict(zip(sdf["variety"], sdf["price"].astype(float)))
        except Exception:  # noqa: BLE001 — 快照表缺失/空,静默降级
            pass
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
        n20 = min(20, len(s) - 1)
        yoy = float(s.iloc[-1]) / float(s.iloc[-1 - n252]) - 1.0
        rec = float(s.iloc[-1]) / float(s.iloc[-1 - n60]) - 1.0
        m20 = float(s.iloc[-1]) / float(s.iloc[-1 - n20]) - 1.0
        win60 = s.iloc[-n60:]
        mark = "🔺" if float(win60.iloc[-1]) >= float(win60.max()) \
            else ("🔻" if float(win60.iloc[-1]) <= float(win60.min()) else "")
        # 偏离度历史分位(与 🚦 横幅/放大视图同源 commodity_dev_stats;缺数据显示 —)
        dst = sf.commodity_dev_stats(s)
        pct = dst.get("pct") if dst else None
        if yoy > 0.10 and rec > -0.05:
            judge, color = "向上", "#16a34a"
        elif yoy > 0.10:
            judge, color = "背离", "#d97706"
        elif yoy > -0.10:
            judge, color = "震荡", "#64748b"
        else:
            judge, color = "向下", "#dc2626"
        summary[judge].append(v)
        overnight = "—"
        if v in spot_map and float(s.iloc[-1]) > 0:
            overnight = f"{float(spot_map[v]) / float(s.iloc[-1]) - 1.0:+.1%}"
        rows.append((v, float(s.iloc[-1]), yoy, m20, rec, pct, overnight, judge, color, mark))
    if not rows:
        return ""
    body = "".join(
        f"<tr><td style='{td}'>{v}</td><td style='{td};text-align:center'>{val:.0f}</td>"
        f"<td style='{td};text-align:center'>{_pct(yoy, True)}</td>"
        f"<td style='{td};text-align:center'>{_pct(m20, True)}</td>"
        f"<td style='{td};text-align:center'>{_pct(rec, True)}</td>"
        f"<td style='{td};text-align:center;color:{pct_color(pct)};font-weight:600'>"
        f"{('—' if _nan(pct) else _pct_exact(pct))}</td>"   # 真实值直显(94.96% 不四舍五入成 95%),着色按原值判
        f"<td style='{td};text-align:center'>{on}</td>"
        f"<td style='{td};text-align:center;color:{color};font-weight:600'>{judge}{mark}</td></tr>"
        for (v, val, yoy, m20, rec, pct, on, judge, color, mark) in rows)
    head = (f"<tr><th style='{th};text-align:left'>商品(板块)</th><th style='{th}'>现价</th>"
            f"<th style='{th}'>同比</th><th style='{th}'>近20日</th><th style='{th}'>近60日</th>"
            f"<th style='{th}'>偏离分位</th><th style='{th}'>隔夜</th><th style='{th}'>判定</th></tr>")
    guide = (f"向上(埋伏方向):{','.join(summary['向上']) or '—'} | "
             f"背离(避):{','.join(summary['背离']) or '—'} | "
             f"向下(避):{','.join(summary['向下']) or '—'}")
    spot_note = f" · 隔夜快照 {spot_date}" if spot_map else ""
    return ('<div class="alerts"><h2>🧲 商品 A 类面板 '
            '<span class="count">上游价 → 周期股业绩领先信号</span></h2>'
            f'<p class="muted">商品价领先财报 1-4 月;判定看同比(趋势)+近60日(边际),近20日=快腿动量,'
            f'偏离分位=偏离度的历史分位(红≥95%/绿≤5%,与🚦横幅同阈),🔺🔻=现价创60日新高/新低。'
            f'{guide}{spot_note}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead>{head}</thead><tbody>{body}</tbody></table></div>')


def _commodity_extreme_banner(store, config=None) -> str:
    """🚦 商品异动雷达(常驻占位,2026-09 提速改版·双段语义,替代混合 chip 的歧义):

    ⚠ 异动提醒段(快腿):20日动量≥±mo_th + 60日新高/新低 → "正在发生,排进研究队列"(非买入
    信号——event-study 首跑:追动量买股 20日超额 -2.6%,Claim 002)。
    ⛔ 极端警戒段(慢腿):价格/MA60 偏离分位 超买≥th/超卖≤1-th → "伸展度历史级,追高风险"。
    同品种可双段同时出现(既在动又在伸展=两句话都成立)。A股配色 红=向上/超买,绿=向下/超卖。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    from ..config import get_config
    from ..data import fetcher
    from . import stock_figures as sf
    cfg = config or get_config()
    lp = (cfg.params.get("stock") or {}).get("leading") or {}
    th = float(lp.get("commodity_extreme_pct", 0.95))
    mo_th = float(lp.get("commodity_momentum_pct", 0.10))
    n_ok = 0
    stats_rows = []          # (variety, stats dict) 按品种展示序
    for v in fetcher.COMMODITY_CODES:
        st = sf.commodity_dev_stats(store.get_commodity_series(v))
        if not st or _nan(st.get("pct")):
            continue
        n_ok += 1
        stats_rows.append((v, st))

    fast, slow = [], []      # fast=(variety, cur, tags, up) / slow=(variety, cur, tags)
    for v, st in stats_rows:
        m20 = st.get("momentum20")
        stags, ftags = [], []
        if st["pct"] >= th:
            stags.append(f"分位 {st['pct']:.0%} · 超买")
        elif st["pct"] <= 1.0 - th:
            stags.append(f"分位 {st['pct']:.0%} · 超卖")
        if not _nan(m20) and abs(float(m20)) >= mo_th:
            ftags.append(f"20日{float(m20):+.0%} · 动量")
        if st.get("new_high60"):
            ftags.append("60日新高")
        if st.get("new_low60"):
            ftags.append("60日新低")
        if ftags:
            up = bool(st.get("new_high60")) or (_nan(m20) is False and float(m20) >= 0)
            fast.append((v, st["cur"], ftags, up))
        if stags:
            slow.append((v, st["cur"], stags))

    n_ob = sum(1 for _, _, tg in slow if any("超买" in t for t in tg))
    n_os = sum(1 for _, _, tg in slow if any("超卖" in t for t in tg))
    n_mo = sum(1 for _, _, tg, _ in fast if any("动量" in t for t in tg))
    n_brk = sum(1 for _, _, tg, _ in fast if any("新高" in t or "新低" in t for t in tg))

    def _fast_chip(v, cur, tags, up):
        sym = "<b class='sym-up'>▲</b>" if up else "<b class='sym-dn'>▼</b>"
        return (f"<span class='dev-chip mv'>{sym} <b>{html.escape(v)}</b> {cur:+.1%}"
                f"<small>{' · '.join(tags)}</small></span>")

    def _slow_chip(v, cur, tags):
        cls = "ob" if any("超买" in t for t in tags) else "os"
        return (f"<span class='dev-chip {cls}'><b>{html.escape(v)}</b> {cur:+.1%}"
                f"<small>{' · '.join(tags)}</small></span>")

    if fast or slow:
        count = (f"<span class='count'>动量{n_mo} · 突破{n_brk} | 超买{n_ob} · 超卖{n_os}</span>")
        parts = []
        if fast:
            parts.append("<p class='dev-sec'>⚠ 异动提醒 · 快腿 → 排进研究队列(非买入信号)</p>"
                         + "".join(_fast_chip(*x) for x in fast))
        if slow:
            parts.append("<p class='dev-sec'>⛔ 极端警戒 · 慢腿 → 伸展度历史级,追高风险</p>"
                         + "".join(_slow_chip(*x) for x in slow))
        state = "".join(parts)
    else:
        count = "<span class='count'>常态</span>"
        state = f"<p class='muted'>当前 {n_ok} 个品种偏离度与动量均处常态区间</p>"
    concl = ""
    if hasattr(store, "get_meta"):
        try:
            concl = store.get_meta("commodity_speed_conclusion", "") or ""
        except Exception:  # noqa: BLE001 — meta 缺失静默
            concl = ""
    concl_html = (f"<p class='muted'>🔬 快腿实证(validate_commodity_speed):{concl}</p>" if concl else "")
    return (f'<div class="alerts"><h2>🚦 商品异动雷达 {count}</h2>'
            f'<p class="muted">慢腿:价格/MA60 偏离分位 超买≥{th:.0%}/超卖≤{1-th:.0%} · '
            f'快腿:20日动量≥±{mo_th:.0%} + 60日新高/新低'
            f'（A股红=向上/绿=向下;观察非信号,不构成买卖建议）</p>{state}{concl_html}</div>')


def _fig_json_readable(fig) -> str:
    """fig → JSON 字符串,数值数组强制展开为普通 JSON 数组。

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
                tr[key] = [None if isinstance(z, float) and math.isnan(z) else z
                           for z in arr.tolist()]
            except Exception:
                pass                                       # 解不开保留原块(Plotly 自己能画)
    return json.dumps(d, ensure_ascii=False)


def _commodity_charts(store) -> str:
    """📈 商品价时序图面板(全品种折线,inline 渲染于周期 tab)。复用 stock_figures.commodity_price_figure;
    DOMContentLoaded 时 newPlot(Plotly 已由个股图表模态加载)。品种清单源自 fetcher.COMMODITY_CODES(单一数据源)。
    点击任一小图 → comm-modal 放大视图(客户端复用同一份 COMM JSON 改样式,不重复传图):
    高度拉满 + 底部 rangeslider 拖拽 + 1月/6月/1年/3年/全部 快捷窗(与 _RANGE_BUTTONS 同五档)。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    from . import stock_figures as sf
    from ..data import fetcher
    import json
    import re
    varieties = list(fetcher.COMMODITY_CODES.keys())
    sers = [(v, store.get_commodity_series(v)) for v in varieties]
    figs = [(v, sf.commodity_price_figure(v, s)) for v, s in sers]
    dev_stats = [sf.commodity_dev_stats(s) for _, s in sers]
    if not figs:
        return ""
    divs = "".join(
        f'<div id="comm-chart-{i}" class="comm-chart" title="点击放大查看" onclick="openCommChart({i})"></div>'
        for i in range(len(figs)))
    # 可读序列化:to_json() 的 bdata 二进制块让前端派生偏离度时读不到逐点数值(见 _fig_json_readable)
    arr = ",".join(re.sub(r"</script", r"<\\/script", _fig_json_readable(f), flags=re.I) for _, f in figs)
    names = ",".join(json.dumps(v, ensure_ascii=False) for v, _ in figs)
    devs = ",".join(json.dumps(d, ensure_ascii=False) for d in dev_stats)
    js = ("var COMM=[" + arr + "];\n"
          "var COMM_NAMES=[" + names + "];\n"
          "var COMM_DEV=[" + devs + "];\n"
          "document.addEventListener('DOMContentLoaded',function(){\n"
          "  if(!window.Plotly)return;\n"
          "  COMM.forEach(function(fig,i){var gd=document.getElementById('comm-chart-'+i);"
          "    if(gd)Plotly.newPlot(gd,fig,{responsive:true,displaylogo:false});});\n"
          "  setTimeout(function(){if(window._applyPlotly)_applyPlotly(_isDark());},60);\n"
          "});\n" + _COMM_MODAL_JS)
    return ('<div class="alerts"><h2>📈 商品价时序(A 类领先信号)</h2>'
            '<p class="muted">商品价领先周期股财报 1-4 月;折线=价,虚线=1 年前水平(同比可视化)。'
            '点击任意图放大:放大视图下行为价格/MA60 偏离度(标注历史极值线与当前第几高/第几低),'
            '底部滑块 + 1月/6月/1年/3年/全部 快捷窗伸缩横轴。结合下方个股 📊 判断。</p>'
            f'<div class="comm-grid">{divs}</div></div>' + _COMM_MODAL_HTML
            + '\n<script>' + js + '</script>')


_COMM_MODAL_HTML = """
<div id="comm-modal" class="modal-overlay" hidden>
  <div class="modal-box">
    <div class="modal-head">
      <span id="comm-modal-title">—</span>
      <button type="button" class="modal-close" onclick="closeCommChart()" title="关闭 (Esc)">✕</button>
    </div>
    <div class="modal-body">
      <div id="comm-modal-chart"></div>
    </div>
  </div>
</div>"""


# 商品放大模态交互(依赖运行时的 Plotly / COMM / COMM_NAMES / COMM_DEV / _applyPlotly / _isDark)。
# 放大视图=双行子图:上行 价格+MA60,下行 偏离度(前端从已下发的价格/MA60 两条 trace 逐点相除派生,
# 零重复传参;排名/极值数字由服务端 ti 同口径算好放 COMM_DEV)。双行共享横轴=每行一个 x 轴
# (x/x2 matches 联动,rangeslider 挂底行 x2)——与 price_deviation_figure 的 make_subplots 同构。
# 注意 comm-modal-chart 刻意不带 class="modal-chart":openChart 会对全部 .modal-chart
# purge/隐藏,挂同类名会被个股模态误伤(display:none 后 newPlot 得零尺寸图)。
_COMM_MODAL_JS = """
function openCommChart(i){
  var src = COMM[i];
  if(!src || !window.Plotly) return;
  var f = JSON.parse(JSON.stringify(src));      // 深拷贝同份数据,只改展示样式
  var L = f.layout || (f.layout = {});
  L.height = Math.max(560, Math.round(window.innerHeight * 0.80));
  L.title = L.title || {}; L.title.font = {size: 16};
  L.margin = L.margin || {}; L.margin.t = 70; L.margin.b = 60;
  var hasRows = f.data && f.data.length >= 2;   // [价格, MA60] 两条 trace 才能派生偏离度
  if(hasRows){
    var D = COMM_DEV[i] || {};
    if(D.new_high60) L.title.text = (L.title.text || '') + ' · 🔺60日新高';    // 突破腿徽章
    else if(D.new_low60) L.title.text = (L.title.text || '') + ' · 🔻60日新低';
    var p = f.data[0], m = f.data[1];
    var dx = [], dy = [];
    for(var k = 0; k < p.x.length; k++){
      var pv = p.y[k], mv = m.y[k];
      dx.push(p.x[k]);
      dy.push((pv == null || mv == null || mv === 0) ? null : pv / mv - 1);
    }
    f.data.push({x: dx, y: dy, xaxis: 'x2', yaxis: 'y2', name: '偏离度(价格/MA60−1)',
                 line: {color: '#52514e', width: 1.4},
                 hovertemplate: '%{x|%Y-%m-%d}<br>偏离 %{y:.1%}<extra></extra>'});
    // 双行共享横轴:每行一个 x 轴,底行 x2 与顶行 x 用 matches 联动,rangeslider 挂 x2(底行)。
    // 与 price_deviation_figure 的 make_subplots(shared_xaxes)+row2 滑块同构——单 x 轴锚在顶行
    // y 底部会把下行带让给轴标签+滑块,偏离度会被盖住(首版踩过的坑)。
    L.yaxis.domain = [0.44, 1];
    L.yaxis.title = {text: '价格'};
    L.yaxis2 = {domain: [0, 0.30], title: {text: '偏离度'}, tickformat: '.0%',
                gridcolor: L.yaxis.gridcolor, zerolinecolor: L.yaxis.zerolinecolor};
    var xa = L.xaxis || (L.xaxis = {});
    xa.type = 'date';
    xa.showticklabels = false;                  // 刻度标签只在底行,避免重复
    xa.matches = 'x2';                          // 顶行跟随底行:主控轴是挂滑块+快捷窗的 x2(make_subplots 同构)。
                                                // matches 若放在挂 rangeselector 的轴上,按钮不渲染(plotly 行为)
    L.xaxis2 = {type: 'date', anchor: 'y2',
                gridcolor: L.yaxis.gridcolor, zerolinecolor: L.yaxis.zerolinecolor,
                rangeslider: {visible: true},
                rangeselector: {x: 0, xanchor: 'left', y: 1.02, yanchor: 'bottom',
                                bgcolor: '#ffffff', activecolor: '#e2e8f0',
                                buttons: [
                  {count:1, label:'1月', step:'month', stepmode:'backward'},
                  {count:6, label:'6月', step:'month', stepmode:'backward'},
                  {count:1, label:'1年', step:'year', stepmode:'backward'},
                  {count:3, label:'3年', step:'year', stepmode:'backward'},
                  {label:'全部', step:'all'}]}};
    if(D.max != null && D.min != null){        // 偏离度历史极值线(红=正/蓝=负,同个股偏离图)
      L.shapes = (L.shapes || []).concat([
        {type:'line', xref:'x2', yref:'y2', x0:dx[0], x1:dx[dx.length-1], y0:D.max, y1:D.max,
         line:{color:'#d03b3b', width:1.2, dash:'dot'}},
        {type:'line', xref:'x2', yref:'y2', x0:dx[0], x1:dx[dx.length-1], y0:D.min, y1:D.min,
         line:{color:'#1c5cab', width:1.2, dash:'dot'}}]);
    }
    var lk = -1;                               // 最后一个有效偏离点=当前
    for(k = dy.length - 1; k >= 0; k--){ if(dy[k] != null){ lk = k; break; } }
    var lastDate = (lk >= 0) ? dx[lk] : null;
    // 历史 Top-K 高/低极值点打排名标注(第几高/第几低,▲/▼ 同 ETF 看板极值标记),极值日
    // ==当前日时跳过(现在点标注已含「第1高/低」语义,避免两段文字叠在同一点)。
    // lastDate 是带时刻的 ISO 串(如 2026-09-04T00:00:00),与 e.d 前缀比对。
    var pushExt = function(e, hi){
      if(!e || !e.d || lastDate === null) return;
      if(lastDate.indexOf(e.d) === 0) return;
      // Top-K=10 时极值日常互相挤在同一小段(如碳酸锂前十高集中在三周内):
      // 标签 10px + 按排名轮换 中/左/右 方位扇形错开,减轻文字重叠;悬停始终有精确日期+值
      var fan = (e.r - 1) % 3;
      var pos = hi ? ['bottom center','bottom left','bottom right'][fan]
                   : ['top center','top left','top right'][fan];
      f.data.push({x: [e.d], y: [e.v], xaxis: 'x2', yaxis: 'y2', mode: 'markers+text',
                   text: ['第' + e.r + (hi ? '高 +' : '低 ') + (e.v*100).toFixed(0) + '%'],
                   textposition: pos, textfont: {size: 10},
                   marker: {size: 8, color: hi ? '#d03b3b' : '#1c5cab',
                            symbol: hi ? 'triangle-up' : 'triangle-down'},
                   showlegend: false,
                   hovertemplate: '第' + e.r + (hi ? '高' : '低') + ' %{x|%Y-%m-%d}<br>偏离 %{y:.1%}<extra></extra>'});
    };
    (D.highs || []).forEach(function(e){ pushExt(e, true); });
    (D.lows || []).forEach(function(e){ pushExt(e, false); });
    if(lk >= 0){
      var tag = '现在 ' + (dy[lk]*100).toFixed(1) + '%';
      if(D.rank_high != null) tag += ' · 第' + D.rank_high + '高 / 第' + D.rank_low + '低';
      f.data.push({x: [dx[lk]], y: [dy[lk]], xaxis: 'x2', yaxis: 'y2', mode: 'markers+text', text: [tag],
                   textposition: 'top center', marker: {size: 9, color: '#0f172a'},
                   showlegend: false, hoverinfo: 'skip'});
    }
  } else {                                     // 占位图(无数据):单行,仅滑块
    var xa0 = L.xaxis || (L.xaxis = {});
    xa0.type = 'date';
    xa0.rangeslider = {visible: true};
  }
  var ov = document.getElementById('comm-modal');
  ov.hidden = false;                            // 先显示(容器拿到真实宽度)再 newPlot,图才能横向占满
  document.body.style.overflow = 'hidden';
  document.getElementById('comm-modal-title').textContent = (COMM_NAMES[i] || '') + ' 商品价时序 · 放大';
  var gd = document.getElementById('comm-modal-chart');
  try{ Plotly.purge(gd); }catch(e){}
  Plotly.newPlot(gd, f, {responsive:true, displaylogo:false})
    .then(function(){ _applyPlotly(_isDark()); });
}
function closeCommChart(){
  var ov = document.getElementById('comm-modal');
  if(!ov) return;
  ov.hidden = true;
  document.body.style.overflow = '';
  try{ Plotly.purge(document.getElementById('comm-modal-chart')); }catch(e){}
}
document.addEventListener('keydown', function(e){
  var ov = document.getElementById('comm-modal');
  if(ov && !ov.hidden && (e.key==='Escape' || e.keyCode===27)) closeCommChart();
});
document.addEventListener('DOMContentLoaded', function(){
  var ov = document.getElementById('comm-modal');
  if(ov) ov.addEventListener('click', function(e){ if(e.target===ov) closeCommChart(); });
});
"""


def _ambush_region(diagnoses: dict, names: dict, as_of: str,
                   score_min: float = 30.0, show_below: int = 3) -> str:
    """🎯 提前埋伏候选排名(只读,基本面领先):深跌×alignment×含金量×未兑现×企稳 综合分降序。

    门槛线(≥score_min)以上的全展示;线下展示 top show_below 个(灰色),让用户看到全貌。
    ≥60 绿/≥40 黄高亮。不含涨跌预测。"""
    from .stock_diagnose import positioning_from_diag
    above, below = [], []
    for sym, d in diagnoses.items():
        p = positioning_from_diag(d)
        sc = p.get("score")
        if _nan(sc):
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
        row = (sc,
               f"<tr><td style='{td}'><b>{html.escape(nm)}</b>"
               f"<br><span class='muted'>{sym}</span></td>"
               f"<td style='{td};text-align:center'>{dd_s}</td>"
               f"<td style='{td};text-align:center'>{et_s}{flags}</td>"
               f"<td style='{td};text-align:center'>{rr_s}</td>"
               f"<td style='{td};text-align:center'>{ex_s}</td>"
               f"<td style='{td};text-align:center;font-size:16px;font-weight:bold;{hot}'>{sc:.0f}</td></tr>")
        (above if sc >= score_min else below).append(row)
    if not above and not below:
        return ""
    above.sort(key=lambda x: x[0], reverse=True)
    below.sort(key=lambda x: x[0], reverse=True)
    th = "padding:8px;border-bottom:2px solid var(--border)"
    head = (f"<tr><th style='{th};text-align:left'>股票</th><th style='{th}'>深跌</th>"
            f"<th style='{th}'>业绩拐头</th><th style='{th}'>近60日涨</th>"
            f"<th style='{th}'>兑现窗口</th><th style='{th}'>埋伏分</th></tr>")
    parts = [f'<table style="width:100%;border-collapse:collapse;font-size:13px"><thead>{head}</thead>'
             f'<tbody>{"".join(r for _, r in above)}</tbody></table>']
    if below:
        shown = below[:show_below]
        sep = ('<tr><td colspan="6" style="padding:4px 6px;text-align:center;color:var(--muted);'
               'font-size:11px;border-top:2px dashed var(--border);border-bottom:1px dashed var(--border)">'
               f'—— 门槛线 {score_min:.0f} 以下(top {len(shown)},灰色参考) ——</td></tr>')
        parts.append(f'<table style="width:100%;border-collapse:collapse;font-size:12px;opacity:0.6">'
                     f'<tbody>{sep}{"".join(r for _, r in shown)}</tbody></table>')
    return ('<div class="alerts"><h2>🎯 提前埋伏候选 <span class="count">深跌×alignment×含金量×未兑现×企稳</span></h2>'
            '<p class="muted">门槛线 ≥30 全展示;线下展示 top 3(灰色参考)。'
            '入场=领先基本面;预告/正报=兑现出场。不含涨跌预测,非荐股。</p>'
            + "\n".join(parts) + '</div>')


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

    _lp = disc.get("latest_period") or "?"
    _dl = disc.get("deadline") or "?"
    disc_txt = (f'{_lp[:4]}报 截止{_dl}'
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
/* 周期 tab 内子 tab(商品周期/其他周期) */
.subtabs { display:flex; gap:4px; flex-wrap:wrap; margin:10px 0 0 4px; border-bottom:2px solid var(--border); }
.subtab { background:transparent; border:none; border-bottom:3px solid transparent; padding:6px 12px; cursor:pointer; font-size:13px; font-weight:600; color:var(--muted); }
.subtab:hover { color:var(--text); }
.subtab.active { color:var(--text); border-bottom-color:#ea580c; }
.subtab-panel { display:none; padding-top:8px; }
.subtab-panel.active { display:block; }
.comm-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(440px,1fr)); gap:10px; margin-top:8px; }
.comm-chart { min-height:300px; position:relative; cursor:pointer; border-radius:6px; }
.comm-chart:hover { box-shadow:0 0 0 2px var(--border); }
.comm-chart::after { content:'⛶ 放大'; position:absolute; top:4px; right:8px; z-index:3;
  font-size:11px; color:var(--muted); background:var(--card); border:1px solid var(--border);
  border-radius:4px; padding:1px 6px; opacity:0; transition:opacity .12s; pointer-events:none; }
.comm-chart:hover::after { opacity:1; }
/* dev-chip:超买/超卖标签(A股配色 红=超买 绿=超卖) */
.dev-chip { display:inline-block; padding:4px 10px; margin:3px 8px 3px 0; border-radius:6px;
  font-size:13px; font-weight:600; }
.dev-chip.ob { background:#fee2e2; color:#b91c1c; }
.dev-chip.os { background:#dcfce7; color:#15803d; }
.dev-chip.mv { background:#dbeafe; color:#1e3a8a; }
.dev-chip .sym-up { color:#b91c1c; }
.dev-chip .sym-dn { color:#15803d; }
.dev-chip small { font-weight:400; font-size:11px; margin-left:6px; opacity:.85; }
.dev-sec { font-size:11.5px; font-weight:700; color:var(--muted); margin:8px 0 2px; }
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
function switchSubTab(name){
  document.querySelectorAll('.subtab').forEach(b=>b.classList.toggle('active', b.dataset.subtab===name));
  document.querySelectorAll('.subtab-panel').forEach(p=>p.classList.toggle('active', p.id==='subtab-'+name));
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
    """点卡片 📊 弹模态窗看该股时序图(价格+偏离 / PE / PB / 业绩 / S07归因 / 分红;
    周期股额外在首位插「股价 vs 上游商品价」双轴叠加图)。
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
        # 周期股:股价 vs 上游商品价 双轴叠加放首位(A 类领先信号,打开 📊 先看商品→股价传导)
        variety = commodity_map.get(sym)
        if variety:
            overlay = sf.commodity_stock_overlay_figure(
                sym, name, variety, store.get_series(sym), store.get_commodity_series(variety))
            built = [overlay] + built
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
    cyclic_commodity = {sym: d for sym, d in tabs["cyclic"].items() if sym in commodity_map}
    cyclic_other = {sym: d for sym, d in tabs["cyclic"].items() if sym not in commodity_map}
    cards_commodity = _cards(cyclic_commodity)
    cards_other = _cards(cyclic_other)
    ambush_section = _ambush_region(cyclic_commodity, names, as_of)   # 仅商品周期股(有 commodity_map)
    commodity_section = _commodity_region(store) if store else ""
    commodity_charts = _commodity_charts(store)
    commodity_banner = _commodity_extreme_banner(store)               # 🚦 偏离度极端区(商品周期股顶部,参照 ETF 看板)
    # 信号提醒按 tab 拆:个股级(scope=个股名)→ 各 tab 顶部;市场级(大盘/指数)→ tab 栏上方全局条
    name2tab = {}
    for _t, _group in tabs.items():
        for _sym in _group:
            name2tab[names.get(_sym, _sym)] = _t
            name2tab[_sym] = _t
    market_alerts = [a for a in alerts_list if a.get("scope") not in name2tab]
    tab_alerts = {t: [a for a in alerts_list if name2tab.get(a.get("scope")) == t] for t in tabs}
    # 周期 tab 子 tab 各自的提醒(商品周期 vs 其他周期)
    comm_names = set()
    for sym in cyclic_commodity:
        comm_names.add(names.get(sym, sym))
        comm_names.add(sym)
    comm_alerts = [a for a in tab_alerts["cyclic"] if a.get("scope") in comm_names]
    other_alerts = [a for a in tab_alerts["cyclic"] if a.get("scope") not in comm_names]
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
        if t == "cyclic":
            comm_al = _alerts_region(comm_alerts, empty_msg=False)
            other_al = _alerts_region(other_alerts, empty_msg=False)
            sub_bar = ('<div class="subtabs">'
                       f'<button class="subtab active" data-subtab="comm" onclick="switchSubTab(\'comm\')">🧲 商品周期股({len(cyclic_commodity)})</button>'
                       f'<button class="subtab" data-subtab="other" onclick="switchSubTab(\'other\')">🔄 其他周期股({len(cyclic_other)})</button>'
                       '</div>')
            sec_comm = (f'<div id="subtab-comm" class="subtab-panel active">\n'
                        f'{commodity_banner}\n{comm_al}\n{commodity_section}\n{commodity_charts}\n{ambush_section}\n'
                        f'<div class="cards-head"><h2>商品周期股({len(cyclic_commodity)})</h2></div>\n'
                        f'<div class="grid">\n{cards_commodity}\n</div>\n</div>')
            sec_other = (f'<div id="subtab-other" class="subtab-panel">\n'
                         f'{other_al}\n'
                         f'<div class="cards-head"><h2>其他周期股({len(cyclic_other)})</h2></div>\n'
                         f'<div class="grid">\n{cards_other}\n</div>\n</div>') if cyclic_other else ""
            inner = f"{sub_bar}\n{sec_comm}\n{sec_other}"
        else:
            inner = f"{lead}\n{head}\n{grid}"
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
