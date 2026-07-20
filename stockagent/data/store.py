"""SQLite store for daily OHLCV + trade calendar + meta. Idempotent upserts."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_prices (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL,
    volume REAL, amount REAL,
    source TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS trade_calendar (
    date   TEXT PRIMARY KEY,
    is_open INTEGER
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS fund_flow (
    sector     TEXT NOT NULL,
    date       TEXT NOT NULL,
    net_inflow REAL,
    rank       INTEGER,
    source     TEXT,
    PRIMARY KEY (sector, date)
);
CREATE TABLE IF NOT EXISTS etf_scale (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    shares REAL,
    premium REAL,
    source TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS etf_nav (
    symbol   TEXT NOT NULL,
    date     TEXT NOT NULL,
    unit_nav REAL,
    acc_nav  REAL,
    source   TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS industry_pe (
    industry  TEXT NOT NULL,
    date      TEXT NOT NULL,
    pe        REAL,
    pe_median REAL,
    source    TEXT,
    PRIMARY KEY (industry, date)
);
CREATE TABLE IF NOT EXISTS etf_earnings (
    symbol        TEXT NOT NULL,
    report_period TEXT NOT NULL,
    weighted_yoy  REAL,
    median_yoy    REAL,
    bull_ratio    REAL,
    bear_ratio    REAL,
    coverage      REAL,
    n_holdings    INTEGER,
    n_matched     INTEGER,
    source        TEXT,
    PRIMARY KEY (symbol, report_period)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON daily_prices(symbol);
CREATE INDEX IF NOT EXISTS idx_fund_flow_sector ON fund_flow(sector);
CREATE INDEX IF NOT EXISTS idx_scale_symbol ON etf_scale(symbol);
CREATE INDEX IF NOT EXISTS idx_nav_symbol ON etf_nav(symbol);
CREATE INDEX IF NOT EXISTS idx_industry_pe ON industry_pe(industry);
CREATE INDEX IF NOT EXISTS idx_etf_earnings_symbol ON etf_earnings(symbol);
CREATE TABLE IF NOT EXISTS index_daily (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL, high REAL, low REAL, close REAL,
    volume REAL,
    source TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS index_pe (
    name      TEXT NOT NULL,
    date      TEXT NOT NULL,
    pe_ttm    REAL,
    pe_median REAL,
    source    TEXT,
    PRIMARY KEY (name, date)
);
CREATE TABLE IF NOT EXISTS index_pb (
    name      TEXT NOT NULL,
    date      TEXT NOT NULL,
    pb        REAL,
    pb_median REAL,
    source    TEXT,
    PRIMARY KEY (name, date)
);
CREATE TABLE IF NOT EXISTS market_pb (
    date      TEXT PRIMARY KEY,
    pb        REAL,
    pb_median REAL,
    pct_all   REAL,
    pct_10y   REAL,
    source    TEXT
);
CREATE TABLE IF NOT EXISTS etf_dividend (
    symbol              TEXT NOT NULL,
    date                TEXT NOT NULL,
    cumulative_dividend REAL,
    source              TEXT,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_index_daily_symbol ON index_daily(symbol);
CREATE INDEX IF NOT EXISTS idx_index_pe_name ON index_pe(name);
CREATE INDEX IF NOT EXISTS idx_index_pb_name ON index_pb(name);
"""


