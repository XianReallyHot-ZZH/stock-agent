"""Audit pool: is each rotation ETF the largest by AUM (总市值) in its sector?

Ranks all live ETFs matching each sector keyword by 总市值 (AUM proxy = 最新价×总份额)
and flags whether the pool's pick is #1. Complements audit_pool.py (which ranks by
daily 成交额, i.e. liquidity, not size). Run:  python scripts/audit_pool_aum.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data import fetch_etf_spot

# pool sector -> name keywords identifying sector peers (matched as OR against ETF 名称)
SECTOR_KW: dict[str, list[str]] = {
    "银行": ["银行"],
    "券商": ["证券", "券商"],
    "食品饮料": ["食品饮料", "食饮", "酒"],
    "医药": ["医药"],
    "创新药": ["创新药"],
    "养殖": ["养殖", "畜牧"],
    "家电": ["家电"],
    "半导体": ["半导体", "芯片"],
    "人工智能": ["人工智能"],
    "通信": ["通信"],
    "传媒": ["传媒"],
    "软件": ["软件"],
    "机器人": ["机器人"],
    "新能源车": ["新能源车"],
    "光伏": ["光伏"],
    "有色金属": ["有色"],
    "煤炭": ["煤炭"],
    "电力": ["电力"],
    "黄金": ["黄金"],
    "化工": ["化工"],
    "军工": ["军工", "国防"],
    "房地产": ["房地产", "地产"],
    "汽车": ["汽车"],
    "创业板": ["创业板"],
    "科创": ["科创"],
    "恒生科技": ["恒生科技"],
    "中概互联": ["中概", "互联"],
    "纳指": ["纳指", "纳斯达克"],
    "红利": ["红利"],
}


def main():
    cfg = get_config()
    print("Fetching live ETF spot (all ETFs)...")
    spot = fetch_etf_spot()
    spot["code"] = spot["code"].astype(str).str.strip()
    spot["name"] = spot["name"].astype(str).str.strip()

    aum_col = "总市值" if "总市值" in spot.columns else ("流通市值" if "流通市值" in spot.columns else None)
    if aum_col is None:
        print("ERROR: spot has neither 总市值 nor 流通市值; columns =", list(spot.columns))
        sys.exit(1)
    print(f"  total ETFs: {len(spot)}   AUM field used: {aum_col}\n")

    spot["aum_yi"] = spot[aum_col].astype(float) / 1e8  # to 亿
    spot = spot[spot["aum_yi"] > 0]  # drop zero/NaN AUM shells

    meta = cfg.symbol_meta()
    rotation = cfg.pool.get("rotation_pool", [])

    not_first = []
    for item in rotation:
        sym = str(item["symbol"]).strip()
        sector = item.get("sector", "")
        kws = SECTOR_KW.get(sector)
        if not kws:
            print(f"[{sector}] (no keyword map — skip)  {sym}")
            continue
        pat = "|".join(kws)
        peers = spot[spot["name"].str.contains(pat, regex=True, na=False)].copy()
        peers = peers.sort_values("aum_yi", ascending=False)

        pool_row = spot[spot["code"] == sym]
        pool_aum = float(pool_row.iloc[0]["aum_yi"]) if len(pool_row) else float("nan")
        pool_name = str(pool_row.iloc[0]["name"]) if len(pool_row) else item.get("name", "??")
        rank = (peers["code"] == sym).values.argmax() + 1 if sym in set(peers["code"]) else -1

        ok = "✅" if rank == 1 else ("⚠️" if rank > 1 else "❓不在同名ETF中")
        print(f"[{sector}] pool={sym} {pool_name} ({pool_aum:.0f}亿) → 排名 {rank}/{len(peers)} {ok}")
        for i, (_, r) in enumerate(peers.head(5).iterrows()):
            mark = "  <== pool" if r["code"] == sym else ""
            print(f"    #{i+1:<2} {r['code']}  {r['aum_yi']:>7.0f}亿  {r['name']}{mark}")
        # also show pool's rank within the broader list even if not top-5
        if rank > 5:
            print(f"    ...pool at #{rank}")
        if rank > 1:
            not_first.append((sector, sym, pool_name, rank, len(peers), pool_aum))
        print()

    print("=" * 60)
    if not_first:
        print(f"⚠️  {len(not_first)} 只并非各自领域 AUM 第一:")
        for sector, sym, name, rank, n, aum in not_first:
            print(f"   {sector:<6} {sym} {name}  排名 {rank}/{n} ({aum:.0f}亿)")
    else:
        print("✅ 全部 27 只 rotation ETF 均为各自领域 AUM 第一")


if __name__ == "__main__":
    main()
