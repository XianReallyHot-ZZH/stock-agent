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
CREATE INDEX IF NOT EXISTS idx_prices_symbol ON daily_prices(symbol);
CREATE INDEX IF NOT EXISTS idx_fund_flow_sector ON fund_flow(sector);
CREATE INDEX IF NOT EXISTS idx_scale_symbol ON etf_scale(symbol);
CREATE INDEX IF NOT EXISTS idx_nav_symbol ON etf_nav(symbol);
CREATE INDEX IF NOT EXISTS idx_industry_pe ON industry_pe(industry);
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
