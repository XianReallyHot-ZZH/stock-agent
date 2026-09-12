"""候选个股池看板渲染(fat renderer)——高业绩池(陈氏季度池)V8 单主轴, 2026-09 重写。

Section 序: 头部状态行 / ①池总览(当前切面) / ②行业构成(涌现簇) / ③风险筛与人工复审(SOP)
/ ④披露时钟与环比 diff / ⑤历史池回放 / ⑥读图说明(口径+validator 结论注入) / 页脚。
视觉语言与 research/tracker 看板同源(CSS vars + chips + sortable + 默认浅色可切深)。
v1 无内嵌 Plotly(表为主),主题切换无需 purge。
"""
from __future__ import annotations

import html as _html

_CSS = """
:root{--bg:#f9f9f7;--card:#ffffff;--text:#1c1c1a;--text2:#52514e;--muted:#898781;
      --faint:#b5b3ac;--border:#e1e0d9;--border2:#c9c8bf;--thbg:#f3f2ee;--hover:#f1f0ea;
      --chipbg:rgba(127,127,127,.08);--head:#0b0b0b;
      --ok:#0ca30c;--warn:#b45309;--crit:#d03b3b;--link:#2563eb;--ovbg:rgba(37,99,235,.06)}
body.dark{--bg:#16181d;--card:#1e222b;--text:#e6e8ee;--text2:#aab0bd;--muted:#8a91a0;
      --faint:#5d6472;--border:#2b313d;--border2:#3a4150;--thbg:#232833;--hover:#262c38;
      --chipbg:rgba(200,200,220,.10);--head:#f2f4f8;
      --ok:#4ade80;--warn:#fbbf24;--crit:#f87171;--link:#60a5fa;--ovbg:rgba(96,165,250,.10)}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 18px 60px}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:4px}
h1{font-size:22px;margin:0}
.meta{color:var(--text2);font-size:13px}
#theme-btn{margin-left:auto;background:var(--card);color:var(--text);border:1px solid var(--border);
      border-radius:8px;padding:6px 12px;cursor:pointer;font-size:14px}
.statline{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 2px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 12px;
      font-size:12.5px;color:var(--text2)}
.stat b{color:var(--text);font-size:14px}
nav.anchor{display:flex;flex-wrap:wrap;gap:6px;margin:14px 0 4px;position:sticky;top:0;
      background:var(--bg);padding:8px 0;z-index:10;border-bottom:1px solid var(--border)}
nav.anchor a{font-size:12.5px;color:var(--link);text-decoration:none;background:var(--card);
      border:1px solid var(--border);border-radius:14px;padding:3px 10px}
nav.anchor a:hover{background:var(--hover)}
h2{font-size:17px;margin:30px 0 6px;color:var(--head)}
.hint{color:var(--muted);font-size:12px;line-height:1.7}
.summary-box{background:var(--ovbg);border-left:4px solid var(--link);padding:12px 16px;
      border-radius:6px;font-size:13.5px;line-height:1.8;color:var(--text2);margin:12px 0}
.summary-box b{color:var(--text)}
.banner{background:var(--card);border:1px solid var(--border);border-radius:8px;
      padding:8px 14px;margin:10px 0;font-size:13px;color:var(--text2)}
.banner.hot{border-left:4px solid var(--crit)}
.banner.cold{border-left:4px solid var(--warn)}
table{border-collapse:collapse;width:100%;margin:10px 0 4px;font-size:13px;background:var(--card)}
th{background:var(--thbg);padding:8px 10px;text-align:right;border-bottom:2px solid var(--border2)}
td{padding:7px 10px;border-bottom:1px solid var(--border);text-align:right;
   font-variant-numeric:tabular-nums}
th:first-child,td:first-child{text-align:left}
tr:hover{background:var(--hover)}
table.sortable th[data-key]{cursor:pointer;user-select:none}
table.sortable th[data-key]::after{content:" ⇅";font-size:9px;color:var(--faint)}
table.sortable th[data-key].asc::after{content:" ▲"}
table.sortable th[data-key].desc::after{content:" ▼"}
.th-key{background:var(--ovbg);border-bottom:2px solid var(--link)}
.count{color:var(--muted);font-weight:normal;font-size:12px}
.chip{font-size:11px;padding:1px 8px;border-radius:9px;background:var(--chipbg);
      color:var(--text2);border:1px solid var(--border);white-space:nowrap}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.chip.ok{color:var(--ok);border-color:var(--ok)}
.pos{color:var(--crit)} body.dark .pos{color:#f87171}   /* A股红=涨/正 */
.neg{color:var(--link)} body.dark .neg{color:#60a5fa}
.dim{color:var(--muted)}
.badge-new{font-size:10px;background:#16a34a;color:#fff;border-radius:4px;padding:0 4px;margin-left:3px}
details.sop{background:var(--card);border:1px solid var(--border);border-radius:8px;
      padding:8px 14px;margin:8px 0;font-size:13px;color:var(--text2)}
details.sop summary{cursor:pointer;color:var(--text);font-weight:600}
details.sop div{margin-top:6px;line-height:1.9}
footer{margin-top:40px;padding-top:12px;border-top:1px solid var(--border);color:var(--faint);
      font-size:11.5px;line-height:1.8}
"""

