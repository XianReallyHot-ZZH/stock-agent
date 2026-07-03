"""Shadow / paper-trading mode (M5).

The strategy currently FAILS the decision gate (see DESIGN §14), so the correct
mode is SHADOW: read the daily reports, track a *paper* portfolio, do NOT trade
real money. This module computes the paper performance of the strategy since the
user began shadowing, by replaying the same backtest engine over the live window.
"""
from __future__ import annotations

from typing import Optional

from .backtest import run_backtest
from .config import Config, get_config
from .data import Store

SHADOW_START_KEY = "shadow_start"


def set_shadow_start(store: Store, date: str) -> None:
    store.set_meta(SHADOW_START_KEY, date)


def get_shadow_start(store: Store) -> Optional[str]:
    return store.get_meta(SHADOW_START_KEY)


def shadow_performance(store: Store, config: Optional[Config] = None) -> dict:
    """Paper performance of the strategy from shadow_start to latest data."""
    cfg = config or get_config()
    start = get_shadow_start(store)
    end = store.last_date(cfg.benchmark_symbol)
    if not start:
        return {"active": False, "reason": "no shadow_start set; pass --since YYYY-MM-DD"}
    if not end:
        return {"active": False, "reason": "no data; run update_data.py"}
    res = run_backtest(store, cfg, start=start, end=end)
    return {
        "active": True,
        "start": start,
        "end": end,
        "n_days": len(res.equity),
        "metrics": res.metrics,
        "benchmark": res.benchmark,
        "gate": res.gate,
    }
