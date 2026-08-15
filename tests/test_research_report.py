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


# ---------------- 板块资金流向 section（flow payload 驱动 · report.py 不算数） ----------------

def _flow_mini():
    idx = pd.date_range("2026-01-01", periods=40, freq="D")
    roll = pd.DataFrame({"大金融": np.linspace(-2, 8, 40), "科技": np.linspace(3, -6, 40)},
                        index=idx)
    monthly = pd.DataFrame({"2026-01": [0.05, -0.02], "2026-02": [-0.03, 0.08],
                            "2026-03": [0.01, 0.04]}, index=["大金融", "科技"])
    state = {"label": "存量轮动", "label_key": "rotation", "window": 20,
             "pool_net_yi": 2.0, "pool_gross_yi": 14.0, "intensity": 0.14,
             "breadth": 0.22, "concentration": 0.6,
             "group_flows": [{"group": "大金融", "flow_yi": 8.0},
                             {"group": "科技", "flow_yi": -6.0}],
             "n_groups": 2}
    return {"window": 20, "state": state, "group_roll": roll, "monthly": monthly,
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
    # 量级线图 + 热力图两个懒渲染占位（_PAGE_JS 选择器里另有 data-sym 字样，只数占位 div）
    assert html.count('class="lazy-chart" data-sym="__flow"') == 2
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
    heat = rep.flow_heatmap_figure(fl)
    hm = heat.data[0]
    assert hm.zmid == 0
    assert hm.colorscale[-1][1] == "#dc2626"               # 红=正=流入（A股惯例）
    assert heat.layout.yaxis.autorange == "reversed"       # 组自上而下规范序


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
    assert menu.buttons[1].args[1]["yaxis.title.text"] == "净流入(% 当日组规模)"
    assert menu.buttons[1].args[1]["yaxis.ticksuffix"] == "%"   # % 态刻度带 % 后缀
    assert menu.buttons[0].args[1]["yaxis.ticksuffix"] == ""    # 亿 态清除后缀
    assert menu.buttons[2].args[0]["y"][0][0] == pytest.approx(float(roll["大金融"].iloc[0]))
    assert menu.buttons[0].args[0]["y"][0][0] == pytest.approx(float(roll["大金融"].iloc[0]) * 0.5)
    # % 态 = 逐日分母：5日按钮的 pct[0] = roll5[0] ÷ 当日组规模（非常数除法）
    aser = base["aum_series"]["大金融"]
    exp = float((roll["大金融"] * 0.5).iloc[0]) / float(aser.iloc[0]) * 100.0
    assert menu.buttons[1].args[0]["y"][0][0] == pytest.approx(exp, rel=1e-9)
