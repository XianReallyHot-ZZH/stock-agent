"""业绩预期 signal: pure aggregation + score + store roundtrip."""
import math
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from stockagent.research import earnings as er
from stockagent.data import Store

PARAMS = {"research": {"earnings": {"min_coverage": 0.30, "min_matched": 5}}}


def _holdings(weights: dict) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": list(weights), "weight": list(weights.values())})

def _forecast(items: dict) -> pd.DataFrame:
    # items: {code: (yoy, type)}
    codes = list(items)
    return pd.DataFrame(
        {"yoy": [items[c][0] for c in codes], "type": [items[c][1] for c in codes]},
        index=codes)

def _perf(items: dict) -> pd.DataFrame:
    # items: {code: (np_yoy, rev_yoy)} — 快报/正式报帧契约（get_stock_express/report_period）
    codes = list(items)
    return pd.DataFrame(
        {"np_yoy": [items[c][0] for c in codes], "rev_yoy": [items[c][1] for c in codes]},
        index=codes)


# ---------- aggregate_earnings ----------
def test_aggregate_weighted_median_buckets_coverage():
    h = _holdings({"A": 10, "B": 20, "C": 30, "D": 5})  # D unmatched
    fc = _forecast({"A": (50, "预增"), "B": (-20, "预减"), "C": (10, "略增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_holdings"] == 4 and s["n_matched"] == 3
    assert s["coverage"] == 60 / 65            # matched weight / total
    assert math.isclose(s["weighted_yoy"], (50 * 10 - 20 * 20 + 10 * 30) / 60)
    assert s["median_yoy"] == 10               # median(50,-20,10)
    assert math.isclose(s["bull_ratio"], 40 / 60)   # A+C
    assert math.isclose(s["bear_ratio"], 20 / 60)   # B


def test_aggregate_no_overlap_empty_coverage():
    h = _holdings({"X": 10, "Y": 20})
    fc = _forecast({"A": (50, "预增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 0 and s["coverage"] == 0.0
    assert math.isnan(s["weighted_yoy"])


def test_aggregate_extreme_yoy_no_crash():
    h = _holdings({"A": 50, "B": 50})
    fc = _forecast({"A": (-236, "增亏"), "B": (184, "预增")})
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 2
    assert math.isclose(s["weighted_yoy"], (-236 + 184) / 2)
    assert s["median_yoy"] == (-236 + 184) / 2
    assert math.isclose(s["bear_ratio"], 0.5) and math.isclose(s["bull_ratio"], 0.5)


def test_aggregate_empty_inputs():
    assert er.aggregate_earnings(None, None)["n_matched"] == 0
    assert er.aggregate_earnings(pd.DataFrame(columns=["code", "weight"]),
                                 _forecast({"A": (10, "预增")}))["n_matched"] == 0


def test_aggregate_drops_nan_yoy_but_keeps_others():
    h = _holdings({"A": 10, "B": 10})
    fc = pd.DataFrame({"yoy": [10.0, None], "type": ["预增", "预减"]}, index=["A", "B"])
    s = er.aggregate_earnings(h, fc)
    assert s["n_matched"] == 1                 # B dropped (NaN yoy)
    assert s["coverage"] == 0.5


# ---------- best_ring_earnings (三环混合头条: 逐名字取最精化环) ----------
def test_best_ring_refinement_order():
    h = _holdings({"A": 10, "B": 20, "C": 30, "D": 40})   # D 三环全缺
    fc = _forecast({"A": (50, "预增"), "C": (10, "略增")})
    ex = _perf({"A": (60, 20), "B": (5, 3)})              # A 快报也有 → 正式报优先
    ac = _perf({"A": (45, 15)})
    s = er.best_ring_earnings(h, fc, ex, ac)
    assert s["n_matched"] == 3 and s["n_holdings"] == 4
    assert math.isclose(s["weighted_yoy"], (45 * 10 + 5 * 20 + 10 * 30) / 60)
    assert s["n_ring"] == {"forecast": 1, "express": 1, "actual": 1}
    assert math.isclose(sum(s["ring_mix"].values()), 1.0)
    assert math.isclose(s["ring_mix"]["forecast"], 30 / 60)
    assert s["coverage"] == 0.6


def test_best_ring_breadth_semantics():
    # 预告环名字按类型桶（「减亏」∈BULL 负 yoy 仍计多·不计空）; 快报/正式环按 YoY 符号
    h = _holdings({"A": 10, "B": 20, "C": 30, "E": 40})
    fc = _forecast({"A": (-30, "减亏")})
    ac = _perf({"B": (25, 0), "C": (-15, 0), "E": (0, 0)})  # 正/负/零
    s = er.best_ring_earnings(h, fc, None, ac)
    assert s["n_matched"] == 4                              # E 的 0 值入聚合
    assert math.isclose(s["bull_ratio"], 30 / 100)          # A(类型) + B(>0)
    assert math.isclose(s["bear_ratio"], 30 / 100)          # C(<0); E(=0) 不计


def test_best_ring_nan_falls_through():
    # store 契约: 正式报行可只带 rev_yoy（np_yoy=NULL）→ 落次精化环
    h = _holdings({"A": 10, "B": 20})
    fc = _forecast({"A": (10, "预增"), "B": (20, "略增")})
    ex = _perf({"A": (15, 5)})
    ac = _perf({"A": (None, 7)})
    s = er.best_ring_earnings(h, fc, ex, ac)
    assert s["n_ring"] == {"forecast": 1, "express": 1, "actual": 0}
    assert math.isclose(s["weighted_yoy"], (15 * 10 + 20 * 20) / 30)


def test_best_ring_empty_inputs():
    s = er.best_ring_earnings(None, None, None, None)
    assert s["n_matched"] == 0
    assert s["ring_mix"] == {"forecast": 0.0, "express": 0.0, "actual": 0.0}
    assert s["n_ring"] == {"forecast": 0, "express": 0, "actual": 0}
    s2 = er.best_ring_earnings(_holdings({"A": 10}), None, None, None)
    assert s2["n_holdings"] == 1 and s2["coverage"] == 0.0


def test_best_ring_superset_monotonic():
    # 回退不降级的根据: mixed 已匹配集 ⊇ 纯预告环（权重·家数单调）
    h = _holdings({"A": 10, "B": 20, "C": 30})
    fc = _forecast({"A": (50, "预增")})
    pure = er.aggregate_earnings(h, fc)
    mix = er.best_ring_earnings(h, fc, _perf({"B": (5, 1)}), _perf({"C": (7, 2)}))
    assert mix["coverage"] >= pure["coverage"] and mix["coverage"] > pure["coverage"]
    assert mix["n_matched"] >= pure["n_matched"]


def test_best_ring_expands_score_gate_coverage():
    # 渲染端「mixed 失效回退 earnings_*」: 同一覆盖门下 mixed 过门集 ⊇ 纯预告过门集
    weights = {c: 10 for c in "ABCDE"}
    fc = _forecast({"A": (50, "预增")})                     # 预告只盖 1/5 → 纯预告不过门
    _, pure_label = er.earnings_score(er.aggregate_earnings(_holdings(weights), fc), PARAMS)
    assert pure_label == er.LABEL_INSUFF
    ac = _perf({c: (20, 5) for c in "BCDE"})                # 正式报补齐 → mixed 过门
    _, mix_label = er.earnings_score(er.best_ring_earnings(_holdings(weights), fc, None, ac),
                                     PARAMS)
    assert mix_label != er.LABEL_INSUFF


# ---------- earnings_score ----------
def test_score_high_growth_band():
    sig = {"median_yoy": 50, "bull_ratio": 0.9, "bear_ratio": 0.05, "coverage": 0.9, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert label == er.LABEL_HIGH and score >= 70


def test_score_crash_band():
    sig = {"median_yoy": -120, "bull_ratio": 0.1, "bear_ratio": 0.9, "coverage": 0.8, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert label == er.LABEL_CRASH and score < 30


def test_score_insufficient_low_coverage():
    sig = {"median_yoy": 80, "bull_ratio": 0.9, "bear_ratio": 0.0, "coverage": 0.10, "n_matched": 10}
    score, label = er.earnings_score(sig, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


def test_score_insufficient_low_n():
    sig = {"median_yoy": 80, "bull_ratio": 0.9, "bear_ratio": 0.0, "coverage": 0.9, "n_matched": 2}
    score, label = er.earnings_score(sig, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


def test_score_none_signal():
    score, label = er.earnings_score(None, PARAMS)
    assert math.isnan(score) and label == er.LABEL_INSUFF


# ---------- store roundtrip ----------
def _store() -> Store:
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_store_upsert_and_latest():
    st = _store()
    st.upsert_etf_earnings(
        [("159865", "20251231", -236.0, -200.0, 0.12, 0.88, 0.69, 39, 30)], source="em")
    st.upsert_etf_earnings(
        [("159865", "20250630", -50.0, -40.0, 0.20, 0.70, 0.60, 35, 25)], source="em")
    got = st.get_etf_earnings("159865")
    assert got["report_period"] == "20251231"     # latest wins
    assert got["weighted_yoy"] == -236.0
    assert got["n_matched"] == 30
    assert st.last_earnings_period() == "20251231"
    assert st.get_etf_earnings("NOPE") is None


def test_store_missing_returns_none():
    assert _store().get_etf_earnings("NOPE") is None


# ---------- period_label ----------
@pytest.mark.parametrize("period, want", [
    ("20251231", "2025年报预告"), ("20260331", "2026一季报预告"),
    ("20260630", "2026中报预告"), ("20260930", "2026三季报预告"),
])
def test_period_label_known_windows(period, want):
    assert er.period_label(period) == want


@pytest.mark.parametrize("period", [None, "", "abc", "2026", "20260713"])
def test_period_label_malformed_falls_back(period):
    out = er.period_label(period)
    assert out == "—" or out.startswith("2026报告期(")   # unknown suffix → fallback, not blank


@pytest.mark.parametrize("period, want", [
    ("20251231", "2025年报"), ("20260331", "2026一季报"),
    ("20260630", "2026中报"), ("20260930", "2026三季报"),
])
def test_period_label_short(period, want):
    assert er.period_label(period, short=True) == want
    assert er.period_label(period) != want             # 缺省仍带「预告」后缀


def test_period_label_short_malformed_falls_back():
    assert er.period_label("20260713", short=True).startswith("2026报告期(")


# ---------- latest_report_period (interim-aware, B 方案) ----------
@pytest.mark.parametrize("now, want", [
    (datetime(2026, 7, 13), "20260630"),   # today: 中报窗口已开 → 中报(B 方案)
    (datetime(2026, 6, 30), "20260331"),   # 中报窗口前夜 → 仍一季报
    (datetime(2026, 7, 1), "20260630"),    # 中报窗口开 → 中报
    (datetime(2026, 7, 15), "20260630"),   # 中报截止日 → 中报
    (datetime(2026, 8, 20), "20260630"),   # 中报已完整 → 中报
    (datetime(2026, 4, 14), "20251231"),   # 一季报窗口前夜 → 仍年报
    (datetime(2026, 4, 15), "20260331"),   # 一季报窗口开 → 一季报
    (datetime(2026, 9, 30), "20260630"),   # 三季报窗口前夜 → 仍中报
    (datetime(2026, 10, 1), "20260930"),   # 三季报窗口开 → 三季报
    (datetime(2026, 1, 1), "20251231"),    # 年报窗口(1/31 截止) → 上一年年报
    (datetime(2026, 3, 31), "20251231"),   # 年报窗口内 → 上一年年报
    (datetime(2026, 12, 31), "20260930"),  # 跨年前 → 仍三季报
])
def test_latest_report_period_interim_windows(now, want):
    assert er.latest_report_period(now) == want


# ---------- disclosure_window（披露窗口时钟 · 交叉横幅的季节门控） ----------

@pytest.mark.parametrize("now, open_, period, next_open", [
    (datetime(2026, 7, 1), True, "20260630", "10/1"),    # 中报开窗日即 open
    (datetime(2026, 7, 15), True, "20260630", "10/1"),   # 截止日 open
    (datetime(2026, 7, 29), True, "20260630", "10/1"),   # 截止+14 天(grace 含端点)仍 open
    (datetime(2026, 7, 30), False, "20260630", "10/1"),  # 次日关闭 → 报下窗口
    (datetime(2026, 8, 16), False, "20260630", "10/1"),  # 非披露季：关闭 + 下窗口三季报
    (datetime(2026, 1, 5), True, "20251231", "4/15"),    # 1 月属上年年报窗
    (datetime(2026, 2, 20), False, "20251231", "4/15"),  # 年报窗关闭(2/14 后) → 一季报
    (datetime(2026, 4, 20), True, "20260331", "7/1"),    # 一季报窗(4/15~4/30+grace)
    (datetime(2026, 6, 15), False, "20260331", "7/1"),   # 5-6 月空档 → 中报
    (datetime(2026, 10, 2), True, "20260930", "1/1"),    # 三季报窗开 → 下窗口次年年报
    (datetime(2026, 12, 25), False, "20260930", "1/1"),  # 12 月空档 → 次年年报 1/1
])
def test_disclosure_window_states(now, open_, period, next_open):
    w = er.disclosure_window(now)
    assert w["open"] is open_
    assert w["period"] == period
    assert w["next_open"] == next_open
    assert w["label"]                      # label 永不为空（关闭态=刚过的窗口名）
    assert w["window_note"] or not open_   # open 态必有窗口注记


def test_disclosure_window_grace_custom():
    # grace=0 → 截止日当天是最后一天；grace 可调（防迟到披露/管道延迟）
    w0 = er.disclosure_window(datetime(2026, 7, 16), grace_days=0)
    assert w0["open"] is False
    w30 = er.disclosure_window(datetime(2026, 8, 10), grace_days=30)
    assert w30["open"] is True and w30["period"] == "20260630"


# ---------- window_dates / cross_hit_spans（窗口回放 · 历史台账） ----------

def test_window_dates_and_validation():
    from datetime import date
    assert er.window_dates("20260630") == (date(2026, 7, 1), date(2026, 7, 29))   # +14 grace
    assert er.window_dates("20260630", grace_days=0) == (date(2026, 7, 1), date(2026, 7, 15))
    assert er.window_dates("20251231") == (date(2026, 1, 1), date(2026, 2, 14))   # 年报窗在次年1月
    for bad in ("", "2026", "20261301", "2026063x", None):
        with pytest.raises(ValueError):
            er.window_dates(bad)


def _nav_crash(n_flat=100, n_drop=12, drop_to=0.72):
    """尾部急跌的 NAV 序列（str 日期索引）→ 末日偏离分位 ≈0（超卖）。"""
    idx = pd.date_range("2026-01-01", periods=n_flat + n_drop, freq="D").strftime("%Y-%m-%d")
    vals = [1.0] * n_flat + [1.0 - (1.0 - drop_to) * (i + 1) / n_drop for i in range(n_drop)]
    return pd.Series(vals, index=idx)


def _nav_rally(n_flat=100, n_rise=12, rise_to=1.30):
    idx = pd.date_range("2026-01-01", periods=n_flat + n_rise, freq="D").strftime("%Y-%m-%d")
    vals = [1.0] * n_flat + [1.0 + (rise_to - 1.0) * (i + 1) / n_rise for i in range(n_rise)]
    return pd.Series(vals, index=idx)


def _fc_panel(codes, announce):
    return pd.DataFrame(
        {"yoy": [50.0] * len(codes), "type": ["预增"] * len(codes),
         "announce_date": [announce] * len(codes)}, index=codes)


def test_cross_hit_spans_opp_span_merge_and_announce_gate():
    nav = _nav_crash()
    days = list(nav.index[-6:])
    cons = pd.DataFrame({"code": [f"S{i:05d}" for i in range(6)], "weight": [1.0] * 6})
    cons_map = {"A": cons}
    # 全部预告在回放首日前 → 6 天连续命中合并为 1 段（另一侧空表不崩 = IndexError 回归）
    fc = _fc_panel(list(cons["code"]), announce=days[0])
    out = er.cross_hit_spans({"A": nav}, fc, cons_map, days, PARAMS)
    assert list(out) == ["A"] and "opp" in out["A"] and "risk" not in out["A"]
    sp = out["A"]["opp"][0]
    assert (sp["first"], sp["last"], sp["n"]) == (days[0], days[-1], 6)
    assert "高增" in sp["detail"] and "多100%" in sp["detail"]
    # 预告在回放第 3 日才公告 → 命中从该日起（point-in-time 无前视）
    fc2 = _fc_panel(list(cons["code"]), announce=days[2])
    out2 = er.cross_hit_spans({"A": nav}, fc2, cons_map, days, PARAMS)
    assert out2["A"]["opp"][0]["first"] == days[2] and out2["A"]["opp"][0]["n"] == 4


def test_cross_hit_spans_risk_and_span_split():
    nav = _nav_rally()
    days = list(nav.index[-6:])
    codes = [f"S{i:05d}" for i in range(6)]
    # 5 家预增 + 1 家预减(权重1/6≈16.7%≥5%地板) → 超买×空广度 risk
    fc = pd.DataFrame(
        {"yoy": [50.0] * 5 + [-60.0], "type": ["预增"] * 5 + ["预减"],
         "announce_date": [days[0]] * 6}, index=codes)
    cons = pd.DataFrame({"code": codes, "weight": [1.0] * 6})
    out = er.cross_hit_spans({"A": nav}, fc, {"A": cons}, days, PARAMS)
    assert "risk" in out["A"] and "opp" not in out["A"]
    assert "空17%" in out["A"]["risk"][0]["detail"]
    # 断段：急跌(命中)→反弹出超卖区(不命中)→再跌(命中) → 两段 span
    n_flat = 100
    idx = pd.date_range("2026-01-01", periods=n_flat + 14, freq="D").strftime("%Y-%m-%d")
    vals = ([1.0] * n_flat
            + [1.0 - 0.28 * (i + 1) / 8 for i in range(8)]            # 跌到 0.72
            + [0.72 + 0.21 * (i + 1) / 3 for i in range(3)]           # 反弹到 0.93（出格）
            + [0.93 - 0.18 * (i + 1) / 3 for i in range(3)])          # 再跌到 0.75
    nav2 = pd.Series(vals, index=idx)
    days2 = list(idx[-9:])                                            # 8 跌 + 反弹 + 再跌
    fc2 = _fc_panel(codes, announce=days2[0])
    out2 = er.cross_hit_spans({"A": nav2}, fc2, {"A": cons}, days2, PARAMS)
    spans = out2["A"]["opp"]
    assert len(spans) == 2 and spans[0]["last"] < spans[1]["first"]
    assert all(sp["n"] >= 1 for sp in spans)


def test_cross_hit_spans_gates_and_skips():
    nav = _nav_crash()
    days = list(nav.index[-6:])
    codes = [f"S{i:05d}" for i in range(6)]
    cons = pd.DataFrame({"code": codes, "weight": [1.0] * 6})
    fc = _fc_panel(codes, announce=days[0])
    # 空广度 1/6 但 label=高增、分位≤5% → 只进 opp（elif 互斥）
    # 覆盖不足（只 1 家有预告 <min_matched 5）→ label 数据不足 → 不进
    fc_thin = _fc_panel(codes[:1], announce=days[0])
    out = er.cross_hit_spans({"A": nav}, fc_thin, {"A": cons}, days, PARAMS)
    assert out == {}
    # cons 空 → 跳过；nav 太短(<ma+20) → 跳过
    out2 = er.cross_hit_spans({"A": nav}, fc, {"A": pd.DataFrame()}, days, PARAMS)
    out3 = er.cross_hit_spans({"A": nav.iloc[:50]}, fc, {"A": cons}, days[:2], PARAMS)
    assert out2 == {} and out3 == {}
