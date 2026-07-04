"""Pluggable rotation-signal registry.

A signal is a module exposing `score_universe(close_by_symbol, params) -> DataFrame`.
Add a signal by registering it here + giving it a params key under rotation.<name>.

Imports are LAZY (inside get_signal) to avoid a circular import: momentum.py
imports select_top_k from signals._common, so this package must not import
momentum at module-load time.
"""
from __future__ import annotations

SIGNAL_NAMES = ["momentum", "reversion", "bb_macd", "share_flow", "momentum_sf"]


def get_signal(name: str):
    """Return the signal module for `name`."""
    name = (name or "momentum").lower()
    if name == "momentum":
        from .. import momentum
        return momentum
    if name == "reversion":
        from . import reversion
        return reversion
    if name == "bb_macd":
        from . import bb_macd
        return bb_macd
    if name == "share_flow":
        from . import share_flow
        return share_flow
    if name == "momentum_sf":
        from . import momentum_sf
        return momentum_sf
    raise ValueError(f"unknown signal '{name}'; available: {SIGNAL_NAMES}")


def current_signal(params: dict):
    """Resolve the signal module selected in params (default momentum)."""
    name = params.get("rotation", {}).get("signal", {}).get("name", "momentum")
    return get_signal(name)
