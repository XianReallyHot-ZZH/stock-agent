"""Plot ETF share (份额) + price history — interactive HTML with hover tooltips.

Each ETF gets a full-width row with dual y-axis:
  - Blue line (left axis): share in 亿份 (monthly)
  - Orange line (right axis): close price (monthly)
Hover over any point to see exact date + value.

Usage:
  python scripts/plot_shares.py                       # all rotation ETFs
  python scripts/plot_shares.py 512480 518880 510300  # specific symbols
  python scripts/plot_shares.py --output shares.html   # custom output path
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from plotly.subplots import make_subplots
import plotly.graph_objects as go

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser(description="Plot ETF share + price (interactive HTML)")
    ap.add_argument("symbols", nargs="*", help="specific symbols (default: all rotation pool)")
    ap.add_argument("--output", default="data/etf_shares.html", help="output HTML path")
    ap.add_argument("--start", default="2021-01-01", help="start date")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    meta = cfg.symbol_meta()
    symbols = args.symbols or cfg.rotation_symbols()

    plots = []
    for sym in symbols:
        sc = store.get_scale_series(sym, start=args.start)
        px = store.get_series(sym, start=args.start)
        if len(sc) == 0 or len(px) == 0:
            continue
        shares = sc["shares"].dropna()
        shares.index = pd.to_datetime(shares.index)
        close = px["close"].dropna()
        close.index = pd.to_datetime(close.index)
        if len(shares) < 2 or len(close) < 2:
            continue
        monthly_shares = shares.resample("ME").last().dropna() / 1e8
        monthly_price = close.resample("ME").last().dropna()
        common = monthly_shares.index.intersection(monthly_price.index)
        if len(common) < 2:
            continue
        name = meta.get(sym, {}).get("name", sym)
        sector = meta.get(sym, {}).get("sector", "")
        plots.append((sym, name, sector,
                      monthly_shares.loc[common], monthly_price.loc[common]))

    if not plots:
        print("No data found. Run: python scripts/backfill_scale.py --start 2021-01-01")
        return

    n = len(plots)
    titles = [f"{name}({sym}) · {sector}" for sym, name, sector, _, _ in plots]
    fig = make_subplots(
        rows=n, cols=1, subplot_titles=titles,
        vertical_spacing=0.5 / max(n, 1),
        specs=[[{"secondary_y": True}]] * n,
    )

    for idx, (sym, name, sector, m_shares, m_price) in enumerate(plots, 1):
        dates = [d.strftime("%Y-%m") for d in m_shares.index]

        fig.add_trace(
            go.Scatter(
                x=dates, y=m_shares.values,
                name="份额(亿份)", line=dict(color="#2563eb", width=2),
                hovertemplate="<b>%{x}</b><br>份额: %{y:.2f} 亿份<extra></extra>",
            ),
            row=idx, col=1, secondary_y=False,
        )
        fig.add_trace(
            go.Scatter(
                x=dates, y=m_price.values,
                name="价格", line=dict(color="#f97316", width=2),
                hovertemplate="<b>%{x}</b><br>价格: %{y:.4f}<extra></extra>",
            ),
            row=idx, col=1, secondary_y=True,
        )
        fig.update_yaxes(title_text="份额(亿份)", title_font=dict(color="#2563eb"),
                         row=idx, col=1, secondary_y=False)
        fig.update_yaxes(title_text="价格", title_font=dict(color="#f97316"),
                         row=idx, col=1, secondary_y=True)

    height = max(600, n * 220)
    fig.update_layout(
        title_text="ETF 份额（亿份）与价格历史（月度 · 鼠标悬停查看数值）",
        title_font_size=16, height=height, width=1100,
        showlegend=False,
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_xaxes(tickangle=-30, nticks=30, tickfont=dict(size=9))

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn")
    print(f"\n📈 {n} ETFs plotted -> {out}")
    print(f"   blue = 份额(亿份)  orange = 价格  | 鼠标悬停查看精确数值")
    print(f"   open in browser: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
