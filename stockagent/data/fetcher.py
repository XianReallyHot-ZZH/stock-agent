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

import logging
import threading
import time
from datetime import datetime
from typing import Optional
import io
import re

import requests

import akshare as ak
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

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
    """sina/baostock 交易所前缀 for a 6-digit code (ETF + 个股共用):
      sh (上交所): 5xxxxx ETF / 6xxxxx 主板 / 688xxx 科创板 / 9xxxxx B股
      sz (深交所): 0xxxxx 主板 / 3xxxxx 创业板 / 159xxx ETF
      bj (北交所): 8xxxxx / 4xxxxx — sina stock_zh_a_daily 不支持,按 bj 返回交调用方自理。
    Phase 2 修:旧 ETF-only 实现(`5→sh, else→sz`)对个股全坏——上证主板 6xx/科创板 68x/B股 9xx
    会被误判 sz(sina 直接 KeyError)。按首位分流后,3 个 ETF 调用者(_fetch_sina/_fetch_baostock/
    fetch_etf_dividend)行为不变(5xx→sh、159xx→sz 仍正确)。
    """
    head = str(symbol)[:1]
    if head in ("5", "6", "9"):
        return "sh"
    if head in ("0", "1", "3"):
        return "sz"
    return "bj"


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


def fetch_market_turnover(start=None, end=None, timeout=60.0):
    """两市日成交额(元,baostock)= 上交所(sh.000001 amount) + 深交所(sz.399001 amount)。
    baostock 的指数 amount = 交易所总成交额(已验证 sz.399001 与 399106 完全一致 → 交易所总数,非成分和)。
    返回 DataFrame indexed by date(str), cols: sse/sz/total。历史回溯 ~1991。"""
    import baostock as bs
    end = end or today_str()
    start = start or "1991-01-01"

    def _query(code):
        rs = bs.query_history_k_data_plus(
            code, "date,amount", start_date=start, end_date=end,
            frequency="d", adjustflag="3",
        )
        out = {}
        while rs.error_code == "0" and rs.next():
            r = rs.get_row_data()
            if r[1] not in ("", None):
                try:
                    out[r[0]] = float(r[1])
                except ValueError:
                    pass
        return out

    def _do():
        lg = bs.login()
        if lg.error_code != "0":
            raise FetchError(f"baostock login {lg.error_msg}")
        try:
            return _query("sh.000001"), _query("sz.399001")
        finally:
            try:
                bs.logout()
            except Exception:  # noqa: BLE001
                pass

    sse, sz = _run_with_timeout(_do, timeout)
    dates = sorted(set(sse) | set(sz))
    if not dates:
        raise FetchError("empty market turnover")
    rows = []
    for d in dates:
        s, z = sse.get(d), sz.get(d)
        parts = [v for v in (s, z) if v is not None]
        rows.append((d, s, z, sum(parts) if parts else None))
    return pd.DataFrame(rows, columns=["date", "sse", "sz", "total"]).set_index("date")


def fetch_market_turnover_tushare(start: str = "1991-01-01", end: Optional[str] = None) -> pd.DataFrame:
    """两市日成交额(tushare `daily_info` 交易所官方口径,2026-09-13 批次2.2 主源)。
    取「上海市场/深圳市场」两总量行(深市含主板+创业板 A 股),amount 亿元→元对齐 baostock 存量
    单位;区间查询 12 行/日→按 ~300 日窗分段(单次 4000 行内)。⑧地量/⑨流动性成分数据源——
    官方口径替换 baostock 拼指数的旧路,顺带消除 399001(深证成指)口径隐患。
    返回同形 DataFrame[date: sse/sz/total(元)]。"""
    from datetime import datetime, timedelta
    from . import tushare_client as tc
    end = end or today_str()
    s_dt = datetime.strptime(str(start)[:10], "%Y-%m-%d")
    e_dt = datetime.strptime(str(end)[:10], "%Y-%m-%d")
    parts: dict[str, dict[str, float]] = {}
    last_err = None
    cur = s_dt
    while cur <= e_dt:
        win_end = min(cur + timedelta(days=300), e_dt)
        try:
            df = tc.query("daily_info",
                          start_date=cur.strftime("%Y%m%d"),
                          end_date=win_end.strftime("%Y%m%d"),
                          fields="trade_date,ts_name,amount")
            for _, r in df.iterrows():
                name = str(r.get("ts_name") or "")
                if name not in ("上海市场", "深圳市场"):
                    continue
                d = _ts_d8(r.get("trade_date"))
                v = _ts_f(r.get("amount"))
                if d and v is not None:
                    parts.setdefault(d, {})[name] = v * 1e8   # 亿元→元
            last_err = None
        except Exception as ex:  # noqa: BLE001
            last_err = ex
        cur = win_end + timedelta(days=1)
    if not parts:
        raise FetchError(f"market_turnover(tushare) failed ({last_err})")
    rows = []
    for d in sorted(parts):
        s = parts[d].get("上海市场")
        z = parts[d].get("深圳市场")
        both = [v for v in (s, z) if v is not None]
        rows.append((d, s, z, sum(both) if both else None))
    return pd.DataFrame(rows, columns=["date", "sse", "sz", "total"]).set_index("date")


def fetch_market_margin(start: str = "2010-03-01", end: Optional[str] = None,
                        timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """上交所融资融券日级总量(stock_margin_sse,信用交易汇总)。两融 2010-03-31 启动 → 历史自彼起。
    单次返回封顶 ~2000 行 → 按**年**分段拉再拼接。返回 DataFrame indexed by date(str):
      financing_sse(融资余额,元) / total_margin_sse(融资融券余额,元)。

    深市总量历史 akshare 不可得(stock_margin_szse 仅当日 1 行快照、stock_margin_detail_szse 逐券明细无法
    拼总量)→ v1 杠杆成分仅沪市口径;沪市占两融大头,作杠杆情绪代理足够(只读诊断,非精确水平)。"""
    from datetime import datetime, timedelta
    end = end or today_str()
    s_dt = datetime.strptime(str(start)[:10], "%Y-%m-%d")
    e_dt = datetime.strptime(str(end)[:10], "%Y-%m-%d")
    frames = []
    last_err = None
    cur_year = s_dt.year
    while cur_year <= e_dt.year:
        ys = max(s_dt, datetime(cur_year, 1, 1))
        ye = min(e_dt, datetime(cur_year, 12, 31))
        s_arg, e_arg = ys.strftime("%Y%m%d"), ye.strftime("%Y%m%d")
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(1.5 * attempt)
            try:
                df = _run_with_timeout(ak.stock_margin_sse, timeout,
                                       start_date=s_arg, end_date=e_arg)
                if df is None or len(df) == 0:
                    raise FetchError("empty")
                cols = list(df.columns)
                date_col = next((c for c in cols if "日期" in str(c)), cols[0])
                fin_col = next((c for c in cols if "融资余额" in str(c)), None)
                tot_col = next((c for c in cols if "融资融券余额" in str(c)), None)
                if fin_col is None:
                    raise FetchError(f"market_margin: no 融资余额 col in {cols}")
                out = pd.DataFrame({
                    "date": pd.to_datetime(df[date_col].astype(str), format="%Y%m%d",
                                           errors="coerce").dt.strftime("%Y-%m-%d"),
                    "financing_sse": pd.to_numeric(df[fin_col], errors="coerce"),
                })
                if tot_col is not None:
                    out["total_margin_sse"] = pd.to_numeric(df[tot_col], errors="coerce")
                out = out.dropna(subset=["date", "financing_sse"]).drop_duplicates("date")
                frames.append(out)
                last_err = None
                break
            except FetchError as e:
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = FetchError(str(e)[:160])
        cur_year += 1
        if cur_year <= e_dt.year:
            time.sleep(0.25)   # 按年分段间小睡,对 sse 友好
    if not frames:
        raise FetchError(f"market_margin failed ({last_err})")
    return (pd.concat(frames, ignore_index=True)
              .drop_duplicates("date").sort_values("date").set_index("date"))


def fetch_margin_tushare(start: str = "2010-03-01", end: Optional[str] = None) -> pd.DataFrame:
    """沪深两融日级总量(tushare `margin` 按 exchange_id 分所×按年分段,2026-09-13 批次2.1)。
    解「深市两融总量历史 akshare 不可得」老缺口(fetcher 旧注释记录)——两融 2010-03-30 起全史。
    输出 DataFrame indexed by date: financing_sse/total_margin_sse(沪,元) +
    financing_cs/total_margin_cs(**沪深合计**;北交所排除——量级微小且 2022 才起步,保两市口径
    稳定,单边缺日则合计=可得侧)。⑨恐惧贪婪杠杆成分据此从沪市单边升级两市(旧列留档并排)。"""
    from datetime import datetime
    from . import tushare_client as tc
    end = end or today_str()
    s_dt = datetime.strptime(str(start)[:10], "%Y-%m-%d")
    e_dt = datetime.strptime(str(end)[:10], "%Y-%m-%d")
    parts: dict[str, dict[str, tuple]] = {}   # date -> {SSE: (rzye, rzrqye), SZSE: (...)}
    last_err = None
    cur_year = s_dt.year
    while cur_year <= e_dt.year:
        ys = max(s_dt, datetime(cur_year, 1, 1))
        ye = min(e_dt, datetime(cur_year, 12, 31))
        for ex in ("SSE", "SZSE"):
            try:
                df = tc.query("margin", exchange_id=ex,
                              start_date=ys.strftime("%Y%m%d"), end_date=ye.strftime("%Y%m%d"),
                              fields="trade_date,exchange_id,rzye,rzrqye")
                for _, r in df.iterrows():
                    d = _ts_d8(r.get("trade_date"))
                    if d:
                        parts.setdefault(d, {})[ex] = (_ts_f(r.get("rzye")),
                                                        _ts_f(r.get("rzrqye")))
                last_err = None
            except Exception as ex_:  # noqa: BLE001
                last_err = ex_
        cur_year += 1
    if not parts:
        raise FetchError(f"margin(tushare) failed ({last_err})")
    rows = []
    for d in sorted(parts):
        sse_f, sse_t = parts[d].get("SSE", (None, None))
        sz_f, sz_t = parts[d].get("SZSE", (None, None))

        def _sum2(a, b):
            if a is None and b is None:
                return None
            return (a or 0.0) + (b or 0.0)
        rows.append({"date": d, "financing_sse": sse_f, "total_margin_sse": sse_t,
                     "financing_cs": _sum2(sse_f, sz_f),
                     "total_margin_cs": _sum2(sse_t, sz_t)})
    return pd.DataFrame(rows).set_index("date")


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


# 货币ETF: 两源净值口径结构性不同(2026-09-13 对账 max|Δ|=3.92),永远走天天基金
NAV_TS_EXCLUDE = {"511990"}


def fetch_etf_nav_tushare(symbol: str, start_date: str = "20000101",
                          end_date: str = "20500101") -> pd.DataFrame:
    """ETF 净值全史(tushare `fund_nav` 按 ts_code,2026-09-13 迁移1.7 主源)。
    返回与 fetch_etf_nav 同形 DataFrame[date: unit_nav, acc_nav](accum_nav=累计净值)。
    5 开头→.SH 其余→.SZ;QDII T+2 滞后同源语义。失败抛 TushareError/FetchError→调用方降级 em。

    对账(2026-09-13,data/recon/tushare_nav.md): 39 标的 38 PASS(|Δ|=0.0000 为主);
    511990 货币ETF FAIL(两源面值/摊余口径结构性不同,max|Δ|=3.92)→ NAV_TS_EXCLUDE 永走 em。"""
    if symbol in NAV_TS_EXCLUDE:
        raise FetchError(f"{symbol}: 货币ETF 两源净值口径不同(对账FAIL),永走 em")
    from . import tushare_client as tc
    ts_code = f"{symbol}.SH" if symbol.startswith("5") else f"{symbol}.SZ"
    df = tc.query("fund_nav", ts_code=ts_code,
                  start_date=str(start_date).replace("-", ""),
                  end_date=str(end_date).replace("-", ""))
    if df is None or len(df) == 0:
        raise FetchError(f"empty fund_nav {symbol}")
    date_col = next((c for c in df.columns if str(c) in ("nav_date", "date")), None)
    if date_col is None or "unit_nav" not in df.columns:
        raise FetchError(f"fund_nav {symbol} 列不符: {list(df.columns)}")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[date_col].astype(str), errors="coerce").dt.strftime("%Y-%m-%d"),
        "unit_nav": pd.to_numeric(df["unit_nav"], errors="coerce"),
        "acc_nav": (pd.to_numeric(df["accum_nav"], errors="coerce")
                    if "accum_nav" in df.columns else float("nan")),
    })
    return (out.dropna(subset=["date", "unit_nav"])
               .drop_duplicates("date").set_index("date").sort_index())


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
    """【⚰️ 退役 2026-09-13·tushare 迁移批次0】PE 因子 unused + cninfo 限流, 无调用方,
    留盘存档勿运行 (ADR-0002 / docs/EXECUTION_PLAN-tushare迁移.md)。

    All CSRC 证监会行业 static-PE for a given date (YYYYMMDD). One call covers every
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
    """【⚰️ 兜底路径已删 2026-09-13·tushare 迁移批次0】端点 2026-08 已死(probe P1),
    manager 不再调用, 留盘存档勿运行 (ADR-0002 / docs/EXECUTION_PLAN-tushare迁移.md)。

    An ETF's latest disclosed stock holdings (重仓股) via fund_portfolio_hold_em.

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
    # announce_date 保留(E3 三环时效链需要); aggregate_earnings 只取 yoy/type, 多列无害
    df["announce_date"] = df["_ann"].dt.strftime("%Y-%m-%d")
    return df.set_index("code")[["yoy", "type", "announce_date"]]


def fetch_stock_express(report_period: str, timeout: float = 60.0) -> pd.DataFrame:
    """All A-share 业绩快报 for a report period (YYYYMMDD) → df indexed by code.

    Columns [np_yoy, rev_yoy, announce_date] — 净利润-同比增长 / 营业收入-同比增长 / 公告日期
    (列名 2026-08-16 实测 16 列). 三环链第二环: 快报=未审计近似值, 深市年报惯例 2 月底前
    (自愿为主, 中期稀疏——期行数少是常态不是端点问题). 脏行(现值 NaN)落 None, 聚合按 notna 过滤.
    """
    df = _run_with_timeout(ak.stock_yjkb_em, timeout, date=report_period)
    if df is None or len(df) == 0:
        raise FetchError(f"empty stock_express {report_period}")
    out = pd.DataFrame({
        "code": df["股票代码"].astype(str).str.zfill(6),
        "np_yoy": pd.to_numeric(df.get("净利润-同比增长"), errors="coerce"),
        "rev_yoy": pd.to_numeric(df.get("营业收入-同比增长"), errors="coerce"),
        "announce_date": df.get("公告日期", "").astype(str).str.slice(0, 10),
    })
    return out.drop_duplicates("code", keep="last").set_index("code")


def fetch_stock_report_actual(report_period: str, timeout: float = 60.0) -> pd.DataFrame:
    """All A-share 定期报告实际值(业绩报表) for a report period → df indexed by code.

    Columns [np_yoy, rev_yoy, announce_date, eps, bvps, np_abs, rev_abs] — 净利润-同比增长 /
    营业总收入-同比增长 / 最新公告日期 / 每股收益 / 每股净资产 / 净利润-净利润(绝对值,累计口径) /
    营业总收入-营业总收入(绝对值)(列名 2026-09-12 实测扩列; 营收口径与快报不同: 总收入 vs 营业收入,
    同比比较不受影响). 三环链第三环: 正式报=审计后硬数据, 披露窗口滞后 45 天-4 个月. 累计口径
    (勿做单季拆分, 调研§3.4). 高业绩池(V8)消费: np_abs/rev_abs→TTM 净利/PE 自算, bvps→PB 分位轨.
    """
    df = _run_with_timeout(ak.stock_yjbb_em, timeout, date=report_period)
    if df is None or len(df) == 0:
        raise FetchError(f"empty stock_report_actual {report_period}")
    out = pd.DataFrame({
        "code": df["股票代码"].astype(str).str.zfill(6),
        "np_yoy": pd.to_numeric(df.get("净利润-同比增长"), errors="coerce"),
        "rev_yoy": pd.to_numeric(df.get("营业总收入-同比增长"), errors="coerce"),
        "announce_date": df.get("最新公告日期", "").astype(str).str.slice(0, 10),
        "eps": pd.to_numeric(df.get("每股收益"), errors="coerce"),
        "bvps": pd.to_numeric(df.get("每股净资产"), errors="coerce"),
        "np_abs": pd.to_numeric(df.get("净利润-净利润"), errors="coerce"),
        "rev_abs": pd.to_numeric(df.get("营业总收入-营业总收入"), errors="coerce"),
    })
    return out.drop_duplicates("code", keep="last").set_index("code")


