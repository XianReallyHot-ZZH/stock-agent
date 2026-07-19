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


def price_basis_family(source_tag: Optional[str]) -> str:
    """Classify a price source tag by its 复权 basis: 'raw' | 'hfq' | 'qfq' | 'unknown'.

    A daily-price series must stay on ONE basis — mixing raw (sina 不复权) with hfq
    (eastmoney 后复权) creates artificial multi-x jumps (e.g. 0.396 -> 1.502 overnight)
    that corrupt trend/MOM signals. Families: sina_raw/eastmoney_raw/baostock_raw -> 'raw';
    *_hfq -> 'hfq'; *_qfq -> 'qfq'. split_adj (fix_splits.py output) is also 'raw' — it only
    stitches rare split discontinuities and stays on the raw scale, so raw increments append
    safely post-split (any genuinely new split is re-caught by re-running fix_splits.py).
    """
    t = (source_tag or "").lower()
    if not t:
        return "unknown"
    if "hfq" in t:
        return "hfq"
    if "qfq" in t:
        return "qfq"
    if "raw" in t or "sina" in t or "split_adj" in t:
        return "raw"
    return "unknown"


def is_basis_consistent(fetched_tag: str, existing_tag: Optional[str]) -> bool:
    """Would upserting a batch tagged `fetched_tag` keep the series on the same basis as
    the existing history (`existing_tag`)? True if families match (or no history yet).

    Safety net for incremental updates against cross-source basis contamination — e.g. an
    hfq point sneaking into a raw series when sina fails and eastmoney wins on the latest day.
    """
    if not existing_tag:
        return True  # fresh symbol — anything is consistent
    return price_basis_family(fetched_tag) == price_basis_family(existing_tag)


# ---------- source adapters ----------
def _eastmoney_adjust(adjust: str) -> str:
    """akshare fund_etf_hist_em uses '' for 不复权; map our 'raw'/'' token to it, else passthrough."""
    return "" if adjust in ("", "raw", None) else adjust


def _fetch_eastmoney(symbol, adjust, start, end, timeout):
    df = _run_with_timeout(
        ak.fund_etf_hist_em, timeout,
        symbol=symbol, period="daily",
        start_date=start.replace("-", "") if start else "20100101",
        end_date=(end or "").replace("-", "") or "20991231",
        adjust=_eastmoney_adjust(adjust),
    )
    if df is None or len(df) == 0:
        raise FetchError("empty")
    return _normalize(df.rename(columns=_ETF_COL_MAP)), f"eastmoney_{adjust or 'raw'}"


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


def _adapter_plan(default_adjust: str, want_family: Optional[str]) -> list[tuple]:
    """Ordered (adapter_fn, adjust_to_pass) pairs for fetch_etf_daily.

    If `want_family` is set (incremental update keeping an existing basis), each adapter is
    given the adjust that makes it PRODUCE that family, and is dropped if it can't — sina can
    only emit raw, so it's excluded when matching an hfq/qfq history. If None (fresh symbol),
    all adapters use `default_adjust` in preference order.
    """
    plan: list[tuple] = []
    for fn in _SOURCES:
        if want_family is None:
            plan.append((fn, default_adjust))
            continue
        if want_family == "raw":
            # sina ignores adjust (always raw); eastmoney 不复权 via adjust=''; baostock flag '3' via 'raw'
            adj = "" if fn is _fetch_eastmoney else ("raw" if fn is _fetch_baostock else "")
            plan.append((fn, adj))
        elif want_family in ("hfq", "qfq"):
            if fn is _fetch_sina:
                continue  # sina has no 复权 — would emit raw, wrong family
            plan.append((fn, want_family))
        else:  # unknown family — don't constrain
            plan.append((fn, default_adjust))
    return plan


def fetch_etf_daily(
    symbol: str,
    adjust: str = "hfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retries: int = 2,
    timeout: float = 40.0,
    prefer_source: Optional[str] = None,
) -> tuple[pd.DataFrame, str]:
    """Fetch ETF daily OHLCV. Returns (DataFrame indexed by date, source_tag).

    DataFrame columns: open, high, low, close, volume, amount (floats).
    Tries eastmoney -> sina -> baostock; first success wins.

    prefer_source: the existing series' source tag (e.g. 'sina_raw'). When set, adapters are
    constrained to produce the SAME 复权 basis as the history, so an incremental update can't
    mix a 后复权 point into a 不复权 series (which would create fake multi-x jumps). Sina can
    only emit raw, so it's dropped when matching an hfq/qfq history. Callers should still guard
    with is_basis_consistent() as a belt-and-suspenders safety net.
    """
    want_family = price_basis_family(prefer_source) if prefer_source else None
    if want_family == "unknown":
        want_family = None  # ambiguous legacy tag — can't enforce, leave unconstrained
    last_err = None
    for source_fn, eff_adjust in _adapter_plan(adjust, want_family):
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(1.5 * attempt)
            try:
                df, tag = source_fn(symbol, eff_adjust, start_date, end_date, timeout)
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


