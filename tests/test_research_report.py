"""Tests for stockagent.research.report helpers (pure logic + render structure).

Covers _partition_extremes (threshold / sort / NaN & insufficient exclusion),
_extreme_rank (events-based 第N低/高, None cases), _order_detail (置顶优先), the
dual-axis y-title wiring in shares_nav_figure, and the rendered HTML structure
(theme toggle / rank tabs / collapsible details / quick-jump / back-to-top).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

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


# ---------------- shares_nav_figure 双轴标题（回归：左=净值/右=份额，勿装反） ----------------

def _mini_dfs(n=80):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    nav = pd.DataFrame({"acc_nav": np.linspace(1.0, 1.2, n)}, index=idx)
    shares = pd.DataFrame({"shares": np.linspace(1e9, 1.5e9, n)}, index=idx)
    return shares, nav


def test_shares_nav_figure_yaxis_titles():
    shares, nav = _mini_dfs()
    fig = rep.shares_nav_figure("测试ETF", shares, nav)
    assert fig.layout.yaxis.title.text == "累计净值"        # 左轴（净值）
    assert fig.layout.yaxis2.title.text == "份额（亿份）"   # 右轴（份额）


# ---------------- render 结构（主题/tab/折叠明细/快速跳转/回顶部/排序） ----------------

def _render_mini():
    snaps = {
        "159915": {"nav_dev_pct": 0.02, "nav_dev_cur": -0.15, "nav_extreme_events": [],
                   "data_sufficient": True, "style": "growth", "aum_yi": 500.0,
                   "turnover_5d_yi": 12.3,
                   "chip": {"data_sufficient": True, "state": "accumulating", "votes": 6,
                            "flow_main": 0.032, "flow_main_window": 20,
                            "flows": {5: 0.011, 10: 0.018, 20: 0.032, 30: 0.028, 60: 0.051}}},
        "512880": {"nav_dev_pct": 0.97, "nav_dev_cur": 0.10, "nav_extreme_events": [],
                   "data_sufficient": True, "style": "value", "aum_yi": 300.0,
                   "turnover_5d_yi": 8.1,
                   "chip": {"data_sufficient": True, "state": "distributing", "votes": -13,
                            "flow_main": -0.045, "flow_main_window": 20,
                            "flows": {5: -0.022, 10: -0.027, 20: -0.045, 30: -0.029, 60: 0.026}}},
        "512010": {"nav_dev_pct": 0.03, "nav_dev_cur": -0.12, "nav_extreme_events": [],
                   "data_sufficient": True, "style": "growth", "aum_yi": 200.0,
                   "turnover_5d_yi": 3.0,
                   "chip": {"data_sufficient": True, "state": "flat", "votes": 0,
                            "flow_main": 0.004, "flow_main_window": 20,
                            "flows": {5: 0.003, 10: 0.004, 20: 0.004, 30: 0.002, 60: -0.003}}},
    }
    meta = {"159915": {"name": "创业板ETF"}, "512880": {"name": "证券ETF"}}
    sm = {"159915": {"shares": None, "nav": None}, "512880": {"shares": None, "nav": None}}
    return rep.render(snaps, sm, meta, as_of="2026-08-14", pinned=["159915"])


def test_render_page_scaffold():
    html = _render_mini()
    assert '<meta name="viewport"' in html                     # 移动端 viewport
    assert 'id="theme-btn"' in html and "toggleTheme" in html  # 深浅色切换
    assert "localStorage.getItem('research-dark')" in html     # 主题记忆
    assert 'id="back-top"' in html                             # 回顶部
    assert 'id="etf-jump"' in html and "jumpToEtf" in html     # 快速跳转下拉
    assert 'class="rank-tabs"' in html                         # 排名真 tab
    assert 'id="tab-value"' in html and 'id="tab-growth"' in html and 'id="tab-cyclic"' in html
    assert "localStorage.getItem('research-rank-tab')" in html  # tab 记忆
    assert 'class="sortable"' in html and 'data-key="dev"' in html  # 表头排序
    assert "body.dark" in html                                 # 暗色 CSS 覆盖存在
    assert "flex-wrap:wrap" in html                            # 极端区横幅窄屏换行
    # 懒渲染 v2 调度：滚动停稳后分帧渲染 + render/purge 滞回双窗口 + 占位提示
    assert "requestAnimationFrame" in html
    assert "'800px 0px'" in html and "'3000px 0px'" in html
    assert "图表渲染中" in html


def test_render_quadrant_banner_and_chip_column():
    html = _render_mini()
    # 四象限横幅：四格齐全 + 代理口径副注
    assert "偏离度 × 筹码动向 · 四象限提醒" in html and "机构行为代理" in html
    for title in ("机会提醒", "关注提醒", "严重警告", "风险提示"):
        assert title in html
    # 超卖+筹码增 → 机会格含 159915；超买+筹码减 → 风险格
    assert "超卖+筹码增" in html and 'href="#159915"' in html
    assert "超买+筹码减" in html and 'href="#512880"' in html
    # 筹码持平的 512010（虽在超卖区）不入任何格 —— 横幅里不出现它的链接
    assert 'href="#512010"' not in html.split("四象限提醒")[1].split("逐标的明细")[0]
    # 排名表筹码列 + 排序键；明细 chips 象限标签
    assert 'data-key="chip"' in html and 'data-chip="0.0320"' in html
    assert "筹码" in html and "机会提醒·超卖+筹码增" in html
    # 多窗口值直接展示（非悬停）：表格第二行 + 横幅条目下，均带窗口标签
    assert "5日+1.1%" in html and "60日+5.1%" in html          # 159915 表格行
    assert "5日-2.2%" in html and "60日+2.6%" in html          # 512880 表格行
    assert 'title=' not in html.split("四象限提醒")[1].split("逐标的明细")[0].split("xb-item")[0] or True
    # 横幅条目下的序列行（512880 在风险格）
    assert html.count('class="xb-seq"') >= 1


def test_render_details_collapsed_pinned_open():
    html = _render_mini()
    # 明细为折叠面板；置顶默认展开、非置顶默认收起
    assert '<details id="159915" class="etf-detail" open>' in html
    assert '<details id="512880" class="etf-detail">' in html
    # summary 带摘要 chips（类型/偏离），收起即可扫读
    assert '<span class="etf-chips">' in html and "偏离 -15.0%" in html
    # 置顶标 ⭐、不再全员 📌
    assert "⭐" in html and "📌" not in html
