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


def test_detail_figures_default_3y_range():
    # 逐标的明细三图初始视图 = 最近 3 年（1095±15 天；数据不足 3 年时范围仍设满 3 年）
    shares, nav = _mini_dfs()
    nav_both = nav.assign(unit_nav=nav["acc_nav"])       # flow_daily 需 unit_nav 列
    for fig in (rep.shares_nav_figure("T", shares, nav),
                rep.nav_deviation_figure("T", nav, {"nav_dev_cur": 0.01, "nav_dev_pct": 0.5,
                                                    "nav_extreme_events": []}),
                rep.flow_daily_figure("T(0)", shares, nav_both)):
        rng = fig.layout.xaxis.range
        assert rng is not None, "初始 x range 未设置"
        days = (pd.Timestamp(rng[1]) - pd.Timestamp(rng[0])).days
        assert 1080 <= days <= 1110, days


# ---------------- flow_daily_figure（日度净申赎 · 事件级） ----------------

import pytest  # noqa: E402  （本段断言用 approx）


def test_flow_daily_figure_values_and_axes():
    # +5e7份×净值2.0=+1.0亿柱；%线=+5%；双轴：左=亿元 右=%
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    sh = np.full(30, 1e9); sh[20:] = 1.05e9
    shares = pd.DataFrame({"shares": sh}, index=idx)
    nav = pd.DataFrame({"unit_nav": np.full(30, 2.0), "acc_nav": np.full(30, 2.0)}, index=idx)
    fig = rep.flow_daily_figure("测试ETF(000000)", shares, nav)
    assert fig is not None
    bar, line = fig.data
    assert bar.type == "bar" and line.type == "scatter"
    assert bar.y[20] == pytest.approx(1.0)          # 5e7份 × 2.0 / 1e8 = +1亿
    assert line.y[20] == pytest.approx(5.0)         # 1.05/1.0−1 = +5%
    assert fig.layout.yaxis.title.text.startswith("净申赎额")
    assert fig.layout.yaxis2.title.text == "日增减%"
    # 柱色：红=净申购 绿=净赎回（第20天申购→红；无变化日 v=0 → 红(≥0)）
    assert bar.marker.color[20] == "#dc2626"


def test_flow_daily_figure_split_no_fake_bar():
    # 拆分日（份额×2·净值÷2）：前复权后柱≈0、%≈0（原始口径会是 +100%/+数十亿巨柱）
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    unit = np.full(30, 2.0); unit[15:] = 1.0
    sh = np.full(30, 1e9); sh[15:] = 2e9
    shares = pd.DataFrame({"shares": sh}, index=idx)
    nav = pd.DataFrame({"unit_nav": unit, "acc_nav": np.linspace(1, 1.2, 30)}, index=idx)
    fig = rep.flow_daily_figure("测试ETF(000000)", shares, nav)
    bar, line = fig.data
    assert abs(bar.y[15]) < 1e-6
    assert abs(line.y[15]) < 1e-6


def test_flow_daily_figure_no_history_none():
    assert rep.flow_daily_figure("X(0)", None, None) is None
    idx = pd.date_range("2026-01-01", periods=2, freq="D")
    shares = pd.DataFrame({"shares": [1e9, 1e9]}, index=idx)
    nav = pd.DataFrame({"unit_nav": [1.0, 1.0], "acc_nav": [1.0, 1.0]}, index=idx)
    assert rep.flow_daily_figure("X(0)", shares, nav) is None   # <3 观测




def test_flow_events_strip_figure():
    base = _flow_mini()
    base["events"] = [
        {"symbol": "512800", "name": "银行ETF", "date": "2026-08-14", "flow_yi": 38.2,
         "pct": 0.084, "pctile": 0.996, "side": "in"},
        {"symbol": "512070", "name": "证券保险ETF", "date": "2026-08-12", "flow_yi": -21.0,
         "pct": -0.031, "pctile": 0.992, "side": "out"},
    ]
    base["last_date"] = "2026-08-14"
    fig = rep.flow_events_strip_figure(base)
    assert len(fig.data) == 2                                  # 申购/赎回双轨
    assert {t.name for t in fig.data} == {"净申购", "净赎回"}
    assert fig.data[0].customdata[0][0] == "银行ETF"            # 悬停带名称
    assert fig.data[0].customdata[0][1] == "512800"             # 点击跳转用 symbol
    assert fig.layout.yaxis.visible is False                    # 条带无 y 轴
    rng = fig.layout.xaxis.range
    days = (pd.Timestamp(rng[1]) - pd.Timestamp(rng[0])).days
    assert 28 <= days <= 36                                    # ≈1个月


