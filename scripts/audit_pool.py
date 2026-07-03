"""Audit the ETF pool: what liquid sector ETFs are NOT in the rotation pool?

Fetches the live ETF snapshot, filters by liquidity, and lists high-volume ETFs
missing from config/etf_pool.yaml — so you can spot sector gaps and decide what
to add. Run:  python scripts/audit_pool.py [--min-amount 1.0]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import fetch_etf_spot
from stockagent.utils.logging_setup import setup_logging

# sector keyword -> display label, to flag obvious gaps
SECTOR_HINTS = [
    ("保险", "保险"), ("化工", "化工"), ("计算机", "计算机"), ("软件", "软件"),
    ("钢铁", "钢铁"), ("石油", "石油石化"), ("农业", "农业"), ("银行", "银行"),
    ("证券", "证券"), ("医药", "医药"), ("半导体", "半导体"), ("芯片", "半导体"),
    ("人工智能", "AI"), ("新能源", "新能源"), ("光伏", "光伏"), ("军工", "军工"),
    ("有色", "有色"), ("煤炭", "煤炭"), ("电力", "电力"), ("房地产", "房地产"),
    ("传媒", "传媒"), ("通信", "通信"), ("电子", "电子"), ("食品", "食品饮料"),
    ("酒", "食品饮料"), ("家电", "家电"), ("汽车", "汽车"), ("红利", "红利"),
    ("消费", "消费"), ("旅游", "旅游"), ("建材", "建材"), ("机械", "机械"),
    ("环保", "环保"), ("金融", "金融"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-amount", type=float, default=1.0, help="min daily 成交额 in 亿")
    ap.add_argument("--top", type=int, default=50)
    args = ap.parse_args()
    setup_logging()
    cfg = get_config()
    in_pool = set(cfg.rotation_symbols()) | {cfg.benchmark_symbol, cfg.risk_off_symbol}

    print("Fetching live ETF spot...")
    spot = fetch_etf_spot()
    print(f"  total ETFs: {len(spot)}\n")

    def amt(row):
        try:
            return float(row.get("成交额", 0) or 0) / 1e8
        except Exception:
            return 0.0

    spot["amt_yi"] = spot.apply(amt, axis=1)

    # current pool liquidity
    print(f"=== current pool ({len(cfg.rotation_symbols())} rotation) ===")
    for s in list(dict.fromkeys(cfg.rotation_symbols())) + [cfg.benchmark_symbol, cfg.risk_off_symbol]:
        row = spot[spot["code"] == s]
        name = str(row.iloc[0]["name"]) if len(row) else "??"
        a = float(row.iloc[0]["amt_yi"]) if len(row) else 0
        flag = " ⚠LOW" if (a < 0.5 and s not in (cfg.benchmark_symbol, cfg.risk_off_symbol)) else ""
        print(f"  {s}  {a:>6.2f}亿  {name}{flag}")

    # liquid ETFs NOT in pool
    cand = spot[(~spot["code"].isin(in_pool)) & (spot["amt_yi"] >= args.min_amount)].copy()
    cand = cand.sort_values("amt_yi", ascending=False).head(args.top)
    print(f"\n=== liquid (≥{args.min_amount}亿) ETFs NOT in pool — top {len(cand)} by 成交额 ===")
    print(f"{'code':<8}{'成交额(亿)':>10}  {'sector':<8}name")
    seen_sectors = set()
    for _, r in cand.iterrows():
        name = str(r["name"])
        sector = ""
        for kw, label in SECTOR_HINTS:
            if kw in name:
                sector = label
                break
        if sector:
            seen_sectors.add(sector)
        print(f"{r['code']:<8}{r['amt_yi']:>10.2f}  {sector:<8}{name}")


if __name__ == "__main__":
    main()
