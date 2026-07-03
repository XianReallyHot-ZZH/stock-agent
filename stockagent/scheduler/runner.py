"""Idempotent + catch-up job runner (Q16).

Local-first reality: the machine WILL miss runs (sleep/off/travel). Every job here
asks "have I done this for the latest trade day?" and heals on wake. Fetch/update
failures never crash a run; the engine degrades to cached data.
"""
from __future__ import annotations

import logging

from ..config import get_config
from ..data import Store
from . import jobs

log = logging.getLogger(__name__)


def run_morning(force: bool = False, use_llm: bool = True) -> dict:
    """Morning report; catch-up: sends the latest unsent decision-date report."""
    cfg = get_config()
    store = Store(cfg.db_path)
    decision_date = store.last_date(cfg.benchmark_symbol)
    if not decision_date:
        log.error("no data; run update_data.py / eod first")
        return {"ok": False}
    return jobs.run_morning_report(config=cfg, force=force, use_llm=use_llm)


def run_eod() -> dict:
    """EOD data update; idempotent backfill, safe to call any time."""
    return jobs.run_eod_update()


def run_daily() -> dict:
    """Convenience: run EOD then morning (for a manual catch-up on wake)."""
    eod = run_eod()
    morn = run_morning()
    return {"eod": eod, "morning": morn}
