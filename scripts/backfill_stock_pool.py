"""候选个股池数据腿回填 (V8 pool · 高业绩池 · 幂等,可断点续跑;2026-09 重写)。

  spot      全市场现货快照(单调用,日更;含市值/PE/PB 列)→ stock_spot   [universe+市值列]
  industry  东财行业板块成分(月更)→ industry_member [类型分流: PEG 轨/PB 轨]
  report    正式报整表带 V8 扩列(eps/bvps/np_abs/rev_abs,16 期深回填)→ stock_report_actual
            [TTM/PE 自算 + PB 分位轨原料;旧 8 期也要重拉一次以补扩列]
  balance   资产负债表整表(zcfz,16 期)→ stock_balance [风险筛: 应收/存贷双高/净资产分母]
  prices    宇宙日线(增量游标,日更)→ daily_prices   [PB 分位/回放的价格底座]
  dividends 宇宙分红明细(sina,周更)→ stock_dividend [运行时前复权事件源]
  survivors 过地板幸存者 sina 精筛腿(扣非/商誉全历史,一次调用终身缓存)→ stock_financials

冷启动 `--all` 顺序: spot → industry → report → balance → prices(~2-4h 大头,16 期股更全)
→ dividends → survivors(自动:跑一次装配拿幸存者清单再逐股拉)。
完成后置 meta pool_prices_ready=1(dashboard_data_check --fix 的日更门)。

Usage:
  python scripts/backfill_stock_pool.py --all                     # 冷启动全跑(或断点续跑)
  python scripts/backfill_stock_pool.py --spot                    # 仅现货快照
  python scripts/backfill_stock_pool.py --industry                # 仅行业成分(跑完打印未映射板块)
  python scripts/backfill_stock_pool.py --report [--periods 16]   # 正式报扩列深回填
  python scripts/backfill_stock_pool.py --balance [--periods 16]  # 资产负债表
  python scripts/backfill_stock_pool.py --prices                  # 仅日线(默认 universe;--codes 覆盖)
  python scripts/backfill_stock_pool.py --dividends               # 仅分红
  python scripts/backfill_stock_pool.py --survivors               # 幸存者 sina 精筛腿
  python scripts/backfill_stock_pool.py --codes 600519,000001 --prices   # 指定代码
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.pool import universe as uni
from stockagent.utils.logging_setup import setup_logging

CHUNK = 100  # prices 分块粒度:进度打印 + 崩溃后重跑只丢当前块
READY_FRAC = 0.95  # universe 中 last_date 距今 ≤10 天的比例达此值 → pool_prices_ready
DEEP_PERIODS = 16  # report/balance 深回填期数(4 年;TTM 需上年同期+年报)


def _universe_codes(dm: DataManager) -> tuple[list[str], int]:
    """从 store 推导 universe codes(全市场 spot 非 ST)。返回 (codes, spot 原始数)。"""
    spot = dm.store.latest_stock_spot()
    cfg = (dm.config.params.get("stock_pool", {}) or {}).get("universe", {}) or {}
    df = uni.derive_universe(spot, exclude_prefixes=tuple(
        cfg.get("exclude_name_prefixes", uni.EXCLUDED_NAME_PREFIXES)))
    return [str(c) for c in df.index], len(spot)


def _unmapped_report(dm: DataManager) -> None:
    """行业板块 vs stock_industry.yaml 的未映射 diff(带成分数)——维护口粮。"""
    cfg = dm.config.industry_class()
    boards = dm.store.industry_boards()
    if not boards:
        return
    unmapped = []
    for b in boards:
        if b not in cfg:
            with dm.store._conn() as c:  # noqa: SLF001 — 维护打印,不值得为此加 Store 方法
                n = c.execute("SELECT COUNT(*) FROM industry_member WHERE industry=?", (b,)).fetchone()[0]
            unmapped.append((b, n))
    if unmapped:
        print(f"  ⚠️ 未映射板块 {len(unmapped)} 个(补进 config/stock_industry.yaml 即入对应轨):")
        for b, n in sorted(unmapped, key=lambda x: -x[1]):
            print(f"     {b}({n} 只)")
    else:
        print("  板块映射全覆盖 ✔")


def _step_prices(dm: DataManager, codes: list[str]) -> None:
    years = int((dm.config.params.get("stock_pool", {}) or {}).get("prices", {})
                .get("history_years", 3))
    print(f"  日线回填: {len(codes)} 只 × {years} 年历史, 分块 {CHUNK}(增量游标,断点续跑)...")
    t0 = datetime.now()
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        res = dm.update_stock_daily(chunk, history_years=years)
        done = min(i + CHUNK, len(codes))
        el = (datetime.now() - t0).total_seconds()
        eta = el / max(done, 1) * (len(codes) - done)
        print(f"  prices {done}/{len(codes)} (+{sum(res.values())} 行, "
              f"{el/60:.1f}min, ETA {eta/60:.1f}min)")
    dm.store.set_meta("last_pool_price_update", datetime.now().strftime("%Y-%m-%d"))
    cutoff = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    fresh = sum(1 for c in codes if (dm.store.last_date(c) or "") >= cutoff)
    frac = fresh / max(len(codes), 1)
    if frac >= READY_FRAC:
        dm.store.set_meta("pool_prices_ready", "1")
        print(f"  pool_prices_ready=1 ({fresh}/{len(codes)} = {frac:.0%} 数据 ≤10 天内)")
    else:
        print(f"  ⚠️ 尚未达 ready 门({fresh}/{len(codes)} = {frac:.0%} < {READY_FRAC:.0%});"
              f"重跑 --prices 续跑缺口")


def _step_survivors(dm: DataManager) -> None:
    """跑一次装配拿过地板幸存者 → 逐股拉 sina(扣非/商誉全历史,幂等缓存)。
    幸存者清单随披露环滚动;已拉的股下次自动跳过(sina 全量返回,upsert 幂等)。"""
    from stockagent.pool.screen import build_high_earnings_snapshot
    print("  装配快照推导幸存者清单(只读)...")
    snap = build_high_earnings_snapshot(dm.store, config=dm.config, persist=False)
    codes = snap.get("sina_needed") or []
    all_pass = snap.get("n_floor_pass", 0)
    print(f"  过地板 {all_pass} 只,其中 sina 缺 {len(codes)} 只(逐股拉,~{len(codes) * 2.0 / 60:.0f}min)...")
    if codes:
        dm.update_stock_financials(codes)
        dm.store.set_meta("last_pool_sina_update", datetime.now().strftime("%Y-%m-%d"))
    else:
        print("  sina 精筛腿已齐 ✔")


def main():
    ap = argparse.ArgumentParser(description="候选个股池数据腿回填(幂等)")
    ap.add_argument("--spot", action="store_true", help="全市场现货快照→stock_spot")
    ap.add_argument("--industry", action="store_true", help="东财行业板块成分→industry_member")
    ap.add_argument("--report", action="store_true", help="正式报扩列深回填(eps/bvps/np_abs/rev_abs)")
    ap.add_argument("--balance", action="store_true", help="资产负债表整表→stock_balance")
    ap.add_argument("--prices", action="store_true", help="宇宙日线→daily_prices(增量)")
    ap.add_argument("--dividends", action="store_true", help="宇宙分红→stock_dividend")
    ap.add_argument("--survivors", action="store_true", help="幸存者 sina 精筛腿(扣非/商誉)")
    ap.add_argument("--all", action="store_true",
                    help="冷启动全跑(spot→industry→report→balance→prices→dividends→survivors)")
    ap.add_argument("--periods", type=int, default=DEEP_PERIODS,
                    help=f"report/balance 深回填期数(默认 {DEEP_PERIODS})")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔代码(默认 universe 推导;prices/dividends/survivors 受用)")
    ap.add_argument("--codes-file", type=str, default="", help="代码清单文件(每行一个;冷启动期 spot 未到时的临时宇宙)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    do_prices = args.prices or args.all
    do_divs = args.dividends or args.all
    if not (args.spot or args.industry or args.report or args.balance
            or do_prices or do_divs or args.survivors or args.all):
        ap.print_help()
        return

    if args.spot or args.all:
        print("=== spot 现货快照(日更·含市值/估值列) ===")
        n = dm.update_stock_spot()
        print(f"  stock_spot: {n} 只" if n else "  ⚠️ spot 失败(universe 将退最后快照)")

    if args.industry or args.all:
        print("=== industry 行业成分(月更) ===")
        n = dm.update_industry_members()
        print(f"  industry_member: {n} 行" if n else "  ⚠️ industry 失败(PB 轨降级,不阻塞)")
        _unmapped_report(dm)

    if args.report or args.all:
        print(f"=== report 正式报扩列深回填({args.periods} 期) ===")
        from stockagent.data.manager import _recent_report_periods
        deep = _recent_report_periods(args.periods)
        res = dm.update_stock_report_actual(periods=deep)
        print(f"  stock_report_actual(含扩列): {sum(res.values())} 行/{len(res)} 期")

    if args.balance or args.all:
        print(f"=== balance 资产负债表整表({args.periods} 期) ===")
        from stockagent.data.manager import _recent_report_periods
        deep = _recent_report_periods(args.periods)
        res = dm.update_stock_balance(periods=deep)
        print(f"  stock_balance: {sum(res.values())} 行/{len(res)} 期"
              if any(res.values()) else "  ⚠️ 全期失败(风险旗降级,不阻塞)")

    codes = None
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    elif args.codes_file:
        codes = [ln.strip() for ln in Path(args.codes_file).read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        print(f"=== 代码清单 {args.codes_file}: {len(codes)} 只(临时宇宙,spot 到位后 --all 补差) ===")
    if do_prices:
        if codes is None:
            print("=== universe 推导 ===")
            codes, spot_n = _universe_codes(dm)
            print(f"  universe: {len(codes)} 只(spot 非 ST;原始 {spot_n})")
            if len(codes) < 500:
                print("  ⚠️ universe 过小(spot 缺?)——先跑 --spot")
                return
        print("=== prices 日线(日更·大头) ===")
        _step_prices(dm, codes)

    if do_divs:
        if codes is None:
            codes, _ = _universe_codes(dm)
        print(f"=== dividends 分红明细({len(codes)} 只,运行时前复权事件源) ===")
        res = dm.update_pool_dividends(codes)
        print(f"  stock_dividend: {sum(res.values())} 行(无分红记录的 0 行正常)")

    if args.survivors or args.all:
        print("=== survivors sina 精筛腿(扣非/商誉) ===")
        _step_survivors(dm)

    print("\n提示: 生成看板 → python scripts/stock_pool_report.py · 验证器 → python scripts/validate_high_earnings_pool.py")


if __name__ == "__main__":
    main()
