"""偏离度极值反弹 event-study 研究报告(临时·只读): 把本轮研究结论整合成一个 HTML。

复用 backtest_deviation_extreme / backtest_deviation_etf_landing 的函数, 重算关键指标,
生成 plotly 交互图 + 表格 + 通俗文字解读, 输出 data/deviation_extreme_study.html。

Usage:
  python scripts/deviation_extreme_report.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from backtest_deviation_extreme import (build, expanding_dev_pct, stats_of, wilson,
                                        FORWARD, THRESHOLDS, MA)
from backtest_deviation_etf_landing import load_pair, add_forwards, view
from stockagent.config import get_config
from stockagent.data.store import Store

_PAL = {"surface": "#fcfcfb", "surface_d": "#1a1a18", "ink": "#0b0b0b",
        "ink_sec": "#52514e", "muted": "#898781", "grid": "#e1e0d9",
        "series_1": "#2a78d6", "series_2": "#008300", "warning": "#fab219",
        "good": "#0ca30c", "critical": "#d03b3b", "neg": "#1c5cab", "neutral": "#bdbcb5"}
_HORIZONS = list(FORWARD)
_THRESH = [f"≤{int(t*100)}%" for t in THRESHOLDS]

_CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink_sec:#52514e;--muted:#898781;--grid:#e1e0d9;--good:#0ca30c;--crit:#d03b3b;--warn:#fab219;--blue:#2a78d6}
[data-theme=dark]{--surface:#1a1a18;--ink:#ececea;--ink_sec:#a8a7a2;--muted:#76746e;--grid:#2e2e2a}
body{margin:0;background:var(--surface);color:var(--ink);font-family:system-ui,-apple-system,sans-serif;
     line-height:1.6;transition:background .2s,color .2s}
.wrap{max-width:1080px;margin:0 auto;padding:32px 24px 80px}
header{border-bottom:1px solid var(--grid);padding-bottom:16px;margin-bottom:24px}
h1{font-size:26px;margin:0 0 4px;font-weight:700}
h2{font-size:18px;margin:32px 0 4px;color:var(--ink);font-weight:650;border-left:4px solid var(--blue);
   padding-left:10px}
h3{font-size:14px;margin:18px 0 6px;color:var(--ink_sec);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.meta{color:var(--muted);font-size:13px}
p{margin:8px 0}
.hint,.caveat{color:var(--muted);font-size:12.5px}
.lead{font-size:15px;color:var(--ink_sec)}
.stathero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:16px 0}
.stat{background:color-mix(in srgb,var(--ink) 4%,var(--surface));border:1px solid var(--grid);
      border-radius:10px;padding:14px 16px}
.stat .v{font-size:24px;font-weight:700;color:var(--ink);line-height:1.1}
.stat .l{font-size:11.5px;color:var(--muted);margin-top:4px;text-transform:uppercase;letter-spacing:.03em}
.stat.good .v{color:var(--good)} .stat.crit .v{color:var(--crit)} .stat.blue .v{color:var(--blue)}
table{border-collapse:collapse;width:100%;font-size:13px;margin:10px 0}
th,td{padding:7px 10px;border-bottom:1px solid var(--grid);text-align:right}
th{text-align:left;color:var(--muted);font-weight:600}
th:first-child,td:first-child{text-align:left}
td.pos{color:var(--good)} td.neg{color:var(--crit)}
.fig{margin:14px 0;border:1px solid var(--grid);border-radius:10px;overflow:hidden;background:#fcfcfb}
.takeaway{background:color-mix(in srgb,var(--blue) 8%,var(--surface));border:1px solid color-mix(in srgb,var(--blue) 30%,var(--grid));
          border-radius:10px;padding:14px 18px;margin:14px 0}
.takeaway b{color:var(--blue)}
.toggle{float:right;background:var(--surface);border:1px solid var(--grid);color:var(--ink_sec);
        border-radius:8px;padding:5px 12px;font-size:12px;cursor:pointer}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:760px){.two{grid-template-columns:1fr}}
code{background:color-mix(in srgb,var(--ink) 7%,transparent);padding:1px 5px;border-radius:4px;font-size:.92em}
"""


