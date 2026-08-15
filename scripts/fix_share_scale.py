"""修复 etf_scale 份额序列里 fix_splits 前复权残留的错位行（run-once）。

背景（2026-08 排查结论）：
- fix_splits.py 曾按拆分事件对 etf_scale 做前复权（source='share_split_adj'），
  但只覆盖 25 个散点日期；后续回填/日更又写回原始值 → 序列里散布错位尖峰，
  典型如 512480 2021-01-27 = 邻值 4x、512200 拆分前日期 = 邻值 0.36x。
  这些「+100% 隔日 -50%」伪影会污染任何基于 Δ份额 的资金流计算。
- 真拆分/份额折算事件（份额跳变 + unit_nav 反向断崖 + AUM 连续）共 12 起，
  原始跳变保留在数据中，改由 stockagent/research/flow.py 计算时做连续性调整。

本脚本：重抓这 25 个交易日的 SSE 份额快照，用原始值覆盖 8 只受影响 ETF 的
对应行（source 回写为 'sse'），使全序列恢复 raw 口径。幂等：重跑只是再抓一遍。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockagent.config import get_config  # noqa: E402
from stockagent.data import fetcher  # noqa: E402
from stockagent.data.store import Store  # noqa: E402

# fix_splits 写过调整值的 25 个散点日期（跨 8 只 ETF 完全一致）
ADJ_DATES = [
    "2021-01-27", "2021-08-17", "2021-08-26", "2021-12-21", "2022-04-15",
    "2022-04-19", "2022-05-10", "2022-05-24", "2022-06-06", "2022-06-28",
    "2022-07-05", "2022-09-30", "2023-04-14", "2023-06-08", "2023-06-21",
    "2023-09-25", "2023-12-19", "2024-07-04", "2024-11-26", "2024-12-17",
    "2025-04-25", "2025-10-10", "2026-04-14", "2026-05-15", "2026-05-22",
]
AFFECTED = {"512010", "512200", "512480", "512690", "512800", "512890", "512980", "515220"}
# 顺带核验 560280 源端疑似脏点（+377%→-77% 尖峰）；源端返回什么写什么，不擅自改
VERIFY_ONLY = {"560280"}


def main() -> None:
    cfg = get_config()
    store = Store(cfg.db_path)
    targets = AFFECTED | VERIFY_ONLY
    changed = 0
    for i, d in enumerate(ADJ_DATES):
        if i > 0:
            time.sleep(0.4)
        try:
            df = fetcher.fetch_etf_scale_sse(d.replace("-", ""))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] fetch {d} failed: {str(e)[:80]}")
            continue
        rows = []
        for _, r in df.iterrows():
            sym = str(r["symbol"])
            sh = r.get("shares")
            if sym in targets and sh is not None and sh == sh:  # notna
                rows.append((sym, d, float(sh), None))
        n = store.upsert_scale(rows, source="sse")
        changed += n
        print(f"{d}: {len(rows)} syms, {n} rows rewritten")
    print(f"done: {changed} rows rewritten to raw (source='sse')")


if __name__ == "__main__":
    main()