_JS = """
function _isDark(){return document.body.classList.contains('dark')}
function _syncThemeBtn(){var b=document.getElementById('theme-btn');if(b)b.textContent=_isDark()?'☀️':'🌙'}
function toggleTheme(){document.body.classList.toggle('dark');
  try{localStorage.setItem('pool-dark',String(_isDark()))}catch(e){};_syncThemeBtn()}
(function(){try{if(localStorage.getItem('pool-dark')==='true')document.body.classList.add('dark')}catch(e){};_syncThemeBtn()})();
// sortable 绑定必须等 DOM 就绪——script 在 <head> 里,解析到这行时 <body> 表格还不存在
// (V7 潜伏 bug: 绑定静默匹配 0 个元素,点击从未生效,2026-09-13 用户报修)
document.addEventListener('DOMContentLoaded',function(){
document.querySelectorAll('table.sortable th[data-key]').forEach(function(th){
  th.addEventListener('click',function(){
    var table=th.closest('table'), body=table.tBodies[0],
        rows=[].slice.call(body.rows), i=th.cellIndex, key=th.getAttribute('data-key'),
        asc=th.classList.contains('asc');
    table.querySelectorAll('th').forEach(function(h){h.classList.remove('asc','desc')});
    th.classList.add(asc?'desc':'asc');
    var num=/^-?\\d/ ;
    rows.sort(function(a,b){
      var x=a.cells[i].getAttribute('data-v')||a.cells[i].textContent.trim(),
          y=b.cells[i].getAttribute('data-v')||b.cells[i].textContent.trim();
      var nx=parseFloat(String(x).replace('%','')), ny=parseFloat(String(y).replace('%',''));
      if(!isNaN(nx)&&!isNaN(ny)&&num.test(String(x).replace(/^\\+/,''))) {return asc?nx-ny:ny-nx}
      return asc? String(x).localeCompare(String(y),'zh') : String(y).localeCompare(String(x),'zh');
    });
    rows.forEach(function(r){body.appendChild(r)});
  });
});
});
"""

_RING_LABEL = {"forecast": "预告", "express": "快报", "actual": "正式报"}
_AXIS_LABEL = {"deducted": "扣非", "reported": "归母", "forecast": "预告"}
_PERIOD_TAIL = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}


def _fmt_period(p) -> str:
    """20260630 → 「2026 中报」。"""
    p = str(p or "")
    return f"{p[:4]}{_PERIOD_TAIL.get(p[4:], '')}" if len(p) == 8 else (p or "—")