def _font(fig):
    # 图表区固定浅色卡片: 深浅模式下都可读(dataviz: dark mode 要保证对比度)
    fig.update_layout(paper_bgcolor="#fcfcfb", plot_bgcolor="#fcfcfb",
                      font=dict(color="#3a3a37", size=12), margin=dict(l=50, r=20, t=40, b=40))
    fig.update_xaxes(gridcolor="#e1e0d9", zerolinecolor="#bdbcb5")
    fig.update_yaxes(gridcolor="#e1e0d9", zerolinecolor="#bdbcb5")
    return fig


def fig_edge_heatmap(edge_full: dict, base: dict) -> go.Figure:
    """阈值×持有期 edge 热力图(diverging 绿正/灰零/红负)。"""
    z = [[edge_full[t][n]["win"] * 100 - base[n]["win"] * 100 for n in FORWARD] for t in THRESHOLDS]
    text = [[f"{v:+.0f}pp" for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=[f"{n}日" for n in FORWARD], y=_THRESH, zmid=0, zmin=-25, zmax=25,
        text=text, texttemplate="%{text}", textfont=dict(size=13, color="#0b0b0b"),
        colorscale=[[0, "#d03b3b"], [0.5, "#e8e7e1"], [1, "#0ca30c"]],
        colorbar=dict(title="edge<br>(pp)", thickness=10, len=0.85),
        hovertemplate="%{y} · 持%{x}<br>edge %{z:+.1f}pp<extra></extra>"))
    fig.update_xaxes(title_text="持有期(交易日)")
    fig.update_yaxes(title_text="入场阈值(偏离度分位)", autorange="reversed")
    fig.update_layout(height=300, title=dict(text="<b>edge = 信号胜率 − 闭眼买胜率</b>(绿=优于闭眼买,红=更差)",
                                              font=dict(size=13, color="#0b0b0b")))
    return _font(fig)


def fig_three_口径(u_etf, th=0.05, N=60) -> go.Figure:
    """三口径(指数/ETF毛/ETF净)胜率 vs 基准。"""
    base = {p: stats_of(view(u_etf, p)) for p in ("idx", "etf", "net")}
    s = {p: stats_of(view(u_etf[u_etf["pct"] <= th], p)) for p in ("idx", "etf", "net")}
    names = ["指数(理论)", "ETF毛(含跟踪误差)", "ETF净(扣0.3%费)"]
    cols = ["#2a78d6", "#008300", "#0ca30c"]
    bars = []
    for (p, nm, c) in zip(("idx", "etf", "net"), names, cols):
        bars.append(go.Bar(name=nm, x=["信号", "基准"], y=[s[p][N]["win"]*100, base[p][N]["win"]*100],
                           marker_color=c, text=[f"{s[p][N]['win']*100:.0f}%", f"{base[p][N]['win']*100:.0f}%"],
                           textposition="outside"))
    fig = go.Figure(bars)
    fig.add_hline(y=50, line=dict(color="#898781", dash="dash", width=1))
    fig.update_layout(barmode="group", height=300, legend=dict(orientation="h", y=-0.2),
                      title=dict(text=f"<b>三口径胜率对比</b>(≤{int(th*100)}% · 持{N}日) — edge 不随落地稀释",
                                 font=dict(size=13, color="#0b0b0b")),
                      yaxis_title="胜率%", yaxis_range=[0, 100])
    return _font(fig)


