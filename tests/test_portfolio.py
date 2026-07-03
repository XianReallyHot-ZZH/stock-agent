import pandas as pd

from stockagent.engine.portfolio import decide_target, diff

PARAMS = {"portfolio": {"k": 3}}
CASH = "511990"


def scored(symbols_scores_eligible):
    rows = []
    for sym, sc, elig in symbols_scores_eligible:
        rows.append({"symbol": sym, "score": sc, "above_ma": elig, "eligible": elig, "last_close": 1.0, "len": 200})
    return pd.DataFrame(rows)


def test_risk_off_all_cash():
    plan = decide_target(scored([("A", 0.5, True)]), "risk_off", PARAMS, CASH)
    assert plan.target == {CASH: 1.0}
    assert plan.cash_weight == 1.0


def test_risk_on_equal_weight_top3():
    plan = decide_target(
        scored([("A", 0.5, True), ("B", 0.4, True), ("C", 0.3, True), ("D", 0.2, True)]),
        "risk_on", PARAMS, CASH,
    )
    assert plan.picks == ["A", "B", "C"]
    assert plan.target == {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    assert abs(plan.cash_weight) < 1e-9


def test_risk_on_no_eligible_goes_cash():
    plan = decide_target(scored([("A", 0.5, False)]), "risk_on", PARAMS, CASH)
    assert plan.target == {CASH: 1.0}


def test_stop_removes_holding_and_parks_cash():
    # A is top-K but is stopped -> removed, its 1/3 slot -> cash
    plan = decide_target(
        scored([("A", 0.5, True), ("B", 0.4, True), ("C", 0.3, True)]),
        "risk_on", PARAMS, CASH, stopped=["A"],
    )
    assert "A" not in plan.target
    assert "B" in plan.target and "C" in plan.target
    assert "A" in plan.stopped
    # 2 slots * 1/3 = 2/3 invested, 1/3 cash (A's slot)
    assert abs(plan.cash_weight - 1 / 3) < 1e-9


def test_diff_buys_sells_holds():
    plan = decide_target(
        scored([("A", 0.5, True), ("B", 0.4, True), ("C", 0.3, True)]),
        "risk_on", PARAMS, CASH,
    )
    # currently hold B and D (D not in target -> sell)
    current = {"B": 1 / 3, "D": 1 / 3}
    acts = diff(current, plan, CASH)
    types = {a["symbol"]: a["type"] for a in acts}
    assert types.get("A") == "buy"
    assert types.get("C") == "buy"
    assert types.get("B") == "hold"
    assert types.get("D") == "sell"