# ---- 1.12/1.13 应急 fallback(2026-09-13 降级件·ADR-0002): 东财/sina 主源不动,tushare 应急重建。
# 实测约束: forecast/express 按报告期拉全市场=5000积分 vip 接口(forecast_vip/express_vip/income_vip),
# 2000 档只能按 ann_date 逐日拉(高峰日 forecast 1002 行实测)→ 披露窗(~4 个月)逐交易日扫描,
# 滤 end_date==报告期再拼整期面板。口径差族: 预告 yoy=幅度区间中值 vs 东财单值;express 的
# yoy_* 字段实测为**上年同期绝对值**(非百分数)→ 同比自算。
def _ts_disclosure_days(period: str) -> list[str]:
    """报告期披露窗 ann_date 交易日序列(期末次日 → +4 个月,自然日 bdate;空窗日返回 0 行跳过)。"""
    end = pd.Timestamp(period)
    days = pd.bdate_range(end + pd.Timedelta(days=1),
                          end + pd.DateOffset(months=4))
    return [d.strftime("%Y%m%d") for d in days]


def fetch_earnings_forecast_tushare(period: str) -> pd.DataFrame:
    """业绩预告东财主源的 tushare 应急重建(`forecast` 按 ann_date 窗扫,1.12 降级件)。
    形状与 fetch_earnings_forecast 一致:[yoy, type, announce_date] index=code(6 位)。
    yoy=(p_change_min+p_change_max)/2 幅度中值(东财为单值,口径差族);type 八类同名;
    同 code 多次公告取最晚 ann_date。全窗 0 行 → raise(调用方保持东财失败语义)。"""
    from . import tushare_client as tc
    frames = []
    for d8 in _ts_disclosure_days(period):
        try:
            df = tc.query("forecast", ann_date=d8,
                          fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max")
        except Exception:  # noqa: BLE001 — 单日失败跳过(幂等重跑自愈)
            continue
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        raise FetchError(f"forecast(tushare ann_date 窗扫) {period} empty")
    raw = pd.concat(frames, ignore_index=True)
    raw = raw[raw["end_date"].astype(str) == str(period)]
    if not len(raw):
        raise FetchError(f"forecast(tushare) {period} 无 end_date 命中")
    raw["code"] = raw["ts_code"].astype(str).str[:6]
    raw["yoy"] = (pd.to_numeric(raw["p_change_min"], errors="coerce")
                  + pd.to_numeric(raw["p_change_max"], errors="coerce")) / 2.0
    raw["type"] = raw["type"].astype(str)
    raw["announce_date"] = (pd.to_datetime(raw["ann_date"].astype(str), format="%Y%m%d",
                                           errors="coerce").dt.strftime("%Y-%m-%d"))
    raw = raw.sort_values("ann_date").drop_duplicates("code", keep="last")
    out = raw.set_index("code")[["yoy", "type", "announce_date"]]
    if not len(out):
        raise FetchError(f"forecast(tushare) {period} rows empty")
    return out


def fetch_stock_express_tushare(period: str) -> pd.DataFrame:
    """业绩快报东财主源的 tushare 应急重建(`express` 按 ann_date 窗扫,1.12 降级件)。
    形状与 fetch_stock_express 一致:[np_yoy, rev_yoy, announce_date] index=code。
    实测 yoy_net_profit/yoy_revenue = **上年同期绝对值** → 同比自算 (本期−同期)/|同期|×100;
    同期值 0/缺 → NaN(聚合按 notna 过滤)。全窗 0 行 → raise。"""
    from . import tushare_client as tc
    frames = []
    for d8 in _ts_disclosure_days(period):
        try:
            df = tc.query("express", ann_date=d8,
                          fields="ts_code,ann_date,end_date,revenue,n_income,yoy_net_profit,yoy_revenue")
        except Exception:  # noqa: BLE001
            continue
        if df is not None and len(df):
            frames.append(df)
    if not frames:
        raise FetchError(f"express(tushare ann_date 窗扫) {period} empty")
    raw = pd.concat(frames, ignore_index=True)
    raw = raw[raw["end_date"].astype(str) == str(period)]
    if not len(raw):
        raise FetchError(f"express(tushare) {period} 无 end_date 命中")
    raw["code"] = raw["ts_code"].astype(str).str[:6]

    def _yoy(cur_col, base_col):
        cur = pd.to_numeric(raw[cur_col], errors="coerce")
        base = pd.to_numeric(raw[base_col], errors="coerce")
        return (cur - base) / base.abs().where(base.abs() > 1e-9) * 100.0

    raw["np_yoy"] = _yoy("n_income", "yoy_net_profit")
    raw["rev_yoy"] = _yoy("revenue", "yoy_revenue")
    raw["announce_date"] = (pd.to_datetime(raw["ann_date"].astype(str), format="%Y%m%d",
                                           errors="coerce").dt.strftime("%Y-%m-%d"))
    raw = raw.sort_values("ann_date").drop_duplicates("code", keep="last")
    out = raw.set_index("code")[["np_yoy", "rev_yoy", "announce_date"]]
    if not len(out):
        raise FetchError(f"express(tushare) {period} rows empty")
    return out


def fetch_stock_balance(report_period: str, timeout: float = 60.0) -> pd.DataFrame:
    """All A-share 资产负债表汇总(zcfz) for a report period → df indexed by code.

    Columns [cash, receivables, inventory, total_assets, total_liab, equity, debt_ratio,
    announce_date] — 货币资金/应收账款/存货/总资产/总负债/股东权益合计/资产负债率(%,÷100 归一)/
    公告日期(列名 2026-09-12 实测 15 列; **无商誉/借款列**——商誉走 sina 逐股精筛腿, 有息负债
    不可得 → 存贷双高用 货币资金/总资产×资产负债率 代理口径, 见 pool/risk.py)。
    高业绩池(V8)风险筛腿: 应收占比/存贷双高代理/净资产分母。
    """
    df = _run_with_timeout(ak.stock_zcfz_em, timeout, date=report_period)
    if df is None or len(df) == 0:
        raise FetchError(f"empty stock_balance {report_period}")
    out = pd.DataFrame({
        "code": df["股票代码"].astype(str).str.zfill(6),
        "cash": pd.to_numeric(df.get("资产-货币资金"), errors="coerce"),
        "receivables": pd.to_numeric(df.get("资产-应收账款"), errors="coerce"),
        "inventory": pd.to_numeric(df.get("资产-存货"), errors="coerce"),
        "total_assets": pd.to_numeric(df.get("资产-总资产"), errors="coerce"),
        "total_liab": pd.to_numeric(df.get("负债-总负债"), errors="coerce"),
        "equity": pd.to_numeric(df.get("股东权益合计"), errors="coerce"),
        "debt_ratio": pd.to_numeric(df.get("资产负债率"), errors="coerce") / 100.0,
        "announce_date": df.get("公告日期", "").astype(str).str.slice(0, 10),
    })
    return out.drop_duplicates("code", keep="last").set_index("code")


# ---- Analyst-consensus weekly snapshot (E0, docs/RESEARCH-ETF行业业绩预期.md §7.3) ----
def parse_consensus_table(df: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """Tidy-parse ak.stock_profit_forecast_em(symbol='') output → snapshot frame.

    Pure function (no I/O) — the testable core of fetch_consensus_snapshot. Input is the
    东财 13 列整表: 代码/名称/研报数/机构投资评级(近六个月)-买入..卖出/「YYYY预测每股收益」×4.
    Output: DataFrame indexed by code(6-digit str) with columns [n_reports, rating_buy,
    rating_over, rating_neutral, rating_reduce, rating_sell, eps_fy1, eps_fy2, fy1_year, fy2_year].
    财年滚动对齐: fy1=当年, fy2=次年 — EPS 列按年份前缀匹配, 年末列名翻滚时自动跟上
    (fy2 无列时为 NaN, E2 聚合按 coverage 门自然降级). Raises FetchError when no
    forecast-year column ≥ now.year exists.
    """
    now = now or datetime.now()
    eps_cols: dict[int, str] = {}
    for c in df.columns:
        m = re.match(r"^(\d{4})预测每股收益$", str(c).strip())
        if m:
            eps_cols[int(m.group(1))] = c
    yrs = sorted(y for y in eps_cols if y >= now.year)[:2]
    if not yrs:
        raise FetchError(f"no forecast-EPS year columns >= {now.year} in {list(df.columns)[:14]}")

    def _col(name: str):
        return pd.to_numeric(df[name], errors="coerce") if name in df.columns else float("nan")

    out = pd.DataFrame({
        "code": df["代码"].astype(str).str.zfill(6),
        "n_reports": _col("研报数"),
        "rating_buy": _col("机构投资评级(近六个月)-买入"),
        "rating_over": _col("机构投资评级(近六个月)-增持"),
        "rating_neutral": _col("机构投资评级(近六个月)-中性"),
        "rating_reduce": _col("机构投资评级(近六个月)-减持"),
        "rating_sell": _col("机构投资评级(近六个月)-卖出"),
        "eps_fy1": _col(eps_cols[yrs[0]]),
        "eps_fy2": _col(eps_cols[yrs[1]]) if len(yrs) > 1 else float("nan"),
        "fy1_year": yrs[0],
        "fy2_year": yrs[1] if len(yrs) > 1 else None,
    })
    return out.drop_duplicates("code", keep="last").set_index("code")


def fetch_consensus_snapshot(min_rows: int = 1000, timeout: float = 90.0) -> pd.DataFrame:
    """Whole-market analyst-consensus snapshot (东财盈利预测整表, E0 周度节奏).

    ak.stock_profit_forecast_em(symbol='') — 整表一次 ~1.4s / ~2800 行. 按股查询
    (symbol='<code>') 已 endpoint-rot 死掉(NoneType; probe_earnings_sources P4, 2026-08-16),
    只能整表取. Thin-guard: 行数 < min_rows 视为端点半死, 抛 FetchError 而非返回垃圾 —
    etf_earnings 层 2026-08 静默写零行的教训(调研报告 §6.2), 空结果绝不落库.
    """
    df = _run_with_timeout(ak.stock_profit_forecast_em, timeout, symbol="")
    n = 0 if df is None else len(df)
    if n < min_rows:
        raise FetchError(f"consensus table too thin ({n} rows < {min_rows})")
    return parse_consensus_table(df)


def fetch_index_constituents(index_code: str, timeout: float = 40.0) -> pd.DataFrame:
    """Index constituents with OFFICIAL weights (中证指数官网, 月度快照) — B 路线主源.

    ak.index_stock_cons_weight_csindex — 全成分+官方权重, 一次一指数(~0.1-0.2s). Returns
    DataFrame with columns [code(6-digit str), name, weight, snapshot_date] and
    attrs["index_name"] = 官方指数名称 — 调用方用 expect 关键词做名称哨兵, 不符即拒
    (防猜错代码/端点串台; 调研期靠它抓到 930999=SHS大湾区 错配). Empty DataFrame when
    the index is unknown to csindex (国证/中华交易服务系指数, e.g. 创业板指/CES半导体) —
    caller must treat empty as skip, never write empty aggregates.
    """
    empty = pd.DataFrame(columns=["code", "name", "weight", "snapshot_date"])
    try:
        df = _run_with_timeout(ak.index_stock_cons_weight_csindex, timeout, symbol=index_code)
    except Exception as e:  # noqa: BLE001
        log.warning("index_stock_cons_weight_csindex(%s) failed: %s", index_code, str(e)[:80])
        return empty
    if df is None or not len(df):
        return empty
    out = pd.DataFrame({
        "code": df["成分券代码"].astype(str).str.zfill(6),
        "name": df["成分券名称"].astype(str) if "成分券名称" in df.columns else "",
        "weight": pd.to_numeric(df["权重"], errors="coerce").fillna(0.0),
        "snapshot_date": str(df["日期"].iloc[0]),
    })
    if "指数名称" in df.columns:
        out.attrs["index_name"] = str(df["指数名称"].iloc[0])
    return out


def fetch_index_constituents_tushare(index_code: str, lookback_days: int = 45) -> pd.DataFrame:
    """指数成分+官方权重(tushare `index_weight`,中证+国证系,2026-09-13 迁移1.10)。
    定位: **国证系主源**(399006 等 csindex 不覆盖——159915 码表缺口的补齐)+ csindex
    失败 fallback;中证系日常仍走 csindex(有成分名称+名称哨兵,index_weight 无名称列)。
    返回同形 [code,name,weight,snapshot_date](name 恒空串——store 保留存量名称);
    取 lookback 窗内最新 trade_date 快照;**权重和 90-110% 哨兵**防串台(index_weight
    无指数名称列,靠此替代名称哨兵)。空指数返回空表(调用方 skip)。"""
    from datetime import date as _date, timedelta as _td
    from . import tushare_client as tc
    empty = pd.DataFrame(columns=["code", "name", "weight", "snapshot_date"])
    ts_code = (f"{index_code}.SZ" if str(index_code).startswith("399")
               else f"{index_code}.SH")
    start = (_date.today() - _td(days=lookback_days)).strftime("%Y%m%d")
    df = tc.query("index_weight", index_code=ts_code, start_date=start,
                  fields="ts_code,con_code,weight,trade_date")
    if df is None or len(df) == 0:
        return empty
    latest = str(df["trade_date"].max())
    df = df[df["trade_date"].astype(str) == latest]
    w = pd.to_numeric(df["weight"], errors="coerce").fillna(0.0)
    if not (90.0 <= float(w.sum()) <= 110.0):
        raise FetchError(f"index_weight {index_code} 权重和 {float(w.sum()):.1f}% "
                         "越界(疑串台/残缺) — 拒写")
    snap = (f"{latest[:4]}-{latest[4:6]}-{latest[6:]}" if len(latest) == 8 else latest)
    return pd.DataFrame({
        "code": df["con_code"].astype(str).str.split(".").str[0].str.zfill(6),
        "name": "",
        "weight": w,
        "snapshot_date": snap,
    })


# ---- Candidate-pool screening feeds (V7 pool · 第六看板 候选个股池 · 只读旁路 ADR-0001) ----
# push2 原生通道(2026-09-12 实证): push2.eastmoney.com 的 WAF 按 TLS 指纹拦 python-requests
# (trust_env=False 真直连也 RemoteDisconnected)、放行 curl;系统代理(Clash 系统代理模式)对 python
# 的 CONNECT 也断。故此族端点不再走 akshare(requests),改原生分页: requests 直连优先 → curl
# 子进程兜底(curl 直连/走 HTTPS_PROXY 环境变量代理均实测通)。datacenter 族(yjbb/zcfz)与 sina 不受影响。
_PUSH2_BASE = "https://push2.eastmoney.com/api/qt/clist/get"


def _push2_clist(fs: str, fields: str, timeout: float = 25.0, max_pages: int = 60) -> list[dict]:
    """push2 clist 全量分页(纯拉取层)。Returns rows(list of {field: 值}, fltt=2 数值化);
    空结果 → FetchError。requests 直连首页探测,失败则全部分页走 curl。"""
    import json as _json
    import subprocess as _sp
    import urllib.parse as _up
    params0 = {"pn": "1", "pz": "200", "po": "1", "np": "1",
               "ut": "bd1d9ddb04089700cf9c27f6f7426281",
               "fltt": "2", "invt": "2", "fid": "f12", "fs": fs, "fields": fields}
    use_curl = False

    def _fetch(params: dict) -> dict:
        nonlocal use_curl
        if not use_curl:
            try:
                import requests as _rq
                s = _rq.Session()
                s.trust_env = False   # 绕 Windows 系统代理(Clash)——python 的 CONNECT 到它也断
                r = s.get(_PUSH2_BASE, params=params, timeout=timeout)
                r.raise_for_status()
                return r.json().get("data") or {}
            except Exception:  # noqa: BLE001 — WAF 指纹拦截常态,退 curl
                use_curl = True
        q = "&".join(f"{k}={_up.quote(str(v), safe='')}" for k, v in params.items())
        try:
            out = _sp.run(
                ["curl", "-s", "-m", str(int(timeout)), "-H", "User-Agent: Mozilla/5.0",
                 f"{_PUSH2_BASE}?{q}"], capture_output=True, text=True,
                timeout=timeout + 15)
            if out.returncode == 0 and out.stdout.strip().startswith("{"):
                return _json.loads(out.stdout).get("data") or {}
        except Exception:  # noqa: BLE001
            pass
        return {}

    rows: list[dict] = []
    for pn in range(1, max_pages + 1):
        data = _fetch({**params0, "pn": str(pn)})
        diff = data.get("diff") or []
        if not diff:
            break
        rows.extend(diff)
        if len(diff) < 200:
            break
        time.sleep(0.15)  # 对 push2 客气一点(整表 27 页级)
    if not rows:
        raise FetchError(f"push2 clist empty (fs={fs[:40]})")
    return rows


def fetch_stock_spot_via_tencent(codes: list[str], timeout: float = 25.0,
                                 batch: int = 60) -> pd.DataFrame:
    """腾讯批量行情(qt.gtimg.cn, 60 码/请求, GBK)——push2 全断时的 spot 兜底数据源。

    与 fetch_stock_spot 同列契约 [name, close, mktcap, float_mktcap, pe_dyn, pb](indexed by
    code)。PE 用腾讯口径(TTM,非东财动态——仅交叉核对列,不进判定,差异读图说明已注明);
    市值亿→元。停牌/退市行腾讯返回空串或价格 0 → 自然落 NaN。代码需带市场前缀
    (sh/sz),本函数按段自推。调用方给「DB 已知代码清单」(report_actual∪daily_prices∪
    consensus 段过滤)——新 IPO 首份财报/价格落地前不在快照里(诚实边界,读图说明注明)。"""
    import requests as _rq
    pre = {"60": "sh", "68": "sh", "00": "sz", "30": "sz"}
    norm = []
    for c in codes:
        s = str(c).zfill(6)
        if s[:2] in pre:
            norm.append(f"{pre[s[:2]]}{s}")
    out_rows = []
    s = _rq.Session()
    s.trust_env = False
    for i in range(0, len(norm), batch):
        chunk = norm[i:i + batch]
        try:
            r = s.get("https://qt.gtimg.cn/q=" + ",".join(chunk), timeout=timeout)
            r.encoding = "gbk"
            for line in r.text.splitlines():
                line = line.strip()
                if not line.startswith("v_") or '="' not in line:
                    continue
                head, payload = line.split('="', 1)
                code = head[4:]                      # v_sh600519 → 600519
                f = payload.rstrip('";\r\n ').split("~")
                if len(f) < 47 or not f[1]:
                    continue
                def _f(idx):
                    try:
                        return float(f[idx])
                    except (ValueError, IndexError):
                        return float("nan")
                price = _f(3)
                if price <= 0:
                    continue                          # 停牌/退市占位行
                out_rows.append({
                    "code": code, "name": f[1], "close": price,
                    "mktcap": _f(45) * 1e8, "float_mktcap": _f(44) * 1e8,
                    "pe_dyn": _f(39), "pb": _f(46),
                })
        except Exception:  # noqa: BLE001 — 单批失败跳过(网络抖动),下一批继续
            continue
        time.sleep(0.12)
    if not out_rows:
        raise FetchError("tencent spot empty (all batches failed)")
    df = pd.DataFrame(out_rows).drop_duplicates("code", keep="last").set_index("code")
    return df


def fetch_stock_spot(min_rows: int = 4000, timeout: float = 60.0, retries: int = 3) -> pd.DataFrame:
    """全市场 A 股现货快照(push2 clist 原生分页, curl 兜底;2026-09-12 起)。

    Returns DataFrame indexed by 6-digit code with [name, close, mktcap, float_mktcap,
    pe_dyn, pb] — 候选池 universe 的 ST/退 名称过滤 + 展示名唯一来源 + 高业绩池(V8)的
    市值/估值当前口径(总市值/流通市值(元)/市盈率-动态/市净率)。close 仅调试用(筛选一律走
    daily_prices 复权序列);停牌股最新价='-' → NaN 自然落。
    Thin-guard: 行数 < min_rows 视为端点半死 → FetchError, 空结果绝不落库(consensus 同款教训)。
    """
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2.0 * attempt)
        try:
            rows = _push2_clist("m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
                                "f2,f9,f12,f14,f20,f21,f23", timeout=timeout)
            if len(rows) < min_rows:
                raise FetchError(f"spot table too thin ({len(rows)} rows < {min_rows})")
            df = pd.DataFrame(rows)
            out = pd.DataFrame({
                "code": df["f12"].astype(str).str.zfill(6),
                "name": df["f14"].astype(str),
                "close": pd.to_numeric(df["f2"], errors="coerce"),
                "mktcap": pd.to_numeric(df["f20"], errors="coerce"),
                "float_mktcap": pd.to_numeric(df["f21"], errors="coerce"),
                "pe_dyn": pd.to_numeric(df["f9"], errors="coerce"),
                "pb": pd.to_numeric(df["f23"], errors="coerce"),
            })
            return out.drop_duplicates("code", keep="last").set_index("code")
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"stock_spot failed ({last_err})")


