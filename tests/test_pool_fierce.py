"""策略2 猛×深跌(纯): 深跌判定 / 周期猛(alignment+g确认) / 成长猛(三腿) / 同尾加速 / 入表资格。"""
import pandas as pd

from stockagent.pool.fierce import (
    accel_from_actuals,
    cyclic_fierce,
    deep_drawdown,
    eligible_s2,
    growth_fierce,
)


def _series(peak=100.0, trough=55.0, n=120):
    vals = [peak * (1 - (1 - trough / peak) * min(i / (n - 1), 1.0)) for i in range(n)]
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-06", periods=n)]
    return pd.Series(vals, index=idx)


# ---------- deep_drawdown ----------
def test_deep_drawdown_threshold():
    assert deep_drawdown(_series(trough=55.0), min_dd=0.40)["deep"] is True   # −45%
    assert deep_drawdown(_series(trough=65.0), min_dd=0.40)["deep"] is False  # −35%
    r = deep_drawdown(pd.Series(dtype=float), min_dd=0.40)
    assert r["deep"] is False


# ---------- cyclic_fierce ----------
def _com(yoy, recent):
    return {"valid": True, "yoy": yoy, "recent": recent}


def test_cyclic_fierce_alignment_and_confirmation():
    """商品向上(health=1.0) × 股价落后(lag 0.15/0.20→0.75) → 0.75;g=15% ≥10% 确认。"""
    r = cyclic_fierce(_com(0.20, 0.10), stock_recent_return=-0.05,
                      consensus_g=0.15, cfg={})
    assert r["valid"] is True
    assert abs(r["fierce"] - 0.75) < 1e-9
    assert r["confirmed"] is True
    assert r["health"] == 1.0


def test_cyclic_fierce_zero_when_commodity_down():
    """商品向下(health=0)=双杀不是错杀 → score 0 → invalid(不入 S2)。"""
    r = cyclic_fierce(_com(-0.20, -0.10), stock_recent_return=-0.05, consensus_g=None)
    assert r["valid"] is False and r["fierce"] == 0.0
    assert r["confirmed"] is False


def test_cyclic_fierce_no_lag_low_score():
    """股价已跟上商品(lag=0) → 猛分 0(不是错杀)。"""
    r = cyclic_fierce(_com(0.20, 0.0), stock_recent_return=0.0, consensus_g=0.5)
    assert r["fierce"] == 0.0 and r["valid"] is False


def test_cyclic_fierce_invalid_com():
    assert cyclic_fierce({"valid": False}, 0.0, 0.2)["valid"] is False


# ---------- growth_fierce ----------
def test_growth_fierce_all_legs_perfect():
    r = growth_fierce(g_rank_pct=0.9, revision_up=True, accel_pp=10.0, cfg={})
    assert r["valid"] is True
    assert abs(r["fierce"] - 1.0) < 1e-9   # rank 1.0 + rev 1.0 + accel clamp 1.0


def test_growth_fierce_two_legs_valid_one_leg_invalid():
    r = growth_fierce(g_rank_pct=0.4, revision_up=False, accel_pp=None, cfg={})
    assert r["valid"] is True                       # 两腿成立
    assert abs(r["fierce"] - (0.5 + 0.0) / 2) < 1e-9  # rank 0.4/0.8=0.5, rev 0
    assert growth_fierce(g_rank_pct=0.9, revision_up=None, accel_pp=None)["valid"] is False


def test_growth_fierce_negative_accel_leg():
    r = growth_fierce(g_rank_pct=0.9, revision_up=True, accel_pp=-10.0, cfg={})
    assert r["valid"] is True
    assert abs(r["fierce"] - (1.0 + 1.0 - 1.0) / 3) < 1e-9   # accel −10/5=−2→clamp −1


def test_growth_fierce_rank_linear():
    r = growth_fierce(g_rank_pct=0.4, revision_up=True, cfg={})  # rank 0.5
    assert abs(r["legs"]["rank"] - 0.5) < 1e-9


# ---------- accel_from_actuals ----------
def _actuals(mapping: dict[str, float]) -> pd.DataFrame:
    df = pd.DataFrame({"np_yoy": list(mapping.values())}, index=list(mapping.keys()))
    df.index.name = "report_period"
    return df


def test_accel_same_tail_compare():
    a = _actuals({"20240630": 10.0, "20250630": 20.0, "20251231": 5.0, "20260630": 30.0})
    assert accel_from_actuals(a) == 10.0    # 最新 20260630 vs 上年同期 20250630: 30−20


def test_accel_missing_prior_year_none():
    a = _actuals({"20250630": 20.0, "20260630": 30.0, "20260331": 8.0})
    # 最新是 20260630,prior 20250630 在 → 10.0;构造 prior 缺失:
    b = _actuals({"20251231": 5.0, "20260630": 30.0})   # 20260630 vs 20250630 缺
    assert accel_from_actuals(b) is None
    assert accel_from_actuals(pd.DataFrame()) is None


# ---------- eligible_s2 ----------
def _fierce(valid=True):
    return {"valid": valid, "fierce": 0.5}


def test_eligible_matrix():
    assert eligible_s2(True, "windowA", _fierce(), "growth") is True
    assert eligible_s2(True, "windowA", _fierce(), "cyclic") is True
    assert eligible_s2(True, "windowB", _fierce(), "cyclic") is True    # 窗口B 仅周期
    assert eligible_s2(True, "windowB", _fierce(), "growth") is False
    assert eligible_s2(True, "disclosure", _fierce(), "cyclic") is False
    assert eligible_s2(True, "windowA", _fierce(False), "growth") is False
    assert eligible_s2(False, "windowA", _fierce(), "growth") is False
    assert eligible_s2(True, "windowA", _fierce(), "value") is False    # 价值无猛分
    assert eligible_s2(True, "windowA", _fierce(), None) is False       # 未映射不入
