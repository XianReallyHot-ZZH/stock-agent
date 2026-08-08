"""Render the 西方宏观预测台账 dashboard → data/western_macro.html (ADR-0001 · Q10 · read-only 旁路).

Pipeline: (optional) extract DRAFT claims from new transcripts → auto-settle matured claims → render.
Idempotent; re-run anytime. Dashboard only (no 微信 — Q10 "先看看效果").

Usage:
  python scripts/western_macro_report.py                       # settle + render (existing claims)
  python scripts/western_macro_report.py --extract             # also extract new transcripts first
  python scripts/western_macro_report.py --extract --since 2026-01-01
"""
from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.western_macro import extract, score
from stockagent.western_macro.dashboard import render_western_macro

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "Billibili-JZ" / "western_economy"
EPISODES_JSON = ROOT / "data" / "western_economy_episodes.json"
OUT = ROOT / "data" / "western_macro.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="Render the western-macro prediction-ledger dashboard.")
    ap.add_argument("--extract", action="store_true", help="extract DRAFT claims from new transcripts first")
    ap.add_argument("--since", default=None, help="(with --extract) only episodes on/after YYYY-MM-DD")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    asof = datetime.now().strftime("%Y-%m-%d")
    asof_full = datetime.now().strftime("%Y-%m-%d %H:%M")

    if args.extract:
        paths = extract.discover_transcripts(DOCS, since=args.since)
        print(f"extract: {len(paths)} transcript(s); 并行抽取...")
        for res in extract.process_episodes_parallel(paths, store, asof_full):
            ep = res.get("episode", "?")
            if "skipped" in res:
                print(f"  {ep}: {res['skipped']}")
            else:
                print(f"  {ep}: +{res.get('claims', 0)} claims, +{res.get('rules', 0)} rules")

    nf = extract.normalize_claim_horizons(store)
    if nf:
        print(f"normalized {nf} mis-dated claim horizons (LLM off-by-year, now a render-time safety net)")
    n = score.settle_claims(store, asof=asof)["settled"]
    print(f"auto-settled {n} claims (asof {asof})")

    out = render_western_macro(store, DOCS, EPISODES_JSON, OUT, asof=asof_full)
    print(f"\n✅ rendered {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
