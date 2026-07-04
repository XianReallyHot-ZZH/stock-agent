"""Plot ETF share (份额) history — monthly, per sector ETF.

Uses the etf_scale data backfilled via backfill_scale.py. Plots a grid of
small line charts (one per ETF), share in 亿份, monthly resolution.

Usage:
  python scripts/plot_shares.py                       # all rotation ETFs
  python scripts/plot_shares.py 512480 518880 510300  # specific symbols
  python scripts/plot_shares.py --output my_shares.png
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser(description="Plot ETF share history (monthly)")
    ap.add_argument("symbols", nargs="*", help="specific symbols (default: all rotation pool)")
    ap.add_argument("--output", default="data/etf_shares.png", help="output PNG path")
    ap.add_argument("--start", default="2021-01-01", help="start date")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    meta = cfg.symbol_meta()
    symbols = args.symbols or cfg.rotation_symbols()

    # CJK font
    for f in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
        try:
            from matplotlib.font_manager import FontProperties
            if FontProperties(family=f).get_name():
                plt.rcParams["font.sans-serif"] = [f]
                break
        except Exception:
            continue
    plt.rcParams["axes.unicode_minus"] = False

    # load monthly shares per symbol
    plots = []
    for sym in symbols:
        sc = store.get_scale_series(sym, start=args.start)
        if len(sc) == 0:
            continue
        shares = sc["shares"].dropna()
        shares.index = pd.to_datetime(shares.index)
        if len(shares) < 2:
            continue
        # resample to monthly (last value per month)
        monthly = shares.resample("ME").last().dropna() / 1e8  # 亿份
        if len(monthly) < 2:
            continue
        name = meta.get(sym, {}).get("name", sym)
        sector = meta.get(sym, {}).get("sector", "")
        plots.append((sym, name, sector, monthly))

    if not plots:
        print("No share data found. Run: python scripts/backfill_scale.py --start 2021-01-01")
        return

    # grid layout — wider subplots + rotated labels for readability
    n = len(plots)
    ncols = min(3, n)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 5, nrows * 3), squeeze=False)
    fig.suptitle("ETF 份额历史（月度，亿份）", fontsize=14, fontweight="bold")

    for idx, (sym, name, sector, monthly) in enumerate(plots):
        row, col = divmod(idx, ncols)
        ax = axes[row][col]
        ax.plot(monthly.index, monthly.values, color="#2563eb", linewidth=1.2)
        ax.fill_between(monthly.index, 0, monthly.values, alpha=0.08, color="#2563eb")
        ax.set_title(f"{name}({sym})", fontsize=9, fontweight="bold")
        ax.tick_params(labelsize=7)
        # fewer ticks + rotate to prevent overlap
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")
        ax.set_ylabel("亿份", fontsize=7)
        # latest value annotation
        if len(monthly):
            last_val = monthly.iloc[-1]
            ax.annotate(f"{last_val:.0f}", (monthly.index[-1], last_val),
                        fontsize=7, color="#dc2626", fontweight="bold",
                        xytext=(3, 3), textcoords="offset points")

    # hide empty subplots
    for idx in range(n, nrows * ncols):
        row, col = divmod(idx, ncols)
        axes[row][col].set_visible(False)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=140)
    print(f"\n📈 {n} ETFs plotted -> {out}")
    print(f"   date range: {args.start} ~ latest")
    plt.close(fig)


if __name__ == "__main__":
    main()
