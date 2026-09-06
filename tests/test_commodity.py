"""Tests for 大宗商品看板(第八看板 · commodity 模块)——纯函数 + 渲染装配,无网络。

覆盖:judge_commodity 统一口径 parity / commodity_radar 分类器 / 面板主语逻辑(国际 vs 国内) /
总览自算广度与合成 / 比价 inner-join / commodity_index 表 roundtrip / render 五 section 装配 /
个股看板速览行(拆大留小的「留小」侧)。
"""
import math

import pandas as pd
import pytest

from stockagent.commodity import overview as ovw
from stockagent.commodity import panel as pnl
from stockagent.commodity import ratios as rt
from stockagent.commodity import render as crep
from stockagent.data import Store
from stockagent.data import fetcher
from stockagent.tracker import stock_figures as sf
from stockagent.tracker.stock_figures import judge_commodity


# ---------------------------------------------------------------- 判定统一 parity
def _legacy_panel_judge(yoy, rec):
    """旧 stock_report._commodity_region 内联实现(2026-09 收口前的行为快照)。"""
    if yoy > 0.10 and rec > -0.05:
        return "向上"
    elif yoy > 0.10:
        return "背离"
    elif yoy > -0.10:
        return "震荡"
    return "向下"


def test_judge_commodity_parity_grid():
    """统一判定与三处历史实现在全网格上一致(nan 语义:nan 同比→向下;nan 近期+高同比→背离)。"""
    yoys = [float("nan"), -0.5, -0.11, -0.1000001, -0.10, -0.05, 0.0, 0.05, 0.0999,
            0.10, 0.100001, 0.11, 0.5]
    recs = [float("nan"), -0.5, -0.06, -0.050001, -0.05, -0.049, 0.0, 0.3]
    for y in yoys:
        for r in recs:
            got = judge_commodity(y, r)
            want = _legacy_panel_judge(y, r)
            assert got == want, (y, r, got, want)


def test_judge_commodity_spot_checks():
    assert judge_commodity(0.5, 0.1) == "向上"
    assert judge_commodity(0.5, -0.06) == "背离"          # 高同比但近期回落
    assert judge_commodity(0.5, float("nan")) == "背离"   # nan 近期 → 与旧口径一致
    assert judge_commodity(0.0, 0.0) == "震荡"
    assert judge_commodity(-0.5, -0.1) == "向下"
    assert judge_commodity(float("nan"), 0.1) == "向下"   # nan 同比 → 向下(面板/图历史行为)
    assert judge_commodity(None, None) == "向下"
    # leading.commodity_alignment 健康度映射收口后不漂移
    from stockagent.tracker.leading import commodity_alignment
    for yoy, rec, want in [(0.5, 0.1, 1.0), (0.5, -0.06, 0.3), (0.0, 0.0, 0.1), (-0.5, -0.1, 0.0)]:
        out = commodity_alignment({"valid": True, "yoy": yoy, "recent": rec}, 0.0)
        assert out["health"] == want, (yoy, rec)


# ---------------------------------------------------------------- 雷达分类器
def _spike_stats():
    """末端尖峰序列 → 分位≈99.6% 超买 + 动量 +40% + 60日新高(全部品种触发)。"""
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    s = pd.Series([100.0] * 299 + [140.0], index=idx, dtype=float)
    return sf.commodity_dev_stats(s)


def test_commodity_radar_classifier():
    st = _spike_stats()
    fast, slow, n_ok = sf.commodity_radar([("铜", st), ("铝", {})], th=0.95, mo_th=0.10)
    assert n_ok == 1                                   # 空 stats 不计入
    assert len(fast) == 1 and fast[0][0] == "铜"
    assert any("动量" in t for t in fast[0][2]) and any("新高" in t for t in fast[0][2])
    assert fast[0][3] is True                          # 向上方向
    assert len(slow) == 1 and any("超买" in t for t in slow[0][2])
    # 常态:中位分位 + 无动量 + 无新高新低 → 两段全空(伪噪声序列,末端非窗口极值)
    idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
    s = pd.Series([100.0 + ((i * 37) % 11) * 0.3 for i in range(300)], index=idx, dtype=float)
    st2 = sf.commodity_dev_stats(s)
    fast2, slow2, _ = sf.commodity_radar([("铜", st2)])
    assert not fast2 and not slow2, (fast2, slow2)
    # 阈值自定义透传:动量阈提到 0.99 → 快腿只剩「60日新高」(新高/新低腿不受 mo_th 门控,by design)
    fast3, slow3, _ = sf.commodity_radar([("铜", st)], th=0.999, mo_th=0.99)
    assert not slow3 and [t for f in fast3 for t in f[2]] == ["60日新高"]


