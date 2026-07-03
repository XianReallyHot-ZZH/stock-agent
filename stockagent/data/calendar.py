"""Trading calendar: refresh from AkShare + query helpers over the Store."""
from __future__ import annotations

from typing import Optional

from . import fetcher
from .store import Store


class Calendar:
    def __init__(self, store: Store):
        self.store = store

    def refresh(self) -> int:
        dates = fetcher.fetch_trade_dates()
        self.store.upsert_calendar(dates)
        return len(dates)

    def is_open(self, date: str) -> Optional[bool]:
        return self.store.is_trade_day(date)

    def ensure_open(self, date: str) -> bool:
        """True if `date` is a trading day; refresh calendar once if unknown."""
        flag = self.store.is_trade_day(date)
        if flag is None:
            try:
                self.refresh()
            except Exception:
                return False
            flag = self.store.is_trade_day(date)
        return bool(flag)

    def trade_days(self, start: Optional[str] = None, end: Optional[str] = None):
        return self.store.trade_days(start, end)

    def prev_trade_day(self, date: str):
        return self.store.prev_trade_day(date)