def _framework_section(snap: dict) -> str:
    """〇 选股框架——看板顶部的内核思想展示(活漏斗用当前真实数字)。默认展开可收起。"""
    u = snap.get("universe_stats") or {}
    note = snap.get("cfg_note") or {}
    gaps = snap.get("gaps") or {}
    rows = snap.get("rows") or []
    red_n = sum(v for k, v in gaps.items() if str(k).startswith("红旗剔除"))
    gated = int(snap.get("n_track_peg", 0)) + int(snap.get("n_track_pb", 0))

    def _stage(num: str, label: str, sub: str) -> str:
        return (f"<div style='flex:1;min-width:118px;background:var(--card);"
                f"border:1px solid var(--border);border-radius:8px;padding:7px 9px;"
                f"text-align:center'><div style='font-size:16px;font-weight:700'>{_e(num)}</div>"
                f"<div style='font-size:12px;color:var(--text)'>{_e(label)}</div>"
                f"<div style='font-size:10.5px;color:var(--muted);margin-top:2px'>{_e(sub)}</div></div>")
    arrow = ("<div style='color:var(--faint);font-size:15px;align-self:center'>→</div>")
    funnel = ("<div style='display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 4px'>"
              + _stage(f"{u.get('n', 0)}", "全市场非 ST", "spot 快照宇宙")
              + arrow
              + _stage(f"{snap.get('n_floor_pass', 0)}", "过三环地板",
                       f"净利≥{note.get('floor_np_yoy', 50):.0f}% ∧ 营收≥{note.get('floor_rev_yoy', 20):.0f}%")
              + arrow
              + _stage(f"−{red_n}", "红旗硬剔", "商誉/存贷双高")
              + arrow
              + _stage(f"{gated}", "过估值门",
                       f"PEG≤{note.get('peg_max', 1.0):.1f} / PB分位≤{note.get('pb_pct_max', 0.30):.0%}")
              + arrow
              + _stage(f"{len(rows)}", "高业绩池", f"两轨合并 Top-{note.get('top_n', 100)}")
              + "</div>")

    principles = [
        ("季度循环", "每季三环(预告→快报→正式报)滚动重筛,每环落地即重判——「每个季度的正式报、预报、"
         "快报,都会根据最新的业绩做调整组合」"),
        ("不预测持续性", "「增速不行了下季度自然被淘汰,不必关心业绩是否能持续——保持组合由高业绩构成」;"
         "池是流动状态,④节环比 diff 就是换血的可视化"),
        ("估值门", "「业绩还没出来股价已经上天的」用 PEG 筛掉——增长必须没被充分定价"),
        ("周期股看 PB", "周期底利润崩、PE 爆表——用资产价格(PB 自身历史分位)看便宜,不用利润"),
        ("行业暴露涌现", "不自上而下预判行业——先筛高业绩,再看哪类占过半(②节涌现簇);陈老师 7 月"
         "由此发现商品周期占半数"),
        ("中小市值弹性", "「一般中小市值的弹性更好,涨幅大,少量资金推动就能涨不少」——市值列常驻"
         "可排序;过滤默认关(开=P80 分位),是否常开由验证器 mktcap_on 消融臂数据裁决"),
        ("决策哲学", "「决策交易的唯一因素是业绩预期和估值,大盘是什么阶段只是辅助」——本看板因此"
         "不择时,只给候选"),
    ]
    pr_html = "".join(
        f"<li><b>{_e(k)}</b>——{_e(v)}</li>" for k, v in principles)

    return (
        "<details class='sop' open><summary>🧭 〇 选股框架——本看板的内核思想(点开/收起)</summary><div>"
        f"{funnel}"
        "<div style='font-size:12px;color:var(--muted);line-height:1.9;margin-top:4px'>"
        "漏斗数字=当前渲染实况(随披露窗变动;641→320 之间还含黄旗不剔/数据缺的个体差异)。"
        "最后一公里是<b>人工排除</b>——模型缩小范围,③节逐旗复审是方法论自带的最后一步,看板不冒充人工。</div>"
        f"<ul style='margin:10px 0 4px;padding-left:18px;font-size:13px;line-height:1.9;"
        f"color:var(--text2)'>{pr_html}</ul>"
        "<div style='font-size:12.5px;background:var(--ovbg);border-left:3px solid var(--link);"
        "border-radius:6px;padding:8px 12px;line-height:1.9'>"
        "<b>使用动线</b>:① 总览(池+黄旗) → <b>③ 人工复审(逐旗排除——你的活)</b> → ② 行业构成"
        "(涌现簇) → ④ 披露时钟(什么时候换血) → ⑤ 历史回放(验证器结论兜底)。"
        "方法论全文与逐条 SOP 详见 docs/stock_pool/MANUAL_REVIEW.md。"
        "</div></div></details>")


def _e(v) -> str:
    return _html.escape(str(v)) if v is not None else ""


def _fmt_pct(v, digits=1) -> str:
    if v is None or v != v:
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{v * 100:+.{digits}f}%</span>"