# ---------------------------------------------------------------- 面板主语逻辑
class _StubStore:
    """最小 store:国内序列全品种同一条;国际序列只给 CAD(LME铜)。"""

    def __init__(self, intl_symbols=frozenset({"CAD"})):
        self.intl_symbols = set(intl_symbols)
        idx = pd.date_range("2024-01-01", periods=300, freq="B").strftime("%Y-%m-%d")
        self.dom = pd.Series([100.0 + i * 0.5 for i in range(300)], index=idx, dtype=float)
        # 国际序列:斜率更陡 → 主语判定与国际/国内 spread60 可区分
        self.intl = pd.Series([200.0 + i * 0.8 for i in range(300)], index=idx, dtype=float)

    def get_commodity_series(self, variety, start=None, end=None):
        return self.dom

    def get_commodity_spot(self):
        return pd.DataFrame({"variety": ["铜"], "date": [pd.Timestamp.now().strftime("%Y-%m-%d")],
                             "price": [103.0], "quote_time": ["090000"]})

    def get_western_series(self, source, symbol, start=None, end=None):
        if source == "fut" and symbol in self.intl_symbols:
            return pd.DataFrame({"close": self.intl.values}, index=self.intl.index)
        return pd.DataFrame()

    def get_commodity_index_series(self, index_name):
        if "期货指数" in index_name:
            return self.dom
        return pd.Series(dtype=float)

    def get_meta(self, key, default=None):
        return default


def test_panel_rows_intl_primary():
    """有国际基准且外盘序列在库 → 主语=国际价(判定/同比/偏离分位按主语),国内列对照+spread60;
    其余品种主语=国内价、has_bench 但缺外盘数据时诚实回退国内。"""
    rows = pnl.panel_rows(_StubStore())
    assert len(rows) == len(fetcher.COMMODITY_CODES)
    by_v = {r["variety"]: r for r in rows}
    cu = by_v["铜"]
    assert cu["has_bench"] and cu["intl"]              # CAD 在库 → 国际主语
    assert cu["primary"] is cu["intl"]
    assert cu["judge"] == "向上"                        # 国际序列斜率 0.8/日 → 高同比
    assert cu["dom"]["cur"] == pytest.approx(100.0 + 299 * 0.5)
    intl_m60 = (200.0 + 299 * 0.8) / (200.0 + 239 * 0.8) - 1.0          # 国际近60日(收益率口径)
    dom_m60 = (100.0 + 299 * 0.5) / (100.0 + 239 * 0.5) - 1.0           # 国内近60日
    assert cu["spread60"] == pytest.approx(intl_m60 - dom_m60)
    assert cu["overnight"] == pytest.approx(103.0 / (100.0 + 299 * 0.5) - 1.0)
    # 铝:有基准但外盘不在库(Stub 只给 CAD) → 回退国内主语,spread None
    al = by_v["铝"]
    assert al["has_bench"] and not al["intl"]
    assert al["primary"] is al["dom"] and al["spread60"] is None
    # 无基准品种(螺纹钢) → 国内定价
    assert not by_v["螺纹钢"]["has_bench"] and by_v["螺纹钢"]["primary"] is by_v["螺纹钢"]["dom"]


def test_primary_series_matches_panel():
    st = _StubStore()
    s, disp, unit = pnl.primary_series(st, "铜")
    assert disp == "铜·LME铜" and unit == "美元/吨"
    assert float(s.iloc[-1]) == pytest.approx(200.0 + 299 * 0.8)
    s2, disp2, _ = pnl.primary_series(st, "螺纹钢")
    assert disp2 == "螺纹钢" and float(s2.iloc[-1]) == pytest.approx(100.0 + 299 * 0.5)