def fetch_etf_spot_shares(symbols: list[str], timeout: float = 60.0) -> dict[str, float]:
    """Current share count for the given symbols (one batched fund_etf_spot_em call).

    For ETFs whose historical shares are unavailable via fund_etf_scale_sse (e.g. 515880 not
    in the SSE 基金份额 list), this at least gives the current level to draw as a reference.
    Returns {symbol: shares_in_units}.
    """
    df = _run_with_timeout(ak.fund_etf_spot_em, timeout)
    if df is None or len(df) == 0:
        return {}
    code_col = next((c for c in df.columns if str(c) in ("代码", "code")), df.columns[0])
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    if share_col is None:
        return {}
    want = set(str(s) for s in symbols)
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        code = str(r[code_col])
        if code in want:
            sh = r.get(share_col)
            if pd.notna(sh):
                out[code] = float(sh)
    return out


# ---- ETF NAV (V3.1 research) — fund-published unit/accumulated NAV, inherently correct ----
def fetch_etf_nav(symbol: str, start_date: str = "20000101", end_date: str = "20500101",
                  timeout: float = 40.0) -> pd.DataFrame:
    """Fund-published NAV history for an ETF (天天基金网).

    Returns DataFrame indexed by date(str): unit_nav, acc_nav. This is the authoritative
    fund value (NOT the exchange trading price) — inherently correct & continuous, so no
    前复权/后复权 needed (split/dividend handling is the fund company's job). Use this for
    fair 规模=份额×净值 and valuation work; do NOT misuse daily_prices.close as NAV.

    Primary: fund_etf_fund_info_em (unit+acc in one call). Some ETF codes aren't in that
    table (e.g. 512980, 159819) → fall back to fund_open_fund_info_em (单位净值走势 + 累计净值走势).
    """
    try:
        df = _run_with_timeout(ak.fund_etf_fund_info_em, timeout,
                               fund=symbol, start_date=start_date, end_date=end_date)
        if df is not None and len(df) > 0 and "单位净值" in df.columns:
            out = pd.DataFrame({
                "date": pd.to_datetime(df["净值日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "unit_nav": pd.to_numeric(df["单位净值"], errors="coerce"),
                "acc_nav": pd.to_numeric(df["累计净值"], errors="coerce"),
            })
            return out.dropna(subset=["date", "unit_nav"]).drop_duplicates("date").set_index("date").sort_index()
    except Exception:  # noqa: BLE001
        pass  # fall through to alternative endpoint

    # Fallback: fund_open_fund_info_em (works for codes the primary table rejects)
    unit = _run_with_timeout(ak.fund_open_fund_info_em, timeout, symbol=symbol, indicator="单位净值走势")
    acc = _run_with_timeout(ak.fund_open_fund_info_em, timeout, symbol=symbol, indicator="累计净值走势")
    if (unit is None or len(unit) == 0) and (acc is None or len(acc) == 0):
        raise FetchError(f"empty nav {symbol}")
    out = pd.DataFrame({"date": []})
    if unit is not None and len(unit) and "单位净值" in unit.columns:
        out = pd.DataFrame({
            "date": pd.to_datetime(unit["净值日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
            "unit_nav": pd.to_numeric(unit["单位净值"], errors="coerce"),
        }).dropna(subset=["date"]).drop_duplicates("date")
    if acc is not None and len(acc) and "累计净值" in acc.columns:
        acc_df = pd.DataFrame({
            "date": pd.to_datetime(acc["净值日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
            "acc_nav": pd.to_numeric(acc["累计净值"], errors="coerce"),
        }).dropna(subset=["date"]).drop_duplicates("date")
        out = out.merge(acc_df, on="date", how="outer") if len(out) else acc_df
    if not len(out):
        raise FetchError(f"empty nav {symbol}")
    return out.sort_values("date").set_index("date")


def fetch_etf_scale_szse_range(start: str, end: str, timeout: float = 60.0) -> pd.DataFrame:
    """SZSE ETF shares for a date range via fund_scale_daily_szse (date-range native).

    Returns DataFrame[symbol, date, shares] for ALL SZSE ETFs in [start,end]. Caller should
    chunk (e.g. month-by-month) to keep payloads small + upsert incrementally. This is the
    deep-market counterpart to fund_etf_scale_sse (SSE-only, per-date). Note: fund_etf_scale_szse()
    with NO args is only a CURRENT spot snapshot (no history) — fund_scale_daily_szse has history.
    """
    s = str(start).replace("-", "")
    e = str(end).replace("-", "")
    df = _run_with_timeout(ak.fund_scale_daily_szse, timeout,
                           start_date=s, end_date=e, symbol="ETF")
    if df is None or len(df) == 0:
        raise FetchError(f"empty szse range {start}..{end}")
    code_col = next((c for c in df.columns if "代码" in str(c)), None)
    date_col = next((c for c in df.columns if "日期" in str(c) or "date" in str(c).lower()), None)
    share_col = next((c for c in df.columns if "份额" in str(c)), None)
    if code_col is None or date_col is None or share_col is None:
        raise FetchError(f"szse range unexpected cols {list(df.columns)}")
    res = pd.DataFrame({
        "symbol": df[code_col].astype(str),
        "date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d"),
        "shares": pd.to_numeric(df[share_col], errors="coerce"),
    })
    return res.dropna(subset=["symbol", "date", "shares"])


# ---- Industry PE (V3.1 research) — cninfo, per-date snapshot of all CSRC industries ----
def fetch_industry_pe(date: str, timeout: float = 40.0, retries: int = 3) -> pd.DataFrame:
    """All CSRC 证监会行业 static-PE for a given date (YYYYMMDD). One call covers every
    industry, so backfill is one-fetch-per-trading-day (mirrors fund_etf_scale_sse pattern).

    Returns DataFrame[industry, pe, pe_median]. PE = 静态市盈率-加权平均 (中位数 as cross-check).
    Static PE uses last annual earnings (lags within-year), acceptable for历史分位 in v1.

    cninfo is throttle-prone (intermittent empty/HTML responses on valid dates) AND has no
    data before ~2023. We retry to ride through throttling; pre-2023 dates stay empty
    (raised as FetchError, which the manager logs + skips).
    """
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2.0)
        try:
            df = _run_with_timeout(ak.stock_industry_pe_ratio_cninfo, timeout,
                                   symbol="证监会行业分类", date=date)
            if df is not None and len(df) > 0 and any("行业名称" in str(c) for c in df.columns):
                break
            last_err = "empty"
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:80]
    else:
        raise FetchError(f"empty industry_pe {date} ({last_err})")

    name_col = next((c for c in df.columns if "行业名称" in str(c)), None)
    pe_col = next((c for c in df.columns if "加权平均" in str(c) and "市盈率" in str(c)), None)
    med_col = next((c for c in df.columns if "中位数" in str(c) and "市盈率" in str(c)), None)
    if name_col is None or pe_col is None:
        raise FetchError(f"industry_pe {date}: missing cols {list(df.columns)}")
    out = pd.DataFrame({
        "industry": df[name_col].astype(str),
        "pe": pd.to_numeric(df[pe_col], errors="coerce"),
        "pe_median": pd.to_numeric(df[med_col], errors="coerce") if med_col else pd.NA,
    })
    return out.dropna(subset=["industry"])


# ---- ETF earnings expectation (V3.2 research) — holdings + 业绩预告, both data.eastmoney ----
def fetch_etf_holdings(symbol: str, year: Optional[int] = None, timeout: float = 40.0) -> pd.DataFrame:
    """An ETF's latest disclosed stock holdings (重仓股) via fund_portfolio_hold_em.

    Returns DataFrame[code, weight, name, period] where weight = 占净值比例 (% of NAV).
    Tries the given year (or current year), falling back to the prior year if empty.
    Commodity / 宽基 / QDII ETFs with no stock holdings → empty DataFrame. Lives on
    data.eastmoney.com (not the blocked push2 host).
    """
    years = [year] if year else [datetime.now().year, datetime.now().year - 1]
    for y in years:
        try:
            df = _run_with_timeout(ak.fund_portfolio_hold_em, timeout, symbol=symbol, date=str(y))
        except Exception:  # noqa: BLE001
            df = None
        if df is None or len(df) == 0:
            continue
        out = pd.DataFrame({
            "code": df["股票代码"].astype(str),
            "weight": pd.to_numeric(df["占净值比例"], errors="coerce"),
            "name": df.get("股票名称", "").astype(str),
            "period": df.get("季度", "").astype(str),
        })
        return out.dropna(subset=["weight"])
    return pd.DataFrame(columns=["code", "weight", "name", "period"])


def fetch_earnings_forecast(report_period: str, timeout: float = 60.0) -> pd.DataFrame:
    """All A-share 业绩预告 for a report period (YYYYMMDD, e.g. '20251231').

    Returns DataFrame indexed by code (6-digit str) with columns [yoy, type]:
    yoy = 业绩变动幅度 (归母净利润同比 %), type = 预告类型. Filters to 归属于上市公司股东的
    净利润 and dedupes by code (keeps the latest 公告日期). data.eastmoney.com, paginates
    internally (~13s for the FY annual period).
    """
    df = _run_with_timeout(ak.stock_yjyg_em, timeout, date=report_period)
    if df is None or len(df) == 0:
        raise FetchError(f"empty earnings_forecast {report_period}")
    df = df[df["预测指标"].astype(str).str.strip() == "归属于上市公司股东的净利润"].copy()
    df["code"] = df["股票代码"].astype(str)
    df["yoy"] = pd.to_numeric(df["业绩变动幅度"], errors="coerce")
    df["type"] = df["预告类型"].astype(str)
    df["_ann"] = pd.to_datetime(df["公告日期"], errors="coerce")
    df = df.sort_values("_ann").drop_duplicates("code", keep="last")  # latest announcement per code
    return df.set_index("code")[["yoy", "type"]]
