"""Verify the ETF pool against live AkShare: codes exist, names match, liquidity ok.

Run:  python scripts/verify_pool.py
Prints a report; use it to correct config/etf_pool.yaml.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import fetch_etf_spot


def main():
    cfg = get_config()
    print("Fetching live ETF spot (all ETFs)...")
    spot = fetch_etf_spot()
    by_code = {r["code"]: r for _, r in spot.iterrows()}
    print(f"  total ETFs on market: {len(by_code)}\n")

    def amt_to_num(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    seen = set()
    print(f"{'code':<8}{'in_pool':<8}{'live_name':<22}{'amount(亿)':<12}{'note'}")
    print("-" * 70)

    meta = cfg.symbol_meta()
    # benchmark + risk_off + rotation (deduped, first occurrence)
    order = []
    for s in (cfg.benchmark_symbol, cfg.risk_off_symbol):
        if s not in seen:
            seen.add(s); order.append(s)
    for s in cfg.rotation_symbols():
        if s not in seen:
            seen.add(s); order.append(s)

    dups_in_pool = [s for s in cfg.pool.get("rotation_pool", []) if sum(
        1 for x in cfg.pool["rotation_pool"] if str(x["symbol"]) == str(s["symbol"])
    ) > 1]

    for s in order:
        row = by_code.get(s)
        role = meta.get(s, {}).get("role", "?")
        if row is None:
            print(f"{s:<8}{'MISS':<8}{'-':<22}{'-':<12}NOT FOUND on market (role={role})")
        else:
            name = str(row.get("name", "?"))
            amt = amt_to_num(row.get("成交额", 0)) / 1e8
            note = []
            if amt < 0.5:
                note.append("LOW-LIQUIDITY")
            print(f"{s:<8}{'ok':<8}{name:<22}{amt:<12.2f}{', '.join(note) if note else role}")
    if dups_in_pool:
        dup_syms = sorted({str(x['symbol']) for x in dups_in_pool})
        print(f"\n[!] Duplicate symbols in rotation_pool: {dup_syms}  -> dedupe needed")


if __name__ == "__main__":
    main()
