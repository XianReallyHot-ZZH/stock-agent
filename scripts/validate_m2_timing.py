"""⑪ M2 拐点 event-study 报告(只读诊断):实证「M2 定大盘」主流叙事的可交易性。

事件 = money_conditions 月度状态机(2月动量 run:连降4月=下行确认/大段后反向2月=拐点),
对历史每次事件算上证综指前向收益,vs 无条件基线。**双口径防前视**是本研究相对叙事复盘的增量:
  lag0   理想口径  事件月次月首个交易日(假设月末即知——edge 上界)
  lag15  公布口径  事件月次月 15 日后首个交易日(央行 ~9-15 日发布,保守可交易口径——headline)
两口径差值 = 「等数据确认时行情走完多少」的实证度量。

实证预期(先说好):18 年月频 → 每臂 n≈5-9,结论必为方向性温度计级别;无 edge 也是结论。

Usage:
  python scripts/validate_m2_timing.py                 # 默认上证综指 000001
  python scripts/validate_m2_timing.py --index 000300  # 换沪深300

措辞:win_rate 一律呈现为「上涨概率」(事件后 N 日收益>0 的占比),不用「胜率」——后者易被
误读为盈亏比;结论模板 conclusion_text 在 pool/study.py(与 PEAD/偏离极量验证器共用,不改)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool.study import arm_stats, baseline_stats, conclusion_text, forward_returns
from stockagent.tracker import money_conditions as mcm

WINDOWS = (20, 60, 120, 250)   # 月频宏观:20≈1月/60≈季度/120≈半年/250≈1年
FOCUS = 60                     # conclusion_text 主报窗口
_KINDS = ("down_confirm", "bottom_turn", "top_turn")
_KIND_COLOR = {"down_confirm": "#fab219", "bottom_turn": "#0ca30c", "top_turn": "#d03b3b"}
_KIND_MARKER = {"down_confirm": "diamond", "bottom_turn": "triangle-up", "top_turn": "triangle-down"}

_PAL = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink_sec": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9", "series_1": "#2a78d6"}

_CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9}
body{margin:0;background:#f9f9f7;color:var(--ink);font-family:system-ui,sans-serif;padding:24px;max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:18px 0 8px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:right}
th{text-align:left;color:var(--muted);font-weight:600}
.hint{color:var(--muted);font-size:12px}
"""


def _event_detail(events: list[dict], close: pd.Series) -> list[dict]:
    """每事件明细:双口径事件日 + lag15 各窗口前向收益 + lag0 中位口径用收益。"""
    out = []
    for e in events:
        d0 = mcm.event_trade_date(e["month"], close.index, "lag0")
        d15 = mcm.event_trade_date(e["month"], close.index, "lag15")
        rec = {"kind": e["kind"], "label": mcm.kind_label(e["kind"]), "month": e["month"][:7],
               "run": e["run"], "yoy": e["yoy"], "d0": d0, "d15": d15}
        fr15 = forward_returns(close, d15, WINDOWS) if d15 else None
        fr0 = forward_returns(close, d0, WINDOWS) if d0 else None
        for n in WINDOWS:
            rec[f"ret15_{n}"] = fr15.get(f"ret_{n}") if fr15 else None
            rec[f"ret0_{n}"] = fr0.get(f"ret_{n}") if fr0 else None
        out.append(rec)
    return out


def _stats(detail: list[dict], kinds: tuple, tag: str, windows=WINDOWS) -> dict:
    """{kind_label: {window: {n, win_rate, median_ret}}} —— arm_stats 口径。"""
    return {mcm.kind_label(k): arm_stats(
        [{f"ret_{n}": r.get(f"{tag}_{n}") for n in windows} for r in detail if r["kind"] == k],
        windows) for k in kinds}


