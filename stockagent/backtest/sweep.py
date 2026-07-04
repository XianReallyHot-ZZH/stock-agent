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
# V2.1 reversion signal sub-grid (expand freely).
REVERSION_GRID = {
    ("portfolio", "k"): [3, 5],
    ("regime", "ma_period"): [120, 200],
    ("stop", "trailing_pct"): [0.08],
    ("rotation", "reversion", "rsi_period"): [14],
    ("rotation", "reversion", "oversold_threshold"): [30, 40],
    ("rotation", "reversion", "long_ma"): [120, 250],
}
BB_MACD_GRID = {
    ("portfolio", "k"): [3, 5],
    ("regime", "ma_period"): [120, 200],
    ("stop", "trailing_pct"): [0.08],
    ("rotation", "bb_macd", "mode"): ["dip", "trend", "both"],
    ("rotation", "bb_macd", "pctb_low"): [0.1, 0.2],
    ("rotation", "bb_macd", "pctb_high"): [1.0],
    ("rotation", "bb_macd", "long_ma"): [120, 250],
}
SHARE_FLOW_GRID = {
    ("portfolio", "k"): [3, 5],
    ("regime", "ma_period"): [120, 200],
    ("stop", "trailing_pct"): [0.08],
    ("rotation", "share_flow", "trend_days"): [40, 60, 120],
    ("rotation", "share_flow", "min_share_change"): [0.0, 0.05],
    ("rotation", "share_flow", "flow_stop_pct"): [0.05, 0.10, 0.20],
}
MOM_BY_NAME = {name: (wins, wts) for name, wins, wts in MOMENTUM}

_ROW_COLS = ["signal", "K", "regime_ma", "stop%", "gate_ma", "momentum", "windows",
             "rsi_period", "oversold", "long_ma",
             "bb_mode", "pctb_low", "pctb_high", "bb_long_ma",
             "share_trend", "share_min_chg", "flow_stop",
             "ann", "mdd", "calmar", "sharpe", "turnover", "gate_pass"]


def _run_one(store, base, overrides, start, end, signal, extra):
    cfg = make_config(base, overrides)
    res = run_backtest(store, cfg, start=start, end=end)
    m, g = res.metrics, res.gate
    row = {c: None for c in _ROW_COLS}
    row.update({
        "signal": signal,
        "K": overrides[("portfolio", "k")],
        "regime_ma": overrides[("regime", "ma_period")],
        "stop%": overrides[("stop", "trailing_pct")],
        "ann": m["annualized"], "mdd": m["max_drawdown"],
        "calmar": m["calmar"], "sharpe": m["sharpe"],
        "turnover": m["annual_turnover"], "gate_pass": g["pass"],
    })
    row.update(extra)
    return row


