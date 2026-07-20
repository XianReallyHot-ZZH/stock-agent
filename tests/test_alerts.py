"""Tests for tracker.alerts — 九条提醒规则触发。"""
from stockagent.tracker import alerts


def _snap(**kw):
    base = {"name": kw.pop("name", "X"), "style": kw.pop("style", "growth")}
    base.update(kw)
    return base


def _idx(breakout_dir="none", grade=0, above_ma=False, ma_trend_up=False, choppy=False, valid=True):
    return {"indices": {"000300": {
        "name": "沪深300", "valid": valid,
        "diagnosis": {
            "breakout": {"direction": breakout_dir, "grade": grade},
            "trend": {"above_ma": above_ma, "ma_trend_up": ma_trend_up},
            "choppy": choppy,
        }}}, "valuation": {}, "style": {}, "period": 60}


def _rules(a): return [x["rule"] for x in a]


def test_E2_breakdown_and_F1():
    a = alerts.evaluate({}, _idx(breakout_dir="down", grade=3, above_ma=False, ma_trend_up=False))
    assert "E2" in _rules(a) and "F1" in _rules(a)
    assert any(x["rule"] == "E2" and x["level"] == "warn" for x in a)


def test_E1_breakout_info():
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, above_ma=True, ma_trend_up=True))
    assert any(x["rule"] == "E1" and x["level"] == "info" for x in a)


def test_E1_choppy_suppressed():
    # 震荡市的突破信号被抑制(E1 不触发 info)
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, choppy=True))
    assert not any(x["rule"] == "E1" and x["level"] == "info" for x in a)


def test_E_low_grade_not_triggered():
    # grade 1(< 2 有效阈值)不触发 E1/E2
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=1))
    assert "E1" not in _rules(a) and "E2" not in _rules(a)


def test_D_chip_phase_bear_and_bull():
    a_bear = alerts.evaluate({"X": _snap(name="煤炭", chip_phase="兑现中段")}, None)
    assert any(x["rule"] == "D" and x["level"] == "warn" for x in a_bear)
    a_bull = alerts.evaluate({"X": _snap(name="银行", chip_phase="见底")}, None)
    assert any(x["rule"] == "D" and x["level"] == "info" for x in a_bull)


def test_B1_dividend_yield_threshold():
    a_hi = alerts.evaluate({"X": _snap(name="银行", style="value", dividend_yield=0.06)}, None)
    assert "B1" in _rules(a_hi)
    a_lo = alerts.evaluate({"X": _snap(name="银行", style="value", dividend_yield=0.03)}, None)
    assert "B1" not in _rules(a_lo)


def test_B1_only_for_value_style():
    # 成长型高股息率不触发(股息率是价值型指标)
    a = alerts.evaluate({"X": _snap(name="半导体", style="growth", dividend_yield=0.08)}, None)
    assert "B1" not in _rules(a)


def test_A1A2_earnings_bear():
    a = alerts.evaluate({"X": _snap(name="半导体", earnings_label="业绩恶化", earnings_yoy=-30)}, None)
    assert any(x["rule"] == "A1/A2" and x["level"] == "warn" for x in a)