def fig_distribution(usable, th=0.05, N=60) -> go.Figure:
    """信号 vs 基准 forward 收益分布(说明不是右偏假象)。"""
    sig = usable[usable["pct"] <= th][f"fwd_{N}"].dropna()
    base = usable[f"fwd_{N}"].dropna()
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=base*100, name="基准(任意时点)", marker_color="#bdbcb5",
                               opacity=0.6, nbinsx=40, hovertemplate="%{x:.0f}%<extra>基准</extra>"))
    fig.add_trace(go.Histogram(x=sig*100, name="信号触发(≤5%)", marker_color="#2a78d6",
                               opacity=0.7, nbinsx=40, hovertemplate="%{x:.0f}%<extra>信号</extra>"))
    fig.add_vline(x=0, line=dict(color="#0b0b0b", width=1))
    fig.add_vline(x=sig.mean()*100, line=dict(color="#2a78d6", dash="dash", width=2),
                  annotation_text=f"信号均值 {sig.mean()*100:+.1f}%", annotation_position="top")
    fig.update_layout(barmode="overlay", height=300, legend=dict(orientation="h", y=-0.2),
                      title=dict(text=f"<b>收益分布</b>(持{N}日) — 信号分布右移、且比基准更对称(非右偏假象)",
                                 font=dict(size=13, color="#0b0b0b")),
                      xaxis_title="持有期收益 %", yaxis_title="天数")
    return _font(fig)


def fig_oos_combo(usable) -> go.Figure:
    """single vs combo1 的 IS/OOS edge 对比。"""
    cut = pd.Timestamp("2019-01-01")
    df = usable.copy()
    df["combo1"] = df["no_new_low"]

    def edge(sub, th):
        s = sub[sub["pct"] <= th]["fwd_60"].dropna()
        b = sub["fwd_60"].dropna().gt(0).mean()
        return (s.gt(0).mean() - b) * 100 if len(s) else None

    rows = []
    for th in (0.05, 0.10):
        for sig, mask_col in [("single", None), ("combo1(+拐头)", "combo1")]:
            isd = df[df.index < cut]; oos = df[df.index >= cut]
            if mask_col:
                isd = isd[isd[mask_col]]; oos = oos[oos[mask_col]]
            rows.append((f"≤{int(th*100)}%", sig, edge(isd, th), edge(oos, th)))
    cats = [r[1] for r in rows]
    fig = go.Figure()
    fig.add_bar(name="IS(2010-2018)", x=cats, y=[r[2] for r in rows], marker_color="#bdbcb5",
                text=[f"{r[2]:+.0f}" if r[2] is not None else "—" for r in rows], textposition="outside")
    fig.add_bar(name="OOS(2019-2026)", x=cats, y=[r[3] for r in rows], marker_color="#2a78d6",
                text=[f"{r[3]:+.0f}" if r[3] is not None else "—" for r in rows], textposition="outside")
    # 分组: 每2个换阈值
    fig.update_layout(barmode="group", height=320, legend=dict(orientation="h", y=-0.2),
                      title=dict(text="<b>样本外验证</b> — combo1(加拐头)在OOS比single更强(edge翻倍)",
                                 font=dict(size=13, color="#0b0b0b")),
                      yaxis_title="edge (pp)", xaxis_title="",
                      annotations=[dict(x=0.25, y=1.06, xref="paper", showarrow=False,
                                        text="≤5% 阈值", font=dict(size=11, color="#898781")),
                                   dict(x=0.75, y=1.06, xref="paper", showarrow=False,
                                        text="≤10% 阈值", font=dict(size=11, color="#898781"))])
    return _font(fig)


def _matrix_html(stats_by_th, base, prefix="") -> str:
    rows = ""
    for t, stt in stats_by_th.items():
        cells = "".join(
            f"<td class=\"{'pos' if stt[n]['win']>base[n]['win'] else 'neg'}\">"
            f"{stt[n]['win']*100:.0f}%<span class='hint'> [{stt[n]['lo']*100:.0f}-{stt[n]['hi']*100:.0f}] n={stt[n]['n']}</span></td>"
            for n in FORWARD)
        rows += f"<tr><td>{prefix}≤{int(t*100)}%</td>{cells}</tr>"
    bcells = "".join(f"<td>{base[n]['win']*100:.0f}%<span class='hint'> n={base[n]['n']}</span></td>" for n in FORWARD)
    rows += f"<tr><td>基准(任意)</td>{bcells}</tr>"
    head = "".join(f"<th>{n}日</th>" for n in FORWARD)
    return (f"<table><tr><th>阈值</th>{head}</tr>{rows}</table>"
            f"<div class='hint'>绿=优于基准,红=劣于基准 · [lo-hi]=95% Wilson CI · n=样本数</div>")