def fetch_stock_spot_from_consensus(min_rows: int = 1000, timeout: float = 90.0) -> pd.DataFrame:
    """[已退役·留盘休眠] spot 名称兜底(V8 起不再接线: 全市场宇宙需要市值/PE/PB 列,名称-only
    兜底撑不住; push2 被拦时 manager 显式报错保最后快照, 不静默降级——2026-09-12 用户批准)。
    保留函数体供历史参考, 调用方已删。"""
    df = _run_with_timeout(ak.stock_profit_forecast_em, timeout, symbol="")
    n = 0 if df is None else len(df)
    if n < min_rows or "代码" not in df.columns or "名称" not in df.columns:
        raise FetchError(f"consensus table too thin for names ({n} rows)")
    out = pd.DataFrame({
        "code": df["代码"].astype(str).str.zfill(6),
        "name": df["名称"].astype(str),
        "close": float("nan"),
    })
    return out.drop_duplicates("code", keep="last").set_index("code")


# 行业名 → 板块代码(BKxxxx)进程内缓存: list 拉一次,cons 逐板块复用(push2 同族原生通道)
_INDUSTRY_CODES: dict[str, str] = {}


def fetch_industry_list(timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """东财行业板块名录(push2 clist 原生, m:90 t:2)。Returns DataFrame [industry];
    同时填 _INDUSTRY_CODES(名→BK代码, fetch_industry_cons 的 fs 参数需要)。
    Thin-guard: < 50 板块视为端点半死 → FetchError。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            rows = _push2_clist("m:90 t:2 f:!50", "f12,f14", timeout=timeout)
            if len(rows) < 50:
                raise FetchError(f"industry list too thin ({len(rows)} boards)")
            names = [str(r.get("f14")) for r in rows]
            _INDUSTRY_CODES.clear()
            _INDUSTRY_CODES.update({str(r.get("f14")): str(r.get("f12")) for r in rows})
            return pd.DataFrame({"industry": names})
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"industry list failed ({last_err})")


def fetch_industry_cons(industry: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """单行业板块成分股(push2 clist 原生, fs=b:BKxxxx;依赖 fetch_industry_list 先跑一次
    填名字→代码缓存,未命中时现拉一次名录)。Returns DataFrame [code(6位), name]。
    空结果按端点异常处理 → FetchError(调用方记日志跳过该板块)。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.0 * attempt)
        try:
            bk = _INDUSTRY_CODES.get(industry)
            if not bk:
                fetch_industry_list(timeout=timeout)
                bk = _INDUSTRY_CODES.get(industry)
            if not bk:
                raise FetchError(f"unknown board {industry}")
            rows = _push2_clist(f"b:{bk} f:!50", "f12,f14", timeout=timeout)
            if not rows:
                raise FetchError("empty")
            out = pd.DataFrame({
                "code": [str(r.get("f12")).zfill(6) for r in rows],
                "name": [str(r.get("f14")) for r in rows],
            })
            return out.drop_duplicates("code", keep="last")
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"industry_cons {industry} failed ({last_err})")


# ---- tushare pro 腿(2000 积分档 · 2026-09-13 引入; 替换被 push2 掐脖子的腿+补齐
#      长期缺数据源; 客户端见 tushare_client.py, token 缺失时各腿 TushareError → 调用方降级) ----
def _ts_norm_code(ts_code: str) -> str:
    """600519.SH / 000001.SZ → 6 位数字代码(.BJ 北交所返回原样,调用方按段过滤)。"""
    s = str(ts_code)
    return s.split(".")[0].zfill(6) if "." in s else s.zfill(6)


def _ts_latest_trade_date(max_back: int = 10) -> str:
    """最近的 tushare daily_basic 有数据的交易日(YYYYMMDD): 从今天往回试(周末/节假日空)。"""
    from datetime import date, timedelta
    from . import tushare_client as tc
    d = date.today()
    for _ in range(max_back):
        df = tc.query("daily_basic", trade_date=d.strftime("%Y%m%d"),
                      fields="ts_code,close,pe_ttm,pb,total_mv,circ_mv")
        if df is not None and len(df):
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    raise FetchError("daily_basic 近 10 天无数据(节假日异常或权限)")


def fetch_stock_spot_tushare(timeout: float = 60.0) -> pd.DataFrame:
    """全市场现货快照(tushare 版): stock_basic 名称 ⨝ daily_basic(最新交易日估值)。

    与 fetch_stock_spot 同列契约 [name, close, mktcap, float_mktcap, pe_dyn, pb]
    (indexed by 6 位 code); pe_dyn 承载 tushare pe_ttm 口径(交叉核对列,同腾讯)。
    总市值/流通市值单位: tushare 万元 → ×1e4 转元。北交所按段自然过滤。"""
    from . import tushare_client as tc
    basic = tc.query("stock_basic", exchange="", list_status="L",
                     fields="ts_code,name,list_date")
    td = _ts_latest_trade_date()
    daily = tc.query("daily_basic", trade_date=td,
                     fields="ts_code,close,pe_ttm,pb,total_mv,circ_mv")
    df = daily.merge(basic[["ts_code", "name"]], on="ts_code", how="left")
    df["code"] = df["ts_code"].map(_ts_norm_code)
    df = df[df["code"].str.startswith(("60", "68", "00", "30"))]
    out = pd.DataFrame({
        "code": df["code"],
        "name": df["name"].fillna("").astype(str),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "mktcap": pd.to_numeric(df["total_mv"], errors="coerce") * 1e4,
        "float_mktcap": pd.to_numeric(df["circ_mv"], errors="coerce") * 1e4,
        "pe_dyn": pd.to_numeric(df["pe_ttm"], errors="coerce"),
        "pb": pd.to_numeric(df["pb"], errors="coerce"),
    })
    if len(out) < 4000:
        raise FetchError(f"tushare spot too thin ({len(out)})")
    return out.drop_duplicates("code", keep="last").set_index("code")


def fetch_sw_industry_tushare(timeout: float = 60.0) -> pd.DataFrame:
    """申万 2021 行业成分(三级)。Returns DataFrame
    [code, l1, l2, l3, in_date, out_date, is_new] —— in/out 日期让回放可以做
    point-in-time 行业归属(东财口径从未有过)。逐 L3 拉成分(~346 次调用,节流后
    ~3-5 分钟,月度节奏一次成本)。"""
    from . import tushare_client as tc
    cat = tc.query("index_classify", level="L3", src="SW2021")
    frames = []
    for _, row in cat.iterrows():
        l3_code = row.get("index_code")
        try:
            m = tc.query("index_member_all", l3_code=l3_code)
        except Exception:  # noqa: BLE001 — 单板块失败跳过
            continue
        if m is None or not len(m):
            continue
        m = m.copy()
        con_col = "ts_code" if "ts_code" in m.columns else "con_code"   # 实测列名 ts_code
        m["code"] = m[con_col].map(_ts_norm_code)
        m = m.rename(columns={"l1_name": "l1", "l2_name": "l2", "l3_name": "l3"})
        for col in ("l1", "l2", "l3"):
            if col not in m.columns:
                m[col] = ""
        frames.append(m[["code", "l1", "l2", "l3", "in_date", "out_date", "is_new"]])
        time.sleep(0.05)
    if not frames:
        raise FetchError("sw industry empty (all L3 failed)")
    out = pd.concat(frames, ignore_index=True)
    out = out[out["code"].str.startswith(("60", "68", "00", "30"))]
    return out.drop_duplicates(["code", "l3"], keep="last")


def fetch_namechange_tushare(timeout: float = 60.0) -> pd.DataFrame:
    """股票曾用名全史(namechange)——历史 ST 过滤的唯一原料(回测按当时名称执行
    非 ST 过滤,消掉终审里最大的已知偏差)。Returns [code, name, start_date,
    end_date, ann_date, change_reason];分页拉全量。注: 上游 issue 反映日期参数
    语义有坑——本腿不做日期过滤,全量拉后由消费方自校验(名称区间连续性)。"""
    from . import tushare_client as tc
    df = tc.query_paged("namechange", page_size=4000, max_pages=60,
                        fields="ts_code,name,start_date,end_date,ann_date,change_reason")
    if not len(df):
        raise FetchError("namechange empty")
    df = df.copy()
    df["code"] = df["ts_code"].map(_ts_norm_code)
    return df[["code", "name", "start_date", "end_date", "ann_date", "change_reason"]]


def fetch_fina_indicator_tushare(code: str, timeout: float = 60.0) -> pd.DataFrame:
    """财务指标(按股全历史一次调用,fina_indicator)——扣非净利润(profit_dedt)批量源。
    2000 积分档 ts_code 必填(按期全市场为 5000 积分 vip 版),按股形状恰好替换 sina
    逐股精筛腿且无限流。Returns [code, report_period, ann_date, profit_dedt, roe]。"""
    from . import tushare_client as tc
    ts_code = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
    df = tc.query("fina_indicator", ts_code=ts_code,
                  fields="ts_code,end_date,ann_date,profit_dedt,roe")
    if not len(df):
        raise FetchError(f"fina_indicator {code} empty")
    out = df.rename(columns={"end_date": "report_period"})
    out["code"] = code
    return out[["code", "report_period", "ann_date", "profit_dedt", "roe"]]


def fetch_balancesheet_tushare(code: str, timeout: float = 60.0) -> pd.DataFrame:
    """资产负债表明细(按股全历史一次调用,balancesheet)——商誉/货币资金/借款/流动项:
    存贷双高精确口径 + 营运资本/长期负债门的数据底座。Returns [code, report_period,
    ann_date, monetary_cap, accounts_receiv, goodwill, total_cur_assets,
    total_cur_liab, total_assets, total_liab, st_borr, lt_borr, bond_payable]。"""
    from . import tushare_client as tc
    ts_code = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"
    src_cols = ("money_cap", "accounts_receiv", "goodwill", "total_cur_assets",
                "total_cur_liab", "total_assets", "total_liab", "st_borr", "lt_borr",
                "bond_payable")   # tushare 实测列名: 货币资金=money_cap(非 monetary_cap)
    out_cols = ("monetary_cap", "accounts_receiv", "goodwill", "total_cur_assets",
                "total_cur_liab", "total_assets", "total_liab", "st_borr", "lt_borr",
                "bond_payable")
    fields = "ts_code,end_date,ann_date," + ",".join(src_cols)
    df = tc.query("balancesheet", ts_code=ts_code, fields=fields)
    if not len(df):
        raise FetchError(f"balancesheet {code} empty")
    out = df.rename(columns=dict(zip(src_cols, out_cols)) | {"end_date": "report_period"})
    out["code"] = code
    return out[["code", "report_period", "ann_date"] + list(out_cols)]

# ---- Broad-index daily / valuation (V4 tracker) — sina + legulegu, proxy-independent ----
def _index_prefix(symbol: str) -> str:
    """Broad-index sina prefix: 399xxx (深证, e.g. 创业板指) -> sz; everything else
    (000016/000300/000905/000688 上证/中证系列) -> sh. Distinct from _szsh_prefix (ETF/stock)."""
    s = str(symbol)
    return "sz" if s.startswith("399") else "sh"


def fetch_index_daily(symbol: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """Broad-index daily OHLCV (sina stock_zh_index_daily, RAW — indices need no 复权).

    Returns DataFrame indexed by date(str): open/high/low/close/volume. Covers 上证综指/沪深300/
    创业板指/科创50/上证50/中证500/中证1000 (科创50 from 2020-01, 中证1000 from 2014-10,
    创业板指 from 2010-06, others back to 2002-2005).
    """
    pre = _index_prefix(symbol)
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.stock_zh_index_daily, timeout, symbol=f"{pre}{symbol}")
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            return _normalize(df)
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"{symbol}: index_daily failed ({last_err})")


def fetch_index_daily_tushare(symbol: str) -> pd.DataFrame:
    """宽基指数日线全史(tushare `index_daily`,2026-09-13 迁移1.8 主源)。399 开头→.SZ 其余→.SH。
    同形 _normalize 输出(open/high/low/close/volume;amount 千元单位——表无此列,弃)。
    vol 单位与 sina 的一致性由对账(recon index)验证。失败→调用方降级 sina。"""
    from . import tushare_client as tc
    ts_code = f"{symbol}.SZ" if str(symbol).startswith("399") else f"{symbol}.SH"
    df = tc.query("index_daily", ts_code=ts_code)
    if df is None or len(df) == 0:
        raise FetchError(f"empty index_daily(tushare) {symbol}")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d",
                               errors="coerce").dt.strftime("%Y-%m-%d"),
        "open": pd.to_numeric(df["open"], errors="coerce"),
        "high": pd.to_numeric(df["high"], errors="coerce"),
        "low": pd.to_numeric(df["low"], errors="coerce"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
        "volume": pd.to_numeric(df["vol"], errors="coerce"),
    })
    return (out.dropna(subset=["date", "close"])
               .drop_duplicates("date").set_index("date").sort_index())


def fetch_index_pe(name: str, timeout: float = 40.0, retries: int = 3) -> pd.DataFrame:
    """Broad-index PE history (legulegu stock_index_pe_lg). `name` = Chinese index name
    (沪深300/上证50/中证500). Returns DataFrame indexed by date(str): pe_ttm = 滚动市盈率,
    pe_median = 滚动市盈率中位数. Back to 2005 (~5k rows). 创业板指/科创50 NOT in the supported
    set (raises FetchError) — caller should skip them."""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2.0)
        try:
            df = _run_with_timeout(ak.stock_index_pe_lg, timeout, symbol=name)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            cols = list(df.columns)
            date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])
            ttm_col = next((c for c in cols if str(c).strip() == "滚动市盈率"), None)
            med_col = next((c for c in cols if str(c).strip() == "滚动市盈率中位数"), None)
            if ttm_col is None:
                raise FetchError(f"index_pe {name}: no 滚动市盈率 col, got {cols}")
            out = pd.DataFrame({
                "date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d"),
                "pe_ttm": pd.to_numeric(df[ttm_col], errors="coerce"),
                "pe_median": pd.to_numeric(df[med_col], errors="coerce") if med_col else pd.NA,
            })
            return out.dropna(subset=["date", "pe_ttm"]).drop_duplicates("date").set_index("date").sort_index()
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"{name}: index_pe failed ({last_err})")


