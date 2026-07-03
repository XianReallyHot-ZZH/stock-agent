"""Walk-forward / out-of-sample validation (M2 robustness).

Proper procedure:
  1. TRAIN (2021-2023): sweep params, pick the best (gate-pass first, then Calmar)
     using ONLY train-period data — pretending we don't know the future.
  2. TEST  (2024-2026): apply those train-selected params to the unseen test period.
     If the gate still PASSES out-of-sample -> strategy generalizes (not just overfit).
     If it FAILS OOS -> the in-sample pass was overfitting; do NOT trust it.

Also reports the full-period champion's performance on the test period, as a sanity
check on the params currently in params.yaml.

Usage: python scripts/walk_forward.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stockagent.backtest import run_backtest
from stockagent.backtest.sweep import MOMENTUM, evaluate_grid, make_config
from stockagent.config import get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging

TRAIN_START, TRAIN_END = "2021-01-01", "2023-12-31"
TEST_START, TEST_END = "2024-01-01", "2026-07-03"

MOM_BY_NAME = {name: (wins, wts) for name, wins, wts in MOMENTUM}

# The full-period champion (currently in params.yaml), for the sanity check.
FULL_WINNER = {
    ("portfolio", "k"): 5,
    ("regime", "ma_period"): 120,
    ("stop", "trailing_pct"): 0.08,
    ("rotation", "trend_gate_ma"): 60,
    ("rotation", "momentum", "windows"): [60, 120, 250],
    ("rotation", "momentum", "weights"): [0.2, 0.3, 0.5],
}


def row_to_overrides(row) -> dict:
    wins, wts = MOM_BY_NAME[row["momentum"]]
    return {
        ("portfolio", "k"): int(row["K"]),
        ("regime", "ma_period"): int(row["regime_ma"]),
        ("stop", "trailing_pct"): float(row["stop%"]),
        ("rotation", "trend_gate_ma"): int(row["gate_ma"]),
        ("rotation", "momentum", "windows"): wins,
        ("rotation", "momentum", "weights"): wts,
    }


def fmt_params(row) -> str:
    return (f"K={int(row['K'])} regime_ma={int(row['regime_ma'])} stop={row['stop%']} "
            f"gate_ma={int(row['gate_ma'])} mom={row['momentum']}({row['windows']})")


def main():
    setup_logging()
    base = get_config()
    store = Store(base.db_path)

    print("=" * 64)
    print(f"WALK-FORWARD  TRAIN {TRAIN_START}..{TRAIN_END}  |  TEST {TEST_START}..{TEST_END}")
    print("=" * 64)

    # ---- 1. TRAIN sweep ----
    print(f"\n[1] TRAIN sweep on {TRAIN_START}..{TRAIN_END} ...")
    df_train = evaluate_grid(store, base, TRAIN_START, TRAIN_END, verbose=False)
    n_pass_train = int(df_train["gate_pass"].sum())
    train_best = df_train.iloc[0]  # sorted: gate_pass desc, calmar desc
    print(f"    train combos passing gate: {n_pass_train}/{len(df_train)}")
    print(f"    train-best selection: {fmt_params(train_best)}")
    print(f"      train ann={train_best['ann']:+.4f} mdd={train_best['mdd']:.4f} "
          f"calmar={train_best['calmar']} {'[PASS]' if train_best['gate_pass'] else '[fail]'}")

    # ---- 2. TEST with train-selected params ----
    print(f"\n[2] TEST (out-of-sample) {TEST_START}..{TEST_END} with TRAIN-selected params ...")
    cfg = make_config(base, row_to_overrides(train_best))
    res = run_backtest(store, cfg, start=TEST_START, end=TEST_END)
    m, g = res.metrics, res.gate
    bm = res.benchmark["csi300_buyhold"]
    print(f"    TEST strategy : ann={m['annualized']:+.4f} mdd={m['max_drawdown']:.4f} sharpe={m['sharpe']}")
    print(f"    TEST csi300bh : ann={bm['annualized']:+.4f} mdd={bm['max_drawdown']:.4f}")
    print(f"    TEST gate     : {'PASS ✅' if g['pass'] else 'FAIL ❌'}")

    # ---- 3. Sanity: full-period champion on TEST ----
    print(f"\n[3] Sanity: full-period CHAMPION (params.yaml) on TEST ...")
    res2 = run_backtest(store, make_config(base, FULL_WINNER), start=TEST_START, end=TEST_END)
    m2, g2 = res2.metrics, res2.gate
    print(f"    champion TEST : ann={m2['annualized']:+.4f} mdd={m2['max_drawdown']:.4f} sharpe={m2['sharpe']} "
          f"-> {'PASS ✅' if g2['pass'] else 'FAIL ❌'}")

    # ---- verdict ----
    print("\n" + "=" * 64)
    if g["pass"] and g2["pass"]:
        print("VERDICT: ✅ 样本外仍 PASS — 策略泛化，非纯过拟合。可进入 shadow 验证。")
    elif g["pass"] or g2["pass"]:
        print("VERDICT: ⚠️ 部分 PASS — 信号弱/不稳定，谨慎；建议更多窗口测试后再信。")
    else:
        print("VERDICT: ❌ 样本外 FAIL — 样本内的 PASS 是过拟合，不可信，勿据此上实盘。")
    print("=" * 64)


if __name__ == "__main__":
    main()
