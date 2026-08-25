"""国内宏观看板渲染 (第七看板 · 只读旁路)。

① 货币信用(M2/M1/社融·⑪ 完整版) ② 利率与流动性(Shibor/FDR007/LPR/中债期限结构/OMO 近似)
③ 政策日历(下次时点+倒计时)。纯数据跟踪,不喂引擎。

Usage:
  python scripts/china_macro_report.py            # 渲染 + 开 data/china_macro.html
  python scripts/china_macro_report.py --no-open
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

from stockagent.china_macro.framework import render_china_macro
from stockagent.config import get_config
from stockagent.data.store import Store

OUT = ROOT / "data" / "china_macro.html"


def main() -> None:
    ap = argparse.ArgumentParser(description="渲染国内宏观看板 (纯数据·只读·永不喂引擎)")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    store = Store(get_config().db_path)
    out = render_china_macro(store, OUT, asof=datetime.now().strftime("%Y-%m-%d %H:%M"))
    print(f"✅ rendered {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
