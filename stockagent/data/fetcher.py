"""Multi-source data fetcher (AkShare eastmoney/sina + Baostock), normalized.

Sources are tried in order of preference; the first that returns data wins.
Each fetch is wrapped with retries + timeout. Source + adjust tag is returned so
callers know how the price was adjusted (hfq vs raw).

Q10 reality: AkShare scrapes eastmoney/sina and is flaky (IP throttling,
RemoteDisconnected). Sina (fund_etf_hist_sina) is the most reliable fallback and
gives full ETF history as RAW (不复权) prices. Baostock gives full STOCK/INDEX
history but only recent (~6mo) ETF history.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Optional

import akshare as ak
import numpy as np
import pandas as pd

_ETF_COL_MAP = {
    "日期": "date", "开盘": "open", "收盘": "close",
    "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount",
}


class FetchError(RuntimeError):
    pass


def _run_with_timeout(fn, timeout: float, *args, **kwargs):
    box: dict = {}

    def target():
        try:
            box["result"] = fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise FetchError(f"timeout after {timeout}s")
    if "error" in box:
        raise FetchError(str(box["error"])[:300])
    return box.get("result")


def _szsh_prefix(symbol: str) -> str:
    """Map a 6-digit code to sina/baostock prefix: sh for 5xxxxx, sz for 1xxxxx."""
    s = str(symbol)
    return "sh" if s.startswith("5") else "sz"


def _normalize(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df[keep].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "volume", "amount"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"]).sort_values("date").drop_duplicates("date")
    return df.set_index("date")


# ---------- source adapters ----------
def _fetch_eastmoney(symbol, adjust, start, end, timeout):
    df = _run_with_timeout(
        ak.fund_etf_hist_em, timeout,
        symbol=symbol, period="daily",
        start_date=start.replace("-", "") if start else "20100101",
        end_date=(end or "").replace("-", "") or "20991231",
        adjust=adjust,
    )
    if df is None or len(df) == 0:
        raise FetchError("empty")
    return _normalize(df.rename(columns=_ETF_COL_MAP)), f"eastmoney_{adjust}"


def _fetch_sina(symbol, adjust, start, end, timeout):
    # Sina gives RAW (不复权) full history; adjust is ignored (recorded as 'raw').
    pre = _szsh_prefix(symbol)
    df = _run_with_timeout(ak.fund_etf_hist_sina, timeout, symbol=f"{pre}{symbol}")
    if df is None or len(df) == 0:
        raise FetchError("empty")
    df = df.rename(columns={c: c.lower() for c in df.columns})
    return _normalize(df), "sina_raw"


def _fetch_baostock(symbol, adjust, start, end, timeout):
    import baostock as bs

    pre = _szsh_prefix(symbol)
    flag = {"hfq": "1", "qfq": "2", "raw": "3"}.get(adjust, "1")
    code = f"{pre}.{symbol}"

    def _do():
        lg = bs.login()
        if lg.error_code != "0":
            raise FetchError(f"login {lg.error_msg}")
        rs = bs.query_history_k_data_plus(
            code, "date,open,high,low,close,volume,amount",
            start_date=start or "2015-01-01", end_date=end or datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag=flag,
        )
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        return rows

    rows = _run_with_timeout(_do, timeout)
    if not rows:
        raise FetchError("empty")
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    return _normalize(df), f"baostock_{adjust}"


_SOURCES = [_fetch_eastmoney, _fetch_sina, _fetch_baostock]


def fetch_etf_daily(
    symbol: str,
    adjust: str = "hfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retries: int = 2,
    timeout: float = 40.0,
) -> tuple[pd.DataFrame, str]:
    """Fetch ETF daily OHLCV. Returns (DataFrame indexed by date, source_tag).

    DataFrame columns: open, high, low, close, volume, amount (floats).
    Tries eastmoney -> sina -> baostock; first success wins.
    """
    last_err = None
    for source_fn in _SOURCES:
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(1.5 * attempt)
            try:
                df, tag = source_fn(symbol, adjust, start_date, end_date, timeout)
                if len(df) > 0:
                    return df, tag
            except FetchError as e:
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = FetchError(str(e)[:200])
        # move to next source
    raise FetchError(f"{symbol}: all sources failed ({last_err})")


def fetch_etf_spot() -> pd.DataFrame:
    df = _run_with_timeout(ak.fund_etf_spot_em, 60.0)
    if df is None:
        raise FetchError("fund_etf_spot_em returned None")
    df = df.rename(columns={"代码": "code", "名称": "name"})
    df["code"] = df["code"].astype(str)
    return df


def fetch_trade_dates() -> list[str]:
    """All historical A-share trade dates (YYYY-MM-DD). Sina-sourced (reliable)."""
    df = _run_with_timeout(ak.tool_trade_date_hist_sina, 40.0)
    if df is None or len(df) == 0:
        raise FetchError("trade_date_hist returned empty")
    col = "trade_date" if "trade_date" in df.columns else df.columns[0]
    dates = pd.to_datetime(df[col])
    return [d.strftime("%Y-%m-%d") for d in dates if d.date() <= datetime.now().date()]


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---- fund flow (V2.3) — eastmoney-only, currently throttled; graceful degrade at manager ----
def _find_col(cols: list[str], keywords: list[str]):
    for c in cols:
        if all(k in str(c) for k in keywords):
            return c
    return None


def fetch_sector_fund_flow_rank(indicator: str = "今日", sector_type: str = "行业资金流",
                                timeout: float = 40.0) -> pd.DataFrame:
    """Snapshot of sector net-inflow ranking. Returns DataFrame[sector, net_inflow]."""
    df = _run_with_timeout(ak.stock_sector_fund_flow_rank, timeout,
                           indicator=indicator, sector_type=sector_type)
    if df is None or len(df) == 0:
        raise FetchError("empty fund_flow_rank")
    cols = list(df.columns)
    name_col = next((c for c in cols if str(c) in ("名称", "行业", "板块")), cols[0])
    inflow_col = _find_col(cols, ["主力净流入", "净额"]) or _find_col(cols, ["净流入"])
    out = pd.DataFrame({"sector": df[name_col].astype(str)})
    if inflow_col is not None:
        out["net_inflow"] = pd.to_numeric(df[inflow_col], errors="coerce")
    return out


def fetch_sector_fund_flow_hist(symbol: str, timeout: float = 40.0) -> pd.DataFrame:
    """Historical daily net-inflow for one sector name. Returns DataFrame indexed by date
    with column net_inflow. Backtestable."""
    df = _run_with_timeout(ak.stock_sector_fund_flow_hist, timeout, symbol=symbol)
    if df is None or len(df) == 0:
        raise FetchError("empty fund_flow_hist")
    cols = list(df.columns)
    date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])
    inflow_col = _find_col(cols, ["主力净流入", "净额"]) or _find_col(cols, ["净流入"])
    out = pd.DataFrame({"date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")})
    if inflow_col is not None:
        out["net_inflow"] = pd.to_numeric(df[inflow_col], errors="coerce")
    out = out.dropna(subset=["date"]).drop_duplicates("date").set_index("date").sort_index()
    return out


# ---- ETF scale / shares (V2.3) — SSE-sourced, not throttled, backtestable ----
def fetch_etf_scale_sse(date: str, timeout: float = 40.0) -> pd.DataFrame:
    """SSE ETF fund-shares snapshot for a given date (YYYYMMDD).

    Returns DataFrame[symbol, shares]. Works for any trading day (history back
    to ~2015), SSE-listed ETFs only (5xxxxx).
    """
    df = _run_with_timeout(ak.fund_etf_scale_sse, timeout, date=date)
    if df is None or len(df) == 0:
        raise FetchError(f"empty etf_scale_sse {date}")
    code_col = next((c for c in df.columns if "代码" in str(c)), df.columns[1])
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    out = pd.DataFrame({"symbol": df[code_col].astype(str)})
    if share_col is not None:
        out["shares"] = pd.to_numeric(df[share_col], errors="coerce")
    return out


def fetch_etf_spot_premium(timeout: float = 60.0) -> pd.DataFrame:
    """Today's ETF spot with shares + IOPV-implied premium, ALL ETFs (incl SZSE).

    Returns DataFrame[code, shares, premium]. premium = price/IOPV - 1 (NaN if IOPV<=0).
    Used for SZSE share forward-accumulation + live premium filter.
    """
    df = _run_with_timeout(ak.fund_etf_spot_em, timeout)
    if df is None or len(df) == 0:
        raise FetchError("empty etf_spot")
    code_col = next((c for c in df.columns if str(c) in ("代码", "code")), df.columns[0])
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    iopv_col = next((c for c in df.columns if "IOPV" in str(c) or "实时估值" in str(c)), None)
    price_col = next((c for c in df.columns if str(c) in ("最新价", "最新价额")), None)
    out = pd.DataFrame({"code": df[code_col].astype(str)})
    if share_col is not None:
        out["shares"] = pd.to_numeric(df[share_col], errors="coerce")
    if iopv_col is not None and price_col is not None:
        px = pd.to_numeric(df[price_col], errors="coerce")
        iopv = pd.to_numeric(df[iopv_col], errors="coerce")
        out["premium"] = np.where(iopv > 0, px / iopv - 1.0, np.nan)
    else:
        out["premium"] = np.nan
    return out
