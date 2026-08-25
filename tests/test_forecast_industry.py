"""预告行业选股 event-study 纯核心测试——过门无前视 / Top-N 确定性 / 截止日定价 / 行业回滚。"""
from __future__ import annotations

import pandas as pd
import pytest

from stockagent.pool.forecast_industry import (
    build_trades, industry_rollup, matched_baseline, period_deadline,
    ret_summary, season_filter, season_start, trade_returns)
from stockagent.pool.prices import dividend_adjusted_open


# ---- 期日历 ----

def test_period_deadline_four_tails():
    assert period_deadline("20241231") == "2025-04-30"
    assert period_deadline("20250331") == "2025-04-30"
    assert period_deadline("20250630") == "2025-08-31"
    assert period_deadline("20250930") == "2025-10-31"
    with pytest.raises(ValueError):
        period_deadline("2025")


def test_season_start_and_filter():
    assert season_start("20251231") == "2025-09-02"      # 期终 −120d
    fc = pd.DataFrame({
        "code": ["a", "b", "c", "d"],
        "report_period": ["20251231"] * 4,
        "yoy": [10.0, 20.0, 30.0, 40.0],
        "type": ["预增"] * 4,
        "announce_date": ["2024-09-06", "2025-10-01", "2026-04-30", "2026-05-02"],
    })
    out, dropped = season_filter(fc)
    assert dropped == 2                                   # 期前脏日期 + 截止后
    assert list(out["code"]) == ["b", "c"]


# ---- 行业回滚 ----

def test_industry_rollup_containment_and_twin():
    members = pd.DataFrame([
        ("电子", "A"), ("电子", "B"), ("电子", "C"), ("电子", "D"), ("电子", "E"),
        ("半导体", "A"), ("半导体", "B"), ("半导体", "C"),          # ⊂ 电子
        ("银行", "X"), ("银行", "Y"), ("银行", "Z"),
        ("银行Ⅱ", "X"), ("银行Ⅱ", "Y"), ("银行Ⅱ", "Z"),           # 同尺寸双子 → 无级标者为父
        ("白酒Ⅱ", "W"), ("白酒Ⅲ", "W"),                           # 链式: Ⅲ→Ⅱ,Ⅱ 孤根
    ] * 1, columns=["industry", "code"])
    code_ind, parent = industry_rollup(members)
    assert parent["半导体"] == "电子"
    assert parent["银行Ⅱ"] == "银行"
    assert parent["白酒Ⅲ"] == "白酒Ⅱ"
    assert code_ind["A"] == "电子"          # 最具体板(半导体)回滚到根
    assert code_ind["X"] == "银行"
    assert code_ind["W"] == "白酒Ⅱ"        # 最小板 Ⅲ → 根 Ⅱ


# ---- 事件构建: 过门 + Top-N ----

def _fc(rows):
    """rows = (code, date, type, yoy) → 预告帧(单期 20251231,行业=电子)。"""
    return pd.DataFrame(rows, columns=["code", "announce_date", "type", "yoy"]).assign(
        report_period="20251231")


def test_build_trades_gate_is_first_day_both_conditions_hold():
    # d1: 3 预喜 → 率 100% 但 n=3 <5 → 不过门; d2: +2 预喜 → n=5 率 100% → Q=d2
    fc = _fc([("c1", "2026-01-05", "预增", 50.0), ("c2", "2026-01-05", "略增", 20.0),
              ("c3", "2026-01-05", "续盈", 5.0), ("c4", "2026-01-08", "预增", 80.0),
              ("c5", "2026-01-08", "预增", 10.0)])
    ind = {c: "电子" for c in ["c1", "c2", "c3", "c4", "c5"]}
    t = build_trades(fc, ind)
    q = set(t["trigger_ind"])
    assert q == {"2026-01-08"}            # 过门日快照: 过门前公告的股也在 Q 入场
    assert t["in_b2"].all()


def test_build_trades_gate_needs_rate_not_just_n():
    # 5 条但预喜率 0.6 → 永不过门
    fc = _fc([("c1", "2026-01-05", "预增", 50.0), ("c2", "2026-01-06", "预增", 80.0),
              ("c3", "2026-01-07", "预减", -30.0), ("c4", "2026-01-08", "预减", -40.0),
              ("c5", "2026-01-09", "预增", 10.0)])
    ind = {c: "电子" for c in ["c1", "c2", "c3", "c4", "c5"]}
    t = build_trades(fc, ind)
    assert t["trigger_ind"].isna().all() and not t["in_b2"].any()
    assert t["trigger_own"].notna().all()  # b1 臂照常


