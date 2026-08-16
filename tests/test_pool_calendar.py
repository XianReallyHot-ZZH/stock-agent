"""披露日历 v2(纯): 正式报截止四期边界 / current_period(min-live 锚定) / 窗口B(Q1 构造性为空) / 状态机。"""
from datetime import date, datetime

import pytest

from stockagent.pool.calendar import (
    current_period,
    formal_deadline,
    per_stock_window,
    window_b_period,
)


# ---------- formal_deadline ----------
def test_formal_deadlines():
    assert formal_deadline("20251231") == date(2026, 4, 30)   # 年报 → 次年 4/30
    assert formal_deadline("20260331") == date(2026, 4, 30)
    assert formal_deadline("20260630") == date(2026, 8, 31)
    assert formal_deadline("20260930") == date(2026, 10, 31)


def test_formal_deadline_bad_period():
    for bad in ("2026", "20260101", 20260630, None):
        with pytest.raises(ValueError):
            formal_deadline(bad)


# ---------- current_period ----------
def test_current_period_anchors():
    assert current_period(date(2026, 8, 16)) == "20260630"    # 中报正式报季(截止 8/31)
    assert current_period(date(2026, 6, 5)) == "20260630"     # 年报已收窗(Q1 4/30 已过)
    assert current_period(date(2026, 2, 10)) == "20251231"    # 年报季
    assert current_period(date(2026, 4, 20)) == "20251231"    # 年报与Q1双截止重叠,取更老的年报
    assert current_period(date(2026, 12, 10)) == "20261231"
    assert current_period(date(2026, 9, 30)) == "20260930"


# ---------- window_b_period ----------
def test_window_b_open_zones():
    wb = window_b_period(date(2026, 6, 5))
    assert wb is not None and wb["period"] == "20260630"      # H1: 5/14(=4/30+14) ~ 6/30
    assert wb["start"] == date(2026, 5, 14) and wb["end"] == date(2026, 6, 30)
    wb2 = window_b_period(date(2026, 12, 10))
    assert wb2 is not None and wb2["period"] == "20261231"    # 年报: 11/14 ~ 12/31
    wb3 = window_b_period(date(2026, 9, 20))
    assert wb3 is not None and wb3["period"] == "20260930"    # Q3: 9/14 ~ 9/30(短窗)


def test_window_b_closed_zones_and_q1_empty():
    assert window_b_period(date(2026, 8, 16)) is None         # 中报披露季,无窗口B
    assert window_b_period(date(2026, 4, 20)) is None
    # Q1 期窗口B 构造性为空: 年报截止 4/30+grace(5/14) 晚于 Q1 预告开窗(4/15)
    assert window_b_period(date(2026, 3, 10)) is None
    assert window_b_period(date(2026, 1, 5)) is None          # 年报窗口B(至12/31)已收
    wb = window_b_period(date(2025, 12, 31))                  # 年报窗口B 末日(闭区间)
    assert wb is not None and wb["period"] == "20251231"


# ---------- per_stock_window ----------
def test_window_a_forecast_landed():
    r = per_stock_window(datetime(2026, 8, 16), "2026-07-10", None, None)
    assert r["state"] == "windowA" and r["chip"] == "窗口A·预告落地"
    assert r["days_to_formal"] == 15                            # 8/31 − 8/16
    assert r["period"] == "20260630"


def test_window_a_express_overrides_forecast_chip():
    r = per_stock_window(datetime(2026, 8, 16), "2026-07-10", "2026-07-20", None)
    assert r["chip"] == "窗口A·快报落地"


def test_window_a_boundary_and_closed():
    # 截止日当天(8/31)窗口A 仍开(闭区间)
    r = per_stock_window(datetime(2026, 8, 31), "2026-07-10", None, None)
    assert r["state"] == "windowA" and r["days_to_formal"] == 0
    # 正式报已出 → closed
    r2 = per_stock_window(datetime(2026, 8, 16), "2026-07-10", None, "2026-08-14")
    assert r2["state"] == "closed"


def test_window_b_only_cyclic():
    r = per_stock_window(datetime(2026, 6, 5), None, None, None, stock_type="cyclic")
    assert r["state"] == "windowB" and r["chip"] == "窗口B·季中预估"
    r2 = per_stock_window(datetime(2026, 6, 5), None, None, None, stock_type="growth")
    assert r2["state"] == "idle" and "非周期" in r2["chip"]


def test_disclosure_states():
    # 7月中(预告窗 7/1-7/15 内)无预告 → 等预告
    r = per_stock_window(datetime(2026, 7, 10), None, None, None)
    assert r["state"] == "disclosure" and r["chip"] == "披露季·等预告"
    # 8月中(预告窗已过)仍无预告 → 等正式报
    r2 = per_stock_window(datetime(2026, 8, 16), None, None, None)
    assert r2["state"] == "disclosure" and r2["chip"] == "无预告·等正式报"


def test_announce_today_counts_landed():
    """公告日=今天 → 落地(闭区间,当日盘后看板可见)。"""
    r = per_stock_window(datetime(2026, 8, 16), "2026-08-16", None, None)
    assert r["state"] == "windowA"
