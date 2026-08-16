"""候选个股池看板渲染(fat renderer)——单页 + 锚点导航(六表交叉引用不宜藏进 tab)。

Section 序: 头部状态行 / 读图说明(口径+validator 结论注入) / ①偏离超卖 / ②猛×深跌 /
③修正动量 / ④PEAD / ⑤变脸监测 / ⑥双击候选(stage-2 腿) / stage-2 候选卡 / 页脚。
视觉语言与 research/tracker 看板同源(CSS vars + chips + sortable + 默认浅色可切深)。
v1 无内嵌 Plotly(表为主;stage-2 卡图管线见后续),主题切换因此无需 purge。
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
.count{color:var(--muted);font-weight:normal;font-size:12px}
.chip{font-size:11px;padding:1px 8px;border-radius:9px;background:var(--chipbg);
      color:var(--text2);border:1px solid var(--border);white-space:nowrap}
.pos{color:var(--crit)} body.dark .pos{color:#f87171}   /* A股红=涨 */
.neg{color:var(--link)} body.dark .neg{color:#60a5fa}
.dim{color:var(--muted)}
.badge-new{font-size:10px;background:#16a34a;color:#fff;border-radius:4px;padding:0 4px;margin-left:3px}
.badge-suspect{font-size:10px;background:#fcd34d;color:#78350f;border-radius:4px;padding:0 4px;margin-left:3px}
footer{margin-top:40px;padding-top:12px;border-top:1px solid var(--border);color:var(--faint);
      font-size:11.5px;line-height:1.8}
"""

_JS = """
function _isDark(){return document.body.classList.contains('dark')}
function _syncThemeBtn(){var b=document.getElementById('theme-btn');if(b)b.textContent=_isDark()?'☀️':'🌙'}
function toggleTheme(){document.body.classList.toggle('dark');
  try{localStorage.setItem('pool-dark',String(_isDark()))}catch(e){};_syncThemeBtn()}
(function(){try{if(localStorage.getItem('pool-dark')==='true')document.body.classList.add('dark')}catch(e){};_syncThemeBtn()})();
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
"""


def _e(v) -> str:
    return _html.escape(str(v)) if v is not None else ""


def _fmt_pct(v, digits=1) -> str:
    if v is None or v != v:
        return "—"
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f"<span class='{cls}'>{v * 100:+.{digits}f}%</span>"


def _anchor_nav() -> str:
    items = [("s1", "① 偏离超卖"), ("s2", "② 猛×深跌"), ("rev", "③ 修正动量"),
             ("pead", "④ PEAD"), ("face", "⑤ 变脸监测"), ("davis", "⑥ 双击候选"),
             ("stage2", "候选深挖")]
    return ("nav class='anchor'>" +
            "".join(f"<a href='#{i}'>{t}</a>" for i, t in items) + "</nav>")


def _table(headers: list[tuple[str, str]], rows: list[list[str]],
           table_id: str = "", sortable: bool = True) -> str:
    """headers = [(key, 显示名)];key='' → 不排序。rows = 已渲染的 td 列表(含 html)。"""
    cls = "sortable" if sortable else ""
    ths = "".join(f"<th{' data-key=' + repr(k) if k else ''}>{_e(t)}</th>" for k, t in headers)
    trs = "".join("<tr>" + "".join(r) + "</tr>" for r in rows) or \
          f"<tr><td colspan='{len(headers)}' class='dim'>（无条目）</td></tr>"
    return f"<table id='{table_id}' class='{cls}'><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table>"


