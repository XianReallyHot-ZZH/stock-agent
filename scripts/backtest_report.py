"""Comprehensive backtest report — interactive HTML with full analysis.

Outputs a single HTML with multiple sections:
1. Summary metrics (return, drawdown, Sharpe, Calmar, trades)
2. Equity curve vs benchmark (interactive, zoom/pan)
3. Drawdown curve (underwater plot)
4. Top-N drawdowns table (depth, duration, recovery)
5. Trade log (entry/exit, P&L, holding days)
6. Position details (current/last holdings with weights)

Usage:
  python scripts/backtest_report.py                          # default signal (reversion)
  python scripts/backtest_report.py --signal momentum_sf     # specific signal
  python scripts/backtest_report.py --start 2024-01-01       # specific period
  python scripts/backtest_report.py --output report.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.backtest import run_backtest
from stockagent.backtest.sweep import make_config
from stockagent.utils.logging_setup import setup_logging


def compute_drawdown_series(equity: pd.Series) -> pd.Series:
    """Drawdown % from running peak (negative = underwater)."""
    peak = equity.cummax()
    return (equity / peak - 1.0)


def find_top_drawdowns(dd: pd.Series, n: int = 10) -> list[dict]:
    """Find top-N drawdown episodes with start/trough/recovery dates."""
    dd = dd.copy()
    dd.index = pd.to_datetime(dd.index)
    episodes = []
    in_dd = False
    peak_date = None

    for i in range(len(dd)):
        if dd.iloc[i] >= 0 and not in_dd:
            peak_date = dd.index[i]
        elif dd.iloc[i] < 0 and not in_dd:
            in_dd = True
            trough = dd.iloc[i]
            trough_date = dd.index[i]
        elif in_dd:
            if dd.iloc[i] < trough:
                trough = dd.iloc[i]
                trough_date = dd.index[i]
            if dd.iloc[i] >= 0:
                # recovered
                episodes.append({
                    "peak_date": peak_date,
                    "trough_date": trough_date,
                    "recovery_date": dd.index[i],
                    "depth": trough,
                    "peak_to_trough_days": (trough_date - peak_date).days,
                    "trough_to_recovery_days": (dd.index[i] - trough_date).days,
                    "total_days": (dd.index[i] - peak_date).days,
                })
                in_dd = False
                peak_date = dd.index[i]

    # if still underwater at end
    if in_dd:
        episodes.append({
            "peak_date": peak_date,
            "trough_date": trough_date,
            "recovery_date": None,
            "depth": trough,
            "peak_to_trough_days": (trough_date - peak_date).days,
            "trough_to_recovery_days": None,
            "total_days": (dd.index[-1] - peak_date).days,
        })

    episodes.sort(key=lambda x: x["depth"])
    return episodes[:n]


def build_trade_log(trades: list, meta: dict) -> pd.DataFrame:
    """Build trade log with round-trip P&L and holding days."""
    rows = []
    held = {}  # symbol -> list of (date, shares, price)

    for t in trades:
        sym = t["symbol"]
        d = pd.to_datetime(t["date"])
        side = t["side"]
        px = t["price"]
        sh = t["shares"]
        name = meta.get(sym, {}).get("name", sym)

        if side == "buy":
            held.setdefault(sym, []).append({"date": d, "shares": sh, "price": px})
        elif side == "sell" and held.get(sym):
            entry = held[sym].pop(0)
            pnl = (px - entry["price"]) * sh
            pnl_pct = (px / entry["price"] - 1) * 100
            days = (d - entry["date"]).days
            rows.append({
                "symbol": sym, "name": name,
                "entry_date": entry["date"].strftime("%Y-%m-%d"),
                "exit_date": d.strftime("%Y-%m-%d"),
                "entry_px": round(entry["price"], 4),
                "exit_px": round(px, 4),
                "shares": int(sh),
                "pnl": round(pnl),
                "pnl_pct": round(pnl_pct, 1),
                "days": days,
            })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description="Comprehensive backtest report (HTML)")
    ap.add_argument("--signal", default=None, help="signal name (default: current params.yaml)")
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=1_000_000)
    ap.add_argument("--output", default="data/backtest_report.html")
    ap.add_argument("--top-dd", type=int, default=10, help="top-N drawdowns to show")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    meta = cfg.symbol_meta()

    if args.signal:
        cfg = make_config(cfg, {("rotation", "signal", "name"): args.signal})
    signal_name = cfg.params.get("rotation", {}).get("signal", {}).get("name", "momentum")

    import datetime
    end = args.end or datetime.datetime.now().strftime("%Y-%m-%d")
    print(f"Running backtest: signal={signal_name}, {args.start}..{end}")
    r = run_backtest(store, cfg, start=args.start, end=end, capital=args.capital)
    m = r.metrics

    # compute series
    equity = r.equity
    equity.index = pd.to_datetime(equity.index)
    dd = compute_drawdown_series(equity)
    top_dd = find_top_drawdowns(dd, args.top_dd)
    trades_df = build_trade_log(r.trades, meta)

    # benchmark
    bench_equity = None
    if r.benchmark_equity is not None and len(r.benchmark_equity):
        bench_equity = r.benchmark_equity
        bench_equity.index = pd.to_datetime(bench_equity.index)
        bench_dd = compute_drawdown_series(bench_equity)

    # === build HTML ===
    fig = make_subplots(
        rows=4, cols=1,
        subplot_titles=[
            "净值曲线（策略 vs 沪深300）",
            "回撤曲线（Underwater Plot）",
            "回撤曲线 vs 基准",
            "逐笔交易 P&L",
        ],
        vertical_spacing=0.08,
        row_heights=[0.35, 0.2, 0.2, 0.25],
    )

    # 1. equity curve
    fig.add_trace(go.Scatter(x=equity.index, y=equity.values / args.capital,
                             name="策略净值", line=dict(color="#2563eb", width=1.5),
                             hovertemplate="%{x|%Y-%m-%d}<br>净值: %{y:.4f}<extra>策略</extra>"),
                  row=1, col=1)
    if bench_equity is not None:
        fig.add_trace(go.Scatter(x=bench_equity.index, y=bench_equity.values / args.capital,
                                 name="沪深300", line=dict(color="#94a3b8", width=1),
                                 hovertemplate="%{x|%Y-%m-%d}<br>净值: %{y:.4f}<extra>沪深300</extra>"),
                      row=1, col=1)

    # 2. drawdown
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100,
                             name="策略回撤", line=dict(color="#dc2626", width=1),
                             fill="tozeroy", fillcolor="rgba(220,38,38,0.1)",
                             hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>策略</extra>"),
                  row=2, col=1)

    # 3. drawdown vs benchmark
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values * 100,
                             name="策略", line=dict(color="#dc2626", width=1),
                             hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>策略</extra>"),
                  row=3, col=1)
    if bench_equity is not None:
        fig.add_trace(go.Scatter(x=bench_dd.index, y=bench_dd.values * 100,
                                 name="沪深300", line=dict(color="#64748b", width=1),
                                 hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>沪深300</extra>"),
                      row=3, col=1)

    # 4. trade P&L scatter
    if len(trades_df):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(trades_df["exit_date"]), y=trades_df["pnl_pct"],
            mode="markers", name="交易",
            marker=dict(
                color=["#16a34a" if p > 0 else "#dc2626" for p in trades_df["pnl_pct"]],
                size=trades_df["days"].clip(3, 15),
                opacity=0.7,
            ),
            text=[f"{r['name']}<br>{r['entry_date']}→{r['exit_date']}<br>{r['pnl_pct']:+.1f}% ({r['days']}天)"
                  for _, r in trades_df.iterrows()],
            hovertemplate="%{text}<extra></extra>",
        ), row=4, col=1)
        fig.add_hline(y=0, line_dash="dash", line_color="#94a3b8", row=4, col=1)

    # layout
    fig.update_layout(
        title_text=f"回测报告 · {signal_name} · {args.start}~{end}",
        title_font_size=16, height=1600, width=1200,
        template="plotly_white", showlegend=True,
        hovermode="x unified",
    )
    fig.update_xaxes(type="date", rangeslider=dict(visible=True, thickness=0.02), row=1, col=1)

    # summary annotation
    bench_str = ""
    if r.benchmark.get("csi300_buyhold"):
        b = r.benchmark["csi300_buyhold"]
        bench_str = (f" | 基准: 年化{b['annualized']:+.2%} 回撤{b['max_drawdown']:.2%}")
    summary = (
        f"年化: {m['annualized']:+.2%} | 最大回撤: {m['max_drawdown']:.2%} | "
        f"Calmar: {m['calmar']:.2f} | Sharpe: {m['sharpe']:.2f} | "
        f"交易: {m.get('n_trades', len(r.trades))}笔{bench_str}"
    )
    fig.add_annotation(text=summary, xref="paper", yref="paper", x=0.5, y=1.02,
                       showarrow=False, font=dict(size=13))

    # top drawdowns table HTML
    dd_rows = ""
    for i, ep in enumerate(top_dd, 1):
        rec = ep["recovery_date"].strftime("%Y-%m-%d") if ep["recovery_date"] else "未恢复"
        dd_rows += (
            f"<tr><td>{i}</td><td>{ep['peak_date'].strftime('%Y-%m-%d')}</td>"
            f"<td>{ep['trough_date'].strftime('%Y-%m-%d')}</td><td>{rec}</td>"
            f"<td style='color:#dc2626;font-weight:bold'>{ep['depth']*100:.1f}%</td>"
            f"<td>{ep['peak_to_trough_days']}</td><td>{ep['trough_to_recovery_days'] or '—'}</td></tr>"
        )

    # trade log table HTML
    trade_rows = ""
    if len(trades_df):
        for _, t in trades_df.sort_values("exit_date", ascending=False).head(50).iterrows():
            color = "#16a34a" if t["pnl_pct"] > 0 else "#dc2626"
            trade_rows += (
                f"<tr><td>{t['name']}</td><td>{t['symbol']}</td>"
                f"<td>{t['entry_date']}</td><td>{t['exit_date']}</td>"
                f"<td>{t['entry_px']}</td><td>{t['exit_px']}</td>"
                f"<td style='color:{color};font-weight:bold'>{t['pnl_pct']:+.1f}%</td>"
                f"<td>{t['pnl']:+,}</td><td>{t['days']}</td></tr>"
            )

    html_wrap = f"""
