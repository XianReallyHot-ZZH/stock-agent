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
    "实际利率10Y": "yield", "通胀预期10Y": "yield", "期限溢价10Y": "yield",
    "2s10s": "curve",
    "美元指数": "price", "黄金": "price", "白银": "price", "铜": "price",
    "原油": "price", "标普500": "price", "纳斯达克": "price", "道琼斯": "price", "A股": "price",
}

# 因果链节点 (顺序 = 框架传导方向)
CHAIN_NODES: list[tuple[str, list[str]]] = [
    ("利率 · 美债收益率 / 实际利率 / 期限溢价", ["美债2Y", "美债10Y", "美债30Y", "实际利率10Y", "通胀预期10Y", "期限溢价10Y"]),
    ("曲线 · 2s10s", ["2s10s"]),
    ("美元 · DXY", ["美元指数"]),
    ("金属", ["黄金", "白银", "铜"]),
    ("能源", ["原油"]),
    ("权益", ["标普500", "纳斯达克", "道琼斯", "A股"]),
]

# 时序图默认渲染窗口(日频 bar ≈ 6y), 供下方 rangeselector 按钮导航(可缩到 1m/3m/6m/1y/3y)
_CHART_WIN = 1500


def _xaxis_ranges(c: dict, hover_fmt: str = "%Y-%m-%d") -> dict:
    """x 轴配置: 日期轴 + range 按钮(1m/3m/6m/1y/3y/all) + 底部 range slider(可拖拽改观察窗口)。
    hover_fmt: 悬停日期格式(日频默认 %Y-%m-%d; 月频传 %Y-%m 避免显示无意义的 -01)。"""
    return dict(
        type="date", tickformat="%Y-%m", hoverformat=hover_fmt, gridcolor=c["border"],
        rangeselector=dict(
            buttons=[
                dict(count=1, label="1m", step="month", stepmode="backward"),
                dict(count=3, label="3m", step="month", stepmode="backward"),
                dict(count=6, label="6m", step="month", stepmode="backward"),
                dict(count=1, label="1y", step="year", stepmode="backward"),
                dict(count=3, label="3y", step="year", stepmode="backward"),
                dict(label="all", step="all"),
            ],
            bgcolor=c["surface"], activecolor=c["accent"],
            font=dict(size=10, color=c["ink"]), x=0, xanchor="left"),
        rangeslider=dict(visible=True, thickness=0.04),
    )


# 单图放大悬浮框(modal)的 CSS + JS —— 普通 string(f-string 不再二次解析括号)
_MODAL_CSS = """
.chart-modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;align-items:center;justify-content:center}
.chart-modal.open{display:flex}
.chart-modal-box{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:30px 14px 14px;width:min(94vw,1500px);max-height:92vh;overflow:auto;position:relative}
.chart-modal-close{position:absolute;top:8px;right:12px;cursor:pointer;background:var(--border);color:var(--ink);border:none;border-radius:6px;padding:4px 12px;font-size:13px;z-index:5}
.expand-btn{position:absolute;top:4px;right:8px;z-index:1002;cursor:pointer;background:var(--surface);color:var(--ink2);border:1px solid var(--border);border-radius:6px;padding:2px 8px;font-size:11px}
.expand-btn:hover{color:var(--accent);border-color:var(--accent)}
"""

