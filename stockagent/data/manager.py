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
    def _backfill_start(self, symbol: str, history_years: Optional[int] = None) -> str:
        """Start date for fetch: last stored date + 1, or history_years ago.
        history_years=None → data.history_years 配置默认(6);候选池价格腿传 3(偏离分位+回撤够用,
        2800 只省一半冷启动机时)。"""
        last = self.store.last_date(symbol)
        if last:
            # resume from day after last stored
            d = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            return d.replace("-", "")
        years = history_years or int(self.config.params.get("data", {}).get("history_years", 6))
        start = (datetime.now() - timedelta(days=365 * years)).strftime("%Y-%m-%d")
        return start.replace("-", "")

    def update_symbol(self, symbol: str, adjust: Optional[str] = None) -> int:
        adjust = adjust or self.config.params.get("data", {}).get("adjust", "hfq")
        existing = self.store.dominant_price_source(symbol)  # keep basis consistent w/ history
        start = self._backfill_start(symbol)
        end = datetime.now().strftime("%Y%m%d")
        try:
            df, source = fetcher.fetch_etf_daily(
                symbol, adjust=adjust, start_date=start, end_date=end,
                prefer_source=existing,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("fetch %s failed (will use cached): %s", symbol, e)
            return 0
        if not fetcher.is_basis_consistent(source, existing):
            # would mix 复权 bases (e.g. an hfq point into a raw history) → skip, keep cached
            # clean. A 1-day gap is preferable to a fake multi-x jump corrupting trend/MOM.
            log.warning(
                "skip %s: fetched source=%s conflicts with history basis=%s (adjust-family mismatch)",
                symbol, source, existing,
            )
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
        syms = symbols or self.config.tracked_symbols()  # 含 research_only(数据腿覆盖观察标的)
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
        syms = symbols or self.config.tracked_symbols()  # 含 research_only(研究看板标的也要净值)
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

    # ---- ETF earnings expectation (V3.2 research; informational, not in composite) ----
    def _latest_report_period(self) -> str:
        """Most recent 业绩预告 report period (YYYYMMDD) whose disclosure window is open.
        Pure logic lives in research.earnings.latest_report_period (tested there); this just
        injects datetime.now()."""
        from ..research import earnings
        return earnings.latest_report_period(datetime.now())

    def update_etf_earnings(self, symbols: Optional[list[str]] = None,
                            report_period: Optional[str] = None) -> int:
        """Fetch each ETF's holdings + the 业绩预告 forecast, aggregate, store. Quarterly cadence.
        One forecast fetch (all stocks) is reused across all ETFs; holdings fetched per ETF."""
        from ..research import earnings
        symbols = symbols or self.config.all_symbols()
        period = report_period or self._latest_report_period()
        try:
            forecast = fetcher.fetch_earnings_forecast(period)
        except Exception as e:  # noqa: BLE001
            log.warning("earnings_forecast %s failed: %s", period, str(e)[:120])
            return 0
        # Safety net against a broken/empty fetch. Annual (YYYY1231) returns ~3000 rows; interim
        # periods return far fewer (一季报/中报/三季报 are 几百) — so floor by period type, not a flat 1000.
        floor = 1000 if period.endswith("1231") else 100
        if len(forecast) < floor:
            log.warning("earnings_forecast %s too thin (%d rows <%d); skipping (try --period)",
                        period, len(forecast), floor)
            return 0
        # 全市场预告面板顺手落库(E3 三环链第一环的成分级底座; stock 层 getter 按观察池 symbol
        # 查询不受影响——多存的非观察池行只是安静躺着, 成分股约1500名字的链聚合才有历史可查)
        fc = (forecast if forecast.index.name else forecast.rename_axis("code")).reset_index()
        if "announce_date" in fc.columns:
            self.store.upsert_stock_forecast(
                list(zip(fc["code"], [period] * len(fc), fc["yoy"], fc["type"], fc["announce_date"])),
                source="em_yjyg")

        rows = []
        meta = self.config.symbol_meta()
        for sym in symbols:
            entry = meta.get(sym) or {}
            idx = entry.get("index_code")
            holdings = None
            if idx:
                cons = self.store.get_constituents(str(idx))
                if len(cons):
                    holdings = cons  # B 路线: 指数官方全成分×权重(update_constituents 落库)
            if holdings is None:
                # A 路线 fallback: top-10 重仓 — fund_portfolio_hold_em 端点 2026-08 已死
                # (JSONDecodeError, probe P1), 多半拿不到; 拿到也只覆盖 25-85% 权重(调研§5).
                try:
                    holdings = fetcher.fetch_etf_holdings(sym)
                except Exception as e:  # noqa: BLE001
                    log.warning("holdings %s failed: %s", sym, str(e)[:80])
                    holdings = None
            if holdings is None or not len(holdings):
                # 断点教训(调研§6.2): 空持仓绝不写零行 — 静默全零表曾掩盖端点死亡两个月
                log.warning("holdings %s unavailable (成分未拉取且 top-10 端点死) — skip", sym)
                continue
            sig = earnings.aggregate_earnings(holdings, forecast)
            rows.append((sym, period, sig["weighted_yoy"], sig["median_yoy"],
                         sig["bull_ratio"], sig["bear_ratio"], sig["coverage"],
                         sig["n_holdings"], sig["n_matched"]))
            wy = sig["weighted_yoy"]
            log.info("earnings %s: cov=%.0f%% n=%d w_yoy=%+.0f%%",
                     sym, sig["coverage"] * 100, sig["n_matched"],
                     wy if not pd.isna(wy) else 0.0)
        n = self.store.upsert_etf_earnings(rows, source="csindex_cons")
        self.store.set_meta("last_earnings_update", period)
        log.info("earnings update %s: %d ETFs", period, n)
        return n

    # ---- 业绩三环链 (E3): 快报 + 正式报 全市场入库 ----
    def _update_perf_panel(self, fetch_fn, table_tag: str, periods: Optional[list[str]],
                           floor: int, results_tag: str) -> dict:
        """共用: 逐期全市场拉取→入库(幂等)。期行数 < floor 跳过不写库(快报中期稀疏属常态,
        但 <10/500 视为端点半死)。V8: report 腿带扩列(eps/bvps/np_abs/rev_abs)。Returns
        {period: rows_written}."""
        periods = periods or _recent_report_periods(8)
        results: dict[str, int] = {p: 0 for p in periods}
        for i, period in enumerate(periods):
            if i:
                time.sleep(0.5)
            try:
                df = fetch_fn(period)
            except Exception as e:  # noqa: BLE001
                log.warning("%s %s failed: %s", results_tag, period, str(e)[:100])
                continue
            if len(df) < floor:
                log.info("%s %s: %d rows < %d (稀疏期常态, skip write)", results_tag, period, len(df), floor)
                continue
            d = (df if df.index.name else df.rename_axis("code")).reset_index()

            def _col(name):
                return d[name] if name in d.columns else [None] * len(d)
            if table_tag == "report":
                rows = list(zip(d["code"], [period] * len(d), _col("announce_date"),
                                _col("np_yoy"), _col("rev_yoy"), _col("eps"),
                                _col("bvps"), _col("np_abs"), _col("rev_abs")))
                results[period] = self.store.upsert_stock_report_actual(rows, source="em_yjbb")
            else:
                rows = list(zip(d["code"], [period] * len(d), _col("announce_date"),
                                _col("np_yoy"), _col("rev_yoy")))
                results[period] = self.store.upsert_stock_express(rows, source="em_yjkb")
            log.info("%s %s: %d rows", results_tag, period, results[period])
        if any(results.values()):
            self.store.set_meta(f"last_{table_tag}_update", fetcher.today_str())
        return results

    def update_stock_express(self, periods: Optional[list[str]] = None) -> dict:
        """业绩快报(stock_yjkb_em)全市场 → stock_express。快报集中年报期(深市2月底惯例),
        中期稀疏。Returns {period: rows}。"""
        return self._update_perf_panel(fetcher.fetch_stock_express, "express", periods, 10, "stock_express")

    def update_stock_report_actual(self, periods: Optional[list[str]] = None) -> dict:
        """定期报告实际值(stock_yjbb_em)全市场 → stock_report_actual(含 V8 扩列
        eps/bvps/np_abs/rev_abs——TTM/PE 自算与 PB 分位轨原料)。季度全量(数千行)。"""
        return self._update_perf_panel(fetcher.fetch_stock_report_actual, "report", periods, 500, "stock_report")

    def update_stock_balance(self, periods: Optional[list[str]] = None) -> dict:
        """全市场资产负债表汇总(stock_zcfz_em)→ stock_balance(V8 高业绩池风险筛腿)。
        按报告期整表(每期一次调用~4s), 披露未落完的期行数自然稀少(随 --fix 季度节奏重拉幂等)。
        Returns {period: rows}。"""
        periods = periods or _recent_report_periods(8)
        results: dict[str, int] = {p: 0 for p in periods}
        for i, period in enumerate(periods):
            if i:
                time.sleep(0.5)
            try:
                df = fetcher.fetch_stock_balance(period)
            except Exception as e:  # noqa: BLE001
                log.warning("stock_balance %s failed: %s", period, str(e)[:100])
                continue
            if len(df) < 100:
                log.info("stock_balance %s: %d rows < 100 (期未披露完, skip)", period, len(df))
                continue
            d = df.rename_axis("code").reset_index()

            def _col(name):
                return d[name] if name in d.columns else [None] * len(d)
            rows = list(zip(d["code"], [period] * len(d), _col("announce_date"),
                            _col("cash"), _col("receivables"), _col("inventory"),
                            _col("total_assets"), _col("total_liab"), _col("equity"),
                            _col("debt_ratio")))
            results[period] = self.store.upsert_stock_balance(rows, source="em_zcfz")
            log.info("stock_balance %s: %d rows", period, results[period])
        if any(results.values()):
            self.store.set_meta("last_stock_balance_update", fetcher.today_str())
        return results

    def update_consensus(self, min_rows: int = 1000) -> int:
        """Whole-market analyst-consensus weekly snapshot (E0, 周度节奏).

        修正动量(E4)的历史靠这里差分积累 — 冷启动 4 周, 所以这个方法独立于 E1-E3 存在并
        应最先开始跑. Empty/thin fetch 不写库(etf_earnings 静默写零行的教训, 调研报告 §6.2).
        Same-day rerun overwrites (idempotent upsert keyed by code+fetch_date).
        """
        try:
            df = fetcher.fetch_consensus_snapshot(min_rows=min_rows)
        except Exception as e:  # noqa: BLE001
            log.warning("consensus fetch failed: %s", str(e)[:120])
            return 0
        today = datetime.now().strftime("%Y%m%d")
        n = self.store.upsert_consensus(df, fetch_date=today)
        self.store.set_meta("last_consensus_update", today)
        log.info("consensus snapshot %s: %d stocks", today, n)
        return n

    def update_constituents(self, symbols: Optional[list[str]] = None) -> int:
        """Refresh index constituents+official weights for pool ETFs (E1 B 路线, 月度节奏).

        index_code/index_expect 来自 etf_pool.yaml(调研§5码表). 空结果/名称哨兵不符 →
        warn + 跳过不写库(防猜错代码与端点串台). QDII/无免费成分源标的(index_code 缺省)
        自然跳过. Returns number of indices refreshed.
        """
        meta = self.config.symbol_meta()
        symbols = symbols or self.config.rotation_symbols()
        n_ok = 0
        for i, sym in enumerate(symbols):
            entry = meta.get(sym) or {}
            idx = entry.get("index_code")
            if not idx:
                continue
            expect = str(entry.get("index_expect", "") or "")
            if i:
                time.sleep(0.3)  # be gentle to csindex
            df = fetcher.fetch_index_constituents(str(idx))
            if not len(df):
                log.warning("constituents %s(%s) empty — skipped (no write)", sym, idx)
                continue
            iname = str(df.attrs.get("index_name", ""))
            if expect and expect not in iname:
                log.warning("constituents %s(%s) sentinel mismatch: got '%s' expect '%s' — skipped",
                            sym, idx, iname, expect)
                continue
            self.store.upsert_constituents(str(idx), df)
            n_ok += 1
            log.info("constituents %s %s(%s): %d names, snapshot %s",
                     sym, iname, idx, len(df), df["snapshot_date"].iloc[0])
        log.info("constituents refresh: %d indices ok", n_ok)
        return n_ok

    # ---- Broad-index daily / valuation (V4 tracker) — 指数择时层数据 ----
    # 7 broad indices, order = 看板展示序(与 tracker.diagnose.BROAD_INDICES 同步)。
    # 000001(上证综指) for ⑦相对周期律; 000852(中证1000) 小盘补充(①偏离极值曲线等)。
    # Note 创业板指(399006)/科创50(000688)/上证综指(000001)/中证1000(000852) daily prices ARE
    # fetchable, but their PE/PB series are NOT in stock_index_pe/pb_lg's supported set (only the 3 below).
    BROAD_INDICES = ["000001", "000300", "399006", "000688", "000016", "000905", "000852"]
    INDEX_PE_NAMES = ["沪深300", "上证50", "中证500"]  # stock_index_pe_lg supported subset

    def update_index_daily(self, symbols: Optional[list[str]] = None) -> dict:
        """Fetch + store full daily OHLCV for broad indices (sina, RAW). Idempotent — sina
        returns full history each call, upsert overwrites. Returns {symbol: rows}."""
        syms = symbols or self.BROAD_INDICES
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_index_daily(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("index_daily %s failed: %s", sym, str(e)[:120])
                results[sym] = 0
                continue
            n = self.store.upsert_index_daily(sym, df, source="sina_raw")
            log.info("index_daily %s: +%d rows (to %s)", sym, n, df.index[-1] if len(df) else "?")
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_index_daily_update", fetcher.today_str())
        return results

    def update_index_pe(self, names: Optional[list[str]] = None) -> dict:
        """Fetch + store broad-index PE history (legulegu). 创业板指/科创50 NOT supported by
        stock_index_pe_lg — do not pass them. Returns {name: rows}."""
        nms = names or self.INDEX_PE_NAMES
        results: dict[str, int] = {}
        for i, nm in enumerate(nms):
            if i > 0:
                time.sleep(1.0)  # legulegu can be throttle-prone
            try:
                df = fetcher.fetch_index_pe(nm)
            except Exception as e:  # noqa: BLE001
                log.warning("index_pe %s failed: %s", nm, str(e)[:120])
                results[nm] = 0
                continue
            n = self.store.upsert_index_pe(nm, df, source="lg")
            log.info("index_pe %s: +%d rows (to %s)", nm, n, df.index[-1] if len(df) else "?")
            results[nm] = n
        if any(results.values()):
            self.store.set_meta("last_index_pe_update", fetcher.today_str())
        return results

    INDEX_PB_NAMES = ["沪深300", "上证50", "中证500"]  # stock_index_pb_lg supported (same as PE)

    def update_index_pb(self, names: Optional[list[str]] = None) -> dict:
        """Fetch + store broad-index PB history (legulegu). 创业板指/科创50 NOT supported.
        Returns {name: rows}."""
        nms = names or self.INDEX_PB_NAMES
        results: dict[str, int] = {}
        for i, nm in enumerate(nms):
            if i > 0:
                time.sleep(1.0)
            try:
                df = fetcher.fetch_index_pb(nm)
            except Exception as e:  # noqa: BLE001
                log.warning("index_pb %s failed: %s", nm, str(e)[:120])
                results[nm] = 0
                continue
            n = self.store.upsert_index_pb(nm, df, source="lg")
            log.info("index_pb %s: +%d rows (to %s)", nm, n, df.index[-1] if len(df) else "?")
            results[nm] = n
        if any(results.values()):
            self.store.set_meta("last_index_pb_update", fetcher.today_str())
        return results

    COMMODITY_VARIETIES = list(fetcher.COMMODITY_CODES.keys())  # 与 fetcher 同源(周期上游领先全集,单一数据源)

    def update_commodity_price(self, varieties: Optional[list[str]] = None,
                               start: str = "2020-01-01", end: Optional[str] = None) -> dict:
        """Fetch + store 商品现货价(日频,周期股上游领先指标,A 类强形式信号)。一次调多品种面板。"""
        varieties = varieties or self.COMMODITY_VARIETIES
        end = end or fetcher.today_str()
        try:
            df = fetcher.fetch_commodity_price(varieties, start, end)
        except Exception as e:  # noqa: BLE001
            log.warning("commodity_price failed: %s", str(e)[:120])
            return {v: 0 for v in varieties}
        results: dict[str, int] = {}
        for v in varieties:
            sub = df[df["variety"] == v]
            rows = [(v, r["date"], r["close"]) for _, r in sub.iterrows()]
            n = self.store.upsert_commodity_price(rows, source="akshare_futures")
            results[v] = n
            log.info("commodity %s: +%d rows (to %s)", v, n, sub["date"].iloc[-1] if len(sub) else "?")
        if any(results.values()):
            self.store.set_meta("last_commodity_update", fetcher.today_str())
        return results

    def update_commodity_spot(self, varieties: Optional[list[str]] = None) -> dict:
        """Fetch + store 商品实时快照(盘前可调:price=昨夜夜盘收盘价)。看板隔夜变动列数据腿。
        与最近日收盘相除=隔夜变动%(在 stock_report 渲染层算,这里只存快照)。"""
        varieties = varieties or self.COMMODITY_VARIETIES
        try:
            df = fetcher.fetch_commodity_spot(varieties)
        except Exception as e:  # noqa: BLE001
            log.warning("commodity_spot failed: %s", str(e)[:120])
            return {v: 0 for v in varieties}
        today = fetcher.today_str()
        rows = [(r["variety"], today, r["price"], r["quote_time"]) for _, r in df.iterrows()]
        n = self.store.upsert_commodity_spot(rows, source="akshare_futures_spot")
        self.store.set_meta("last_commodity_spot_update", today)
        log.info("commodity_spot: %d varieties snapshotted", n)
        return {r["variety"]: 1 for _, r in df.iterrows()}

    COMMODITY_BENCHMARK_SYMBOLS = sorted({s for s, _, _ in fetcher.COMMODITY_BENCHMARKS.values()})

    def update_commodity_benchmarks(self, symbols: Optional[list[str]] = None) -> dict:
        """Fetch + store 国际基准日线(第八看板面板主语)。写 western_macro_series source='fut'——
        与 western 腿同表幂等(GC/SI/CL 双腿都会刷,谁后跑谁新,无冲突);本腿独立 meta 保证
        大宗商品看板的新鲜度不依赖宏观看板的刷新节奏。返回 {symbol: 最新日期}。"""
        symbols = symbols or self.COMMODITY_BENCHMARK_SYMBOLS
        try:
            df = fetcher.fetch_commodity_benchmarks(symbols)
        except Exception as e:  # noqa: BLE001
            log.warning("commodity_benchmarks failed: %s", str(e)[:120])
            return {}
        n = self.store.upsert_western_macro(df, source_tag="commodity_bench")
        self.store.set_meta("last_commodity_benchmark_update", fetcher.today_str())
        per = {str(k): str(v) for k, v in df.groupby("symbol")["date"].max().items()}
        log.info("commodity_benchmarks: +%d rows (to %s)", n, per)
        return per

    def update_commodity_index(self, names: Optional[list[str]] = None) -> dict:
        """Fetch + store 中证商品指数(第八看板官方总览;南华 akshare 端点已死,ccidx 官方源替代)。"""
        names = names or list(fetcher.CCIDX_INDEXES.keys())
        results: dict[str, int] = {}
        for nm in names:
            try:
                df = fetcher.fetch_commodity_index(nm)
            except Exception as e:  # noqa: BLE001
                log.warning("commodity_index %s failed: %s", nm, str(e)[:120])
                continue
            rows = [(nm, r["date"], r["close"], r.get("pct")) for _, r in df.iterrows()]
            results[nm] = self.store.upsert_commodity_index(rows, source="ccidx")
            log.info("commodity_index %s: +%d rows (to %s)", nm, results[nm],
                     df["date"].iloc[-1] if len(df) else "?")
        if results:
            self.store.set_meta("last_commodity_index_update", fetcher.today_str())
        return results

    def update_commodity_basis(self, start: str = "2019-01-01",
                               end: Optional[str] = None) -> int:
        """Fetch + store 基差+期限结构(100ppi,二期剩余)。按半年窗分段(端点逐日请求,≈1.4min/年),
        从库内最新日期增量(全史幂等重拉代价 ~11min,避免)。返回新增行数。"""
        end = end or fetcher.today_str()
        last = self.store.last_commodity_basis_date()
        cur = max(start, (last or start))
        total = 0
        while cur < end:
            w_end = min(pd.Timestamp(cur) + pd.DateOffset(months=6) - pd.Timedelta(days=1),
                        pd.Timestamp(end)).strftime("%Y-%m-%d")
            try:
                df = fetcher.fetch_commodity_basis(cur, w_end)
            except Exception as e:  # noqa: BLE001 — 单窗失败记日志继续(重跑自愈)
                log.warning("commodity_basis %s~%s failed: %s", cur, w_end, str(e)[:120])
                cur = (pd.Timestamp(w_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                continue
            if len(df):
                rows = [(r["symbol"], r["date"], r["spot_price"], r["near_price"], r["dom_price"],
                         r["near_month"], r["dom_month"], r["dom_basis"], r["dom_basis_rate"],
                         r["near_basis_rate"]) for _, r in df.iterrows()]
                total += self.store.upsert_commodity_basis(rows, source="100ppi")
            log.info("commodity_basis %s~%s: +%d rows", cur, w_end, len(df))
            cur = (pd.Timestamp(w_end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if total:
            self.store.set_meta("last_commodity_basis_update", fetcher.today_str())
        return total

    def update_commodity_inventory(self, start: str = "2021-01-01",
                                   end: Optional[str] = None) -> int:
        """Fetch + store 郑商所仓单(周采样:每周三;CZCE 四品种 FG/SA/UR/PG——多史免费源仅此一家,
        见 fetcher 端点真相注)。从库内最新周增量;单日失败静默跳过(重跑自愈)。返回新增行数。"""
        end = end or fetcher.today_str()
        last = None
        for v in fetcher.CZCE_INVENTORY_SYMBOLS:
            s = self.store.get_commodity_inventory(v)
            if len(s):
                d = str(s.index[-1])
                last = d if last is None else max(last, d)
        cur = pd.Timestamp(max(start, (last or start))) + pd.offsets.Week(weekday=2)   # 次一个周三
        total = 0
        n_fail = 0
        while cur <= pd.Timestamp(end):
            iso = cur.strftime("%Y-%m-%d")
            try:
                df = fetcher.fetch_czce_receipts(iso.replace("-", ""))
            except Exception as e:  # noqa: BLE001
                log.warning("czce_receipts %s failed: %s", iso, str(e)[:120])
                df = pd.DataFrame()
            if len(df):
                rows = [(r["variety"], r["date"], r["volume"]) for _, r in df.iterrows()]
                total += self.store.upsert_commodity_inventory(rows, source="czce")
            else:
                n_fail += 1
            cur = cur + pd.offsets.Week(weekday=2)
        if total:
            self.store.set_meta("last_commodity_inventory_update", fetcher.today_str())
        log.info("commodity_inventory(czce weekly): +%d rows (%d empty weeks)", total, n_fail)
        return total

    def update_market_pb(self) -> int:
        """Fetch + store whole-A-market PB history + percentiles (legulegu). Single series."""
        try:
            df = fetcher.fetch_market_pb()
        except Exception as e:  # noqa: BLE001
            log.warning("market_pb failed: %s", str(e)[:120])
            return 0
        n = self.store.upsert_market_pb(df, source="lg")
        self.store.set_meta("last_market_pb_update", fetcher.today_str())
        log.info("market_pb: +%d rows (to %s)", n, df.index[-1] if len(df) else "?")
        return n

    def update_market_turnover(self) -> int:
        """Fetch + store 两市日成交额(baostock sh.000001 + sz.399001 amount 求和)。⑧地量监测数据源。"""
        try:
            df = fetcher.fetch_market_turnover()
        except Exception as e:  # noqa: BLE001
            log.warning("market_turnover failed: %s", str(e)[:120])
            return 0
        n = self.store.upsert_market_turnover(df, source="baostock")
        self.store.set_meta("last_market_turnover_update", fetcher.today_str())
        log.info("market_turnover: +%d rows (to %s)", n, df.index[-1] if len(df) else "?")
        return n

    def update_market_margin(self) -> int:
        """Fetch + store 上交所融资融券日级总量(stock_margin_sse,按年分段拉)。⑨恐惧贪婪·杠杆成分数据源。
        冷启动(空表)从 2010-03 全量回填;否则从上次末日增量(通常只当年一段)。深市历史不可得,v1 仅沪市。"""
        last = self.store.last_market_margin_date()
        start = "2010-03-01" if not last else last
        try:
            df = fetcher.fetch_market_margin(start=start, end=fetcher.today_str())
        except Exception as e:  # noqa: BLE001
            log.warning("market_margin failed: %s", str(e)[:120])
            return 0
        n = self.store.upsert_market_margin(df, source="sse")
        self.store.set_meta("last_market_margin_update", fetcher.today_str())
        log.info("market_margin: +%d rows (to %s)", n, df.index[-1] if len(df) else "?")
        return n

    def update_china_money(self) -> dict:
        """Fetch + store 中国货币条件月度数据(国内宏观看板①: M2/M1/M0 + 社融增量, 金十源)。
        两源独立容错(社融源滞后/偶发被拦不拖垮货币腿);源返回全历史 → 全量 upsert 幂等。
        Returns {money: rows, tsf: rows}。"""
        out = {"money": 0, "tsf": 0}
        try:
            rows = fetcher.fetch_china_money_supply()
            out["money"] = self.store.upsert_china_money(rows)
            self.store.set_meta("last_china_money_update", fetcher.today_str())
            log.info("china_money: +%d rows (to %s)", out["money"],
                     rows[0]["month"] if rows else "?")
        except Exception as e:  # noqa: BLE001
            log.warning("china_money failed: %s", str(e)[:120])
        try:
            rows = fetcher.fetch_china_tsf()
            out["tsf"] = self.store.upsert_china_tsf(rows)
            log.info("china_tsf: +%d rows (to %s)", out["tsf"],
                     rows[0]["month"] if rows else "?")
        except Exception as e:  # noqa: BLE001
            log.warning("china_tsf failed: %s", str(e)[:120])
        return out

    def update_china_rates(self) -> dict:
        """Fetch + store 中国利率与流动性四腿(第七看板: Shibor/FDR定盘/LPR/中债期限结构,金十源)。
        逐腿独立容错;源返回全历史 → 全量 upsert 幂等(repo 按年分段拉)。Returns {leg: rows}。"""
        out = {"shibor": 0, "repo": 0, "lpr": 0, "cnbond": 0}
        legs = [
            ("shibor", fetcher.fetch_shibor, self.store.upsert_shibor),
            ("repo", fetcher.fetch_repo_fix, self.store.upsert_repo_fix),
            ("lpr", fetcher.fetch_lpr, self.store.upsert_lpr),
            ("cnbond", fetcher.fetch_cn_bond, self.store.upsert_cn_bond),
        ]
        for name, fetch, upsert in legs:
            try:
                rows = fetch()
                out[name] = upsert(rows)
                log.info("china_rates %s: +%d rows (to %s)", name, out[name],
                         rows[-1].get("date") if rows else "?")
            except Exception as e:  # noqa: BLE001
                log.warning("china_rates %s failed: %s", name, str(e)[:120])
            time.sleep(0.3)
        if any(out.values()):
            self.store.set_meta("last_china_rates_update", fetcher.today_str())
        return out

    def update_cb_balance(self) -> dict:
        """Fetch + store 央行资产负债表(第七看板,月频滞后~1月;claim_odc=OMO/MLF 余额)。幂等。"""
        out = {"cb_balance": 0}
        try:
            rows = fetcher.fetch_cb_balance()
            out["cb_balance"] = self.store.upsert_cb_balance(rows)
            self.store.set_meta("last_cb_balance_update", fetcher.today_str())
            log.info("cb_balance: +%d rows (to %s)", out["cb_balance"],
                     rows[-1]["month"] if rows else "?")
        except Exception as e:  # noqa: BLE001
            log.warning("cb_balance failed: %s", str(e)[:120])
        return out

    def update_lgb_issue(self) -> dict:
        """Fetch + store 地方政府债发行明细(v2 社融可观测成分;逐券 code 主键幂等,
        按月窗全量重拉 ~60 窗 ~1-2min)。失败月跳过不致命(重跑自愈)。"""
        out = {"lgb": 0}
        try:
            rows = fetcher.fetch_lgb_issue()
            out["lgb"] = self.store.upsert_lgb_issue(rows)
            self.store.set_meta("last_lgb_issue_update", fetcher.today_str())
            log.info("lgb_issue: +%d rows", out["lgb"])
        except Exception as e:  # noqa: BLE001
            log.warning("lgb_issue failed: %s", str(e)[:120])
        return out

    def update_tsy_issue(self) -> dict:
        """Fetch + store 国债发行明细(远期批:政府债另一半;同 lgb 逐券幂等)。"""
        out = {"tsy": 0}
        try:
            rows = fetcher.fetch_tsy_issue()
            out["tsy"] = self.store.upsert_tsy_issue(rows)
            self.store.set_meta("last_tsy_issue_update", fetcher.today_str())
            log.info("tsy_issue: +%d rows", out["tsy"])
        except Exception as e:  # noqa: BLE001
            log.warning("tsy_issue failed: %s", str(e)[:120])
        return out

    def update_china_real(self) -> dict:
        """Fetch + store 通胀/实体五腿月度(cpi_yoy/ppi_yoy/pmi/retail_yoy/ind_yoy,金十各族)。
        逐腿独立容错;幂等。Returns {metric: rows}(失败腿=0)。"""
        res = fetcher.fetch_china_real()
        out = {k: 0 for k in ("cpi_yoy", "ppi_yoy", "pmi", "retail_yoy", "ind_yoy")}
        for metric, rows in res.items():
            if not rows:
                continue
            payload = [{"metric": metric, **r} for r in rows]
            out[metric] = self.store.upsert_macro_monthly(payload)
            log.info("china_real %s: +%d rows", metric, out[metric])
        if any(out.values()):
            self.store.set_meta("last_china_real_update", fetcher.today_str())
        return out

    def update_index_all(self) -> None:
        """Convenience: refresh all index-layer data (daily + PE + PB + market PB + turnover)."""
        self.update_index_daily()
        self.update_index_pe()
        self.update_index_pb()
        self.update_market_pb()
        self.update_market_turnover()

    # ---- ETF dividend (V4 tracker · 价值型股息率数据) ----
    def update_etf_dividend(self, symbols: Optional[list[str]] = None) -> dict:
        """Fetch + store ETF 分红历史(sina)。覆盖稀疏——部分 ETF 无分红数据,正常跳过(0 行)。
        价值型股息率 = 近 12 月单次分红之和(累计差分) ÷ 当前价格。"""
        syms = symbols or self.config.tracked_symbols()  # 含 research_only
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            if i > 0:
                time.sleep(0.3)
            try:
                df = fetcher.fetch_etf_dividend(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("etf_dividend %s failed: %s", sym, str(e)[:80])
                results[sym] = 0
                continue
            if len(df) == 0:
                results[sym] = 0
                continue  # 稀疏:该 ETF 无分红记录,正常
            n = self.store.upsert_etf_dividend(sym, df, source="sina")
            log.info("etf_dividend %s: +%d rows (to %s)", sym, n, df.index[-1])
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_etf_dividend_update", fetcher.today_str())
        return results

    # ---- Stock daily / valuation (V5 tracker · Phase 2 个股层数据栈) ----
    # C0 默认观察池:覆盖 4 交易所 + 三类风格,仅供数据栈联调;C1 个股诊断会迁到 config/stock_pool.yaml。
    STOCK_WATCHLIST = [
        "600519",  # 茅台  上证主板  消费·价值
        "600036",  # 招行  上证主板  金融·价值
        "300750",  # 宁德  创业板    新能源·成长
        "000651",  # 格力  深证主板  家电·价值
        "688981",  # 中芯  科创板    半导体·成长/周期
        "300760",  # 迈瑞  创业板    医疗器械·成长
        "002475",  # 立讯  深证主板  消费电子·成长
        "300124",  # 汇川  创业板    工控·成长
        "600276",  # 恒瑞  上证主板  创新药·成长
        "002460",  # 赣锋  深证主板  锂矿·周期
        "002466",  # 天齐  深证主板  锂矿·周期
        "601628",  # 国寿  上证主板  保险·价值(央企)
        # 商品周期代表(A 类商品价领先信号;commodity_map 映射上游商品,每品种 2-3 只,按板块分组)
        # —— 有色 ——
        "600362",  # 江西铜业 铜
        "000630",  # 铜陵有色 铜
        "601899",  # 紫金矿业 铜(多元化,铜近似)
        "603993",  # 洛阳钼业 铜(多元化,铜近似)
        "601600",  # 中国铝业 铝
        "000807",  # 云铝股份 铝
        "000933",  # 神火股份 铝(电解铝)
        "000060",  # 中金岭南 锌
        "600497",  # 驰宏锌锗 锌
        "601969",  # 海南矿业 铁矿石(含油,A股最接近代理)
        "000655",  # 金岭矿业 铁矿石
        "002716",  # 金贵银业 白银(冶炼)
        "000603",  # 盛达资源 白银(A股最纯代理)
        # —— 黑色 ——
        "600019",  # 宝钢股份 螺纹钢
        "600507",  # 方大特钢 螺纹钢(长材)
        "000983",  # 山西焦煤 焦煤
        "601666",  # 平煤股份 焦煤
        # —— 贵金属 ——
        "600547",  # 山东黄金 黄金
        "600916",  # 中金黄金 黄金
        # —— 能源 ——
        "601857",  # 中国石油 原油
        "600938",  # 中海油   原油
        # —— 化工建材 ——
        "601636",  # 旗滨集团 玻璃
        "600660",  # 福耀玻璃 玻璃
        "000683",  # 远兴能源 纯碱
        "600928",  # 中盐化工 纯碱
        # —— 农业 ——
        "002714",  # 牧原股份 生猪
        "300498",  # 温氏股份 生猪
        "000876",  # 新希望   生猪
    ]
    STOCK_NAMES = {  # 显示名(server/scripts 共享,避免两处维护;C1 迁 stock_pool.yaml 时带 name 字段)
        "600519": "贵州茅台", "600036": "招商银行", "300750": "宁德时代",
        "000651": "格力电器", "688981": "中芯国际",
        "300760": "迈瑞医疗", "002475": "立讯精密", "300124": "汇川技术", "600276": "恒瑞医药",
        "002460": "赣锋锂业", "002466": "天齐锂业", "601628": "中国人寿",
        "600362": "江西铜业", "000630": "铜陵有色", "601899": "紫金矿业", "603993": "洛阳钼业",
        "600547": "山东黄金", "600916": "中金黄金", "600019": "宝钢股份",
        "601857": "中国石油", "600938": "中海油",
        "601600": "中国铝业", "000060": "中金岭南", "601969": "海南矿业", "000983": "山西焦煤",
        "000603": "盛达资源", "601636": "旗滨集团", "000683": "远兴能源", "002714": "牧原股份",
        "000807": "云铝股份", "000933": "神火股份", "600497": "驰宏锌锗", "600507": "方大特钢",
        "000655": "金岭矿业", "601666": "平煤股份", "002716": "金贵银业", "600660": "福耀玻璃",
        "600928": "中盐化工", "300498": "温氏股份", "000876": "新希望",
    }

    def update_stock_daily(self, symbols: Optional[list[str]] = None,
                           adjust: Optional[str] = None,
                           history_years: Optional[int] = None) -> dict:
        """个股日线增量 → daily_prices(与 ETF 同表不同 symbol,复用 upsert_prices)。
        增量游标(_backfill_start) + basis 一致性守卫(is_basis_consistent,防 hfq/raw 混)与 ETF 路径同。
        history_years=None → 配置默认;候选池价格腿传 3(见 _backfill_start 注)。
        Returns {symbol: rows_added}。"""
        syms = symbols or self.STOCK_WATCHLIST
        adjust = adjust or self.config.params.get("data", {}).get("adjust", "hfq")
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            existing = self.store.dominant_price_source(sym)
            start = self._backfill_start(sym, history_years=history_years)
            end = fetcher.today_str().replace("-", "")
            if i > 0:
                time.sleep(0.4)
            try:
                df, source = fetcher.fetch_stock_daily(sym, adjust=adjust, start_date=start, end_date=end)
            except Exception as e:  # noqa: BLE001
                log.warning("stock_daily %s failed (will use cached): %s", sym, str(e)[:120])
                results[sym] = 0
                continue
            if not fetcher.is_basis_consistent(source, existing):
                log.warning("skip stock %s: fetched source=%s conflicts with basis=%s", sym, source, existing)
                results[sym] = 0
                continue
            n = self.store.upsert_prices(sym, df, source=source)
            log.info("stock_daily %s: +%d rows (to %s, src=%s)",
                     sym, n, df.index[-1] if len(df) else "?", source)
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_stock_daily_update", fetcher.today_str())
        return results

    def update_stock_valuation(self, symbols: Optional[list[str]] = None,
                               indicators: Optional[list[str]] = None) -> dict:
        """个股估值(百度金矿,5 指标)→ stock_valuation 表。百度 period='全部' 每次返回 IPO 起全历史
        (稀疏半月级),全量 upsert 幂等覆盖——无增量游标(数据小、全量返回,与日线不同)。
        Returns {symbol: {indicator: rows_added}}。"""
        syms = symbols or self.STOCK_WATCHLIST
        inds = indicators or list(fetcher.STOCK_VALUATION_INDICATORS)
        results: dict[str, dict] = {}
        for i, sym in enumerate(syms):
            per: dict[str, int] = {}
            for j, ind in enumerate(inds):
                if j > 0:
                    time.sleep(0.3)  # be gentle to baidu
                try:
                    df = fetcher.fetch_stock_valuation(sym, ind)
                except Exception as e:  # noqa: BLE001
                    log.warning("stock_valuation %s %s failed: %s", sym, ind, str(e)[:100])
                    per[ind] = 0
                    continue
                n = self.store.upsert_stock_valuation(sym, ind, df, source="baidu")
                log.info("stock_valuation %s %s: +%d rows (to %s)",
                         sym, ind, n, df.index[-1] if len(df) else "?")
                per[ind] = n
            results[sym] = per
            if i < len(syms) - 1:
                time.sleep(0.4)
        if any(any(p.values()) for p in results.values()):
            self.store.set_meta("last_stock_valuation_update", fetcher.today_str())
        return results

    def update_stock_financials(self, symbols: Optional[list[str]] = None) -> dict:
        """个股财务摘要(常用指标 17 项, sina)→ stock_financials 长表。sina 每次返回全历史(~102 期)
        → 全量幂等 upsert,无增量游标。Returns {symbol: rows_added}。"""
        syms = symbols or self.STOCK_WATCHLIST
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            if i > 0:
                time.sleep(0.5)  # be gentle to sina
            try:
                df = fetcher.fetch_stock_financials(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("stock_financials %s failed: %s", sym, str(e)[:120])
                results[sym] = 0
                continue
            n = self.store.upsert_stock_financials(sym, df, source="sina")
            log.info("stock_financials %s: +%d rows (%d periods × %d metrics, to %s)",
                     sym, n, df["report_period"].nunique() if len(df) else 0,
                     df["metric"].nunique() if len(df) else 0,
                     df["report_period"].max() if len(df) else "?")
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_stock_financials_update", fetcher.today_str())
        return results

    def update_stock_dividend(self, symbols: Optional[list[str]] = None) -> dict:
        """个股分红明细(sina, 只存 实施)→ stock_dividend。幂等主键 (symbol, ex_date)。
        Returns {symbol: rows_added}。部分股票无分红记录 → 0 行,正常。"""
        syms = symbols or self.STOCK_WATCHLIST
        results: dict[str, int] = {}
        for i, sym in enumerate(syms):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_stock_dividend(sym)
            except Exception as e:  # noqa: BLE001
                log.warning("stock_dividend %s failed: %s", sym, str(e)[:100])
                results[sym] = 0
                continue
            if len(df) == 0:
                results[sym] = 0
                continue  # 无分红记录,正常
            n = self.store.upsert_stock_dividend(sym, df, source="sina")
            log.info("stock_dividend %s: +%d rows (to %s)", sym, n, df.index[-1])
            results[sym] = n
        if any(results.values()):
            self.store.set_meta("last_stock_dividend_update", fetcher.today_str())
        return results

    def update_stock_forecasts(self, symbols: Optional[list[str]] = None,
                               periods: Optional[list[str]] = None) -> dict:
        """个股业绩预告(stock_yjyg_em,全市场面板按 watchlist 过滤)→ stock_forecast。每期一次市场调用,
        过滤 watchlist 后 upsert(只存观察池股票)。稀疏——稳定股常无预告(0 行正常)。
        periods 默认最近 8 个季末。Returns {period: rows_added}。"""
        syms = symbols or self.STOCK_WATCHLIST
        symset = {str(s) for s in syms}
        periods = periods or _recent_report_periods(8)
        results: dict[str, int] = {p: 0 for p in periods}
        for i, period in enumerate(periods):
            if i > 0:
                time.sleep(0.5)
            try:
                panel = fetcher.fetch_stock_forecast_panel(period)
            except Exception as e:  # noqa: BLE001
                log.warning("forecast_panel %s failed: %s", period, str(e)[:100])
                continue
            rows = [
                (str(r["code"]), period, r.get("yoy"), r.get("type"), r.get("announce_date"))
                for _, r in panel.iterrows() if str(r["code"]) in symset
            ]
            n = self.store.upsert_stock_forecast(rows, source="em_yjyg")
            results[period] = n
            log.info("stock_forecast %s: +%d rows (%d watchlist / %d market)", period, n, len(rows), len(panel))
        if any(results.values()):
            self.store.set_meta("last_stock_forecast_update", fetcher.today_str())
        return results

    # ---- Candidate-pool feeds (V7 pool · 第六看板 候选个股池 · 只读旁路 ADR-0001) ----
    def update_stock_spot(self) -> int:
        """全市场现货快照(日更·单调用)→ stock_spot。universe 的 ST/退 过滤 + 展示名唯一来源
        + V8 市值/估值列。push2 被拦时**不再静默降级**名称兜底(全市场宇宙需要市值列,
        名称-only 兜底撑不住;2026-09-12 用户批准)——fetcher 内重试 3 次后仍败返回 0,
        调用方保最后快照并显式报错。"""
        try:
            df = fetcher.fetch_stock_spot()
        except Exception as e:  # noqa: BLE001
            log.error("stock_spot FAILED after retries: %s — 保留最后快照, universe 退旧日期",
                      str(e)[:150])
            return 0
        today = fetcher.today_str()
        n = self.store.upsert_stock_spot(df, date=today, source="em_spot")
        self.store.set_meta("last_stock_spot_update", today)
        pruned = self.store.prune_stock_spot(keep_days=90)
        log.info("stock_spot %s: %d names (pruned %d old rows)", today, n, pruned)
        return n

    def update_industry_members(self, sleep: float = 0.3) -> int:
        """东财行业板块成分(~86 板块逐板块拉,月度节奏)→ industry_member(整帧全量替换)。
        单板块失败记日志跳过;全部失败 → 0 行不写库。Returns 成分总行数。"""
        try:
            boards = fetcher.fetch_industry_list()
        except Exception as e:  # noqa: BLE001
            log.warning("industry list failed: %s", str(e)[:120])
            return 0
        names = boards["industry"].tolist()
        today = fetcher.today_str()
        frames = []
        for i, board in enumerate(names):
            if i > 0:
                time.sleep(sleep)
            try:
                df = fetcher.fetch_industry_cons(board)
            except Exception as e:  # noqa: BLE001
                log.warning("industry_cons %s failed: %s", board, str(e)[:100])
                continue
            df = df.copy()
            df["industry"] = board
            frames.append(df)
        if not frames:
            log.warning("industry members: all %d boards failed — no write", len(names))
            return 0
        panel = pd.concat(frames, ignore_index=True)[["industry", "code", "name"]]
        n = self.store.upsert_industry_members(panel, snapshot_date=today)
        self.store.set_meta("last_industry_update", today)
        log.info("industry_member %s: %d rows, %d/%d boards ok", today, n, len(frames), len(names))
        return n

    def update_pool_dividends(self, symbols: list[str]) -> dict:
        """候选池分红明细(薄包装 update_stock_dividend)——运行时前复权(pool/prices.py)的
        除权事件源。分红季(5-7 月)周更,非季月 meta 门控自然跳过。Returns {symbol: rows}。"""
        results = self.update_stock_dividend(symbols)
        if any(results.values()):
            self.store.set_meta("last_pool_dividend_update", fetcher.today_str())
        return results

    # ---- Western-macro series (V6 tracker · 西方宏观预测台账 只读旁路 · ADR-0001) ----
    # 只读诊断:这些西方宏观数据永不喂 A 股轮动引擎。UST/美股指数/外盘期货/外汇 + 6 腿重算 DXY。
    # 全 AkShare(forex 单源 push2his,部分环境被拦——失败记日志不致命)。预测台账(claims/settlements)
    # 在后续阶段接入,本阶段只铺数据。
    US_INDEX_SYMBOLS_W = [".INX", ".IXIC", ".DJI"]
    FOREIGN_FUTURE_SYMBOLS_W = ["GC", "SI", "CL", "OIL"]  # 铜用沪铜(见 fetcher 注),不抓外盘铜
    DXY_FOREX_LEGS_W = ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"]

    def update_western_macro(self) -> dict:
        """抓取并入库西方宏观序列(UST/美股/外盘期货/外汇)+ 6 腿重算 DXY → western_macro_series。
        幂等(AkShare 全量返回,upsert 覆盖)。逐系列容错(失败跳过,不拖垮整批)。Returns {group: rows}。"""
        groups = {"ust": 0, "usidx": 0, "fut": 0, "forex": 0, "dxy": 0}
        try:
            df = fetcher.fetch_us_treasury()
            groups["ust"] = self.store.upsert_western_macro(df, source_tag="akshare_bond")
            log.info("western ust: +%d rows", groups["ust"])
        except Exception as e:  # noqa: BLE001
            log.warning("western ust failed: %s", str(e)[:120])
        for i, sym in enumerate(self.US_INDEX_SYMBOLS_W):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_us_index(sym)
                n = self.store.upsert_western_macro(df, source_tag="akshare_sina")
                groups["usidx"] += n
                log.info("western usidx %s: +%d rows", sym, n)
            except Exception as e:  # noqa: BLE001
                log.warning("western usidx %s failed: %s", sym, str(e)[:100])
        for i, sym in enumerate(self.FOREIGN_FUTURE_SYMBOLS_W):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_foreign_future(sym)
                n = self.store.upsert_western_macro(df, source_tag="akshare_fut")
                groups["fut"] += n
                log.info("western fut %s: +%d rows", sym, n)
            except Exception as e:  # noqa: BLE001
                log.warning("western fut %s failed: %s", sym, str(e)[:100])
        leg_closes: dict = {}
        for i, sym in enumerate(self.DXY_FOREX_LEGS_W):
            if i > 0:
                time.sleep(0.4)
            try:
                df = fetcher.fetch_forex_pair(sym)  # AkShare push2his(部分网络被拦)
                self.store.upsert_western_macro(df, source_tag="akshare_forex")
                groups["forex"] += len(df)
                leg_closes[sym] = pd.Series(df["close"].values, index=df["date"].values).astype(float)
            except Exception as e:  # noqa: BLE001
                log.warning("western forex(akshare) %s failed: %s", sym, str(e)[:100])
        if len(leg_closes) < len(self.DXY_FOREX_LEGS_W):
            # AkShare 缺腿 → fallback ECB/Frankfurter(免费无 key,一次取 6 币换算)。用户授权非 AkShare 源
            # (DXY 数据优先于"纯 AkShare"洁癖)。AkShare 在本网被拦时这成为事实外汇源。
            log.info("forex akshare incomplete (%d/6) → fallback ECB/Frankfurter", len(leg_closes))
            try:
                df = fetcher.fetch_forex_pairs_ecb()
                self.store.upsert_western_macro(df, source_tag="ecb_frankfurter")
                groups["forex"] += len(df)
                for sym in self.DXY_FOREX_LEGS_W:
                    sub = df[df["symbol"] == sym]
                    if len(sub):
                        leg_closes[sym] = pd.Series(sub["close"].values, index=sub["date"].values).astype(float)
            except Exception as e:  # noqa: BLE001
                log.warning("western forex(ECB) failed: %s", str(e)[:120])
        try:
            dxy = fetcher.reconstruct_dxy_series(leg_closes)
            if len(dxy):
                rows = pd.DataFrame([
                    {"source": "dxy", "symbol": "DXY", "date": d, "close": float(v)}
                    for d, v in dxy.items()
                ])
                groups["dxy"] = self.store.upsert_western_macro(rows, source_tag="reconstructed")
                log.info("western dxy reconstructed: +%d rows (to %s)", groups["dxy"], dxy.index[-1])
        except Exception as e:  # noqa: BLE001
            log.warning("western dxy reconstruct failed: %s", str(e)[:120])
        if any(groups.values()):
            self.store.set_meta("last_western_macro_update", fetcher.today_str())
        return groups

    def update_gold_micro(self) -> dict:
        """抓取并入库黄金微观紧缺数据(COMEX 库存/CFTC 商业持仓/央行购金)→ 专表。幂等, 逐组容错。
        JZ 框架 L2/L3/L4 证据。Returns {group: rows}。"""
        out = {"comex": 0, "cftc": 0, "cb": 0}
        # COMEX 库存(黄金+白银)
        for cn in ("黄金", "白银"):
            try:
                rows = fetcher.fetch_comex_inventory(cn)
                n = self.store.upsert_comex_inventory(rows)
                out["comex"] += n
                log.info("gold-micro comex %s: +%d rows", cn, n)
            except Exception as e:  # noqa: BLE001
                log.warning("gold-micro comex %s failed: %s", cn, str(e)[:100])
            time.sleep(0.3)
        # CFTC 非商业(投机)持仓(黄金+白银, 一次调用)
        try:
            rows = fetcher.fetch_cftc_speculative()
            n = self.store.upsert_cftc_position(rows)
            out["cftc"] = n
            log.info("gold-micro cftc(投机): +%d rows", n)
        except Exception as e:  # noqa: BLE001
            log.warning("gold-micro cftc failed: %s", str(e)[:100])
        # CFTC 商业(merchant)持仓(套保/逼空信号, 另一 COT 半)
        try:
            rows = fetcher.fetch_cftc_commercial()
            n = self.store.upsert_cftc_position(rows)
            out["cftc"] += n
            log.info("gold-micro cftc(商业): +%d rows", n)
        except Exception as e:  # noqa: BLE001
            log.warning("gold-micro cftc(商业) failed: %s", str(e)[:100])
        # 央行黄金储备(中国, 月频, 实物万盎司存量)
        try:
            rows = fetcher.fetch_cb_gold()
            n = self.store.upsert_cb_gold(rows)
            out["cb"] = n
            log.info("gold-micro cb(CN 实物): +%d rows", n)
        except Exception as e:  # noqa: BLE001
            log.warning("gold-micro cb failed: %s", str(e)[:100])
        # FRED 实际利率/通胀预期(免费CSV, 非-akshare 源, 用户授权; 黄金的死敌=实际利率)
        out["fred"] = 0
        for sid in ("DFII10", "T10YIE"):
            try:
                df = fetcher.fetch_fred_series(sid)
                n = self.store.upsert_western_macro(df, source_tag="fred")
                out["fred"] += n
                log.info("gold-micro fred %s: +%d rows", sid, n)
            except Exception as e:  # noqa: BLE001
                log.warning("gold-micro fred %s failed: %s", sid, str(e)[:100])
        # NY Fed ACM 期限溢价(XLS, 非-akshare 源·用户授权)
        try:
            df = fetcher.fetch_acm_term_premium(10)
            n = self.store.upsert_western_macro(df, source_tag="nyfed_acm")
            out["fred"] += n
            log.info("gold-micro acm 期限溢价: +%d rows", n)
        except Exception as e:  # noqa: BLE001
            log.warning("gold-micro acm failed: %s", str(e)[:100])

        if any(out.values()):
            self.store.set_meta("last_gold_micro_update", fetcher.today_str())
        return out

    def update_economic_calendar(self, days_back: int = 7, days_forward: int = 45) -> dict:
        """抓经济日历(近7天已公布+未来45天排期·源通常只给约30天, 筛重要性≥2)→ economic_calendar 表。逐日容错。"""
        out = {"calendar": 0}
        try:
            rows = fetcher.fetch_economic_calendar(days_back=days_back, days_forward=days_forward)
            out["calendar"] = self.store.upsert_economic_calendar(rows)
            log.info("economic_calendar: +%d rows", out["calendar"])
        except Exception as e:  # noqa: BLE001
            log.warning("economic_calendar failed: %s", str(e)[:120])
        if out["calendar"]:
            self.store.set_meta("last_economic_calendar_update", fetcher.today_str())
        return out


def _recent_report_periods(n: int = 8) -> list[str]:
    """最近 n 个报告期(季末 YYYYMMDD,从今天往回,降序)。季末:3-31/6-30/9-30/12-31。"""
    from datetime import date
    ends = [(12, 31), (9, 30), (6, 30), (3, 31)]  # 年内降序
    today = date.today()
    out: list[str] = []
    y = today.year
    while len(out) < n:
        for qm, qd in ends:
            key = f"{y}{qm:02d}{qd:02d}"
            d = date(y, qm, qd)
            if d <= today and key not in out:
                out.append(key)
                if len(out) >= n:
                    break
        y -= 1
    return out
