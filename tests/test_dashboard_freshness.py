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


# ---- _industry_leg: 行业腿主源判别(申万优先,空才东财回退——与 pool.screen 同优先级) ----

import pandas as pd


class _StubStore:
    """只实现 _industry_leg 用到的四个方法。"""

    def __init__(self, sw_rows, em_rows, sw_meta=None, em_last=None):
        self._sw = pd.DataFrame(sw_rows, columns=["code", "industry"])
        self._sw = self._sw.set_index("code") if len(self._sw) else self._sw
        self._em = pd.DataFrame(em_rows, columns=["code", "industry"])
        self._em = self._em.set_index("code") if len(self._em) else self._em
        self._sw_meta = sw_meta
        self._em_last = em_last

    def sw_industry_map(self):
        return self._sw if len(self._sw) else pd.DataFrame(columns=["industry", "l1", "l2"])

    def industry_map(self):
        return self._em if len(self._em) else pd.DataFrame(columns=["industry"])

    def get_meta(self, key):
        return self._sw_meta if key == "last_sw_industry_update" else None

    def last_industry_snapshot(self):
        return self._em_last


def test_industry_leg_prefers_sw_when_present():
    st = _StubStore(sw_rows=[("000001", "银行"), ("600000", "银行"), ("300750", "电池")],
                    em_rows=[("000001", "银行")], sw_meta="2026-10-07", em_last="2026-09-01")
    leg = ddc._industry_leg(st)
    assert leg["sw"] is True and leg["src"] == "申万"
    assert leg["last"] == "2026-10-07"
    assert leg["n_boards"] == 2  # 银行+电池: distinct L3
    assert len(leg["ind_map"]) == 3


def test_industry_leg_falls_back_to_em_when_sw_empty():
    st = _StubStore(sw_rows=[], em_rows=[("000001", "银行"), ("600519", "白酒")],
                    em_last="2026-09-01")
    leg = ddc._industry_leg(st)
    assert leg["sw"] is False and leg["src"] == "东财"
    assert leg["last"] == "2026-09-01"
    assert leg["n_boards"] == 2


def test_industry_leg_both_empty_reports_zero_boards():
    st = _StubStore(sw_rows=[], em_rows=[])
    leg = ddc._industry_leg(st)
    assert leg["sw"] is False and leg["n_boards"] == 0
    assert leg["last"] is None
