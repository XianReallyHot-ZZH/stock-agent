"""Scheduled jobs: eod_update (15:30) + morning_report (08:30).

Both are idempotent + catch-up (Q16): safe to re-run; missed runs heal on wake.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import Config, get_config
from ..data import DataManager, Store
from ..engine import Engine
from ..notify import broadcast
from ..report import compose_report
from ..state import State

log = logging.getLogger(__name__)


def run_eod_update(config: Optional[Config] = None) -> dict:
    """End-of-day: refresh calendar + fetch today's bars. Idempotent (backfills)."""
    cfg = config or get_config()
    dm = DataManager(config=cfg)
    # only meaningful on trade days, but update_all is a no-op-ish backfill otherwise
    res = dm.update_all()
    log.info("eod_update done: %s", {k: v for k, v in res.items() if v})
    return res


def run_morning_report(config: Optional[Config] = None, force: bool = False, use_llm: bool = True) -> dict:
    """Generate + push the morning report based on the latest close.

    Idempotent: skips if already sent for this decision_date (unless force).
    Updates target holdings + logs send result.
    """
    cfg = config or get_config()
    store = Store(cfg.db_path)
    state = State(cfg.db_path)
    engine = Engine(store, cfg)

    decision_date = store.last_date(cfg.benchmark_symbol)
    if not decision_date:
        log.error("no data in store; run update_data.py first")
        return {"ok": False, "reason": "no_data"}

    if state.report_sent_today(decision_date) and not force:
        log.info("report for %s already sent; skip (use force=True to resend)", decision_date)
        return {"ok": True, "skipped": True, "decision_date": decision_date}

    current = state.get_target_holdings()
    sig = engine.generate_signals(decision_date, current_holdings=current)
    sig["adherence"] = state.adherence()  # M4: show self-discipline drift

    report = compose_report(sig, cfg, use_llm=use_llm)
    results = broadcast(report, title=f"stock-agent {decision_date}")

    # update target holdings for next cycle (derive entry dates for new positions)
    new_symbols = [s for s in sig["target"] if s != cfg.risk_off_symbol]
    entry_dates = state.derive_entry_dates(decision_date, list(sig["target"].keys()))
    holdings = {s: {"weight": w, "entry_date": entry_dates.get(s, decision_date)} for s, w in sig["target"].items()}
    state.set_target_holdings(decision_date, holdings)

    sent_ok = False
    for ch, ok in results.items():
        state.mark_report_sent(decision_date, ch, bool(ok))
        sent_ok = sent_ok or bool(ok)
    if not results:
        # no notifier configured: still mark attempted so we don't loop, but flag
        state.mark_report_sent(decision_date, "console", True)

    log.info("morning_report for %s: channels=%s", decision_date, results)
    return {"ok": True, "decision_date": decision_date, "channels": results, "report": report}
