"""Backfill / update 中国宏观利率数据 (第七看板 国内宏观).

Shibor(2015起) + FR/FDR 回购定盘利率(2020-09起,按年分段) + LPR(1991起)
+ 中债国债期限结构(1990起) + 央行资产负债表(1993起,月频).
Idempotent — 全量重拉 upsert 覆盖. Safe to re-run.

Usage:
  python scripts/backfill_china_macro.py          # 全部五腿
  python scripts/backfill_china_macro.py --rates  # 只四条利率腿
  python scripts/backfill_china_macro.py --cb     # 只央行资产负债表
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", action="store_true", help="only 利率四腿(shibor/repo/lpr/cnbond)")
    ap.add_argument("--cb", action="store_true", help="only 央行资产负债表(月频)")
    args = ap.parse_args()
    setup_logging()
    dm = DataManager(config=get_config())

    selective = args.rates or args.cb
    if args.rates or not selective:
        res = dm.update_china_rates()
        if not any(res.values()):
            print("⚠️ 利率四腿全部失败(金十偶发被拦,重跑即可)")
    if args.cb or not selective:
        dm.update_cb_balance()

    _summary(dm)


if __name__ == "__main__":
    main()
