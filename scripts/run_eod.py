"""End-of-day data update (M3). Idempotent backfill; safe to run any time.

Usage: python scripts/run_eod.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.scheduler import run_eod
from stockagent.utils.logging_setup import setup_logging

if __name__ == "__main__":
    setup_logging()
    run_eod()