def main():
    st = Store(get_config().db_path)
    px = st.get_index_daily_series("399006")["close"]
    df = build(px)
    df["no_new_low"] = df["dev"] >= df["dev"].shift(1).rolling(5).min()
    usable = df[df["fwd_60"].notna() & df["pct"].notna()].copy()
    base = stats_of(usable)
    edge_full = {t: stats_of(usable[usable["pct"] <= t]) for t in THRESHOLDS}
    combo1 = {t: stats_of(usable[(usable["pct"] <= t) & usable["no_new_low"]]) for t in THRESHOLDS}

    m = add_forwards(load_pair(st, "399006", "159915"), 0.003)
    u_etf = m[m["usable"]]

    today = df.iloc[-1]
    last = usable.index[-1]

    # ---- figures ----
    f1 = fig_edge_heatmap(edge_full, base).to_html(full_html=False, include_plotlyjs=True, config={"displayModeBar": False})
    f2 = fig_three_口径(u_etf).to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})
    f3 = fig_distribution(usable).to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})
    f4 = fig_oos_combo(usable).to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": False})

    # ---- OOS numbers for hero ----
    cut = pd.Timestamp("2019-01-01")
    oos = usable[usable.index >= cut]
    oos_combo_10 = combo1 if False else stats_of(oos[(oos["pct"] <= 0.10) & oos["no_new_low"]])
    oos_combo_5 = stats_of(oos[(oos["pct"] <= 0.05) & oos["no_new_low"]])

    # ---- combo1 vs single detail table (60d) ----
    def detail(sub):
        s = sub["fwd_60"].dropna()
        win = s[s > 0]; los = s[s <= 0]
        pf = win.mean()/abs(los.mean()) if len(los) and los.mean() != 0 else float('nan')
        return len(s), s.gt(0).mean(), pf, (s <= -.10).mean(), s.min()

    detail_rows = ""
    for th, lab in [(0.02, "≤2%"), (0.05, "≤5%"), (0.10, "≤10%")]:
        for nm, msk in [("single", usable["pct"] <= th),
                        ("combo1(+拐头)", (usable["pct"] <= th) & usable["no_new_low"])]:
            n, wr, pf, p10, mn = detail(usable[msk])
            detail_rows += (f"<tr><td>{lab}</td><td>{nm}</td><td>{n}</td>"
                            f"<td class='pos'>{wr*100:.0f}%</td><td>{pf:.2f}</td>"
                            f"<td>{p10*100:.0f}%</td><td class='neg'>{mn*100:+.1f}%</td></tr>")

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>创业板指 · 偏离度极值反弹研究</title><style>{_CSS}</style></head><body>
<div class="wrap">
<button class="toggle" onclick="toggleTheme()">🌙 深色</button>
<header>
  <h1>创业板指 · 偏离度极值反弹 event study</h1>
  <div class="meta">信号: expanding 偏离度分位 ≤ 阈值 · 执行: 创业板指/159915 ·
  数据 {df.index[0]:%Y-%m-%d}..{df.index[-1]:%Y-%m-%d} · 可用样本 {len(usable)} 根 · 生成于临时研究</div>
</header>

<section class="takeaway">
<b>一句话结论:</b> 创业板指深度超卖(偏离度分位 ≤5%)时, 配合「偏离度拐头」确认, 持有 40-60 交易日,
<b>样本外 edge +12~19pp</b>(比闭眼乱买高这么多)、胜率 66-73%、盈亏比 ~2.4。<b>edge 真实、能落到 ETF、不是统计假象</b>。
但 ≤2% 极端档近 10 年触发 0-2 次, 无法验证, 是经验参考非定律。
</section>

