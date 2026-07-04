"""Fund-flow endpoint availability audit (V2.3 watchdog).

Probes the eastmoney fund-flow endpoints used by the data layer. When this
shows them AVAILABLE, the `fund_flow` signal can be built + validated (the data
pipe is already in place). When BLOCKED, the signal stays deferred.

Usage: python scripts/audit_fund_flow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import fetch_sector_fund_flow_hist, fetch_sector_fund_flow_rank
from stockagent.utils.logging_setup import setup_logging


def probe(name, fn) -> bool:
    try:
        df = fn()
        n = len(df) if df is not None else 0
        rng = ""
        if n and getattr(df.index, "name", None) == "date":
            rng = f"  {df.index.min()}..{df.index.max()}"
        print(f"  OK   {name}: {n} rows{rng}")
        if n:
            print(f"        cols={list(df.columns)} sample={df.head(1).to_dict('records')[:1]}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  ERR  {name}: {type(e).__name__}: {str(e)[:90]}")
        return False


def main():
    setup_logging()
    cfg = get_config()
    print("=" * 60)
    print("FUND-FLOW ENDPOINT AVAILABILITY AUDIT")
    print("=" * 60)

    r1 = probe("sector_fund_flow_rank(行业,今日)", lambda: fetch_sector_fund_flow_rank())
    syms = cfg.rotation_symbols()
    sample_sector = cfg.symbol_meta().get(syms[0], {}).get("sector", "银行") if syms else "银行"
    r2 = probe(f"sector_fund_flow_hist({sample_sector})",
               lambda: fetch_sector_fund_flow_hist(sample_sector))

    print("\n" + "=" * 60)
    if r1 or r2:
        print("VERDICT: ✅ 资金流接口可用 → 可进 fund_flow 信号建设 + walk-forward 验证")
    else:
        print("VERDICT: ❌ 资金流接口仍被 eastmoney 限流 → fund_flow 信号继续待数据")
        print("        定期重跑本脚本；一旦可用，管道已就绪，建信号很快。")
    print("=" * 60)


if __name__ == "__main__":
    main()