def fetch_index_pb(name: str, timeout: float = 40.0, retries: int = 3) -> pd.DataFrame:
    """Broad-index PB history (legulegu stock_index_pb_lg). `name` = Chinese index name
    (沪深300/上证50/中证500). Returns DataFrame indexed by date(str): pb, pb_median.
    Back to 2005. 创业板指/科创50 NOT supported (raises FetchError)."""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2.0)
        try:
            df = _run_with_timeout(ak.stock_index_pb_lg, timeout, symbol=name)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            cols = list(df.columns)
            date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])
            pb_col = next((c for c in cols if str(c).strip() == "市净率"), None)
            med_col = next((c for c in cols if str(c).strip() == "市净率中位数"), None)
            if pb_col is None:
                raise FetchError(f"index_pb {name}: no 市净率 col, got {cols}")
            out = pd.DataFrame({
                "date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d"),
                "pb": pd.to_numeric(df[pb_col], errors="coerce"),
                "pb_median": pd.to_numeric(df[med_col], errors="coerce") if med_col else pd.NA,
            })
            return out.dropna(subset=["date", "pb"]).drop_duplicates("date").set_index("date").sort_index()
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"{name}: index_pb failed ({last_err})")


# 商品(周期股上游领先指标)→ 期货代码映射。用于 A 类强形式领先信号(周期股业绩的日频领先)。
# 有序 dict:顺序即看板展示顺序(新能源金属→基本金属→黑色→贵金属→能源→化工建材→农业)。
# 新增/调整品种只改这里;manager.COMMODITY_VARIETIES 与 stock_report 看板品种列表均从此派生(单一数据源)。
COMMODITY_CODES = {
    "碳酸锂": "LC", "铜": "CU", "铝": "AL", "锌": "ZN",
    "螺纹钢": "RB", "铁矿石": "I", "焦煤": "JM",
    "黄金": "AU", "白银": "AG", "原油": "SC",
    "LPG": "PG",
    "玻璃": "FG", "纯碱": "SA", "尿素": "UR",
    "豆粕": "M", "玉米": "C", "生猪": "LH",
}


def fetch_commodity_price(varieties: list[str], start: str = "2020-01-01",
                          end: Optional[str] = None, timeout: float = 40.0,
                          retries: int = 2) -> pd.DataFrame:
    """商品期货连续合约日线(日频,周期股上游领先指标,A 类强形式信号)。varieties=品种中文名(见 COMMODITY_CODES)。
    用 futures_zh_daily_sina(symbol=code+'0',sina 连续合约)——秒级、全历史、当前(远快于 spot 面板)。
    逐品种循环,失败品种跳过(不拖垮整批);全失败才 raise。返回长表 [variety, date, close]。"""
    frames = []
    last_err = None
    for v in varieties:
        code = COMMODITY_CODES.get(v)
        if not code:
            continue
        for attempt in range(retries):
            if attempt > 0:
                time.sleep(1.0)
            try:
                df = _run_with_timeout(ak.futures_zh_daily_sina, timeout, symbol=code + "0")
                if df is None or len(df) == 0:
                    raise FetchError("empty")
                d = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
                sub = pd.DataFrame({"variety": v, "date": d,
                                    "close": pd.to_numeric(df["close"], errors="coerce")})
                sub = sub.dropna(subset=["date", "close"]).drop_duplicates(["variety", "date"])
                if start:
                    sub = sub[sub["date"] >= start]
                if end:
                    sub = sub[sub["date"] <= end]
                frames.append(sub)
                last_err = None
                break
            except FetchError as e:
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = FetchError(str(e)[:200])
    if not frames:
        raise FetchError(f"commodity_price {varieties} failed ({last_err})")
    return pd.concat(frames, ignore_index=True).sort_values(["variety", "date"])


# tushare 主力连续合约代码后缀(2026-09-13 迁移1.11;RB.SHF=螺纹钢主力 已对账实证,
# 各所后缀经 fut_basic 核: 上期.SHF/能源.INE/大商.DCE/郑商.ZCE/广期.GFX)
COMMODITY_TS_SUFFIX = {
    "LC": "GFX", "CU": "SHF", "AL": "SHF", "ZN": "SHF",
    "RB": "SHF", "I": "DCE", "JM": "DCE",
    "AU": "SHF", "AG": "SHF", "SC": "INE",
    "PG": "ZCE", "FG": "ZCE", "SA": "ZCE", "UR": "ZCE",
    "M": "DCE", "C": "DCE", "LH": "DCE",
}
# tushare fut_daily 无主力连续数据的品种(2026-09-13 实测 empty): 广期碳酸锂/郑商 LPG
# ——这两品种永走 sina 连续,manager 按品种补位
TS_COMMODITY_EXCLUDE = {"碳酸锂", "LPG"}


def fetch_commodity_price_tushare(varieties: list[str], start: str = "2020-01-01",
                                  end: Optional[str] = None) -> pd.DataFrame:
    """商品主力连续日线(tushare `fut_daily` ts_code=<品种>.<所>,2026-09-13 迁移1.11 主源)。
    与 sina 连续(code+'0')同为主力拼接——两源主力判定日可能差 1-2 天,换月附近 close 跳变
    差由对账(recon commodity·口径差族)量化(中位 0.0000%,>1% 日=换月窗口);正常交易日一致。
    TS_COMMODITY_EXCLUDE 品种(tushare 无主力连续)直接跳过→调用方走 sina 补位。
    2020 起 ≤2000 行/品种单次。逐品种循环,失败品种跳过;全失败 raise。
    返回同形长表 [variety, date, close]。"""
    from . import tushare_client as tc
    s = str(start).replace("-", "") or "20200101"
    e = str(end or today_str()).replace("-", "")
    frames = []
    last_err = None
    for v in varieties:
        if v in TS_COMMODITY_EXCLUDE:
            continue
        code = COMMODITY_CODES.get(v)
        suf = COMMODITY_TS_SUFFIX.get(code or "")
        if not code or not suf:
            continue
        try:
            df = tc.query("fut_daily", ts_code=f"{code}.{suf}",
                          start_date=s if s >= "20200101" else "20200101", end_date=e,
                          fields="ts_code,trade_date,close")
            if df is None or len(df) == 0:
                raise FetchError("empty")
            d = (pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d",
                                errors="coerce").dt.strftime("%Y-%m-%d"))
            sub = pd.DataFrame({"variety": v, "date": d,
                                "close": pd.to_numeric(df["close"], errors="coerce")})
            sub = sub.dropna(subset=["date", "close"]).drop_duplicates(["variety", "date"])
            if len(sub):
                frames.append(sub)
            last_err = None
        except Exception as ex:  # noqa: BLE001
            last_err = ex
    if not frames:
        raise FetchError(f"commodity_price(tushare) {varieties} failed ({last_err})")
    return pd.concat(frames, ignore_index=True).sort_values(["variety", "date"])


def fetch_commodity_spot(varieties: Optional[list[str]] = None, timeout: float = 12.0,
                         retries: int = 1) -> pd.DataFrame:
    """商品实时快照(盘前/盘中可调,A 类信号提速:夜盘隔夜变动在开盘前可见)。

    用 futures_zh_spot(symbol=code+'0', market='CF', sina 实时)——含夜盘时段的最新价:
    早 8:30 拉到的是昨夜夜盘收盘价,与最近日收盘相除 = 隔夜变动%(无夜盘品种隔夜≈0)。
    逐品种循环,失败品种跳过;全失败才 raise。返回长表 [variety, price, quote_time]。"""
    varieties = varieties if varieties is not None else list(COMMODITY_CODES.keys())
    frames = []
    last_err = None
    for v in varieties:
        code = COMMODITY_CODES.get(v)
        if not code:
            continue
        for attempt in range(retries + 1):
            if attempt > 0:
                time.sleep(0.8)
            try:
                df = _run_with_timeout(ak.futures_zh_spot, timeout, symbol=code + "0",
                                       market="CF", adjust="0")
                if df is None or len(df) == 0:
                    raise FetchError("empty")
                row = df.iloc[0]
                px = pd.to_numeric(pd.Series([row.get("current_price")]), errors="coerce").iloc[0]
                if pd.isna(px) or float(px) <= 0:
                    raise FetchError("no price")
                frames.append(pd.DataFrame([{"variety": v, "price": float(px),
                                             "quote_time": str(row.get("time", ""))}]))
                last_err = None
                break
            except FetchError as e:
                last_err = e
            except Exception as e:  # noqa: BLE001
                last_err = FetchError(str(e)[:200])
    if not frames:
        raise FetchError(f"commodity_spot {varieties} failed ({last_err})")
    return pd.concat(frames, ignore_index=True)


# 大宗商品国际基准(第八看板 · 2026-09):品种 → (sina 外盘符号, 名称, 单位)。
# 有基准的品种看板以国际价为主语、国内价对照(国际价=研究传导方向,国内价=A股投资指导——
# 国际价格波动一般传导至国内)。无基准品种(黑色/建材/化肥/新能源金属/生猪)面板标「国内定价」。
# 端点真相(2026-09-06 实测):LME铜=CAD(沪铜对口)、LME铝=AHD、LME锌=ZSD、CBOT豆粕=SM、CBOT玉米=C,
# 均 futures_foreign_hist(sina)~10年全历史且当日新鲜;HG(COMEX铜)/LHC 数据失真不用(见上方注)。
# 豆粕对口 SM(CBOT豆粕)而非 S(美豆)——与国内豆粕口径一致。
COMMODITY_BENCHMARKS = {
    "铜": ("CAD", "LME铜", "美元/吨"),
    "铝": ("AHD", "LME铝", "美元/吨"),
    "锌": ("ZSD", "LME锌", "美元/吨"),
    "黄金": ("GC", "COMEX黄金", "美元/盎司"),
    "白银": ("SI", "COMEX白银", "美元/盎司"),
    "原油": ("CL", "WTI原油", "美元/桶"),
    "豆粕": ("SM", "CBOT豆粕", "美元/短吨"),
    "玉米": ("C", "CBOT玉米", "美分/蒲式耳"),
}

# 商品总览官方指数(第八看板 📊):中证商品指数(ccidx.com 官方源,futures_index_ccidx 日频,~4年)。
# 注:南华指数 akshare 端点(qhkch.com 源)已死(KeyError,源站停更)——官方总览用中证商品指数替代。
CCIDX_INDEXES = {"中证商品期货指数": "100001.CCI", "中证商品期货价格指数": "000001.CCI"}


def fetch_commodity_benchmarks(symbols: Optional[list[str]] = None,
                               retries: int = 2) -> pd.DataFrame:
    """国际基准日线批量(futures_foreign_hist,逐符号容错——同 fetch_commodity_price 式,失败符号跳过)。
    symbols 缺省 = COMMODITY_BENCHMARKS 全部符号(含 GC/SI/CL,与 western 腿同表幂等,保证看板主语新鲜度)。
    返回长表 [source='fut', symbol, date, open,high,low,close,volume](与 fetch_foreign_future 同构,
    可直接 upsert_western_macro)。全失败才 raise。"""
    symbols = symbols if symbols is not None else sorted({s for s, _, _ in COMMODITY_BENCHMARKS.values()})
    frames, last_err = [], None
    for sym in symbols:
        try:
            frames.append(fetch_foreign_future(sym, retries=retries))
            last_err = None
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    if not frames:
        raise FetchError(f"commodity_benchmarks {symbols} failed ({last_err})")
    return pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])


