"""Research module (V3.1): read-only ETF 行业研究 — valuation + chip + trend 性价比.

Does NOT feed the trading engine. Produces a per-ETF research snapshot consumed by the
HTML dashboard (scripts/research_report.py). See plans/breezy-jumping-pond.md.
"""
from .scoring import (
    valuation_score,
    chip_score,
    trend_score,
    composite,
    analyze_etf,
    pe_percentile,
    reduction_from_peak,
)

__all__ = [
    "valuation_score",
    "chip_score",
    "trend_score",
    "composite",
    "analyze_etf",
    "pe_percentile",
    "reduction_from_peak",
]