_MODAL_JS = """
<script>
(function(){
  var m=document.createElement('div');m.className='chart-modal';
  m.innerHTML='<div class="chart-modal-box"><button class="chart-modal-close" type="button">\\u2715 关闭</button><div id="chartModalPlot"></div></div>';
  document.body.appendChild(m);
  function _close(){m.classList.remove('open');try{Plotly.purge('chartModalPlot');}catch(e){}}
  m.querySelector('.chart-modal-close').onclick=_close;
  m.addEventListener('click',function(e){if(e.target===m){_close();}});
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&m.classList.contains('open')){_close();}});
  window.expandChart=function(srcId){
    var src=document.getElementById(srcId);
    if(!src||!src.data){return;}
    m.classList.add('open');
    var h=Math.max(440, window.innerHeight*0.78);
    Plotly.newPlot('chartModalPlot', src.data,
      Object.assign({}, src.layout, {height:h, autosize:true, margin:{l:70,r:60,t:30,b:50}}),
      {responsive:true, displaylogo:false, scrollZoom:true});
  };
  function inject(){
    document.querySelectorAll('.plotly-graph-div').forEach(function(gd){
      if(gd.querySelector('.expand-btn')){return;}
      if(!gd.style.position){gd.style.position='relative';}
      var b=document.createElement('button');b.className='expand-btn';b.type='button';b.textContent='\\u{1F50D} 展开';
      b.onclick=function(ev){ev.preventDefault();ev.stopPropagation();window.expandChart(gd.id);};
      gd.appendChild(b);
    });
  }
  window.addEventListener('load',function(){inject();setTimeout(inject,800);setTimeout(inject,2500);});
})();
</script>
"""


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
    n = min(_CHART_WIN, len(s))
    g = s.iloc[-n:]
    ma60 = ma_series(s, MA_PERIOD).iloc[-n:]
    devp = _rolling_dev_pct(deviation_series(s, MA_PERIOD), DEV_LOOKBACK).iloc[-n:]
    dt = pd.to_datetime(list(g.index))
    label_col = _label_color(an.get("label", ""), c)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in g.values], mode="lines", name=asset,
                             line=dict(color=c["accent"], width=1.4),
                             hovertemplate="%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) for v in ma60.values], mode="lines", name="MA60",
                             line=dict(color=c["ink2"], width=1.0),
                             hovertemplate="%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=dt, y=[float(v) if v == v else None for v in devp.values],
                             mode="lines", name="偏离分位", yaxis="y2",
                             line=dict(color=c["open"], width=1.0, dash="dot"),
                             fill="tozeroy", hovertemplate="%{y:.2f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[dt[-1]], y=[float(g.iloc[-1])], mode="markers",
                             marker=dict(size=12, color=label_col, symbol="diamond",
                                         line=dict(width=1.5, color="#ffffff")), hoverinfo="skip"))
    for y0, y1, col in [(0.0, DEV_LOW, c["edge"]), (DEV_HIGH, 1.0, c["miss"])]:
        fig.add_shape(type="rect", xref="paper", yref="y2", x0=0, x1=1, y0=y0, y1=y1,
                      fillcolor=col, opacity=0.10, line_width=0, layer="below")
    from .dashboard import ASSET_YLABEL
    ylabel = ASSET_YLABEL.get(asset, asset)   # 左轴: 资产单位(价/收益率/指数...)
    fig.update_layout(margin=dict(l=64, r=50, t=8, b=28), height=240, showlegend=False,
                      hovermode="x unified",
                      paper_bgcolor=c["surface"], plot_bgcolor=c["bg"],
                      font=dict(size=11, color=c["ink"]),
                      xaxis=_xaxis_ranges(c),
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


def _gold_micro_block(store, c: dict, first=None) -> str:
    """🔬 黄金微观紧缺(L2/L3/L4 实证): COMEX库存 / CFTC非商业(投机)净仓位+分位 / 央行购金节奏。
    JZ 框架的微观证据层: 库存↓=紧缺, 投机净多单极值=泡沫预警, 央行购金=底的锚。
    first=[bool] 共享标志(由首个图块嵌入 plotly.js, 本块复用)。"""
    if first is None:
        first = [True]
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
        val = cb["value"].astype(float)
        cur = float(val.iloc[-1])
        prev = float(val.iloc[-2]) if len(val) > 1 else cur
        delta = cur - prev                              # 万盎司/月(实物净购金)
        chips.append(chip("央行(中国)黄金", f"{cur:.0f}万oz", f"近月净购 {delta:+.0f}万oz(≈{delta*0.0311:+.1f}吨)", c["accent"]))
    chips.append('</div>')
    parts = [f'<div class="chart"><div class="chart-t">🔬 黄金微观紧缺 '
             f'<span class="muted">(L2库存/L3紧缺/L4投机泡沫 · JZ 框架微观证据 · GOFO/全球ETF无源待补)</span></div>',
             "".join(chips)]

    def _embed(fig, div_id):
        h = fig.to_html(include_plotlyjs=first[0], full_html=False, div_id=div_id)
        first[0] = False
        return h

    # 图: COMEX库存 (近2y)
    if not ci.empty:
        s = ci.iloc[-_CHART_WIN:]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pd.to_datetime(list(s.index)), y=[float(v) for v in s.values],
                                 mode="lines", name="COMEX金库存", line=dict(color=c["accent"], width=1.4),
                                 fill="tozeroy", hovertemplate="%{x|%Y-%m-%d}  %{y:.0f}吨<extra></extra>"))
        fig.update_layout(margin=dict(l=56, r=16, t=6, b=24), height=180, showlegend=False,
                          paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                          xaxis=_xaxis_ranges(c),
                          yaxis=dict(title="吨", gridcolor=c["border"]))
        parts.append('<div class="chart-t" style="margin-top:8px">COMEX 黄金库存(吨 · ↓=紧缺/逼空压力)</div>' + _embed(fig, "micro_comex"))

    # 图: CFTC 投机净仓位(净多) + 商业净仓位(净空, 镜像) + 泡沫分位带 (近6y)
    if not cf.empty:
        net = cf["net_pos"].astype(float).iloc[-312:]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=pd.to_datetime(list(net.index)), y=[float(v) for v in net.values],
                                 mode="lines", name="投机(净多)", line=dict(color=c["hit"], width=1.4),
                                 hovertemplate="%{y:,.0f}<extra></extra>"))
        cfm = store.get_cftc_position("GC_M")
        if not cfm.empty:
            nm = cfm["net_pos"].astype(float).iloc[-312:]
            fig.add_trace(go.Scatter(x=pd.to_datetime(list(nm.index)), y=[float(v) for v in nm.values],
                                     mode="lines", name="商业(净空)", line=dict(color=c["miss"], width=1.2, dash="dot"),
                                     hovertemplate="%{y:,.0f}<extra></extra>"))
        hi = float(cf["net_pos"].astype(float).iloc[-312:].quantile(0.85)) if len(cf) > 10 else 0
        fig.add_hline(y=hi, line_dash="dot", line_color=c["miss"], annotation_text="投机85%分位(泡沫区)", annotation_font_size=9)
        fig.update_layout(margin=dict(l=56, r=16, t=6, b=24), height=200, showlegend=True,
                          hovermode="x unified",
                          legend=dict(orientation="h", y=1.02, x=0, font=dict(size=9)),
                          paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                          xaxis=_xaxis_ranges(c),
                          yaxis=dict(title="净仓位(手)", gridcolor=c["border"]))
        parts.append('<div class="chart-t" style="margin-top:8px">CFTC 持仓: 投机净多(金,↑极值=泡沫) vs 商业净空(红,空单极小=逼空前兆)</div>' + _embed(fig, "micro_cftc"))

    # 图: 央行购金 (中国, 月频, 近10y): 柱=存量(万oz) / 线=月环比%
    if not cb.empty:
        sub = cb.iloc[-120:]
        vals = sub["value"].astype(float)
        mom = (vals.pct_change() * 100.0)         # 月环比增减%(=净购金节奏)
        dtm = pd.to_datetime(list(sub.index))
        fig = go.Figure()
        fig.add_trace(go.Bar(x=dtm, y=vals.values, name="黄金储备(万oz)",
                             marker_color=c["accent"], yaxis="y",
                             hovertemplate="%{y:.0f}<extra></extra>"))
        fig.add_trace(go.Scatter(x=dtm, y=mom.values, name="环比%", mode="lines+markers",
                                 yaxis="y2", line=dict(color=c["hit"], width=1.4),
                                 marker=dict(size=4),
                                 hovertemplate="%{y:+.2f}%<extra></extra>"))
        fig.update_layout(margin=dict(l=56, r=52, t=6, b=24), height=220, showlegend=True,
                          hovermode="x unified",
                          legend=dict(orientation="h", y=1.08, x=0, font=dict(size=9)),
                          paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                          xaxis=_xaxis_ranges(c, "%Y-%m"),
                          yaxis=dict(title="万盎司", gridcolor=c["border"]),
                          yaxis2=dict(overlaying="y", side="right", title="环比%", showgrid=False))
        parts.append('<div class="chart-t" style="margin-top:8px">中国央行黄金储备(柱=存量万oz / 线=月环比% · 持续增持=底的锚)</div>' + _embed(fig, "micro_cb"))
    else:
        # cb 数据暂缺(sina jsonp 端点偶发被拦): 占位标题+说明, 不再静默漏图
        parts.append('<div class="chart-t" style="margin-top:8px">中国央行黄金储备(实物万oz · 持续增持=底的锚)</div>'
                     '<div class="muted" style="padding:10px 0">央行购金数据暂缺'
                     '(sina 端点偶发被拦,重跑 <code>python scripts/backfill_gold_micro.py</code> 即可恢复)。</div>')
    parts.append('</div>')
    return "".join(parts)