def _fmt_num(v, digits=1, suffix="") -> str:
    if v is None or v != v:
        return "—"
    return f"{v:.{digits}f}{suffix}"


def _fmt_mktcap(v) -> str:
    """市值(元)→ 亿。"""
    if v is None or v != v:
        return "—"
    return f"{float(v) / 1e8:.0f}亿"


def _anchor_nav() -> str:
    items = [("fw", "〇 选股框架"), ("pool", "① 池总览"), ("sector", "② 行业构成"),
             ("risk", "③ 风险与人工复审"), ("clock", "④ 披露时钟·diff"),
             ("history", "⑤ 历史回放"), ("guide", "⑥ 读图说明")]
    return ("nav class='anchor'>" +
            "".join(f"<a href='#{i}'>{t}</a>" for i, t in items) + "</nav>")


def _table(headers: list, rows: list[list[str]],
           table_id: str = "", sortable: bool = True) -> str:
    """headers = [(key, 显示名) 或 (key, 显示名, 原生属性串)];key='' → 不排序。"""
    cls = "sortable" if sortable else ""
    ths = ""
    for h in headers:
        k, t = h[0], h[1]
        extra = f" {h[2]}" if len(h) > 2 else ""
        ths += f"<th{' data-key=' + repr(k) if k else ''}{extra}>{_e(t)}</th>"
    trs = "".join("<tr>" + "".join(r) + "</tr>" for r in rows) or \
          f"<tr><td colspan='{len(headers)}' class='dim'>（无条目）</td></tr>"
    return f"<table id='{table_id}' class='{cls}'><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>"


def _pool_section(snap: dict) -> str:
    rows = snap.get("rows") or []
    note = snap.get("cfg_note") or {}
    clock = snap.get("clock") or {}
    phase = clock.get("phase") or {}
    data_p = clock.get("data_period")
    out = [f"<h2 id='pool'>① 高业绩池 · 当前切面 <span class='count'>"
           f"{len(rows)} 只(过地板 {snap.get('n_floor_pass', 0)} → 估值/风险门后 Top-{note.get('top_n', 100)})</span></h2>"]
    if phase:
        out.append(
            f"<div class='banner'>📅 <b>{_e(phase.get('label', ''))}</b>"
            f" · 池由 <b>{_e(_fmt_period(data_p))}</b> 披露构成 · "
            f"下个变动: {_e(phase.get('next_label', ''))} {_e(phase.get('next_date', ''))}"
            f"(<b>{phase.get('days_to_next', 0)}</b> 天)</div>")
    out.append(
           "<div class='hint'>地板=净利同比 ≥{:.0f}% 且营收同比 ≥{:.0f}%(预告环单腿·无营收数据);"
           "非周期轨 PEG=PE_ttm/扣非g ≤{:.1f}(「业绩没出股价已上天」筛掉);周期轨 PB 自身历史分位 ≤{:.0f}%"
           "(周期底 PE 爆表被误杀,PB 低=资产便宜);每格分数自带轨别标签(PEG 数值/PB分位 %),"
           "不依赖类型列。类型列「—」=行业成分缺失(月更腿未通,全池暂走 PEG 轨;行业表通了后周期股自动分流 PB 轨)。"
           "红旗硬剔/黄旗带旗入池交人工。入场=该股自身披露日"
           "(逐股状态机,point-in-time);下一次披露不过地板即出池——「自然被淘汰」,不预测持续性。</div>".format(
               note.get("floor_np_yoy", 50.0), note.get("floor_rev_yoy", 20.0),
               note.get("peg_max", 1.0), (note.get("pb_pct_max", 0.30) or 0) * 100))
    body = []
    for r in rows:
        flags = "".join(f"<span class='chip warn'>{_e(f)}</span> "
                        for f in (r.get("yellow") or []))
        ring = _RING_LABEL.get(r.get("ring"), "—")
        if r.get("period_used"):
            ring += f"·{_fmt_period(r['period_used'])}"   # 正式报·2026中报——凭哪份文件办的会员
        m = r.get("metrics") or {}
        score = r.get("score")
        if r.get("track") == "peg":
            score_html = (f"<td data-v='{score:.3f}'>{score:.2f} "
                          f"<span class='chip'>PEG</span></td>"
                          f"<td data-v='{r.get('pe_ttm') if r.get('pe_ttm') is not None else -1}'>"
                          f"{_fmt_num(r.get('pe_ttm'), 1)}</td>"
                          f"<td class='dim'>—</td>")
        else:
            score_html = (f"<td data-v='{score:.3f}'>{score:.0%} "
                          f"<span class='chip'>PB分位</span></td><td class='dim'>—</td>"
                          f"<td data-v='{score:.3f}'>{_fmt_num((r.get('pb_pct')), 0)}</td>")
        np_v = r.get("np_yoy_bulk")
        axis = _AXIS_LABEL.get(r.get("np_axis") or "", "")
        body.append([
            f"<td>{_e(r['name'])} <span class='dim'>{r['code']}</span> {flags}</td>",
            f"<td>{_e(r.get('industry') or '未映射')}</td>",
            f"<td>{_e(r.get('type') or '—')}</td>",
            f"<td><span class='chip'>{ring}</span></td>",
            f"<td>{_e(r.get('announce_date') or '—')}</td>",
            f"<td data-v='{np_v if np_v is not None else -999}'>"
            f"<span class='{'pos' if (np_v or 0) > 0 else 'neg'}'>{_fmt_num(np_v, 0, '%')}</span>"
            f" <span class='chip' title='扣非=正式环精筛口径/归母=粗筛回退'>{_e(axis)}</span></td>",
            f"<td data-v='{r['rev_yoy'] if r.get('rev_yoy') is not None else -999}'>"
            f"{_fmt_num(r.get('rev_yoy'), 0, '%')}</td>",
            score_html,
            f"<td data-v='{float(r['mktcap']) if r.get('mktcap') is not None else -1}'>"
            f"{_fmt_mktcap(r.get('mktcap'))}</td>",
        ])
    out.append(_table(
        [("name", "股票"), ("industry", "行业"), ("type", "类型"), ("ring", "入场环"),
         ("entered", "入场日"), ("np", "净利yoy"), ("rev", "营收yoy"),
         ("score", "PEG/PB分位",
          "class='th-key' title='入池排序键:估值门内各轨按此值升序→轨内名次百分位合并取Top-N;"
          "表行序=合并名次(越上=轨内相对越便宜)'"),
         ("pe", "PE_ttm"), ("pb", "PB分位"), ("mktcap", "市值")],
        body))
    return "".join(out)


