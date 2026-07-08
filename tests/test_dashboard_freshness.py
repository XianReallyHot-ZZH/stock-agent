"""Freshness-check reference uses trade_calendar (authoritative), not the benchmark's own
price date (circular). Regression for the bug where --fix skipped price refresh on 1–2 day
gaps because it compared today vs the benchmark date with a >3-day threshold.
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard_data_check as ddc  # noqa: E402


def _conn_with_calendar(dates: list[str]) -> sqlite3.Connection:
    """In-memory conn whose trade_calendar contains exactly `dates` (all is_open=1)."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE trade_calendar(date TEXT, is_open INTEGER)")
    for d in dates:
        conn.execute("INSERT INTO trade_calendar(date, is_open) VALUES(?, 1)", (d,))
    return conn


def test_target_after_close_picks_today():
    # 07-08 is a trading day and it's past 15:00 → today's close is available
    conn = _conn_with_calendar(["2026-07-06", "2026-07-07", "2026-07-08"])
    assert ddc._target_trading_day(conn, datetime(2026, 7, 8, 16, 0)) == "2026-07-08"


def test_target_before_close_picks_prior_trading_day():
    # 07-08 pre-close (08:00) → bound is 07-07 → latest closed session is 07-07
    conn = _conn_with_calendar(["2026-07-06", "2026-07-07", "2026-07-08"])
    assert ddc._target_trading_day(conn, datetime(2026, 7, 8, 8, 0)) == "2026-07-07"


def test_target_skips_forward_to_last_calendar_le_date():
    # bound (07-10) is itself a trading day → picked directly
    conn = _conn_with_calendar(["2026-07-08", "2026-07-09", "2026-07-10"])
    assert ddc._target_trading_day(conn, datetime(2026, 7, 11, 10, 0)) == "2026-07-10"


def test_target_bound_between_trading_days_falls_back():
    # bound 07-12 (a non-trading gap) → MAX(date<=07-12) = 07-10
    conn = _conn_with_calendar(["2026-07-09", "2026-07-10"])
    assert ddc._target_trading_day(conn, datetime(2026, 7, 13, 8, 30)) == "2026-07-10"


def test_target_empty_calendar_returns_none():
    conn = _conn_with_calendar([])
    assert ddc._target_trading_day(conn, datetime(2026, 7, 8, 16, 0)) is None
