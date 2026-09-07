"""大宗商品看板 HTML 渲染 — 第八看板交付通道(2026-09 从个股诊断看板拆出)。

六 section:🚦异动雷达(国内口径,与实证同源) → 📊环境总览(官方中证商品指数+自算广度) →
🧲品种面板(国际基准为主语/国内价对照) → 📈品种+比价时序图(点击放大) → ⚖比价矩阵 →
🎫投资标的映射(二期·错配度)。纯 f-string HTML,照 tracker.stock_report 风格;
深浅色可切默认浅色。只读观测,无推送。
"""
from __future__ import annotations

import html
import math
import re
from pathlib import Path

import pandas as pd

from ..config import get_config
from ..data import fetcher
from ..tracker import stock_figures as sf
from . import figures as cfg_fig
from . import fundamentals as fund
from . import overview as ovw
from . import panel as pnl
from . import ratios as rt
from . import targets as tgt


def _nan(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _pct(v, signed: bool = False) -> str:
    if _nan(v):
        return "—"
    return f"{v*100:+.1f}%" if signed else f"{v*100:.0f}%"


def _pct_exact(pct) -> str:
    """偏离分位真实值直显(与个股看板同式:94.96% 不四舍五入成 95%,显示与着色阈值不打架)。"""
    return f"{int(pct * 10000) / 100:g}%"


def pct_color(pct) -> str:
    """偏离分位着色(阈值与 🚦 雷达一致:超买≥95% 红 / 超卖≤5% 绿,A股配色)。"""
    if _nan(pct):
        return "var(--muted)"
    if float(pct) >= 0.95:
        return "#b91c1c"
    if float(pct) <= 0.05:
        return "#15803d"
    return "var(--muted)"


_J_COLOR = {"向上": "#16a34a", "背离": "#d97706", "震荡": "#64748b", "向下": "#dc2626"}


def _fmt_px(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.1f}"


# ---------------------------------------------------------------- 🚦 雷达
def _radar_section(store, config=None) -> str:
    """🚦 商品异动雷达(整体迁自个股看板,国内序列口径不变——与 validate_commodity_speed
    实证/夜盘快照同源)。双段语义:⚠快腿=正在发生(研究排队) / 慢腿=60日偏离度历史极值——超买=追高风险、超卖=飞刀与错杀观察(双向分节,2026-09)。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return ""
    cfg = config or get_config()
    lp = (cfg.params.get("stock") or {}).get("leading") or {}
    th = float(lp.get("commodity_extreme_pct", 0.95))
    mo_th = float(lp.get("commodity_momentum_pct", 0.10))
    stats_rows = []
    for v in fetcher.COMMODITY_CODES:
        st = sf.commodity_dev_stats(store.get_commodity_series(v))
        if st:
            stats_rows.append((v, st))
    fast, slow, n_ok = sf.commodity_radar(stats_rows, th, mo_th)

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

    count = (f"<span class='count'>动量{n_mo} · 突破{n_brk} | 超买{n_ob} · 超卖{n_os}</span>")
    slow_ob = [x for x in slow if any("超买" in t for t in x[2])]
    slow_os = [x for x in slow if any("超卖" in t for t in x[2])]

    def _leg(title: str, chips_html: str, empty_note: str) -> str:
        """三条腿常驻分节:空腿显「无」占位(2026-09 用户反馈——静默省略会让人以为检测不存在)。"""
        return (f"<p class='dev-sec'>{title}</p>"
                + (chips_html or f"<p class='muted' style='margin:2px 0 8px'>{empty_note}</p>"))

    state = "".join([
        _leg("⚠ 异动提醒 · 快腿 → 排进研究队列(非买入信号)",
             "".join(_fast_chip(*x) for x in fast),
             f"无(20日动量≥±{mo_th:.0%} 与 60日新高/新低 均未触发 · {n_ok} 品种常态)"),
        _leg("⛔ 极端警戒 · 慢腿超买 → 60日偏离度历史极值,追高风险",
             "".join(_slow_chip(*x) for x in slow_ob),
             f"无(当前无品种偏离分位≥{th:.0%})"),
        _leg("🟢 深跌警戒 · 慢腿超卖 → 跌幅历史级,接飞刀风险/错杀观察窗(非买入信号)",
             "".join(_slow_chip(*x) for x in slow_os),
             f"无(当前无品种偏离分位≤{1-th:.0%})"),
    ])
    concl = ""
    if hasattr(store, "get_meta"):
        try:
            concl = store.get_meta("commodity_speed_conclusion", "") or ""
        except Exception:  # noqa: BLE001 — meta 缺失静默
            concl = ""
    concl_html = (f"<p class='muted'>🔬 快腿实证(validate_commodity_speed):{concl}</p>" if concl else "")
    return (f'<div class="alerts"><h2>🚦 商品异动雷达 {count}</h2>'
            f'<p class="muted">慢腿超买:偏离分位≥{th:.0%}(追高风险) / 慢腿超卖:≤{1-th:.0%}'
            f'(深跌历史级——接飞刀有风险、错杀观察窗;偏离度实证无方向 edge) · '
            f'快腿:20日动量≥±{mo_th:.0%} + 60日新高/新低(国内序列口径,与实证同源)'
            f'（A股红=向上/绿=向下;观察非信号,不构成买卖建议）</p>{state}{concl_html}</div>')


# ---------------------------------------------------------------- 📊 总览
def _overview_section(store, config=None) -> str:
    """官方中证商品指数(两线)+ 自算等权合成对照 + 品种广度。官方缺失 → 自算顶上(标注)。"""
    cfg = config or get_config()
    windows = (cfg.params.get("commodity") or {}).get("overview", {}).get("breadth_windows") or [20, 60]
    idx_map = {nm: store.get_commodity_index_series(nm) for nm in fetcher.CCIDX_INDEXES}
    idx_map = {nm: s for nm, s in idx_map.items() if s is not None and len(s) >= 2}
    inputs = pnl.breadth_inputs(store)
    synth = ovw.synthetic_index(inputs)
    official_ok = bool(idx_map)

    snaps = []
    for nm, s in idx_map.items():
        sp = ovw.index_snapshot(s)
        if sp:
            snaps.append(f"<span class='ov-chip'><b>{html.escape(nm)}</b> {sp['last']:.0f}"
                         f"<small> 年度同比{_pct(sp['yoy'], True)} · 近60日{_pct(sp['m60'], True)} · {sp['date']}</small></span>")
    breadth_frames = {w: ovw.breadth_series(inputs, int(w)) for w in windows}
    for w, b in breadth_frames.items():
        if b is not None and len(b):
            u = b["up_frac"].iloc[-1]
            snaps.append(f"<span class='ov-chip'><b>{w}日广度</b> 上涨{u:.0%}"
                         f"<small> 自算非官方 · {b.index[-1]}</small></span>")
    chips = (" ".join(snaps) if snaps
             else "<p class='muted'>官方指数与广度数据均不足(先跑 backfill_commodity.py)</p>")

    fig_idx = cfg_fig.overview_figure(idx_map, synth, official_ok)
    fig_bd = cfg_fig.breadth_figure(*[breadth_frames.get(int(w)) for w in sorted(breadth_frames)])
    parts = [f'<div class="alerts"><h2>📊 商品环境总览 '
             f'<span class="count">官方中证商品指数 · 自算广度</span></h2>'
             f'<p class="muted">官方腿=中证商品指数(ccidx.com,日频;南华 akshare 端点已死故用中证)。'
             f'自算合成=17 品种等权累计(基数100,无权重/无展期调整,非官方指数——只作温度对照)。'
             f'广度=上涨品种占比(自算)。观察非信号。</p>'
             f'<div class="chips-row">{chips}</div>'
             f'<div id="ovw-fig-idx" style="min-height:340px"></div>'
             f'<div id="ovw-fig-bd" style="min-height:300px"></div></div>']
    # 总览两图 inline 渲染(已是全宽,不入放大模态)
    data_js = ("var OVW=[" + ",".join(
        re.sub(r"</script", r"<\\/script", sf.fig_json_readable(f), flags=re.I)
        for f in (fig_idx, fig_bd)) + "];\n"
        "document.addEventListener('DOMContentLoaded',function(){\n"
        "  if(!window.Plotly)return;\n"
        "  ['ovw-fig-idx','ovw-fig-bd'].forEach(function(id,i){\n"
        "    var gd=document.getElementById(id); if(gd&&OVW[i])Plotly.newPlot(gd,OVW[i],{responsive:true,displaylogo:false});});\n"
        "  setTimeout(function(){if(window._applyPlotly)_applyPlotly(_isDark());},60);\n"
        "});")
    return parts[0] + "\n<script>" + data_js + "</script>"


# ---------------------------------------------------------------- 🧲 面板
def _panel_section(store, config=None) -> str:
    """品种面板:有国际基准的品种国际价为主语(研究传导方向),国内价对照(大A投资指导);
    无基准品种标「国内定价」。判定=主语口径四态(judge_commodity 统一口径)。"""
    rows = pnl.panel_rows(store, config)
    if not rows:
        return ""
    td = "padding:6px;border-bottom:1px solid var(--border)"
    th = "padding:8px;border-bottom:2px solid var(--border)"
    body, summary = [], {"向上": [], "背离": [], "向下": [], "震荡": []}
    for r in rows:
        p = r["primary"]
        summary[r["judge"]].append(r["variety"])
        # 主语徽标按**实际主语**打:外盘序列在库 → 国际;有基准但缺数据 → 诚实降级「国内·基准缺」
        if r["intl"]:
            subject = f'<span class="subj intl">{html.escape(r["intl_name"])}</span>'
        elif r["has_bench"]:
            subject = '<span class="subj dom">国内·基准缺</span>'
        else:
            subject = '<span class="subj dom">国内定价</span>'
        px = _fmt_px(p["cur"]) + (f'<small class="unit">{html.escape(r["intl_unit"])}</small>'
                                  if r["has_bench"] and r["intl_unit"] else "")
        if r["has_bench"] and r["dom"]:
            d = r["dom"]
            spread = (f'<small class="muted">内外60日{_pct(r["spread60"], True)}</small>'
                      if not _nan(r["spread60"]) else "")
            dom_cell = f'{_fmt_px(d["cur"])}<small class="muted"> · 同比{_pct(d["yoy"], True)}</small>{spread}'
        else:
            dom_cell = '<span class="muted">（主语）</span>'
        overnight = "—" if _nan(r["overnight"]) else f"{r['overnight']:+.1%}"
        # 60日偏离度(价格/MA60−1)+历史极值排名:偏离>0 取「第几高」、<0 取「第几低」(方向对应侧,
        # 2026-09 用户口径);≤10 名红/绿高亮,与放大视图标注同源
        dev = p.get("dev")
        rk_h, rk_l = p.get("rank_high"), p.get("rank_low")
        dev_cell = "—" if _nan(dev) else f"{dev:+.1%}"
        if _nan(dev) or dev == 0 or _nan(rk_h) or _nan(rk_l):
            rank_cell = "<span class='muted'>—</span>"
        elif dev > 0:
            rank_cell = (f"<span style='color:#b91c1c;font-weight:600'>第{rk_h}高</span>" if rk_h <= 10
                         else f"<span class='muted'>第{rk_h}高</span>")
        else:
            rank_cell = (f"<span style='color:#15803d;font-weight:600'>第{rk_l}低</span>" if rk_l <= 10
                         else f"<span class='muted'>第{rk_l}低</span>")
        def _dv(v):        # 排序用 data-v(缺失→空串,JS 端沉底)
            return "" if _nan(v) else f"{v:.6g}"

        body.append(
            f"<tr><td style='{td}'><b>{html.escape(r['variety'])}</b>{p['mark']}</td>"
            f"<td style='{td};text-align:center'>{subject}</td>"
            f"<td style='{td};text-align:center'>{px}</td>"
            f"<td style='{td};text-align:center'>{_pct(p['yoy'], True)}</td>"
            f"<td style='{td};text-align:center' data-v='{_dv(p['m10'])}'>{_pct(p['m10'], True)}</td>"
            f"<td style='{td};text-align:center' data-v='{_dv(p['m20'])}'>{_pct(p['m20'], True)}</td>"
            f"<td style='{td};text-align:center' data-v='{_dv(p['m60'])}'>{_pct(p['m60'], True)}</td>"
            f"<td style='{td};text-align:center' data-v='{_dv(dev)}'>{dev_cell}</td>"
            f"<td style='{td};text-align:center;color:{pct_color(p['pct'])};font-weight:600'>"
            f"{('—' if _nan(p['pct']) else _pct_exact(p['pct']))}</td>"
            f"<td style='{td};text-align:center'>{rank_cell}</td>"
            f"<td style='{td};text-align:center'>{overnight}</td>"
            f"<td style='{td};text-align:center;color:{_J_COLOR[r['judge']]};font-weight:600'>{r['judge']}</td>"
            f"<td style='{td};text-align:center'>{dom_cell}</td></tr>")
    head = (f"<tr><th style='{th};text-align:left'>品种</th><th style='{th}'>主语</th>"
            f"<th style='{th}'>现价</th><th style='{th}'>同比</th>"
            f"<th style='{th}' id='pth-4' class='sortable' title='点击排序(降序→升序→还原)' onclick=\"sortPanel(4)\">近10日</th>"
            f"<th style='{th}' id='pth-5' class='sortable' title='点击排序(降序→升序→还原)' onclick=\"sortPanel(5)\">近20日</th>"
            f"<th style='{th}' id='pth-6' class='sortable' title='点击排序(降序→升序→还原)' onclick=\"sortPanel(6)\">近60日</th>"
            f"<th style='{th}' id='pth-7' class='sortable' title='点击排序(降序→升序→还原)' onclick=\"sortPanel(7)\">60日偏离度</th>"
            f"<th style='{th}'>60日偏离分位</th><th style='{th}'>极值排名</th><th style='{th}'>隔夜</th>"
            f"<th style='{th}'>判定</th><th style='{th}'>国内对照</th></tr>")
    n_intl = sum(1 for r in rows if r["has_bench"])
    guide = (f"向上:{','.join(summary['向上']) or '—'} | 背离:{','.join(summary['背离']) or '—'} | "
             f"向下:{','.join(summary['向下']) or '—'}")
    spot_note = (f" · 隔夜快照 {rows[0]['spot_date']}" if rows and rows[0].get("spot_date") else "")
    return ('<div class="alerts"><h2>🧲 品种面板 '
            f'<span class="count">国际主语 {n_intl}/{len(rows)} · 国内价=A股投资指导</span></h2>'
            f'<p class="muted">有国际通用基准的品种以国际价为主语(国际价格波动一般传导至国内),'
            f'国内价作对照;判定/同比/动量/偏离分位均按主语口径(同比=近一年/252交易日;60日偏离度=价格/MA60−1,其分位红≥95%/绿≤5%与🚦雷达慢腿同源同阈;极值排名:偏离>0取自身历史第几高、<0取第几低,≤10名红/绿高亮)。判定四态=同比×近60日:向上=同比>+10%且近60日>−5%(年度上行且未回落,埋伏方向);背离=同比>+10%但近60日≤−5%(高位回落·前瞻恶化,即M1口径,避);震荡=同比±10%内(中性);向下=同比≤−10%(周期确认向下,即M2口径,避)。隔夜=国内夜盘快照 vs 国内日收盘'
            f'(A股开盘前最新脉搏)。近10/20/60日·偏离度表头可点击排序(降序→升序→还原)。{guide}{spot_note}</p>'
            f'<div style="overflow-x:auto"><table id="panel-table" style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead>{head}</thead><tbody id="panel-body">{"".join(body)}</tbody></table></div></div>'
            + "\n<script>" + _PANEL_SORT_JS + "</script>")


# 面板排序:近10/20/60日·60日偏离度 四列三态(降序→升序→还原板块原序);
# 数值藏在 td 的 data-v(缺失=空串 → 排序沉底)。纯客户端重排,不重算。
_PANEL_SORT_JS = """
var panelSortCol = null, panelSortDir = 0;        // dir: 0=原序 1=降序 2=升序
var panelOrigRows = null;
function sortPanel(col){
  var tb = document.getElementById('panel-body');
  if(!tb) return;
  if(panelSortCol !== col){ panelSortCol = col; panelSortDir = 1; }
  else { panelSortDir = (panelSortDir + 1) % 3; if(panelSortDir === 0) panelSortCol = null; }
  if(!panelOrigRows) panelOrigRows = Array.prototype.slice.call(tb.rows);
  var rows;
  if(panelSortCol === null){
    rows = panelOrigRows.slice();
  } else {
    rows = Array.prototype.slice.call(tb.rows);
    var c = panelSortCol;
    rows.sort(function(a, b){
      var va = parseFloat(a.cells[c].getAttribute('data-v'));
      var vb = parseFloat(b.cells[c].getAttribute('data-v'));
      if(isNaN(va)) va = -Infinity;
      if(isNaN(vb)) vb = -Infinity;
      return (va - vb) * (panelSortDir === 2 ? 1 : -1);
    });
  }
  rows.forEach(function(r){ tb.appendChild(r); });
  document.querySelectorAll('#panel-table th.sortable').forEach(function(th){
    th.textContent = th.textContent.replace(/\\s*[▲▼]$/, '');
  });
  if(panelSortCol !== null){
    var th = document.getElementById('pth-' + panelSortCol);
    if(th) th.textContent = th.textContent + (panelSortDir === 2 ? ' ▲' : ' ▼');
  }
}
"""


# ---------------------------------------------------------------- 📈 时序图 + ⚖ 比价
def _charts_and_ratios(store, config=None) -> tuple[str, str]:
    """📈 品种时序图(主语序列;与面板口径一致) + ⚖ 比价矩阵(表+图)。共用一套 COMM payload
    与放大模态(traces[0]=线 traces[1]=MA60 同构,模态的偏离度派生两处通用)。"""
    if store is None or not hasattr(store, "get_commodity_series"):
        return "", ""
    figs, names, devs = [], [], []
    for v in fetcher.COMMODITY_CODES:
        s, disp, _unit = pnl.primary_series(store, v)
        if s is None or len(s) < 2:
            continue
        figs.append(sf.commodity_price_figure(disp, s))
        names.append(disp)
        devs.append(sf.commodity_dev_stats(s))
    n_var = len(figs)

    ratio_rows = rt.ratio_rows(store, config=config)
    ratio_html_rows = []
    for i, r in enumerate(ratio_rows):
        figs.append(cfg_fig.ratio_figure(r["name"], r["series"], r["pct"]))
        names.append(r["name"])
        devs.append({})
        tdr = "padding:6px;border-bottom:1px solid var(--border)"
        ratio_html_rows.append(
            f"<tr><td style='{tdr}'><b>{html.escape(r['name'])}</b></td>"
            f"<td style='{tdr};text-align:center;font-weight:600'>{r['cur']:.2f}</td>"
            f"<td style='{tdr};text-align:center;color:{pct_color(r['pct'])};font-weight:600'>{_pct_exact(r['pct'])}</td>"
            f"<td style='{tdr};text-align:center'>{_pct(r['yoy'], True)}</td>"
            f"<td style='{tdr};text-align:center'>{_pct(r['m60'], True)}</td>"
            f"<td style='{tdr};text-align:center'><span class='muted'>{r['n']}期 {r['first']}→{r['last']}</span></td>"
            f"<td style='{tdr}'><span class='muted'>{html.escape(r['note'])}</span></td></tr>")

    if not figs:
        return "", ""

    var_divs = "".join(
        f'<div id="comm-chart-{i}" class="comm-chart" title="点击放大查看" onclick="openCommChart({i})"></div>'
        for i in range(n_var))
    charts_html = ('<div class="alerts"><h2>📈 品种时序(主语序列)</h2>'
                   '<p class="muted">有国际基准的品种画国际价(与面板主语一致),其余画国内价;'
                   '虚线=1年前水平(同比可视化),MA60=趋势参照。点击放大:下行为偏离度(价格/MA60−1,'
                   '历史极值线+第几高/第几低标注)+底部滑块+快捷窗。</p>'
                   f'<div class="comm-grid">{var_divs}</div></div>')

    ratio_tbl = ""
    if ratio_html_rows:
        thr = "padding:8px;border-bottom:2px solid var(--border)"
        head = (f"<tr><th style='{thr};text-align:left'>比价</th><th style='{thr}'>当前值</th>"
                f"<th style='{thr}'>全史分位</th><th style='{thr}'>同比</th><th style='{thr}'>近60日</th>"
                f"<th style='{thr}'>覆盖</th><th style='{thr};text-align:left'>含义</th></tr>")
        ratio_divs = "".join(
            f'<div id="comm-chart-{n_var + i}" class="comm-chart" title="点击放大查看" '
            f'onclick="openCommChart({n_var + i})"></div>' for i in range(len(ratio_html_rows)))
        ratio_tbl = ('<div class="alerts"><h2>⚖ 比价与内外盘对照 <span class="count">黄金只做分母</span></h2>'
                     '<p class="muted">比价=分子/分母(inner-join 对齐);金银比的主语是白银、油金比是原油'
                     '(黄金叙事归宏观框架看板,此处只做分母)。分位=当前值在自身全史的位置'
                     '(红≥95% 绿≤5%)。观察非信号。</p>'
                     f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
                     f'<thead>{head}</thead><tbody>{"".join(ratio_html_rows)}</tbody></table></div>'
                     f'<div class="comm-grid">{ratio_divs}</div></div>')

    import json
    arr = ",".join(re.sub(r"</script", r"<\\/script", sf.fig_json_readable(f), flags=re.I) for f in figs)
    names_js = ",".join(json.dumps(n, ensure_ascii=False) for n in names)
    devs_js = ",".join(json.dumps(d, ensure_ascii=False) for d in devs)
    js = ("var COMM=[" + arr + "];\n"
          "var COMM_NAMES=[" + names_js + "];\n"
          "var COMM_DEV=[" + devs_js + "];\n"
          "document.addEventListener('DOMContentLoaded',function(){\n"
          "  if(!window.Plotly)return;\n"
          "  COMM.forEach(function(fig,i){var gd=document.getElementById('comm-chart-'+i);"
          "    if(gd)Plotly.newPlot(gd,fig,{responsive:true,displaylogo:false});});\n"
          "  setTimeout(function(){if(window._applyPlotly)_applyPlotly(_isDark());},60);\n"
          "});\n" + _COMM_MODAL_JS)
    return charts_html, ratio_tbl + _COMM_MODAL_HTML + "\n<script>" + js + "</script>"


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


# 放大模态交互(迁自个股看板 2026-09,依赖运行时 Plotly/COMM/COMM_NAMES/COMM_DEV/_applyPlotly/_isDark)。
# 双行:上=价格+MA60,下=偏离度(前端从两条 trace 逐点相除派生);底行 x2 主控挂滑块/快捷窗。
_COMM_MODAL_JS = """
function openCommChart(i){
  var src = COMM[i];
  if(!src || !window.Plotly) return;
  var f = JSON.parse(JSON.stringify(src));
  var L = f.layout || (f.layout = {});
  L.height = Math.max(560, Math.round(window.innerHeight * 0.80));
  L.title = L.title || {}; L.title.font = {size: 16};
  L.margin = L.margin || {}; L.margin.t = 70; L.margin.b = 60;
  var hasRows = f.data && f.data.length >= 2;
  if(hasRows){
    var D = COMM_DEV[i] || {};
    if(D.new_high60) L.title.text = (L.title.text || '') + ' · 🔺60日新高';
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
    L.yaxis.domain = [0.44, 1];
    L.yaxis.title = {text: '价格'};
    L.yaxis2 = {domain: [0, 0.30], title: {text: '偏离度'}, tickformat: '.0%',
                gridcolor: L.yaxis.gridcolor, zerolinecolor: L.yaxis.zerolinecolor};
    var xa = L.xaxis || (L.xaxis = {});
    xa.type = 'date';
    xa.showticklabels = false;
    xa.matches = 'x2';
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
    if(D.max != null && D.min != null){
      L.shapes = (L.shapes || []).concat([
        {type:'line', xref:'x2', yref:'y2', x0:dx[0], x1:dx[dx.length-1], y0:D.max, y1:D.max,
         line:{color:'#d03b3b', width:1.2, dash:'dot'}},
        {type:'line', xref:'x2', yref:'y2', x0:dx[0], x1:dx[dx.length-1], y0:D.min, y1:D.min,
         line:{color:'#1c5cab', width:1.2, dash:'dot'}}]);
    }
    var lk = -1;
    for(k = dy.length - 1; k >= 0; k--){ if(dy[k] != null){ lk = k; break; } }
    var lastDate = (lk >= 0) ? dx[lk] : null;
    var pushExt = function(e, hi){
      if(!e || !e.d || lastDate === null) return;
      if(lastDate.indexOf(e.d) === 0) return;
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
  } else {
    var xa0 = L.xaxis || (L.xaxis = {});
    xa0.type = 'date';
    xa0.rangeslider = {visible: true};
  }
  var ov = document.getElementById('comm-modal');
  ov.hidden = false;
  document.body.style.overflow = 'hidden';
  document.getElementById('comm-modal-title').textContent = (COMM_NAMES[i] || '') + ' · 放大';
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


# ---------------------------------------------------------------- 🎫 投资标的映射(二期)
def _gap_color(g, win: int = 60) -> str:
    """错配度着色:阈值随窗宽 √ 缩放(波动率尺度)——60日/同比=±10pp、20日≈±5.8、10日≈±4.1。
    ≥+阈值=ETF领先(蓝) / ≤−阈值=ETF落后·错杀观察(橙) / 常态灰。"""
    if _nan(g):
        return "var(--muted)"
    t = 0.10 * (win / 60.0) ** 0.5
    if float(g) >= t:
        return "#1e3a8a"
    if float(g) <= -t:
        return "#b45309"
    return "var(--text)"


def _targets_section(store, config=None) -> str:
    """🎫 投资标的映射(二期):品种→大A可投标的 + 错配度(ETF NAV 涨幅−品种涨幅,同窗口)。
    正=ETF领先(情绪/展期溢价)、负=ETF落后(错杀观察);期货ETF NAV 含展期、QDII 含汇率。
    偏离度/筹码全家桶在行业研究看板不复刻;T+0 品种永不进引擎宇宙。"""
    rows, missing = tgt.target_rows(store, config)
    if not rows and not missing:
        return ""
    td = "padding:6px;border-bottom:1px solid var(--border)"
    th = "padding:8px;border-bottom:2px solid var(--border)"
    body = []
    for r in rows:
        pool_link = (' <a class="comm-link" href="research_report.html" '
                     'title="偏离度/筹码/资金流在行业研究看板">📈</a>' if r["in_pool"] else "")
        note = (f'<br><span class="muted" style="font-size:10px">{html.escape(r["note"])}</span>'
                if r.get("note") else "")
        name_cell = (f'<b>{html.escape(r["name"])}</b><span class="muted"> {r["symbol"]}</span>'
                     f'<br><span class="muted" style="font-size:10px">{html.escape(r["kind"])}'
                     f'{" · 对照国际" if r["ref_kind"] == "intl" else ""}</span>{pool_link}{note}')
        g10, g20, g60, gy = r["gap10"], r["gap20"], r["gap60"], r["gap_yoy"]

        def _pp(v):
            return "—" if _nan(v) else f"{v*100:+.1f}pp"

        def _dv(v):        # 排序用 data-v(缺失→空串,JS 端沉底;与面板同法)
            return "" if _nan(v) else f"{v:.6g}"

        def _num(v, color=""):
            c = f";color:{color}" if color else ""
            return (f"<td style='{td};text-align:center{c}' data-v='{_dv(v)}'>"
                    f"{'—' if _nan(v) else _pct(v, True)}</td>")

        body.append(
            f"<tr><td style='{td}'>{name_cell}</td>"
            f"<td style='{td};text-align:center'><b>{html.escape(r['variety'])}</b></td>"
            f"{_num(r['etf']['m10'])}{_num(r['comm']['m10'])}"
            f"<td style='{td};text-align:center;color:{_gap_color(g10, 10)};font-weight:600' data-v='{_dv(g10)}'>{_pp(g10)}</td>"
            f"{_num(r['etf']['m20'])}{_num(r['comm']['m20'])}"
            f"<td style='{td};text-align:center;color:{_gap_color(g20, 20)};font-weight:600' data-v='{_dv(g20)}'>{_pp(g20)}</td>"
            f"{_num(r['etf']['m60'])}{_num(r['comm']['m60'])}"
            f"<td style='{td};text-align:center;color:{_gap_color(g60)};font-weight:600' data-v='{_dv(g60)}'>{_pp(g60)}</td>"
            f"{_num(r['etf']['yoy'])}{_num(r['comm']['yoy'])}"
            f"<td style='{td};text-align:center;color:{_gap_color(gy)};font-weight:600' data-v='{_dv(gy)}'>{_pp(gy)}</td></tr>")
    # 12 数值列(2..13)可点击三态排序(与面板同法:降序→升序→还原配置原序)
    tcols = ["ETF近10日", "品种近10日", "错配10日", "ETF近20日", "品种近20日", "错配20日",
             "ETF近60日", "品种近60日", "错配60日", "ETF同比", "品种同比", "错配同比"]
    head = (f"<tr><th style='{th};text-align:left'>标的</th><th style='{th}'>映射品种</th>"
            + "".join(
                f"<th style='{th}' id='tth-{i}' class='sortable' title='点击排序(降序→升序→还原)' "
                f"onclick=\"sortTargets({i})\">{lbl}</th>"
                for i, lbl in enumerate(tcols, start=2))
            + "</tr>")
    n_syms = len({r["symbol"] for r in rows})
    miss_html = ""
    if missing:
        miss_html = ('<p class="muted">⚠ NAV 未回填:' +
                     "、".join(f"{m['name']}({m['symbol']})" for m in missing) +
                     " — python scripts/backfill_commodity.py --targets</p>")
    return (f'<div class="alerts"><h2>🎫 投资标的映射(大A) '
            f'<span class="count">{n_syms} 标的 · {len(rows)} 映射 · 错配度=ETF−品种</span></h2>'
            f'<p class="muted">错配=ETF(NAV)涨幅−标的商品涨幅(近10/20/60日+同比四组同窗口;蓝≥+阈值=ETF'
            f'领先/溢价、橙≤−阈值=ETF落后·错杀观察,阈值随窗宽√缩放:10/20/60日≈±4.1/±5.8/±10pp·同比=±10pp)。'
            f'期货ETF 的 NAV 含展期结构、QDII 含汇率=「含摩擦的跟踪差」;'
            f'股票ETF 是股票组合代理(含个股 alpha)。观察非信号,不构成买卖建议;'
            f'T+0 品种与引擎 T+1 假设不合永不进宇宙。十二数值列表头可点击排序(降序→升序→还原)。</p>'
            f'<div style="overflow-x:auto"><table id="targets-table" style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead>{head}</thead><tbody id="targets-body">{"".join(body)}</tbody></table></div>{miss_html}</div>'
            + "\n<script>" + _TARGETS_SORT_JS + "</script>")


# 标的表排序:12 数值列(2..13)三态(降序→升序→还原配置原序);数值藏 td 的 data-v(缺失沉底)。
# 与 _PANEL_SORT_JS 同法不同表;纯客户端重排,不重算。
_TARGETS_SORT_JS = """
var targetsSortCol = null, targetsSortDir = 0;    // dir: 0=原序 1=降序 2=升序
var targetsOrigRows = null;
function sortTargets(col){
  var tb = document.getElementById('targets-body');
  if(!tb) return;
  if(targetsSortCol !== col){ targetsSortCol = col; targetsSortDir = 1; }
  else { targetsSortDir = (targetsSortDir + 1) % 3; if(targetsSortDir === 0) targetsSortCol = null; }
  if(!targetsOrigRows) targetsOrigRows = Array.prototype.slice.call(tb.rows);
  var rows;
  if(targetsSortCol === null){
    rows = targetsOrigRows.slice();
  } else {
    rows = Array.prototype.slice.call(tb.rows);
    var c = targetsSortCol;
    rows.sort(function(a, b){
      var va = parseFloat(a.cells[c].getAttribute('data-v'));
      var vb = parseFloat(b.cells[c].getAttribute('data-v'));
      if(isNaN(va)) va = -Infinity;
      if(isNaN(vb)) vb = -Infinity;
      return (va - vb) * (targetsSortDir === 2 ? 1 : -1);
    });
  }
  rows.forEach(function(r){ tb.appendChild(r); });
  document.querySelectorAll('#targets-table th.sortable').forEach(function(th){
    th.textContent = th.textContent.replace(/\\s*[▲▼]$/, '');
  });
  if(targetsSortCol !== null){
    var th = document.getElementById('tth-' + targetsSortCol);
    if(th) th.textContent = th.textContent + (targetsSortDir === 2 ? ' ▲' : ' ▼');
  }
}
"""


# ---------------------------------------------------------------- 🔬 基差·期限结构·库存(二期剩余)
def _fundamentals_section(store, config=None) -> str:
    """🔬 基差/期限结构/库存观察(二期剩余,2026-09 过 event-study 礼遇后入板):
    主力基差率+分位 | 近月-主力斜率年化+分位 | CZCE 四品种库存分位+4周变化。
    实证结论活注入(meta:commodity_basis_conclusion / commodity_inventory_conclusion);
    温度计非开关。基差/库存史不足 250/100 观测的品种诚实缺省(—)。"""
    if store is None or not hasattr(store, "get_commodity_basis"):
        return ""
    td = "padding:6px;border-bottom:1px solid var(--border)"
    th = "padding:8px;border-bottom:2px solid var(--border)"

    def _cell_pct(v, pct, fmt="{:+.1%}", suffix=""):
        if _nan(v):
            return "<td style='{0};text-align:center'>—</td>".format(td)
        c = pct_color(pct)
        p = "—" if _nan(pct) else _pct_exact(pct)
        return (f"<td style='{td};text-align:center'>{fmt.format(v)}{suffix}"
                f"<br><span style='color:{c};font-size:11px'>分位{p}</span></td>")

    czce_set = set(fetcher.CZCE_INVENTORY_SYMBOLS)
    body, n_basis, n_inv = [], 0, 0
    for variety, code in fetcher.COMMODITY_CODES.items():
        bdf = None
        try:
            bdf = store.get_commodity_basis(code)
        except Exception:  # noqa: BLE001 — 表缺失静默
            bdf = None
        basis_rate = basis_pct = term = term_pct = None
        if bdf is not None and len(bdf):
            br = pd.to_numeric(bdf["dom_basis_rate"], errors="coerce").dropna()
            if len(br) >= 250:
                basis_rate = float(br.iloc[-1])
                basis_pct = fund.current_pct(br, 250)      # O(n) 快路径(全序列版留给验证器)
                n_basis += 1
            ts = fund.term_slope_annualized(bdf).dropna()
            if len(ts) >= 250:
                term = float(ts.iloc[-1])
                term_pct = fund.current_pct(ts, 250)
        inv_pct = inv_w4 = None
        if code in czce_set:
            try:
                inv = store.get_commodity_inventory(code)
            except Exception:  # noqa: BLE001
                inv = None
            if inv is not None and len(inv):
                iv = inv.astype(float).dropna()
                if len(iv) >= 100:
                    inv_pct = fund.current_pct(iv, 100)
                    w4 = iv.pct_change(4).iloc[-1]
                    inv_w4 = None if _nan(w4) else float(w4)
                    n_inv += 1
        if _nan(basis_rate) and _nan(term) and _nan(inv_pct):
            continue
        inv_cell = (f"<td style='{td};text-align:center'>—</td>") if _nan(inv_pct) else (
            f"<td style='{td};text-align:center'>分位{_pct_exact(inv_pct)}"
            f"<br><span style='font-size:11px;color:{pct_color(inv_pct)}'>4周{_pct(inv_w4, True)}</span></td>")
        body.append(
            f"<tr><td style='{td}'><b>{html.escape(variety)}</b></td>"
            + _cell_pct(basis_rate, basis_pct)
            + _cell_pct(term, term_pct, fmt="{:+.0%}", suffix="/年")
            + inv_cell + "</tr>")
    if not body:
        return ""
    head = (f"<tr><th style='{th};text-align:left'>品种</th><th style='{th}'>主力基差率</th>"
            f"<th style='{th}'>期限斜率(年化)</th><th style='{th}'>库存分位(CZCE)</th></tr>")

    def _concl(key, prefix):
        if not hasattr(store, "get_meta"):
            return ""
        try:
            c = store.get_meta(key, "") or ""
        except Exception:  # noqa: BLE001
            return ""
        return f'<p class="muted">🔬 {prefix}:{c}</p>' if c else ""

    concl_html = (_concl("commodity_basis_conclusion", "基差/期限实证(validate_commodity_basis)")
                  + _concl("commodity_inventory_conclusion", "库存实证(validate_commodity_inventory)"))
    return (f'<div class="alerts"><h2>🔬 基差·期限结构·库存 '
            f'<span class="count">基差 {n_basis} 品种 · 库存 {n_inv} 品种(CZCE)</span></h2>'
            f'<p class="muted">基差率=(主力期货−现货)/现货,正=升水;期限斜率=主力/近月−1 年化,'
            f'正=contango/负=现货紧(backwardation);库存=郑商所交割仓单周采样(≠社会总库存)。'
            f'分位=expanding 历史位(红≥95%/绿≤5%,与面板/雷达同阈)。温度计非开关,不构成买卖建议。</p>'
            f'<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">'
            f'<thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>{concl_html}</div>')


_LEGEND_HTML = """
<details class="legend"><summary>📖 读图说明(口径与边界)</summary>
<div class="legend-body">
<div><b>主语口径</b> · 有国际通用基准的品种(LME/COMEX/CBOT)以国际价为主语——国际价格波动一般传导至国内;
国内价=大A投资指导口径(周期股传导链 commodity_map→alignment 吃的也是国内价)。面板判定与个股看板卡片的
判定可能偶发分歧(主语 vs 国内口径),这是两个用途不是 bug。</div>
<div><b>黄金只做分母</b> · 金银比/油金比的主语是白银/原油;黄金自身叙事(定位器/微观紧缺)归宏观框架看板,此处零复制。</div>
<div><b>🚦 雷达口径</b> · 国内序列(与 validate_commodity_speed 实证、夜盘快照同源)。⚠快腿=20日动量/新高新低
(研究排队,实证:追买跑输);⛔慢腿超买=偏离分位≥95%·追高风险 / 🟢慢腿超卖=≤5%·飞刀与错杀观察(60日偏离度历史极值,双向)。<b>温度计非开关,不构成买卖建议</b>。</div>
<div><b>📊 总览</b> · 官方腿=中证商品指数(ccidx.com;南华 akshare 端点已死);自算合成/广度=17 品种等权,
无权重无展期调整,<b>非官方指数</b>,只作温度对照。指数点位无绝对意义(只看变化率);全看板「同比」
均=近 252 交易日(约一年)口径。</div>
<div><b>⚖ 比价</b> · 螺矿比≈钢厂利润代理(近似);分位=全史位置。观察非信号。</div>
<div><b>🎫 投资标的</b> · 错配=ETF(NAV)涨幅−品种涨幅(近10/20/60日+同比四组同窗口,着色阈值随窗宽
√缩放:10/20/60日≈±4.1/±5.8/±10pp·同比=±10pp):正=ETF领先(情绪/展期溢价)、负=ETF落后
(商品涨了ETF没涨=错杀观察,或股票端独立逻辑)。期货ETF NAV 含展期、QDII 含汇率、股票ETF 含个股
alpha——都是「含摩擦的跟踪差」。T+0 品种永不进引擎宇宙;偏离度/筹码在行业研究看板不复刻。</div>
<div><b>数据腿</b> · 国内 17 品种=sina 连续合约(backfill_stock_data --comm);国际基准=sina 外盘
(backfill_commodity.py);指数=ccidx。隔夜=夜盘快照 vs 国内日收盘(超 2 天显示 —)。</div>
<div><b>🔬 基差/期限/库存</b> · 基差+期限结构=100ppi 生意社(2019 起,过了 event-study 礼遇后入板,
结论活注入);库存=郑商所交割仓单周采样(2021 起,<b>仅玻璃/纯碱/尿素/LPG 四品种</b>——SHFE/DCE 端点死、
GFEX 解析坏、99qh 死、东财仅 72 天,金属/碳酸锂库存无多史免费源待补)。仓单≠社会总库存。</div>
</div></details>"""


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
.legend { margin:0 0 14px; }
.legend > summary { cursor:pointer; display:inline-block; color:var(--muted); font-size:12px; list-style:none; }
.legend > summary::-webkit-details-marker { display:none; }
.legend > summary::before { content:"▸ "; }
.legend[open] > summary::before { content:"▾ "; }
.legend > summary:hover { color:var(--text); }
.legend-body { margin-top:6px; padding:8px 10px; background:var(--bg); border:1px solid var(--border); border-radius:6px; font-size:11px; line-height:1.75; color:var(--muted); }
.legend-body b { color:var(--text); font-weight:600; }
.comm-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(440px,1fr)); gap:10px; margin-top:8px; }
.comm-chart { min-height:300px; position:relative; cursor:pointer; border-radius:6px; }
.comm-chart:hover { box-shadow:0 0 0 2px var(--border); }
.comm-chart::after { content:'⛶ 放大'; position:absolute; top:4px; right:8px; z-index:3;
  font-size:11px; color:var(--muted); background:var(--card); border:1px solid var(--border);
  border-radius:4px; padding:1px 6px; opacity:0; transition:opacity .12s; pointer-events:none; }
.comm-chart:hover::after { opacity:1; }
/* dev-chip:雷达标签(A股配色 红=超买/向上 绿=超卖/向下) */
.dev-chip { display:inline-block; padding:4px 10px; margin:3px 8px 3px 0; border-radius:6px; font-size:13px; font-weight:600; }
.dev-chip.ob { background:#fee2e2; color:#b91c1c; }
.dev-chip.os { background:#dcfce7; color:#15803d; }
.dev-chip.mv { background:#dbeafe; color:#1e3a8a; }
.dev-chip .sym-up { color:#b91c1c; }
.dev-chip .sym-dn { color:#15803d; }
.dev-chip small { font-weight:400; font-size:11px; margin-left:6px; opacity:.85; }
.dev-sec { font-size:11.5px; font-weight:700; color:var(--muted); margin:8px 0 2px; }
/* 总览 chips */
.chips-row { margin:2px 0 8px; }
.ov-chip { display:inline-block; padding:5px 12px; margin:3px 8px 3px 0; border-radius:8px;
  background:var(--bg); border:1px solid var(--border); font-size:13px; }
.ov-chip small { color:var(--muted); font-size:11px; margin-left:4px; }
/* 面板主语徽标 */
.subj { display:inline-block; font-size:11px; padding:2px 8px; border-radius:10px; font-weight:600; color:#fff; }
.subj.intl { background:#2563eb; }
.subj.dom { background:#94a3b8; }
.unit { color:var(--muted); font-size:10px; margin-left:3px; }
/* 可排序表头(面板 近10/20/60日·偏离度 / 标的表 12 数值列;三态:降序→升序→还原) */
.sortable { cursor:pointer; user-select:none; }
.sortable:hover { color:#2563eb; }
/* 放大模态(迁自个股看板) */
.modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.55); display:flex; align-items:flex-start; justify-content:center; padding:28px 14px; z-index:1000; overflow:auto; }
.modal-overlay[hidden] { display:none; }
.modal-box { background:var(--card); border:1px solid var(--border); border-radius:10px; width:100%; max-width:2200px; box-shadow:0 16px 50px rgba(0,0,0,.45); }
.modal-head { display:flex; justify-content:space-between; align-items:center; padding:10px 16px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--card); border-radius:10px 10px 0 0; z-index:1; }
.modal-head #comm-modal-title { font-weight:700; font-size:16px; }
.modal-close { background:transparent; border:none; color:var(--muted); font-size:20px; cursor:pointer; padding:2px 10px; border-radius:6px; line-height:1; }
.modal-close:hover { background:var(--border); color:var(--text); }
.modal-body { padding:10px 14px 18px; }
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
  localStorage.setItem('commodity-dark',dark);
  _syncBtn();
  _applyPlotly(dark);
}
(function(){
  if(localStorage.getItem('commodity-dark')==='true')document.body.classList.add('dark');
  _syncBtn();
  function init(){_applyPlotly(_isDark());}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
  else init();
})();
"""


def render(store, as_of: str, config=None, title: str = "大宗商品看板") -> str:
    """渲染大宗商品看板 HTML(全 section;store 缺商品表时各 section 诚实缺省)。"""
    cfg = config or get_config()
    radar = _radar_section(store, cfg)
    overview = _overview_section(store, cfg)
    panel_html = _panel_section(store, cfg)
    charts_html, ratios_html = _charts_and_ratios(store, cfg)
    targets_html = _targets_section(store, cfg)
    fundamentals_html = _fundamentals_section(store, cfg)

    from plotly.offline import get_plotlyjs
    plotly_js = re.sub(r"</script", r"<\\/script", get_plotlyjs(), flags=re.I)

    n_intl = len(fetcher.COMMODITY_BENCHMARKS)
    return f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>{_CSS}</style></head>
<body>
<div class="header">
  <button id="theme-btn" class="toggle" onclick="toggleTheme()">🌙 深浅色</button>
  <h1>{html.escape(title)}</h1>
  <p class="muted">as_of {html.escape(as_of)} · 17 品种(国际主语 {n_intl}/国内定价 {17 - n_intl}) · 第八看板 · 只读观测 · 温度计非开关</p>
</div>
{_LEGEND_HTML}
{radar}
{overview}
{panel_html}
{charts_html}
{ratios_html}
{targets_html}
{fundamentals_html}
<script>{plotly_js}</script>
<script>{_THEME_JS}</script>
</body></html>"""


def write_html(html_str: str, output: str | Path) -> Path:
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html_str, encoding="utf-8")
    return out