def _s1_section(rows: list[dict], total: int, cfg_note: dict) -> str:
    out = [f"<h2 id='s1'>① 偏离超卖候选 <span class='count'>展示 {len(rows)} / 触发 {total}"
           f"(全池扫描,自身历史百分位 ≤{cfg_note.get('trigger_pct', 0.05):.0%})</span></h2>",
           "<div class='hint'>触发=自身 MA60 偏离历史百分位 ≤5%(非横截面);复合分="
           "0.6×深度+0.4×企稳;护栏未过的行照显示但分数为空(排除出候选)。⚠️=除权嫌疑"
           "(分红表未及刷新,周更自愈,不入深挖);🆕=近 5 日新触发。</div>"]
    body = []
    for r in rows:
        new = "<span class='badge-new'>新</span>" if (r.get("days_in_run") or 99) <= 5 else ""
        sus = "<span class='badge-suspect'>⚠️除权</span>" if r.get("suspect") else ""
        score = f"{r['score']:.0f}" if r.get("score") is not None else \
            f"<span class='dim' title='{_e('、'.join(r.get('guard_flags') or []))}'>护栏:{_e('、'.join(r.get('guard_flags') or [])[:24])}</span>"
        body.append([
            f"<td>{_e(r['name'])} <span class='dim'>{r['code']}</span>{new}{sus}</td>",
            f"<td>{_e(r.get('industry') or '未映射')}</td>",
            f"<td>{_e(r.get('type') or '—')}</td>",
            f"<td data-v='{r['dev_pct']:.4f}'>{r['dev_pct']:.1%}</td>",
            f"<td data-v='{r['cur_dev']:.4f}'>{_fmt_pct(r['cur_dev'])}</td>",
            f"<td data-v='{r['stabilize']:.2f}'>{r['stabilize']:.2f}</td>",
            f"<td data-v='{r['score'] if r.get('score') is not None else -1}'>{score}</td>",
        ])
    out.append(_table([("name", "股票"), ("industry", "行业"), ("type", "类型"),
                       ("dev_pct", "偏离分位"), ("cur_dev", "当前偏离"),
                       ("stabilize", "企稳"), ("score", "复合分")], body))
    return "".join(out)


def _s2_section(rows: list[dict], total: int, window_note) -> str:
    wb = ""
    if window_note:
        wb = (f" 窗口B(仅周期): {window_note['start']}~{window_note['end']}"
              f"(下期 {window_note['period']})")
    out = [f"<h2 id='s2'>② 业绩预期猛 × 深跌 <span class='count'>展示 {len(rows)} / 资格 {total}</span></h2>",
           f"<div class='hint'>资格 = 250日回撤≥40% ∧ 披露窗口有效 ∧ 猛分有效。周期猛=商品健康×"
           f"股价落后+g确认(纯研报g筛周期=买在预期顶);成长猛=前瞻g分位/4周上修/已报加速 三腿≥2。"
           f"窗口A=预告/快报落地→正式报截止(全类型);{wb}。Q1 期窗口B 天然为空(年报截止晚于Q1预告开窗)。</div>"]
    body = []
    for r in rows:
        conf = "✔" if r.get("confirmed") else "<span class='dim'>未确认</span>"
        body.append([
            f"<td>{_e(r['name'])} <span class='dim'>{r['code']}</span></td>",
            f"<td>{_e(r.get('industry') or '未映射')}</td>",
            f"<td>{_e(r.get('type') or '—')}</td>",
            f"<td><span class='chip'>{_e(r['chip'])}</span></td>",
            f"<td data-v='{r['days_to_formal']}'>{r['days_to_formal']}日</td>",
            f"<td data-v='{r['drawdown']:.3f}'>{_fmt_pct(r['drawdown'])}</td>",
            f"<td data-v='{r['fierce']:.3f}'>{r['fierce']:.2f}</td>",
            f"<td class='dim'>{_e(r.get('legs') or '')}</td>",
            f"<td>{conf if r.get('type') == 'cyclic' else '—'}</td>",
        ])
    out.append(_table([("name", "股票"), ("industry", "行业"), ("type", "类型"),
                       ("chip", "窗口"), ("days", "距正式报"), ("drawdown", "250日回撤"),
                       ("fierce", "猛分"), ("legs", "猛分分解"), ("confirmed", "g确认")], body))
    return "".join(out)


def _rev_section(rev: dict) -> str:
    cold = rev.get("cold_start")
    banner = ""
    if cold:
        banner = (f"<div class='banner cold'>⏳ 一致预期修正动量 累积中 {cold['have']}/{cold['need']} ——"
                  f"E0 周度快照自 2026-08 起积累,约 {cold['need'] - cold['have']} 周后激活"
                  f"(期间本表为空,诚实降级非故障)</div>")
    rows = rev.get("rows") or []
    body = []
    for r in rows:
        cls = "pos" if r["rev_pct"] > 0 else "neg"
        body.append([
            f"<td>{_e(r.get('name', r['code']))} <span class='dim'>{r['code']}</span></td>",
            f"<td data-v='{r['rev_pct']:.4f}'><span class='{cls}'>{r['rev_pct']:+.1f}%</span></td>",
            f"<td>{'⬆' if r['up'] else ('⬇' if r['down'] else '·')}</td>",
        ])
    return (f"<h2 id='rev'>③ 一致预期修正动量(个股版 E4) <span class='count'>Top {len(rows)}</span></h2>"
            f"{banner}"
            + _table([("name", "股票"), ("rev", "4周EPS修正"), ("dir", "方向")], body))


