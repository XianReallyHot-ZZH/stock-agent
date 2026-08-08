"""Backfill western-macro series (UST / US equities / foreign futures / forex + reconstructed DXY)
into western_macro_series. Read-only 旁路 (ADR-0001) — never feeds the A-share rotation engine.

Idempotent: AkShare returns full history each call, upsert overwrites. Forex lives on
push2his.eastmoney.com (blocked in some envs — see CLAUDE.md); failures are logged, not fatal,
and DXY reconstruction simply yields 0 rows if the forex legs didn't fetch.

Usage:
    python scripts/backfill_western_macro.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import DataManager

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    mgr = DataManager(config=get_config())
    groups = mgr.update_western_macro()
    total = sum(groups.values())
    print("\n=== western-macro backfill ===")
    for k, v in groups.items():
        print(f"  {k:8s}: +{v} rows")
    print(f"  total: +{total} rows")
    if groups.get("dxy", 0) == 0:
        print("  ⚠ DXY 未重算(外汇腿抓取失败? push2his 被拦?)——本机/换网络重试")


if __name__ == "__main__":
    main()
