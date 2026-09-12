"""sector 纯函数(V8): 行业构成统计 + 商品关联周期涌现簇信号(50/50 对照已删——用户裁定)。"""
from stockagent.pool import sector as sec


def _rows():
    out = []
    for i in range(6):   # 6 只商品关联周期
        out.append({"industry": "能源金属" if i < 3 else "工业金属",
                    "type": "cyclic", "commodity_variety": "碳酸锂" if i < 3 else "铜"})
    for i in range(4):   # 4 只其他
        out.append({"industry": "白酒Ⅱ", "type": "value"})
    return out


def test_composition_counts_and_shares():
    comp = sec.composition(_rows())
    assert comp["n_total"] == 10
    by = {d["industry"]: d for d in comp["by_industry"]}
    assert by["能源金属"]["n"] == 3 and abs(by["能源金属"]["share"] - 0.3) < 1e-9
    assert comp["by_industry"][0]["n"] == 4   # 排序:只数降序(白酒4 > 金属各3)
    assert comp["by_type"]["cyclic"] == 6 and comp["by_type"]["value"] == 4


def test_composition_empty():
    c = sec.composition([])
    assert c["n_total"] == 0 and c["by_industry"] == []


def test_emergent_signal_majority_commodity_cyclic():
    em = sec.emergent(_rows(), threshold=0.50)
    assert em is not None
    assert abs(em["share"] - 0.6) < 1e-9
    assert set(em["industries"]) == {"能源金属", "工业金属"}


def test_emergent_signal_below_threshold_none():
    # 2/6 = 33% < 50% → 无涌现簇
    rows = [{"industry": "能源金属", "type": "cyclic", "commodity_variety": "碳酸锂"}] * 2 + \
           [{"industry": "白酒Ⅱ", "type": "value"}] * 4
    assert sec.emergent(rows) is None
    # cyclic 但无 commodity 映射(未映射降级) → 不计入分子
    rows2 = [{"industry": "证券Ⅱ", "type": "cyclic", "commodity_variety": None}] * 5 + \
            [{"industry": "银行", "type": "value"}]
    assert sec.emergent(rows2) is None
    # 空池 → None
    assert sec.emergent([]) is None