def _gold_vs_chart(store, pairs: list, ylabel_right: str, c: dict, div_id: str, win: int = _CHART_WIN):
    """黄金(左轴) vs 若干指标(右轴) 双Y轴对比图。pairs=[(label, asset_key, color), ...]。
    看反向/同向/背离(黄金vs10Y对手盘、vs DXY反向、vs 2s10s陡峭化利好)。"""
    gold = series_for("黄金", store)
    if gold is None or len(gold) == 0:
        return None
    gold = gold.sort_index().iloc[-win:]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pd.to_datetime(list(gold.index)), y=[float(v) for v in gold.values],
                             name="黄金", line=dict(color=c["accent"], width=1.5),
                             hovertemplate="%{y:.0f}<extra></extra>"))
    for label, asset, col in pairs:
        s = series_for(asset, store)
        if s is None or len(s) == 0:
            continue
        s = s.sort_index().iloc[-win:]
        fig.add_trace(go.Scatter(x=pd.to_datetime(list(s.index)), y=[float(v) for v in s.values],
                                 name=label, line=dict(color=col, width=1.2), yaxis="y2",
                                 hovertemplate="%{y:.2f}<extra></extra>"))
    fig.update_layout(margin=dict(l=56, r=56, t=4, b=22), height=220, showlegend=True,
                      hovermode="x unified",
                      legend=dict(orientation="h", y=1.08, x=0, font=dict(size=9)),
                      paper_bgcolor=c["surface"], plot_bgcolor=c["bg"], font=dict(size=10, color=c["ink"]),
                      xaxis=_xaxis_ranges(c),
                      yaxis=dict(title=dict(text="黄金(USD)", font=dict(size=9)), gridcolor=c["border"]),
                      yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                  title=dict(text=ylabel_right, font=dict(size=9))))
    return fig


