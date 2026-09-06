"""大宗商品看板数据回填 CLI — 第八看板新增腿(2026-09)。

覆盖**新增**三腿:国际基准(LME/COMEX/CBOT,写 western_macro_series source='fut')+
中证商品指数(官方总览,写 commodity_index)+ 投资标的 NAV(二期·非池内标的→etf_nav;
池内标的随研究看板 NAV 腿走不重复拉)。国内 17 品种日线/夜盘快照的回填归属不动,
仍在 `backfill_stock_data.py --comm`(幂等,冷启动便利可一并跑)。

Usage:
  python scripts/backfill_commodity.py              # 全部(基准 + 指数 + 标的NAV,~1-2min)
  python scripts/backfill_commodity.py --bench      # 只国际基准
  python scripts/backfill_commodity.py --index      # 只中证商品指数
  python scripts/backfill_commodity.py --targets    # 只投资标的 NAV(非池内:有色期货/能化/豆粕/白银LOF/南方原油)
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
    ap = argparse.ArgumentParser(description="大宗商品看板数据回填(国际基准+官方指数+标的NAV,幂等)")
    ap.add_argument("--bench", action="store_true", help="只国际基准(LME/COMEX/CBOT → western_macro_series fut)")
    ap.add_argument("--index", action="store_true", help="只中证商品指数(→ commodity_index)")
    ap.add_argument("--targets", action="store_true", help="只投资标的 NAV(非池内标的 → etf_nav)")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    dm = DataManager(config=cfg)
    all_three = not (args.bench or args.index or args.targets)

    if args.bench or all_three:
        per = dm.update_commodity_benchmarks()
        if per:
            print("  国际基准:")
            for sym, last in sorted(per.items()):
                print(f"    {sym:4} → {last}")
        else:
            print("  ⚠ 国际基准拉取失败(重跑即可)")
    if args.index or all_three:
        res = dm.update_commodity_index()
        for nm, n in res.items():
            print(f"  {nm}: +{n} 行")
        if not res:
            print("  ⚠ 指数拉取失败(重跑即可)")
    if args.targets or all_three:
        specs = (cfg.params.get("commodity") or {}).get("targets") or []
        pool = set(cfg.tracked_symbols())
        syms = [str(s["symbol"]) for s in specs
                if str(s.get("symbol", "")) and str(s["symbol"]) not in pool]
        if not syms:
            print("  投资标的 NAV:无待拉标的(全部在池,随研究看板 NAV 腿走)")
        else:
            res = dm.update_etf_nav(syms)
            ok = {k: v for k, v in res.items() if v}
            print(f"  投资标的 NAV: {len(ok)}/{len(syms)} 只有增量"
                  + (f" {ok}" if ok else " ⚠ 全部失败(重跑即可)"))


if __name__ == "__main__":
    main()
