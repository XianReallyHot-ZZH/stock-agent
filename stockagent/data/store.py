"""SQLite store for daily OHLCV + trade calendar + meta. Idempotent upserts."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd


def _num(x):
    """数值列强转: None/NaN → None, 否则 float。供 upsert 数值列用。"""
    if x is None:
        return None
    try:
        if isinstance(x, float) and pd.isna(x):
            return None
    except Exception:  # noqa: BLE001
        pass
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN guard


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
CREATE TABLE IF NOT EXISTS commodity_price (
    variety  TEXT NOT NULL,   -- 碳酸锂/铜/铝/锌/螺纹钢/铁矿石/焦煤/黄金/白银/原油/玻璃/纯碱/生猪(周期上游领先,见 fetcher.COMMODITY_CODES)
    date     TEXT NOT NULL,
    close    REAL,
    source   TEXT,
    PRIMARY KEY (variety, date)
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
CREATE TABLE IF NOT EXISTS stock_consensus (
    code       TEXT NOT NULL,
    fetch_date TEXT NOT NULL,   -- 周度快照日(YYYYMMDD); E0 起积累, E4 修正动量的差分底座
    n_reports  REAL,
    rating_buy REAL, rating_over REAL, rating_neutral REAL, rating_reduce REAL, rating_sell REAL,
    eps_fy1    REAL,
    eps_fy2    REAL,
    fy1_year   INTEGER,
    fy2_year   INTEGER,
    source     TEXT,
    PRIMARY KEY (code, fetch_date)
);
CREATE INDEX IF NOT EXISTS idx_consensus_code ON stock_consensus(code);
CREATE TABLE IF NOT EXISTS index_constituents (
    index_code    TEXT NOT NULL,   -- 中证指数代码(etf_pool.yaml index_code, 调研§5码表)
    code          TEXT NOT NULL,
    name          TEXT,
    weight        REAL,            -- 官方权重(%NAV, csindex 月度快照)
    snapshot_date TEXT,
    PRIMARY KEY (index_code, code)
);
CREATE INDEX IF NOT EXISTS idx_constituents_idx ON index_constituents(index_code);
CREATE TABLE IF NOT EXISTS stock_express (
    symbol        TEXT NOT NULL,   -- 业绩快报(三环第二环, E3): 未审计近似值·深市年报惯例2月底
    report_period TEXT NOT NULL,
    announce_date TEXT,
    np_yoy        REAL,            -- 净利润同比%
    rev_yoy       REAL,            -- 营业收入同比%
    source        TEXT,
    PRIMARY KEY (symbol, report_period)
);
CREATE TABLE IF NOT EXISTS stock_report_actual (
    symbol        TEXT NOT NULL,   -- 定期报告实际值(三环第三环, E3): 审计后·披露滞后45天-4个月
    report_period TEXT NOT NULL,
    announce_date TEXT,
    np_yoy        REAL,
    rev_yoy       REAL,
    source        TEXT,
    PRIMARY KEY (symbol, report_period)
);
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
CREATE TABLE IF NOT EXISTS market_turnover (
    date   TEXT PRIMARY KEY,
    sse    REAL,
    sz     REAL,
    total  REAL,
    source TEXT
);
CREATE TABLE IF NOT EXISTS market_margin (   -- ⑨ 恐惧贪婪·杠杆成分:上交所信用交易日级汇总
    date             TEXT PRIMARY KEY,
    financing_sse    REAL,   -- 上交所融资余额(元)。深市总量历史 akshare 不可得 → v1 仅沪市(_sz 列待扩)
    total_margin_sse REAL,   -- 上交所融资融券余额(元)
    source           TEXT
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
CREATE TABLE IF NOT EXISTS stock_valuation (
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    indicator TEXT NOT NULL,
    value     REAL,
    source    TEXT,
    PRIMARY KEY (symbol, date, indicator)
);
CREATE INDEX IF NOT EXISTS idx_stock_valuation_symbol ON stock_valuation(symbol);
CREATE TABLE IF NOT EXISTS stock_financials (
    symbol        TEXT NOT NULL,
    report_period TEXT NOT NULL,
    metric        TEXT NOT NULL,
    value         REAL,
    source        TEXT,
    PRIMARY KEY (symbol, report_period, metric)
);
CREATE INDEX IF NOT EXISTS idx_stock_financials_symbol ON stock_financials(symbol);
CREATE TABLE IF NOT EXISTS stock_dividend (
    symbol         TEXT NOT NULL,
    ex_date        TEXT NOT NULL,
    announce_date  TEXT,
    cash_per_share REAL,
    stock_div_10   REAL,
    trans_10       REAL,
    source         TEXT,
    PRIMARY KEY (symbol, ex_date)
);
CREATE INDEX IF NOT EXISTS idx_stock_dividend_symbol ON stock_dividend(symbol);
CREATE TABLE IF NOT EXISTS stock_forecast (
    symbol        TEXT NOT NULL,
    report_period TEXT NOT NULL,
    yoy           REAL,
    type          TEXT,
    announce_date TEXT,
    source        TEXT,
    PRIMARY KEY (symbol, report_period)
);
CREATE INDEX IF NOT EXISTS idx_stock_forecast_symbol ON stock_forecast(symbol);
CREATE TABLE IF NOT EXISTS western_macro_series (
    source     TEXT NOT NULL,   -- ustk/usidx/fut/forex/dxy (只读旁路 · ADR-0001)
    symbol     TEXT NOT NULL,   -- US10Y / .INX / GC / USDJPY / DXY
    date       TEXT NOT NULL,
    open  REAL, high REAL, low REAL, close REAL,   -- close 统一承载价格水平或收益率(UST)
    volume REAL,
    source_tag TEXT,
    PRIMARY KEY (source, symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_western_macro_symbol ON western_macro_series(symbol);
CREATE TABLE IF NOT EXISTS comex_inventory (   -- 黄金微观·紧缺实证(L2/L3) · 只读旁路 ADR-0001
    symbol TEXT NOT NULL,                       -- GC 黄金 / SI 白银
    date   TEXT NOT NULL,
    tonnes REAL,                                -- COMEX 库存(吨)
    ounces REAL,                                -- COMEX 库存(盎司)
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS cftc_position (      -- CFTC 非商业持仓(投机)·泡沫预警(L4)
    symbol   TEXT NOT NULL,                      -- GC 黄金 / SI 白银
    date     TEXT NOT NULL,                      -- 周频(周二报告)
    long_pos REAL, short_pos REAL, net_pos REAL, -- 非商业(投机/large spec)多/空/净仓位
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS cb_gold (            -- 央行黄金储备·底的锚(L1/L2) · 月频
    country TEXT NOT NULL,                       -- CN 中国(后续可扩 RU/IN...)
    date    TEXT NOT NULL,                       -- YYYY-MM-01
    value   REAL,                                -- 黄金储备(吨或万盎司,随源)
    yoy     REAL, mom REAL,                      -- 同比/环比(%)
    PRIMARY KEY (country, date)
);
CREATE TABLE IF NOT EXISTS economic_calendar (   -- 经济日历/事件·框架催化剂层(数据真伪+未来FOMC/CPI)
    date       TEXT NOT NULL,                    -- YYYY-MM-DD
    time       TEXT,                             -- HH:MM
    region     TEXT,                             -- 地区(美国/中国/欧元区...)
    event      TEXT NOT NULL,                    -- 事件名
    actual     REAL,                             -- 公布值(已公布才有)
    forecast   REAL,                             -- 预期值
    previous   REAL,                             -- 前值
    importance INTEGER,                          -- 重要性 1/2/3(筛 ≥2)
    PRIMARY KEY (date, time, event)
);
CREATE TABLE IF NOT EXISTS wm_claims (
    uid          TEXT PRIMARY KEY,   -- 稳定 hash(episode_date|asset|type|statement 规范化)→ 幂等再抽取
    episode_date TEXT NOT NULL,      -- 哪一期说的 (YYYY-MM-DD)
    asset        TEXT NOT NULL,      -- 黄金/美元指数/US10Y/标普500/半导体/原油/铜/A股/恒生...
    claim_type   TEXT NOT NULL,      -- direction/range/level/timing/scenario
    statement    TEXT NOT NULL,      -- 原话/提炼的可证伪断言
    direction    TEXT,               -- up/down/flat (direction/range/scenario 主推用)
    level_value  REAL,               -- 点位(level 类): 如 4800
    range_low    REAL, range_high REAL,  -- 区间(range 类): 如 70/90
    horizon      TEXT,               -- 兑现日期 YYYY-MM-DD 或事件触发描述(timing 类)
    confidence   TEXT,               -- strong/medium/weak
    basis_nodes  TEXT,               -- 逗号分隔的驱动图节点 id(见 western_macro/drivers.py)
    is_primary   INTEGER,            -- scenario: 1=主推分支, 0=备选(备选命中不计 edge)
    parent_uid   TEXT,               -- scenario 备选分支指向主推 uid
    state        TEXT NOT NULL DEFAULT 'draft',  -- draft/confirmed/vetoed
    source       TEXT,               -- llm/manual
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_wm_claims_episode ON wm_claims(episode_date);
CREATE INDEX IF NOT EXISTS idx_wm_claims_asset ON wm_claims(asset);
CREATE INDEX IF NOT EXISTS idx_wm_claims_state ON wm_claims(state);
CREATE TABLE IF NOT EXISTS wm_settlements (
    claim_uid        TEXT PRIMARY KEY,
    actual_direction TEXT,           -- up/down/flat
    actual_value     REAL,
    hit              INTEGER,        -- 方向是否命中 0/1
    baseline_hit     INTEGER,        -- 朴素基准是否也命中 0/1
    edge             INTEGER,        -- hit AND NOT baseline_hit
    method           TEXT,           -- auto/manual
    settled_at       TEXT,
    note             TEXT
);
CREATE TABLE IF NOT EXISTS wm_rules (
    uid          TEXT PRIMARY KEY,
    episode_date TEXT NOT NULL,
    statement    TEXT NOT NULL,
    rule_type    TEXT,               -- 禁定投/禁做空/加仓/减仓/止盈...
    state        TEXT NOT NULL DEFAULT 'draft',
    note         TEXT
);
CREATE INDEX IF NOT EXISTS idx_wm_rules_episode ON wm_rules(episode_date);
CREATE TABLE IF NOT EXISTS stock_spot (           -- 候选个股池(V7): 全市场现货快照·日更单调用
    code   TEXT NOT NULL,                        -- 6 位代码
    date   TEXT NOT NULL,                        -- 快照日 YYYY-MM-DD
    name   TEXT,                                 -- 最新名称(ST/退 过滤 + 展示名唯一来源)
    close  REAL,                                 -- 现货最新价(仅调试;筛选一律用 daily_prices 复权序列)
    source TEXT,
    PRIMARY KEY (code, date)
);
CREATE INDEX IF NOT EXISTS idx_stock_spot_code ON stock_spot(code);
CREATE TABLE IF NOT EXISTS industry_member (     -- 候选个股池(V7): 东财行业板块成分·月更
    industry      TEXT NOT NULL,                 -- 板块名(stock_board_industry_name_em 口径)
    code          TEXT NOT NULL,
    name          TEXT,
    snapshot_date TEXT,
    source        TEXT,
    PRIMARY KEY (industry, code)
);
CREATE INDEX IF NOT EXISTS idx_industry_member_code ON industry_member(code);
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

    def forecast_period_counts(self, min_rows: int = 100, limit: int = 8) -> list[tuple]:
        """全市场级预告报告期清单 [(report_period, n_rows)..]，期新在前。
        窗口台账回放用它挑有全市场数据的期（个股观察池的零星期 <min_rows 被滤掉）。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT report_period, COUNT(*) n FROM stock_forecast "
                "GROUP BY report_period HAVING n >= ? "
                "ORDER BY report_period DESC LIMIT ?", (min_rows, limit)).fetchall()
        return [(str(r[0]), int(r[1])) for r in rows]

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

    def upsert_commodity_price(self, rows: list[tuple], source: str = "") -> int:
        """rows: iterable of (variety, date, close). Idempotent upsert."""
        if not rows:
            return 0
        payload = [(v, d, float(c) if c is not None and not pd.isna(c) else None, source)
                   for (v, d, c) in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO commodity_price(variety,date,close,source) VALUES(?,?,?,?) "
                "ON CONFLICT(variety,date) DO UPDATE SET close=excluded.close,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_commodity_series(self, variety: str, start: Optional[str] = None,
                             end: Optional[str] = None) -> pd.Series:
        """variety 日 close 序列(date 升序,index=date)。A 类强形式领先信号用。"""
        q = "SELECT date,close FROM commodity_price WHERE variety=?"
        params: list = [variety]
        if start:
            q += " AND date>=?"; params.append(start)
        if end:
            q += " AND date<=?"; params.append(end)
        q += " ORDER BY date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return pd.Series(dtype=float)
        return df.set_index("date")["close"].astype(float)

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

    # ---- analyst-consensus weekly snapshots (E0, docs/RESEARCH-ETF行业业绩预期.md) ----
    _CONS_COLS = ["n_reports", "rating_buy", "rating_over", "rating_neutral",
                  "rating_reduce", "rating_sell", "eps_fy1", "eps_fy2", "fy1_year", "fy2_year"]

    def upsert_consensus(self, df: pd.DataFrame, fetch_date: str,
                         source: str = "em_profit_forecast") -> int:
        """Snapshot upsert keyed by (code, fetch_date) — same-day rerun overwrites, weekly cadence.
        df indexed by code with columns [n_reports, rating_*, eps_fy1, eps_fy2, fy1_year, fy2_year]."""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (str(code), fetch_date, *(_num(r.get(c)) for c in self._CONS_COLS), source)
            for code, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_consensus(code,fetch_date,n_reports,rating_buy,rating_over,"
                "rating_neutral,rating_reduce,rating_sell,eps_fy1,eps_fy2,fy1_year,fy2_year,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code,fetch_date) DO UPDATE SET "
                "n_reports=excluded.n_reports,rating_buy=excluded.rating_buy,"
                "rating_over=excluded.rating_over,rating_neutral=excluded.rating_neutral,"
                "rating_reduce=excluded.rating_reduce,rating_sell=excluded.rating_sell,"
                "eps_fy1=excluded.eps_fy1,eps_fy2=excluded.eps_fy2,fy1_year=excluded.fy1_year,"
                "fy2_year=excluded.fy2_year,source=excluded.source",
                rows,
            )
        return len(rows)

    def consensus_snapshot_dates(self) -> list:
        """All snapshot dates present, ascending — E0 验证 + E4 修正窗挑选."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT fetch_date FROM stock_consensus ORDER BY fetch_date").fetchall()
        return [r[0] for r in rows]

    def get_consensus_snapshot(self, asof: Optional[str] = None) -> tuple:
        """Latest snapshot at/before `asof` (YYYYMMDD; None = newest).

        Returns (fetch_date, DataFrame indexed by code with _CONS_COLS) —
        ("", empty frame) when no snapshot exists yet (E0 冷启动期).
        """
        with self._conn() as c:
            if asof:
                row = c.execute("SELECT MAX(fetch_date) FROM stock_consensus WHERE fetch_date<=?",
                                (asof,)).fetchone()
            else:
                row = c.execute("SELECT MAX(fetch_date) FROM stock_consensus").fetchone()
            date = row[0] if row and row[0] else None
            if not date:
                return "", pd.DataFrame(columns=self._CONS_COLS)
            rows = c.execute(
                "SELECT code,n_reports,rating_buy,rating_over,rating_neutral,rating_reduce,"
                "rating_sell,eps_fy1,eps_fy2,fy1_year,fy2_year FROM stock_consensus "
                "WHERE fetch_date=?", (date,)).fetchall()
        if not rows:
            return date, pd.DataFrame(columns=self._CONS_COLS)
        return date, pd.DataFrame(rows, columns=["code"] + self._CONS_COLS).set_index("code")

    # ---- index constituents (E1, docs/EXECUTION_PLAN-ETF业绩预期.md §3) ----
    def upsert_constituents(self, index_code: str, df: pd.DataFrame) -> int:
        """Official constituents+weights snapshot upsert, keyed by (index_code, code) —
        monthly refresh cadence, same-snapshot rerun overwrites. df columns:
        [code, name, weight, snapshot_date]."""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (index_code, str(r["code"]), str(r.get("name", "")),
             _num(r.get("weight")), str(r.get("snapshot_date", "")))
            for _, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO index_constituents(index_code,code,name,weight,snapshot_date) "
                "VALUES(?,?,?,?,?) ON CONFLICT(index_code,code) DO UPDATE SET "
                "name=excluded.name,weight=excluded.weight,snapshot_date=excluded.snapshot_date",
                rows,
            )
        return len(rows)

    def get_constituents(self, index_code: str) -> pd.DataFrame:
        """DataFrame[code(str), weight(float)] for an index — the aggregate_earnings
        holdings contract. Empty frame when this index has no stored constituents."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT code,weight FROM index_constituents WHERE index_code=?",
                (index_code,)).fetchall()
        if not rows:
            return pd.DataFrame(columns=["code", "weight"])
        return pd.DataFrame(rows, columns=["code", "weight"])

    def last_constituent_snapshot(self, index_code: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(snapshot_date) FROM index_constituents WHERE index_code=?",
                (index_code,)).fetchone()
        return row[0] if row and row[0] else None

    # ---- 业绩三环链 (E3, docs/EXECUTION_PLAN-ETF业绩预期.md §5) ----
    def _upsert_perf_table(self, table: str, rows: list, source: str = "") -> int:
        """rows: (symbol, report_period, announce_date, np_yoy, rev_yoy). 幂等 (symbol, report_period)."""
        if not rows:
            return 0

        def _s(x):
            if x is None or x == "" or (isinstance(x, float) and pd.isna(x)):
                return None
            return str(x)[:10]

        payload = [(str(s), str(rp), _s(a), _num(n), _num(r), source)
                   for (s, rp, a, n, r) in rows]
        with self._conn() as c:
            c.executemany(
                f"INSERT INTO {table}(symbol,report_period,announce_date,np_yoy,rev_yoy,source) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,report_period) DO UPDATE SET "
                "announce_date=excluded.announce_date,np_yoy=excluded.np_yoy,"
                "rev_yoy=excluded.rev_yoy,source=excluded.source",
                payload,
            )
        return len(payload)

    def upsert_stock_express(self, rows: list, source: str = "") -> int:
        return self._upsert_perf_table("stock_express", rows, source)

    def upsert_stock_report_actual(self, rows: list, source: str = "") -> int:
        return self._upsert_perf_table("stock_report_actual", rows, source)

    def _get_perf_period(self, table: str, report_period: str) -> pd.DataFrame:
        """一期全市场帧, indexed by code [np_yoy, rev_yoy, announce_date]（链聚合的输入契约）."""
        with self._conn() as c:
            df = pd.read_sql_query(
                f"SELECT symbol,np_yoy,rev_yoy,announce_date FROM {table} "
                "WHERE report_period=?", c, params=(report_period,))
        if len(df) == 0:
            return pd.DataFrame(columns=["np_yoy", "rev_yoy", "announce_date"])
        return df.rename(columns={"symbol": "code"}).set_index("code")

    def get_stock_express_period(self, report_period: str) -> pd.DataFrame:
        return self._get_perf_period("stock_express", report_period)

    def get_stock_report_period(self, report_period: str) -> pd.DataFrame:
        return self._get_perf_period("stock_report_actual", report_period)

    def get_stock_forecast_period(self, report_period: str) -> pd.DataFrame:
        """一期全市场业绩预告帧, indexed by code [yoy, type, announce_date]（链聚合输入契约）."""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT symbol,yoy,type,announce_date FROM stock_forecast "
                "WHERE report_period=?", c, params=(report_period,))
        if len(df) == 0:
            return pd.DataFrame(columns=["yoy", "type", "announce_date"])
        return df.rename(columns={"symbol": "code"}).set_index("code")

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

    # ---- 两市日成交额(⑧ 成交量地量监测 · baostock)----
    def upsert_market_turnover(self, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with sse/sz/total (yuan). SSE=sh.000001, SZ=sz.399001."""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (str(d), _f(r.get("sse")), _f(r.get("sz")), _f(r.get("total")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO market_turnover(date,sse,sz,total,source) "
                "VALUES(?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                "sse=excluded.sse,sz=excluded.sz,total=excluded.total,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_market_turnover_series(self, start: Optional[str] = None,
                                   end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,sse,sz,total FROM market_turnover"
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

    def last_market_turnover_date(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM market_turnover").fetchone()
            return row[0] if row and row[0] else None

    # ---- 融资融券余额(⑨ 恐惧贪婪·杠杆成分 · 上交所信用交易日级汇总 stock_margin_sse)----
    def upsert_market_margin(self, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by date(str) with financing_sse/total_margin_sse (yuan)。
        v1 仅沪市(stock_margin_sse 日级总量);深市总量历史 akshare 不可得。"""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (str(d), _f(r.get("financing_sse")), _f(r.get("total_margin_sse")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO market_margin(date,financing_sse,total_margin_sse,source) "
                "VALUES(?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
                "financing_sse=excluded.financing_sse,"
                "total_margin_sse=excluded.total_margin_sse,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_market_margin_series(self, start: Optional[str] = None,
                                 end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,financing_sse,total_margin_sse FROM market_margin"
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

    def last_market_margin_date(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM market_margin").fetchone()
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

    # ---- stock valuation (V5 tracker · Phase 2 个股层 · 百度金矿 PE/PB/总市值) ----
    # 个股日线复用 daily_prices(upsert_prices/get_series,与 ETF 同表不同 symbol),无新表。
    def upsert_stock_valuation(self, symbol: str, indicator: str, df: pd.DataFrame,
                               source: str = "") -> int:
        """df indexed by date(str) with value. Idempotent upsert keyed by (symbol,date,indicator).
        百度 period='全部' 每次返回全历史(IPO起,稀疏),全量 upsert 幂等覆盖——无需增量游标。"""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (symbol, str(d), indicator, _f(r.get("value")), source)
            for d, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_valuation(symbol,date,indicator,value,source) "
                "VALUES(?,?,?,?,?) ON CONFLICT(symbol,date,indicator) DO UPDATE SET "
                "value=excluded.value,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_stock_valuation_series(self, symbol: str, indicator: str,
                                   start: Optional[str] = None,
                                   end: Optional[str] = None) -> pd.DataFrame:
        q = "SELECT date,value FROM stock_valuation WHERE symbol=? AND indicator=?"
        params: list = [symbol, indicator]
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

    def last_stock_valuation_date(self, symbol: str,
                                  indicator: Optional[str] = None) -> Optional[str]:
        with self._conn() as c:
            if indicator:
                row = c.execute(
                    "SELECT MAX(date) FROM stock_valuation WHERE symbol=? AND indicator=?",
                    (symbol, indicator)).fetchone()
            else:
                row = c.execute(
                    "SELECT MAX(date) FROM stock_valuation WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- stock financials (V5 tracker · Phase 2 C0.5 · sina 财务摘要 常用指标 17 项) ----
    def upsert_stock_financials(self, symbol: str, df: pd.DataFrame, source: str = "") -> int:
        """df 长表 columns [report_period, metric, value](report_period=YYYYMMDD, metric=EN 键)。
        幂等 upsert 主键 (symbol, report_period, metric)。sina 每次返回全历史(102 期~25年)→ 全量覆盖。"""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (symbol, str(r["report_period"]), str(r["metric"]), _f(r.get("value")), source)
            for _, r in df.iterrows()
            if pd.notna(r.get("report_period")) and pd.notna(r.get("metric"))
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_financials(symbol,report_period,metric,value,source) "
                "VALUES(?,?,?,?,?) ON CONFLICT(symbol,report_period,metric) DO UPDATE SET "
                "value=excluded.value,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_stock_financials_series(self, symbol: str, metric: str,
                                    start: Optional[str] = None,
                                    end: Optional[str] = None) -> pd.Series:
        """单指标时间序列(indexed by report_period, ascending)。C1 算增速/波动直接吃。"""
        q = "SELECT report_period,value FROM stock_financials WHERE symbol=? AND metric=?"
        params: list = [symbol, metric]
        if start:
            q += " AND report_period>=?"
            params.append(start)
        if end:
            q += " AND report_period<=?"
            params.append(end)
        q += " ORDER BY report_period ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return pd.Series(dtype=float)
        return pd.Series(pd.to_numeric(df["value"], errors="coerce").values,
                         index=df["report_period"].values).dropna()

    def get_stock_financials_panel(self, symbol: str,
                                   metrics: Optional[list[str]] = None) -> pd.DataFrame:
        """宽表面板(index=report_period, columns=metric)——C1 多指标诊断便利读取。
        metrics=None → 全部已存指标。NaN 补缺(某期缺某指标)。"""
        q = "SELECT report_period,metric,value FROM stock_financials WHERE symbol=?"
        params: list = [symbol]
        if metrics:
            ph = ",".join("?" * len(metrics))
            q += f" AND metric IN ({ph})"
            params += list(metrics)
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return (df.pivot_table(index="report_period", columns="metric", values="value",
                               aggfunc="first")
                  .sort_index())

    def last_stock_financials_period(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(report_period) FROM stock_financials WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- stock dividend (V5 tracker · Phase 2 C0.5 · sina 分红明细) ----
    def upsert_stock_dividend(self, symbol: str, df: pd.DataFrame, source: str = "") -> int:
        """df indexed by ex_date(除权除息日,=影响第一天) with cash_per_share/stock_div_10/trans_10/
        announce_date。只存 进度=实施 的(已在 fetcher 过滤)。幂等主键 (symbol, ex_date)。"""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = []
        for d, r in df.iterrows():
            rows.append((
                symbol, str(d),
                str(r["announce_date"]) if pd.notna(r.get("announce_date")) else None,
                _f(r.get("cash_per_share")), _f(r.get("stock_div_10")), _f(r.get("trans_10")),
                source,
            ))
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_dividend(symbol,ex_date,announce_date,cash_per_share,"
                "stock_div_10,trans_10,source) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(symbol,ex_date) DO UPDATE SET "
                "announce_date=excluded.announce_date,cash_per_share=excluded.cash_per_share,"
                "stock_div_10=excluded.stock_div_10,trans_10=excluded.trans_10,source=excluded.source",
                rows,
            )
        return len(rows)

    def get_stock_dividend_series(self, symbol: str, start: Optional[str] = None,
                                  end: Optional[str] = None) -> pd.DataFrame:
        q = ("SELECT ex_date,announce_date,cash_per_share,stock_div_10,trans_10 "
             "FROM stock_dividend WHERE symbol=?")
        params: list = [symbol]
        if start:
            q += " AND ex_date>=?"
            params.append(start)
        if end:
            q += " AND ex_date<=?"
            params.append(end)
        q += " ORDER BY ex_date ASC"
        with self._conn() as c:
            df = pd.read_sql_query(q, c, params=params)
        if len(df) == 0:
            return df
        return df.set_index("ex_date")

    def last_stock_dividend_date(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(ex_date) FROM stock_dividend WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- stock forecast (V5 tracker · Phase 2 S08-G1 业绩预告链) ----
    # 业绩预告(预告/快报/正式报 链的最早一环)。稀疏——仅显著变动才发,容忍缺失。
    def upsert_stock_forecast(self, rows: list[tuple], source: str = "") -> int:
        """rows: iterable of (symbol, report_period, yoy, type, announce_date)。幂等主键 (symbol, report_period)。"""
        if not rows:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        def _s(x):  # 字符串或 None(空/NaN → None);日期截到 10 字符 YYYY-MM-DD
            if x is None or x == "" or (isinstance(x, float) and pd.isna(x)):
                return None
            return str(x)[:10]

        payload = [(str(s), str(rp), _f(yo), _s(t), _s(a), source) for (s, rp, yo, t, a) in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_forecast(symbol,report_period,yoy,type,announce_date,source) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(symbol,report_period) DO UPDATE SET "
                "yoy=excluded.yoy,type=excluded.type,announce_date=excluded.announce_date,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_stock_forecast_series(self, symbol: str) -> pd.DataFrame:
        """某股所有报告期的业绩预告,indexed by report_period(升序): yoy/type/announce_date。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT report_period,yoy,type,announce_date FROM stock_forecast "
                "WHERE symbol=? ORDER BY report_period ASC", c, params=(symbol,))
        if len(df) == 0:
            return df
        return df.set_index("report_period")

    def last_stock_forecast_period(self, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(report_period) FROM stock_forecast WHERE symbol=?", (symbol,)).fetchone()
            return row[0] if row and row[0] else None

    # ---- western-macro series (V6 tracker · 西方宏观预测台账 只读旁路 · ADR-0001) ----
    def upsert_western_macro(self, df: pd.DataFrame, source_tag: str = "") -> int:
        """df columns: source, symbol, date, open, high, low, close, volume(可缺)。
        幂等 upsert 主键 (source, symbol, date)。close 统一承载价格水平/收益率(UST)。"""
        if df is None or len(df) == 0:
            return 0

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        rows = [
            (str(r["source"]), str(r["symbol"]), str(r["date"]),
             _f(r.get("open")), _f(r.get("high")), _f(r.get("low")), _f(r.get("close")),
             _f(r.get("volume")), source_tag)
            for _, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO western_macro_series(source,symbol,date,open,high,low,close,volume,source_tag) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,symbol,date) DO UPDATE SET "
                "open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,"
                "volume=excluded.volume,source_tag=excluded.source_tag",
                rows,
            )
        return len(rows)

    def get_western_series(self, source: str, symbol: str,
                           start: Optional[str] = None, end: Optional[str] = None) -> pd.DataFrame:
        """西方宏观某 series(open/high/low/close/volume, indexed by date 升序)。"""
        q = ("SELECT date,open,high,low,close,volume FROM western_macro_series "
             "WHERE source=? AND symbol=?")
        params: list = [source, symbol]
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

    def western_symbols(self, source: Optional[str] = None) -> list[str]:
        with self._conn() as c:
            if source:
                rows = c.execute("SELECT DISTINCT symbol FROM western_macro_series WHERE source=?",
                                 (source,)).fetchall()
            else:
                rows = c.execute("SELECT DISTINCT symbol FROM western_macro_series").fetchall()
        return [r[0] for r in rows]

    def last_western_date(self, source: str, symbol: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(date) FROM western_macro_series WHERE source=? AND symbol=?",
                (source, symbol)).fetchone()
            return row[0] if row and row[0] else None

    # ---- 黄金微观紧缺数据 (comex_inventory / cftc_position / cb_gold · L2/L3/L4) ----
    def upsert_comex_inventory(self, rows: list[dict]) -> int:
        """rows: {symbol,date,tonnes,ounces}。幂等。"""
        if not rows:
            return 0
        payload = [(r["symbol"], r["date"],
                    _num(r.get("tonnes")), _num(r.get("ounces")))
                   for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO comex_inventory(symbol,date,tonnes,ounces) VALUES(?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET tonnes=excluded.tonnes,ounces=excluded.ounces",
                payload)
        return len(payload)

    def get_comex_inventory(self, symbol: str = "GC") -> pd.Series:
        """COMEX 库存(吨)序列, date 升序 index=date。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT date,tonnes FROM comex_inventory WHERE symbol=? ORDER BY date", c, params=(symbol,))
        if df.empty:
            return pd.Series(dtype=float)
        return df.set_index("date")["tonnes"].astype(float)

    def upsert_cftc_position(self, rows: list[dict]) -> int:
        """rows: {symbol,date,long_pos,short_pos,net_pos}。幂等。"""
        if not rows:
            return 0
        payload = [(r["symbol"], r["date"],
                    _num(r.get("long_pos")), _num(r.get("short_pos")), _num(r.get("net_pos")))
                   for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO cftc_position(symbol,date,long_pos,short_pos,net_pos) VALUES(?,?,?,?,?) "
                "ON CONFLICT(symbol,date) DO UPDATE SET long_pos=excluded.long_pos,"
                "short_pos=excluded.short_pos,net_pos=excluded.net_pos", payload)
        return len(payload)

    def get_cftc_position(self, symbol: str = "GC") -> pd.DataFrame:
        """CFTC 非商业(投机)持仓 DataFrame(date 升序 index=date, cols=long/short/net)。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT date,long_pos,short_pos,net_pos FROM cftc_position WHERE symbol=? ORDER BY date",
                c, params=(symbol,))
        if df.empty:
            return pd.DataFrame(columns=["long_pos", "short_pos", "net_pos"])
        return df.set_index("date").astype(float)

    def upsert_cb_gold(self, rows: list[dict]) -> int:
        """rows: {country,date,value,yoy,mom}。幂等。"""
        if not rows:
            return 0
        payload = [(r["country"], r["date"],
                    _num(r.get("value")), _num(r.get("yoy")), _num(r.get("mom"))) for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO cb_gold(country,date,value,yoy,mom) VALUES(?,?,?,?,?) "
                "ON CONFLICT(country,date) DO UPDATE SET value=excluded.value,yoy=excluded.yoy,mom=excluded.mom",
                payload)
        return len(payload)

    def get_cb_gold(self, country: str = "CN") -> pd.DataFrame:
        """央行黄金储备 DataFrame(date 升序 index=date, cols=value/yoy/mom)。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT date,value,yoy,mom FROM cb_gold WHERE country=? ORDER BY date", c, params=(country,))
        if df.empty:
            return pd.DataFrame(columns=["value", "yoy", "mom"])
        return df.set_index("date")

    # ---- 经济日历 (economic_calendar · 框架催化剂层) ----
    def upsert_economic_calendar(self, rows: list[dict]) -> int:
        """rows: {date,time,region,event,actual,forecast,previous,importance}。幂等。"""
        if not rows:
            return 0
        payload = [(r.get("date"), r.get("time"), r.get("region"), r.get("event"),
                    _num(r.get("actual")), _num(r.get("forecast")), _num(r.get("previous")),
                    int(r["importance"]) if r.get("importance") not in (None, "") else None)
                   for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO economic_calendar(date,time,region,event,actual,forecast,previous,importance) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(date,time,event) DO UPDATE SET "
                "region=excluded.region,actual=excluded.actual,forecast=excluded.forecast,"
                "previous=excluded.previous,importance=excluded.importance", payload)
        return len(payload)

    def get_economic_calendar(self, region: Optional[str] = None, since: Optional[str] = None,
                              until: Optional[str] = None, min_importance: int = 2) -> pd.DataFrame:
        """经济日历 DataFrame(date,time,region,event,actual,forecast,previous,importance, 升序)。"""
        q, p = "SELECT date,time,region,event,actual,forecast,previous,importance FROM economic_calendar WHERE 1=1", []
        if region:
            q += " AND region LIKE ?"; p.append(f"%{region}%")
        if since:
            q += " AND date>=?"; p.append(since)
        if until:
            q += " AND date<=?"; p.append(until)
        if min_importance:
            q += " AND COALESCE(importance,0)>=?"; p.append(min_importance)
        q += " ORDER BY date,time"
        with self._conn() as c:
            return pd.read_sql_query(q, c, params=p)

    # ---- western-macro prediction ledger (wm_claims / wm_settlements / wm_rules) ----
    def upsert_wm_claims(self, rows: list[dict]) -> int:
        """rows: dicts(uid/episode_date/asset/claim_type/statement + 可选 direction/level_value/
        range_low/range_high/horizon/confidence/basis_nodes/is_primary/parent_uid/state/source/created_at)。
        幂等 upsert 主键 uid。重抽时**不覆盖 state**(已确认/否决的人工状态保留)。"""
        if not rows:
            return 0

        def _s(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)

        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        def _i(x):
            return int(x) if x is not None and not (isinstance(x, float) and pd.isna(x)) else None

        payload = [(
            _s(r.get("uid")), _s(r.get("episode_date")), _s(r.get("asset")),
            _s(r.get("claim_type")), _s(r.get("statement")), _s(r.get("direction")),
            _f(r.get("level_value")), _f(r.get("range_low")), _f(r.get("range_high")),
            _s(r.get("horizon")), _s(r.get("confidence")), _s(r.get("basis_nodes")),
            _i(r.get("is_primary")), _s(r.get("parent_uid")),
            _s(r.get("state") or "draft"), _s(r.get("source")), _s(r.get("created_at")),
        ) for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO wm_claims(uid,episode_date,asset,claim_type,statement,direction,"
                "level_value,range_low,range_high,horizon,confidence,basis_nodes,is_primary,"
                "parent_uid,state,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET "
                "episode_date=excluded.episode_date,asset=excluded.asset,claim_type=excluded.claim_type,"
                "statement=excluded.statement,direction=excluded.direction,level_value=excluded.level_value,"
                "range_low=excluded.range_low,range_high=excluded.range_high,horizon=excluded.horizon,"
                "confidence=excluded.confidence,basis_nodes=excluded.basis_nodes,"
                "is_primary=excluded.is_primary,parent_uid=excluded.parent_uid,source=excluded.source",
                payload,
            )
        return len(payload)

    def get_wm_claims(self, state: Optional[str] = None, asset: Optional[str] = None,
                      episode_date: Optional[str] = None, claim_type: Optional[str] = None,
                      min_date: Optional[str] = None) -> list[dict]:
        q, p = "SELECT * FROM wm_claims WHERE 1=1", []
        if state:
            q += " AND state=?"; p.append(state)
        if asset:
            q += " AND asset=?"; p.append(asset)
        if episode_date:
            q += " AND episode_date=?"; p.append(episode_date)
        if claim_type:
            q += " AND claim_type=?"; p.append(claim_type)
        if min_date:
            q += " AND episode_date>=?"; p.append(min_date)
        q += " ORDER BY episode_date DESC, asset"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(q, p).fetchall()
        return [dict(r) for r in rows]

    def set_wm_claim_state(self, uid: str, state: str) -> int:
        """draft → confirmed/vetoed。返回受影响行数(0=uid 不存在)。"""
        with self._conn() as c:
            cur = c.execute("UPDATE wm_claims SET state=? WHERE uid=?", (state, uid))
            return cur.rowcount

    def upsert_wm_settlement(self, row: dict) -> int:
        def _f(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else float(x)

        def _i(x):
            return int(x) if x is not None and not (isinstance(x, float) and pd.isna(x)) else None

        with self._conn() as c:
            c.execute(
                "INSERT INTO wm_settlements(claim_uid,actual_direction,actual_value,hit,baseline_hit,"
                "edge,method,settled_at,note) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(claim_uid) DO UPDATE SET "
                "actual_direction=excluded.actual_direction,actual_value=excluded.actual_value,"
                "hit=excluded.hit,baseline_hit=excluded.baseline_hit,edge=excluded.edge,"
                "method=excluded.method,settled_at=excluded.settled_at,note=excluded.note",
                (row.get("claim_uid"), row.get("actual_direction"), _f(row.get("actual_value")),
                 _i(row.get("hit")), _i(row.get("baseline_hit")), _i(row.get("edge")),
                 row.get("method"), row.get("settled_at"), row.get("note")),
            )
        return 1

    def get_wm_settlements(self) -> list[dict]:
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT * FROM wm_settlements").fetchall()
        return [dict(r) for r in rows]

    def upsert_wm_rules(self, rows: list[dict]) -> int:
        if not rows:
            return 0

        def _s(x):
            return None if x is None or (isinstance(x, float) and pd.isna(x)) else str(x)

        payload = [(_s(r.get("uid")), _s(r.get("episode_date")), _s(r.get("statement")),
                    _s(r.get("rule_type")), _s(r.get("state") or "draft"), _s(r.get("note")))
                   for r in rows]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO wm_rules(uid,episode_date,statement,rule_type,state,note) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET "
                "episode_date=excluded.episode_date,statement=excluded.statement,"
                "rule_type=excluded.rule_type,note=excluded.note",
                payload,
            )
        return len(payload)

    def get_wm_rules(self, state: Optional[str] = None) -> list[dict]:
        q, p = "SELECT * FROM wm_rules WHERE 1=1", []
        if state:
            q += " AND state=?"; p.append(state)
        q += " ORDER BY episode_date DESC"
        with self._conn() as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(q, p).fetchall()
        return [dict(r) for r in rows]

    # ---- candidate-pool screening (V7 pool · 第六看板 候选个股池 · 只读旁路 ADR-0001) ----
    def upsert_stock_spot(self, df: pd.DataFrame, date: str, source: str = "em_spot") -> int:
        """全市场现货快照 upsert keyed by (code, date) — same-day rerun overwrites(幂等)。
        df indexed by code with [name, close]。名称 = ST/退 过滤与展示名的唯一来源。"""
        if df is None or len(df) == 0:
            return 0
        rows = [
            (str(code), date,
             str(r.get("name", "")) if pd.notna(r.get("name")) else "",
             _num(r.get("close")), source)
            for code, r in df.iterrows()
        ]
        with self._conn() as c:
            c.executemany(
                "INSERT INTO stock_spot(code,date,name,close,source) VALUES(?,?,?,?,?) "
                "ON CONFLICT(code,date) DO UPDATE SET "
                "name=excluded.name,close=excluded.close,source=excluded.source",
                rows,
            )
        return len(rows)

    def latest_stock_spot(self) -> pd.DataFrame:
        """最新快照日整帧 indexed by code [name, close]（空表 → 空帧）。"""
        with self._conn() as c:
            row = c.execute("SELECT MAX(date) FROM stock_spot").fetchone()
            if not row or not row[0]:
                return pd.DataFrame(columns=["name", "close"])
            df = pd.read_sql_query(
                "SELECT code,name,close FROM stock_spot WHERE date=?", c, params=(row[0],))
        if len(df) == 0:
            return pd.DataFrame(columns=["name", "close"])
        return df.set_index("code")

    def prune_stock_spot(self, keep_days: int = 90) -> int:
        """快照历史只留 keep_days 天(日更防膨胀;universe 只消费最新一份)。"""
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        with self._conn() as c:
            cur = c.execute("DELETE FROM stock_spot WHERE date<?", (cutoff,))
            return cur.rowcount

    def upsert_industry_members(self, df: pd.DataFrame, snapshot_date: str,
                                source: str = "em_board") -> int:
        """东财行业板块成分整帧写入。全量替换式:先清非本快照日的旧行再插——月度快照整帧口径,
        防板块更名/调出成分的残留幽灵。df columns [industry, code, name]。"""
        if df is None or len(df) == 0:
            return 0
        payload = [
            (str(r["industry"]), str(r["code"]),
             str(r.get("name", "")) if pd.notna(r.get("name")) else "",
             snapshot_date, source)
            for _, r in df.iterrows()
        ]
        with self._conn() as c:
            c.execute("DELETE FROM industry_member WHERE snapshot_date<>?", (snapshot_date,))
            c.executemany(
                "INSERT INTO industry_member(industry,code,name,snapshot_date,source) "
                "VALUES(?,?,?,?,?) ON CONFLICT(industry,code) DO UPDATE SET "
                "name=excluded.name,snapshot_date=excluded.snapshot_date,source=excluded.source",
                payload,
            )
        return len(payload)

    def industry_map(self) -> pd.DataFrame:
        """code → industry 映射(indexed by code [industry]；空表 → 空帧)。
        东财 2026-08 起板块名切申万三级口径,一股挂多层级(一级+二级+三级);
        取该股票所属板块中成分数最小者(=最深层级,分类最具体)且与拉取顺序无关。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT m.code AS code, m.industry AS industry, b.n AS n "
                "FROM industry_member m JOIN "
                "(SELECT industry, COUNT(*) AS n FROM industry_member GROUP BY industry) b "
                "ON m.industry = b.industry "
                "ORDER BY b.n ASC, m.industry", c)
        if len(df) == 0:
            return pd.DataFrame(columns=["industry"])
        return df.drop_duplicates("code", keep="first").set_index("code")[["industry"]]

    def industry_members(self) -> pd.DataFrame:
        """全量板块成员 [industry, code, name]（单快照,upsert 时已删旧）。行业层级回滚
        (pool.forecast_industry.industry_rollup)要全量——industry_map 的最具体板去重
        会丢父子包含信息。空表 → 空帧。"""
        with self._conn() as c:
            df = pd.read_sql_query(
                "SELECT industry, code, name FROM industry_member", c)
        return df

    def industry_boards(self) -> list[str]:
        """当前快照的板块名清单(升序)——stock_industry.yaml 未映射 diff 用。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT DISTINCT industry FROM industry_member ORDER BY industry").fetchall()
        return [r[0] for r in rows]

    def last_industry_snapshot(self) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT MAX(snapshot_date) FROM industry_member").fetchone()
            return row[0] if row and row[0] else None
