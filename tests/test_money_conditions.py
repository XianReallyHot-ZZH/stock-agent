"""⑪ 货币条件 tests — 纯函数(状态机/对齐/剪刀差/脉冲) + store round-trip + fetcher 解析 + 看板构建器。

No network — 合成月度序列钉死三臂触发/每 run 一次/无前视(截断不变性),合成日线索引钉死
lag0/lag15 公布日对齐(周末滚动/跨年/数据末端),合成中文列 DataFrame 钉死金十解析。
构建器测试与 ⑩ 同款(_money_html 子串/_money_figure traces)。
"""
import pandas as pd
import pytest

from stockagent.data import fetcher
from stockagent.data.store import Store
from stockagent.tracker import money_conditions as mc
from stockagent.tracker.diagnose import diagnose_money_conditions


def _monthly(values, start="2020-01"):
    idx = pd.period_range(start, periods=len(values), freq="M").strftime("%Y-%m-01")
    return pd.Series(values, index=idx, dtype=float)


# ---------- 状态机:三臂触发 ----------
def test_three_arms_fire_once_each():
    """平段→大升段→大跌段→回升段:见顶回落/下行确认/触底回升 各恰一次,月份精确。"""
    yoy = _monthly([8, 8, 8, 8, 8, 8,
                    8.5, 9.0, 9.5, 10.0, 10.5, 11.0,
                    10.0, 9.0, 8.0, 7.0, 6.0, 5.5,
                    6.0, 6.5, 7.0, 7.5])
    ev = mc.m2_episode_events(yoy)
    got = [(e["kind"], e["month"]) for e in ev]
    assert got == [("top_turn", "2021-02-01"),      # 大升后连降2月(2月动量口径)
                   ("down_confirm", "2021-04-01"),  # 连降4月
                   ("bottom_turn", "2021-09-01")]   # 大降后连升2月


def test_monotone_rise_no_events():
    yoy = _monthly([8 + 0.2 * i for i in range(30)])
    assert mc.m2_episode_events(yoy) == []


def test_down_confirm_once_per_long_run():
    """8 个月长降段只触发一次(非每月重触发)。"""
    yoy = _monthly([12, 12, 12, 12] + [12 - 0.8 * i for i in range(1, 9)])
    ev = mc.m2_episode_events(yoy)
    assert [e["kind"] for e in ev].count("down_confirm") == 1


def test_single_month_bounce_does_not_break_fall_run():
    """MOM_K=2 卖点:降段中的单月反抽不打断 run(环比口径会断)。"""
    yoy = _monthly([12, 12, 12, 12, 11, 10, 9, 8, 8.6, 7, 6, 5, 4])  # 8.6=反抽月(2020-09)
    ev = mc.m2_episode_events(yoy)
    assert [e["kind"] for e in ev] == ["down_confirm"]
    assert ev[0]["month"] == "2020-08-01"   # 第 4 个降月触发(反抽月之前)
    # 反抽月(09)未打断 run:末态 run 覆盖 2020-05..2021-01 连续 9 个月(环比口径此处会归零)
    st = mc.episode_state(yoy)
    assert st["direction"] == "down" and st["run"] == 9


def test_truncation_invariance_no_lookahead():
    """前缀重放 == 全史前缀(状态机只用 trailing 信息)。"""
    yoy = _monthly([8, 8, 8, 8, 8, 8, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0,
                    10.0, 9.0, 8.0, 7.0, 6.0, 5.5, 6.0, 6.5, 7.0, 7.5])
    full = mc.m2_episode_events(yoy)
    for p in range(4, len(yoy) + 1):
        prefix = mc.m2_episode_events(yoy.iloc[:p])
        assert prefix == [e for e in full if e["pos"] < p], f"prefix len={p}"


def test_episode_state_and_label():
    yoy = _monthly([8, 8.2, 8.5, 8.8, 8.8, 8.4, 8.2, 8.0])  # 末段连降
    st = mc.episode_state(yoy)
    assert st["direction"] == "down"
    label = mc.state_label(st)
    assert "连降" in label
    st2 = mc.episode_state(_monthly([8, 8, 8]))
    assert st2["direction"] == "flat" and "走平" in mc.state_label(st2)


# ---------- 公布日对齐 ----------
def _daily_idx():
    return pd.bdate_range("2020-01-01", periods=320).strftime("%Y-%m-%d")


def test_trade_date_lag0_first_trading_day_of_next_month():
    # 2020-02-01 周六 → 首个交易日 2020-02-03
    assert mc.event_trade_date("2020-01-01", _daily_idx(), "lag0") == "2020-02-03"


def test_trade_date_lag15_weekend_rolls_forward():
    # 2020-02-15 周六 → 顺延到周一 17 日
    assert mc.event_trade_date("2020-01-01", _daily_idx(), "lag15") == "2020-02-17"


def test_trade_date_december_rollover():
    assert mc.event_trade_date("2020-12-01", _daily_idx(), "lag0") == "2021-01-01"
    assert mc.event_trade_date("2020-12-01", _daily_idx(), "lag15") == "2021-01-15"


def test_trade_date_beyond_data_end_is_none():
    idx = _daily_idx()   # 覆盖到 ~2021-03
    assert mc.event_trade_date("2021-04-01", idx, "lag0") is None