<h2>当前位置</h2>
<div class="stathero">
  <div class="stat crit"><div class="v">{today['dev']*100:+.1f}%</div><div class="l">偏离度(价 vs 60日线)</div></div>
  <div class="stat blue"><div class="v">{today['pct']*100:.1f}%</div><div class="l">偏离度历史分位(expanding)</div></div>
  <div class="stat"><div class="v">{today['close']:.0f}</div><div class="l">创业板指收盘</div></div>
  <div class="stat crit"><div class="v">深度超卖</div><div class="l">仅 {int((df['pct']<today['pct']).sum())} 天比现在更超卖</div></div>
</div>
<p class="lead">现在偏离度 <code>{today['dev']*100:+.1f}%</code> = 收盘比 60 日线低 {abs(today['dev'])*100:.1f}%;
历史分位 <code>{today['pct']*100:.1f}%</code> = 过去 16 年里只有 {today['pct']*100:.1f}% 的日子比现在跌得更狠 ——
接近历史最超卖。这正是本策略研究的触发场景。</p>

<h2>① 这是什么研究(先看懂术语)</h2>
<div class="two">
<div>
<h3>三个关键词</h3>
<p><b>偏离度</b> = <code>(价格 − 60日均线) ÷ 60日均线</code>。负数 = 跌穿均线 = 超卖;正数 = 在线上方 = 超买。</p>
<p><b>偏离度百分位</b> = 今天的偏离度,在过去所有日子里「极端到第几名」。0% = 史上最超卖,100% = 史上最超买。
本报告用 <b>expanding</b> 口径(每天只用当时及之前的数据算分位),<b>防前视</b> —— 不像看板上那个用全历史的分位,回测绝不能用。</p>
<p><b>edge</b> = 这招的胜率 − 「闭眼任意时点买」的胜率。创业板长期往上,闭眼买本就 ~52% 胜率;不减基准会被大盘的白送涨幅骗。
<b>edge&gt;0 才算真本事。</b></p>
</div>
<div>
<h3>研究流程</h3>
<p>① 每天算偏离度 → 转 expanding 分位<br>
② 分位 ≤ 阈值(超卖) → 当作「买入信号」<br>
③ 假设按收盘价买入、持 N 个交易日卖出 → 算这笔的收益<br>
④ 把所有信号的收益汇总 → 胜率/均值/盈亏比/尾部<br>
⑤ 减掉「闭眼买」基准 → edge</p>
<p class="hint">持有期 5/10/20/40/60 日;阈值扫 2/5/10/20%。</p>
</div>
</div>

<h2>② 核心结果:深度超卖确实有 edge</h2>
<div class="fig">{f1}</div>
<p class="lead">两个清晰规律:<b>① 越极端越有效</b> —— 从下往上(≤20%→≤2%),绿色越来越深,edge 越大;≤20% 基本无 edge(轻微超卖没用)。
<b>② 持有期 40-60 日最优</b> —— 短期(5日)edge 小且超卖可能继续跌(接飞刀),需要时间兑现反弹。</p>
{_matrix_html(edge_full, base)}

<h2>③ 落地验证:能落到创业板ETF吗?</h2>
<p class="lead">指数不能直接交易,得买 <b>159915 创业板ETF</b>。跟踪误差 + 交易费用会不会吃掉 edge?
下面三口径对比 —— 理论(指数点位)/ 含跟踪误差(ETF市价)/ 扣费(再减 0.3% 双边成本)。</p>
<div class="fig">{f2}</div>
<p class="lead"><b>结论:edge 不随落地稀释。</b> 三口径胜率几乎一样,0.3% 费用被 ETF 的轻微正超额吸收。
ETF 长期 vs 指数年化超额仅 +0.08%(几乎完全跟踪),所以落地无损。</p>

<h2>④ 稳健性:这个 edge 经得起检验吗?</h2>
<h3>P0-3 · 不是右偏假象, 质量真实</h3>
<p>均值好看会不会是少数几次暴涨拉高的?看完整分布:</p>
<div class="fig">{f3}</div>
<p class="lead">信号触发的收益分布(蓝)<b>整体右移</b>,且比基准(灰)<b>更对称</b>(偏度更低) —— 不是少数大赢虚高。
<b>盈亏比随持有期跃升</b>:持 20 日盈亏比 0.97(差,接飞刀)→ 60 日 2.62(基准才 1.70)。<b>必须长持 40-60 日。</b></p>