def _gold_locator_block(store, c: dict, first=None) -> str:
    """🥇 黄金阶段定位器 v2: 价格结构+微观紧缺+利率美元驱动 → 阶段+驱动三栏+置信度+操作建议。
    first=[bool] 共享标志: 本块在源文最前 → 首图(金价)嵌入 plotly.js,其后图复用。"""
    if first is None:
        first = [True]
    from .gold_locator import gold_locate
    r = gold_locate(store)
    if not r.get("valid"):
        return ('<div class="chart"><div class="chart-t">🥇 黄金阶段定位器</div>'
                '<div class="muted">黄金数据不足。</div></div>')

    stage_col = (c["miss"] if "泡沫" in r["stage_note"] else
                 {"筑底/底部震荡": c["edge"], "反弹初期": c["edge"], "趋势上行": c["accent"],
                  "头部区域": c["hit"], "回调下跌": c["miss"]}.get(r["stage_note"], c["accent"]))
    conf_txt = f"{r['confidence']*100:.0f}%·{r['confidence_band']}"
    regime_col = c["accent"] if r["bull_intact"] else c["ink2"]

    def chip(label, val, sub="", color=None):
        vs = f' style="color:{color}"' if color else ""
        return (f'<div class="card"><div class="card-v"{vs}>{val}</div><div class="card-l">{label}</div>'
                f'{f"<div class=card-s>{sub}</div>" if sub else ""}</div>')

    head = ('<div class="cards">'
            + chip("当前阶段", r["stage_note"], "价格结构+驱动精炼", stage_col)
            + chip("底层", r["regime"], "央行+期限溢价+实际利率(1年)", regime_col)
            + chip("信心", conf_txt, "阶段强度×驱动一致×regime")
            + '</div>')

    def flag_col(f):
        if f in ("利好", "紧缺", "逼空"):
            return c["edge"]
        if f in ("逆风", "泡沫"):
            return c["miss"]
        return c["ink2"]

    def col(title, drvs):
        rows = "".join(
            f'<div style="display:flex;justify-content:space-between;gap:8px;padding:4px 0;'
            f'border-bottom:1px solid {c["border"]};font-size:12px">'
            f'<span class="muted">{d["label"]}</span>'
            f'<span><b>{d["value"]}</b> <span style="color:{flag_col(d["flag"])}">{d["dir"]} {d["flag"]}</span></span>'
            f'</div>' for d in drvs if d)
        return f'<div style="flex:1;min-width:220px"><div class="chart-t">{title}</div>{rows}</div>'

    panel = ('<div style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0">'
             + col("底层 · 牛市在否(L1)", r["drivers"]["底层"])
             + col("中期 · 方向(L2)", r["drivers"]["中期"])
             + col("短期 · 位置(L3)", r["drivers"]["短期"])
             + '</div>')

    warns = "".join(f'<li style="font-size:12px;color:{c["miss"]}">{w}</li>' for w in r["warnings"])
    warns = f'<ul style="margin:6px 0;padding-left:18px">{warns}</ul>' if warns else ""
    action = f'<div class="banner info" style="margin:8px 0">📋 <b>操作建议</b>: {r["action"]}</div>'

    price_html = ""
    if series_for("黄金", store) is not None:
        fig = _asset_chart("黄金", store, asset_analysis("黄金", store), c)
        if fig is not None:
            price_html = fig.to_html(include_plotlyjs=first[0], full_html=False, div_id="locator_gold")
            first[0] = False

    # 黄金 vs 关键驱动 双Y轴对比图(看反向/背离)
    def _vs(pairs, title, yright, div):
        fig = _gold_vs_chart(store, pairs, yright, c, div)
        if fig is None:
            return ""
        h = fig.to_html(include_plotlyjs=first[0], full_html=False, div_id=div)
        first[0] = False
        return f'<div class="chart-t" style="margin-top:10px">{title}</div>' + h

    vs_html = (
        _vs([("2Y", "美债2Y", c["ink2"]), ("10Y", "美债10Y", c["miss"])],
            "黄金 vs 美债收益率(对手盘·通常反向; 同涨=框架脱钩)", "收益率 %", "vs_rates")
        + _vs([("DXY", "美元指数", c["miss"])], "黄金 vs 美元指数 DXY(反向)", "DXY", "vs_dxy")
        + _vs([("2s10s", "2s10s", c["edge"])], "黄金 vs 2s10s 利差(陡峭化↑=利好)", "利差", "vs_curve"))

    return ('<div class="chart"><div class="chart-t">🥇 黄金阶段定位器 '
            '<span class="muted">(价格结构+微观紧缺+利率美元驱动 · 框架四层判定 · 只读不喂引擎 ADR-0001)</span></div>'
            + head + panel + action + warns + price_html + vs_html + '</div>')