def fetch_commodity_index(name: str = "中证商品期货指数",
                          timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """中证商品指数日线(官方 ccidx.com,日频,~4 年;商品总览官方腿)。
    name 见 CCIDX_INDEXES。返回长表 [date, close, pct](close=收盘点位,pct=日涨跌幅%)。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.futures_index_ccidx, timeout, symbol=name)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            out = pd.DataFrame({
                "date": pd.to_datetime(df["日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "close": pd.to_numeric(df["收盘点位"], errors="coerce"),
                "pct": pd.to_numeric(df["涨跌幅"], errors="coerce"),
            }).dropna(subset=["date", "close"]).drop_duplicates("date")
            return out.sort_values("date")
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"commodity_index {name} failed ({last_err})")


def fetch_commodity_basis(start: str, end: str,
                          symbols: Optional[list[str]] = None,
                          retries: int = 1) -> pd.DataFrame:
    """基差+期限结构日表(100ppi 生意社,futures_spot_price_daily,2018 起;二期剩余·event-study 礼遇)。
    一次调用内部逐日请求,窗长=耗时(≈1.4min/年)——调用方按半年窗分段。
    返回长表 [symbol, date, spot/near_price/dom_price, near_month/dom_month(YYMM),
    dom_basis_rate, near_basis_rate](basis=期货−现货,rate=(期货−现货)/现货;正=升水)。
    非交易日该源会打 UserWarning,此处静默。"""
    import warnings

    symbols = symbols if symbols is not None else list(COMMODITY_CODES.values())
    last_err = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(2.0)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df = _run_with_timeout(ak.futures_spot_price_daily, 600.0,
                                       start_day=start, end_day=end, vars_list=symbols)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            out = pd.DataFrame({
                "symbol": df["symbol"].astype(str).str.upper(),
                "date": pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce").dt.strftime("%Y-%m-%d"),
                "spot_price": pd.to_numeric(df["spot_price"], errors="coerce"),
                "near_price": pd.to_numeric(df["near_contract_price"], errors="coerce"),
                "dom_price": pd.to_numeric(df["dominant_contract_price"], errors="coerce"),
                "near_month": pd.to_numeric(df["near_month"], errors="coerce"),
                "dom_month": pd.to_numeric(df["dominant_month"], errors="coerce"),
                "dom_basis": pd.to_numeric(df["dom_basis"], errors="coerce"),
                "dom_basis_rate": pd.to_numeric(df["dom_basis_rate"], errors="coerce"),
                "near_basis_rate": pd.to_numeric(df["near_basis_rate"], errors="coerce"),
            }).dropna(subset=["symbol", "date"]).drop_duplicates(["symbol", "date"])
            return out.sort_values(["symbol", "date"])
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"commodity_basis {start}~{end} failed ({last_err})")


# 库存(仓单)端点真相(2026-09-06 实测,二期剩余审计;2026-09-13 批次2.3 tushare fut_wsr 扩腿后修订):
#   futures_inventory_99(99qh)      死(JSONDecodeError,源站改版)
#   futures_inventory_em(东财)      活但仅 72 天(~3个月)——event-study 不够深,只配日度观察累积
#   futures_shfe_warehouse_receipt  死(JSONDecodeError)
#   futures_warehouse_receipt_dce   死(JSONDecodeError)——DCE 品种(铁矿I/焦煤JM/豆粕M/玉米C/生猪LH/
#                                   LPG PG)库存无源,fut_wsr 也不覆盖 DCE(2026-09-13 实测单日 55 品种无 DCE)
#   futures_gfex_warehouse_receipt  解析坏(KeyError 增减)——但 fut_wsr 覆盖 GFEX(碳酸锂 LC 2024 起)
#   futures_warehouse_receipt_czce  活(0.9s/次,dict{品种代码:逐仓库行})——CZCE 品种唯一可靠源
#   tushare fut_wsr                 活(2026-09-13 批次2.3):仓库粒度,按 trade_date 拉全市场分页
#                                   (单次~1200 行含截断,必须 query_paged);SHFE/INE/GFEX 干净
#                                   (vol=pre_vol+vol_chg 恒等成立;同名仓库双行=完税+保税两段,
#                                   如 CU 世天威外高桥,sum 全行=正确总量);
#                                   **CZCE 品种不可用**——混入升贴水/有效预报列错位行(FG 对账实证:
#                                   sum=1568=真仓单1268+升贴水300;UR 缺续表仓库 4395 vs 官方总计 7645)
CZCE_INVENTORY_SYMBOLS = ["FG", "SA", "UR"]   # PG(LPG)郑商所仓单接口无该键(2026-09-06 实测)——LPG 库存无源
# fut_wsr 扩腿品种(批次2.3,2026-09-13):SHFE/INE/GFEX,与 COMMODITY_CODES 的交集减 CZCE(DCE 全缺)。
# 单位随品种:CU/AL/ZN/RB=吨、AU/AG=千克、SC=桶、LC=手——分析为品种内自身分位/环比,无跨品种量纲比较
WSR_INVENTORY_SYMBOLS = ["CU", "AL", "ZN", "RB", "AU", "AG", "SC", "LC"]


def fetch_czce_receipts(date: str, timeout: float = 20.0) -> pd.DataFrame:
    """郑商所仓单日报(逐日调用)→ 按品种取「总计」行(官方口径)。返回 [variety, date, volume]。
    **口径修正(2026-09-13 批次2.3)**:旧版对全表 仓单数量 列求和,而 CZCE 报表含 仓库行+小计行+
    总计行(长表还分裂续行丢名)→ 恰好 3× 高估(FG 3804 vs 官方 1268 实证);分位/环比比例不变,
    event-study 结论不受影响,绝对值全量重灌修正。只返回 CZCE_INVENTORY_SYMBOLS 内品种;
    该日无数据/源失败 → 空表(调用方跳过,幂等重跑)。"""
    try:
        d = _run_with_timeout(ak.futures_warehouse_receipt_czce, timeout, date=date)
    except Exception:  # noqa: BLE001 — 单日失败静默(周采样靠重跑自愈)
        return pd.DataFrame(columns=["variety", "date", "volume"])
    rows = []
    d8 = str(date).replace("-", "")
    iso = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
    for sym, sub in (d or {}).items():
        if str(sym).upper() not in CZCE_INVENTORY_SYMBOLS or not isinstance(sub, pd.DataFrame):
            continue
        if "仓单数量" not in sub.columns or not len(sub):
            continue
        code_col = sub.columns[0]                       # 仓库编号(首列;小计/总计行在此)
        num = pd.to_numeric(sub["仓单数量"], errors="coerce")
        total_rows = sub[sub[code_col].astype(str).str.strip() == "总计"]
        if len(total_rows) and pd.notna(num[total_rows.index[0]]):
            vol = float(num[total_rows.index[0]])       # 官方总计行(首选——续行丢名也不受影响)
        else:                                           # 无总计行:仓库行求和(编号为数字者,剔除小计/总计/续行)
            keep = sub[code_col].astype(str).str.fullmatch(r"\d{3,4}", na=False)
            vol = float(num[keep].sum()) if keep.any() else float("nan")
        if pd.notna(vol) and vol > 0:
            rows.append({"variety": str(sym).upper(), "date": iso, "volume": float(vol)})
    return pd.DataFrame(rows, columns=["variety", "date", "volume"])


def fetch_inventory_wsr_tushare(date: str) -> pd.DataFrame:
    """交易所仓单日报(tushare `fut_wsr`,批次2.3)→ WSR_INVENTORY_SYMBOLS 品种按日聚合。
    单次调用 ~1200 行截断(含重复),必须 query_paged 分页;聚合=逐仓库 vol 求和——SHFE 同名
    仓库双行=完税+保税两段,sum 全行即注册仓单总量(CZCE 例外不在此腿,见端点真相注)。
    返回 [variety, date, volume];空/失败 → 空表(周采样调用方跳过,幂等重跑)。"""
    from . import tushare_client as tc
    d8 = str(date).replace("-", "")
    iso = f"{d8[:4]}-{d8[4:6]}-{d8[6:]}"
    try:
        df = tc.query_paged("fut_wsr", page_size=800, trade_date=d8)
    except Exception:  # noqa: BLE001 — 单日失败静默(周采样靠重跑自愈)
        return pd.DataFrame(columns=["variety", "date", "volume"])
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["variety", "date", "volume"])
    df = df.drop_duplicates()
    sub = df[df["symbol"].astype(str).str.upper().isin(WSR_INVENTORY_SYMBOLS)]
    if not len(sub):
        return pd.DataFrame(columns=["variety", "date", "volume"])
    agg = (pd.to_numeric(sub["vol"], errors="coerce").groupby(sub["symbol"]).sum())
    rows = [{"variety": str(sym).upper(), "date": iso, "volume": float(v)}
            for sym, v in agg.items() if pd.notna(v) and v > 0]
    return pd.DataFrame(rows, columns=["variety", "date", "volume"])


# ---- 批次2.4 展期口径(2026-09-13): 主力换月映射 + 逐合约收盘 → 展期调整连续 ----
def fetch_fut_mapping_tushare(code: str, start: str = "2020-01-01",
                              end: Optional[str] = None) -> pd.DataFrame:
    """主力换月映射(tushare `fut_mapping`,ts_code=<品种>.<所>)。返回 [ts_code, date, mapping_code]。
    event-study 前向收益的「精确展期」原料——主连拼接未复权在换月日的跳空由此修正。"""
    from . import tushare_client as tc
    suf = COMMODITY_TS_SUFFIX.get(code)
    if not suf:
        return pd.DataFrame(columns=["ts_code", "date", "mapping_code"])
    s = str(start).replace("-", "")
    e = str(end or today_str()).replace("-", "")
    df = tc.query("fut_mapping", ts_code=f"{code}.{suf}", start_date=s, end_date=e)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["ts_code", "date", "mapping_code"])
    out = pd.DataFrame({
        "ts_code": f"{code}.{suf}",
        "date": pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d",
                               errors="coerce").dt.strftime("%Y-%m-%d"),
        "mapping_code": df["mapping_ts_code"].astype(str),
    }).dropna(subset=["date"]).drop_duplicates(["ts_code", "date"])
    return out.sort_values("date")


def fetch_fut_contract_daily_tushare(ts_code: str) -> pd.DataFrame:
    """单合约日线收盘(tushare `fut_daily`,ts_code=实际合约如 RB2701.SHF;全生命 ≤250 行)。
    返回 [ts_code, date, close];失败 raise(调用方记日志跳过该合约,重跑自愈)。"""
    from . import tushare_client as tc
    df = tc.query("fut_daily", ts_code=ts_code, fields="ts_code,trade_date,close")
    if df is None or len(df) == 0:
        raise FetchError(f"fut_daily {ts_code} empty")
    out = pd.DataFrame({
        "ts_code": ts_code,
        "date": pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d",
                               errors="coerce").dt.strftime("%Y-%m-%d"),
        "close": pd.to_numeric(df["close"], errors="coerce"),
    }).dropna(subset=["date", "close"]).drop_duplicates(["ts_code", "date"])
    if not len(out):
        raise FetchError(f"fut_daily {ts_code} rows empty")
    return out.sort_values("date")


def fetch_market_pb(timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """Whole-A-market PB history + percentiles (legulegu stock_a_all_pb). Single market-wide
    series, back to 2005. Returns DataFrame indexed by date(str): pb, pb_median, pct_all
    (quantile in all history), pct_10y (quantile in recent 10y). Any unavailable col is omitted."""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.stock_a_all_pb, timeout)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            cols = list(df.columns)
            date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])

            def _pick(keywords):
                for c in cols:
                    if any(k.lower() in str(c).lower() for k in keywords):
                        return c
                return None

            pb_col = _pick(["middlepb", "市净率"]) or _pick(["pb"])
            med_col = _pick(["equalweight", "等权"]) or _pick(["中位"])
            pct_all = _pick(["quantileinallhistory", "全部历史", "allhistory"])
            pct_10y = _pick(["quantileinrecent10years", "近十年", "recent10"])
            if pb_col is None:
                raise FetchError(f"market_pb: no PB col in {cols}")
            out = pd.DataFrame({
                "date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d"),
                "pb": pd.to_numeric(df[pb_col], errors="coerce"),
            })
            if med_col is not None:
                out["pb_median"] = pd.to_numeric(df[med_col], errors="coerce")
            if pct_all is not None:
                out["pct_all"] = pd.to_numeric(df[pct_all], errors="coerce")
            if pct_10y is not None:
                out["pct_10y"] = pd.to_numeric(df[pct_10y], errors="coerce")
            return out.dropna(subset=["date", "pb"]).drop_duplicates("date").set_index("date").sort_index()
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"market_pb failed ({last_err})")


def fetch_etf_dividend(symbol: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """ETF 分红历史(sina fund_etf_dividend_sina)。返回 DataFrame indexed by date:
    cumulative_dividend(自成立累计分红,单位元)。覆盖稀疏——部分 ETF 返回空属正常,返回空 df
    (不报错)。价值型股息率 = 近 12 月单次分红之和(累计差分) ÷ 当前价格。"""
    pre = _szsh_prefix(symbol)
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.0)
        try:
            df = _run_with_timeout(ak.fund_etf_dividend_sina, timeout, symbol=f"{pre}{symbol}")
            if df is None or len(df) == 0:
                return pd.DataFrame(columns=["cumulative_dividend"])
            cols = list(df.columns)
            date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])
            div_col = (next((c for c in cols if "累计" in str(c) and "分红" in str(c)), None)
                       or next((c for c in cols if "分红" in str(c)), None))
            if div_col is None:
                return pd.DataFrame(columns=["cumulative_dividend"])
            out = pd.DataFrame({
                "date": pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d"),
                "cumulative_dividend": pd.to_numeric(df[div_col], errors="coerce"),
            })
            return out.dropna(subset=["date"]).drop_duplicates("date").set_index("date").sort_index()
        except Exception:  # noqa: BLE001
            pass
    return pd.DataFrame(columns=["cumulative_dividend"])


# ---- Stock daily / valuation (V5 tracker · Phase 2 个股层数据栈) ----
# 个股日线 sina stock_zh_a_daily(prefix 由 _szsh_prefix 算);估值金矿 stock_zh_valuation_baidu
# (PE/PB/总市值免财报硬算)。复用 ETF/指数的 timeout/normalize 基础设施,纯抓取层不入库(store/manager
# 在下一步建 stock_valuation 表 + 增量游标时再接)。
def fetch_stock_daily(
    symbol: str,
    adjust: str = "hfq",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    retries: int = 2,
    timeout: float = 40.0,
) -> tuple[pd.DataFrame, str]:
    """个股日线 OHLCV (sina stock_zh_a_daily)。Returns (DataFrame indexed by date(str), source_tag)。

    DataFrame 列与 ETF 路径一致(经 _normalize): open/high/low/close/volume/amount。sina 原始还含
    outstanding_share/turnover,被 _normalize 裁掉——需流通股本/换手的研究另取原始帧。
    adjust: '' / 'raw' → 不复权(sina 原始价,拆分需 fix_splits);'qfq'/'hfq' → 前/后复权(趋势连续,
    个股研究推荐 hfq)。prefix 由 _szsh_prefix 算(6xx/68x/9xx→sh,0xx/3xx→sz);北交所(8/4开头) sina
    不支持,会抛 FetchError。
    """
    pre = _szsh_prefix(symbol)
    if pre == "bj":
        raise FetchError(f"{symbol}: 北交所个股 sina stock_zh_a_daily 不支持(前缀 bj)")
    adj = "" if adjust in ("", "raw", None) else adjust
    # sina stock_zh_a_daily 对空串 start/end 会内部 DatetimeIndex 切片报错,故仅在显式给值时传入
    # (省略 = 拉全历史,与指数 fetch_index_daily 同口径)。
    kwargs: dict = {"symbol": f"{pre}{symbol}", "adjust": adj}
    s = (start_date or "").replace("-", "")
    e = (end_date or "").replace("-", "")
    if s:
        kwargs["start_date"] = s
    if e:
        kwargs["end_date"] = e
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.stock_zh_a_daily, timeout, **kwargs)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            return _normalize(df), f"sina_stock_{adj or 'raw'}"
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"{symbol}: stock_daily failed ({last_err})")


# stock_zh_valuation_baidu 实测可用指标(2026-07-21 探针:茅台/招行)。
# 市销率(TTM) 返回 None(不可靠)→ 故意不入集;无股息率(另从 stock_history_dividend derive)。
STOCK_VALUATION_INDICATORS = {
    "pe_ttm": "市盈率(TTM)",
    "pe_static": "市盈率(静)",
    "pb": "市净率",
    "pcf": "市现率",
    "market_cap": "总市值",
}


def fetch_stock_valuation(
    symbol: str,
    indicator: str,
    period: str = "全部",
    timeout: float = 40.0,
    retries: int = 2,
) -> pd.DataFrame:
    """个股估值历史(百度 stock_zh_valuation_baidu)。indicator 取 STOCK_VALUATION_INDICATORS 的键
    (pe_ttm/pe_static/pb/pcf/market_cap)。返回 DataFrame indexed by date(str): value。

    实测字段固定 [date, value];period='全部' 从 IPO 起(茅台 2001-08-31 起)到最新,**稀疏**(半月级,
    非日频,茅台 25 年仅 ~607 点)——足够算历史分位,但不要当日频价序列用。无股息率。
    """
    cn = STOCK_VALUATION_INDICATORS.get(indicator)
    if cn is None:
        raise FetchError(
            f"unknown valuation indicator: {indicator} "
            f"(supported: {sorted(STOCK_VALUATION_INDICATORS)})"
        )
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.0)
        try:
            df = _run_with_timeout(
                ak.stock_zh_valuation_baidu, timeout,
                symbol=symbol, indicator=cn, period=period,
            )
            if df is None or len(df) == 0 or "value" not in df.columns or "date" not in df.columns:
                raise FetchError("empty")
            out = pd.DataFrame({
                "date": pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "value": pd.to_numeric(df["value"], errors="coerce"),
            })
            return (out.dropna(subset=["date", "value"])
                       .drop_duplicates("date").set_index("date").sort_index())
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001 — baidu 对不支持的指标抛 'NoneType not subscriptable'
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"{symbol} {indicator}: valuation failed ({last_err})")


# ---- 个股估值 tushare 主源 (2026-09-13 迁移 1.1 · ADR-0002) ----
# baidu 稀疏半月级/无股息率/PCF 抛 NoneType → daily_basic 日频全史替代。
# 口径: total_mv/circ_mv tushare=万元 → ×1e-4 转亿元,对齐 baidu 历史存量单位
# (2026-09-13 对账实测: 茅台 15940.54/中石油 20571.56 = 亿);pcf 无消费方(grep 验证)不迁;
# 白送 ps_ttm/dv_ratio(股息率)/turnover_rate/circ_mv;北交所(4/8 开头)估值首次可得。
TS_VALUATION_MAP = {
    "pe_ttm": ("pe_ttm", 1.0),
    "pe_static": ("pe", 1.0),
    "pb": ("pb", 1.0),
    "ps_ttm": ("ps_ttm", 1.0),
    "dv_ratio": ("dv_ratio", 1.0),
    "turnover_rate": ("turnover_rate", 1.0),
    "market_cap": ("total_mv", 1e-4),
    "circ_mv": ("circ_mv", 1e-4),
}


def fetch_valuation_tushare(code: str) -> dict[str, pd.DataFrame]:
    """个股估值全史(tushare daily_basic 按 ts_code 单次调用)→ {indicator: DataFrame[value]}
    indexed by date(str)。单股 ≤6000 行覆盖 IPO 起全史、日频;6xx/9xx→.SH,0xx/3xx→.SZ,
    4xx/8xx→.BJ。失败抛 TushareError/FetchError 由调用方(manager)降级 baidu。"""
    from . import tushare_client as tc
    ts_code = (f"{code}.SH" if code.startswith(("6", "9"))
               else f"{code}.BJ" if code.startswith(("4", "8"))
               else f"{code}.SZ")
    fields = "trade_date," + ",".join(src for src, _ in TS_VALUATION_MAP.values())
    df = tc.query("daily_basic", ts_code=ts_code, fields=fields)
    if df is None or len(df) == 0:
        raise FetchError(f"{code}: daily_basic empty")
    df = df.sort_values("trade_date")
    dates = pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    out: dict[str, pd.DataFrame] = {}
    for ind, (src, mult) in TS_VALUATION_MAP.items():
        s = pd.to_numeric(df[src], errors="coerce") * mult
        frame = pd.DataFrame({"value": s.to_numpy()}, index=dates.to_numpy())
        frame = frame.dropna()
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        if len(frame):
            out[ind] = frame
    if not out:
        raise FetchError(f"{code}: daily_basic 全指标 NaN")
    return out


# stock_financial_abstract(常用指标) 的 17 项→EN 键映射。实测(2026-07-22)指标名跨股稳定
# (茅台/招行完全一致)。营收/利润为原始元单位(显示时 ÷1e8 转亿);ROE/毛利率等为百分数原值。
STOCK_FINANCIAL_METRICS = {
    "revenue":          "营业总收入",
    "operating_cost":   "营业成本",
    "net_profit":       "归母净利润",
    "net_profit_total": "净利润",
    "np_deducted":      "扣非净利润",
    "equity_total":     "股东权益合计(净资产)",
    "goodwill":         "商誉",
    "ocf":              "经营现金流量净额",
    "eps":              "基本每股收益",
    "bvps":             "每股净资产",
    "cps":              "每股现金流",
    "roe":              "净资产收益率(ROE)",
    "roa":              "总资产报酬率(ROA)",
    "gross_margin":     "毛利率",
    "net_margin":       "销售净利率",
    "expense_ratio":    "期间费用率",
    "debt_ratio":       "资产负债率",
}


def fetch_stock_financials(symbol: str, timeout: float = 40.0,
                           retries: int = 2) -> pd.DataFrame:
    """个股财务摘要(sina stock_financial_abstract 常用指标 17 项)。返回**长表** DataFrame
    [report_period, metric, value](report_period=YYYYMMDD, metric=EN 键,见 STOCK_FINANCIAL_METRICS)。
    全历史(~102 期 ≈ 25 年,每次全量返回)。C1 增速/利润波动/利润归因/戴维斯/避坑都吃它。"""
    cn_to_en = {v: k for k, v in STOCK_FINANCIAL_METRICS.items()}
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.0)
        try:
            fa = _run_with_timeout(ak.stock_financial_abstract, timeout, symbol=symbol)
            if fa is None or len(fa) == 0 or "选项" not in fa.columns or "指标" not in fa.columns:
                raise FetchError("empty")
            fa = fa[(fa["选项"] == "常用指标") & (fa["指标"].isin(cn_to_en))]
            if len(fa) == 0:
                raise FetchError("no 常用指标 rows")
            period_cols = [c for c in fa.columns if c not in ("选项", "指标")]
            rows = []
            for _, r in fa.iterrows():
                en = cn_to_en[r["指标"]]
                for p in period_cols:
                    v = r[p]
                    if v is not None and not (isinstance(v, float) and pd.isna(v)):
                        rows.append({"report_period": str(p), "metric": en,
                                     "value": pd.to_numeric(v, errors="coerce")})
            out = pd.DataFrame(rows, columns=["report_period", "metric", "value"])
            return out.dropna(subset=["value"])
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"{symbol}: stock_financials failed ({last_err})")


def fetch_stock_financials_tushare(symbol: str) -> pd.DataFrame:
    """sina 17 项财报摘要的 tushare 应急重建(income∪fina_indicator 逐股全史;1.13 降级件·2026-09-13)。
    覆盖 **14/17 项**:equity_total/goodwill 需 balancesheet、ocf 需 cashflow——应急件不扩接口,
    缺项诚实留空(长表按 metric 键消费,少键=少指标不炸;sina 恢复后全量 upsert 覆盖自愈)。
    expense_ratio 自算 =(销售+管理+财务费用)/营业总收入×100。同形长表 [report_period, metric, value]。
    双接口 0 行 → raise(调用方保持 sina 失败语义,下次重试)。"""
    from . import tushare_client as tc
    code = str(symbol).split(".")[0].zfill(6)
    ts_code = (f"{code}.SH" if code.startswith(("6", "9"))
               else f"{code}.BJ" if code.startswith(("4", "8"))
               else f"{code}.SZ")
    inc = tc.query("income", ts_code=ts_code,
                   fields="ann_date,end_date,total_revenue,oper_cost,sell_exp,admin_exp,fin_exp,"
                          "n_income,n_income_attr_p")
    fi = tc.query("fina_indicator", ts_code=ts_code,
                  fields="ann_date,end_date,eps,profit_dedt,roe,roa,grossprofit_margin,"
                         "netprofit_margin,debt_to_assets,bps,ocfps")
    if (inc is None or not len(inc)) and (fi is None or not len(fi)):
        raise FetchError(f"{ts_code}: income∪fina_indicator 全空")
    rows: list[dict] = []

    def _emit(frame: pd.DataFrame, mapping: dict) -> None:
        if frame is None or not len(frame):
            return
        f = frame.sort_values("ann_date").drop_duplicates("end_date", keep="last")
        per = f["end_date"].astype(str)
        for key, src in mapping.items():
            if src not in f.columns:
                continue
            s = pd.to_numeric(f[src], errors="coerce")
            for p, v in zip(per, s):
                if v is not None and v == v:
                    rows.append({"report_period": p, "metric": key, "value": float(v)})

    _emit(inc, {"revenue": "total_revenue", "operating_cost": "oper_cost",
                "net_profit": "n_income_attr_p", "net_profit_total": "n_income"})
    _emit(fi, {"np_deducted": "profit_dedt", "eps": "eps", "bvps": "bps", "cps": "ocfps",
               "roe": "roe", "roa": "roa", "gross_margin": "grossprofit_margin",
               "net_margin": "netprofit_margin", "debt_ratio": "debt_to_assets"})
    if inc is not None and len(inc) and {"sell_exp", "admin_exp", "fin_exp", "total_revenue"} <= set(inc.columns):
        f = inc.sort_values("ann_date").drop_duplicates("end_date", keep="last")
        exp = ((pd.to_numeric(f["sell_exp"], errors="coerce").fillna(0)
                + pd.to_numeric(f["admin_exp"], errors="coerce").fillna(0)
                + pd.to_numeric(f["fin_exp"], errors="coerce").fillna(0))
               / pd.to_numeric(f["total_revenue"], errors="coerce").abs().where(
                   pd.to_numeric(f["total_revenue"], errors="coerce").abs() > 1e-9) * 100.0)
        for p, v in zip(f["end_date"].astype(str), exp):
            if v is not None and v == v:
                rows.append({"report_period": p, "metric": "expense_ratio", "value": float(v)})
    out = pd.DataFrame(rows, columns=["report_period", "metric", "value"])
    if not len(out):
        raise FetchError(f"{ts_code}: financials(tushare) rows empty")
    return out


def fetch_stock_dividend(symbol: str, timeout: float = 40.0,
                         retries: int = 2) -> pd.DataFrame:
    """个股分红明细(sina stock_history_dividend_detail, indicator='分红')。返回 DataFrame indexed
    by ex_date(除权除息日=影响第一天): cash_per_share(派息/10, 每股现金元)/stock_div_10(送股per10)/
    trans_10(转增per10)/announce_date。**只存 进度=实施** 的(预告/预案不进)。C1 股息率 + 利润归因分红贡献吃它。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.0)
        try:
            dv = _run_with_timeout(ak.stock_history_dividend_detail, timeout,
                                   symbol=symbol, indicator="分红")
            if dv is None or len(dv) == 0:
                return pd.DataFrame(columns=["announce_date", "cash_per_share",
                                             "stock_div_10", "trans_10"])
            dv = dv[dv["进度"].astype(str).str.strip() == "实施"].copy()
            if len(dv) == 0:
                return pd.DataFrame(columns=["announce_date", "cash_per_share",
                                             "stock_div_10", "trans_10"])
            out = pd.DataFrame({
                "ex_date": pd.to_datetime(dv["除权除息日"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "announce_date": pd.to_datetime(dv["公告日期"], errors="coerce").dt.strftime("%Y-%m-%d"),
                "cash_per_share": pd.to_numeric(dv["派息"], errors="coerce") / 10.0,
                "stock_div_10": pd.to_numeric(dv["送股"], errors="coerce"),
                "trans_10": pd.to_numeric(dv["转增"], errors="coerce"),
            })
            out = out.dropna(subset=["ex_date"]).drop_duplicates("ex_date").set_index("ex_date").sort_index()
            return out
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"{symbol}: stock_dividend failed ({last_err})")


def fetch_stock_dividend_tushare(symbol: str) -> pd.DataFrame:
    """个股分红明细(tushare `dividend` 按 ts_code,只存 实施,2026-09-13 迁移1.9 主源)。
    口径映射(2026-09-13 对账实证): cash_div_tax=**每股**税前派息(茅台 28.02423 与 sina
    每股 28.0242 精确相等)→直接用;stk_div=每股送转(**送+转增合一**,消费方 pool/prices.py
    前复权按 和 用、展示层不拆)→×10 存 stock_div_10,trans_10 恒 0(sina 历史存量的
    送/转 拆分仅旧源行保留)。同形 ex_date-indexed 输出;无分红返回空表(正常);
    接口失败抛错由 manager 降级 sina。"""
    from . import tushare_client as tc
    ts_code = (f"{symbol}.SH" if symbol.startswith(("6", "9"))
               else f"{symbol}.BJ" if symbol.startswith(("4", "8"))
               else f"{symbol}.SZ")
    df = tc.query("dividend", ts_code=ts_code,
                  fields="ts_code,end_date,ann_date,div_proc,cash_div_tax,stk_div,ex_date")
    cols = ["announce_date", "cash_per_share", "stock_div_10", "trans_10"]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=cols)
    dv = df[df["div_proc"].astype(str).str.strip() == "实施"].copy()
    if len(dv) == 0:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "ex_date": pd.to_datetime(dv["ex_date"].astype(str), format="%Y%m%d",
                                  errors="coerce").dt.strftime("%Y-%m-%d"),
        "announce_date": pd.to_datetime(dv["ann_date"].astype(str), format="%Y%m%d",
                                        errors="coerce").dt.strftime("%Y-%m-%d"),
        "cash_per_share": pd.to_numeric(dv["cash_div_tax"], errors="coerce"),
        "stock_div_10": pd.to_numeric(dv["stk_div"], errors="coerce") * 10.0,
        "trans_10": 0.0,
    })
    return (out.dropna(subset=["ex_date"])
               .drop_duplicates("ex_date").set_index("ex_date").sort_index())