# ---------------------------------------------------------------- 总览(自算)
def test_breadth_series_math():
    idx = pd.date_range("2024-01-01", periods=30, freq="B").strftime("%Y-%m-%d")
    up = pd.Series([100.0 + i for i in range(30)], index=idx)         # 全程涨
    dn = pd.Series([100.0 - i for i in range(30)], index=idx)         # 全程跌
    b = ovw.breadth_series({"up": up, "dn": dn}, window=5)
    assert len(b) > 0
    assert (b["up_frac"] == 0.5).all()                                # 一半上涨
    eq_expect = ((129.0 / 124.0 - 1.0) + (71.0 / 76.0 - 1.0)) / 2.0    # 等权=两腿均值
    assert b["eq_ret"].iloc[-1] == pytest.approx(eq_expect)
    # 日历不齐:并集自动跳缺
    dn2 = dn.iloc[:-3]
    b2 = ovw.breadth_series({"up": up, "dn": dn2}, window=5)
    assert b2["up_frac"].iloc[-3] == 1.0                              # 末日只剩 up 有数据
    assert ovw.breadth_series({}, 20).empty


def test_synthetic_index_and_snapshot():
    idx = pd.date_range("2024-01-01", periods=10, freq="B").strftime("%Y-%m-%d")
    s = pd.Series([100.0 * (1.01 ** i) for i in range(10)], index=idx)
    syn = ovw.synthetic_index({"a": s})
    assert len(syn) == 9                                              # pct_change 丢首日,几何级数日收益恒 1%
    assert syn.iloc[0] == pytest.approx(101.0, rel=1e-9)              # 首个收益日起步 100×1.01
    assert syn.iloc[-1] == pytest.approx(1.01 ** 9 * 100, rel=1e-6)
    snap = ovw.index_snapshot(s)
    assert snap["last"] == pytest.approx(100.0 * 1.01 ** 9)
    assert snap["n"] == 10
    assert ovw.index_snapshot(pd.Series(dtype=float)) == {}


# ---------------------------------------------------------------- 比价
def test_ratio_series_inner_join_and_rows():
    idx = pd.date_range("2024-01-01", periods=50, freq="B").strftime("%Y-%m-%d")
    num = pd.Series([10.0] * 50, index=idx)
    den = pd.Series([2.0] * 50, index=idx[:50])
    den.iloc[-10:] = 4.0                                              # 末段分母翻倍 → 比价减半
    r = rt.ratio_series(num, den)
    assert len(r) == 50 and r.iloc[0] == pytest.approx(5.0) and r.iloc[-1] == pytest.approx(2.5)
    assert rt.ratio_series(None, den).empty and rt.ratio_series(num, None).empty
    # 日历交集
    den2 = pd.Series([2.0] * 40, index=idx[:40])
    assert len(rt.ratio_series(num, den2)) == 40


def test_ratio_rows_resolve_intl_and_dom():
    st = _StubStore()
    specs = [
        {"name": "金银比", "num": "SI", "den": "GC", "note": "x"},      # 两者都不在 Stub → 跳过
        {"name": "铜铝比", "num": "CAD", "den": "AHD", "note": "x"},    # AHD 不在 → 跳过
        {"name": "螺矿比", "num": "国内:螺纹钢", "den": "国内:铁矿石", "note": "x"},  # 国内双腿 → 出行
    ]
    rows = rt.ratio_rows(st, specs=specs)
    assert [r["name"] for r in rows] == ["螺矿比"]
    assert rows[0]["cur"] == pytest.approx(1.0)                        # 同一条 stub 序列相除
    assert rows[0]["pct"] == pytest.approx(0.0)                        # 恒定比值 → 分位 0(无小于它的)


# ---------------------------------------------------------------- store roundtrip
def test_commodity_index_roundtrip(tmp_path):
    st = Store(tmp_path / "t.sqlite")
    rows = [("中证商品期货指数", "2026-09-04", 2354.94, 0.61),
            ("中证商品期货指数", "2026-09-03", 2341.81, 0.54)]
    assert st.upsert_commodity_index(rows, source="ccidx") == 2
    assert st.upsert_commodity_index(rows, source="ccidx") == 2        # 幂等
    s = st.get_commodity_index_series("中证商品期货指数")
    assert list(s.index) == ["2026-09-03", "2026-09-04"]
    assert s.iloc[-1] == pytest.approx(2354.94)
    assert st.get_commodity_index_series("不存在").empty


