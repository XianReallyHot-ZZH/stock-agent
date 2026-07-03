"""Generate + push the morning report (M3). Idempotent + catch-up.

Usage:
  python scripts/run_morning_report.py            # send (skips if already sent today)
  python scripts/run_morning_report.py --force    # resend
  python scripts/run_morning_report.py --no-llm   # template commentary only
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.scheduler import run_morning
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-llm", action="store_true")
    args = ap.parse_args()
    setup_logging()
    res = run_morning(force=args.force, use_llm=not args.no_llm)
    if res.get("report"):
        print("\n" + "=" * 50)
        print(res["report"])
        print("=" * 50)
    print("\nresult:", {k: v for k, v in res.items() if k != "report"})


if __name__ == "__main__":
    main()
