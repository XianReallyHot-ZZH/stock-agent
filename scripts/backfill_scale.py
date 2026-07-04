"""Backfill / update ETF share (scale) history (V2.3 data layer).

Historical (one-time): python scripts/backfill_scale.py --start 2021-01-01 [--step 5]
  --step N samples every N trading days (5=weekly, faster, fine for monthly trends).
  SSE-listed pool ETFs only (5xxxxx); SZSE (159xxx) accumulated forward via --today.

Daily forward:        python scripts/backfill_scale.py --today   (spot: all pool ETFs + premium)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store, fetcher
from stockagent.data.manager import DataManager
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--step", type=int, default=1, help="sample every N trading days (5=weekly)")
    ap.add_argument("--today", action="store_true", help="just store today via spot (all ETFs + premium)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    if args.today:
        n = dm.update_etf_scale()
        print(f"today spot update: +{n} rows")
    else:
        end = args.end or fetcher.today_str()
        n = dm.backfill_etf_scale(args.start, end, step_days=args.step)
        print(f"\nbackfill done: +{n} rows total")

    store = dm.store
    print("\n=== sample scale coverage ===")
    for s in ["510300", "512480", "518880", "159915"]:
        df = store.get_scale_series(s)
        if len(df):
            print(f"  {s}: {len(df)} rows, {df.index.min()}..{df.index.max()} | latest={float(df['shares'].iloc[-1])/1e8:.2f}亿份")
        else:
            print(f"  {s}: (no data)")


if __name__ == "__main__":
    main()
