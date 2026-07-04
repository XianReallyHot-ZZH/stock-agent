"""Multi-window (expanding, anchored) walk-forward — reversion robustness (V2.1 step B).

One train/test split can mislead. Here we roll FOUR yearly test windows, each with
an expanding train anchored at 2021-01-01, select reversion params on train, test
OOS. Robustness = consistent generalization across windows (not just one split).

Usage: python scripts/walk_forward_multi.py [--with-momentum]
"""
from __future__ import annotations

import argparse
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.backtest import run_backtest
from stockagent.backtest.sweep import (evaluate_bb_macd, evaluate_momentum,
                                       evaluate_reversion, evaluate_share_flow,
                                       make_config, row_to_overrides)
from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging

# (train_start, train_end, test_start, test_end)
WINDOWS = [
    ("2021-01-01", "2022-12-31", "2023-01-01", "2023-12-31"),
    ("2021-01-01", "2023-12-31", "2024-01-01", "2024-12-31"),
    ("2021-01-01", "2024-12-31", "2025-01-01", "2025-12-31"),
    ("2021-01-01", "2025-12-31", "2026-01-01", "2026-07-03"),
]


def _best_str(row) -> str:
    if row["signal"] == "reversion":
        return f"K={int(row['K'])} regime={int(row['regime_ma'])} rsi={row['rsi_period']} oversold={row['oversold']} long_ma={row['long_ma']}"
    if row["signal"] == "bb_macd":
        return f"K={int(row['K'])} regime={int(row['regime_ma'])} mode={row['bb_mode']} pctb_low={row['pctb_low']} pctb_high={row['pctb_high']} long_ma={row['bb_long_ma']}"
    if row["signal"] == "share_flow":
        return f"K={int(row['K'])} regime={int(row['regime_ma'])} accum_thr={row['accum_thr']}"
    return f"K={int(row['K'])} regime={int(row['regime_ma'])} mom={row['momentum']}({row['windows']})"


def run_signal(store, base, eval_fn, signal):
    rows = []
    for tr_s, tr_e, te_s, te_e in WINDOWS:
        df = eval_fn(store, base, tr_s, tr_e)
        if len(df) == 0:
            print(f"  test {te_s[:4]}: (no train combos)")
            continue
        best = df.iloc[0]
        res = run_backtest(store, make_config(base, row_to_overrides(best)), start=te_s, end=te_e)
        bm = res.benchmark["csi300_buyhold"]
        r = {
            "test_year": te_s[:4], "train_best": _best_str(best),
            "strat_ann": res.metrics["annualized"], "strat_mdd": res.metrics["max_drawdown"],
            "strat_sharpe": res.metrics["sharpe"],
            "bm_ann": bm["annualized"], "bm_mdd": bm["max_drawdown"], "gate": res.gate["pass"],
        }
        rows.append(r)
        print(f"  test {r['test_year']}: strat ann={r['strat_ann']:+.4f} mdd={r['strat_mdd']:.4f} | "
              f"bm ann={r['bm_ann']:+.4f} mdd={r['bm_mdd']:.4f} | gate={'PASS ✅' if r['gate'] else 'fail'}")
        print(f"           train-best: {r['train_best']}")
    return rows


def summarize(signal, rows):
    if not rows:
        return
    gates = sum(r["gate"] for r in rows)
    dd_better = sum(r["strat_mdd"] > r["bm_mdd"] for r in rows)      # less negative = smaller drawdown
    ret_better = sum(r["strat_ann"] >= r["bm_ann"] for r in rows)
    print(f"  => {signal}: gate PASS {gates}/{len(rows)} | "
          f"avg strat ann={st.mean(r['strat_ann'] for r in rows):+.4f} mdd={st.mean(r['strat_mdd'] for r in rows):.4f} | "
          f"drawdown<bm {dd_better}/{len(rows)} | return>=bm {ret_better}/{len(rows)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-momentum", action="store_true", help="also run momentum for comparison (slow)")
    args = ap.parse_args()
    setup_logging()
    base = get_config()
    store = Store(base.db_path)

    print("=" * 64)
    print("MULTI-WINDOW WALK-FORWARD (expanding, anchored 2021-01-01)")
    print("=" * 64)

    print("\n--- reversion ---")
    summarize("reversion", run_signal(store, base, evaluate_reversion, "reversion"))

    print("\n--- bb_macd ---")
    summarize("bb_macd", run_signal(store, base, evaluate_bb_macd, "bb_macd"))

    print("\n--- share_flow ---")
    summarize("share_flow", run_signal(store, base, evaluate_share_flow, "share_flow"))

    if args.with_momentum:
        print("\n--- momentum (baseline) ---")
        summarize("momentum", run_signal(store, base, evaluate_momentum, "momentum"))

    print("\n" + "=" * 64)
    print("判定：reversion 若 gate PASS 过半 或 drawdown<基准 过半 => 防御性稳健，可作 shadow 主信号。")
    print("=" * 64)


if __name__ == "__main__":
    main()