def test_trade_date_bad_mode_raises():
    with pytest.raises(ValueError):
        mc.event_trade_date("2020-01-01", _daily_idx(), "lag7")


# ---------- 剪刀差 / 社融脉冲 ----------
def test_scissor_series():
    m1 = _monthly([5, 6])
    m2 = _monthly([7, 8])
    sc = mc.scissor_series(m1, m2)
    assert list(sc.round(6)) == [-2.0, -2.0]


def test_tsf_pulse_series_ttm_over_m2():
    inc = _monthly([1200.0] * 24)
    amt = _monthly([100000.0] * 24)
    pu = mc.tsf_pulse_series(inc, amt)
    assert len(pu) == 13                             # 前 11 个月 TTM 不足丢弃
    assert float((pu - 14.4).abs().max()) < 1e-9     # 1200*12/100000*100


# ---------- 金十解析 ----------
def _money_df():
    return pd.DataFrame({
        "月份": ["2026年07月份", "2026年06月份", "2024年1月份", "x"],
        "货币和准货币(M2)-数量(亿元)": [3555077.24, 3567108.43, 301.0, None],
        "货币和准货币(M2)-同比增长": [7.7, 8.0, 8.3, None],
        "货币(M1)-数量(亿元)": [1154623.0, 1184775.53, 112.0, None],
        "货币(M1)-同比增长": [4.0, 4.0, 1.2, None],
        "流通中的现金(M0)-数量(亿元)": [148202.86, 147364.79, 11.0, None],
        "流通中的现金(M0)-同比增长": [11.6, 11.8, 5.2, None],
    })


def test_parse_china_money_supply():
    rows = fetcher.parse_china_money_supply(_money_df())
    assert len(rows) == 3                                   # 坏 'x' 行跳过
    by = {r["month"]: r for r in rows}
    assert by["2026-07-01"]["m2_yoy"] == 7.7
    assert by["2026-07-01"]["m1_amt"] == 1154623.0
    assert by["2024-01-01"]["m0_yoy"] == 5.2                # 个位月补零


def test_parse_china_tsf():
    df = pd.DataFrame({"月份": ["201501", "202604", "bad", "2026"],
                       "社会融资规模增量": [20516.0, 6245.0, 1.0, float("nan")]})
    rows = fetcher.parse_china_tsf(df)
    assert {r["month"]: r["tsf_inc"] for r in rows} == {"2015-01-01": 20516.0,
                                                        "2026-04-01": 6245.0}


# ---------- store round-trip ----------
def test_store_china_money_roundtrip(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    rows = [{"month": "2026-07-01", "m2_amt": 3555077.24, "m2_yoy": 7.7,
             "m1_amt": 1154623.0, "m1_yoy": 4.0, "m0_amt": 148202.86, "m0_yoy": 11.6},
            {"month": "2026-06-01", "m2_amt": 3567108.43, "m2_yoy": 8.0,
             "m1_amt": 1184775.53, "m1_yoy": 4.0, "m0_amt": None, "m0_yoy": None}]
    assert st.upsert_china_money(rows) == 2
    assert st.upsert_china_money(rows) == 2                  # 幂等重拉
    df = st.get_china_money_series()
    assert list(df.index) == ["2026-06-01", "2026-07-01"]    # 升序
    assert df.loc["2026-07-01", "m2_yoy"] == 7.7
    assert pd.isna(df.loc["2026-06-01", "m0_amt"])           # NaN→None→NULL 回读
    assert st.last_china_money_date() == "2026-07-01"
    # 社融
    assert st.upsert_china_tsf([{"month": "2026-04-01", "tsf_inc": 6245.0}]) == 1
    assert st.get_china_tsf_series().iloc[0]["tsf_inc"] == 6245.0


# ---------- diagnose ----------
def test_diagnose_money_conditions_insufficient(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    out = diagnose_money_conditions(st)
    assert out["valid"] is False


def test_diagnose_money_conditions_valid(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    vals = [8, 8, 8, 8, 8, 8, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0,
            10.0, 9.0, 8.0, 7.0, 6.0, 5.5, 6.0, 6.5, 7.0, 7.5,
            7.6, 7.7, 7.8, 7.9]  # 26 月 ≥ MIN_MONTHS
    rows = [{"month": str(m), "m2_amt": 3.0e6, "m2_yoy": v,
             "m1_amt": 1.1e6, "m1_yoy": v - 3.0, "m0_amt": 1.4e5, "m0_yoy": 10.0}
            for m, v in zip(pd.period_range("2020-01", periods=len(vals), freq="M")
                            .strftime("%Y-%m-01"), vals)]
    st.upsert_china_money(rows)
    out = diagnose_money_conditions(st)
    assert out["valid"] is True
    assert out["m2_yoy"] == pytest.approx(7.9)
    assert out["month_last"] == "2022-02-01"
    assert isinstance(out["events"], list) and out["events"]
    assert out["state_label"] and "连升" in out["state_label"]
    assert len(out["scissor_series"]) == len(vals)
    assert len(out["pulse_series"]) == 0                     # 无社融数据 → 空序列不抛
