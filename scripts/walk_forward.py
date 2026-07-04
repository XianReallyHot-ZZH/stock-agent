"""Walk-forward / out-of-sample validation (V2.1: signal-aware).

Procedure:
  1. TRAIN (2021-2023): sweep BOTH signals (momentum + reversion) + their params,
     pick each signal's best (gate-pass first, then Calmar) using ONLY train data.
  2. TEST  (2024-2026): apply each signal's train-selected params to the unseen
     test period. PASS out-of-sample -> generalizes; FAIL -> overfit, don't trust.

Usage: python scripts/walk_forward.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.backtest import run_backtest
from stockagent.backtest.sweep import MOMENTUM, evaluate_grid, make_config, row_to_overrides
from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging

TRAIN_START, TRAIN_END = "2021-01-01", "2023-12-31"
TEST_START, TEST_END = "2024-01-01", "2026-07-03"

MOM_BY_NAME = {name: (wins, wts) for name, wins, wts in MOMENTUM}

# Full-period momentum champion (params.yaml), for the sanity check.
FULL_WINNER = {
    ("rotation", "signal", "name"): "momentum",
    ("portfolio", "k"): 5,
    ("regime", "ma_period"): 120,
    ("stop", "trailing_pct"): 0.08,
    ("rotation", "trend_gate_ma"): 60,
    ("rotation", "momentum", "windows"): [60, 120, 250],
    ("rotation", "momentum", "weights"): [0.2, 0.3, 0.5],
}


def fmt_params(row) -> str:
    if row["signal"] == "momentum":
        return (f"K={int(row['K'])} regime_ma={int(row['regime_ma'])} stop={row['stop%']} "
                f"gate_ma={int(row['gate_ma'])} mom={row['momentum']}({row['windows']})")
    if row["signal"] == "bb_macd":
        return (f"K={int(row['K'])} regime_ma={int(row['regime_ma'])} stop={row['stop%']} "
                f"mode={row['bb_mode']} pctb_low={row['pctb_low']} pctb_high={row['pctb_high']} long_ma={row['bb_long_ma']}")
    if row["signal"] == "share_flow":
        return (f"K={int(row['K'])} regime_ma={int(row['regime_ma'])} stop={row['stop%']} "
                f"accum_thr={row['accum_thr']}")
    return (f"K={int(row['K'])} regime_ma={int(row['regime_ma'])} stop={row['stop%']} "
            f"rsi={row['rsi_period']} oversold={row['oversold']} long_ma={row['long_ma']}")


def _test(store, base, row, label):
    cfg = make_config(base, row_to_overrides(row))
    res = run_backtest(store, cfg, start=TEST_START, end=TEST_END)
    m, bm, g = res.metrics, res.benchmark["csi300_buyhold"], res.gate
    print(f"  [{label}] {fmt_params(row)}")
    print(f"      TEST strat: ann={m['annualized']:+.4f} mdd={m['max_drawdown']:.4f} sharpe={m['sharpe']} | "
          f"csi300bh ann={bm['annualized']:+.4f} mdd={bm['max_drawdown']:.4f} -> {'PASS ✅' if g['pass'] else 'FAIL ❌'}")
    return g["pass"]


def main():
    setup_logging()
    base = get_config()
    store = Store(base.db_path)

    print("=" * 64)
    print(f"WALK-FORWARD (V2.1)  TRAIN {TRAIN_START}..{TRAIN_END}  |  TEST {TEST_START}..{TEST_END}")
    print("=" * 64)

    print(f"\n[1] TRAIN sweep (momentum + reversion) on {TRAIN_START}..{TRAIN_END} ...")
    df_train = evaluate_grid(store, base, TRAIN_START, TRAIN_END, verbose=False)
    print(f"    train combos: {len(df_train)} total, {int(df_train['gate_pass'].sum())} pass gate")
    by_sig_pass = df_train.groupby("signal")["gate_pass"].sum().to_dict()
    print(f"    train pass by signal: {by_sig_pass}")

    print(f"\n[2] TEST (out-of-sample) each signal's TRAIN-best on {TEST_START}..{TEST_END}:")
    results = {}
    for sig in ("momentum", "reversion", "bb_macd", "share_flow"):
        sub = df_train[df_train["signal"] == sig]
        if len(sub) == 0:
            print(f"  [{sig}] no train combos, skip")
            continue
        results[sig] = _test(store, base, sub.iloc[0], f"{sig} train-best")

    print(f"\n[3] Sanity: full-period momentum CHAMPION (params.yaml) on TEST ...")
    res2 = run_backtest(store, make_config(base, FULL_WINNER), start=TEST_START, end=TEST_END)
    m2, g2 = res2.metrics, res2.gate
    print(f"    champion TEST : ann={m2['annualized']:+.4f} mdd={m2['max_drawdown']:.4f} sharpe={m2['sharpe']} "
          f"-> {'PASS ✅' if g2['pass'] else 'FAIL ❌'}")
    results["champion(momentum)"] = g2["pass"]

    print("\n" + "=" * 64)
    passed = [k for k, v in results.items() if v]
    if passed:
        print(f"VERDICT: ✅ 样本外 PASS: {passed}")
        print("        这些信号/参数泛化，可进 shadow 进一步跟踪。")
    else:
        print("VERDICT: ❌ 全部样本外 FAIL — 样本内表现是过拟合，勿据此上实盘。")
        print("        价格类因子（动量/均值回归/BB+MACD）在 A股 样本外均无稳定 edge。")
    print("=" * 64)


if __name__ == "__main__":
    main()
