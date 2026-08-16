"""策略1 复合分(纯): 护栏三闸 + oversold_score 手算 + 超卖段起点(expanding 分位防前视)。"""
import numpy as np
import pandas as pd

from stockagent.pool.scoring import guardrails, oversold_run_start, oversold_score


# ---------- guardrails ----------
def test_guard_all_pass():
    g = guardrails("预增", 0.02, "up")
    assert g["pass_"] is True and g["flags"] == []


def test_guard_loss_forecast_blocks():
    for t in ("首亏", "续亏", "增亏"):
        g = guardrails(t, None, None)
        assert g["pass_"] is False
        assert any("预亏族" in f for f in g["flags"])


def test_guard_revision_drop_blocks():
    g = guardrails(None, -0.05, None)   # 4周下修 −5% < −3% 阈
    assert g["pass_"] is False
    assert any("下修" in f for f in g["flags"])
    assert guardrails(None, -0.02, None)["pass_"] is True   # −2% 阈内放行


def test_guard_facechange_down_blocks():
    assert guardrails(None, None, "down")["pass_"] is False
    assert guardrails(None, None, "up")["pass_"] is True


def test_guard_missing_data_passes_with_flags():
    g = guardrails(None, None, None)
    assert g["pass_"] is True          # 缺数据 ≠ 已知风险
    assert g["flags"] == ["预告缺", "修正缺", "变脸缺"]


# ---------- oversold_score ----------
def _dev(pct, valid=True, cur=-0.15):
    return {"valid": valid, "pct": pct, "cur_dev": cur, "max_dev": 0.2, "min_dev": cur}


def test_score_hand_computed():
    """pct=0.02, trigger=0.05 → depth=0.6; stabilize=1.0; 0.6/0.4 权重 → 76 分。"""
    r = oversold_score(_dev(0.02), 1.0, {"pass_": True, "flags": []}, {})
    assert r["trigger"] is True
    assert abs(r["depth"] - 0.6) < 1e-9
    assert abs(r["score"] - 100 * (0.6 * 0.6 + 0.4 * 1.0)) < 1e-9   # 76


def test_score_boundary_pct_triggers_with_zero_depth():
    r = oversold_score(_dev(0.05), 0.6, {"pass_": True, "flags": []}, {})
    assert r["trigger"] is True and r["depth"] == 0.0
    assert abs(r["score"] - 100 * 0.4 * 0.6) < 1e-9


def test_score_not_triggered_and_invalid():
    r = oversold_score(_dev(0.06), 1.0, {"pass_": True, "flags": []}, {})
    assert r["trigger"] is False and r["score"] is None
    r2 = oversold_score(_dev(float("nan"), valid=False), 1.0, {"pass_": True, "flags": []}, {})
    assert r2["trigger"] is False and r2["score"] is None


def test_score_no_trigger_when_above_ma():
    """稳态上行股的退化防御: 分位≈0 但 cur_dev>0(线方)→ 不是超卖。"""
    r = oversold_score(_dev(0.0, cur=0.012), 1.0, {"pass_": True, "flags": []}, {})
    assert r["trigger"] is False and r["score"] is None


def test_score_guard_fail_shows_but_null_score():
    """护栏不过: trigger 仍 True(显示行),score=None(排除出候选/排序)。"""
    r = oversold_score(_dev(0.01), 1.0, {"pass_": False, "flags": ["预亏族(续亏)"]}, {})
    assert r["trigger"] is True
    assert r["score"] is None
    assert r["guard_pass"] is False


def test_score_stabilize_crash_discounted():
    """飞刀(stabilize 0.25)比分量从 40 打到 10。"""
    r = oversold_score(_dev(0.0), 0.25, {"pass_": True, "flags": []}, {})
    assert abs(r["score"] - (100 * 0.6 * 1.0 + 100 * 0.4 * 0.25)) < 1e-9   # 70


# ---------- oversold_run_start ----------
def _crash_series(n_base=100, n_crash=25):
    """前段 10→20 线性 + 微波动,末 n_crash 根 ×0.8(跌破 MA → 超卖段)。"""
    base = np.linspace(10.0, 20.0, n_base) * (1 + 0.004 * np.sin(np.arange(n_base)))
    vals = np.concatenate([base, base[-1] * np.linspace(1.0, 0.78, n_crash)])
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-06", periods=n_base + n_crash)]
    return pd.Series(vals, index=idx)


def test_run_start_finds_crash_onset():
    s = _crash_series()
    start = oversold_run_start(s)
    assert start is not None
    # 段起点应在崩塌早期(非首根;expanding 分位在首触最深时 ≤5%)
    assert start in s.index[-40:]


def test_run_start_none_when_not_oversold():
    """加速上行(dev 单调走高 → expanding 分位 ≈1)→ 不在超卖段。"""
    i = np.arange(120)
    vals = 10.0 * np.exp(2e-4 * i ** 1.3)
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-06", periods=120)]
    assert oversold_run_start(pd.Series(vals, index=idx)) is None


def test_run_start_short_series_none():
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-06", periods=10)]
    assert oversold_run_start(pd.Series(np.linspace(10, 5, 10), index=idx)) is None