def _sector_section(snap: dict) -> str:
    comp = snap.get("composition") or {}
    emerg = snap.get("emergent")
    out = ["<h2 id='sector'>② 行业构成 <span class='count'>"
           f"{comp.get('n_industries', 0)} 个行业 · 共 {comp.get('n_total', 0)} 只</span></h2>",
           "<div class='hint'>行业暴露从池里涌现,不自上而下预判——先有高业绩筛选,再看哪类行业占比过半"
           "(陈老师 7 月由此发现商品周期占半数)。看板只陈列事实,不做配比建议。</div>"]
    if emerg:
        inds = "、".join(emerg["industries"][:12]) + ("…" if len(emerg["industries"]) > 12 else "")
        out.append(f"<div class='banner hot'>🔥 涌现簇: 商品关联周期占比 "
                   f"<b>{emerg['share']:.0%}</b>({emerg['n']}/{emerg['n_total']})"
                   f"——{inds}</div>")
    else:
        out.append("<div class='banner'>无涌现簇(商品关联周期占比 &lt;50%)——常态,非故障</div>")
    body = []
    for d in comp.get("by_industry") or []:
        body.append([
            f"<td>{_e(d['industry'])}</td>",
            f"<td data-v='{d['n']}'>{d['n']}</td>",
            f"<td data-v='{d['share']:.4f}'>{d['share']:.0%}</td>",
        ])
    typ = comp.get("by_type") or {}
    typ_line = " · ".join(f"{k} {v}" for k, v in sorted(typ.items(), key=lambda x: -x[1]))
    out.append(f"<div class='banner'>类型分布: {_e(typ_line or '—')}</div>")
    out.append(_table([("industry", "行业"), ("n", "池内只数"), ("share", "占比")], body))
    return "".join(out)


