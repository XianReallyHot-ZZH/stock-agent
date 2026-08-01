"""Backfill / update 个股层数据 (V5 tracker · Phase 2 C0 + C0.5).

  C0:  个股日线 (sina stock_zh_a_daily) → daily_prices
       个股估值 (百度 stock_zh_valuation_baidu, PE/PB/总市值 等 5 指标, IPO 起) → stock_valuation
  C0.5: 个股财报 (sina stock_financial_abstract 常用指标 17 项, ~25 年) → stock_financials
       个股分红 (sina stock_history_dividend_detail, 实施) → stock_dividend
  C0.6: 个股业绩预告 (eastmoney stock_yjyg_em, 最近 8 期) → stock_forecast (S08-G1 预告链)
幂等——日线按增量游标、其余全量 upsert(每次返回全历史),重跑无副作用。

默认观察池 = DataManager.STOCK_WATCHLIST(5 只,覆盖 4 交易所 + 三类风格,C1 会迁到 stock_pool.yaml)。

Usage:
  python scripts/backfill_stock_data.py                          # 观察池全量(日线+估值+财报+分红+预告)
  python scripts/backfill_stock_data.py --daily                  # 仅日线
  python scripts/backfill_stock_data.py --val                    # 仅估值(5 指标)
  python scripts/backfill_stock_data.py --fin                    # 仅财报(17 指标)
  python scripts/backfill_stock_data.py --div                    # 仅分红
  python scripts/backfill_stock_data.py --forecast               # 仅业绩预告(最近8期,全市场面板过滤)
  python scripts/backfill_stock_data.py --codes 600519,000001    # 指定个股
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.data.fetcher import STOCK_VALUATION_INDICATORS, STOCK_FINANCIAL_METRICS
from stockagent.utils.logging_setup import setup_logging

_YI = 1e8  # 财报营收/利润 原始元 → 亿元


def _summary(dm: DataManager, codes: list[str]):
    store = dm.store
    print("\n=== 个股层数据覆盖 ===")
    for code in codes:
        # 日线(daily_prices 复用)
        dp = store.get_series(code)
        if len(dp):
            print(f"  日线 {code}: {len(dp)} 行, {dp.index.min()}..{dp.index.max()} | "
                  f"最新close={float(dp['close'].iloc[-1]):.2f}")
        else:
            print(f"  日线 {code}: (无)")
        # 估值(stock_valuation,5 指标)
        for ind in STOCK_VALUATION_INDICATORS:
            v = store.get_stock_valuation_series(code, ind)
            if len(v):
                print(f"  {ind:<12} {code}: {len(v)} 行, {v.index.min()}..{v.index.max()} | "
                      f"最新={float(v['value'].iloc[-1]):.2f}")
            else:
                print(f"  {ind:<12} {code}: (无)")
        # 财报(stock_financials 长表 → 宽表面板,取最新报告期关键指标)
        pnl = store.get_stock_financials_panel(code, ["revenue", "net_profit", "np_deducted",
                                                      "eps", "roe", "gross_margin"])
        if len(pnl):
            last = pnl.index[-1]
            r = pnl.loc[last]
            rev = float(r["revenue"]) / _YI if r.get("revenue") is not None else float("nan")
            np_ = float(r["net_profit"]) / _YI if r.get("net_profit") is not None else float("nan")
            eps = float(r["eps"]) if r.get("eps") is not None else float("nan")
            print(f"  财报 {code}: {len(pnl)} 期, ..{last} | "
                  f"营收={rev:.0f}亿 归母={np_:.0f}亿 EPS={eps:.2f}")
        else:
            print(f"  财报 {code}: (无)")
        # 分红(stock_dividend)
        dv = store.get_stock_dividend_series(code)
        if len(dv):
            print(f"  分红 {code}: {len(dv)} 次, {dv.index.min()}..{dv.index.max()} | "
                  f"最近每股现金={float(dv['cash_per_share'].iloc[-1]):.3f}元")
        else:
            print(f"  分红 {code}: (无)")
        # 业绩预告(stock_forecast,稀疏)
        fc = store.get_stock_forecast_series(code)
        if len(fc):
            last = fc.iloc[-1]
            print(f"  预告 {code}: {len(fc)} 期, ..{fc.index[-1]} | "
                  f"最近 {last.get('type','?')} yoy={last.get('yoy'):.0f}% 公告 {last.get('announce_date','?')}")
        else:
            print(f"  预告 {code}: (无)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily", action="store_true", help="仅个股日线(→daily_prices)")
    ap.add_argument("--val", action="store_true", help="仅个股估值(→stock_valuation, 5 指标)")
    ap.add_argument("--fin", action="store_true", help="仅个股财报(→stock_financials, 17 指标)")
    ap.add_argument("--div", action="store_true", help="仅个股分红(→stock_dividend)")
    ap.add_argument("--forecast", action="store_true", help="仅业绩预告(→stock_forecast, 最近8期)")
    ap.add_argument("--comm", action="store_true", help="回填商品现货价(周期上游领先·碳酸锂/铜/螺纹钢/黄金/原油)")
    ap.add_argument("--codes", type=str, default="",
                    help="逗号分隔的 6 位个股代码(默认 STOCK_WATCHLIST)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    dm = DataManager(config=cfg)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()] or DataManager.STOCK_WATCHLIST

    selective = args.daily or args.val or args.fin or args.div or args.forecast or args.comm
    if args.daily or not selective:
        dm.update_stock_daily(codes)
    if args.val or not selective:
        dm.update_stock_valuation(codes)
    if args.fin or not selective:
        dm.update_stock_financials(codes)
    if args.div or not selective:
        dm.update_stock_dividend(codes)
    if args.forecast or not selective:
        dm.update_stock_forecasts(codes)
    if args.comm:
        dm.update_commodity_price()

    _summary(dm, codes)


if __name__ == "__main__":
    main()
