"""大宗商品看板数据回填 CLI — 第八看板新增腿(2026-09)。

只覆盖**新增**两腿:国际基准(LME/COMEX/CBOT,写 western_macro_series source='fut')+
中证商品指数(官方总览,写 commodity_index)。国内 17 品种日线/夜盘快照的回填归属不动,
仍在 `backfill_stock_data.py --comm`(幂等,冷启动便利可一并跑)。

Usage:
  python scripts/backfill_commodity.py            # 全部(基准 + 指数,~1min)
  python scripts/backfill_commodity.py --bench    # 只国际基准
  python scripts/backfill_commodity.py --index    # 只中证商品指数
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser(description="大宗商品看板数据回填(国际基准+官方指数,幂等)")
    ap.add_argument("--bench", action="store_true", help="只国际基准(LME/COMEX/CBOT → western_macro_series fut)")
    ap.add_argument("--index", action="store_true", help="只中证商品指数(→ commodity_index)")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    dm = DataManager(config=cfg)

    if args.bench or not (args.bench or args.index):
        per = dm.update_commodity_benchmarks()
        if per:
            print("  国际基准:")
            for sym, last in sorted(per.items()):
                print(f"    {sym:4} → {last}")
        else:
            print("  ⚠ 国际基准拉取失败(重跑即可)")
    if args.index or not (args.bench or args.index):
        res = dm.update_commodity_index()
        for nm, n in res.items():
            print(f"  {nm}: +{n} 行")
        if not res:
            print("  ⚠ 指数拉取失败(重跑即可)")


if __name__ == "__main__":
    main()
