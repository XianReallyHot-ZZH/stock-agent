"""Fix split/merger discontinuities in BOTH price and share data.

Scans all pool ETFs:
1. daily_prices (OHLCV) — detects splits, applies ratio adjustment
2. etf_scale (shares) — same split events, adjusts shares inversely
   (price ÷ ratio, shares × ratio → total value preserved)

Usage: python scripts/fix_splits.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data.split_adjust import detect_splits, adjust_ohlcv
from stockagent.utils.logging_setup import setup_logging


def fix_price_splits(store, cfg):
    """Fix splits in daily_prices (OHLCV)."""
    total = 0
    for sym in cfg.all_symbols():
        df = store.get_series(sym)
        if len(df) < 10:
            continue
        splits = detect_splits(df["close"])
        if not splits:
            continue
        name = cfg.symbol_meta().get(sym, {}).get("name", "")
        print(f"\n{sym} {name}: {len(splits)} price split(s)")
        for sp in splits:
            print(f"  {sp['date']}: {sp['prev_close']:.4f} -> {sp['event_close']:.4f} "
                  f"({sp['return']*100:+.1f}%) ratio={sp['ratio']:.4f}")
        adjusted, _ = adjust_ohlcv(df, splits)
        store.upsert_prices(sym, adjusted, source="split_adj")
        total += len(splits)
    return total


def fix_share_splits(store, cfg):
    """Fix splits in etf_scale (shares) — adjust inversely to price.

    When price halves (ratio=0.5), shares double (÷ ratio = ÷ 0.5 = ×2).
    This keeps total value (price × shares) constant across the split.
    """
    total = 0
    for sym in cfg.all_symbols():
        sc = store.get_scale_series(sym)
        if len(sc) < 10:
            continue
        # detect splits from PRICE data (more reliable than share jumps)
        price_df = store.get_series(sym)
        if len(price_df) < 10:
            continue
        splits = detect_splits(price_df["close"])
        if not splits:
            continue

        name = cfg.symbol_meta().get(sym, {}).get("name", "")
        print(f"\n{sym} {name}: fixing {len(splits)} share split(s)")

        adjusted = sc.copy()
        for sp in reversed(splits):
            split_date = sp["date"]
            ratio = sp["ratio"]
            # shares adjust INVERSELY to price: shares_new = shares_old / ratio
            # For 前复权 style: all dates BEFORE split get shares × (1/ratio)
            # Wait — actually shares increase at split (more units after split).
            # For 前复权 (make continuous): pre-split shares × (1/ratio) to match post-split scale.
            inv_ratio = 1.0 / ratio
            mask = adjusted.index < split_date
            adjusted.loc[mask, "shares"] = adjusted.loc[mask, "shares"] * inv_ratio
            print(f"  {split_date}: ratio={ratio:.4f} inv_ratio={inv_ratio:.4f} {mask.sum()} rows adjusted")

        # write back
        rows = [(sym, d, float(r["shares"]), None) for d, r in adjusted.iterrows() if pd.notna(r["shares"])]
        store.upsert_scale(rows, source="split_adj")
        total += len(splits)
    return total


def main():
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)

    print("=" * 60)
    print("FIXING SPLITS IN PRICE DATA (daily_prices)")
    print("=" * 60)
    price_fixes = fix_price_splits(store, cfg)

    print("\n" + "=" * 60)
    print("FIXING SPLITS IN SHARE DATA (etf_scale)")
    print("=" * 60)
    share_fixes = fix_share_splits(store, cfg)

    print(f"\n=== done: {price_fixes} price splits + {share_fixes} share splits fixed ===")


if __name__ == "__main__":
    main()
