"""国内宏观看板(第七看板) tests — 政策日历纯函数 + 金十解析器 + store round-trip + 构建器冒烟。

No network — 合成 DataFrame/日期钉死行为。构建器测试与 ⑩/⑪ 同款(trace 名/HTML 子串)。
"""
from datetime import date

import pandas as pd
import plotly.graph_objects as go

from stockagent.china_macro import framework as fw
from stockagent.china_macro import nowcast as nc
from stockagent.china_macro import policy
from stockagent.data import fetcher
from stockagent.data.store import Store


# ---------- 政策日历(纯) ----------
def test_policy_calendar_sorted_with_countdown():
    ev = policy.policy_calendar(date(2026, 8, 25))
    assert len(ev) == len(policy.POLICY_RULES)          # 每规则出下一次时点
    dates = [e["date"] for e in ev]
    assert dates == sorted(dates)                       # 升序
    first = ev[0]
    assert first["days_left"] == (date.fromisoformat(first["date"]) - date(2026, 8, 25)).days
    assert all(e["days_left"] >= 0 for e in ev)


def test_policy_calendar_monthly_rules_only_next_occurrence():
    """每月规则(LPR/M2公布)只出未来首次,不按月刷屏。"""
    ev = policy.policy_calendar(date(2026, 8, 25))
    lpr = [e for e in ev if e["name"] == "LPR 报价"]
    m2 = [e for e in ev if "金融统计数据" in e["name"]]
    assert len(lpr) == 1 and lpr[0]["date"] == "2026-09-20"
    assert len(m2) == 1 and m2[0]["date"] == "2026-09-12"


def test_policy_calendar_year_rollover():
    """年末视角:下次两会/政治局落在次年。"""
    ev = policy.policy_calendar(date(2026, 12, 20))
    names = [(e["name"], e["date"]) for e in ev]
    assert ("两会·政府工作报告", "2027-03-05") in names
    assert any("中央经济工作会议" in n and d.startswith("2027") for n, d in names)


# ---------- 金十解析器 ----------
def test_parse_shibor():
    df = pd.DataFrame({"日期": ["2026-08-24", "bad"], "O/N-定价": [1.419, 2.0],
                       "1W-定价": [1.432, 2.0], "2W-定价": [1.43, 2.0], "1M-定价": [1.4175, 2.0],
                       "3M-定价": [1.43, 2.0], "6M-定价": [1.45, 2.0], "9M-定价": [1.47, 2.0],
                       "1Y-定价": [1.48, 2.0]})
    rows = fetcher.parse_shibor(df)
    assert len(rows) == 1 and rows[0]["date"] == "2026-08-24"
    assert rows[0]["overnight"] == 1.419 and rows[0]["y1"] == 1.48


def test_parse_repo_fix():
    df = pd.DataFrame({"date": [date(2026, 8, 24)], "FR001": [1.47], "FR007": [1.44],
                       "FR014": [1.45], "FDR001": [1.42], "FDR007": [1.43], "FDR014": [1.42]})
    rows = fetcher.parse_repo_fix(df)
    assert rows == [{"date": "2026-08-24", "fr001": 1.47, "fr007": 1.44, "fr014": 1.45,
                     "fdr001": 1.42, "fdr007": 1.43, "fdr014": 1.42}]


def test_parse_lpr():
    df = pd.DataFrame({"TRADE_DATE": [date(2026, 8, 20)], "LPR1Y": [3.0],
                       "LPR5Y": [3.5], "RATE_1": [4.35], "RATE_2": [4.9]})
    assert fetcher.parse_lpr(df) == [{"date": "2026-08-20", "lpr1y": 3.0, "lpr5y": 3.5,
                                      "base1y": 4.35, "base5y": 4.9}]


def test_parse_cn_bond_china_cols_only():
    df = pd.DataFrame({"日期": [date(2026, 8, 24)],
                       "中国国债收益率2年": [1.2332], "中国国债收益率5年": [1.3851],
                       "中国国债收益率10年": [1.6794], "中国国债收益率30年": [2.122],
                       "中国国债收益率10年-2年": [0.4462],
                       "美国国债收益率10年": [4.7]})       # 美国列不该被存
    rows = fetcher.parse_cn_bond(df)
    assert len(rows) == 1 and rows[0]["y10"] == 1.6794
    assert "美国" not in str(rows[0]) and rows[0]["spread_10y2y"] == 0.4462


