"""Tests for stock_report HTML renderer (Phase 2 交付通道)。
No network — synthetic diagnose dicts only(实盘看板另跑 stock_report.py 验)。"""
import math

import pandas as pd

from stockagent.tracker import stock_report as srep


def _diag():
    return {
        "price_last": 1308.0, "date_last": "2026-07-21", "pe_ttm": 19.8, "pb": 6.04,
        "classification": {"primary": "value", "secondary": []},
        "valuation_zone": {"pe_pct": 0.04, "pb_pct": 0.10, "zone": "低位·便宜", "valid": True},
        "features": {"revenue_cagr": 0.10, "profit_cagr": 0.095, "profit_vol": 0.089,
                     "pe_pct": 0.04, "div_yield": 0.0397, "cagr_years": 3, "vol_years": 5},
        "pitfalls": {"net_profit": {"yoy": -0.045, "abnormal": False, "trustworthy": -0.045, "valid": True},
                     "revenue": {"yoy": -0.01, "valid": True},
                     "disclosure": {"latest_period": "20251231", "deadline": "2026-04-30",
                                    "disclosed_by_asof": True}},
        "forecast": {"valid": True, "latest": {"period": "20241231", "yoy": 15.0, "type": "略增",
                                               "announce_date": "2025-01-03", "sentiment": "bullish"},
                     "a1_deceleration": False, "a2_turn_bearish": False},
        "davis": {"type": "double_play_watch", "label": "双击观察·低PE+业绩探底待回升",
                  "profit_yoy_latest": -0.045, "pe_change": -0.018, "pe_pct": 0.037, "valid": True},
        "price_timing": {"deviation": {"pct": 0.12, "cur_dev": 0.08, "valid": True},
                         "breakout": {"direction": "up", "grade": 1, "label": "突破"},
                         "cross": {"direction": "up", "date": "2026-05-10", "bars_ago": 50},
                         "valid": True},
    }


def test_render_contains_key_sections():
    alerts = [{"level": "warn", "scope": "贵州茅台", "rule": "A3",
               "msg": "营收增速下滑(16%→-1%)→ 业绩前瞻预警"}]
    h = srep.render({"600519": _diag()}, alerts, as_of="2026-07-22",
                    names={"600519": "贵州茅台"})
    assert "<!DOCTYPE html>" in h
    assert "tab-cyclic" in h and "tab-value" in h and "tab-growth" in h  # 三 tab 面板
    assert "switchTab" in h and 'data-tab="cyclic"' in h                  # tab 切换 + 默认周期
    assert "tab-panel active" in h                                        # 默认激活
    assert "贵州茅台" in h
    assert "营收CAGR(3y)" in h and "净利CAGR(3y)" in h   # CAGR 标窗口年数
    assert "利润波动(5y)" in h                            # 波动窗口
    assert "线上+8.0%" in h and "上穿" in h               # E3 诚实展示位置+穿越(非误称突破)
    assert "value" not in h or "#16a34a" in h          # value badge color
    assert "低位·便宜" in h
    assert "双击观察" in h
    assert "营收增速下滑" in h                           # 告警区
    assert "⚠1 💡0" in h                               # 告警计数
    assert h.count("<details") == 1 and "指标说明" in h  # 单一说明,放卡片标题旁


def test_render_no_alerts_message():
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "贵州茅台"})
    # 无告警分支:显示「当前无触发」,不输出计数 span
    assert "当前无触发" in h
    assert "⚠0" not in h


def test_render_alerts_split_by_tab():
    alerts = [
        {"level": "warn", "scope": "贵州茅台", "rule": "A3", "msg": "营收增速下滑"},   # 个股 → value tab
        {"level": "info", "scope": "大盘", "rule": "F1", "msg": "沪深300风险开关"},     # 市场 → 全局条
    ]
    h = srep.render({"600519": _diag()}, alerts, as_of="2026-07-22", names={"600519": "贵州茅台"})
    # 市场级在 tab 栏上方的全局条
    assert "🌐 市场级提醒" in h and "沪深300风险开关" in h
    # 个股级落在 value tab 内,不在 cyclic tab 内
    i_cyc, i_val = h.find('id="tab-cyclic"'), h.find('id="tab-value"')
    cyc_seg = h[i_cyc:i_val]
    assert "营收增速下滑" not in cyc_seg          # 周期 tab 无该个股提醒
    assert "营收增速下滑" in h[i_val:]            # value tab 有