def _ensure_column(c, table: str, col: str, decl: str):
    cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_schema(self):
        with self._conn() as c:
            c.executescript(SCHEMA)
            _ensure_column(c, "daily_prices", "source", "TEXT")
            _ensure_column(c, "etf_earnings", "n_matched", "INTEGER")

    # ---- meta ----
    def get_meta(self, key: str, default=None):
        with self._conn() as c:
            row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def set_meta(self, key: str, value: str):
        with self._conn() as c:
            c.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def last_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM daily_prices WHERE symbol=?", (symbol,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def dominant_price_source(self, symbol: str) -> Optional[str]:
        """Most common source tag in this symbol's daily_prices — its de-facto 复权 basis
        (e.g. 'sina_raw'). Used to keep incremental updates on the same basis. None if no data."""
        with self._conn() as c:
            row = c.execute(
                "SELECT source FROM daily_prices WHERE symbol=? AND source IS NOT NULL "
                "GROUP BY source ORDER BY count(*) DESC, source ASC LIMIT 1",
                (symbol,),
            ).fetchone()
            return row[0] if row and row[0] else None

    # ---- prices ----
    def upsert_prices(self, symbol: str, df: pd.DataFrame, source: str = ""):
        """df indexed by date(str) with open/high/low/close/volume/amount."""
        if df is None or len(df) == 0:
            return 0
        rows = []
        for d, r in df.iterrows():
            rows.append(
                (
                    symbol,
                    str(d),
                    float(r.get("open", 0) or 0),
                    float(r.get("high", 0) or 0),
                    float(r.get("low", 0) or 0),
                    float(r.get("close", 0) or 0),
                    float(r.get("volume", 0) or 0),
                    float(r.get("amount", 0) or 0),
                    source,
                )
            )
        with self._conn() as c:
            c.executemany(
                "INSERT INTO daily_prices(symbol,date,open,high,low,close,volume,amount,source) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,amount=excluded.amount,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_series(
        self,
        symbol: str,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> pd.DataFrame:
        """Return OHLCV DataFrame indexed by date(str), ascending."""
        q = "SELECT date,open,high,low,close,volume,amount FROM daily_prices WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def symbols(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT DISTINCT symbol FROM daily_prices").fetchall()
            return [r[0] for r in rows]

    # ---- calendar ----
    def upsert_calendar(self, dates_open: Iterable[str]):
        # SQL hardcodes is_open=1 (only one `?` placeholder for date), so rows carry just the date.
        rows = [(d,) for d in dates_open]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO trade_calendar(date,is_open) VALUES(?,1) "
                "ON CONFLICT(date) DO UPDATE SET is_open=1",
                rows,
            )

    def is_trade_day(self, date: str) -> Optional[bool]:
        """None if calendar doesn't cover this date; True/False otherwise."""
        with self._conn() as c:
            row = c.execute(
                "SELECT is_open FROM trade_calendar WHERE date=?", (date,)
            ).fetchone()
            return bool(row[0]) if row else None

    def trade_days(self, start: Optional[str] = None, end: Optional[str] = None) -> list[str]:
        q = "SELECT date FROM trade_calendar WHERE is_open=1"
        params: list = []
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            return [r[0] for r in c.execute(q, params).fetchall()]

    def prev_trade_day(self, date: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM trade_calendar WHERE is_open=1 AND date<?",
                (date,),
            ).fetchone()
            return row[0] if row and row[0] else None

    # ---- fund flow (V2.3) ----
    def upsert_fund_flow(self, sector: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with net_inflow (and optional rank)."""
        if df is None or len(df) == 0:
            return 0
        rows = []
        for d, r in df.iterrows():
            rank = r.get("rank")
            rows.append(
                (sector, str(d), float(r.get("net_inflow", 0) or 0),
                 int(rank) if rank not in (None, "") and not pd.isna(rank) else None, source)
            )
        with self._conn() as c:
            c.executemany(
                "INSERT INTO fund_flow(sector,date,net_inflow,rank,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(sector,date) DO UPDATE SET "
                "net_inflow=excluded.net_inflow,rank=excluded.rank,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_fund_flow(self, sector: str, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,net_inflow,rank FROM fund_flow WHERE sector=?"
        params: list = [sector]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_fund_flow_date(self, sector: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM fund_flow WHERE sector=?", (sector,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def fund_flow_sectors(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT DISTINCT sector FROM fund_flow").fetchall()
            return [r[0] for r in rows]

    # ---- etf scale / shares (V2.3) ----
    def upsert_scale(self, rows: list[tuple], source: str = "") -> int:
        """rows: iterable of (symbol, date, shares, premium). Idempotent upsert."""
        if not rows:
            return 0
        payload = [(s, d, sh, pm, source) for (s, d, sh, pm) in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO etf_scale(symbol,date,shares,premium,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET "
                "shares=excluded.shares,premium=excluded.premium,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_scale_series(self, symbol: str, start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,shares,premium FROM etf_scale WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date>=?"; params.append(start)
        if end:
            q += " AND date<=?"; params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_scale_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM etf_scale WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- etf nav (V3.1 research) ----
    def upsert_nav(self, symbol: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with unit_nav, acc_nav."""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (symbol, str(d),
             float(r.get("unit_nav", 0) or 0),
             float(r.get("acc_nav", 0) or 0),
             source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO etf_nav(symbol,date,unit_nav,acc_nav,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET "
                "unit_nav=excluded.unit_nav,acc_nav=excluded.acc_nav,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_nav_series(self, symbol: str, start: Optional[str] = None,
                       end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,unit_nav,acc_nav FROM etf_nav WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date>=?"; params.append(start)
        if end:
            q += " AND date<=?"; params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_nav_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM etf_nav WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- industry PE (V3.1 research) ----
    def upsert_industry_pe(self, rows: list[tuple], source: str = "") -> int:
        """rows: iterable of (industry, date, pe, pe_median). Idempotent upsert."""
        if not rows:
            return 0
        payload = [
            (ind, d,
             float(pe) if pe is not None and not pd.isna(pe) else None,
             float(pm) if pm is not None and not pd.isna(pm) else None,
             source)
            for (ind, d, pe, pm) in rows
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO industry_pe(industry,date,pe,pe_median,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(industry,date) DO UPDATE SET "
                "pe=excluded.pe,pe_median=excluded.pe_median,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_industry_pe_series(self, industry: str, start: Optional[str] = None,
                               end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,pe,pe_median FROM industry_pe WHERE industry=?"
        params: list = [industry]
        if start:
            q += " AND date>=?"; params.append(start)
        if end:
            q += " AND date<=?"; params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_industry_pe_date(self, industry: Optional[str] = None) -> Optional[str]:
        with self._conn() as c:
            if industry:
                row = c.execute(
                    "SELECT MAX(date) FROM industry_pe WHERE industry=?", (industry,)).fetchone()
            else:
                row = c.execute("SELECT MAX(date) FROM industry_pe").fetchone()
            return row[0] if row and row[0] else None

    # ---- ETF earnings expectation (V3.2 research; informational, not in composite) ----
    def upsert_etf_earnings(self, rows: list[tuple], source: str = "") -> int:
        """rows: iterable of (symbol, report_period, weighted_yoy, median_yoy, bull_ratio,
        bear_ratio, coverage, n_holdings, n_matched). Idempotent upsert keyed by
        (symbol, report_period)."""
        if not rows:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        def _i(x):
            return int(x) if x is not None and not pd.isna(x) else None

        payload = [
            (sym, rp, _f(wy), _f(my), _f(br), _f(be), _f(cv), _i(nh), _i(nm), source)
            for (sym, rp, wy, my, br, be, cv, nh, nm) in rows
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO etf_earnings(symbol,report_period,weighted_yoy,median_yoy,"
                "bull_ratio,bear_ratio,coverage,n_holdings,n_matched,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol,report_period) DO UPDATE SET "
                "weighted_yoy=excluded.weighted_yoy,median_yoy=excluded.median_yoy,"
                "bull_ratio=excluded.bull_ratio,bear_ratio=excluded.bear_ratio,"
                "coverage=excluded.coverage,n_holdings=excluded.n_holdings,"
                "n_matched=excluded.n_matched,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_etf_earnings(self, symbol: str) -> Optional[dict]:
        """Latest report_period earnings signal for `symbol`, or None."""
        with self._conn() as c:
            row = c.execute(
                "SELECT report_period,weighted_yoy,median_yoy,bull_ratio,bear_ratio,"
                "coverage,n_holdings,n_matched FROM etf_earnings WHERE symbol=? "
                "ORDER BY report_period DESC LIMIT 1", (symbol,)).fetchone()
        if not row:
            return None
        return {"report_period": row[0], "weighted_yoy": row[1], "median_yoy": row[2],
                "bull_ratio": row[3], "bear_ratio": row[4], "coverage": row[5],
                "n_holdings": row[6], "n_matched": row[7]}

    def last_earnings_period(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(report_period) FROM etf_earnings").fetchone()
            return row[0] if row and row[0] else None

    # ---- broad-index daily / valuation (V4 tracker) ----
    def upsert_index_daily(self, symbol: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with open/high/low/close/volume."""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (symbol, str(d),
             float(r.get("open", 0) or 0), float(r.get("high", 0) or 0),
             float(r.get("low", 0) or 0), float(r.get("close", 0) or 0),
             float(r.get("volume", 0) or 0), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO index_daily(symbol,date,open,high,low,close,volume,source) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(symbol,date) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_index_daily_series(self, symbol: str, start: Optional[str] = None,
                               end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,open,high,low,close,volume FROM index_daily WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_index_daily_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM index_daily WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    def upsert_index_pe(self, name: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with pe_ttm (and optional pe_median)."""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (name, str(d), _f(r.get("pe_ttm")), _f(r.get("pe_median")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO index_pe(name,date,pe_ttm,pe_median,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(name,date) DO UPDATE SET "
                "pe_ttm=excluded.pe_ttm,pe_median=excluded.pe_median,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_index_pe_series(self, name: str, start: Optional[str] = None,
                            end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,pe_ttm,pe_median FROM index_pe WHERE name=?"
        params: list = [name]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_index_pe_date(self, name: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM index_pe WHERE name=?", (name,)).fetchone()
            return row[0] if row and row[0] else None

    def upsert_index_pb(self, name: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with pb (and optional pb_median)."""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (name, str(d), _f(r.get("pb")), _f(r.get("pb_median")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO index_pb(name,date,pb,pb_median,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(name,date) DO UPDATE SET "
                "pb=excluded.pb,pb_median=excluded.pb_median,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_index_pb_series(self, name: str, start: Optional[str] = None,
                            end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,pb,pb_median FROM index_pb WHERE name=?"
        params: list = [name]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_index_pb_date(self, name: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM index_pb WHERE name=?", (name,)).fetchone()
            return row[0] if row and row[0] else None

    def upsert_market_pb(self, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with pb (and optional pb_median/pct_all/pct_10y)."""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (str(d), _f(r.get("pb")), _f(r.get("pb_median")),
             _f(r.get("pct_all")), _f(r.get("pct_10y")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO market_pb(date,pb,pb_median,pct_all,pct_10y,source) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                "pb=excluded.pb,pb_median=excluded.pb_median,pct_all=excluded.pct_all,"
                "pct_10y=excluded.pct_10y,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_market_pb_series(self, start: Optional[str] = None,
                             end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,pb,pb_median,pct_all,pct_10y FROM market_pb"
        params: list = []
        clauses = []
        if start:
            clauses.append("date>=?")
            params.append(start)
        if end:
            clauses.append("date<=?")
            params.append(end)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_market_pb_date(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM market_pb").fetchone()
            return row[0] if row and row[0] else None

    # ---- ETF dividend (V4 tracker · 价值型股息率) ----
    def upsert_etf_dividend(self, symbol: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with cumulative_dividend. Sparse — many ETFs have no rows."""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (symbol, str(d), float(r.get("cumulative_dividend", 0) or 0), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO etf_dividend(symbol,date,cumulative_dividend,source) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET "
                "cumulative_dividend=excluded.cumulative_dividend,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_etf_dividend_series(self, symbol: str, start: Optional[str] = None,
                                end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,cumulative_dividend FROM etf_dividend WHERE symbol=?"
        params: list = [symbol]
        if start:
            q += " AND date>=?"
            params.append(start)
        if end:
            q += " AND date<=?"
            params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("date")

    def last_etf_dividend_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM etf_dividend WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None
