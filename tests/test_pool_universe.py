"""universe 纯函数(V8): 全市场 spot 非 ST 宇宙(不再 ∩ consensus)+ 行业 join + 覆盖统计。"""
import pandas as pd

from stockagent.pool import universe as uni


def _spot() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["贵州茅台", "ST某某", "退市某某", "中国平安", "宁德时代", "某北交所"],
        "close": [1500.0, 2.1, 0.8, 48.0, 210.0, 5.0],
        "mktcap": [1.88e12, 4.2e9, 1e9, 8.7e11, 9.2e11, 6e9],
        "float_mktcap": [1.88e12, 4.2e9, 1e9, 8.7e11, 8.8e11, 3e9],
        "pe_dyn": [25.0, 40.0, 12.0, 9.0, 22.0, 30.0],
        "pb": [8.1, 0.9, 0.5, 1.1, 4.5, 2.0],
    }, index=["600519", "000999", "000998", "000001", "300750", "873001"])


# ---------- derive_universe(V8: spot-only) ----------
def test_derive_universe_filters_st_and_segments():
    u = uni.derive_universe(_spot())
    # ST/退 排除 + 北交所(873xxx)排除 → 剩 茅台/平安/宁德
    assert set(u.index) == {"600519", "000001", "300750"}
    # V8 扩列随宇宙带出
    assert abs(float(u.loc["600519", "mktcap"]) - 1.88e12) < 1.0
    assert abs(float(u.loc["300750", "pe_dyn"]) - 22.0) < 1e-9


def test_derive_universe_empty_and_missing_cols():
    empty = uni.derive_universe(pd.DataFrame())
    assert len(empty) == 0
    # 旧格式 spot(无扩列)→ 只带 name/close,安全降级
    old = pd.DataFrame({"name": ["贵州茅台"], "close": [1500.0]}, index=["600519"])
    u = uni.derive_universe(old)
    assert set(u.index) == {"600519"}
    assert list(u.columns) == ["name", "close"]


def test_derive_universe_custom_prefixes():
    u = uni.derive_universe(_spot(), exclude_prefixes=("ST",))
    # 只排 ST;退市/北交所按段处理(退市 000998 属 00 段,保留)
    assert "000998" in u.index
    assert "000999" not in u.index


# ---------- join_industry(V7 沿用,回归保护) ----------
def test_join_industry_types_and_unmapped():
    spot = pd.DataFrame({"name": ["赣锋锂业", "贵州茅台"]}, index=["002460", "600519"])
    imap = pd.DataFrame({"industry": ["能源金属", "白酒Ⅱ"]}, index=["002460", "600519"])
    cfg = {"能源金属": {"type": "cyclic", "commodity": "碳酸锂"},
           "白酒Ⅱ": {"type": "value"}}
    j = uni.join_industry(spot, imap, cfg)
    assert j.loc["002460", "type"] == "cyclic"
    assert j.loc["002460", "commodity_variety"] == "碳酸锂"
    assert j.loc["600519", "type"] == "value"
    assert j.loc["600519", "commodity_variety"] is None
    # 无行业映射 → 未映射 + type=None(PEG 轨,PB 轨降级)
    j2 = uni.join_industry(spot, pd.DataFrame(), cfg)
    assert j2.loc["600519", "industry"] == "未映射"
    assert j2.loc["600519", "type"] is None


def test_industry_coverage_counts():
    spot = pd.DataFrame({"name": ["a", "b", "c"]}, index=["600001", "600002", "600003"])
    imap = pd.DataFrame({"industry": ["铜", "银行", "神秘板块"]},
                        index=["600001", "600002", "600003"])
    cfg = {"铜": {"type": "cyclic", "commodity": "铜"}, "银行": {"type": "value"}}
    cov = uni.industry_coverage(uni.join_industry(spot, imap, cfg))
    assert cov["n"] == 3 and cov["n_typed"] == 2
    assert cov["n_cyclic"] == 1 and cov["n_value"] == 1
    assert abs(cov["pct"] - 2 / 3) < 1e-9
    assert uni.industry_coverage(pd.DataFrame())["n"] == 0
