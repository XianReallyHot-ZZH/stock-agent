"""Parameter sweep over the strategy (M2): grid-search tunable params, rank by gate.

Each backtest is honest (same engine, same costs/T+1). We rank combos by a
risk-adjusted score and flag which (if any) PASS the decision gate.

Usage:
  python scripts/sweep_params.py                       # default small grid
  python scripts/sweep_params.py --start 2021-01-01
"""
from __future__ import annotations

import argparse
import copy
import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from stockagent.backtest import run_backtest
from stockagent.config import Config, get_config
from stockagent.data import Store
from stockagent.utils.logging_setup import setup_logging


def make_config(base: Config, overrides: dict) -> Config:
    params = copy.deepcopy(base.params)
    for keys, val in overrides.items():
        d = params
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = val
    return Config(params=params, pool=base.pool, env=base.env)


# Weight presets for fixed windows [20, 60, 120] (order: 20d, 60d, 120d).
WEIGHT_PRESETS = [
    ("baseline_偏长120", [0.2, 0.3, 0.5]),
    ("short_偏短20",     [0.5, 0.3, 0.2]),
    ("even_均衡",        [0.333, 0.334, 0.333]),
    ("mid_偏中60",       [0.2, 0.6, 0.2]),
    ("xlong_极长120",    [0.1, 0.2, 0.7]),
    ("xshort_极短20",    [0.7, 0.2, 0.1]),
    ("pure_20",          [1.0, 0.0, 0.0]),
    ("pure_60",          [0.0, 1.0, 0.0]),
    ("pure_120",         [0.0, 0.0, 1.0]),
]


def _sweep_weights(base: Config, store, args):
    """Fix windows=[20,60,120] + best-known other params; sweep weight presets x K."""
    fixed = {
        ("rotation", "momentum", "windows"): [20, 60, 120],
        ("regime", "ma_period"): 120,
        ("stop", "trailing_pct"): 0.12,
        ("rotation", "trend_gate_ma"): 60,
    }
    ks = [3, 5]
    total = len(WEIGHT_PRESETS) * len(ks)
    print(f"Weight sweep: windows=[20,60,120] fixed, other params at best-known; "
          f"{len(WEIGHT_PRESETS)} weights x K{ks} = {total} combos\n")
    rows = []
    i = 0
    for wname, wts in WEIGHT_PRESETS:
        for k in ks:
            i += 1
            overrides = dict(fixed)
            overrides[("rotation", "momentum", "weights")] = wts
            overrides[("portfolio", "k")] = k
            cfg = make_config(base, overrides)
            try:
                res = run_backtest(store, cfg, start=args.start, end=args.end)
                m = res.metrics
                g = res.gate
                rows.append({
                    "weights": wname, "w": str(wts), "K": k,
                    "ann": m["annualized"], "mdd": m["max_drawdown"],
                    "calmar": m["calmar"], "sharpe": m["sharpe"],
                    "turnover": m["annual_turnover"], "gate_pass": g["pass"],
                })
                mark = "PASS" if g["pass"] else "fail"
                print(f"  [{i}/{total}] {wname:<16} K={k} -> ann={m['annualized']:+.3f} "
                      f"mdd={m['max_drawdown']:.3f} calmar={m['calmar']} [{mark}]")
            except Exception as e:  # noqa: BLE001
                print(f"  [{i}/{total}] ERROR {wname}: {e}")
    if not rows:
        return
    df = pd.DataFrame(rows).sort_values(["gate_pass", "calmar"], ascending=[False, False]).reset_index(drop=True)
    out = Path("data/sweep_weights.csv")
    df.to_csv(out, index=False)
    print("\n=== weight sweep ranked by gate-pass then Calmar ===")
    print(df.to_string(index=False))
    print(f"\n{int(df['gate_pass'].sum())}/{len(df)} PASS. -> {out}")


# Full strategy grid (used by `full` sweep + walk-forward).
GRID = {
    ("portfolio", "k"): [3, 5],
    ("regime", "ma_period"): [60, 120, 200],
    ("stop", "trailing_pct"): [0.08, 0.12],
    ("rotation", "trend_gate_ma"): [20, 60],
}
MOMENTUM = [
    ("mid",   [20, 60, 120], [0.2, 0.3, 0.5]),
    ("short", [10, 20, 60],  [0.4, 0.3, 0.3]),
    ("long",  [60, 120, 250], [0.2, 0.3, 0.5]),
    ("pure60",  [60],  [1.0]),
    ("pure120", [120], [1.0]),
]


def evaluate_grid(store, base: Config, start: str, end: str, verbose: bool = True) -> pd.DataFrame:
    """Run the full param grid x momentum presets over [start,end]. Returns ranked DataFrame."""
    keys = list(GRID.keys())
    base_combos = list(itertools.product(*[GRID[k] for k in keys]))
    total = len(base_combos) * len(MOMENTUM)
    if verbose:
        print(f"Sweeping {total} combos over {start}..{end}")
    rows = []
    i = 0
    for vals in base_combos:
        for mname, wins, wts in MOMENTUM:
            i += 1
            overrides = dict(zip(keys, vals))
            overrides[("rotation", "momentum", "windows")] = wins
            overrides[("rotation", "momentum", "weights")] = wts
            cfg = make_config(base, overrides)
            try:
                res = run_backtest(store, cfg, start=start, end=end)
                m = res.metrics
                g = res.gate
                rows.append({
                    "K": overrides[("portfolio", "k")],
                    "regime_ma": overrides[("regime", "ma_period")],
                    "stop%": overrides[("stop", "trailing_pct")],
                    "gate_ma": overrides[("rotation", "trend_gate_ma")],
                    "momentum": mname,
                    "windows": str(wins),
                    "ann": m["annualized"], "mdd": m["max_drawdown"],
                    "calmar": m["calmar"], "sharpe": m["sharpe"],
                    "turnover": m["annual_turnover"], "gate_pass": g["pass"],
                })
            except Exception:  # noqa: BLE001
                continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).sort_values(["gate_pass", "calmar"], ascending=[False, False]).reset_index(drop=True)
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-01-01")
    ap.add_argument("--end", default="2026-07-02")
    ap.add_argument("--focus", choices=["full", "weights"], default="full",
                    help="full=grid x momentum presets; weights=fix windows[20,60,120], sweep weights only")
    args = ap.parse_args()
    setup_logging()

    base = get_config()
    store = Store(base.db_path)

    if args.focus == "weights":
        return _sweep_weights(base, store, args)

    df = evaluate_grid(store, base, args.start, args.end, verbose=True)
    out = Path("data/sweep_results.csv")
    df.to_csv(out, index=False)
    print("\n=== TOP 10 by gate-pass then Calmar ===")
    print(df.head(10).to_string(index=False))
    print("\n=== best Calmar per momentum preset ===")
    print(df.sort_values("calmar", ascending=False).drop_duplicates("momentum").to_string(index=False))
    n_pass = int(df["gate_pass"].sum())
    print(f"\n{n_pass}/{len(df)} combos PASS the decision gate.")
    print(f"full results -> {out}")


if __name__ == "__main__":
    main()
