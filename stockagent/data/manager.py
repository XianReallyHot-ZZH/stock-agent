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
        """Daily: store today's ETF shares (基金份额) for all pool symbols.

        Uses fund_etf_scale_sse (基金份额, same metric as backfill) for SSE ETFs.
        Falls back to fund_etf_spot_em (流通份额) only for SZSE ETFs not in SSE data.
        This ensures metric consistency across the entire etf_scale table.
        """
        today = fetcher.today_str()
        pool = set(self.config.all_symbols())

        # primary: SSE 基金份额 (same metric as backfill)
        sse_rows = []
        try:
            sse_df = fetcher.fetch_etf_scale_sse(today.replace("-", ""))
            for _, r in sse_df.iterrows():
                sym = str(r["symbol"])
                if sym not in pool:
                    continue
                sh = r.get("shares")
                if pd.notna(sh):
                    sse_rows.append((sym, today, float(sh), None))
        except Exception as e:  # noqa: BLE001
            log.warning("etf_scale SSE fetch failed: %s", str(e)[:120])

        sse_syms = {r[0] for r in sse_rows}
        n = self.store.upsert_scale(sse_rows, source="sse_daily")

        # fallback: SZSE ETFs not in SSE (use spot 流通份额, best available)
        szse_missing = {s for s in pool if s.startswith("1") and s not in sse_syms}
        if szse_missing:
            try:
                spot = fetcher.fetch_etf_spot_premium()
                spot_rows = []
                for _, r in spot.iterrows():
                    code = str(r["code"])
                    if code not in szse_missing:
                        continue
                    sh = r.get("shares")
                    if pd.notna(sh):
                        prem = r.get("premium")
                        spot_rows.append((code, today, float(sh), float(prem) if pd.notna(prem) else None))
                n += self.store.upsert_scale(spot_rows, source="spot_szse")
                log.info("etf_scale SZSE fallback: +%d rows (流通份额, metric differs)", len(spot_rows))
            except Exception as e:  # noqa: BLE001
                log.warning("etf_scale SZSE fallback failed: %s", str(e)[:120])

        self.store.set_meta("last_scale_update", today)
        log.info("etf_scale updated: +%d rows (to %s, primary=SSE 基金份额)", n, today)
        return n

    def backfill_etf_scale(self, start: str, end: str, step_days: int = 1,
                           source: str = "all") -> int:
        """One-time historical backfill of ETF shares. source: 'all' | 'sse' | 'szse'.

        SSE via fund_etf_scale_sse (per-date, benchmark timeline). SZSE via
        fund_scale_daily_szse (date-range native → ONE batched fetch, far faster than
        per-date). source='szse' skips already-backfilled SSE to fill only the deep-market gap.
        """
        want_sse = source in ("all", "sse")
        want_szse = source in ("all", "szse")
        pool_szse = {s for s in self.config.all_symbols() if str(s).startswith("1")} if want_szse else set()
        total = 0

        # --- SSE: per-date (benchmark price dates as the trading-day timeline) ---
        if want_sse:
            bench = self.store.get_series(self.config.benchmark_symbol, start=start, end=end)
            days = list(bench.index)
            if not days:
                log.warning("backfill_etf_scale SSE: no benchmark dates in [%s,%s]", start, end)
            else:
                pool_sse = {s for s in self.config.all_symbols() if str(s).startswith("5")}
                sampled = days[::max(1, step_days)]
                for i, d in enumerate(sampled):
                    if i > 0:
                        time.sleep(0.4)
                    try:
                        df = fetcher.fetch_etf_scale_sse(d.replace("-", ""))
                    except Exception as e:  # noqa: BLE001
                        log.warning("scale sse %s failed: %s", d, str(e)[:80])
                        df = None
                    rows = []
                    if df is not None:
                        for _, r in df.iterrows():
                            sym = str(r["symbol"])
                            if sym not in pool_sse:
                                continue
                            sh = r.get("shares")
                            if pd.notna(sh):
                                rows.append((sym, d, float(sh), None))
                    total += self.store.upsert_scale(rows, source="sse")
                log.info("etf_scale SSE backfill: %d dates sampled, +%d rows", len(sampled), total)

        # --- SZSE: month-by-month range fetch + incremental upsert (survives interruption) ---
        if want_szse and pool_szse:
            from datetime import datetime, timedelta
            cur = datetime.strptime(str(start).replace("-", "")[:6] + "01", "%Y%m%d")
            end_dt = datetime.strptime(str(end).replace("-", "")[:6] + "01", "%Y%m%d")
            months = 0
            n_szse = 0
            while cur <= end_dt:
                ms = cur.strftime("%Y%m%d")
                nxt = cur.replace(year=cur.year + 1, month=1, day=1) if cur.month == 12 \
                    else cur.replace(month=cur.month + 1, day=1)
                me = (nxt - timedelta(days=1)).strftime("%Y%m%d")
                try:
                    df = fetcher.fetch_etf_scale_szse_range(ms, me)
                    rows = [(str(r["symbol"]), str(r["date"]), float(r["shares"]), None)
                            for _, r in df.iterrows() if str(r["symbol"]) in pool_szse]
                    if rows:
                        n_szse += self.store.upsert_scale(rows, source="szse")
                    months += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("szse %s..%s failed: %s", ms, me, str(e)[:80])
                cur = nxt
            total += n_szse
            log.info("etf_scale SZSE backfill: %d months, +%d rows (%d pool symbols)",
                     months, n_szse, len(pool_szse))

        if total:
            self.store.set_meta("last_scale_backfill", fetcher.today_str())
        return total

    # ---- ETF NAV (V3.1 research) ----
    def _nav_start(self, symbol: str, last: Optional[str]) -> str:
        if last:
            return (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        years = int(self.config.params.get("data", {}).get("history_years", 6))
        return (datetime.now() - timedelta(days=365 * years)).strftime("%Y%m%d")

    def update_etf_nav(self, symbols: Optional[list[str]] = None) -> dict:
        """Daily incremental NAV per ETF. Returns {symbol: rows_added}."""
        syms = symbols or self.config.rotation_symbols()
        results: dict[str, int] = {}
        end = fetcher.today_str().replace("-", "")
        for i, sym in enumerate(syms):
            start = self._nav_start(sym, self.store.last_nav_date(sym))
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_etf_nav(sym, start_date=start, end_date=end)
            except Exception as e:  # noqa: BLE001
                log.warning("nav fetch %s failed: %s", sym, str(e)[:120])
                results[sym] = 0
                continue
            n = self.store.upsert_nav(sym, df, source="em")
            log.info("nav %s: +%d rows (to %s)", sym, n, df.index[-1] if len(df) else "?")
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_nav_update", fetcher.today_str())
        return results

    def backfill_etf_nav(self, start: str, end: str) -> int:
        """One-time historical NAV backfill (fund_etf_fund_info_em takes a date range natively)."""
        pool = self.config.all_symbols()
        s, e = start.replace("-", ""), end.replace("-", "")
        total = 0
        for i, sym in enumerate(pool):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_etf_nav(sym, start_date=s, end_date=e)
            except Exception as ex:  # noqa: BLE001
                log.warning("nav backfill %s failed: %s", sym, str(ex)[:100])
                continue
            total += self.store.upsert_nav(sym, df, source="em")
        self.store.set_meta("last_nav_backfill", fetcher.today_str())
        log.info("nav backfill: +%d rows across %d symbols", total, len(pool))
        return total

    # ---- Industry PE (V3.1 research) ----
    def update_industry_pe(self, date: Optional[str] = None) -> int:
        """Daily: store all CSRC industries' PE for one date. One fetch covers every sector."""
        date = date or fetcher.today_str()
        try:
            df = fetcher.fetch_industry_pe(date.replace("-", ""))
        except Exception as e:  # noqa: BLE001
            log.warning("industry_pe fetch failed: %s", str(e)[:120])
            return 0
        rows = [(r["industry"], date, r.get("pe"), r.get("pe_median"))
                for _, r in df.iterrows() if pd.notna(r.get("pe"))]
        n = self.store.upsert_industry_pe(rows, source="cninfo")
        self.store.set_meta("last_industry_pe_update", date)
        log.info("industry_pe %s: +%d rows (%d industries)", date, n, len(rows))
        return n

    def backfill_industry_pe(self, start: str, end: str, step_days: int = 1,
                             sleep: float = 1.5) -> int:
        """One-time historical backfill; uses benchmark's stored price dates as the timeline.

        cninfo is throttle-prone under sustained calling — raise `sleep` (e.g. 8s) and
        `step_days` (e.g. 30=monthly) for a gentler pace that gets through. History ~2023+."""
        bench = self.store.get_series(self.config.benchmark_symbol, start=start, end=end)
        days = list(bench.index)
        if not days:
            log.warning("backfill_industry_pe: no benchmark dates in [%s,%s]", start, end)
            return 0
        sampled = days[::max(1, step_days)]
        total = 0
        ok = 0
        for i, d in enumerate(sampled):
            if i > 0:
                time.sleep(sleep)
            try:
                df = fetcher.fetch_industry_pe(d.replace("-", ""))
            except Exception as e:  # noqa: BLE001
                log.warning("industry_pe %s failed: %s", d, str(e)[:80])
                continue
            rows = [(r["industry"], d, r.get("pe"), r.get("pe_median"))
                    for _, r in df.iterrows() if pd.notna(r.get("pe"))]
            if rows:
                ok += 1
            total += self.store.upsert_industry_pe(rows, source="cninfo")
        self.store.set_meta("last_industry_pe_backfill", fetcher.today_str())
        log.info("industry_pe backfill: %d/%d dates ok, +%d rows", ok, len(sampled), total)
        return total
