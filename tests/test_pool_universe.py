"""候选池 universe 推导(纯函数): ST/退 排除 + n_reports 门 + 代码段过滤 + 行业 join 诚实降级。"""
import pandas as pd

from stockagent.pool.universe import (
    derive_universe,
    industry_coverage,
    is_excluded_name,
    is_supported_code,
    join_industry,
)


def _consensus() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "n_reports": [44, 20, 2, 10, 8],
            "eps_fy1": [68.0, 2.3, 1.0, 5.0, None],
            "eps_fy2": [75.0, 2.5, 1.2, 6.0, None],
            "fy1_year": [2026, 2026, 2026, 2026, 2026],
            "fy2_year": [2027, 2027, 2027, 2027, None],
        },
        index=["600519", "000001", "600547", "830799", "900901"],
    )


def _spot() -> pd.DataFrame:
    return pd.DataFrame(
        {"name": ["贵州茅台", "平安银行", "山东黄金", "ST贝特", "老B股"],
         "close": [1500.0, 48.0, 30.0, 1.2, 0.8]},
        index=["600519", "000001", "600547", "830799", "900901"],
    )


# ---------- 名称/代码过滤 ----------
def test_is_excluded_name():
    assert is_excluded_name("ST贝特")
    assert is_excluded_name("*ST某某")
    assert is_excluded_name("S*ST老三")
    assert is_excluded_name("SST续亏")
    assert is_excluded_name("退市西水")
    assert not is_excluded_name("贵州茅台")
    assert not is_excluded_name("长安汽车")  # 「退」不能中缀误伤:startswith 才排
    assert is_excluded_name(None)
    assert is_excluded_name("")


def test_is_supported_code():
    assert is_supported_code("600519")   # 沪主板
    assert is_supported_code("688981")   # 科创
    assert is_supported_code("000001")   # 深主板
    assert is_supported_code("002460")   # 原中小板(00 段)
    assert is_supported_code("300750")   # 创业板
    assert is_supported_code("301236")   # 创业板注册制(30 段)
    assert not is_supported_code("830799")  # 北交所
    assert not is_supported_code("430047")  # 北交所(4 段)
    assert not is_supported_code("900901")  # B 股
    assert is_supported_code(600519)        # int 容错(zfill)


# ---------- derive_universe ----------
def test_derive_universe_gates():
    u = derive_universe(_consensus(), _spot(), min_reports=3)
    # 600519/000001 通过;600547 n_reports=2 被研报门拦;830799 北交所+ST 双拦;900901 B股拦
    assert list(u.index) == ["600519", "000001"]
    assert u.loc["600519", "name"] == "贵州茅台"
    assert "eps_fy1" in u.columns and "fy2_year" in u.columns


def test_derive_universe_st_filter_beats_consensus():
    cons = _consensus()
    spot = _spot().copy()
    spot.loc["600519", "name"] = "ST茅台"  # 摘帽变戴帽
    u = derive_universe(cons, spot, min_reports=3)
    assert list(u.index) == ["000001"]


def test_derive_universe_empty_spot():
    u = derive_universe(_consensus(), pd.DataFrame(columns=["name"]), min_reports=3)
    assert len(u) == 0


# ---------- join_industry ----------
def _ind_map() -> pd.DataFrame:
    return pd.DataFrame({"industry": ["酿酒行业", "能源金属"]},
                        index=["600519", "002460"])


def test_join_industry_typed_and_unmapped():
    u = derive_universe(_consensus(), _spot(), min_reports=3)
    cfg = {"酿酒行业": {"type": "value"}, "能源金属": {"type": "cyclic", "commodity": "碳酸锂"}}
    j = join_industry(u, _ind_map(), cfg)
    assert j.loc["600519", "type"] == "value"
    assert j.loc["600519", "commodity_variety"] is None or pd.isna(j.loc["600519", "commodity_variety"])
    # 000001 无行业行 → 未映射,type=None(诚实降级)
    assert j.loc["000001", "industry"] == "未映射"
    assert j.loc["000001", "type"] is None


def test_join_industry_known_board_missing_from_cfg():
    u = derive_universe(_consensus(), _spot(), min_reports=3)
    j = join_industry(u, _ind_map(), {})  # yaml 空:全员未映射但 industry 列保留实际板块名
    assert j.loc["600519", "industry"] == "酿酒行业"
    assert j.loc["600519", "type"] is None


def test_industry_coverage_counts():
    u = derive_universe(_consensus(), _spot(), min_reports=3)
    cfg = {"酿酒行业": {"type": "value"}}
    j = join_industry(u, _ind_map(), cfg)
    cov = industry_coverage(j)
    assert cov == {"n": 2, "n_typed": 1, "n_cyclic": 0, "n_growth": 0, "n_value": 1, "pct": 0.5}


def test_industry_coverage_empty():
    assert industry_coverage(pd.DataFrame(columns=["type"]))["pct"] == 0.0
