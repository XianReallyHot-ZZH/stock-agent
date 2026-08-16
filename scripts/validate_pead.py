"""PEAD(预告超预期漂移) event-study: surprise 两臂 → 公告日前向收益,vs 无条件基线。

事件 = universe 内全部业绩预告(stock_forecast, 8 期),surprise 口径与 pool/pead.py 同源
(point-in-time 双腿): leg C(年报期,公告日/公告日−365d 两份 consensus 快照)优先,
leg A(上年同期正式报 np_yoy,公告日早于本公告)回退。E0 未积累的历史期自然落 leg A。
臂: 全部 / surprise ≥ +10pp(超预期) / surprise ≤ −10pp(不及预期)。
结论(含无 edge)写 meta pead_conclusion,看板读图说明注入。

Usage:
  python scripts/validate_pead.py            # 全 universe, ~2-5min
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.pool import study
from stockagent.pool import universe as uni
from stockagent.pool.pead import pead_surprise
from stockagent.pool.prices import dividend_adjusted_close

WINDOWS = (5, 20, 60)
FOCUS = 20
SURPRISE_PP = 10.0

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/pead_study.html")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    cfg = get_config()
    ucfg = (cfg.params.get("stock_pool", {}) or {}).get("universe", {}) or {}
    _, cons = store.get_consensus_snapshot()
    spot = store.latest_stock_spot()
    codes = [str(c) for c in uni.derive_universe(
        cons, spot, min_reports=int(ucfg.get("min_reports", 3))).index]
    if not codes:
        print("universe 为空: 先跑 backfill_stock_pool.py --all")
        sys.exit(1)
    codeset = set(codes)
    print(f"PEAD event-study: universe {len(codes)} 只, 事件=8 期预告×point-in-time surprise...")

    # consensus 快照缓存(按 asof 日)
    snap_cache: dict[str, tuple] = {}

    def _snap(asof_dash: str) -> tuple:
        key = asof_dash.replace("-", "")
        if key not in snap_cache:
            snap_cache[key] = store.get_consensus_snapshot(asof=key)
        return snap_cache[key]

    def _prior_actual(code: str, period: str, before: str) -> float | None:
        tail = period[4:]
        prior_period = f"{int(period[:4]) - 1}{tail}"
        frame = store.get_stock_report_period(prior_period)
        if code not in frame.index:
            return None
        row = frame.loc[code]
        ann = str(row.get("announce_date") or "")[:10]
        if ann and ann >= before:
            return None   # 上年同期正式报公告晚于本预告 → 前视,弃用
        v = row.get("np_yoy")
        return None if v is None or pd.isna(v) else float(v)

    events: list[dict] = []
    closes: dict[str, pd.Series] = {}
    n_leg_c = 0
    for period, _n in store.forecast_period_counts():
        fc = store.get_stock_forecast_period(period)
        sub = fc[[c in codeset for c in fc.index]]
        for code, row in sub.iterrows():
            yoy, ann = row.get("yoy"), str(row.get("announce_date") or "")[:10]
            if pd.isna(yoy) or not ann or ann < "2010-01-01":
                continue
            # leg C 材料: 公告日快照 + 公告日−365d 快照(同列 eps_fy1)
            _, s_now = _snap(ann)
            prior_day = (datetime.strptime(ann, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
            _, s_prior = _snap(prior_day)
            eps_now = fy1 = eps_prior = None
            if code in s_now.index:
                eps_now = s_now.loc[code, "eps_fy1"] if "eps_fy1" in s_now.columns else None
                fy1 = s_now.loc[code, "fy1_year"] if "fy1_year" in s_now.columns else None
            if code in s_prior.index and "eps_fy1" in s_prior.columns:
                eps_prior = s_prior.loc[code, "eps_fy1"]
            prior_np = _prior_actual(code, period, ann)
            r = pead_surprise(float(yoy), period, eps_now=eps_now, eps_prior=eps_prior,
                              fy1_year=fy1, prior_actual_yoy=prior_np)
            if not r["valid"]:
                continue
            n_leg_c += r["leg"] == "C"
            events.append({"code": str(code), "period": period, "announce_date": ann,
                           "type": row.get("type"), "forecast_yoy": float(yoy),
                           "surprise_pp": r["surprise_pp"], "expected": r["expected"],
                           "leg": r["leg"]})
    if not events:
        print("无可评估事件(预告/consensus/正式报 缺?)")
        sys.exit(0)

    # 前向收益(公告日起,复权序列)
    used: list[dict] = []
    for ev in events:
        code = ev["code"]
        if code not in closes:
            price = store.get_series(code)
            if len(price) < 30:
                closes[code] = None
                continue
            div = store.get_stock_dividend_series(code)
            closes[code], _ = dividend_adjusted_close(
                price["close"].astype(float), div if len(div) else None)
        s = closes[code]
        if s is None:
            continue
        fr = study.forward_returns(s, ev["announce_date"], WINDOWS)
        if fr is None:
            continue
        ev.update(fr)
        used.append(ev)

    arms = {
        "全部": study.arm_stats(used, WINDOWS),
        f"超预期(≥+{SURPRISE_PP:.0f}pp)": study.arm_stats(
            [e for e in used if e["surprise_pp"] >= SURPRISE_PP], WINDOWS),
        f"不及预期(≤-{SURPRISE_PP:.0f}pp)": study.arm_stats(   # ASCII 减号: GBK 控制台兼容
            [e for e in used if e["surprise_pp"] <= -SURPRISE_PP], WINDOWS),
    }
    base_closes = {c: s for c, s in closes.items() if s is not None}
    baseline = study.baseline_stats(base_closes, WINDOWS)
    conclusion = study.conclusion_text(arms, baseline, WINDOWS, focus=FOCUS, subject="PEAD")

    n_c = sum(1 for e in used if e["leg"] == "C")
    print(f"\n事件 {len(used)} 个(leg C {n_c} / leg A {len(used) - n_c}); 前向 {WINDOWS}")
    print(f"结论: {conclusion}")
    store.set_meta("pead_conclusion", conclusion)

    # ---- HTML ----
    fig = go.Figure()
    for (name, st), color in zip(arms.items(), (_PAL["series_1"], _PAL["good"], _PAL["critical"])):
        fig.add_trace(go.Bar(
            x=[f"{n}日" for n in WINDOWS], y=[st[n]["win_rate"] * 100 for n in WINDOWS],
            name=name, text=[f"{st[n]['win_rate'] * 100:.0f}%" for n in WINDOWS],
            textposition="outside", marker_color=color))
    fig.add_trace(go.Scatter(x=[f"{n}日" for n in WINDOWS],
                             y=[baseline[n]["win_rate"] * 100 for n in WINDOWS],
                             name="无条件基线", mode="lines+markers",
                             line=dict(color=_PAL["muted"], dash="dash")))
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

    top = sorted(used, key=lambda e: -e["surprise_pp"])[:30]
    rows_html = "".join(
        f"<tr><td>{e['announce_date']}</td><td style='text-align:left'>{e['code']}</td>"
        f"<td>{e['period']}</td><td>{e['type'] or ''}</td>"
        f"<td>{e['forecast_yoy']:.0f}%</td><td>{e['expected']:.0f}%</td>"
        f"<td style='color:{_PAL['good'] if e['surprise_pp'] > 0 else _PAL['critical']}'>"
        f"{e['surprise_pp']:+.0f}pp</td><td>{e['leg']}</td></tr>" for e in top)

    html = (
        f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>PEAD event-study</title><style>{_CSS}</style></head><body>"
        f"<h1>📣 PEAD 预告超预期漂移 event-study</h1>"
        f"<div class='meta'>{len(codes)} 只 universe · 事件 {len(used)} 个"
        f"(leg C {n_c} / leg A {len(used) - n_c}) · surprise=预告yoy−公告时点隐含预期 ·"
        f" 前向 {WINDOWS} 交易日(公告日起,分红前复权)</div>"
        f"<h2>结论(诚实,含打脸)</h2><div class='hint'>{conclusion}<br>"
        f"leg C 依赖 consensus 周度快照历史(E0 2026-08 起积累,历史事件自然落 leg A);"
        f"样本内规律非因果;A 股预告后漂移是否成立由本报告裁决,不预设。</div>"
        f"{fig.to_html(full_html=False, include_plotlyjs=True)}"
        f"<h2>臂 × horizon(胜率 / 中位收益)</h2>{tbl}"
        f"<h2>超预期 Top 30(按 surprise 降序)</h2>"
        f"<table><tr><th>公告日</th><th style='text-align:left'>代码</th><th>报告期</th>"
        f"<th>类型</th><th>预告yoy</th><th>隐含预期</th><th>surprise</th><th>腿</th></tr>"
        f"{rows_html}</table></body></html>")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"报告 -> {out}  (meta pead_conclusion 已写入)")


if __name__ == "__main__":
    main()
