"""宏观框架看板渲染 (Phase 3 北向目标 · ADR-0001 只读旁路)。

纯数据跟踪与分析: 沿因果链(利率→曲线→美元→金属→能源→权益)给每个宏观资产的「当前在哪」分析。
不含 claim/台账/命中率; 无结算/抽取/LLM。

Usage:
  python scripts/macro_framework_report.py            # 渲染 + 开 data/macro_framework.html
  python scripts/macro_framework_report.py --no-open
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:  # Windows 控制台默认 GBK → 强制 utf-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from stockagent.config import get_config
from stockagent.data.store import Store
from stockagent.western_macro.framework import render_macro_framework

OUT = ROOT / "data" / "macro_framework.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="渲染宏观框架看板 (纯数据·只读·ADR-0001)")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    asof = datetime.now().strftime("%Y-%m-%d %H:%M")
    out = render_macro_framework(store, OUT, asof=asof)
    print(f"✅ rendered {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
