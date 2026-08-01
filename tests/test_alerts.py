"""Tests for tracker.alerts — 九条提醒规则触发。"""
from stockagent.tracker import alerts


def _snap(**kw):
    base = {"name": kw.pop("name", "X"), "style": kw.pop("style", "growth")}
    base.update(kw)
    return base


def _idx(breakout_dir="none", grade=0, above_ma=False, ma_trend_up=False, choppy=False, valid=True,
         cross_dir=None, cross_ago=3, rc_env_pos=None, tv_dry=None):
    cross = ({"direction": cross_dir, "bars_ago": cross_ago, "date": "2026-07-18"}
             if cross_dir else None)
    base = {"indices": {"000300": {
        "name": "沪深300", "valid": valid,
        "diagnosis": {
            "breakout": {"direction": breakout_dir, "grade": grade},
            "cross": cross,
            "trend": {"above_ma": above_ma, "ma_trend_up": ma_trend_up},
            "choppy": choppy,
        }}}, "valuation": {}, "style": {}, "period": 60}
    if rc_env_pos is not None:
        base["relative_cycle"] = {"valid": True, "env_pos": rc_env_pos}
    if tv_dry is not None:
        base["turnover"] = {"valid": True, "is_dry": tv_dry,
                            "ratio_now": 0.5 if tv_dry else 0.9,
                            "turnover_yi": 5000.0, "win_rate_20": 0.5, "sample": 134}
    return base


def _rules(a): return [x["rule"] for x in a]


# ---- C2 个股提醒(evaluate_stocks)----
def _stock(sym="600519", name="茅台", **kw):
    base = {"name": name}
    base.update(kw)
    return {sym: base}


def test_A2_forecast_turn_bearish():
    s = _stock(forecast={"valid": True, "a2_turn_bearish": True,
                         "latest": {"type": "预减", "yoy": -20.0, "announce_date": "2026-01-15"}})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert any(x["rule"] == "A2" and x["level"] == "warn" for x in a)


def test_A1_forecast_deceleration():
    s = _stock(forecast={"valid": True, "a1_deceleration": True,
                         "latest": {"type": "预增", "yoy": 10.0, "announce_date": "2025-01-15"}})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    # ann 2025-01-15 距 asof 2026-07-22 > 90 天 → 不触发 G1,只 A1
    assert any(x["rule"] == "A1" and x["level"] == "warn" for x in a)
    assert not any(x["rule"] == "G1" for x in a)


def test_G1_forecast_bullish_when_supported():
    # 上游撑+股价未透支 → 正向催化(info)——铜式
    s = _stock(forecast={"valid": True, "a1_deceleration": False, "a2_turn_bearish": False,
                         "latest": {"type": "略增", "yoy": 15.0, "announce_date": "2026-06-15"}},
               leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"divergent": False, "down": False}}},
               reversal={"recent_return": -0.05})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    g1 = [x for x in a if x["rule"] == "G1"]
    assert g1 and g1[0]["level"] == "info" and "正向催化" in g1[0]["msg"]


def test_G1_forecast_warn_when_commodity_weak():
    # 上游背离 → 利好出尽(warn)——锂式
    s = _stock(forecast={"valid": True, "a1_deceleration": False, "a2_turn_bearish": False,
                         "latest": {"type": "预增", "yoy": 800.0, "announce_date": "2026-06-15"}},
               leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"divergent": True, "down": False}}},
               reversal={"recent_return": -0.40})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    g1 = [x for x in a if x["rule"] == "G1"]
    assert g1 and g1[0]["level"] == "warn" and "利好出尽" in g1[0]["msg"]


def test_G1_forecast_warn_when_priced_in():
    # 股价已大涨(透支)→ 利好出尽(warn)
    s = _stock(forecast={"valid": True, "a1_deceleration": False, "a2_turn_bearish": False,
                         "latest": {"type": "预增", "yoy": 50.0, "announce_date": "2026-06-15"}},
               reversal={"recent_return": 0.30})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    g1 = [x for x in a if x["rule"] == "G1"]
    assert g1 and g1[0]["level"] == "warn" and "透支" in g1[0]["msg"]


