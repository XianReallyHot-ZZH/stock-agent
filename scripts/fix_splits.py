"""Fix split/merger discontinuities in stored price data (sina_raw → split-adjusted).

Scans all pool ETFs, detects one-day drops >25% (split events), applies ratio
adjustment to historical OHLCV. Updates the database in-place (source tag
changed to 'split_adj'). Run once after initial data load.

Usage: python scripts/fix_splits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data.split_adjust import adjust_ohlcv, detect_splits
from stockagent.utils.logging_setup import setup_logging


def main():
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)

    total_splits = 0
    total_symbols_fixed = 0

    for sym in cfg.all_symbols():
        df = store.get_series(sym)
        if len(df) < 10:
            continue

        splits = detect_splits(df["close"])
        if not splits:
            continue

        name = cfg.symbol_meta().get(sym, {}).get("name", "")
        print(f"\n{sym} {name}: {len(splits)} split(s) detected")
        for sp in splits:
            print(f"  {sp['date']}: {sp['prev_close']:.4f} -> {sp['event_close']:.4f} "
                  f"({sp['return']*100:+.1f}%) ratio={sp['ratio']:.4f}")

        # adjust OHLCV
        adjusted, _ = adjust_ohlcv(df, splits)
        # write back (upsert with new source tag)
        n = store.upsert_prices(sym, adjusted, source="split_adj")
        total_splits += len(splits)
        total_symbols_fixed += 1
        print(f"  -> adjusted + stored {n} rows (source=split_adj)")

    print(f"\n=== done: {total_splits} splits fixed across {total_symbols_fixed} ETFs ===")


if __name__ == "__main__":
    main()
