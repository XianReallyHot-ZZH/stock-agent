"""国内宏观看板(第七看板) tests — 政策日历纯函数 + 金十解析器 + store round-trip + 构建器冒烟。

No network — 合成 DataFrame/日期钉死行为。构建器测试与 ⑩/⑪ 同款(trace 名/HTML 子串)。
"""
from datetime import date

import pandas as pd
import plotly.graph_objects as go

from stockagent.china_macro import framework as fw
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