def test_build_trades_bear_in_denominator_and_topn_determinism():
    # c3 预减进分母: Q=c5 日 n=5 率 4/5=0.8
    fc = _fc([("c1", "2026-01-05", "预增", 50.0), ("c2", "2026-01-08", "预增", 80.0),
              ("c3", "2026-01-10", "预减", -30.0), ("c4", "2026-01-12", "扭亏", 200.0),
              ("c5", "2026-01-15", "预增", 100.0)])
    fc = pd.concat([fc, _fc([("c6", "2026-02-01", "预增", 300.0),
                             ("c9", "2026-02-05", "预增", 90.0),
                             ("c7", "2026-02-10", "预增", 60.0),
                             ("c8", "2026-02-10", "略增", None)])])
    codes = [f"c{i}" for i in range(1, 10)]
    ind = {c: "电子" for c in codes}
    t = build_trades(fc, ind).set_index("code")
    assert "c3" not in t.index                              # 预减不出行
    # Q=2026-01-15: 4 预喜 ≤ Top5 全入; 扭亏 c4 入 strat 不入 ex_tk
    for c in ["c1", "c2", "c4", "c5"]:
        assert t.loc[c, "trigger_ind"] == "2026-01-15"
        assert t.loc[c, "in_strat"]
    assert not t.loc["c4", "in_ex_tk"]
    assert t.loc["c1", "in_ex_tk"]
    # c6(300) 02-01 到: 池 5 预喜全 ≤5 → 入; c9(90) 02-05: 池 6 → 第 4 名入
    assert t.loc["c6", "trigger_ind"] == "2026-02-01" and t.loc["c6", "in_strat"]
    assert t.loc["c9", "in_strat"]
    # c7(60) 02-10: 池 7 预喜(300/200/100/90/80 压住)→ 第 6 名出局;b2 照收
    assert t.loc["c7", "trigger_ind"] == "2026-02-10" and not t.loc["c7", "in_strat"]
    assert t.loc["c7", "in_b2"]
    # yoy 缺失不能排名 → 不入 strat/ex_tk,仍入 b2
    assert not t.loc["c8", "in_strat"] and t.loc["c8", "in_b2"]


def test_build_trades_unmapped_industry_never_groups():
    fc = _fc([("u1", "2026-01-05", "预增", 50.0), ("u2", "2026-01-06", "预增", 60.0)])
    t = build_trades(fc, {})                       # 无映射 → 不成伪行业
    assert len(t) == 2
    assert t["trigger_ind"].isna().all() and not t["in_b2"].any()


def test_build_trades_tie_break_by_code():
    fc = _fc([(f"c{k}", "2026-01-05", "预增", 100.0) for k in range(1, 7)])
    ind = {f"c{k}": "电子" for k in range(1, 7)}
    t = build_trades(fc, ind, top_n=5).set_index("code")
    assert t.loc["c1", "in_strat"] and not t.loc["c6", "in_strat"]   # 平 yoy → code 升序


# ---- 定价 ----

def _mk_trades(rows):
    return pd.DataFrame(rows, columns=[
        "code", "period", "industry", "type", "yoy", "announce",
        "trigger_own", "trigger_ind", "in_b2", "in_strat", "in_ex_tk"])


def test_trade_returns_entry_exit_and_status():
    td = ["2026-01-15", "2026-01-16", "2026-01-19", "2026-04-30", "2026-05-06"]
    opens = {
        "a": pd.Series([9.0, 10.0, 11.0, 11.0, 12.0], index=td),   # 正常出入
        "s": pd.Series([5.0, 5.0, 5.0, 5.0, 5.0], index=td),       # 平价
    }
    t = trade_returns(
        _mk_trades([
            ("a", "20251231", "电子", "预增", 100.0, "2026-01-15",
             "2026-01-15", "2026-01-15", True, True, True),
            ("s", "20251231", "电子", "预增", 50.0, "2026-01-16",
             "2026-01-16", None, False, False, False),              # 仅 own 腿
        ]), opens, td)
    r = t.set_index("code")
    # a: 入场 2026-01-16 开盘 10,出场=截止(04-30)后首交易日 05-06 开盘 12 → +20%
    assert r.loc["a", "entry_ind"] == "2026-01-16" and r.loc["a", "exit_ind"] == "2026-05-06"
    assert r.loc["a", "ret_ind"] == pytest.approx(0.2)
    assert r.loc["a", "status_ind"] == "closed"
    assert r.loc["a", "hold_ind"] == 3   # 01-16 → 05-06 = 3 个市场日步进
    assert r.loc["s", "status_ind"] == "na"                       # trigger_ind=None
    assert r.loc["s", "status_own"] == "closed" and r.loc["s", "ret_own"] == 0.0


