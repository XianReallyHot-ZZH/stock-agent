"""生成候选个股池看板(V8 高业绩池;thin CLI,fat renderer 在 stockagent/pool/report.py)。

Usage:
  python scripts/stock_pool_report.py            # 装配快照 → 精筛腿(按需) → 渲染 stock_pool.html
  python scripts/stock_pool_report.py --open     # 生成后用默认浏览器打开
  python scripts/stock_pool_report.py --no-sina  # 跳过 sina 精筛腿刷新(扣非/商誉,幸存者逐股)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.data.store import Store
from stockagent.pool.report import write_html
from stockagent.pool.screen import build_high_earnings_snapshot

ROOT = Path(__file__).resolve().parent.parent


def _refresh_sina_legs(store: Store, snap: dict) -> bool:
    """sina 精筛腿(扣非/商誉)对过地板幸存者按需拉,7 天节流。
    Returns True if refreshed(调用方应重建快照让扣非/风险旗吃到新腿)。"""
    cfg = get_config()
    last = store.get_meta("last_pool_sina_update")  # YYYY-MM-DD
    days = 7
    if last and (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days <= days:
        return False
    codes = snap.get("sina_needed") or []
    if not codes:
        return False
    dm = DataManager(store=store, config=cfg)
    est = len(codes) * 2.0 / 60
    print(f"sina 精筛腿: {len(codes)} 只过地板幸存者(扣非+商誉,~{est:.0f}min)...")
    dm.update_stock_financials(codes)
    store.set_meta("last_pool_sina_update", datetime.now().strftime("%Y-%m-%d"))
    return True


def main():
    ap = argparse.ArgumentParser(description="候选个股池看板(第六看板·V8 高业绩池·只读)")
    ap.add_argument("--open", action="store_true", help="生成后打开浏览器")
    ap.add_argument("--no-sina", action="store_true", help="跳过 sina 精筛腿刷新")
    ap.add_argument("--asof", type=str, default="", help="回放日期 YYYY-MM-DD(调试;不落池档案)")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    t0 = time.time()
    snap = build_high_earnings_snapshot(store, asof=args.asof or None)
    if not args.no_sina and args.asof == "" and _refresh_sina_legs(store, snap):
        snap = build_high_earnings_snapshot(store)   # 扣非/商誉吃新腿
    out = ROOT / "data" / "stock_pool.html"
    write_html(out, snap)
    em = snap.get("emergent")
    print(f"高业绩池: 宇宙 {snap['universe_stats']['n']} 只 · 过地板 {snap['n_floor_pass']}"
          f" · 池 {snap['n_gated_pool']}(PEG轨 {snap['n_track_peg']}/PB轨 {snap['n_track_pb']})"
          f" · 报告期 {snap['period']}"
          f" · 涌现簇 {'%.0f%%' % (em['share'] * 100) if em else '无'}"
          f" · diff +{len(snap['diff']['entered'])}/-{len(snap['diff']['exited'])}"
          f" · {time.time() - t0:.1f}s")
    print(f"-> {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
