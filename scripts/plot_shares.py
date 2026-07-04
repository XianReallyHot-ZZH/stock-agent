"""Plot ETF share (份额) history + price — monthly, one row per ETF.

Each ETF gets a full-width row with dual y-axis:
  - Left axis (blue): share in 亿份 (monthly)
  - Right axis (orange): close price (monthly, 前复权)

Usage:
  python scripts/plot_shares.py                       # all rotation ETFs
  python scripts/plot_shares.py 512480 518880 510300  # specific symbols
  python scripts/plot_shares.py --output my_shares.png
"""
from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser(description="Plot ETF share + price history (monthly)")
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

    # load monthly shares + price per symbol
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
        monthly_shares = shares.resample("ME").last().dropna() / 1e8  # 亿份
        monthly_price = close.resample("ME").last().dropna()
        if len(monthly_shares) < 2 or len(monthly_price) < 2:
            continue
        # align on common dates
        common = monthly_shares.index.intersection(monthly_price.index)
        if len(common) < 2:
            continue
        monthly_shares = monthly_shares.loc[common]
        monthly_price = monthly_price.loc[common]
        name = meta.get(sym, {}).get("name", sym)
        sector = meta.get(sym, {}).get("sector", "")
        plots.append((sym, name, sector, monthly_shares, monthly_price))

    if not plots:
        print("No data found. Run: python scripts/backfill_scale.py --start 2021-01-01")
        return

    # one row per ETF, full width
    n = len(plots)
    fig, axes = plt.subplots(n, 1, figsize=(12, n * 2.2), squeeze=False)
    fig.suptitle("ETF 份额（亿份）与价格历史（月度）", fontsize=14, fontweight="bold", y=0.995)

    for idx, (sym, name, sector, m_shares, m_price) in enumerate(plots):
        ax = axes[idx][0]
        # left y-axis: shares (blue)
        ax.plot(m_shares.index, m_shares.values, color="#2563eb", linewidth=1.5, label="份额(亿份)")
        ax.fill_between(m_shares.index, 0, m_shares.values, alpha=0.06, color="#2563eb")
        ax.set_ylabel("份额(亿份)", fontsize=8, color="#2563eb")
        ax.tick_params(axis="y", labelsize=7, colors="#2563eb")

        # right y-axis: price (orange)
        ax2 = ax.twinx()
        ax2.plot(m_price.index, m_price.values, color="#f97316", linewidth=1.5, label="价格")
        ax2.set_ylabel("价格", fontsize=8, color="#f97316")
        ax2.tick_params(axis="y", labelsize=7, colors="#f97316")

        # title
        ax.set_title(f"{name}({sym}) · {sector}", fontsize=9, fontweight="bold", loc="left")

        # x-axis formatting
        ax.tick_params(axis="x", labelsize=7)
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        for label in ax.get_xticklabels():
            label.set_rotation(30)
            label.set_ha("right")

        # grid
        ax.grid(axis="x", alpha=0.2, linestyle="--")

        # latest value annotations
        if len(m_shares):
            ax.annotate(f"{m_shares.iloc[-1]:.0f}亿份", (m_shares.index[-1], m_shares.iloc[-1]),
                        fontsize=7, color="#2563eb", fontweight="bold",
                        xytext=(5, 3), textcoords="offset points")
        if len(m_price):
            ax2.annotate(f"{m_price.iloc[-1]:.3f}", (m_price.index[-1], m_price.iloc[-1]),
                         fontsize=7, color="#f97316", fontweight="bold",
                         xytext=(5, -10), textcoords="offset points")

    fig.tight_layout(rect=[0, 0, 1, 0.99])
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=130)
    print(f"\n📈 {n} ETFs plotted -> {out}")
    print(f"   blue = 份额(亿份)  orange = 价格  monthly resolution")
    plt.close(fig)


if __name__ == "__main__":
    main()
