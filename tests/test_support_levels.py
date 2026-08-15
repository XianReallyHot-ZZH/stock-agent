"""Tests for support_levels — 平台顶/前低 两类规则选位 + 守住/破位结局 + 前向风险原语。

No network — 合成序列喂入;小参数(win/min_days/side 等)压缩场景长度。
"""
import math

import numpy as np
import pandas as pd

from stockagent.tracker import support_levels as sl


def _ser(values, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(values), freq="B").strftime("%Y-%m-%d")
    return pd.Series(values, index=idx, dtype=float)


def _flat(n, base=100.0, wig=0.2):
    rng = np.random.default_rng(7)
    return [base + (rng.random() - 0.5) * 2 * wig for _ in range(n)]


# ---------- 平台检测 ----------
def test_detect_platforms_finds_box_top():
    # 20 日箱体 → 陡坡尾(0.8/日,10日窗 range>5% 不再成箱,避免平尾自身成第二个平台)
    s = _ser(_flat(20) + [100 + i * 0.8 for i in range(1, 31)])
    plats = sl.detect_platforms(s, win=10, max_range=0.05, min_days=3)
    assert len(plats) == 1
    assert 99.8 <= plats[0]["level"] <= 100.2              # 顶≈箱体上沿


def test_detect_platforms_none_on_trend():
    s = _ser([100 + i for i in range(30)])                 # 单边上涨,无箱体
    assert sl.detect_platforms(s, win=10, max_range=0.05, min_days=3) == []


# ---------- 平台顶突破回测:守住 / 破位未收 / 破位收回 ----------
def _platform_series(after_touch, n_tail=70):
    """14 日箱体(~100) → 突破 106 ×3 日(带上方) → 回踩 100.5 → after_touch(结局段) → 尾部 70 日。"""
    return _ser(_flat(14) + [106.0, 106.5, 106.0] + [100.5] + after_touch + [101 + i * 0.1 for i in range(n_tail)])


KW = dict(win=10, max_range=0.05, min_days=3, min_above=2,
          resolve_win=5, reclaim_days=2, cooldown=5)


def test_platform_retest_hold():
    s = _platform_series([100.8, 101.5, 102.0, 103.0, 104.0])   # 回踩后不破 99
    evs = sl.breakout_retest_events(s, None, **KW)
    assert len(evs) == 1 and evs[0]["outcome"] == "hold"
    assert not evs[0]["pending"]


def test_platform_retest_break_down():
    s = _platform_series([98.0, 98.5, 99.0, 99.5, 100.0])       # 收破 99 且 2 日内不收回 101
    evs = sl.breakout_retest_events(s, None, **KW)
    assert len(evs) == 1 and evs[0]["outcome"] == "break_down"
    assert evs[0]["break_date"] is not None


def test_platform_retest_break_reclaim():
    s = _platform_series([98.0, 101.5, 102.0, 103.0, 104.0])    # 破位次日收回带上 → 假破
    evs = sl.breakout_retest_events(s, None, **KW)
    assert evs[0]["outcome"] == "break_reclaim"


def test_platform_failed_breakout_no_event():
    # 突破次日即跌回带内(min_above=2 不满足)→ 突破失败,不产事件
    s = _ser(_flat(14) + [106.0, 100.0] + [99.0] * 80)
    assert sl.breakout_retest_events(s, None, **KW) == []


# ---------- 前低枢轴 + 回测 ----------
def test_pivot_lows_finds_strict_bottom():
    s = _ser([105, 103, 100, 96, 90, 96, 100, 105, 108, 110])   # V 底
    piv = sl.pivot_lows(s, side=3)
    assert [p["level"] for p in piv] == [90.0]


def test_low_retest_hold():
    # 前置 3 日引导,保证 V 底(90)位于 index≥side=3 可被枢轴检测
    s = _ser([104, 100, 96, 90, 96, 100, 95.0, 95.5, 90.5, 91.0, 92.0, 93.0, 94.0]
             + [94 + i * 0.2 for i in range(70)])
    evs = sl.low_retest_events(s, None, side=3, resolve_win=5, reclaim_days=2, cooldown=5)
    assert len(evs) == 1 and evs[0]["outcome"] == "hold"
    assert evs[0]["kind"] == "low" and abs(evs[0]["level"] - 90.0) < 1e-9


# ---------- 合并 + kind 标注 ----------
def test_merged_events_kind_and_cooldown():
    s = _platform_series([100.8, 101.5, 102.0, 103.0, 104.0])
    evs = sl.merged_events(s, None, **{k: v for k, v in KW.items()
                                       if k in ("win", "max_range", "min_days", "min_above",
                                                "resolve_win", "reclaim_days", "cooldown")})
    assert evs and all(e["kind"] in ("platform", "low") for e in evs)
    pos = [s.index.get_loc(e["touch"]) for e in evs]
    assert all(b - a >= 5 for a, b in zip(pos, pos[1:]))         # 全局 cooldown 生效


