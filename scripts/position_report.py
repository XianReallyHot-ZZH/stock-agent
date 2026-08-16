"""生成仓位管理看板 HTML(第五看板 · 估值档 × 预案表对照)。

Usage: python scripts/position_report.py [--out PATH]
数据需先回填: python scripts/backfill_index.py(与指数择时看板同源,零新增数据腿)
预案表配置: config/params.yaml position_plan(档位→权益仓位%区间,用户自定义)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.tracker import position
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/position.html")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)
    path = position.render_position_report(store, cfg.params, args.out)
    print(f"看板已生成: {path}")


if __name__ == "__main__":
    main()
