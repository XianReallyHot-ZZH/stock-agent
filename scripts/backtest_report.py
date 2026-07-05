"""Comprehensive backtest report — interactive HTML with full analysis.

Outputs a single HTML with multiple sections:
1. Summary metrics (return, drawdown, Sharpe, Calmar, trades)
2. Equity curve vs benchmark (interactive, zoom/pan)
3. Drawdown curve (underwater plot)
4. Top-N drawdowns table (depth, duration, recovery)
5. Trade log (entry/exit, P&L, holding days)

Each chart is its own full-width, responsive plotly figure (no subplot
cramming/overlap). Width fills the page; height is generously tall.

Usage:
  python scripts/backtest_report.py                          # default signal (params.yaml)
  python scripts/backtest_report.py --signal value_flow      # specific signal
  python scripts/backtest_report.py --start 2024-01-01       # specific period
  python scripts/backtest_report.py --output report.html
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.backtest import run_backtest
from stockagent.backtest.sweep import make_config
from stockagent.utils.logging_setup import setup_logging

# Each chart's pixel height (~double the old per-subplot allocation) and the
# vertical gap between charts. Width is responsive (fills the page).
CHART_HEIGHT = 720
CHART_GAP = 44

_TEMPLATE = "plotly_white"
# stable palette
C_STRAT = "#2563eb"
C_BENCH = "#94a3b8"
C_DOWN = "#dc2626"
C_UP = "#16a34a"
C_BENCH_DD = "#64748b"


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


def _base_layout(title: str, height: int, hovermode: str = "x unified") -> dict:
    """Shared per-figure layout: responsive width, tall height, legend on top,
    breathing-room margins so titles/axes never clip the next chart."""
    return dict(
        title=dict(text=title, font=dict(size=15)),
        height=height,
        template=_TEMPLATE,
        hovermode=hovermode,
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=70, r=30, t=70, b=50),
    )


def equity_figure(equity: pd.Series, bench_equity, capital: float, height: int) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=equity.index, y=equity.values / capital, name="策略净值",
        line=dict(color=C_STRAT, width=1.6),
        hovertemplate="%{x|%Y-%m-%d}<br>净值: %{y:.4f}<extra>策略</extra>"))
    if bench_equity is not None:
        fig.add_trace(go.Scatter(
            x=bench_equity.index, y=bench_equity.values / capital, name="沪深300",
            line=dict(color=C_BENCH, width=1),
            hovertemplate="%{x|%Y-%m-%d}<br>净值: %{y:.4f}<extra>沪深300</extra>"))
    fig.update_layout(**_base_layout("净值曲线（策略 vs 沪深300）", height))
    fig.update_xaxes(type="date", rangeslider=dict(visible=True, thickness=0.03))
    fig.update_yaxes(title_text="净值（起始=1）", gridcolor="#e2e8f0")
    return fig


def drawdown_figure(dd: pd.Series, height: int) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values * 100, name="策略回撤",
        line=dict(color=C_DOWN, width=1), fill="tozeroy", fillcolor="rgba(220,38,38,0.12)",
        hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>策略</extra>"))
    fig.update_layout(**_base_layout("回撤曲线（Underwater Plot）", height))
    fig.update_yaxes(title_text="回撤 %", gridcolor="#e2e8f0")
    fig.update_xaxes(type="date")
    return fig


def drawdown_vs_bench_figure(dd: pd.Series, bench_dd, height: int) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=dd.index, y=dd.values * 100, name="策略",
        line=dict(color=C_DOWN, width=1.2),
        hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>策略</extra>"))
    if bench_dd is not None:
        fig.add_trace(go.Scatter(
            x=bench_dd.index, y=bench_dd.values * 100, name="沪深300",
            line=dict(color=C_BENCH_DD, width=1.2),
            hovertemplate="%{x|%Y-%m-%d}<br>回撤: %{y:.1f}%<extra>沪深300</extra>"))
    fig.update_layout(**_base_layout("回撤曲线 vs 基准", height))
    fig.update_yaxes(title_text="回撤 %", gridcolor="#e2e8f0")
    fig.update_xaxes(type="date")
    return fig


def trades_figure(trades_df: pd.DataFrame, height: int) -> go.Figure:
    fig = go.Figure()
    if len(trades_df):
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(trades_df["exit_date"]), y=trades_df["pnl_pct"],
            mode="markers", name="交易",
            marker=dict(
                color=[C_UP if p > 0 else C_DOWN for p in trades_df["pnl_pct"]],
                size=trades_df["days"].clip(4, 16), opacity=0.75,
                line=dict(width=0.5, color="white")),
            customdata=trades_df[["name", "entry_date", "days"]].values,
            hovertemplate=("%{customdata[0]}<br>%{customdata[1]} → %{x|%Y-%m-%d}<br>"
                           "%{y:+.1f}%（%{customdata[2]} 天）<extra></extra>")))
        fig.add_hline(y=0, line_dash="dash", line_color=C_BENCH)
    fig.update_layout(**_base_layout("逐笔交易 P&L", height, hovermode="closest"))
    fig.update_yaxes(title_text="收益率 %", gridcolor="#e2e8f0")
    fig.update_xaxes(type="date")
    return fig


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

    bench_equity, bench_dd = None, None
    if r.benchmark_equity is not None and len(r.benchmark_equity):
        bench_equity = r.benchmark_equity
        bench_equity.index = pd.to_datetime(bench_equity.index)
        bench_dd = compute_drawdown_series(bench_equity)

    # === 4 separate, full-width, responsive figures (no subplot overlap) ===
    figs = [
        equity_figure(equity, bench_equity, args.capital, CHART_HEIGHT),
        drawdown_figure(dd, CHART_HEIGHT),
        drawdown_vs_bench_figure(dd, bench_dd, CHART_HEIGHT),
        trades_figure(trades_df, CHART_HEIGHT),
    ]
    # first figure carries the plotly.js CDN include; the rest reuse it
    chart_blocks = []
    for i, f in enumerate(figs):
        div = f.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False),
                        config={"responsive": True})
        chart_blocks.append(f'<div class="chart-block">{div}</div>')
    charts_html = "\n".join(chart_blocks)

    # summary line
    bench_str = ""
    if r.benchmark.get("csi300_buyhold"):
        b = r.benchmark["csi300_buyhold"]
        bench_str = f" | 基准: 年化{b['annualized']:+.2%} 回撤{b['max_drawdown']:.2%}"
    summary = (
        f"年化 {m['annualized']:+.2%} | 最大回撤 {m['max_drawdown']:.2%} | "
        f"Calmar {m['calmar']:.2f} | Sharpe {m['sharpe']:.2f} | "
        f"交易 {m.get('n_trades', len(r.trades))} 笔{bench_str}"
    )

    # top drawdowns table
    dd_rows = ""
    for i, ep in enumerate(top_dd, 1):
        rec = ep["recovery_date"].strftime("%Y-%m-%d") if ep["recovery_date"] else "未恢复"
        dd_rows += (
            f"<tr><td>{i}</td><td>{ep['peak_date'].strftime('%Y-%m-%d')}</td>"
            f"<td>{ep['trough_date'].strftime('%Y-%m-%d')}</td><td>{rec}</td>"
            f"<td style='color:{C_DOWN};font-weight:bold'>{ep['depth']*100:.1f}%</td>"
            f"<td>{ep['peak_to_trough_days']}</td><td>{ep['trough_to_recovery_days'] or '—'}</td></tr>"
        )

    # trade log table (most recent 50)
    trade_rows = ""
    if len(trades_df):
        for _, t in trades_df.sort_values("exit_date", ascending=False).head(50).iterrows():
            color = C_UP if t["pnl_pct"] > 0 else C_DOWN
            trade_rows += (
                f"<tr><td>{t['name']}</td><td>{t['symbol']}</td>"
                f"<td>{t['entry_date']}</td><td>{t['exit_date']}</td>"
                f"<td>{t['entry_px']}</td><td>{t['exit_px']}</td>"
                f"<td style='color:{color};font-weight:bold'>{t['pnl_pct']:+.1f}%</td>"
                f"<td>{t['pnl']:+,}</td><td>{t['days']}</td></tr>"
            )

    html = f"""<html><head><meta charset="utf-8"><title>回测报告 · {signal_name}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f8fafc; }}
