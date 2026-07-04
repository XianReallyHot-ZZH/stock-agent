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
from . import indicators as ind
from . import stop as stop_mod
from .portfolio import TargetPlan, decide_target, diff
from .regime import regime_state
from .signals import current_signal, get_signal


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

        # 2) rotation scores (V2.1: pluggable signal; V2.3: ctx carries share data)
        close_by_sym = {s: self._close(s, decision_date) for s in self.config.rotation_symbols()}
        share_by_sym = {}
        for s in self.config.rotation_symbols():
            sc = self.store.get_scale_series(s, end=decision_date)
            share_by_sym[s] = sc["shares"] if "shares" in sc.columns else pd.Series(dtype=float)
        ctx = {"share": share_by_sym, "close": close_by_sym}
        scored = current_signal(params).score_universe(close_by_sym, params, ctx=ctx)

        # 3) stops on current holdings (daily protection layer)
        # V2.4: signal-specific exit via check_exits (share_flow uses flow-stop);
        # other signals fall back to price trailing_pct stop.
        sig = current_signal(params)
        stopped = []
        stop_warnings = []
        if hasattr(sig, "check_exits"):
            stopped = sig.check_exits(current_holdings, ctx, params)
            # warnings: compute near-flow-stop for reporting (best-effort)
            for sym, info in current_holdings.items():
                if sym in stopped:
                    continue
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
                if dd <= -(0.20 - 0.02):  # near price backstop
                    stop_warnings.append({"symbol": sym, "drawdown_from_peak": round(dd, 4)})
        else:
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
                elif dd <= -(self.stop_pct - 0.02):
                    stop_warnings.append({"symbol": sym, "drawdown_from_peak": round(dd, 4)})

        # 4) target plan (priority: stop > rotation > regime, applied inside)
        plan: TargetPlan = decide_target(scored, regime, params, self.risk_off, stopped=stopped)

        # 5) actions vs current holdings
        current_w = {
            sym: (info.get("weight", 0.0) if isinstance(info, dict) else float(info))
            for sym, info in current_holdings.items()
        }
        actions = diff(current_w, plan, self.risk_off)

        # 6) rich per-symbol detail for the report (signal-aware describe)
        sig = current_signal(params)
        signal_name = params.get("rotation", {}).get("signal", {}).get("name", "momentum")
        ranking_top = scored.head(6)
        details = []
        for _, row in ranking_top.iterrows():
            sym = row["symbol"]
            desc = sig.describe_symbol(self._close(sym, decision_date), params, ctx={**ctx, "symbol": sym})
            details.append({"symbol": sym, "score": row["score"], "eligible": bool(row["eligible"]), "summary": desc["summary"]})

        holdings_detail = []
        for sym, w in plan.target.items():
            if sym == self.risk_off:
                continue
            info = current_holdings.get(sym, {}) if isinstance(current_holdings.get(sym), dict) else {}
            entry = info.get("entry_date")
            dd = None
            if entry:
                cs = self._close_since(sym, entry, decision_date)
                if len(cs):
                    dd = round(float(ind.drawdown_from_peak(cs)), 4)
            desc = sig.describe_symbol(self._close(sym, decision_date), params, ctx={**ctx, "symbol": sym})
            holdings_detail.append({"symbol": sym, "weight": round(w, 4), "drawdown_from_peak": dd, "summary": desc["summary"]})

        bench_dist = (bench_last / bench_ma - 1.0) if (not pd.isna(bench_ma) and bench_ma > 0) else None

        # 7) strength board — ALWAYS momentum (60/120/250-day returns), observational,
        #    independent of the active trading signal. Shown in the report as "what's
        #    hot now", NOT a trade signal.
        mom_sig = get_signal("momentum")
        mom_scored = mom_sig.score_universe(close_by_sym, params)
        strength_board = []
        for _, row in mom_scored.head(6).iterrows():
            sym = row["symbol"]
            desc = mom_sig.describe_symbol(self._close(sym, decision_date), params)
            strength_board.append({"symbol": sym, "score": row["score"], "summary": desc["summary"]})

        return {
            "decision_date": decision_date,
            "signal_name": signal_name,
            "risk_off_symbol": self.risk_off,
            "regime": regime,
            "benchmark": {
                "symbol": self.benchmark,
                "last": round(bench_last, 4),
                "ma": round(bench_ma, 4),
                "above_ma": bool(bench_last > bench_ma) if not pd.isna(bench_ma) else None,
                "distance_pct": round(bench_dist, 4) if bench_dist is not None else None,
            },
            "ranking": scored[["symbol", "score", "eligible"]].to_dict("records"),
            "details": details,
            "holdings_detail": holdings_detail,
            "strength_board": strength_board,
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
