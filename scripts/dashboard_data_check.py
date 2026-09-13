"""Research-dashboard data coverage + freshness check. Optionally --fix gaps to current.

Reports, per pool ETF: shares / nav / price last-date vs the latest trading day
(trade_calendar's last CLOSED session — independent of any symbol's own data), plus
industry-PE coverage. Flags stale/missing data.
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
from stockagent.pool import universe as pool_universe

INDUSTRY_STALE_DAYS = 45  # 东财行业板块成分月更(候选池策略2 三类分流)


def _within_days(d1: str, d2: str, tol: int) -> bool:
    """True if d1 is within `tol` calendar days of d2 (d1 may lag, e.g. QDII NAV T+2)."""
    try:
        return abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days) <= tol
    except (TypeError, ValueError):
        return False


def _target_trading_day(conn, now: datetime) -> str | None:
    """Latest A-share trading day whose session has CLOSED — the authoritative freshness
    target, independent of any one symbol's data.

    Uses trade_calendar (sourced from sina's full trade-date list), NOT the benchmark's own
    price date — which is circular: it can never detect that the benchmark itself is stale, so
    a 1–2 day gap goes unnoticed (the old --fix only refreshed when >3 calendar days behind).
    Today counts only past 15:00 close; otherwise the prior trading day (so a pre-close run
    targets yesterday's close, matching the "报告 8:30 基于前日收盘" convention). Returns None
    if the calendar is empty (caller falls back to the benchmark date).
    """
    bound = now.date() if now.hour >= 15 else now.date() - timedelta(days=1)
    row = _fetch(conn, "SELECT MAX(date) FROM trade_calendar WHERE is_open=1 AND date<=?",
                 (bound.strftime("%Y-%m-%d"),))
    return row[0] if row and row[0] else None


def _fetch(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def _pool_report(store, conn, ref) -> dict:
    """候选个股池段(V8 高业绩池): 全市场宇宙规模 / 价格新鲜 x/N / 行业快照 / 风险腿新鲜度。"""
    info: dict = {"universe": [], "universe_n": 0}
    spot = store.latest_stock_spot()
    if len(spot) == 0:
        print("候选个股池: (未初始化 — python scripts/backfill_stock_pool.py --all 冷启动)")
        return info
    pcfg = (get_config().params.get("stock_pool", {}) or {})
    ucfg = pcfg.get("universe", {}) or {}
    u = pool_universe.derive_universe(
        spot, exclude_prefixes=tuple(ucfg.get("exclude_name_prefixes",
                                              pool_universe.EXCLUDED_NAME_PREFIXES)))
    codes = [str(c) for c in u.index]
    info["universe"] = codes
    info["universe_n"] = len(codes)
    n_fresh = 0
    if codes and ref:
        for c in codes:
            d = _fetch(conn, "SELECT MAX(date) FROM daily_prices WHERE symbol=?", (c,))[0]
            n_fresh += d is not None and d >= ref
    ind_last = store.last_industry_snapshot() or "（无）"
    ind_n = _fetch(conn, "SELECT COUNT(DISTINCT industry) FROM industry_member")[0]
    cov = pool_universe.industry_coverage(pool_universe.join_industry(
        u, store.industry_map(), get_config().industry_class()))
    bal_periods = len(store.stock_balance_periods())
    bal_last = store.get_meta("last_stock_balance_update") or "（无）"
    npabs_n = _fetch(conn, "SELECT COUNT(*) FROM stock_report_actual WHERE np_abs IS NOT NULL")[0]
    print(f"候选个股池(V8 高业绩池): universe {len(codes)} 只(全市场非 ST)"
          f" · 价格新鲜 {n_fresh}/{len(codes)} · 行业快照 {ind_last}({ind_n} 板块,"
          f"映射覆盖 {cov['pct']:.0%}) · 资产负债 {bal_periods} 期(至 {bal_last})"
          f" · 正式报扩列 {npabs_n} 行")
    return info


def report(conn, cfg, syms, store: "Store | None" = None) -> dict:
    """Print coverage + freshness table. Returns summary dict for fix decisions."""
    bench_last = _fetch(conn, "SELECT MAX(date) FROM daily_prices WHERE symbol=?",
                        (cfg.benchmark_symbol,))[0]
    target = _target_trading_day(conn, datetime.now())
    ref = target or bench_last  # authoritative calendar target; fall back to benchmark if no calendar
    today = datetime.now().strftime("%Y-%m-%d")
    if target:
        print(f"\n最新交易日(已收盘): {target}   基准{cfg.benchmark_symbol}价格: {bench_last}   (今天 {today})")
    else:
        print(f"\n基准 {cfg.benchmark_symbol} 最新价格日: {bench_last}   (今天 {today}, 日历为空)")
    print()

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
        p_ok = p is not None and p >= ref
        sh_ok = sh is not None and sh >= ref
        nv_ok = nv is not None and _within_days(nv, ref, 2)
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
    earn_period = _fetch(conn, "SELECT MAX(report_period) FROM etf_earnings")[0] or "（无）"
    earn_n = _fetch(conn, "SELECT COUNT(DISTINCT symbol) FROM etf_earnings")[0]
    earn_cov = _fetch(conn, "SELECT COUNT(DISTINCT symbol) FROM etf_earnings WHERE coverage>0")[0]
    print(f"业绩预期: 报告期 {earn_period}（{earn_n} 行 / {earn_cov} 只有效信号 coverage>0）")
    cons_n = _fetch(conn, "SELECT COUNT(DISTINCT index_code) FROM index_constituents")[0]
    cons_last = _fetch(conn, "SELECT MAX(snapshot_date) FROM index_constituents")[0] or "（无）"
    print(f"指数成分: {cons_n} 个指数 · 快照 {cons_last}")
    csnap_row = _fetch(conn, "SELECT value FROM meta WHERE key='last_consensus_update'")
    print(f"一致预期快照: {csnap_row[0] if csnap_row and csnap_row[0] else '（无）'}")

    # ---- index layer (V4 tracker — 指数择时层) ----
    idx_names = [("000016", "上证50"), ("000300", "沪深300"), ("000905", "中证500"),
                 ("399006", "创业板指"), ("000688", "科创50")]
    print("指数层(择时):")
    for sym, nm in idx_names:
        d = _fetch(conn, "SELECT MAX(date) FROM index_daily WHERE symbol=?", (sym,))[0]
        ok = d is not None and ref is not None and d >= ref
        print(f"  {nm:6}({sym}) 日线 {str(d):12}{' OK' if ok else ' [缺/旧]'}")
    for nm in ("沪深300", "上证50", "中证500"):
        d = _fetch(conn, "SELECT MAX(date) FROM index_pe WHERE name=?", (nm,))[0]
        print(f"  {nm:6} PE {str(d):12}")
    d = _fetch(conn, "SELECT MAX(date) FROM market_pb")[0]
    print(f"  全市场PB {str(d):12}")
    m2_last = _fetch(conn, "SELECT MAX(month) FROM china_money_supply")[0]
    tsf_last = _fetch(conn, "SELECT MAX(month) FROM china_tsf")[0]
    print(f"  货币条件 M2 {str(m2_last)[:7] if m2_last else '（无）':10} · 社融 {str(tsf_last)[:7] if tsf_last else '（无）'}(月频,⑪)")
    sb_last = _fetch(conn, "SELECT MAX(date) FROM shibor_daily")[0]
    fdr_last = _fetch(conn, "SELECT MAX(date) FROM repo_fix_daily")[0]
    lpr_last = _fetch(conn, "SELECT MAX(date) FROM lpr_monthly")[0]
    bond_last = _fetch(conn, "SELECT MAX(date) FROM cn_bond_daily")[0]
    cb_last = _fetch(conn, "SELECT MAX(month) FROM cb_balance_monthly")[0]
    print(f"  利率腿 Shibor {str(sb_last):12} FDR {str(fdr_last):12} LPR {str(lpr_last):12}"
          f" 中债 {str(bond_last):12} 央行表 {str(cb_last)[:7] if cb_last else '（无）'}(第七看板)")
    lgb_n = _fetch(conn, "SELECT COUNT(*) FROM lgb_bond_issue")[0]
    tsy_n = _fetch(conn, "SELECT COUNT(*) FROM tsy_bond_issue")[0]
    cpi_last = _fetch(conn, "SELECT MAX(month) FROM china_macro_monthly WHERE metric='cpi_yoy'")[0]
    pmi_last = _fetch(conn, "SELECT MAX(month) FROM china_macro_monthly WHERE metric='pmi'")[0]
    print(f"  政府债明细 地方债 {lgb_n} 券 · 国债 {tsy_n} 券 | 通胀/实体 cpi .."
          f"{str(cpi_last)[:7] if cpi_last else '（无）'} pmi ..{str(pmi_last)[:7] if pmi_last else '（无）'}(第七看板)")

    # ---- commodity (个股层 · A 类领先信号 · 17种 + 夜盘快照) ----
    try:
        comm_last = _fetch(conn, "SELECT MAX(date) FROM commodity_price")[0]
        comm_n = _fetch(conn, "SELECT COUNT(DISTINCT variety) FROM commodity_price")[0]
        spot_last = _fetch(conn, "SELECT MAX(date) FROM commodity_spot")[0]
        print(f"  商品价(领先信号) {comm_n} 品种 至 {str(comm_last):12} 快照 {str(spot_last) if spot_last else '无'}")
    except Exception:  # noqa: BLE001 — 表不存在(极老库)不阻塞报告
        pass

    # ---- candidate pool (V7 第六看板 · 候选个股池) ----
    pool_info: dict = {"universe": [], "universe_n": 0}
    if store is not None:
        pool_info = _pool_report(store, conn, ref)
    return {"bench_last": bench_last, "target": target, "ref": ref,
            "earn_period": earn_period,
            "rows": rows, "n_shares_zero": n_shares_zero,
            "pool_universe": pool_info.get("universe", []),
            "pool_universe_n": pool_info.get("universe_n", 0)}


def main():
    ap = argparse.ArgumentParser(description="Research-dashboard data check (+--fix)")
    ap.add_argument("--fix", action="store_true", help="backfill stale/missing data to current")
    ap.add_argument("--no-pool", dest="no_pool", action="store_true",
                    help="跳过候选个股池数据腿(V8 高业绩池 2026-09 重启后默认恢复运行;"
                         "临时停用时传此 flag)")
    ap.add_argument("--symbols", nargs="*", default=None)
    args = ap.parse_args()
    cfg = get_config()
    store = Store(cfg.db_path)
    dm = DataManager(store=store, config=cfg)
    syms = args.symbols or cfg.tracked_symbols()  # 含 research_only(如黄金518880)——研究看板数据新鲜度全覆盖

    conn = sqlite3.connect(str(cfg.db_path))
    print("=== 数据新鲜度检查 ===")
    info = report(conn, cfg, syms, store=store)

    if not args.fix:
        return

    # ---- --fix: bring stale data current ----
    print("\n=== 开始补齐 (--fix) ===")
    dm.refresh_calendar()  # the reference must be current (cheap, idempotent, one sina call)
    target = _target_trading_day(conn, datetime.now()) or info["bench_last"]
    if not target:
        print("无基准价格且日历为空，先跑 update_data.py。"); return
    bench_last = info["bench_last"]

    # 1) prices: refresh if benchmark lags the latest trading day (≥1 day). _target_trading_day
    #    is calendar-based, so this catches 1–2 day gaps the old >3-day heuristic missed (it used
    #    the benchmark's own date as reference — circular, blind to its own staleness).
    if not bench_last or bench_last < target:
        print(f"  基准落后(价格到{bench_last}，目标{target})，刷新价格...")
        dm.update_all()
        bench_last = store.last_date(cfg.benchmark_symbol) or bench_last
        print(f"  基准现到 {bench_last}")

    # 2) shares: backfill gap (SSE per-date + SZSE range) from last+1 to target
    last_sh = _fetch(conn, "SELECT MAX(date) FROM etf_scale WHERE shares IS NOT NULL")[0]
    if last_sh and last_sh < target:
        start = (datetime.strptime(last_sh, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  份额补缺 {start}..{target} ...")
        dm.backfill_etf_scale(start, target, step_days=1, source="all")

    # 3) nav: incremental per-symbol (resumes from each symbol's last_nav_date)
    print("  净值增量更新..."); dm.update_etf_nav()

    # 5) constituents (E1): refresh if missing or snapshot >45 days old (月度节奏)
    cons_last = _fetch(conn, "SELECT MAX(snapshot_date) FROM index_constituents")[0]
    cons_stale = cons_last is None or (
        (datetime.now() - datetime.strptime(cons_last, "%Y-%m-%d")).days > 45)
    if cons_stale:
        print(f"  指数成分刷新(缺失或>45天, 当前 {cons_last or '无'})...")
        dm.update_constituents()

    # 5.5) consensus weekly snapshot (E0): refresh if >9 days old — 修正动量(E4)历史积累
    cs_last = store.get_meta("last_consensus_update")
    cs_stale = cs_last is None or (
        (datetime.now() - datetime.strptime(cs_last, "%Y%m%d")).days > 9)
    if cs_stale:
        print(f"  一致预期周度快照(当前 {cs_last or '无'})...")
        dm.update_consensus()

    # 5.6) 业绩三环链 (E3): 快报+正式报 全市场入库, 周度节奏(披露季 Jul/Apr/Oct/Jan-Feb 更新鲜)
    chain_last = store.get_meta("last_chain_update")
    chain_stale = chain_last is None or (
        (datetime.now() - datetime.strptime(chain_last, "%Y%m%d")).days > 7)
    if chain_stale:
        print(f"  业绩三环链刷新·快报+正式报(当前 {chain_last or '无'})...")
        dm.update_stock_express()
        dm.update_stock_report_actual()
        store.set_meta("last_chain_update", datetime.now().strftime("%Y%m%d"))

    # 6) earnings expectation: refresh if missing, a newer complete report period exists,
    #    or every row is a silent zero (coverage=0 — the 2026-08 dead-endpoint signature).
    #    Quarterly/annual cadence — usually a no-op except around report seasons.
    want_period = dm._latest_report_period()
    have_period = store.last_earnings_period()
    earn_cov = _fetch(conn, "SELECT COUNT(DISTINCT symbol) FROM etf_earnings WHERE coverage>0")[0]
    if have_period != want_period or earn_cov == 0:
        print(f"  业绩预期更新 ({have_period or '无'} → {want_period}, 有效信号 {earn_cov}) ...")
        dm.update_etf_earnings(report_period=want_period)

    # 6) index layer (V4 tracker): broad-index daily + PE + market PB — full refresh (idempotent)
    print("  指数层刷新(daily+PE+PB)...")
    dm.update_index_daily()
    dm.update_index_pe()
    dm.update_market_pb()

    # 6.5) china money (⑪ 货币条件 · 月频): refresh if >35 days (次月中旬出新值后)
    money_last = store.get_meta("last_china_money_update")   # YYYY-MM-DD
    money_stale = money_last is None or (
        (datetime.now() - datetime.strptime(money_last, "%Y-%m-%d")).days > 35)
    if money_stale:
        print(f"  货币条件刷新·M2/M1/社融(月频, 当前 {money_last or '无'})...")
        dm.update_china_money()

    # 6.7) china rates (第七看板 国内宏观 · 利率四腿日频): refresh if >2 days
    rates_last = store.get_meta("last_china_rates_update")
    rates_stale = rates_last is None or (
        (datetime.now() - datetime.strptime(rates_last, "%Y-%m-%d")).days > 2)
    if rates_stale:
        print(f"  利率腿刷新·Shibor/FDR/LPR/中债(当前 {rates_last or '无'})...")
        dm.update_china_rates()
    # 6.8) cb balance (第七看板 · 央行资产负债表月频): refresh if >35 days
    cb_last = store.get_meta("last_cb_balance_update")
    cb_stale = cb_last is None or (
        (datetime.now() - datetime.strptime(cb_last, "%Y-%m-%d")).days > 35)
    if cb_stale:
        print(f"  央行资产负债表刷新(月频, 当前 {cb_last or '无'})...")
        dm.update_cb_balance()
    # 6.9) lgb+tsy issue (第七看板 · 政府债发行明细,社融可观测成分): refresh if >2 days
    lgb_last = store.get_meta("last_lgb_issue_update")
    lgb_stale = lgb_last is None or (
        (datetime.now() - datetime.strptime(lgb_last, "%Y-%m-%d")).days > 2)
    if lgb_stale:
        print(f"  政府债发行明细刷新·地方+国债(社融可观测成分, 当前 {lgb_last or '无'})...")
        dm.update_lgb_issue()
        dm.update_tsy_issue()
    # 6.10) china real (第七看板远期批 · 通胀/实体月度五腿): refresh if >35 days
    real_last = store.get_meta("last_china_real_update")
    real_stale = real_last is None or (
        (datetime.now() - datetime.strptime(real_last, "%Y-%m-%d")).days > 35)
    if real_stale:
        print(f"  通胀/实体月度刷新·CPI/PPI/PMI/社零/工业(当前 {real_last or '无'})...")
        dm.update_china_real()

    # 6.11) commodity daily + spot (个股层 · 17种 · A 类领先信号提速,2026-09):
    #       日线落后基准交易日才拉;快照每日一次(盘前首跑=昨夜夜盘收盘价 → 看板隔夜变动列)
    comm_last = store.get_meta("last_commodity_update")
    if comm_last is None or comm_last < target:
        print(f"  商品价日线刷新·17种(当前 {comm_last or '无'} → {target})...")
        dm.update_commodity_price()
    spot_last = store.get_meta("last_commodity_spot_update")
    if spot_last != datetime.now().strftime("%Y-%m-%d"):
        print(f"  商品实时快照·夜盘隔夜(当前 {spot_last or '无'})...")
        dm.update_commodity_spot()
    # 6.12) commodity intl benchmarks + ccidx index (第八看板 大宗商品 · 2026-09):
    #       国际基准(LME/COMEX/CBOT,写 western_macro_series fut)+ 中证商品指数(官方总览);
    #       落后基准交易日才拉(外盘日历≠A股日历,多拉幂等无害)
    bench_last = store.get_meta("last_commodity_benchmark_update")
    if bench_last is None or bench_last < target:
        print(f"  商品国际基准刷新·LME/COMEX/CBOT(当前 {bench_last or '无'} → {target})...")
        dm.update_commodity_benchmarks()
    cidx_last = store.get_meta("last_commodity_index_update")
    if cidx_last is None or cidx_last < target:
        print(f"  中证商品指数刷新·官方总览(当前 {cidx_last or '无'} → {target})...")
        dm.update_commodity_index()
    # 6.13) commodity investable targets NAV (第八看板 🎫 投资标的映射 · 二期 2026-09):
    #       非池内标的的净值腿(池内标的随研究看板 NAV 腿走不重复拉);QDII NAV 滞后 1-2 天,
    #       天天判 stale 也无害(几只标的、增量拉,幂等)
    tgt_specs = (get_config().params.get("commodity") or {}).get("targets") or []
    tgt_pool = set(get_config().tracked_symbols())
    tgt_syms = [str(s["symbol"]) for s in tgt_specs
                if str(s.get("symbol", "")) and str(s["symbol"]) not in tgt_pool]
    tgt_stale = [s for s in tgt_syms if (store.last_nav_date(s) or "") < (target or "9999-12-31")]
    if tgt_stale:
        print(f"  商品标的净值刷新·投资映射({len(tgt_stale)}/{len(tgt_syms)} 落后)...")
        dm.update_etf_nav(tgt_stale)
    # 6.14) commodity basis + inventory (第八看板 🔬 基差/期限/库存 · 二期剩余 2026-09):
    #       基差=100ppi 日更(增量半年窗,秒级);库存=CZCE 周采样(次一个周三才有新数据,>5天才拉)
    basis_last = store.get_meta("last_commodity_basis_update")
    if basis_last is None or basis_last < target:
        print(f"  基差+期限结构刷新·100ppi(当前 {basis_last or '无'} → {target})...")
        dm.update_commodity_basis()
    inv_last = store.get_meta("last_commodity_inventory_update")
    if inv_last is None or (datetime.now() - datetime.strptime(inv_last, "%Y-%m-%d")).days > 5:
        print(f"  仓单刷新·周采样(当前 {inv_last or '无'})...")
        dm.update_commodity_inventory()
    # 6.15) fut_mapping 换月映射+逐合约收盘(批次2.4 展期口径·event-study 前向收益;>35天门控月更节奏)
    roll_last = store.get_meta("last_fut_rollover_update")
    if roll_last is None or (datetime.now() - datetime.strptime(roll_last, "%Y-%m-%d")).days > 35:
        print(f"  换月映射+逐合约刷新(当前 {roll_last or '无'})...")
        dm.update_fut_rollover()

    # 7-9) candidate-pool legs (V8 高业绩池·2026-09 重启): spot/行业/分红/日线/资产负债
    #      (2026-08-29 暂停已解除——看板重写为高业绩池, 数据更新随本批恢复; --no-pool 可再停)
    if args.no_pool:
        print("  候选个股池数据更新跳过(--no-pool)")
    else:
        # 7) candidate-pool spot: daily snapshot — universe 的 ST 过滤 + 展示名 + 市值/估值列
        spot_last = store.get_meta("last_stock_spot_update")
        if spot_last != datetime.now().strftime("%Y-%m-%d"):
            print(f"  候选池现货快照(当前 {spot_last or '无'})...")
            n = dm.update_stock_spot()
            print(f"  stock_spot: {n} 只" if n else "  ⚠️ spot 失败(退最后快照,不阻塞)")

        # 7.5) 正式报扩列补拉: 最新 2 期 np_abs 缺 → 重拉带扩列(TTM/PE 原料)
        with store._conn() as _c:  # noqa: SLF001 — 维护查询
            _latest2 = [r[0] for r in _c.execute(
                "SELECT report_period FROM stock_report_actual GROUP BY report_period "
                "ORDER BY report_period DESC LIMIT 2")]
            _n_ext = {r[0]: r[1] for r in _c.execute(
                "SELECT report_period, COUNT(*) FROM stock_report_actual "
                "WHERE np_abs IS NOT NULL GROUP BY report_period")}
        if _latest2 and any(_n_ext.get(p, 0) < 500 for p in _latest2):
            print(f"  正式报扩列补拉(np_abs 缺, 期 {_latest2})...")
            dm.update_stock_report_actual(periods=_latest2)

        # 8) industry (月更) + balance (季更·随披露环) + dividends (周更)
        ind_last = store.last_industry_snapshot()
        if ind_last is None or (datetime.now() - datetime.strptime(ind_last, "%Y-%m-%d")).days > INDUSTRY_STALE_DAYS:
            print(f"  候选池行业成分(缺失或>{INDUSTRY_STALE_DAYS}天, 当前 {ind_last or '无'})...")
            n = dm.update_industry_members()
            print(f"  industry_member: {n} 行" if n else "  ⚠️ industry 失败(PB 轨降级,不阻塞)")
        bal_last = store.get_meta("last_stock_balance_update")
        if bal_last is None or (datetime.now() - datetime.strptime(bal_last, "%Y-%m-%d")).days > 35:
            print(f"  资产负债表整表(缺失或>35天, 当前 {bal_last or '无'})...")
            res = dm.update_stock_balance()
            print(f"  stock_balance: {sum(res.values())} 行"
                  if any(res.values()) else "  ⚠️ 资产负债失败(风险旗降级,不阻塞)")
        if info.get("pool_universe") and store.get_meta("pool_prices_ready") == "1":
            div_days = int((get_config().params.get("stock_pool", {}) or {})
                           .get("prices", {}).get("dividend_refresh_days", 7))
            div_last = store.get_meta("last_pool_dividend_update")  # YYYY-MM-DD
            stale = div_last is None or (
                datetime.now() - datetime.strptime(div_last, "%Y-%m-%d")).days > div_days
            if stale:
                print(f"  候选池分红明细(当前 {div_last or '无'})...")
                res = dm.update_pool_dividends(info["pool_universe"])
                print(f"  stock_dividend: +{sum(res.values())} 行")

        # 9) candidate-pool prices (日更·大头): gate=冷启动已 ready;只补 universe 内落后者。
        #    放最后一步——失败/超时不遮蔽其余修复;增量游标次日自愈。
        if store.get_meta("pool_prices_ready") == "1" and info.get("pool_universe"):
            years = int((get_config().params.get("stock_pool", {}) or {})
                        .get("prices", {}).get("history_years", 3))
            stale_codes = [c for c in info["pool_universe"]
                           if (store.last_date(c) or "") < (target or "9999-12-31")]
            if stale_codes:
                # 数据源优先级: tushare 整表聚合腿优先(~1min), 残余/失败退逐股 sina
                print(f"  候选池日线·tushare 整表优先({len(stale_codes)} 只落后)...")
                n_mkt = dm.update_stock_daily_market()
                still = [c for c in stale_codes
                         if (store.last_date(c) or "") < (target or "9999-12-31")]
                print(f"  整表腿: +{n_mkt} 行, 残余 {len(still)} 只")
                if still:
                    print(f"  逐股增量({len(still)} 只,~{len(still) * 1.2 / 60:.0f}min)...")
                    res = dm.update_stock_daily(still, history_years=years)
                    store.set_meta("last_pool_price_update",
                                   datetime.now().strftime("%Y-%m-%d"))
                    print(f"  候选池日线: +{sum(res.values())} 行")
        elif store.get_meta("pool_prices_ready") != "1":
            print("  候选池日线: 未冷启动(不阻塞) — python scripts/backfill_stock_pool.py --all")

    print("\n=== 补齐后复查 ===")
    report(conn, cfg, syms, store=store)


if __name__ == "__main__":
    main()
