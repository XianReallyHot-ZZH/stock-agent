"""Data manager: idempotent, self-healing updates of all tracked symbols.

Designed for local-first (Q16): every job asks "did I already do today's update?"
and backfills any missing days. Fetch failures are logged but never crash a run —
the engine degrades to whatever the local store already holds.
"""
from __future__ import annotations

import logging
import time
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

    # ---- fund flow (V2.3) ----
    def update_fund_flow(self, symbols: Optional[list[str]] = None) -> dict:
        """Fetch + store historical sector fund-flow per rotation ETF's sector.

        eastmoney-only (currently often throttled). Failures degrade gracefully
        (log + 0 rows) — never crashes a run. Returns {symbol: rows_added}.
        Sector name comes from config.symbol_meta()[sym]['sector'].
        """
        meta = self.config.symbol_meta()
        syms = symbols or self.config.rotation_symbols()
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            sector = meta.get(sym, {}).get("sector")
            if not sector:
                results[sym] = 0
                continue
            if i > 0:
                import time as _t
                _t.sleep(0.8)  # eastmoney fund-flow is throttle-prone
            try:
                df = fetcher.fetch_sector_fund_flow_hist(sector)
            except Exception as e:  # noqa: BLE001
                log.warning("fund_flow fetch %s (%s) failed: %s", sym, sector, str(e)[:120])
                results[sym] = 0
                continue
            n = self.store.upsert_fund_flow(sector, df, source="eastmoney")
            log.info("fund_flow %s (%s): +%d rows (to %s)",
                     sym, sector, n, df.index[-1] if len(df) else "?")
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_fund_flow_update", fetcher.today_str())
        return results

    # ---- etf scale / shares (V2.3) ----
    def update_etf_scale(self) -> int:
        """Daily: store today's ETF shares + premium for all pool symbols (via spot).
        Covers SSE + SZSE; forward-accumulates history. Idempotent + degrades gracefully."""
        today = fetcher.today_str()
        try:
            spot = fetcher.fetch_etf_spot_premium()
        except Exception as e:  # noqa: BLE001
            log.warning("etf_scale spot fetch failed: %s", str(e)[:120])
            return 0
        pool = set(self.config.all_symbols())
        rows = []
        for _, r in spot.iterrows():
            code = str(r["code"])
            if code not in pool:
                continue
            sh = r.get("shares")
            if pd.notna(sh):
                prem = r.get("premium")
                rows.append((code, today, float(sh), float(prem) if pd.notna(prem) else None))
        n = self.store.upsert_scale(rows, source="spot")
        self.store.set_meta("last_scale_update", today)
        log.info("etf_scale updated: +%d rows (to %s)", n, today)
        return n

    def backfill_etf_scale(self, start: str, end: str, step_days: int = 1) -> int:
        """One-time historical backfill of SSE ETF shares (SZSE has no history via akshare).

        Uses the benchmark's stored price dates as the trading-day timeline (robust — no
        calendar-table dependency). Fetches fund_etf_scale_sse(date) for pool SSE symbols.
        ~0.4s/date. step_days>1 (e.g. 5 = weekly) speeds it up for monthly trends.
        """
        bench = self.store.get_series(self.config.benchmark_symbol, start=start, end=end)
        days = list(bench.index)
        if not days:
            log.warning("backfill_etf_scale: no benchmark price dates in [%s,%s]", start, end)
            return 0
        pool_sse = {s for s in self.config.all_symbols() if str(s).startswith("5")}
        sampled = days[::max(1, step_days)]
        total = 0
        for i, d in enumerate(sampled):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_etf_scale_sse(d.replace("-", ""))
            except Exception as e:  # noqa: BLE001
                log.warning("scale %s failed: %s", d, str(e)[:80])
                continue
            rows = []
            for _, r in df.iterrows():
                sym = str(r["symbol"])
                if sym not in pool_sse:
                    continue
                sh = r.get("shares")
                if pd.notna(sh):
                    rows.append((sym, d, float(sh), None))
            total += self.store.upsert_scale(rows, source="sse")
        self.store.set_meta("last_scale_backfill", fetcher.today_str())
        log.info("etf_scale backfill: %d dates sampled, +%d rows", len(sampled), total)
        return total
