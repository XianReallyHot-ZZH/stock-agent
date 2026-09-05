"""Tests for stock_report HTML renderer (Phase 2 交付通道)。
No network — synthetic diagnose dicts only(实盘看板另跑 stock_report.py 验)。"""
import math
import re

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


def test_commodity_chart_enlarge_modal():
    """点击小图 → comm-modal 放大视图:客户端复用 COMM 数据改样式,放大后带 rangeslider+快捷窗。
    守卫两处易碎点:①放大容器不得带 class="modal-chart"(否则被个股模态 purge/隐藏成零尺寸);
    ②COMM 数据只传一份(放大=改样式不重复传图,JSON 里数据数组不翻倍)。"""
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    series = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)

    class _ComStore(_StubStore):
        def get_commodity_series(self, variety):
            return series

    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22",
                    names={"002466": "天齐"}, store=_ComStore())
    # 小图点击可放大 + 放大模态存在 + 交互函数齐备
    assert 'onclick="openCommChart(0)"' in h
    assert 'id="comm-modal"' in h and "closeCommChart" in h
    # 放大视图的横轴伸缩:rangeslider 拖拽 + 五档快捷窗(与个股叠加图 _RANGE_BUTTONS 同档)
    assert "rangeslider" in h and "rangeselector" in h
    assert "'3年'" in h and "'全部'" in h
    # 放大容器不带 modal-chart 类(个股模态 openChart 会对全部 .modal-chart purge/隐藏)
    m = re.search(r'<div id="comm-modal-chart"[^>]*>', h)
    assert m and 'class=' not in m.group(0)
    # 数据只传一份:COMM 数组 1 个,放大不复制(无 COMM2/大图数组)
    assert h.count("var COMM=") == 1 and "COMM2" not in h
    # 关闭后 purge 释放 + Esc/遮罩关闭绑定
    assert "Plotly.purge(document.getElementById('comm-modal-chart'))" in h
    assert "e.target===ov" in h
    # 放大视图下行=偏离度子图:前端从价格/MA60 两条 trace 派生(y2 轴),排名数字服务端随 COMM_DEV 下发
    assert "COMM_DEV=" in h and "yaxis2" in h and "偏离度" in h
    assert "第' + D.rank_high + '高 / 第' + D.rank_low + '低" in h
    # 历史极值 Top-K 在发生位置打排名标注(第几高/第几低;极值日==当前日跳过,现在点已含排名语义)
    assert '"highs"' in h and '"lows"' in h
    assert "'第' + e.r + (hi ? '高 +' : '低 ')" in h
    assert "triangle-up" in h and "triangle-down" in h
    assert "lastDate.indexOf(e.d) === 0" in h
    # 双行共享横轴结构守卫:底行 x2 是主控(挂滑块+快捷窗),顶行 x matches 跟随。
    # 两个已知坑:①单 x 轴锚顶行 y 底会把下行带让给轴标签+滑块,偏离度被盖住;
    # ②matches 放在挂 rangeselector 的轴上时快捷窗按钮不渲染(plotly 行为)
    assert "xaxis: 'x2'" in h and "xa.matches = 'x2'" in h and "anchor: 'y2'" in h
    assert "rangeselector: {x: 0" in h and "'3年'" in h and "'全部'" in h
    # COMM 数值必须是普通 JSON 数组(to_json 的 {dtype,bdata} base64 块 Plotly 能画但 JS 读不到逐点值,
    # 偏离度派生会得到全 null → 整行不渲染;回归守卫见 2026-09 修复)
    i0, i1 = h.index("var COMM=["), h.index("var COMM_NAMES=")
    assert '"bdata"' not in h[i0:i1]
    assert '"y": [100.0' in h[i0:i1]   # 首点 100+0*0.5(上升序列 stub,价格 trace 展开为可读数组)
    # store=None:整面板(含放大模态)静默省略(向后兼容无图表产物路径)
    h2 = srep.render({"600519": _diag()}, [], as_of="2026-07-22", names={"600519": "茅台"})
    assert "var COMM=" not in h2 and "id=\"comm-modal\"" not in h2


def test_commodity_dev_stats_rank():
    """偏离度统计:排名=全历史序数(1=最极端);MA 口径与 commodity_price_figure 的 MA60 同源;
    highs/lows=历史 Top-K 极值点(放大视图打「第几高/第几低」标注),并列值按时间先后稳定排序。"""
    from stockagent.tracker import stock_figures as sfig

    idx = pd.date_range("2024-01-01", periods=101, freq="B").strftime("%Y-%m-%d")
    # 末端尖峰:当前偏离=历史最偏高 → 第1高/最后1低(尖峰日自身进 MA60 分母:MA=(59×100+140)/60)
    up = pd.Series([100.0] * 100 + [140.0], index=idx)
    st = sfig.commodity_dev_stats(up)
    assert st["rank_high"] == 1 and st["rank_low"] == st["n"]
    expect = 140 / ((59 * 100 + 140) / 60) - 1
    assert abs(st["cur"] - expect) < 1e-9
    assert st["max"] == st["highs"][0]["v"] and abs(st["highs"][0]["v"] - expect) < 1e-9
    assert st["min"] == 0.0 and st["lows"][0]["v"] == 0.0
    # 分位(与 deviation_extremes 同口径):101 根里有效偏离 42 个,尖峰前全 0 → pct=41/42
    assert abs(st["pct"] - 41 / 42) < 1e-9
    # Top-10:第1高=尖峰日;并列 0 值按时间先后稳定排序(idx[59] 是首个有效偏离日)
    assert st["highs"][0]["d"] == idx[-1] and st["highs"][0]["r"] == 1
    assert st["highs"][1] == {"r": 2, "v": 0.0, "d": idx[59]}
    assert [e["d"] for e in st["highs"][1:]] == [idx[59 + k] for k in range(9)]
    assert [e["d"] for e in st["lows"]] == [idx[59 + k] for k in range(10)]
    assert [e["r"] for e in st["highs"]] == list(range(1, 11))
    assert [e["r"] for e in st["lows"]] == list(range(1, 11))
    # 快腿(2026-09 提速改版):20日动量 + 60日新高/新低(价格口径)
    assert abs(st["momentum20"] - 0.4) < 1e-9 and st["new_high60"] is True and st["new_low60"] is False
    # 末端跳水:第1低/最后1高
    dn = pd.Series([100.0] * 100 + [60.0], index=idx)
    st2 = sfig.commodity_dev_stats(dn)
    assert st2["rank_low"] == 1 and st2["rank_high"] == st2["n"]
    assert st2["lows"][0]["d"] == idx[-1] and st2["highs"][0]["d"] == idx[59]
    assert st2["pct"] == 0.0                                   # 跳水日=历史最偏低(分位 0)
    assert abs(st2["momentum20"] + 0.4) < 1e-9 and st2["new_low60"] is True and st2["new_high60"] is False
    # 数据不足(偏离值 <20 期)→ {}(诚实缺省,前端不加偏离度行)
    assert sfig.commodity_dev_stats(pd.Series([100.0] * 50)) == {}
    assert sfig.commodity_dev_stats(None) == {}


