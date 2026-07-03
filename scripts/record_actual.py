"""Record your ACTUAL holdings for adherence/self-discipline tracking (M4).

The system maintains a "target" portfolio (what the rules say to hold). You feed
back your *actual* holdings (weekly, or tap "已执行" after acting on a report);
the next morning report shows your 自律度 (adherence %) — drift between target
and reality.

Usage:
  python scripts/record_actual.py 512480:0.34 562500:0.33 512880:0.33
  python scripts/record_actual.py --file config/actual_holdings.yaml
  python scripts/record_actual.py --executed   # mark: I did what today's report said
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.state import State
from stockagent.utils.logging_setup import setup_logging


def parse_pairs(pairs: list[str]) -> dict:
    out = {}
    for p in pairs:
        sym, w = p.split(":")
        out[sym.strip()] = float(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="*", help="symbol:weight pairs, e.g. 512480:0.33")
    ap.add_argument("--file", help="yaml/json file with {symbol: weight}")
    ap.add_argument("--executed", action="store_true",
                    help="shortcut: record today's TARGET as actual (you followed the report)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    state = State(cfg.db_path)
    store = Store(cfg.db_path)

    as_of = store.last_date(cfg.benchmark_symbol)
    if not as_of:
        print("no data; run update_data.py first")
        return

    if args.executed:
        holdings = {s: info["weight"] for s, info in state.get_target_holdings().items()}
        print(f"[--executed] recording target as actual for {as_of}: {holdings}")
    elif args.file:
        data = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
        holdings = {str(k): float(v) for k, v in data.items()}
    elif args.pairs:
        holdings = parse_pairs(args.pairs)
    else:
        ap.error("provide symbol:weight pairs, --file, or --executed")

    state.record_actual(as_of, holdings)
    adh = state.adherence()
    print(f"\nrecorded actual holdings for {as_of}:")
    for s, w in holdings.items():
        print(f"  {s}: {w}")
    print(f"\n🧭 自律度: {adh.get('adherence_pct')}%  (drift {adh.get('total_drift')})")
    print("target:", adh.get("target"))


if __name__ == "__main__":
    main()