def _risk_section(snap: dict) -> str:
    gaps = snap.get("gaps") or {}
    rows = snap.get("rows") or []
    flagged = [r for r in rows if r.get("yellow")]
    out = ["<h2 id='risk'>③ 风险筛与人工复审 <span class='count'>"
           f"{len(flagged)} 只带黄旗</span></h2>",
           "<div class='hint'>红旗(商誉/净资产>30% · 存贷双高代理)=信号本身即风险,硬剔不入池;"
           "黄旗=需要人判断行业语境,带旗入池交人工。模型缩小范围→<b>人工排除</b>是方法论自带的"
           "最后一步,看板不冒充人工。</div>"]
    body = []
    for r in flagged:
        m = r.get("metrics") or {}
        notes = []
        if m.get("receivable_rev") is not None:
            notes.append(f"应收/营收 {m['receivable_rev']:.0%}")
        if m.get("goodwill_jump") is not None:
            notes.append(f"商誉环比 {m['goodwill_jump']:+.0%}")
        if m.get("goodwill_equity") is not None:
            notes.append(f"商誉/净资产 {m['goodwill_equity']:.0%}")
        body.append([
            f"<td>{_e(r['name'])} <span class='dim'>{r['code']}</span></td>",
            f"<td>{_e(r.get('industry') or '未映射')}</td>",
            f"<td style='text-align:left'>" +
            "".join(f"<span class='chip warn'>{_e(f)}</span> " for f in r["yellow"]) + "</td>",
            f"<td class='dim' style='text-align:left'>{_e(' · '.join(notes))}</td>",
        ])
    out.append(_table([("name", "股票"), ("industry", "行业"),
                       ("flags", "黄旗"), ("metrics", "度量")], body, sortable=False))
    if gaps:
        gl = " · ".join(f"{k}×{v}" for k, v in sorted(gaps.items(), key=lambda x: -x[1]))
        out.append(f"<div class='banner cold'>数据缺口(诚实呈现,不冒充安全): {_e(gl)}</div>")
    out.append(_sop_details())
    return "".join(out)


def _sop_details() -> str:
    """内嵌人工复审 SOP checklist(教学层;详版 docs/stock_pool/MANUAL_REVIEW.md)。"""
    return (
        "<details class='sop'><summary>📋 人工复审 SOP(每个旗怎么查·点开教学)</summary><div>"
        "<b>扭亏</b> → 查上年同期是否「洗大澡」(资产减值/商誉冲销堆基数)、本期增长是否主业贡献;"
        "东财 F10「财务分析」+ 年报「非经常性损益」节。<br>"
        "<b>商誉激增(并购代理)</b> → F10「并购重组」公告:增长是买来的还是内生?业绩承诺(对赌)"
        "占净利比例、承诺到期年(到期后变脸高发);扣非滤不掉并表——连续计入。<br>"
        "<b>应收/营收高</b> → 先看行业(建筑/军工/政府客户行业性高,不硬杀);再查应收增速 vs 营收"
        "增速(应收涨更快=放松信用换收入)、1 年以上账龄占比、经营现金流/净利(ocf 长期低于净利=纸面富贵)。<br>"
        "<b>存贷双高(代理口径)</b> → 查利息收入 vs 货币资金规模是否匹配(账上巨款却高息借款="
        "康得新式前科);有息负债精确口径需 F10 资产负债表(短借+长借+应付债券)——批量端点无该列,"
        "本旗是代理,读图说明⑥注明。<br>"
        "<b>营运资本/长期负债(无自动数据源)</b> → F10 手查:(流动资产−流动负债)/长期借款,"
        "陈老师原文四条之一,过低=短债长投错配。<br>"
        "<b>未精筛</b> → sina 精筛腿(扣非/商誉)未拉到,本次用归母口径,报告脚本下次自动补。<br>"
        "<b>通用·增长质量</b> → 归母 vs 扣非背离(一次性利润识别,个股诊断看板 Q1 同原语);"
        "增长靠提价还是放量;单客户依赖度(前五客户占比)。"
        "</div></details>")