def test_A3_revenue_deceleration():
    # 上年增速 = base/prev-1 = 115/100-1 = 15%; 当年 yoy=2% → 下滑>5pp
    s = _stock(pitfalls={"revenue": {"valid": True, "yoy": 0.02, "base": 115.0, "prev_base": 100.0},
                         "disclosure": {}})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert any(x["rule"] == "A3" and x["level"] == "warn" for x in a)


def test_Q1_low_quality_fires():
    # 归母高增但扣非掉队 → low_quality → Q1 warn
    s = _stock(earnings_quality={"valid": True, "low_quality": True,
                                 "reason": "增速背离归母+150%/扣非+1%",
                                 "np_yoy": 1.5, "ded_yoy": 0.01, "non_recurring_frac": 0.36})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert any(x["rule"] == "Q1" and x["level"] == "warn" for x in a)


def test_Q1_clean_no_fire():
    s = _stock(earnings_quality={"valid": True, "low_quality": False,
                                 "np_yoy": 0.25, "ded_yoy": 0.24, "non_recurring_frac": 0.05})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert not any(x["rule"] == "Q1" for x in a)


def test_Q1_invalid_no_fire():
    # 扣非稀疏/未对齐 → valid False → 不触发
    s = _stock(earnings_quality={"valid": False, "low_quality": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert not any(x["rule"] == "Q1" for x in a)


# ---- R1 周期反转候选(evaluate,ETF 层)----
def test_R1_cyclical_reversal_fires():
    snap = _snap(name="有色金属ETF", style="cyclic", reversal_score=75.0,
                 days_since_report=30, earnings_yoy=1.2, drawdown=-0.55)
    a = alerts.evaluate({"512400": snap}, None)
    assert any(x["rule"] == "R1" and x["level"] == "info" for x in a)


def test_R1_stale_no_fire():
    # 财报超 150 天 → 时效过期,不发
    snap = _snap(name="化工ETF", style="cyclic", reversal_score=75.0,
                 days_since_report=200, earnings_yoy=1.2, drawdown=-0.55)
    a = alerts.evaluate({"159870": snap}, None)
    assert not any(x["rule"] == "R1" for x in a)


def test_R1_low_score_no_fire():
    # 综合分 <60 → 不发
    snap = _snap(name="煤炭ETF", style="cyclic", reversal_score=40.0,
                 days_since_report=30, earnings_yoy=0.3, drawdown=-0.2)
    a = alerts.evaluate({"515220": snap}, None)
    assert not any(x["rule"] == "R1" for x in a)


def test_R1_non_cyclic_no_fire():
    # 非 cyclic → 规则 gated,不发(即便高分)
    snap = _snap(name="某成长ETF", style="growth", reversal_score=80.0,
                 days_since_report=30, earnings_yoy=1.2, drawdown=-0.55)
    a = alerts.evaluate({"X": snap}, None)
    assert not any(x["rule"] == "R1" for x in a)


# ---- P1 提前埋伏候选(evaluate_stocks,基本面领先)----
def test_P1_ambush_fires():
    # 深跌 + 扭亏 + 干净 + 未涨 + 企稳(近60日 +5%) → 埋伏分≈83 ≥40 → P1
    s = _stock(reversal={"drawdown": -0.6, "recent_return": 0.05},
               pitfalls={"net_profit": {"latest": 16.0, "base": -21.0}},
               earnings_quality={"low_quality": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert any(x["rule"] == "P1" and x["level"] == "info" for x in a)


def test_P1_falling_knife_no_fire():
    # 近60日急跌(<-15%,飞刀)→ 企稳因子重罚 → 不发(即便深跌+扭亏+未兑现)
    s = _stock(reversal={"drawdown": -0.6, "recent_return": -0.20},
               pitfalls={"net_profit": {"latest": 16.0, "base": -21.0}},
               earnings_quality={"low_quality": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "P1" for x in a)


def test_P1_already_priced_no_fire():
    # 近60日涨30%(已兑现)→ 未兑现因子 0 → 不发
    s = _stock(reversal={"drawdown": -0.6, "recent_return": 0.30},
               pitfalls={"net_profit": {"latest": 16.0, "base": -21.0}},
               earnings_quality={"low_quality": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "P1" for x in a)


def test_P1_no_turn_no_fire():
    # 仍亏(earnings_turn 0)→ 不发
    s = _stock(reversal={"drawdown": -0.6, "recent_return": 0.05},
               pitfalls={"net_profit": {"latest": -5.0, "base": -21.0}},
               earnings_quality={"low_quality": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "P1" for x in a)


def test_P1_low_quality_no_fire():
    # 含金量低(Q1)→ quality 0.2 → 分≈17 <40 → 不发
    s = _stock(reversal={"drawdown": -0.6, "recent_return": 0.05},
               pitfalls={"net_profit": {"latest": 16.0, "base": -21.0}},
               earnings_quality={"low_quality": True})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "P1" for x in a)


# ---- M1 上游商品背离(evaluate_stocks)----
def test_M1_commodity_divergence_fires():
    s = _stock(leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"divergent": True, "variety": "碳酸锂",
                                                     "yoy": 1.0, "recent": -0.3}}})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert any(x["rule"] == "M1" and x["level"] == "warn" for x in a)


def test_M1_commodity_up_no_fire():
    s = _stock(leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"divergent": False, "variety": "铜",
                                                     "yoy": 0.35, "recent": 0.02}}})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "M1" for x in a)