def fetch_stock_forecast_panel(report_period: str, timeout: float = 60.0,
                                retries: int = 2) -> pd.DataFrame:
    """全市场业绩预告(stock_yjyg_em)某报告期(YYYYMMDD)。返回 DataFrame[code, yoy, type, announce_date]:
      yoy = 业绩变动幅度(归母净利同比 %), type = 预告类型, announce_date = 公告日期。
    过滤 归属于上市公司股东的净利润,按 code 去重保留最新公告。市场级单调用(~3-13s/期,分页),
    调用方按 watchlist 过滤。稀疏——仅显著变动才发预告(稳定大市值股常无)。预告/快报/正式报 链的最早一环。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5)
        try:
            df = _run_with_timeout(ak.stock_yjyg_em, timeout, date=report_period)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df[df["预测指标"].astype(str).str.strip() == "归属于上市公司股东的净利润"].copy()
            if len(df) == 0:
                raise FetchError("no 归母净利润 rows")
            df["code"] = df["股票代码"].astype(str)
            df["yoy"] = pd.to_numeric(df["业绩变动幅度"], errors="coerce")
            df["type"] = df["预告类型"].astype(str).str.strip()
            df["announce_date"] = pd.to_datetime(df["公告日期"], errors="coerce").dt.strftime("%Y-%m-%d")
            df = df.sort_values("announce_date", na_position="last").drop_duplicates("code", keep="last")
            return df[["code", "yoy", "type", "announce_date"]].reset_index(drop=True)
        except FetchError as ex:
            last_err = ex
        except Exception as ex:  # noqa: BLE001
            last_err = FetchError(str(ex)[:200])
    raise FetchError(f"forecast_panel {report_period} failed ({last_err})")


# ==================== Western-macro series (V6 tracker · 西方宏观预测台账 只读旁路) ====================
# ADR-0001 围栏:这些西方宏观数据只供"预测台账"只读诊断,永不喂 A 股轮动引擎。
# 全部 AkShare:UST(bond_zh_us_rate,一次覆盖 2/5/10/30y + 2s10s)/美股指数(index_us_stock_sina,sina)/
# 外盘期货(futures_foreign_hist,investing)/外汇(forex_hist_em,eastmoney push2his)。DXY 无干净 AkShare
# 源 → 6 成分腿按 ICE 公式重算(reconstruct_dxy)。外汇单源(push2his)在部分环境被拦——见 CLAUDE.md。

# bond_zh_us_rate 的美国国债列名 → 标准符号(实测 2026-08 稳定)
US_TREASURY_TENORS = {
    "US2Y": "美国国债收益率2年",
    "US5Y": "美国国债收益率5年",
    "US10Y": "美国国债收益率10年",
    "US30Y": "美国国债收益率30年",
    "US2S10S": "美国国债收益率10年-2年",
}


def fetch_us_treasury(timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """美债收益率(bond_zh_us_rate,一次调用覆盖 2/5/10/30y + 10y-2y 利差,2000 起日频)。
    返回长表 [source='ust', symbol, date, close=收益率%]。中国国债列忽略。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.bond_zh_us_rate, timeout, start_date="20000101")
            if df is None or len(df) == 0:
                raise FetchError("empty")
            cols = list(df.columns)
            date_col = next((c for c in cols if "日期" in str(c) or str(c).lower() == "date"), cols[0])
            dts = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
            frames = []
            for sym, cn in US_TREASURY_TENORS.items():
                if cn not in df.columns:
                    continue
                sub = pd.DataFrame({"source": "ust", "symbol": sym, "date": dts,
                                    "close": pd.to_numeric(df[cn], errors="coerce")})
                frames.append(sub.dropna(subset=["date", "close"]).drop_duplicates(["symbol", "date"]))
            if not frames:
                raise FetchError(f"no US tenor cols in {cols}")
            return pd.concat(frames, ignore_index=True).sort_values(["symbol", "date"])
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"us_treasury failed ({last_err})")


def fetch_us_index(symbol: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """美股宽基指数日线(index_us_stock_sina,sina,2004 起 ~22 年)。symbol='.INX'/'.IXIC'/'.DJI'。
    返回长表 [source='usidx', symbol, date, open/high/low/close/volume]。RAW(指数无需复权)。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.index_us_stock_sina, timeout, symbol=symbol)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            df = _normalize(df)
            out = df.reset_index()
            out.insert(0, "source", "usidx")
            out.insert(1, "symbol", symbol)
            keep = [c for c in ("source", "symbol", "date", "open", "high", "low", "close", "volume")
                    if c in out.columns]
            return out[keep]
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"us_index {symbol} failed ({last_err})")


# futures_foreign_hist 白名单内、西方宏观台账用到的外盘期货符号
FOREIGN_FUTURE_SYMBOLS = {"GC": "COMEX黄金", "SI": "COMEX白银", "CL": "WTI原油", "OIL": "Brent原油"}
# 注:外盘铜 futures_foreign_hist 数据失真——LHC 非有效符号(返回 ~82 垃圾值)、HG(COMEX铜)~658,
# 且 volume/position 全 0,不可用。铜/有色改用沪铜主连(CU0, commodity_price 表, 元/吨),
# 与项目"铜价"一致。


def fetch_foreign_future(symbol: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """外盘期货日线(futures_foreign_hist,investing 源,~10 年;CL/WTI 30 年)。symbol='GC'/'SI'/'CL'/
    'OIL'/'LHC'/'HG'。返回长表 [source='fut', symbol, date, open/high/low/close/volume]。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.futures_foreign_hist, timeout, symbol=symbol)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            df = _normalize(df)
            out = df.reset_index()
            out.insert(0, "source", "fut")
            out.insert(1, "symbol", symbol)
            keep = [c for c in ("source", "symbol", "date", "open", "high", "low", "close", "volume")
                    if c in out.columns]
            return out[keep]
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"foreign_future {symbol} failed ({last_err})")


DXY_FOREX_LEGS = ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF"]


def fetch_forex_pair(symbol: str, timeout: float = 40.0, retries: int = 2) -> pd.DataFrame:
    """外汇对日线(forex_hist_em,eastmoney push2his)。symbol='EURUSD'/'USDJPY'/'GBPUSD'/'USDCAD'/
    'USDSEK'/'USDCHF'。返回长表 [source='forex', symbol, date, open/high/low/close]。注意 push2his 在
    部分环境被拦(见 CLAUDE.md 主力资金端点);部署环境通常可用。"""
    last_err = None
    for attempt in range(retries):
        if attempt > 0:
            time.sleep(1.5 * attempt)
        try:
            df = _run_with_timeout(ak.forex_hist_em, timeout, symbol=symbol)
            if df is None or len(df) == 0:
                raise FetchError("empty")
            df = df.rename(columns={c: str(c).lower() for c in df.columns})
            df = _normalize(df)
            out = df.reset_index()
            out.insert(0, "source", "forex")
            out.insert(1, "symbol", symbol)
            keep = [c for c in ("source", "symbol", "date", "open", "high", "low", "close", "volume")
                    if c in out.columns]
            return out[keep]
        except FetchError as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = FetchError(str(e)[:200])
    raise FetchError(f"forex {symbol} failed ({last_err})")


def fetch_forex_pairs_ecb(pairs: list[str] = None, start: str = "19990101",
                          timeout: float = 40.0) -> pd.DataFrame:
    """6 DXY 成分外汇腿 · ECB 参考汇率(Frankfurter API · 免费·无 key·1999 起日频)。

    AkShare forex_hist_em(push2his 在部分网络被拦)的 fallback。一次调用取 EUR→6 币,换算成
    USD-base 对(ICE DXY 公式所需口径):
      EURUSD = USD/EUR;  USDJPY = EURJPY/EURUSD;  GBPUSD = EURUSD/EURGBP;
      USDCAD = EURCAD/EURUSD;  USDSEK = EURSEK/EURUSD;  USDCHF = EURCHF/EURUSD。
    返回长表 [source='forex', symbol, date, close]。
    """
    import json as _json
    import urllib.request as _urlreq
    pairs = pairs or DXY_FOREX_LEGS
    s = f"{start[:4]}-{start[4:6]}-{start[6:8]}" if len(start) >= 8 else "1999-01-04"
    end = datetime.now().strftime("%Y-%m-%d")
    url = (f"https://api.frankfurter.app/{s}..{end}"
           "?from=EUR&to=USD,JPY,GBP,CAD,SEK,CHF")

    def _do():
        req = _urlreq.Request(url, headers={"User-Agent": "stockagent"})
        with _urlreq.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read())

    j = _run_with_timeout(_do, timeout)
    rates = (j or {}).get("rates") or {}
    rows = []
    for d in sorted(rates):
        x = rates[d] or {}
        try:
            eu = {k: float(x[k]) for k in ("USD", "JPY", "GBP", "CAD", "SEK", "CHF") if k in x}
        except (KeyError, ValueError, TypeError):
            continue
        usd = eu.get("USD")
        if not usd or usd <= 0:
            continue
        conv = {
            "EURUSD": usd,
            "USDJPY": eu["JPY"] / usd if "JPY" in eu else None,
            "GBPUSD": usd / eu["GBP"] if "GBP" in eu else None,
            "USDCAD": eu["CAD"] / usd if "CAD" in eu else None,
            "USDSEK": eu["SEK"] / usd if "SEK" in eu else None,
            "USDCHF": eu["CHF"] / usd if "CHF" in eu else None,
        }
        for p in pairs:
            v = conv.get(p)
            if v and v > 0:
                rows.append({"source": "forex", "symbol": p, "date": d, "close": v})
    if not rows:
        raise FetchError("forex_ecb empty")
    return pd.DataFrame(rows).drop_duplicates(["symbol", "date"]).sort_values(["symbol", "date"])


