"""复权核心(纯): 分红前复权(交易所公式/多事件累乘/越界flag) + 除权嫌疑带。防除息假日极值是 S1 正确性核心。"""  # noqa: E501
import numpy as np
import pandas as pd

from stockagent.pool.prices import dividend_adjusted_close, unexplained_cliffs


def _close(vals: list[float], start="2026-01-05") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(vals))
    return pd.Series(vals, index=[d.strftime("%Y-%m-%d") for d in idx])


def _div(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """rows: (ex_date, cash, stock_div_10, trans_10)"""
    return pd.DataFrame(
        {ex: {"cash_per_share": c, "stock_div_10": s, "trans_10": t} for ex, c, s, t in rows}
    ).T.sort_index()


# ---------- dividend_adjusted_close ----------
def test_cash_dividend_adjustment():
    """10 送 1 元现金: 前历史 ×(prev−1)/(prev×1)=0.99,除息日前后连续。"""
    close = _close([100.0] * 3 + [99.0] * 3)          # d4 起除息价 99
    div = _div([(close.index[3], 1.0, 0.0, 0.0)])     # ex = 第4根
    adj, events = dividend_adjusted_close(close, div)
    assert not events[0]["flagged"]
    np.testing.assert_allclose(adj.iloc[:3], 99.0)    # 100×0.99
    np.testing.assert_allclose(adj.iloc[3:], 99.0)
    assert float(adj.pct_change().abs().max()) == 0.0  # 调整后完全连续


def test_10_trans_8_split_like_adjustment():
    """10转8(trans_10=8): share_ratio=1.8, factor=20/(20×1.8)。"""
    close = _close([20.0, 20.0, 11.0, 11.0])          # 转增后除权价 ~11.1
    div = _div([(close.index[2], 0.0, 0.0, 8.0)])
    adj, events = dividend_adjusted_close(close, div)
    assert not events[0]["flagged"]
    np.testing.assert_allclose(adj.iloc[:2], 20.0 / 1.8, rtol=1e-9)
    # 调整后断崖从 −45% 缩到 ≈0(除权参考价 20/1.8≈11.11 vs 市价 11,残余为真实波动)
    assert abs(float(adj.iloc[2] / adj.iloc[1]) - 1.0) < 0.02


def test_multiple_events_cumulative():
    """两次现金分红: 事件1调前段,事件2以「已调整的 prev」算 factor → 全序列在两事件处连续。
    close=[100,100,99,99,98,98]: f1=(100−1)/100=0.99(调前2根);f2=(99−0.9)/99(调前4根)
    → 前4根全落 98.1。"""
    close = _close([100.0] * 2 + [99.0] * 2 + [98.0] * 2)
    d1, d2 = close.index[2], close.index[4]
    div = _div([(d1, 1.0, 0.0, 0.0), (d2, 0.9, 0.0, 0.0)])
    adj, _ = dividend_adjusted_close(close, div)
    np.testing.assert_allclose(adj.iloc[:4], 98.1, rtol=1e-6)
    np.testing.assert_allclose(adj.iloc[4:], 98.0)


def test_factor_out_of_band_flagged_no_adjust():
    """cash > prev(factor 为负)→ flagged 且不动序列(宁可不调不加噪)。"""
    close = _close([100.0, 100.0, 100.0])
    div = _div([(close.index[2], 150.0, 0.0, 0.0)])
    adj, events = dividend_adjusted_close(close, div)
    assert events[0]["flagged"] is True
    np.testing.assert_allclose(adj.to_numpy(), 100.0)


def test_none_dividends_passthrough_and_early_ex():
    close = _close([100.0, 101.0, 102.0])
    adj, events = dividend_adjusted_close(close, None)
    assert len(events) == 0
    np.testing.assert_allclose(adj.to_numpy(), close.to_numpy())
    # ex_date 早于/等于首根(取不到 prev)→ 跳过
    div = _div([(close.index[0], 1.0, 0.0, 0.0), ("2025-12-01", 1.0, 0.0, 0.0)])
    adj2, events2 = dividend_adjusted_close(close, div)
    assert events2 == []


def test_raw_fake_oversold_healed_by_adjustment():
    """集成: 平滑上行序列里插一个 −3% 除息台阶——raw 有假断崖,调整后消失(S1 不被假触发)。"""
    n = 120
    base = np.linspace(10.0, 20.0, n)
    vals = base.copy()
    ex_i = 60
    vals[ex_i:] *= 0.97                       # 假装除息 3%
    close = _close(list(vals))
    div = _div([(close.index[ex_i], 0.30, 0.0, 0.0)])   # ~3% 现金分红(10→9.7 附近)
    adj, _ = dividend_adjusted_close(close, div)
    raw_cliff = float(close.pct_change().iloc[ex_i])
    adj_cliff = float(adj.pct_change().iloc[ex_i])
    assert raw_cliff < -0.02                   # raw 有台阶
    assert abs(adj_cliff) < 0.005              # 调整后连续(残余=线性斜率)


# ---------- unexplained_cliffs ----------
def _old_div(date: str) -> pd.DataFrame:
    """远期分红记录(证明该股是分红户,但不在附近)——嫌疑判定的前提。"""
    return _div([("2024-06-15", 2.0, 0.0, 0.0)])


def test_cliff_suspect_band():
    """−6% 孤立断崖+分红户但附近无记录 → 嫌疑;附近有记录 → 不是;−3%/−20% 带外不是。"""
    idx = pd.bdate_range("2026-01-05", periods=6)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    close = pd.Series([100.0, 100.0, 94.0, 94.0, 75.0, 75.0], index=dates)  # −6% 与 −20%
    assert unexplained_cliffs(close, _old_div(dates[2])) == [dates[2]]   # 孤立 −6% → 嫌疑
    div = _div([(dates[2], 6.0, 0.0, 0.0)])
    assert unexplained_cliffs(close, div) == []                          # 附近有记录 → 解释了
    small = pd.Series([100.0, 100.0, 97.0, 97.0], index=dates[:4])
    assert unexplained_cliffs(small, _old_div(dates[2])) == []           # −3% 带下界外(行情噪音)
    crash = pd.Series([100.0, 80.0, 80.0], index=dates[:3])
    assert unexplained_cliffs(crash, _old_div(dates[1])) == []           # −20% 带上界外(真崩盘)


def test_cliff_requires_dividend_payer():
    """非分红户(dividends 空/None): 任何跌幅都不是除息伪影。"""
    idx = pd.bdate_range("2026-01-05", periods=4)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    close = pd.Series([100.0, 100.0, 92.0, 92.0], index=dates)
    assert unexplained_cliffs(close, None) == []
    assert unexplained_cliffs(close, pd.DataFrame(columns=["cash_per_share"])) == []


def test_cliff_isolation_consecutive_slide_not_flagged():
    """连续阴跌(滑梯)不是除息缺口: 第二日因前一日已跌被孤立性排除。"""
    idx = pd.bdate_range("2026-01-05", periods=5)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    close = pd.Series([100.0, 100.0, 94.0, 88.7, 88.7], index=dates)   # −6% 接 −5.6%
    sus = unexplained_cliffs(close, _old_div(dates[2]))
    assert sus == [dates[2]]  # 只有滑梯首日入候选;次日(前一日<−1%)排除


def test_cliff_nearby_dividend_window():
    """分红记录在 ±5 日内 → 该跌幅不是嫌疑(实施日与登记日错位容忍)。"""
    idx = pd.bdate_range("2026-01-05", periods=4)
    dates = [d.strftime("%Y-%m-%d") for d in idx]
    close = pd.Series([100.0, 92.0, 92.0, 92.0], index=dates)
    div = _div([(dates[2], 8.0, 0.0, 0.0)])   # 记录晚一天到
    assert unexplained_cliffs(close, div) == []