# 经济日历: 黄金框架相关关键词(利率/数据/美债/Fed)
_CAL_KEYWORDS = ("利率", "Fed", "美联储", "联邦基金", "FOMC", "会议纪要", "点阵图", "褐皮书", "加息", "降息",
                 "CPI", "PCE", "核心", "非农", "就业", "ADP", "失业", "JOLTs", "PMI", "GDP", "零售", "通胀", "PPI",
                 "国债竞拍", "资产负债表", "扩表", "缩表", "赤字", "预算", "消费者信心", "工厂订单", "耐用品", "贸易帐")

# 事件 → 对黄金的影响 (关键词, actual↑影响, actual↓影响); hi==lo=非方向性(条件化全文)
# 按 JZ 框架: 利率↑=逆风, 通胀/就业强=加息压力=利空, 通胀/就业弱=降息=利好, 扩表=印钱=利好
_CAL_IMPACT = [
    (["FOMC", "利率决议", "联邦基金利率"], "降息=利好 / 加息=利空", "降息=利好 / 加息=利空"),
    (["会议纪要", "点阵图", "褐皮书"], "鸽派=利好 / 鹰派=利空", "鸽派=利好 / 鹰派=利空"),
    (["核心CPI", "核心PCE", "CPI", "PCE", "PPI", "通胀"], "加息压力·利空", "降息·利好"),
    (["非农", "ADP", "就业人口", "私营企业"], "走强·利空", "降息·利好"),
    (["失业率", "初请", "续请"], "降息·利好", "利空"),       # 失业↑=降息=利好(反向)
    (["JOLTs", "职位空缺"], "利空", "降温·利好"),
    (["PMI"], "走强·利空", "避险·利好"),
    (["GDP"], "利空", "降息·利好"),
    (["零售"], "利空", "利好"),
    (["资产负债表"], "扩表·利好", "缩表·利空"),             # 余额↑=扩表=利好(反向)
    (["赤字", "预算"], "赤字扩=债务压力·长期利好", "赤字扩=债务压力·长期利好"),
    (["贸易帐"], "逆差扩=美元压力·间接利好", "逆差扩=美元压力·间接利好"),
    (["耐用品", "工厂订单", "工业"], "利空", "利好"),
    (["消费者信心"], "利空", "避险·利好"),
    (["国债竞拍"], "需求弱(收益率↑/倍数↓)·利好 / 强·利空", "需求弱(收益率↑/倍数↓)·利好 / 强·利空"),
]


