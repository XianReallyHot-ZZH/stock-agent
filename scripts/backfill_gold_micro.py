"""Backfill 黄金微观紧缺数据 (COMEX 库存 / CFTC 商业持仓 / 央行购金) into 专表。
只读旁路 (ADR-0001) — 永不喂 A股轮动引擎。JZ 框架 L2/L3/L4 微观证据。

幂等: AkShare 全量返回, upsert 覆盖。逐组容错(失败跳过)。
数据缺口(无免费源, 不在此补): GOFO/租赁利率(LBMA 2015 停发)、全球 ETF(GLD/IAU)流。

Usage:
    python scripts/backfill_gold_micro.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from stockagent.config import get_config
from stockagent.data import DataManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    mgr = DataManager(config=get_config())
    groups = mgr.update_gold_micro()
    total = sum(groups.values())
    print("\n=== gold-micro backfill ===")
    for k, v in groups.items():
        print(f"  {k:6s}: +{v} rows")
    print(f"  total: +{total} rows")
    if not any(groups.values()):
        print("  ⚠ 全部抓取失败(akshare 被拦?)——换网络/重试")


if __name__ == "__main__":
    main()
