"""Research-dashboard data coverage + freshness check. Optionally --fix gaps to current.

Reports, per pool ETF: shares / nav / price last-date vs the latest trading day (benchmark's
last price date), plus industry-PE coverage. Flags stale/missing data.
With --fix: brings stale data current — prices via update_all, shares via gap backfill,
nav via incremental update, PE via gap backfill (throttle-tuned) — then re-reports.

Usage:
  python scripts/dashboard_data_check.py            # report only
  python scripts/dashboard_data_check.py --fix      # report + backfill gaps to current
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store, DataManager

PE_STALE_DAYS = 14  # PE is weekly cadence + cninfo-throttle-prone; only refresh if >2wk stale


def _within_days(d1: str, d2: str, tol: int) -> bool:
    """True if d1 is within `tol` calendar days of d2 (d1 may lag, e.g. QDII NAV T+2)."""
    try:
        return abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days) <= tol
    except (TypeError, ValueError):
        return False


def _latest_trading_day(store: Store, cfg) -> str | None:
    """Benchmark's last price date = the latest trading day the system knows."""
    return store.last_date(cfg.benchmark_symbol)


def _fetch(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def report(conn, cfg, syms) -> dict:
    """Print coverage + freshness table. Returns summary dict for fix decisions."""
    bench_last = _fetch(conn, "SELECT MAX(date) FROM daily_prices WHERE symbol=?",
                        (cfg.benchmark_symbol,))[0]
    pe_last = _fetch(conn, "SELECT MAX(date) FROM industry_pe")[0] or "（无）"
    pe_dates = _fetch(conn, "SELECT COUNT(DISTINCT date) FROM industry_pe")[0]
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n基准 {cfg.benchmark_symbol} 最新交易日: {bench_last}   (今天 {today})")
    print(f"行业 PE: 最新 {pe_last}  共 {pe_dates} 个日期\n")

    rows = []
    n_price_ok = n_shares_ok = n_nav_ok = 0
    n_shares_zero = n_nav_zero = 0
    for s in syms:
        p = _fetch(conn, "SELECT MAX(date) FROM daily_prices WHERE symbol=?", (s,))[0]
        sh = _fetch(conn, "SELECT MAX(date) FROM etf_scale WHERE symbol=? AND shares IS NOT NULL", (s,))[0]
        nsh = _fetch(conn, "SELECT COUNT(*) FROM etf_scale WHERE symbol=? AND shares IS NOT NULL", (s,))[0]
        nv = _fetch(conn, "SELECT MAX(date) FROM etf_nav WHERE symbol=? AND unit_nav IS NOT NULL", (s,))[0]
        nnv = _fetch(conn, "SELECT COUNT(*) FROM etf_nav WHERE symbol=? AND unit_nav IS NOT NULL", (s,))[0]
        # price/shares are A-share same-day (strict). NAV is fund-published and QDII ETFs
        # (中概互联/纳指/恒生科技) legitimately lag T+1/T+2 (overseas mkt close) → allow 2 days.
        p_ok = p is not None and p >= bench_last
        sh_ok = sh is not None and sh >= bench_last
        nv_ok = nv is not None and _within_days(nv, bench_last, 2)
        n_price_ok += p_ok; n_shares_ok += sh_ok; n_nav_ok += nv_ok
        if nsh == 0: n_shares_zero += 1
        if nnv == 0: n_nav_zero += 1
        rows.append((s, cfg.symbol_meta().get(s, {}).get("name", s), p, sh, nsh, nv, nnv, p_ok, sh_ok, nv_ok))

    print(f"{'ETF':14} {'price':12} {'shares':12} {'sh#':>5} {'nav':12} {'nav#':>5}")
    for s, nm, p, sh, nsh, nv, nnv, p_ok, sh_ok, nv_ok in rows:
        flag = ""
        if nsh == 0: flag += " [无份额]"
        if nnv == 0: flag += " [无净值]"
        if not sh_ok and nsh > 0: flag += " [份额旧]"
        if not nv_ok and nnv > 0: flag += " [净值旧]"
        print(f"{nm[:12]:12} {s} {str(p):12} {str(sh):12} {nsh:>5} {str(nv):12} {nnv:>5}{flag}")

    print(f"\n汇总: price新鲜 {n_price_ok}/{len(syms)} · shares新鲜 {n_shares_ok}/{len(syms)}"
          f" · nav新鲜 {n_nav_ok}/{len(syms)} · 份额全缺 {n_shares_zero} · 净值全缺 {n_nav_zero}")
    pe_stale = pe_last == "（无）" or (
        bench_last and (datetime.strptime(bench_last, "%Y-%m-%d")
                        - datetime.strptime(pe_last, "%Y-%m-%d")).days > PE_STALE_DAYS)
    print(f"PE新鲜: {'旧(>' + str(PE_STALE_DAYS) + '天)' if pe_stale else 'OK'}\n")
    return {"bench_last": bench_last, "pe_last": pe_last, "pe_stale": pe_stale,
            "rows": rows, "n_shares_zero": n_shares_zero}


def main():
    ap = argparse.ArgumentParser(description="Research-dashboard data check (+--fix)")
    ap.add_argument("--fix", action="store_true", help="backfill stale/missing data to current")
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()
    cfg = get_config()
    store = Store(cfg.db_path)
    dm = DataManager(store=store, config=cfg)
    syms = args.symbols or cfg.rotation_symbols()

    conn = sqlite3.connect(str(cfg.db_path))
    print("=== 数据新鲜度检查 ===")
    info = report(conn, cfg, syms)

    if not args.fix:
        return

    # ---- --fix: bring stale data current ----
    print("\n=== 开始补齐 (--fix) ===")
    bench_last = info["bench_last"]
    if not bench_last:
        print("基准无价格数据，先跑 update_data.py。"); return

    # 1) prices: if benchmark behind, refresh all (idempotent, resumes per symbol)
    today = datetime.now().strftime("%Y-%m-%d")
    if (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(bench_last, "%Y-%m-%d")).days > 3:
        print(f"  基准落后(到{bench_last})，刷新价格..."); dm.update_all();
        bench_last = _latest_trading_day(store, cfg) or bench_last
        print(f"  基准现到 {bench_last}")

    # 2) shares: backfill gap (SSE per-date + SZSE range) from last+1 to bench_last
    last_sh = _fetch(conn, "SELECT MAX(date) FROM etf_scale WHERE shares IS NOT NULL")[0]
    if last_sh and last_sh < bench_last:
        start = (datetime.strptime(last_sh, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  份额补缺 {start}..{bench_last} ...")
        dm.backfill_etf_scale(start, bench_last, step_days=1, source="all")

    # 3) nav: incremental per-symbol (resumes from each symbol's last_nav_date)
    print("  净值增量更新..."); dm.update_etf_nav()

    # 4) PE: only if stale (>2wk) — cninfo throttle, don't hammer for small gaps
    if info["pe_stale"] and info["pe_last"] != "（无）":
        start = (datetime.strptime(info["pe_last"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  行业PE补缺 {start}..{bench_last} (step=7 sleep=8) ...")
        dm.backfill_industry_pe(start, bench_last, step_days=7, sleep=8)

    print("\n=== 补齐后复查 ===")
    report(conn, cfg, syms)


if __name__ == "__main__":
    main()
