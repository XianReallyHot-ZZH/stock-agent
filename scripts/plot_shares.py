"""Plot ETF share (份额) + price history — daily, interactive with zoom/pan.

Each ETF is its own full-width, responsive figure with dual y-axis:
  - Blue line (left axis): share in 亿份 (daily)
  - Orange line (right axis): close price (daily)
Hover for exact values; drag to pan; scroll to zoom; double-click to reset.

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

# Each chart's pixel height (~double the old per-ETF allocation) and the gap
# between charts. Width is responsive (fills the page).
CHART_HEIGHT = 480
CHART_GAP = 40

_TEMPLATE = "plotly_white"
C_SHARE = "#2563eb"   # blue = 份额
C_PRICE = "#f97316"   # orange = 净值


def share_figure(sym: str, name: str, sector: str,
                 shares: pd.Series, price: pd.Series, height: int) -> go.Figure:
    """One ETF: dual y-axis (份额 left / 净值 right), rangeselector + rangeslider."""
    fig = make_subplots(rows=1, cols=1, specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Scattergl(
            x=shares.index, y=shares.values, name="份额(亿份)",
            line=dict(color=C_SHARE, width=1),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>份额: %{y:.2f} 亿份<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scattergl(
            x=price.index, y=price.values, name="净值",
            line=dict(color=C_PRICE, width=1),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>净值: %{y:.4f}<extra></extra>",
        ),
        secondary_y=True,
    )
    fig.update_layout(
        title=dict(text=f"{name}({sym}) · {sector}", font=dict(size=15)),
        height=height, template=_TEMPLATE, showlegend=False, hovermode="x unified",
        margin=dict(l=70, r=70, t=60, b=40),
    )
    fig.update_yaxes(title_text="份额(亿份)", title_font=dict(color=C_SHARE),
                     secondary_y=False, gridcolor="#e2e8f0")
    fig.update_yaxes(title_text="净值", title_font=dict(color=C_PRICE),
                     secondary_y=True, showgrid=False)
    fig.update_xaxes(
        rangeselector=dict(
            buttons=list([
                dict(count=1, label="1月", step="month", stepmode="backward"),
                dict(count=3, label="3月", step="month", stepmode="backward"),
                dict(count=6, label="6月", step="month", stepmode="backward"),
                dict(count=1, label="1年", step="year", stepmode="backward"),
                dict(label="全部", step="all"),
            ]),
            bgcolor="#f0f0f0",
        ),
        rangeslider=dict(visible=True, thickness=0.03),
        type="date",
    )
    return fig


def main():
    ap = argparse.ArgumentParser(description="Plot ETF share + price (daily interactive)")
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
        daily_shares = shares / 1e8  # 亿份
        common = daily_shares.index.intersection(close.index)
        if len(common) < 2:
            continue
        name = meta.get(sym, {}).get("name", sym)
        sector = meta.get(sym, {}).get("sector", "")
        plots.append((sym, name, sector,
                      daily_shares.loc[common], close.loc[common]))

    if not plots:
        print("No data found. Run: python scripts/backfill_scale.py --start 2021-01-01")
        return

    # === one independent, full-width, responsive figure per ETF (no subplot overlap) ===
    chart_blocks = []
    for i, (sym, name, sector, d_shares, d_price) in enumerate(plots):
        fig = share_figure(sym, name, sector, d_shares, d_price, CHART_HEIGHT)
        # first figure carries the plotly.js CDN include; the rest reuse it
        div = fig.to_html(full_html=False, include_plotlyjs=("cdn" if i == 0 else False),
                          config={"responsive": True})
        chart_blocks.append(f'<div class="chart-block">{div}</div>')
    charts_html = "\n".join(chart_blocks)

    n = len(plots)
    html = f"""<html><head><meta charset="utf-8"><title>ETF 份额与净值</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; margin: 20px; background: #f8fafc; }}
h2 {{ color: #1e293b; border-bottom: 2px solid #e2e8f0; padding-bottom: 8px; }}
p.hint {{ font-size: 14px; color: #475569; margin: 8px 0 20px; }}
.legend {{ display: inline-block; padding: 4px 12px; border-radius: 6px; font-size: 13px; font-weight: bold; color: white; }}
/* each chart fills the page width; gap prevents any vertical overlap */
.chart-block {{ width: 100%; margin: 0 0 {CHART_GAP}px 0; }}
.chart-block > div {{ width: 100% !important; max-width: 100% !important; }}
</style></head><body>
<h2>📊 ETF 份额（亿份）与净值 · 日线 · {n} 只</h2>
<p class="hint">
  <span class="legend" style="background:{C_SHARE}">━ 份额(亿份) 左轴</span>
  &nbsp;<span class="legend" style="background:{C_PRICE}">━ 净值 右轴</span>
  &nbsp;&nbsp;顶部按钮：1月/3月/6月/1年/全部 · 拖拽缩放 · 双击重置
</p>
{charts_html}
</body></html>"""

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"\n📈 {n} ETFs plotted -> {out}")
    print(f"   blue = 份额(亿份)  orange = 净值  | 日线分辨率")
    print(f"   顶部按钮: 1月/3月/6月/1年/全部  | 拖拽缩放  | 双击重置")
    print(f"   open in browser: file:///{out.resolve()}")


if __name__ == "__main__":
    main()
