"""Initialize / refresh the local SQLite store with all tracked symbols.

Usage:
  python scripts/update_data.py            # full: calendar + all symbols
  python scripts/update_data.py --no-cal   # skip calendar refresh
  python scripts/update_data.py 512480     # only given symbols
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import DataManager, Store
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", help="optional subset of symbols")
    ap.add_argument("--no-cal", action="store_true", help="skip calendar refresh")
    args = ap.parse_args()

    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    syms = args.symbols or cfg.all_symbols()
    print(f"Updating {len(syms)} symbols -> {cfg.db_path}")
    res = dm.update_all(symbols=syms, refresh_calendar=not args.no_cal)

    store = dm.store
    print("\n=== summary ===")
    for s in syms:
        last = store.last_date(s)
        n_rows = len(store.get_series(s))
        print(f"  {s}: {n_rows:>5} rows, last={last}, +{res.get(s, 0)}")
    print(f"\nlast_full_update = {store.get_meta('last_full_update')}")


if __name__ == "__main__":
    main()