def test_render_handles_nan_fields():
    d = _diag()
    d["features"] = {"revenue_cagr": float("nan"), "profit_cagr": float("nan"),
                     "profit_vol": float("nan"), "pe_pct": float("nan"), "div_yield": float("nan")}
    d["price_last"] = float("nan")
    d["forecast"] = {"valid": False}
    h = srep.render({"600519": d}, [], as_of="2026-07-22", names={"600519": "茅台"})
    assert "—" in h                                     # NaN → 破折号,不崩
    assert "无预告历史" in h


def test_write_html(tmp_path):
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22")
    out = srep.write_html(h, tmp_path / "sub" / "stock.html")
    assert out.exists() and out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


class _StubStore:
    """最小 store —— 全返回空,figure builder 走占位(不抛),验证 render 装配模态。"""
    def get_series(self, sym):
        return pd.DataFrame()

    def get_stock_valuation_series(self, sym, indicator):
        return pd.DataFrame()

    def get_stock_financials_panel(self, sym, metrics=None):
        return pd.DataFrame()

    def get_stock_dividend_series(self, sym):
        return pd.DataFrame()

    def get_commodity_series(self, variety):
        return pd.Series(dtype=float)   # 空 → 商品图走占位


def test_render_with_store_emits_modal():
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22",
                    names={"600519": "贵州茅台"}, store=_StubStore())
    # 模态壳 + 懒渲染 JS
    assert 'id="chart-modal"' in h
    assert "var CHARTS={" in h and "var _NAMES={" in h
    assert "function openChart" in h and "function closeChart" in h
    # 6 个图槽位:价格+偏离 / PE / PB / 业绩 / 利润归因 / 分红
    for i in range(6):
        assert f'<div id="m-chart-{i}"' in h
    # 卡片 📊 按钮触发 openChart(sym)
    assert "openChart('600519')" in h
    # JSON 嵌入(图数据以对象字面量存在,非预渲染 <script>)
    assert '"600519":[' in h
    # 不再有 inline chart-block(改成模态)
    assert 'class="chart-block"' not in h
    assert "Plotly.newPlot" in h


def test_render_without_store_no_charts():
    """store=None → 完全无图表相关产物(向后兼容)。"""
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "贵州茅台"})
    assert "chart-modal" not in h
    assert "var CHARTS" not in h
    assert "openChart" not in h


def test_tab_of_routing():
    cm = {"002466": "碳酸锂"}
    assert srep._tab_of("002466", {"classification": {"primary": "value"}}, cm) == "cyclic"  # commodity_map 优先
    assert srep._tab_of("600519", {"classification": {"primary": "value"}}, {}) == "value"
    assert srep._tab_of("300750", {"classification": {"primary": "growth"}}, {}) == "growth"
    assert srep._tab_of("000001", {"classification": {"primary": None}}, {}) == "growth"      # 未分类→成长
    assert srep._tab_of("600019", {"classification": {"primary": "cyclic"}}, {}) == "cyclic"


def test_render_tab_structure_and_counts():
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "贵州茅台"})
    assert '<div class="tabs">' in h
    assert "💰 价值(1)" in h            # 价值 tab 带 1 只
    assert "🔄 周期(0)" in h            # 周期 tab 0 只(该股非周期)
    # 周期 panel 即使空也存在(为后续周期分析留位);价值 panel 含该股卡片
    assert "贵州茅台" in h


def test_commodity_price_figure():
    import pandas as pd
    from stockagent.tracker import stock_figures as sf
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    s = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)  # 上升→向上
    fig = sf.commodity_price_figure("铜", s)
    title = fig.layout.title.text
    assert "铜" in title and "向上" in title
    # 空/不足 → 占位(不抛)
    assert sf.commodity_price_figure("铜", pd.Series(dtype=float)) is not None