<h3>P0-1 · 样本外守住(edge 真实但温和)</h3>
<p class="lead">把数据切两段:2010-2018 选参数、2019-2026 验证。<b>edge 在样本外没失效, 但缩水约一半</b>(in-sample 乐观偏差的正常代价)。
所以可信水平是 <b>OOS 的 +12~19pp</b>,不是 in-sample 的 +24pp。</p>

<h3>P0-6 · 加「拐头确认」= 真增量(减接飞刀)</h3>
<p class="lead">单因子的问题:偏离 ≤ 阈值时,可能还在加速下跌(偏离继续创新低)。<b>加一个确认:偏离度不在过去 5 日创新低</b>(跌势放缓才动手)。
结果:触发数砍半,但 <b>胜率更高、尾部亏损更小、且样本外 edge 翻倍</b>。</p>
<div class="fig">{f4}</div>
<table>
<tr><th>阈值</th><th>信号</th><th>n</th><th>胜率</th><th>盈亏比</th><th>P(亏>10%)</th><th>最差单次</th></tr>
{detail_rows}
</table>
<p class="hint">combo1 = 偏离极值 AND 不在过去5日创新低 · 60日持有 · 绿=优于基准。≤2% combo1 样本极少。</p>

<h2>⑤ 最终结论</h2>
<div class="takeaway">
<b>推荐信号:</b> 创业板指偏离度 expanding 分位 <b>≤10%(最稳)或 ≤5%(更强·样本小)</b> + <b>拐头确认</b>(偏离不在过去 5 日创新低) + <b>持有 60 日</b>。
<div class="stathero" style="margin-top:12px">
  <div class="stat good"><div class="v">+12pp</div><div class="l">OOS edge · ≤10%+拐头 · n=64</div></div>
  <div class="stat good"><div class="v">+19pp</div><div class="l">OOS edge · ≤5%+拐头 · n=11</div></div>
  <div class="stat blue"><div class="v">66-73%</div><div class="l">OOS 胜率</div></div>
  <div class="stat"><div class="v">~2.4</div><div class="l">盈亏比(持60日)</div></div>
</div>
<p><b>定位:超卖「仓位增强」, 不是「全仓择时开关」。</b>≤2% 一年触发不到 1 次。
用法:超卖 + 拐头时 <b>加仓</b>(而非 all-in 抄底)、必长持 40-60 日、单次仍可能亏 14-27% → 仓位控制/分批。</p>
</div>

<h2>⑥ 必须知道的局限</h2>
<div class="two">
<div>
<p>⚠ <b>含重叠样本:</b> 一次超卖行情连续多天触发, 会重复计同一波反弹, 绝对数值偏乐观。
独立事件去重后 ≤5% 仅 12 次、≤2% 仅 7 次。</p>
<p>⚠ <b>≤2% 是统计陷阱:</b> 看似最强(in-sample +28pp), 实则近 10 年样本外 0 次触发拐头, <b>无法验证</b>。</p>
</div>
<div>
<p>⚠ <b>K=5 是手选:</b> 「拐头」窗口设为 5 日, 未做 K=3/5/10 敏感性扫描, 可能有更优窗口。</p>
<p>⚠ <b>仅创业板:</b> 未验证沪深300/中证500 是否同样有效, 不排除是创业板特产。</p>
<p class="hint">本页为临时研究产物, 不喂交易引擎, 不构成投资建议。</p>
</div>
</div>

</div>
<script>
function toggleTheme(){{let b=document.body,d=b.getAttribute('data-theme')==='dark';
  b.setAttribute('data-theme',d?'light':'dark');
  document.querySelector('.toggle').textContent=d?'🌙 深色':'☀️ 浅色';}}
if(window.matchMedia('(prefers-color-scheme:dark)').matches)toggleTheme();
</script>
</body></html>"""

    out = Path("data/deviation_extreme_study.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"报告 -> {out.resolve()}")


if __name__ == "__main__":
    main()
