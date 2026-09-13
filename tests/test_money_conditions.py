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


# ---------- 社融存量同比(批次2.5 真口径) ----------
def test_tsf_stock_yoy_series_basic():
    """月度连续序列:同比 = 值/12月前−1。"""
    stk = _monthly([100.0] * 12 + [108.0] * 6)       # 13 月起 +8%
    yoy = mc.tsf_stock_yoy_series(stk)
    assert yoy.index[0] == "2021-01-01"
    assert float(yoy.iloc[-1]) == pytest.approx(8.0)
    assert float(yoy.iloc[0]) == pytest.approx(8.0)


def test_tsf_stock_yoy_series_sparse_annual_no_misalign():
    """早年仅年末值:年末对年末恰好相隔 12 月→年度同比有效;非 12 月整倍数间隔的点(季度值)
    对不上 12 月前→NaN,不硬算跨行错位。"""
    idx = ["2002-12-01", "2003-12-01", "2004-12-01", "2004-03-01"]
    stk = pd.Series([14.85, 18.17, 20.41, 19.0], index=idx)
    yoy = mc.tsf_stock_yoy_series(stk)
    assert list(yoy.index) == ["2003-12-01", "2004-12-01"]
    assert float(yoy.loc["2003-12-01"]) == pytest.approx((18.17 / 14.85 - 1) * 100)
    assert float(yoy.loc["2004-12-01"]) == pytest.approx((20.41 / 18.17 - 1) * 100)


def test_tsf_stock_yoy_series_full_monthly_after_gap():
    """密集段起点 12 个月后才出同比(与 pulse 的 TTM 前置丢弃同理)。"""
    stk = _monthly([14.85] + [15.0] * 12)            # 2020-01 + 12 个月 = 13 期
    yoy = mc.tsf_stock_yoy_series(stk)
    assert list(yoy.index) == ["2021-01-01"]
    assert float(yoy.iloc[0]) == pytest.approx((15.0 / 14.85 - 1) * 100)


def test_tsf_stock_yoy_series_empty():
    assert len(mc.tsf_stock_yoy_series(pd.Series(dtype=float))) == 0


def test_tsf_stock_yoy_series_scope_break_poisons_12m():
    """口径断点(单月 |MoM|>10%)后 12 个月同比(分母跨断点)全丢,断点月起满 12 月恢复。
    复刻 2017-01 实证:155.99→184.14(+18%),2017 全年 yoy 丢弃、2018-01 恢复。"""
    vals = [100.0] * 24                                  # 2015-01..2016-12 旧口径
    vals += [118.0] + [119.0] * 11                       # 2017-01 断点 +18%,其后 11 月
    vals += [120.0] * 12                                 # 2018
    stk = _monthly(vals, start="2015-01")
    yoy = mc.tsf_stock_yoy_series(stk)
    # 2017-01..2017-12 全部丢弃;2016-12(100/89…即旧口径内部)仍有效;2018-01 恢复(118→120 ≈1.7%)
    assert "2017-01-01" not in yoy.index and "2017-06-01" not in yoy.index
    assert "2017-12-01" not in yoy.index
    assert "2018-01-01" in yoy.index
    assert float(yoy.loc["2018-01-01"]) == pytest.approx((120.0 / 118.0 - 1) * 100)
    # 断点前正常同比保留(2016 各月 = 100/89.29 系列全为 0%:线性平坦段)
    assert float(yoy.loc["2016-06-01"]) == pytest.approx(0.0)


def test_tsf_stock_yoy_series_no_false_break_on_sparse_annual():
    """年度稀疏点(仅 12 月有值):shift(1) 两侧缺月→NaN 不误判断点,Dec/Dec 同比保留。"""
    idx = pd.period_range("2002", periods=6, freq="Y").strftime("%Y-12-01")
    stk = pd.Series([14.85, 18.17, 20.41, 22.0, 24.0, 26.0], index=idx)
    yoy = mc.tsf_stock_yoy_series(stk)
    assert len(yoy) == 5                                  # 次年起每年一个 Dec/Dec 点
    assert float(yoy.iloc[0]) == pytest.approx((18.17 / 14.85 - 1) * 100)


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
    assert len(out["stock_yoy_series"]) == 0                 # 存量列缺同理(批次2.5 回退代理)


def test_diagnose_money_conditions_with_stock(tmp_path):
    """批次2.5: ts_stock 列在库 → 真口径同比序列出模,脉冲代理并存(看板择优)。"""
    st = Store(tmp_path / "t.sqlite")
    months = pd.period_range("2020-01", periods=26, freq="M").strftime("%Y-%m-01")
    st.upsert_china_money([{"month": m, "m2_amt": 3.0e6, "m2_yoy": 8.0,
                            "m1_amt": 1.1e6, "m1_yoy": 5.0} for m in months])
    st.upsert_china_tsf([{"month": m, "tsf_inc": 30000.0 + 100.0 * i,
                          "ts_stock": 250.0 + 0.5 * i} for i, m in enumerate(months)])
    out = diagnose_money_conditions(st)
    assert out["valid"] is True
    assert len(out["pulse_series"]) > 0
    assert len(out["stock_yoy_series"]) > 0
    # 线性存量:每期同比≈0.5/间隔12期前的存量(≈256→yoy≈1.95%上下,只验量级与单调索引)
    yoy = out["stock_yoy_series"]
    assert list(yoy.index) == sorted(yoy.index)
    assert 0.5 < float(yoy.iloc[-1]) < 3.0