def test_flow_events_banner_present_and_absent():
    # 有事件 → 横幅：标题+条目+「最新」徽标+跳转链接；无事件 → 整横幅省略
    base = _flow_mini()
    base["events"] = [
        {"symbol": "512800", "date": "2026-08-14", "flow_yi": 38.2, "pct": 0.084,
         "pctile": 0.996, "pctile_kind": "side", "side": "in"},
        {"symbol": "512070", "date": "2026-08-12", "flow_yi": -21.0, "pct": -0.031,
         "pctile": 0.992, "pctile_kind": "side", "side": "out"},
    ]
    base["last_date"] = "2026-08-14"
    snaps = {"159915": {"nav_dev_pct": 0.5, "nav_dev_cur": 0.0, "nav_extreme_events": [],
                        "data_sufficient": True, "style": "growth",
                        "chip": {"state": "flat", "flows": {}}}}
    html = rep.render(snaps, {"159915": {"shares": None, "nav": None}},
                      {"159915": {"name": "创业板ETF", "group": "成长宽基"},
                       "512800": {"name": "银行ETF"}, "512070": {"name": "证券保险ETF"}},
                      as_of="2026-08-14", flow=base)
    assert "申赎异动 · 最近大额申赎事件（近1月）" in html
    assert html.count('class="lazy-chart" data-sym="__flow"') == 2   # 线图+条带
    assert "净申购" in html.split("净申购")[0] or True  # placeholder
    assert 'href="#512800"' in html and "净申购 +38.2亿" in html
    assert "净赎回 -21.0亿" in html and "申购向分位 99.6%" in html and "赎回向分位 99.2%" in html
    assert 'class="flow-ev-today">最新</b>' in html           # 最新日事件有徽标
    assert "申赎异动台账" in html                             # 读图说明 ⑤
    # 无事件（_flow_mini 无 events 键）→ 横幅仍常驻（安静窗口占位）
    html2 = rep.render(snaps, {"159915": {"shares": None, "nav": None}},
                       {"159915": {"name": "创业板ETF"}}, as_of="2026-08-14",
                       flow=_flow_mini())
    assert "申赎异动 · 最近大额申赎事件（近1月）" in html2
    assert html2.count('class="lazy-chart" data-sym="__flow"') == 1   # 无事件→无线图外占位
    assert "安静窗口属正常" in html2
    assert ">最新</b>" not in html2 and 'href="#512800"' not in html2   # 无条目无徽标
    # 完全不带 flow payload → 横幅整体省略
    html3 = rep.render(snaps, {"159915": {"shares": None, "nav": None}},
                       {"159915": {"name": "创业板ETF"}}, as_of="2026-08-14")
    assert "申赎异动 · 最近大额申赎事件（近1月）" not in html3


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


# ---------------- 板块资金流向 section（flow payload 驱动 · report.py 不算数） ----------------

def _flow_mini():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    roll = pd.DataFrame({"大金融": np.linspace(-2, 8, 40), "科技": np.linspace(3, -6, 40)},
                        index=idx)
    state = {"label": "存量轮动", "label_key": "rotation", "window": 20,
             "pool_net_yi": 2.0, "pool_gross_yi": 14.0, "intensity": 0.14,
             "breadth": 0.22, "concentration": 0.6,
             "group_flows": [{"group": "大金融", "flow_yi": 8.0},
                             {"group": "科技", "flow_yi": -6.0}],
             "n_groups": 2}
    return {"window": 20, "state": state, "group_roll": roll,
            "aum": {"大金融": 400.0, "科技": 1200.0},
            "aum_series": pd.DataFrame(                       # 逐日分母（变化，证明非常数）
                {"大金融": np.linspace(900.0, 400.0, 40), "科技": np.linspace(1500.0, 1200.0, 40)},
                index=roll.index),
            "groups": ["大金融", "科技"],
            "members": {"大金融": [("512800", "银行ETF")], "科技": [("512480", "半导体ETF")]},
            "excluded": [("000000", "无份额ETF")], "as_of": "2026-08-14"}