# ---------------------------------------------------------------- 渲染装配
def test_render_full_page_sections():
    h = crep.render(_StubStore(), as_of="2026-09-06")
    assert h.startswith("<!DOCTYPE html>")
    for kw in ["商品异动雷达", "商品环境总览", "品种面板", "品种时序", "比价与内外盘对照",
               "读图说明", "中证商品期货指数", "LME铜", "国内定价", "commodity-dark"]:
        assert kw in h, f"缺 {kw}"
    # 主语徽标:铜=国际(LME铜),螺纹钢=国内定价
    assert h.count('class="subj intl"') == 1 and h.count('class="subj dom"') == 16
    # 放大模态 + 可读 JSON(无 bdata 二进制块)
    assert 'id="comm-modal"' in h and "openCommChart" in h
    i0, i1 = h.index("var COMM=["), h.index("var COMM_NAMES=")
    assert '"bdata"' not in h[i0:i1]
    # 总览图 inline 渲染
    assert "var OVW=[" in h and 'id="ovw-fig-idx"' in h
    # 阈值/温度计口径注记
    assert "温度计非开关" in h


def test_render_meta_conclusion_and_quiet(tmp_path):
    """meta 实证结论活注入;雷达常态走占位(Stub 序列陡增 → 实际触发,这里只验注入路径不崩)。"""
    class _MetaStore(_StubStore):
        def get_meta(self, key, default=None):
            return "快腿追买跑输基线(2026-09-05 首跑)" if key == "commodity_speed_conclusion" else default

    h = crep.render(_MetaStore(), as_of="2026-09-06")
    assert "快腿追买跑输基线" in h


# ---------------------------------------------------------------- 个股看板速览行(拆大留小)
def test_stock_report_summary_row():
    """个股看板周期 tab:商品内容全迁后只留 🧭速览行(异常 chips+广度+跳转),无面板/时序/模态。"""
    from stockagent.tracker import stock_report as srep
    # 最小诊断 stub(test_stock_report._diag 的本地副本,避免跨文件 import)
    diag = {
        "price_last": 10.0, "date_last": "2026-09-04", "pe_ttm": 8.0, "pb": 1.0,
        "classification": {"primary": "cyclic", "secondary": []},
        "valuation_zone": {"pe_pct": 0.1, "pb_pct": 0.1, "zone": "低位·便宜", "valid": True},
        "features": {"revenue_cagr": 0.1, "profit_cagr": 0.1, "profit_vol": 0.5,
                     "pe_pct": 0.1, "div_yield": 0.02, "cagr_years": 3, "vol_years": 5},
        "pitfalls": {"net_profit": {"yoy": 0.1, "abnormal": False, "trustworthy": 0.1, "valid": True},
                     "revenue": {"yoy": 0.1, "valid": True},
                     "disclosure": {"latest_period": "20251231", "deadline": "2026-04-30",
                                    "disclosed_by_asof": True}},
        "forecast": {"valid": False},
        "davis": {"type": "neutral", "label": "中性", "profit_yoy_latest": 0.1,
                  "pe_change": 0.0, "pe_pct": 0.1, "valid": True},
        "price_timing": {"deviation": {"pct": 0.1, "cur_dev": 0.05, "valid": True},
                         "breakout": {"direction": "up", "grade": 1, "label": "突破"},
                         "cross": {"direction": "up", "date": "2026-05-10", "bars_ago": 50},
                         "valid": True},
        "earnings_quality": {"valid": False},
    }

    class _ChartStore(_StubStore):
        def get_series(self, sym):
            return pd.DataFrame()

        def get_stock_valuation_series(self, sym, indicator):
            return pd.DataFrame()

        def get_stock_financials_panel(self, sym, metrics=None):
            return pd.DataFrame()

        def get_stock_dividend_series(self, sym):
            return pd.DataFrame()

    h = srep.render({"002466": diag}, [], as_of="2026-09-06",
                    names={"002466": "天齐"}, store=_ChartStore())
    # 速览行在,旧三 section 与商品模态全不在
    assert "商品环境速览" in h and "commodity.html" in h
    for gone in ["商品 A 类面板", "商品价时序", "商品异动雷达", 'id="comm-modal"', "var COMM="]:
        assert gone not in h, f"旧商品 section 残留: {gone}"
    # 埋伏表仍在(拆大留小的「留」侧)
    assert "提前埋伏候选" in h
    # Stub 国内序列陡增 → 速览行应出现异常 chips(dev-chip)与广度统计
    assert "dev-chip" in h and "20日上涨" in h
