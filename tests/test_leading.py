"""Tests for tracker.leading — A 类领先信号(商品价/...). 纯函数 commodity_score + 装配。"""
import numpy as np
import pandas as pd

from stockagent.tracker import leading


# ---- commodity_score(纯函数)----
def test_commodity_score_rising():
    s = pd.Series([100.0] * 252 + [150.0])   # +50% over 252d → 满分
    c = leading.commodity_score(s)
    assert c["valid"] is True
    assert abs(c["yoy"] - 0.5) < 1e-9
    assert abs(c["score"] - 1.0) < 1e-9


def test_commodity_score_falling():
    s = pd.Series([100.0] * 252 + [50.0])    # -50% → 0
    c = leading.commodity_score(s)
    assert abs(c["score"] - 0.0) < 1e-9


def test_commodity_score_flat_and_short_window():
    s = pd.Series([100.0] * 300)             # 平 → 0.5
    assert abs(leading.commodity_score(s)["score"] - 0.5) < 1e-9
    # 短序列:用可用长度做 window(n=len-1)
    c = leading.commodity_score(pd.Series([100.0, 120.0]))   # yoy=+20% → 0.7
    assert c["valid"] is True and abs(c["score"] - 0.7) < 1e-9


def test_commodity_score_insufficient():
    assert leading.commodity_score(pd.Series([100.0]))["valid"] is False   # <2
    assert leading.commodity_score(None)["valid"] is False


# ---- leading_signal(装配,路由)----
def _store():
    import tempfile
    from pathlib import Path
    from stockagent.data import Store
    f = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    f.close()
    return Store(Path(f.name))


def test_leading_signal_commodity_route():
    # 给商品表灌碳酸锂数据,映射 symbol→碳酸锂 → leading 应走商品路径
    st = _store()
    rows = [("碳酸锂", "2025-01-01", 50000.0), ("碳酸锂", "2026-01-01", 100000.0)]
    st.upsert_commodity_price(rows, source="test")
    cfg = type("C", (), {"params": {"stock": {"commodity_map": {"002466": "碳酸锂"},
                                              "leading": {"commodity_yoy_window": 252}}}})()
    lg = leading.leading_signal("002466", st, config=cfg, asof="2026-01-02")
    assert lg["valid"] is True
    assert lg["score"] == 1.0                       # 翻倍 → 满分
    assert "碳酸锂" in lg["label"]


def test_leading_signal_no_mapping_invalid():
    st = _store()
    cfg = type("C", (), {"params": {"stock": {"commodity_map": {}, "leading": {}}}})()
    lg = leading.leading_signal("000001", st, config=cfg)
    assert lg["valid"] is False and lg["score"] == 0.0


# ---- commodity_signal:近期 + 背离(divergent)----
def _cfg(yoy=10, recent=5):
    return type("C", (), {"params": {"stock": {"leading": {
        "commodity_yoy_window": yoy, "commodity_recent_window": recent}}}})()


def test_commodity_signal_divergent():
    st = _store()
    # 同比涨(+50%)、近期跌(-25%)→ 背离
    rows = [("碳酸锂", f"2024-01-{i:02d}", float(v))
            for i, v in enumerate([100] * 6 + [200] * 5 + [150], 1)]
    st.upsert_commodity_price(rows, source="test")
    cs = leading.commodity_signal(st, "碳酸锂", config=_cfg())
    assert cs["valid"] is True
    assert cs["divergent"] is True
    assert cs["yoy"] > 0.4 and cs["recent"] < -0.2


def test_commodity_signal_not_divergent_when_up():
    st = _store()
    # 同比涨、近期也涨 → 不背离
    rows = [("铜", f"2024-01-{i:02d}", float(v))
            for i, v in enumerate([100] * 6 + [150] * 5 + [160], 1)]
    st.upsert_commodity_price(rows, source="test")
    cs = leading.commodity_signal(st, "铜", config=_cfg())
    assert cs["valid"] is True
    assert cs["divergent"] is False
