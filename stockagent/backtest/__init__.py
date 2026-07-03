"""Backtest: self-researched vectorized/event-loop engine + metrics + decision gate."""
from . import metrics
from .vector_backtest import run_backtest, BacktestResult
from .sweep import make_config

__all__ = ["metrics", "run_backtest", "BacktestResult", "make_config"]
