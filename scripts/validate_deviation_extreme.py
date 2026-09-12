"""策略1(偏离超卖) event-study 深度报告: 偏离极值→前向收益,三臂 vs 无条件基线。

臂定义(point-in-time,防前视):
  raw        全部事件(expanding 分位 ≤5% 的超卖段起点,段间 120 日冷却)
  企稳       事件日 stabilize_factor ≥0.6(走平/缓跌;飞刀 <0.6 剔除)
  企稳+护栏  企稳 ∧ 无预亏族预告 ∧ 非连亏(事件日前公告的最新预告/正式报判定;
             一致预期下修闸因 E0 快照历史不足,诚实缺席,见报告注记)
基线 = 同股票池全时点(每 10 根抽 1)无条件前向收益。
结论(含「无 edge」)写 meta deviation_extreme_conclusion,由看板读图说明注入——
支撑位/地量的教训: 时效可能成立、胜率未必分离,温度计非开关。

Usage:
  python scripts/validate_deviation_extreme.py            # 全 universe, 5-15min 本地计算
  python scripts/validate_deviation_extreme.py --codes 600519,000001   # 指定子集(调试)
"""
from __future__ import annotations

[已退役 2026-09-12] V8 重写(六表退役,用户批准 Q1=A): 本验证器属于旧策略1/PEAD 表,
其结论已写 meta(deviation_extreme_conclusion / pead_conclusion)留档;脚本的 pool.screen/
pool.universe 依赖已随重写变更,import 会失败——留盘存档,勿直接运行。


import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool import study
from stockagent.pool import universe as uni
from stockagent.pool.prices import dividend_adjusted_close
from stockagent.pool.screen import gather_actuals, gather_forecasts
from stockagent.pool.scoring import LOSS_FORECAST_TYPES
from stockagent.tracker.stock_diagnose import stabilize_factor

WINDOWS = (5, 20, 60)
FOCUS = 20

_PAL = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink_sec": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9",
        "series_1": "#2a78d6", "good": "#0ca30c", "critical": "#d03b3b", "warning": "#fab219"}

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