# ---------- 前向风险 ----------
def test_forward_risk_rows_known_values():
    s = _ser([100.0] * 5 + [100.0, 90.0, 95.0, 92.0, 96.0, 98.0] + [98 + i * 0.1 for i in range(65)])
    ev = [{"level": 100.0, "touch": s.index[2], "confirm": s.index[5],
           "outcome": "hold", "break_date": None, "kind": "low",
           "platform_start": s.index[0], "platform_end": s.index[0],
           "breakout": s.index[2], "vol_ratio": float("nan"),
           "high_vol_break": False, "pending": False}]
    rows = sl.forward_risk_rows(s, ev, forward=(5, 10))
    assert len(rows) == 1
    r = rows[0]
    assert abs(r["ret_5"] - (98.0 / 100.0 - 1)) < 1e-9           # 确认日(100)后第5日=98
    assert abs(r["mdd_5"] - (90.0 / 100.0 - 1)) < 1e-9           # 窗内最低 90
    assert r["vol_20"] > 0 and not math.isnan(r["vol_20"])


def test_forward_risk_rows_skips_incomplete():
    s = _ser([100.0] * 8)                                        # 确认日后不足 60 日 → 跳过
    ev = [{"confirm": s.index[3], "touch": s.index[2], "outcome": "hold",
           "level": 100.0, "break_date": None, "kind": "low", "platform_start": "",
           "platform_end": "", "breakout": "", "vol_ratio": float("nan"),
           "high_vol_break": False, "pending": False}]
    assert sl.forward_risk_rows(s, ev) == []


# ---------- 分组摘要 + bootstrap ----------
def test_group_summary_counts_and_win():
    rows = [{"outcome": "hold", "ret_20": 0.1, "vol_20": 0.1},
            {"outcome": "hold", "ret_20": -0.1, "vol_20": 0.2},
            {"outcome": "break_down", "ret_20": 0.2, "vol_20": 0.3}]
    g = sl.group_summary(rows)
    assert g["hold"]["n"] == 2 and g["hold"]["win_20"] == 0.5
    assert abs(g["break_down"]["vol_20_med"] - 0.3) < 1e-9


def test_bootstrap_separation():
    assert sl.bootstrap_median_diff([1.0, 2, 3, 4, 5], [10, 11, 12, 13, 14])["separated"] is True
    assert sl.bootstrap_median_diff([1.0, 2, 3, 4, 5], [1.5, 2.5, 3.5, 4.5, 5.5])["separated"] is False
    assert sl.bootstrap_median_diff([1.0], [])["separated"] is False


# ---------- ⑩ 监测快照(看板用) ----------
def test_monitor_snapshot_hold_and_next_below():
    s = _platform_series([100.8, 101.5, 102.0, 103.0, 104.0])   # 守住,尾部涨到 ~108
    snap = sl.monitor_snapshot(s, None, **{k: v for k, v in KW.items()
                                           if k in ("win", "max_range", "min_days", "min_above",
                                                    "resolve_win", "reclaim_days", "cooldown")})
    assert snap["n_events"] == 1
    lv = snap["levels"][0]
    assert lv["outcome"] == "hold" and lv["state"] == "已守住"
    assert not lv["in_zone"] and lv["dist"] > 0                     # 现价(~108)在位上方
    assert snap["next_below"]["level"] == lv["level"]               # 未破位且在下方 → 第一支撑
    assert snap["next_above"] is None


def test_monitor_snapshot_pending_in_zone():
    # 尾部只剩 2 日:回踩窗口(resolve_win=5)未走完 → 真·测试中(非 pending 前向不足)
    s = _platform_series([], n_tail=2)
    s.iloc[-1] = 100.2                                              # 现价拉回带内(位±2%)
    kw = {k: v for k, v in KW.items()
          if k in ("win", "max_range", "min_days", "min_above",
                   "resolve_win", "reclaim_days", "cooldown")}
    kw["zone_eps"] = 0.02
    snap = sl.monitor_snapshot(s, None, **kw)
    lv = snap["levels"][0]
    assert "测试中" in lv["state"] and lv["in_zone"] is True


def test_monitor_snapshot_break_down_vol_window():
    # 破位·未收,确认(break+2)距今 17 日 ≤ 20 → 波动窗口内
    s = _platform_series([98.0, 98.5, 99.0, 99.5, 100.0], n_tail=15)
    snap = sl.monitor_snapshot(s, None, **{k: v for k, v in KW.items()
                                           if k in ("win", "max_range", "min_days", "min_above",
                                                    "resolve_win", "reclaim_days", "cooldown")})
    lv = snap["levels"][0]
    assert lv["outcome"] == "break_down" and "波动窗口内" in lv["state"]
