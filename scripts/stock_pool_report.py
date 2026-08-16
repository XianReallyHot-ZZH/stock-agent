"""生成候选个股池看板(thin CLI,照 position_report.py 惯例;fat renderer 在 stockagent/pool/report.py)。

Usage:
  python scripts/stock_pool_report.py            # 装配快照 → 渲染 data/stock_pool.html
  python scripts/stock_pool_report.py --open     # 生成后用默认浏览器打开
  python scripts/stock_pool_report.py --no-stage2   # 跳过 stage-2 候选腿刷新(v1 默认即无)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.config import get_config
from stockagent.data.manager import DataManager
from stockagent.data.store import Store
from stockagent.pool.report import write_html
from stockagent.pool.screen import build_pool_snapshot

ROOT = Path(__file__).resolve().parent.parent


def _refresh_stage2_legs(store: Store, snap: dict) -> bool:
    """stage-2 候选腿周度刷新: 估值(baidu)+财报(sina) 对 Top-N 并集按需拉。
    Returns True if refreshed(调用方应重建快照让 davis 吃到新腿)。"""
    from datetime import datetime
    cfg = get_config()
    s2cfg = (cfg.params.get("stock_pool", {}) or {}).get("stage2", {}) or {}
    days = int(s2cfg.get("weekly_refresh_days", 7))
    last = store.get_meta("last_stage2_update")  # YYYY-MM-DD
    if last and (datetime.now() - datetime.strptime(last, "%Y-%m-%d")).days <= days:
        return False
    codes = snap.get("stage2_codes") or []
    if not codes:
        return False
    dm = DataManager(store=store, config=cfg)
    print(f"stage-2 腿刷新: {len(codes)} 只候选(估值+财报,~{len(codes) * 4 / 60:.0f}min)...")
    dm.update_stock_valuation(codes)
    dm.update_stock_financials(codes)
    store.set_meta("last_stage2_update", datetime.now().strftime("%Y-%m-%d"))
    return True


def main():
    ap = argparse.ArgumentParser(description="候选个股池看板(第六看板·只读)")
    ap.add_argument("--open", action="store_true", help="生成后打开浏览器")
    ap.add_argument("--no-stage2", action="store_true", help="跳过 stage-2 候选腿周度刷新")
    ap.add_argument("--asof", type=str, default="", help="回放日期 YYYY-MM-DD(调试)")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    t0 = time.time()
    snap = build_pool_snapshot(store, asof=args.asof or None)
    if not args.no_stage2 and _refresh_stage2_legs(store, snap):
        snap = build_pool_snapshot(store, asof=args.asof or None)   # davis 吃新腿
    out = ROOT / "data" / "stock_pool.html"
    write_html(out, snap)
    print(f"候选个股池: universe {snap['universe_stats']['n']} 只 · S1 触发 {snap['s1_total']}"
          f" · S2 资格 {snap['s2_total']} · PEAD {len(snap['pead_rows'])} 条"
          f" · 变脸 ↑{len(snap['face_rows_up'])}/↓{len(snap['face_rows_down'])}"
          f" · stage-2 候选 {len(snap.get('stage2_codes') or [])} 只"
          f"(davis {len(snap['davis_rows'])}) · {time.time() - t0:.1f}s")
    print(f"-> {out}")
    if args.open:
        import webbrowser
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