def _fmt_cell(st: dict, n: int) -> str:
    s = st.get(n) or {}
    if not s.get("n"):
        return "<td>—</td>"
    wr, med = s["win_rate"], s["median_ret"]
    color = "#0ca30c" if wr >= 0.55 else "#d03b3b" if wr <= 0.45 else "var(--ink-sec)"
    return (f"<td>{s['n']} · <span style='color:{color};font-weight:600'>{wr * 100:.0f}%</span>"
            f" · {med * 100:+.1f}%</td>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="000001", help="价格基准指数(默认 000001 上证综指)")
    ap.add_argument("--out", default="data/m2_timing_study.html")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    money = st.get_china_money_series()
    if len(money) < mcm.MIN_MONTHS:
        print(f"数据不足:china_money_supply 仅 {len(money)} 月(<{mcm.MIN_MONTHS});先跑 "
              "python scripts/backfill_index.py --money")
        sys.exit(1)
    m2 = pd.to_numeric(money["m2_yoy"], errors="coerce").dropna()
    close_all = st.get_index_daily_series(args.index)["close"]
    # 基线与事件同期起(2008 货币序列起点)——公平对照,不混 90s 制度期
    close = close_all[close_all.index >= str(m2.index[0])].dropna()
    if len(close) < max(WINDOWS) + 20:
        print(f"指数数据不足:{args.index} 同期仅 {len(close)} 日")
        sys.exit(1)

    events = mcm.m2_episode_events(m2)
    detail = _event_detail(events, close)
    stats15 = _stats(detail, _KINDS, "ret15")
    stats0 = _stats(detail, _KINDS, "ret0")
    baseline = baseline_stats({args.index: close}, WINDOWS, sample_step=10)
    concl = conclusion_text(stats15, baseline, WINDOWS, focus=FOCUS, subject="M2拐点(公布口径)")

    # ---- console summary ----
    from collections import Counter
    cnt = Counter(r["kind"] for r in detail)
    print(f"=== ⑪ M2 拐点 event-study (基准 {args.index} · 事件 {dict(cnt)} · 基线抽样 "
          f"{baseline[FOCUS]['n'] if baseline.get(FOCUS) else 0} 点) ===")
    for name, s15 in stats15.items():
        s0 = stats0.get(name, {})
        for tag, s in (("lag15", s15), ("lag0", s0)):
            f = s.get(FOCUS) or {}
            if f.get("n"):
                print(f"  {name}·{tag:5} 60日: n={f['n']} 上涨概率{f['win_rate'] * 100:.0f}% "
                      f"中位{f['median_ret'] * 100:+.1f}%")
    b = baseline.get(FOCUS) or {}
    if b.get("n"):
        print(f"  基线(随便哪天买) 60日: 上涨概率{b['win_rate'] * 100:.0f}% 中位{b['median_ret'] * 100:+.1f}%")
    print(f"结论: {concl}")
    state = mcm.episode_state(m2)
    print(f"当前状态: {mcm.state_label(state)}")

    # ---- figure 1: M2 同比全史 + 三臂事件标记 ----
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(
        x=pd.to_datetime(m2.index), y=m2.to_numpy(dtype=float), name="M2 同比%",
        line=dict(color=_PAL["series_1"], width=2),
        hovertemplate="%{x|%Y-%m}<br>M2 同比 %{y:.1f}%<extra></extra>"))
    m2_map = {str(m)[:7]: float(v) for m, v in m2.items()}
    for k in _KINDS:
        ev = [e for e in events if e["kind"] == k]
        if not ev:
            continue
        fig1.add_trace(go.Scatter(
            x=pd.to_datetime([e["month"] for e in ev]),
            y=[m2_map.get(e["month"][:7], e["yoy"]) for e in ev],
            name=mcm.kind_label(k), mode="markers",
            marker=dict(symbol=_KIND_MARKER[k], size=10, color=_KIND_COLOR[k],
                        line=dict(color=_PAL["ink"], width=0.5)),
            customdata=[[e["yoy"], e["run"]] for e in ev],
            hovertemplate=("<b>%{x|%Y-%m}</b> " + mcm.kind_label(k)
                           + "<br>当时 M2 同比 %{customdata[0]:.1f}%<br>run=%{customdata[1]}"
                           "<extra></extra>")))
    fig1.update_layout(height=420, margin=dict(l=44, r=20, t=20, b=30),
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=True)
    fig1.update_yaxes(title_text="M2 同比 %", gridcolor=_PAL["grid"])
    fig1.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m",
                      rangeslider_visible=True,
                      rangeselector=dict(buttons=[
                          dict(count=3, label="3年", step="year", stepmode="backward"),
                          dict(count=5, label="5年", step="year", stepmode="backward"),
                          dict(label="全部", step="all"),
                      ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))

    # ---- figure 2: lag15 各臂×窗口胜率 vs 基线 ----
    fig2 = go.Figure()
    win_labels = [f"{n}日" for n in WINDOWS]
    base_wr = [(baseline.get(n) or {}).get("win_rate", float("nan")) * 100 for n in WINDOWS]
    for k in _KINDS:
        name = mcm.kind_label(k)
        wrs = [((stats15.get(name) or {}).get(n) or {}).get("win_rate", float("nan")) * 100
               for n in WINDOWS]
        fig2.add_trace(go.Bar(
            x=win_labels, y=wrs, name=name, marker_color=_KIND_COLOR[k], opacity=0.85,
            text=[f"{w:.0f}%" if w == w else "—" for w in wrs], textposition="outside"))
    fig2.add_trace(go.Scatter(
        x=win_labels, y=base_wr, name="基线(无条件)", mode="markers+lines",
        line=dict(color=_PAL["ink"], width=1.2, dash="dash"),
        marker=dict(symbol="x", size=9, color=_PAL["ink"])))
    fig2.update_layout(height=300, margin=dict(l=40, r=20, t=20, b=30), barmode="group",
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=True,
                       yaxis_title="60日后上涨概率% · 公布口径", xaxis_title="事件后持有 N 交易日")
    fig2.update_yaxes(gridcolor=_PAL["grid"], range=[0, 100])
    fig2.update_xaxes(gridcolor=_PAL["grid"])

    # ---- figure 3: 滞后侵蚀(焦点窗口):lag0 vs lag15 vs 基线 中位收益 ----
    names = [mcm.kind_label(k) for k in _KINDS]
    med0 = [((stats0.get(nm) or {}).get(FOCUS) or {}).get("median_ret", float("nan")) * 100
            for nm in names]
    med15 = [((stats15.get(nm) or {}).get(FOCUS) or {}).get("median_ret", float("nan")) * 100
             for nm in names]
    medb = ((baseline.get(FOCUS) or {}).get("median_ret", float("nan"))) * 100
    fig3 = go.Figure()
    fig3.add_trace(go.Bar(x=names, y=med0, name=f"lag0 理想(月末即知)",
                          marker_color="#9ec7f0", text=[f"{v:+.1f}" if v == v else "—"
                                                        for v in med0], textposition="outside"))
    fig3.add_trace(go.Bar(x=names, y=med15, name=f"lag15 公布(可交易)",
                          marker_color="#2a78d6", text=[f"{v:+.1f}" if v == v else "—"
                                                        for v in med15], textposition="outside"))
    fig3.add_hline(y=medb, line=dict(color=_PAL["ink"], dash="dash", width=1.2),
                   annotation_text=f"基线中位 {medb:+.1f}%")
    fig3.update_layout(height=300, margin=dict(l=44, r=20, t=30, b=30), barmode="group",
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=True,
                       yaxis_title=f"{FOCUS}日中位收益 %", xaxis_title="臂(事件类型)")
    fig3.update_yaxes(gridcolor=_PAL["grid"], zerolinecolor=_PAL["grid"])
    fig3.update_xaxes(gridcolor=_PAL["grid"])

    # ---- 主表: lag15 各臂×窗口 n·胜率·中位 ----
    head_cells = "".join(f"<th>{n}日 n·上涨概率·中位</th>" for n in WINDOWS)
    main_rows = ""
    for nm in names:
        s15 = stats15.get(nm) or {}
        cells = "".join(_fmt_cell(s15, n) for n in WINDOWS)
        main_rows += f"<tr><td style='text-align:left'>{nm}</td>{cells}</tr>"
    b_cells = "".join(_fmt_cell(baseline, n) for n in WINDOWS)
    main_rows += f"<tr><td style='text-align:left'>基线(随便哪天买)</td>{b_cells}</tr>"
    main_table = (f"<h2>公布口径(lag15) 主表</h2>"
                  f"<table><tr><th style='text-align:left'>臂</th>{head_cells}</tr>{main_rows}</table>")

    # ---- 滞后侵蚀表: lag0 vs lag15 中位差(pp) ----
    ero_rows = ""
    for nm in names:
        cells = ""
        for n in WINDOWS:
            m0 = ((stats0.get(nm) or {}).get(n) or {}).get("median_ret", float("nan"))
            m15 = ((stats15.get(nm) or {}).get(n) or {}).get("median_ret", float("nan"))
            cells += (f"<td>{m0 * 100:+.1f} → {m15 * 100:+.1f}"
                      f"<br><span class='hint'>(Δ{(m15 - m0) * 100:+.1f}pp)</span></td>"
                      if m0 == m0 and m15 == m15 else "<td>—</td>")
        ero_rows += f"<tr><td style='text-align:left'>{nm}</td>{cells}</tr>"
    ero_table = (f"<h2>滞后侵蚀:理想(lag0) → 公布(lag15) 中位收益</h2>"
                 f"<div class='hint'>公布日约在事件月次月中旬(此处取 15 日保守近似)。Δ 为负 = 等数据确认时"
                 f"「行情已走完」的部分——对「M2 拐点可直接跟」叙事的直接检验。n 小,读差值方向不读精度。</div>"
                 f"<table><tr><th style='text-align:left'>臂</th>"
                 + "".join(f"<th>{n}日</th>" for n in WINDOWS) + f"</tr>{ero_rows}</table>")

    # ---- 事件清单(newest first, 收益为 lag15) ----
    rows_html = ""
    for r in reversed(detail):
        cells = ""
        for n in WINDOWS:
            v = r.get(f"ret15_{n}")
            if v is None:
                cells += "<td>—</td>"
            else:
                c = "#0ca30c" if v > 0 else "#d03b3b"
                cells += f"<td style='color:{c}'>{v * 100:+.1f}%</td>"
        rows_html += (f"<tr><td style='text-align:left'>{r['month']}</td>"
                      f"<td style='text-align:left;color:{_KIND_COLOR[r['kind']]};font-weight:600'>"
                      f"{r['label']}</td><td>{r['yoy']:.1f}%</td><td>{r['run']}</td>"
                      f"<td>{r['d0'] or '—'}</td><td>{r['d15'] or '—'}</td>{cells}</tr>")

    foot = ("<h2>口径脚注(诚实)</h2><div class='hint'>"
            f"· 事件=2月动量 run 状态机(连降{mcm.DOWN_RUN}月=下行确认/大段后反向{mcm.TURN_RUN}月=拐点),"
            "纯 trailing 规则、无前视;见顶回落与下行确认可能在同一下行段先后触发(前=早信号,后=确认)。<br>"
            f"· 公布日近似=次月 {mcm.PUB_DAY} 日(央行实际 9-15 日);lag0/lag15 双口径即敏感性分析。<br>"
            "· 样本 n 每臂仅 5-9 → 读方向不读精度,不 bootstrap 显著性(⑧⑨ 同礼遇);结论(含无 edge)"
            "写 meta 注入指数择时看板 ⑪ 读图说明。<br>"
            "· 数据:金十源(央行金融统计),偶有历史修订;M1 口径断点不影响 M2 主信号;指数用 raw close 不复权"
            "(仓库口径)。金十 M2 序列 2008 起——2008 前的周期(如 04-07)不在样本。<br>"
            "· 温度计非开关,永不喂交易引擎。</div>")

    last_px = str(close.index[-1])[:10]
    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>M2 拐点 event-study</title><style>{_CSS}</style></head><body>"
        f"<h1>⑪ M2 拐点 event-study</h1>"
        f"<div class='meta'>「M2 定大盘」实证 · 基准 {args.index} · M2 序列 {str(m2.index[0])[:7]}.."
        f"{str(m2.index[-1])[:7]} · 指数截至 {last_px} · 事件 {len(detail)}</div>"
        f"<div class='hint'><b>结论(headline=公布口径):</b> {concl}</div>"
        f"<div class='hint'>数字格式:臂 = 事件后 60 日<b>上涨概率</b>% / 中位涨跌% (n=历史事件次数);"
        f"基线 = 每 10 日抽样的「随便哪天买」。胜率一词一律写作上涨概率,避免与盈亏幅度混淆。</div>"
        f"<h2>M2 同比全史 + 事件标记</h2>{fig1.to_html(full_html=False, include_plotlyjs=True)}"
        f"<h2>各臂×窗口胜率(公布口径 · ✕虚线=无条件基线)</h2>"
        f"{fig2.to_html(full_html=False, include_plotlyjs=False)}"
        f"<h2>滞后侵蚀({FOCUS}日中位)</h2>{fig3.to_html(full_html=False, include_plotlyjs=False)}"
        f"{main_table}{ero_table}"
        f"<h2>事件清单(最新在上 · 收益=公布口径 lag15)</h2>"
        f"<table><tr><th style='text-align:left'>事件月</th><th style='text-align:left'>类型</th>"
        f"<th>当时M2同比</th><th>run</th><th>lag0事件日</th><th>lag15事件日</th>"
        + "".join(f"<th>{n}日收益</th>" for n in WINDOWS)
        + f"</tr>{rows_html}</table>{foot}</body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    st.set_meta("china_money_conclusion", concl)
    print(f"\n报告 -> {out}\n结论已写 meta(china_money_conclusion) → 指数择时看板 ⑪ 活注入")


if __name__ == "__main__":
    main()
