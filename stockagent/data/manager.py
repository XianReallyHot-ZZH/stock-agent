"""Data manager: idempotent, self-healing updates of all tracked symbols.

Designed for local-first (Q16): every job asks "did I already do today's update?"
and backfills any missing days. Fetch failures are logged but never crash a run —
the engine degrades to whatever the local store already holds.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from ..config import get_config
from . import fetcher
from .calendar import Calendar
from .store import Store

log = logging.getLogger(__name__)


class DataManager:
    def __init__(self, store: Optional[Store] = None, config=None):
        self.config = config or get_config()
        self.store = store or Store(self.config.db_path)
        self.calendar = Calendar(self.store)

    # ---- calendar ----
    def refresh_calendar(self) -> int:
        try:
            n = self.calendar.refresh()
            log.info("calendar refreshed: %d trade days", n)
            return n
        except Exception as e:  # noqa: BLE001
            log.error("calendar refresh failed: %s", e)
            return 0

    # ---- per-symbol ----
    def _backfill_start(self, symbol: str) -> str:
        """Start date for fetch: last stored date + 1, or history_years ago."""
        last = self.store.last_date(symbol)
        if last:
            # resume from day after last stored
            d = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            return d.replace("-", "")
        years = int(self.config.params.get("data", {}).get("history_years", 6))
        start = (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
        return start.replace("-", "")

    def update_symbol(self, symbol: str, adjust: Optional[str] = None) -> int:
        adjust = adjust or self.config.params.get("data", {}).get("adjust", "hfq")
        start = self._backfill_start(symbol)
        end = datetime.now().strftime("%Y%m%d")
        try:
            df, source = fetcher.fetch_etf_daily(symbol, adjust=adjust, start_date=start, end_date=end)
        except Exception as e:  # noqa: BLE001
            log.warning("fetch %s failed (will use cached): %s", symbol, e)
            return 0
        n = self.store.upsert_prices(symbol, df, source=source)
        log.info("updated %s: +%d rows (to %s, src=%s)", symbol, n, df.index[-1] if len(df) else "?", source)
        return n

    def update_all(self, symbols: Optional[list[str]] = None, refresh_calendar: bool = True) -> dict:
        """Idempotent full refresh. Returns {symbol: rows_added}."""
        if refresh_calendar:
            self.refresh_calendar()
        symbols = symbols or self.config.all_symbols()
        results: dict[str, int] = {}
        for i, s in enumerate(symbols):
            if i > 0:
                import time as _t

                _t.sleep(0.6)  # be gentle to eastmoney, avoid RemoteDisconnected bursts
            results[s] = self.update_symbol(s)
        self.store.set_meta("last_full_update", fetcher.today_str())
        return results

    # ---- query ----
    def get_series(self, symbol: str, end: Optional[str] = None, lookback: int = 260):
        """Last `lookback` rows up to `end` (inclusive). end defaults to last stored date."""
        df = self.store.get_series(symbol, end=end)
        if len(df) > lookback:
            df = df.iloc[-lookback:]
        return df
