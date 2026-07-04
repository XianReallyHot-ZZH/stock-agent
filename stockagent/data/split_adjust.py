"""Split/merger adjustment for ETF price data.

Sina returns 不复权 (raw) prices — splits/mergers create artificial price jumps
(50%+ one-day "crashes"). This module detects those events and applies ratio
adjustments to historical prices, producing a split-adjusted series equivalent
to 前复权 (for splits; dividends are minor and left for future).

Algorithm:
  1. Scan daily returns for one-day drops > threshold (default 25%).
  2. For each event, compute split_ratio = price_after / price_before.
  3. Multiply ALL prices before the event by split_ratio.
  4. Multiple events are applied cumulatively (most recent first, going back).
"""
from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)

SPLIT_THRESHOLD_DOWN = -0.25  # one-day return < -25% => likely split
SPLIT_THRESHOLD_UP = 1.0     # one-day return > +100% => likely reverse split/merger


def detect_splits(close: pd.Series) -> list[dict]:
    """Detect split/merger events (both forward splits and reverse splits).

    Returns list of {date, ratio, prev_close, event_close, return} sorted oldest-first.
    Forward split: price drops >25% → ratio < 0.75
    Reverse split: price jumps >100% → ratio > 2.0
    """
    rets = close.pct_change().dropna()
    splits = []
    for d, r in rets.items():
        if r < SPLIT_THRESHOLD_DOWN or r > SPLIT_THRESHOLD_UP:
            prev_close = float(close.loc[:d].iloc[-2])
            event_close = float(close.loc[d])
            ratio = event_close / prev_close
            kind = "reverse_split" if r > 0 else "split"
            splits.append({
                "date": d,
                "ratio": ratio,
                "prev_close": prev_close,
                "event_close": event_close,
                "return": r,
                "kind": kind,
            })
    return splits


def adjust_for_splits(close: pd.Series, splits: list[dict] | None = None) -> tuple[pd.Series, list[dict]]:
    """Apply split adjustments to a close-price series (前复权 style).

    Returns (adjusted_close, splits_detected).
    Adjusted series is continuous across split events.
    """
    if splits is None:
        splits = detect_splits(close)

    adjusted = close.copy().astype(float)

    # Apply most-recent splits first (going backward), each affecting all earlier dates
    for sp in reversed(splits):
        split_date = sp["date"]
        ratio = sp["ratio"]
        # All dates BEFORE the split get multiplied by ratio
        mask = adjusted.index < split_date
        adjusted.loc[mask] = adjusted.loc[mask] * ratio
        log.info("  split at %s: ratio=%.4f, adjusted %d earlier rows", split_date, ratio, mask.sum())

    return adjusted, splits


def adjust_ohlcv(df: pd.DataFrame, splits: list[dict] | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Apply split adjustments to OHLCV (open/high/low/close/volume).

    For splits: OHLC multiplied by ratio for pre-split dates; volume divided by ratio.
    """
    if splits is None:
        splits = detect_splits(df["close"])

    adjusted = df.copy()
    for sp in reversed(splits):
        split_date = sp["date"]
        ratio = sp["ratio"]
        mask = adjusted.index < split_date
        for col in ("open", "high", "low", "close"):
            if col in adjusted.columns:
                adjusted.loc[mask, col] = adjusted.loc[mask, col] * ratio
        if "volume" in adjusted.columns:
            adjusted.loc[mask, "volume"] = adjusted.loc[mask, "volume"] / ratio
        log.info("  OHLCV split at %s: ratio=%.4f, %d rows", split_date, ratio, mask.sum())

    return adjusted, splits
