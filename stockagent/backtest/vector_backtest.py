"""Self-researched backtester (Q11): transparent event loop over trade days.

Rules implemented to honor A-share reality + the seven sins:
- Signal computed on close of day T; fills at OPEN of T+1 (+/- single_side_cost). No lookahead.
- T+1 enforced: a position bought at open T cannot be sold at open T+1 unless held
  overnight (entry_date < T). Stops naturally skip same-day entries.
- Rotation rebalances only on Friday (weekly); regime flips + stops act every day.
- Cash/risk-off capital earns the money-market ETF's daily return.
- Costs: single_side_cost each way (ETF has no stamp duty).

Reuses the SAME pure functions as the live Engine (score_universe/regime_state/
stop_triggered/decide_target) so backtest and live are consistent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import Config, get_config
from ..data import Store
from ..engine import stop as stop_mod
from ..engine.portfolio import decide_target
from ..engine.regime import RISK_OFF, RISK_ON, RegimeFilter
from ..engine.signals import current_signal
from . import metrics as M

log = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    equity: pd.Series
    trades: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    benchmark: dict = field(default_factory=dict)
    gate: dict = field(default_factory=dict)
    benchmark_equity: pd.Series = None  # csi300 buy&hold, aligned to equity
    sixty_forty_equity: pd.Series = None


def _load_panel(store: Store, symbols: list[str], benchmark: str, risk_off: str):
    bench = store.get_series(benchmark)
    timeline = bench.index
    all_syms = list(dict.fromkeys(symbols + [benchmark, risk_off]))
    closes = pd.DataFrame(index=timeline)
    opens = pd.DataFrame(index=timeline)
    for s in all_syms:
        df = store.get_series(s).reindex(timeline)
        closes[s] = df["close"] if "close" in df else np.nan
        opens[s] = df["open"] if "open" in df else np.nan
    # V2.3: ETF shares (for share_flow signal). Rotation symbols only; SSE has history,
    # SZSE sparse/empty (signal handles NaN). Reindex to price timeline (forward-fill
    # within gaps so share trend is continuous across non-reporting days).
    shares_wide = pd.DataFrame(index=timeline)
    for s in symbols:
        sc = store.get_scale_series(s)
        if len(sc) and "shares" in sc.columns:
            shares_wide[s] = sc["shares"].reindex(timeline).ffill()
    return timeline, closes, opens, shares_wide


def run_backtest(
    store: Store,
    config: Config = None,
    start: str | None = None,
    end: str | None = None,
    capital: float = 1_000_000.0,
) -> BacktestResult:
    config = config or get_config()
    p = config.params
    rot_syms = config.rotation_symbols()
    bench = p["regime"]["benchmark_symbol"]
    risk_off = p["regime"]["risk_off_symbol"]
    k = int(p["portfolio"]["k"])
    regime_ma = int(p["regime"]["ma_period"])
    stop_pct = float(p["stop"]["trailing_pct"])
    stop_method = p.get("stop", {}).get("method", "fixed")
    atr_period = int(p.get("stop", {}).get("atr_period", 14))
    atr_mult = float(p.get("stop", {}).get("atr_mult", 3.0))
    sig = current_signal(p)  # V2.4: resolve once for signal-specific exits
    rebal_dow = int(p["rotation"].get("rebalance_weekday", 4))
    cost = float(p["backtest"]["single_side_cost"])
    rebal_thr = float(p.get("portfolio", {}).get("rebalance_threshold", 0.0))

    timeline, closes, opens, shares_wide = _load_panel(store, rot_syms, bench, risk_off)
    ro_ret = closes[risk_off].pct_change().fillna(0.0)

    days = timeline[(timeline >= (start or timeline[0])) & (timeline <= (end or timeline[-1]))]

    # V2.7: precompute regime labels (RegimeFilter with band + confirmation)
    _band = float(p.get("regime", {}).get("band_pct", 0.0))
    _confirm = int(p.get("regime", {}).get("confirm_days", 1))
    rf = RegimeFilter(regime_ma, band_pct=_band, confirm_days=_confirm)
    regime_labels = rf.process_series(closes[bench].dropna())

    cash = capital
    shares: dict[str, float] = {}
    entry_date: dict[str, str] = {}
    equity_curve = []
    trades = []
    pending: list = []  # orders from prior day's signal, to fill at today's open

    def equity_at(d):
        cl = closes.loc[d]
        return cash + sum(sh * cl[s] for s, sh in shares.items() if s in cl and not pd.isna(cl[s]))

    for i, d in enumerate(days):
        dt = pd.to_datetime(d)
        # 1) cash grows at money-market rate (t-1 -> t)
        if i > 0:
            cash *= (1.0 + float(ro_ret.loc[d])) if d in ro_ret.index else 1.0

        op = opens.loc[d]
        cl = closes.loc[d]

        # 2) execute pending orders at today's OPEN
        for order in pending:
            sym = order["symbol"]
            if sym not in op or pd.isna(op[sym]):
                continue  # can't fill (no data) -> carry order? drop for V1
            px = float(op[sym])
            if order["type"] == "sell_all":
                sh = shares.get(sym, 0.0)
                if sh > 0:
                    cash += sh * px * (1 - cost)
                    trades.append({"date": d, "side": "sell", "symbol": sym, "shares": sh, "price": px})
                    shares[sym] = 0.0
                    entry_date.pop(sym, None)
            elif order["type"] == "set_weight":
                tgt_value = order["weight"] * order["equity_ref"]
                cur_value = shares.get(sym, 0.0) * px
                delta = tgt_value - cur_value
                # V2.9: skip micro-rebalancing (deviation below threshold)
                if tgt_value > 0 and abs(delta) / tgt_value < rebal_thr:
                    continue
                if delta > 1 and px > 0:
                    buy_sh = delta / px
                    shares[sym] = shares.get(sym, 0.0) + buy_sh
                    cash -= delta * (1 + cost)
                    if sym not in entry_date:
                        entry_date[sym] = d
                    trades.append({"date": d, "side": "buy", "symbol": sym, "shares": buy_sh, "price": px})
                elif delta < -1 and shares.get(sym, 0.0) > 0:
                    sell_sh = min(shares[sym], -delta / px)
                    shares[sym] -= sell_sh
                    cash += sell_sh * px * (1 - cost)
                    trades.append({"date": d, "side": "sell", "symbol": sym, "shares": sell_sh, "price": px})
                    if shares[sym] <= 1e-9:
                        shares[sym] = 0.0
                        entry_date.pop(sym, None)
        pending = []

        # 3) equity at close
        eq = equity_at(d)
        equity_curve.append((d, eq))

        # 4) signal at close -> pending orders for next open
        regime = regime_labels.get(d, RISK_ON)  # V2.7: precomputed RegimeFilter

        forced_sells = []
        # V2.4: signal-specific exit via check_exits; fallback to price stop.
        if hasattr(sig, "check_exits"):
            positions = {
                sym: {"entry_date": entry_date[sym]}
                for sym in list(shares.keys())
                if shares.get(sym, 0) > 0 and sym != risk_off
                and entry_date.get(sym) and entry_date[sym] < d
            }
            ctx_stop = {
                "close": {sym: closes[sym].loc[:d] for sym in positions if sym in closes.columns},
                "share": {sym: shares_wide[sym].loc[:d] for sym in positions if sym in shares_wide.columns},
            }
            forced_sells = sig.check_exits(positions, ctx_stop, p)
        else:
            # stops on holdings (daily), skip same-day entries (T+1)
            for sym in list(shares.keys()):
                if shares.get(sym, 0) <= 0 or sym == risk_off:
                    continue
                ed = entry_date.get(sym)
                if not ed or ed >= d:
                    continue
                cs = closes[sym].loc[ed:d].dropna()
                if stop_method == "atr":
                    _hit = len(cs) and stop_mod.stop_triggered_vol(cs, atr_period, atr_mult)
                else:
                    _hit = len(cs) and stop_mod.stop_triggered(cs, stop_pct)
                if _hit:
                    forced_sells.append(sym)

        if regime == RISK_OFF:
            # liquidate everything to cash
            for sym in list(shares.keys()):
                if shares.get(sym, 0) > 0 and sym != risk_off:
                    forced_sells.append(sym)
            for sym in set(forced_sells):
                pending.append({"type": "sell_all", "symbol": sym})
        else:
            for sym in set(forced_sells):
                pending.append({"type": "sell_all", "symbol": sym})
            # rotation only on the chosen weekday
            if dt.dayofweek == rebal_dow:
                close_slice = {s: closes[s].loc[:d].dropna() for s in rot_syms}
                share_slice = {s: shares_wide[s].loc[:d].dropna() for s in rot_syms if s in shares_wide.columns}
                scored = sig.score_universe(close_slice, p, ctx={"share": share_slice})
                # V2.8: sticky positions for STICKY signals (share_flow)
                _held_set = {sym for sym, sh in shares.items() if sh > 0 and sym != risk_off}
                _held = _held_set if getattr(sig, "STICKY", False) else None
                _super = getattr(sig, "SUPER_STICKY", False)
                plan = decide_target(scored, RISK_ON, p, risk_off, stopped=forced_sells, held=_held, super_sticky=_super)
                # BUGFIX: sell currently-held symbols that rotated OUT of target
                _target_syms = set(plan.target.keys()) - {risk_off}
                _rotate_out = _held_set - _target_syms
                for sym in _rotate_out:
                    pending.append({"type": "sell_all", "symbol": sym})
                eq_ref = eq
                for sym, w in plan.target.items():
                    if sym == risk_off:
                        continue
                    pending.append({"type": "set_weight", "symbol": sym, "weight": w, "equity_ref": eq_ref})

    equity = pd.Series([v for _, v in equity_curve], index=[d for d, _ in equity_curve], dtype=float)
    equity.name = "strategy"

    strat_m = M.summarize(equity, "rotation_strategy")
    strat_m["n_trades"] = len(trades)
    # turnover (annualized, one-way)
    turnover_values = [abs(t["shares"] * t["price"]) for t in trades]
    years = max(len(equity) / M.PERIODS_PER_YEAR, 1e-6)
    strat_m["annual_turnover"] = round(sum(turnover_values) / capital / years, 2)

    # benchmarks
    bench_eq = (closes[bench].loc[days].dropna() / closes[bench].loc[days].dropna().iloc[0]) * capital
    bm_m = M.summarize(bench_eq, "csi300_buyhold")

    # 60/40 (60% benchmark, 40% money-market)
    ro_eq = (closes[risk_off].loc[days].dropna() / closes[risk_off].loc[days].dropna().iloc[0]) * capital
    aligned = pd.concat([bench_eq, ro_eq], axis=1).dropna()
    sixty_forty = None
    if len(aligned):
        bench_eq_a = aligned.iloc[:, 0]
        ro_eq_a = aligned.iloc[:, 1]
        sixty_forty = 0.6 * (bench_eq_a / bench_eq_a.iloc[0]) + 0.4 * (ro_eq_a / ro_eq_a.iloc[0])
        sixty_forty = sixty_forty * capital
        sf_m = M.summarize(sixty_forty, "60_40")
    else:
        sf_m = {}

    # decision gate (Q11): lower max drawdown than benchmark AND annualized not worse
    gate = {
        "pass": bool(
            strat_m["max_drawdown"] > bm_m["max_drawdown"]  # less negative = smaller drawdown
            and strat_m["annualized"] >= bm_m["annualized"]
        ),
        "strategy_max_dd": strat_m["max_drawdown"],
        "benchmark_max_dd": bm_m["max_drawdown"],
        "strategy_annualized": strat_m["annualized"],
        "benchmark_annualized": bm_m["annualized"],
        "verdict": "",
    }
    gate["verdict"] = (
        "PASS: lower drawdown AND >= benchmark return"
        if gate["pass"]
        else "FAIL: not beating buy&hold on a risk-adjusted basis — do NOT go live"
    )

    return BacktestResult(equity=equity, trades=trades, metrics=strat_m,
                          benchmark={"csi300_buyhold": bm_m, "60_40": sf_m}, gate=gate,
                          benchmark_equity=bench_eq, sixty_forty_equity=sixty_forty if isinstance(sixty_forty, pd.Series) else None)
