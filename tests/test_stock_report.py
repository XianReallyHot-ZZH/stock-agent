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
                     "pe_pct": 0.04, "div_yield": 0.0397},
        "pitfalls": {"net_profit": {"yoy": -0.045, "abnormal": False, "trustworthy": -0.045, "valid": True},
                     "revenue": {"yoy": -0.01, "valid": True},
                     "disclosure": {"latest_period": "20251231", "deadline": "2026-04-30",
                                    "disclosed_by_asof": True}},
        "forecast": {"valid": True, "latest": {"period": "20241231", "yoy": 15.0, "type": "略增",
                                               "announce_date": "2025-01-03", "sentiment": "bullish"},
                     "a1_deceleration": False, "a2_turn_bearish": False},
        "davis": {"type": "double_play_watch", "label": "双击观察·低PE+业绩探底待回升",
                  "profit_yoy_latest": -0.045, "pe_change": -0.018, "pe_pct": 0.037, "valid": True},
        "price_timing": {"deviation": {"pct": 0.12, "valid": True},
                         "breakout": {"direction": "up", "grade": 1, "label": "突破"}, "valid": True},
    }


def test_render_contains_key_sections():
    alerts = [{"level": "warn", "scope": "贵州茅台", "rule": "A3",
               "msg": "营收增速下滑(16%→-1%)→ 业绩前瞻预警"}]
    h = srep.render({"600519": _diag()}, alerts, as_of="2026-07-22",
                    names={"600519": "贵州茅台"})
    assert "<!DOCTYPE html>" in h
    assert "个股诊断卡片" in h
    assert "贵州茅台" in h
    assert "value" not in h or "#16a34a" in h          # value badge color
    assert "低位·便宜" in h
    assert "双击观察" in h
    assert "营收增速下滑" in h                           # 告警区
    assert "⚠1 💡0" in h                               # 告警计数


def test_render_no_alerts_message():
    h = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "贵州茅台"})
    # 无告警分支:显示「当前无触发」,不输出计数 span
    assert "当前无触发" in h
    assert "⚠0" not in h


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
