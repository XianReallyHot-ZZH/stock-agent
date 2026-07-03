"""Run the strategy backtest + decision gate (M2).

Usage:
  python scripts/run_backtest.py
  python scripts/run_backtest.py --start 2021-01-01 --end 2026-07-02
  python scripts/run_backtest.py --plot equity.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.backtest import run_backtest
from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--plot", default=None, help="optional path to save equity curve png")
    args = ap.parse_args()

    setup_logging()
    cfg = get_config()
    store = Store(cfg.db_path)

    print(f"Backtesting {len(cfg.rotation_symbols())} symbols, "
          f"benchmark={cfg.benchmark_symbol}, K={cfg.params['portfolio']['k']}")
    res = run_backtest(store, cfg, start=args.start, end=args.end, capital=args.capital)

    print("\n=== STRATEGY ===")
    for k, v in res.metrics.items():
        print(f"  {k:<18} {v}")
    print("\n=== BENCHMARKS ===")
    for name, m in res.benchmark.items():
        if m:
            print(f"  [{name}] ann={m['annualized']:.4f} mdd={m['max_drawdown']:.4f} "
                  f"calmar={m['calmar']} sharpe={m['sharpe']}")
    print("\n=== DECISION GATE (Q11) ===")
    print(json.dumps(res.gate, indent=2, ensure_ascii=False))

    if args.plot:
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            # CJK font (Windows); fall back silently if unavailable
            for f in ("Microsoft YaHei", "SimHei", "Arial Unicode MS"):
                try:
                    from matplotlib.font_manager import FontProperties

                    if FontProperties(family=f).get_name():
                        plt.rcParams["font.sans-serif"] = [f]
                        break
                except Exception:
                    continue
            plt.rcParams["axes.unicode_minus"] = False

            fig, ax = plt.subplots(figsize=(12, 6))
            norm = args.capital
            (res.equity / norm).plot(ax=ax, label="策略(动量轮动)", linewidth=2)
            if res.benchmark_equity is not None:
                (res.benchmark_equity / norm).plot(ax=ax, label="沪深300 买入持有", alpha=0.8)
            if res.sixty_forty_equity is not None:
                (res.sixty_forty_equity / norm).plot(ax=ax, label="60/40 股债", alpha=0.6)
            gate_txt = "PASS ✅" if res.gate["pass"] else "FAIL ❌（未跑赢买入持有）"
            ax.set_title(f"策略 vs 基准  |  决策门: {gate_txt}\n"
                         f"策略 年化={res.metrics['annualized']:+.2%} 回撤={res.metrics['max_drawdown']:.2%}  |  "
                         f"沪深300 年化={res.benchmark['csi300_buyhold']['annualized']:+.2%} 回撤={res.benchmark['csi300_buyhold']['max_drawdown']:.2%}")
            ax.set_ylabel("净值 (起始=1)")
            ax.legend(loc="best")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(args.plot, dpi=120)
            print(f"\n📈 净值曲线已保存 -> {args.plot}")
        except Exception as e:  # noqa: BLE001
            print(f"(画图跳过: {e})")


if __name__ == "__main__":
    main()
