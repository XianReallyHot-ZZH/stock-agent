"""Backfill / update broad-index data for the 指数择时层 (V4 tracker).

5 宽基日线 (sina) + 沪深300/上证50/中证500 PE (legulegu) + 全市场 PB (legulegu).
Idempotent — each endpoint returns full history, upsert overwrites. Safe to re-run.

Usage:
  python scripts/backfill_index.py            # refresh all index-layer data
  python scripts/backfill_index.py --daily    # only the 5 broad-index daily OHLCV
  python scripts/backfill_index.py --pe       # only the 3 PE series
  python scripts/backfill_index.py --pb       # only whole-market PB
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.utils.logging_setup import setup_logging


def _summary(dm: DataManager):
    store = dm.store
    print("\n=== 指数层数据覆盖 ===")
    names = [("000016", "上证50"), ("000300", "沪深300"), ("000905", "中证500"),
             ("399006", "创业板指"), ("000688", "科创50")]
    for sym, nm in names:
        df = store.get_index_daily_series(sym)
        if len(df):
            print(f"  日线 {nm}({sym}): {len(df)} 行, {df.index.min()}..{df.index.max()}")
        else:
            print(f"  日线 {nm}({sym}): (无)")
    for nm in ["沪深300", "上证50", "中证500"]:
        df = store.get_index_pe_series(nm)
        if len(df):
            last_pe = float(df["pe_ttm"].iloc[-1])
            print(f"  PE   {nm}: {len(df)} 行, {df.index.min()}..{df.index.max()} | 最新TTM={last_pe:.2f}")
        else:
            print(f"  PE   {nm}: (无)")
    for nm in ["沪深300", "上证50", "中证500"]:
        df = store.get_index_pb_series(nm)
        if len(df):
            last_pb = float(df["pb"].iloc[-1])
            print(f"  PB   {nm}: {len(df)} 行, {df.index.min()}..{df.index.max()} | 最新PB={last_pb:.3f}")
        else:
            print(f"  PB   {nm}: (无)")
    df = store.get_market_pb_series()
    if len(df):
        print(f"  全市场PB: {len(df)} 行, {df.index.min()}..{df.index.max()} | "
              f"最新PB={float(df['pb'].iloc[-1]):.3f}")
    else:
        print("  全市场PB: (无)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="only 5 broad-index daily OHLCV")
    ap.add_argument("--pe", action="store_true", help="指数 PE+PB (沪深300/上证50/中证500)")
    ap.add_argument("--pb", action="store_true", help="only whole-market PB")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    selective = args.daily or args.pe or args.pb
    if args.daily or not selective:
        dm.update_index_daily()
    if args.pe or not selective:
        dm.update_index_pe()
        dm.update_index_pb()
    if args.pb or not selective:
        dm.update_market_pb()

    _summary(dm)


if __name__ == "__main__":
    main()