def test_trade_returns_no_price_open_late():
    # 数据尾早于截止 → open;无价格序列 → no_price;停牌把入场拖过出场 → late
    td = ["2026-04-28", "2026-04-29", "2026-04-30", "2026-05-06"]
    opens = {
        "z": pd.Series([7.0, 7.0, 7.0, 8.0], index=td),           # 触发 04-28 → 入 04-29
    }
    t = trade_returns(
        _mk_trades([
            ("z", "20251231", "电子", "预增", 100.0, "2026-04-28",
             "2026-04-28", "2026-04-28", True, True, True),
            ("np", "20251231", "电子", "预增", 100.0, "2026-01-05",
             "2026-01-05", "2026-01-05", True, True, True),
        ]), opens, td)
    r = t.set_index("code")
    assert r.loc["np", "status_own"] == "no_price"

    # late: 停牌跳过入场窗末段,首根 bar 落在出场日之后
    td2 = ["2026-04-28", "2026-04-29", "2026-04-30", "2026-05-06"]
    opens2 = {"h": pd.Series([7.0, 7.0, 7.0, 9.0], index=td2)}
    t2 = trade_returns(
        _mk_trades([("h", "20251231", "电子", "预增", 100.0, "2026-04-29",
                     "2026-04-29", "2026-04-29", True, True, True)]),
        opens2, td2)
    # 触发 04-29 → e_day=04-30;首根 bar≥04-30 即 04-30(在 slack 内)
    assert t2.loc[0, "status_own"] == "closed"

    # 真 late: e_day 与出场日之间无 bar,bar 只在 05-06
    td3 = ["2026-04-28", "2026-04-29", "2026-05-06"]
    opens3 = {"h": pd.Series([7.0, 7.0, 9.0], index=td3)}
    t3 = trade_returns(
        _mk_trades([("h", "20251231", "电子", "预增", 100.0, "2026-04-29",
                     "2026-04-29", "2026-04-29", True, True, True)]),
        opens3, td3)
    # 触发 04-29 → e_day=05-06(04-30 不在日历),首 bar=05-06,出场首 bar=05-06 → late
    assert t3.loc[0, "status_own"] == "late"

    # 数据尾早于截止日 → open
    t4 = trade_returns(
        _mk_trades([("z", "20251231", "电子", "预增", 100.0, "2026-04-28",
                     "2026-04-28", "2026-04-28", True, True, True)]),
        {"z": pd.Series([7.0], index=["2026-04-28"])}, ["2026-04-28"])
    assert t4.loc[0, "status_own"] == "open"


def test_matched_baseline_mean_and_nan_skip():
    mat = pd.DataFrame(
        {"a": [10.0, 11.0], "b": [10.0, 12.0], "c": [float("nan"), 12.0]},
        index=["d1", "d2"])
    out = matched_baseline(mat, pd.Series(["d1", "d1"]), pd.Series(["d2", "d3"]))
    assert out[0] == pytest.approx((0.1 + 0.2) / 2)     # c 缺 d1 → 跳过
    assert pd.isna(out[1])                              # d3 不在索引


def test_ret_summary_empty_and_basic():
    s = ret_summary([0.1, -0.2, 0.3, None])
    assert s["n"] == 3 and s["win_rate"] == pytest.approx(2 / 3)
    assert s["median"] == pytest.approx(0.1)
    e = ret_summary([])
    assert e["n"] == 0 and pd.isna(e["win_rate"])


# ---- open 复权 ----

def test_dividend_adjusted_open_shares_close_factors():
    idx = ["2026-01-01", "2026-01-02", "2026-01-03"]
    close = pd.Series([10.0, 10.0, 10.0], index=idx)
    open_ = pd.Series([9.5, 10.0, 10.0], index=idx)
    div = pd.DataFrame({"cash_per_share": [1.0], "stock_div_10": [0.0],
                        "trans_10": [0.0]}, index=["2026-01-03"])
    out = dividend_adjusted_open(open_, close, div)
    # factor = (10-1)/10 = 0.9 → ex_date 之前的 open ×0.9
    assert list(out) == pytest.approx([9.5 * 0.9, 9.0, 10.0])
