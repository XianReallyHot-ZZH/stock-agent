"""Cold-start setup for the ETF research dashboard on a fresh clone.

A fresh clone has NO data (the SQLite DB is gitignored), so the dashboard needs a full
historical backfill before it can render. This script orchestrates it in dependency order:
  0. dep check (akshare/plotly/pandas/...) + ensure .env
  1. prices + trade calendar  (update_all — needed as the timeline for share/PE backfills)
  2. shares (SSE per-date, then SZSE range)
  3. NAV (fund-published, all ETFs)
  4. industry PE (cninfo — slowest, throttle-tuned)
  5. render the dashboard

Each stage is idempotent (resumes from where it stopped), so re-running after a failure
continues rather than restarts. Total ~1hr, dominated by PE + shares. --skip-pe gives a
fast first look (valuation=NaN → 2-factor ranking still works).

Usage:
  python scripts/setup_research_dashboard.py             # full cold start
  python scripts/setup_research_dashboard.py --skip-pe   # fast: skip the ~30min PE backfill
  python scripts/setup_research_dashboard.py --from 2022-01-01
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# stage banner helper
def _stage(n, title, eta=""):
    print(f"\n{'='*60}\n[stage {n}] {title}  {eta}\n{'='*60}", flush=True)


def check_deps() -> bool:
    """Verify required packages importable; print install hint if not."""
    _stage(0, "依赖检查")
    missing = []
    for mod in ("pandas", "numpy", "akshare", "requests", "yaml", "dotenv", "plotly"):
        try:
            __import__(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if missing:
        print(f"缺少依赖: {missing}")
        print("请先安装:  pip install -r requirements.txt")
        return False
    print("依赖齐全 OK")
    return True


def ensure_env() -> None:
    """Copy .env.example → .env if missing. LLM key is optional (template fallback works)."""
    env = ROOT / ".env"
    ex = ROOT / ".env.example"
    if env.exists():
        print(".env 已存在")
        return
    if ex.exists():
        shutil.copy(ex, env)
        print(f"已从 .env.example 创建 .env（LLM key 可选；不填则走规则模板）")
    else:
        print("⚠ 无 .env 也无 .env.example（LLM 解读将走规则模板，不影响生成）")


def main():
    ap = argparse.ArgumentParser(description="Cold-start setup for the research dashboard")
    ap.add_argument("--from", dest="start", default="2021-01-01", help="backfill start date")
    ap.add_argument("--skip-pe", action="store_true", help="skip the slow cninfo PE backfill")
    args = ap.parse_args()

    if not check_deps():
        sys.exit(1)
    ensure_env()

    from stockagent.config import get_config
    from stockagent.data import Store, DataManager
    cfg = get_config()
    store = Store(cfg.db_path)
    dm = DataManager(store=store, config=cfg)

    # detect existing state
    bench_last = store.last_date(cfg.benchmark_symbol)
    if bench_last:
        print(f"\n注意: 基准已有数据（到 {bench_last}）。各 stage 会增量续填，不重复已抓部分。")

    # stage 1: prices + calendar (must be first — benchmark timeline drives share/PE backfills)
    _stage(1, "价格 + 交易日历 (update_all)", "(~5min, 29 symbols)")
    dm.update_all(refresh_calendar=True)

    end = "2026-07-05"
    # stage 2: shares — SSE per-date, then SZSE range
    _stage(2, "份额回填 — SSE", f"({args.start}..{end}, ~9min)")
    dm.backfill_etf_scale(args.start, end, step_days=1, source="sse")
    _stage(2, "份额回填 — SZSE（深市，按月增量）", "(~9min)")
    dm.backfill_etf_scale(args.start, end, step_days=1, source="szse")

    # stage 3: NAV (all ETFs, date-range native — fast)
    _stage(3, "单位净值 NAV（真NAV，全 ETF）", "(~2min)")
    dm.backfill_etf_nav(args.start, end)

    # stage 4: industry PE (cninfo, slow + throttle-prone)
    if args.skip_pe:
        _stage(4, "行业 PE [跳过 --skip-pe]", "(估值因子将为 NaN，看板走双因子；以后可单独回填)")
    else:
        pe_start = "2023-01-01"  # cninfo history starts ~2023
        _stage(4, "行业 PE (cninfo，周度·限流)", f"({pe_start}..{end}, ~30min)")
        dm.backfill_industry_pe(pe_start, end, step_days=7, sleep=8)

    # stage 5: render
    _stage(5, "生成看板")
    cmd = [sys.executable, str(ROOT / "scripts" / "research_report.py")]
    print("运行:", " ".join(cmd))
    subprocess.run(cmd, check=False)
    print(f"\n{'='*60}\n完成。打开 data/research_report.html 查看。\n{'='*60}")


if __name__ == "__main__":
    main()