def test_parse_cb_balance_month_format():
    df = pd.DataFrame({"统计时间": ["2026.7", "1993.6", "x"],
                       "对其他存款性公司债权": [180000.0, None, 1.0],
                       "储备货币": [360000.0, 10089.1, 1.0],
                       "政府存款": [50000.0, None, 1.0],
                       "总资产": [450000.0, None, 1.0]})
    rows = fetcher.parse_cb_balance(df)
    assert {r["month"] for r in rows} == {"2026-07-01", "1993-06-01"}
    by = {r["month"]: r for r in rows}
    assert by["2026-07-01"]["claim_odc"] == 180000.0
    assert by["1993-06-01"]["govt_deposit"] is None      # 早期缺列 → None


# ---------- store round-trip(_upsert_simple 泛型) ----------
def test_store_china_rates_roundtrip(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    assert st.upsert_shibor([{"date": "2026-08-24", "overnight": 1.419, "w1": 1.432}]) == 1
    assert st.upsert_shibor([{"date": "2026-08-24", "overnight": 1.42}]) == 1  # 幂等覆盖
    df = st.get_shibor_series()
    assert len(df) == 1 and float(df.loc["2026-08-24", "overnight"]) == 1.42
    assert pd.isna(df.loc["2026-08-24", "y1"])           # 未给列 → NULL 回读 NaN
    assert st.upsert_repo_fix([{"date": "2026-08-24", "fdr007": 1.43}]) == 1
    assert st.upsert_lpr([{"date": "2026-08-20", "lpr1y": 3.0, "lpr5y": 3.5}]) == 1
    assert st.upsert_cn_bond([{"date": "2026-08-24", "y10": 1.6794, "spread_10y2y": 0.4462}]) == 1
    assert st.upsert_cb_balance([{"month": "2026-07-01", "claim_odc": 180000.0}]) == 1
    assert float(st.get_cb_balance_series().loc["2026-07-01", "claim_odc"]) == 180000.0


# ---------- 构建器 ----------
def test_money_html_insufficient():
    assert "数据不足" in fw._money_html({"valid": False}, "", "")


def test_policy_html_renders():
    ev = policy.policy_calendar(date(2026, 8, 25))
    html = fw._policy_html(ev)
    assert "LPR 报价" in html and "中央经济工作会议" in html
    assert "CLAIMS_LEDGER" in html and "硬编码典型时点" in html


def test_policy_html_empty():
    assert "为空" in fw._policy_html([])


def _repo_df():
    idx = pd.bdate_range("2026-01-01", periods=40).strftime("%Y-%m-%d")
    return pd.DataFrame({"fr001": 1.4, "fr007": 1.45, "fr014": 1.45,
                         "fdr001": 1.4, "fdr007": 1.43, "fdr014": 1.41}, index=idx)


def test_funding_figure_traces():
    fig = fw._funding_figure(_repo_df(), pd.DataFrame(dtype=float))
    assert isinstance(fig, go.Figure)
    names = [t.name or "" for t in fig.data]
    assert any("FDR007" in n for n in names) and any("FR007" in n for n in names)
    fig2 = fw._funding_figure(pd.DataFrame(dtype=float), pd.DataFrame(dtype=float))
    assert len(fig2.data) == 0                            # 空数据不抛


def test_term_and_omo_figures():
    idx = pd.bdate_range("2026-01-01", periods=40).strftime("%Y-%m-%d")
    lpr = pd.DataFrame({"lpr1y": 3.0, "lpr5y": 3.5, "base1y": 4.35, "base5y": 4.9}, index=idx)
    bond = pd.DataFrame({"y2": 1.23, "y5": 1.39, "y10": 1.68, "y30": 2.12,
                         "spread_10y2y": 0.45}, index=idx)
    fig = fw._term_figure(lpr, bond)
    names = [t.name or "" for t in fig.data]
    assert any("LPR 1Y" in n for n in names) and any("10Y−2Y" in n for n in names)
    cb = pd.DataFrame({"claim_odc": [10.0e4, 10.5e4, 10.3e4], "base_money": [35e4] * 3,
                       "govt_deposit": [5e4] * 3, "total_assets": [45e4] * 3},
                      index=["2026-05-01", "2026-06-01", "2026-07-01"])
    fig2 = fw._omo_figure(cb)
    kinds = [t.type for t in fig2.data]
    assert "bar" in kinds and any(t.type == "scatter" for t in fig2.data)  # 柱=净投放 线=余额
    assert len(fw._omo_figure(pd.DataFrame(dtype=float)).data) == 0


def test_rates_html_insufficient_and_tiles():
    empty = pd.DataFrame(dtype=float)
    assert "数据不足" in fw._rates_html(None, None, None, None, None, [])
    html = fw._rates_html(_repo_df(), None, None, None, None, [])
    assert "FDR007" in html and "1.43%" in html           # tile 最新值
    cb = pd.DataFrame({"claim_odc": [10.0e4, 10.5e4]}, index=["2026-06-01", "2026-07-01"])
    html2 = fw._rates_html(_repo_df(), None, None, None, cb, [])
    assert "万亿" in html2 and "2026-07" in html2          # OMO 近似 tile


# ---------- v2: nowcast 纯函数 ----------
def test_lgb_monthly_series():
    detail = pd.DataFrame({
        "issue_date": ["2026-07-02", "2026-07-15", "2026-08-01", None],
        "actual_amt": [100.0, 50.0, 30.0, 999.0],
    }, index=["c1", "c2", "c3", "c4"])
    s = nc.lgb_monthly_series(detail)
    assert list(s.index) == ["2026-07", "2026-08"]
    assert s["2026-07"] == 150.0 and s["2026-08"] == 30.0
    assert len(nc.lgb_monthly_series(pd.DataFrame(dtype=float))) == 0


def test_mtd_progress():
    idx = [f"2025-{m:02d}" for m in range(9, 13)] + [f"2026-{m:02d}" for m in range(1, 8)]
    monthly = pd.Series([1000.0] * 11, index=idx)          # 11 个完整月
    monthly["2026-08"] = 500.0                             # 本月进行时
    p = nc.mtd_progress(monthly, "2026-08")
    assert p["cur"] == 500.0 and p["base"] == 1000.0
    assert p["last_full"] == 1000.0 and p["ratio"] == 0.5
    # 本月尚无发行 → cur NaN 不抛
    p2 = nc.mtd_progress(monthly, "2026-09")
    assert p2["cur"] != p2["cur"]                          # NaN
    assert len(nc.mtd_progress(pd.Series(dtype=float), "2026-08")) == 4


def test_meeting_anchor_stats_direction():
    """单升序列 → 全锚点上行概率 1.0;末尾锚未走完 ahead 不进统计。"""
    idx = pd.period_range("2022-01", periods=48, freq="M").strftime("%Y-%m-01")
    ramp = pd.Series([8.0 + 0.1 * i for i in range(46)] + [10.0, None], index=idx).dropna()
    # 46 个月(2022-01..2025-10):每年锚点后 3 月必更高(线性升)
    st = nc.meeting_anchor_stats(ramp, anchors=(12,), ahead=3)
    s = st[12]
    assert s["n"] == 3                                     # 2022/2023/2024 年 12 月锚
    assert s["up_prob"] == 1.0 and s["median_delta"] > 0
    # 降序列 → 0.0
    down = pd.Series([10.0 - 0.1 * i for i in range(46)], index=idx[:46])
    st2 = nc.meeting_anchor_stats(down, anchors=(4,), ahead=3)
    assert st2[4]["up_prob"] == 0.0


def test_meeting_anchor_stats_year_rollover():
    """12 月锚 +3 月 = 次年 3 月(跨年换算)。序列止于 2023-06 → 只有 2022-12 锚完整。"""
    idx = pd.period_range("2022-01", periods=18, freq="M").strftime("%Y-%m-01")
    v = pd.Series(0.0, index=idx)
    v["2023-03-01"] = 1.0                                  # 只有 2022-12 锚的后值抬高
    st = nc.meeting_anchor_stats(v, anchors=(12,), ahead=3)
    assert st[12]["n"] == 1 and st[12]["latest_delta"] == 1.0


def test_parse_lgb_issue():
    df = pd.DataFrame({
        "债券代码": ["2105798", "", "566862"],
        "债券简称": ["21湖北债105", "x", "西藏2613"],
        "发行起始日": [date(2021, 9, 2), date(2021, 9, 3), date(2026, 7, 2)],
        "计划发行总量": [4.13, 1.0, 1.3],
        "实际发行总量": [4.13, 1.0, 1.3],
        "缴款日": [date(2021, 9, 3), None, date(2026, 7, 3)],
    })
    rows = fetcher.parse_lgb_issue(df)
    assert len(rows) == 2                                  # 空代码行跳过
    by = {r["code"]: r for r in rows}
    assert by["2105798"]["issue_date"] == "2021-09-02" and by["2105798"]["actual_amt"] == 4.13


def test_parse_china_tsf_components():
    df = pd.DataFrame({"月份": ["202604", "bad"],
                       "社会融资规模增量": [6245.0, 1.0],
                       "其中-人民币贷款": [-4006.0, 1.0],
                       "其中-企业债券": [4520.0, None],
                       "其中-非金融企业境内股票融资": [835.0, 1.0]})
    rows = fetcher.parse_china_tsf(df)
    assert rows == [{"month": "2026-04-01", "tsf_inc": 6245.0,
                     "rmb_loans": -4006.0, "corp_bond": 4520.0, "equity_fin": 835.0}]


def test_store_lgb_and_tsf_components_roundtrip(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    assert st.upsert_lgb_issue([{"code": "c1", "name": "西藏2613",
                                 "issue_date": "2026-07-02", "plan_amt": 1.3,
                                 "actual_amt": 1.3, "pay_date": "2026-07-03"}]) == 1
    df = st.get_lgb_issue()
    assert float(df.loc["c1", "actual_amt"]) == 1.3
    assert st.upsert_china_tsf([{"month": "2026-04-01", "tsf_inc": 6245.0,
                                 "rmb_loans": -4006.0, "corp_bond": None,
                                 "equity_fin": 835.0}]) == 1
    tsf = st.get_china_tsf_series()
    assert float(tsf.loc["2026-04-01", "rmb_loans"]) == -4006.0
    assert pd.isna(tsf.loc["2026-04-01", "corp_bond"])
    assert "equity_fin" in tsf.columns


# ---------- v2: 构建器 ----------
def test_lgb_figure_and_nowcast_html():
    monthly = pd.Series({"2026-07": 20000.0, "2026-08": 15000.0})
    fig = fw._lgb_figure(monthly, "2026-08")
    assert fig.data[0].type == "bar" and len(fig.data[0].x) == 2
    html = fw._nowcast_html(monthly, pd.DataFrame(dtype=float), [], "2026-08")
    assert "月内进行时" in html and "观测非预测" in html
    assert "数据不足" in fw._nowcast_html(pd.Series(dtype=float), pd.DataFrame(dtype=float), [], "2026-08")
    tsf = pd.DataFrame({"rmb_loans": [4446.0], "corp_bond": [2000.0], "equity_fin": [500.0]},
                       index=["2026-04-01"])
    html2 = fw._nowcast_html(monthly, tsf, [], "2026-08")
    assert "4,446" in html2 and "社融口径" in html2


def test_tsf_comp_figure_traces():
    tsf = pd.DataFrame({"tsf_inc": [1.0], "rmb_loans": [4446.0], "corp_bond": [2000.0],
                        "equity_fin": [500.0]}, index=["2026-04-01"])
    fig = fw._tsf_comp_figure(tsf)
    names = [t.name or "" for t in fig.data]
    assert any("人民币贷款" in n for n in names) and any("企业债券" in n for n in names)
    assert len(fw._tsf_comp_figure(pd.DataFrame(dtype=float)).data) == 0


def test_meeting_html_renders():
    stats = {12: {"label": "12 月(政治局+中央经济工作会议)", "n": 18, "up_prob": 0.56,
                  "median_delta": 0.3, "latest_ym": "2025-12", "latest_delta": 0.9}}
    html = fw._meeting_html(stats)
    assert "上行概率" in html and "56%" in html and "历史对照非因果" in html
    assert "数据不足" in fw._meeting_html({})