def _guard_at(fc: pd.DataFrame | None, ac: pd.DataFrame | None, date: str) -> bool:
    """事件日 point-in-time 护栏: 无预亏族预告 ∧ 非连亏(≥3 期负增长)。"""
    if fc is not None and len(fc):
        past = fc[fc["announce_date"].astype(str).str[:10] <= date]
        if len(past):
            latest = past.sort_values("report_period").iloc[-1]
            if latest.get("type") in LOSS_FORECAST_TYPES:
                return False
    if ac is not None and len(ac):
        past = ac[ac["announce_date"].astype(str).str[:10] <= date]
        seq = past.sort_index()["np_yoy"].tolist()
        n_neg = 0
        for v in reversed(seq):
            if isinstance(v, (int, float)) and not pd.isna(v) and v < 0:
                n_neg += 1
            else:
                break
        if n_neg >= 3:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", type=str, default="", help="逗号分隔子集(调试;默认全 universe)")
    ap.add_argument("--out", default="data/deviation_extreme_study.html")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    cfg = get_config()
    ucfg = (cfg.params.get("stock_pool", {}) or {}).get("universe", {}) or {}
    _, cons = store.get_consensus_snapshot()
    spot = store.latest_stock_spot()
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = [str(c) for c in uni.derive_universe(
            cons, spot, min_reports=int(ucfg.get("min_reports", 3))).index]
    if not codes:
        print("universe 为空: 先跑 backfill_stock_pool.py --all(consensus+spot)")
        sys.exit(1)
    print(f"策略1 event-study: universe {len(codes)} 只, 事件=偏离 expanding 分位≤5% 段起点...")

    codeset = set(codes)
    forecasts = gather_forecasts(store, codeset)
    actuals = gather_actuals(store, codeset)
    closes: dict[str, pd.Series] = {}
    events_all: list[dict] = []
    n_used = 0
    for i, code in enumerate(codes):
        if i % 300 == 0:
            print(f"  {i}/{len(codes)} (事件累计 {len(events_all)})")
        price = store.get_series(code)
        if len(price) < 300:
            continue
        div = store.get_stock_dividend_series(code)
        adj, _ = dividend_adjusted_close(price["close"].astype(float),
                                         div if len(div) else None)
        closes[code] = adj
        n_used += 1
        for ev in study.deviation_events(adj, code):
            d = ev["date"]
            pos = ev["pos"]
            # 事件日企稳(近 60 日涨幅,以事件日为终点)
            recent = (float(adj.iloc[pos]) / float(adj.iloc[max(pos - 60, 0)]) - 1.0
                      if pos >= 2 else float("nan"))
            sf = stabilize_factor(recent)
            guard = _guard_at(forecasts.get(code), actuals.get(code), d)
            fr = study.forward_returns(adj, d, WINDOWS)
            if fr is None:
                continue
            events_all.append({"code": code, "date": d, "stabilize": sf,
                               "guard": guard, **fr})
    if not events_all:
        print("无事件(历史不足或 universe 过小)")
        sys.exit(0)

    arms = {
        "raw": study.arm_stats(events_all, WINDOWS),
        "企稳(sf≥0.6)": study.arm_stats(
            [e for e in events_all if e["stabilize"] >= 0.6], WINDOWS),
        "企稳+护栏": study.arm_stats(
            [e for e in events_all if e["stabilize"] >= 0.6 and e["guard"]], WINDOWS),
    }
    baseline = study.baseline_stats(closes, WINDOWS)
    conclusion = study.conclusion_text(arms, baseline, WINDOWS, focus=FOCUS)

    print(f"\n样本: {n_used} 只有价格, 事件 {len(events_all)} 个(前向窗口 {WINDOWS})")
    print(f"结论: {conclusion}")
    store.set_meta("deviation_extreme_conclusion", conclusion)

    # ---- HTML ----
    fig = go.Figure()
    for (name, st), color in zip(arms.items(), (_PAL["series_1"], _PAL["warning"], _PAL["good"])):
        fig.add_trace(go.Bar(
            x=[f"{n}日" for n in WINDOWS],
            y=[st[n]["win_rate"] * 100 for n in WINDOWS],
            name=name, text=[f"{st[n]['win_rate'] * 100:.0f}%" for n in WINDOWS],
            textposition="outside", marker_color=color))
    base_wr = [baseline[n]["win_rate"] * 100 for n in WINDOWS]
    fig.add_trace(go.Scatter(x=[f"{n}日" for n in WINDOWS], y=base_wr, name="无条件基线",
                             mode="lines+markers", line=dict(color=_PAL["critical"], dash="dash")))
    fig.update_layout(height=340, barmode="group", paper_bgcolor=_PAL["surface"],
                      plot_bgcolor=_PAL["surface"], font=dict(color=_PAL["ink"]),
                      yaxis_title="胜率%", yaxis_range=[0, 100])
    fig.update_yaxes(gridcolor=_PAL["grid"]); fig.update_xaxes(gridcolor=_PAL["grid"])

    def _row(name, st):
        cells = "".join(
            f"<td>{st[n]['win_rate'] * 100:.0f}% / {st[n]['median_ret'] * 100:+.1f}%"
            f"<span class='hint'> n={st[n]['n']}</span></td>" for n in WINDOWS)
        return f"<tr><td style='text-align:left'>{name}</td>{cells}</tr>"

    tbl = ("<table><tr><th style='text-align:left'>臂</th>"
           + "".join(f"<th>{n}日 胜率/中位</th>" for n in WINDOWS) + "</tr>")
    for name, st in arms.items():
        tbl += _row(name, st)
    tbl += _row("无条件基线", baseline) + "</table>"

    recent = sorted(events_all, key=lambda e: e["date"])[-30:]
    ev_rows = "".join(
        f"<tr><td>{e['date']}</td><td style='text-align:left'>{e['code']}</td>"
        f"<td>{e['stabilize']:.2f}</td><td>{'✔' if e['guard'] else '✘'}</td>"
        f"<td style='color:{_PAL['good'] if (e['ret_20'] or 0) > 0 else _PAL['critical']}'>"
        f"{(e['ret_20'] or float('nan')) * 100:+.1f}%</td>"
        f"<td>{(e['ret_60'] or float('nan')) * 100:+.1f}%</td></tr>"
        for e in reversed(recent))

    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>偏离极值 event-study</title><style>{_CSS}</style></head><body>"
        f"<h1>🎯 策略1 偏离超卖 event-study</h1>"
        f"<div class='meta'>{len(codes)} 只 universe / {n_used} 只有价格 / 事件 {len(events_all)} 个 ·"
        f" 事件=自身 expanding 分位≤5% 段起点(120日冷却) · 前向 {WINDOWS} 交易日</div>"
        f"<h2>结论(诚实,含打脸)</h2><div class='hint'>{conclusion}<br>"
        f"护栏臂的「预期下修」闸因 consensus 快照历史不足缺席(E0 2026-08 起积累);"
        f"样本内规律非因果;温度计非开关——支撑位/地量同款礼遇。</div>"
        f"{fig.to_html(full_html=False, include_plotlyjs=True)}"
        f"<h2>臂 × horizon(胜率 / 中位收益)</h2>{tbl}"
        f"<h2>最近 30 事件</h2>"
        f"<table><tr><th>日期</th><th style='text-align:left'>代码</th><th>企稳</th>"
        f"<th>护栏</th><th>20日</th><th>60日</th></tr>{ev_rows}</table>"
        f"</body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"报告 -> {out}  (meta deviation_extreme_conclusion 已写入,看板读图说明注入)")


if __name__ == "__main__":
    main()
