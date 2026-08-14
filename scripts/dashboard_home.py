"""生成四看板总入口壳页 data/index.html (左侧导航 + iframe 装载, 无数据依赖·秒级)。

四个看板 HTML 各自生成后, 刷新壳页即可见(mtime 重读); 未生成的看板在壳内提示生成命令。

Usage:
  python scripts/dashboard_home.py              # 生成 + 开浏览器
  python scripts/dashboard_home.py --no-open
"""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:  # Windows 控制台默认 GBK → 强制 utf-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from stockagent.dashboard_home import render_home


def main() -> None:
    ap = argparse.ArgumentParser(description="生成四看板总入口 data/index.html (iframe 导航壳)")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    out = render_home(ROOT / "data")
    print(f"✅ rendered {out}")
    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
