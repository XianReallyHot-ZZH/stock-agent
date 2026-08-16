"""修正动量个股版 + PEAD 无前视 + 变脸四检测器(纯)。"""
import pandas as pd

from stockagent.pool.facechange import facechange
from stockagent.pool.pead import pead_rank, pead_surprise
from stockagent.pool.revision import revision_table, stock_revision


# ---------- stock_revision ----------
def _row(n_reports=10, eps=1.0, fy=2026):
    return {"n_reports": n_reports, "eps_fy1": eps, "fy1_year": fy}


def test_stock_revision_basic_and_deadband():
    r = stock_revision(_row(eps=1.1), _row(eps=1.0))
    assert r is not None and abs(r["rev"] - 0.1) < 1e-9 and r["up"] is True
    # 死区内微动: 无方向
    r2 = stock_revision(_row(eps=1.005), _row(eps=1.0))
    assert r2["up"] is False and r2["down"] is False
    r3 = stock_revision(_row(eps=0.97), _row(eps=1.0))
    assert r3["down"] is True


def test_stock_revision_guards():
    assert stock_revision(_row(fy=2026), _row(fy=2025)) is None      # 财年翻滚
    assert stock_revision(_row(eps=1.0), _row(eps=-1.0)) is None     # 负基数
    assert stock_revision(_row(eps=1.0), _row(eps=0.0)) is None
    assert stock_revision(_row(n_reports=2), _row(eps=1.0)) is None  # 研报数门
    assert stock_revision(None, _row(eps=1.0)) is None


def test_revision_table_cold_start_and_rows():
    snap_now = pd.DataFrame([_row(eps=1.2)], index=["600519"]).rename_axis("code")
    snap_then = pd.DataFrame([_row(eps=1.0)], index=["600519"]).rename_axis("code")
    # 快照数 3 < 4 → 冷启动横幅
    t = revision_table(snap_now, snap_then, dates_available=3, codes=["600519"])
    assert t["rows"] == [] and t["cold_start"] == {"have": 3, "need": 4}
    t2 = revision_table(snap_now, snap_then, dates_available=4, codes=["600519"])
    assert t2["cold_start"] is None
    assert len(t2["rows"]) == 1 and abs(t2["rows"][0]["rev_pct"] - 20.0) < 1e-9


# ---------- pead ----------
def test_pead_leg_c_annual_consensus():
    """年报期 + 财年对齐: expected=(11/10−1)=10%,surprise=30−10=+20pp,leg C。"""
    r = pead_surprise(30.0, "20251231", eps_now=11.0, eps_prior=10.0, fy1_year=2025)
    assert r["valid"] is True and r["leg"] == "C"
    assert abs(r["surprise_pp"] - 20.0) < 1e-9 and abs(r["expected"] - 10.0) < 1e-9


def test_pead_leg_c_guards():
    # 财年不对齐(公告时 fy1 已翻到下年) → 退 leg A
    r = pead_surprise(30.0, "20251231", eps_now=11.0, eps_prior=10.0,
                      fy1_year=2026, prior_actual_yoy=12.0)
    assert r["leg"] == "A" and abs(r["expected"] - 12.0) < 1e-9
    # 非年报期(中报): eps 列是全年预期,不能除 → leg A
    r2 = pead_surprise(30.0, "20260630", eps_now=11.0, eps_prior=10.0,
                       fy1_year=2026, prior_actual_yoy=15.0)
    assert r2["leg"] == "A"
    # 负基数 → leg A
    r3 = pead_surprise(30.0, "20251231", eps_now=11.0, eps_prior=-10.0,
                       fy1_year=2025, prior_actual_yoy=8.0)
    assert r3["leg"] == "A"


def test_pead_invalid_when_both_legs_missing():
    r = pead_surprise(30.0, "20260630")
    assert r["valid"] is False and r["surprise_pp"] is None


def test_pead_rank_filter_sort_topn():
    evts = [
        {"code": "A", "surprise_pp": 5.0},      # 低于阈,不入
        {"code": "B", "surprise_pp": 25.0},
        {"code": "C", "surprise_pp": -40.0},    # |−40| ≥10,负超预期也入(双臂验证素材)
        {"code": "D", "surprise_pp": 60.0},
        {"code": "E", "surprise_pp": None},
    ]
    rows = pead_rank(evts, surprise_min_pp=10.0, top_n=2)
    assert [r["code"] for r in rows] == ["D", "B"]


# ---------- facechange ----------
def _acts(mapping: dict[str, float]) -> pd.DataFrame:
    df = pd.DataFrame({"np_yoy": list(mapping.values())}, index=list(mapping.keys()))
    df.index.name = "report_period"
    return df.sort_index()


def test_facechange_gear_down_xiaomi():
    """小米型: 20%+ 掉到个位数(同尾 −23pp)。"""
    a = _acts({"20240630": 30.0, "20250630": 28.0, "20251231": 5.0, "20260630": 5.0})
    r = facechange(a)
    assert r["direction"] == "down" and "gear_down" in r["kinds"]
    assert "跳档" in r["detail"]


def test_facechange_trend_break_tencent():
    """腾讯型: 同尾连续 ≥3 步下行累计 ≥30pp(50→40→28→10)。"""
    a = _acts({"20221231": 50.0, "20231231": 40.0, "20241231": 28.0, "20251231": 10.0})
    r = facechange(a)
    assert r["direction"] == "down" and "trend_break" in r["kinds"]


def test_facechange_consec_neg_meituan():
    """美团型: 逐报告期连续 ≥3 期负增长(跨尾也计)。"""
    a = _acts({"20250331": 5.0, "20250630": -5.0, "20250930": -10.0, "20251231": -3.0})
    r = facechange(a)
    assert r["direction"] == "down" and "consec_neg" in r["kinds"]
    assert "连续 3" in r["detail"]


def test_facechange_turn_up():
    """同尾(0630)两步下行后回升 ≥10pp: 30→15→5→20。"""
    a = _acts({"20230630": 30.0, "20240630": 15.0, "20250630": 5.0, "20260630": 20.0})
    r = facechange(a)
    assert r["direction"] == "up" and "turn_up" in r["kinds"]
    assert "拐头" in r["detail"]


def test_facechange_no_signal_and_empty():
    a = _acts({"20240630": 10.0, "20250630": 12.0, "20260630": 11.0})
    r = facechange(a)
    assert r["valid"] is True and r["direction"] is None and r["kinds"] == []
    assert facechange(pd.DataFrame())["valid"] is False
    assert len(r["tail"]) == 3


def test_facechange_same_tail_only():
    """跨尾不硬比: 最新 20260630 vs 20251231 不构成同尾比较(累计口径防御)。"""
    a = _acts({"20251231": 50.0, "20260630": 10.0})   # 0630 序列仅一期 → 无跳档误报
    r = facechange(a)
    assert "gear_down" not in r["kinds"]
