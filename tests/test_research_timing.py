"""Tests for stockagent.research.timing (ETF 择时跟踪纯函数).

Covers: copied deviation primitives on synthetic series; scissor_divergence across
divergent / convergent / flat / lumpy-shares / sub-floor scenarios; timing_snapshot
data_sufficient gating.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stockagent.research import timing as tm


def _idx(n: int = 250) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="D")


# ---------------- deviation primitives (copied) ----------------

def test_ma_series_short_and_known():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    ma = tm.ma_series(s, period=2)
    assert np.isnan(ma.iloc[0])
    assert ma.iloc[1] == 1.5 and ma.iloc[-1] == 4.5
    # too short → all-NaN with same index
    short = tm.ma_series(pd.Series([1.0, 2.0]), period=60)
    assert short.isna().all() and len(short) == 2


def test_deviation_series_sign():
    s = pd.Series(np.linspace(1.0, 1.2, 200), index=_idx(200))  # rising → dev≥0 at tail
    dev = tm.deviation_series(s, period=60)
    assert dev.iloc[:59].isna().all()
    assert dev.iloc[-1] > 0


def test_deviation_extremes_valid_and_pct_bounds():
    # oscillation around 100 → deviation swings both ways
    s = pd.Series(100 + 30 * np.sin(np.linspace(0, 4 * np.pi, 200)), index=_idx(200))
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"] is True
    assert ext["max_dev"] > 0 > ext["min_dev"]
    assert 0.0 <= ext["pct"] <= 1.0


def test_deviation_extremes_short_series_invalid():
    s = pd.Series(np.linspace(1, 2, 50), index=_idx(50))  # < ma_period
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"] is False
    assert np.isnan(ext["pct"])


def test_deviation_extremes_tail_spike_is_high_percentile():
    # flat then ramp up → last bar is among the highest deviations
    close = np.r_[np.full(180, 100.0), np.linspace(100, 135, 40)]
    s = pd.Series(close, index=_idx(220))
    ext = tm.deviation_extremes(s, period=60)
    assert ext["valid"]
    assert ext["pct"] > 0.90  # near the top of its own history


def test_deviation_extreme_events_have_ranked_extremes():
    s = pd.Series(100 + 30 * np.sin(np.linspace(0, 4 * np.pi, 200)), index=_idx(200))
    evs = tm.deviation_extreme_events(s, period=60)
    assert len(evs) > 0
    sides = {e["side"] for e in evs}
    assert "low" in sides and "high" in sides
    # each side's ranks are 1..k contiguous, starting at 1 (1 = most extreme)
    for side in ("low", "high"):
        ranks = sorted(e["rank"] for e in evs if e["side"] == side)
        assert ranks[0] == 1
        assert ranks == list(range(1, len(ranks) + 1))


def test_deviation_extreme_events_default_top10():
    # 慢振荡序列:极值区间多 → 默认各侧截到 Top-10(2026-09-08 从 8 放宽)
    s = pd.Series(100 + 30 * np.sin(np.linspace(0, 40 * np.pi, 4000)), index=_idx(4000))
    evs = tm.deviation_extreme_events(s, period=60)
    for side in ("low", "high"):
        ranks = sorted(e["rank"] for e in evs if e["side"] == side)
        assert ranks == list(range(1, 11))            # 两侧各满 10 个


# ---------------- side_percentile（方向内分位·悬停通用原语） ----------------

def test_side_percentile_pools_and_fallback():
    # 正负各 40 天:各自池内统计,两侧最深处各 = 100%
    vals = list(np.linspace(0.01, 0.05, 40)) + list(np.linspace(-0.05, -0.01, 40))
    sp = tm.side_percentile(pd.Series(vals, index=_idx(80)))
    assert sp.iloc[39] == 1.0 and sp.iloc[40] == 1.0
    assert sp.notna().all()
    # 负侧仅 3 天(<30) → 负日退绝对值双向分位;正侧样本也不足 → 全部双向
    s2 = pd.Series([0.01, 0.02, -0.06, 0.03, -0.01, 0.04, -0.02, 0.05], index=_idx(8))
    sp2 = tm.side_percentile(s2)
    ar = s2.abs().rank(method="average", pct=True)
    assert list(sp2) == list(ar)
    # NaN 透传;空序列 → 空
    sp3 = tm.side_percentile(pd.Series([np.nan, 0.01, np.nan]))
    assert sp3.iloc[0] != sp3.iloc[0] and sp3.iloc[1] == sp3.iloc[1]
    assert tm.side_percentile(pd.Series(dtype=float)).empty


# ---------------- scissor_divergence ----------------

def test_scissor_divergent_share_up_nav_down():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 0.8, 250), index=idx)          # -20%
    shares = pd.Series(np.linspace(1e8, 1.3e8, 250), index=idx)     # +30%
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_up_nav_down"
    assert r["share_drift"] > 0 and r["nav_drift"] < 0
    assert r["window"] >= 20


def test_scissor_divergent_share_down_nav_up():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 1.25, 250), index=idx)         # +25%
    shares = pd.Series(np.linspace(1.3e8, 1e8, 250), index=idx)     # -23%
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_down_nav_up"


def test_scissor_convergent_not_detected():
    idx = _idx(250)
    nav = pd.Series(np.linspace(1.0, 1.2, 250), index=idx)          # both up → same sign
    shares = pd.Series(np.linspace(1e8, 1.3e8, 250), index=idx)
    assert tm.scissor_divergence(shares, nav)["detected"] is False


def test_scissor_flat_not_detected():
    idx = _idx(250)
    nav = pd.Series(np.full(250, 1.0), index=idx)
    shares = pd.Series(np.full(250, 1e8), index=idx)
    assert tm.scissor_divergence(shares, nav)["detected"] is False


def test_scissor_lumpy_shares_handled():
    # shares flat then a block step jump (creation/redemption is lumpy, not daily-smooth)
    idx = _idx(250)
    sh = np.full(250, 1e8)
    sh[150:] = 1.25e8                                             # +25% step
    nav = pd.Series(np.linspace(1.0, 0.8, 250), index=idx)        # -20%
    shares = pd.Series(sh, index=idx)
    r = tm.scissor_divergence(shares, nav)
    assert r["detected"] is True
    assert r["direction"] == "share_up_nav_down"


def test_scissor_sub_floor_not_detected():
    # divergence concentrated in the last 60 days: ±4% (between a 2% and 5% floor)
    idx = _idx(250)
    nav = pd.Series(np.r_[np.full(190, 1.0), np.linspace(1.0, 0.96, 60)], index=idx)      # -4%
    shares = pd.Series(np.r_[np.full(190, 1e8), np.linspace(1e8, 1.04e8, 60)], index=idx)  # +4%
    assert tm.scissor_divergence(shares, nav, floor=0.05)["detected"] is False  # 4% < 5%
    assert tm.scissor_divergence(shares, nav, floor=0.02)["detected"] is True   # 4% ≥ 2%


def test_scissor_none_inputs():
    assert tm.scissor_divergence(None, None)["detected"] is False
    assert tm.scissor_divergence(None, pd.Series([1.0]))["detected"] is False


# ---------------- timing_snapshot ----------------

def test_timing_snapshot_short_history_insufficient():
    nav_df = pd.DataFrame({"unit_nav": np.linspace(1, 1.1, 30), "acc_nav": np.linspace(1, 1.1, 30)},
                          index=_idx(30))
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["data_sufficient"] is False
    assert np.isnan(snap["nav_dev_pct"])
    assert snap["scissor"]["detected"] is False


def test_timing_snapshot_sufficient_has_fields():
    idx = _idx(200)
    nav_df = pd.DataFrame({"acc_nav": np.linspace(1.0, 1.2, 200)}, index=idx)
    shares_df = pd.DataFrame({"shares": np.linspace(1e8, 1.3e8, 200)}, index=idx)
    snap = tm.timing_snapshot(nav_df, shares_df, ma_period=60)
    assert snap["data_sufficient"] is True
    assert 0.0 <= snap["nav_dev_pct"] <= 1.0
    assert isinstance(snap["nav_extreme_events"], list)
    assert snap["nav_col"] == "acc_nav"
    # shares + nav same direction here (both up) → no divergence
    assert snap["scissor"]["detected"] is False


def test_timing_snapshot_prefers_acc_nav():
    idx = _idx(200)
    # acc_nav continuous; unit_nav has an artificial cliff → must pick acc_nav
    unit = np.linspace(1.0, 1.2, 200)
    unit[120:] = unit[120:] * 0.5  # cliff
    nav_df = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.2, 200)}, index=idx)
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["nav_col"] == "acc_nav"


def test_timing_snapshot_event_rank_today_is_trough():
    # 尾部深跌到最后一根(今天=episode 谷点) → nav_dev_rank = 事件 rank(图 ▼第N 同数)
    idx = _idx(200)
    nav = np.linspace(1.0, 1.2, 200)
    nav[-8:] = nav[-8] * np.linspace(1.0, 0.80, 8)      # 尾部 -20% 直落,今天最深
    snap = tm.timing_snapshot(pd.DataFrame({"acc_nav": nav}, index=idx), None, ma_period=60)
    lows = [e for e in snap["nav_extreme_events"] if e["side"] == "low"]
    assert snap["nav_dev_rank"] == 1                    # 史上最深谷=今天
    assert str(lows[0]["date"])[:10] == str(idx[-1])[:10]


def test_timing_snapshot_event_rank_rebound_day_hypothetical():
    # 谷点在 T-3、今天从谷底反弹(同 episode·非谷点) → 假设性排名 = 更深 episode 数+1
    idx = _idx(200)
    nav = np.linspace(1.0, 1.2, 200)
    nav[-8:-3] = nav[-8] * np.linspace(1.0, 0.80, 5)    # 跌到 T-3 触底 -20%
    nav[-3:] = nav[-4] * np.linspace(1.0, 1.03, 3)      # 今天反弹但仍深跌
    snap = tm.timing_snapshot(pd.DataFrame({"acc_nav": nav}, index=idx), None, ma_period=60)
    assert snap["nav_dev_rank"] == 2                    # 只有 T-3 那个谷比今天深 → 第2低
    assert snap["nav_dev_cur"] < 0                      # 仍在超卖侧


def test_timing_snapshot_daily_flow_uses_last_real_share_date():
    # 份额 T+1 滞后:nav 到 T、份额只到 T-3(且 T-3 有真实变动)——「日申赎」必须取
    # 最后一个真实份额观测日的值,而非 ffill 出来的 Δ=0(2026-09-08 收盘后全 0 bug)
    idx = _idx(300)
    nav = pd.DataFrame({"unit_nav": np.linspace(1.0, 1.3, 300),
                        "acc_nav": np.linspace(1.0, 1.3, 300)}, index=idx)
    shares = np.full(297, 1e9)
    shares[-1] = 1.05e9                                  # T-3 那天真实 +5%
    sh_df = pd.DataFrame({"shares": shares}, index=idx[:297])
    snap = tm.timing_snapshot(nav, sh_df, ma_period=60)
    dfl = snap["daily_flow"]
    assert dfl["date"] == str(idx[296].date())           # 最后真实观测日,非 nav 末日
    assert dfl["pct"] == pytest.approx(5.0)              # +5%,不是 ffill 的 0
    assert dfl["flow_yi"] == pytest.approx(0.05e9 * float(nav["unit_nav"].iloc[296]) / 1e8)  # Δ份×当日净值
    assert snap["nav_last"] == str(idx[299].date())      # 滞后判定基准


# ---------------- chip_direction（筹码方向 · 近端加权投票 + 死区） ----------------

def _chip_series(n=200, mid_pct=0.0, tail_ramp_pct=0.0, ramp_days=70, seed=11):
    """构造 (nav, shares)：中段(40%起)阶跃 mid_pct；末 ramp_days 日线性再变 tail_ramp_pct。
    线性 ramp 保证每个投票窗口（5..60 < ramp_days）都能看到变化而非平台。"""
    rng = np.random.default_rng(seed)
    idx = _idx(n)
    nav = pd.Series(1.0 * np.cumprod(1 + rng.normal(0, 0.001, n)), index=idx)
    base = np.full(n, 1e9)
    if mid_pct:
        base[int(n * 0.4):] = 1e9 * (1 + mid_pct)
    if tail_ramp_pct:
        start = base[n - ramp_days]
        base[n - ramp_days:] = np.linspace(start, start * (1 + tail_ramp_pct), ramp_days)
    return nav, pd.Series(base, index=idx)


def test_chip_weights_near_heavier():
    nav, shares = _chip_series(mid_pct=0.05)
    r = tm.chip_direction(shares, nav, windows=(5, 10, 20, 30, 60))
    assert r["weights"] == {5: 5, 10: 4, 20: 3, 30: 2, 60: 1}   # 越近权重越高（等差）
    assert r["vote_threshold"] == 2


def test_chip_accumulating_consensus():
    nav, shares = _chip_series(mid_pct=0.10, tail_ramp_pct=0.06)   # 中段+10%，末70日线性再+6%
    r = tm.chip_direction(shares, nav, windows=(5, 10, 20, 30, 60), deadzone=0.01)
    assert r["data_sufficient"]
    assert r["state"] == "accumulating" and r["votes"] >= 2
    assert r["flow_main_window"] == 20


def test_chip_distributing_consensus():
    nav, shares = _chip_series(mid_pct=-0.10, tail_ramp_pct=-0.06)
    r = tm.chip_direction(shares, nav, windows=(5, 10, 20, 30, 60), deadzone=0.01)
    assert r["state"] == "distributing" and r["votes"] <= -2


def test_chip_deadzone_flat():
    # 全程变化在 ±0.5%（死区内）→ 全部弃票 → flat
    nav, shares = _chip_series(mid_pct=0.005, tail_ramp_pct=-0.004)
    r = tm.chip_direction(shares, nav, windows=(5, 10, 20, 30, 60), deadzone=0.01)
    assert r["state"] == "flat" and r["votes"] == 0
    assert all(abs(v) <= 0.01 for v in r["flows"].values())


def _sh_with_points(n=200, last=1.0e9, pts=None):
    """显式端点构造：pts = {index: 份额}，其余填 base；窗口 flow 只看端点。"""
    sh = np.full(n, 1.02e9)
    for i, v in (pts or {}).items():
        sh[i] = v
    sh[-1] = last
    return pd.Series(sh, index=_idx(n))


def test_chip_far_consensus_beats_single_near_vote():
    # 5日 +2%（权5）但 10/20/30/60 日均 −2%（权4+3+2+1=9）→ 票和 −5 → 减（远端共识翻盘）
    nav = pd.Series(np.linspace(1.0, 1.02, 200), index=_idx(200))
    sh = _sh_with_points(last=1.0e9, pts={195: 0.98e9})   # 其余端点在 base 1.02e9
    r = tm.chip_direction(sh, nav, windows=(5, 10, 20, 30, 60), deadzone=0.01)
    assert r["votes"] == 5 - 4 - 3 - 2 - 1 and r["state"] == "distributing"


def test_chip_near_votes_beat_far():
    # 5/10日 +3%（权9）、20日死区（0票）、30/60日 −3%（权3）→ 票和 +6 → 增（近端主导）
    nav = pd.Series(np.linspace(1.0, 1.02, 200), index=_idx(200))
    sh = np.full(200, 1.03e9)
    sh[180:190] = 0.995e9        # 20日端点(180)死区内
    sh[190:195] = 0.97e9         # 10日端点(190) +3%
    sh[195:] = 0.97e9            # 5日端点(195) +3%（末端=1.0e9 由下句覆盖）
    sh = pd.Series(sh, index=_idx(200))
    sh.iloc[-1] = 1.0e9
    r = tm.chip_direction(sh, nav, windows=(5, 10, 20, 30, 60), deadzone=0.01)
    assert r["votes"] == 5 + 4 + 0 - 2 - 1 and r["state"] == "accumulating"


def test_chip_single_window_threshold_downgrades():
    # 单窗口：权重 1 → 阈值降级为 1（一票即定）
    idx = _idx(200)
    nav = pd.Series(np.linspace(1.0, 1.02, 200), index=idx)
    for pct, want in ((0.05, "accumulating"), (-0.05, "distributing"), (0.005, "flat")):
        sh = np.full(200, 1e9)
        sh[-60:] = np.linspace(1e9, 1e9 * (1 + pct), 60)
        r = tm.chip_direction(pd.Series(sh, index=idx), nav, windows=(20,), deadzone=0.01)
        assert r["state"] == want, (pct, r)


def test_chip_insufficient_or_none():
    idx = _idx(60)
    nav = pd.Series(np.linspace(1, 1.05, 60), index=idx)
    shares = pd.Series(np.full(60, 1e9), index=idx)
    # 60 日窗口历史不足 → 该票弃；5/10/20/30 可用 → 仍 data_sufficient
    r = tm.chip_direction(shares, nav, windows=(5, 10, 20, 30, 60))
    assert r["data_sufficient"] and 60 not in r["flows"]
    assert not tm.chip_direction(None, nav)["data_sufficient"]
    assert not tm.chip_direction(shares, None)["data_sufficient"]
    # 全部窗口都不足 → False
    short_idx = _idx(10)
    assert not tm.chip_direction(pd.Series(np.full(10, 1e9), index=short_idx),
                                 pd.Series(np.linspace(1, 1.01, 10), index=short_idx),
                                 windows=(20, 60))["data_sufficient"]


def test_timing_snapshot_embeds_chip():
    idx = _idx(200)
    nav_df = pd.DataFrame({"acc_nav": np.linspace(1.0, 1.2, 200)}, index=idx)
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["chip"]["data_sufficient"] is False
    assert snap["chip"]["state"] == "flat"
    shares_df = pd.DataFrame({"shares": np.linspace(1e9, 1.3e9, 200)}, index=idx)
    snap2 = tm.timing_snapshot(nav_df, shares_df, ma_period=60)
    assert snap2["chip"]["data_sufficient"] is True
    assert snap2["chip"]["state"] in ("accumulating", "distributing", "flat")


# ---------------- split_adjusted_shares（拆分/折算 前复权 · 份额系指标共用前置） ----------------

def _split_frames(n=200, split_at=100, ratio=2.0):
    """构造 (nav_df, shares_df)：split_at 日份额 ×(1/ratio 系数语义见下) 且 unit_nav 反向。

    ratio>1 = 拆分（份额 ×ratio、净值 ÷ratio）；ratio<1 = 反向折算（份额 ÷、净值 ×）。
    acc_nav 始终连续（复权），符合真实数据形态。
    """
    idx = _idx(n)
    unit = np.linspace(2.0, 2.1, n)
    sh = np.full(n, 1e9)
    unit[split_at:] = unit[split_at:] / ratio
    sh[split_at:] = 1e9 * ratio
    nav_df = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.3, n)}, index=idx)
    shares_df = pd.DataFrame({"shares": sh}, index=idx)
    return nav_df, shares_df


def test_split_adjusted_shares_split_detected_and_continuous():
    nav_df, shares_df = _split_frames(ratio=2.0)
    adj, events = tm.split_adjusted_shares(shares_df, nav_df)
    assert len(events) == 1
    assert abs(events[0]["ratio"] - 2.0) < 1e-9
    # 前复权后全序列连续：单日环比无 >10% 跳变，前后段同量级（末段口径 2e9）
    roc = adj.pct_change().dropna().abs()
    assert roc.max() < 0.10
    assert abs(adj.iloc[0] - 2e9) < 1e6 and abs(adj.iloc[-1] - 2e9) < 1e6


def test_split_adjusted_shares_reverse_consolidation():
    # 反向折算：份额 ÷2、unit_nav ×2 → 同样检测并前复权
    nav_df, shares_df = _split_frames(ratio=0.5)
    adj, events = tm.split_adjusted_shares(shares_df, nav_df)
    assert len(events) == 1
    roc = adj.pct_change().dropna().abs()
    assert roc.max() < 0.10
    assert abs(adj.iloc[-1] - 5e8) < 1e6


def test_split_adjusted_shares_multiple_splits_cumulative():
    idx = _idx(200)
    unit = np.linspace(2.0, 2.1, 200)
    sh = np.full(200, 1e9)
    unit[60:] = unit[60:] / 2
    sh[60:] = 2e9
    unit[140:] = unit[140:] / 3
    sh[140:] = 6e9
    nav_df = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.3, 200)}, index=idx)
    adj, events = tm.split_adjusted_shares(pd.DataFrame({"shares": sh}, index=idx), nav_df)
    assert len(events) == 2
    roc = adj.pct_change().dropna().abs()
    assert roc.max() < 0.10
    assert abs(adj.iloc[-1] - 6e9) < 1e6


def test_split_adjusted_shares_dividend_not_split():
    # 分红：unit_nav 断崖 -30% 但份额无 ≥20% 跳变 → 不调整（如 512690 2021-12-31）
    idx = _idx(200)
    unit = np.linspace(2.0, 2.1, 200)
    unit[100:] = unit[100:] * 0.7
    nav_df = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1.0, 1.3, 200)}, index=idx)
    shares_df = pd.DataFrame({"shares": np.full(200, 1e9)}, index=idx)
    adj, events = tm.split_adjusted_shares(shares_df, nav_df)
    assert events == []
    assert abs(adj.max() - 1e9) < 1e6


def test_split_adjusted_shares_big_real_creation_not_split():
    # 真实巨额申购：份额 +30% 但 unit_nav 正常波动 → 保留（真信号）
    idx = _idx(200)
    nav_df = pd.DataFrame({"unit_nav": np.linspace(1.0, 1.02, 200),
                           "acc_nav": np.linspace(1.0, 1.02, 200)}, index=idx)
    sh = np.full(200, 1e9)
    sh[100:] = 1.3e9
    adj, events = tm.split_adjusted_shares(pd.DataFrame({"shares": sh}, index=idx), nav_df)
    assert events == []
    assert abs(adj.iloc[-1] - 1.3e9) < 1e6   # 跳变原样保留


def test_timing_snapshot_chip_uses_split_adjusted_shares():
    # 回归修复：刚拆分 ETF 的长窗口筹码票不得被 +100% 假"申赎"污染
    nav_df, shares_df = _split_frames(ratio=2.0)   # 第 100 日拆分，前后份额各自平坦
    snap = tm.timing_snapshot(nav_df, shares_df, ma_period=60)
    assert len(snap["share_splits"]) == 1
    assert snap["chip"]["data_sufficient"] is True
    assert snap["chip"]["state"] == "flat"          # 前复权后各窗口 flow≈0（原始口径会是 +100% → 假 accumulating）
    assert max(abs(v) for v in snap["chip"]["flows"].values()) < 0.01


# ---------------- latest_daily_flow（最新日净申赎 · 当日脉搏） ----------------
def test_latest_daily_flow_basic_extreme_high():
    # 299 日平坦 + 末日 +2% 净申购：pct=+2%，其余全 0 → 带符号分位=100%（史上最大申购日）
    n = 300
    idx = _idx(n)
    adj = pd.Series(np.full(n, 1e9), index=idx)
    adj.iloc[-1] = 1.02e9
    nav = pd.Series(np.full(n, 2.0), index=idx)
    r = tm.latest_daily_flow(adj, nav, min_history=250)
    assert r["data_sufficient"] is True
    assert abs(r["pct"] - 2.0) < 1e-9
    assert r["pctile"] == 1.0                       # 全历史严格小于今日
    assert abs(r["flow_yi"] - 0.02 * 1e9 * 2.0 / 1e8) < 1e-6   # +0.4亿
    assert r["date"] == str(idx[-1].date())
    assert r["n_history"] == n - 1


def test_latest_daily_flow_extreme_low_and_mid():
    # 末日 -5% 净赎回（史上最大异动日·双向口径）→ |x| 分位 1.0（方向由符号/着色表达）
    n = 300
    idx = _idx(n)
    base = np.concatenate([np.full(n - 1, 1e9), [0.95e9]])
    r = tm.latest_daily_flow(pd.Series(base, index=idx),
                             pd.Series(np.full(n, 2.0), index=idx))
    assert r["pctile"] == 1.0
    # 波动序列，末日|变化率|取历史|变化率|中位 → 绝对值分位应落在中部
    rng = np.random.default_rng(7)
    sh = 1e9 * np.cumprod(1 + rng.normal(0, 0.005, n))
    med_abs = np.median(np.abs(np.diff(sh) / sh[:-1]))
    sh[-1] = sh[-2] * (1 + med_abs)
    r2 = tm.latest_daily_flow(pd.Series(sh, index=idx), None)
    assert 0.2 < r2["pctile"] < 0.8
    assert np.isnan(r2["flow_yi"])                  # 无 unit_nav → 金额缺、%/分位照常


def test_latest_daily_flow_side_pctile_beats_abs_on_skew():
    """方向内分位 vs 绝对值分位的语义差（2026-08 定稿原因）：申购端尾巴更肥的
    历史里，史上最大赎回日 按方向=100%（赎回向第一），绝对值口径被 +7% 申购日
    压低 <100%——前者才是「这个方向空前」的忠实表达。"""
    n = 300
    idx = _idx(n)
    rng = np.random.default_rng(3)
    chg = rng.normal(0.0, 0.008, n)                 # 双向小波动
    chg[50] = 0.07                                  # 一次 +7% 巨额申购(史上最肥)
    chg[-1] = -0.05                                 # 末日 -5%：史上最大赎回
    sh = 1e9 * np.cumprod(1 + chg)
    r = tm.latest_daily_flow(pd.Series(sh, index=idx),
                             pd.Series(np.full(n, 2.0), index=idx))
    assert r["pctile_kind"] == "side"
    assert r["pctile"] == 1.0                       # 赎回向史上第一
    # 绝对值口径下它排在 +7% 之后 → <100%（对照：横幅的双向分位）
    pct = pd.Series(chg).iloc[1:]
    abs_rank = float(pct.abs().rank(method="average", pct=True).iloc[-1])
    assert abs_rank < 1.0


def test_latest_daily_flow_side_fallback_to_abs():
    """某方向样本 < side_min_obs → 诚实退绝对值双向分位（kind="abs"）。"""
    n = 300
    idx = _idx(n)
    rng = np.random.default_rng(5)
    chg = np.abs(rng.normal(0.004, 0.004, n))       # 几乎全申购的历史
    chg[-1] = -0.03                                 # 末日罕见赎回: 赎回侧样本=1
    sh = 1e9 * np.cumprod(1 + chg)
    r = tm.latest_daily_flow(pd.Series(sh, index=idx), None)
    assert r["pctile_kind"] == "abs" and r["n_side"] == 1
    assert r["pctile"] == 1.0                       # 绝对值口径仍是史上最大异动


def test_latest_daily_flow_min_history_gate():
    n = 100
    idx = _idx(n)
    adj = pd.Series(np.linspace(1e8, 1.3e8, n), index=idx)
    r = tm.latest_daily_flow(adj, pd.Series(np.full(n, 2.0), index=idx), min_history=250)
    assert r["data_sufficient"] is False
    assert r["n_history"] == n - 1                  # 诚实报历史长度
    assert tm.latest_daily_flow(None, None)["data_sufficient"] is False


def test_timing_snapshot_includes_daily_flow():
    # 快照接线：daily_flow 随 snapshot 返回，日期=份额末日；金额用 unit_nav 口径
    n = 300
    idx = _idx(n)
    nav_df = pd.DataFrame({"unit_nav": np.full(n, 2.0),
                           "acc_nav": np.linspace(1.0, 1.2, n)}, index=idx)
    sh = np.full(n, 1e9)
    sh[-1] = 1.01e9                                 # 末日 +1% 净申购
    snap = tm.timing_snapshot(nav_df, pd.DataFrame({"shares": sh}, index=idx), ma_period=60)
    dfl = snap["daily_flow"]
    assert dfl["data_sufficient"] is True
    assert dfl["date"] == str(idx[-1].date())
    assert abs(dfl["pct"] - 1.0) < 1e-9
    assert dfl["pctile"] == 1.0                     # 唯一非零日 → 史上最大申购日


def test_timing_snapshot_insufficient_path_has_daily_flow_key():
    nav_df = pd.DataFrame({"acc_nav": np.linspace(1, 1.1, 30)}, index=_idx(30))
    snap = tm.timing_snapshot(nav_df, None, ma_period=60)
    assert snap["daily_flow"]["data_sufficient"] is False
