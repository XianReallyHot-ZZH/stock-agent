"""Tests for stockagent.research.timing (ETF 择时跟踪纯函数).

Covers: copied deviation primitives on synthetic series; scissor_divergence across
divergent / convergent / flat / lumpy-shares / sub-floor scenarios; timing_snapshot
data_sufficient gating.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

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
