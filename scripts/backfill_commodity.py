"""大宗商品看板数据回填 CLI — 第八看板新增腿(2026-09)。

覆盖**新增**三腿:国际基准(LME/COMEX/CBOT,写 western_macro_series source='fut')+
中证商品指数(官方总览,写 commodity_index)+ 投资标的 NAV(二期·非池内标的→etf_nav;
池内标的随研究看板 NAV 腿走不重复拉)。国内 17 品种日线/夜盘快照的回填归属不动,
仍在 `backfill_stock_data.py --comm`(幂等,冷启动便利可一并跑)。

Usage:
  python scripts/backfill_commodity.py              # 全部(基准+指数+标的NAV+基差+仓单)
  python scripts/backfill_commodity.py --bench      # 只国际基准
  python scripts/backfill_commodity.py --index      # 只中证商品指数
  python scripts/backfill_commodity.py --targets    # 只投资标的 NAV(非池内:有色期货/能化/豆粕/白银LOF/南方原油)
  python scripts/backfill_commodity.py --basis      # 只基差+期限结构(首次全史 ~11min,之后增量)
  python scripts/backfill_commodity.py --inv        # 只仓单·周采样(CZCE 总计口径+tushare fut_wsr 八品种;首次 ~8min)
  python scripts/backfill_commodity.py --inv --refill  # CZCE 腿全量重灌(2026-09-13 3× 口径修正用,一次性)
  python scripts/backfill_commodity.py --roll       # 只主力换月映射+逐合约收盘(批次2.4 展期口径;首次 ~5min)
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
    ap = argparse.ArgumentParser(description="大宗商品看板数据回填(国际基准+官方指数+标的NAV+基差/库存,幂等)")
    ap.add_argument("--bench", action="store_true", help="只国际基准(LME/COMEX/CBOT → western_macro_series fut)")
    ap.add_argument("--index", action="store_true", help="只中证商品指数(→ commodity_index)")
    ap.add_argument("--targets", action="store_true", help="只投资标的 NAV(非池内标的 → etf_nav)")
    ap.add_argument("--basis", action="store_true",
                    help="只基差+期限结构(100ppi→commodity_basis;首次全史 2019起 ~11min,之后增量秒级)")
    ap.add_argument("--inv", action="store_true",
                    help="只仓单周采样(CZCE FG/SA/UR 总计口径 + fut_wsr CU/AL/ZN/RB/AU/AG/SC/LC→commodity_inventory;首次 2019起 ~8min)")
    ap.add_argument("--refill", action="store_true",
                    help="配合 --inv:CZCE 腿全量重灌(2026-09-13 小计/总计 3× 口径修正,一次性)")
    ap.add_argument("--roll", action="store_true",
                    help="只主力换月映射+逐合约收盘(批次2.4 展期口径→fut_mapping/fut_contract_daily;event-study 前向收益用)")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    dm = DataManager(config=cfg)
    all_legs = not (args.bench or args.index or args.targets or args.basis or args.inv or args.roll)

    if args.bench or all_legs:
        per = dm.update_commodity_benchmarks()
        if per:
            print("  国际基准:")
            for sym, last in sorted(per.items()):
                print(f"    {sym:4} → {last}")
        else:
            print("  ⚠ 国际基准拉取失败(重跑即可)")
    if args.index or all_legs:
        res = dm.update_commodity_index()
        for nm, n in res.items():
            print(f"  {nm}: +{n} 行")
        if not res:
            print("  ⚠ 指数拉取失败(重跑即可)")
    if args.targets or all_legs:
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
    if args.basis or all_legs:
        n = dm.update_commodity_basis()
        print(f"  基差+期限结构(100ppi): +{n} 行(首次全史 2019起 ~11min,此处增量)")
    if args.inv or all_legs:
        n = dm.update_commodity_inventory(refill=args.refill)
        print(f"  仓单·周采样(CZCE 总计+fut_wsr 八品种{',refill' if args.refill else ''}): +{n} 行")
    if args.roll or all_legs:
        r = dm.update_fut_rollover()
        print(f"  换月映射+逐合约(批次2.4): mapping +{r['mapping']} 行 · 合约 +{r['contracts']}"
              f" ({r['failed']} 失败)")


if __name__ == "__main__":
    main()