def _pead_section(rows: list[dict], conclusion: str | None) -> str:
    concl = conclusion or "未运行 python scripts/validate_pead.py —— 结论注入占位"
    body = []
    for r in rows:
        cls = "pos" if r["surprise_pp"] > 0 else "neg"
        body.append([
            f"<td>{_e(r.get('name', r['code']))} <span class='dim'>{r['code']}</span></td>",
            f"<td>{_e(r['period'])}</td>",
            f"<td>{_e(r.get('type') or '')}</td>",
            f"<td data-v='{r['forecast_yoy']:.1f}'>{r['forecast_yoy']:.0f}%</td>",
            f"<td data-v='{r['expected']:.1f}'>{r['expected']:.0f}%</td>",
            f"<td data-v='{r['surprise_pp']:.1f}'><span class='{cls}'>{r['surprise_pp']:+.0f}pp</span>"
            f" <span class='chip'>{r['leg']}</span></td>",
            f"<td>{_e(r['announce_date'])}</td>",
        ])
    return (f"<h2 id='pead'>④ PEAD 预告超预期 <span class='count'>|surprise|≥10pp,Top {len(rows)}</span></h2>"
            f"<div class='hint'>surprise = 预告yoy − 公告时点隐含预期(leg C=consensus隐含·年报期 / "
            f"leg A=上年同期实际;point-in-time 无前视)。漂移是否成立由 event-study 裁决:"
            f"<br><b>实证结论</b>: {_e(concl)}</div>"
            + _table([("name", "股票"), ("period", "报告期"), ("type", "类型"),
                      ("yoy", "预告yoy"), ("expected", "隐含预期"),
                      ("surprise", "surprise"), ("date", "公告日")], body))


def _face_section(up: list[dict], down: list[dict]) -> str:
    def _rows(rs):
        body = []
        for r in rs:
            tail = " → ".join(f"{p[4:]}:{v:.0f}%" for p, v in (r.get("tail") or []))
            body.append([
                f"<td>{_e(r['name'])} <span class='dim'>{r['code']}</span></td>",
                f"<td>{_e(r.get('industry') or '未映射')}</td>",
                f"<td style='text-align:left'>{_e(r['detail'])}</td>",
                f"<td class='dim' style='text-align:left'>{_e(tail)}</td>",
            ])
        return _table([("name", "股票"), ("industry", "行业"), ("detail", "变脸"),
                       ("tail", "近4期序列")], body, sortable=False)

    return (f"<h2 id='face'>⑤ 业绩变脸监测 <span class='count'>向下 {len(down)} · 向上 {len(up)}</span></h2>"
            f"<div class='hint'>陈老师方法论系统化: 跳档(小米型)/趋势破位(腾讯型)/连亏(美团型)"
            f"→ 向下(喂策略1护栏);拐头向上 → 埋伏正因子。np_yoy 累计口径,同尾比较防失真。</div>"
            f"<div class='banner' style='border-left:4px solid var(--crit)'>▼ 向下(策略1 护栏来源)</div>"
            + _rows(down)
            + f"<div class='banner' style='border-left:4px solid var(--ok)'>▲ 向上(拐头)</div>"
            + _rows(up))


def _davis_section(rows: list[dict]) -> str:
    body = []
    for r in rows:
        body.append([
            f"<td>{_e(r.get('name', r['code']))} <span class='dim'>{r['code']}</span></td>",
            f"<td>{_e(r.get('label') or '—')}</td>",
            f"<td style='text-align:left'>{_e(r.get('note') or '')}</td>",
        ])
    return (f"<h2 id='davis'>⑥ 戴维斯双击候选 <span class='count'>{len(rows)}</span></h2>"
            f"<div class='hint'>S1∪S2 头部候选 × 估值/财务腿(周度按需拉)——业绩方向×估值方向"
            f"六档(stage-2 深挖,baidu 估值腿就绪后填)。</div>"
            + _table([("name", "股票"), ("label", "档位"), ("note", "注记")], body))


def _stage2_section(cards: list[dict]) -> str:
    if not cards:
        return ("<h2 id='stage2'>候选深挖(stage-2)</h2><div class='banner'>stage-2 候选腿未就绪——"
                "生成时自动对每策略 Top-30 并集周度拉估值/财报腿(--no-stage2 跳过);"
                "腿齐后此处为候选 davis 摘要卡,双击表同源。</div>")
    blocks = []
    for c in cards:
        blocks.append(f"<details><summary>{_e(c.get('name', c['code']))} "
                      f"<span class='dim'>{c['code']}</span> "
                      f"<span class='chip'>{_e(c.get('tag', ''))}</span></summary>"
                      f"<div class='hint'>{_e(c.get('summary', ''))}</div></details>")
    return ("<h2 id='stage2'>候选深挖(stage-2)<span class='count'>Top 候选诊断卡</span></h2>"
            + "".join(blocks))