def test_M1_no_commodity_no_fire():
    s = _stock(leading={"valid": False, "score": 0.0, "components": {}})
    a = alerts.evaluate_stocks(s, asof="2026-07-30")
    assert not any(x["rule"] == "M1" for x in a)


# ---- M2 上游商品同比转负(evaluate_stocks)----
def test_M2_commodity_down_fires():
    s = _stock(leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"down": True, "divergent": False,
                                                     "variety": "螺纹钢", "yoy": -0.04}}})
    a = alerts.evaluate_stocks(s, asof="2026-08-01")
    assert any(x["rule"] == "M2" and x["level"] == "warn" for x in a)


def test_M2_no_fire_when_commodity_up():
    # 铜向上(divergent/down 都 False)→ M1/M2 都不触发
    s = _stock(leading={"valid": True, "score": 0.0,
                        "components": {"commodity": {"down": False, "divergent": False,
                                                     "variety": "铜", "yoy": 0.35}}})
    a = alerts.evaluate_stocks(s, asof="2026-08-01")
    assert not any(x["rule"] in ("M1", "M2") for x in a)


# ---- E3 超卖:飞刀 vs 套利买点 ----
def test_E3_oversold_knife_warns():
    # 深超卖但近60日暴跌 → 飞刀(warn,非买点)
    s = _stock(price_timing={"deviation": {"valid": True, "pct": 0.02}},
               reversal={"recent_return": -0.40})
    a = alerts.evaluate_stocks(s, asof="2026-08-01")
    e3 = [x for x in a if x["rule"] == "E3"]
    assert e3 and e3[0]["level"] == "warn" and "飞刀" in e3[0]["msg"]


def test_E3_oversold_stable_is_buy():
    # 深超卖且近期未暴跌 → 套利买点(info)
    s = _stock(price_timing={"deviation": {"valid": True, "pct": 0.03}},
               reversal={"recent_return": -0.02})
    a = alerts.evaluate_stocks(s, asof="2026-08-01")
    e3 = [x for x in a if x["rule"] == "E3"]
    assert e3 and e3[0]["level"] == "info" and "套利买点" in e3[0]["msg"]


def test_A3_no_fire_when_accelerating():
    s = _stock(pitfalls={"revenue": {"valid": True, "yoy": 0.30, "base": 130.0, "prev_base": 100.0},
                         "disclosure": {}})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert not any(x["rule"] == "A3" for x in a)


def test_G2_disclosure_deadline_near():
    s = _stock(pitfalls={"revenue": {"valid": False},
                         "disclosure": {"latest_period": "20251231", "deadline": "2026-08-10"}})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")  # 截止 2026-08-10 距今 19 天 ≤45
    assert any(x["rule"] == "G2" and x["level"] == "info" for x in a)