# ICE 美元指数几何加权。USD 为基础货币的对(USDJPY/USDCAD/USDSEK/USDCHF)正指数;USD 为报价货币的
# 对(EURUSD/GBPUSD)负指数。常数 50.14348112 使基期(1973)=100。实测 2024 末式汇率→DXY≈103(对)。
_DXY_WEIGHTS = {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119,
                "USDCAD": 0.091, "USDSEK": 0.042, "USDCHF": 0.036}
_DXY_CONST = 50.14348112


def reconstruct_dxy(rates: dict) -> float:
    """单日 ICE DXY 重算。rates={pair: price},6 腿齐全才准(缺腿报 FetchError)。
    参考快照 {EURUSD:1.10, USDJPY:150, GBPUSD:1.27, USDCAD:1.36, USDSEK:10.5, USDCHF:0.88} → ≈103.0。"""
    missing = [k for k in _DXY_WEIGHTS if k not in rates or rates[k] is None]
    if missing:
        raise FetchError(f"reconstruct_dxy missing legs: {missing}")
    prod = _DXY_CONST
    for pair, w in _DXY_WEIGHTS.items():
        prod *= float(rates[pair]) ** w
    return prod


def reconstruct_dxy_series(leg_closes: dict) -> pd.Series:
    """逐日重算 DXY 水平时序。leg_closes={pair: pd.Series(close, index=date)}。6 腿 inner-join 对齐
    (某腿整条缺失或某日缺 → 该日跳过)。返回 pd.Series(DXY level, index=date, 升序)。"""
    aligned = None
    for pair, s in leg_closes.items():
        if pair not in _DXY_WEIGHTS or s is None or len(s) == 0:
            continue
        col = s.astype(float).rename(pair)
        aligned = col.to_frame() if aligned is None else aligned.join(col, how="inner")
    if aligned is None or len(aligned) == 0:
        return pd.Series(dtype=float)
    if len([p for p in _DXY_WEIGHTS if p in aligned.columns]) < len(_DXY_WEIGHTS):
        return pd.Series(dtype=float)  # 缺整条腿 → 无法重算
    aligned = aligned.dropna(subset=list(_DXY_WEIGHTS))
    if len(aligned) == 0:
        return pd.Series(dtype=float)
    prod = pd.Series(_DXY_CONST, index=aligned.index, dtype=float)
    for pair, w in _DXY_WEIGHTS.items():
        prod = prod * (aligned[pair].astype(float) ** w)
    return prod.sort_index()


# ---- 黄金微观紧缺数据 (COMEX 库存 / CFTC 投机+商业持仓 / 央行购金) · 只读旁路 ADR-0001 ----
# JZ 框架 L2/L3/L4 证据: 库存↓=紧缺, CFTC投机净多单极值=泡沫/商业净空单极小=逼空, 央行净购金=底的锚。
# 央行口径=实物万盎司存量(月度差分=净购金), 不用美元价值(被金价驱动会误导)。
# 数据缺口(无免费源): GOFO/租赁利率(LBMA 2015 停发)、全球 ETF(GLD/IAU)流。
# FRED(免费无key, 同 ECB 外汇先例, 用户授权非-akshare 源): DFII10 实际利率 / T10YIE 通胀预期。
_COMEX_SYM = {"黄金": "GC", "白银": "SI"}
_CFTC_SYM = {"黄金": "GC", "白银": "SI"}
_CB_DATE_RE = re.compile(r"(\d{4})[\./](\d{1,2})")
_FRED_SERIES = {"DFII10": "10年期实际利率", "T10YIE": "10年期通胀预期"}


def _retry_ak(fn, retries: int = 2, delay: float = 1.0, **kw):
    """简单重试包装 ak 调用(ak 易被拦/RemoteDisconnected)。"""
    last = None
    for _ in range(retries + 1):
        try:
            return fn(**kw)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay)
    raise FetchError(f"ak 调用重试耗尽: {last}")


def _norm_date(v) -> Optional[str]:
    """各种日期形态 → 'YYYY-MM-DD'。"""
    try:
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def parse_comex_inventory(df, cn_symbol: str = "黄金") -> list[dict]:
    """ak.futures_comex_inventory 的 df → [{symbol,date,tonnes,ounces}]。"""
    sym = _COMEX_SYM.get(cn_symbol, cn_symbol)
    if df is None or len(df) == 0:
        return []
    col_t, col_o = f"COMEX{cn_symbol}库存量-吨", f"COMEX{cn_symbol}库存量-盎司"
    out = []
    for _, r in df.iterrows():
        d = _norm_date(r.get("日期"))
        if not d:
            continue
        out.append({"symbol": sym, "date": d, "tonnes": r.get(col_t), "ounces": r.get(col_o)})
    return out


def fetch_comex_inventory(cn_symbol: str = "黄金", retries: int = 2) -> list[dict]:
    df = _retry_ak(ak.futures_comex_inventory, retries=retries, symbol=cn_symbol)
    return parse_comex_inventory(df, cn_symbol)


def parse_cftc_speculative(df, cn_assets: tuple = ("黄金", "白银")) -> list[dict]:
    """ak.macro_usa_cftc_c_holding (CFTC 商品类非商业/投机持仓, 宽表) → [{symbol,date,long_pos,short_pos,net_pos}]。
    非商业=投机/large speculator; 净多单极值=泡沫预警(JZ)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for cn, sym in _CFTC_SYM.items():
        if cn not in cn_assets:
            continue
        col_l, col_s, col_n = f"{cn}-多头仓位", f"{cn}-空头仓位", f"{cn}-净仓位"
        if col_l not in df.columns or col_n not in df.columns:
            continue
        for _, r in df.iterrows():
            d = _norm_date(r.get("日期"))
            if not d:
                continue
            out.append({"symbol": sym, "date": d,
                        "long_pos": r.get(col_l), "short_pos": r.get(col_s), "net_pos": r.get(col_n)})
    return out


def fetch_cftc_speculative(retries: int = 2) -> list[dict]:
    df = _retry_ak(ak.macro_usa_cftc_c_holding, retries=retries)
    return parse_cftc_speculative(df)


def parse_cftc_commercial(df, cn_assets: tuple = ("黄金", "白银")) -> list[dict]:
    """ak.macro_usa_cftc_merchant_goods_holding (CFTC 商品类商业/merchant 持仓, 宽表) → [{symbol='GC_M',...}]。
    商业=矿商/银行套保, 通常净空; 净空单极小=逼空前兆(JZ)。symbol 加 _M 后缀与投机(GC)区分。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for cn, sym in _CFTC_SYM.items():
        if cn not in cn_assets:
            continue
        col_l, col_s, col_n = f"{cn}-多头仓位", f"{cn}-空头仓位", f"{cn}-净仓位"
        if col_l not in df.columns or col_n not in df.columns:
            continue
        for _, r in df.iterrows():
            d = _norm_date(r.get("日期"))
            if not d:
                continue
            out.append({"symbol": sym + "_M", "date": d,
                        "long_pos": r.get(col_l), "short_pos": r.get(col_s), "net_pos": r.get(col_n)})
    return out


def fetch_cftc_commercial(retries: int = 2) -> list[dict]:
    df = _retry_ak(ak.macro_usa_cftc_merchant_goods_holding, retries=retries)
    return parse_cftc_commercial(df)


def parse_cb_gold(df, country: str = "CN") -> list[dict]:
    """ak.macro_china_foreign_exchange_gold (月频, 'YYYY.M', 黄金储备=**实物万盎司存量**) → [{country,date,value}]。
    用实物口径(非美元价值); 月度差分=真实净购金量(看板算)。早期缺值跳过。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        m = _CB_DATE_RE.match(str(r.get("统计时间") or ""))
        if not m:
            continue
        d = f"{m.group(1)}-{int(m.group(2)):02d}-01"
        val = r.get("黄金储备")
        if val is None or val != val:   # NaN 跳过
            continue
        out.append({"country": country, "date": d, "value": float(val), "yoy": None, "mom": None})
    return out


def fetch_cb_gold(country: str = "CN", retries: int = 4) -> list[dict]:
    """央行黄金储备(实物万盎司, 月频)。

    sina jsonp 端点(macro_china_foreign_exchange_gold)内部对 ~13 页分页各发一次请求,
    偶发被拦返回非 JSON → demjson 抛 JSONDecodeError, 13 页里任一页被拦整批作废。
    这里用指数退避重试(比 _retry_ak 固定间隔更耐临时被拦); 单次成功即返回, 全失败抛 FetchError。
    """
    last = None
    for i in range(retries + 1):
        try:
            df = ak.macro_china_foreign_exchange_gold()
            return parse_cb_gold(df, country)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(2.0 * (i + 1))   # 2,4,6,8s 退避
    raise FetchError(f"cb_gold 抓取重试耗尽(sina jsonp 偶发被拦): {last}")


def fetch_fred_series(series_id: str, timeout: float = 30.0) -> pd.DataFrame:
    """FRED 公开 fredgraph.csv(免费无 key) → western_macro_series 行(source=fred)。
    列: observation_date, <series_id>; 缺值 '.'/NaN 丢弃。"""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    if series_id not in df.columns:
        return pd.DataFrame(columns=["source", "symbol", "date", "close"])
    df = df.rename(columns={"observation_date": "date", series_id: "close"}).dropna(subset=["close"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    out = pd.DataFrame({"source": "fred", "symbol": series_id,
                        "date": df["date"].astype(str), "close": df["close"].astype(float)})
    return out


_ACM_URL = "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"


def fetch_acm_term_premium(maturity: int = 10, timeout: float = 60.0) -> pd.DataFrame:
    """NY Fed ACM 期限溢价(XLS, 免费无key, 非-akshare 源·用户授权) → western_macro_series 行。
    maturity=10 → ACMTP10(10年期期限溢价)。DATE 形如 '31-Jul-2026'。"""
    resp = requests.get(_ACM_URL, timeout=timeout)
    resp.raise_for_status()
    df = pd.read_excel(io.BytesIO(resp.content))
    col = f"ACMTP{int(maturity):02d}"
    if "DATE" not in df.columns or col not in df.columns:
        return pd.DataFrame(columns=["source", "symbol", "date", "close"])
    sub = df[["DATE", col]].dropna()
    dates = pd.to_datetime(sub["DATE"]).dt.strftime("%Y-%m-%d")
    return pd.DataFrame({"source": "nyfed_acm", "symbol": col,
                         "date": dates.astype(str), "close": sub[col].astype(float)})


# ---- 经济日历/事件 (news_economic_baidu · 框架催化剂层) ----
def parse_economic_calendar(df, min_importance: int = 2) -> list[dict]:
    """ak.news_economic_baidu 的 df → [{date,time,region,event,actual,forecast,previous,importance}]。
    筛重要性≥min(去 EIA 等低重要性噪声)。"""
    if df is None or len(df) == 0:
        return []
    if "重要性" in df.columns:
        imp = pd.to_numeric(df["重要性"], errors="coerce")
        df = df[imp.fillna(0) >= min_importance]
    out = []
    for _, r in df.iterrows():
        out.append({
            "date": str(r.get("日期"))[:10], "time": str(r.get("时间") or ""),
            "region": str(r.get("地区") or ""), "event": str(r.get("事件") or ""),
            "actual": r.get("公布"), "forecast": r.get("预期"), "previous": r.get("前值"),
            "importance": r.get("重要性"),
        })
    return out


def fetch_economic_calendar(days_back: int = 7, days_forward: int = 45, retries: int = 1) -> list[dict]:
    """抓 [today-days_back, today+days_forward] 区间的经济日历(逐日 news_economic_baidu), 筛重要性≥2。
    含已公布(公布值 actual)与未来排期(actual 缺)。逐日容错。前看 45 天(覆盖下次 FOMC)。"""
    from datetime import date, timedelta
    today = date.today()
    out: list[dict] = []
    for k in range(-days_back, days_forward + 1):
        d = today + timedelta(days=k)
        try:
            df = _retry_ak(ak.news_economic_baidu, retries=retries, date=d.strftime("%Y%m%d"))
            out.extend(parse_economic_calendar(df))
        except Exception as e:  # noqa: BLE001
            log.warning("economic_calendar %s failed: %s", d, str(e)[:80])
        time.sleep(0.15)
    return out


# ---- 中国货币条件 (货币条件 · M2/M1/社融 月频, 金十源;国内宏观看板①数据腿) ----
_MONEY_MONTH_RE = re.compile(r"(\d{4})年(\d{1,2})月份")
_MONEY_COLS = {
    "m2_amt": "货币和准货币(M2)-数量(亿元)", "m2_yoy": "货币和准货币(M2)-同比增长",
    "m1_amt": "货币(M1)-数量(亿元)", "m1_yoy": "货币(M1)-同比增长",
    "m0_amt": "流通中的现金(M0)-数量(亿元)", "m0_yoy": "流通中的现金(M0)-同比增长",
}


def parse_china_money_supply(df) -> list[dict]:
    """ak.macro_china_money_supply (金十, 月频, '2026年07月份') → [{month,m2_*,m1_*,m0_*}]。
    同比直接用源列(不重算); 2024-01 起 M1 新口径(含个人活期)——序列有断点,上层图注、只展示不进研究。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        m = _MONEY_MONTH_RE.match(str(r.get("月份") or ""))
        if not m:
            continue
        row = {"month": f"{m.group(1)}-{int(m.group(2)):02d}-01"}
        for key, col in _MONEY_COLS.items():
            row[key] = r.get(col)
        out.append(row)
    return out


def fetch_china_money_supply(retries: int = 3) -> list[dict]:
    """M2/M1/M0 月度(2008 起, 金十)。端点偶发被拦 → 指数退避(金十 jsonp 同族,比固定间隔耐拦)。"""
    last = None
    for i in range(retries + 1):
        try:
            return parse_china_money_supply(ak.macro_china_money_supply())
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.5 * (i + 1))
    raise FetchError(f"china_money_supply 抓取重试耗尽: {last}")


def parse_china_tsf(df) -> list[dict]:
    """ak.macro_china_shrzgm (社融增量, 月频, '201501') → [{month,tsf_inc,rmb_loans,corp_bond,equity_fin}]。
    增量+三分子项(人民币贷款/企业债券/股票融资——信贷黑箱的历史对照);存量同比无免费源,
    上层用 增量TTM/M2 作脉冲代理(源滞后货币约 2-3 个月)。"""
    if df is None or len(df) == 0:
        return []
    _nn = lambda v: None if v is None or v != v else v  # noqa: E731
    out = []
    for _, r in df.iterrows():
        s = str(r.get("月份") or "")
        if len(s) != 6 or not s.isdigit():
            continue
        v = r.get("社会融资规模增量")
        if v is None or v != v:   # 增量缺 → 整行跳过
            continue
        out.append({"month": f"{s[:4]}-{s[4:]}-01", "tsf_inc": float(v),
                    "rmb_loans": _nn(r.get("其中-人民币贷款")),
                    "corp_bond": _nn(r.get("其中-企业债券")),
                    "equity_fin": _nn(r.get("其中-非金融企业境内股票融资"))})
    return out


def fetch_china_tsf(retries: int = 3) -> list[dict]:
    last = None
    for i in range(retries + 1):
        try:
            return parse_china_tsf(ak.macro_china_shrzgm())
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.5 * (i + 1))
    raise FetchError(f"china_tsf 抓取重试耗尽: {last}")


# ---- 中国利率与流动性 (第七看板 国内宏观 · 金十源,2026-08-24 探针验证) ----
_SHIBOR_COLS = {"overnight": "O/N-定价", "w1": "1W-定价", "w2": "2W-定价", "m1": "1M-定价",
                "m3": "3M-定价", "m6": "6M-定价", "m9": "9M-定价", "y1": "1Y-定价"}


def parse_shibor(df) -> list[dict]:
    """ak.macro_china_shibor_all (金十,2015起,日频) → [{date,overnight,w1..y1}](只取定价列,弃涨跌幅)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        d = _norm_date(r.get("日期"))
        if not d:
            continue
        row = {"date": d}
        for k, col in _SHIBOR_COLS.items():
            row[k] = r.get(col)
        out.append(row)
    return out


def fetch_shibor(retries: int = 3) -> list[dict]:
    return _retry_ak_wrap(parse_shibor, ak.macro_china_shibor_all, retries)


def parse_repo_fix(df) -> list[dict]:
    """ak.repo_rate_hist (定盘利率,日频) → [{date,fr001..fdr014}]。FDR=银银间(DR 系,央行政策目标利率)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        d = _norm_date(r.get("date"))
        if not d:
            continue
        out.append({"date": d, "fr001": r.get("FR001"), "fr007": r.get("FR007"),
                    "fr014": r.get("FR014"), "fdr001": r.get("FDR001"),
                    "fdr007": r.get("FDR007"), "fdr014": r.get("FDR014")})
    return out