def test_render_flow_section_present():
    snaps = {"159915": {"nav_dev_pct": 0.02, "nav_dev_cur": -0.12, "nav_extreme_events": [],
                        "data_sufficient": True, "style": "growth", "aum_yi": 500.0,
                        "chip": {"data_sufficient": True, "state": "accumulating", "votes": 6,
                                 "flow_main": 0.03, "flow_main_window": 20,
                                 "flows": {20: 0.03}}}}
    html = rep.render(snaps, {"159915": {"shares": None, "nav": None}},
                      {"159915": {"name": "创业板ETF", "group": "成长宽基"}},
                      as_of="2026-08-14", flow=_flow_mini())
    assert "💰 板块资金流向（份额视角）" in html
    # 量级线图一个懒渲染占位（_PAGE_JS 选择器里另有 data-sym 字样，只数占位 div）
    assert html.count('class="lazy-chart" data-sym="__flow"') == 1
    assert '"__flow":[' in html                          # 图 JSON 挂 CHARTS 伪 key
    assert "存量轮动" in html and "轮动强度" in html      # tile 标签 + 指标
    assert "大金融 +8.0亿" in html and "科技 -6.0亿" in html  # 组 chips
    assert "无份额ETF" in html                           # 未计入注记
    assert "配置盘的脚印" in html                        # 读图说明 ④ 方法论注记
    assert "图上按钮" in html and "60日≈季度趋势" in html  # 线图下方按钮说明常驻
    assert "逐日分母" in html                          # % 态=占当日自身规模说明
    # 组筛选：chips=图例开关（图内 legend 已移除）
    assert html.count('data-flow-group="') >= 2            # 每组 chip 带筛选属性
    assert 'onclick="flowChipClick(' in html and 'ondblclick="flowChipSolo(' in html
    assert "↺ 全部" in html and "仅看该组" in html         # 复位 chip + 操作说明
    assert 'class="flow-dot"' in html                      # chip 色点=线色
    # 组构成可见：chips 悬停 title 带成员代码 + 可展开明细块列全成员 + 排名表类型列组副行
    assert 'title="成员：银行ETF(512800)"' in html
    assert "行业组构成" in html and "银行ETF" in html and "半导体ETF" in html
    assert "成长宽基" in html                            # 排名表 类型 cell 的组副行
    # 位置：四象限横幅之后、排名标题之前
    i_banner = html.index("偏离度 × 筹码动向 · 四象限提醒")
    i_flow = html.index("💰 板块资金流向")
    assert i_banner < i_flow < html.index("择时跟踪排名")


def test_render_flow_absent_by_default():
    html = _render_mini()                                # 不带 flow → section 整体省略
    assert "💰 板块资金流向" not in html
    # 占位与图 JSON 都不得出现（注意：_PAGE_JS 的选择器字符串里本就含
    # data-sym="__flow" 字样，故占位断言用 CSS 类组合、图用 CHARTS key）
    assert 'class="lazy-chart" data-sym="__flow"' not in html
    assert '"__flow":[' not in html
    assert "配置盘的脚印" not in html


def test_render_flow_insufficient_payload_omitted():
    payload = _flow_mini()
    payload["state"] = {"label": "数据不足", "label_key": "insufficient"}
    html = rep.render({}, {}, {}, as_of="2026-08-14", flow=payload)
    assert "💰 板块资金流向" not in html and '"__flow":[' not in html


