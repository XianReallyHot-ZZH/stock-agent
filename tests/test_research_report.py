"""Tests for stockagent.research.report extreme-banner helpers (pure logic).

Covers _partition_extremes (threshold / sort / NaN & insufficient exclusion) and
_extreme_rank (events-based 第N低/高, None cases). HTML rendering itself is not tested.
"""
from __future__ import annotations

import math

from stockagent.research import report as rep

NaN = float("nan")


def _snap(pct, cur, events=None, sufficient=True):
    return {"nav_dev_pct": pct, "nav_dev_cur": cur,
            "nav_extreme_events": events or [], "data_sufficient": sufficient}


# ---------------- _partition_extremes ----------------

def test_partition_thresholds_and_sort():
    snaps = {
        "A": _snap(0.02, -0.15),   # oversold, most extreme
        "B": _snap(0.04, -0.10),   # oversold, milder
        "C": _snap(0.97, +0.14),   # overbought, milder
        "D": _snap(0.99, +0.20),   # overbought, most extreme
        "E": _snap(0.50, +0.01),   # neutral — excluded
        "F": _snap(0.06, -0.05),   # just above oversold threshold — excluded
        "G": _snap(0.94, +0.10),   # just below overbought threshold — excluded
    }
    ov, ob = rep._partition_extremes(snaps)
    assert [s for s, _ in ov] == ["A", "B"]   # 最超卖(最低分位)在前
    assert [s for s, _ in ob] == ["D", "C"]   # 最超买(最高分位)在前


def test_partition_excludes_nan_and_insufficient():
    snaps = {
        "N": _snap(NaN, NaN),                       # NaN 分位 — 排除
        "I": _snap(0.01, -0.20, sufficient=False),  # 数据不足 — 排除
        "O": _snap(0.03, -0.12),                    # 超卖 — 保留
    }
    ov, ob = rep._partition_extremes(snaps)
    assert [s for s, _ in ov] == ["O"]
    assert ob == []


def test_partition_boundary_inclusive():
    # pct 恰好 = 阈值也算进入（≤0.05 / ≥0.95）
    snaps = {"LO": _snap(0.05, -0.08), "HI": _snap(0.95, +0.08)}
    ov, ob = rep._partition_extremes(snaps)
    assert [s for s, _ in ov] == ["LO"]
    assert [s for s, _ in ob] == ["HI"]


# ---------------- _extreme_rank ----------------

def test_extreme_rank_low_side():
    events = [
        {"date": 1, "dev": -0.20, "side": "low", "rank": 1},
        {"date": 2, "dev": -0.18, "side": "low", "rank": 2},
        {"date": 3, "dev": -0.15, "side": "low", "rank": 3},
    ]
    assert rep._extreme_rank(_snap(0.01, -0.21, events)) == 1     # 比所有事件更极端 → 第1低
    assert rep._extreme_rank(_snap(0.03, -0.19, events)) == 2     # 仅次于 -0.20
    assert rep._extreme_rank(_snap(0.04, -0.16, events)) == 3     # 介于 rank2 / rank3
    assert rep._extreme_rank(_snap(0.05, -0.10, events)) is None  # 不在 top 极端 → None


def test_extreme_rank_high_side():
    events = [
        {"date": 1, "dev": 0.25, "side": "high", "rank": 1},
        {"date": 2, "dev": 0.20, "side": "high", "rank": 2},
    ]
    assert rep._extreme_rank(_snap(0.99, 0.26, events)) == 1      # 比所有高 → 第1高
    assert rep._extreme_rank(_snap(0.96, 0.21, events)) == 2      # 仅次于 0.25
    assert rep._extreme_rank(_snap(0.95, 0.15, events)) is None   # 不在 top


def test_extreme_rank_nan_and_empty():
    assert rep._extreme_rank(_snap(NaN, NaN)) is None
    assert rep._extreme_rank(_snap(0.02, -0.15, events=[])) is None   # 无事件


# ---------------- _order_detail (逐标的明细顺序：置顶优先) ----------------

def _rsnap(pct, sufficient=True):
    return {"nav_dev_pct": pct, "data_sufficient": sufficient}


def test_order_detail_pins_first_in_given_order():
    ranked = {
        "159915": _rsnap(0.12),   # 创业板（较不极端）
        "588000": _rsnap(0.17),   # 科创50（较不极端）
        "512010": _rsnap(0.96),   # 本该排前（最极端之一）
        "515880": _rsnap(0.02),   # 另一侧最极端
    }
    meta = {s: {"name": s} for s in ranked}
    syms = [s for s, _ in rep._order_detail(ranked, {}, ["159915", "588000"], meta)]
    assert syms[:2] == ["159915", "588000"]               # 置顶最前、保持给定顺序
    assert syms.index("159915") < syms.index("512010")    # 置顶先于非置顶
    # 余下按 |pct−0.5| 降序：515880(0.48) > 512010(0.46)
    assert syms[2:] == ["515880", "512010"]


def test_order_detail_no_pin_falls_back_to_extremeness():
    ranked = {"A": _rsnap(0.96), "B": _rsnap(0.10), "C": _rsnap(0.50)}
    meta = {s: {"name": s} for s in ranked}
    assert [s for s, _ in rep._order_detail(ranked, {}, None, meta)] == ["A", "B", "C"]


def test_order_detail_unknown_pin_skipped():
    ranked = {"A": _rsnap(0.96)}
    assert [s for s, _ in rep._order_detail(ranked, {}, ["ZZZ", "A"], {"A": {"name": "A"}})] == ["A"]


def test_order_detail_pinned_in_excluded_still_first():
    ranked = {"A": _rsnap(0.96)}
    excluded = {"B": _rsnap(0.10, sufficient=False)}
    meta = {"A": {"name": "A"}, "B": {"name": "B"}}
    assert [s for s, _ in rep._order_detail(ranked, excluded, ["B"], meta)] == ["B", "A"]