def test_commodity_extreme_banner():
    """🚦 商品异动雷达(周期 tab 顶部,双段语义):⚠异动提醒=快腿(动量/突破,研究排队·非买入信号)、
    ⛔极端警戒=慢腿(分位≥95% 超买/≤5% 超卖);同品种可双段出现;空数据走常态占位;store=None 省略。"""
    from stockagent.data import fetcher

    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    # 末端尖峰:当前偏离≈历史最偏高(分位≈99.6%≥95%)→ 全部品种判超买(红)
    series = pd.Series([100.0] * 299 + [140.0], index=idx, dtype=float)

    class _ComStore(_StubStore):
        def get_commodity_series(self, variety):
            return series

    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22", names={"002466": "天齐"}, store=_ComStore())
    assert "商品异动雷达" in h
    # 双通道:尖峰序列 → 分位≈99.6% 超买 + 20日动量+40% ≥10% 动量 + 60日新高突破,全部 17 品种触发
    assert "动量17 · 突破17 | 超买17 · 超卖0" in h
    # 双段语义分离:同品种两段都出现(既在动又在伸展)
    assert "异动提醒" in h and "极端警戒" in h
    assert "dev-chip mv" in h and "dev-chip ob" in h
    assert "20日+40% · 动量" in h and "60日新高" in h
    assert "排进研究队列" in h and "追高风险" in h
    assert "观察非信号" in h                      # 温度计非开关口径注记
    # 空序列:横幅常驻占位(常态区间)
    h2 = srep.render({"002466": _diag()}, [], as_of="2026-07-22", names={"002466": "天齐"}, store=_StubStore())
    assert "商品异动雷达" in h2 and "常态区间" in h2
    # store=None:横幅省略(向后兼容无图表路径)
    h3 = srep.render({"002466": _diag()}, [], as_of="2026-07-22", names={"002466": "天齐"})
    assert "商品异动雷达" not in h3


def test_commodity_panel_covers_all_varieties():
    """面板品种清单与 fetcher.COMMODITY_CODES 同源(单一数据源);2026-09 扩至 17 种(化肥/农业/气),全 17 种渲染入面板。"""
    from stockagent.data import fetcher
    from stockagent.data.manager import DataManager

    # 单一数据源:manager 列表派生自 fetcher(等长同序)
    assert DataManager.COMMODITY_VARIETIES == list(fetcher.COMMODITY_CODES.keys())
    # 品种齐备(回归守卫:防止品种被误删;含 2026-09 新增 LPG/尿素/豆粕/玉米)
    assert set(["铝", "锌", "铁矿石", "焦煤", "白银", "玻璃", "纯碱", "生猪",
                "LPG", "尿素", "豆粕", "玉米"]) <= set(fetcher.COMMODITY_CODES)
    assert len(fetcher.COMMODITY_CODES) == 17

    # store 给每个品种返回真实序列(300 点上升 → 判定"向上")→ 面板应渲染全部品种名
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    series = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)

    class _ComStore(_StubStore):
        def get_commodity_series(self, variety):
            return series

    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22",
                    names={"002466": "天齐"}, store=_ComStore())
    for v in fetcher.COMMODITY_CODES:                # 全 17 个品种名都出现在面板
        assert v in h, f"面板缺品种 {v}"
    # 偏离分位列:表头 + 极端着色(线性上升序列末端偏离=历史最低 → 绿≤5%)
    assert "偏离分位" in h and "color:#15803d" in h


def test_ambush_names_open_stock_modal():
    """🎯 埋伏表股票名可点 → openChart(sym) 打开个股时序图模态(与卡片 📊 同款交互)。"""
    h = srep.render({"002466": _diag()}, [], as_of="2026-07-22", names={"002466": "天齐"}, store=_StubStore())
    # 埋伏表名字锚点接线到个股模态
    m = re.search(r"<a href='javascript:void\(0\)' class='amb-link'[^>]*onclick=\"openChart\('002466'\)\"", h)
    assert m, "埋伏表股票名未接线 openChart"
    # 悬停提示 + 主题变量配色(深浅两态可用)
    assert "查看时序图" in h and ".amb-link:hover" in h