def _clock_section(snap: dict) -> str:
    clock = snap.get("clock") or {}
    mix = clock.get("ring_mix") or {}
    pmix = clock.get("period_mix") or {}
    diff = snap.get("diff") or {}
    pmix_txt = " · ".join(f"{k}×{v}" for k, v in sorted(pmix.items(), reverse=True))
    data_p = clock.get("data_period")
    out = [f"<h2 id='clock'>④ 披露时钟 · 环比 diff</h2>",
           f"<div class='banner'>管道期 <b>{_e(clock.get('period', ''))}</b>"
           f"(正式报截止 {_e(clock.get('formal_deadline', ''))},剩 {clock.get('days_to_formal', 0)} 天 · "
           f"预告开窗 {_e(clock.get('forecast_open', ''))}) · "
           f"数据期 <b>{_e(data_p or '—')}</b>"
           + (f"<span class='dim'>({_e(pmix_txt)})</span>" if pmix_txt and len(pmix) > 1 else "")
           + f" · 过地板路径: 预告 {mix.get('forecast', 0)} / 快报 {mix.get('express', 0)} / "
           f"正式 {mix.get('actual', 0)}</div>",
           "<div class='hint'>管道期=披露管道仍在飞行的最老一期;数据期=池当前由哪个报告期的环撑着"
           "(披露间隙期上期环撑到下期披露落地才换血——状态机语义,无任意批重建时点)。"
           "每季三环(预告→快报→正式报)滚动重筛,每环落地即重判——"
           "「每个季度的正式报、预报、快报,都会根据最新的业绩做调整组合」。</div>"]
    for tw in clock.get("theme_windows") or []:
        out.append(f"<div class='banner hot'>⏱ 主题死线(事实陈列·可证伪,见 CLAIMS_LEDGER): "
                   f"{_e(tw.get('label', ''))} —— 窗至 {_e(tw.get('until', ''))}</div>")
    ent = diff.get("entered") or []
    ext = diff.get("exited") or []
    out.append(f"<div class='banner'>环比 新进 <b>{len(ent)}</b> · 淘汰 <b>{len(ext)}</b>"
               "(vs 上次快照;「增速不行了下季度自然被淘汰」的可视化)</div>")
    if ent:
        out.append("<div style='line-height:2.2'>🟢 新进: " + "".join(
            f"<span class='chip'>{_e(d['name'])}</span> " for d in ent[:60]) + "</div>")
    if ext:
        out.append("<div style='line-height:2.2'>🔴 淘汰: " + "".join(
            f"<span class='chip'>{_e(d['name'])}</span> " for d in ext[:60]) + "</div>")
    return "".join(out)


def _history_section(snap: dict) -> str:
    hist = snap.get("history") or []
    concl = snap.get("conclusion")
    body = []
    for h in hist[:12]:
        body.append([
            f"<td>{_e(h['asof'])}</td>",
            f"<td data-v='{h['n']}'>{h['n']}</td>",
            f"<td>{_e(h.get('period') or '—')}</td>",
        ])
    out = ["<h2 id='history'>⑤ 历史池回放 <span class='count'>留档 {len(hist)} 份快照</span></h2>",
           "<div class='hint'>池成员逐次渲染留档(pool_membership 表);深回放与业绩对齐归验证器"
           "——<b>实证结论</b>: " +
           (_e(concl) if concl else
            "未运行 python scripts/validate_high_earnings_pool.py —— 结论注入占位") + "</div>",
           _table([("asof", "快照日"), ("n", "池规模"), ("period", "报告期")], body)]
    return "".join(out)


def _guide_section(snap: dict) -> str:
    note = snap.get("cfg_note") or {}
    return (
        "<div class='summary-box' id='guide'><b>⑥ 读图说明</b> —— 只读筛选旁路,永不喂交易引擎"
        "(ADR-0001);给候选不给买卖点,人决策综合多看板。<br>"
        "<b>三环口径差异(数据先天,非妥协)</b>: 预告环只有净利维度(单腿地板);快报环双轴未审计;"
        "正式环=扣非双轴(sina 逐股精筛,幸存者才有;未拉到时归母回退+「未精筛」旗)。<br>"
        "<b>估值口径</b>: PE_ttm=市值/TTM归母净利(TTM=上年报+本期YTD−上年同期YTD);市值_t=现股本"
        "(现市值/现价反推)×价_t——回放近似,股本缓变假设;PB=raw价/每股净资产(报告期阶梯,公告日"
        "point-in-time);spot 动态 PE 仅交叉核对列不进判定。<br>"
        "<b>代理口径诚实注</b>: 存贷双高=货币资金/总资产≥15%∧资产负债率≥40%(有息负债批量端点无列);"
        "并购代理=商誉环比激增(陈老师的人工甄别不可全自动);营运资本/长期负债无数据源→纯人工项。<br>"
        "<b>温度计非开关</b>: 8 季×3 环≈20 个有效窗口,对「70% 场合跑赢所有指数」是小样本检验——"
        "验证器结论照实注入,可能显示无 edge,那是诚实。</div>")