def test_E3_oversold_and_overbought():
    low = _stock(price_timing={"deviation": {"valid": True, "pct": 0.03}})
    a1 = alerts.evaluate_stocks(low, asof="2026-07-22")
    assert any(x["rule"] == "E3" and x["level"] == "info" for x in a1)   # 超卖买点
    high = _stock(price_timing={"deviation": {"valid": True, "pct": 0.97}})
    a2 = alerts.evaluate_stocks(high, asof="2026-07-22")
    assert any(x["rule"] == "E3" and x["level"] == "warn" for x in a2)   # 超买卖点


def test_E4_blue_vs_growth_divergence():
    idx = {"style": {"valid": True, "blue_up": True, "growth_up": False}}
    a = alerts.evaluate_stocks({}, index_diag=idx, asof="2026-07-22")
    assert any(x["rule"] == "E4" and "蓝筹" in x["msg"] for x in a)


def test_stock_no_alerts_when_clean():
    s = _stock(pitfalls={"revenue": {"valid": True, "yoy": 0.20, "base": 120.0, "prev_base": 100.0},
                         "disclosure": {"latest_period": "20251231", "deadline": "2027-04-30"}},
               price_timing={"deviation": {"valid": True, "pct": 0.50}},
               forecast={"valid": False})
    a = alerts.evaluate_stocks(s, asof="2026-07-22")
    assert a == []  # 增速上行 + 偏离中位 + 无预告 + 截止日远 → 无触发


def test_E2_breakdown_and_F1():
    a = alerts.evaluate({}, _idx(breakout_dir="down", grade=3, above_ma=False, ma_trend_up=False,
                                cross_dir="down"))
    assert "E2" in _rules(a) and "F1" in _rules(a)
    assert any(x["rule"] == "E2" and x["level"] == "warn" for x in a)


def test_E1_breakout_info():
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, above_ma=True, ma_trend_up=True,
                                cross_dir="up"))
    assert any(x["rule"] == "E1" and x["level"] == "info" for x in a)


def test_E1_choppy_suppressed():
    # 震荡市的突破信号被抑制(E1 不触发 info)
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, choppy=True, cross_dir="up"))
    assert not any(x["rule"] == "E1" and x["level"] == "info" for x in a)


def test_E_low_grade_not_triggered():
    # grade 1(< 2 有效阈值)不触发 E1/E2
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=1, cross_dir="up"))
    assert "E1" not in _rules(a) and "E2" not in _rules(a)


def test_E1_stale_above_no_recent_cross_not_triggered():
    # 一直在均线上(grade3)但无近期穿越 → 不应误触发 E1(修复前的 bug:在线上就叫突破)
    a = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, above_ma=True, ma_trend_up=True,
                                cross_dir=None))
    assert "E1" not in _rules(a)
    # 穿越但太久远(30 天前)也不算「有效突破」
    a2 = alerts.evaluate({}, _idx(breakout_dir="up", grade=3, cross_dir="up", cross_ago=30))
    assert "E1" not in _rules(a2)


# ---- E5 ⑦相对周期极点(创业板 vs 上证 点差处5年包络上/下沿)----
def test_E5_cycle_extreme_high():
    a = alerts.evaluate({}, _idx(rc_env_pos=0.90))
    assert any(x["rule"] == "E5" and x["level"] == "info" and "创业板" in x["msg"] for x in a)


def test_E5_cycle_extreme_low():
    a = alerts.evaluate({}, _idx(rc_env_pos=0.10))
    assert any(x["rule"] == "E5" and "上证" in x["msg"] for x in a)


def test_E5_no_fire_at_center():
    # 中枢(env_pos 20-80%)不发 E5
    a = alerts.evaluate({}, _idx(rc_env_pos=0.50))
    assert not any(x["rule"] == "E5" for x in a)


# ---- V1 ⑧成交量地量(两市成交额/MA250≤0.6 → 量底信号)----
def test_V1_volume_dry_fires():
    a = alerts.evaluate({}, _idx(tv_dry=True))
    assert any(x["rule"] == "V1" and x["level"] == "info" for x in a)


def test_V1_not_dry_no_fire():
    a = alerts.evaluate({}, _idx(tv_dry=False))
    assert not any(x["rule"] == "V1" for x in a)


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
