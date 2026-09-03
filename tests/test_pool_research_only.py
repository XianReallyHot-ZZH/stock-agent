"""Tests for research_only pool rows — 黄金ETF 518880 只上研究看板、不进引擎宇宙。

背景（2026-09）：etf_pool.yaml 是引擎与研究看板共用的唯一池子；rotation_symbols()
原样直读 rotation_pool。518880（T+0 品种、无业绩成分）回归跟踪时加了 research_only
行级标志：rotation_symbols() 过滤（引擎/回测/晨报宇宙不变），tracked_symbols()/
all_symbols() 包含（研究看板 + 数据腿覆盖）。这些断言锁死该边界。
"""
from stockagent.config import get_config
from stockagent.tracker import classifier as clf
from stockagent.research import report as rreport

GOLD = "518880"


def test_rotation_symbols_excludes_research_only():
    """引擎宇宙回归断言：黄金不进 rotation，规模与加金前一致（36）。"""
    cfg = get_config()
    rot = cfg.rotation_symbols()
    assert GOLD not in rot
    assert len(rot) == 36  # 加 518880(research-only) 前后，引擎宇宙规模不变


def test_tracked_and_all_symbols_include_research_only():
    cfg = get_config()
    tracked = cfg.tracked_symbols()
    assert GOLD in tracked
    assert len(tracked) == 37
    assert set(cfg.rotation_symbols()) < set(tracked)  # 真子集
    assert GOLD in cfg.all_symbols()  # 数据腿（价格/份额/净值）覆盖


def test_gold_meta_fields():
    cfg = get_config()
    m = cfg.symbol_meta()[GOLD]
    assert m["group"] == "黄金"          # 单成员组 → 资金流向第 26 组
    assert m["style"] == "cyclic"        # 周期 tab（商品）
    assert m.get("research_only") is True
    assert not m.get("index_code")       # 无业绩成分底座 → ⑤预期列降级（同 159915 等先例）
    assert not m.get("csrc_industry")    # 不触发 classifier 行业一致性警告


def test_gold_classifies_cyclic():
    assert clf.classify(GOLD) == ("cyclic", [])


def test_gold_not_in_classify_all():
    """classify_all 只覆盖引擎宇宙；黄金不进（consistency_warnings 同理）。"""
    cfg = get_config()
    assert GOLD not in clf.classify_all(cfg)


def test_flow_colors_cover_all_groups():
    """26 组色板不取模回卷（否则黄金组与银行组同色）。"""
    cfg = get_config()
    groups = {m.get("group") for m in cfg.symbol_meta().values() if m.get("group")}
    assert len(groups) == 26
    assert len(rreport._FLOW_COLORS) >= len(groups)
    assert len(set(rreport._FLOW_COLORS)) == len(rreport._FLOW_COLORS)  # 无重复色
