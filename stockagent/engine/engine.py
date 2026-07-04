"""Engine orchestration: load data -> run regime/rotation/stop -> produce signals.

`Engine.generate_signals(date, current_holdings)` returns the structured signal dict
that the report writer consumes. Pure w.r.t. its inputs (store + date + holdings);
no side effects. Reused by the live scheduler and by tests.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ..config import Config, get_config
from ..data import Store
from . import momentum as mom
from . import stop as stop_mod
from .portfolio import TargetPlan, decide_target, diff
from .regime import regime_state
from .signals import current_signal


class Engine:
    def __init__(self, store: Store, config: Optional[Config] = None, lookback: int = 260):
        self.store = store
        self.config = config or get_config()
        self.lookback = lookback
        p = self.config.params
        self.benchmark = p["regime"]["benchmark_symbol"]
        self.risk_off = p["regime"]["risk_off_symbol"]
        self.regime_ma = int(p["regime"]["ma_period"])
        self.stop_pct = float(p["stop"]["trailing_pct"])
        self.k = int(p["portfolio"]["k"])

    # ---- data helpers ----
    def _close(self, symbol: str, end: str) -> pd.Series:
        df = self.store.get_series(symbol, end=end)
        if len(df) > self.lookback:
            df = df.iloc[-self.lookback:]
        return df["close"] if "close" in df else pd.Series(dtype=float)

    def _close_since(self, symbol: str, entry_date: str, end: str) -> pd.Series:
        df = self.store.get_series(symbol, start=entry_date, end=end)
        return df["close"] if "close" in df and len(df) else pd.Series(dtype=float)

    # ---- main ----
    def generate_signals(self, decision_date: str, current_holdings: Optional[dict] = None) -> dict:
        """Produce the full signal set as of `decision_date` (data up to & including it).

        current_holdings: {symbol: {"weight": float, "entry_date": "YYYY-MM-DD"}}.
        If None/empty, the engine treats the portfolio as flat (fresh start).
        """
        current_holdings = current_holdings or {}
        params = self.config.params

        # 1) regime
        bench_close = self._close(self.benchmark, decision_date)
        regime = regime_state(bench_close, self.regime_ma)
        bench_ma = float(bench_close.tail(self.regime_ma).mean()) if len(bench_close) >= self.regime_ma else float("nan")
        bench_last = float(bench_close.iloc[-1]) if len(bench_close) else float("nan")

        # 2) rotation scores (V2.1: pluggable signal — momentum or reversion)
        close_by_sym = {s: self._close(s, decision_date) for s in self.config.rotation_symbols()}
        scored = current_signal(params).score_universe(close_by_sym, params)

        # 3) stops on current holdings (daily protection layer)
        stopped = []
        stop_warnings = []
        for sym, info in current_holdings.items():
            if sym in (self.risk_off, self.benchmark):
                continue
            entry = info.get("entry_date") if isinstance(info, dict) else None
            if not entry:
                continue
            cs = self._close_since(sym, entry, decision_date)
            if len(cs) == 0:
                continue
            peak = stop_mod.peak_since_entry(cs)
            last = float(cs.iloc[-1])
            dd = (last / peak - 1.0) if peak > 0 else 0.0
            if stop_mod.stop_triggered(cs, self.stop_pct):
                stopped.append(sym)
            elif dd <= -(self.stop_pct - 0.02):  # within 2pp of stop -> warn
                stop_warnings.append({"symbol": sym, "drawdown_from_peak": round(dd, 4)})

        # 4) target plan (priority: stop > rotation > regime, applied inside)
        plan: TargetPlan = decide_target(scored, regime, params, self.risk_off, stopped=stopped)

        # 5) actions vs current holdings
        current_w = {
            sym: (info.get("weight", 0.0) if isinstance(info, dict) else float(info))
            for sym, info in current_holdings.items()
        }
        actions = diff(current_w, plan, self.risk_off)

        return {
            "decision_date": decision_date,
            "regime": regime,
            "benchmark": {
                "symbol": self.benchmark,
                "last": round(bench_last, 4),
                "ma": round(bench_ma, 4),
                "above_ma": bool(bench_last > bench_ma) if not pd.isna(bench_ma) else None,
            },
            "ranking": scored[["symbol", "score", "eligible"]].to_dict("records"),
            "top_k": plan.picks,
            "target": plan.target,
            "cash_weight": round(plan.cash_weight, 4),
            "stopped": stopped,
            "actions": actions,
            "warnings": {
                "near_stop": stop_warnings,
                "near_regime_line": (
                    abs(bench_last - bench_ma) / bench_ma < 0.02
                    if not pd.isna(bench_ma) and bench_ma > 0
                    else False
                ),
            },
        }