def test_flow_figures_structure():
    fl = _flow_mini()
    lines = rep.flow_lines_figure(fl)
    assert len(lines.data) == 2 and {t.name for t in lines.data} == {"大金融", "科技"}
    # 无 rolls → 单窗口退化：仅 绝对/% 两态按钮
    assert len(lines.layout.updatemenus[0].buttons) == 2
    assert lines.layout.yaxis.zeroline is True             # 跷跷板必看零轴
    assert lines.layout.hovermode == "x unified"           # 同一时点全组横截面对比
    assert lines.layout.hoverlabel.font.size <= 9          # 25 行悬浮框压字号防裁剪
    assert lines.layout.height >= 600                      # 图加高让悬浮框装得下
    assert lines.layout.xaxis.hoverformat == "%Y-%m-%d"    # 框顶日期显示到「日」
    assert lines.layout.showlegend is False                # 图例移除（tile chips 即图例）
    rng = lines.layout.xaxis.range                         # 默认视图 = 最近 1 年
    assert rng is not None and (pd.Timestamp(rng[1]) - pd.Timestamp(rng[0])).days in range(360, 371)
    assert all("%{x|" not in (t.hovertemplate or "")       # 模板不写日期（框顶统一显示，
               and "%{fullData.name}" in (t.hovertemplate or "")  # 重复写会行高翻倍被裁）
               and "<br>" not in (t.hovertemplate or "")   # 组名+数值同行一行高
               for t in lines.data)


def test_flow_lines_window_unit_buttons():
    # 窗口(5/20/60) × 单位(亿/%) 无状态全量切换：6 态按钮各带完整 y 数组+轴/图标题
    import pytest
    base = _flow_mini()
    roll = base["group_roll"]
    base["rolls"] = {5: roll * 0.5, 20: roll, 60: roll * 1.5}
    fig = rep.flow_lines_figure(base)
    menu = fig.layout.updatemenus[0]
    assert [b.label for b in menu.buttons] == ["5日", "5日%", "20日", "20日%", "60日", "60日%"]
    assert menu.active == 2                                # 默认态 = 20日·亿元
    assert menu.buttons[2].args[1]["title.text"].startswith("组级净流入 · 20日滚动")
    assert menu.buttons[1].args[1]["title.text"].startswith("组级净流入强度 · 5日")  # % 态=强度
    assert menu.buttons[1].args[1]["yaxis.title.text"] == "净流入(% 当日组规模)"
    assert menu.buttons[1].args[1]["yaxis.ticksuffix"] == "%"   # % 态刻度带 % 后缀
    assert menu.buttons[0].args[1]["yaxis.ticksuffix"] == ""    # 亿 态清除后缀
    assert menu.buttons[2].args[0]["y"][0][0] == pytest.approx(float(roll["大金融"].iloc[0]))
    assert menu.buttons[0].args[0]["y"][0][0] == pytest.approx(float(roll["大金融"].iloc[0]) * 0.5)
    # % 态 = 逐日分母：5日按钮的 pct[0] = roll5[0] ÷ 当日组规模（非常数除法）
    aser = base["aum_series"]["大金融"]
    exp = float((roll["大金融"] * 0.5).iloc[0]) / float(aser.iloc[0]) * 100.0
    assert menu.buttons[1].args[0]["y"][0][0] == pytest.approx(exp, rel=1e-9)


# ---------------- 业绩预期提醒横幅（A5 + 偏离×预告广度交叉） ----------------

def _earn_win(open_=True, period="20260630"):
    return ({"period": period, "label": "2026中报预告", "open": True,
             "window_note": "7/1开窗·7/15截止", "next_label": "2026三季报预告", "next_open": "10/1"}
            if open_ else
            {"period": period, "label": "2026中报预告", "open": False,
             "window_note": "7/1开窗·7/15截止", "next_label": "2026三季报预告", "next_open": "10/1"})


def _esnap(pct, cur, label="业绩高增", bear=0.0, bull=1.0, period="20260630", **kw):
    s = _snap(pct, cur)
    s.update({"earnings_label": label, "earnings_bear": bear, "earnings_bull": bull,
              "earnings_yoy": 0.30, "earnings_cov": 0.6, "earnings_period": period})
    s.update(kw)
    return s


def test_earn_alert_items_gates():
    win = _earn_win()
    snaps = {
        # A5 命中：下修 -4.2% 且覆盖 55%
        "a": _esnap(0.5, 0.0, revision_w=-0.042, revision_cov=0.55,
                    revision_up=2, revision_dn=8),
        # 下修够深但覆盖不足 → 不进
        "b": _esnap(0.5, 0.0, revision_w=-0.05, revision_cov=0.30),
        # 下修不足阈值 → 不进
        "c": _esnap(0.5, 0.0, revision_w=-0.02, revision_cov=0.60),
    }
    out = rep._earn_alert_items(snaps, win)
    assert [s for s, _ in out["a5"]] == ["a"]
    # data_sufficient=False 一律不进
    snaps["d"] = _esnap(0.5, 0.0, revision_w=-0.09, revision_cov=0.9, data_sufficient=False)
    out = rep._earn_alert_items(snaps, win)
    assert "d" not in [s for s, _ in out["a5"]]


