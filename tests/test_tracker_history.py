"""历史案例回归测试(B4)— 用真实回填数据验证指标在已知历史事件上的定性正确。

依赖 DB 已回填指数数据(backfill_index.py);数据不足则 skip(新环境/CI 友好)。
非纯单元测试 —— 这些是用真实市场历史校准指标的「活校验」。
"""
from __future__ import annotations

import pytest

from stockagent.config import get_config
from stockagent.data import Store
from stockagent.tracker import indicators as ti


@pytest.fixture(scope="module")
def store():
    return Store(get_config().db_path)


def _close_to(store, sym, end):
    df = store.get_index_daily_series(sym, end=end)
    return df["close"] if "close" in df.columns and len(df) else df


def test_2015_bull_hs300_above_ma(store):
    # 2015 上半年牛市,沪深300 应稳稳站在 60 日线上
    close = _close_to(store, "000300", "2015-05-29")
    if len(close) < 60:
        pytest.skip("no 2015 HS300 history")
    st = ti.trend_state(close, 60)
    assert st["valid"] and st["above_ma"] is True


def test_2015_bull_hs300_deviation_elevated(store):
    # 牛市末段,价格相对 60 日线应有明显正偏离
    close = _close_to(store, "000300", "2015-05-29")
    if len(close) < 80:
        pytest.skip("no 2015 HS300 history")
    ex = ti.deviation_extremes(close, 60)
    assert ex["valid"] and ex["cur_dev"] > 0.03


def test_2018_bear_hs300_below_ma(store):
    # 2018 去杠杆熊市,沪深300 应在 60 日线下
    close = _close_to(store, "000300", "2018-10-19")
    if len(close) < 60:
        pytest.skip("no 2018 HS300 history")
    st = ti.trend_state(close, 60)
    assert st["valid"] and st["above_ma"] is False


def test_2015_clean_trend_not_choppy(store):
    # 2015 牛市是干净单边趋势,不应被判为震荡市
    close = _close_to(store, "000300", "2015-05-29")
    if len(close) < 120:
        pytest.skip("no 2015 HS300 history")
    assert ti.is_choppy(close, 60) is False
