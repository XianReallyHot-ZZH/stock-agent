"""Shadow / paper-trading runner (M5).

Tracks paper performance since you began shadowing + emits today's report.
Strategy currently FAILS the decision gate -> this is the ONLY mode to use until
it passes; do NOT trade real money on these signals.

Usage:
  python scripts/run_shadow.py --since 2026-06-01   # mark shadow start
  python scripts/run_shadow.py                       # show paper perf + today's report
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.scheduler import run_morning
from stockagent.shadow import get_shadow_start, set_shadow_start, shadow_performance
from stockagent.utils.logging_setup import setup_logging

BANNER = (
    "🟪 SHADOW / 纸盘模式 — 仅为模拟跟踪，策略尚未通过决策门，"
    "请勿据此投入真金白银。"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="set shadow start date YYYY-MM-DD")
    ap.add_argument("--report", action="store_true", help="also emit today's signal/report")
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)

    print("=" * 60)
    print(BANNER)
    print("=" * 60)

    if args.since:
        set_shadow_start(store, args.since)
        print(f"shadow start set -> {args.since}\n")
    else:
        ss = get_shadow_start(store)
        print(f"shadow start: {ss or '(not set — use --since YYYY-MM-DD)'}\n")

    perf = shadow_performance(store, cfg)
    if perf.get("active"):
        m = perf["metrics"]
        bm = perf["benchmark"].get("csi300_buyhold", {})
        print(f"paper window: {perf['start']} .. {perf['end']}  ({perf['n_days']} days)")
        print(f"  strategy : ann={m['annualized']:+.4f}  mdd={m['max_drawdown']:.4f}  "
              f"calmar={m['calmar']}  sharpe={m['sharpe']}  turnover={m.get('annual_turnover')}")
        if bm:
            print(f"  csi300bh : ann={bm['annualized']:+.4f}  mdd={bm['max_drawdown']:.4f}")
        print(f"  gate     : {perf['gate']['verdict']}")
    else:
        print(f"(shadow inactive: {perf.get('reason')})")

    if args.report:
        print("\n--- today's signal (paper) ---")
        res = run_morning(force=True, use_llm=False)
        if res.get("report"):
            print(res["report"])


if __name__ == "__main__":
    main()