def render(snapshot: dict) -> str:
    """渲染 data/stock_pool.html(snapshot=screen.build_high_earnings_snapshot 契约)。纯字符串拼装。"""
    u = snapshot.get("universe_stats") or {}
    note = snapshot.get("cfg_note") or {}
    med = snapshot.get("mktcap_median")
    med_html = (f"<div class='stat' title='池内成员总市值中位数(排队取正中,抗极端值)——"
                f"陈述体型结构,不代表筛选倾向'>池内市值中位 <b>{float(med) / 1e8:.0f}亿</b></div>") \
        if med is not None else ""
    stats = (
        f"<div class='statline'>"
        f"<div class='stat'>宇宙 <b>{u.get('n', 0)}</b> 只(全市场非ST)</div>"
        f"<div class='stat'>过地板 <b>{snapshot.get('n_floor_pass', 0)}</b></div>"
        f"<div class='stat'>池 <b>{snapshot.get('n_gated_pool', 0)}</b>"
        f"<span class='dim'>(PEG轨 {snapshot.get('n_track_peg', 0)}/PB轨 {snapshot.get('n_track_pb', 0)}"
        f" · Top-{note.get('top_n', 100)})</span></div>"
        f"<div class='stat' title='正在进行的财报季——下一波业绩数字的来源。"
        f"此期财报从预告窗开始陆续公布,每公布一家池子重判一家(过门留/不过出),"
        f"截止日全部收官、换血完成;披露间隙期池子冻结(手里的是上一季已收官的数字,"
        f"见①节📅横幅的数据期)'>报告期 <b>{_e(snapshot.get('period', ''))}</b></div>"
        f"{med_html}"
        f"<div class='stat' title='宇宙中被分到周期/成长/价值三类的比例"
        f"(东财板块×stock_industry.yaml);0%=行业成分表未拉成(push2 拦,--fix 自动重试)'"
        f">行业映射 <b>{u.get('pct', 0):.0%}</b>"
        f"<span class='dim'>(周期{u.get('n_cyclic', 0)}/成长{u.get('n_growth', 0)}"
        f"/价值{u.get('n_value', 0)})</span></div>"
        f"<div class='stat'>spot <b>{_e(snapshot.get('spot_date') or '无')}</b></div>"
        f"</div>")

    body = (
        f"<div class='wrap'>"
        f"<header><h1>🎯 候选个股池 · 高业绩池</h1>"
        f"<span class='meta'>as of {_e(snapshot.get('as_of', ''))} · 第六看板 · 只读 ·"
        f" 陈氏季度池 V8</span>"
        f"<button id='theme-btn' onclick='toggleTheme()'>🌙</button></header>"
        f"{stats}{_anchor_nav()}"
        f"<div id='fw'>{_framework_section(snapshot)}</div>"
        + _pool_section(snapshot)
        + _sector_section(snapshot)
        + _risk_section(snapshot)
        + _clock_section(snapshot)
        + _history_section(snapshot)
        + _guide_section(snapshot)
        + "<footer>生成: python scripts/stock_pool_report.py · 参数: config/params.yaml stock_pool.high_pool ·"
          " 行业映射: config/stock_industry.yaml(未映射→PEG轨,不入周期PB轨)<br>"
          "方法论出处: 重远投资观(季度高业绩池·2026-09 提炼, docs/stock_pool/MANUAL_REVIEW.md 存档) ·"
          " 只读诊断 · 永不喂交易引擎(docs/adr/0001 同款围栏) · 温度计非开关,实证结论可能显示无 edge——那是诚实</footer>"
        f"</div>")
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>候选个股池 · 高业绩池 · {snapshot.get('as_of', '')}</title>"
            f"<style>{_CSS}</style><script>{_JS}</script></head><body>{body}</body></html>")


def write_html(path, snapshot: dict) -> None:
    from pathlib import Path
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snapshot), encoding="utf-8")