def _impact_parts(event: str):
    """→ (high_text, low_text) 或 None。high=actual>forecast 的影响。"""
    for kws, hi, lo in _CAL_IMPACT:
        if any(k in event for k in kws):
            return (hi, lo)
    return None


def _color_hint(hint: str, c: dict) -> str:
    """利好着绿、利空着红, 方便扫读。"""
    return (hint.replace("利好", f'<span style="color:{c["edge"]}">利好</span>')
                .replace("利空", f'<span style="color:{c["miss"]}">利空</span>'))


def _released_impact(event: str, actual, forecast, c: dict) -> str:
    """已公布: 按 surprise 方向(actual vs forecast)给命中半句结论; 无法判方向→条件化全文。"""
    parts = _impact_parts(event)
    if not parts:
        return ""
    hi, lo = parts
    if hi == lo:
        return _color_hint(hi, c)                          # 非方向性指标(FOMC/赤字等)
    if actual is None or actual != actual or forecast is None or forecast != forecast:
        return _color_hint(f"{hi} / {lo}", c)              # 无 forecast → 给双向条件
    if actual > forecast:
        return _color_hint(hi, c)
    if actual < forecast:
        return _color_hint(lo, c)
    return "符合预期"


def _upcoming_impact(event: str, c: dict) -> str:
    """未公布: 给双向条件(高/低各自影响)。"""
    parts = _impact_parts(event)
    if not parts:
        return ""
    hi, lo = parts
    return _color_hint(hi, c) if hi == lo else _color_hint(f"{hi} / {lo}", c)