h2 {{ color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
p.summary {{ font-size: 15px; color: #334155; margin: 8px 0 20px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 13px; background: white; }}
th {{ background: #f1f5f9; padding: 8px 12px; text-align: left; border-bottom: 2px solid #cbd5e1; }}
td {{ padding: 6px 12px; border-bottom: 1px solid #e2e8f0; }}
tr:hover {{ background: #f8fafc; }}
.metrics {{ display: flex; gap: 20px; margin: 16px 0; flex-wrap: wrap; }}
.metric {{ background: white; padding: 12px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
.metric .val {{ font-size: 24px; font-weight: bold; }}
.metric .label {{ font-size: 12px; color: #64748b; }}
/* each chart fills the page width; gap prevents any vertical overlap */
.chart-block {{ width: 100%; margin: 0 0 {CHART_GAP}px 0; }}
.chart-block > div {{ width: 100% !important; max-width: 100% !important; }}
</style></head><body>
<h2>📊 回测报告 · {signal_name} · {args.start} ~ {end}</h2>
<p class="summary">{summary}</p>
<div class="metrics">
  <div class="metric"><div class="val" style="color:{C_UP if m['annualized']>0 else C_DOWN}">{m['annualized']:+.2%}</div><div class="label">年化收益</div></div>
  <div class="metric"><div class="val" style="color:{C_DOWN}">{m['max_drawdown']:.2%}</div><div class="label">最大回撤</div></div>
  <div class="metric"><div class="val">{m['calmar']:.2f}</div><div class="label">Calmar</div></div>
  <div class="metric"><div class="val">{m['sharpe']:.2f}</div><div class="label">Sharpe</div></div>
  <div class="metric"><div class="val">{m.get('n_trades', len(r.trades))}</div><div class="label">交易笔数</div></div>
  <div class="metric"><div class="val">{m.get('annual_turnover', 0):.1f}x</div><div class="label">年换手率</div></div>
</div>
<h2>📈 净值 / 回撤 / 交易</h2>
{charts_html}
<h2>📉 Top-{args.top_dd} 回撤</h2>
<table><thead><tr><th>#</th><th>峰值日</th><th>谷底日</th><th>恢复日</th><th>深度</th><th>下跌天数</th><th>恢复天数</th></tr></thead>
<tbody>{dd_rows}</tbody></table>
<h2>📋 交易记录（最近50笔）</h2>
<table><thead><tr><th>ETF</th><th>代码</th><th>买入日</th><th>卖出日</th><th>买入价</th><th>卖出价</th><th>收益率</th><th>盈亏(元)</th><th>持有天数</th></tr></thead>
<tbody>{trade_rows}</tbody></table>
</body></html>"""

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")

    print(f"\n📊 Report saved -> {out}")
    print(f"   {summary}")
    if top_dd:
        print(f"   top drawdown: {top_dd[0]['depth']*100:.1f}% "
              f"({top_dd[0]['peak_date'].strftime('%Y-%m-%d')} ~ {top_dd[0]['trough_date'].strftime('%Y-%m-%d')})")
    print(f"   trades: {len(trades_df)} round-trips")
    print(f"   open: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