def test_commodity_stock_overlay_figure():
    """股价 vs 商品价 双轴叠加:双线 + 右轴(y2);缺股价 → 退独立商品图;都缺 → 占位。"""
    import pandas as pd
    from stockagent.tracker import stock_figures as sf
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    stock_df = pd.DataFrame({"close": [10.0 + i * 0.01 for i in range(300)]}, index=idx)
    comm = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)  # 上升→向上
    fig = sf.commodity_stock_overlay_figure("600362", "江西铜业", "铜", stock_df, comm)
    names = [t.name for t in fig.data]
    assert "江西铜业股价(左轴)" in names and "铜价(右轴)" in names      # 股价 + 商品价
    assert "股价 MA60" in names and "铜 MA60" in names                 # 两条 MA60(左右轴对称)
    assert len(fig.data) == 4                                          # 4 线
    assert any(getattr(t, "yaxis", "") == "y2" for t in fig.data)     # 双轴(右轴 y2)
    assert "双轴" in fig.layout.title.text and "江西铜业" in fig.layout.title.text
    # 横轴窗口拖拽 + 快捷按钮(切窗口观察商品→股价传导)
    assert fig.layout.xaxis.type == "date"
    assert fig.layout.xaxis.rangeslider.visible is True
    assert [b.label for b in fig.layout.xaxis.rangeselector.buttons] == ["1月", "6月", "1年", "3年", "全部"]
    # 季度分界竖线:2024-01-01 起 300 交易日(约至 2025-02)→ 4 条(4/1、7/1、10/1、次年1/1)
    # 其中 1 月线=年份线略加重(#94a3b8),其余季度线淡灰(#cbd5e1);全部置于曲线下层
    vls = fig.layout.shapes
    assert len(vls) == 4 and all(s.x0 == s.x1 and s.layer == "below" for s in vls)
    assert sum(1 for s in vls if s.line.color == "#94a3b8") == 1        # 恰 1 条年份线
    assert sum(1 for s in vls if s.line.color == "#cbd5e1") == 3        # 3 条季度线
    # 缺股价 → 退独立商品图(标题无"双轴")
    fig2 = sf.commodity_stock_overlay_figure("600362", "江西铜业", "铜", pd.DataFrame(), comm)
    assert "双轴" not in (fig2.layout.title.text or "")
    # 都缺 → 占位(不抛)
    assert sf.commodity_stock_overlay_figure("600362", "江西铜业", "铜",
                                             pd.DataFrame(), pd.Series(dtype=float)) is not None


def test_render_commodity_panel_and_modal_7th():
    # 002466 在 commodity_map → 周期 tab:含商品价时序面板 + 模态第 7 张商品图
    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22", names={"002466": "天齐"}, store=_StubStore())
    assert "comm-chart-0" in h and "商品价时序" in h          # 周期 tab 有商品价面板
    assert h.count('class="modal-chart"') >= 7               # 模态至少 7 槽(周期股追加商品图)
    assert '"002466":' in h and "openChart('002466')" in h
    # 非商品股(600519)只有 6 槽
    h2 = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "茅台"}, store=_StubStore())
    assert h2.count('class="modal-chart"') == 6


def test_commodity_panel_covers_all_varieties():
    """面板品种清单与 fetcher.COMMODITY_CODES 同源(单一数据源);新增 8 种齐备,全 13 种渲染入面板。"""
    from stockagent.data import fetcher
    from stockagent.data.manager import DataManager

    # 单一数据源:manager 列表派生自 fetcher(等长同序)
    assert DataManager.COMMODITY_VARIETIES == list(fetcher.COMMODITY_CODES.keys())
    # 新增 8 种齐备(回归守卫:防止品种被误删)
    assert set(["铝", "锌", "铁矿石", "焦煤", "白银", "玻璃", "纯碱", "生猪"]) <= set(fetcher.COMMODITY_CODES)
    assert len(fetcher.COMMODITY_CODES) == 13

    # store 给每个品种返回真实序列(300 点上升 → 判定"向上")→ 面板应渲染全部品种名
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    series = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)

    class _ComStore(_StubStore):
        def get_commodity_series(self, variety):
            return series

    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22",
                    names={"002466": "天齐"}, store=_ComStore())
    for v in fetcher.COMMODITY_CODES:                # 全 13 个品种名都出现在面板
        assert v in h, f"面板缺品种 {v}"
