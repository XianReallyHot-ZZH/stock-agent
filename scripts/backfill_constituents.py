"""Refresh ETF→index constituents with official weights (E1 底座, B 路线, 月度节奏).

中证指数官网成分+权重(index_stock_cons_weight_csindex, 月度快照) → index_constituents 表。
名称哨兵: 返回指数名必须含 etf_pool.yaml 的 index_expect, 不符即拒(防猜错代码)。
空结果不写库。QDII / CES半导体(512480) / 创业板指(159915) 无 index_code → 自然跳过。

Usage:
  python scripts/backfill_constituents.py                     # 全池 rotation
  python scripts/backfill_constituents.py --symbols 512800 512880
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.data.manager import DataManager
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser(description="Refresh index constituents (E1)")
    ap.add_argument("--symbols", nargs="*", default=None, help="pool symbols to refresh (default: all rotation)")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)
    dm = DataManager(store=store, config=cfg)

    n = dm.update_constituents(symbols=args.symbols)
    print(f"\nconstituents refreshed: {n} indices")

    print("\n=== 成分覆盖 ===")
    meta = cfg.symbol_meta()
    for sym in (args.symbols or cfg.rotation_symbols()):
        entry = meta.get(sym) or {}
        idx = entry.get("index_code")
        if not idx:
            print(f"  {sym} {entry.get('name', ''):12} — (无 index_code, 降级: 无免费成分源)")
            continue
        cons = store.get_constituents(str(idx))
        snap = store.last_constituent_snapshot(str(idx))
        print(f"  {sym} {entry.get('name', ''):12} {idx:8} {len(cons):4d} 成分  快照 {snap or '(无)'}")


if __name__ == "__main__":
    main()