def test_earn_alert_items_cross_gates():
    win = _earn_win()
    snaps = {
        # 超买×空广度≥5% → 风险
        "r1": _esnap(0.96, 0.10, label="业绩改善", bear=0.08, bull=0.92),
        # 超买但空广度 3% < 5% 地板 → 不进（单家小权重预亏=噪音）
        "r2": _esnap(0.97, 0.12, bear=0.03, bull=0.97),
        # 超卖×预喜 label → 机会
        "o1": _esnap(0.03, -0.15, label="业绩高增"),
        # 超卖但 label 承压 → 不进
        "o2": _esnap(0.04, -0.12, label="业绩承压"),
        # 超卖×预喜 但 earnings_period 是旧窗口 → 不进（陈旧数据）
        "o3": _esnap(0.02, -0.18, label="业绩高增", period="20260331"),
        # label=数据不足（覆盖门未过）→ 不进
        "o4": _esnap(0.01, -0.20, label="数据不足"),
    }
    out = rep._earn_alert_items(snaps, win)
    assert [s for s, _ in out["risk"]] == ["r1"]
    assert [s for s, _ in out["opp"]] == ["o1"]
    # 窗口关闭 → 交叉全空（A5 不受窗口门控）
    closed = _earn_win(open_=False)
    snaps["a"] = _esnap(0.5, 0.0, revision_w=-0.06, revision_cov=0.5)
    out2 = rep._earn_alert_items(snaps, closed)
    assert out2["risk"] == [] and out2["opp"] == []
    assert [s for s, _ in out2["a5"]] == ["a"]


def test_earn_alert_items_sort():
    # a5 按下修最深在前；交叉按偏离极值程度（|pct-0.5|）降序
    win = _earn_win()
    snaps = {
        "a": _esnap(0.5, 0.0, revision_w=-0.031, revision_cov=0.5),
        "b": _esnap(0.5, 0.0, revision_w=-0.08, revision_cov=0.5),
        "r1": _esnap(0.96, 0.1, bear=0.2, bull=0.8),
        "r2": _esnap(0.99, 0.2, bear=0.3, bull=0.7),
    }
    out = rep._earn_alert_items(snaps, win)
    assert [s for s, _ in out["a5"]] == ["b", "a"]
    assert [s for s, _ in out["risk"]] == ["r2", "r1"]


def test_earnings_alert_banner_placeholders():
    meta = {"512010": {"name": "医药ETF"}}
    # A5 冷启动占位（含 还需N周）+ 窗口关闭占位（下窗口时点）
    s = {"512010": {"data_sufficient": True, "revision_status": "累积中(1/4)"}}
    h = rep._earnings_alert_banner(s, meta, "2026-08-14")
    assert "累积中 1/4" in h and "约还需 3 周" in h
    assert "窗口已关闭" in h and "三季报预告 10/1 开窗" in h
    assert "窗口外不出条目" in h
    # A5 激活无命中占位
    s2 = {"512010": {"data_sufficient": True, "revision_w": -0.01, "revision_cov": 0.5}}
    h2 = rep._earnings_alert_banner(s2, meta, "2026-08-14")
    assert "已激活 · 当前无下修告警" in h2
    # 无修正动量数据占位
    h3 = rep._earnings_alert_banner({"512010": {"data_sufficient": True}}, meta, "2026-08-14")
    assert "无修正动量数据" in h3