def fetch_repo_fix(start: str = "2020-09-30", end: str | None = None,
                   retries: int = 2) -> list[dict]:
    """FR/FDR 定盘利率(需日期窗口参数,按年分段拉全史——同 fetch_market_margin 分段先例)。"""
    end = end or today_str()
    out: list[dict] = []
    seg_start = start
    while seg_start < end:
        y = int(seg_start[:4])
        seg_end = min(f"{y}-12-31", end)
        last = None
        rows = None
        for i in range(retries + 1):
            try:
                rows = parse_repo_fix(
                    ak.repo_rate_hist(start_date=seg_start.replace("-", ""),
                                      end_date=seg_end.replace("-", "")))
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (i + 1))
        if rows is None:
            raise FetchError(f"repo_fix {seg_start}..{seg_end} 重试耗尽: {last}")
        out.extend(rows)
        seg_start = f"{y + 1}-01-01"
        time.sleep(0.4)
    return out


def parse_lpr(df) -> list[dict]:
    """ak.macro_china_lpr (月频20日,1991起含旧贷款基准利率) → [{date,lpr1y,lpr5y,base1y,base5y}]。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        d = _norm_date(r.get("TRADE_DATE"))
        if not d:
            continue
        out.append({"date": d, "lpr1y": r.get("LPR1Y"), "lpr5y": r.get("LPR5Y"),
                    "base1y": r.get("RATE_1"), "base5y": r.get("RATE_2")})
    return out


def fetch_lpr(retries: int = 3) -> list[dict]:
    return _retry_ak_wrap(parse_lpr, ak.macro_china_lpr, retries)


# ---- 中国宏观 tushare 主源 (2026-09-13 迁移 1.2-1.6 · ADR-0002;列名当日语探实证) ----
# shibor={date,on,1w,2w,1m,3m,6m,9m,1y}; cn_m={month,m0/m1/m2+*_yoy/*_mom};
# cn_cpi={month,nt_yoy(全国同比),..}; cn_ppi={month,ppi_yoy,..}; sf_month={month,inc_month,inc_cumval,stk_endval}
_TS_SHIBOR_COLS = {"overnight": "on", "w1": "1w", "w2": "2w", "m1": "1m",
                   "m3": "3m", "m6": "6m", "m9": "9m", "y1": "1y"}


def _ts_f(v):
    return float(v) if v is not None and v == v else None


def _ts_d8(s) -> Optional[str]:
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else None


def fetch_shibor_tushare(start: str = "2006-01-01") -> list[dict]:
    """Shibor 全史(tushare `shibor`,2006-10 起,深于金十 2015)。单次 2000 行 → 按年分段
    (同 fetch_repo_fix 先例)。返回与 parse_shibor 同形 [{date,overnight,w1..y1}]。"""
    from . import tushare_client as tc
    end = today_str()
    out: list[dict] = []
    seg = start
    while seg < end:
        y = int(seg[:4])
        seg_end = min(f"{y}-12-31", end)
        df = tc.query("shibor", start_date=seg.replace("-", ""),
                      end_date=seg_end.replace("-", ""))
        for _, r in df.iterrows():
            d = _ts_d8(r.get("date"))
            if not d:
                continue
            row = {"date": d}
            for k, col in _TS_SHIBOR_COLS.items():
                row[k] = _ts_f(r.get(col))
            out.append(row)
        seg = f"{y + 1}-01-01"
    if not out:
        raise FetchError("shibor(tushare) empty")
    return out


def fetch_lpr_tushare() -> list[dict]:
    """LPR(tushare `shibor_lpr`)。⚠实测限频 1 次/小时 → 单次全区间、retries=1。列名文档
    lpr_1y/lpr_5y(防御式兼容);旧贷款基准利率(base*)无对应列→None,store keep_null 保留金十存量。"""
    from . import tushare_client as tc
    df = tc.query("shibor_lpr", start_date="19910101",
                  end_date=today_str().replace("-", ""), retries=1)
    if df is None or len(df) == 0:
        raise FetchError("shibor_lpr empty")
    c1y = next((c for c in df.columns if str(c).lower() in ("lpr_1y", "lpr1y")), None)
    c5y = next((c for c in df.columns if str(c).lower() in ("lpr_5y", "lpr5y")), None)
    dcol = next((c for c in df.columns if str(c).lower() in ("trade_date", "date")), None)
    if c1y is None or c5y is None or dcol is None:
        raise FetchError(f"shibor_lpr 列名不符: {list(df.columns)}")
    out = []
    for _, r in df.iterrows():
        d = _ts_d8(r.get(dcol))
        if not d:
            continue
        out.append({"date": d, "lpr1y": _ts_f(r.get(c1y)), "lpr5y": _ts_f(r.get(c5y)),
                    "base1y": None, "base5y": None})
    if not out:
        raise FetchError("shibor_lpr rows empty")
    return out


def fetch_money_supply_tushare() -> list[dict]:
    """M2/M1/M0 月度(tushare `cn_m`,1978 起单次全量,深于金十 2008)。
    返回与 parse_china_money_supply 同形 [{month,m2_*,m1_*,m0_*}]。"""
    from . import tushare_client as tc
    df = tc.query("cn_m")
    if df is None or len(df) == 0:
        raise FetchError("cn_m empty")
    out = []
    for _, r in df.iterrows():
        m = str(r.get("month") or "")
        if len(m) != 6 or not m.isdigit():
            continue
        out.append({"month": f"{m[:4]}-{m[4:]}-01",
                    "m2_amt": _ts_f(r.get("m2")), "m2_yoy": _ts_f(r.get("m2_yoy")),
                    "m1_amt": _ts_f(r.get("m1")), "m1_yoy": _ts_f(r.get("m1_yoy")),
                    "m0_amt": _ts_f(r.get("m0")), "m0_yoy": _ts_f(r.get("m0_yoy"))})
    if not out:
        raise FetchError("cn_m rows empty")
    return out


def fetch_tsf_inc_tushare() -> list[dict]:
    """社融增量(tushare `sf_month`,2002 起)——仅 tsf_inc(分项无)。
    定位=应急 fallback(金十列更全仍主源);upsert 走 keep_null 保分项。"""
    from . import tushare_client as tc
    df = tc.query("sf_month")
    if df is None or len(df) == 0:
        raise FetchError("sf_month empty")
    out = []
    for _, r in df.iterrows():
        m = str(r.get("month") or "")
        v = _ts_f(r.get("inc_month"))
        if len(m) != 6 or not m.isdigit() or v is None:
            continue
        out.append({"month": f"{m[:4]}-{m[4:]}-01", "tsf_inc": v,
                    "rmb_loans": None, "corp_bond": None, "equity_fin": None})
    if not out:
        raise FetchError("sf_month rows empty")
    return out


def fetch_tsf_stock_tushare() -> list[dict]:
    """社融存量余额(tushare `sf_month.stk_endval`,2002-12 起·早年仅年末值)→ ts_stock。
    常规月调腿(批次 2.5):金十无存量列,此为唯一供给;单位=**万亿元**(源原生,
    202412=408.34 与官方分毫不差),与同表 tsf_inc(亿)不同——图注/tile 标注。
    keep_null: 仅动 ts_stock 列,不碰金十分项。"""
    from . import tushare_client as tc
    df = tc.query("sf_month")
    if df is None or len(df) == 0:
        raise FetchError("sf_month empty")
    out = []
    for _, r in df.iterrows():
        m = str(r.get("month") or "")
        v = _ts_f(r.get("stk_endval"))
        if len(m) != 6 or not m.isdigit() or v is None:
            continue
        out.append({"month": f"{m[:4]}-{m[4:]}-01", "ts_stock": v,
                    "tsf_inc": None, "rmb_loans": None, "corp_bond": None,
                    "equity_fin": None})
    if not out:
        raise FetchError("sf_month stk_endval rows empty")
    return out


def fetch_cpi_ppi_tushare() -> dict:
    """CPI/PPI 同比月度(tushare cn_cpi nt_yoy 1951 起 / cn_ppi ppi_yoy 1978 起,深于金十)。
    Returns {'cpi_yoy': [{month,value}], 'ppi_yoy': [...]}——与 fetch_china_real 对应腿同形。"""
    from . import tushare_client as tc
    out: dict[str, list] = {}
    for metric, api, col in (("cpi_yoy", "cn_cpi", "nt_yoy"), ("ppi_yoy", "cn_ppi", "ppi_yoy")):
        df = tc.query(api)
        rows = []
        for _, r in (df.iterrows() if df is not None and len(df) else []):
            m = str(r.get("month") or "")
            v = _ts_f(r.get(col))
            if len(m) == 6 and m.isdigit() and v is not None:
                rows.append({"month": f"{m[:4]}-{m[4:]}-01", "value": v})
        out[metric] = rows
    if not out["cpi_yoy"] and not out["ppi_yoy"]:
        raise FetchError("cn_cpi/cn_ppi empty")
    return out


def parse_cn_bond(df) -> list[dict]:
    """ak.bond_zh_us_rate (中美国债收益率同表) → 只取中国列 [{date,y2,y5,y10,y30,spread_10y2y}]
    (美国列归 western_macro 域,不在此存)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        d = _norm_date(r.get("日期"))
        if not d:
            continue
        out.append({"date": d, "y2": r.get("中国国债收益率2年"), "y5": r.get("中国国债收益率5年"),
                    "y10": r.get("中国国债收益率10年"), "y30": r.get("中国国债收益率30年"),
                    "spread_10y2y": r.get("中国国债收益率10年-2年")})
    return out


def fetch_cn_bond(start_date: str = "19901219", retries: int = 3) -> list[dict]:
    return _retry_ak_wrap(parse_cn_bond, ak.bond_zh_us_rate, retries, start_date=start_date)


def parse_cb_balance(df) -> list[dict]:
    """ak.macro_china_central_bank_balance (央行资产负债表,月频,统计时间 '1993.6'式)
    → [{month,claim_odc,base_money,govt_deposit,total_assets}]。对其他存款性公司债权=OMO/MLF 余额
    (月度差分≈净投放的滞后近似);早期缺列 NaN → None 归一。"""
    if df is None or len(df) == 0:
        return []
    _nn = lambda v: None if v is None or v != v else v   # noqa: E731  NaN→None(pandas 混列读成 NaN)
    out = []
    for _, r in df.iterrows():
        m = _CB_DATE_RE.match(str(r.get("统计时间") or ""))
        if not m:
            continue
        out.append({
            "month": f"{m.group(1)}-{int(m.group(2)):02d}-01",
            "claim_odc": _nn(r.get("对其他存款性公司债权")),
            "base_money": _nn(r.get("储备货币")),
            "govt_deposit": _nn(r.get("政府存款")),
            "total_assets": _nn(r.get("总资产")),
        })
    return out


def fetch_cb_balance(retries: int = 3) -> list[dict]:
    return _retry_ak_wrap(parse_cb_balance, ak.macro_china_central_bank_balance, retries)


def _retry_ak_wrap(parse, fn, retries: int = 3, **kw) -> list[dict]:
    """金十系单调用端点通用壳:重试 + 解析(指数退避,偶发被拦耐拦)。"""
    last = None
    for i in range(retries + 1):
        try:
            return parse(fn(**kw))
        except Exception as e:  # noqa: BLE001
            last = e
            if i < retries:
                time.sleep(1.5 * (i + 1))
    raise FetchError(f"{getattr(fn, '__name__', 'ak')} 抓取重试耗尽: {last}")


def parse_lgb_issue(df) -> list[dict]:
    """ak.bond_local_government_issue_cninfo / bond_treasure_issue_cninfo(同构 15 列) → 逐券
    [{code,name,issue_date,plan_amt,actual_amt,pay_date}]。金额单位亿元(月度聚合在 nowcast 纯函数)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        code = str(r.get("债券代码") or "").strip()
        if not code:
            continue
        out.append({"code": code, "name": r.get("债券简称"),
                    "issue_date": _norm_date(r.get("发行起始日")),
                    "plan_amt": r.get("计划发行总量"),
                    "actual_amt": r.get("实际发行总量"),
                    "pay_date": _norm_date(r.get("缴款日"))})
    return out


def _fetch_bond_issue_cninfo(ak_fn, start: str, end: str, retries: int) -> list[dict]:
    """cninfo 发行明细通用分段拉取(月窗+逐窗重试,失败月跳过重跑自愈;去重由 store code 主键兜底)。"""
    out: list[dict] = []
    y, m = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    while (y, m) <= (ey, em):
        w_start = f"{y:04d}-{m:02d}-01"
        nm, ny = (m + 1, y) if m < 12 else (1, y + 1)
        w_end = min(f"{ny:04d}-{nm:02d}-01", end)
        rows = None
        last = None
        for i in range(retries + 1):
            try:
                rows = parse_lgb_issue(ak_fn(start_date=w_start.replace("-", ""),
                                             end_date=w_end.replace("-", "")))
                break
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.5 * (i + 1))
        if rows is None:
            log.warning("%s %s..%s 重试耗尽(跳过该月,重跑自愈): %s",
                        getattr(ak_fn, "__name__", "cninfo"), w_start, w_end, str(last)[:80])
        else:
            out.extend(rows)
        y, m = ny, nm
        time.sleep(0.5)
    return out


def fetch_lgb_issue(start: str = "2021-09-01", end: str | None = None,
                    retries: int = 2) -> list[dict]:
    """地方债发行明细全史(2021-09 起,源最早窗口)。"""
    return _fetch_bond_issue_cninfo(ak.bond_local_government_issue_cninfo,
                                    start, end or today_str(), retries)


def fetch_tsy_issue(start: str = "2021-09-01", end: str | None = None,
                    retries: int = 2) -> list[dict]:
    """国债发行明细全史(bond_treasure_issue_cninfo,同构;远期批补齐政府债另一半)。"""
    return _fetch_bond_issue_cninfo(ak.bond_treasure_issue_cninfo,
                                    start, end or today_str(), retries)


# ---- 通胀/实体月度序列 (远期批 · 金十各族,2026-08-25 探针验证) ----
def parse_cn_month_value(df, value_col: str) -> list[dict]:
    """'YYYY年MM月份' 式月份表(金十 CPI/PPI/社零族) → [{month,value}]。值缺跳过。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        m = _MONEY_MONTH_RE.match(str(r.get("月份") or ""))
        v = r.get(value_col)
        if not m or v is None or v != v:
            continue
        out.append({"month": f"{m.group(1)}-{int(m.group(2)):02d}-01", "value": float(v)})
    return out


def parse_report_monthly(df, ref_month: str = "same") -> list[dict]:
    """'商品/日期/今值' 式报告表(官方 PMI/工业增加值族) → [{month,value}]。
    ref_month: 'same'=参考月=日期月(官方 PMI:月末发布当月值,旧数据 1 日标签同月);
    'prev_if_mid'=day=1 → 日期月,day>1 → 日期月−1(工业增加值:月中发布上月值,旧数据 1 日标签当月)。"""
    if df is None or len(df) == 0:
        return []
    out = []
    for _, r in df.iterrows():
        d = r.get("日期")
        v = r.get("今值")
        if v is None or v != v:
            continue                                     # 未来排期行(今值空)跳过
        d = _norm_date(d)
        if not d:
            continue
        y, mm, dd = int(d[:4]), int(d[5:7]), int(d[8:10])
        if ref_month == "prev_if_mid" and dd > 1:
            t = y * 12 + (mm - 1) - 1
            y, mm = t // 12, t % 12 + 1
        out.append({"month": f"{y:04d}-{mm:02d}-01", "value": float(v)})
    return out


def fetch_china_real(retries: int = 2, skip: tuple = ()) -> dict:
    """通胀/实体五腿一次拉齐(逐腿独立容错):cpi_yoy/ppi_yoy/pmi(官方制造业)/retail_yoy/ind_yoy。
    skip: 跳过的 metric 元组(tushare 已供时省金十调用,2026-09-13 迁移 1.6)。
    Returns {metric: rows}。财新 PMI 弃用(源日期语义混杂,新旧行发布日/参考月口径不一,防错位)。"""
    legs = [
        ("cpi_yoy", lambda: parse_cn_month_value(
            _call(ak.macro_china_cpi, retries), "全国-同比增长")),
        ("ppi_yoy", lambda: parse_cn_month_value(
            _call(ak.macro_china_ppi, retries), "当月同比增长")),
        ("retail_yoy", lambda: parse_cn_month_value(
            _call(ak.macro_china_consumer_goods_retail, retries), "同比增长")),
        ("pmi", lambda: parse_cn_month_value(
            _call(ak.macro_china_pmi, retries), "制造业-指数")),   # 月份表口径 2008起正常更新
        # (macro_china_pmi_yearly 2005起但源停更至2025-08,弃;工业增加值同族滞后至2025-07,图注说明)
        ("ind_yoy", lambda: parse_report_monthly(
            _call(ak.macro_china_industrial_production_yoy, retries), "prev_if_mid")),
    ]
    out: dict = {}
    for metric, leg in legs:
        if metric in skip:
            out[metric] = None
            continue
        try:
            out[metric] = leg()
        except Exception as e:  # noqa: BLE001
            log.warning("china_real %s failed: %s", metric, str(e)[:100])
            out[metric] = None
    return out


def _call(fn, retries: int = 2):
    """金十单调用重试壳(供 fetch_china_real 的 lambda 用,不带解析)。"""
    last = None
    for i in range(retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise FetchError(f"{getattr(fn, '__name__', 'ak')} 重试耗尽: {last}")


