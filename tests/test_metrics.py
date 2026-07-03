import numpy as np
import pandas as pd

from stockagent.backtest import metrics as M


def eq(values):
    return pd.Series(values, dtype=float)


def test_total_and_annualized():
    e = eq([100, 110, 121])  # +21% over 2 periods
    assert round(M.total_return(e), 6) == 0.21


def test_max_drawdown():
    # 100 -> 120 -> 90 -> 95 : worst dd from 120 to 90 = -25%
    e = eq([100, 120, 90, 95])
    mdd, _ = M.max_drawdown(e)
    assert round(mdd, 4) == -0.25


def test_no_drawdown():
    e = eq([1, 2, 3, 4])
    mdd, _ = M.max_drawdown(e)
    assert mdd == 0.0


def test_calmar_signs():
    e = eq([100, 120, 90])  # ann over 2 periods; mdd -25%
    # annualized: (90/100)^(252/2)-1 ; mdd -0.25 -> calmar = ann/0.25
    ann = M.annualized_return(e)
    c = M.calmar(e)
    assert round(c, 4) == round(ann / 0.25, 4)
