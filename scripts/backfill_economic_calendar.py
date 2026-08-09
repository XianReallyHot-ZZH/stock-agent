"""Backfill 经济日历/事件 (近 7 天已公布 + 未来 45 天排期, 筛重要性≥2) → economic_calendar 表。
只读旁路 (ADR-0001)。框架催化剂层: 已公布数据(actual vs forecast=surprise/数据真伪) + 未来 FOMC/CPI/非农/PCE 时点。

Usage:
    python scripts/backfill_economic_calendar.py
    python scripts/backfill_economic_calendar.py --back 10 --forward 30
"""
from __future__ import annotations

import argparse
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
    ap = argparse.ArgumentParser(description="回填经济日历(美国高重要性事件)")
    ap.add_argument("--back", type=int, default=7, help="回看天数(已公布)")
    ap.add_argument("--forward", type=int, default=45, help="前看天数(未来排期,默认45覆盖下次FOMC)")
    args = ap.parse_args()

    mgr = DataManager(config=get_config())
    out = mgr.update_economic_calendar(days_back=args.back, days_forward=args.forward)
    print("\n=== economic_calendar backfill ===")
    print(f"  calendar: +{out['calendar']} rows")
    if not out["calendar"]:
        print("  ⚠ 抓取失败(akshare 被拦?)——换网络/重试")


if __name__ == "__main__":
    main()
