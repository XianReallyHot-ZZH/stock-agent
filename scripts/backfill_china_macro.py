"""Backfill / update 中国宏观利率数据 (第七看板 国内宏观).

Shibor(2015起) + FR/FDR 回购定盘利率(2020-09起,按年分段) + LPR(1991起)
+ 中债国债期限结构(1990起) + 央行资产负债表(1993起,月频)
+ 地方政府债发行明细(2021-09起,逐券,v2 社融可观测成分).
Idempotent — 全量重拉 upsert 覆盖. Safe to re-run.

Usage:
  python scripts/backfill_china_macro.py          # 全部六腿
  python scripts/backfill_china_macro.py --rates  # 只四条利率腿
  python scripts/backfill_china_macro.py --cb     # 只央行资产负债表
  python scripts/backfill_china_macro.py --lgb    # 只地方债发行明细(月窗分段 ~1-2min)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.utils.logging_setup import setup_logging


def _summary(dm: DataManager):
    store = dm.store
    print("\n=== 国内宏观数据覆盖 ===")
    rows = [
        ("Shibor 定价", store.get_shibor_series()),
        ("FR/FDR 定盘", store.get_repo_fix_series()),
        ("LPR", store.get_lpr_series()),
        ("中债期限结构", store.get_cn_bond_series()),
        ("央行资产负债表", store.get_cb_balance_series()),
    ]
    for name, df in rows:
        if len(df):
            print(f"  {name:10}: {len(df)} 行, {df.index.min()}..{df.index.max()}")
        else:
            print(f"  {name:10}: (无)")
    lgb = store.get_lgb_issue()
    if len(lgb):
        print(f"  {'地方债明细':8}: {len(lgb)} 券, "
              f"{lgb['issue_date'].min()}..{lgb['issue_date'].max()}")
    else:
        print("  地方债明细: (无)")
    tsf = store.get_china_tsf_series()
    if len(tsf) and "rmb_loans" in tsf.columns and tsf["rmb_loans"].notna().any():
        print(f"  社融分项  : {int(tsf['rmb_loans'].notna().sum())} 月有分项(贷款/企业债/股票)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", action="store_true", help="only 利率四腿(shibor/repo/lpr/cnbond)")
    ap.add_argument("--cb", action="store_true", help="only 央行资产负债表(月频)")
    ap.add_argument("--lgb", action="store_true", help="only 地方债发行明细(月窗分段 ~1-2min)")
    args = ap.parse_args()
    setup_logging()
    dm = DataManager(config=get_config())

    selective = args.rates or args.cb or args.lgb
    if args.rates or not selective:
        res = dm.update_china_rates()
        if not any(res.values()):
            print("⚠️ 利率四腿全部失败(金十偶发被拦,重跑即可)")
    if args.cb or not selective:
        dm.update_cb_balance()
    if args.lgb or not selective:
        res = dm.update_lgb_issue()
        if not res.get("lgb"):
            print("⚠️ 地方债明细失败(cninfo 限流?重跑自愈)")

    _summary(dm)


if __name__ == "__main__":
    main()
