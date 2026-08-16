"""候选个股池数据腿回填 (V7 pool · 第六看板 · 幂等,可断点续跑)。

  spot      全市场现货快照(单调用,日更)→ stock_spot   [universe ST 过滤 + 展示名]
  industry  东财行业板块成分(~86 板块,月更)→ industry_member [策略2 三类分流]
  prices    覆盖池日线(增量游标,日更·~2300 只)→ daily_prices   [全部策略的价格底座]
  dividends 覆盖池分红明细(sina,周更)→ stock_dividend [pool/prices.py 运行时前复权事件源]

冷启动 `--all` 顺序: spot → consensus(缺才拉) → industry → prices(~1.5-3h 大头,
3 年历史,增量游标天然断点续跑) → dividends(~20-30min) → 全市场预告面板(缺才拉)。
完成后置 meta pool_prices_ready=1(dashboard_data_check --fix 的日更门)。

Usage:
  python scripts/backfill_stock_pool.py --all                # 冷启动全跑(或断点续跑)
  python scripts/backfill_stock_pool.py --spot               # 仅现货快照
  python scripts/backfill_stock_pool.py --industry           # 仅行业成分(跑完打印未映射板块)
  python scripts/backfill_stock_pool.py --prices             # 仅日线(默认 universe;--codes 覆盖)
  python scripts/backfill_stock_pool.py --dividends          # 仅分红
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


def _universe_codes(dm: DataManager) -> tuple[list[str], int]:
    """从 store 推导 universe codes(consensus 最新快照 ∩ spot 最新快照)。返回 (codes, 原始数)。"""
    _, cons = dm.store.get_consensus_snapshot()
    spot = dm.store.latest_stock_spot()
    cfg = (dm.config.params.get("stock_pool", {}) or {}).get("universe", {}) or {}
    df = uni.derive_universe(cons, spot, min_reports=int(cfg.get("min_reports", 3)),
                             exclude_prefixes=tuple(cfg.get("exclude_name_prefixes",
                                                            uni.EXCLUDED_NAME_PREFIXES)))
    return [str(c) for c in df.index], len(cons)


def _ensure_consensus(dm: DataManager) -> None:
    """consensus 快照缺失时拉一份(universe 的前提;E0 周度由 --fix 维护,这里只兜冷启动)。"""
    date, _ = dm.store.get_consensus_snapshot()
    if date:
        print(f"  consensus 快照已有 {date}(跳过;周度由 dashboard_data_check --fix 维护)")
        return
    print("  consensus 快照缺失,拉取整表(~1.4s)...")
    n = dm.update_consensus()
    print(f"  consensus: {n} 只")


def _unmapped_report(dm: DataManager) -> None:
    """行业板块 vs stock_industry.yaml 的未映射 diff(带成分数)——维护口粮。"""
    cfg = dm.config.industry_class()
    boards = dm.store.industry_boards()
    if not boards:
        return
    unmapped = []
    for b in boards:
        if b not in cfg:
            # 成分数:industry_member 里该板块行数
            with dm.store._conn() as c:  # noqa: SLF001 — 维护打印,不值得为此加 Store 方法
                n = c.execute("SELECT COUNT(*) FROM industry_member WHERE industry=?", (b,)).fetchone()[0]
            unmapped.append((b, n))
    if unmapped:
        print(f"  ⚠️ 未映射板块 {len(unmapped)} 个(补进 config/stock_industry.yaml 即入策略2):")
        for b, n in sorted(unmapped, key=lambda x: -x[1]):
            print(f"     {b}({n} 只)")
    else:
        print("  板块映射全覆盖 ✔")


def _step_prices(dm: DataManager, codes: list[str]) -> None:
    years = int((dm.config.params.get("stock_pool", {}) or {}).get("prices", {})
                .get("history_years", 3))
    print(f"  日线回填: {len(codes)} 只 × {years} 年历史, 分块 {CHUNK}(增量游标,断点续跑)...")
    t0 = datetime.now()
    total_rows = 0
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        res = dm.update_stock_daily(chunk, history_years=years)
        total_rows += sum(res.values())
        done = min(i + CHUNK, len(codes))
        el = (datetime.now() - t0).total_seconds()
        eta = el / max(done, 1) * (len(codes) - done)
        print(f"  prices {done}/{len(codes)} (+{sum(res.values())} 行, "
              f"{el/60:.1f}min, ETA {eta/60:.1f}min)")
    dm.store.set_meta("last_pool_price_update", datetime.now().strftime("%Y-%m-%d"))
    # ready 门:universe 中足够多的股票已有近期数据
    cutoff = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    fresh = sum(1 for c in codes if (dm.store.last_date(c) or "") >= cutoff)
    frac = fresh / max(len(codes), 1)
    if frac >= READY_FRAC:
        dm.store.set_meta("pool_prices_ready", "1")
        print(f"  pool_prices_ready=1 ({fresh}/{len(codes)} = {frac:.0%} 数据 ≤10 天内)")
    else:
        print(f"  ⚠️ 尚未达 ready 门({fresh}/{len(codes)} = {frac:.0%} < {READY_FRAC:.0%});"
              f"重跑 --prices 续跑缺口")


def main():
    ap = argparse.ArgumentParser(description="候选个股池数据腿回填(幂等)")
    ap.add_argument("--spot", action="store_true", help="全市场现货快照→stock_spot")
    ap.add_argument("--industry", action="store_true", help="东财行业板块成分→industry_member")
    ap.add_argument("--prices", action="store_true", help="覆盖池日线→daily_prices(增量)")
    ap.add_argument("--dividends", action="store_true", help="覆盖池分红→stock_dividend")
    ap.add_argument("--all", action="store_true", help="冷启动全跑(spot→consensus兜底→industry→prices→dividends)")
    ap.add_argument("--codes", type=str, default="", help="逗号分隔代码(默认 universe 推导;仅 prices/dividends 受用)")
    ap.add_argument("--codes-file", type=str, default="", help="代码清单文件(每行一个;冷启动期 spot 未到时的临时宇宙)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    do_prices = args.prices or args.all
    do_divs = args.dividends or args.all
    if not (args.spot or args.industry or do_prices or do_divs):
        ap.print_help()
        return

    if args.spot or args.all:
        print("=== spot 现货快照(日更) ===")
        n = dm.update_stock_spot()
        print(f"  stock_spot: {n} 只" if n else "  ⚠️ spot 失败(universe 将退最后快照)")

    if args.all:
        print("=== consensus 兜底 ===")
        _ensure_consensus(dm)

    if args.industry or args.all:
        print("=== industry 行业成分(月更) ===")
        n = dm.update_industry_members()
        print(f"  industry_member: {n} 行" if n else "  ⚠️ industry 失败(策略2 降级,其余策略不受影响)")
        _unmapped_report(dm)

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
            codes, cons_n = _universe_codes(dm)
            print(f"  universe: {len(codes)} 只(consensus 原始 {cons_n};spot 过滤后)")
            if len(codes) < 50:
                print("  ⚠️ universe 过小(consensus/spot 缺?)——先跑 --spot + consensus 兜底")
                return
        print("=== prices 日线(日更·大头) ===")
        _step_prices(dm, codes)

    if do_divs:
        if codes is None:
            codes, _ = _universe_codes(dm)
        print(f"=== dividends 分红明细({len(codes)} 只,运行时前复权事件源) ===")
        res = dm.update_pool_dividends(codes)
        print(f"  stock_dividend: {sum(res.values())} 行(无分红记录的 0 行正常)")

    print("\n提示: 生成看板 → python scripts/stock_pool_report.py")


if __name__ == "__main__":
    main()