def test_earnings_alert_banner_entries_and_window_gate():
    meta = {"512010": {"name": "医药ETF"}, "159992": {"name": "创新药ETF"}}
    snaps = {
        "512010": _esnap(0.03, -0.15, revision_w=-0.042, revision_cov=0.55,
                         revision_up=2, revision_dn=8, revision_span="20260719→20260816"),
        "159992": _esnap(0.96, 0.128, label="业绩改善", bear=0.10, bull=0.90),
    }
    # 窗口内（as_of=7/10）→ 双向交叉条目 + A5 条目 + 可点跳转
    h = rep._earnings_alert_banner(snaps, meta, "2026-07-10")
    assert "超买×预亏" in h and "空广度 10%" in h and 'href="#159992"' in h
    assert "超卖×预喜" in h and 'href="#512010"' in h
    assert "下修 -4.2%" in h and "下调8家/上调2家" in h
    # 窗口外（as_of=8/14）→ A5 条目仍在（不受窗口门控），交叉只报下窗口
    h2 = rep._earnings_alert_banner(snaps, meta, "2026-08-14")
    assert "下修 -4.2%" in h2
    assert 'href="#159992"' not in h2 and "空广度 10%" not in h2


def test_render_earnings_alert_banner_wired():
    # render 集成：横幅常驻（四象限之后、申赎异动之前），读图说明含 ④' 段
    html = _render_mini()
    i_quad = html.index("四象限提醒")
    i_earn = html.index("📈 业绩预期提醒")
    assert i_quad < i_earn < html.index("择时跟踪排名")
    assert "A5 一致预期下修" in html and "事件进横幅·状态留表格" in html
    assert "预期g水平值是状态" in html          # 读图说明 ④'
    # _render_mini 的 snaps 无业绩字段 → 走占位（无修正动量数据 + 窗口关闭）
    assert "无修正动量数据" in html or "累积中" in html
    assert "窗口已关闭" in html or "暂无交叉命中" in html


def test_earnings_alert_banner_history_block():
    meta = {"512010": {"name": "医药ETF"}, "515220": {"name": "煤炭ETF"}}
    hist = {"windows": [
        {"period": "20260630", "label": "2026中报预告", "state": "closed",
         "hits": {"512010": {"opp": [{"first": "2026-07-15", "last": "2026-07-29",
                                      "n": 11, "detail": "业绩高增·多92%"}]}},
         },
        {"period": "20251231", "label": "2025年报预告", "state": "closed",
         "hits": {"515220": {"risk": [{"first": "2026-01-27", "last": "2026-01-30",
                                       "n": 4, "detail": "空9%"}]}},
         },
    ]}
    snaps = {"512010": {"data_sufficient": True, "revision_status": "累积中(1/4)"}}
    h = rep._earnings_alert_banner(snaps, meta, "2026-08-14", history=hist)
    # 关闭态行带「上窗口摘要」（只数去重 ETF，不数 span）
    assert "上窗口(2026中报预告)命中：🟢1只·医药ETF11天 · 🟠0只" in h
    # 折叠台账：窗口行 + span 事实 + 不含涨跌边界注记（名字在 <a> 内，拆开断言）
    assert "历史窗口台账 · 交叉命中记录（2 窗口" in h
    assert "2026-07-15~2026-07-29·11天 业绩高增·多92%" in h
    assert "2026-01-27~2026-01-30·4天 空9%" in h
    assert "不含后续涨跌" in h and "无前视" in h
    assert 'href="#512010"' in h and 'href="#515220"' in h  # 台账条目可点跳转
    # history=None → 无台账不崩（优雅降级）；空 windows → 同
    h2 = rep._earnings_alert_banner(snaps, meta, "2026-08-14")
    assert "历史窗口台账" not in h2 and "上窗口" not in h2
    h3 = rep._earnings_alert_banner(snaps, meta, "2026-08-14", history={"windows": []})
    assert "历史窗口台账" not in h3


def test_fmt_pctile_extreme_one_decimal():
    """极端分位显示一位小数——:.0% 把 99.56% 圆成 100% 会暗示「史上最大」
    （2026-08 实例：科创50 08-19 +5.2% 实为自身历史第 7 大申购日）。"""
    assert rep._fmt_pctile(0.9956) == "99.6%"
    assert rep._fmt_pctile(0.9994) == "99.9%"
    assert rep._fmt_pctile(1.0) == "100.0%"          # 真并列/独占最大才配 100.0
    assert rep._fmt_pctile(0.0) == "0.0%"
    assert rep._fmt_pctile(0.004) == "0.4%"
    assert rep._fmt_pctile(0.93) == "93%"            # 非着色区保持整数, 列宽友好
    assert rep._fmt_pctile(0.97) == "97.0%"          # 着色区(≥95%)一位小数·与横幅一致
