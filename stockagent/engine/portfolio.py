"""Portfolio decision logic (Q3-Q8): combine regime + rotation + stops into a target.

Priority hard rule (Q13): stop > rotation > regime. This module computes the
*ideal* target holdings for a decision date. Simulation (T+1 fills, costs) lives
in the backtest; live execution lives in the scheduler. Both reuse this logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .regime import RISK_OFF, RISK_ON
from .signals._common import select_top_k


@dataclass
class TargetPlan:
    regime: str
    target: dict[str, float] = field(default_factory=dict)  # symbol -> weight (sums to ~1)
    cash_weight: float = 0.0
    picks: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)  # symbol -> reason


def decide_target(
    scored: pd.DataFrame,
    regime: str,
    params: dict,
    risk_off_symbol: str,
    stopped: list[str] | None = None,
    held: set | None = None,
    super_sticky: bool = False,
) -> TargetPlan:
    """Compute target holdings applying priority stop > rotation > regime."""
    stopped = stopped or []
    k = int(params.get("portfolio", {}).get("k", 3))
    plan = TargetPlan(regime=regime)

    # Base target from regime + rotation
    if regime == RISK_OFF:
        plan.target = {risk_off_symbol: 1.0}
        plan.cash_weight = 1.0
    else:
        picks = select_top_k(scored, k, held=held, super_sticky=super_sticky)
        plan.picks = picks
        if not picks:
            plan.target = {risk_off_symbol: 1.0}  # nothing eligible -> cash
            plan.cash_weight = 1.0
        else:
            w = 1.0 / k  # equal weight of 1/k each; unused slots -> cash
            plan.target = {s: w for s in picks}
            plan.cash_weight = 1.0 - w * len(picks)

    # Stops override: a stopped symbol is sold, its slot parks in cash (not refilled
    # until next weekly rotation — Q8).
    if stopped:
        slot_w = 1.0 / k if regime == RISK_ON else 0.0
        for s in stopped:
            if s in plan.target:
                del plan.target[s]
                plan.cash_weight += slot_w
                plan.stopped.append(s)
                plan.reasons[s] = "stop_loss"

    # annotate rotation reasons
    for s in plan.target:
        if s != risk_off_symbol and s not in plan.reasons:
            plan.reasons[s] = "rotation_top_k"
    if regime == RISK_OFF:
        plan.reasons[risk_off_symbol] = "risk_off"

    return plan


def diff(current: dict[str, float], plan: TargetPlan, risk_off_symbol: str,
         rebalance_threshold: float = 0.0) -> list[dict]:
    """Produce buy/sell/hold actions between current holdings and the target plan.

    V2.9: if rebalance_threshold > 0, small weight deviations are labeled 'hold'
    (no action) instead of generating micro buy/sell.
    """
    target = plan.target
    actions: list[dict] = []
    for s, w in target.items():
        cur = current.get(s, 0.0)
        if s == risk_off_symbol and w > 0:
            actions.append({"type": "to_cash" if cur < w - 1e-9 else "hold_cash", "symbol": s, "weight": w})
        elif abs(cur - w) <= rebalance_threshold:
            actions.append({"type": "hold", "symbol": s, "weight": w, "note": "微调不操作"})
        elif cur < w - 1e-9:
            actions.append({"type": "buy", "symbol": s, "weight": w, "reason": plan.reasons.get(s, "")})
        else:
            actions.append({"type": "hold", "symbol": s, "weight": w})
    for s, w in current.items():
        if s not in target:
            actions.append({"type": "sell", "symbol": s, "weight": w, "reason": plan.reasons.get(s, "rotated_out")})
    return actions
