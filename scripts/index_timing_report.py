"""生成指数择时层 HTML 看板(V4 tracker Phase 1-B)。

Usage: python scripts/index_timing_report.py [--out PATH] [--period 60]
数据需先回填: python scripts/backfill_index.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.tracker import dashboard
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/index_timing.html")
    ap.add_argument("--period", type=int, default=60)
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)
    path = dashboard.render_index_timing(store, args.out, period=args.period)
    print(f"看板已生成: {path}")


if __name__ == "__main__":
    main()
