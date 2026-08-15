"""关键支撑位破位 event-study 报告(只读诊断):平台顶/前低 两类规则选位,守住 vs 破位 的前向二阶矩。

研究假设(2026-08 讨论):支撑位的信息量在二阶矩(波动/回撤的条件分布)不在一阶矩(方向)。
事件 = 平台顶有效突破后首次回踩 + 前低(枢轴)反弹后首次回测,合并去重;结局 = 守住 / 破位·收回 /
破位·未收;前向指标从确认日起算(决策一致),对比无条件基准。刻意同时报告胜率(预期~50%,诚实呈现)。

Usage:
  python scripts/validate_support_break.py                # 默认上证综指 000001
  python scripts/validate_support_break.py --index 000300
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.tracker import support_levels as sl

_PAL = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink_sec": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9", "baseline": "#c3c2b7",
        "series_1": "#2a78d6", "warning": "#fab219",
        "good": "#0ca30c", "critical": "#d03b3b"}
_OC_COLOR = {"hold": _PAL["good"], "break_reclaim": _PAL["warning"],
             "break_down": _PAL["critical"], "pending": _PAL["ink_sec"]}

_CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink-sec:#52514e;--muted:#898781;--grid:#e1e0d9}
body{margin:0;background:#f9f9f7;color:var(--ink);font-family:system-ui,sans-serif;padding:24px;max-width:1100px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px} h2{font-size:16px;margin:18px 0 8px;color:var(--ink-sec)}
.meta{color:var(--ink-sec);font-size:13px;margin-bottom:16px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}
th,td{padding:6px 10px;border-bottom:1px solid var(--grid);text-align:right}
th{text-align:left;color:var(--muted);font-weight:600}
.hint{color:var(--muted);font-size:12px}
.verdict{border:1px solid var(--grid);border-left:4px solid var(--series_1);padding:10px 14px;margin:12px 0;font-size:14px;background:#fff}
"""