<html><head><meta charset="utf-8"><title>回测报告 · {signal_name}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f8fafc; }}
h2 {{ color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; }}
th {{ background: #f1f5f9; padding: 8px 12px; text-align: left; border-bottom: 2px solid #cbd5e1; }}
td {{ padding: 6px 12px; border-bottom: 1px solid #e2e8f0; }}
tr:hover {{ background: #f8fafc; }}
.metrics {{ display: flex; gap: 24px; margin: 16px 0; flex-wrap: wrap; }}
.metric {{ background: white; padding: 12px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.metric .val {{ font-size: 24px; font-weight: bold; }}
.metric .label {{ font-size: 12px; color: #64748b; }}
</style></head><body>
<h2>📊 回测报告 · {signal_name} · {args.start} ~ {end}</h2>
<div class="metrics">
  <div class="metric"><div class="val" style="color:{'#16a34a' if m['annualized']>0 else '#dc2626'}">{m['annualized']:+.2%}</div><div class="label">年化收益</div></div>
  <div class="metric"><div class="val" style="color:#dc2626">{m['max_drawdown']:.2%}</div><div class="label">最大回撤</div></div>
  <div class="metric"><div class="val">{m['calmar']:.2f}</div><div class="label">Calmar</div></div>
  <div class="metric"><div class="val">{m['sharpe']:.2f}</div><div class="label">Sharpe</div></div>
  <div class="metric"><div class="val">{m.get('n_trades', len(r.trades))}</div><div class="label">交易笔数</div></div>
  <div class="metric"><div class="val">{m.get('annual_turnover', 0):.1f}x</div><div class="label">年换手率</div></div>
</div>
<h2>📉 Top-{args.top_dd} 回撤</h2>
<table><thead><tr><th>#</th><th>峰值日</th><th>谷底日</th><th>恢复日</th><th>深度</th><th>下跌天数</th><th>恢复天数</th></tr></thead>
<tbody>{dd_rows}</tbody></table>
<h2>📋 交易记录（最近50笔）</h2>
<table><thead><tr><th>ETF</th><th>代码</th><th>买入日</th><th>卖出日</th><th>买入价</th><th>卖出价</th><th>收益率</th><th>盈亏(元)</th><th>持有天数</th></tr></thead>
<tbody>{trade_rows}</tbody></table>
<h2>📈 图表</h2>
</body></html>
"""

    # write: HTML header + plotly div + tables
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn", full_html=False)
    # prepend the summary HTML, append closing
    full_html = html_wrap.replace("</body></html>", "") + out.read_text(encoding="utf-8") + "</body></html>"
    out.write_text(full_html, encoding="utf-8")

    print(f"\n📊 Report saved -> {out}")
    print(f"   {summary}")
    print(f"   top drawdown: {top_dd[0]['depth']*100:.1f}% ({top_dd[0]['peak_date'].strftime('%Y-%m-%d')} ~ {top_dd[0]['trough_date'].strftime('%Y-%m-%d')})")
    print(f"   trades: {len(trades_df)} round-trips")
    print(f"   open: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
