"""Tests for tracker.classifier — ETF 三类分类 + 行业一致性校验。"""
from stockagent.config import get_config
from stockagent.tracker import classifier as clf


def test_classify_known_styles():
    cfg = get_config()
    assert clf.classify("512800", cfg)[0] == "value"      # 银行
    assert clf.classify("512480", cfg)[0] == "growth"     # 半导体
    assert clf.classify("515220", cfg)[0] == "cyclic"     # 煤炭


def test_classify_secondary():
    cfg = get_config()
    s, alt = clf.classify("512690", cfg)                  # 食品饮料 主价值·次成长
    assert s == "value" and "growth" in alt


def test_classify_unknown_returns_none():
    # benchmark 510300 无 style → (None, [])
    cfg = get_config()
    assert clf.classify("510300", cfg) == (None, [])


def test_classify_all_covers_rotation():
    cfg = get_config()
    all_clf = clf.classify_all(cfg)
    assert set(all_clf.keys()) == set(cfg.rotation_symbols())
    # rotation pool 里每只都应有有效 style
    assert all(v[0] in clf.VALID_STYLES for v in all_clf.values())


def test_consistency_no_warnings():
    # style 标注应和行业典型一致;若有警告 → 标注或行业映射要复核
    cfg = get_config()
    warns = clf.consistency_warnings(cfg)
    assert warns == [], "一致性警告(需复核):\n" + "\n".join(warns)