def render(snapshot: dict) -> str:
    """渲染 data/stock_pool.html(snapshot=screen.build_pool_snapshot 契约)。纯字符串拼装。"""
    u = snapshot.get("universe_stats") or {}
    pf = snapshot.get("price_freshness") or {}
    concl_d = (snapshot.get("conclusions") or {}).get("deviation")
    concl_p = (snapshot.get("conclusions") or {}).get("pead")
    n4 = snapshot.get("n_snapshots", 0)
    cold4 = n4 < 4

    stats = (
        f"<div class='statline'>"
        f"<div class='stat'>宇宙 <b>{u.get('n', 0)}</b> 只(覆盖池∩非ST)</div>"
        f"<div class='stat'>行业映射 <b>{u.get('pct', 0):.0%}</b>"
        f"<span class='dim'>(周期{u.get('n_cyclic', 0)}/成长{u.get('n_growth', 0)}/价值{u.get('n_value', 0)})</span></div>"
        f"<div class='stat'>价格新鲜 <b>{pf.get('fresh', 0)}/{pf.get('n', 0)}</b>(≤7日)</div>"
        f"<div class='stat'>披露周期 <b>{_e(snapshot.get('period', ''))}</b></div>"
        f"<div class='stat'>E4 快照 <b>{n4}</b>/4{' ⏳' if cold4 else ''}</div>"
        f"<div class='stat'>行业快照 <b>{_e(snapshot.get('industry_snapshot') or '无')}</b></div>"
        f"</div>")

    guide = (
        "<div class='summary-box'><b>读图说明</b> —— 只读筛选旁路,永不喂交易引擎(ADR-0001);"
        "给候选不给买卖点,人决策综合多看板。口径: ①偏离=自身历史百分位(非横截面);"
        "价格=raw存储+分红表运行时前复权(除权嫌疑行带⚠️);业绩=np_yoy 累计口径(同尾比较);"
        "猛分仅周期/成长(价值无猛概念);窗口B仅周期(商品可观测)。<br>"
        f"<b>策略1 实证</b>: {_e(concl_d) or '未运行 python scripts/validate_deviation_extreme.py —— 结论注入占位'}<br>"
        f"<b>PEAD 实证</b>: {_e(concl_p) or '未运行 python scripts/validate_pead.py —— 结论注入占位'}"
        "</div>")

    body = (
        f"<div class='wrap'>"
        f"<header><h1>🎯 候选个股池</h1>"
        f"<span class='meta'>as of {_e(snapshot.get('as_of', ''))} · 第六看板 · 只读</span>"
        f"<button id='theme-btn' onclick='toggleTheme()'>🌙</button></header>"
        f"{stats}{_anchor_nav()}{guide}"
        + _s1_section(snapshot.get("s1_rows") or [], snapshot.get("s1_total", 0),
                      (snapshot.get("cfg_note") or {}))
        + _s2_section(snapshot.get("s2_rows") or [], snapshot.get("s2_total", 0),
                      snapshot.get("window_note"))
        + _rev_section(snapshot.get("revision") or {})
        + _pead_section(snapshot.get("pead_rows") or [], concl_p)
        + _face_section(snapshot.get("face_rows_up") or [], snapshot.get("face_rows_down") or [])
        + _davis_section(snapshot.get("davis_rows") or [])
        + _stage2_section(snapshot.get("stage2_cards") or [])
        + "<footer>生成: python scripts/stock_pool_report.py · 数据: stock_pool 参数节(config/params.yaml) ·"
          " 行业映射: config/stock_industry.yaml(未映射板块不入策略2,其余策略不受影响)<br>"
          "只读诊断 · 永不喂交易引擎(docs/adr/0001 同款围栏) · 温度计非开关,实证结论可能显示无 edge——那是诚实</footer>"
        f"</div>")
    return ("<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>候选个股池 · {snapshot.get('as_of', '')}</title>"
            f"<style>{_CSS}</style><script>{_JS}</script></head><body>{body}</body></html>")


def write_html(path, snapshot: dict) -> None:
    from pathlib import Path
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snapshot), encoding="utf-8")
