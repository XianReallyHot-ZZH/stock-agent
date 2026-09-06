"""大宗商品看板 CLI — 第八看板交付通道(2026-09 从个股诊断拆出)。

只读观测温度计 + 周期股择时深化底座:🚦雷达(国内口径) + 📊总览(官方中证商品指数+自算广度)
+ 🧲品种面板(国际基准主语/国内价对照) + 📈时序图 + ⚖比价矩阵。不碰交易引擎,不推送。
数据腿:国内 17 品种=backfill_stock_data --comm;国际基准/官方指数=backfill_commodity.py
(dashboard_data_check --fix 已自动带)。

Usage:
  python scripts/commodity_report.py                # → data/commodity.html(默认浏览器打开)
  python scripts/commodity_report.py --no-open
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.commodity import panel as pnl
from stockagent.commodity import render as crep
from stockagent.commodity import ratios as rt
from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging


def _fmt(v, signed=False):
    import math
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v*100:+.0f}%" if signed else f"{v*100:.0f}%"


def main():
    ap = argparse.ArgumentParser(description="大宗商品看板 (read-only · 第八看板)")
    ap.add_argument("--as-of", default=None, help="评估日 YYYY-MM-DD(默认今天)")
    ap.add_argument("--output", default="data/commodity.html")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    setup_logging()

    cfg = get_config()
    store = Store(cfg.db_path)
    asof = args.as_of or datetime.now().strftime("%Y-%m-%d")

    html = crep.render(store, asof, config=cfg)
    out = crep.write_html(html, args.output)

    # 控制台摘要
    rows = pnl.panel_rows(store, cfg)
    up = [r["variety"] for r in rows if r["judge"] == "向上"]
    dn = [r["variety"] for r in rows if r["judge"] == "向下"]
    print(f"\n🛢 大宗商品看板 -> {out}")
    print(f"   as_of={asof}  {len(rows)} 品种(国际主语 {sum(1 for r in rows if r['has_bench'])})")
    if rows:
        print(f"   向上: {','.join(up) or '—'}")
        print(f"   向下: {','.join(dn) or '—'}")
    rrows = rt.ratio_rows(store, config=cfg)
    for r in rrows:
        print(f"   {r['name']:8} {r['cur']:8.2f}  分位{_fmt(r['pct'])}  同比{_fmt(r['yoy'], True)}")
    print(f"\n   open: file:///{out.resolve()}")

    if not args.no_open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()