def _ranked(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(["gate_pass", "calmar"], ascending=[False, False]).reset_index(drop=True)


def row_to_overrides(row) -> dict:
    """Rebuild the make_config overrides dict from a sweep-result row (signal-aware)."""
    sig = row["signal"]
    o = {
        ("rotation", "signal", "name"): sig,
        ("portfolio", "k"): int(row["K"]),
        ("regime", "ma_period"): int(row["regime_ma"]),
        ("stop", "trailing_pct"): float(row["stop%"]),
    }
    if sig == "momentum":
        wins, wts = MOM_BY_NAME[row["momentum"]]
        o[("rotation", "trend_gate_ma")] = int(row["gate_ma"])
        o[("rotation", "momentum", "windows")] = wins
        o[("rotation", "momentum", "weights")] = wts
    elif sig == "bb_macd":
        o[("rotation", "bb_macd", "mode")] = row["bb_mode"]
        o[("rotation", "bb_macd", "pctb_low")] = float(row["pctb_low"])
        o[("rotation", "bb_macd", "pctb_high")] = float(row["pctb_high"])
        o[("rotation", "bb_macd", "long_ma")] = int(row["bb_long_ma"])
    elif sig == "share_flow":
        o[("rotation", "share_flow", "trend_days")] = int(row["share_trend"])
        o[("rotation", "share_flow", "min_share_change")] = float(row["share_min_chg"])
        o[("rotation", "share_flow", "flow_stop_pct")] = float(row["flow_stop"])
    else:  # reversion
        o[("rotation", "reversion", "rsi_period")] = int(row["rsi_period"])
        o[("rotation", "reversion", "oversold_threshold")] = float(row["oversold"])
        o[("rotation", "reversion", "long_ma")] = int(row["long_ma"])
    return o


def evaluate_momentum(store, base: Config, start: str, end: str) -> pd.DataFrame:
    """Sweep the momentum signal's grid over [start,end]."""
    rows = []
    mkeys = list(GRID.keys())
    for vals in itertools.product(*[GRID[k] for k in mkeys]):
        for mname, wins, wts in MOMENTUM:
            overrides = dict(zip(mkeys, vals))
            overrides[("rotation", "signal", "name")] = "momentum"
            overrides[("rotation", "momentum", "windows")] = wins
            overrides[("rotation", "momentum", "weights")] = wts
            extra = {"gate_ma": overrides[("rotation", "trend_gate_ma")],
                     "momentum": mname, "windows": str(wins)}
            try:
                rows.append(_run_one(store, base, overrides, start, end, "momentum", extra))
            except Exception:
                continue
    return _ranked(rows)


def evaluate_reversion(store, base: Config, start: str, end: str) -> pd.DataFrame:
    """Sweep ONLY the reversion signal's grid over [start,end]. Faster than evaluate_grid."""
    rows = []
    rkeys = list(REVERSION_GRID.keys())
    for vals in itertools.product(*[REVERSION_GRID[k] for k in rkeys]):
        overrides = dict(zip(rkeys, vals))
        overrides[("rotation", "signal", "name")] = "reversion"
        extra = {"rsi_period": overrides[("rotation", "reversion", "rsi_period")],
                 "oversold": overrides[("rotation", "reversion", "oversold_threshold")],
                 "long_ma": overrides[("rotation", "reversion", "long_ma")]}
        try:
            rows.append(_run_one(store, base, overrides, start, end, "reversion", extra))
        except Exception:
            continue
    return _ranked(rows)


def evaluate_bb_macd(store, base: Config, start: str, end: str) -> pd.DataFrame:
    """Sweep ONLY the bb_macd signal's grid over [start,end]."""
    rows = []
    bkeys = list(BB_MACD_GRID.keys())
    for vals in itertools.product(*[BB_MACD_GRID[k] for k in bkeys]):
        overrides = dict(zip(bkeys, vals))
        overrides[("rotation", "signal", "name")] = "bb_macd"
        extra = {"bb_mode": overrides[("rotation", "bb_macd", "mode")],
                 "pctb_low": overrides[("rotation", "bb_macd", "pctb_low")],
                 "pctb_high": overrides[("rotation", "bb_macd", "pctb_high")],
                 "bb_long_ma": overrides[("rotation", "bb_macd", "long_ma")]}
        try:
            rows.append(_run_one(store, base, overrides, start, end, "bb_macd", extra))
        except Exception:
            continue
    return _ranked(rows)


def evaluate_grid(store, base: Config, start: str, end: str, verbose: bool = True) -> pd.DataFrame:
    """Run ALL signals' param grids over [start,end]. Returns ranked DataFrame with a `signal` column."""
    df_m = evaluate_momentum(store, base, start, end)
    df_r = evaluate_reversion(store, base, start, end)
    df_b = evaluate_bb_macd(store, base, start, end)
    df_s = evaluate_share_flow(store, base, start, end)
    frames = [d for d in (df_m, df_r, df_b, df_s) if len(d)]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(df):
        df = df.sort_values(["gate_pass", "calmar"], ascending=[False, False]).reset_index(drop=True)
    if verbose:
        nm, nr, nb, ns = len(df_m), len(df_r), len(df_b), len(df_s)
        print(f"Evaluated {nm+nr+nb+ns} combos ({nm} momentum + {nr} reversion + {nb} bb_macd + {ns} share_flow) over {start}..{end}")
    return df


def evaluate_share_flow(store, base: Config, start: str, end: str) -> pd.DataFrame:
    """Sweep ONLY the share_flow signal's grid over [start,end]."""
    rows = []
    skeys = list(SHARE_FLOW_GRID.keys())
    for vals in itertools.product(*[SHARE_FLOW_GRID[k] for k in skeys]):
        overrides = dict(zip(skeys, vals))
        overrides[("rotation", "signal", "name")] = "share_flow"
        extra = {"share_trend": overrides[("rotation", "share_flow", "trend_days")],
                 "share_min_chg": overrides[("rotation", "share_flow", "min_share_change")],
                 "flow_stop": overrides[("rotation", "share_flow", "flow_stop_pct")]}
        try:
            rows.append(_run_one(store, base, overrides, start, end, "share_flow", extra))
        except Exception:
            continue
    return _ranked(rows)


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
    show = ["signal", "K", "regime_ma", "stop%", "gate_ma", "momentum", "rsi_period", "oversold", "long_ma",
            "bb_mode", "pctb_low", "pctb_high", "bb_long_ma",
            "share_trend", "share_min_chg", "share_long_ma",
            "ann", "mdd", "calmar", "sharpe", "gate_pass"]
    print("\n=== TOP 10 by gate-pass then Calmar ===")
    print(df[show].head(10).to_string(index=False))
    print("\n=== best per signal (by Calmar) ===")
    for sig in ("momentum", "reversion", "bb_macd", "share_flow"):
        sub = df[df["signal"] == sig]
        if len(sub):
            print(f"  [{sig}] {dict(sub.sort_values('calmar', ascending=False).iloc[0][show])}")
    n_pass = int(df["gate_pass"].sum())
    by_sig = df.groupby("signal")["gate_pass"].sum().to_dict() if len(df) else {}
    print(f"\n{n_pass}/{len(df)} combos PASS the decision gate. per-signal: {by_sig}")
    print(f"full results -> {out}")


if __name__ == "__main__":
    main()
