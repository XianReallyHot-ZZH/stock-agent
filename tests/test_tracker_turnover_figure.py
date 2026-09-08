"""Tests for 指数择时 ⑧ 成交量地量监测时序图(默认窗口=近3年)。

_turnover_figure: 长历史(>3年)默认 x 窗=近3年(同③估值图模式)、y 轴按可见段显式
设范围(不被 1991 起的全史 y 幅压扁);短历史(<3年)不设 range(退「全部」autorange)。
No network — 合成 Series/DataFrame 喂入,断言 figure layout。
"""
import numpy as np
import pandas as pd

from stockagent.tracker import dashboard as d


def _synth(n_years: int):
    dates = pd.date_range(end="2026-09-07", periods=n_years * 250, freq="B")
    # 指数:老段极高(30000)→近期段低位(3000-4000):若 y 轴误用全史口径,近期必被压扁。
    # 近期段取 900 交易日(>3 日历年的 ~783 交易日),确保 3 年默认窗内只有近期段
    n_old = max(len(dates) - 900, 0)
    close = pd.Series(
        np.concatenate([np.linspace(30000.0, 28000.0, n_old),
                        np.linspace(3000.0, 3900.0, len(dates) - n_old)]),
        index=dates)
    tot = pd.DataFrame({"total": np.linspace(5e10, 2e11, len(dates))}, index=dates)
    return tot, close


def test_turnover_figure_default_window_3y():
    tot, close = _synth(12)
    fig = d._turnover_figure(tot, close, [])
    xr = fig.layout.xaxis.range
    assert xr is not None
    span_days = (pd.Timestamp(xr[1]) - pd.Timestamp(xr[0])).days
    assert 1090 <= span_days <= 1100                       # ≈ 3 年(闰日容差)
    assert pd.Timestamp(xr[1]) == pd.Timestamp(tot.index[-1])


def test_turnover_figure_y_fits_visible_window():
    tot, close = _synth(12)
    events = [tot.index[2600], tot.index[300]]             # 一在窗内一在窗外
    fig = d._turnover_figure(tot, close, events)
    yr = fig.layout.yaxis.range                            # 上证综指(左轴)
    assert yr is not None and yr[1] < 28000                # 可见段口径:老段 30000 不进 y 轴
    assert 0 < yr[0] < 3117 and yr[1] > 3900               # 覆盖可见段高低(窗起于近期段~3117,±8% pad)
    yr2 = fig.layout.yaxis2.range                          # 成交额(右轴,亿)
    assert yr2 is not None and all(np.isfinite(yr2))


def test_turnover_figure_short_history_keeps_autorange():
    tot, close = _synth(2)
    fig = d._turnover_figure(tot, close, [])
    assert fig.layout.xaxis.range is None                  # <3 年 → 不设窗,退「全部」
    assert fig.layout.yaxis.range is None
