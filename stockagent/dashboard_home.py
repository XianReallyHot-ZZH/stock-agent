"""八看板总入口壳页 (iframe 导航 · 只读 · 无数据依赖)。

data/index.html: 左侧导航 + 右侧 iframe 装载八个现有看板 HTML, 切换不重载(保留滚动/状态)。
各看板生成器零改动——壳只负责导航/记忆上次选择/as_of(mtime) 标注; 缺哪个看板就提示生成命令。
左侧导航可收缩成 52px 图标轨(«/» 或 Ctrl/⌘+B;状态记忆 sa_home_collapsed), 方便全宽看板。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

# (key, 文件名, 图标, 名称, 一行描述, 生成命令)
DASHBOARDS = [
    ("macro", "macro_framework.html", "🌍", "宏观框架",
     "因果链·黄金定位器·微观紧缺·日历", "python scripts/macro_framework_report.py"),
    ("china_macro", "china_macro.html", "🏛️", "国内宏观",
     "货币信用·利率流动性·政策日历", "python scripts/china_macro_report.py"),
    ("index_timing", "index_timing.html", "📈", "指数择时",
     "估值开关·趋势·相对周期·地量·恐贪", "python scripts/index_timing_report.py"),
    ("research", "research_report.html", "📊", "行业研究",
     "净值偏离度 + 份额净值剪刀差", "python scripts/research_report.py"),
    ("stock", "stock_diagnose.html", "🔍", "个股诊断",
     "三类分类·归因·戴维斯·避坑·埋伏", "python scripts/stock_report.py"),
    ("commodity", "commodity.html", "🛢", "大宗商品",
     "品种面板·国际基准·比价·雷达", "python scripts/commodity_report.py"),
    ("pool", "stock_pool.html", "🎯", "候选个股池",
     "六策略筛全池·超卖/猛×深跌/PEAD/变脸", "python scripts/stock_pool_report.py"),
    ("position", "position.html", "⚖️", "仓位管理",
     "估值档·预案对照·档位统计", "python scripts/position_report.py"),
]


def nav_entries(data_dir: Path, now: datetime | None = None) -> list[dict]:
    """每个看板的文件存在性/as_of(mtime)/当日新鲜度。纯函数, 供渲染与测试。"""
    now = now or datetime.now()
    out: list[dict] = []
    for key, fname, icon, name, desc, cmd in DASHBOARDS:
        p = data_dir / fname
        mtime = datetime.fromtimestamp(p.stat().st_mtime) if p.exists() else None
        out.append({
            "key": key, "file": fname, "icon": icon, "name": name, "desc": desc, "cmd": cmd,
            "exists": mtime is not None,
            "asof": mtime.strftime("%m-%d %H:%M") if mtime else "未生成",
            "asof_full": mtime.strftime("%Y-%m-%d %H:%M") if mtime else "",
            "fresh": bool(mtime and mtime.date() == now.date()),
        })
    return out


_TEMPLATE = """<!doctype html>
<html lang="zh-CN" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>stock-agent · 八看板总入口</title>
<style>
:root { --bg:#f7f7f5; --side:#ffffff; --text:#1a1a19; --muted:#6b7280; --line:#e5e7eb;
        --accent:#ea580c; --ok:#16a34a; --warn:#d97706; }
[data-theme=dark] { --bg:#0d0d0c; --side:#1a1a19; --text:#f3f4f6; --muted:#9ca3af;
        --line:#2c2c2a; --accent:#f97316; --ok:#4ade80; --warn:#fbbf24; }
* { box-sizing:border-box; margin:0; padding:0 }
html,body { height:100% }
body { display:flex; font:14px/1.5 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
       background:var(--bg); color:var(--text) }
aside { width:236px; flex:none; background:var(--side); border-right:1px solid var(--line);
        display:flex; flex-direction:column; height:100vh; transition:width .18s ease }
aside.mini { width:52px }
.brand { display:flex; align-items:center; gap:8px; padding:16px 14px 10px; font-weight:700; font-size:16px }
.brand .sub { display:block; font-size:12px; font-weight:400; color:var(--muted); margin-top:2px;
              white-space:normal; line-height:1.35 }  /* 时间戳完整换行,不被 .btitle 的 nowrap 截断 */
.btitle { overflow:hidden; white-space:nowrap }
.cb { flex:none; width:22px; height:22px; border:1px solid var(--line); border-radius:6px;
      background:transparent; color:var(--muted); cursor:pointer; font-size:13px; line-height:1;
      display:flex; align-items:center; justify-content:center; padding:0 }
.cb:hover { background:rgba(127,127,127,.12); color:var(--text) }
aside.mini .brand { justify-content:center; padding-left:8px; padding-right:8px }
aside.mini .btitle { display:none }
nav { flex:1; overflow-y:auto; padding:4px 8px }
.nav-item { position:relative; display:flex; width:100%; align-items:center; gap:10px; padding:10px; margin:2px 0;
            border:none; border-radius:8px; background:transparent; cursor:pointer; text-align:left;
            color:var(--text); font:inherit }
.nav-item:hover { background:rgba(127,127,127,.12) }
.nav-item.active { background:rgba(234,88,12,.12); box-shadow:inset 3px 0 0 var(--accent) }
.nav-item.missing { opacity:.5 }
.ico { font-size:20px }
.txt { flex:1; min-width:0 }
.txt .nm { display:block; font-weight:600 }
.txt .ds { display:block; font-size:11px; color:var(--muted); white-space:nowrap;
           overflow:hidden; text-overflow:ellipsis }
.side { display:flex; flex-direction:column; align-items:flex-end; gap:3px; flex:none }
.asof { font-size:10px; color:var(--muted); text-align:right; line-height:1.3 }
.dot { display:inline-block; width:7px; height:7px; border-radius:50% }
.dot.fresh { background:var(--ok) } .dot.stale { background:var(--warn) } .dot.none { background:var(--muted) }
/* 收缩态: 图标轨——文字全隐, 新鲜度点转图标右上角标; 悬停 title 携带名称+时间 */
aside.mini .txt, aside.mini .asof { display:none }
aside.mini .side { position:absolute; top:5px; right:7px }
aside.mini .nav-item { justify-content:center; gap:0; padding:10px 0 }
aside.mini nav { padding:4px 6px }
aside.mini .foot .hint { display:none }
aside.mini .foot .tb { width:100% }
aside.mini .foot .tb .tbw { display:none }
.foot { padding:10px 16px 14px; border-top:1px solid var(--line); font-size:11px; color:var(--muted) }
.foot .tb { margin-bottom:8px; padding:4px 10px; border:1px solid var(--line); border-radius:6px;
            background:transparent; color:var(--text); cursor:pointer; font:inherit; font-size:12px }
main { flex:1; position:relative; height:100vh }
iframe { width:100%; height:100%; border:none; display:none; background:#fff }
#ph { position:absolute; inset:0; display:none; flex-direction:column; align-items:center;
      justify-content:center; gap:10px; color:var(--muted); background:var(--bg); text-align:center }
#ph code { background:var(--side); border:1px solid var(--line); padding:3px 10px;
           border-radius:6px; font-size:12px; color:var(--text) }
</style>
</head>
<body>
<aside>
  <div class="brand">
    <button id="collapse" class="cb" title="收起/展开导航 (Ctrl/⌘+B)">«</button>
    <span class="btitle">📈 stock-agent<span class="sub">八看板总入口 · __GEN_AT__</span></span>
  </div>
  <nav id="nav"></nav>
  <div class="foot">
    <button id="theme" class="tb">🌙 <span class="tbw">深色</span></button>
    <div class="hint">绿点=今日已生成 · 黄点=过期<br>刷新: /dashboards 或逐个 report 脚本</div>
  </div>
</aside>
<main>
  <iframe id="frame" title="看板内容"></iframe>
  <div id="ph"></div>
</main>
<script>
const ITEMS = __ITEMS_JSON__;
const LS_LAST = "sa_home_last", LS_THEME = "sa_home_theme";
const root = document.documentElement, nav = document.getElementById("nav");
const frame = document.getElementById("frame"), ph = document.getElementById("ph");

function select(key) {
  const it = ITEMS.find(x => x.key === key);
  if (!it) return;
  document.querySelectorAll(".nav-item").forEach(
    b => b.classList.toggle("active", b.dataset.key === key));
  if (!it.exists) {  // 未生成: 给命令不空白
    frame.style.display = "none"; ph.style.display = "flex";
    ph.innerHTML = '<div style="font-size:40px">' + it.icon + "</div>" +
      "<div><b>" + it.name + "</b> 尚未生成</div><code>" + it.cmd + "</code>";
    return;
  }
  ph.style.display = "none"; frame.style.display = "block";
  // 已加载的不重设 src → 切回保留滚动位置/交互状态
  if (frame.dataset.loaded !== it.file) { frame.src = it.file; frame.dataset.loaded = it.file; }
  try { localStorage.setItem(LS_LAST, key); } catch (e) { /* file:// 隐私模式 */ }
}

ITEMS.forEach(it => {
  const b = document.createElement("button");
  b.className = "nav-item" + (it.exists ? "" : " missing");
  b.dataset.key = it.key;
  b.title = it.asof_full ? it.name + " · 生成于 " + it.asof_full : it.name + " · 未生成";
  b.innerHTML = '<span class="ico">' + it.icon + "</span>" +
    '<span class="txt"><span class="nm">' + it.name + "</span>" +
    '<span class="ds">' + it.desc + "</span></span>" +
    '<span class="side"><span class="dot ' +
    (it.exists ? (it.fresh ? "fresh" : "stale") : "none") + '"></span>' +
    '<span class="asof">' + it.asof + "</span></span>";
  b.onclick = () => select(it.key);
  nav.appendChild(b);
});

// 初始: 上次选择 → 第一个已生成 → 第一个
let last = null;
try { last = localStorage.getItem(LS_LAST); } catch (e) {}
select(ITEMS.some(x => x.key === last) ? last : (ITEMS.find(x => x.exists) || ITEMS[0]).key);

// 壳自身深浅色(各看板内仍有自己的切换)
try { if (localStorage.getItem(LS_THEME) === "dark") root.dataset.theme = "dark"; } catch (e) {}
const tb = document.getElementById("theme");
const paintTb = () => { tb.innerHTML = (root.dataset.theme === "dark" ? "☀️ " : "🌙 ") +
  '<span class="tbw">' + (root.dataset.theme === "dark" ? "浅色" : "深色") + "</span>"; };
tb.onclick = () => {
  root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
  try { localStorage.setItem(LS_THEME, root.dataset.theme); } catch (e) {}
  paintTb();
};
paintTb();

// 左侧导航可收缩: 图标轨模式(52px;文字隐·新鲜度点转角标·悬停 title 带名称时间), Ctrl/⌘+B 快捷
const LS_COLLAPSED = "sa_home_collapsed";
const asideEl = document.querySelector("aside"), cb = document.getElementById("collapse");
const paintCb = () => { cb.textContent = asideEl.classList.contains("mini") ? "»" : "«"; };
const setCollapsed = m => {
  asideEl.classList.toggle("mini", m);
  try { localStorage.setItem(LS_COLLAPSED, m ? "1" : "0"); } catch (e) {}
  paintCb();
};
cb.onclick = () => setCollapsed(!asideEl.classList.contains("mini"));
document.addEventListener("keydown", e => {
  if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) { e.preventDefault(); cb.click(); }
});
try { if (localStorage.getItem(LS_COLLAPSED) === "1") setCollapsed(true); } catch (e) {}
paintCb();
</script>
</body>
</html>
"""


def render_home(data_dir: Path, out_path: Path | None = None) -> Path:
    """渲染壳页 data/index.html。无数据依赖(只读各看板 HTML 的 mtime), 秒级。"""
    out_path = out_path or data_dir / "index.html"
    html = (_TEMPLATE
            .replace("__ITEMS_JSON__", json.dumps(nav_entries(data_dir), ensure_ascii=False))
            .replace("__GEN_AT__", datetime.now().strftime("%Y-%m-%d %H:%M")))
    out_path.write_text(html, encoding="utf-8")
    return out_path
