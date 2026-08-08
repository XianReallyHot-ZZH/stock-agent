"""Extract DRAFT claims/rules from JZ 西方经济 transcripts → wm_claims/wm_rules (ADR-0001 · Q9).

Idempotent + transcript-driven: scans docs/Billibili-JZ/western_economy/*.txt (non-segs), skips
episodes already extracted (unless --force). Output is DRAFT — confirm via the dashboard before it
counts toward the track record. Re-extract preserves human state (confirmed/vetoed).

Usage:
  python scripts/extract_western_claims.py                       # all unprocessed transcripts
  python scripts/extract_western_claims.py --since 2026-08-01    # only Aug 2026 onward
  python scripts/extract_western_claims.py --force --since 2026-08-01   # re-extract (keeps state)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.western_macro import extract

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
DOCS = Path(__file__).resolve().parent.parent / "docs" / "Billibili-JZ" / "western_economy"


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract DRAFT claims from western_economy transcripts.")
    ap.add_argument("--since", default=None, help="only episodes on/after YYYY-MM-DD")
    ap.add_argument("--force", action="store_true", help="re-extract even if already done")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    asof = datetime.now().strftime("%Y-%m-%d %H:%M")
    paths = extract.discover_transcripts(DOCS, since=args.since)
    if not paths:
        print(f"no transcripts found under {DOCS}")
        return
    print(f"{len(paths)} transcript(s) to consider (--since={args.since})")
    tot_c = tot_r = 0
    for p in paths:
        res = extract.process_episode(p, store, asof, force=args.force)
        ep = res.get("episode", p.name)
        if "skipped" in res:
            print(f"  {ep}: skip ({res['skipped']})")
        else:
            c, r = res.get("claims", 0), res.get("rules", 0)
            tot_c += c
            tot_r += r
            print(f"  {ep}: +{c} claims, +{r} rules")
    print(f"\n=== extract done: +{tot_c} claims, +{tot_r} rules ===")


if __name__ == "__main__":
    main()