def _economic_calendar_block(store, c: dict, asof: str) -> str:
    """📰 经济日历/事件: 近期已公布美国高重要性数据(公布vs预期=surprise) + 未来 FOMC/CPI/非农 时点。"""
    from datetime import date, timedelta
    today = (asof or date.today().isoformat())[:10]
    try:
        t0 = date.fromisoformat(today)
    except ValueError:
        t0 = date.today()
    back = (t0 - timedelta(days=7)).isoformat()
    fwd = (t0 + timedelta(days=45)).isoformat()
    df = store.get_economic_calendar(region="美国", since=back, until=fwd, min_importance=2)
    if df.empty:
        return ('<div class="chart"><div class="chart-t">📰 经济日历</div>'
                '<div class="muted">无数据。先跑 <code>python scripts/backfill_economic_calendar.py</code>。</div></div>')
    df = df[df["event"].fillna("").str.contains("|".join(_CAL_KEYWORDS), regex=True)].copy()
    if df.empty:
        return ('<div class="chart"><div class="chart-t">📰 经济日历</div>'
                '<div class="muted">近 7 天/未来窗口内无美国高重要性相关事件。</div></div>')

    def stars(n):
        try:
            return "★" * int(n)
        except Exception:  # noqa: BLE001
            return ""

    def cell_actual(a, f):
        if a is None or a != a:
            return "—"
        if f is None or f != f:
            return f"{a:g}"
        d = a - f
        col = c["miss"] if d > 1e-9 else (c["edge"] if d < -1e-9 else c["ink2"])
        tag = " ↑高于" if d > 1e-9 else (" ↓低于" if d < -1e-9 else "")
        return f'<span style="color:{col}"><b>{a:g}</b>{tag}</span>'

    # 近期已公布(公布值存在)
    rel = df[df["actual"].notna()].sort_values("date", ascending=False).head(12)
    rows_r = "".join(
        f"<tr><td class='muted'>{r['date']}</td><td class='stmt'>{r['event']}</td>"
        f"<td>{cell_actual(r['actual'], r['forecast'])}</td>"
        f"<td class='muted'>{r['forecast'] if r['forecast']==r['forecast'] else '—'}</td>"
        f"<td class='muted'>{r['previous'] if r['previous']==r['previous'] else '—'}</td>"
        f"<td class='muted' style='font-size:11px'>{_released_impact(r['event'], r['actual'], r['forecast'], c)}</td></tr>"
        for _, r in rel.iterrows())
    tbl_r = (('<div class="chart-t">已公布(近7天 · 粗体=公布值 · 影响=按实际方向给结论)</div>'
              '<table><tr><th>日期</th><th>事件</th><th>公布</th><th>预期</th><th>前值</th><th>对黄金影响</th></tr>'
              + rows_r + '</table>') if len(rel) else '<div class="muted">无近期已公布。</div>')

    # 未来排期(公布值缺): 不截行——head(15) 曾把 FOMC/非农/CPI 砍掉(2026-08 发现),
    # 全量展示到源排期上限; 日历源(百度)通常只给约未来30天, 45天是请求窗口非保证。
    up = df[df["actual"].isna()].sort_values(["date", "time"])
    rows_u = "".join(
        f"<tr><td class='muted'>{r['date']} {r['time'] or ''}</td><td class='stmt'>{r['event']}</td>"
        f"<td class='muted'>{r['forecast'] if r['forecast']==r['forecast'] else '—'}</td>"
        f"<td class='muted'>{r['previous'] if r['previous']==r['previous'] else '—'}</td>"
        f"<td>{stars(r['importance'])}</td>"
        f"<td class='muted' style='font-size:11px'>{_upcoming_impact(r['event'], c)}</td></tr>"
        for _, r in up.iterrows())
    horizon = up["date"].max() if len(up) else ""
    tbl_u = ((f'<div class="chart-t" style="margin-top:10px">即将公布(催化剂时点 · 决定埋伏时机 · '
              f'共{len(up)}条 · 排期至 {horizon}，源通常只给约未来30天)</div>'
              '<table><tr><th>时间</th><th>事件</th><th>预期</th><th>前值</th><th>重要性</th><th>对黄金影响</th></tr>'
              + rows_u + '</table>') if len(up) else '<div class="muted">无未来排期。</div>')

    return ('<div class="chart"><div class="chart-t">📰 经济日历 / 事件 '
            '<span class="muted">(美国 · 重要性≥2 · 利率/通胀/就业/美债/Fed · 数据真伪+催化剂时点)</span></div>'
            + tbl_r + tbl_u
            + '<div class="muted" style="font-size:11px;margin-top:6px">↑高于/↓低于=公布 vs 预期(surprise); '
            '方向对黄金的影响因指标而异(如 CPI 超预期=加息压力=短线利空, 非农走弱=降息预期=利好)。</div></div>')


def render_macro_framework(store, out_path: Path, asof: str = "") -> Path:
    """渲染宏观框架看板 → data/macro_framework.html (纯数据·只读·不喂引擎)。"""
    from .dashboard import _DARK, _LIGHT, _css, _driver_svg

    D, L = _DARK, _LIGHT
    all_assets = [a for _, assets in CHAIN_NODES for a in assets]
    an_by_asset = {a: asset_analysis(a, store) for a in all_assets}

    # plotly.js 只嵌一次: 按源文顺序, 首个图块(locator 金价图)嵌入, nodes/micro 复用
    first = [True]
    locator_html = _gold_locator_block(store, L, first)

    # 分节点图 (统一普通数据跟踪展示)
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
                         + fig.to_html(include_plotlyjs=first[0], full_html=False, div_id=div_id)
                         + '</div>')
            first[0] = False
        node_html.append("\n".join(parts))

    n_valid = sum(1 for an in an_by_asset.values() if an.get("valid"))
    gold_micro_html = _gold_micro_block(store, L, first)   # 源文在 nodes 之后
    calendar_html = _economic_calendar_block(store, L, asof)
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
{_MODAL_CSS}
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

<h2>🥇 黄金阶段定位器(旗舰 · 价格结构+微观紧缺+利率美元驱动 → 阶段+驱动+置信度+操作建议)</h2>
{locator_html}

{''.join(node_html)}

<h2>🔬 黄金微观紧缺(L2/L3/L4 实证 · JZ 框架微观证据)</h2>
{gold_micro_html}

<h2>📰 经济日历 / 事件(美国高重要性 · 数据真伪 + 催化剂时点 · backfill_economic_calendar.py)</h2>
{calendar_html}

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
</script>{_MODAL_JS}</body></html>"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