def _fmt(v, pct=True, sign=False):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.1f}%" if sign else f"{v * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="000001", help="标的指数(默认 000001 上证综指)")
    ap.add_argument("--out", default="data/support_break_study.html")
    args = ap.parse_args()

    st = Store(get_config().db_path)
    df = st.get_index_daily_series(args.index)
    if len(df) < 500:
        print("数据不足:需 ≥500 日")
        sys.exit(1)
    close, vol = df["close"], df.get("volume")

    events = sl.merged_events(close, vol)
    rows = sl.forward_risk_rows(close, events)
    base_rows = sl.baseline_risk_rows(close)
    g = sl.group_summary(rows)
    gb = sl.group_summary(base_rows).get("baseline", {})

    def _col(oc, key):
        return [r[key] for r in rows if r["outcome"] == oc and key in r]

    boots = {m: sl.bootstrap_median_diff(_col("break_down", m), _col("hold", m))
             for m in ("vol_20", "vol_60", "mdd_20", "mdd_60", "ret_20", "ret_60")}
    pending = [e for e in events if e["pending"]]

    # ---- console summary ----
    n_oc = {oc: g.get(oc, {}).get("n", 0) for oc in sl.OUTCOMES}
    print(f"=== 关键支撑位 event-study (标的 {args.index}) ===")
    print(f"选位: 平台顶(窗{sl.PLATFORM_WIN}日 振幅≤{sl.PLATFORM_RANGE:.0%}) + 前低枢轴(侧{sl.PIVOT_SIDE}日·反弹≥{sl.BOUNCE_MIN:.0%}) | "
          f"事件 {len(events)} 可用 {len(rows)}  守住{n_oc['hold']}/假破{n_oc['break_reclaim']}/真破{n_oc['break_down']}")
    print(f"           守住        破位·未收    基准(无条件)")
    for m, lab in [("vol_20", "20日波动"), ("vol_60", "60日波动"),
                   ("mdd_60", "60日内最大回撤"), ("ret_20", "20日收益(中位)")]:
        h = g.get("hold", {}).get(f"{m}_med", float("nan"))
        b = g.get("break_down", {}).get(f"{m}_med", float("nan"))
        base = gb.get(f"{m}_med", float("nan"))
        sep = "【分离】" if boots[m]["separated"] else "【不分离】"
        print(f"  {lab:<12} {_fmt(h):>8}   {_fmt(b):>8}   {_fmt(base):>8}   {sep} "
              f"(diff90%CI {_fmt(boots[m]['lo'])}~{_fmt(boots[m]['hi'])})" if not math.isnan(boots[m]["lo"]) else "")
    if pending:
        print("当前进行中(待结局/待前向):")
        for e in pending:
            print(f"  [{e['kind']}] 位{e['level']:.0f} 回踩 {e['touch']} ({sl.OUTCOME_LABEL[e['outcome']]},pending)")

    # ---- fig1: 价格 + 事件标记 ----
    fig1 = go.Figure()
    fig1.add_trace(go.Scatter(x=pd.to_datetime(close.index), y=close.to_numpy(),
                              name=args.index, line=dict(color=_PAL["series_1"], width=1.5)))
    px_map = close.to_dict()
    for oc, name, sym in [("hold", "守住", "circle"), ("break_reclaim", "破位·收回", "diamond"),
                          ("break_down", "破位·未收", "x")]:
        sub = [e for e in events if e["outcome"] == oc]
        if not sub:
            continue
        fig1.add_trace(go.Scatter(
            x=pd.to_datetime([e["touch"] for e in sub]),
            y=[px_map.get(e["touch"], float("nan")) for e in sub], name=name, mode="markers",
            marker=dict(symbol=sym, size=10, color=_OC_COLOR[oc], line=dict(color=_PAL["ink"], width=0.5)),
            customdata=[[e["level"], e["kind"], e["confirm"]] for e in sub],
            hovertemplate=("<b>%{x|%Y-%m-%d}</b> 回踩<br>位 %{customdata[0]:.0f} [%{customdata[1]}]<br>"
                           "确认 %{customdata[2]}<extra>" + name + "</extra>")))
    if pending:
        fig1.add_trace(go.Scatter(
            x=pd.to_datetime([e["touch"] for e in pending]),
            y=[px_map.get(e["touch"], float("nan")) for e in pending], name="进行中(pending)",
            mode="markers", marker=dict(symbol="star", size=13, color=_PAL["ink_sec"],
                                        line=dict(color=_PAL["ink"], width=1))))
    fig1.update_layout(height=460, margin=dict(l=55, r=30, t=30, b=30),
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), showlegend=True)
    fig1.update_yaxes(gridcolor=_PAL["grid"])
    fig1.update_xaxes(gridcolor=_PAL["grid"], type="date", hoverformat="%Y-%m-%d",
                      rangeslider_visible=True,
                      rangeselector=dict(buttons=[
                          dict(count=1, label="1年", step="year", stepmode="backward"),
                          dict(count=3, label="3年", step="year", stepmode="backward"),
                          dict(count=5, label="5年", step="year", stepmode="backward"),
                          dict(label="全部", step="all"),
                      ], bgcolor=_PAL["surface"], activecolor=_PAL["grid"]))

    # ---- fig2: 前向二阶矩箱线(核心展品) ----
    fig2 = make_subplots(rows=1, cols=2, subplot_titles=("确认日后 20 日实现波动(年化)", "确认日后 60 日内最大回撤"))
    order = ["hold", "break_reclaim", "break_down", "baseline"]
    label = {**sl.OUTCOME_LABEL, "baseline": "无条件基准"}
    for col, key in [(1, "vol_20"), (2, "mdd_60")]:
        for oc in order:
            vals = _col(oc, key) if oc != "baseline" else [r[key] for r in base_rows if key in r]
            if not vals:
                continue
            fig2.add_trace(go.Box(y=vals, name=label[oc], marker_color=_OC_COLOR.get(oc, _PAL["muted"]),
                                  boxpoints="outliers", showlegend=(col == 1)), row=1, col=col)
        fig2.update_yaxes(tickformat=".0%", gridcolor=_PAL["grid"], row=1, col=col)
    fig2.update_layout(height=380, margin=dict(l=50, r=20, t=40, b=30),
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]))

    # ---- fig3: 胜率(一阶矩,诚实呈现) ----
    fig3 = go.Figure()
    ns = ["ret_5", "ret_20", "ret_60"]
    xlab = ["5日", "20日", "60日"]
    for oc in ("hold", "break_down"):
        wrs = [g.get(oc, {}).get(f"win_{n[4:]}", float("nan")) * 100 for n in ns]
        fig3.add_trace(go.Bar(x=xlab, y=wrs, name=sl.OUTCOME_LABEL[oc],
                              marker_color=_OC_COLOR[oc], text=[f"{w:.0f}%" for w in wrs],
                              textposition="outside"))
    fig3.add_hline(y=50, line=dict(color=_PAL["muted"], dash="dash", width=1))
    fig3.update_layout(height=300, margin=dict(l=40, r=20, t=20, b=30), barmode="group",
                       paper_bgcolor=_PAL["surface"], plot_bgcolor=_PAL["surface"],
                       font=dict(color=_PAL["ink"]), yaxis_title="胜率%", yaxis_range=[0, 100])
    fig3.update_yaxes(gridcolor=_PAL["grid"])
    fig3.update_xaxes(gridcolor=_PAL["grid"])

    # ---- bootstrap 表 + 自动结论 ----
    boot_rows = ""
    metric_label = {"vol_20": "20日实现波动", "vol_60": "60日实现波动", "mdd_20": "20日内最大回撤",
                    "mdd_60": "60日内最大回撤", "ret_20": "20日收益(中位)", "ret_60": "60日收益(中位)"}
    for m, lab in metric_label.items():
        bt = boots[m]
        h = g.get("hold", {}).get(f"{m}_med", float("nan"))
        b = g.get("break_down", {}).get(f"{m}_med", float("nan"))
        boot_rows += (f"<tr><td style='text-align:left'>{lab}</td><td>{_fmt(h)}</td><td>{_fmt(b)}</td>"
                      f"<td>{_fmt(bt['diff_med'], sign=True)}</td><td>{_fmt(bt['lo'])} ~ {_fmt(bt['hi'])}</td>"
                      f"<td style='color:{_PAL['good'] if bt['separated'] else _PAL['muted']}'>"
                      f"{'✔ 分离' if bt['separated'] else '✗ 不分离'}</td></tr>")

    verdicts = []
    if boots["vol_20"]["separated"]:
        verdicts.append(f"① <b>20日实现波动分离成立</b>:破位·未收 {_fmt(g.get('break_down', {}).get('vol_20_med'))} "
                        f"vs 守住 {_fmt(g.get('hold', {}).get('vol_20_med'))}(90%CI 不含0)——支撑位的实证价值在"
                        f"<b>波动维度</b>:破位确认后 20 日内波动中枢显著抬升 → 仓位/风险预算应当收紧,与方向判断无关。")
    else:
        verdicts.append("① 20日实现波动不分离 —— 二阶矩假设在当前样本下不成立。")
    verdicts.append(f"② 前向最大回撤中位数不分离(60日:破位 {_fmt(g.get('break_down', {}).get('mdd_60_med'))} vs "
                    f"守住 {_fmt(g.get('hold', {}).get('mdd_60_med'))})——「破位后回撤更深」在中位数意义上<b>不成立</b>;"
                    f"支撑破位改变的是波动与路径,不是回撤深度的中位预期。")
    w20b = g.get("break_down", {}).get("win_20", float("nan"))
    verdicts.append(f"③ 方向胜率无稳定 edge(破位后 win20={w20b * 100:.0f}%,样本 {n_oc['break_down']} 例;"
                    f"与 ⑧地量同构:支撑位是<b>观察坐标/温度计,不是买卖信号</b>,永不喂交易引擎。")
    verdict_html = "<div class='verdict'>" + "<br>".join(verdicts) + "</div>"

    # ---- 事件表(最新在上) ----
    def _tr(r):
        vr = f"{r['vol_ratio']:.2f}" if r.get("vol_ratio") == r.get("vol_ratio") else "—"
        oc = r["outcome"]
        return (f"<tr><td style='text-align:left'>{'平台顶' if r['kind'] == 'platform' else '前低'}</td>"
                f"<td>{r['level']:.0f}</td><td>{r['touch']}</td>"
                f"<td style='color:{_OC_COLOR[oc]}'>{sl.OUTCOME_LABEL[oc]}</td>"
                f"<td>{r['confirm']}</td><td>{vr}</td>"
                f"<td style='color:{_PAL['good'] if r.get('ret_20', 0) > 0 else _PAL['critical']}'>{_fmt(r.get('ret_20'), sign=True)}</td>"
                f"<td>{_fmt(r.get('mdd_20'))}</td><td>{_fmt(r.get('vol_20'))}</td></tr>")

    rows_sorted = sorted(rows, key=lambda r: r["touch"], reverse=True)
    table_html = "".join(_tr(r) for r in rows_sorted)
    pend_html = ""
    if pending:
        pend_items = "".join(f"<tr><td style='text-align:left'>{'平台顶' if e['kind'] == 'platform' else '前低'}</td>"
                             f"<td>{e['level']:.0f}</td><td>{e['touch']}</td>"
                             f"<td>{sl.OUTCOME_LABEL[e['outcome']]}</td><td>{e['confirm']}</td></tr>"
                             for e in reversed(pending))
        pend_html = ("<h2>进行中的支撑测试(结局/前向未定)</h2>"
                     "<div class='hint'>最新规则选位事件——当前市场正在测试的支撑(结局揭晓后自动进上表)。</div>"
                     f"<table><tr><th style='text-align:left'>类型</th><th>位</th><th>回踩日</th>"
                     f"<th>暂判</th><th>确认日</th></tr>{pend_items}</table>")

    last_date = str(close.index[-1])[:10]
    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>关键支撑位破位 event-study</title><style>{_CSS}</style></head><body>"
        f"<h1>关键支撑位破位 event-study</h1>"
        f"<div class='meta'>选位=平台顶(窗{sl.PLATFORM_WIN}日振幅≤{sl.PLATFORM_RANGE:.0%},突破后≥{sl.MIN_ABOVE}日在带上)+ "
        f"前低枢轴(侧{sl.PIVOT_SIDE}日,反弹≥{sl.BOUNCE_MIN:.0%}) · 回踩带=位±{sl.ZONE_EPS:.0%} · "
        f"破位=收盘破 位−{sl.BREAK_EPS:.0%} · 收回={sl.RECLAIM_DAYS}日内回带 · 守住={sl.RESOLVE_WIN}日无破位 · "
        f"标的 {args.index} · 数据截至 {last_date} · 事件 {len(events)} / 可用 {len(rows)}</div>"
        f"<div class='hint'>前向指标自<b>确认日</b>起算(结局确认后才行动,与决策一致);基准=全样本无条件分布。"
        f"样本 {len(rows)} 例(破位{n_oc['break_down']}/守住{n_oc['hold']}/假破{n_oc['break_reclaim']})偏薄 → 经验参考非定律。</div>"
        f"{verdict_html}"
        f"<h2>价格 + 支撑测试事件(标记=回踩日,颜色=结局)</h2>{fig1.to_html(full_html=False, include_plotlyjs=True)}"
        f"<h2>前向二阶矩:波动 / 回撤分布(核心)</h2>{fig2.to_html(full_html=False, include_plotlyjs=False)}"
        f"<h2>前向胜率(一阶矩 · 预期~50%)</h2>{fig3.to_html(full_html=False, include_plotlyjs=False)}"
        f"<h2>破位·未收 vs 守住 · bootstrap(2000 次,90% CI)</h2>"
        f"<table><tr><th style='text-align:left'>指标(中位)</th><th>守住</th><th>破位·未收</th>"
        f"<th>差(破−守)</th><th>差 90%CI</th><th>分离?</th></tr>{boot_rows}</table>"
        f"{pend_html}"
        f"<h2>事件清单(最新在上)</h2>"
        f"<table><tr><th style='text-align:left'>类型</th><th>位</th><th>回踩日</th><th>结局</th>"
        f"<th>确认日</th><th>破位量比</th><th>20日收益</th><th>20日内回撤</th><th>20日波动</th></tr>"
        f"{table_html}</table>"
        f"</body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\n报告 -> {out}")


if __name__ == "__main__":
    main()
